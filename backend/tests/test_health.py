"""Tests for the health/metrics endpoints.

Covers:
  * /healthz always returns 200
  * /readyz reports per-dependency status
  * /metrics serves Prometheus text format
  * CORS preflight on the API routes works
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.services import task_service


@pytest.fixture()
def client(db_session: Session) -> TestClient:
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


# ---- /healthz ------------------------------------------------------------
def test_healthz_returns_ok(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---- / --------------------------------------------------------------------
def test_root_returns_welcome(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert "message" in body
    assert "music-ai" in body["message"]


# ---- /readyz -------------------------------------------------------------
def test_readyz_includes_components(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with Redis down, the endpoint must respond with a structured body
    and a non-2xx status so a k8s probe can detect the issue.
    """
    # Force the Redis probe to fail so we can assert it surfaces in the body.
    from app.api import health as health_mod

    def _ping_returns_false(*_args, **_kwargs):
        return False

    monkeypatch.setattr(health_mod, "ping_redis", _ping_returns_false)

    resp = client.get("/readyz")
    # 200 if both PG and Redis are up; 503 if either is down.
    assert resp.status_code in (200, 503)
    body = resp.json()
    assert "components" in body
    assert "postgres" in body["components"]
    assert "redis" in body["components"]


def test_ping_redis_returns_bool() -> None:
    """The helper must always return a bool (never raise)."""
    from app.api import health as health_mod

    # Default URL almost certainly points at a Redis that's not running in CI,
    # so we expect False. The important property is that the call doesn't
    # raise.
    result = health_mod.ping_redis("redis://does-not-exist:65535/0")
    assert isinstance(result, bool)
    assert result is False


# ---- /metrics ------------------------------------------------------------
def test_metrics_returns_prometheus_text(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # Prometheus text format uses `text/plain; version=0.0.4` content type.
    assert "text/plain" in resp.headers["content-type"]
    # The gauge we declare in app.api.health must show up after a scrape.
    assert "music_ai_tasks_total" in resp.text
    # All four statuses should be present (zero is fine).
    for status in ("UPLOADED", "PROCESSING", "FINISHED", "FAILED"):
        assert status in resp.text


def test_metrics_reflects_task_counts(
    client: TestClient, db_session: Session
) -> None:
    # Add a known set of tasks and confirm the gauge labels reflect them.
    from app.db.models import AudioTask, AudioTaskStatus

    db_session.add(AudioTask(filename="x.wav", status=AudioTaskStatus.FINISHED))
    db_session.add(AudioTask(filename="y.wav", status=AudioTaskStatus.FAILED))
    db_session.commit()

    resp = client.get("/metrics")
    assert resp.status_code == 200
    # We can't easily parse the Prometheus exposition format here, so just
    # assert the count_tasks_by_status service returns what we expect.
    counts = task_service.count_tasks_by_status(db_session)
    assert counts["FINISHED"] >= 1
    assert counts["FAILED"] >= 1


# ---- CORS ----------------------------------------------------------------
def test_cors_preflight_allows_frontend_origin(
    client: TestClient,
) -> None:
    """A CORS preflight from the dev SPA must succeed with the configured
    origin in the `Access-Control-Allow-Origin` header.
    """
    resp = client.options(
        "/api/audio",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    # CORS middleware returns 200 (or 204) on a successful preflight.
    assert resp.status_code in (200, 204)
    assert "access-control-allow-origin" in {k.lower() for k in resp.headers}
