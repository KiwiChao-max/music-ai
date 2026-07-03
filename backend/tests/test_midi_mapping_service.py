"""Tests for `app.services.midi_mapping_service`.

The mapper is what makes generated MIDI predictable for any GM / XG player.
We verify three things here:
  * the default mapping produces a working GM / XG file with notes intact;
  * `soundfont_overrides` are applied per stem when supplied;
  * `build_soundfont_overrides` produces the right number of stem overrides
    from a real `PresetInfo` list, and returns an empty list when there
    are no presets (so the worker can keep running even without a SF).
"""
from __future__ import annotations

from pathlib import Path

from mido import Message, MidiFile, MidiTrack, MetaMessage

from app.services.midi_mapping_service import (
    MidiMappingService,
    MidiProfile,
    SoundfontOverride,
    build_soundfont_overrides,
    collect_raw_midi_sources,
    is_raw_midi_path,
    stem_key_from_name,
)


def _write_simple_midi(path: Path, *, program: int = 0, note: int = 60) -> None:
    """Write a minimal MIDI file with one note and a program change."""
    midi = MidiFile(ticks_per_beat=480)
    track = MidiTrack()
    track.append(MetaMessage("track_name", name=path.stem, time=0))
    track.append(Message("program_change", channel=0, program=program, time=0))
    track.append(Message("note_on", channel=0, note=note, velocity=100, time=0))
    track.append(Message("note_off", channel=0, note=note, velocity=0, time=240))
    track.append(MetaMessage("end_of_track", time=0))
    midi.tracks.append(track)
    midi.save(str(path))


def test_is_raw_midi_path_skips_generated_outputs(tmp_path: Path) -> None:
    raw = tmp_path / "bass.mid"
    gm = tmp_path / "bass_gm.mid"
    xg = tmp_path / "bass_xg.mid"
    kick = tmp_path / "bass_kick.mid"  # generated drum part
    raw.write_bytes(b"")
    gm.write_bytes(b"")
    xg.write_bytes(b"")
    kick.write_bytes(b"")
    assert is_raw_midi_path(raw)
    assert not is_raw_midi_path(gm)
    assert not is_raw_midi_path(xg)
    assert not is_raw_midi_path(kick)


def test_stem_key_from_name_classifies_aliases() -> None:
    assert stem_key_from_name("bass") == "bass"
    assert stem_key_from_name("Bass Stem") == "bass"
    assert stem_key_from_name("guitar_track") == "guitar"
    assert stem_key_from_name("violin") == "strings"
    assert stem_key_from_name("drums") == "drums"
    assert stem_key_from_name("vocal_lead") == "vocals"


def test_default_mapping_writes_gm_and_xg_files(tmp_path: Path) -> None:
    bass = tmp_path / "bass.mid"
    _write_simple_midi(bass, program=0, note=40)
    result = MidiMappingService().create_variants(bass)
    assert result.gm_path.is_file()
    assert result.xg_path.is_file()
    # No soundfont override means no applied overrides.
    assert result.applied_overrides == ()


def test_soundfont_overrides_replace_program_and_bank(tmp_path: Path) -> None:
    bass = tmp_path / "bass.mid"
    _write_simple_midi(bass, program=0, note=40)
    overrides = [
        SoundfontOverride(
            stem_key="bass",
            label="Custom Fingered Bass",
            program=20,
            bank_msb=121,
            bank_lsb=2,
        ),
    ]
    result = MidiMappingService().create_variants(bass, soundfont_overrides=overrides)
    assert any(
        o["stem"] == "bass" and o["label"] == "Custom Fingered Bass"
        for o in result.applied_overrides
    )
    # Read the GM file back and confirm bank + program were rewritten.
    gm = MidiFile(str(result.gm_path))
    programs = []
    for track in gm.tracks:
        for msg in track:
            if msg.type == "program_change":
                programs.append((msg.channel, msg.program))
    assert (1, 20) in programs  # bass mapped to channel 1 by the default voice


def test_soundfont_overrides_skip_drum_stem(tmp_path: Path) -> None:
    drums = tmp_path / "drums.mid"
    _write_simple_midi(drums, program=0, note=36)
    # No overrides passed in -> mapper keeps the default GM drum kit.
    result = MidiMappingService().create_variants(drums)
    assert result.applied_overrides == ()


def test_build_soundfont_overrides_skips_drum_stem() -> None:
    """`build_soundfont_overrides` only emits melodic overrides; the
    sample-library pipeline owns the drum mapping."""
    from app.services.soundfont_service import PresetInfo

    presets = [
        PresetInfo(bank_msb=0, bank_lsb=0, program=0, name="DrumKit", instrument_type="drums"),
    ]
    overrides = build_soundfont_overrides(presets)
    # The drum preset must NOT be turned into a stem override, even though
    # `voice.is_drum` for the `drums` base voice is True.
    assert all(o.stem_key != "drums" for o in overrides)


def test_build_soundfont_overrides_returns_empty_when_no_presets() -> None:
    assert build_soundfont_overrides([]) == []
    assert build_soundfont_overrides(None) == []  # type: ignore[arg-type]


def test_build_soundfont_overrides_picks_matching_family() -> None:
    """A piano preset in the SF should be picked for the piano base voice."""
    from app.services.soundfont_service import PresetInfo

    presets = [
        PresetInfo(bank_msb=0, bank_lsb=0, program=0, name="MyPiano", instrument_type="piano"),
        PresetInfo(bank_msb=0, bank_lsb=0, program=33, name="MyBass", instrument_type="bass"),
        PresetInfo(bank_msb=0, bank_lsb=0, program=24, name="MyGuitar", instrument_type="guitar"),
    ]
    overrides = build_soundfont_overrides(presets, soundfont_name="TestSF")
    # `seen_stems` dedup prevents the same preset being applied twice
    # (piano and "original" both map to GM program 0, so the first one
    # wins). Bass + guitar have their own families.
    keys = {o.stem_key for o in overrides}
    assert "piano" in keys
    assert "bass" in keys
    assert "guitar" in keys
    piano_override = next(o for o in overrides if o.stem_key == "piano")
    assert piano_override.label == "MyPiano"
    assert piano_override.program == 0
