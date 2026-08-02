"""Celery application instance.

Start a worker with::

    cd backend
    celery -A app.celery_app:celery worker --loglevel=info

The API process imports ``app.celery_app`` so it can dispatch tasks; the
worker process imports the same module to discover registered tasks.
Keep this file thin: configuration only, no task bodies.

Queue topology
--------------

Two named queues separate heavy audio work from light housekeeping:

* ``audio_heavy`` --- Demucs, Basic Pitch, ADTOS (CPU / GPU / memory
  heavy).  A dedicated worker pool with low concurrency (resource-aware)
  consumes this queue.
* ``celery`` (default) --- everything else: health checks, future
  lightweight tasks.  A high-concurrency worker pool can consume this
  queue without blocking heavy work.

Tasks are routed by name: ``app.process_audio_task`` goes to
``audio_heavy``; everything else stays on ``celery``.  This prevents
a backlog of lightweight tasks from starving the heavy pipeline, and
vice versa.
"""

from __future__ import annotations

import logging
from datetime import UTC

from app.config import settings
from app.logging_config import setup_logging

# Initialise logging before any module-level loggers are created.
setup_logging()

from celery import Celery  # noqa: E402
from kombu import Exchange, Queue  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exchanges & queues
# ---------------------------------------------------------------------------
_default_exchange = Exchange("tasks", type="direct", durable=True)

_audio_heavy_queue = Queue(
    "audio_heavy",
    exchange=_default_exchange,
    routing_key="audio_heavy",
    durable=True,
)

_default_queue = Queue(
    "celery",
    exchange=_default_exchange,
    routing_key="celery",
    durable=True,
)

# ---------------------------------------------------------------------------
# Resource probe (best-effort --- non-Linux platforms skip gracefully)
# ---------------------------------------------------------------------------
_worker_concurrency = settings.worker_concurrency
if _worker_concurrency <= 0:
    try:
        from app.worker_probe import probe_resources, resolve_concurrency

        _resources = probe_resources()
        _worker_concurrency = resolve_concurrency(
            explicit=None,  # don't pass explicit=0, it would override
            resources=_resources,
        )
    except Exception:
        logger.warning("worker resource probe failed; falling back to concurrency=2")
        _worker_concurrency = 2

# Apply the explicit override if the operator set WORKER_CONCURRENCY > 0.
if settings.worker_concurrency > 0:
    _worker_concurrency = settings.worker_concurrency

logger.info(
    "celery: concurrency=%d, max_memory_per_child=%d MiB, max_tasks_per_child=%d",
    _worker_concurrency,
    settings.worker_max_memory_per_child_mb,
    settings.worker_max_tasks_per_child,
)

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------
celery = Celery(
    "music_ai",
    broker=settings.resolved_celery_broker_url,
    backend=settings.resolved_celery_result_backend,
    include=["app.tasks_audio", "app.tasks_scheduled"],
)

