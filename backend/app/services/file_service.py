"""On-disk storage helpers for audio uploads.

Layout (everything hangs off `settings.storage_dir`):

    <storage_dir>/
        uploads/<task_id>/original.<ext>      # raw upload
        outputs/<task_id>/                     # worker artifacts
            vocals.wav, drums.wav, bass.wav, other.wav, *.mid

One directory per task means troubleshooting is a single `ls` away, and we
never have to reason about filename collisions between uploads.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from app.config import settings
from app.db.models import AudioTask
from app.services.task_service import safe_filename


def task_upload_dir(task_id: int) -> Path:
    """Directory holding this task's raw upload. Created on first use."""
    return settings.resolved_upload_dir / str(task_id)


def task_output_dir(task_id: int) -> Path:
    """Directory where the worker should write this task's outputs."""
    return settings.resolved_output_dir / f"task_{task_id}"


def storage_path(task_id: int, filename: str) -> Path:
    """On-disk path for a stored upload; mkdirs the per-task directory.

    The on-disk filename is always `original.<ext>` — the user-supplied
    `filename` is only used to pick the extension, never embedded in the path.
    That keeps paths safe (no traversal / weird characters) and predictable.
    """
    task_dir = task_upload_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(safe_filename(filename)).suffix or ".bin"
    return task_dir / f"original{ext}"


def save_upload(task: AudioTask, source_file) -> Path:
    """Stream `source_file` to disk under the task id. Caller owns the file."""
    target = storage_path(task.id, task.filename)
    with target.open("wb") as out:
        shutil.copyfileobj(source_file, out)
    return target


def remove_task_files(task: AudioTask) -> None:
    """Best-effort cleanup of this task's upload + output directories.

    Safe to call even if neither directory exists (e.g. upload never
    completed, or worker never ran).
    """
    for d in (task_upload_dir(task.id), task_output_dir(task.id)):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
