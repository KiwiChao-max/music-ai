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
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import RefreshToken, RefreshTokenStatus
from app.utils.errors import AuthError

# bcrypt has a 72-byte password input limit; `passwords` are passed through
# `secrets.compare_digest` after hashing, so longer inputs are silently
# truncated by bcrypt. That's a well-known limitation; we accept it
# because every password we accept is well under 72 bytes after UTF-8
# encoding (we already enforce a 128-char max on signup).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

TokenType = Literal["access", "refresh", "download"]

# Download-token TTL is short (5 minutes) because it is embedded in URLs
# for <audio>/<video> elements that cannot set Authorization headers.
# A leaked URL in logs/Referer/history expires quickly.
DOWNLOAD_TOKEN_TTL_SECONDS = 300


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
    return datetime.now(UTC)


def _ttl_for(token_type: TokenType) -> timedelta:
    if token_type == "access":
        return timedelta(minutes=settings.access_token_ttl_minutes)
    return timedelta(minutes=settings.refresh_token_ttl_minutes)


def create_token(
    subject: str | int,
    *,
    token_type: TokenType,
    jti: str | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a new JWT.

    `subject` is the user id (as a string, or an int that gets stringified
    into the ``sub`` claim).  `jti` is a unique token identifier (UUID4) ---
    refresh tokens always get one, access tokens may optionally include it.
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
    return cast(str, jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def decode_token(token: str, *, expected_type: TokenType) -> dict[str, Any]:
    """Verify the signature + expiry + type. Raises on any mismatch."""
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise ValueError(f"invalid token: {exc}") from exc

    if payload.get("type") != expected_type:
        raise ValueError(
            f"wrong token type: expected {expected_type!r}, got {payload.get('type')!r}"
        )
    if not payload.get("sub"):
        raise ValueError("token missing subject")
    return payload


# ---- download tokens (short-lived scoped URLs for media elements) ---------


def create_download_token(
    user_id: int,
    task_id: int,
    *,
    scope: str,
    filename: str,
) -> str:
    """Create a short-lived, file-scoped token for media downloads.

    Used to authenticate <audio>/<video> element requests that cannot set
    the Authorization header. The token is scoped to a single task, scope
    (upload/output), and filename, and expires after 5 minutes. It is NOT
    stored server-side; integrity + authenticity are guaranteed by HMAC.
    """
    now = _now()
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + DOWNLOAD_TOKEN_TTL_SECONDS,
        "type": "download",
        "task_id": task_id,
        "scope": scope,
        "filename": filename,
    }
    return cast(str, jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm))


def verify_download_token(
    token: str,
    *,
    task_id: int,
    scope: str,
    filename: str,
) -> int:
    """Verify a download token and return the user_id.

    Raises ValueError if the token is invalid, expired, or doesn't match
    the requested task/scope/filename.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise ValueError(f"invalid download token: {exc}") from exc

    if payload.get("type") != "download":
        raise ValueError("wrong token type for download")
    if payload.get("task_id") != task_id:
        raise ValueError("download token task mismatch")
    if payload.get("scope") != scope:
        raise ValueError("download token scope mismatch")
    if payload.get("filename") != filename:
        raise ValueError("download token filename mismatch")

    sub = payload.get("sub")
    if not sub:
        raise ValueError("download token missing subject")
    return int(sub)


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

    access_token = create_token(user_id, token_type="access", jti=access_jti, extra_claims=extra)
    refresh_token = create_token(user_id, token_type="refresh", jti=refresh_jti, extra_claims=extra)

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
class TokenReuseError(AuthError):
    """Raised when a previously-used refresh token is presented again ---
    this is a strong signal of token theft.  All tokens in the family
    have been revoked; the user must re-authenticate."""

    code = "token_reuse"
    message = "Session expired. Please log in again."


class TokenRevokedError(AuthError):
    """Raised when a revoked token is presented."""

    code = "token_revoked"
    message = "Session expired. Please log in again."


class TokenNotFoundError(AuthError):
    """Raised when the token hash is not found in the DB (expired, cleaned
    up, or never existed)."""

    code = "token_not_found"
    message = "Session expired. Please log in again."


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
    except ValueError as e:
        raise TokenNotFoundError("invalid or expired refresh token") from e

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
    new_refresh = create_token(user_id, token_type="refresh", jti=new_jti, extra_claims=extra)

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
    result = cast(CursorResult, db.execute(stmt))
    db.flush()
    return result.rowcount or 0


# ---- cleanup (call periodically, e.g. via a cron job) ---------------------
def purge_expired_tokens(db: Session) -> int:
    """Delete expired token records.  Returns the number of rows removed.

    Uses a single bulk DELETE statement instead of loading all rows into
    memory and deleting one-by-one, which is critical for large tables.
    """
    stmt = delete(RefreshToken).where(RefreshToken.expires_at < _now())
    result = cast(CursorResult, db.execute(stmt))
    db.flush()
    return result.rowcount or 0
