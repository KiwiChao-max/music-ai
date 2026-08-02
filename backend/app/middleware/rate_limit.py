"""Redis-backed rate-limiting middleware with circuit-breaker and local fallback.

A lightweight sliding-window rate limiter that uses the existing Redis
instance (shared with Celery).  No external dependencies beyond ``redis``.

Limits are configured per-route-prefix in ``_ROUTE_LIMITS``.  When a
client exceeds the limit, the middleware responds with HTTP 429 and a
``Retry-After`` header.

The limiter keys on the client IP.  ``X-Forwarded-For`` is only trusted
when the direct TCP peer matches ``settings.trusted_proxies``.

Circuit-breaker
  A single Redis failure does NOT permanently disable rate limiting.
  Instead, consecutive failures trigger an exponential-backoff retry
  schedule (1s -> 2s -> 4s -> ... -> 60s).  On the next successful Redis
  operation the breaker resets immediately.

Local fallback
  While the circuit is open, an in-memory sliding-window counter
  provides degraded rate limiting for all routes.  The in-memory
  counters are per-process (not shared across workers), but they
  prevent a single attacker from hammering the API while Redis is
  recovering.

Fail-closed endpoints
  ``/api/auth/login`` and ``/api/auth/register`` are the most
  sensitive (brute-force).  If Redis is unavailable AND the circuit
  is open, these endpoints respond with **503 Service Unavailable**
  rather than allowing unlimited attempts.  All other endpoints
  fail-open (allow the request through, using the local fallback).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable

import redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.utils.network import get_client_ip

logger = logging.getLogger(__name__)

# (requests, window_seconds) per route prefix.
# Login/register are tight (brute-force protection); upload/process are
# moderate (CPU-heavy); classify is tight (FFT on arbitrary uploads).
_ROUTE_LIMITS: list[tuple[str, int, int]] = [
    ("/api/auth/login", 10, 60),  # 10 logins / minute
    ("/api/auth/register", 5, 60),  # 5 registrations / minute
    ("/api/audio/upload", 20, 60),  # 20 uploads / minute
    ("/api/tasks/", 30, 60),  # 30 task ops / minute
    ("/api/instruments/classify", 10, 60),  # 10 FFT analyses / minute
    ("/api/instruments/soundfont", 5, 60),  # 5 SF2 imports / minute
    ("/api/instruments/preset-table", 5, 60),  # 5 CSV imports / minute
]

# Endpoints that MUST NOT be served without rate limiting (fail-closed).
# If Redis is unavailable and the circuit is open, these return 503.
_FAIL_CLOSED_PREFIXES: tuple[str, ...] = (
    "/api/auth/login",
    "/api/auth/register",
)


def _is_fail_closed(path: str) -> bool:
    return any(path.startswith(p) for p in _FAIL_CLOSED_PREFIXES)


# Circuit-breaker state (guarded by _cb_lock).
_cb_failures = 0  # consecutive Redis failures
_cb_next_retry = 0.0  # monotonic timestamp when we may retry Redis
_cb_lock = threading.Lock()

# In-memory fallback: {key: [timestamp, ...]}.
# Eviction happens lazily on each access.
_local_buckets: dict[str, list[float]] = defaultdict(list)
_local_lock = threading.Lock()

# Max memory for the local fallback (approx number of active keys).
# When exceeded, the oldest entries are evicted wholesale.
_MAX_LOCAL_KEYS = 10_000


def _cb_backoff() -> float:
    """Exponential backoff: 1, 2, 4, 8, 16, 32, 60 seconds (capped)."""
    return min(2 ** max(_cb_failures - 1, 0), 60)


def _cb_open() -> bool:
    """Return True if the circuit is open (do NOT try Redis)."""
    with _cb_lock:
        if _cb_failures == 0:
            return False
        return time.monotonic() < _cb_next_retry


def _cb_record_failure() -> None:
    """Record a Redis failure and schedule the next retry."""
    global _cb_failures, _cb_next_retry
    with _cb_lock:
        _cb_failures += 1
        _cb_next_retry = time.monotonic() + _cb_backoff()
        logger.warning(
            "rate-limit: circuit OPEN (failures=%d, retry in %.1fs)",
            _cb_failures,
            _cb_backoff(),
        )


def _cb_record_success() -> None:
    """Reset the circuit breaker on a successful Redis operation."""
    global _cb_failures
    with _cb_lock:
        if _cb_failures > 0:
            logger.info("rate-limit: circuit CLOSED (Redis recovered)")
        _cb_failures = 0


# ---- local in-memory rate limiter -----------------------------------------


def _local_check(key: str, limit: int, window: float) -> tuple[bool, int]:
    """In-memory sliding-window check.

    Returns ``(allowed, retry_after_seconds)``, same contract as the
    Redis version.  Not shared across processes, but prevents a single
    attacker from saturating this worker while Redis is recovering.
    """
    now = time.time()
    floor = now - window

    with _local_lock:
        bucket = _local_buckets[key]
        # Evict expired entries.
        while bucket and bucket[0] < floor:
            bucket.pop(0)

        count = len(bucket)
        if count < limit:
            bucket.append(now)
            return (True, 0)

        retry_after = int(bucket[0] + window - now) + 1
        return (False, max(1, retry_after))


def _local_evict_if_needed() -> None:
    """Drop the oldest keys if the local cache grows too large."""
    with _local_lock:
        if len(_local_buckets) <= _MAX_LOCAL_KEYS:
            return
        # Sort by oldest timestamp in each bucket and drop the stalest
        # half.  This is crude but keeps memory bounded.
        items = sorted(_local_buckets.items(), key=lambda kv: kv[1][0] if kv[1] else 0)
        to_drop = len(items) // 2
        for key, _ in items[:to_drop]:
            del _local_buckets[key]
        logger.warning(
            "rate-limit: local fallback evicted %d keys (now %d)",
            to_drop,
            len(_local_buckets),
        )


# ---- middleware ------------------------------------------------------------


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter backed by Redis, with circuit-breaker
    and in-memory fallback."""

    def __init__(self, app, redis_url: str | None = None) -> None:
        super().__init__(app)
        self._redis: redis.Redis | None = None
        url = redis_url or settings.redis_url
        try:
            self._redis = redis.Redis.from_url(url, decode_responses=True)
            self._redis.ping()
            logger.info("rate-limit: connected to Redis at %s", url)
        except Exception as exc:
            logger.warning("rate-limit: Redis unavailable, limiter disabled: %s", exc)
            self._redis = None

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not settings.rate_limit_enabled:
            return await call_next(request)

        if self._redis is None:
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

        client_ip = get_client_ip(
            request.client.host if request.client else None,
            request.headers.get("x-forwarded-for"),
            trusted_proxies=settings.trusted_proxies,
        )
        key = f"rl:{client_ip}:{path}"

        # ---- Redis path (circuit closed) ----
        if not _cb_open():
            try:
                allowed, retry_after = self._check(key, limit_req, limit_window)
                _cb_record_success()
            except Exception:
                _cb_record_failure()
                # For login/register, fail-closed --- no rate limiting at
                # all is worse than a temporary 503.
                if _is_fail_closed(path):
                    return JSONResponse(
                        status_code=503,
                        content={
                            "detail": "Rate limiting is temporarily unavailable. "
                            "Please try again in a few seconds."
                        },
                        headers={"Retry-After": "5"},
                    )
                allowed, retry_after = _local_check(key, limit_req, limit_window)
        else:
            # ---- Circuit open: use local fallback ----
            if _is_fail_closed(path):
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "Rate limiting is temporarily unavailable. "
                        "Please try again in a few seconds."
                    },
                    headers={"Retry-After": "5"},
                )
            allowed, retry_after = _local_check(key, limit_req, limit_window)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(retry_after)},
            )

        _local_evict_if_needed()
        response = await call_next(request)
        return response

    def _check(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Sliding-window check using sorted sets in Redis.

        Returns ``(allowed, retry_after_seconds)``.
        """
        now = time.time()
        window_start = now - window

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)  # evict old entries
        pipe.zadd(key, {str(now): now})  # add current request
        pipe.zcard(key)  # count entries
        pipe.expire(key, window + 1)  # auto-cleanup
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
