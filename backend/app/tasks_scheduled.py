"""Scheduled / periodic Celery tasks (driven by Celery Beat).

These tasks are lightweight housekeeping jobs: purging expired tokens,
cleaning up old finished task artifacts, etc. They run on the default
queue (low concurrency is fine --- they are IO-bound and quick).
"""

from __future__ import annotations

import logging

from app.celery_app import celery
from app.db.session import SessionLocal
from app.services import auth_service, file_service

logger = logging.getLogger(__name__)


@celery.task(name="app.purge_expired_tokens", max_retries=0)
def purge_expired_tokens() -> int:
    """Delete refresh-token rows whose ``expires_at`` is in the past.

    Called by Celery Beat once per day. Returns the number of rows deleted.
    """
    with SessionLocal() as db:
        deleted = auth_service.purge_expired_tokens(db)
        db.commit()
    logger.info("purge_expired_tokens: removed %d expired token rows", deleted)
    return deleted


@celery.task(name="app.cleanup_old_tasks", max_retries=0)
def cleanup_old_tasks(max_age_days: int = 30) -> int:
    """Remove on-disk artifacts for tasks finished more than ``max_age_days`` ago.

    Called by Celery Beat once per day. Returns the number of task directories
    cleaned up.
    """
    deleted = file_service.cleanup_expired_tasks(max_age_days=max_age_days)
    logger.info(
        "cleanup_old_tasks: cleaned up %d task directories older than %d days",
        deleted,
        max_age_days,
    )
    return deleted
