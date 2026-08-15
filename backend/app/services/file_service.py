"""Storage helpers for audio uploads and worker outputs.

Two backends are supported (configured via ``STORAGE_BACKEND``):

* ``local`` (default) --- direct filesystem access.  Paths are real
  filesystem paths under ``STORAGE_DIR``.
* ``s3`` --- S3-compatible object storage.  Paths are logical keys;
  the API and worker download/upload at the boundaries.

Key layout (consistent across backends):

    uploads/<task_id>/original.<ext>      # raw upload
    outputs/task_<task_id>/               # worker artifacts
        vocals.wav, drums.wav, bass.wav, other.wav, *.mid

When using S3, the worker downloads the upload to a local temp
directory, processes it there, and uploads the output directory
back to S3.  This means the worker code (which uses ``Path``
objects extensively) stays unchanged; only the boundary methods
here change.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.config import settings
from app.db.models import AudioTask
from app.services.task_service import safe_filename
from app.storage import get_storage
from app.utils.errors import UploadError

logger = logging.getLogger(__name__)


class UploadTooLargeError(UploadError):
    """Raised when an upload exceeds the configured byte limit (HTTP 413)."""

    status_code = 413  # Payload Too Large
    code = "upload_too_large"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message or "Upload exceeds the allowed size limit.",
            code=self.code,
        )


# ---------------------------------------------------------------------------
# Key construction (backend-agnostic)
# ---------------------------------------------------------------------------


def _upload_key(task_id: int, filename: str) -> str:
    ext = Path(safe_filename(filename)).suffix or ".bin"
    return f"uploads/{task_id}/original{ext}"


def _upload_key_prefix(task_id: int) -> str:
    return f"uploads/{task_id}/"


def _output_key_prefix(task_id: int) -> str:
    return f"outputs/task_{task_id}/"


# ---------------------------------------------------------------------------
# Legacy path helpers (for local storage backward compatibility)
# ---------------------------------------------------------------------------


def task_upload_dir(task_id: int) -> Path:
    """Directory holding this task's raw upload (local storage only)."""
    return settings.storage_dir / "uploads" / str(task_id)


def task_output_dir(task_id: int) -> Path:
    """Directory where the worker writes outputs (local storage only)."""
    return settings.storage_dir / "outputs" / f"task_{task_id}"


def storage_path(task_id: int, filename: str) -> Path:
    """On-disk path for a stored upload (local storage only)."""
    task_dir = task_upload_dir(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(safe_filename(filename)).suffix or ".bin"
    return task_dir / f"original{ext}"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def save_upload(
    task: AudioTask,
    source_file,
    *,
    max_bytes: int | None = None,
) -> Path:
    """Stream *source_file* to storage and return a local ``Path`` for
    metadata validation (audio_validation runs immediately after).

    The file is first written to a temp file so we can count bytes
    and enforce the size limit.  After the limit is verified, the
    file is uploaded to the storage backend.

    For local storage, the temp file is the final location; for S3,
    the temp file is uploaded and the caller is responsible for
    cleanup (the temp file is deleted automatically when the context
    manager exits, but we return the path so the caller can read it
    for validation first).
    """
    limit = max_bytes if max_bytes is not None else settings.max_upload_bytes
    storage = get_storage()

    # Write to a temp file first so we can enforce the byte limit.
    # We use a persistent temp file (not TemporaryFile) because
    # soundfile needs a real path, not a file descriptor.
    suffix = Path(safe_filename(task.filename)).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115
        suffix=suffix, delete=False, dir=settings.storage_dir
    )
    tmp_path = Path(tmp.name)
    try:
        written = 0
        while True:
            chunk = source_file.read(1024 * 1024)
            if not chunk:
                break
            if limit > 0 and written + len(chunk) > limit:
                raise UploadTooLargeError(f"upload exceeds {limit} bytes")
            tmp.write(chunk)
            written += len(chunk)
        tmp.close()

        key = _upload_key(task.id, task.filename)
        if settings.storage_backend != "s3":
            # Local storage: the temp file already lives under STORAGE_DIR,
            # so `os.replace` is an instant rename. The generic upload path
            # (`shutil.copy2`) would write up to 200 MB a second time and
            # double peak disk usage for the duration of the upload.
            target = storage_path(task.id, task.filename)
            try:
                os.replace(tmp_path, target)
            except OSError:
                # Cross-device (STORAGE_DIR on a different mount): copy.
                shutil.copy2(tmp_path, target)
                tmp_path.unlink(missing_ok=True)
            logger.info(
                "save_upload: task %s, %d bytes -> %s",
                task.id,
                written,
                key,
            )
            return target

        # S3: upload the temp file and return it for metadata validation.
        storage.upload(local_path=tmp_path, key=key)

        logger.info(
            "save_upload: task %s, %d bytes -> %s",
            task.id,
            written,
            key,
        )
        return tmp_path
    except Exception:
        # Clean up the temp file on any error.
        tmp.close()
        tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# Worker boundary helpers
# ---------------------------------------------------------------------------


