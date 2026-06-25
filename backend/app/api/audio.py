"""Audio task REST endpoints."""
from __future__ import annotations

import wave
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.audio import AudioTaskRead, UploadResponse
from app.services import file_service, task_service

router = APIRouter(prefix="/api/audio", tags=["audio"])


def _probe_duration(path: Path) -> float | None:
    """Best-effort WAV duration probe using only the stdlib.

    Returns `None` for non-WAV files (we don't pull in `mutagen` yet). The
    worker can re-probe with a proper audio lib in Milestone 2.
    """
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            if rate <= 0:
                return None
            return round(frames / float(rate), 2)
    except (wave.Error, EOFError, FileNotFoundError):
        return None


@router.post("/upload", response_model=UploadResponse, status_code=201)
def upload_audio(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Persist the uploaded file and create an `audio_tasks` row.

    Flow: insert DB row first, then stream file to disk. If the file write
    fails we roll the row back so state and disk stay in sync.
    """
    task = task_service.create_task(db, file.filename or "upload.bin")
    db.commit()  # release the row so the FK-free file path is well-defined
    try:
        path = file_service.save_upload(task, file.file)
    except Exception:
        # Best-effort cleanup; the row may have been committed already.
        if db.in_transaction():
            with db.begin_nested():
                task_service.delete_task(db, task.id)
        else:
            task_service.delete_task(db, task.id)
        db.commit()
        file_service.remove_task_files(task)
        raise

    # Stamp the conventional output path and (best-effort) duration.
    output_dir = str(file_service.task_output_dir(task.id))
    task_service.set_output_dir(db, task, output_dir)
    task_service.set_duration(db, task, _probe_duration(path))
    db.commit()

    return UploadResponse(task_id=task.id)


@router.get("", response_model=list[AudioTaskRead])
def list_tasks(db: Session = Depends(get_db)) -> list[AudioTaskRead]:
    """Return all tasks, newest first."""
    return [AudioTaskRead.model_validate(t) for t in task_service.list_tasks(db)]


@router.get("/{task_id}", response_model=AudioTaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> AudioTaskRead:
    """Return a single task by id."""
    task = task_service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return AudioTaskRead.model_validate(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a task and its on-disk files (uploads + worker outputs)."""
    task = task_service.delete_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    db.commit()
    file_service.remove_task_files(task)
