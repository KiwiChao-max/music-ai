"""Storage backend abstraction.

Two backends are provided:

* ``LocalStorage`` --- direct filesystem access (development).
* ``S3Storage`` --- S3-compatible object storage (production, MinIO, AWS S3).

The backend is selected via ``STORAGE_BACKEND`` (``local`` or ``s3``).
All configuration lives under the ``STORAGE_*`` prefix in ``config.py``.

Lifecycle policy
----------------
When using ``S3Storage``, configure a bucket lifecycle rule on the
storage provider side to auto-delete objects older than N days.  For
local development, the ``cleanup_expired`` function can be called
periodically (e.g. via a cron-like Celery task) to prune old task
directories.
"""
from __future__ import annotations

from app.storage.base import StorageBackend
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage

__all__ = [
    "StorageBackend",
    "LocalStorage",
    "S3Storage",
    "get_storage",
]


def get_storage() -> StorageBackend:
    """Return the configured storage backend singleton.

    The backend is chosen once at import time based on
    ``settings.storage_backend``.  Call this from any module that needs
    to read or write files.
    """
    from app.config import settings

    if settings.storage_backend == "s3":
        return S3Storage(
            endpoint_url=settings.storage_s3_endpoint_url,
            access_key=settings.storage_s3_access_key,
            secret_key=settings.storage_s3_secret_key,
            bucket=settings.storage_s3_bucket,
            region=settings.storage_s3_region,
        )
    return LocalStorage(root=settings.storage_dir)