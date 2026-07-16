"""Sample library REST endpoints.

Libraries let users upload their own drum kits so the front-end can play
back generated drum MIDI with their samples instead of the default GM
bank. Each library has a name, an optional description, and a list of
samples keyed by GM percussion note (35-81).

Endpoints
---------
* ``GET    /api/instruments/libraries``        — list all libraries
* ``POST   /api/instruments/libraries``        — upload a new library (multipart)
* ``GET    /api/instruments/libraries/{id}``   — fetch one library with its files
* ``POST   /api/instruments/libraries/{id}/activate`` — set the active library
* ``DELETE /api/instruments/libraries/{id}``   — delete a library
* ``GET    /api/instruments/active``           — currently active library (or 204)
* ``GET    /api/instruments/libraries/{id}/files/{note}`` — stream a single
  sample file by GM note, so the front-end can preload all samples for
  custom-drum playback.
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.api.deps import CurrentUser, OptionalUser, get_current_user
from app.schemas.sample_library import (
    BatchRemoveSamples,
    LibraryInfo,
    SampleFileInfo,
    UpdateLibrary,
)
from app.services import sample_library_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


def _auth_user(user: OptionalUser):
    """Optional auth gate matching audio.py's pattern."""
    if settings.auth_required and user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _check_resource_owner(user: object | None, owner_id: int | None) -> None:
    """Raise 403 if the user does not own the resource and is not an admin.

    A resource with ``owner_id=None`` is a legacy / global resource
    that can only be managed by admins.
    """
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if getattr(user, "role", None) == "admin":
        return
    if owner_id is None:
        raise HTTPException(
            status_code=403,
            detail="only admins can manage global resources",
        )
    if user.id != owner_id:
        raise HTTPException(
            status_code=403,
            detail="you do not own this resource",
        )

_MAX_SAMPLES_PER_LIBRARY = 80
_MAX_SAMPLE_BYTES = 5 * 1024 * 1024  # 5 MB per sample
_MAX_TOTAL_BYTES = 80 * 1024 * 1024  # 80 MB total per upload
_MAX_SF2_BYTES = 200 * 1024 * 1024  # 200 MB per SF2 upload
_MAX_CSV_BYTES = 1 * 1024 * 1024    # 1 MB per CSV preset table


def _safe_zip_read(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int
) -> bytes | None:
    """Read a zip entry with a hard decompressed-size cap.

    ``info.file_size`` in the zip header can be spoofed, so we stream-read
    chunks and abort once the decompressed size exceeds ``max_bytes``.
    Returns ``None`` if the entry is unreadable or exceeds the cap.
    """
    try:
        chunks: list[bytes] = []
        total = 0
        with zf.open(info) as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    return None
                chunks.append(chunk)
        return b"".join(chunks)
    except Exception:  # noqa: BLE001 - corrupt entry, skip it
        return None


@router.get("/active")
def get_active_library(db: Session = Depends(get_db)) -> Response:
    """Return the currently active library, or 204 if none is active."""
    info = sample_library_service.SampleLibraryService().active_library(db)
    if info is None:
        return Response(status_code=204)
    return _library_response(info)


@router.get("/libraries", response_model=list[LibraryInfo])
def list_libraries(db: Session = Depends(get_db)) -> list[LibraryInfo]:
    return [
        LibraryInfo.model_validate(info)
        for info in sample_library_service.SampleLibraryService().list_libraries(db)
    ]


