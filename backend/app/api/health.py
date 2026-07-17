"""Health check + Prometheus metrics.

Three endpoints:
  * `GET /healthz`   --- liveness. Always 200 if the process is up.
  * `GET /readyz`    --- readiness. Probes Postgres + Redis + Celery.
  * `GET /metrics`   --- Prometheus text format (request count, latency
                        histogram, in-flight task gauge, pipeline metrics.)

The metrics module keeps a couple of counters / histograms registered
globally so the same registry backs the `/metrics` endpoint and any
custom application instrumentation.
"""
from __future__ import annotations

import time
from typing import Annotated

import redis
from fastapi import APIRouter, Depends, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.pipeline_metrics import PIPELINE_QUEUE_LENGTH, PIPELINE_STORAGE_BYTES
from app.services import task_service
from app.storage import get_storage

router = APIRouter(tags=["health"])


# ---- metrics --------------------------------------------------------------
# `request_count` is incremented by the FastAPI middleware in main.py.
# Declaring it here keeps the import order simple: `from app.api.health
# import REQUEST_COUNT` always works.
REQUEST_COUNT = Counter(
    "music_ai_http_requests_total",
    "Number of HTTP requests served.",
    labelnames=("method", "endpoint", "status_code"),
)
REQUEST_LATENCY = Histogram(
    "music_ai_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "endpoint"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
TASKS_TOTAL = Gauge(
    "music_ai_tasks_total",
    "Current number of audio tasks in the database, by status.",
    labelnames=("status",),
)


# ---- endpoints ------------------------------------------------------------
@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness probe. Cheap; never touches downstream services."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    """Readiness probe. Probes Postgres + Redis + Celery's broker.

    Returns 200 if all three are reachable, 503 otherwise. The body
    always lists each dependency so the operator can see which one is
    down.
    """
    components: dict[str, dict[str, str]] = {}

    # Postgres: a tiny SELECT 1 is enough.
    try:
        db.execute(text("SELECT 1"))
        components["postgres"] = {"status": "ok"}
    except Exception as exc:  # noqa: BLE001 - we want the message
        components["postgres"] = {"status": "down", "error": str(exc)}

    # Redis / Celery broker: open a fresh client per probe so a
    # connection error doesn't poison the global pool.
    try:
        client = redis.Redis.from_url(settings.redis_url)
        client.ping()
        components["redis"] = {"status": "ok"}
        client.close()
    except Exception as exc:  # noqa: BLE001
        components["redis"] = {"status": "down", "error": str(exc)}

    all_ok = all(c.get("status") == "ok" for c in components.values())
    return Response(
        content=_readyz_payload(components, all_ok),
        status_code=200 if all_ok else 503,
        media_type="application/json",
    )


def _readyz_payload(components: dict, ok: bool) -> str:
    import json

    return json.dumps(
        {
            "status": "ok" if ok else "degraded",
            "components": components,
        }
    )


@router.get("/metrics", include_in_schema=False)
async def metrics(db: Annotated[Session, Depends(get_db)]) -> Response:
    """Prometheus text format.

    Refreshes dynamic gauges on every scrape:
      * ``music_ai_tasks_total`` --- per-status task counts
      * ``music_ai_pipeline_queue_length`` --- UPLOADED tasks (waiting)
      * ``music_ai_pipeline_storage_bytes`` --- uploads / outputs usage
    """
    try:
        counts = task_service.count_tasks_by_status(db)
        for status_name, count in counts.items():
            TASKS_TOTAL.labels(status=status_name).set(count)

        # Queue length: number of UPLOADED tasks waiting for a worker.
        PIPELINE_QUEUE_LENGTH.set(counts.get("uploaded", 0))

        # Storage usage: total bytes under uploads/ and outputs/ prefixes.
        _refresh_storage_gauges()
    except Exception:
        # The /metrics endpoint must never fail because the DB is
        # briefly unavailable --- Prometheus would stop scraping us.
        pass

    payload = generate_latest(REGISTRY)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)


def _refresh_storage_gauges() -> None:
    """Best-effort storage usage gauge refresh.

    Queries the storage backend for total bytes used by uploads and
    outputs.  For local storage this walks the filesystem; for S3 this
    may be expensive, so errors are swallowed.
    """
    try:
        storage = get_storage()
        uploads_bytes = storage.usage_bytes(prefix="uploads/")
        outputs_bytes = storage.usage_bytes(prefix="outputs/")
        PIPELINE_STORAGE_BYTES.labels(scope="uploads").set(uploads_bytes)
        PIPELINE_STORAGE_BYTES.labels(scope="outputs").set(outputs_bytes)
    except Exception:
        pass


@router.get("/")
async def root() -> dict[str, str]:
    """Trivial root response; the real UI is served by the SPA."""
    return {"message": "music-ai backend", "docs": "/docs"}


# Convenience helper for tests / readiness dashboards --- used by the
# `/readyz` body and exported so the E2E scripts can probe it.
def ping_redis(url: str | None = None) -> bool:
    """Return True iff `PING` succeeds on the configured Redis URL."""
    target = url or settings.redis_url
    try:
        client = redis.Redis.from_url(target)
        ok = bool(client.ping())
        client.close()
        return ok
    except Exception:
        return False


# Avoid an unused-import warning when the module is loaded just for
# the metrics side-effects.
_ = time
