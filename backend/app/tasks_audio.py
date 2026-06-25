"""Celery tasks that drive the audio pipeline.

The actual work lives in `app.workers.audio_worker.process_task` so it can
also be invoked directly (CLI, tests, scripts). The Celery binding here is
a thin adapter: dispatch via Redis, run the same function on a worker.
"""
from __future__ import annotations

import logging

from app.celery_app import celery
from app.workers import audio_worker

logger = logging.getLogger(__name__)


@celery.task(
    name="app.process_audio_task",
    bind=True,
    acks_late=True,
    max_retries=0,  # the worker already writes FAILED to the DB; no auto-retry
)
def process_audio_task(self, task_id: int) -> dict:
    """Run the Demucs pipeline for one task.

    The task id is the only thing that travels through Redis; the heavy
    audio data stays on disk. Returns a tiny status dict so callers that
    use `AsyncResult` get a sane return value.
    """
    logger.info("celery task %s picked up audio task %s", self.request.id, task_id)
    audio_worker.process_task(task_id)
    return {"task_id": task_id, "status": "FINISHED"}
