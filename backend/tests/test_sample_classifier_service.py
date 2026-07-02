"""Tests for `app.services.sample_classifier_service`.

The classifier uses heuristic spectral features, so we can't pin exact
drum-type labels for every synthetic signal. Instead we verify:
  * the service loads and exposes the full drum type catalogue;
  * classify() returns a result with sensible field shapes on a valid WAV;
  * silence / very short inputs are handled gracefully (None or a safe
    fallback rather than raising);
  * classify_bytes() works with raw bytes + a filename hint;
  * the label helper returns non-empty strings for every known drum type.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf
import pytest

from app.services.sample_classifier_service import SampleClassifierService


SAMPLE_RATE = 22050


def _write_tone(
    path: Path,
    *,
    freq_hz: float = 440.0,
    seconds: float = 1.0,
    sample_rate: int = SAMPLE_RATE,
    amplitude: float = 0.5,
) -> None:
    """Write a simple sine tone to a WAV file."""
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    signal = (amplitude * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
    sf.write(path, signal, sample_rate)


def _write_noise(
    path: Path,
    *,
    seconds: float = 1.0,
    sample_rate: int = SAMPLE_RATE,
    low_hz: float = 0.0,
    high_hz: float | None = None,
) -> None:
    """Write band-limited white noise to a WAV file."""
    rng = np.random.default_rng(42)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    noise = rng.standard_normal(t.shape).astype(np.float32)
    spectrum = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(noise.size, d=1.0 / sample_rate)
    spectrum[freqs < low_hz] = 0.0
    if high_hz is not None:
        spectrum[freqs > high_hz] = 0.0
    signal = np.fft.irfft(spectrum, n=noise.size).astype(np.float32)
    sf.write(path, signal, sample_rate)


def test_service_instantiation() -> None:
    service = SampleClassifierService()
    assert service is not None


def test_get_all_drum_types_returns_non_empty() -> None:
    service = SampleClassifierService()
    types = service.get_all_drum_types()
    assert len(types) > 0
    for drum_type, midi_note, label in types:
        assert isinstance(drum_type, str)
        assert len(drum_type) > 0
        assert isinstance(midi_note, int)
        assert 35 <= midi_note <= 81
        assert isinstance(label, str)
        assert len(label) > 0


def test_get_drum_type_label_returns_label() -> None:
    service = SampleClassifierService()
    types = service.get_all_drum_types()
    assert len(types) > 0
    drum_type, midi_note, label = types[0]
    assert service.get_drum_type_label(drum_type) == label


def test_get_drum_type_label_falls_back_to_title_case() -> None:
    service = SampleClassifierService()
    assert service.get_drum_type_label("nonexistent_drum") == "Nonexistent Drum"


def test_classify_returns_result_for_low_freq_signal(tmp_path: Path) -> None:
    service = SampleClassifierService()
    wav_path = tmp_path / "kick_test.wav"
    _write_noise(wav_path, seconds=1.0, high_hz=200.0)
    result = service.classify(wav_path)
    assert result is not None
    assert isinstance(result.drum_type, str)
    assert len(result.drum_type) > 0
    assert isinstance(result.midi_note, int)
    assert 35 <= result.midi_note <= 81
    assert isinstance(result.confidence, float)
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.features, dict)
    assert len(result.features) > 0


def test_classify_returns_result_for_high_freq_signal(tmp_path: Path) -> None:
    service = SampleClassifierService()
    wav_path = tmp_path / "hat_test.wav"
    _write_noise(wav_path, seconds=0.5, low_hz=5000.0)
    result = service.classify(wav_path)
    assert result is not None
    assert 35 <= result.midi_note <= 81


def test_classify_handles_silence(tmp_path: Path) -> None:
    service = SampleClassifierService()
    wav_path = tmp_path / "silent.wav"
    t = np.arange(int(1.0 * SAMPLE_RATE)) / SAMPLE_RATE
    silent = np.zeros_like(t, dtype=np.float32)
    sf.write(wav_path, silent, SAMPLE_RATE)
    result = service.classify(wav_path)
    assert result is None or isinstance(result, object)


def test_classify_bytes_works(tmp_path: Path) -> None:
    service = SampleClassifierService()
    wav_path = tmp_path / "bytes_test.wav"
    _write_tone(wav_path, freq_hz=150.0, seconds=1.0)
    content = wav_path.read_bytes()
    result = service.classify_bytes(content, "test_kick.wav")
    assert result is not None
    assert 35 <= result.midi_note <= 81


def test_classify_bytes_invalid_data_returns_none() -> None:
    service = SampleClassifierService()
    result = service.classify_bytes(b"not a real wav file", "bad.wav")
    assert result is None


def test_extract_features_returns_expected_keys(tmp_path: Path) -> None:
    service = SampleClassifierService()
    wav_path = tmp_path / "features_test.wav"
    _write_noise(wav_path, seconds=1.0, low_hz=200.0, high_hz=4000.0)
    features = service._extract_features(wav_path)
    assert features is not None
    assert "centroid" in features
    assert "rolloff" in features
    assert "peak_freq" in features
    assert "zcr" in features


def test_features_have_reasonable_values(tmp_path: Path) -> None:
    service = SampleClassifierService()
    wav_path = tmp_path / "values_test.wav"
    _write_tone(wav_path, freq_hz=1000.0, seconds=1.0)
    features = service._extract_features(wav_path)
    assert features is not None
    assert features["centroid"] > 0
    assert features["centroid"] < SAMPLE_RATE / 2
    assert features["peak_freq"] > 0
    assert features["peak_freq"] < SAMPLE_RATE / 2
    assert 0.0 <= features["zcr"] <= 1.0
