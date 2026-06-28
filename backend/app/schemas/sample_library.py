"""Pydantic schemas for the sample library API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SampleFileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str
    midi_note: int = Field(ge=35, le=81)
    relative_path: str
    velocity_offset: int = 0


class LibraryInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    is_active: bool
    provider: str = "drum_kit"
    created_at: datetime
    updated_at: datetime
    files: list[SampleFileInfo] = Field(default_factory=list)
