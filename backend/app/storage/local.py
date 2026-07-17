"""Local filesystem storage backend (development).

Maps logical keys to filesystem paths under a configurable root
directory.  This is the default backend for development; in production
switch to ``S3Storage`` via ``STORAGE_BACKEND=s3``.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import BinaryIO

from app.storage.base import StorageBackend

logger = logging.getLogger(__name__)


class LocalStorage(StorageBackend):
    """Direct filesystem access.

    Keys are treated as relative paths under ``root``.  For example,
    key ``uploads/42/original.wav`` maps to ``<root>/uploads/42/original.wav``.
    """

    def __init__(self, *, root: Path) -> None:
        self._root = root.resolve()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _resolve(self, key: str) -> Path:
        safe = key.lstrip("/").replace("\\", "/")
        # Prevent path traversal: resolve the combined path and verify
        # it stays under self._root.
        resolved = (self._root / safe).resolve()
        if not str(resolved).startswith(str(self._root)):
            raise ValueError(f"key {key!r} escapes root directory")
        return resolved

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def upload(self, *, local_path: Path, key: str) -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)

    def download(self, *, key: str, local_path: Path) -> None:
        source = self._resolve(key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, local_path)

    def open_read(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        path.unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        path = self._resolve(prefix)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink(missing_ok=True)

    def list_keys(self, prefix: str) -> list[str]:
        base = self._resolve(prefix)
        root_s = str(self._root)
        if not base.is_dir():
            return []
        result: list[str] = []
        for entry in sorted(base.rglob("*")):
            if entry.is_file():
                key = str(entry.resolve())[len(root_s) :].lstrip("/\\").replace("\\", "/")
                result.append(key)
        return result

    def presigned_url(self, key: str, *, expires: int = 3600) -> str:
        # Local storage doesn't support presigned URLs; return a
        # relative path that the API download endpoint can serve.
        return f"/api/tasks/files/{key}"

    def cleanup_expired(self, *, prefix: str, max_age_days: int) -> int:
        base = self._resolve(prefix)
        if not base.exists():
            return 0
        cutoff = time.time() - max_age_days * 86400
        deleted = 0
        # Walk bottom-up so we can also remove empty directories.
        for root, dirs, files in os.walk(str(base), topdown=False):
            root_path = Path(root)
            for name in files:
                file_path = root_path / name
                try:
                    if file_path.stat().st_mtime < cutoff:
                        file_path.unlink()
                        deleted += 1
                except OSError:
                    pass
            # Remove empty directories (except the prefix root itself).
            if root_path != base:
                try:
                    root_path.rmdir()
                except OSError:
                    pass
        return deleted

    def upload_dir(self, *, local_dir: Path, key_prefix: str) -> int:
        count = 0
        prefix = key_prefix.rstrip("/")
        for entry in local_dir.iterdir():
            if entry.is_file():
                key = f"{prefix}/{entry.name}"
                self.upload(local_path=entry, key=key)
                count += 1
        return count

    def download_dir(self, *, key_prefix: str, local_dir: Path) -> int:
        count = 0
        local_dir.mkdir(parents=True, exist_ok=True)
        for key in self.list_keys(key_prefix):
            local_path = local_dir / Path(key).name
            self.download(key=key, local_path=local_path)
            count += 1
        return count

    def usage_bytes(self, *, prefix: str) -> int:
        """Sum file sizes under *prefix*, returning 0 on error."""
        try:
            base = self._root / prefix
            if not base.exists():
                return 0
            total = 0
            for f in base.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
            return total
        except OSError:
            return 0