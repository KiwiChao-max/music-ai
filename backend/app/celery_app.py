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
celery.conf.update(
    task_acks_late=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