# Sensible defaults for a CPU-bound audio pipeline.
#
# * ``acks_late=True`` --- if the worker dies mid-task, the message is
#   re-queued instead of being lost. The DB row stays in PROCESSING and
#   the next worker picks it up; ``claim_for_processing`` already guards
#   against double-execution so this is safe.
# * ``task_track_started=True`` --- the result is set to STARTED as soon as
#   the task begins executing, useful for debugging but not required by
#   the API contract (the API polls the DB, not the Celery result).
# * ``worker_prefetch_multiplier=1`` --- long-running tasks should not be
#   batched; one task per worker at a time keeps memory bounded and
#   avoids head-of-line blocking.
# * ``task_acks_on_failure_or_timeout=False`` (default) --- the message is
#   re-queued on unhandled exceptions, but the worker has already marked
#   the DB row as FAILED inside ``process_task``, so the retry would just
#   fail again. If you want auto-retry, wrap the task with ``autoretry_for``.
# * ``task_time_limit`` --- hard kill deadline. Demucs on a 5-minute track
#   takes ~6-10 minutes on CPU; 30 minutes is a generous ceiling that
#   still catches infinite loops / deadlocks. The worker is killed by
#   SIGKILL, so ``acks_late`` re-queues the message and the next worker
#   claims it via ``claim_for_processing``.
# * ``task_soft_time_limit`` --- SoftTimeLimitExceeded is raised 2 minutes
#   before the hard kill, giving the task a chance to mark itself FAILED
#   in the DB before the worker dies.
# * ``task_reject_on_worker_lost=True`` --- when the worker process is
#   killed (OOM, spot instance termination), the message is rejected
#   back to the broker instead of being acked, so another worker can
#   pick it up.
# * ``worker_max_memory_per_child`` --- after a worker child exceeds this
#   RSS, it is gracefully replaced *after* the current task finishes.
#   Prevents slow memory leaks from accumulating across tasks.
# * ``worker_max_tasks_per_child`` --- belt-and-suspenders: recycle the
#   child after N tasks even if RSS stays under the limit.
celery.conf.update(
    # --- task execution ---
    task_acks_late=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Time limits.
    task_time_limit=60 * 30,
    task_soft_time_limit=60 * 28,
    task_reject_on_worker_lost=True,
    # --- queue topology ---
    task_queues=(_audio_heavy_queue, _default_queue),
    task_default_queue="celery",
    task_default_exchange="tasks",
    task_default_routing_key="celery",
    # Route audio tasks to the heavy queue; everything else stays on default.
    task_routes={
        "app.process_audio_task": {
            "queue": "audio_heavy",
            "routing_key": "audio_heavy",
        },
    },
    # --- worker pool ---
    worker_concurrency=_worker_concurrency,
    # Memory protection: recycle bloated children.
    worker_max_memory_per_child=(
        settings.worker_max_memory_per_child_mb * 1024
        if settings.worker_max_memory_per_child_mb > 0
        else None
    ),
    worker_max_tasks_per_child=(
        settings.worker_max_tasks_per_child if settings.worker_max_tasks_per_child > 0 else None
    ),
    # --- beat schedule (periodic tasks) ---
    # Run at 03:00 UTC daily so cleanup doesn't interfere with peak hours.
    beat_schedule={
        "purge-expired-tokens-daily": {
            "task": "app.purge_expired_tokens",
            "schedule": 60 * 60 * 24,  # once per day (seconds)
            "options": {"queue": "celery", "expires": 60 * 60},  # skip if an hour late
        },
        "cleanup-old-tasks-daily": {
            "task": "app.cleanup_old_tasks",
            "schedule": 60 * 60 * 24,
            "options": {"queue": "celery", "expires": 60 * 60},
        },
    },
)

# ---------------------------------------------------------------------------
# Dead-letter queue (signal-based, best-effort)
# ---------------------------------------------------------------------------
# Celery doesn't have a built-in DLQ, so we emulate one: when a task is
# rejected or exceeds its retry limit, route it to a ``dead_letter`` queue
# for inspection. This is wired up in ``tasks_audio.py`` via ``task_failure``
# signal --- see ``_on_task_failure`` there.
from celery.signals import after_setup_logger, after_setup_task_logger, task_failure  # noqa: E402


@after_setup_logger.connect
def _configure_celery_logger(logger, loglevel, format, colorize, **_kwargs):
    """Replace Celery's default formatter with our unified JSON/text formatter."""
    import sys

    from app.logging_config import JsonFormatter, RequestIdFilter, TextFormatter

    # Remove Celery's default handlers and re-add ours so the format is
    # consistent across API and worker processes.
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    import logging

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(loglevel or logging.INFO)
    stream.addFilter(RequestIdFilter())
    if settings.log_json:
        stream.setFormatter(JsonFormatter())
    else:
        use_color = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()
        stream.setFormatter(TextFormatter(use_color=use_color))
    logger.addHandler(stream)
    logger.setLevel(loglevel or getattr(logging, settings.log_level.upper(), logging.INFO))


# Apply the same configuration to the per-task logger (celery.task).
after_setup_task_logger.connect(_configure_celery_logger)


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
    retry would just fail again) --- we just record enough metadata to
    diagnose the failure without grepping worker logs.
    """
    try:
        with celery.connection_or_acquire() as conn, conn.channel() as channel:
            import json
            from datetime import datetime

            payload = json.dumps(
                {
                    "task_id": task_id,
                    "task_name": getattr(sender, "name", None),
                    "args": list(args or []),
                    "kwargs": kwargs or {},
                    "exception": repr(exception) if exception else None,
                    "ts": datetime.now(UTC).isoformat(),
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
    except Exception:
        pass
