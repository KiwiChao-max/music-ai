"""Task-level orchestration endpoints.

Three thin endpoints that ride on top of the existing `audio_tasks` model:

    POST /api/tasks/{id}/process  ->  202 Accepted, dispatches a Celery task
    GET  /api/tasks/{id}/status   ->  { status, progress }
    GET  /api/tasks/{id}/stems    ->  [ { name, url }, ... ]

The Celery worker (running separately as `celery -A app.celery_app worker`)
picks the task up off Redis, runs the Demucs pipeline, and writes progress
back to the DB. The frontend is expected to follow up with GET /status
(poll) and finally GET /stems (once the task is FINISHED).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import OptionalAuthUser, OptionalUser, check_task_ownership
from app.config import settings
from app.db.models import AudioTaskStatus
from app.db.session import get_db
from app.schemas.audio import MusicAnalysisResponse, ProcessResponse, StemInfo, TaskStatusResponse
from app.services import (
    auth_service,
    file_service,
    midi_mapping_service,
    task_service,
    user_service,
)
from app.tasks_audio import process_audio_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


# Suffixes that count as an "output file" the worker can produce.
#   * `.wav/.mp3/.flac/.ogg/.m4a` --- Demucs audio stems
#   * `.mid/.midi`                --- Basic Pitch MIDI transcription
# The API sorts audio first, then MIDI, so the UI can group them visually.
_AUDIO_SUFFIXES: set[str] = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
_MIDI_SUFFIXES: set[str] = {".mid", ".midi"}
_OUTPUT_SUFFIXES: set[str] = _AUDIO_SUFFIXES | _MIDI_SUFFIXES


def _artifact_url(
    task_id: int,
    scope: str,
    filename: str,
    user_id: int | None,
) -> str:
    """Build a task-scoped artifact URL with a short-lived signed download token.

    When ``user_id`` is None (anonymous dev mode), the URL has no token ---
    the download endpoint will rely on the ownership check that allows
    anonymous access to unowned tasks in dev mode.

    For authenticated users the token is a dedicated "download" JWT (not
    the user's access token) scoped to this specific task/scope/filename
    with a 5-minute TTL. This avoids putting long-lived access tokens
    into URLs where they could leak via server logs, browser history,
    or Referer headers.
    """
    from urllib.parse import quote

    encoded_name = quote(filename, safe="")
    base = f"/api/tasks/{task_id}/files/{scope}/{encoded_name}"
    if user_id is None:
        return base
    dl_token = auth_service.create_download_token(user_id, task_id, scope=scope, filename=filename)
    return f"{base}?token={dl_token}"


def _validate_artifact_filename(filename: str) -> None:
    """Reject path traversal attempts in artifact filenames."""
    if filename != Path(filename).name:
        raise HTTPException(status_code=404, detail="artifact not found")


# ---------------------------------------------------------------------------
# POST /api/tasks/{id}/process
# ---------------------------------------------------------------------------
@router.post(
    "/{task_id}/process",
    status_code=202,
    response_model=ProcessResponse,
)
def start_processing(
    task_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> ProcessResponse:
    """Kick off the Demucs pipeline for an uploaded task.

    - 404 if the task does not exist
    - 403 if the task belongs to another user
    - 409 if the task is already PROCESSING or FINISHED (one job per task;
      re-upload to retry a finished one)
    - 202 + spawn worker if the task is UPLOADED or FAILED (retry)

    The state transition UPLOADED/FAILED -> PROCESSING is done atomically
    inside a single SQL UPDATE, so two concurrent /process calls cannot both
    spawn a worker for the same task.
    """
    # Pre-check ownership before the atomic claim so we return 403 (not 409)
    # for other users' tasks.
    existing = task_service.get_task(db, task_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="task not found")
    check_task_ownership(existing, user)

    task = task_service.claim_for_processing(db, task_id)
    if task is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"task is in state {existing.status.value}; "
                "only UPLOADED or FAILED tasks can be started"
            ),
        )

    # Hand off to the Celery worker. The DB has already been flipped to
    # PROCESSING by `claim_for_processing`, so the worker just runs the
    # pipeline and writes progress / stems / final status. If the worker is
    # not running, the message sits in Redis and is processed when one comes
    # up --- the frontend will keep polling /status and see PROCESSING + 0%.
    #
    # If the broker is unreachable (Redis down, DNS error, etc.), we
    # atomically roll the task back to UPLOADED so it doesn't get stuck in
    # PROCESSING forever.  The rollback is conditional on the task still
    # being in PROCESSING --- if the worker somehow already picked it up,
    # we fall back to marking it FAILED.
    try:
        async_result = process_audio_task.delay(task.id)
    except Exception as exc:  # broker down, DNS issue, etc.
        logger.exception("failed to dispatch task %s to celery: %s", task.id, exc)
        reason = "celery dispatch failed --- service unavailable"
        rolled_back = task_service.rollback_claim(db, task.id, reason=reason)
        if rolled_back is None:
            # The task was already claimed by a worker (unlikely but
            # possible).  Mark it FAILED so the user knows something
            # went wrong rather than seeing it stuck in PROCESSING.
            task_service.mark_failed_quick(db, task.id, reason=reason)
        raise HTTPException(
            status_code=503,
            detail=(
                "task could not be dispatched to the worker. "
                "The task has been reset --- please try again."
            ),
        ) from None
    logger.info("dispatched task %s to celery (celery_id=%s)", task.id, async_result.id)

    return ProcessResponse(task_id=task.id, status=task.status)


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}/status
# ---------------------------------------------------------------------------
@router.get(
    "/{task_id}/status",
    response_model=TaskStatusResponse,
)
def get_task_status(
    task_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> TaskStatusResponse:
    """Lightweight status snapshot for polling UIs."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    check_task_ownership(task, user)
    return TaskStatusResponse(status=task.status, progress=task.progress)


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}/stems
# ---------------------------------------------------------------------------
@router.get(
    "/{task_id}/stems",
    response_model=list[StemInfo],
)
def list_stems(
    task_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> list[StemInfo]:
    """Return the separated stems once the task has FINISHED.

    409 until then, so the frontend can show a clear "processing" state
    instead of getting an empty list and assuming "no stems exist".
    """
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    check_task_ownership(task, user)
    if task.status != AudioTaskStatus.FINISHED:
        raise HTTPException(
            status_code=409,
            detail=f"task not ready (status={task.status.value})",
        )

    audio_stems: list[StemInfo] = []
    midi_stems: list[StemInfo] = []

    # Uploaded original file(s).
    uid = user.id if user else None
    for name in file_service.list_upload_files(task):
        suffix = Path(name).suffix.lower()
        if suffix in _AUDIO_SUFFIXES:
            audio_stems.append(
                StemInfo(
                    name="original",
                    url=_artifact_url(task.id, "upload", name, uid),
                    kind="audio",
                )
            )
            break

    # Worker output files.
    for name in file_service.list_output_files(task):
        suffix = Path(name).suffix.lower()
        if suffix in _AUDIO_SUFFIXES:
            audio_stems.append(
                StemInfo(
                    name=Path(name).stem,
                    url=_artifact_url(task.id, "output", name, uid),
                    kind="audio",
                )
            )
        elif suffix in _MIDI_SUFFIXES:
            midi_stems.append(
                StemInfo(
                    name=Path(name).stem,
                    url=_artifact_url(task.id, "output", name, uid),
                    kind="midi",
                    profile=midi_mapping_service.midi_profile_from_name(Path(name).stem),
                )
            )
    return audio_stems + midi_stems


@router.get("/{task_id}/files/{scope}/{filename}")
def download_artifact(
    task_id: int,
    scope: str,
    filename: str,
    token: str | None = None,
    db: Session = Depends(get_db),
    user: OptionalUser = None,
):
    """Stream a task artifact after applying the normal ownership policy.

    Supports both Bearer header auth (for API clients) and ?token= query
    parameter auth (for <a href>/<audio>/<img> elements that can't set
    headers). The query parameter accepts a short-lived download token
    (not the user's access token) scoped to this specific file with a
    5-minute TTL, mitigating token leakage via logs/Referer/history.
    The query parameter is checked only when no Bearer token is present.

    Uses ``OptionalUser`` (instead of ``OptionalAuthUser``) because the
    ?token= parameter is an auth mechanism of its own; we enforce
    ``auth_required`` manually below after both auth paths are tried.

    The file is streamed from the configured storage backend (local
    filesystem or S3) so the API container does not need a shared volume
    mount.
    """
    if user is None and token:
        try:
            user_id = auth_service.verify_download_token(
                token,
                task_id=task_id,
                scope=scope,
                filename=filename,
            )
            user = user_service.get_user(db, user_id)
        except ValueError:
            raise HTTPException(
                status_code=401, detail="invalid or expired download token"
            ) from None

    # Enforce auth_required for production: if no user was resolved via
    # either Bearer header or ?token=, and auth is required, reject.
    if settings.auth_required and user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    check_task_ownership(task, user)
    _validate_artifact_filename(filename)

    if scope == "upload":
        file_obj = file_service.open_upload_file(task)
    elif scope == "output":
        file_obj = file_service.open_output_file(task, filename)
    else:
        raise HTTPException(status_code=404, detail="artifact not found")

    suffix = Path(filename).suffix.lower()
    media_type = _media_type_for_suffix(suffix)

    # 获取文件路径后关闭 storage 层打开的文件句柄，改用 FileResponse
    # 直接读文件，原生支持 Range 请求，浏览器拖动进度条时才能正确
    # seek 到指定位置，而不是从头重新播放。
    file_path = file_obj.name
    file_obj.close()

    return FileResponse(
        file_path,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )


def _media_type_for_suffix(suffix: str) -> str:
    """Map file extension to a reasonable MIME type."""
    mapping = {
        ".wav": "audio/wav",
        ".wave": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".mid": "audio/midi",
        ".midi": "audio/midi",
    }
    return mapping.get(suffix, "application/octet-stream")


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}/analysis
# ---------------------------------------------------------------------------
@router.get(
    "/{task_id}/analysis",
    response_model=MusicAnalysisResponse,
)
def get_task_analysis(
    task_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> MusicAnalysisResponse:
    """Return generated music analysis once the task has FINISHED."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    check_task_ownership(task, user)
    if task.status != AudioTaskStatus.FINISHED:
        raise HTTPException(
            status_code=409,
            detail=f"task not ready (status={task.status.value})",
        )

    # Read analysis.json from the storage backend (works for both
    # local filesystem and S3).
    import json

    try:
        file_obj = file_service.open_output_file(task, "analysis.json")
        analysis = json.load(file_obj)
        file_obj.close()
    except Exception:
        raise HTTPException(status_code=404, detail="analysis not found") from None
    # Attach the LLM commentary if the worker produced one. The two live
    # side by side so the UI can show "the AI's take" without a second
    # round-trip.
    payload = dict(analysis)
    # Normalize detected_instruments: support both
    # [{"instrument":..., "probability":...}] (dict) and
    # [[name, prob], ...] (list) formats.
    raw_instruments = payload.get("detected_instruments")
    if isinstance(raw_instruments, list) and raw_instruments:
        normalized = []
        for item in raw_instruments:
            if isinstance(item, dict) and "instrument" in item:
                normalized.append(
                    {"instrument": item["instrument"], "probability": item.get("probability", 0)}
                )
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                normalized.append({"instrument": str(item[0]), "probability": float(item[1])})
            else:
                normalized.append(item)
        payload["detected_instruments"] = normalized
    payload["commentary"] = task.commentary
    payload["commentary_model"] = task.commentary_model
    payload["commentary_generated_at"] = (
        task.commentary_generated_at.isoformat() if task.commentary_generated_at else None
    )
    return MusicAnalysisResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}/commentary
# ---------------------------------------------------------------------------
@router.get("/{task_id}/commentary")
def get_task_commentary(
    task_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> dict:
    """Return just the LLM commentary (and metadata) for a task.

    Kept as its own endpoint so the detail page can fetch commentary
    lazily (only when the user scrolls to it) without re-downloading
    the whole analysis JSON.
    """
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    check_task_ownership(task, user)
    return {
        "task_id": task.id,
        "commentary": task.commentary,
        "commentary_model": task.commentary_model,
        "commentary_generated_at": (
            task.commentary_generated_at.isoformat() if task.commentary_generated_at else None
        ),
    }
