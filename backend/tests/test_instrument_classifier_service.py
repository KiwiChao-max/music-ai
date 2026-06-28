"""Tests for `app.services.instrument_classifier_service`.

The classifier is a dependency-light rule engine — we don't try to pin
exact posterior values (they're tuned empirically) but we do assert:

  * every instrument is represented in the output posteriors;
  * the probabilities sum to ~1.0;
  * the dominant instrument is the highest-probability one;
  * `detect` does not raise on a silent input (returns a clean zeroed
    detection);
  * `split_instrument_stem` writes at least one WAV when given a signal
    with enough energy.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.instrument_classifier_service import (
    INSTRUMENTS,
    InstrumentClassifierService,
)


def _write_tone(
    path: Path,
    *,
    freq: float = 440.0,
    seconds: float = 1.0,
    sample_rate: int = 24_000,
) -> None:
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    # Sine + a small amount of noise so the spectral features are not
    # completely degenerate (which would defeat the classifier).
    rng = np.random.default_rng(0)
    signal = 0.7 * np.sin(2 * np.pi * freq * t) + 0.05 * rng.standard_normal(t.shape)
    sf.write(str(path), signal.astype(np.float32), sample_rate, subtype="PCM_16")


def test_detect_returns_full_posterior_set(tmp_path: Path, storage_dir: Path) -> None:
    audio = tmp_path / "tone.wav"
    _write_tone(audio)
    service = InstrumentClassifierService()
    detection = service.detect(audio)
    assert set(detection.probabilities.keys()) == set(INSTRUMENTS)
    # Probabilities are normalized to sum to ~1.0 (within rounding).
    total = sum(detection.probabilities.values())
    assert abs(total - 1.0) < 0.01 or total == 0.0
    # `dominant` must be the highest-probability instrument (or the
    # fallback if the signal was silent).
    dominant = max(detection.probabilities, key=detection.probabilities.get)
    assert detection.dominant == dominant
    assert detection.total_frames > 0


def test_detect_on_silence_returns_zeroed_posteriors(
    tmp_path: Path, storage_dir: Path
) -> None:
    """A signal that's *too short* to compute any features must short-circuit
    to the documented "other_melodic" fallback. A longer silent signal goes
    through the rule engine — its posteriors are still all ~0 because the
    per-frame rules never trigger on a dead-silent frame.
    """
    audio = tmp_path / "silent.wav"
    # Tiny file (one frame) so `_compute_features` returns no features and
    # `detect` returns the zeroed fallback without ever calling the rules.
    sf.write(str(audio), np.zeros(1024, dtype=np.float32), 24_000, subtype="PCM_16")
    service = InstrumentClassifierService()
    detection = service.detect(audio)
    assert detection.probabilities
    assert all(v == 0.0 for v in detection.probabilities.values())
    assert detection.dominant == "other_melodic"
    assert detection.total_frames == 0


def test_split_instrument_stem_writes_wav_files(
    tmp_path: Path, storage_dir: Path
) -> None:
    audio = tmp_path / "tone.wav"
    _write_tone(audio, freq=220.0, seconds=1.5)
    output_dir = tmp_path / "out"

    service = InstrumentClassifierService()
    result = service.split_instrument_stem(audio, output_dir, stem_name="other")

    # At least one instrument should have been kept after the energy
    # threshold; the exact set depends on the rule engine's tuning.
    assert result.instrument_paths, "no instrument stems were written"
    for path in result.instrument_paths.values():
        assert path.is_file()
        assert path.suffix == ".wav"
        # WAV is not silent.
        y, _sr = sf.read(str(path))
        assert float(np.max(np.abs(y))) > 0.0

    # Detection is populated and consistent with the per-instrument
    # `instrument_paths` keys.
    assert set(result.detection.probabilities.keys()) == set(INSTRUMENTS)
    assert result.detection.total_frames > 0
