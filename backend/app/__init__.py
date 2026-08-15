"""music-ai application package.

Compatibility shim for redis-py 5+/8+ which defaults to the RESP3
protocol (``HELLO 3`` handshake).  RESP3 changes the wire format of
several responses (notably sorted-set ranges return flat lists instead
of ``[(member, score), ...]`` tuples), which breaks Kombu's QoS
visibility timeout logic and Celery's result backend.

We monkey-patch every Redis connection entry point to force
``protocol=2`` (RESP2) so both the API server (rate limit, event bus,
health checks) and the Celery worker (broker + result backend) work
correctly regardless of the installed redis-py version.
"""

from __future__ import annotations

from typing import Any

import redis as _redis
from redis.connection import Connection as _Connection
from redis.connection import ConnectionPool as _ConnectionPool

# ---------------------------------------------------------------------------
# Patch 1: Redis() constructor --- set protocol=2 on every new client.
# ---------------------------------------------------------------------------
_orig_redis_init: Any = _redis.Redis.__init__
_orig_from_url: Any = _redis.Redis.from_url


def _patched_redis_init(self, *args, **kwargs):
    kwargs.setdefault("protocol", 2)
    _orig_redis_init(self, *args, **kwargs)


@classmethod  # type: ignore[misc]
def _patched_from_url(cls, url, **kwargs):
    kwargs.setdefault("protocol", 2)
    return _orig_from_url.__func__(cls, url, **kwargs)


# setattr() keeps the monkey-patching out of mypy's "cannot assign to a
# method" checks while leaving the runtime behavior byte-identical. The
# constant attribute names below are the whole point of the patch.
setattr(_redis.Redis, "__init__", _patched_redis_init)  # noqa: B010
setattr(_redis.Redis, "from_url", _patched_from_url)  # noqa: B010

# Also patch StrictRedis if it aliases Redis (redis-py 5+ removed the alias,
# but some older code paths may still reference it).
if hasattr(_redis, "StrictRedis") and _redis.StrictRedis is not _redis.Redis:
    setattr(_redis.StrictRedis, "__init__", _patched_redis_init)  # noqa: B010
    setattr(_redis.StrictRedis, "from_url", _patched_from_url)  # noqa: B010

# ---------------------------------------------------------------------------
# Patch 2: ConnectionPool --- ensures the pool propagates protocol=2 to
# every Connection it creates, even if Kombu constructs pools directly.
# ---------------------------------------------------------------------------
_orig_pool_init = _ConnectionPool.__init__


def _patched_pool_init(self, *args, **kwargs):
    kwargs.setdefault("protocol", 2)
    _orig_pool_init(self, *args, **kwargs)


setattr(_ConnectionPool, "__init__", _patched_pool_init)  # noqa: B010

# ---------------------------------------------------------------------------
# Patch 3: low-level Connection --- belt-and-suspenders: even if a
# Connection is instantiated directly (bypassing Redis() and
# ConnectionPool), force RESP2 and drop unsupported kwargs that some
# Kombu versions accidentally forward.
# ---------------------------------------------------------------------------
_orig_conn_init = _Connection.__init__


def _patched_conn_init(self, *args, **kwargs):
    kwargs.setdefault("protocol", 2)
    # Kombu < 5.4 may forward ``maint_notifications_config`` which
    # redis-py does not accept; silently drop it.
    kwargs.pop("maint_notifications_config", None)
    _orig_conn_init(self, *args, **kwargs)


setattr(_Connection, "__init__", _patched_conn_init)  # noqa: B010

# Also patch SSL / Unix connection subclasses if they exist, since they
# inherit from Connection but may override __init__.
for _cls_name in ("SSLConnection", "UnixDomainSocketConnection", "BlockingConnectionPool"):
    _cls = getattr(_redis.connection, _cls_name, None)
    if _cls is not None and _cls is not _Connection and _cls is not _ConnectionPool:
        _orig_sub_init = _cls.__init__

        def _make_patched(orig_init):
            def _patched(self, *args, **kwargs):
                kwargs.setdefault("protocol", 2)
                kwargs.pop("maint_notifications_config", None)
                return orig_init(self, *args, **kwargs)

            return _patched

        setattr(_cls, "__init__", _make_patched(_orig_sub_init))  # noqa: B010
