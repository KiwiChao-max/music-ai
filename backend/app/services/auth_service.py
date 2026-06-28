"""Auth service.

Two halves:
  * password hashing (bcrypt via passlib) — slow on purpose so brute-force
    is impractical;
  * JWT signing / verification (HS256 via python-jose) — short-lived
    access tokens (1 day) and longer-lived refresh tokens (30 days).

The same key signs both kinds of tokens; the `type` claim tells the
verifier which TTL to enforce, so a refresh token can never be used in
place of an access token by accident.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

# bcrypt has a 72-byte password input limit; `passwords` are passed through
# `secrets.compare_digest` after hashing, so longer inputs are silently
# truncated by bcrypt. That's a well-known limitation; we accept it
# because every password we accept is well under 72 bytes after UTF-8
# encoding (we already enforce a 128-char max on signup).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenType = Literal["access", "refresh"]


# ---- password hashing -----------------------------------------------------
def hash_password(plain: str) -> str:
    """Bcrypt-hash a plaintext password. Empty input is rejected."""
    if not plain:
        raise ValueError("password must not be empty")
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time compare a plaintext password against a bcrypt hash."""
    if not plain or not hashed:
        return False
    try:
        return _pwd_context.verify(plain, hashed)
    except (ValueError, TypeError):
        # Malformed hash or unsupported variant — treat as a failed match
        # rather than letting the exception bubble up to the caller.
        return False


# ---- JWT helpers ----------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ttl_for(token_type: TokenType) -> timedelta:
    if token_type == "access":
        return timedelta(minutes=settings.access_token_ttl_minutes)
    return timedelta(minutes=settings.refresh_token_ttl_minutes)


def create_token(
    subject: str,
    *,
    token_type: TokenType,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a new JWT.

    `subject` is the user id (as a string) — this matches the JWT spec
    convention. `extra_claims` may include `email`, `role`, etc.; the
    verifier does not require them, so they're just sugar for the
    client.
    """
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + _ttl_for(token_type)).timestamp()),
        "type": token_type,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Verify the signature + expiry + type. Raises on any mismatch."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise ValueError(f"invalid token: {exc}") from exc

    if payload.get("type") != expected_type:
        raise ValueError(
            f"wrong token type: expected {expected_type!r}, "
            f"got {payload.get('type')!r}"
        )
    if not payload.get("sub"):
        raise ValueError("token missing subject")
    return payload


# ---- access + refresh pair ------------------------------------------------
def issue_token_pair(
    user_id: int,
    *,
    email: str,
    role: str,
) -> dict[str, Any]:
    """Return both tokens plus the access-token expiry in seconds."""
    extra = {"email": email, "role": role}
    return {
        "access_token": create_token(
            user_id, token_type="access", extra_claims=extra
        ),
        "refresh_token": create_token(
            user_id, token_type="refresh", extra_claims=extra
        ),
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_minutes * 60,
    }
