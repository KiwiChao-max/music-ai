"""Audio task REST endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.audio import AudioTaskRead, UploadResponse
from app.services import audio_task_service as svc

router = APIRouter(prefix="/api/audio", tags=["audio"])


@router.post("/upload", response_model=UploadResponse, status_code=201)
def upload_audio(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> UploadResponse:
    """Persist the uploaded file and create an `audio_tasks` row.

    Flow: insert DB row first, then stream file to disk. If the file write
    fails we roll the row back so state and disk stay in sync.
    """
    task = svc.create_task(db, file.filename or "upload.bin")
    db.commit()  # release the row so the FK-free file path is well-defined
    try:
        svc.save_upload(task, file.file)
    except Exception:
        # Best-effort cleanup; the row may have been committed already.
        with db.begin_nested() if db.in_transaction() else _NullCtx():
            svc.delete_task(db, task.id)
        db.commit()
        if svc.storage_path(task.id, task.filename).exists():
            svc.storage_path(task.id, task.filename).unlink()
        raise
    return UploadResponse(task_id=task.id)


class _NullCtx:
    def __enter__(self): return self
    def __exit__(self, *exc): return False


@router.get("", response_model=list[AudioTaskRead])
def list_tasks(db: Session = Depends(get_db)) -> list[AudioTaskRead]:
    """Return all tasks, newest first."""
    return [AudioTaskRead.model_validate(t) for t in svc.list_tasks(db)]


@router.get("/{task_id}", response_model=AudioTaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)) -> AudioTaskRead:
    """Return a single task by id."""
    task = svc.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return AudioTaskRead.model_validate(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)) -> None:
    """Delete a task and its associated upload file."""
    task = svc.delete_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    db.commit()
    svc.remove_upload(task)
