"""Tests for `app.services.file_service`.

Covers the upload size enforcement (regression for the "write-before-check"
bug) and the directory layout that the worker relies on.
"""
from __future__ import annotations

import io
from pathlib import Path

from app.db.models import AudioTask
from app.services import file_service


def _make_task(task_id: int = 1, filename: str = "song.wav") -> AudioTask:
    """Build an AudioTask without touching the DB --- file_service only reads
    the `id` and `filename` attributes."""
    return AudioTask(id=task_id, filename=filename)


def test_storage_path_lays_out_per_task(storage_dir: Path) -> None:
    path = file_service.storage_path(42, "song.mp3")
    assert path == storage_dir / "uploads" / "42" / "original.mp3"
    # The directory must exist by the time the function returns.
    assert path.parent.is_dir()


def test_storage_path_falls_back_to_bin_extension(storage_dir: Path) -> None:
    path = file_service.storage_path(1, "no-extension")
    assert path.suffix == ".bin"


def test_save_upload_streams_under_the_size_limit(storage_dir: Path) -> None:
    task = _make_task(filename="song.wav")
    body = io.BytesIO(b"a" * 2048)
    path = file_service.save_upload(task, body, max_bytes=10_000)
    assert path.read_bytes() == b"a" * 2048


def test_save_upload_rejects_oversize_payload_before_writing_full_chunk(
    storage_dir: Path,
) -> None:
    """Regression: previously the size check happened *after* the offending
    chunk was written, so the on-disk file could be up to one full chunk
    (~1 MiB) over the limit. Now we check first and never go over.
    """
    task = _make_task(filename="song.wav")
    # Pick a limit that falls mid-chunk (1 MiB chunks), so the bug would be
    # visible as ~1 MiB of overflow on disk.
    limit = 1500
    body = io.BytesIO(b"x" * 4096)

    raised: Exception | None = None
    try:
        file_service.save_upload(task, body, max_bytes=limit)
    except file_service.UploadTooLargeError as exc:
        raised = exc

    assert raised is not None
    assert "exceeds" in str(raised)
    # The partial file must be cleaned up --- the upload directory should be
    # empty after the rejection.
    upload_dir = file_service.task_upload_dir(task.id)
    assert upload_dir.exists()
    assert list(upload_dir.iterdir()) == []


def test_save_upload_honors_limit_zero_to_disable(storage_dir: Path) -> None:
    task = _make_task(filename="song.wav")
    # limit == 0 means "no limit" per the implementation.
    path = file_service.save_upload(task, io.BytesIO(b"a" * 1000), max_bytes=0)
    assert path.read_bytes() == b"a" * 1000


def test_remove_task_files_is_safe_when_nothing_exists(storage_dir: Path) -> None:
    task = _make_task()
    # Neither upload nor output dirs exist; this must not raise.
    file_service.remove_task_files(task)


def test_remove_task_files_cleans_both_directories(storage_dir: Path) -> None:
    task = _make_task()
    upload = file_service.task_upload_dir(task.id)
    output = file_service.task_output_dir(task.id)
    upload.mkdir(parents=True)
    output.mkdir(parents=True)
    (upload / "original.wav").write_bytes(b"x")
    (output / "drums.wav").write_bytes(b"y")

    file_service.remove_task_files(task)
    assert not upload.exists()
    assert not output.exists()
