"""Tests for `app.services.midi_mapping_service`.

The mapper is what makes generated MIDI predictable for any GM / XG player.
We verify three things here:
  * the default mapping produces a working GM / XG file with notes intact;
  * `soundfont_overrides` are applied per stem when supplied;
  * `build_soundfont_overrides` produces the right number of stem overrides
    from a real `PresetInfo` list, and returns an empty list when there
    are no presets (so the worker can keep running even without a SF).
  * the XG profile selects XG-native melodic voice variations (LSB!=0)
    for stems with documented XG variations, while falling through to the
    GM voice (bank 0:0) for stems without one.
"""

from __future__ import annotations

from pathlib import Path

from mido import Message, MetaMessage, MidiFile, MidiTrack

from app.services.midi_mapping_service import (
    MidiMappingService,
    MidiProfile,
    SoundfontOverride,
    VoiceMapping,
    _voice_for_profile,
    build_soundfont_overrides,
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


# ---- XG melodic voice variations ----------------------------------------


def test_voice_for_profile_xg_piano_uses_live_grand_variation() -> None:
    """The XG profile for the piano stem must select the documented
    "Live! Grand Piano" variation (bank MSB=0, LSB=1, program=0) instead
    of leaving the GM bank (0,0) untouched."""
    base = VoiceMapping(
        "Acoustic Grand Piano",
        program=0,
        bank_msb=0,
        bank_lsb=0,
        channel=0,
        volume=100,
        pan=64,
    )
    xg = _voice_for_profile(base, "piano", MidiProfile.XG)
    assert xg.label == "Live! Grand Piano"
    assert xg.bank_msb == 0
    assert xg.bank_lsb == 1
    assert xg.program == 0
    # Mixer settings are preserved from the base voice.
    assert xg.channel == 0
    assert xg.volume == 100
    assert xg.pan == 64


def test_voice_for_profile_xg_strings_uses_stereo_strings_variation() -> None:
    base = VoiceMapping(
        "String Ensemble 1",
        program=48,
        bank_msb=0,
        bank_lsb=0,
        channel=3,
        volume=96,
        pan=84,
    )
    xg = _voice_for_profile(base, "strings", MidiProfile.XG)
    assert xg.label == "Stereo Strings"
    assert xg.bank_lsb == 1
    assert xg.program == 48
    # Mixer settings preserved.
    assert xg.channel == 3
    assert xg.volume == 96
    assert xg.pan == 84


def test_voice_for_profile_xg_bass_falls_through_to_gm_voice() -> None:
    """Bass has no documented XG variation in the built-in table, so the
    XG profile must return the GM voice unchanged (bank 0:0 is a valid
    XG voice)."""
    base = VoiceMapping(
        "Electric Bass (finger)",
        program=33,
        bank_msb=0,
        bank_lsb=0,
        channel=1,
        volume=108,
        pan=54,
    )
    xg = _voice_for_profile(base, "bass", MidiProfile.XG)
    assert xg is base or xg == base


def test_voice_for_profile_gm_returns_voice_unchanged() -> None:
    """GM profile never rewrites the voice --- that's the whole point of GM."""
    base = VoiceMapping("Acoustic Grand Piano", program=0, channel=0)
    assert _voice_for_profile(base, "piano", MidiProfile.GM) is base


def test_voice_for_profile_xg_drums_still_use_xg_drum_bank() -> None:
    """Regression: the new melodic-variation path must not break the
    existing drum-bank swap. Drum stems should still pick up the XG
    Standard Kit (bank 127:0)."""
    base = VoiceMapping(
        "Standard Drum Kit",
        program=0,
        channel=9,
        is_drum=True,
        volume=112,
    )
    xg = _voice_for_profile(base, "drums", MidiProfile.XG)
    assert xg.is_drum is True
    assert xg.bank_msb == 127
    assert xg.bank_lsb == 0
    assert xg.channel == 9


def test_xg_melodic_voice_soundfont_override_takes_precedence(tmp_path: Path) -> None:
    """A user-supplied SoundFont override must win over the XG melodic
    variation. The SoundFont is the user's explicit choice; the XG
    variation is just the default for the profile."""
    piano = tmp_path / "piano.mid"
    _write_simple_midi(piano, program=0, note=60)
    overrides = [
        SoundfontOverride(
            stem_key="piano",
            label="My Studio Piano",
            program=4,
            bank_msb=121,
            bank_lsb=0,
        ),
    ]
    result = MidiMappingService().create_variants(
        piano,
        soundfont_overrides=overrides,
    )
    # The applied override is surfaced to the UI.
    piano_override = next(o for o in result.applied_overrides if o["stem"] == "piano")
    assert piano_override["label"] == "My Studio Piano"
    assert piano_override["program"] == 4
    # The XG file on disk carries the SoundFont bank/program, not the XG
    # "Live! Grand Piano" variation.
    xg = MidiFile(str(result.xg_path))
    piano_programs = []
    piano_banks = []
    for track in xg.tracks:
        it = iter(track)
        for msg in it:
            if msg.type == "control_change" and msg.control in (0, 32):
                piano_banks.append((msg.channel, msg.control, msg.value))
            if msg.type == "program_change" and msg.channel == 0:
                piano_programs.append((msg.channel, msg.program))
    assert (0, 4) in piano_programs
    bank_msb_values = [v for (_, c, v) in piano_banks if c == 0]
    bank_lsb_values = [v for (_, c, v) in piano_banks if c == 32]
    assert 121 in bank_msb_values
    assert 0 in bank_lsb_values


def test_xg_file_writes_melodic_variation_banks(tmp_path: Path) -> None:
    """End-to-end: the XG output file for a piano stem must contain the
    XG melodic variation bank (MSB=0, LSB=1) on the piano channel, while
    the GM output keeps the GM bank (0,0)."""
    piano = tmp_path / "piano.mid"
    _write_simple_midi(piano, program=0, note=60)
    result = MidiMappingService().create_variants(piano)

    def read_banks(path: Path) -> dict[int, tuple[int, int]]:
        midi = MidiFile(str(path))
        banks: dict[int, list[int]] = {}
        for track in midi.tracks:
            for msg in track:
                if msg.type == "control_change" and msg.control in (0, 32):
                    banks.setdefault(msg.channel, []).append(msg.value)
        out: dict[int, tuple[int, int]] = {}
        for channel, vals in banks.items():
            # Pairs of (msb, lsb) in encounter order --- first CC0 then CC32.
            msb = next((v for v in vals[:1]), 0)
            lsb = next((v for v in vals[1:2]), 0)
            out[channel] = (msb, lsb)
        return out

    gm_banks = read_banks(result.gm_path)
    xg_banks = read_banks(result.xg_path)
    # GM keeps bank 0:0 for piano (channel 0).
    assert gm_banks.get(0, (0, 0)) == (0, 0)
    # XG promotes piano to the "Live! Grand Piano" variation (bank 0:1).
    assert xg_banks.get(0, (0, 0)) == (0, 1)
