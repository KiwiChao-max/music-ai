"""SoundFont & preset table REST endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import OptionalAuthUser
from app.api.instruments.common import (
    MAX_CSV_BYTES,
    MAX_SF2_BYTES,
    check_resource_owner,
)
from app.db.session import get_db

router = APIRouter()


@router.get("/gm-instruments")
def list_gm_instruments() -> list[dict]:
    """Return all GM program numbers with their standard instrument names."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    return [{"program": program, "name": name} for program, name in service.list_gm_instruments()]


@router.post("/preset-table/import")
def import_preset_table(
    file: UploadFile = File(...),
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> dict:
    """Import a preset table from a CSV file and save to database.

    Expected CSV columns:
      - bank_msb (optional, default 0)
      - bank_lsb (optional, default 0)
      - program (required, 0-127)
      - name (required, preset name)
      - category (optional)
      - instrument_type (optional: piano, guitar, bass, strings, etc.)

    Sync endpoint: the CSV parse + DB write run in the threadpool.
    """
    from app.services.soundfont_service import SoundFontService

    content = file.file.read()
    if len(content) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV file exceeds {MAX_CSV_BYTES} bytes",
        )
    service = SoundFontService()
    presets = service.import_preset_table(content, name)

    if not presets:
        raise HTTPException(status_code=400, detail="no valid presets found in CSV")

    return service.save_soundfont_to_db(
        db,
        name=name,
        description=None,
        sf_type="preset_table",
        file_path=None,
        presets=presets,
        owner_id=user.id if user is not None else None,
    )


@router.post("/soundfont/import")
def import_soundfont(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> dict:
    """Import a SoundFont 2 (.sf2) file, extract presets, and save.

    Sync endpoint: the full SF2 file write + mmap parse + DB commit run
    in the threadpool.
    """
    from app.services.soundfont_service import SoundFontService

    content = file.file.read()
    if len(content) > MAX_SF2_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"SF2 file exceeds {MAX_SF2_BYTES} bytes",
        )
    service = SoundFontService()
    sf_info = service.import_soundfont(name, content, description)

    if sf_info is None:
        raise HTTPException(status_code=400, detail="could not parse SoundFont file")

    return service.save_soundfont_to_db(
        db,
        name=name,
        description=description,
        sf_type="sf2",
        file_path=sf_info.file_path,
        presets=sf_info.presets,
        owner_id=user.id if user is not None else None,
    )


@router.get("/soundfonts")
def list_soundfonts(db: Session = Depends(get_db)) -> list[dict]:
    """List all SoundFonts and preset tables."""
    from app.services.soundfont_service import SoundFontService

    return SoundFontService().list_soundfonts(db)


@router.get("/soundfonts/{soundfont_id}")
def get_soundfont(soundfont_id: int, db: Session = Depends(get_db)) -> dict:
    """Get a SoundFont by ID with its presets."""
    from app.services.soundfont_service import SoundFontService

    result = SoundFontService().get_soundfont(db, soundfont_id)
    if result is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    return result


@router.get("/soundfonts/active")
def get_active_soundfont(db: Session = Depends(get_db)) -> Response:
    """Get the currently active SoundFont, or 204 if none."""
    from app.services.soundfont_service import SoundFontService

    result = SoundFontService().get_active_soundfont(db)
    if result is None:
        return Response(status_code=204)
    return Response(content=json.dumps(result, default=str), media_type="application/json")


@router.post("/soundfonts/{soundfont_id}/activate")
def activate_soundfont(
    soundfont_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> dict:
    """Activate a SoundFont (deactivates all others)."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    sf = service.get_soundfont(db, soundfont_id)
    if sf is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    check_resource_owner(user, sf.get("owner_id"))
    result = service.activate_soundfont(db, soundfont_id)
    if result is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    return result


@router.delete("/soundfonts/{soundfont_id}", status_code=204)
def delete_soundfont(
    soundfont_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> Response:
    """Delete a SoundFont and all its presets."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    sf = service.get_soundfont(db, soundfont_id)
    if sf is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    check_resource_owner(user, sf.get("owner_id"))
    deleted = service.delete_soundfont(db, soundfont_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="soundfont not found")
    return Response(status_code=204)
