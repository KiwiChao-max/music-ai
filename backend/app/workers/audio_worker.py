"""Audio task worker.

Runs the processing pipeline for one ``audio_tasks`` row:

    upload -> source separation -> instrument detection
           -> audio-to-MIDI -> GM/XG mapping -> analysis -> commentary

Each processing phase lives in its own module so the worker stays
focused on orchestration: ``stems`` (Demucs), ``transcription``
(drums + melodic), and ``postprocess`` (mapping, analysis, commentary).

The single public entry point is ``process_task(task_id)``.

Memory protection
-----------------
Before the heavy pipeline starts, the worker checks its current RSS
against the configured ``WORKER_MEMORY_GATE_MB``.  If the process is
already bloated (e.g. a previous task leaked memory), the task is
rejected and re-queued so a fresh worker child can pick it up.  This
is the *pre-task gate*; the *post-task recycling* is handled by
Celery's ``worker_max_memory_per_child``.

Pipeline metrics
----------------
The worker records Prometheus metrics for every task:
  * queue wait time (UPLOADED -> PROCESSING)
  * per-stage wall-clock durations
  * total pipeline duration
  * failure reasons (by exception type)
  * memory peak (RSS) during execution
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.pipeline_metrics import (
    PIPELINE_FAILURES_TOTAL,
    PIPELINE_MEMORY_PEAK_BYTES,
    PIPELINE_QUEUE_WAIT_SECONDS,
    PIPELINE_STAGE_DURATION_SECONDS,
    PIPELINE_TASKS_COMPLETED,
    PIPELINE_TOTAL_DURATION_SECONDS,
)
from app.services import file_service, task_service
from app.services.event_bus import get_event_bus
from app.utils.errors import MSG_TASK_PROCESSING_FAILED
from app.worker_limits import MemoryPressureError, _rss_mb, enforce_memory_limit
from app.workers import postprocess, stems, transcription

logger = logging.getLogger(__name__)


def _publish_progress(task, progress: int, step: str) -> None:
    """Fan out a progress event on Redis so WebSocket clients can pick
    it up in real time. Best-effort: if Redis is down we just log and
    continue --- the DB row is still the source of truth for the polling
    fallback.
    """
    get_event_bus().publish_progress(
        task.id,
        progress=progress,
        current_step=step,
        status=task.status.value if task.status else None,
    )


def _report(db, task, progress: int, step: str) -> None:
    """Commit a progress update. Kept in one helper so we don't sprinkle
    ``db.commit()`` calls throughout the pipeline --- every step ends with
    this call so the API can observe fresh state on the next /status poll.
    """
    task_service.set_progress(db, task, progress, step)
    db.commit()
    logger.info("task %s: %s%% %s", task.id, progress, step)
    _publish_progress(task, progress, step)


def _run_pipeline(
    db,
    task,
    audio_path: Path,
    output_dir: Path,
) -> None:
    """Run the pipeline as a sequence of explicit steps.

    Each step is timed and recorded as a Prometheus histogram
    observation via ``PIPELINE_STAGE_DURATION_SECONDS``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    _report(db, task, 10, "Preparing audio...")
    output_dir.mkdir(parents=True, exist_ok=True)

    _report(db, task, 30, "Separating stems...")
    _t0 = time.monotonic()
    stem_map = stems.separate_stems(audio_path, output_dir)
    PIPELINE_STAGE_DURATION_SECONDS.labels(stage="stems").observe(
        time.monotonic() - _t0
    )

    _report(db, task, 50, "Splitting instruments...")
    _t0 = time.monotonic()
    detection = postprocess.split_instruments(audio_path, output_dir, stem_map)
    PIPELINE_STAGE_DURATION_SECONDS.labels(stage="instrument_detection").observe(
        time.monotonic() - _t0
    )
    logger.info(
        "task %s: instrument detection %s",
        task.id,
        {k: round(v, 3) for k, v in detection.probabilities.items()},
    )

    _report(db, task, 72, "Transcribing to MIDI...")
    _t0 = time.monotonic()
    midi_paths = transcription.transcribe_stems_or_mix(
        audio_path, output_dir, stem_map,
    )
    PIPELINE_STAGE_DURATION_SECONDS.labels(stage="transcription").observe(
        time.monotonic() - _t0
    )
    logger.info("task %s: wrote %d midi file(s)", task.id, len(midi_paths))

    _report(db, task, 88, "Mapping GM/XG MIDI...")
    _t0 = time.monotonic()
    if not midi_paths:
        raise RuntimeError("cannot map MIDI before transcription completes")
    mapping = postprocess.map_midi(output_dir, midi_paths[0], db=db)
    PIPELINE_STAGE_DURATION_SECONDS.labels(stage="midi_mapping").observe(
        time.monotonic() - _t0
    )
    if mapping.applied_overrides:
        logger.info(
            "task %s: soundfont overrides applied: %s",
            task.id,
            [
                f"{o['stem']} -> {o['label']}"
                for o in mapping.applied_overrides
            ],
        )

    _report(db, task, 94, "Analyzing music...")
    _t0 = time.monotonic()
    analysis_path = postprocess.analyze_music(
        output_dir, detection=detection, mapping=mapping,
    )
    PIPELINE_STAGE_DURATION_SECONDS.labels(stage="analysis").observe(
        time.monotonic() - _t0
    )
    logger.info("task %s: analysis written to %s", task.id, analysis_path)

    _report(db, task, 98, "Writing commentary...")
    _t0 = time.monotonic()
    postprocess.generate_commentary(db, task, output_dir)
    PIPELINE_STAGE_DURATION_SECONDS.labels(stage="commentary").observe(
        time.monotonic() - _t0
    )

    _report(db, task, 100, "Done")

    if not stem_map:
        stems.emit_placeholder_stems(output_dir)


