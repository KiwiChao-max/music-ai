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
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import AudioTaskStatus
from app.db.session import get_db
from app.schemas.audio import MusicAnalysisResponse, ProcessResponse, StemInfo, TaskStatusResponse
from app.services import midi_mapping_service, music_analysis_service, task_service
from app.tasks_audio import process_audio_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

# Suffixes that count as an "output file" the worker can produce.
#   * `.wav/.mp3/.flac/.ogg/.m4a` — Demucs audio stems
#   * `.mid/.midi`                — Basic Pitch MIDI transcription
# The API sorts audio first, then MIDI, so the UI can group them visually.
_AUDIO_SUFFIXES: set[str] = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
_MIDI_SUFFIXES: set[str] = {".mid", ".midi"}
_OUTPUT_SUFFIXES: set[str] = _AUDIO_SUFFIXES | _MIDI_SUFFIXES


def _public_url(file_path: Path) -> str:
    """Map an absolute file path under `storage_dir` to a public URL.

    Mounted by `main.py` at /storage/, e.g.
        storage/outputs/task_6/drums.wav  ->  /storage/outputs/task_6/drums.wav
    """
    rel = file_path.resolve().relative_to(settings.storage_dir.resolve())
    return f"/storage/{rel.as_posix()}"


# ---------------------------------------------------------------------------
# POST /api/tasks/{id}/process
# ---------------------------------------------------------------------------
@router.post(
    "/{task_id}/process",
    status_code=202,
    response_model=ProcessResponse,
)
def start_processing(
    task_id: int, db: Session = Depends(get_db)
) -> ProcessResponse:
    """Kick off the Demucs pipeline for an uploaded task.

    - 404 if the task does not exist
    - 409 if the task is already PROCESSING or FINISHED (one job per task;
      re-upload to retry a finished one)
    - 202 + spawn worker if the task is UPLOADED or FAILED (retry)

    The state transition UPLOADED/FAILED -> PROCESSING is done atomically
    inside a single SQL UPDATE, so two concurrent /process calls cannot both
    spawn a worker for the same task.
    """
    task = task_service.claim_for_processing(db, task_id)
    if task is None:
        existing = task_service.get_task(db, task_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="task not found")
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
    task_id: int, db: Session = Depends(get_db)
) -> TaskStatusResponse:
    """Lightweight status snapshot for polling UIs."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatusResponse(status=task.status, progress=task.progress)


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}/stems
# ---------------------------------------------------------------------------
@router.get(
    "/{task_id}/stems",
    response_model=list[StemInfo],
)
def list_stems(
    task_id: int, db: Session = Depends(get_db)
) -> list[StemInfo]:
    """Return the separated stems once the task has FINISHED.

    409 until then, so the frontend can show a clear "processing" state
    instead of getting an empty list and assuming "no stems exist".
    """
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
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

    # Collect audio and MIDI outputs separately, then concatenate with
    # audio first so the frontend naturally shows the "playable" rows
    # before the "downloadable" ones.
    audio_stems: list[StemInfo] = []
    midi_stems: list[StemInfo] = []
    for f in sorted(out_dir.iterdir()):
        if not f.is_file():
            continue
        suffix = f.suffix.lower()
        if suffix in _AUDIO_SUFFIXES:
            audio_stems.append(StemInfo(name=f.stem, url=_public_url(f), kind="audio"))
        elif suffix in _MIDI_SUFFIXES:
            midi_stems.append(
                StemInfo(
                    name=f.stem,
                    url=_public_url(f),
                    kind="midi",
                    profile=midi_mapping_service.midi_profile_from_name(f.stem),
                )
            )
    return audio_stems + midi_stems


# ---------------------------------------------------------------------------
# GET /api/tasks/{id}/analysis
# ---------------------------------------------------------------------------
@router.get(
    "/{task_id}/analysis",
    response_model=MusicAnalysisResponse,
)
def get_task_analysis(
    task_id: int, db: Session = Depends(get_db)
) -> MusicAnalysisResponse:
    """Return generated music analysis once the task has FINISHED."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
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
    return MusicAnalysisResponse.model_validate(analysis)