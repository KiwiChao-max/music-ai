"""Tests for the audio task REST endpoints, with focus on the new
auth + ownership + quota logic.

Auth is disabled by default in `settings.auth_required` so the existing
upload/list/delete paths work without a token. These tests flip the
flag on (and create a real user) to exercise the gated behaviour.
"""
from __future__ import annotations

import io
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AudioTask, AudioTaskStatus, User
from app.main import app
from app.services import user_service

_AT = chr(64)  # '@'
EMAIL_A = f"alice{_AT}example.com"
EMAIL_B = f"bob{_AT}example.com"
PWD = "hunter22hunter"


def _make_wav_bytes(duration_s: float = 0.05) -> bytes:
    """Tiny valid WAV (mono 8 kHz 16-bit) — small enough to fit any
    quota and parseable by `soundfile.info` for the duration probe.
    """
    sample_rate = 8000
    n_samples = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * n_samples)
    return buf.getvalue()


@pytest.fixture()
def client(db_session: Session) -> TestClient:
    """Bind the FastAPI app to the test session via dependency override."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    from app.db.session import get_db
    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def auth_required(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "auth_required", True)
    yield
    monkeypatch.setattr(settings, "auth_required", False)


def _register(client: TestClient, *, email: str, username: str) -> str:
    resp = client.post(
        "/api/auth/register",
        json={"email": email, "username": username, "password": PWD},
    )
    assert resp.status_code == 201
    return resp.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_user(db: Session, *, email: str, username: str) -> User:
    user = user_service.create_user(
        db, email=email, username=username, password=PWD
    )
    db.commit()
    return user


# ---- auth_required gate --------------------------------------------------
def test_upload_rejects_anonymous_when_auth_required(
    client: TestClient, auth_required, db_session: Session
) -> None:
    resp = client.post(
        "/api/audio/upload",
        files={"file": ("song.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 401


def test_list_rejects_anonymous_when_auth_required(
    client: TestClient, auth_required
) -> None:
    resp = client.get("/api/audio")
    assert resp.status_code == 401


# ---- task ownership ------------------------------------------------------
def test_get_task_returns_own_task(
    client: TestClient, auth_required, db_session: Session
) -> None:
    token = _register(client, email=EMAIL_A, username="alice")
    resp = client.post(
        "/api/audio/upload",
        headers=_bearer(token),
        files={"file": ("mine.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    resp = client.get(f"/api/audio/{task_id}", headers=_bearer(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == task_id


def test_get_task_forbidden_for_other_user(
    client: TestClient, auth_required, db_session: Session
) -> None:
    token_a = _register(client, email=EMAIL_A, username="alice")
    token_b = _register(client, email=EMAIL_B, username="bob")

    # Alice uploads a task
    resp = client.post(
        "/api/audio/upload",
        headers=_bearer(token_a),
        files={"file": ("private.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    # Bob tries to read it
    resp = client.get(f"/api/audio/{task_id}", headers=_bearer(token_b))
    assert resp.status_code == 403


def test_list_tasks_scoped_to_caller(
    client: TestClient, auth_required, db_session: Session
) -> None:
    token_a = _register(client, email=EMAIL_A, username="alice")
    token_b = _register(client, email=EMAIL_B, username="bob")

    # Each user uploads one task.
    a_resp = client.post(
        "/api/audio/upload",
        headers=_bearer(token_a),
        files={"file": ("a.wav", _make_wav_bytes(), "audio/wav")},
    )
    b_resp = client.post(
        "/api/audio/upload",
        headers=_bearer(token_b),
        files={"file": ("b.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert a_resp.status_code == 201
    assert b_resp.status_code == 201
    a_id = a_resp.json()["task_id"]
    b_id = b_resp.json()["task_id"]

    # Alice must not see Bob's task.
    list_a = client.get("/api/audio", headers=_bearer(token_a)).json()
    assert all(t["id"] != b_id for t in list_a)
    assert any(t["id"] == a_id for t in list_a)

    list_b = client.get("/api/audio", headers=_bearer(token_b)).json()
    assert all(t["id"] != a_id for t in list_b)
    assert any(t["id"] == b_id for t in list_b)


def test_delete_task_forbidden_for_other_user(
    client: TestClient, auth_required, db_session: Session
) -> None:
    token_a = _register(client, email=EMAIL_A, username="alice")
    token_b = _register(client, email=EMAIL_B, username="bob")

    resp = client.post(
        "/api/audio/upload",
        headers=_bearer(token_a),
        files={"file": ("private.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert resp.status_code == 201
    task_id = resp.json()["task_id"]

    # Bob can't delete it.
    resp = client.delete(
        f"/api/audio/{task_id}", headers=_bearer(token_b)
    )
    assert resp.status_code == 403


def test_admin_sees_all_tasks(
    client: TestClient, auth_required, db_session: Session
) -> None:
    """An admin (role=admin) must see every task across users."""
    token_a = _register(client, email=EMAIL_A, username="alice")
    user_b = _make_user(db_session, email=EMAIL_B, username="bob")
    user_b.role = "admin"
    db_session.commit()
    # Bob logs in to get a fresh token reflecting the new role.
    token_b = client.post(
        "/api/auth/login",
        json={"identifier": EMAIL_B, "password": PWD},
    ).json()["access_token"]

    a_resp = client.post(
        "/api/audio/upload",
        headers=_bearer(token_a),
        files={"file": ("a.wav", _make_wav_bytes(), "audio/wav")},
    )
    b_resp = client.post(
        "/api/audio/upload",
        headers=_bearer(token_b),
        files={"file": ("b.wav", _make_wav_bytes(), "audio/wav")},
    )
    a_id = a_resp.json()["task_id"]
    b_id = b_resp.json()["task_id"]

    list_admin = client.get("/api/audio", headers=_bearer(token_b)).json()
    ids = {t["id"] for t in list_admin}
    assert a_id in ids
    assert b_id in ids


# ---- quota enforcement ---------------------------------------------------
def test_upload_rejected_when_task_quota_reached(
    client: TestClient, auth_required, db_session: Session
) -> None:
    """A user with max_tasks=1 can't upload a second in-flight task."""
    user = _make_user(db_session, email=EMAIL_A, username="alice")
    user.max_tasks = 1
    db_session.commit()
    token = client.post(
        "/api/auth/login",
        json={"identifier": EMAIL_A, "password": PWD},
    ).json()["access_token"]

    first = client.post(
        "/api/audio/upload",
        headers=_bearer(token),
        files={"file": ("first.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert first.status_code == 201

    second = client.post(
        "/api/audio/upload",
        headers=_bearer(token),
        files={"file": ("second.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert second.status_code == 429
    assert "quota" in second.json()["detail"].lower()


def test_finished_task_does_not_count_against_quota(
    client: TestClient, auth_required, db_session: Session
) -> None:
    """A FINISHED task is not 'active' so the user can upload another."""
    user = _make_user(db_session, email=EMAIL_A, username="alice")
    user.max_tasks = 1
    db_session.commit()
    token = client.post(
        "/api/auth/login",
        json={"identifier": EMAIL_A, "password": PWD},
    ).json()["access_token"]

    first = client.post(
        "/api/audio/upload",
        headers=_bearer(token),
        files={"file": ("first.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert first.status_code == 201
    first_id = first.json()["task_id"]

    # Mark the first task FINISHED directly in the DB.
    task = db_session.get(AudioTask, first_id)
    task.status = AudioTaskStatus.FINISHED
    db_session.commit()

    # The second upload should now succeed.
    second = client.post(
        "/api/audio/upload",
        headers=_bearer(token),
        files={"file": ("second.wav", _make_wav_bytes(), "audio/wav")},
    )
    assert second.status_code == 201
