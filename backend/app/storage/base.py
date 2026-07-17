"""Abstract storage backend interface.

All storage backends must implement this protocol.  The API and worker
modules interact with storage exclusively through this interface, so
switching from local filesystem to S3 is a single config change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Iterator


class StorageBackend(ABC):
    """Abstract interface for file storage.

    Keys are logical path strings like ``uploads/42/original.wav`` or
    ``outputs/task_42/vocals.wav``.  The backend is responsible for
    mapping these to its own namespace (filesystem path, S3 object key,
    etc.).
    """

    @abstractmethod
    def upload(self, *, local_path: Path, key: str) -> None:
        """Upload a local file to the storage backend.

        Parameters
        ----------
        local_path:
            Path to the local file to upload.
        key:
            Logical key (path) in the storage namespace.
        """
        ...

    @abstractmethod
    def download(self, *, key: str, local_path: Path) -> None:
        """Download a file from the storage backend to a local path.

        Parameters
        ----------
        key:
            Logical key to download.
        local_path:
            Destination path.  Parent directories are created if needed.
        """
        ...

    @abstractmethod
    def open_read(self, key: str) -> BinaryIO:
        """Open a file stored under *key* for binary reading.

        The caller is responsible for closing the returned stream.
        """
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a single object by key."""
        ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        """Delete all objects whose keys start with *prefix*.

        This is used for task cleanup (``uploads/42/``, ``outputs/task_42/``).
        """
        ...

    @abstractmethod
    def list_keys(self, prefix: str) -> list[str]:
        """Return all keys under *prefix*, sorted lexicographically."""
        ...

    @abstractmethod
    def presigned_url(self, key: str, *, expires: int = 3600) -> str:
        """Generate a time-limited download URL for *key*.

        The URL is suitable for embedding in an ``<audio>`` tag or
        passing to a waveform renderer.  For local storage this is a
        file:// URL (or a relative API path); for S3 it is a presigned
        GET URL.

        Parameters
        ----------
        key:
            Logical key.
        expires:
            URL validity in seconds (default 1 hour).
        """
        ...

    @abstractmethod
    def cleanup_expired(self, *, prefix: str, max_age_days: int) -> int:
        """Delete objects under *prefix* older than *max_age_days* days.

        Returns the number of objects deleted.
        """
        ...

    # ------------------------------------------------------------------
    # Directory helpers (used by the worker to batch upload results)
    # ------------------------------------------------------------------

    @abstractmethod
    def upload_dir(self, *, local_dir: Path, key_prefix: str) -> int:
        """Upload every file under *local_dir* to ``key_prefix/<filename>``.

        Non-recursive: only immediate children of *local_dir* are uploaded.

        Returns the number of files uploaded.
        """
        ...

    @abstractmethod
    def download_dir(self, *, key_prefix: str, local_dir: Path) -> int:
        """Download every object under *key_prefix* to *local_dir*.

        Returns the number of files downloaded.
        """
        ...

    @abstractmethod
    def usage_bytes(self, *, prefix: str) -> int:
        """Return the total size in bytes of all objects under *prefix*.

        Used for the Prometheus storage-usage gauge.  Implementations
        should be cheap for local filesystems and may be a no-op for
        S3 (where bucket-level metrics are preferred).
        """
        ...