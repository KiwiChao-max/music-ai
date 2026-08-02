"""Pydantic schemas for the sample library API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SampleFileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    label: str
    midi_note: int = Field(ge=35, le=81)
    relative_path: str
    velocity_offset: int = 0
    velocity_min: int = Field(ge=1, le=127, default=1)
    velocity_max: int = Field(ge=1, le=127, default=127)


class UpdateLibrary(BaseModel):
    name: str | None = None
    description: str | None = None


class BatchRemoveSamples(BaseModel):
    sample_ids: list[int]


class LibraryInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    is_active: bool
    provider: str = "drum_kit"
    owner_id: int | None = None
    created_at: datetime
    updated_at: datetime
    files: list[SampleFileInfo] = Field(default_factory=list)
