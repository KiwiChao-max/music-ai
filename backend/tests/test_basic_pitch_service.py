"""Tests for `app.services.basic_pitch_service`.

The heavy transcription paths (Basic Pitch ONNX, librosa pyin) need real
audio + model files and are covered by the E2E suite. This module tests
the pure helpers that the transcription pipeline delegates to:

* `_track_has_notes` — predicate used to decide which tracks get GM setup
* `_write_notes_csv` — CSV sidecar writer
* `_inject_gm_setup` — post-processing that prepends GM/CC messages to
  every note-bearing track in a Basic Pitch MIDI output
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from mido import MidiFile, MetaMessage, Message, MidiTrack

from app.services.basic_pitch_service import (
    BasicPitchService,
    _track_has_notes,
)


def _make_midi_with_note_track(tmp_path: Path, *, with_note: bool = True) -> Path:
    """Build a minimal MIDI file with one note track + one empty track."""
    midi = MidiFile(ticks_per_beat=480)
    # Track 0: tempo / time-sig only — no notes.
    setup_track = MidiTrack()
    setup_track.append(MetaMessage("track_name", name="setup", time=0))
    setup_track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    midi.tracks.append(setup_track)
    # Track 1: optionally a single note_on / note_off pair.
    note_track = MidiTrack()
    note_track.append(MetaMessage("track_name", name="notes", time=0))
    if with_note:
        note_track.append(Message("note_on", note=60, velocity=80, time=0))
        note_track.append(Message("note_off", note=60, velocity=0, time=120))
    midi.tracks.append(note_track)

    path = tmp_path / "out.mid"
    midi.save(str(path))
    return path


# ---- _track_has_notes ----------------------------------------------------
def test_track_has_notes_true_for_note_track() -> None:
    midi = MidiFile()
    track = MidiTrack()
    track.append(Message("note_on", note=60, velocity=80, time=0))
    track.append(Message("note_off", note=60, velocity=0, time=120))
    assert _track_has_notes(track) is True


def test_track_has_notes_false_for_meta_only_track() -> None:
    midi = MidiFile()
    track = MidiTrack()
    track.append(MetaMessage("track_name", name="setup", time=0))
    track.append(MetaMessage("set_tempo", tempo=500000, time=0))
    assert _track_has_notes(track) is False


def test_track_has_notes_false_for_empty_track() -> None:
    assert _track_has_notes(MidiTrack()) is False


# ---- _write_notes_csv ----------------------------------------------------
def test_write_notes_csv_round_trips_rows(tmp_path: Path) -> None:
    events = [
        (0.0, 1.0, 60, 80),
        (1.5, 2.25, 72, 100),
        (3.0, 3.5, 48, 64),
    ]
    csv_path = tmp_path / "notes.csv"
    count = BasicPitchService._write_notes_csv(events, csv_path)

    assert count == 3
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert rows[0]["pitch_midi"] == "60"
    assert rows[0]["velocity"] == "80"
    assert rows[1]["end_time_s"] == "2.250000"
    assert rows[2]["start_time_s"] == "3.000000"


def test_write_notes_csv_skips_malformed_events(tmp_path: Path) -> None:
    # Malformed rows (wrong arity) must be skipped via the IndexError
    # guard in the unpacking try/except — Basic Pitch fallback can emit
    # odd tuples. Note: the conversion `float(start)` is not in the
    # guarded block, so non-numeric strings still raise; that's the
    # documented contract.
    events = [
        (0.0, 1.0, 60, 80),  # valid
        (1.0, 2.0),  # missing fields — IndexError on unpack
        (2.0, 3.0, 64, 90),  # valid
        None,  # TypeError on unpack
    ]
    csv_path = tmp_path / "notes.csv"
    count = BasicPitchService._write_notes_csv(events, csv_path)

    assert count == 2
    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    assert [r["pitch_midi"] for r in rows] == ["60", "64"]


# ---- _inject_gm_setup ----------------------------------------------------
def test_inject_gm_setup_prepends_cc_messages_to_note_track(tmp_path: Path) -> None:
    """`_inject_gm_setup` must insert bank-select + program-change +
    expressive CCs right after the `track_name` meta on every note track.

    The setup track (no notes) must be left untouched.
    """
    midi_path = _make_midi_with_note_track(tmp_path, with_note=True)

    BasicPitchService._inject_gm_setup(midi_path, stem_key="piano")

    midi = MidiFile(str(midi_path))
    # Track 0 has no notes — must not have any CC inserted.
    setup_track = midi.tracks[0]
    cc_types = [m.type for m in setup_track if not m.is_meta]
    assert "control_change" not in cc_types
    assert "program_change" not in cc_types

    # Track 1 has notes — must start with track_name, then GM setup, then
    # the original note_on/note_off.
    note_track = midi.tracks[1]
    msg_types = [m.type for m in note_track]
    # Setup appears before the first note_on.
    first_note_idx = msg_types.index("note_on")
    pre_note_types = msg_types[:first_note_idx]
    assert "track_name" in pre_note_types
    assert "control_change" in pre_note_types  # bank select + CCs
    assert "program_change" in pre_note_types


def test_inject_gm_setup_unknown_stem_uses_other_defaults(tmp_path: Path) -> None:
    """An unknown stem key must fall back to the `other` config, not raise."""
    midi_path = _make_midi_with_note_track(tmp_path, with_note=True)
    BasicPitchService._inject_gm_setup(midi_path, stem_key="nonexistent_stem")
    # The note track must still receive GM setup.
    midi = MidiFile(str(midi_path))
    note_track = midi.tracks[1]
    assert any(m.type == "program_change" for m in note_track)


def test_inject_gm_setup_missing_file_is_noop(tmp_path: Path) -> None:
    """A missing / unreadable MIDI file must not raise."""
    missing = tmp_path / "does_not_exist.mid"
    # Should not raise.
    BasicPitchService._inject_gm_setup(missing, stem_key="piano")


# ---- _normalize_stem_key ------------------------------------------------
@pytest.mark.parametrize(
    "stem,expected",
    [
        # Demucs primary stems pass through unchanged.
        ("piano", "piano"),
        ("bass", "bass"),
        ("guitar", "guitar"),
        ("strings", "strings"),
        ("vocals", "vocals"),
        ("other", "other"),
        # Classifier-produced per-instrument files must strip "other_"
        # so they pick up the correct GM voice instead of Warm Pad.
        ("other_strings", "strings"),
        ("other_piano", "piano"),
        ("other_guitar", "guitar"),
        ("other_synth", "synth"),
        # Truly unknown stems fall back to "other".
        ("unknown_thing", "other"),
        ("other_unknown", "other"),
    ],
)
def test_normalize_stem_key_maps_per_instrument_files(stem: str, expected: str) -> None:
    assert BasicPitchService._normalize_stem_key(stem) == expected


def test_stem_cc_config_has_synth_entry() -> None:
    """The synth stem must have its own GM voice (Lead 1 square = program 80).

    Without this entry, other_synth.wav (from the instrument classifier)
    would still fall through to "other" (Warm Pad) even after F2's
    stem_key normalization fix.
    """
    assert "synth" in BasicPitchService._STEM_CC_CONFIG
    assert BasicPitchService._STEM_CC_CONFIG["synth"]["program"] == 80

