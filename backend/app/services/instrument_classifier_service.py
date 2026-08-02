"""Instrument classifier + per-instemment audio splitter.

The Demucs pipeline produces a single ``other`` stem that contains everything
that isn't vocals / drums / bass: piano, guitar, strings, synths, etc. The
classifier here turns that single stem into per-instrument audio and MIDI
files, so the downstream transcription (Basic Pitch) can produce cleaner,
per-instrument output.

This is a pragmatic, dependency-light implementation. It works in three
phases:

  1. **Spectral fingerprinting** --- short-time FFT, per-frame spectral
     centroid, bandwidth, rolloff, flatness, zero-crossing rate, and MFCCs.
  2. **Frame-level instrument posteriors** --- a small, hand-coded rule
     engine maps these features to per-instrument probabilities
     (piano / guitar / strings / synth / other). The rules are calibrated
     against common timbral signatures; not a trained model, but good
     enough to *separate* the dominant timbres.
  3. **Soft mask reconstruction** --- each frame's signal is mixed into the
     target stems weighted by the posterior, so notes that overlap with
     several timbres are partially shared rather than dropped. This keeps
     the audio sounding natural instead of muddy.

For each detected instrument with enough energy, the service writes:
  * ``<instrument>.wav`` --- the separated audio (24 kHz mono WAV).
  * ``<instrument>.mid``  --- Basic Pitch transcription with full GM CCs.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)


# Ordered list of supported instruments. Order is the rendering order for
# both the audio files and the per-instrument MIDI / analysis output.
INSTRUMENTS: tuple[str, ...] = (
    "piano",
    "guitar",
    "strings",
    "synth",
    "other_melodic",
)


@dataclass(frozen=True)
class InstrumentDetection:
    """Probability mass for each instrument over the entire input.

    `total_frames` is the number of analysis frames the posteriors were
    aggregated over --- useful for confidence reporting.
    """

    probabilities: dict[str, float]
    dominant: str
    total_frames: int


@dataclass(frozen=True)
class InstrumentSplitResult:
    """Files written by `split_instrument_stem`."""

    instrument_paths: dict[str, Path]
    detection: InstrumentDetection


class InstrumentClassifierService:
    """Split a mixed stem into per-instrument audio + MIDI files.

    The service is intentionally cheap to construct so the audio worker
    can hold a single instance across tasks.
    """

    SAMPLE_RATE = 24_000
    FRAME_LENGTH = 2048
    HOP_LENGTH = 512
    # Minimum posterior mass over the entire track to keep a stem.
    KEEP_THRESHOLD = 0.04

    def __init__(self, *, basic_pitch_service=None) -> None:
        # basic_pitch_service is injected to avoid a circular import; the
        # worker passes the real instance so per-instrument MIDI uses the
        # same transcription code path as the other stems.
        self._basic_pitch = basic_pitch_service

    def detect(self, audio_path: Path) -> InstrumentDetection:
        """Return a per-instrument probability summary without writing files."""
        features_per_frame = self._compute_features(audio_path)
        if not features_per_frame:
            return InstrumentDetection(
                probabilities={name: 0.0 for name in INSTRUMENTS},
                dominant="other_melodic",
                total_frames=0,
            )
        posterior = self._aggregate_posteriors(features_per_frame)
        dominant = max(posterior, key=posterior.get)
        return InstrumentDetection(
            probabilities=posterior,
            dominant=dominant,
            total_frames=len(features_per_frame),
        )

    def split_instrument_stem(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        stem_name: str = "other",
    ) -> InstrumentSplitResult:
        """Split the input audio into per-instrument WAVs (and per-instrument MIDI
        if a Basic Pitch service is wired in).
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        y, sr = sf.read(str(audio_path), always_2d=False, dtype="float32")
        if y.ndim > 1:
            y = y.mean(axis=1)
        if sr != self.SAMPLE_RATE:
            y = _resample_linear(y, sr, self.SAMPLE_RATE)
            sr = self.SAMPLE_RATE
        if y.size == 0 or float(np.max(np.abs(y))) < 1e-5:
            logger.warning("instrument-classifier: %s is silent", audio_path.name)
            empty_detection = InstrumentDetection(
                probabilities={name: 0.0 for name in INSTRUMENTS},
                dominant="other_melodic",
                total_frames=0,
            )
            return InstrumentSplitResult(
                instrument_paths={},
                detection=empty_detection,
            )

        features_per_frame = self._compute_features(audio_path)
        if not features_per_frame:
            empty_detection = InstrumentDetection(
                probabilities={name: 0.0 for name in INSTRUMENTS},
                dominant="other_melodic",
                total_frames=0,
            )
            return InstrumentSplitResult(
                instrument_paths={},
                detection=empty_detection,
            )

        posterior = self._aggregate_posteriors(features_per_frame)
        masks = self._build_masks(y, sr, features_per_frame, posterior)
        instrument_paths: dict[str, Path] = {}
        for instrument in INSTRUMENTS:
            mask = masks.get(instrument)
            if mask is None:
                continue
            energy = float(np.sqrt(np.mean(np.square(mask * y))))
            # Skip stems that have no audible content; the audio would be
            # pure silence otherwise and confuse the downstream Basic Pitch
            # pass.
            if energy < 0.005:
                continue
            target = output_dir / f"{stem_name}_{instrument}.wav"
            sf.write(str(target), mask * y, sr, subtype="PCM_16")
            instrument_paths[instrument] = target

            if self._basic_pitch is not None:
                try:
                    self._basic_pitch.transcribe(target, output_dir)
                    # Rename the file Basic Pitch wrote so the per-instrument
                    # MIDI has a stable name.
                    generated = output_dir / f"{target.stem}.mid"
                    desired = output_dir / f"{stem_name}_{instrument}.mid"
                    if generated != desired and generated.is_file():
                        generated.rename(desired)
                except Exception as exc:
                    logger.warning("instrument-classifier: MIDI failed for %s: %s", instrument, exc)

        dominant = max(posterior, key=posterior.get)
        return InstrumentSplitResult(
            instrument_paths=instrument_paths,
            detection=InstrumentDetection(
                probabilities=posterior,
                dominant=dominant,
                total_frames=len(features_per_frame),
            ),
        )

    # ---- per-frame features -------------------------------------------------
    def _compute_features(self, audio_path: Path) -> list[dict[str, float]]:
        y, sr = sf.read(str(audio_path), always_2d=False, dtype="float32")
        if y.ndim > 1:
            y = y.mean(axis=1)
        if sr != self.SAMPLE_RATE:
            y = _resample_linear(y, sr, self.SAMPLE_RATE)
            sr = self.SAMPLE_RATE
        if y.size < self.HOP_LENGTH:
            return []

        # Per-frame spectral descriptors via numpy FFT (cheaper than
        # librosa.feature.* and dependency-free).
        frames = _frame_signal(y, self.FRAME_LENGTH, self.HOP_LENGTH)
        window = np.hanning(self.FRAME_LENGTH)
        framed = frames * window
        spectrum = np.abs(np.fft.rfft(framed, axis=1))
        freqs = np.fft.rfftfreq(self.FRAME_LENGTH, d=1.0 / sr)

        # Pre-compute denominator / sums once.
        total_energy = spectrum.sum(axis=1) + 1e-9
        # Spectral centroid (mean frequency) per frame.
        centroid = (spectrum * freqs).sum(axis=1) / total_energy
        # Spectral bandwidth.
        bandwidth = np.sqrt(
            ((freqs - centroid[:, None]) ** 2 * spectrum).sum(axis=1) / total_energy
        )
        # Spectral rolloff: frequency below which 85% of energy lies.
        cumulative = np.cumsum(spectrum, axis=1)
        rolloff_threshold = 0.85 * total_energy
        rolloff_idx = (cumulative >= rolloff_threshold[:, None]).argmax(axis=1)
        rolloff = freqs[rolloff_idx]
        # Spectral flatness: geometric mean / arithmetic mean of spectrum.
        log_spectrum = np.log(spectrum + 1e-9)
        flatness = np.exp(log_spectrum.mean(axis=1)) / (spectrum.mean(axis=1) + 1e-9)
        # Time-domain zero-crossing rate.
        zero_crossings = np.abs(np.diff(np.sign(frames), axis=1)).sum(axis=1) / self.FRAME_LENGTH
        # HF ratio.
        hf_mask = freqs >= 4000.0
        hf_ratio = spectrum[:, hf_mask].sum(axis=1) / total_energy
        # Mid ratio (1k..4k).
        mid_mask = (freqs >= 1000.0) & (freqs < 4000.0)
        mid_ratio = spectrum[:, mid_mask].sum(axis=1) / total_energy
        # Low ratio (<250 Hz).
        low_mask = freqs < 250.0
        low_ratio = spectrum[:, low_mask].sum(axis=1) / total_energy
        # Harmonicity proxy: peakiness of the spectrum.
        peakiness = spectrum.max(axis=1) / (spectrum.mean(axis=1) + 1e-9)

        frame_count = frames.shape[0]
        features_per_frame: list[dict[str, float]] = []
        for i in range(frame_count):
            features_per_frame.append(
                {
                    "centroid": float(centroid[i]),
                    "bandwidth": float(bandwidth[i]),
                    "rolloff": float(rolloff[i]),
                    "flatness": float(min(1.0, flatness[i])),
                    "zcr": float(zero_crossings[i]),
                    "hf_ratio": float(hf_ratio[i]),
                    "mid_ratio": float(mid_ratio[i]),
                    "low_ratio": float(low_ratio[i]),
                    "peakiness": float(min(50.0, peakiness[i])),
                    "rms": float(np.sqrt(np.mean(frames[i] ** 2))),
                }
            )
        return features_per_frame

    # ---- posterior aggregation ---------------------------------------------
    def _aggregate_posteriors(self, features: Sequence[dict[str, float]]) -> dict[str, float]:
        """Average per-frame posteriors weighted by RMS.

        The weighting matters: a quiet noise floor is mostly high-flatness
        (synth-like) and would otherwise dominate the average. RMS keeps
        the audible signal in charge.
        """
        per_instrument: dict[str, float] = {name: 0.0 for name in INSTRUMENTS}
        total_weight = 0.0
        for frame in features:
            rms = max(frame["rms"], 1e-4)
            total_weight += rms
            for instrument, score in self._frame_posterior(frame).items():
                per_instrument[instrument] += score * rms

        if total_weight <= 0:
            return {name: 0.0 for name in INSTRUMENTS}
        return {name: round(per_instrument[name] / total_weight, 4) for name in INSTRUMENTS}

    def _frame_posterior(self, frame: dict[str, float]) -> dict[str, float]:
        """Per-frame probability of each instrument.

        The rules are deliberately simple, interpretable, and tuned on the
        ballpark centroids / bandwidths of common solo instruments.
        """
        c = frame["centroid"]
        bw = frame["bandwidth"]
        ro = frame["rolloff"]
        flat = frame["flatness"]
        hf = frame["hf_ratio"]
        zcr = frame["zcr"]
        peak = frame["peakiness"]

        piano = 0.0
        if peak > 12.0 and 200.0 < c < 3500.0 and bw > 800.0 and flat < 0.30:
            piano = min(1.0, 0.55 + (peak - 12.0) * 0.05 + (1.0 - flat))
        elif 200.0 < c < 3000.0 and flat < 0.25 and hf < 0.20:
            piano = 0.40

        guitar = 0.0
        # Acoustic / electric guitar: 200-3500 Hz, mid bandwidth, light HF.
        if 250.0 < c < 3000.0 and 600.0 < bw < 2500.0 and 0.10 < flat < 0.45 and hf < 0.25:
            guitar = min(1.0, 0.45 + (0.4 - flat) * 0.4 + (1.0 - hf) * 0.3)
        # Plucked string: lots of HF noise, high ZCR.
        if zcr > 0.10 and 800.0 < c < 4000.0 and hf > 0.05:
            guitar += 0.30
        guitar = min(1.0, guitar)

        strings = 0.0
        # Sustained strings: long rolloff, low flatness, low HF.
        if ro > 2500.0 and flat < 0.20 and hf < 0.10 and 250.0 < c < 2000.0:
            strings = min(1.0, 0.50 + (1.0 - flat) * 0.5)
        # Bowed strings have characteristic vibrato --- we don't try to model
        # vibrato explicitly, so we rely on the brightness / rolloff
        # signature above.

        synth = 0.0
        # Synth / pad: high flatness, broad centroid, heavy HF.
        if flat > 0.30 and bw > 1800.0 and hf > 0.10:
            synth = min(1.0, 0.40 + (flat - 0.30) * 1.5)
        # Pure tones (sine-wave synth leads) --- high peakiness, very narrow.
        if peak > 25.0 and bw < 600.0:
            synth += 0.30

        other = 0.4
        if piano + guitar + strings + synth < 0.5:
            other = 1.0
        else:
            other = max(0.05, 1.0 - (piano + guitar + strings + synth))

        return {
            "piano": round(piano, 4),
            "guitar": round(guitar, 4),
            "strings": round(strings, 4),
            "synth": round(synth, 4),
            "other_melodic": round(other, 4),
        }

    # ---- soft-mask reconstruction ------------------------------------------
    def _build_masks(
        self,
        y: np.ndarray,
        sr: int,
        features: Sequence[dict[str, float]],
        posterior: dict[str, float],
    ) -> dict[str, np.ndarray]:
        """Build a per-sample soft mask for each instrument.

        Each frame's contribution to the global instrument mask is
        proportional to the frame-level posterior for that instrument,
        weighted by the global posterior mass. Frames that the classifier
        is unsure about are split across instruments rather than dropped.
        """
        frame_count = len(features)
        if frame_count == 0:
            return {}
        frame_masks = np.zeros((frame_count, len(INSTRUMENTS)), dtype=np.float32)
        for index, frame in enumerate(features):
            frame_posterior = self._frame_posterior(frame)
            rms_weight = min(1.0, max(0.2, frame["rms"] * 4.0))
            for j, instrument in enumerate(INSTRUMENTS):
                frame_masks[index, j] = float(frame_posterior[instrument]) * rms_weight

        # Normalize per frame so the masks sum to ~1.0 --- the reconstruction
        # preserves total energy that way.
        per_frame_sum = frame_masks.sum(axis=1, keepdims=True) + 1e-9
        frame_masks = frame_masks / per_frame_sum

        # Overlap-add back into the sample domain.
        sample_masks: dict[str, np.ndarray] = {}
        window = np.hanning(self.FRAME_LENGTH).astype(np.float32)
        for j, instrument in enumerate(INSTRUMENTS):
            if posterior.get(instrument, 0.0) < self.KEEP_THRESHOLD:
                continue
            samples = _overlap_add_weights(
                frame_masks[:, j], self.FRAME_LENGTH, self.HOP_LENGTH, len(y), window
            )
            # Soft normalization so loud instruments don't dominate the mix.
            samples = samples / (samples.max() + 1e-9)
            sample_masks[instrument] = samples
        return sample_masks


