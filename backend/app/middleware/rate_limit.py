"""Redis-backed rate-limiting middleware.

A lightweight sliding-window rate limiter that uses the existing Redis
instance (shared with Celery).  No external dependencies beyond ``redis``.

Limits are configured per-route-prefix in ``_ROUTE_LIMITS``.  When a
client exceeds the limit, the middleware responds with HTTP 429 and a
``Retry-After`` header.

The limiter keys on the client IP (from ``X-Forwarded-For`` or the raw
remote address).  When Redis is unavailable, the middleware degrades
gracefully — it logs a warning and lets the request through, so a Redis
outage never takes the API down.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

import redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings

logger = logging.getLogger(__name__)

# (requests, window_seconds) per route prefix.
# Login/register are tight (brute-force protection); upload/process are
# moderate (CPU-heavy); classify is tight (FFT on arbitrary uploads).
_ROUTE_LIMITS: list[tuple[str, int, int]] = [
    ("/api/auth/login", 10, 60),       # 10 logins / minute
    ("/api/auth/register", 5, 60),     # 5 registrations / minute
    ("/api/audio/upload", 20, 60),     # 20 uploads / minute
    ("/api/tasks/", 30, 60),           # 30 task ops / minute
    ("/api/instruments/classify", 10, 60),  # 10 FFT analyses / minute
    ("/api/instruments/soundfont", 5, 60),  # 5 SF2 imports / minute
    ("/api/instruments/preset-table", 5, 60),  # 5 CSV imports / minute
]

_REDIS_UNAVAILABLE = False  # set to True after first connection failure


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter backed by Redis."""

    def __init__(self, app, redis_url: str | None = None) -> None:
        super().__init__(app)
        self._redis: redis.Redis | None = None
        url = redis_url or settings.redis_url
        try:
            self._redis = redis.Redis.from_url(url, decode_responses=True)
            self._redis.ping()
            logger.info("rate-limit: connected to Redis at %s", url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rate-limit: Redis unavailable, limiter disabled: %s", exc)
            self._redis = None

    async def dispatch(
        self, request: Request, call_next: Callable
    ) -> Response:
        global _REDIS_UNAVAILABLE

        if not settings.rate_limit_enabled:
            return await call_next(request)

        if self._redis is None or _REDIS_UNAVAILABLE:
            return await call_next(request)

        path = request.url.path
        method = request.method

        # Only rate-limit write/pricy endpoints.
        if method not in ("POST", "PUT", "DELETE"):
            return await call_next(request)

        # Find the matching route prefix.
        limit_req, limit_window = 0, 0
        for prefix, req_count, window in _ROUTE_LIMITS:
            if path.startswith(prefix):
                limit_req, limit_window = req_count, window
                break

        if limit_req == 0:
            return await call_next(request)

        client_ip = self._client_ip(request)
        key = f"rl:{client_ip}:{path}"

        try:
            allowed, retry_after = self._check(key, limit_req, limit_window)
        except Exception:  # noqa: BLE001 - never block on Redis errors
            logger.warning("rate-limit: Redis error, allowing request")
            _REDIS_UNAVAILABLE = True
            return await call_next(request)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        return response

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _check(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Sliding-window check using sorted sets in Redis.

        Returns ``(allowed, retry_after_seconds)``.
        """
        now = time.time()
        window_start = now - window

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)   # evict old entries
        pipe.zadd(key, {str(now): now})                # add current request
        pipe.zcard(key)                                 # count entries
        pipe.expire(key, window + 1)                    # auto-cleanup
        _, _, count, _ = pipe.execute()

        if count <= limit:
            return (True, 0)

        # Calculate retry-after: time until the oldest entry expires.
        oldest = self._redis.zrange(key, 0, 0, withscores=True)
        if oldest:
            oldest_score = oldest[0][1]
            retry_after = int(oldest_score + window - now) + 1
            return (False, max(1, retry_after))
        return (False, window)
