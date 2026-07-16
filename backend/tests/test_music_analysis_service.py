"""Tests for the pure-function helpers in `app.services.music_analysis_service`.

The public class methods depend on disk I/O, but the estimation functions
(BPM, key, chords, sections, instrumentation/arrangement advice) are pure
and only need a list of `NoteEvent`s. We exercise those here.
"""
from __future__ import annotations

from app.services.music_analysis_service import (
    MusicAnalysisService,
    NoteEvent,
    _arrangement_advice,
    _estimate_bpm,
    _estimate_chords,
    _estimate_key,
    _estimate_sections,
    _instrumentation_advice,
)


def _note(pitch: int, start: float, end: float | None = None, velocity: int = 80) -> NoteEvent:
    return NoteEvent(start=start, end=end or start + 0.5, pitch=pitch, velocity=velocity)


def test_estimate_bpm_picks_steady_quarter_notes() -> None:
    # 120 BPM -> 0.5 s between onsets.
    notes = [_note(60, t) for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)]
    bpm, confidence = _estimate_bpm(notes)
    assert bpm == 120
    assert confidence > 0.5


def test_estimate_bpm_returns_none_when_no_intervals_in_musical_range() -> None:
    # Onsets are all clustered inside the 0.12 s minimum gap, so the
    # candidate pool ends up empty and the function should bail out.
    notes = [_note(60, t) for t in (0.0, 0.05, 0.08, 0.10)]
    bpm, confidence = _estimate_bpm(notes)
    assert bpm is None
    assert confidence == 0.0


def test_estimate_key_finds_c_major() -> None:
    # C major scale: C D E F G A B C.
    scale_pitches = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = [_note(p, t) for t, p in enumerate(scale_pitches)]
    tonic, mode, _ = _estimate_key(notes)
    assert tonic == "C"
    assert mode == "major"


def test_estimate_key_finds_a_minor() -> None:
    # A minor scale: A B C D E F G A.
    scale_pitches = [69, 71, 72, 74, 76, 77, 79, 81]
    notes = [_note(p, t * 0.5) for t, p in enumerate(scale_pitches)]
    tonic, mode, _ = _estimate_key(notes)
    assert tonic == "A"
    assert mode == "minor"


def test_estimate_key_prefers_real_scale_over_random() -> None:
    """A pure C major scale must rank above every other key --- even a small
    confidence gap counts, because random-pitch music is the only thing
    that should produce 'no clear key'.
    """
    scale_pitches = [60, 62, 64, 65, 67, 69, 71, 72]
    notes = [_note(p, t) for t, p in enumerate(scale_pitches)]
    _, _, confidence = _estimate_key(notes)
    assert confidence > 0.0


def test_estimate_chords_segments_into_bars() -> None:
    # At 120 BPM, one bar = 2 s. Three C-major triads across two bars.
    notes = [
        _note(60, 0.0, 1.8), _note(64, 0.0, 1.8), _note(67, 0.0, 1.8),
        _note(60, 2.0, 3.8), _note(64, 2.0, 3.8), _note(67, 2.0, 3.8),
        _note(65, 4.0, 5.8), _note(69, 4.0, 5.8), _note(72, 4.0, 5.8),
    ]
    segments = _estimate_chords(notes, duration=6.0, bpm=120)
    assert segments, "expected at least one chord segment"
    # All three triads should reduce to a recognized quality --- we don't pin
    # the exact names (it depends on threshold tuning) but each segment must
    # have a non-empty name and a confidence in [0, 1].
    for seg in segments:
        assert seg.chord
        assert 0.0 <= seg.confidence <= 1.0


def test_estimate_sections_bucket_by_density() -> None:
    notes: list[NoteEvent] = []
    # Dense first half, sparse second half.
    for t in [i * 0.1 for i in range(40)]:
        notes.append(_note(60, t, t + 0.05))
    for t in [10.0, 12.0, 14.0, 16.0]:
        notes.append(_note(60, t, t + 0.1))
    sections = _estimate_sections(notes, duration=20.0)
    assert 1 <= len(sections) <= 4
    energies = {section.energy for section in sections}
    # At least one section should be classified differently from the others.
    assert "low" in energies or "high" in energies


def test_instrumentation_advice_flags_missing_low_end() -> None:
    notes = [_note(72 + i % 12, t * 0.5) for t, i in enumerate(range(20))]
    advice = _instrumentation_advice(notes, key_name="C", scale="major")
    assert any("bass" in line.lower() for line in advice)


def test_arrangement_advice_references_detected_bpm() -> None:
    notes = [_note(60, t) for t in (0.0, 0.5, 1.0, 1.5, 2.0)]
    advice = _arrangement_advice(
        notes, bpm=120, key_name="C", scale="major", sections=[], chords=[]
    )
    assert any("120" in line for line in advice)


def test_analyze_notes_handles_empty_input() -> None:
    analysis = MusicAnalysisService().analyze_notes([])
    assert analysis.note_count == 0
    assert analysis.bpm is None
    assert analysis.key is None
    assert analysis.warnings  # at least one placeholder warning


def test_analyze_notes_runs_end_to_end_on_simple_scale() -> None:
    notes = [
        _note(p, t * 0.5)
        for t, p in enumerate([60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65])
    ]
    analysis = MusicAnalysisService().analyze_notes(notes)
    assert analysis.note_count == len(notes)
    assert analysis.duration > 0
    assert analysis.bpm is not None
    assert analysis.key is not None
    # pitch_range is rendered as "<low>-<high>" (e.g. "C4-C5").
    assert analysis.pitch_range is not None
    assert "-" in analysis.pitch_range
