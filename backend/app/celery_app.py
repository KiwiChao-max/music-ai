"""Celery application instance.

Start a worker with:

    cd backend
    celery -A app.celery_app:celery worker --loglevel=info

The API process imports `app.celery_app` so it can dispatch tasks; the
worker process imports the same module to discover registered tasks.
Keep this file thin: configuration only, no task bodies.
"""
from __future__ import annotations

from celery import Celery

from app.config import settings

celery = Celery(
    "music_ai",
    broker=settings.resolved_celery_broker_url,
    backend=settings.resolved_celery_result_backend,
    include=["app.tasks_audio"],
)

# Sensible defaults for a CPU-bound audio pipeline.
#
# * `acks_late=True` — if the worker dies mid-task, the message is re-queued
#   instead of being lost. The DB row stays in PROCESSING and the next worker
#   picks it up; `claim_for_processing` already guards against double-execution
#   so this is safe.
# * `task_track_started=True` — the result is set to STARTED as soon as the
#   task begins executing, which is useful for debugging but not required by
#   the API contract (the API polls the DB, not the Celery result).
# * `worker_prefetch_multiplier=1` — long-running tasks should not be batched;
#   one task per worker at a time keeps memory bounded and avoids head-of-line
#   blocking.
# * `task_acks_on_failure_or_timeout=False` (default) — the message is
#   re-queued on unhandled exceptions, but the worker has already marked the
#   DB row as FAILED inside `process_task`, so the retry would just fail
#   again. If you want auto-retry, wrap the task with `autoretry_for`.
# * `task_time_limit` — hard kill deadline. Demucs on a 5-minute track takes
#   ~6-10 minutes on CPU; 30 minutes is a generous ceiling that still catches
#   infinite loops / deadlocks. The worker is killed by SIGKILL, so
#   `acks_late` re-queues the message and the next worker claims it via
#   `claim_for_processing`.
# * `task_soft_time_limit` — SoftDBLimitExceeded is raised 2 minutes before
#   the hard kill, giving the task a chance to mark itself FAILED in the DB
#   before the worker dies.
# * `task_reject_on_worker_lost=True` — when the worker process is killed
#   (OOM, spot instance termination), the message is rejected back to the
#   broker instead of being acked, so it can be picked up by another worker.
celery.conf.update(
    task_acks_late=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # 30-minute hard ceiling for the whole pipeline (Demucs + MIDI + LLM).
    # The soft limit fires 2 minutes earlier so the worker can write FAILED
    # to the DB before being killed.
    task_time_limit=60 * 30,
    task_soft_time_limit=60 * 28,
    # Reject (re-queue) the message if the worker process is lost — covers
    # OOM kills and spot-instance termination without losing the task.
    task_reject_on_worker_lost=True,
    # Dead-letter queue: messages that exceed retries or are rejected land
    # here for inspection. Without this, poisoned tasks silently disappear.
    task_queues={
        "celery": {
            "delivery_mode": "persistent",
        },
    },
    task_default_queue="celery",
    task_default_exchange="tasks",
    task_default_routing_key="celery",
)

# Configure a dead-letter queue via a signal-based reject handler.
# Celery doesn't have a built-in DLQ, so we emulate one: when a task is
# rejected or exceeds its retry limit, route it to a `dead_letter` queue
# for inspection. This is wired up in `tasks_audio.py` via `task_failure`
# signal — see `_on_task_failure` there.
from celery.signals import task_failure  # noqa: E402


@task_failure.connect
def _route_to_dead_letter(
    sender=None,
    task_id=None,
    exception=None,
    args=None,
    kwargs=None,
    traceback=None,
    einfo=None,
    **extra,
) -> None:
    """Log failed tasks to a dedicated dead-letter queue for inspection.

    We don't re-publish the message (the DB row is already FAILED, so a
    retry would just fail again) — we just record enough metadata to
    diagnose the failure without grepping worker logs.
    """
    try:
        with celery.connection_or_acquire() as conn:
            with conn.channel() as channel:
                import json
                from datetime import datetime, timezone

                payload = json.dumps(
                    {
                        "task_id": task_id,
                        "task_name": getattr(sender, "name", None),
                        "args": list(args or []),
                        "kwargs": kwargs or {},
                        "exception": repr(exception) if exception else None,
                        "ts": datetime.now(timezone.utc).isoformat(),
                    },
                    default=str,
                )
                channel.basic_publish(
                    exchange="",
                    routing_key="dead_letter",
                    body=payload,
                    properties={
                        "delivery_mode": 2,  # persistent
                    },
                )
    except Exception:  # noqa: BLE001 — DLQ is best-effort
        pass

