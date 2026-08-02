"""Rule-based music analysis for generated MIDI note CSV files.

The service is intentionally deterministic and dependency-light. It reads the
`*_notes.csv` files emitted by Basic Pitch and produces a structured analysis
JSON with tempo, key, chord snapshots, coarse sections, instrumentation advice,
and arrangement suggestions. A future LLM pass can use this JSON as context,
but the product already has useful output without an external model call.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

ANALYSIS_FILENAME = "analysis.json"

_MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
_PITCH_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_MAJOR_SCALE = {0, 2, 4, 5, 7, 9, 11}
_MINOR_SCALE = {0, 2, 3, 5, 7, 8, 10}
_CHORD_QUALITIES: tuple[tuple[str, set[int]], ...] = (
    ("maj", {0, 4, 7}),
    ("min", {0, 3, 7}),
    ("dim", {0, 3, 6}),
    ("sus4", {0, 5, 7}),
    ("sus2", {0, 2, 7}),
    ("5", {0, 7}),
)


@dataclass(frozen=True)
class NoteEvent:
    start: float
    end: float
    pitch: int
    velocity: int

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class ChordSegment:
    start: float
    end: float
    chord: str
    confidence: float


@dataclass(frozen=True)
class MusicSection:
    label: str
    start: float
    end: float
    energy: str
    density: float
    suggestion: str


@dataclass(frozen=True)
class MusicAnalysis:
    bpm: int | None
    bpm_confidence: float
    key: str | None
    key_confidence: float
    scale: str | None
    note_count: int
    duration: float
    pitch_range: str | None
    chords: list[ChordSegment]
    sections: list[MusicSection]
    instrumentation: list[str]
    arrangement: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["chords"] = [asdict(chord) for chord in self.chords]
        data["sections"] = [asdict(section) for section in self.sections]
        return data


class MusicAnalysisService:
    """Analyze Basic Pitch note CSV output and persist an analysis JSON."""

    def analyze_output_dir(self, output_dir: Path) -> MusicAnalysis:
        notes = _load_notes(output_dir)
        return self.analyze_notes(notes)

    def analyze_and_write(self, output_dir: Path) -> Path:
        analysis = self.analyze_output_dir(output_dir)
        path = analysis_path(output_dir)
        with path.open("w", encoding="utf-8") as f:
            json.dump(analysis.to_dict(), f, ensure_ascii=False, indent=2)
        return path

    def analyze_notes(self, notes: list[NoteEvent]) -> MusicAnalysis:
        warnings: list[str] = []
        if not notes:
            warnings.append(_i18n("warn_no_notes"))
            return MusicAnalysis(
                bpm=None,
                bpm_confidence=0.0,
                key=None,
                key_confidence=0.0,
                scale=None,
                note_count=0,
                duration=0.0,
                pitch_range=None,
                chords=[],
                sections=[],
                instrumentation=[_i18n("no_notes_instrumentation")],
                arrangement=[_i18n("no_notes_arrangement")],
                warnings=warnings,
            )

        notes = sorted(notes, key=lambda n: (n.start, n.pitch))
        duration = max(note.end for note in notes)
        bpm, bpm_confidence = _estimate_bpm(notes)
        key_name, scale, key_confidence = _estimate_key(notes)
        chords = _estimate_chords(notes, duration, bpm)
        sections = _estimate_sections(notes, duration)
        pitch_range = (
            f"{_pitch_name(min(n.pitch for n in notes))}-{_pitch_name(max(n.pitch for n in notes))}"
        )

        if len(notes) < 8:
            warnings.append(_i18n("warn_few_notes"))
        if key_confidence < 0.18:
            warnings.append(_i18n("warn_low_key_confidence"))
        if bpm is None:
            warnings.append(_i18n("warn_no_bpm"))

        instrumentation = _instrumentation_advice(notes, key_name, scale)
        arrangement = _arrangement_advice(notes, bpm, key_name, scale, sections, chords)

        return MusicAnalysis(
            bpm=bpm,
            bpm_confidence=round(bpm_confidence, 3),
            key=key_name,
            key_confidence=round(key_confidence, 3),
            scale=scale,
            note_count=len(notes),
            duration=round(duration, 3),
            pitch_range=pitch_range,
            chords=chords,
            sections=sections,
            instrumentation=instrumentation,
            arrangement=arrangement,
            warnings=warnings,
        )


def analysis_path(output_dir: Path) -> Path:
    return output_dir / ANALYSIS_FILENAME


def read_analysis(output_dir: Path) -> dict | None:
    path = analysis_path(output_dir)
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _load_notes(output_dir: Path) -> list[NoteEvent]:
    notes: list[NoteEvent] = []
    for csv_path in sorted(output_dir.glob("*_notes.csv")):
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    start = float(row["start_time_s"])
                    end = float(row["end_time_s"])
                    pitch = int(row["pitch_midi"])
                    velocity = int(row.get("velocity") or 80)
                except (KeyError, TypeError, ValueError):
                    continue
                if end <= start or pitch < 0 or pitch > 127:
                    continue
                notes.append(NoteEvent(start=start, end=end, pitch=pitch, velocity=velocity))
    return notes


def _estimate_bpm(notes: list[NoteEvent]) -> tuple[int | None, float]:
    onsets = sorted({round(note.start, 3) for note in notes})
    intervals = [b - a for a, b in itertools.pairwise(onsets) if 0.12 <= b - a <= 2.0]
    if not intervals:
        return None, 0.0

    candidates: Counter[int] = Counter()
    for interval in intervals:
        bpm = 60.0 / interval
        while bpm < 70:
            bpm *= 2
        while bpm > 190:
            bpm /= 2
        candidates[int(round(bpm / 2) * 2)] += 1

    if not candidates:
        return None, 0.0
    bpm, count = candidates.most_common(1)[0]
    return bpm, min(1.0, count / max(1, len(intervals)))


def _estimate_key(notes: list[NoteEvent]) -> tuple[str | None, str | None, float]:
    histogram = [0.0] * 12
    for note in notes:
        weight = max(0.05, note.duration) * max(1, note.velocity) / 100.0
        histogram[note.pitch % 12] += weight

    total = sum(histogram)
    if total <= 0:
        return None, None, 0.0
    histogram = [value / total for value in histogram]

    scored: list[tuple[float, int, str]] = []
    for tonic in range(12):
        major_score = _profile_score(histogram, _rotate(_MAJOR_PROFILE, tonic))
        minor_score = _profile_score(histogram, _rotate(_MINOR_PROFILE, tonic))
        scored.append((major_score, tonic, "major"))
        scored.append((minor_score, tonic, "minor"))

    scored.sort(reverse=True)
    best, tonic, mode = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    confidence = max(0.0, min(1.0, best - second))
    return _PITCH_NAMES[tonic], mode, confidence


def _estimate_chords(
    notes: list[NoteEvent], duration: float, bpm: int | None
) -> list[ChordSegment]:
    if duration <= 0:
        return []
    window = 60.0 / bpm * 4.0 if bpm else max(2.0, duration / 8.0)
    window = max(1.0, min(4.0, window))
    segments: list[ChordSegment] = []

    cursor = 0.0
    while cursor < duration:
        end = min(duration, cursor + window)
        active = [note for note in notes if note.start < end and note.end > cursor]
        chord, confidence = _name_chord(active)
        if chord:
            if segments and segments[-1].chord == chord:
                prev = segments[-1]
                segments[-1] = ChordSegment(
                    prev.start, end, chord, max(prev.confidence, confidence)
                )
            else:
                segments.append(
                    ChordSegment(round(cursor, 2), round(end, 2), chord, round(confidence, 3))
                )
        cursor = end

    return segments[:16]


def _estimate_sections(notes: list[NoteEvent], duration: float) -> list[MusicSection]:
    if duration <= 0:
        return []
    section_count = max(1, min(4, math.ceil(duration / 20.0)))
    section_len = duration / section_count
    densities: list[float] = []
    buckets: list[tuple[float, float, list[NoteEvent]]] = []

    for idx in range(section_count):
        start = idx * section_len
        end = duration if idx == section_count - 1 else (idx + 1) * section_len
        active = [note for note in notes if note.start < end and note.end > start]
        density = len(active) / max(1.0, end - start)
        densities.append(density)
        buckets.append((start, end, active))

    max_density = max(densities) if densities else 0.0
    sections: list[MusicSection] = []
    for idx, (start, end, active) in enumerate(buckets):
        density = densities[idx]
        ratio = density / max_density if max_density else 0.0
        energy = "high" if ratio >= 0.72 else "medium" if ratio >= 0.38 else "low"
        label = chr(ord("A") + idx)
        suggestion = _section_suggestion(idx, energy, len(active))
        sections.append(
            MusicSection(
                label=label,
                start=round(start, 2),
                end=round(end, 2),
                energy=energy,
                density=round(density, 2),
                suggestion=suggestion,
            )
        )
    return sections


def _name_chord(notes: list[NoteEvent]) -> tuple[str | None, float]:
    if not notes:
        return None, 0.0
    pitch_classes = {note.pitch % 12 for note in notes}
    if len(pitch_classes) < 2:
        return None, 0.0

    best: tuple[float, str] | None = None
    for root in range(12):
        normalized = {(pc - root) % 12 for pc in pitch_classes}
        for quality, chord_set in _CHORD_QUALITIES:
            matched = len(normalized & chord_set)
            extra = len(normalized - chord_set)
            score = matched / len(chord_set) - extra * 0.12
            name = _PITCH_NAMES[root] if quality == "maj" else f"{_PITCH_NAMES[root]}{quality}"
            if best is None or score > best[0]:
                best = (score, name)

    if best is None or best[0] < 0.45:
        return None, 0.0
    return best[1], max(0.0, min(1.0, best[0]))


def _i18n(key: str, *params: str) -> str:
    """Build an i18n-friendly advice string: $key||param1||param2||..."""
    if params:
        return "$" + key + "||" + "||".join(params)
    return "$" + key


def _instrumentation_advice(
    notes: list[NoteEvent], key_name: str | None, scale: str | None
) -> list[str]:
    low = sum(1 for note in notes if note.pitch < 48)
    mid = sum(1 for note in notes if 48 <= note.pitch <= 72)
    high = sum(1 for note in notes if note.pitch > 72)
    total = max(1, len(notes))
    advice = []

    if low / total < 0.18:
        advice.append(_i18n("bass_add"))
    else:
        advice.append(_i18n("bass_focus"))

    if mid / total >= 0.45:
        advice.append(_i18n("midrange_use"))
    else:
        advice.append(_i18n("midrange_add"))

    if high / total >= 0.22:
        advice.append(_i18n("high_use"))
    else:
        advice.append(_i18n("high_add"))

    if key_name and scale:
        advice.append(_i18n("key_center", key_name, scale))
    return advice


def _arrangement_advice(
    notes: list[NoteEvent],
    bpm: int | None,
    key_name: str | None,
    scale: str | None,
    sections: list[MusicSection],
    chords: list[ChordSegment],
) -> list[str]:
    advice = []
    if bpm:
        groove = "half-time" if bpm >= 145 else "steady 4/4" if bpm >= 95 else "laid-back"
        advice.append(_i18n("drum_groove", groove, str(bpm)))
    else:
        advice.append(_i18n("click_manual"))

    if chords:
        progression = " - ".join(chord.chord for chord in chords[:4])
        advice.append(_i18n("chord_motif", progression))
    elif key_name and scale:
        advice.append(_i18n("build_progression", key_name, scale))

    if sections:
        low_sections = [section.label for section in sections if section.energy == "low"]
        high_sections = [section.label for section in sections if section.energy == "high"]
        if low_sections:
            advice.append(_i18n("sparse_intro", low_sections[0]))
        if high_sections:
            advice.append(_i18n("hook_chorus", high_sections[0]))

    avg_velocity = sum(note.velocity for note in notes) / max(1, len(notes))
    if avg_velocity < 55:
        advice.append(_i18n("velocity_soft"))
    elif avg_velocity > 100:
        advice.append(_i18n("velocity_hard"))
    else:
        advice.append(_i18n("velocity_ok"))
    return advice


def _section_suggestion(index: int, energy: str, note_count: int) -> str:
    if index == 0 and energy == "low":
        return _i18n("section_intro")
    if energy == "high":
        return _i18n("section_hook")
    if note_count < 4:
        return _i18n("section_sparse")
    return _i18n("section_verse")


def _profile_score(histogram: list[float], profile: list[float]) -> float:
    profile_total = sum(profile)
    normalized = [value / profile_total for value in profile]
    return sum(a * b for a, b in zip(histogram, normalized, strict=False))


def _rotate(values: list[float], shift: int) -> list[float]:
    return values[-shift:] + values[:-shift] if shift else values[:]


def _pitch_name(midi_pitch: int) -> str:
    octave = midi_pitch // 12 - 1
    return f"{_PITCH_NAMES[midi_pitch % 12]}{octave}"
