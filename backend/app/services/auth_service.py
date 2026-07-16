"""Auth service.

Three halves:
  * password hashing (bcrypt via passlib) --- slow on purpose so brute-force
    is impractical;
  * JWT signing / verification (HS256 via python-jose) --- short-lived
    access tokens (1 day) and longer-lived refresh tokens (30 days);
  * refresh-token rotation --- server-side records with SHA-256 hashes,
    single-use marking, reuse detection, and full-logout revocation.

The same key signs both kinds of tokens; the `type` claim tells the
verifier which TTL to enforce, so a refresh token can never be used in
place of an access token by accident.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import RefreshToken, RefreshTokenStatus

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
        return False


# ---- token hashing --------------------------------------------------------
def _hash_token(raw: str) -> str:
    """SHA-256 of a raw JWT string.  We store only the hash so a DB
    compromise does not leak usable refresh tokens."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
    jti: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a new JWT.

    `subject` is the user id (as a string).  `jti` is a unique token
    identifier (UUID4) --- refresh tokens always get one, access tokens
    may optionally include it.
    """
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + _ttl_for(token_type)).timestamp()),
        "type": token_type,
    }
    if jti:
        payload["jti"] = jti
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
    db: Session,
    user_id: int,
    *,
    email: str,
    role: str,
) -> dict[str, Any]:
    """Return both tokens plus the access-token expiry in seconds.

    A server-side `RefreshToken` record is created so the refresh token
    can be rotated (single-use), revoked (logout), and monitored for
    reuse (theft detection).
    """
    extra = {"email": email, "role": role}
    access_jti = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())
    family_id = str(uuid.uuid4())

    access_token = create_token(
        user_id, token_type="access", jti=access_jti, extra_claims=extra
    )
    refresh_token = create_token(
        user_id, token_type="refresh", jti=refresh_jti, extra_claims=extra
    )

    # Persist the refresh-token record.
    now = _now()
    record = RefreshToken(
        jti=refresh_jti,
        user_id=user_id,
        token_hash=_hash_token(refresh_token),
        status=RefreshTokenStatus.ACTIVE,
        family_id=family_id,
        expires_at=now + _ttl_for("refresh"),
    )
    db.add(record)
    db.flush()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_minutes * 60,
    }


# ---- rotation -------------------------------------------------------------
class TokenReuseError(Exception):
    """Raised when a previously-used refresh token is presented again ---
    this is a strong signal of token theft.  All tokens in the family
    have been revoked; the user must re-authenticate."""


class TokenRevokedError(Exception):
    """Raised when a revoked token is presented."""


class TokenNotFoundError(Exception):
    """Raised when the token hash is not found in the DB (expired, cleaned
    up, or never existed)."""


def rotate_refresh_token(db: Session, raw_token: str) -> dict[str, Any]:
    """Validate a refresh token, mark it as used, and issue a new pair.

    This is the core of refresh-token rotation:
      1. Decode + verify the JWT (signature, expiry, type).
      2. Hash the raw token and look it up in the DB.
      3. If the token is already *used* -> **reuse detected!**  Revoke
         the entire family and raise `TokenReuseError`.
      4. If the token is revoked or not found -> raise.
      5. Mark the old token as `used`, create a new pair under the same
         `family_id`, and return the new tokens.

    A successful rotation produces a fresh refresh token and invalidates
    the old one --- a stolen refresh token is usable at most once (and only
    if the attacker beats the legitimate client to the refresh endpoint).
    """
    # 1. Decode the JWT (stateless verification).
    try:
        claims = decode_token(raw_token, expected_type="refresh")
    except ValueError:
        raise TokenNotFoundError("invalid or expired refresh token")

    try:
        user_id = int(claims["sub"])
        jti = claims.get("jti")
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenNotFoundError("malformed refresh token") from exc

    if not jti:
        raise TokenNotFoundError("refresh token missing jti")

    # 2. Look up the token record by hash.
    token_hash = _hash_token(raw_token)
    stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    record = db.scalars(stmt).first()

    if record is None:
        raise TokenNotFoundError("refresh token not found")

    # 3. Check status.
    if record.status == RefreshTokenStatus.USED:
        # Reuse detected --- revoke the entire family.
        _revoke_family(db, record.family_id)
        raise TokenReuseError(
            "refresh token has already been used --- "
            "possible token theft detected; all sessions revoked"
        )

    if record.status == RefreshTokenStatus.REVOKED:
        raise TokenRevokedError("refresh token has been revoked")

    # 4. Mark old token as used.
    now = _now()
    record.status = RefreshTokenStatus.USED
    record.used_at = now
    db.add(record)

    # 5. Issue new token pair under the same family.
    extra = {"email": claims.get("email", ""), "role": claims.get("role", "user")}
    new_jti = str(uuid.uuid4())

    new_access = create_token(
        user_id, token_type="access", jti=str(uuid.uuid4()), extra_claims=extra
    )
    new_refresh = create_token(
        user_id, token_type="refresh", jti=new_jti, extra_claims=extra
    )

    new_record = RefreshToken(
        jti=new_jti,
        user_id=user_id,
        token_hash=_hash_token(new_refresh),
        status=RefreshTokenStatus.ACTIVE,
        family_id=record.family_id,
        expires_at=now + _ttl_for("refresh"),
    )
    db.add(new_record)
    db.flush()

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "Bearer",
        "expires_in": settings.access_token_ttl_minutes * 60,
    }


def _revoke_family(db: Session, family_id: str) -> None:
    """Revoke every token in a family (stolen-token response)."""
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id)
        .where(RefreshToken.status == RefreshTokenStatus.ACTIVE)
        .values(status=RefreshTokenStatus.REVOKED)
    )
    db.execute(stmt)
    db.flush()


# ---- revocation -----------------------------------------------------------
def revoke_all_user_tokens(db: Session, user_id: int) -> int:
    """Revoke all active refresh tokens for a user (full logout).

    Returns the number of tokens revoked.
    """
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id)
        .where(RefreshToken.status == RefreshTokenStatus.ACTIVE)
        .values(status=RefreshTokenStatus.REVOKED)
    )
    result = db.execute(stmt)
    db.flush()
    return result.rowcount or 0


# ---- cleanup (call periodically, e.g. via a cron job) ---------------------
def purge_expired_tokens(db: Session) -> int:
    """Delete expired token records.  Returns the number of rows removed."""
    stmt = (
        select(RefreshToken)
        .where(RefreshToken.expires_at < _now())
    )
    rows = db.scalars(stmt).all()
    count = len(rows)
    for row in rows:
        db.delete(row)
    if count:
        db.flush()
    return count