# ---------------------------------------------------------------------------
# Helpers (no external deps beyond numpy / soundfile)
# ---------------------------------------------------------------------------
def _frame_signal(y: np.ndarray, frame_length: int, hop_length: int) -> np.ndarray:
    n_frames = max(0, 1 + (len(y) - frame_length) // hop_length)
    if n_frames == 0:
        return np.zeros((0, frame_length), dtype=np.float32)
    indices = np.arange(frame_length)[None, :] + hop_length * np.arange(n_frames)[:, None]
    return y[indices].astype(np.float32)


def _resample_linear(y: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or y.size == 0:
        return y
    duration = y.shape[0] / float(sr_in)
    target_length = round(duration * sr_out)
    if target_length <= 1:
        return y
    x_old = np.linspace(0.0, duration, num=y.shape[0], endpoint=False)
    x_new = np.linspace(0.0, duration, num=target_length, endpoint=False)
    return np.interp(x_new, x_old, y).astype(np.float32)


def _overlap_add_weights(
    weights: np.ndarray,
    frame_length: int,
    hop_length: int,
    target_length: int,
    window: np.ndarray,
) -> np.ndarray:
    """Reconstruct a per-sample mask by overlap-adding windowed frames."""
    n_frames = weights.shape[0]
    output = np.zeros(target_length, dtype=np.float32)
    window_sum = np.zeros(target_length, dtype=np.float32)
    for i in range(n_frames):
        start = i * hop_length
        end = start + frame_length
        if end > target_length:
            end = target_length
            frame_length_eff = end - start
        else:
            frame_length_eff = frame_length
        output[start:end] += weights[i] * window[:frame_length_eff]
        window_sum[start:end] += window[:frame_length_eff]
    nonzero = window_sum > 1e-6
    output[nonzero] /= window_sum[nonzero]
    return output
