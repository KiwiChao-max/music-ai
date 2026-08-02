"""Shared Redis event bus for task progress.

The worker publishes progress events on ``task:{id}``; the WebSocket
server subscribes to the same channel and relays them to the client.

Key design decisions:

* **Lazy singleton** --- each process (API worker, Celery child) gets
  its own ``EventBus`` instance with its own connection pool.  This is
  safe across Celery ``prefork`` children because the pool is created
  *after* the fork, so connections are never shared across processes.

* **Connection pool** --- a single ``redis.ConnectionPool`` is shared
  across all calls within a process, so the worker doesn't open a new
  TCP connection for every progress update.

* **Terminal events** --- when the task finishes (success or failure),
  the worker publishes a ``task_finished`` event.  The WebSocket
  server listens for this event and closes the connection without
  polling the database.  This eliminates the 1-second DB poll that
  was the primary source of database pressure under load.

* **Fallback DB poll** --- if the worker crashes after writing the
  terminal state to the DB but before publishing the event, the
  WebSocket server has a low-frequency (5-second) DB poll as a safety
  net.  This is a best-effort fallback; the client can also re-query
  the REST API after the WS closes.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from typing import Any

import redis

from app.config import settings

logger = logging.getLogger(__name__)

# Channel name template.
_CHANNEL = "task:{task_id}"

# ---------------------------------------------------------------------------
# Lazy singleton (per-process)
# ---------------------------------------------------------------------------
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Return the per-process singleton ``EventBus``.

    Celery's ``prefork`` pool forks children *after* importing modules,
    so any module-level resource created at import time would be
    shared across children (broken connections).  This function defers
    pool creation to the first call in each child, guaranteeing a
    fresh, independent pool.
    """
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


# ---------------------------------------------------------------------------
# EventBus
# ---------------------------------------------------------------------------
class EventBus:
    """Redis pub/sub wrapper for task progress events."""

    def __init__(self) -> None:
        self._pool: redis.ConnectionPool | None = None

    # -- internal ----------------------------------------------------------

    def _get_pool(self) -> redis.ConnectionPool:
        if self._pool is None:
            self._pool = redis.ConnectionPool.from_url(
                settings.redis_url,
                socket_connect_timeout=2,
                socket_keepalive=True,
                health_check_interval=30,
            )
        return self._pool

    def _client(self) -> redis.Redis:
        return redis.Redis(connection_pool=self._get_pool())

    def _publish(self, task_id: int, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False)
        try:
            client = self._client()
            client.publish(_CHANNEL.format(task_id=task_id), payload)
        except Exception as exc:
            logger.debug("event_bus: publish failed: %s", exc)

    # -- public API --------------------------------------------------------

    def publish_progress(
        self,
        task_id: int,
        progress: int,
        current_step: str,
        *,
        status: str | None = None,
    ) -> None:
        """Publish a progress update to the ``task:{id}`` channel."""
        self._publish(
            task_id,
            {
                "type": "progress",
                "task_id": task_id,
                "status": status,
                "progress": progress,
                "current_step": current_step,
                "ts": time.time(),
            },
        )

    def publish_task_finished(
        self,
        task_id: int,
        *,
        status: str,
        progress: int = 100,
        error_message: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        """Publish a terminal event to the ``task:{id}`` channel.

        The WebSocket server listens for this event and closes the
        connection after sending it to the client.  This eliminates
        the need for the 1-second DB poll in ``_pump_pubsub``.
        """
        self._publish(
            task_id,
            {
                "type": "task_finished",
                "task_id": task_id,
                "status": status,
                "progress": progress,
                "error_message": error_message,
                "finished_at": finished_at,
                "ts": time.time(),
            },
        )

    def publish_snapshot(self, task) -> None:
        """Publish a snapshot of the current task state.

        Used by the WebSocket server on connect so the client always
        has a fresh baseline, even if the worker is between updates.
        """
        self._publish(
            task.id,
            {
                "type": "snapshot",
                "task_id": task.id,
                "status": task.status.value,
                "progress": task.progress,
                "current_step": task.current_step,
                "error_message": task.error_message,
                "ts": time.time(),
            },
        )

    def subscribe(self, task_id: int) -> redis.client.PubSub:
        """Return a pubsub object subscribed to ``task:{task_id}``.

        The caller is responsible for closing the pubsub and the
        underlying connection when done.

        Returns ``None`` on failure, so the caller can switch to the
        DB-polling fallback.
        """
        try:
            client = self._client()
            pubsub = client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(_CHANNEL.format(task_id=task_id))
            return pubsub
        except Exception as exc:
            logger.warning("event_bus: subscribe failed, falling back to polling: %s", exc)
            return None

    def close(self) -> None:
        """Close the connection pool (called on process shutdown)."""
        if self._pool is not None:
            with contextlib.suppress(Exception):
                self._pool.disconnect()
            self._pool = None
