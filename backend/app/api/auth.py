"""Auth API: register, login, refresh, me, logout, csrf.

``POST /api/auth/register`` creates a new account and returns the same
token pair the login endpoint would. Both are public; ``GET /me`` is the
canonical "who am I" probe for the SPA.

Tokens
------
* **access token** --- short-lived (15 min), returned in the JSON body.
  The SPA keeps it in memory only (never localStorage).
* **refresh token** --- longer-lived (7 days), stored in an HttpOnly /
  Secure / SameSite=Strict cookie so XSS cannot exfiltrate it.
* **CSRF token** --- a separate non-HttpOnly cookie that the SPA reads
  and sends back as a ``X-CSRF-Token`` header on state-changing
  requests (POST/PUT/PATCH/DELETE).  The server compares the cookie
  value with the header value to prevent cross-site request forgery.

Refresh tokens are rotated on every use --- each refresh consumes the old
token and issues a new one.  Reuse of a consumed token (theft signal)
revokes the entire token family, forcing re-authentication.
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser
from app.config import settings
from app.db.session import get_db
from app.schemas.auth import (
    MessageResponse,
    RefreshRequest,
    UserCreate,
    UserLogin,
    UserPublic,
)
from app.services import auth_service, user_service
from app.utils.errors import log_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---- cookie helpers --------------------------------------------------------


def _refresh_cookie_settings() -> dict[str, Any]:
    """Return the kwargs for ``Response.set_cookie`` for the refresh token."""
    return {
        "key": settings.refresh_token_cookie_name,
        "httponly": True,
        "secure": settings.production_mode,
        "samesite": "strict",
        "max_age": settings.refresh_token_ttl_minutes * 60,
        "path": "/api/auth",
        "domain": settings.refresh_token_cookie_domain or None,
    }


def _set_refresh_cookie(response: Response, token: str) -> None:
    """Set the refresh token as an HttpOnly cookie."""
    kwargs = _refresh_cookie_settings()
    kwargs["value"] = token
    response.set_cookie(**kwargs)


def _clear_refresh_cookie(response: Response) -> None:
    kwargs = _refresh_cookie_settings()
    kwargs["value"] = ""
    kwargs["max_age"] = 0
    response.delete_cookie(
        settings.refresh_token_cookie_name,
        path="/api/auth",
        domain=settings.refresh_token_cookie_domain or None,
        secure=settings.production_mode,
        httponly=True,
        samesite="strict",
    )


def _get_refresh_from_cookie(request: Request) -> str | None:
    return request.cookies.get(settings.refresh_token_cookie_name)


def _set_csrf_cookie(response: Response) -> str:
    """Set a CSRF token cookie and return the token value (so the caller
    can also include it in the JSON body so the SPA can cache it).

    The CSRF cookie is NOT HttpOnly --- the SPA reads it and sends it back
    as an ``X-CSRF-Token`` header on state-changing requests."""
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        httponly=False,
        secure=settings.production_mode,
        samesite="strict",
        max_age=settings.refresh_token_ttl_minutes * 60,
        path="/",
    )
    return token


# ---- token response builder ------------------------------------------------


def _token_response(
    access_token: str,
    refresh_token: str,
    expires_in: int,
    user: UserPublic,
) -> JSONResponse:
    """Return a JSON response with the access token + user, and set the
    refresh token as an HttpOnly cookie."""
    csrf = secrets.token_urlsafe(32)
    content = {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "user": user.model_dump(mode="json"),
        "csrf_token": csrf,
    }
    resp = JSONResponse(content=content, status_code=200)
    _set_refresh_cookie(resp, refresh_token)
    resp.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf,
        httponly=False,
        secure=settings.production_mode,
        samesite="strict",
        max_age=settings.refresh_token_ttl_minutes * 60,
        path="/",
    )
    return resp


# ---- endpoints -------------------------------------------------------------


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: UserCreate,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """Create a new user and return the initial token pair."""
    try:
        user = user_service.create_user(
            db,
            email=payload.email,
            username=payload.username,
            password=payload.password,
            full_name=payload.full_name,
        )
    except user_service.EmailAlreadyExistsError as exc:
        db.rollback()
        log_error(exc, context="registration: email already in use")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except user_service.UsernameAlreadyExistsError as exc:
        db.rollback()
        log_error(exc, context="registration: username already taken")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        log_error(exc, context="registration: invalid input")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    tokens = auth_service.issue_token_pair(db, user.id, email=user.email, role=user.role)
    user_service.touch_last_login(db, user)
    db.commit()
    return _token_response(
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["expires_in"],
        UserPublic.model_validate(user),
    )


@router.post("/login")
def login(
    payload: UserLogin,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
):
    """Verify credentials and return a fresh token pair."""
    try:
        user = user_service.authenticate(
            db, identifier=payload.identifier, password=payload.password
        )
    except user_service.InvalidCredentialsError as exc:
        log_error(exc, context="login: invalid credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    tokens = auth_service.issue_token_pair(db, user.id, email=user.email, role=user.role)
    db.commit()
    return _token_response(
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["expires_in"],
        UserPublic.model_validate(user),
    )


@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    payload: RefreshRequest | None = None,
):
    """Trade a refresh token for a new access token (and a new refresh).

    The refresh token is read from (in priority order):
      1. The JSON body (for backwards-compatibility with existing clients)
      2. The HttpOnly cookie (the preferred path)

    Implements refresh-token rotation with reuse detection:
      * Each refresh token can be used **once** --- the old token is
        marked as consumed and a new one is issued.
      * If a consumed token is presented again, the entire token family
        is revoked (possible theft) and the user must re-authenticate.
    """
    raw_token = payload.refresh_token if payload and payload.refresh_token else None
    if not raw_token:
        raw_token = _get_refresh_from_cookie(request)
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token not provided",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        tokens = auth_service.rotate_refresh_token(db, raw_token)
    except auth_service.TokenReuseError as exc:
        db.commit()
        _clear_refresh_cookie(response)
        log_error(exc, context="refresh: token reuse detected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (auth_service.TokenRevokedError, auth_service.TokenNotFoundError) as exc:
        _clear_refresh_cookie(response)
        log_error(exc, context="refresh: token invalid")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Re-fetch the user so the response carries up-to-date fields.
    try:
        claims = auth_service.decode_token(tokens["access_token"], expected_type="access")
        user_id = int(claims["sub"])
    except (ValueError, KeyError, TypeError):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    user = user_service.get_user(db, user_id)
    if user is None or not user.is_active:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found or inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_service.touch_last_login(db, user)
    db.commit()
    return _token_response(
        tokens["access_token"],
        tokens["refresh_token"],
        tokens["expires_in"],
        UserPublic.model_validate(user),
    )


@router.get("/me", response_model=UserPublic)
def me(user: CurrentUser) -> UserPublic:
    """Return the currently authenticated user."""
    return UserPublic.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
def logout(
    user: CurrentUser,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    """Revoke all active refresh tokens for the current user.

    The client should discard its access token and the server will clear
    the refresh-token cookie.  Any attempt to use a revoked refresh
    token will receive a 401.
    """
    count = auth_service.revoke_all_user_tokens(db, user.id)
    db.commit()
    _clear_refresh_cookie(response)
    return MessageResponse(message=f"logged out ({count} session(s) revoked)")


@router.get("/csrf")
def get_csrf_token(
    request: Request,
    response: Response,
):
    """Return a fresh CSRF token (as both a cookie and JSON).

    The SPA calls this on mount to get a CSRF token, then sends it back
    as an ``X-CSRF-Token`` header on every state-changing request.
    """
    token = _set_csrf_cookie(response)
    return {"csrf_token": token}
