"""Celery tasks that drive the audio pipeline.

The actual work lives in `app.workers.audio_worker.process_task` so it can
also be invoked directly (CLI, tests, scripts). The Celery binding here is
a thin adapter: dispatch via Redis, run the same function on a worker.
"""
from __future__ import annotations

import logging

from celery.exceptions import SoftTimeLimitExceeded

from app.celery_app import celery
from app.db.session import SessionLocal
from app.db.models import AudioTaskStatus
from app.services import task_service
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
    try:
        audio_worker.process_task(task_id)
    except SoftTimeLimitExceeded:
        # Soft time limit (28 min) fired --- we have ~2 min before the hard
        # kill. Mark the task as FAILED so the client isn't left waiting
        # on a PROCESSING row that will never update.
        logger.error("celery task %s exceeded soft time limit for task %s", self.request.id, task_id)
        try:
            with SessionLocal() as db:
                task = task_service.get_task(db, task_id)
                if task is not None and task.status == AudioTaskStatus.PROCESSING:
                    task_service.mark_finished(
                        db,
                        task,
                        success=False,
                        error_message="task exceeded the 28-minute soft time limit",
                    )
                    db.commit()
        except Exception:  # noqa: BLE001 --- best-effort cleanup before kill
            logger.exception("failed to mark task %s as FAILED after soft time limit", task_id)
        raise
    return {"task_id": task_id, "status": "FINISHED"}
