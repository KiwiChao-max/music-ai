"""AudioTask CRUD + status transitions.

Keeps all DB-facing logic in one module so the API layer, the worker, and any
future background jobs share a single source of truth.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import AudioTask, AudioTaskStatus


def safe_filename(name: str) -> str:
    """Keep only the basename and strip path separators to avoid escapes."""
    return Path(name).name or "upload.bin"


# ---- reads ----------------------------------------------------------------
def list_tasks(
    db: Session,
    *,
    limit: int = 100,
    offset: int = 0,
    user_id: int | None = None,
    public_only: bool = False,
    status: AudioTaskStatus | None = None,
) -> list[AudioTask]:
    """Return tasks, newest first. When `user_id` is set, filter the
    list to that user (used by non-admin endpoints to scope to "my
    tasks"). `None` means "no filter" (admin view). When
    `public_only` is True, only tasks with ``user_id IS NULL`` are
    returned (used for anonymous callers in open-auth mode).
    When `status` is set, filter to that specific status.
    """
    stmt = select(AudioTask)
    if user_id is not None:
        stmt = stmt.where(AudioTask.user_id == user_id)
    elif public_only:
        stmt = stmt.where(AudioTask.user_id.is_(None))
    if status is not None:
        stmt = stmt.where(AudioTask.status == status)
    stmt = (
        stmt.order_by(AudioTask.created_at.desc(), AudioTask.id.desc()).limit(limit).offset(offset)
    )
    return list(db.scalars(stmt).all())


def get_task(db: Session, task_id: int) -> AudioTask | None:
    return db.get(AudioTask, task_id)


def count_tasks_by_status(db: Session) -> dict[str, int]:
    """Return `{status.value: count}` for every status, filling missing
    statuses with 0 so the metrics endpoint can publish a stable
    label set."""
    stmt = select(AudioTask.status, func.count(AudioTask.id)).group_by(AudioTask.status)
    counts = {status.value: 0 for status in AudioTaskStatus}
    for status_value, count in db.execute(stmt).all():
        counts[status_value.value] = int(count)
    return counts


# ---- writes ---------------------------------------------------------------
def create_task(db: Session, filename: str, *, user_id: int | None = None) -> AudioTask:
    task = AudioTask(
        filename=safe_filename(filename),
        status=AudioTaskStatus.UPLOADED,
        user_id=user_id,
    )
    db.add(task)
    db.flush()  # populate task.id without committing
    return task


def delete_task(db: Session, task_id: int) -> AudioTask | None:
    task = db.get(AudioTask, task_id)
    if task is not None:
        db.delete(task)
        db.flush()
    return task


def set_status(db: Session, task: AudioTask, status: AudioTaskStatus) -> None:
    task.status = status
    db.add(task)
    db.flush()


def claim_for_processing(db: Session, task_id: int) -> AudioTask | None:
    """Atomically flip UPLOADED/FAILED -> PROCESSING.

    Returns the task on success, or `None` if the task does not exist OR is
    already in a terminal-running state (PROCESSING / FINISHED). The atomic
    UPDATE prevents two concurrent POST /process calls from both spawning a
    worker for the same task.
    """
    stmt = (
        update(AudioTask)
        .where(
            AudioTask.id == task_id,
            AudioTask.status.in_([AudioTaskStatus.UPLOADED, AudioTaskStatus.FAILED]),
        )
        .values(status=AudioTaskStatus.PROCESSING)
        .returning(AudioTask)
    )
    task = db.execute(stmt).scalar_one_or_none()
    if task is not None:
        db.commit()
        db.refresh(task)
    return task


def rollback_claim(db: Session, task_id: int, *, reason: str | None = None) -> AudioTask | None:
    """Atomically revert a PROCESSING task back to UPLOADED.

    Only touches the row if it is **still** in PROCESSING --- if the worker
    already picked the task up and started working, we must NOT overwrite
    its status.  Returns the task on success, or `None` if the task was
    no longer in PROCESSING (worker already started, or already finished).

    This is the counterpart to ``claim_for_processing``: when Celery
    dispatch fails (broker down, etc.), call this to undo the claim so
    the user can retry.
    """
    from datetime import datetime

    stmt = (
        update(AudioTask)
        .where(
            AudioTask.id == task_id,
            AudioTask.status == AudioTaskStatus.PROCESSING,
        )
        .values(
            status=AudioTaskStatus.UPLOADED,
            progress=0,
            current_step=None,
            error_message=reason,
            finished_at=None,
            updated_at=datetime.now(UTC),
        )
        .returning(AudioTask)
    )
    task = db.execute(stmt).scalar_one_or_none()
    if task is not None:
        db.commit()
        db.refresh(task)
    return task


def mark_failed_quick(db: Session, task_id: int, *, reason: str) -> AudioTask | None:
    """Atomically move a PROCESSING task to FAILED with a short reason.

    Used when we can't even roll back to UPLOADED (e.g. the task was
    already claimed by the worker somehow).  The user can still see the
    failure reason and re-upload.
    """
    from datetime import datetime

    stmt = (
        update(AudioTask)
        .where(
            AudioTask.id == task_id,
            AudioTask.status == AudioTaskStatus.PROCESSING,
        )
        .values(
            status=AudioTaskStatus.FAILED,
            error_message=reason,
            finished_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        .returning(AudioTask)
    )
    task = db.execute(stmt).scalar_one_or_none()
    if task is not None:
        db.commit()
        db.refresh(task)
    return task


def worker_claim(db: Session, task_id: int) -> AudioTask | None:
    """Atomically claim a task for worker execution.

    Allows transitions from:
    * UPLOADED or FAILED → PROCESSING (normal case)
    * PROCESSING + current_step IS NULL → PROCESSING (API claimed via
      ``claim_for_processing`` but no worker has started yet). This is the
      common path because the API dispatches the Celery task *after* calling
      ``claim_for_processing``.
    * PROCESSING + updated_at past ``CLAIM_TIMEOUT_SECONDS`` (stale-claim
      recovery: the previous worker crashed/OOM'd). Without this timeout, a
      single worker crash leaves a task permanently stuck in PROCESSING.

    Returns the task on success, or ``None`` if the task is no longer
    claimable (e.g. it was already marked FINISHED or cancelled).

    This mirrors ``claim_for_processing`` but is used by the Celery worker
    at the start of ``process_task`` to guard against double-processing
    after a worker crash/OOM causes message redelivery.
    """
    from datetime import datetime, timedelta

    # Stale-claim threshold: generous enough that a legitimately long task
    # (Demucs on a 10-minute track can take 15-25 minutes on CPU) never gets
    # pre-empted, but short enough that crashed tasks are recovered without
    # manual intervention. Matches celery's task_time_limit (30 min) + buffer.
    claim_timeout_seconds = 60 * 35

    stale_cutoff = datetime.now(UTC) - timedelta(seconds=claim_timeout_seconds)

    stmt = (
        update(AudioTask)
        .where(
            AudioTask.id == task_id,
            # Claimable states:
            #   1. Freshly uploaded or previously failed (normal path).
            #   2. PROCESSING but current_step IS NULL (API claimed it, no
            #      worker has started yet — the common case).
            #   3. Stuck in PROCESSING with no recent update (crashed worker).
            (AudioTask.status.in_([AudioTaskStatus.UPLOADED, AudioTaskStatus.FAILED]))
            | (
                (AudioTask.status == AudioTaskStatus.PROCESSING)
                & (AudioTask.current_step.is_(None))
            )
            | (
                (AudioTask.status == AudioTaskStatus.PROCESSING)
                & (AudioTask.updated_at < stale_cutoff)
            ),
        )
        .values(
            status=AudioTaskStatus.PROCESSING,
            progress=0,
            current_step="Starting...",
            error_message=None,
            finished_at=None,
            updated_at=datetime.now(UTC),
        )
        .returning(AudioTask)
    )
    task = db.execute(stmt).scalar_one_or_none()
    if task is not None:
        db.commit()
        db.refresh(task)
    return task


def set_progress(
    db: Session,
    task: AudioTask,
    progress: int,
    current_step: str | None = None,
) -> None:
    """Update the progress bar + human-readable step label. Clamped 0-100."""
    task.progress = max(0, min(100, int(progress)))
    if current_step is not None:
        task.current_step = current_step
    db.add(task)
    db.flush()


def set_duration(db: Session, task: AudioTask, duration: float | None) -> None:
    task.duration = duration
    db.add(task)
    db.flush()


def set_output_dir(db: Session, task: AudioTask, output_dir: str | None) -> None:
    task.output_dir = output_dir
    db.add(task)
    db.flush()


def mark_finished(
    db: Session,
    task: AudioTask,
    success: bool,
    error_message: str | None = None,
) -> None:
    """Move to FINISHED (or FAILED with error) and stamp `finished_at`."""
    from datetime import datetime

    task.status = AudioTaskStatus.FINISHED if success else AudioTaskStatus.FAILED
    task.progress = 100 if success else task.progress
    task.error_message = None if success else error_message
    task.finished_at = datetime.now(UTC)
    db.add(task)
    db.flush()
