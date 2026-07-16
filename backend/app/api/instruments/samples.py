"""Sample library REST endpoints.

Libraries let users upload their own drum kits so the front-end can play
back generated drum MIDI with their samples instead of the default GM
bank. Each library has a name, an optional description, and a list of
samples keyed by GM percussion note (35-81).
"""
from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from app.api.deps import OptionalAuthUser
from app.api.instruments.common import (
    MAX_SAMPLES_PER_LIBRARY,
    MAX_SAMPLE_BYTES,
    MAX_TOTAL_BYTES,
    check_resource_owner,
)
from app.db.session import get_db
from app.schemas.sample_library import (
    BatchRemoveSamples,
    LibraryInfo,
    UpdateLibrary,
)
from app.services import sample_library_service
from app.utils.errors import log_error

logger = logging.getLogger(__name__)

router = APIRouter()


# ---- helpers ---------------------------------------------------------------


def _safe_zip_read(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, max_bytes: int
) -> bytes | None:
    """Read a zip entry with a hard decompressed-size cap."""
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
    except Exception:  # noqa: BLE001
        return None


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


def _library_response(info) -> Response:
    return Response(
        content=json.dumps(
            LibraryInfo.model_validate(info).model_dump(), default=str
        ),
        media_type="application/json",
    )


# ---- endpoints -------------------------------------------------------------


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
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    """Upload a new library.

    Two upload modes are supported:

    * ``files`` --- multiple ``UploadFile`` form parts (drag-and-drop).
    * ``zip_file`` --- a single ``.zip`` archive whose members are the
      samples. Useful for batch imports from a third-party pack.
    """
    service = sample_library_service.SampleLibraryService()
    payload: list[tuple[str, bytes]] = []
    total_bytes = 0

    for upload in files or []:
        if not upload.filename:
            continue
        content = await upload.read()
        if len(content) > MAX_SAMPLE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"sample '{upload.filename}' exceeds {MAX_SAMPLE_BYTES} bytes",
            )
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"total upload exceeds {MAX_TOTAL_BYTES} bytes",
            )
        payload.append((upload.filename, content))
        if len(payload) >= MAX_SAMPLES_PER_LIBRARY:
            break

    if zip_file is not None and zip_file.filename:
        zip_bytes = await zip_file.read()
        if len(zip_bytes) > MAX_TOTAL_BYTES:
            raise HTTPException(status_code=413, detail="zip archive too large")
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    if info.file_size > MAX_SAMPLE_BYTES:
                        logger.warning("skipping oversize sample: %s", info.filename)
                        continue
                    data = _safe_zip_read(zf, info, MAX_SAMPLE_BYTES)
                    if data is None:
                        logger.warning(
                            "skipping oversize or unreadable sample: %s",
                            info.filename,
                        )
                        continue
                    total_bytes += len(data)
                    if total_bytes > MAX_TOTAL_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="zip contents exceed the upload size limit",
                        )
                    payload.append((info.filename, data))
                    if len(payload) >= MAX_SAMPLES_PER_LIBRARY:
                        break
        except zipfile.BadZipFile as exc:
            log_error(exc, context=f"create_library: invalid zip from user {getattr(user, 'id', 'anon')}")
            raise HTTPException(
                status_code=400, detail="invalid zip archive"
            ) from exc

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
        log_error(exc, context=f"create_library failed for user {getattr(user, 'id', 'anon')}")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LibraryInfo.model_validate(info)


