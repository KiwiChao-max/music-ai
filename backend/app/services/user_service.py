"""User CRUD + quota resolution.

Quota math is centralised here so the API and the worker agree on the
limit. `effective_max_tasks` and `effective_max_upload_bytes` consult
the user's row first and fall back to the server-wide default; 0 in
either place means "no limit" / "use the global default".
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AudioTask, AudioTaskStatus, User
from app.services import auth_service

# Maximum length enforced on signup. The bcrypt input limit is 72 bytes
# but most clients hash the value before sending in real systems, so 128
# is a comfortable cap that still gives users a memorable passphrase.
_USERNAME_MAX = 64
_PASSWORD_MIN = 8
_PASSWORD_MAX = 128
_EMAIL_MAX = 255
_FULL_NAME_MAX = 128


class EmailAlreadyExistsError(Exception):
    """Raised when signup is attempted with an email that is already taken."""


class UsernameAlreadyExistsError(Exception):
    """Raised when signup is attempted with a username that is already taken."""


class InvalidCredentialsError(Exception):
    """Raised on a failed login (wrong email or wrong password)."""


class UserNotFoundError(Exception):
    """Raised when a lookup by id does not find the user."""


# ---- reads ----------------------------------------------------------------
def get_user(db: Session, user_id: int) -> Optional[User]:
    return db.get(User, user_id)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Case-insensitive lookup. SQLite tests don't have a LOWER() index
    so the `func.lower` call still works; it just falls back to a scan
    on the unique index path.
    """
    if not email:
        return None
    stmt = select(User).where(func.lower(User.email) == email.lower().strip())
    return db.scalars(stmt).first()


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    if not username:
        return None
    stmt = select(User).where(
        func.lower(User.username) == username.lower().strip()
    )
    return db.scalars(stmt).first()


# ---- writes ---------------------------------------------------------------
def create_user(
    db: Session,
    *,
    email: str,
    username: str,
    password: str,
    full_name: str | None = None,
) -> User:
    """Register a new user. Email and username are validated and
    uniqueness is checked; password is hashed with bcrypt before
    persistence.
    """
    email = (email or "").strip().lower()
    username = (username or "").strip()
    full_name = (full_name or "").strip() or None
    if not email or len(email) > _EMAIL_MAX:
        raise ValueError("invalid email")
    if not username or len(username) > _USERNAME_MAX:
        raise ValueError("invalid username")
    if len(password) < _PASSWORD_MIN:
        raise ValueError(
            f"password must be at least {_PASSWORD_MIN} characters"
        )
    if len(password) > _PASSWORD_MAX:
        raise ValueError(
            f"password must be at most {_PASSWORD_MAX} characters"
        )
    if full_name is not None and len(full_name) > _FULL_NAME_MAX:
        raise ValueError("full name too long")

    user = User(
        email=email,
        username=username,
        password_hash=auth_service.hash_password(password),
        full_name=full_name,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Differentiate which unique constraint fired so the API can
        # return a precise error message.
        msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
        if "email" in msg:
            raise EmailAlreadyExistsError("email already in use") from exc
        if "username" in msg:
            raise UsernameAlreadyExistsError("username already taken") from exc
        raise

    return user


def authenticate(
    db: Session, *, identifier: str, password: str
) -> User:
    """Look up by email OR username and verify the password.

    Raises `InvalidCredentialsError` on either unknown identifier or
    wrong password --- we deliberately do not distinguish the two to
    avoid leaking which accounts exist.
    """
    if not identifier or not password:
        raise InvalidCredentialsError("invalid credentials")
    user = get_user_by_email(db, identifier) or get_user_by_username(
        db, identifier
    )
    if user is None or not user.is_active:
        raise InvalidCredentialsError("invalid credentials")
    if not auth_service.verify_password(password, user.password_hash):
        raise InvalidCredentialsError("invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    db.flush()
    return user


def touch_last_login(db: Session, user: User) -> None:
    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    db.flush()


# ---- quota ----------------------------------------------------------------
def effective_max_tasks(user: User | None) -> int:
    """Return the user's per-user task limit, falling back to the
    server-wide default. 0 means "no limit" (well, technically the
    server enforces a hard upper bound at the API layer, but 0 here
    just disables the soft check).
    """
    if user is None:
        return settings.default_max_tasks_per_user
    return user.max_tasks or settings.default_max_tasks_per_user


def effective_max_upload_bytes(user: User | None) -> int:
    """Return the user's per-user upload size limit, falling back to the
    global `max_upload_bytes` setting when the user's override is 0.
    """
    if user is not None and user.max_upload_bytes:
        return user.max_upload_bytes
    if settings.default_max_upload_bytes_per_user:
        return settings.default_max_upload_bytes_per_user
    return settings.max_upload_bytes


def count_active_tasks(db: Session, user_id: int) -> int:
    """Number of in-flight tasks the user owns (UPLOADED or PROCESSING)."""
    stmt = (
        select(func.count(AudioTask.id))
        .where(AudioTask.user_id == user_id)
        .where(
            AudioTask.status.in_(
                [AudioTaskStatus.UPLOADED, AudioTaskStatus.PROCESSING]
            )
        )
    )
    return int(db.execute(stmt).scalar_one() or 0)
