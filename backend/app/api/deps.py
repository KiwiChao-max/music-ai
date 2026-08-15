"""FastAPI dependencies that turn a Bearer JWT into a `User` row.

The module is the single source of truth for "is this request
authenticated?" --- every protected endpoint pulls `current_user` from
here so we never accidentally forget to verify the token.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import User
from app.db.session import get_db
from app.logging_config import set_user_id
from app.services import auth_service, user_service

# `tokenUrl` only matters for the auto-generated Swagger UI; the
# endpoint we expose for login is `/api/auth/login`.
_oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/login",
    auto_error=True,
)


def _credentials_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str, Depends(_oauth2_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    """Resolve the access-token bearer into a `User` row.

    Raises 401 on missing / invalid / expired / wrong-type token, or
    when the referenced user has been deactivated. 403 is reserved
    for permission checks (`require_admin` below).
    """
    try:
        payload = auth_service.decode_token(token, expected_type="access")
    except ValueError as exc:
        raise _credentials_error(str(exc)) from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _credentials_error("malformed token") from exc

    user = user_service.get_user(db, user_id)
    if user is None or not user.is_active:
        raise _credentials_error("user not found or inactive")
    set_user_id(user.id)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_user_optional(
    token: Annotated[
        str | None, Depends(OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False))
    ],
    db: Annotated[Session, Depends(get_db)],
) -> User | None:
    """Like `get_current_user` but returns `None` instead of raising when
    no token is presented. Used for endpoints that work both with and
    without auth (e.g. listing public tasks).
    """
    if not token:
        return None
    try:
        payload = auth_service.decode_token(token, expected_type="access")
    except ValueError:
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    user = user_service.get_user(db, user_id)
    if user is None or not user.is_active:
        return None
    set_user_id(user.id)
    return user


OptionalUser = Annotated[User | None, Depends(get_current_user_optional)]


def require_admin(user: CurrentUser) -> User:
    """Gate admin-only endpoints. Must be applied after `CurrentUser`."""
    if getattr(user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user


# ---- optional auth gate ---------------------------------------------------
# The three callers below share the same "enforce auth when
# settings.auth_required is True" contract.  Centralising them here
# makes it impossible for a new endpoint to accidentally land without
# an auth check.


def require_auth_or_none(user: OptionalUser) -> User | None:
    """Optional auth gate used by endpoints that work both with and
    without authentication (upload, list, etc.).

    When ``settings.auth_required`` is True (production), a missing or
    invalid token raises 401.  In dev mode (auth_required=False),
    anonymous access is allowed and ``user`` will be ``None``.
    """
    if settings.auth_required and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


OptionalAuthUser = Annotated[User | None, Depends(require_auth_or_none)]


def check_task_ownership(
    task: object,
    user: OptionalUser,
) -> None:
    """Raise 403 if the user does not own the task and is not an admin.

    ``task`` is expected to have a ``user_id`` attribute.  When
    ``task.user_id`` is ``None``, the task is considered public
    (anonymous upload in dev mode) and any authenticated non-admin
    user is denied access.
    """
    if user is None:
        return  # anonymous access --- caller decides what to allow
    if getattr(user, "role", None) == "admin":
        return
    task_user_id = getattr(task, "user_id", None)
    if task_user_id is not None and task_user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not your task",
        )
    if task_user_id is None and user is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not your task",
        )