@router.get("/libraries/{library_id}", response_model=LibraryInfo)
def get_library(library_id: int, db: Session = Depends(get_db)) -> LibraryInfo:
    info = sample_library_service.SampleLibraryService().get_library(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(info)


@router.post("/libraries", response_model=LibraryInfo, status_code=201)
async def create_library(
    name: str = Form(...),
    description: str | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
    zip_file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    """Upload a new library.

    Two upload modes are supported:

    * ``files`` — multiple ``UploadFile`` form parts (drag-and-drop).
    * ``zip_file`` — a single ``.zip`` archive whose members are the
      samples. Useful for batch imports from a third-party pack.

    Filenames are mapped to GM percussion notes by alias lookup
    (kick.wav, snare.wav, closed_hat.wav, ...). Samples with no
    recognised name are skipped.
    """
    service = sample_library_service.SampleLibraryService()
    payload: list[tuple[str, bytes]] = []
    total_bytes = 0

    for upload in files or []:
        if not upload.filename:
            continue
        content = await upload.read()
        if len(content) > _MAX_SAMPLE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"sample '{upload.filename}' exceeds {_MAX_SAMPLE_BYTES} bytes",
            )
        total_bytes += len(content)
        if total_bytes > _MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"total upload exceeds {_MAX_TOTAL_BYTES} bytes",
            )
        payload.append((upload.filename, content))
        if len(payload) >= _MAX_SAMPLES_PER_LIBRARY:
            break

    if zip_file is not None and zip_file.filename:
        zip_bytes = await zip_file.read()
        if len(zip_bytes) > _MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="zip archive too large")
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    # Don't trust info.file_size — it can be spoofed in the
                    # zip header. Instead, stream-read with a hard cap.
                    if info.file_size > _MAX_SAMPLE_BYTES:
                        logger.warning("skipping oversize sample: %s", info.filename)
                        continue
                    # Read with streaming size enforcement to prevent zip
                    # bombs (small compressed payload, huge decompressed).
                    data = _safe_zip_read(zf, info, _MAX_SAMPLE_BYTES)
                    if data is None:
                        logger.warning("skipping oversize or unreadable sample: %s", info.filename)
                        continue
                    total_bytes += len(data)
                    if total_bytes > _MAX_TOTAL_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="zip contents exceed the upload size limit",
                        )
                    payload.append((info.filename, data))
                    if len(payload) >= _MAX_SAMPLES_PER_LIBRARY:
                        break
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail=f"invalid zip archive: {exc}") from exc

    if not payload:
        raise HTTPException(
            status_code=400,
            detail="no audio files provided (use `files` field or `zip_file`)",
        )

    try:
        info = service.create_library(
            db,
            name=name,
            files=payload,
            description=description,
            owner_id=user.id if user is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LibraryInfo.model_validate(info)


@router.post("/libraries/{library_id}/activate", response_model=LibraryInfo)
def activate_library(
    library_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, lib.owner_id)
    info = service.activate(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(info)


@router.post("/libraries/{library_id}/deactivate", response_model=LibraryInfo)
def deactivate_library(
    library_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, lib.owner_id)
    info = service.deactivate(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(info)


@router.patch("/libraries/{library_id}", response_model=LibraryInfo)
def update_library(
    library_id: int,
    payload: UpdateLibrary,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    """Update a sample library's name and/or description."""
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, lib.owner_id)
    try:
        updated = service.update_library(
            db,
            library_id,
            name=payload.name,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="library not found")
    return updated


@router.delete("/libraries/{library_id}", status_code=204)
def delete_library(
    library_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> Response:
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, lib.owner_id)
    deleted = service.delete_library(db, library_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="library not found")
    return Response(status_code=204)


@router.get("/libraries/{library_id}/export")
def export_library(library_id: int, db: Session = Depends(get_db)) -> Response:
    """Export a sample library as a JSON mapping file.

    Returns a JSON file with the library's GM percussion note mapping.
    Useful for backup or sharing custom drum kits.
    """
    service = sample_library_service.SampleLibraryService()
    data = service.export_library(db, library_id)
    if data is None:
        raise HTTPException(status_code=404, detail="library not found")

    import json
    content = json.dumps(data, indent=2, ensure_ascii=False)
    filename = f"{data['name'].replace(' ', '_')}_mapping.json"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/libraries/{library_id}/samples/{sample_id}", response_model=LibraryInfo)
def update_sample(
    library_id: int,
    sample_id: int,
    midi_note: int | None = Form(default=None),
    label: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    """Update a sample's MIDI note or label.

    Use this to manually correct auto-classified samples or rename them.
    """
    service = sample_library_service.SampleLibraryService()
    info = service.get_library(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, info.owner_id)

    if midi_note is not None:
        try:
            result = service.update_sample_note(db, library_id, sample_id, midi_note)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="sample not found")
        info = result

    if label is not None:
        try:
            result = service.update_sample_label(db, library_id, sample_id, label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="sample not found")
        info = result

    return LibraryInfo.model_validate(info)


@router.post("/libraries/{library_id}/samples", response_model=LibraryInfo, status_code=201)
async def add_sample(
    library_id: int,
    file: UploadFile = File(...),
    midi_note: int | None = Form(default=None),
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    """Add a single sample to an existing library.

    If `midi_note` is not provided, the service will try to detect it from
    the filename first, then fall back to audio content classification.
    """
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, lib.owner_id)
    content = await file.read()

    if len(content) > _MAX_SAMPLE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"sample exceeds {_MAX_SAMPLE_BYTES} bytes",
        )

    try:
        result = service.add_sample_to_library(
            db,
            library_id=library_id,
            filename=file.filename or "sample.wav",
            content=content,
            midi_note=midi_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="library not found")

    return LibraryInfo.model_validate(result)


@router.delete("/libraries/{library_id}/samples/batch", response_model=LibraryInfo)
def batch_remove_samples(
    library_id: int,
    payload: BatchRemoveSamples,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    """Remove multiple samples from a library at once."""
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, lib.owner_id)
    result = service.batch_remove_samples(db, library_id, payload.sample_ids)
    if result is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(result)


@router.delete("/libraries/{library_id}/samples/{sample_id}", response_model=LibraryInfo)
def remove_sample(
    library_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> LibraryInfo:
    """Remove a single sample from a library."""
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    _check_resource_owner(user, lib.owner_id)
    result = service.remove_sample_from_library(db, library_id, sample_id)
    if result is None:
        raise HTTPException(status_code=404, detail="library or sample not found")
    return LibraryInfo.model_validate(result)


@router.get("/libraries/{library_id}/files/{note}")
def get_sample_file(
    library_id: int,
    note: int,
    db: Session = Depends(get_db),
) -> FileResponse:
    """Stream a single sample file by GM note.

    The front-end can preload all 18+ samples at once by issuing one
    request per GM note. Returns 404 if the library or note is missing.
    """
    if note < 35 or note > 81:
        raise HTTPException(status_code=400, detail="note must be in 35..81 (GM percussion)")
    service = sample_library_service.SampleLibraryService()
    info = service.get_library(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    for sample in info.files:
        if sample.midi_note == note:
            full_path = service._root / sample.relative_path  # type: ignore[attr-defined]
            if not full_path.is_file():
                raise HTTPException(status_code=410, detail="sample file missing on disk")
            media_type = _media_type_for(full_path)
            return FileResponse(
                path=full_path,
                media_type=media_type,
                filename=Path(sample.relative_path).name,
            )
    raise HTTPException(status_code=404, detail="no sample for that note in this library")


def _media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aif": "audio/aiff",
        ".aiff": "audio/aiff",
    }.get(suffix, "application/octet-stream")


def _library_response(info) -> Response:  # pragma: no cover - tiny helper
    import json

    return Response(
        content=json.dumps(LibraryInfo.model_validate(info).model_dump(), default=str),
        media_type="application/json",
    )


# ---------------------------------------------------------------------------
# Sample Classification API
# ---------------------------------------------------------------------------
@router.post("/classify")
async def classify_sample(
    file: UploadFile = File(...),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> dict:
    """Classify a single audio sample using spectral analysis.

    Returns the detected drum type, GM MIDI note, confidence score,
    and extracted features. Useful for previewing classification before
    uploading a full library.
    """
    from app.services.sample_classifier_service import SampleClassifierService

    content = await file.read()
    if len(content) > _MAX_SAMPLE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"sample exceeds {_MAX_SAMPLE_BYTES} bytes",
        )
    classifier = SampleClassifierService()
    result = classifier.classify_bytes(content, file.filename or "sample.wav")

    if result is None:
        raise HTTPException(status_code=400, detail="could not classify sample")

    return {
        "filename": file.filename,
        "drum_type": result.drum_type,
        "drum_type_label": classifier.get_drum_type_label(result.drum_type),
        "midi_note": result.midi_note,
        "confidence": round(result.confidence, 4),
        "features": {k: round(v, 4) for k, v in result.features.items()},
    }


@router.get("/drum-types")
def list_drum_types() -> list[dict]:
    """Return all supported drum types with their GM notes and labels."""
    from app.services.sample_classifier_service import SampleClassifierService

    classifier = SampleClassifierService()
    return [
        {
            "drum_type": drum_type,
            "midi_note": midi_note,
            "label": label,
        }
        for drum_type, midi_note, label in classifier.get_all_drum_types()
    ]


# ---------------------------------------------------------------------------
# SoundFont & Preset Table API
# ---------------------------------------------------------------------------
@router.get("/gm-instruments")
def list_gm_instruments() -> list[dict]:
    """Return all GM program numbers with their standard instrument names."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    return [
        {"program": program, "name": name}
        for program, name in service.list_gm_instruments()
    ]


@router.post("/preset-table/import")
async def import_preset_table(
    file: UploadFile = File(...),
    name: str = Form(...),
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> dict:
    """Import a preset table from a CSV file and save to database.

    Expected CSV columns:
      - bank_msb (optional, default 0)
      - bank_lsb (optional, default 0)
      - program (required, 0-127)
      - name (required, preset name)
      - category (optional)
      - instrument_type (optional: piano, guitar, bass, strings, etc.)
    """
    from app.services.soundfont_service import SoundFontService

    content = await file.read()
    if len(content) > _MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV file exceeds {_MAX_CSV_BYTES} bytes",
        )
    service = SoundFontService()
    presets = service.import_preset_table(content, name)

    if not presets:
        raise HTTPException(status_code=400, detail="no valid presets found in CSV")

    result = service.save_soundfont_to_db(
        db,
        name=name,
        description=None,
        sf_type="preset_table",
        file_path=None,
        presets=presets,
        owner_id=user.id if user is not None else None,
    )

    return result


@router.post("/soundfont/import")
async def import_soundfont(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> dict:
    """Import a SoundFont 2 (.sf2) file, extract presets, and save to database."""
    from app.services.soundfont_service import SoundFontService

    content = await file.read()
    if len(content) > _MAX_SF2_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"SF2 file exceeds {_MAX_SF2_BYTES} bytes",
        )
    service = SoundFontService()
    sf_info = service.import_soundfont(name, content, description)

    if sf_info is None:
        raise HTTPException(status_code=400, detail="could not parse SoundFont file")

    result = service.save_soundfont_to_db(
        db,
        name=name,
        description=description,
        sf_type="sf2",
        file_path=sf_info.file_path,
        presets=sf_info.presets,
        owner_id=user.id if user is not None else None,
    )

    return result


@router.get("/soundfonts")
def list_soundfonts(db: Session = Depends(get_db)) -> list[dict]:
    """List all SoundFonts and preset tables."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    return service.list_soundfonts(db)


@router.get("/soundfonts/{soundfont_id}")
def get_soundfont(soundfont_id: int, db: Session = Depends(get_db)) -> dict:
    """Get a SoundFont by ID with its presets."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    result = service.get_soundfont(db, soundfont_id)
    if result is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    return result


@router.get("/soundfonts/active")
def get_active_soundfont(db: Session = Depends(get_db)) -> Response:
    """Get the currently active SoundFont, or 204 if none."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    result = service.get_active_soundfont(db)
    if result is None:
        return Response(status_code=204)
    import json
    return Response(content=json.dumps(result, default=str), media_type="application/json")


@router.post("/soundfonts/{soundfont_id}/activate")
def activate_soundfont(
    soundfont_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> dict:
    """Activate a SoundFont (deactivates all others)."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    sf = service.get_soundfont(db, soundfont_id)
    if sf is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    _check_resource_owner(user, sf.get("owner_id"))
    result = service.activate_soundfont(db, soundfont_id)
    if result is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    return result


@router.delete("/soundfonts/{soundfont_id}", status_code=204)
def delete_soundfont(
    soundfont_id: int,
    db: Session = Depends(get_db),
    user: Annotated[object | None, Depends(_auth_user)] = None,
) -> Response:
    """Delete a SoundFont and all its presets."""
    from app.services.soundfont_service import SoundFontService

    service = SoundFontService()
    sf = service.get_soundfont(db, soundfont_id)
    if sf is None:
        raise HTTPException(status_code=404, detail="soundfont not found")
    _check_resource_owner(user, sf.get("owner_id"))
    deleted = service.delete_soundfont(db, soundfont_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="soundfont not found")
    return Response(status_code=204)


# Re-export for convenience so other modules can import a single name.
__all__ = ["router"]


# Avoid a stale `settings` import warning when the file is imported by
# Alembic env (no `sample_library_service` user). The `settings` import is
# already pulled in transitively by the service module, so this is a no-op.
_ = settings
