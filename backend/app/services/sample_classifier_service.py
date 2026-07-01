"""Sample classification service.

Automatically detects drum sample types from audio content using spectral
features. This enables users to upload samples with arbitrary filenames and
have them correctly mapped to GM percussion notes.

The classifier uses a rule-based approach similar to the drum detection in
drum_midi_service.py, tuned specifically for short one-shot samples rather
than continuous audio. It extracts:
  - spectral centroid (frequency center of mass)
  - peak frequency
  - spectral rolloff
  - RMS energy envelope
  - duration
  - harmonic content

And maps these to drum types: kick, snare, hihat (open/closed), tom (5 sizes),
cymbals (crash, ride, china, splash), and hand percussion.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_DRUM_TYPE_TO_GM_NOTE: dict[str, int] = {
    "kick": 36,
    "kick_acoustic": 35,
    "snare": 38,
    "snare_electric": 40,
    "rim": 37,
    "clap": 39,
    "hihat_closed": 42,
    "hihat_open": 46,
    "hihat_pedal": 44,
    "tom_floor": 41,
    "tom_low": 45,
    "tom_lomid": 47,
    "tom_himid": 48,
    "tom_high": 50,
    "crash": 49,
    "crash_2": 57,
    "ride": 51,
    "ride_bell": 53,
    "ride_2": 59,
    "china": 52,
    "splash": 55,
    "tambourine": 54,
    "cowbell": 56,
    "vibraslap": 58,
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
    "shaker": 70,
}


@dataclass(frozen=True)
class SampleClassification:
    drum_type: str
    midi_note: int
    confidence: float
    features: dict[str, float]


class SampleClassifierService:
    """Classify audio samples into drum types using spectral analysis."""

    SAMPLE_RATE = 22050

    def __init__(self) -> None:
        pass

    def classify(self, audio_path: Path) -> SampleClassification | None:
        """Classify a single audio sample file.

        Returns a SampleClassification with the detected drum type, GM note,
        confidence score (0.0-1.0), and extracted features. Returns None if
        the file cannot be processed.
        """
        try:
            features = self._extract_features(audio_path)
            if not features:
                return None
            drum_type, confidence = self._classify_from_features(features)
            midi_note = _DRUM_TYPE_TO_GM_NOTE.get(drum_type, 36)
            return SampleClassification(
                drum_type=drum_type,
                midi_note=midi_note,
                confidence=confidence,
                features=features,
            )
        except Exception as exc:
            logger.warning("sample-classifier: failed to classify %s: %s", audio_path.name, exc)
            return None

    def classify_bytes(self, content: bytes, filename: str) -> SampleClassification | None:
        """Classify audio content from bytes (for upload streaming)."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as f:
            f.write(content)
            temp_path = Path(f.name)
        try:
            return self.classify(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _extract_features(self, audio_path: Path) -> dict[str, float] | None:
        import numpy as np
        import librosa

        try:
            y, sr = librosa.load(str(audio_path), sr=self.SAMPLE_RATE, mono=True)
        except Exception:
            return None

        if y.size == 0 or float(np.max(np.abs(y))) < 1e-5:
            return None

        duration = float(len(y)) / sr

        if sr != self.SAMPLE_RATE:
            y = librosa.resample(y, orig_sr=sr, target_sr=self.SAMPLE_RATE)
            sr = self.SAMPLE_RATE

        rms = librosa.feature.rms(y=y)[0]
        peak_amplitude = float(np.max(np.abs(y)))
        mean_rms = float(np.mean(rms))
        std_rms = float(np.std(rms))

        spectrum = np.abs(np.fft.rfft(y * np.hanning(len(y))))
        freqs = np.fft.rfftfreq(len(y), d=1.0 / sr)
        total_energy = float(np.sum(spectrum)) + 1e-9

        centroid = float(np.sum(freqs * spectrum) / total_energy)

        rolloff_threshold = 0.85 * total_energy
        cumulative = np.cumsum(spectrum)
        rolloff_idx = np.argmax(cumulative >= rolloff_threshold)
        rolloff = float(freqs[rolloff_idx]) if rolloff_idx < len(freqs) else 0.0

        peak_idx = int(np.argmax(spectrum))
        peak_freq = float(freqs[peak_idx]) if peak_idx < len(freqs) else 0.0

        low_mask = freqs < 200.0
        mid_mask = (freqs >= 200.0) & (freqs < 2000.0)
        high_mask = freqs >= 2000.0
        very_high_mask = freqs >= 5000.0

        low_ratio = float(np.sum(spectrum[low_mask]) / total_energy)
        mid_ratio = float(np.sum(spectrum[mid_mask]) / total_energy)
        high_ratio = float(np.sum(spectrum[high_mask]) / total_energy)
        very_high_ratio = float(np.sum(spectrum[very_high_mask]) / total_energy)

        zcr = float(np.mean(librosa.feature.zero_crossing_rate(y=y)[0]))

        autocorr = librosa.autocorrelate(y, max_size=2048)
        harmonicity = float(np.max(autocorr[1:]) / (np.max(autocorr) + 1e-9)) if len(autocorr) > 1 else 0.0

        decay_samples = int(0.1 * sr)
        if len(y) > decay_samples:
            peak_pos = np.argmax(np.abs(y))
            tail_start = min(peak_pos + decay_samples, len(y) - 1)
            tail_energy = float(np.sum(np.abs(y[tail_start:]))) / (len(y) - tail_start)
            attack_ratio = float(np.abs(y[peak_pos])) / (tail_energy + 1e-9)
        else:
            attack_ratio = 1.0

        return {
            "duration": duration,
            "centroid": centroid,
            "rolloff": rolloff,
            "peak_freq": peak_freq,
            "low_ratio": low_ratio,
            "mid_ratio": mid_ratio,
            "high_ratio": high_ratio,
            "very_high_ratio": very_high_ratio,
            "zcr": zcr,
            "harmonicity": harmonicity,
            "peak_amplitude": peak_amplitude,
            "mean_rms": mean_rms,
            "std_rms": std_rms,
            "attack_ratio": attack_ratio,
        }

    def _classify_from_features(self, features: dict[str, float]) -> tuple[str, float]:
        c = features["centroid"]
        pf = features["peak_freq"]
        ro = features["rolloff"]
        lr = features["low_ratio"]
        mr = features["mid_ratio"]
        hr = features["high_ratio"]
        vhr = features["very_high_ratio"]
        zcr = features["zcr"]
        harm = features["harmonicity"]
        dur = features["duration"]
        attack = features["attack_ratio"]

        candidates: list[tuple[str, float]] = []

        if lr > 0.25 and c < 1500.0 and pf < 300.0:
            if lr > 0.40 and harm > 0.3:
                candidates.append(("kick", min(0.98, 0.6 + lr * 0.8)))
            else:
                candidates.append(("kick_acoustic", min(0.95, 0.55 + lr * 0.6)))

        if mr > 0.30 and hr > 0.20 and c > 2000.0:
            if attack > 10.0 and zcr > 0.08:
                candidates.append(("snare", min(0.95, 0.5 + mr * 0.5)))
            elif harm > 0.2:
                candidates.append(("snare_electric", min(0.90, 0.45 + mr * 0.4)))

        if hr > 0.40 and c > 4000.0:
            if vhr > 0.25 and dur > 0.15:
                candidates.append(("hihat_open", min(0.92, 0.5 + vhr)))
            elif dur < 0.08:
                candidates.append(("hihat_closed", min(0.95, 0.55 + hr)))
            elif attack < 5.0:
                candidates.append(("hihat_pedal", min(0.85, 0.5 + hr * 0.5)))

        if ro > 3000.0 and vhr > 0.15:
            if dur > 0.3:
                candidates.append(("crash", min(0.95, 0.5 + vhr * 1.5)))
            elif c > 6000.0:
                candidates.append(("china", min(0.90, 0.5 + vhr)))
            elif dur < 0.2:
                candidates.append(("splash", min(0.85, 0.5 + hr)))

        if hr > 0.25 and 3500.0 < c < 6000.0:
            if pf > 4000.0 and attack > 15.0:
                candidates.append(("ride_bell", min(0.88, 0.45 + mr * 0.3)))
            elif dur > 0.25:
                candidates.append(("ride", min(0.90, 0.45 + hr * 0.4)))

        if mr > 0.25 and lr > 0.10:
            if c < 400.0:
                candidates.append(("tom_floor", min(0.90, 0.5 + lr)))
            elif c < 800.0:
                candidates.append(("tom_low", min(0.90, 0.5 + lr * 0.5)))
            elif c < 1500.0:
                candidates.append(("tom_lomid", min(0.90, 0.5 + mr)))
            elif c < 2500.0:
                candidates.append(("tom_himid", min(0.90, 0.5 + mr)))
            elif c < 3500.0:
                candidates.append(("tom_high", min(0.90, 0.5 + hr)))

        if mr > 0.40 and zcr < 0.05 and harm > 0.3:
            candidates.append(("cowbell", min(0.85, 0.5 + mr * 0.5)))

        if hr > 0.30 and zcr > 0.10 and dur < 0.1:
            candidates.append(("tambourine", min(0.80, 0.45 + hr)))

        if hr > 0.20 and zcr > 0.15:
            candidates.append(("shaker", min(0.75, 0.4 + hr)))

        if not candidates:
            if lr > 0.3:
                return "kick", 0.5
            elif hr > 0.3:
                return "hihat_closed", 0.45
            else:
                return "snare", 0.4

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0]

    def get_drum_type_label(self, drum_type: str) -> str:
        """Return a human-readable label for a drum type."""
        labels: dict[str, str] = {
            "kick": "Kick",
            "kick_acoustic": "Acoustic Kick",
            "snare": "Snare",
            "snare_electric": "Electric Snare",
            "rim": "Rimshot",
            "clap": "Clap",
            "hihat_closed": "Closed Hi-Hat",
            "hihat_open": "Open Hi-Hat",
            "hihat_pedal": "Pedal Hi-Hat",
            "tom_floor": "Floor Tom",
            "tom_low": "Low Tom",
            "tom_lomid": "Low-Mid Tom",
            "tom_himid": "Hi-Mid Tom",
            "tom_high": "High Tom",
            "crash": "Crash Cymbal",
            "crash_2": "Crash Cymbal 2",
            "ride": "Ride Cymbal",
            "ride_bell": "Ride Bell",
            "ride_2": "Ride Cymbal 2",
            "china": "China Cymbal",
            "splash": "Splash Cymbal",
            "tambourine": "Tambourine",
            "cowbell": "Cowbell",
            "vibraslap": "Vibraslap",
            "bongo": "Bongo",
            "conga": "Conga",
            "timbale": "Timbale",
            "agogo": "Agogo",
            "cabasa": "Cabasa",
            "maracas": "Maracas",
            "whistle": "Whistle",
            "guiro": "Guiro",
            "claves": "Claves",
            "woodblock": "Woodblock",
            "cuica": "Cuica",
            "triangle": "Triangle",
            "shaker": "Shaker",
        }
        return labels.get(drum_type, drum_type.replace("_", " ").title())

    def get_all_drum_types(self) -> list[tuple[str, int, str]]:
        """Return all supported drum types with their GM notes and labels."""
        result = []
        for drum_type, note in _DRUM_TYPE_TO_GM_NOTE.items():
            result.append((drum_type, note, self.get_drum_type_label(drum_type)))
        return sorted(result, key=lambda x: x[1])
