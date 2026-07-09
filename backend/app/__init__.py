"""music-ai application package.

Local-dev compatibility shim for Redis 3.2.100 on Windows: this old server
predates the RESP3 ``HELLO`` command, while redis-py 5+ defaults to RESP3
handshake which fails with ``unknown command 'HELLO'``.

We monkey-patch the redis library to force protocol=2 (RESP2) at every
connection entry point — ``Redis.__init__``, ``Redis.from_url``, and
``Connection``/``ConnectionPool`` constructors — so both the API (rate
limit middleware, direct Redis usage) and the Celery worker (which uses
kombu's ConnectionPool directly) can talk to the legacy server without
downgrading redis-py.
"""
from __future__ import annotations

import redis as _redis
from redis.connection import Connection as _Connection

# --- Patch 1: Redis() constructor and Redis.from_url ------------------------
_orig_redis_init = _redis.Redis.__init__
_orig_from_url = _redis.Redis.from_url


def _patched_redis_init(self, *args, **kwargs):
    # redis-py 8.x parses ?protocol=2 from the URL query string; ensure
    # RESP2 is used so Redis 3.2.100 on Windows can connect.
    kwargs.setdefault("protocol", 2)
    _orig_redis_init(self, *args, **kwargs)


@classmethod
def _patched_from_url(cls, url, **kwargs):
    kwargs.setdefault("protocol", 2)
    return _orig_from_url.__func__(cls, url, **kwargs)


_redis.Redis.__init__ = _patched_redis_init
_redis.Redis.from_url = _patched_from_url

# --- Patch 2: low-level Connection (kombu builds pools from params) ---------
_orig_conn_init = _Connection.__init__


def _patched_conn_init(self, *args, **kwargs):
    kwargs.setdefault("protocol", 2)
    kwargs.pop("maint_notifications_config", None)
    _orig_conn_init(self, *args, **kwargs)


_Connection.__init__ = _patched_conn_init
