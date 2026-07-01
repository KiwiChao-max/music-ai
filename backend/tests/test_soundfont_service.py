"""Tests for `app.services.soundfont_service`.

Verifies:
  * GM instrument list is complete (128 entries) and well-formed;
  * instrument-type lookup maps every GM program to a valid type;
  * CSV preset table import parses all columns correctly and skips
    invalid rows;
  * bank/program/name fields are round-tripped correctly;
  * empty or malformed CSV does not raise;
  * map_gm_to_custom() matches by instrument type when present;
  * SF2 import returns None for non-SF2 bytes (graceful failure).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.soundfont_service import (
    PresetInfo,
    SoundFontService,
)


def test_service_instantiation(tmp_path: Path) -> None:
    service = SoundFontService(storage_dir=tmp_path / "sf")
    assert service is not None


def test_list_gm_instruments_has_128_entries() -> None:
    service = SoundFontService()
    instruments = service.list_gm_instruments()
    assert len(instruments) == 128
    for program, name in instruments:
        assert isinstance(program, int)
        assert 0 <= program <= 127
        assert isinstance(name, str)
        assert len(name) > 0


def test_get_instrument_type_for_gm_program() -> None:
    service = SoundFontService()
    types_seen: set[str] = set()
    for program in range(128):
        t = service.get_instrument_type_for_gm_program(program)
        assert t is not None
        types_seen.add(t)
    assert "piano" in types_seen
    assert "guitar" in types_seen
    assert "bass" in types_seen
    assert "strings" in types_seen
    assert "brass" in types_seen
    assert "woodwind" in types_seen


def test_get_instrument_type_out_of_range_returns_none() -> None:
    service = SoundFontService()
    assert service.get_instrument_type_for_gm_program(-1) is None
    assert service.get_instrument_type_for_gm_program(200) is None


def test_import_preset_table_parses_all_columns() -> None:
    service = SoundFontService()
    csv_text = (
        "bank_msb,bank_lsb,program,name,category,instrument_type\n"
        "0,0,0,Acoustic Grand Piano,Piano,piano\n"
        "0,0,24,Acoustic Guitar (nylon),Guitar,guitar\n"
        "121,0,0,XG Piano,XG Piano,piano\n"
    )
    presets = service.import_preset_table(csv_text, "test_table")
    assert len(presets) == 3
    assert presets[0].bank_msb == 0
    assert presets[0].bank_lsb == 0
    assert presets[0].program == 0
    assert presets[0].name == "Acoustic Grand Piano"
    assert presets[0].category == "Piano"
    assert presets[0].instrument_type == "piano"
    assert presets[1].program == 24
    assert presets[1].instrument_type == "guitar"
    assert presets[2].bank_msb == 121


def test_import_preset_table_skips_invalid_rows() -> None:
    service = SoundFontService()
    csv_text = (
        "bank_msb,bank_lsb,program,name\n"
        "0,0,0,Piano 1\n"
        "not_a_number,0,1,Bad Row\n"
        "0,0,200,Program too high\n"
        "0,0,,Missing program\n"
        "0,0,5,\n"
        "0,0,7,Valid One\n"
    )
    presets = service.import_preset_table(csv_text, "test")
    assert len(presets) == 2
    assert presets[0].name == "Piano 1"
    assert presets[1].name == "Valid One"


def test_import_preset_table_accepts_bytes() -> None:
    service = SoundFontService()
    csv_bytes = b"bank_msb,bank_lsb,program,name\n0,0,42,Cello\n"
    presets = service.import_preset_table(csv_bytes, "test")
    assert len(presets) == 1
    assert presets[0].program == 42
    assert presets[0].name == "Cello"


def test_import_preset_table_empty_csv() -> None:
    service = SoundFontService()
    presets = service.import_preset_table("", "empty")
    assert isinstance(presets, list)
    assert len(presets) == 0


def test_map_gm_to_custom_by_program() -> None:
    service = SoundFontService()
    presets = [
        PresetInfo(bank_msb=0, bank_lsb=0, program=0, name="My Piano"),
        PresetInfo(bank_msb=0, bank_lsb=0, program=24, name="My Guitar"),
    ]
    mapping = service.map_gm_to_custom(0, presets)
    assert mapping is not None
    assert mapping.gm_program == 0
    assert mapping.target_program == 0
    assert mapping.target_name == "My Piano"


def test_map_gm_to_custom_by_instrument_type() -> None:
    service = SoundFontService()
    presets = [
        PresetInfo(bank_msb=121, bank_lsb=0, program=5, name="XG EPiano", instrument_type="piano"),
        PresetInfo(bank_msb=121, bank_lsb=0, program=27, name="XG Clean Guitar", instrument_type="guitar"),
    ]
    mapping = service.map_gm_to_custom(0, presets, instrument_type="piano")
    assert mapping is not None
    assert mapping.target_name == "XG EPiano"
    assert mapping.target_bank_msb == 121


def test_map_gm_to_custom_no_match() -> None:
    service = SoundFontService()
    presets = [PresetInfo(bank_msb=0, bank_lsb=0, program=0, name="Piano")]
    mapping = service.map_gm_to_custom(42, presets)
    assert mapping is None


def test_import_soundfont_returns_none_for_invalid_file(tmp_path: Path) -> None:
    service = SoundFontService(storage_dir=tmp_path / "sf")
    result = service.import_soundfont("test", b"not a real sf2 file", "desc")
    assert result is None


def test_import_soundfont_cleans_up_on_failure(tmp_path: Path) -> None:
    service = SoundFontService(storage_dir=tmp_path / "sf")
    service.import_soundfont("bad_sf", b"definitely not sf2", None)
    sf_dir = tmp_path / "sf"
    files = list(sf_dir.glob("*.sf2")) if sf_dir.exists() else []
    assert len(files) == 0


def test_preset_info_is_frozen() -> None:
    p = PresetInfo(bank_msb=0, bank_lsb=0, program=0, name="Test")
    with pytest.raises(AttributeError):
        p.name = "mutated"  # type: ignore[misc]
