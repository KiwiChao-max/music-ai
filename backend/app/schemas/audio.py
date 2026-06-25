"""Pydantic v2 schemas for the audio task API.

Pydantic v2 changes worth noting:
    - `class Config` is replaced with `model_config = ConfigDict(...)`
    - `orm_mode = True` is replaced with `from_attributes = True`
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AudioTaskStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class AudioTaskBase(BaseModel):
    filename: str
    status: AudioTaskStatus = AudioTaskStatus.UPLOADED


class AudioTaskRead(AudioTaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    progress: int = 0
    current_step: Optional[str] = None
    duration: Optional[float] = None
    output_dir: Optional[str] = None
    error_message: Optional[str] = None
    finished_at: Optional[datetime] = None


class AudioTaskCreate(AudioTaskBase):
    """Payload used by internal callers; the public upload endpoint
    does not accept this — the filename comes from the multipart upload."""


class UploadResponse(BaseModel):
    task_id: int


# ---- /api/tasks/{id}/* ---------------------------------------------------
class ProcessResponse(BaseModel):
    """Returned by `POST /api/tasks/{id}/process` after the worker is spawned.

    The task's `status` is whatever the DB held when the call was made —
    typically UPLOADED (just uploaded) or FAILED (retry). The worker
    immediately flips it to PROCESSING in the background; the caller should
    poll `/status` to follow along.
    """
    task_id: int
    status: AudioTaskStatus


class TaskStatusResponse(BaseModel):
    """Returned by `GET /api/tasks/{id}/status`."""
    status: AudioTaskStatus
    progress: int


class StemInfo(BaseModel):
    """One separated stem (vocals / drums / bass / other / ...)."""
    name: str   # e.g. "drums"
    url: str    # e.g. "/storage/outputs/task_6/drums.wav"
