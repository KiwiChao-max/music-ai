"""AudioTask CRUD + status transitions.

Keeps all DB-facing logic in one module so the API layer, the worker, and any
future background jobs share a single source of truth.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import AudioTask, AudioTaskStatus


def safe_filename(name: str) -> str:
    """Keep only the basename and strip path separators to avoid escapes."""
    return Path(name).name or "upload.bin"


# ---- reads ----------------------------------------------------------------
def list_tasks(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    user_id: int | None = None,
    public_only: bool = False,
) -> list[AudioTask]:
    """Return tasks, newest first. When `user_id` is set, filter the
    list to that user (used by non-admin endpoints to scope to "my
    tasks"). `None` means "no filter" (admin view). When
    `public_only` is True, only tasks with ``user_id IS NULL`` are
    returned (used for anonymous callers in open-auth mode).
    """
    stmt = select(AudioTask)
    if user_id is not None:
        stmt = stmt.where(AudioTask.user_id == user_id)
    elif public_only:
        stmt = stmt.where(AudioTask.user_id.is_(None))
    stmt = (
        stmt.order_by(AudioTask.created_at.desc(), AudioTask.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt).all())


def get_task(db: Session, task_id: int) -> AudioTask | None:
    return db.get(AudioTask, task_id)


def count_tasks_by_status(db: Session) -> dict[str, int]:
    """Return `{status.value: count}` for every status, filling missing
    statuses with 0 so the metrics endpoint can publish a stable
    label set."""
    stmt = select(AudioTask.status, func.count(AudioTask.id)).group_by(
        AudioTask.status
    )
    counts = {status.value: 0 for status in AudioTaskStatus}
    for status_value, count in db.execute(stmt).all():
        counts[status_value.value] = int(count)
    return counts


# ---- writes ---------------------------------------------------------------
def create_task(
    db: Session, filename: str, *, user_id: int | None = None
) -> AudioTask:
    task = AudioTask(
        filename=safe_filename(filename),
        status=AudioTaskStatus.UPLOADED,
        user_id=user_id,
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
