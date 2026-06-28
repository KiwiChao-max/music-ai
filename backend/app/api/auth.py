"""Auth API: register, login, refresh, me, logout.

`POST /api/auth/register` creates a new account and returns the same
token pair the login endpoint would. Both are public; `GET /me` is the
canonical "who am I" probe for the SPA.
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
        user.id, email=user.email, role=user.role
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
        # 401 so the SPA can show a generic "wrong credentials" toast.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    tokens = auth_service.issue_token_pair(
        user.id, email=user.email, role=user.role
    )
    db.commit()
    return TokenResponse(user=UserPublic.model_validate(user), **tokens)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    """Trade a refresh token for a new access token (and a new refresh).

    We rotate the refresh token too: a stolen refresh token can be used
    only once before it's invalidated, which limits the blast radius.
    """
    try:
        claims = auth_service.decode_token(
            payload.refresh_token, expected_type="refresh"
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="malformed refresh token",
        ) from exc

    user = user_service.get_user(db, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user not found or inactive",
        )

    tokens = auth_service.issue_token_pair(
        user.id, email=user.email, role=user.role
    )
    user_service.touch_last_login(db, user)
    db.commit()
    return TokenResponse(user=UserPublic.model_validate(user), **tokens)


@router.get("/me", response_model=UserPublic)
def me(user: CurrentUser) -> UserPublic:
    """Return the currently authenticated user."""
    return UserPublic.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
def logout(_: CurrentUser) -> MessageResponse:
    """Stateless logout. The client just discards the tokens; this
    endpoint exists so the SPA can call it on sign-out (and to give the
    OpenAPI doc a `security: bearerAuth` line at the right place).
    """
    return MessageResponse(message="logged out")