def process_task(task_id: int) -> None:
    """Run the pipeline for one task. Always transitions to FINISHED or FAILED.

    Raises ``MemoryPressureError`` if the pre-task memory gate is
    exceeded.  Celery (with ``acks_late=True``) will re-queue the
    message so a fresh worker child picks it up.

    Records Prometheus metrics for queue wait time, stage durations,
    failure reasons, memory peak, and total pipeline duration.
    """
    # Pre-task memory gate: refuse to start if the worker is already
    # bloated.  This is a cheap check (a few /proc reads) that
    # prevents the most common OOM scenario.
    if settings.worker_memory_gate_mb > 0:
        enforce_memory_limit(settings.worker_memory_gate_mb)

    _pipeline_start = time.monotonic()
    _peak_rss_mb = 0

    def _sample_rss() -> None:
        nonlocal _peak_rss_mb
        rss = _rss_mb()
        if rss > _peak_rss_mb:
            _peak_rss_mb = rss

    with SessionLocal() as db:
        task = task_service.get_task(db, task_id)
        if task is None:
            logger.warning("process_task: task %s not found", task_id)
            return

        try:
            # Record queue wait time: created_at -> now.
            if task.created_at is not None:
                wait_s = (
                    datetime.now(timezone.utc) - task.created_at
                ).total_seconds()
                PIPELINE_QUEUE_WAIT_SECONDS.observe(max(wait_s, 0.0))

            task_service.set_status(db, task, AudioTaskStatus.PROCESSING)
            task_service.set_progress(db, task, 0, "Starting...")
            task.error_message = None
            db.commit()

            _sample_rss()

            audio_path = file_service.get_upload_for_processing(task)
            output_dir = file_service.get_output_dir_for_processing(task)
            _run_pipeline(db, task, audio_path, output_dir)

            _sample_rss()

            # Upload results to the storage backend.  For local storage
            # this is a no-op; for S3 this uploads all output files.
            file_service.upload_results(task, output_dir)

            task_service.mark_finished(db, task, success=True)
            db.commit()
            get_event_bus().publish_task_finished(
                task.id,
                status=AudioTaskStatus.FINISHED.value,
                progress=100,
            )

            PIPELINE_TASKS_COMPLETED.labels(outcome="finished").inc()
            PIPELINE_TOTAL_DURATION_SECONDS.observe(
                time.monotonic() - _pipeline_start,
            )
            if _peak_rss_mb > 0:
                PIPELINE_MEMORY_PEAK_BYTES.observe(
                    _peak_rss_mb * 1024 * 1024,
                )
            logger.info("task %s finished", task_id)

        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("task %s failed: %s", task_id, exc)
            task = task_service.get_task(db, task_id)
            if task is not None:
                task_service.mark_finished(
                    db, task, success=False, error_message=MSG_TASK_PROCESSING_FAILED,
                )
                db.commit()
                get_event_bus().publish_task_finished(
                    task.id,
                    status=AudioTaskStatus.FAILED.value,
                    progress=0,
                    error_message=MSG_TASK_PROCESSING_FAILED,
                )

            PIPELINE_FAILURES_TOTAL.labels(
                exception_type=type(exc).__name__,
            ).inc()
            PIPELINE_TASKS_COMPLETED.labels(outcome="failed").inc()
            PIPELINE_TOTAL_DURATION_SECONDS.observe(
                time.monotonic() - _pipeline_start,
            )
            if _peak_rss_mb > 0:
                PIPELINE_MEMORY_PEAK_BYTES.observe(
                    _peak_rss_mb * 1024 * 1024,
                )
            raise


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )

    parser = argparse.ArgumentParser(description="Process a single audio task.")
    parser.add_argument("task_id", type=int, help="audio_tasks.id to process")
    args = parser.parse_args()

    try:
        process_task(args.task_id)
    except Exception:
        sys.exit(1)