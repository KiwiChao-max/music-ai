"""Drum-stem onset detection and GM drum MIDI export.

This service turns a separated ``drums.wav`` stem into a combined GM drum MIDI
plus per-part MIDI files for the full General MIDI percussion map (notes
35-81). It is a pragmatic signal-analysis pass, not a trained drum
transcription model: the goal is to make drum material editable and mappable
while keeping the pipeline fully local.

The output covers:

  * Kick (35 / 36)
  * Snare + hand clap (37 / 38 / 39 / 40)
  * Hi-hat (open, closed, pedal --- 42 / 44 / 46)
  * Toms (5 pieces: floor, low, low-mid, hi-mid, high --- 41 / 43 / 45 / 47 / 48 / 50)
  * Cymbals (crash 1+2, ride 1+2, china, splash, ride bell --- 49 / 51-55 / 57 / 59)
  * Small percussion (cowbell, tambourine, latin percussion --- 54 / 56 / 60-81)
  * Fills (a separate ``fill`` track capturing dense bursts so drummers can
    edit them independently)

The MIDI writer also outputs the standard GM controllers so the file is
playable in any GM-aware DAW without manual re-mixing:

  * CC7  (channel volume)   --- 100
  * CC10 (pan)              --- 64
  * CC11 (expression)       --- 127
  * CC64 (sustain pedal)    --- only for melodic tracks; drums ignore
"""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass
from pathlib import Path

from .midi_cc import velocity_from_strength

logger = logging.getLogger(__name__)

# Minimum confidence below which the classifier falls back to a
# "nearest main part" rather than blindly returning a low-confidence
# tom_lomid or snare. This is the gate that prevents the final-resort
# line from dumping ambiguous hits into the most common bucket.
_CLASSIFIER_CONFIDENCE_FLOOR = 0.55

# Per-part "nearest main part" fallback mapping. When the classifier
# returns a part below the confidence floor, we remap to the closest
# primary part by spectral region. This is intentionally conservative:
# the ambiguous hit is still placed somewhere useful (kick / snare /
# tom / cymbal / hat) but never silently dropped.
_CONFIDENCE_FALLBACK: dict[str, tuple[str, str]] = {
    # Pitch-bucketed toms collapse to the nearest main tom.
    "tom_high": ("tom_high", "tom_himid"),
    "tom_himid": ("tom_himid", "tom_lomid"),
    "tom_lomid": ("tom_lomid", "tom_himid"),
    "tom_low": ("tom_low", "tom_lomid"),
    "tom_floor": ("tom_floor", "tom_low"),
    # Cymbal family -> primary member.
    "china": ("crash", "china"),
    "splash": ("crash", "splash"),
    "ride_bell": ("ride", "ride_bell"),
    # Rare percussion -> closest common part.
    "sidestick": ("snare", "sidestick"),
    "cowbell": ("snare", "cowbell"),
    "tambourine": ("hihat_closed", "tambourine"),
    "percussion": ("hihat_closed", "percussion"),
}

# Ordered public list of part buckets. Consumers (frontend, mapping service)
# rely on this ordering to render splits in a stable, human-friendly way.
DRUM_PARTS: tuple[str, ...] = (
    "kick",
    "snare",
    "sidestick",
    "hihat_closed",
    "hihat_open",
    "tom_high",
    "tom_himid",
    "tom_lomid",
    "tom_low",
    "tom_floor",
    "crash",
    "ride",
    "china",
    "splash",
    "ride_bell",
    "tambourine",
    "cowbell",
    "percussion",
    "fill",
)

