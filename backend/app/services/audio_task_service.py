"""Business logic for audio tasks.

The API layer is intentionally thin: it parses HTTP, calls this service, and
returns Pydantic models. Everything that touches the database or filesystem
lives here so it can be reused (e.g. by background workers in Milestone 2).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AudioTask, AudioTaskStatus


def _safe_filename(name: str) -> str:
    """Keep only the basename and strip path separators to avoid escapes."""
    return Path(name).name or "upload.bin"


def storage_path(task_id: int, filename: str) -> Path:
    """Compute the on-disk path for a stored upload and ensure the dir exists."""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    return settings.upload_dir / f"{task_id}_{_safe_filename(filename)}"


# ---- reads ----------------------------------------------------------------
def list_tasks(db: Session) -> list[AudioTask]:
    stmt = select(AudioTask).order_by(AudioTask.created_at.desc(), AudioTask.id.desc())
    return list(db.scalars(stmt).all())


def get_task(db: Session, task_id: int) -> AudioTask | None:
    return db.get(AudioTask, task_id)


# ---- writes ---------------------------------------------------------------
def create_task(db: Session, filename: str) -> AudioTask:
    task = AudioTask(
        filename=_safe_filename(filename),
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


# ---- file storage ---------------------------------------------------------
def save_upload(task: AudioTask, source_file) -> Path:
    """Stream `source_file` to disk under the task id. Caller owns the file."""
    target = storage_path(task.id, task.filename)
    with target.open("wb") as out:
        shutil.copyfileobj(source_file, out)
    return target


def remove_upload(task: AudioTask) -> None:
    target = storage_path(task.id, task.filename)
    if target.exists():
        target.unlink()
