"""Pydantic schemas for the auth API.

`password` is write-only and never returned in a response. `token` fields
are returned on login / refresh and consumed by the client.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    """Payload for `POST /api/auth/register`."""

    email: EmailStr
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=128)


class UserLogin(BaseModel):
    """Payload for `POST /api/auth/login`."""

    identifier: str = Field(
        min_length=1,
        description="Email or username. The login endpoint accepts either.",
    )
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    """User record returned by the API. No password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    username: str
    full_name: str | None = None
    role: str = "user"
    is_active: bool = True
    max_tasks: int = 0
    max_upload_bytes: int = 0
    created_at: datetime
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    """Both tokens together, returned by login + refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(
        description="Access-token lifetime in seconds.",
    )
    user: UserPublic


class RefreshRequest(BaseModel):
    """Payload for `POST /api/auth/refresh`."""

    refresh_token: str | None = Field(default=None, min_length=1)


class MessageResponse(BaseModel):
    """Generic `{ "message": "..." }` envelope for endpoints that
    don't need to return data (e.g. password change ack).
    """

    message: str
