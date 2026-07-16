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
    does not accept this --- the filename comes from the multipart upload."""


class UploadResponse(BaseModel):
    task_id: int


# ---- /api/tasks/{id}/* ---------------------------------------------------
class ProcessResponse(BaseModel):
    """Returned by `POST /api/tasks/{id}/process` after the worker is spawned.

    The task's `status` is whatever the DB held when the call was made ---
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
    """One output file produced by the worker (a separated stem or a MIDI file).

    `kind` tells the frontend whether to render a play/pause button (`audio`)
    or just a download button (`midi`). New kinds (e.g. `pdf` for sheet
    music) can be added without breaking existing callers --- the frontend
    defaults to a download link for anything it doesn't recognize.
    """
    name: str   # e.g. "drums" or "original"
    url: str    # e.g. "/storage/outputs/task_6/drums.wav"
    kind: str = "audio"  # "audio" | "midi"
    profile: str | None = None  # for MIDI: "raw" | "gm" | "xg"


class ChordSegment(BaseModel):
    start: float
    end: float
    chord: str
    confidence: float


class MusicSection(BaseModel):
    label: str
    start: float
    end: float
    energy: str
    density: float
    suggestion: str


class DetectedInstrument(BaseModel):
    instrument: str
    probability: float


class MusicAnalysisResponse(BaseModel):
    bpm: int | None = None
    bpm_confidence: float = 0.0
    key: str | None = None
    key_confidence: float = 0.0
    scale: str | None = None
    note_count: int = 0
    duration: float = 0.0
    pitch_range: str | None = None
    chords: list[ChordSegment] = []
    sections: list[MusicSection] = []
    instrumentation: list[str] = []
    arrangement: list[str] = []
    warnings: list[str] = []

    # Optional fields attached by the API layer (not part of the raw
    # analysis.json written by the worker). `model_config` below
    # disables "ignore extra" so the LLM step can grow over time
    # without forcing a schema bump.
    commentary: str | None = None
    commentary_model: str | None = None
    commentary_generated_at: str | None = None
    detected_instruments: list[DetectedInstrument] | None = None
    dominant_instrument: str | None = None
    soundfont_overrides: list[dict] | None = None

    model_config = ConfigDict(extra="ignore")