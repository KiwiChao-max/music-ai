"""SoundFont and preset table management service.

Supports:
  - Importing SoundFont 2 (.sf2) files
  - Importing preset tables from CSV (e.g., electronic keyboard tone lists)
  - Mapping GM/XG program numbers to custom presets
  - Exporting preset mappings for use in MIDI rendering

The service stores presets in the database and provides methods to:
  - Load SF2 files and extract preset information
  - Parse CSV preset tables
  - Map GM program numbers to custom presets
  - List available presets
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PresetInfo:
    bank_msb: int
    bank_lsb: int
    program: int
    name: str
    category: str | None = None
    instrument_type: str | None = None


@dataclass(frozen=True)
class SoundFontInfo:
    id: int | None
    name: str
    description: str | None
    file_path: str
    preset_count: int
    presets: list[PresetInfo]


@dataclass(frozen=True)
class PresetMapping:
    gm_program: int
    target_bank_msb: int
    target_bank_lsb: int
    target_program: int
    target_name: str


# Instrument-type aliases: common CSV variations → canonical key.
# Case-insensitive; the CSV importer normalises through this dict.
_INSTRUMENT_TYPE_ALIASES: dict[str, str] = {
    "piano": "piano",
    "grand piano": "piano",
    "acoustic piano": "piano",
    "electric piano": "piano",
    "ep": "piano",
    "guitar": "guitar",
    "acoustic guitar": "guitar",
    "electric guitar": "guitar",
    "bass": "bass",
    "bass guitar": "bass",
    "electric bass": "bass",
    "acoustic bass": "bass",
    "strings": "strings",
    "string": "strings",
    "string ensemble": "strings",
    "violin": "strings",
    "cello": "strings",
    "viola": "strings",
    "organ": "organ",
    "pipe organ": "organ",
    "brass": "brass",
    "trumpet": "brass",
    "trombone": "brass",
    "sax": "woodwind",
    "saxophone": "woodwind",
    "flute": "woodwind",
    "clarinet": "woodwind",
    "woodwind": "woodwind",
    "synth": "synth_lead",
    "synthesizer": "synth_lead",
    "lead": "synth_lead",
    "pad": "synth_pad",
    "synth pad": "synth_pad",
    "drums": "drums",
    "drum": "drums",
    "percussion": "percussion",
    "vocal": "vocals",
    "voice": "vocals",
    "choir": "vocals",
    "ethnic": "ethnic",
    "fx": "synth_fx",
    "sound effect": "synth_fx",
}


def _normalize_instrument_type(raw: str | None) -> str | None:
    """Normalise a user-supplied instrument_type string to a canonical key."""
    if raw is None:
        return None
    key = raw.strip().lower()
    if not key:
        return None
    return _INSTRUMENT_TYPE_ALIASES.get(key, key)


def _normalize_name(name: str) -> set[str]:
    """Extract normalised keyword tokens from an instrument name.

    "Acoustic Grand Piano" → {"acoustic", "grand", "piano"}
    "Grand Piano 1"       → {"grand", "piano", "1"}
    """
    return set(
        token.strip("()[]{},.0123456789")
        for token in name.lower().replace("-", " ").replace("/", " ").split()
        if token.strip("()[]{},.0123456789")
    )


def _name_similarity(gm_name: str, preset_name: str) -> float:
    """Return a 0..1 similarity score based on token overlap.

    Jaccard-like: |intersection| / max(|A|, |B|).  A score of 1.0 means
    all tokens of one name are contained in the other.
    """
    a = _normalize_name(gm_name)
    b = _normalize_name(preset_name)
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    denom = max(len(a), len(b))
    return intersection / denom if denom > 0 else 0.0


# GM standard instrument list (programs 0-127). Module-level constant so
# we don't rebuild the 128-entry list on every call to list_gm_instruments.
_GM_INSTRUMENTS: tuple[tuple[int, str], ...] = (
    (0, "Acoustic Grand Piano"),
    (1, "Bright Acoustic Piano"),
    (2, "Electric Grand Piano"),
    (3, "Honky-tonk Piano"),
    (4, "Electric Piano 1"),
    (5, "Electric Piano 2"),
    (6, "Harpsichord"),
    (7, "Clavinet"),
    (8, "Celesta"),
    (9, "Glockenspiel"),
    (10, "Music Box"),
    (11, "Vibraphone"),
    (12, "Marimba"),
    (13, "Xylophone"),
    (14, "Tubular Bells"),
    (15, "Dulcimer"),
    (16, "Drawbar Organ"),
    (17, "Percussive Organ"),
    (18, "Rock Organ"),
    (19, "Church Organ"),
    (20, "Reed Organ"),
    (21, "Accordion"),
    (22, "Harmonica"),
    (23, "Tango Accordion"),
    (24, "Acoustic Guitar (nylon)"),
    (25, "Acoustic Guitar (steel)"),
    (26, "Electric Guitar (jazz)"),
    (27, "Electric Guitar (clean)"),
    (28, "Electric Guitar (muted)"),
    (29, "Overdriven Guitar"),
    (30, "Distortion Guitar"),
    (31, "Guitar Harmonics"),
    (32, "Acoustic Bass"),
    (33, "Electric Bass (finger)"),
    (34, "Electric Bass (pick)"),
    (35, "Fretless Bass"),
    (36, "Slap Bass 1"),
    (37, "Slap Bass 2"),
    (38, "Synth Bass 1"),
    (39, "Synth Bass 2"),
    (40, "Violin"),
    (41, "Viola"),
    (42, "Cello"),
    (43, "Contrabass"),
    (44, "Tremolo Strings"),
    (45, "Pizzicato Strings"),
    (46, "Orchestral Harp"),
    (47, "Timpani"),
    (48, "String Ensemble 1"),
    (49, "String Ensemble 2"),
    (50, "Synth Strings 1"),
    (51, "Synth Strings 2"),
    (52, "Choir Aahs"),
    (53, "Voice Oohs"),
    (54, "Synth Choir"),
    (55, "Orchestra Hit"),
    (56, "Trumpet"),
    (57, "Trombone"),
    (58, "Tuba"),
    (59, "Muted Trumpet"),
    (60, "French Horn"),
    (61, "Brass Section"),
    (62, "Synth Brass 1"),
    (63, "Synth Brass 2"),
    (64, "Soprano Sax"),
    (65, "Alto Sax"),
    (66, "Tenor Sax"),
    (67, "Baritone Sax"),
    (68, "Oboe"),
    (69, "English Horn"),
    (70, "Bassoon"),
    (71, "Clarinet"),
    (72, "Piccolo"),
    (73, "Flute"),
    (74, "Recorder"),
    (75, "Pan Flute"),
    (76, "Blown Bottle"),
    (77, "Shakuhachi"),
    (78, "Whistle"),
    (79, "Ocarina"),
    (80, "Lead 1 (square)"),
    (81, "Lead 2 (sawtooth)"),
    (82, "Lead 3 (calliope)"),
    (83, "Lead 4 (chiff)"),
    (84, "Lead 5 (charang)"),
    (85, "Lead 6 (voice)"),
    (86, "Lead 7 (fifths)"),
    (87, "Lead 8 (bass + lead)"),
    (88, "Pad 1 (new age)"),
    (89, "Pad 2 (warm)"),
    (90, "Pad 3 (polysynth)"),
    (91, "Pad 4 (choir)"),
    (92, "Pad 5 (bowed)"),
    (93, "Pad 6 (metallic)"),
    (94, "Pad 7 (halo)"),
    (95, "Pad 8 (sweep)"),
    (96, "FX 1 (rain)"),
    (97, "FX 2 (soundtrack)"),
    (98, "FX 3 (crystal)"),
    (99, "FX 4 (atmosphere)"),
    (100, "FX 5 (brightness)"),
    (101, "FX 6 (goblins)"),
    (102, "FX 7 (echoes)"),
    (103, "FX 8 (sci-fi)"),
    (104, "Sitar"),
    (105, "Banjo"),
    (106, "Shamisen"),
    (107, "Koto"),
    (108, "Kalimba"),
    (109, "Bag pipe"),
    (110, "Fiddle"),
    (111, "Shanai"),
    (112, "Tinkle Bell"),
    (113, "Agogo"),
    (114, "Steel Drums"),
    (115, "Woodblock"),
    (116, "Taiko Drum"),
    (117, "Melodic Tom"),
    (118, "Synth Drum"),
    (119, "Reverse Cymbal"),
    (120, "Guitar Fret Noise"),
    (121, "Breath Noise"),
    (122, "Seashore"),
    (123, "Bird Tweet"),
    (124, "Telephone Ring"),
    (125, "Helicopter"),
    (126, "Applause"),
    (127, "Gunshot"),
)
_GM_INSTRUMENT_NAMES: dict[int, str] = dict(_GM_INSTRUMENTS)


class SoundFontService:
    """Manage SoundFont files and preset mappings."""

    def __init__(self, storage_dir: Path | None = None) -> None:
        from app.config import settings
        self._storage_dir = storage_dir or Path(settings.storage_dir) / "soundfonts"
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def import_soundfont(self, name: str, content: bytes, description: str | None = None) -> SoundFontInfo | None:
        """Import a SoundFont 2 (.sf2) file.

        Extracts preset information and stores the file. Returns SoundFontInfo
        with all presets, or None if the file cannot be parsed.
        """
        temp_path = self._storage_dir / f"{name}.sf2"
        temp_path.write_bytes(content)

        try:
            presets = self._extract_sf2_presets(temp_path)
            if not presets:
                temp_path.unlink(missing_ok=True)
                return None

            return SoundFontInfo(
                id=None,
                name=name,
                description=description,
                file_path=str(temp_path.relative_to(self._storage_dir.parent)),
                preset_count=len(presets),
                presets=presets,
            )
        except Exception as exc:
            logger.warning("soundfont: failed to parse %s: %s", name, exc)
            temp_path.unlink(missing_ok=True)
            return None

    def import_preset_table(self, csv_content: str | bytes, name: str) -> list[PresetInfo]:
        """Import a preset table from CSV format.

        Expected CSV columns:
          - bank_msb (optional, default 0)
          - bank_lsb (optional, default 0)
          - program (required, 0-127)
          - name (required, preset name)
          - category (optional)
          - instrument_type (optional: piano, guitar, bass, strings, etc.)

        Returns a list of PresetInfo objects.
        """
        if isinstance(csv_content, bytes):
            csv_content = csv_content.decode("utf-8")

        presets: list[PresetInfo] = []
        reader = csv.DictReader(io.StringIO(csv_content))

        for row in reader:
            try:
                program = int(row["program"])
                if program < 0 or program > 127:
                    continue

                bank_msb = int(row.get("bank_msb", "0"))
                bank_lsb = int(row.get("bank_lsb", "0"))
                preset_name = row.get("name", "")
                if not preset_name:
                    continue

                presets.append(PresetInfo(
                    bank_msb=bank_msb,
                    bank_lsb=bank_lsb,
                    program=program,
                    name=preset_name,
                    category=row.get("category"),
                    instrument_type=_normalize_instrument_type(row.get("instrument_type")),
                ))
            except (KeyError, ValueError):
                continue

        return presets

    def map_gm_to_custom(
        self,
        gm_program: int,
        presets: list[PresetInfo],
        instrument_type: str | None = None,
    ) -> PresetMapping | None:
        """Map a GM program number to a custom preset.

        Matching strategy (tried in order):
          1. Exact instrument_type match.
          2. Exact program number match.
          3. Fuzzy name match — token overlap with GM instrument name.
        """
        # 1. Exact instrument_type match.
        if instrument_type:
            candidates = [
                p for p in presets
                if p.instrument_type == instrument_type
            ]
            if candidates:
                return PresetMapping(
                    gm_program=gm_program,
                    target_bank_msb=candidates[0].bank_msb,
                    target_bank_lsb=candidates[0].bank_lsb,
                    target_program=candidates[0].program,
                    target_name=candidates[0].name,
                )

        # 2. Exact program match.
        candidates = [
            p for p in presets
            if p.program == gm_program
        ]
        if candidates:
            return PresetMapping(
                gm_program=gm_program,
                target_bank_msb=candidates[0].bank_msb,
                target_bank_lsb=candidates[0].bank_lsb,
                target_program=candidates[0].program,
                target_name=candidates[0].name,
            )

        # 3. Fuzzy name match (token overlap).
        gm_name = _GM_INSTRUMENT_NAMES.get(gm_program, "")
        if gm_name and presets:
            best_score = 0.0
            best_preset: PresetInfo | None = None
            for p in presets:
                score = _name_similarity(gm_name, p.name)
                if score > best_score:
                    best_score = score
                    best_preset = p
            if best_preset is not None and best_score >= 0.4:
                logger.debug(
                    "soundfont: fuzzy match gm=%d (%s) -> preset=%s (score=%.2f)",
                    gm_program, gm_name, best_preset.name, best_score,
                )
                return PresetMapping(
                    gm_program=gm_program,
                    target_bank_msb=best_preset.bank_msb,
                    target_bank_lsb=best_preset.bank_lsb,
                    target_program=best_preset.program,
                    target_name=best_preset.name,
                )

        return None

    def list_gm_instruments(self) -> list[tuple[int, str]]:
        """Return GM program numbers with their standard names."""
        return list(_GM_INSTRUMENTS)

    def get_instrument_type_for_gm_program(self, gm_program: int) -> str | None:
        """Return the instrument type for a GM program number."""
        type_ranges = {
            "piano": range(0, 8),
            "keyboard": range(8, 24),
            "organ": range(16, 24),
            "guitar": range(24, 32),
            "bass": range(32, 40),
            "strings": range(40, 48),
            "orchestra": range(48, 56),
            "brass": range(56, 64),
            "woodwind": range(64, 80),
            "synth_lead": range(80, 88),
            "synth_pad": range(88, 96),
            "synth_fx": range(96, 104),
            "ethnic": range(104, 112),
            "percussion": range(112, 128),
        }

        for instrument_type, program_range in type_ranges.items():
            if gm_program in program_range:
                return instrument_type
        return None

    def _extract_sf2_presets(self, sf2_path: Path) -> list[PresetInfo]:
        """Extract presets from a SoundFont 2 file.

        Tries `sf2utils` (a proper SF2 parser) first; falls back to a
        simplified parser that reads only the phdr chunk.  The simplified
        parser is sufficient for most single-layer SF2 files, but complex
        SoundFonts with multiple zones and generators are better handled
        by sf2utils.
        """
        presets = self._extract_sf2_with_sf2utils(sf2_path)
        if presets:
            return presets
        return self._extract_sf2_simplified(sf2_path)

    @staticmethod
    def _extract_sf2_with_sf2utils(sf2_path: Path) -> list[PresetInfo]:
        """Try parsing with sf2utils (optional dependency)."""
        try:
            from sf2utils.sf2parse import Sf2File
        except ImportError:
            return []

        presets: list[PresetInfo] = []
        try:
            with open(sf2_path, "rb") as f:
                sf2 = Sf2File(f)
        except Exception as exc:
            logger.debug("soundfont: sf2utils failed: %s", exc)
            return []

        for preset in sf2.presets:
            if not preset.name or preset.bank > 0x7FFF:
                continue
            bank_msb = (preset.bank >> 8) & 0x7F
            bank_lsb = preset.bank & 0x7F
            presets.append(PresetInfo(
                bank_msb=bank_msb,
                bank_lsb=bank_lsb,
                program=preset.preset,
                name=preset.name,
                instrument_type=None,
            ))
        return presets

    @staticmethod
    def _extract_sf2_simplified(sf2_path: Path) -> list[PresetInfo]:
        """Simplified SF2 parser — reads phdr chunk only.

        Handles the common case where each preset header maps to exactly
        one instrument.  Complex SF2 files with multiple zones per preset
        should use sf2utils instead.

        Uses ``mmap`` instead of ``f.read()`` so a 200 MB SF2 file
        doesn't allocate a 200 MB Python `bytes` object — the OS pages
        the file in on demand as we touch the phdr chunk.
        """
        import mmap

        presets: list[PresetInfo] = []

        try:
            with open(sf2_path, "rb") as f:
                # `fileno()` gives us the raw OS file descriptor; mmap
                # maps it into the address space without copying.
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
                    if len(data) < 4 or data[:4] != b"RIFF":
                        return []

                    preset_start = data.find(b"phdr")
                    if preset_start == -1:
                        return []

                    offset = preset_start + 8
                    while offset + 38 <= len(data):
                        name = data[offset:offset+20].decode("ascii", errors="ignore").strip("\x00")
                        preset = int.from_bytes(data[offset+20:offset+22], "little")
                        bank = int.from_bytes(data[offset+22:offset+24], "little")
                        offset += 38

                        if name and preset >= 0 and preset <= 127:
                            bank_msb = (bank >> 8) & 0xFF
                            bank_lsb = bank & 0xFF
                            presets.append(PresetInfo(
                                bank_msb=bank_msb,
                                bank_lsb=bank_lsb,
                                program=preset,
                                name=name,
                            ))

        except (ValueError, OSError) as exc:
            # ValueError: empty file (mmap can't map 0 bytes)
            # OSError: file too large / permission denied
            logger.debug("soundfont: simplified parser failed: %s", exc)

        return presets

    # ---- database CRUD ----------------------------------------------------

    def list_soundfonts(self, db) -> list[dict]:
        """List all SoundFonts and preset tables from the database."""
        from sqlalchemy import select
        from app.db.models import SoundFont

        rows = list(db.scalars(select(SoundFont).order_by(SoundFont.created_at.desc())))
        return [self._sf_row_to_dict(row) for row in rows]

    def get_soundfont(self, db, soundfont_id: int) -> dict | None:
        """Get a SoundFont by ID with its presets."""
        from sqlalchemy import select
        from app.db.models import SoundFont, SoundFontPreset

        sf = db.get(SoundFont, soundfont_id)
        if sf is None:
            return None

        presets = list(db.scalars(
            select(SoundFontPreset)
            .where(SoundFontPreset.soundfont_id == soundfont_id)
            .order_by(SoundFontPreset.bank_msb, SoundFontPreset.bank_lsb, SoundFontPreset.program)
        ))

        result = self._sf_row_to_dict(sf)
        result["presets"] = [self._preset_row_to_dict(p) for p in presets]
        return result

    def get_active_soundfont(self, db) -> dict | None:
        """Get the currently active SoundFont, or None."""
        from sqlalchemy import select
        from app.db.models import SoundFont

        sf = db.scalars(
            select(SoundFont).where(SoundFont.is_active == 1).limit(1)
        ).first()
        if sf is None:
            return None
        return self.get_soundfont(db, sf.id)

    def activate_soundfont(self, db, soundfont_id: int) -> dict | None:
        """Activate a SoundFont (deactivates all others)."""
        from sqlalchemy import update
        from app.db.models import SoundFont

        sf = db.get(SoundFont, soundfont_id)
        if sf is None:
            return None

        db.execute(update(SoundFont).values(is_active=0))
        sf.is_active = 1
        db.commit()
        db.refresh(sf)
        return self.get_soundfont(db, soundfont_id)

    def delete_soundfont(self, db, soundfont_id: int) -> bool:
        """Delete a SoundFont and its presets."""
        from sqlalchemy import select
        from app.db.models import SoundFont, SoundFontPreset

        sf = db.get(SoundFont, soundfont_id)
        if sf is None:
            return False

        for preset in db.scalars(
            select(SoundFontPreset).where(SoundFontPreset.soundfont_id == soundfont_id)
        ):
            db.delete(preset)

        if sf.file_path:
            full_path = self._storage_dir.parent / sf.file_path
            if full_path.is_file():
                full_path.unlink(missing_ok=True)

        db.delete(sf)
        db.commit()
        return True

    def save_soundfont_to_db(
        self,
        db,
        *,
        name: str,
        description: str | None,
        sf_type: str,
        file_path: str | None,
        presets: list[PresetInfo],
    ) -> dict:
        """Save a SoundFont and its presets to the database."""
        from app.db.models import SoundFont, SoundFontPreset

        sf = SoundFont(
            name=name.strip(),
            description=description,
            type=sf_type,
            file_path=file_path,
            preset_count=len(presets),
            is_active=0,
        )
        db.add(sf)
        db.flush()

        for p in presets:
            db.add(SoundFontPreset(
                soundfont_id=sf.id,
                bank_msb=p.bank_msb,
                bank_lsb=p.bank_lsb,
                program=p.program,
                name=p.name,
                category=p.category,
                instrument_type=p.instrument_type,
            ))

        db.commit()
        db.refresh(sf)
        return self.get_soundfont(db, sf.id)  # type: ignore[return-value]

    # ---- internal helpers -------------------------------------------------

    def _sf_row_to_dict(self, sf) -> dict:
        return {
            "id": sf.id,
            "name": sf.name,
            "description": sf.description,
            "type": sf.type,
            "file_path": sf.file_path,
            "preset_count": sf.preset_count,
            "is_active": bool(sf.is_active),
            "created_at": sf.created_at.isoformat() if sf.created_at else "",
            "updated_at": sf.updated_at.isoformat() if sf.updated_at else "",
        }

    def _preset_row_to_dict(self, p) -> dict:
        return {
            "id": p.id,
            "bank_msb": p.bank_msb,
            "bank_lsb": p.bank_lsb,
            "program": p.program,
            "name": p.name,
            "category": p.category,
            "instrument_type": p.instrument_type,
        }
