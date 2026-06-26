"""Audio task worker.

Drives the full processing pipeline for one `audio_tasks` row:

    UPLOADED  ->  PROCESSING  ->  FINISHED | FAILED

`process_task(task_id)` is the only entry point callers (API, CLI, future
queue consumer) should use. It opens its own DB session so it is safe to
invoke from a thread, a worker process, or a Celery task.

The pipeline has three real steps and two cosmetic ones:

    1. Prepare audio               (probe duration, normalize, ...)
    2. Separate stems (Demucs)     [currently placeholder silent WAVs]
    3. Transcribe to MIDI (Basic   [real, runs on the original mix]
       Pitch)
    4. Finalize outputs
    5. Map to GM / XG bank         [Milestone 3+]

Step 2 still writes silent placeholders so the /stems contract keeps
working. When real Demucs is wired in, MIDI will move to step 2.5 and
transcribe each melodic stem (vocals/piano/other/...) separately.

The status / progress / current_step / commit contract is the public
contract that the API and frontend depend on — do not change those
without coordinating with `app/api/tasks.py` and the React polling code.
"""
from __future__ import annotations

import logging
import time
import wave
from pathlib import Path

from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.services import basic_pitch_service, file_service, task_service

logger = logging.getLogger(__name__)


# Stems emitted by the 4-stem Demucs model. The placeholder pipeline writes
# a tiny silent WAV per stem so the `/api/tasks/{id}/stems` contract is
# testable end-to-end. When real Demucs lands, the placeholder is replaced
# with a real audio file of the same name.
_PLACEHOLDER_STEMS: tuple[str, ...] = ("vocals", "drums", "bass", "other")


# Steps reported to the user. `delay` is the placeholder wait that the real
# AI call will eventually replace. Keep the progress numbers monotonically
# increasing and spaced so the bar actually moves during long operations.
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
    """Demucs is not wired in yet; write empty placeholder stems so /stems
    has something to return. Replaced by a real Demucs call in a later
    milestone.
    """
    for stem in _PLACEHOLDER_STEMS:
        _write_silent_wav(output_dir / f"{stem}.wav")


def _run_basic_pitch(audio_path: Path, output_dir: Path) -> Path:
    """Run Basic Pitch on the original mix and return the produced .mid path.

    The full mix is the right input for now: Demucs is still a placeholder,
    so per-stem transcription would just feed silent WAVs into the model.
    Once Demucs is real, this function will be moved into the per-stem
    branch and called once per melodic stem.

    Anything `BasicPitchService.transcribe` raises propagates up — the
    outer `process_task` will catch it, roll back, and mark the task
    FAILED with the exception message.
    """
    service = basic_pitch_service.BasicPitchService()
    result = service.transcribe(audio_path, output_dir)
    return result.midi_path


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

        # Replace the "Transcribing to MIDI..." step with the real Basic
        # Pitch call. Everything else still uses the placeholder delay so
        # the progress UX is unchanged when Demucs lands.
        if step == "Transcribing to MIDI...":
            midi_path = _run_basic_pitch(audio_path, output_dir)
            logger.info("task %s: midi written to %s", task.id, midi_path)
        else:
            time.sleep(delay)

    # Demucs placeholder — produces the silent stems the /stems endpoint
    # serves today. No-op once real Demucs is wired into the steps above.
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
