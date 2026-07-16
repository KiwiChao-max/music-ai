"""Tests for the Redis-backed rate-limiting middleware.

These tests use a real Redis instance (same as the ws tests). If Redis is
unavailable, the limiter degrades gracefully and the tests are skipped.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def rate_limited_app() -> FastAPI:
    """Minimal app with the rate-limit middleware and a dummy endpoint.

    Temporarily re-enables rate limiting (disabled globally in conftest)
    and flushes Redis rate-limit keys so each test starts clean.
    """
    import redis as redis_lib
    from app.config import settings
    from app.middleware.rate_limit import RateLimitMiddleware

    # Flush any leftover rate-limit keys from previous runs.
    try:
        r = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
        for key in r.scan_iter("rl:*"):
            r.delete(key)
    except Exception:
        pass

    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = True

    app = FastAPI()

    @app.post("/api/auth/login")
    def _login():
        return {"ok": True}

    @app.post("/api/auth/register")
    def _register():
        return {"ok": True}

    @app.get("/api/audio")
    def _list():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware)

    yield app

    settings.rate_limit_enabled = original


def _redis_available() -> bool:
    try:
        import redis
        from app.config import settings
        r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        r.ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _redis_available(), reason="Redis not available")
class TestRateLimitMiddleware:
    """Rate-limit tests that require a live Redis instance."""

    def test_allows_requests_under_limit(self, rate_limited_app: FastAPI):
        """Requests within the limit should succeed."""
        client = TestClient(rate_limited_app)
        # Login limit is 10/min --- first 10 should be fine.
        for _ in range(10):
            resp = client.post("/api/auth/login")
            assert resp.status_code == 200

    def test_blocks_requests_over_limit(self, rate_limited_app: FastAPI):
        """The 11th login within 60s should return 429."""
        client = TestClient(rate_limited_app)
        for _ in range(10):
            client.post("/api/auth/login")
        resp = client.post("/api/auth/login")
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Rate limit exceeded. Try again later."
        assert "Retry-After" in resp.headers

    def test_get_requests_not_limited(self, rate_limited_app: FastAPI):
        """GET requests should bypass the limiter entirely."""
        client = TestClient(rate_limited_app)
        for _ in range(50):
            resp = client.get("/api/audio")
            assert resp.status_code == 200

    def test_separate_limits_per_route(self, rate_limited_app: FastAPI):
        """Hitting /login limit should not block /register."""
        client = TestClient(rate_limited_app)
        # Exhaust login limit.
        for _ in range(10):
            client.post("/api/auth/login")
        # Register should still work (limit is 5/min, 0 used).
        resp = client.post("/api/auth/register")
        assert resp.status_code == 200
