"""Instrument API package.

Splits the monolithic ``instruments.py`` into three focused modules:

* ``samples`` --- sample library CRUD (upload, list, activate, delete, etc.)
* ``classification`` --- audio sample classification endpoints
* ``soundfonts`` --- SoundFont / preset table import, list, activate, delete

``main.py`` imports ``instruments_router`` from here, which merges all
three sub-routers under the shared ``/api/instruments`` prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.instruments.classification import router as classification_router
from app.api.instruments.samples import router as samples_router
from app.api.instruments.soundfonts import router as soundfonts_router

instruments_router = APIRouter(prefix="/api/instruments")
instruments_router.include_router(samples_router)
instruments_router.include_router(classification_router)
instruments_router.include_router(soundfonts_router)
