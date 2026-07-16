"""WebSocket progress channel.

`GET /api/ws/tasks/{id}/progress` opens a long-lived connection that
streams every progress update for the task until it reaches a terminal
state (FINISHED / FAILED) or the client disconnects.

The worker writes each progress update to the DB (`task.progress` /
`task.current_step`) AND publishes a JSON event on a Redis pub/sub
channel named `task:{id}`. This module subscribes to that channel,
relays events to the WS client, and adds a `type: "snapshot"` event
right after connect so the client always has a fresh baseline.

The DB polling thread is the source of truth for the "final" event:
the Redis pub/sub message can be lost if the worker is mid-restart
when the client connects, so we always re-check the DB after a short
delay and emit a `task_finished` event if the task is already
FINISHED / FAILED.

Security notes
--------------
* Per-IP connection cap (`settings.ws_max_connections_per_ip`) — a
  single client cannot exhaust the event loop by opening thousands of
  sockets.
* Hard lifetime cap (`settings.ws_max_lifetime_seconds`) — a stream
  that never reaches a terminal state (worker crashed without
  publishing) is force-closed so the client can reconnect.
* Ownership check matches the REST policy: anonymous callers can only
  watch `user_id IS NULL` (legacy/public) tasks; authenticated
  non-admin callers can only watch their own tasks; admins see all.
* DB sessions are opened per-query rather than held for the WS
  lifetime, so a long-lived socket doesn't pin a connection from the
  pool.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

import redis
from fastapi import APIRouter, Query, WebSocket, status

from app.config import settings
from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.services import auth_service, task_service
from app.utils.network import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()

# Two events per second is plenty for a progress bar; sending more just
# burns CPU on the client. The pub/sub layer is the throttle.
_SEND_INTERVAL = 0.25

# Names of terminal statuses — once we see one of these, we send the
# final event and close.
_TERMINAL = {AudioTaskStatus.FINISHED.value, AudioTaskStatus.FAILED.value}

# Per-IP connection counter. WebSocket accept/disconnect can run on
# different event-loop threads, so we guard the dict with a plain lock.
_ws_connections: dict[str, int] = {}
_ws_lock = threading.Lock()


def _ws_acquire(ip: str) -> bool:
    """Try to claim a WS slot for `ip`. Returns False if the per-IP cap
    is already reached."""
    with _ws_lock:
        current = _ws_connections.get(ip, 0)
        if current >= settings.ws_max_connections_per_ip:
            return False
        _ws_connections[ip] = current + 1
        return True


def _ws_release(ip: str) -> None:
    with _ws_lock:
        current = _ws_connections.get(ip, 0)
        if current <= 1:
            _ws_connections.pop(ip, None)
        else:
            _ws_connections[ip] = current - 1


def _client_ip(websocket: WebSocket) -> str:
    """Extract the real client IP, only trusting ``X-Forwarded-For``
    from known reverse-proxy addresses (see ``settings.trusted_proxies``)."""
    return get_client_ip(
        websocket.client.host if websocket.client else None,
        websocket.headers.get("x-forwarded-for", ""),
        trusted_proxies=settings.trusted_proxies,
    )


def _serialize_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


async def _send(websocket: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await websocket.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - client may have gone away
        logger.debug("ws: send failed: %s", exc)


def _snapshot(task) -> dict[str, Any]:
    return {
        "type": "snapshot",
        "task_id": task.id,
        "status": task.status.value,
        "progress": task.progress,
        "current_step": task.current_step,
        "error_message": task.error_message,
        "finished_at": _serialize_dt(task.finished_at),
        "ts": time.time(),
    }


def _terminal_event(task) -> dict[str, Any]:
    return {
        "type": "task_finished",
        "task_id": task.id,
        "status": task.status.value,
        "progress": task.progress,
        "error_message": task.error_message,
        "finished_at": _serialize_dt(task.finished_at),
        "ts": time.time(),
    }


def _decode_token_or_none(token: str | None) -> tuple[int | None, str | None]:
    """Return ``(user_id, role)`` from a valid access token, or
    ``(None, None)`` on failure.

    The WebSocket auth model is "best-effort" — if the token is
    missing / invalid we still allow the connection so the SPA can
    probe the channel during a refresh. The server filters progress
    events by ownership: an anonymous caller gets only public state
    for legacy tasks; an authenticated non-owner is rejected outright.
    """
    if not token:
        return None, None
    try:
        payload = auth_service.decode_token(token, expected_type="access")
        return int(payload["sub"]), payload.get("role")
    except (ValueError, KeyError, TypeError):
        return None, None


def _allowed_to_watch(task, user_id: int | None, role: str | None) -> bool:
    """Ownership gate matching the REST endpoints.

    * admins see everything;
    * legacy tasks (`user_id IS NULL`) are watchable by anyone;
    * otherwise the caller must own the task.
    """
    if role == "admin":
        return True
    if task.user_id is None:
        return True
    return user_id is not None and task.user_id == user_id


@router.websocket("/api/ws/tasks/{task_id}/progress")
async def task_progress(
    websocket: WebSocket,
    task_id: int,
    token: str | None = Query(default=None),
) -> None:
    """Stream progress events for `task_id` to the client.

    The client may supply the JWT either as a `token` query parameter
    (the WebSocket spec can't carry custom headers reliably across
    browsers) or implicitly via the `Authorization` header on the
    upgrade request.
    """
    # Combine query token + header token. The OAuth2PasswordBearer does
    # not know about query params, so we read the header ourselves.
    header_token = websocket.headers.get("Authorization", "")
    if header_token.lower().startswith("bearer "):
        header_token = header_token[7:]
    else:
        header_token = None
    user_id, role = _decode_token_or_none(token or header_token)

    client_ip = _client_ip(websocket)
    if not _ws_acquire(client_ip):
        # Reject before accept — the test client raises on accept+close
        # without a receive, so we use the policy-violation close code.
        await websocket.accept()
        await _send(
            websocket,
            {
                "type": "error",
                "code": "too_many_connections",
                "message": "too many concurrent WebSocket connections",
            },
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    try:
        # Short-lived DB session for the initial snapshot + ownership
        # check. We don't hold it for the WS lifetime — that would pin
        # a pool connection for as long as the client stays subscribed.
        with SessionLocal() as db:
            task = task_service.get_task(db, task_id)

        if task is None:
            await _send(
                websocket,
                {
                    "type": "error",
                    "code": "not_found",
                    "message": f"task {task_id} not found",
                },
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if not _allowed_to_watch(task, user_id, role):
            await _send(
                websocket,
                {
                    "type": "error",
                    "code": "forbidden",
                    "message": "you don't own this task",
                },
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # Always send a snapshot first so the client has the current
        # status even if the worker is between updates.
        await _send(websocket, _snapshot(task))

        if task.status.value in _TERMINAL:
            await _send(websocket, _terminal_event(task))
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            return

        deadline = time.monotonic() + settings.ws_max_lifetime_seconds
        pubsub = _open_pubsub(task_id)
        if pubsub is not None:
            try:
                await _pump_pubsub(websocket, pubsub, task_id, deadline)
            finally:
                # Close both the pubsub and the underlying Redis client
                # — `pubsub.close()` only closes the subscription, not
                # the connection, so leaking it accumulates FDs.
                try:
                    pubsub.close()
                except Exception:  # noqa: BLE001
                    pass
                client = getattr(pubsub, "client", None)
                if client is not None:
                    try:
                        client.close()
                    except Exception:  # noqa: BLE001
                        pass
        else:
            # Redis not reachable — fall back to DB polling so the
            # endpoint still works in degraded environments.
            await _poll_db(websocket, task_id, deadline)
    finally:
        _ws_release(client_ip)


def _open_pubsub(task_id: int):
    """Open a Redis pub/sub connection. Returns `None` on failure so
    the caller can switch to DB polling."""
    try:
        client = redis.Redis.from_url(settings.redis_url)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(f"task:{task_id}")
        return pubsub
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ws: redis pub/sub unavailable, falling back to polling: %s", exc
        )
        return None


async def _pump_pubsub(
    websocket: WebSocket,
    pubsub,
    task_id: int,
    deadline: float,
) -> None:
    """Forward every message on `task:{id}` to the WS client.

    The DB is polled once a second to detect the terminal state — the
    pub/sub channel doesn't carry a "this is the last event" signal,
    so the DB row is the source of truth.
    """
    last_db_check = 0.0
    loop = asyncio.get_event_loop()
    while True:
        if time.monotonic() >= deadline:
            await _send(
                websocket,
                {
                    "type": "error",
                    "code": "lifetime_exceeded",
                    "message": "connection exceeded the maximum lifetime",
                },
            )
            await websocket.close(code=status.WS_1001_GOING_AWAY)
            return
        # Drain the queue with a short timeout so we can also poll the
        # DB for terminal-state changes (the pub/sub doesn't emit a
        # "final" message by itself).
        message = await loop.run_in_executor(
            None, _pubsub_get, pubsub, _SEND_INTERVAL
        )
        if message is not None:
            try:
                event = json.loads(message)
            except (TypeError, ValueError):
                event = {"type": "progress", "raw": str(message)}
            await _send(websocket, event)

        now = time.monotonic()
        if now - last_db_check >= 1.0:
            last_db_check = now
            # Open a short-lived session for the DB poll — holding one
            # for the whole WS lifetime pins a pool connection.
            with SessionLocal() as db:
                task = task_service.get_task(db, task_id)
            if task is None:
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return
            if task.status.value in _TERMINAL:
                await _send(websocket, _terminal_event(task))
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return


def _pubsub_get(pubsub, timeout: float):
    """Blocking `get_message` moved to a worker thread so the asyncio
    loop stays responsive."""
    try:
        return pubsub.get_message(timeout=timeout)
    except Exception:
        return None


async def _poll_db(websocket: WebSocket, task_id: int, deadline: float) -> None:
    """DB-only fallback used when Redis pub/sub is unavailable.

    Polls the task every 0.5 s and sends a snapshot if the
    progress/status changed since the last emit."""
    last_progress = None
    last_status = None
    while True:
        if time.monotonic() >= deadline:
            await _send(
                websocket,
                {
                    "type": "error",
                    "code": "lifetime_exceeded",
                    "message": "connection exceeded the maximum lifetime",
                },
            )
            await websocket.close(code=status.WS_1001_GOING_AWAY)
            return
        await asyncio.sleep(0.5)
        with SessionLocal() as db:
            task = task_service.get_task(db, task_id)
        if task is None:
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            return
        if (
            task.progress != last_progress
            or task.status.value != last_status
        ):
            await _send(websocket, _snapshot(task))
            last_progress = task.progress
            last_status = task.status.value
        if task.status.value in _TERMINAL:
            await _send(websocket, _terminal_event(task))
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            return
