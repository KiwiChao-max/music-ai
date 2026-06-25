"""Pydantic v2 schemas for the audio task API.

Pydantic v2 changes worth noting:
    - `class Config` is replaced with `model_config = ConfigDict(...)`
    - `orm_mode = True` is replaced with `from_attributes = True`
"""
from __future__ import annotations

from enum import Enum

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


class AudioTaskCreate(AudioTaskBase):
    """Payload used by internal callers; the public upload endpoint
    does not accept this — the filename comes from the multipart upload."""


class UploadResponse(BaseModel):
    task_id: int
