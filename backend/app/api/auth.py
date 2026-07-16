"""Auth API: register, login, refresh, me, logout.

`POST /api/auth/register` creates a new account and returns the same
token pair the login endpoint would. Both are public; `GET /me` is the
canonical "who am I" probe for the SPA.

Refresh tokens are rotated on every use — each refresh consumes the old
token and issues a new one.  Reuse of a consumed token (theft signal)
revokes the entire token family, forcing re-authentication.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.auth import (
    MessageResponse,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserLogin,
    UserPublic,
)
from app.services import auth_service, user_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: UserCreate,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except user_service.UsernameAlreadyExistsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    tokens = auth_service.issue_token_pair(
        db, user.id, email=user.email, role=user.role
    )
    user_service.touch_last_login(db, user)
    db.commit()
    return TokenResponse(user=UserPublic.model_validate(user), **tokens)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: UserLogin,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Verify credentials and return a fresh token pair."""
    try:
        user = user_service.authenticate(
            db, identifier=payload.identifier, password=payload.password
        )
    except user_service.InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    tokens = auth_service.issue_token_pair(
        db, user.id, email=user.email, role=user.role
    )
    db.commit()
    return TokenResponse(user=UserPublic.model_validate(user), **tokens)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Trade a refresh token for a new access token (and a new refresh).

    Implements refresh-token rotation with reuse detection:
      * Each refresh token can be used **once** — the old token is
        marked as consumed and a new one is issued.
      * If a consumed token is presented again, the entire token family
        is revoked (possible theft) and the user must re-authenticate.
    """
    try:
        tokens = auth_service.rotate_refresh_token(db, payload.refresh_token)
    except auth_service.TokenReuseError as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except (auth_service.TokenRevokedError, auth_service.TokenNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Re-fetch the user so the response carries up-to-date fields.
    try:
        claims = auth_service.decode_token(
            tokens["access_token"], expected_type="access"
        )
        user_id = int(claims["sub"])
    except (ValueError, KeyError, TypeError):
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token verification failed",
            headers={"WWW-Authenticate": "Bearer"},
        )

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
    return TokenResponse(user=UserPublic.model_validate(user), **tokens)


@router.get("/me", response_model=UserPublic)
def me(user: CurrentUser) -> UserPublic:
    """Return the currently authenticated user."""
    return UserPublic.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
def logout(
    user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
) -> MessageResponse:
    """Revoke all active refresh tokens for the current user.

    The client should discard its tokens after this call.  Any attempt
    to use a revoked refresh token will receive a 401.
    """
    count = auth_service.revoke_all_user_tokens(db, user.id)
    db.commit()
    return MessageResponse(message=f"logged out ({count} session(s) revoked)")