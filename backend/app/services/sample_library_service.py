"""Sample library service.

Manages user-uploaded sample libraries (drum kits for now, SoundFont is
next). The service is responsible for:

  * persisting `SampleLibrary` and `SampleFile` rows;
  * copying uploaded files into the project's storage directory;
  * mapping filenames to GM drum notes by parsing common naming conventions
    (e.g. ``kick.wav`` -> note 36, ``snare.wav`` -> note 38);
  * listing, activating, and deleting libraries.

Naming convention used to map filename -> GM note. The set of aliases covers
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

# Filename alias -> GM percussion note (35..81). Lowercased, extension-free.
_FILENAME_ALIASES: dict[str, int] = {
    # Kick family
    "kick": 36,
    "bass_drum": 36,
    "bassdrum": 36,
    "bd": 36,
    "kik": 36,
    "acoustic_kick": 35,
    # Snare family
    "snare": 38,
    "snr": 38,
    "sd": 38,
    "acoustic_snare": 38,
    "elec_snare": 40,
    "rim": 37,
    "sidestick": 37,
    "rimshot": 37,
    "clap": 39,
    "hand_clap": 39,
    # Hi-hats
    "closed_hat": 42,
    "chh": 42,
    "hhc": 42,
    "hat_closed": 42,
    "open_hat": 46,
    "ohh": 46,
    "hho": 46,
    "hat_open": 46,
    "pedal_hat": 44,
    "phh": 44,
    "hhp": 44,
    # Toms (lowest to highest)
    "low_floor_tom": 41,
    "lft": 41,
    "floor_tom": 41,
    "low_tom": 45,
    "lt": 45,
    "low_mid_tom": 47,
    "lmt": 47,
    "hi_mid_tom": 48,
    "hmt": 48,
    "high_tom": 50,
    "ht": 50,
    # Cymbals
    "crash": 49,
    "crash1": 49,
    "crash_1": 49,
    "crash_cymbal": 49,
    "crash2": 57,
    "crash_2": 57,
    "splash": 55,
    "splash_cymbal": 55,
    "china": 52,
    "chinese_cymbal": 52,
    "ride": 51,
    "ride_cymbal": 51,
    "ride1": 51,
    "ride2": 59,
    "ride_bell": 53,
    "bell": 53,
    # Hand percussion
    "tambourine": 54,
    "tamb": 54,
    "cowbell": 56,
    "vibraslap": 58,
    # Latin percussion (use one slot per family for simplicity).
    "bongo": 60,
    "conga": 62,
    "timbale": 65,
    "agogo": 67,
    "cabasa": 69,
    "maracas": 70,
    "whistle": 71,
    "guiro": 73,
    "claves": 75,
    "woodblock": 76,
    "cuica": 78,
    "triangle": 80,
}

# Audio extensions we accept.
_AUDIO_EXTENSIONS = {".wav", ".aif", ".aiff", ".flac", ".ogg", ".mp3", ".m4a"}


@dataclass(frozen=True)
class SampleFileInfo:
    id: int
    label: str
    midi_note: int
    relative_path: str
    velocity_offset: int
    velocity_min: int = 1
    velocity_max: int = 127


@dataclass(frozen=True)
class LibraryInfo:
    id: int
    name: str
    description: str | None
    is_active: bool
    provider: str
    owner_id: int | None
    created_at: str
    updated_at: str
    files: tuple[SampleFileInfo, ...]


class SampleLibraryService:
    """CRUD + filesystem side-effects for sample libraries."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path(settings.storage_dir) / "sample-libraries"
        self._classifier = None

    @property
    def _classifier_service(self):
        if self._classifier is None:
            from app.services.sample_classifier_service import SampleClassifierService

            self._classifier = SampleClassifierService()
        return self._classifier

    # ---- public API --------------------------------------------------------
    def create_library(
        self,
        db: Session,
        *,
        name: str,
        files: list[tuple[str, bytes]],
        description: str | None = None,
        owner_id: int | None = None,
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

        library = SampleLibrary(
            name=name.strip(),
            description=description,
            owner_id=owner_id,
        )
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
                classification = self._classifier_service.classify_bytes(content, safe_name)
                if classification is not None:
                    note = classification.midi_note
                    logger.info(
                        "sample-library: auto-classified %s as %s (note=%d, confidence=%.2f)",
                        safe_name,
                        classification.drum_type,
                        note,
                        classification.confidence,
                    )
                else:
                    continue

            target = library_dir / safe_name
            target.write_bytes(content)
            v_min, v_max = _resolve_velocity_range(safe_name)
            sample_files.append(
                SampleFile(
                    library_id=library.id,
                    label=Path(safe_name).stem,
                    midi_note=note,
                    file_path=str(target.relative_to(self._root)),
                    velocity_offset=0,
                    velocity_min=v_min,
                    velocity_max=v_max,
                )
            )

        if not sample_files:
            db.expunge(library)
            shutil.rmtree(library_dir, ignore_errors=True)
            raise ValueError(
                "no samples could be mapped to GM drum notes. "
                "Try samples with recognizable names (kick.wav, snare.wav) "
                "or ensure your samples are valid audio files."
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
            self._to_info(library, files_by_library.get(library.id, [])) for library in libraries
        ]

    def get_library(self, db: Session, library_id: int) -> LibraryInfo | None:
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None
        files = list(
            db.scalars(
                select(SampleFile)
                .where(SampleFile.library_id == library_id)
                .order_by(SampleFile.midi_note)
            )
        )
        return self._to_info(library, files)

    def activate(self, db: Session, library_id: int) -> LibraryInfo | None:
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None
        # Atomic swap: deactivate all, activate the chosen one. The unique
        # partial index on `is_active = true` ensures only one library is
        # active at a time even if two requests race.
        db.execute(update(SampleLibrary).values(is_active=False))
        library.is_active = True
        db.commit()
        db.refresh(library)
        return self.get_library(db, library_id)

    def deactivate(self, db: Session, library_id: int) -> LibraryInfo | None:
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None
        library.is_active = False
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
            select(SampleLibrary).where(SampleLibrary.is_active == True).limit(1)  # noqa: E712
        ).first()
        if library is None:
            return None
        return self.get_library(db, library.id)

    def update_library(
        self,
        db: Session,
        library_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> LibraryInfo | None:
        """Update a library's name and/or description."""
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None

        changed = False
        if name is not None:
            stripped = name.strip()
            if not stripped:
                raise ValueError("name cannot be empty")
            if library.name != stripped:
                library.name = stripped
                changed = True

        if description is not None:
            new_desc = description.strip() if description else None
            if library.description != new_desc:
                library.description = new_desc
                changed = True

        if changed:
            db.commit()
            db.refresh(library)
            logger.info("sample-library: updated id=%d", library_id)

        return self.get_library(db, library_id)

    def update_sample_note(
        self, db: Session, library_id: int, sample_file_id: int, new_midi_note: int
    ) -> LibraryInfo | None:
        """Update the MIDI note assignment for a sample file in a library."""
        if new_midi_note < 35 or new_midi_note > 81:
            raise ValueError("midi_note must be in 35..81 (GM percussion)")

        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None

        sample_file = db.scalars(
            select(SampleFile).where(
                SampleFile.id == sample_file_id,
                SampleFile.library_id == library_id,
            )
        ).first()
        if sample_file is None:
            return None

        sample_file.midi_note = new_midi_note
        db.commit()
        logger.info(
            "sample-library: updated note for sample id=%d in library id=%d: %d -> %d",
            sample_file_id,
            library_id,
            sample_file.midi_note,
            new_midi_note,
        )
        return self.get_library(db, library_id)

    def update_sample_label(
        self, db: Session, library_id: int, sample_file_id: int, new_label: str
    ) -> LibraryInfo | None:
        """Update the display label for a sample file."""
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None

        sample_file = db.scalars(
            select(SampleFile).where(
                SampleFile.id == sample_file_id,
                SampleFile.library_id == library_id,
            )
        ).first()
        if sample_file is None:
            return None

        safe_label = new_label.strip()[:64]
        if not safe_label:
            raise ValueError("label cannot be empty")

        sample_file.label = safe_label
        db.commit()
        return self.get_library(db, library_id)

    def add_sample_to_library(
        self,
        db: Session,
        library_id: int,
        filename: str,
        content: bytes,
        midi_note: int | None = None,
    ) -> LibraryInfo | None:
        """Add a single sample to an existing library."""
        if midi_note is not None and (midi_note < 35 or midi_note > 81):
            raise ValueError("midi_note must be in 35..81 (GM percussion)")

        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None

        safe_name = _safe_filename(filename)
        if not safe_name:
            raise ValueError("invalid audio file")

        library_dir = self._root / str(library_id)
        library_dir.mkdir(parents=True, exist_ok=True)

        note = midi_note or _resolve_note_from_name(safe_name)
        if note is None:
            classification = self._classifier_service.classify_bytes(content, safe_name)
            if classification is not None:
                note = classification.midi_note
            else:
                raise ValueError("could not determine MIDI note for sample")

        target = library_dir / safe_name
        target.write_bytes(content)

        v_min, v_max = _resolve_velocity_range(safe_name)
        sample_file = SampleFile(
            library_id=library_id,
            label=Path(safe_name).stem,
            midi_note=note,
            file_path=str(target.relative_to(self._root)),
            velocity_offset=0,
            velocity_min=v_min,
            velocity_max=v_max,
        )
        db.add(sample_file)
        db.commit()

        logger.info(
            "sample-library: added sample %s to library id=%d (note=%d)",
            safe_name,
            library_id,
            note,
        )
        return self.get_library(db, library_id)

    def remove_sample_from_library(
        self, db: Session, library_id: int, sample_file_id: int
    ) -> LibraryInfo | None:
        """Remove a sample from a library."""
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None

        sample_file = db.scalars(
            select(SampleFile).where(
                SampleFile.id == sample_file_id,
                SampleFile.library_id == library_id,
            )
        ).first()
        if sample_file is None:
            return None

        file_path = self._root / sample_file.file_path
        if file_path.is_file():
            file_path.unlink(missing_ok=True)

        db.delete(sample_file)
        db.commit()

        remaining = db.scalars(select(SampleFile).where(SampleFile.library_id == library_id)).all()
        if not remaining:
            logger.warning(
                "sample-library: library id=%d has no samples left after removal",
                library_id,
            )

        logger.info(
            "sample-library: removed sample id=%d from library id=%d",
            sample_file_id,
            library_id,
        )
        return self.get_library(db, library_id)

    def batch_remove_samples(
        self, db: Session, library_id: int, sample_ids: list[int]
    ) -> LibraryInfo | None:
        """Remove multiple samples from a library at once."""
        library = db.get(SampleLibrary, library_id)
        if library is None:
            return None

        if not sample_ids:
            return self.get_library(db, library_id)

        sample_files = db.scalars(
            select(SampleFile).where(
                SampleFile.library_id == library_id,
                SampleFile.id.in_(sample_ids),
            )
        ).all()

        removed_count = 0
        for sf in sample_files:
            file_path = self._root / sf.file_path
            if file_path.is_file():
                file_path.unlink(missing_ok=True)
            db.delete(sf)
            removed_count += 1

        db.commit()

        logger.info(
            "sample-library: batch removed %d samples from library id=%d",
            removed_count,
            library_id,
        )
        return self.get_library(db, library_id)

    def export_library(self, db: Session, library_id: int) -> dict | None:
        """Export a library as a JSON-serializable MIDI mapping config.

        Returns a dict with library metadata and a note -> sample mapping
        that can be imported elsewhere or used by the playback engine.
        """
        info = self.get_library(db, library_id)
        if info is None:
            return None

        mapping: dict[int, dict] = {}
        for sample in info.files:
            mapping[sample.midi_note] = {
                "label": sample.label,
                "velocity_offset": sample.velocity_offset,
                "velocity_min": sample.velocity_min,
                "velocity_max": sample.velocity_max,
                "relative_path": sample.relative_path,
            }

        return {
            "version": 1,
            "name": info.name,
            "description": info.description,
            "provider": info.provider,
            "format": "gm_percussion_mapping",
            "note_range": [35, 81],
            "sample_count": len(info.files),
            "mapping": mapping,
        }

    # ---- internal helpers --------------------------------------------------
    def _to_info(self, library: SampleLibrary, files: list[SampleFile]) -> LibraryInfo:
        return LibraryInfo(
            id=library.id,
            name=library.name,
            description=library.description,
            is_active=bool(library.is_active),
            provider=library.provider,
            owner_id=library.owner_id,
            created_at=library.created_at.isoformat() if library.created_at else "",
            updated_at=library.updated_at.isoformat() if library.updated_at else "",
            files=tuple(
                SampleFileInfo(
                    id=sample_file.id,
                    label=sample_file.label,
                    midi_note=sample_file.midi_note,
                    relative_path=sample_file.file_path,
                    velocity_offset=sample_file.velocity_offset,
                    velocity_min=sample_file.velocity_min,
                    velocity_max=sample_file.velocity_max,
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


# Velocity-layer naming conventions.  When a filename contains a known
# dynamic suffix (pp / p / mf / f / ff) or an explicit range
# (vel_001_064), the sample is assigned to that velocity layer so the
# frontend player can pick the right sample per incoming MIDI velocity.
_VELOCITY_LAYER_MAP: dict[str, tuple[int, int]] = {
    "pp": (1, 42),
    "p": (43, 63),
    "mp": (50, 72),
    "mf": (64, 90),
    "f": (91, 110),
    "ff": (111, 127),
    "soft": (1, 63),
    "hard": (64, 127),
    "low": (1, 63),
    "high": (64, 127),
    "quiet": (1, 50),
    "loud": (90, 127),
}

_VEL_RANGE_RE = re.compile(r"vel[_\s-]*(\d{1,3})[_\s-]*(\d{1,3})")
# Short-form velocity range: "v1-50", "v_51_100", "v 86 127".
# Both numbers are 1..127 velocity values (NOT layer indices).
# The leading (?:^|[_\s-]) lets the pattern start at a filename token
# boundary --- \b won't fire between "_" and "v" because both are word
# characters, so we need an explicit boundary class.
_V_SHORT_RANGE_RE = re.compile(r"(?:^|[_\s-])v[_\s-]*(\d{1,3})[_\s-]+(\d{1,3})(?:$|[_\s.])")


def _resolve_velocity_range(filename: str) -> tuple[int, int]:
    """Parse velocity layer from filename. Returns (v_min, v_max) or (1, 127).

    Supports:
      - Dynamic suffixes:  ``kick_pp.wav``, ``snare_ff.wav``
      - Explicit ranges:   ``kick_vel_001_064.wav``, ``snare_vel 065 127.wav``
      - Short-form ranges: ``kick_v1-50.wav``, ``snare_v_51_100.wav``
      - English labels:    ``snare_soft.wav``, ``crash_hard.wav``
    """
    stem = Path(filename).stem.lower()

    # Explicit range: "vel_001_064" or "vel 065 127"
    m = _VEL_RANGE_RE.search(stem)
    if m:
        lo = max(1, min(127, int(m.group(1))))
        hi = max(1, min(127, int(m.group(2))))
        return (lo, hi) if lo <= hi else (hi, lo)

    # Short-form range: "v1-50", "v_51_100", "v 86 127"
    # Requires a separator between the two numbers so "v1" alone (layer
    # index without an explicit upper bound) is NOT consumed here --- it
    # falls through to dynamic-suffix handling or (1, 127).
    m = _V_SHORT_RANGE_RE.search(stem)
    if m:
        lo = max(1, min(127, int(m.group(1))))
        hi = max(1, min(127, int(m.group(2))))
        return (lo, hi) if lo <= hi else (hi, lo)

    # Dynamic / English suffix
    for token in reversed(stem.split("_")):
        token = token.strip("-")
        if token in _VELOCITY_LAYER_MAP:
            return _VELOCITY_LAYER_MAP[token]

    return (1, 127)