# ---------------------------------------------------------------------------
# Full General MIDI percussion map (notes 35..81) for parts we care about.
# Each part maps to ONE primary note (used for hit events). The detection
# classifier chooses the part, then we emit the canonical GM note.
# ---------------------------------------------------------------------------
_GM_DRUM_NOTES: dict[str, int] = {
    # Tonal kick
    "kick": 36,  # Bass Drum 1
    # Snare family
    "snare": 38,  # Acoustic Snare
    "sidestick": 37,  # Side Stick
    # Hi-hats
    "hihat_closed": 42,  # Closed Hi-Hat
    "hihat_open": 46,  # Open Hi-Hat
    # Toms (high to low)
    "tom_high": 50,  # High Tom
    "tom_himid": 48,  # Hi-Mid Tom
    "tom_lomid": 47,  # Low-Mid Tom
    "tom_low": 45,  # Low Tom
    "tom_floor": 41,  # Low Floor Tom
    # Cymbals
    "crash": 49,  # Crash Cymbal 1
    "ride": 51,  # Ride Cymbal 1
    "china": 52,  # Chinese Cymbal
    "splash": 55,  # Splash Cymbal
    "ride_bell": 53,  # Ride Bell
    # Hand / small percussion
    "tambourine": 54,
    "cowbell": 56,
    "percussion": 60,  # High Bongo --- placeholder; the writer also accepts
    # an explicit note for fine-grained hits.
    # "fill" is a heuristic overlay; it remaps to a low-mid tom so the part
    # MIDI is still playable on a default GM kit.
    "fill": 47,
}

# Per-part note lengths in seconds. Short hats get tiny tails, cymbals ring.
_NOTE_LENGTHS_SECONDS: dict[str, float] = {
    "kick": 0.10,
    "snare": 0.09,
    "sidestick": 0.05,
    "hihat_closed": 0.045,
    "hihat_open": 0.30,
    "tom_high": 0.10,
    "tom_himid": 0.11,
    "tom_lomid": 0.12,
    "tom_low": 0.13,
    "tom_floor": 0.14,
    "crash": 0.40,
    "ride": 0.30,
    "china": 0.45,
    "splash": 0.35,
    "ride_bell": 0.25,
    "tambourine": 0.10,
    "cowbell": 0.08,
    "percussion": 0.05,
    "fill": 0.10,
}


@dataclass(frozen=True)
class DrumHit:
    time_s: float
    part: str
    midi_note: int
    velocity: int
    confidence: float
    spectral_centroid: float = 0.0
    spectral_flux: float = 0.0


