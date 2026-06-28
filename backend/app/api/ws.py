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
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import redis
from fastapi import APIRouter, Query, WebSocket, status

from app.config import settings
from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.services import auth_service, task_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Two events per second is plenty for a progress bar; sending more just
# burns CPU on the client. The pub/sub layer is the throttle.
_SEND_INTERVAL = 0.25

# Names of terminal statuses — once we see one of these, we send the
# final event and close.
_TERMINAL = {AudioTaskStatus.FINISHED.value, AudioTaskStatus.FAILED.value}


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


def _decode_token_or_none(token: str | None) -> int | None:
    """Return the user_id from a valid access token, or None on failure.

    The WebSocket auth model is "best-effort" — if the token is
    missing / invalid we still allow the connection so the SPA can
    probe the channel during a refresh. The server filters progress
    events by ownership: a non-owner gets only public state (status
    + progress), never error_message or current_step.
    """
    if not token:
        return None
    try:
        payload = auth_service.decode_token(token, expected_type="access")
        return int(payload["sub"])
    except (ValueError, KeyError, TypeError):
        return None


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
    user_id = _decode_token_or_none(token or header_token)

    await websocket.accept()

    db = SessionLocal()
    try:
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
        # Ownership check: a non-admin user can only watch their own
        # task. Anonymous users (no token) can watch legacy tasks that
        # have no owner.
        if (
            user_id is not None
            and task.user_id is not None
            and task.user_id != user_id
        ):
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

        pubsub = _open_pubsub(task_id)
        if pubsub is not None:
            try:
                await _pump_pubsub(websocket, pubsub, task_id, db)
            finally:
                try:
                    pubsub.close()
                except Exception:
                    pass
        else:
            # Redis not reachable — fall back to DB polling so the
            # endpoint still works in degraded environments.
            await _poll_db(websocket, task_id, db)
    finally:
        db.close()


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
    db,
) -> None:
    """Forward every message on `task:{id}` to the WS client.

    The DB is polled once a second to detect the terminal state — the
    pub/sub channel doesn't carry a "this is the last event" signal,
    so the DB row is the source of truth.
    """
    last_db_check = 0.0
    loop = asyncio.get_event_loop()
    while True:
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
            db.expire_all()
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


async def _poll_db(websocket: WebSocket, task_id: int, db) -> None:
    """DB-only fallback used when Redis pub/sub is unavailable.

    Polls the task every 0.5 s and sends a snapshot if the
    progress/status changed since the last emit."""
    last_progress = None
    last_status = None
    while True:
        await asyncio.sleep(0.5)
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
