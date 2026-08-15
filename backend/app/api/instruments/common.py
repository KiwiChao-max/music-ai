"""Shared helpers for the instruments API package."""

from __future__ import annotations

from fastapi import HTTPException

from app.db.models import User

# Size / count limits shared across sub-modules.
MAX_SAMPLES_PER_LIBRARY = 80
MAX_SAMPLE_BYTES = 5 * 1024 * 1024  # 5 MB per sample
MAX_TOTAL_BYTES = 80 * 1024 * 1024  # 80 MB total per upload
MAX_SF2_BYTES = 200 * 1024 * 1024  # 200 MB per SF2 upload
MAX_CSV_BYTES = 1 * 1024 * 1024  # 1 MB per CSV preset table


def check_resource_owner(user: User | None, owner_id: int | None) -> None:
    """Raise 403 if the user does not own the resource and is not an admin.

    A resource with ``owner_id=None`` is a legacy / global resource
    that can only be managed by admins.
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if getattr(user, "role", None) == "admin":
        return
    if owner_id is None:
        raise HTTPException(
            status_code=403,
            detail="only admins can manage global resources",
        )
    if user.id != owner_id:
        raise HTTPException(
            status_code=403,
            detail="you do not own this resource",
        )
