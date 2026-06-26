"""Drum-stem onset detection and GM drum MIDI export.

This service turns a separated ``drums.wav`` stem into a combined GM drum MIDI
plus per-part MIDI files for kick, snare, hat, tom, cymbal and fill. It is a
pragmatic signal-analysis pass, not a trained drum transcription model: the
goal is to make drum material editable and mappable while keeping the pipeline
fully local.
"""
from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DRUM_PARTS: tuple[str, ...] = ("kick", "snare", "hat", "tom", "cymbal", "fill")

_GM_DRUM_NOTES: dict[str, int] = {
    "kick": 36,    # Acoustic Bass Drum
    "snare": 38,   # Acoustic Snare
    "hat": 42,     # Closed Hi-Hat
    "tom": 45,     # Low Tom
    "cymbal": 49,  # Crash Cymbal 1
    "fill": 47,    # Low-Mid Tom
}

_NOTE_LENGTHS_SECONDS: dict[str, float] = {
    "kick": 0.10,
    "snare": 0.09,
    "hat": 0.045,
    "tom": 0.12,
    "cymbal": 0.35,
    "fill": 0.11,
}


@dataclass(frozen=True)
class DrumHit:
    time_s: float
    part: str
    midi_note: int
    velocity: int
    confidence: float


@dataclass(frozen=True)
class DrumMidiResult:
    combined_path: Path
    part_paths: dict[str, Path]
    events_csv_path: Path
    event_count: int
    bpm: float


