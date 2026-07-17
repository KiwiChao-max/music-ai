"""S3-compatible object storage backend (production).

Uses ``boto3`` to talk to AWS S3, MinIO, or any S3-compatible service.
Lifecycle policies (auto-delete old objects) should be configured on
the bucket/provider side, not in application code.

Dependency
----------
Add ``boto3`` to your requirements when using this backend:

    pip install boto3

If ``boto3`` is not installed, importing this module will raise an
``ImportError`` with a helpful message.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import BinaryIO

from app.storage.base import StorageBackend

logger = logging.getLogger(__name__)


class S3Storage(StorageBackend):
    """S3-compatible object storage.

    Parameters
    ----------
    endpoint_url:
        S3 endpoint URL (required for MinIO, optional for AWS).
    access_key / secret_key:
        Credentials.  When using IAM roles (EC2/ECS), leave both empty
        and boto3 will auto-discover credentials from the instance
        metadata service.
    bucket:
        Bucket name.  Must already exist.
    region:
        AWS region (ignored by MinIO but required for signature v4).
    """

    _MAX_KEYS = 1000

    def __init__(
        self,
        *,
        endpoint_url: str = "",
        access_key: str = "",
        secret_key: str = "",
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for S3 storage. Install it with: pip install boto3"
            ) from exc

        self._bucket = bucket
        kwargs: dict = {}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key
        kwargs["region_name"] = region

        self._client = boto3.client("s3", **kwargs)

        # Verify the bucket exists (fail-fast on startup).
        try:
            self._client.head_bucket(Bucket=bucket)
        except Exception as exc:
            logger.warning(
                "S3 bucket %r check failed: %s.  Ensure the bucket exists.",
                bucket, exc,
            )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _sanitize_key(self, key: str) -> str:
        return key.lstrip("/").replace("\\", "/")

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def upload(self, *, local_path: Path, key: str) -> None:
        self._client.upload_file(
            Filename=str(local_path),
            Bucket=self._bucket,
            Key=self._sanitize_key(key),
        )

    def download(self, *, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(
            Bucket=self._bucket,
            Key=self._sanitize_key(key),
            Filename=str(local_path),
        )

    def open_read(self, key: str) -> BinaryIO:
        # boto3 get_object returns a StreamingBody; wrap it in a
        # BytesIO-like interface for compatibility.
        import io

        resp = self._client.get_object(
            Bucket=self._bucket,
            Key=self._sanitize_key(key),
        )
        return io.BytesIO(resp["Body"].read())

    def delete(self, key: str) -> None:
        self._client.delete_object(
            Bucket=self._bucket,
            Key=self._sanitize_key(key),
        )

    def delete_prefix(self, prefix: str) -> None:
        safe = self._sanitize_key(prefix)
        # S3 delete_objects is limited to 1000 keys per call; paginate.
        while True:
            resp = self._client.list_objects_v2(
                Bucket=self._bucket,
                Prefix=safe,
                MaxKeys=self._MAX_KEYS,
            )
            contents = resp.get("Contents", [])
            if not contents:
                break
            self._client.delete_objects(
                Bucket=self._bucket,
                Delete={
                    "Objects": [{"Key": obj["Key"]} for obj in contents],
                    "Quiet": True,
                },
            )
            if not resp.get("IsTruncated"):
                break

    def list_keys(self, prefix: str) -> list[str]:
        safe = self._sanitize_key(prefix)
        keys: list[str] = []
        continuation_token: str | None = None
        while True:
            kwargs: dict = {
                "Bucket": self._bucket,
                "Prefix": safe,
                "MaxKeys": self._MAX_KEYS,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            resp = self._client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []):
                keys.append(obj["Key"])
            if not resp.get("IsTruncated"):
                break
            continuation_token = resp.get("NextContinuationToken")
        return sorted(keys)

    def presigned_url(self, key: str, *, expires: int = 3600) -> str:
        return self._client.generate_presigned_url(
            ClientMethod="get_object",
            Params={
                "Bucket": self._bucket,
                "Key": self._sanitize_key(key),
            },
            ExpiresIn=expires,
        )

    def cleanup_expired(self, *, prefix: str, max_age_days: int) -> int:
        """Delete objects older than *max_age_days*.

        Note: prefer configuring a bucket lifecycle policy on the S3
        provider side.  This method is a fallback for environments that
        cannot set lifecycle rules (e.g. shared MinIO instances).
        """
        safe = self._sanitize_key(prefix)
        cutoff = time.time() - max_age_days * 86400
        deleted = 0
        continuation_token: str | None = None
        while True:
            kwargs: dict = {
                "Bucket": self._bucket,
                "Prefix": safe,
                "MaxKeys": self._MAX_KEYS,
            }
            if continuation_token:
                kwargs["ContinuationToken"] = continuation_token
            resp = self._client.list_objects_v2(**kwargs)
            to_delete: list[dict] = []
            for obj in resp.get("Contents", []):
                if obj["LastModified"].timestamp() < cutoff:
                    to_delete.append({"Key": obj["Key"]})
            if to_delete:
                self._client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": to_delete, "Quiet": True},
                )
                deleted += len(to_delete)
            if not resp.get("IsTruncated"):
                break
            continuation_token = resp.get("NextContinuationToken")
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
            filename = Path(key).name
            local_path = local_dir / filename
            self.download(key=key, local_path=local_path)
            count += 1
        return count

    def usage_bytes(self, *, prefix: str) -> int:
        """S3 usage is expensive to compute; use bucket-level metrics instead."""
        return 0