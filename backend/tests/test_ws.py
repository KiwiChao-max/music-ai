"""Tests for the WebSocket progress channel.

These tests stub `app.db.session.SessionLocal` to return the same
in-memory SQLite engine the rest of the suite uses, so the WS handler
sees the same task rows the test inserted.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import session as db_session
from app.db.models import AudioTask, AudioTaskStatus
from app.main import app
from app.services import user_service

_AT = chr(64)  # '@'
EMAIL_A = f"alice{_AT}example.com"
EMAIL_B = f"bob{_AT}example.com"
PWD = "hunter22hunter"


@pytest.fixture()
def ws_engine():
    """Per-test in-memory SQLite engine."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    from app.db.base import Base
    from app.db import models  # noqa: F401  (registers models on Base.metadata)
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def ws_session_factory(ws_engine):
    """Yield a session bound to the test engine and patch `SessionLocal`
    so the WebSocket handler uses the same in-memory DB."""
    SessionTesting = sessionmaker(
        bind=ws_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    real_session_local = db_session.SessionLocal
    db_session.SessionLocal = SessionTesting
    # `app/api/ws.py` did `from app.db.session import SessionLocal`,
    # so the name is bound in *its* namespace too — patch there as well.
    from app.api import ws as ws_mod
    real_ws_session_local = ws_mod.SessionLocal
    ws_mod.SessionLocal = SessionTesting
    try:
        yield SessionTesting
    finally:
        db_session.SessionLocal = real_session_local
        ws_mod.SessionLocal = real_ws_session_local


@pytest.fixture()
def client(ws_session_factory) -> TestClient:
    """Plain TestClient — no DB dependency overrides needed for WS."""
    # Reset the per-IP connection counter so a leaked slot in a prior
    # test can't poison the next one.
    from app.api import ws as ws_mod
    with ws_mod._ws_lock:
        ws_mod._ws_connections.clear()
    return TestClient(app)


# ---- snapshot for new task ----------------------------------------------
def test_ws_sends_snapshot_for_existing_task(
    client: TestClient, ws_session_factory
) -> None:
    """A fresh UPLOADED task must produce a `snapshot` event right after
    connect, with the current state from the DB."""
    with ws_session_factory() as db:
        task = AudioTask(filename="song.wav", status=AudioTaskStatus.UPLOADED)
        db.add(task)
        db.commit()
        task_id = task.id

    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress"
    ) as ws:
        first = ws.receive_text()
        event = json.loads(first)
        assert event["type"] == "snapshot"
        assert event["task_id"] == task_id
        assert event["status"] == "UPLOADED"
        assert event["progress"] == 0


# ---- terminal state ------------------------------------------------------
def test_ws_closes_immediately_for_finished_task(
    client: TestClient, ws_session_factory
) -> None:
    """A FINISHED task must produce a snapshot AND a `task_finished`
    event, then close the connection."""
    with ws_session_factory() as db:
        task = AudioTask(
            filename="song.wav",
            status=AudioTaskStatus.FINISHED,
            progress=100,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress"
    ) as ws:
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "snapshot"
        assert snapshot["status"] == "FINISHED"

        terminal = json.loads(ws.receive_text())
        assert terminal["type"] == "task_finished"
        assert terminal["status"] == "FINISHED"
        assert terminal["progress"] == 100


def test_ws_closes_for_failed_task(
    client: TestClient, ws_session_factory
) -> None:
    """A FAILED task emits a `task_finished` event with the error message."""
    with ws_session_factory() as db:
        task = AudioTask(
            filename="song.wav",
            status=AudioTaskStatus.FAILED,
            error_message="demucs crashed",
            finished_at=datetime.now(timezone.utc),
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress"
    ) as ws:
        ws.receive_text()  # snapshot
        terminal = json.loads(ws.receive_text())
        assert terminal["type"] == "task_finished"
        assert terminal["status"] == "FAILED"
        assert terminal["error_message"] == "demucs crashed"


# ---- not found -----------------------------------------------------------
def test_ws_emits_error_for_missing_task(
    client: TestClient, ws_session_factory
) -> None:
    """A 404-ish task must produce an `error` event and close."""
    with client.websocket_connect("/api/ws/tasks/9999/progress") as ws:
        event = json.loads(ws.receive_text())
        assert event["type"] == "error"
        assert event["code"] == "not_found"
        # The next receive should raise (the server closed the socket).
        with pytest.raises(Exception):
            ws.receive_text()