def get_upload_for_processing(task: AudioTask) -> Path:
    """Return a *local* ``Path`` to the uploaded file for the worker.

    For local storage this is a direct filesystem path.  For S3, the
    file is downloaded to a temp directory and the caller is responsible
    for cleaning up the temp directory after processing.
    """
    storage = get_storage()
    if settings.storage_backend == "s3":
        local_dir = _worker_temp_dir(task.id)
        local_dir.mkdir(parents=True, exist_ok=True)
        key = _upload_key(task.id, task.filename)
        local_path = local_dir / Path(key).name
        storage.download(key=key, local_path=local_path)
        return local_path
    return storage_path(task.id, task.filename)


def get_output_dir_for_processing(task: AudioTask) -> Path:
    """Return a *local* ``Path`` for the worker to write outputs into.

    For local storage, this is the conventional output directory under
    ``STORAGE_DIR``.  For S3, this is a temp directory.
    """
    if settings.storage_backend == "s3":
        out_dir = _worker_temp_dir(task.id) / "outputs"
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
    out_dir = task_output_dir(task.id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def upload_results(task: AudioTask, local_dir: Path) -> None:
    """Upload all worker output files to the storage backend.

    For local storage this is a no-op (files are already in the right
    place).  For S3, every file in *local_dir* is uploaded and the
    temp directory is deleted.
    """
    if settings.storage_backend == "s3":
        storage = get_storage()
        prefix = _output_key_prefix(task.id)
        count = storage.upload_dir(local_dir=local_dir, key_prefix=prefix)
        logger.info(
            "upload_results: task %s, %d files -> %s",
            task.id,
            count,
            prefix,
        )
        # Clean up the worker temp directory.
        import shutil

        shutil.rmtree(_worker_temp_dir(task.id), ignore_errors=True)


def _worker_temp_dir(task_id: int) -> Path:
    """Temp directory for S3 worker processing."""
    return settings.storage_dir / "tmp" / f"task_{task_id}"


# ---------------------------------------------------------------------------
# Download / listing helpers (for the API)
# ---------------------------------------------------------------------------


def list_output_files(task: AudioTask) -> list[str]:
    """Return the filenames (not full paths) of all worker output files."""
    storage = get_storage()
    prefix = _output_key_prefix(task.id)
    keys = storage.list_keys(prefix)
    return [Path(k).name for k in keys]


def list_upload_files(task: AudioTask) -> list[str]:
    """Return the filenames of the uploaded file(s)."""
    storage = get_storage()
    prefix = _upload_key_prefix(task.id)
    keys = storage.list_keys(prefix)
    return [Path(k).name for k in keys]


def upload_file_key(task: AudioTask) -> str:
    """Logical storage key for a task's original upload."""
    return _upload_key(task.id, task.filename)


def output_file_key(task: AudioTask, filename: str) -> str:
    """Logical storage key for a worker output file."""
    return f"{_output_key_prefix(task.id)}{filename}"


def open_output_file(task: AudioTask, filename: str) -> BinaryIO:
    """Open a worker output file for binary reading.

    Returns a file-like object.  The caller is responsible for closing it.
    """
    storage = get_storage()
    return storage.open_read(output_file_key(task, filename))


def open_upload_file(task: AudioTask) -> BinaryIO:
    """Open the original uploaded file for binary reading."""
    storage = get_storage()
    return storage.open_read(upload_file_key(task))


def presigned_output_url(task: AudioTask, filename: str, *, expires: int = 3600) -> str:
    """Generate a time-limited download URL for a worker output file."""
    storage = get_storage()
    return storage.presigned_url(output_file_key(task, filename), expires=expires)


def presigned_upload_url(task: AudioTask, *, expires: int = 3600) -> str:
    """Generate a time-limited download URL for the original upload."""
    storage = get_storage()
    return storage.presigned_url(upload_file_key(task), expires=expires)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def remove_task_files(task: AudioTask) -> None:
    """Delete all files for this task from the storage backend.

    Safe to call even if the task has no files (e.g. upload never
    completed, or worker never ran).
    """
    storage = get_storage()
    for prefix in (_upload_key_prefix(task.id), _output_key_prefix(task.id)):
        try:
            storage.delete_prefix(prefix)
        except Exception:
            logger.exception(
                "remove_task_files: delete_prefix failed for task %s, prefix=%s",
                task.id,
                prefix,
            )


def cleanup_expired_tasks(*, max_age_days: int) -> int:
    """Delete upload and output files older than *max_age_days* days.

    Returns the total number of objects deleted.  When using S3, prefer
    configuring a bucket lifecycle rule instead.
    """
    if max_age_days <= 0:
        return 0
    storage = get_storage()
    deleted = 0
    for prefix in ("uploads/", "outputs/"):
        try:
            deleted += storage.cleanup_expired(
                prefix=prefix,
                max_age_days=max_age_days,
            )
        except Exception:
            logger.exception("cleanup_expired_tasks: failed for prefix=%s", prefix)
    return deleted
