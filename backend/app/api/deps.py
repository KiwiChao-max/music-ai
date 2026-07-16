"""FastAPI dependencies that turn a Bearer JWT into a `User` row.

The module is the single source of truth for "is this request
authenticated?" — every protected endpoint pulls `current_user` from
here so we never accidentally forget to verify the token.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db.session import get_db
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
):
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
    return user


CurrentUser = Annotated[object, Depends(get_current_user)]


def get_current_user_optional(
    token: Annotated[str | None, Depends(
        OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)
    )],
    db: Annotated[Session, Depends(get_db)],
):
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
    return user


OptionalUser = Annotated[object, Depends(get_current_user_optional)]


def require_admin(user: CurrentUser) -> object:
    """Gate admin-only endpoints. Must be applied after `CurrentUser`."""
    if getattr(user, "role", None) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )
    return user