@dataclass(frozen=True)
class DrumMidiResult:
    combined_path: Path
    part_paths: dict[str, Path]
    events_csv_path: Path
    event_count: int
    bpm: float
    part_counts: dict[str, int]


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
        raw_hits = self._detect_hits(y, sr, bpm=bpm)
        hits = self._derive_fills(raw_hits)

        combined_path = output_dir / f"{stem_name}.mid"
        part_paths = {part: output_dir / f"{stem_name}_{part}.mid" for part in DRUM_PARTS}
        events_csv_path = output_dir / f"{stem_name}_events.csv"
        events_json_path = output_dir / f"{stem_name}_events.json"

        self._write_midi(combined_path, hits, bpm=bpm, track_name=f"{stem_name}: GM drum kit")

        # Per-part files. Every part file contains that part's hits only,
        # so a drummer can drop just the kick track into a DAW.
        part_counts: dict[str, int] = {part: 0 for part in DRUM_PARTS}
        for part, part_path in part_paths.items():
            if part == "fill":
                part_hits = [hit for hit in hits if hit.part == "fill"]
            else:
                part_hits = [hit for hit in hits if hit.part == part]
            part_counts[part] = len(part_hits)
            self._write_midi(
                part_path,
                part_hits,
                bpm=bpm,
                track_name=f"{stem_name}: {part}",
            )

        self._write_events_csv(events_csv_path, hits)
        self._write_events_json(events_json_path, hits, bpm=bpm)

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
            part_counts=part_counts,
        )

    # ---- audio load / BPM ---------------------------------------------------
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
        except Exception as exc:
            logger.debug("drum-midi: tempo estimate failed: %s", exc)
            return self.default_bpm
        if not math.isfinite(bpm) or bpm < 40.0 or bpm > 240.0:
            return self.default_bpm
        return bpm

    # ---- onset + per-hit feature extraction ---------------------------------
    def _detect_hits(self, y, sr: int, *, bpm: float | None = None) -> list[DrumHit]:
        import librosa
        import numpy as np

        if y.size == 0 or float(np.max(np.abs(y))) < 1e-5:
            return []

        # ── BPM-adaptive onset parameters ────────────────────────────────
        # Different tempo ranges need different sensitivity. At 60 BPM
        # (slow ballads) the space between hits is ~1s, so a high delta
        # avoids false positives from reverb tails. At 180 BPM (blast
        # beats / dense fills), the inter-hit gap is ~0.33s, so we
        # lower delta and skip wait to catch every transient.
        if bpm is None:
            bpm = self._estimate_bpm(y, sr)

        if bpm < 80.0:
            # Slow / ballad: sparse hits, high threshold to avoid reverb
            # tails being picked up as separate hits.
            delta = 0.22
            wait = 2
            pre_max = 5
            pre_avg = 4
            post_avg = 7
        elif bpm < 130.0:
            # Mid-tempo (most pop/rock): the default set.
            delta = 0.16
            wait = 1
            pre_max = 3
            pre_avg = 3
            post_avg = 5
        elif bpm < 170.0:
            # Fast tempo: lower threshold so we don't miss quick hits.
            delta = 0.12
            wait = 0
            pre_max = 2
            pre_avg = 2
            post_avg = 4
        else:
            # Very fast / extreme: aggressive sensitivity. The risk of
            # false positives is higher, but missing a blast-beat snare
            # is worse than flagging a ghost note.
            delta = 0.08
            wait = 0
            pre_max = 2
            pre_avg = 2
            post_avg = 3

        # `aggregate` is invoked by librosa as `aggregate(data_slice, axis=-1)`.
        # We use the per-frame mean --- the previous `np.median` over the whole
        # matrix broadcast a single scalar to every frame and silently
        # disabled onset detection.
        def _mean_over_freq(matrix: np.ndarray, axis: int = -1) -> np.ndarray:
            return np.mean(matrix, axis=axis)

        onset_env = librosa.onset.onset_strength(y=y, sr=sr, aggregate=_mean_over_freq)
        frames = librosa.onset.onset_detect(
            y=y,
            sr=sr,
            onset_envelope=onset_env,
            units="frames",
            backtrack=False,
            pre_max=pre_max,
            post_max=pre_max,
            pre_avg=pre_avg,
            post_avg=post_avg,
            delta=delta,
            wait=wait,
        )
        times = list(librosa.frames_to_time(frames, sr=sr))

        # Onset detectors commonly miss a transient that starts at t=0.
        early = y[: int(0.06 * sr)]
        if early.size and float(np.max(np.abs(early))) > 0.05:
            times.insert(0, 0.0)

        times = _dedupe_times(times, min_gap_s=0.045)
        if not times:
            return []

        # Pre-compute per-onset features so the classifier is one O(1) lookup
        # per hit rather than redoing FFTs.
        features: list[tuple[float, float, float, float, float]] = []
        for time_s in times:
            features.append(self._extract_features(y, sr, float(time_s)))

        # Velocity reference: use the 95th percentile rather than the
        # maximum so the relative dynamic range is preserved.  Per-track
        # normalisation (strength / max_strength) stretches every track
        # to fill 35-127, destroying playing expression and dynamics.
        # With the 95th-percentile reference, the softest ghost notes
        # stay quiet and the loudest accents stay loud --- only the top
        # 5 % of hits are clamped to velocity 127.
        strengths = [feat[4] for feat in features]
        ref_strength = (
            float(np.percentile(strengths, 95)) if len(strengths) > 1 else (max(strengths) or 1.0)
        )

        raw_hits: list[DrumHit] = []
        for time_s, (part, confidence, centroid, flux, strength) in zip(
            times, features, strict=False
        ):
            raw_hits.append(
                DrumHit(
                    time_s=float(time_s),
                    part=part,
                    midi_note=_GM_DRUM_NOTES[part],
                    velocity=velocity_from_strength(min(1.0, strength / ref_strength)),
                    confidence=confidence,
                    spectral_centroid=centroid,
                    spectral_flux=flux,
                )
            )
        return raw_hits

    def _extract_features(
        self,
        y,
        sr: int,
        time_s: float,
    ) -> tuple[str, float, float, float, float]:
        """Return (part, confidence, spectral_centroid, spectral_flux, rms)."""
        import numpy as np

        start = max(0, int(time_s * sr))
        end = min(len(y), start + int(0.18 * sr))
        window = y[start:end]
        if window.size < 16:
            return "snare", 0.2, 0.0, 0.0, 0.0

        shaped = window * np.hanning(window.size)
        spectrum = np.abs(np.fft.rfft(shaped))
        freqs = np.fft.rfftfreq(shaped.size, d=1.0 / sr)
        total = float(np.sum(spectrum)) + 1e-9

        low_ratio = float(np.sum(spectrum[freqs < 180.0]) / total)
        low_mid_ratio = float(np.sum(spectrum[(freqs >= 180.0) & (freqs < 900.0)]) / total)
        mid_ratio = float(np.sum(spectrum[(freqs >= 900.0) & (freqs < 3500.0)]) / total)
        high_ratio = float(np.sum(spectrum[freqs >= 3500.0]) / total)
        very_high_ratio = float(np.sum(spectrum[freqs >= 7500.0]) / total)
        centroid = float(np.sum(freqs * spectrum) / total)
        peak_freq = float(freqs[int(np.argmax(spectrum))]) if spectrum.size else 0.0
        strength = float(np.sqrt(np.mean(np.square(window)))) if window.size else 0.0

        early_end = min(window.size, max(1, int(0.04 * sr)))
        early_energy = float(np.sqrt(np.mean(np.square(window[:early_end]))))
        late_start = min(window.size, int(0.08 * sr))
        late = window[late_start:]
        late_energy = float(np.sqrt(np.mean(np.square(late)))) if late.size else 0.0
        sustain_ratio = late_energy / (early_energy + 1e-9)

        # Spectral flux: how much the spectrum changes right after the onset.
        # High flux is typical of noisy / cymbal hits; low flux is typical of
        # tonal / kick hits.
        flux = 0.0
        compare_start = end
        compare_end = min(len(y), end + int(0.10 * sr))
        if compare_end > compare_start:
            compare = y[compare_start:compare_end]
            if compare.size > 16:
                compare_spectrum = np.abs(np.fft.rfft(compare * np.hanning(compare.size)))
                min_len = min(spectrum.size, compare_spectrum.size)
                flux = float(
                    np.sqrt(np.mean(np.square(spectrum[:min_len] - compare_spectrum[:min_len])))
                )

        part, confidence = self._classify(
            low_ratio=low_ratio,
            low_mid_ratio=low_mid_ratio,
            mid_ratio=mid_ratio,
            high_ratio=high_ratio,
            very_high_ratio=very_high_ratio,
            centroid=centroid,
            peak_freq=peak_freq,
            strength=strength,
            sustain_ratio=sustain_ratio,
            flux=flux,
        )

        # ── Confidence fallback: remap low-confidence hits to the ─────────
        # nearest main part instead of blindly accepting a likely wrong
        # classification. The `_CONFIDENCE_FALLBACK` dict maps each part
        # to a (primary, secondary) tuple. We use `primary` when the
        # classifier returned the secondary label with low confidence;
        # we use `secondary` when the classifier returned the primary
        # label --- that way ambiguous hits collapse to the nearest
        # well-defined part rather than defaulting to "tom_lomid".
        if confidence < _CLASSIFIER_CONFIDENCE_FLOOR and part in _CONFIDENCE_FALLBACK:
            primary, secondary = _CONFIDENCE_FALLBACK[part]
            if part == secondary:
                part = primary
            elif part == primary:
                part = secondary
            # Confidence stays low --- the remap is a best-effort guess,
            # not a confident classification.
            if confidence < 0.40:
                confidence = max(confidence, 0.40)

        return part, confidence, centroid, flux, strength

    @staticmethod
    def _classify(
        *,
        low_ratio: float,
        low_mid_ratio: float,
        mid_ratio: float,
        high_ratio: float,
        very_high_ratio: float,
        centroid: float,
        peak_freq: float,
        strength: float,
        sustain_ratio: float,
        flux: float,
    ) -> tuple[str, float]:
        # Kick: tonal low-end. Strong low_ratio, low centroid, peak below ~220Hz.
        if low_ratio > 0.32 and centroid < 1600.0 and peak_freq < 220.0:
            return "kick", min(0.98, 0.55 + low_ratio)

        # Hi-hat open vs closed. Closed is short and very high centroid;
        # open is longer-tailed (high sustain_ratio) and slightly less bright.
        if high_ratio > 0.55 and centroid > 4500.0:
            if sustain_ratio > 0.28:
                return "hihat_open", min(0.95, 0.45 + high_ratio + sustain_ratio * 0.2)
            if strength < 0.07 and centroid > 5500.0:
                return "hihat_closed", min(0.95, 0.50 + high_ratio)
            # Pedal hat: weak, very high centroid, low sustain.
            if strength < 0.04:
                return "hihat_closed", 0.55

        # Crash: very high centroid + long sustain + strong flux.
        if very_high_ratio > 0.20 and centroid > 5200.0 and sustain_ratio > 0.40:
            return "crash", min(0.95, 0.50 + very_high_ratio + sustain_ratio * 0.15)

        # China: high centroid, very long sustain.
        if centroid > 5800.0 and sustain_ratio > 0.55:
            return "china", min(0.90, 0.50 + sustain_ratio * 0.2)

        # Splash: short and bright, decay within ~150ms.
        if centroid > 5500.0 and 0.25 < sustain_ratio <= 0.40:
            return "splash", 0.78

        # Ride bell: short burst, peak in 3-5kHz, very short sustain.
        if 3000.0 < peak_freq < 5500.0 and sustain_ratio < 0.18 and strength > 0.06:
            return "ride_bell", min(0.88, 0.45 + mid_ratio * 0.3)

        # Ride: similar to hi-hat but less bright, longer sustain, lower flux.
        if high_ratio > 0.30 and 3500.0 < centroid < 6000.0 and sustain_ratio > 0.20:
            return "ride", min(0.90, 0.45 + high_ratio * 0.4 + sustain_ratio * 0.2)

        # Toms by pitch class.
        if low_mid_ratio > 0.30 and mid_ratio < 0.35:
            if centroid < 350.0:
                return "tom_floor", min(0.90, 0.50 + low_ratio)
            if centroid < 800.0:
                return "tom_low", min(0.90, 0.50 + low_mid_ratio)
            if centroid < 1500.0:
                return "tom_lomid", min(0.90, 0.50 + low_mid_ratio)
            if centroid < 2500.0:
                return "tom_himid", min(0.90, 0.50 + mid_ratio)
            return "tom_high", min(0.90, 0.50 + mid_ratio * 0.6)

        # Snare: mid+high content, attack-heavy, low sustain.
        if high_ratio > 0.35 and mid_ratio > 0.20 and sustain_ratio < 0.20:
            if strength < 0.04 and centroid > 6000.0:
                # Tiny edge noise --- not a snare.
                return "hihat_closed", 0.55
            return "snare", min(0.92, 0.46 + mid_ratio * 0.4 + high_ratio * 0.3)

        # Side stick: short, mid-dominant, almost no sustain.
        if mid_ratio > 0.45 and sustain_ratio < 0.10 and centroid < 3000.0:
            return "sidestick", min(0.85, 0.50 + mid_ratio * 0.3)

        # Hand clap-ish: noisy mid/high, very low sustain, strong flux.
        if mid_ratio + high_ratio > 0.55 and flux > 0.04 and sustain_ratio < 0.15:
            return "snare", min(0.88, 0.42 + (mid_ratio + high_ratio) * 0.3)

        # Cowbell: narrow band around 800Hz, short sustain.
        if 600.0 < peak_freq < 1200.0 and low_mid_ratio > 0.30 and sustain_ratio < 0.20:
            return "cowbell", 0.78

        # Tambourine: noisy high-mid, short sustain.
        if 2000.0 < centroid < 4500.0 and high_ratio > 0.30 and sustain_ratio < 0.18:
            return "tambourine", 0.74

        # Generic latin percussion fallback.
        if centroid > 4500.0 and sustain_ratio < 0.15:
            return "percussion", 0.60

        # Last-resort: ambiguous mid-energy hit.
        return ("tom_lomid" if centroid < 2500.0 else "snare"), 0.45

    @staticmethod
    def _derive_fills(hits: list[DrumHit]) -> list[DrumHit]:
        """Overlay a `fill` part on top of dense burst segments.

        A fill is any cluster of 3+ hits within ~450ms, or any hit whose
        predecessor follows within 220ms. The original hit stays in its
        primary part (kick / snare / tom) and a mirror with `part='fill'`
        is added so drummers can edit the fill bus independently.
        """
        if len(hits) < 2:
            return list(hits)

        fill_flags = [False] * len(hits)
        for index, hit in enumerate(hits):
            left = max(0, index - 4)
            right = min(len(hits), index + 5)
            nearby = [
                other
                for other in hits[left:right]
                if abs(other.time_s - hit.time_s) <= 0.45 and other is not hit
            ]
            prev_gap = hit.time_s - hits[index - 1].time_s if index > 0 else 99.0
            next_gap = hits[index + 1].time_s - hit.time_s if index + 1 < len(hits) else 99.0
            if len(nearby) >= 2 or prev_gap < 0.22 or next_gap < 0.22:
                fill_flags[index] = hit.part in {
                    "kick",
                    "snare",
                    "tom_high",
                    "tom_himid",
                    "tom_lomid",
                    "tom_low",
                    "tom_floor",
                }

        if not any(fill_flags):
            return list(hits)

        out: list[DrumHit] = []
        for hit, is_fill in zip(hits, fill_flags, strict=False):
            out.append(hit)
            if is_fill:
                out.append(
                    DrumHit(
                        time_s=hit.time_s,
                        part="fill",
                        midi_note=_GM_DRUM_NOTES["fill"],
                        velocity=hit.velocity,
                        confidence=min(0.98, hit.confidence + 0.08),
                        spectral_centroid=hit.spectral_centroid,
                        spectral_flux=hit.spectral_flux,
                    )
                )
        return out

    # ---- MIDI / CSV writers -------------------------------------------------
    @staticmethod
    def _write_midi(path: Path, hits: list[DrumHit], *, bpm: float, track_name: str) -> None:
        from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo, second2tick

        from app.services.midi_cc import gm_setup_messages

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
        for message in gm_setup_messages(
            channel=9,
            program=0,
            bank_msb=0,
            bank_lsb=0,
            volume=112,
            expression=127,
            pan=64,
        ):
            drum_track.append(message)

        events = []
        for hit in hits:
            start_tick = round(second2tick(hit.time_s, ticks_per_beat, tempo))
            duration_s = _NOTE_LENGTHS_SECONDS.get(hit.part, 0.09)
            end_tick = start_tick + max(1, round(second2tick(duration_s, ticks_per_beat, tempo)))
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
                    Message(
                        "note_off",
                        channel=9,
                        note=hit.midi_note,
                        velocity=0,
                        time=0,
                    ),
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
            writer.writerow(
                [
                    "time_s",
                    "part",
                    "midi_note",
                    "velocity",
                    "confidence",
                    "spectral_centroid",
                    "spectral_flux",
                ]
            )
            for hit in hits:
                writer.writerow(
                    [
                        f"{hit.time_s:.6f}",
                        hit.part,
                        hit.midi_note,
                        hit.velocity,
                        f"{hit.confidence:.3f}",
                        f"{hit.spectral_centroid:.1f}",
                        f"{hit.spectral_flux:.4f}",
                    ]
                )

    @staticmethod
    def _write_events_json(path: Path, hits: list[DrumHit], *, bpm: float) -> None:
        """Write a JSON event list for the browser to schedule samples.

        The frontend's `SampleBasedDrumPlayer` consumes this file to play
        back the drum track with a user-uploaded sample library. Keeping
        the format JSON (rather than MIDI-in-the-browser) avoids pulling
        a MIDI parser into the bundle; the list is small enough to fit
        comfortably in a single fetch even for long songs.
        """
        import json

        payload = {
            "bpm": round(bpm, 2),
            "events": [
                {
                    "t": round(hit.time_s, 4),
                    "note": int(hit.midi_note),
                    "velocity": int(hit.velocity),
                    "part": hit.part,
                }
                for hit in hits
            ],
        }
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)


def _dedupe_times(times: list[float], *, min_gap_s: float) -> list[float]:
    deduped: list[float] = []
    for time_s in sorted(float(t) for t in times):
        if not deduped or time_s - deduped[-1] >= min_gap_s:
            deduped.append(time_s)
    return deduped