@router.post("/libraries/{library_id}/activate", response_model=LibraryInfo)
def activate_library(
    library_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, lib.owner_id)
    info = service.activate(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(info)


@router.post("/libraries/{library_id}/deactivate", response_model=LibraryInfo)
def deactivate_library(
    library_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, lib.owner_id)
    info = service.deactivate(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(info)


@router.patch("/libraries/{library_id}", response_model=LibraryInfo)
def update_library(
    library_id: int,
    payload: UpdateLibrary,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    """Update a sample library's name and/or description."""
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, lib.owner_id)
    try:
        updated = service.update_library(
            db, library_id, name=payload.name, description=payload.description
        )
    except ValueError as exc:
        log_error(exc, context=f"update_library {library_id} failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="library not found")
    return updated


@router.delete("/libraries/{library_id}", status_code=204)
def delete_library(
    library_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> Response:
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, lib.owner_id)
    deleted = service.delete_library(db, library_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="library not found")
    return Response(status_code=204)


@router.get("/libraries/{library_id}/export")
def export_library(library_id: int, db: Session = Depends(get_db)) -> Response:
    """Export a sample library as a JSON mapping file."""
    service = sample_library_service.SampleLibraryService()
    data = service.export_library(db, library_id)
    if data is None:
        raise HTTPException(status_code=404, detail="library not found")
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
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    """Update a sample's MIDI note or label."""
    service = sample_library_service.SampleLibraryService()
    info = service.get_library(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, info.owner_id)

    if midi_note is not None:
        try:
            result = service.update_sample_note(db, library_id, sample_id, midi_note)
        except ValueError as exc:
            log_error(exc, context=f"update_sample_note {library_id}/{sample_id} failed")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="sample not found")
        info = result

    if label is not None:
        try:
            result = service.update_sample_label(db, library_id, sample_id, label)
        except ValueError as exc:
            log_error(exc, context=f"update_sample_label {library_id}/{sample_id} failed")
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="sample not found")
        info = result

    return LibraryInfo.model_validate(info)


@router.post(
    "/libraries/{library_id}/samples", response_model=LibraryInfo, status_code=201
)
async def add_sample(
    library_id: int,
    file: UploadFile = File(...),
    midi_note: int | None = Form(default=None),
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    """Add a single sample to an existing library."""
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, lib.owner_id)
    content = await file.read()

    if len(content) > MAX_SAMPLE_BYTES:
        raise HTTPException(
            status_code=413, detail=f"sample exceeds {MAX_SAMPLE_BYTES} bytes"
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
        log_error(exc, context=f"add_sample_to_library {library_id} failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(result)


@router.delete(
    "/libraries/{library_id}/samples/batch", response_model=LibraryInfo
)
def batch_remove_samples(
    library_id: int,
    payload: BatchRemoveSamples,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    """Remove multiple samples from a library at once."""
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, lib.owner_id)
    result = service.batch_remove_samples(db, library_id, payload.sample_ids)
    if result is None:
        raise HTTPException(status_code=404, detail="library not found")
    return LibraryInfo.model_validate(result)


@router.delete(
    "/libraries/{library_id}/samples/{sample_id}", response_model=LibraryInfo
)
def remove_sample(
    library_id: int,
    sample_id: int,
    db: Session = Depends(get_db),
    user: OptionalAuthUser = None,
) -> LibraryInfo:
    """Remove a single sample from a library."""
    service = sample_library_service.SampleLibraryService()
    lib = service.get_library(db, library_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    check_resource_owner(user, lib.owner_id)
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
    """Stream a single sample file by GM note."""
    if note < 35 or note > 81:
        raise HTTPException(
            status_code=400, detail="note must be in 35..81 (GM percussion)"
        )
    service = sample_library_service.SampleLibraryService()
    info = service.get_library(db, library_id)
    if info is None:
        raise HTTPException(status_code=404, detail="library not found")
    for sample in info.files:
        if sample.midi_note == note:
            full_path = service._root / sample.relative_path  # type: ignore[attr-defined]
            if not full_path.is_file():
                raise HTTPException(
                    status_code=410, detail="sample file missing on disk"
                )
            return FileResponse(
                path=full_path,
                media_type=_media_type_for(full_path),
                filename=Path(sample.relative_path).name,
            )
    raise HTTPException(
        status_code=404, detail="no sample for that note in this library"
    )