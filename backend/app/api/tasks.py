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
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.api.deps import OptionalUser
from app.db.models import AudioTaskStatus
from app.db.session import get_db
from app.schemas.audio import MusicAnalysisResponse, ProcessResponse, StemInfo, TaskStatusResponse
from app.services import auth_service, file_service, midi_mapping_service, music_analysis_service, task_service, user_service
from app.tasks_audio import process_audio_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _auth_user(user: OptionalUser):
    """Optional auth gate matching audio.py's pattern."""
    if settings.auth_required and user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _check_ownership(task, user) -> None:
    """Raise 403 if the user is not the owner (or admin)."""
    if user is not None and getattr(user, "role", None) != "admin" and task.user_id not in (None, user.id):
        raise HTTPException(status_code=403, detail="not your task")

# Suffixes that count as an "output file" the worker can produce.
#   * `.wav/.mp3/.flac/.ogg/.m4a` — Demucs audio stems
#   * `.mid/.midi`                — Basic Pitch MIDI transcription
# The API sorts audio first, then MIDI, so the UI can group them visually.
_AUDIO_SUFFIXES: set[str] = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
_MIDI_SUFFIXES: set[str] = {".mid", ".midi"}
_OUTPUT_SUFFIXES: set[str] = _AUDIO_SUFFIXES | _MIDI_SUFFIXES


def _artifact_url(task_id: int, scope: str, file_path: Path) -> str:
    """Build a task-scoped artifact URL without exposing the storage layout."""
    from urllib.parse import quote

    return f"/api/tasks/{task_id}/files/{scope}/{quote(file_path.name, safe='')}"


def _artifact_path(task_id: int, scope: str, filename: str) -> Path:
    """Resolve one known task artifact while rejecting traversal attempts."""
    if filename != Path(filename).name:
        raise HTTPException(status_code=404, detail="artifact not found")
    if scope == "upload":
        directory = file_service.task_upload_dir(task_id)
    elif scope == "output":
        directory = file_service.task_output_dir(task_id)
    else:
        raise HTTPException(status_code=404, detail="artifact not found")
    candidate = directory / filename
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="artifact not found")
    return candidate


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
    user: Annotated[object | None, Depends(_auth_user)] = None,
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
    _check_ownership(existing, user)

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
    # up — the frontend will keep polling /status and see PROCESSING + 0%.
    try:
        async_result = process_audio_task.delay(task.id)
    except Exception as exc:  # broker down, DNS issue, etc.
        logger.exception("failed to dispatch task %s to celery: %s", task.id, exc)
        raise HTTPException(
            status_code=503,
            detail=(
                f"task is queued in the database but could not be dispatched: {exc}"
            ),
        )
    logger.info(
        "dispatched task %s to celery (celery_id=%s)", task.id, async_result.id
    )

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
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> TaskStatusResponse:
    """Lightweight status snapshot for polling UIs."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _check_ownership(task, user)
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
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> list[StemInfo]:
    """Return the separated stems once the task has FINISHED.

    409 until then, so the frontend can show a clear "processing" state
    instead of getting an empty list and assuming "no stems exist".
    """
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _check_ownership(task, user)
    if task.status != AudioTaskStatus.FINISHED:
        raise HTTPException(
            status_code=409,
            detail=f"task not ready (status={task.status.value})",
        )
    if not task.output_dir:
        return []

    out_dir = Path(task.output_dir)
    if not out_dir.is_dir():
        return []

    audio_stems: list[StemInfo] = []
    midi_stems: list[StemInfo] = []

    upload_dir = file_service.task_upload_dir(task.id)
    for candidate in sorted(upload_dir.glob("original.*")):
        if candidate.is_file() and candidate.suffix.lower() in _AUDIO_SUFFIXES:
            audio_stems.append(
                StemInfo(name="original", url=_artifact_url(task.id, "upload", candidate), kind="audio"),
            )
            break

    for f in sorted(out_dir.iterdir()):
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        if suffix in _AUDIO_SUFFIXES:
            audio_stems.append(StemInfo(name=f.stem, url=_artifact_url(task.id, "output", f), kind="audio"))
        elif suffix in _MIDI_SUFFIXES:
            midi_stems.append(
                StemInfo(
                    name=f.stem,
                    url=_artifact_url(task.id, "output", f),
                    kind="midi",
                    profile=midi_mapping_service.midi_profile_from_name(f.stem),
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
    user: Annotated[object | None, Depends(_auth_user)] = None,
):
    """Stream a task artifact after applying the normal ownership policy.

    Supports both Bearer header auth (for API clients) and ?token= query
    parameter auth (for <a href>/<audio>/<img> elements that can't set
    headers). The query parameter is checked only when no Bearer token is
    present.
    """
    from fastapi.responses import FileResponse

    if user is None and token:
        try:
            payload = auth_service.decode_token(token, expected_type="access")
            user_id = int(payload["sub"])
            user = user_service.get_user(db, user_id)
        except (ValueError, KeyError, TypeError):
            raise HTTPException(status_code=401, detail="invalid token")

    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _check_ownership(task, user)
    return FileResponse(_artifact_path(task.id, scope, filename))


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
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> MusicAnalysisResponse:
    """Return generated music analysis once the task has FINISHED."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _check_ownership(task, user)
    if task.status != AudioTaskStatus.FINISHED:
        raise HTTPException(
            status_code=409,
            detail=f"task not ready (status={task.status.value})",
        )
    if not task.output_dir:
        raise HTTPException(status_code=404, detail="analysis not found")

    analysis = music_analysis_service.read_analysis(Path(task.output_dir))
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
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
                normalized.append({"instrument": item["instrument"], "probability": item.get("probability", 0)})
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                normalized.append({"instrument": str(item[0]), "probability": float(item[1])})
            else:
                normalized.append(item)
        payload["detected_instruments"] = normalized
    payload["commentary"] = task.commentary
    payload["commentary_model"] = task.commentary_model
    payload["commentary_generated_at"] = (
        task.commentary_generated_at.isoformat()
        if task.commentary_generated_at
        else None
    )
    return MusicAnalysisResponse.model_validate(payload)


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}/commentary
# ---------------------------------------------------------------------------
@router.get("/{task_id}/commentary")
def get_task_commentary(
    task_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> dict:
    """Return just the LLM commentary (and metadata) for a task.

    Kept as its own endpoint so the detail page can fetch commentary
    lazily (only when the user scrolls to it) without re-downloading
    the whole analysis JSON.
    """
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    _check_ownership(task, user)
    return {
        "task_id": task.id,
        "commentary": task.commentary,
        "commentary_model": task.commentary_model,
        "commentary_generated_at": (
            task.commentary_generated_at.isoformat()
            if task.commentary_generated_at
            else None
        ),
    }