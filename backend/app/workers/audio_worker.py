"""Audio task worker.

Drives the full processing pipeline for one `audio_tasks` row:

    UPLOADED  ->  PROCESSING  ->  FINISHED | FAILED

`process_task(task_id)` is the only entry point callers (API, CLI, future
queue consumer) should use. It opens its own DB session so it is safe to
invoke from a thread, a worker process, or a Celery task.

Milestone 2 will replace the body of `_run_pipeline` with the real Demucs
+ Basic Pitch calls. The status / progress contract stays the same.
"""
from __future__ import annotations

import logging
import time
import wave
from pathlib import Path

from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.services import file_service, task_service

logger = logging.getLogger(__name__)


# Stems emitted by the 4-stem Demucs model. Milestone 2 will write real
# audio for each; the placeholder pipeline writes a tiny silent WAV so the
# `/api/tasks/{id}/stems` contract is testable end-to-end.
_PLACEHOLDER_STEMS: tuple[str, ...] = ("vocals", "drums", "bass", "other")


# Steps reported to the user. In Milestone 1 the body is a sleep, but the
# progress / current_step / commit contract is exactly what Milestone 2 will
# follow when we swap sleeps for real Demucs / Basic Pitch calls.
_PIPELINE_STEPS: list[tuple[int, str, float]] = [
    (10, "Preparing audio...", 0.4),
    (30, "Separating vocals...", 0.6),
    (55, "Separating drums...", 0.6),
    (80, "Transcribing to MIDI...", 0.6),
    (95, "Finalizing outputs...", 0.3),
]


def _write_silent_wav(path: Path, *, seconds: float = 0.1, rate: int = 8000) -> None:
    """Write a minimal valid silent mono 8-bit WAV. Used as a stem placeholder."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(b"\x80" * int(rate * seconds))


def _emit_placeholder_stems(output_dir: Path) -> None:
    """Milestone 1 only: write empty placeholder stems so /stems has something to return.

    Milestone 2 will replace this whole `_run_pipeline` body with the real
    Demucs call, which writes proper stems directly into `output_dir`.
    """
    for stem in _PLACEHOLDER_STEMS:
        _write_silent_wav(output_dir / f"{stem}.wav")


def _run_pipeline(
    db,
    task,
    audio_path: Path,
    output_dir: Path,
) -> None:
    """Run the heavy lifting and stream progress to the DB."""
    output_dir.mkdir(parents=True, exist_ok=True)

    for progress, step, delay in _PIPELINE_STEPS:
        task_service.set_progress(db, task, progress, step)
        db.commit()
        logger.info("task %s: %s%% %s", task.id, progress, step)
        time.sleep(delay)

    _emit_placeholder_stems(output_dir)


def process_task(task_id: int) -> None:
    """Run the pipeline for one task. Always transitions to FINISHED or FAILED."""
    with SessionLocal() as db:
        task = task_service.get_task(db, task_id)
        if task is None:
            logger.warning("process_task: task %s not found", task_id)
            return

        try:
            # Reset transient fields for a fresh run.
            task_service.set_status(db, task, AudioTaskStatus.PROCESSING)
            task_service.set_progress(db, task, 0, "Starting...")
            task.error_message = None
            db.commit()

            audio_path = file_service.storage_path(task.id, task.filename)
            output_dir = Path(task.output_dir) if task.output_dir else audio_path.parent
            _run_pipeline(db, task, audio_path, output_dir)

            task_service.mark_finished(db, task, success=True)
            db.commit()
            logger.info("task %s finished", task_id)

        except Exception as exc:  # noqa: BLE001 - we want to catch everything
            db.rollback()
            logger.exception("task %s failed: %s", task_id, exc)
            task = task_service.get_task(db, task_id)
            if task is not None:
                task_service.mark_finished(
                    db, task, success=False, error_message=str(exc)
                )
                db.commit()
            raise


# ---- CLI ------------------------------------------------------------------
if __name__ == "__main__":  # pragma: no cover - manual entry point
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Process a single audio task.")
    parser.add_argument("task_id", type=int, help="audio_tasks.id to process")
    args = parser.parse_args()

    try:
        process_task(args.task_id)
    except Exception:
        sys.exit(1)
