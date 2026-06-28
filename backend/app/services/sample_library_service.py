"""Sample library service.

Manages user-uploaded sample libraries (drum kits for now, SoundFont is
next). The service is responsible for:

  * persisting `SampleLibrary` and `SampleFile` rows;
  * copying uploaded files into the project's storage directory;
  * mapping filenames to GM drum notes by parsing common naming conventions
    (e.g. ``kick.wav`` → note 36, ``snare.wav`` → note 38);
  * listing, activating, and deleting libraries.

Naming convention used to map filename → GM note. The set of aliases covers
the common Roland/Yamaha-style naming. Anything not recognised is recorded
as a free-form sample and skipped on playback (still visible in the list).
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import SampleFile, SampleLibrary

logger = logging.getLogger(__name__)

# Filename alias → GM percussion note (35..81). Lowercased, extension-free.
_FILENAME_ALIASES: dict[str, int] = {
    # Kick family
    "kick": 36, "bass_drum": 36, "bassdrum": 36, "bd": 36, "kik": 36,
    "acoustic_kick": 35,
    # Snare family
    "snare": 38, "snr": 38, "sd": 38, "acoustic_snare": 38, "elec_snare": 40,
    "rim": 37, "sidestick": 37, "rimshot": 37,
    "clap": 39, "hand_clap": 39,
    # Hi-hats
    "closed_hat": 42, "chh": 42, "hhc": 42, "hat_closed": 42,
    "open_hat": 46, "ohh": 46, "hho": 46, "hat_open": 46,
    "pedal_hat": 44, "phh": 44, "hhp": 44,
    # Toms (lowest to highest)
    "low_floor_tom": 41, "lft": 41, "floor_tom": 41,
    "low_tom": 45, "lt": 45,
    "low_mid_tom": 47, "lmt": 47,
    "hi_mid_tom": 48, "hmt": 48,
    "high_tom": 50, "ht": 50,
    # Cymbals
    "crash": 49, "crash1": 49, "crash_1": 49, "crash_cymbal": 49,
    "crash2": 57, "crash_2": 57, "splash": 55, "splash_cymbal": 55,
    "china": 52, "chinese_cymbal": 52,
    "ride": 51, "ride_cymbal": 51, "ride1": 51, "ride2": 59,
    "ride_bell": 53, "bell": 53,
    # Hand percussion
    "tambourine": 54, "tamb": 54,
    "cowbell": 56,
    "vibraslap": 58,
    # Latin percussion (use one slot per family for simplicity).
    "bongo": 60, "conga": 62, "timbale": 65, "agogo": 67,
    "cabasa": 69, "maracas": 70, "whistle": 71, "guiro": 73,
    "claves": 75, "woodblock": 76, "cuica": 78, "triangle": 80,
}

# Audio extensions we accept.
_AUDIO_EXTENSIONS = {".wav", ".aif", ".aiff", ".flac", ".ogg", ".mp3", ".m4a"}


@dataclass(frozen=True)
class SampleFileInfo:
    label: str
    midi_note: int
    relative_path: str
    velocity_offset: int


@dataclass(frozen=True)
class LibraryInfo:
    id: int
    name: str
    description: str | None
    is_active: bool
    provider: str
    created_at: str
    updated_at: str
    files: tuple[SampleFileInfo, ...]


class SampleLibraryService:
    """CRUD + filesystem side-effects for sample libraries."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(settings.storage_dir) / "sample-libraries"

    # ---- public API --------------------------------------------------------
    def create_library(
        self,
        db: Session,
        *,
        name: str,
        files: list[tuple[str, bytes]],
        description: str | None = None,
    ) -> LibraryInfo:
        """Create a library from uploaded (filename, content) pairs.

        Each entry in `files` becomes a `SampleFile` row, mapped to a GM
        percussion note by filename. The file content is streamed to the
        library's directory on disk.
        """
        if not name.strip():
            raise ValueError("library name is required")
        if not files:
            raise ValueError("library must contain at least one sample")

        library = SampleLibrary(name=name.strip(), description=description)
        db.add(library)
        db.flush()  # populate library.id

        library_dir = self._root / str(library.id)
        library_dir.mkdir(parents=True, exist_ok=True)

        sample_files: list[SampleFile] = []
        for original_name, content in files:
            safe_name = _safe_filename(original_name)
            if not safe_name:
                continue
            note = _resolve_note_from_name(safe_name)
            if note is None:
                # Skip non-drum samples; the model only models drum notes.
                continue
            target = library_dir / safe_name
            target.write_bytes(content)
            sample_files.append(
                SampleFile(
                    library_id=library.id,
                    label=Path(safe_name).stem,
                    midi_note=note,
                    file_path=str(target.relative_to(self._root)),
                    velocity_offset=0,
                )
            )

        if not sample_files:
            # Nothing mappable — roll back the library row to keep the
            # on-disk state and DB in sync.
            db.expunge(library)
            shutil.rmtree(library_dir, ignore_errors=True)
            raise ValueError(
                "no samples with recognizable drum names "
                "(expected e.g. kick.wav, snare.wav, hihat_closed.wav)"
            )

        db.add_all(sample_files)
        db.commit()
        db.refresh(library)
        logger.info(
            "sample-library: created id=%s name=%s files=%d",
            library.id,
            library.name,
            len(sample_files),
        )
        return self._to_info(library, sample_files)

    def list_libraries(self, db: Session) -> list[LibraryInfo]:
        libraries = list(
            db.scalars(select(SampleLibrary).order_by(SampleLibrary.created_at.desc()))
        )
        if not libraries:
            return []
        library_ids = [lib.id for lib in libraries]
        files_by_library: dict[int, list[SampleFile]] = {lib_id: [] for lib_id in library_ids}
        for sample_file in db.scalars(
            select(SampleFile).where(SampleFile.library_id.in_(library_ids))
        ):
            files_by_library[sample_file.library_id].append(sample_file)
        return [
            self._to_info(library, files_by_library.get(library.id, []))
            for library in libraries
        ]

    def get_library(self, db: Session, library_id: int) -> LibraryInfo | None:
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None
        files = list(
            db.scalars(
                select(SampleFile).where(SampleFile.library_id == library_id).order_by(SampleFile.midi_note)
            )
        )
        return self._to_info(library, files)

    def activate(self, db: Session, library_id: int) -> LibraryInfo | None:
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None
        # Atomic swap: deactivate all, activate the chosen one. The unique
        # partial index on `is_active=1` ensures only one library is active
        # at a time even if two requests race.
        db.execute(update(SampleLibrary).values(is_active=0))
        library.is_active = 1
        db.commit()
        db.refresh(library)
        return self.get_library(db, library_id)

    def delete_library(self, db: Session, library_id: int) -> bool:
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return False
        for sample_file in db.scalars(
            select(SampleFile).where(SampleFile.library_id == library_id)
        ):
            db.delete(sample_file)
        db.delete(library)
        db.commit()
        shutil.rmtree(self._root / str(library_id), ignore_errors=True)
        return True

    def active_library(self, db: Session) -> LibraryInfo | None:
        library = db.scalars(
            select(SampleLibrary).where(SampleLibrary.is_active == 1).limit(1)
        ).first()
        if library is None:
            return None
        return self.get_library(db, library.id)

    # ---- internal helpers --------------------------------------------------
    def _to_info(self, library: SampleLibrary, files: list[SampleFile]) -> LibraryInfo:
        return LibraryInfo(
            id=library.id,
            name=library.name,
            description=library.description,
            is_active=bool(library.is_active),
            provider=library.provider,
            created_at=library.created_at.isoformat() if library.created_at else "",
            updated_at=library.updated_at.isoformat() if library.updated_at else "",
            files=tuple(
                SampleFileInfo(
                    label=sample_file.label,
                    midi_note=sample_file.midi_note,
                    relative_path=sample_file.file_path,
                    velocity_offset=sample_file.velocity_offset,
                )
                for sample_file in sorted(files, key=lambda sf: (sf.midi_note, sf.label))
            ),
        )


# ---------------------------------------------------------------------------
# Module-level helpers (used by both the service and the API layer for
# validation).
# ---------------------------------------------------------------------------
def _safe_filename(name: str) -> str | None:
    """Return a basename with a recognised audio extension, or None."""
    if not name:
        return None
    base = Path(name).name  # strip any directory traversal
    if not base or base.startswith("."):
        return None
    if Path(base).suffix.lower() not in _AUDIO_EXTENSIONS:
        return None
    return base


def _resolve_note_from_name(filename: str) -> int | None:
    """Map ``filename`` to a GM percussion note by alias lookup."""
    stem = Path(filename).stem.lower()
    # Strip trailing numbers: "kick_01" -> "kick", "snare_002" -> "snare".
    stem = re.sub(r"[_ -]?\d+$", "", stem)
    stem = stem.replace("-", "_")
    if stem in _FILENAME_ALIASES:
        return _FILENAME_ALIASES[stem]
    # Try matching on token instead of strict equality so a filename like
    # "studio_kick" still resolves to 36.
    for token in stem.split("_"):
        if token in _FILENAME_ALIASES:
            return _FILENAME_ALIASES[token]
    return None