# ---- ownership -----------------------------------------------------------
def test_ws_rejects_non_owner(client: TestClient, ws_session_factory) -> None:
    """A non-admin user must not be able to watch someone else's task."""
    with ws_session_factory() as db:
        bob = user_service.create_user(
            db, email=EMAIL_B, username="bob", password=PWD
        )
        db.commit()
        # Task is owned by Bob.
        task = AudioTask(
            filename="private.wav",
            status=AudioTaskStatus.UPLOADED,
            user_id=bob.id,
        )
        db.add(task)
        db.commit()
        task_id = task.id
        bob_id = bob.id

    # Alice's access token.
    with ws_session_factory() as db:
        alice = user_service.create_user(
            db, email=EMAIL_A, username="alice", password=PWD
        )
        db.commit()
    token = _issue_token(alice.id, email=EMAIL_A, role="user")

    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress?token={token}"
    ) as ws:
        event = json.loads(ws.receive_text())
        assert event["type"] == "error"
        assert event["code"] == "forbidden"
        with pytest.raises(Exception):
            ws.receive_text()
    _ = bob_id  # silence unused warning if we ever add a check


def test_ws_allows_owner_to_watch(
    client: TestClient, ws_session_factory
) -> None:
    with ws_session_factory() as db:
        alice = user_service.create_user(
            db, email=EMAIL_A, username="alice", password=PWD
        )
        db.commit()
        task = AudioTask(
            filename="mine.wav",
            status=AudioTaskStatus.UPLOADED,
            user_id=alice.id,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    token = _issue_token(alice.id, email=EMAIL_A, role="user")
    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress?token={token}"
    ) as ws:
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "snapshot"
        assert snapshot["task_id"] == task_id


def test_ws_allows_anonymous_for_legacy_task(
    client: TestClient, ws_session_factory
) -> None:
    """Tasks with `user_id IS NULL` (legacy / pre-auth uploads) must be
    watchable by anyone — the same policy the REST endpoints use."""
    with ws_session_factory() as db:
        task = AudioTask(
            filename="legacy.wav",
            status=AudioTaskStatus.UPLOADED,
            user_id=None,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress"
    ) as ws:
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "snapshot"
        assert snapshot["task_id"] == task_id


def test_ws_rejects_anonymous_for_owned_task(
    client: TestClient, ws_session_factory
) -> None:
    """Anonymous callers (no token) must NOT be able to watch a task
    that belongs to a user. Previously the ownership check only fired
    when a token was present, so anonymous clients could subscribe to
    anyone's task — including ones carrying private error messages."""
    with ws_session_factory() as db:
        bob = user_service.create_user(
            db, email=EMAIL_B, username="bob", password=PWD
        )
        db.commit()
        task = AudioTask(
            filename="private.wav",
            status=AudioTaskStatus.UPLOADED,
            user_id=bob.id,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress"
    ) as ws:
        event = json.loads(ws.receive_text())
        assert event["type"] == "error"
        assert event["code"] == "forbidden"
        with pytest.raises(Exception):
            ws.receive_text()


def test_ws_allows_admin_to_watch_any_task(
    client: TestClient, ws_session_factory
) -> None:
    """Admins can watch any task regardless of ownership."""
    with ws_session_factory() as db:
        bob = user_service.create_user(
            db, email=EMAIL_B, username="bob", password=PWD
        )
        db.commit()
        task = AudioTask(
            filename="bob_private.wav",
            status=AudioTaskStatus.UPLOADED,
            user_id=bob.id,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    admin_token = _issue_token(1, email="[email protected]", role="admin")
    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress?token={admin_token}"
    ) as ws:
        snapshot = json.loads(ws.receive_text())
        assert snapshot["type"] == "snapshot"
        assert snapshot["task_id"] == task_id


# ---- connection limits ---------------------------------------------------
def test_ws_rejects_too_many_connections_per_ip(
    client: TestClient, ws_session_factory, monkeypatch
) -> None:
    """When the per-IP concurrent-connection cap is 0, any connect is
    rejected with a `too_many_connections` error and closed."""
    from app.api import ws as ws_mod

    monkeypatch.setattr(ws_mod.settings, "ws_max_connections_per_ip", 0)

    with ws_session_factory() as db:
        task = AudioTask(
            filename="legacy.wav",
            status=AudioTaskStatus.UPLOADED,
            user_id=None,
        )
        db.add(task)
        db.commit()
        task_id = task.id

    with client.websocket_connect(
        f"/api/ws/tasks/{task_id}/progress"
    ) as ws:
        event = json.loads(ws.receive_text())
        assert event["type"] == "error"
        assert event["code"] == "too_many_connections"
        with pytest.raises(Exception):
            ws.receive_text()


# ---- helpers -------------------------------------------------------------
def _issue_token(user_id: int, *, email: str, role: str) -> str:
    """Mint a short-lived access token using the same auth helper the
    REST endpoints use."""
    from app.services import auth_service

    return auth_service.create_token(
        str(user_id),
        token_type="access",
        extra_claims={"email": email, "role": role},
    )
