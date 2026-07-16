"""WebSocket progress channel.

``GET /api/ws/tasks/{id}/progress`` opens a long-lived connection that
streams every progress update for the task until it reaches a terminal
state (FINISHED / FAILED) or the client disconnects.

Architecture
------------
The worker publishes progress events on a Redis Pub/Sub channel named
``task:{id}``. When the task finishes, the worker publishes a
``task_finished`` event on the same channel.  This module subscribes to
that channel, relays events to the WS client, and closes the connection
when it receives ``task_finished``.

A low-frequency (5-second) DB poll serves as a safety net for the rare
edge case where the worker crashes after writing the terminal state to
the DB but before publishing the event.  This eliminates the 1-second
DB poll that was the primary source of database pressure under load.

Security notes
--------------
* Per-IP connection cap (``settings.ws_max_connections_per_ip``).
* Hard lifetime cap (``settings.ws_max_lifetime_seconds``).
* Ownership check matches the REST policy.
* DB sessions are opened per-query rather than held for the WS lifetime.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, WebSocket, status

from app.config import settings
from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.services import auth_service, task_service
from app.services.event_bus import get_event_bus
from app.utils.network import get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()

# Two events per second is plenty for a progress bar; sending more just
# burns CPU on the client.
_SEND_INTERVAL = 0.25

# How often the DB poll runs as a safety net (seconds).  This is *not*
# the primary mechanism for detecting terminal state --- the worker
# publishes a ``task_finished`` event on Redis.  The DB poll only
# catches the rare case where the worker crashes between committing to
# the DB and publishing the event.
_SAFETY_POLL_INTERVAL = max(1.0, settings.ws_safety_poll_interval)

# Names of terminal statuses.
_TERMINAL = {AudioTaskStatus.FINISHED.value, AudioTaskStatus.FAILED.value}

# Per-IP connection counter.
_ws_connections: dict[str, int] = {}
_ws_lock = threading.Lock()


def _ws_acquire(ip: str) -> bool:
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
    except Exception as exc:  # noqa: BLE001
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
    if not token:
        return None, None
    try:
        payload = auth_service.decode_token(token, expected_type="access")
        return int(payload["sub"]), payload.get("role")
    except (ValueError, KeyError, TypeError):
        return None, None


def _allowed_to_watch(task, user_id: int | None, role: str | None) -> bool:
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
    """Stream progress events for ``task_id`` to the client."""
    # Combine three token sources (in priority order):
    #   1. ``token`` query parameter (legacy)
    #   2. ``Authorization`` header
    #   3. ``Sec-WebSocket-Protocol`` header (browser-native, no URL leakage)
    header_token = websocket.headers.get("Authorization", "")
    if header_token.lower().startswith("bearer "):
        header_token = header_token[7:]
    else:
        header_token = None
    protocol_token = websocket.headers.get("Sec-WebSocket-Protocol", "")
    user_id, role = _decode_token_or_none(token or header_token or protocol_token)

    client_ip = _client_ip(websocket)
    if not _ws_acquire(client_ip):
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
        # Ownership check + initial snapshot.
        with SessionLocal() as db:
            task = task_service.get_task(db, task_id)

        if task is None:
            await _send(
                websocket,
                {"type": "error", "code": "not_found", "message": f"task {task_id} not found"},
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        if not _allowed_to_watch(task, user_id, role):
            await _send(
                websocket,
                {"type": "error", "code": "forbidden", "message": "you don't own this task"},
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await _send(websocket, _snapshot(task))

        if task.status.value in _TERMINAL:
            await _send(websocket, _terminal_event(task))
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            return

        deadline = time.monotonic() + settings.ws_max_lifetime_seconds
        pubsub = get_event_bus().subscribe(task_id)
        if pubsub is not None:
            try:
                await _pump_pubsub(websocket, pubsub, task_id, deadline)
            finally:
                try:
                    pubsub.close()
                except Exception:  # noqa: BLE001
                    pass
                client = getattr(pubsub, "connection_pool", None)
                if client is not None:
                    try:
                        client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
        else:
            # Redis not reachable --- fall back to DB-only polling.
            await _poll_db_fallback(websocket, task_id, deadline)
    finally:
        _ws_release(client_ip)


async def _pump_pubsub(
    websocket: WebSocket,
    pubsub,
    task_id: int,
    deadline: float,
) -> None:
    """Forward Redis Pub/Sub events to the WS client.

    The worker publishes ``task_finished`` when the task reaches a
    terminal state, so we no longer need to poll the DB every second.
    A low-frequency (5-second) safety poll catches the rare edge case
    where the worker crashes between committing the terminal state to
    the DB and publishing the event.
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

        # Drain the pub/sub queue with a short timeout.
        message = await loop.run_in_executor(
            None, _pubsub_get, pubsub, _SEND_INTERVAL
        )
        if message is not None:
            try:
                event = json.loads(message)
            except (TypeError, ValueError):
                event = {"type": "progress", "raw": str(message)}
            await _send(websocket, event)

            # If the worker published a terminal event, close the
            # connection immediately --- no need to poll the DB.
            if event.get("type") == "task_finished":
                await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
                return

        # Low-frequency safety poll: check the DB in case the worker
        # crashed after committing to the DB but before publishing.
        now = time.monotonic()
        if now - last_db_check >= _SAFETY_POLL_INTERVAL:
            last_db_check = now
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
    """Blocking ``get_message`` moved to a worker thread so the asyncio
    loop stays responsive."""
    try:
        return pubsub.get_message(timeout=timeout)
    except Exception:
        return None


async def _poll_db_fallback(
    websocket: WebSocket, task_id: int, deadline: float
) -> None:
    """DB-only fallback used when Redis Pub/Sub is unavailable.

    Polls the task every 0.5 s and sends a snapshot if the
    progress/status changed since the last emit.
    """
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
        if task.progress != last_progress or task.status.value != last_status:
            await _send(websocket, _snapshot(task))
            last_progress = task.progress
            last_status = task.status.value
        if task.status.value in _TERMINAL:
            await _send(websocket, _terminal_event(task))
            await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
            return