"""Audio task REST endpoints."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import soundfile as sf
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import (
    CurrentUser,
    OptionalUser,
    get_current_user,
    get_current_user_optional,
)
from app.config import settings
from app.db.session import get_db
from app.schemas.audio import AudioTaskRead, UploadResponse
from app.services import file_service, task_service, user_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audio", tags=["audio"])


def _auth_user(
    user: OptionalUser,
) -> CurrentUser | None:
    """Optional auth gate: enforce the global `auth_required` flag.

    Returns the resolved user (or `None` for anonymous calls) so the
    endpoint can stamp `user_id` on newly created tasks.
    """
    if settings.auth_required and user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

_ALLOWED_AUDIO_EXTENSIONS = {
    ".aac",
    ".aiff",
    ".aif",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wave",
    ".webm",
    ".wma",
}


def _looks_like_audio(file: UploadFile) -> bool:
    content_type = (file.content_type or "").lower()
    if content_type.startswith("audio/"):
        return True
    suffix = Path(file.filename or "").suffix.lower()
    return suffix in _ALLOWED_AUDIO_EXTENSIONS


def _cleanup_failed_upload(db: Session, task_id: int) -> None:
    """Delete the task row and its on-disk files, swallowing file errors.

    DB and disk cleanup happen in two steps on purpose. We must commit the
    row deletion so a subsequent retry can create a fresh row with the same
    id, but we *also* want to remove the partially-written upload. If the
    filesystem operation fails, we log it and move on — the next upload for
    the same task will overwrite the partial file anyway, and
    `remove_task_files` is already best-effort (it uses
    `shutil.rmtree(ignore_errors=True)`).
    """
    task = task_service.delete_task(db, task_id)
    db.commit()
    if task is not None:
        try:
            file_service.remove_task_files(task)
        except Exception:  # noqa: BLE001 - cleanup must never raise
            logger.exception(
                "cleanup_failed_upload: file removal failed for task %s", task_id
            )


def _probe_duration(path: Path) -> float | None:
    """Best-effort duration probe using `soundfile` (WAV/FLAC/OGG/...).

    Returns `None` for formats soundfile cannot decode without an external
    decoder (e.g. some MP3 builds) so the worker can re-probe if needed.
    """
    try:
        info = sf.info(str(path))
    except (OSError, RuntimeError):
        return None
    if info.samplerate <= 0 or info.frames <= 0:
        return None
    return round(info.frames / float(info.samplerate), 2)


@router.post("/upload", response_model=UploadResponse, status_code=201)
def upload_audio(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> UploadResponse:
    """Persist the uploaded file and create an `audio_tasks` row.

    Flow: insert DB row (flush so we get the id), stream file to disk, then
    commit once. If the file write fails we roll the row back so state and
    disk stay in sync — no half-created task lingers in the DB.
    """
    if not _looks_like_audio(file):
        raise HTTPException(
            status_code=415,
            detail="only audio uploads are supported",
        )

    # Per-user quotas: the soft cap is checked before we touch the DB so
    # a user with N+1 pending uploads gets a 429 rather than a half-
    # created task that pollutes the admin view.
    if user is not None:
        active = user_service.count_active_tasks(db, user.id)
        if (
            user_service.effective_max_tasks(user) > 0
            and active >= user_service.effective_max_tasks(user)
        ):
            raise HTTPException(
                status_code=429,
                detail=(
                    f"task quota reached: {active} active task(s), "
                    f"limit is {user_service.effective_max_tasks(user)}"
                ),
            )
        per_user_max = user_service.effective_max_upload_bytes(user)
        effective_max = (
            min(per_user_max, settings.max_upload_bytes)
            if per_user_max > 0
            else settings.max_upload_bytes
        )
    else:
        effective_max = settings.max_upload_bytes

    task = task_service.create_task(
        db, file.filename or "upload.bin", user_id=getattr(user, "id", None)
    )
    db.flush()  # populate task.id without committing
    try:
        path = file_service.save_upload(
            task,
            file.file,
            max_bytes=effective_max,
        )
    except file_service.UploadTooLargeError as exc:
        db.rollback()
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise

    # Stamp the conventional output path and (best-effort) duration.
    output_dir = str(file_service.task_output_dir(task.id))
    task_service.set_output_dir(db, task, output_dir)
    task_service.set_duration(db, task, _probe_duration(path))
    db.commit()

    return UploadResponse(task_id=task.id)


@router.get("", response_model=list[AudioTaskRead])
def list_tasks(
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> list[AudioTaskRead]:
    """Return tasks, newest first. `limit` is clamped to [1, 500].

    When auth is enabled, the list is filtered to the caller's own
    tasks (admins see every task).
    """
    if limit < 1:
        limit = 1
    elif limit > 500:
        limit = 500
    if offset < 0:
        offset = 0
    only_user_id = None
    if user is not None and getattr(user, "role", None) != "admin":
        only_user_id = user.id
    return [
        AudioTaskRead.model_validate(t)
        for t in task_service.list_tasks(
            db, limit=limit, offset=offset, user_id=only_user_id
        )
    ]


@router.get("/{task_id}", response_model=AudioTaskRead)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> AudioTaskRead:
    """Return a single task by id."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if user is not None and getattr(user, "role", None) != "admin" and task.user_id not in (None, user.id):
        raise HTTPException(status_code=403, detail="not your task")
    return AudioTaskRead.model_validate(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> None:
    """Delete a task and its on-disk files (uploads + worker outputs)."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if user is not None and getattr(user, "role", None) != "admin" and task.user_id not in (None, user.id):
        raise HTTPException(status_code=403, detail="not your task")
    task_service.delete_task(db, task_id)
    db.commit()
    try:
        file_service.remove_task_files(task)
    except Exception:  # noqa: BLE001 - cleanup must never raise
        logger.exception("delete_task: file removal failed for task %s", task_id)
