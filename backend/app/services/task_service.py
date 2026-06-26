"""AudioTask CRUD + status transitions.

Keeps all DB-facing logic in one module so the API layer, the worker, and any
future background jobs share a single source of truth.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import AudioTask, AudioTaskStatus


def safe_filename(name: str) -> str:
    """Keep only the basename and strip path separators to avoid escapes."""
    return Path(name).name or "upload.bin"


# ---- reads ----------------------------------------------------------------
def list_tasks(db: Session, *, limit: int = 100, offset: int = 0) -> list[AudioTask]:
    stmt = (
        select(AudioTask)
        .order_by(AudioTask.created_at.desc(), AudioTask.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt).all())


def get_task(db: Session, task_id: int) -> AudioTask | None:
    return db.get(AudioTask, task_id)


# ---- writes ---------------------------------------------------------------
def create_task(db: Session, filename: str) -> AudioTask:
    task = AudioTask(
        filename=safe_filename(filename),
        status=AudioTaskStatus.UPLOADED,
    )
    db.add(task)
    db.flush()  # populate task.id without committing
    return task


def delete_task(db: Session, task_id: int) -> AudioTask | None:
    task = db.get(AudioTask, task_id)
    if task is not None:
        db.delete(task)
        db.flush()
    return task


def set_status(
    db: Session, task: AudioTask, status: AudioTaskStatus
) -> None:
    task.status = status
    db.add(task)
    db.flush()


def claim_for_processing(db: Session, task_id: int) -> AudioTask | None:
    """Atomically flip UPLOADED/FAILED -> PROCESSING.

    Returns the task on success, or `None` if the task does not exist OR is
    already in a terminal-running state (PROCESSING / FINISHED). The atomic
    UPDATE prevents two concurrent POST /process calls from both spawning a
    worker for the same task.
    """
    stmt = (
        update(AudioTask)
        .where(
            AudioTask.id == task_id,
            AudioTask.status.in_(
                [AudioTaskStatus.UPLOADED, AudioTaskStatus.FAILED]
            ),
        )
        .values(status=AudioTaskStatus.PROCESSING)
        .returning(AudioTask)
    )
    task = db.execute(stmt).scalar_one_or_none()
    if task is not None:
        db.commit()
        db.refresh(task)
    return task


def set_progress(
    db: Session,
    task: AudioTask,
    progress: int,
    current_step: str | None = None,
) -> None:
    """Update the progress bar + human-readable step label. Clamped 0-100."""
    task.progress = max(0, min(100, int(progress)))
    if current_step is not None:
        task.current_step = current_step
    db.add(task)
    db.flush()


def set_duration(db: Session, task: AudioTask, duration: float | None) -> None:
    task.duration = duration
    db.add(task)
    db.flush()


def set_output_dir(db: Session, task: AudioTask, output_dir: str | None) -> None:
    task.output_dir = output_dir
    db.add(task)
    db.flush()


def mark_finished(
    db: Session,
    task: AudioTask,
    success: bool,
    error_message: str | None = None,
) -> None:
    """Move to FINISHED (or FAILED with error) and stamp `finished_at`."""
    from datetime import datetime, timezone

    task.status = AudioTaskStatus.FINISHED if success else AudioTaskStatus.FAILED
    task.progress = 100 if success else task.progress
    task.error_message = None if success else error_message
    task.finished_at = datetime.now(timezone.utc)
    db.add(task)
    db.flush()
