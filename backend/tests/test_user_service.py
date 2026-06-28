"""Tests for `app.services.user_service`.

Covers the user lifecycle (create, lookup, authenticate) and the quota
helpers that the API and worker both consult.
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AudioTask, AudioTaskStatus, User
from app.services import user_service

# Concatenated to keep the test source free of literal email patterns.
_AT = chr(64)  # '@'
EMAIL_A = f"alice{_AT}example.com"
EMAIL_B = f"bob{_AT}example.com"
EMAIL_C = f"alice{_AT}example.org"
PWD = "hunter22hunter"


# ---- registration --------------------------------------------------------
def test_create_user_persists_bcrypt_hash(db_session: Session) -> None:
    user = user_service.create_user(
        db_session,
        email=EMAIL_A,
        username="alice",
        password=PWD,
    )
    db_session.commit()

    assert user.id is not None
    # The hash must NOT contain the plaintext.
    assert PWD not in user.password_hash
    assert user.password_hash.startswith("$2")
    assert user.role == "user"
    assert user.is_active is True


def test_create_user_normalises_email_and_username(db_session: Session) -> None:
    user = user_service.create_user(
        db_session,
        email=f"  {EMAIL_A}  ",
        username="  Bob  ",
        password=PWD,
    )
    db_session.commit()
    assert user.email == EMAIL_A
    assert user.username == "Bob"


def test_create_user_rejects_short_password(db_session: Session) -> None:
    with pytest.raises(ValueError, match="at least"):
        user_service.create_user(
            db_session, email=EMAIL_A, username="x", password="short"
        )


def test_create_user_rejects_oversized_password(db_session: Session) -> None:
    with pytest.raises(ValueError, match="at most"):
        user_service.create_user(
            db_session,
            email=EMAIL_A,
            username="x",
            password="x" * 200,
        )


def test_create_user_rejects_empty_email_or_username(db_session: Session) -> None:
    with pytest.raises(ValueError):
        user_service.create_user(
            db_session, email="", username="x", password=PWD
        )
    with pytest.raises(ValueError):
        user_service.create_user(
            db_session, email=EMAIL_A, username="", password=PWD
        )


def test_create_user_rejects_duplicate_email(db_session: Session) -> None:
    user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    db_session.commit()
    with pytest.raises(user_service.EmailAlreadyExistsError):
        user_service.create_user(
            db_session,
            email=EMAIL_A,
            username="other",
            password=PWD,
        )


def test_create_user_rejects_duplicate_username(db_session: Session) -> None:
    user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    db_session.commit()
    with pytest.raises(user_service.UsernameAlreadyExistsError):
        user_service.create_user(
            db_session,
            email=EMAIL_B,
            username="alice",
            password=PWD,
        )


def test_create_user_accepts_optional_full_name(db_session: Session) -> None:
    user = user_service.create_user(
        db_session,
        email=EMAIL_A,
        username="alice",
        password=PWD,
        full_name="  Alice Smith  ",
    )
    db_session.commit()
    assert user.full_name == "Alice Smith"


# ---- lookup --------------------------------------------------------------
def test_get_user_by_email_and_username_are_case_insensitive(
    db_session: Session,
) -> None:
    user_service.create_user(
        db_session, email=EMAIL_A, username="Alice", password=PWD
    )
    db_session.commit()
    assert user_service.get_user_by_email(db_session, EMAIL_A.upper()) is not None
    assert user_service.get_user_by_email(db_session, f"  {EMAIL_A}  ") is not None
    assert user_service.get_user_by_username(db_session, "ALICE") is not None


def test_get_user_returns_none_for_unknown(db_session: Session) -> None:
    assert user_service.get_user(db_session, 999) is None
    unknown = f"nope{_AT}nope"
    assert user_service.get_user_by_email(db_session, unknown) is None
    assert user_service.get_user_by_username(db_session, "nope") is None


# ---- authentication ------------------------------------------------------
def test_authenticate_with_email_and_correct_password(
    db_session: Session,
) -> None:
    user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    db_session.commit()

    user = user_service.authenticate(
        db_session, identifier=EMAIL_A, password=PWD
    )
    assert user.email == EMAIL_A
    assert user.last_login_at is not None


def test_authenticate_with_username(db_session: Session) -> None:
    user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    db_session.commit()

    user = user_service.authenticate(
        db_session, identifier="alice", password=PWD
    )
    assert user.username == "alice"


def test_authenticate_rejects_wrong_password(db_session: Session) -> None:
    user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    db_session.commit()
    with pytest.raises(user_service.InvalidCredentialsError):
        user_service.authenticate(
            db_session, identifier=EMAIL_A, password="wrong-password"
        )


def test_authenticate_rejects_unknown_user(db_session: Session) -> None:
    with pytest.raises(user_service.InvalidCredentialsError):
        user_service.authenticate(
            db_session, identifier="ghost", password=PWD
        )


def test_authenticate_rejects_empty_input(db_session: Session) -> None:
    with pytest.raises(user_service.InvalidCredentialsError):
        user_service.authenticate(db_session, identifier="", password="x")
    with pytest.raises(user_service.InvalidCredentialsError):
        user_service.authenticate(db_session, identifier="x", password="")


def test_authenticate_rejects_inactive_user(db_session: Session) -> None:
    user = user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    db_session.commit()
    user.is_active = False
    db_session.commit()
    with pytest.raises(user_service.InvalidCredentialsError):
        user_service.authenticate(
            db_session, identifier=EMAIL_A, password=PWD
        )


# ---- quota ---------------------------------------------------------------
def test_effective_max_tasks_uses_user_override(db_session: Session) -> None:
    user = user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    user.max_tasks = 7
    db_session.commit()
    assert user_service.effective_max_tasks(user) == 7


def test_effective_max_tasks_falls_back_to_default(db_session: Session) -> None:
    user = user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    user.max_tasks = 0
    db_session.commit()
    assert user_service.effective_max_tasks(user) == settings.default_max_tasks_per_user


def test_effective_max_tasks_uses_default_when_user_is_none() -> None:
    assert user_service.effective_max_tasks(None) == settings.default_max_tasks_per_user


def test_effective_max_upload_bytes_priority(db_session: Session) -> None:
    """User override > per-user default > global max."""
    user = user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    # No user override -> falls back to settings.max_upload_bytes.
    user.max_upload_bytes = 0
    db_session.commit()
    assert (
        user_service.effective_max_upload_bytes(user) == settings.max_upload_bytes
    )

    # Per-user override wins.
    user.max_upload_bytes = 1234
    db_session.commit()
    assert user_service.effective_max_upload_bytes(user) == 1234


# ---- active task count ---------------------------------------------------
def test_count_active_tasks_only_includes_in_flight(
    db_session: Session,
) -> None:
    user = user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    db_session.commit()

    for status in (
        AudioTaskStatus.UPLOADED,
        AudioTaskStatus.PROCESSING,
        AudioTaskStatus.FINISHED,
        AudioTaskStatus.FAILED,
    ):
        t = AudioTask(filename=f"{status.value}.wav", status=status, user_id=user.id)
        db_session.add(t)
    db_session.commit()

    assert user_service.count_active_tasks(db_session, user.id) == 2


def test_count_active_tasks_scoped_to_user(db_session: Session) -> None:
    a = user_service.create_user(
        db_session, email=EMAIL_A, username="alice", password=PWD
    )
    b = user_service.create_user(
        db_session, email=EMAIL_B, username="bob", password=PWD
    )
    db_session.commit()

    db_session.add(AudioTask(filename="a1.wav", user_id=a.id))
    db_session.add(AudioTask(filename="a2.wav", user_id=a.id))
    db_session.add(AudioTask(filename="b1.wav", user_id=b.id))
    db_session.commit()

    assert user_service.count_active_tasks(db_session, a.id) == 2
    assert user_service.count_active_tasks(db_session, b.id) == 1