class DrumMidiService:
    """Create editable GM drum MIDI from a separated drum stem."""

    def __init__(self, *, sample_rate: int = 22050, default_bpm: float = 120.0) -> None:
        self.sample_rate = sample_rate
        self.default_bpm = default_bpm

    def create_drum_midi(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        stem_name: str = "drums",
    ) -> DrumMidiResult:
        """Write ``drums.mid`` and ``drums_<part>.mid`` files under output_dir."""
        output_dir.mkdir(parents=True, exist_ok=True)
        y, sr = self._load_audio(audio_path)
        bpm = self._estimate_bpm(y, sr)
        hits = self._detect_hits(y, sr)

        combined_path = output_dir / f"{stem_name}.mid"
        part_paths = {part: output_dir / f"{stem_name}_{part}.mid" for part in DRUM_PARTS}
        events_csv_path = output_dir / f"{stem_name}_events.csv"

        self._write_midi(combined_path, hits, bpm=bpm, track_name=f"{stem_name}: GM drum kit")
        for part, part_path in part_paths.items():
            if part == "fill":
                part_hits = self._fill_hits(hits)
            else:
                part_hits = [hit for hit in hits if hit.part == part]
            self._write_midi(part_path, part_hits, bpm=bpm, track_name=f"{stem_name}: {part}")
        self._write_events_csv(events_csv_path, hits)

        logger.info(
            "drum-midi: wrote %s plus %d part MIDI file(s), hits=%d bpm=%.1f",
            combined_path.name,
            len(part_paths),
            len(hits),
            bpm,
        )
        return DrumMidiResult(
            combined_path=combined_path,
            part_paths=part_paths,
            events_csv_path=events_csv_path,
            event_count=len(hits),
            bpm=bpm,
        )

    def _load_audio(self, audio_path: Path):
        import librosa

        if not audio_path.is_file():
            raise FileNotFoundError(f"drum audio file not found: {audio_path}")
        y, sr = librosa.load(str(audio_path), sr=self.sample_rate, mono=True)
        return y, sr

    def _estimate_bpm(self, y, sr: int) -> float:
        import librosa
        import numpy as np

        if y.size == 0 or float(np.max(np.abs(y))) < 1e-5:
            return self.default_bpm
        try:
            tempo, _beats = librosa.beat.beat_track(y=y, sr=sr)
            bpm = float(np.asarray(tempo).reshape(-1)[0])
        except Exception as exc:  # noqa: BLE001 - fallback is good enough here
            logger.debug("drum-midi: tempo estimate failed: %s", exc)
            return self.default_bpm
        if not math.isfinite(bpm) or bpm < 40.0 or bpm > 240.0:
            return self.default_bpm
        return bpm

    def _detect_hits(self, y, sr: int) -> list[DrumHit]:
        import librosa
        import numpy as np

        if y.size == 0 or float(np.max(np.abs(y))) < 1e-5:
            return []

        onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=np.median)
        frames = librosa.onset.onset_detect(
            y=y,
            sr=sr,
            onset_envelope=onset_env,
            units="frames",
            backtrack=False,
            pre_max=3,
            post_max=3,
            pre_avg=3,
            post_avg=5,
            delta=0.16,
            wait=1,
        )
        times = list(librosa.frames_to_time(frames, sr=sr))

        # Onset detectors commonly miss a transient that starts at t=0.
        early = y[: int(0.06 * sr)]
        if early.size and float(np.max(np.abs(early))) > 0.05:
            times.insert(0, 0.0)

        times = _dedupe_times(times, min_gap_s=0.045)
        raw_hits: list[DrumHit] = []
        strengths: list[float] = []
        for time_s in times:
            part, confidence, strength = self._classify_hit(y, sr, float(time_s))
            strengths.append(strength)
            raw_hits.append(
                DrumHit(
                    time_s=float(time_s),
                    part=part,
                    midi_note=_GM_DRUM_NOTES[part],
                    velocity=64,
                    confidence=confidence,
                )
            )

        if not raw_hits:
            return []

        max_strength = max(strengths) or 1.0
        hits = [
            DrumHit(
                time_s=hit.time_s,
                part=hit.part,
                midi_note=hit.midi_note,
                velocity=_velocity_from_strength(strength / max_strength),
                confidence=hit.confidence,
            )
            for hit, strength in zip(raw_hits, strengths, strict=True)
        ]
        return hits

    def _classify_hit(self, y, sr: int, time_s: float) -> tuple[str, float, float]:
        import numpy as np

        start = max(0, int(time_s * sr))
        end = min(len(y), start + int(0.18 * sr))
        window = y[start:end]
        if window.size < 16:
            return "snare", 0.2, 0.0

        envelope = np.hanning(window.size)
        shaped = window * envelope
        spectrum = np.abs(np.fft.rfft(shaped))
        freqs = np.fft.rfftfreq(shaped.size, d=1.0 / sr)
        total = float(np.sum(spectrum)) + 1e-9

        low_ratio = float(np.sum(spectrum[freqs < 180.0]) / total)
        low_mid_ratio = float(np.sum(spectrum[(freqs >= 180.0) & (freqs < 900.0)]) / total)
        mid_ratio = float(np.sum(spectrum[(freqs >= 900.0) & (freqs < 3500.0)]) / total)
        high_ratio = float(np.sum(spectrum[freqs >= 3500.0]) / total)
        centroid = float(np.sum(freqs * spectrum) / total)
        peak_freq = float(freqs[int(np.argmax(spectrum))]) if spectrum.size else 0.0
        strength = float(np.sqrt(np.mean(np.square(window)))) if window.size else 0.0

        early_energy = float(np.sqrt(np.mean(np.square(window[: max(1, int(0.05 * sr))]))))
        late_start = min(window.size, int(0.10 * sr))
        late = window[late_start:]
        late_energy = float(np.sqrt(np.mean(np.square(late)))) if late.size else 0.0
        sustain_ratio = late_energy / (early_energy + 1e-9)

        if low_ratio > 0.32 and centroid < 1600.0 and peak_freq < 220.0:
            return "kick", min(0.98, 0.55 + low_ratio), strength
        if high_ratio > 0.60 and centroid > 5200.0 and sustain_ratio > 0.30:
            return "cymbal", min(0.98, 0.50 + high_ratio), strength
        if high_ratio > 0.52 and centroid > 4700.0:
            if strength > 0.08 and (low_mid_ratio + mid_ratio) > 0.16 and sustain_ratio < 0.22:
                return "snare", min(0.92, 0.46 + mid_ratio + (high_ratio * 0.35)), strength
            return "hat", min(0.95, 0.46 + high_ratio), strength
        if low_mid_ratio > 0.35 and peak_freq < 950.0 and high_ratio < 0.42:
            return "tom", min(0.92, 0.45 + low_mid_ratio), strength
        if mid_ratio + high_ratio > 0.55:
            return "snare", min(0.92, 0.42 + mid_ratio + (high_ratio * 0.4)), strength
        return ("tom" if centroid < 2200.0 else "snare"), 0.45, strength

    @staticmethod
    def _fill_hits(hits: list[DrumHit]) -> list[DrumHit]:
        fill_hits: list[DrumHit] = []
        for index, hit in enumerate(hits):
            left = max(0, index - 4)
            right = min(len(hits), index + 5)
            nearby = [other for other in hits[left:right] if abs(other.time_s - hit.time_s) <= 0.45]
            prev_gap = hit.time_s - hits[index - 1].time_s if index > 0 else 99.0
            next_gap = hits[index + 1].time_s - hit.time_s if index + 1 < len(hits) else 99.0
            dense_run = len(nearby) >= 3 or prev_gap < 0.22 or next_gap < 0.22
            if dense_run and hit.part in {"kick", "snare", "tom"}:
                fill_hits.append(
                    DrumHit(
                        time_s=hit.time_s,
                        part="fill",
                        midi_note=_GM_DRUM_NOTES["fill"],
                        velocity=hit.velocity,
                        confidence=min(0.98, hit.confidence + 0.08),
                    )
                )
        return fill_hits


    @staticmethod
    def _write_midi(path: Path, hits: list[DrumHit], *, bpm: float, track_name: str) -> None:
        from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo, second2tick

        ticks_per_beat = 480
        tempo = bpm2tempo(bpm)
        midi = MidiFile(type=1, ticks_per_beat=ticks_per_beat)

        meta_track = MidiTrack()
        meta_track.append(MetaMessage("track_name", name="tempo", time=0))
        meta_track.append(MetaMessage("set_tempo", tempo=tempo, time=0))
        meta_track.append(MetaMessage("end_of_track", time=0))
        midi.tracks.append(meta_track)

        drum_track = MidiTrack()
        drum_track.append(MetaMessage("track_name", name=track_name, time=0))
        drum_track.append(Message("program_change", channel=9, program=0, time=0))

        events = []
        for hit in hits:
            start_tick = int(round(second2tick(hit.time_s, ticks_per_beat, tempo)))
            duration_s = _NOTE_LENGTHS_SECONDS.get(hit.part, 0.09)
            end_tick = start_tick + max(1, int(round(second2tick(duration_s, ticks_per_beat, tempo))))
            events.append(
                (
                    start_tick,
                    1,
                    Message(
                        "note_on",
                        channel=9,
                        note=hit.midi_note,
                        velocity=hit.velocity,
                        time=0,
                    ),
                )
            )
            events.append(
                (
                    end_tick,
                    0,
                    Message("note_off", channel=9, note=hit.midi_note, velocity=0, time=0),
                )
            )

        last_tick = 0
        for tick, _order, message in sorted(events, key=lambda item: (item[0], item[1])):
            message.time = max(0, tick - last_tick)
            drum_track.append(message)
            last_tick = tick

        drum_track.append(MetaMessage("end_of_track", time=0))
        midi.tracks.append(drum_track)
        path.parent.mkdir(parents=True, exist_ok=True)
        midi.save(str(path))

    @staticmethod
    def _write_events_csv(path: Path, hits: list[DrumHit]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s", "part", "midi_note", "velocity", "confidence"])
            for hit in hits:
                writer.writerow(
                    [
                        f"{hit.time_s:.6f}",
                        hit.part,
                        hit.midi_note,
                        hit.velocity,
                        f"{hit.confidence:.3f}",
                    ]
                )


def _dedupe_times(times: list[float], *, min_gap_s: float) -> list[float]:
    deduped: list[float] = []
    for time_s in sorted(float(t) for t in times):
        if not deduped or time_s - deduped[-1] >= min_gap_s:
            deduped.append(time_s)
    return deduped


def _velocity_from_strength(normalized: float) -> int:
    normalized = max(0.0, min(1.0, normalized))
    return max(35, min(127, int(round(40 + 87 * math.sqrt(normalized)))))

