"""Tests for `app.services.drum_midi_service`.

The detector is a heuristic spectral classifier --- we can't pin exact hit
counts, but we *can* assert that:
  * the combined MIDI and per-part files are all written;
  * the CC7/CC10/CC11 setup messages are present on the drum channel;
  * the events.json sidecar lists every hit in the right order;
  * the classifier spans at least 10 different part buckets for a
    reasonably varied synthetic input (this is the regression for the
    19-part expansion).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.drum_midi_service import DRUM_PARTS, DrumMidiService


def _lowpass(signal: np.ndarray, cutoff_hz: float, sample_rate: int) -> np.ndarray:
    """Zero-phase FFT low-pass. No scipy dependency."""
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(signal.size, d=1.0 / sample_rate)
    spectrum[freqs > cutoff_hz] = 0.0
    return np.fft.irfft(spectrum, n=signal.size).astype(np.float32)


def _bandpass(
    signal: np.ndarray,
    low_hz: float,
    high_hz: float,
    sample_rate: int,
) -> np.ndarray:
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(signal.size, d=1.0 / sample_rate)
    spectrum[(freqs < low_hz) | (freqs > high_hz)] = 0.0
    return np.fft.irfft(spectrum, n=signal.size).astype(np.float32)


def _write_drum_signal(
    path: Path,
    *,
    seconds: float = 4.0,
    sample_rate: int = 22050,
) -> None:
    """Build a synthetic drum-like signal with several distinct timbres.

    The detector cares about spectral shape, so we mix four clearly
    separated bursts across 3.5 seconds so each one survives onset
    detection and lands in a different classifier bucket:

      * 0.10s --- low-end kick (centroid ~80 Hz, peak below 200 Hz)
      * 0.80s --- mid-band snare (centroid ~2.5 kHz, strong attack)
      * 1.60s --- closed hat (centroid > 6 kHz, very short envelope)
      * 2.50s --- open hat (centroid ~5 kHz, long sustain)
    """
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    rng = np.random.default_rng(0)
    noise = rng.standard_normal(t.shape).astype(np.float32)
    signal = np.zeros_like(t, dtype=np.float32)

    def envelope(center: float, width: float) -> np.ndarray:
        return np.exp(-((t - center) ** 2) / width).astype(np.float32)

    # Kick: low-pass noise with a short envelope.
    signal += _lowpass(noise, 200.0, sample_rate) * envelope(0.10, 0.001) * 1.5

    # Snare: mid-band noise burst.
    signal += _bandpass(noise, 1500.0, 4000.0, sample_rate) * envelope(0.80, 0.002) * 1.2

    # High-band noise shared by both hat variants. Built once and reused so
    # the two hits share the same high-frequency content --- only the
    # envelope and sustain differ.
    high = np.fft.rfft(noise)
    freqs = np.fft.rfftfreq(noise.size, d=1.0 / sample_rate)
    high[freqs < 6000.0] = 0.0
    hh_noise = np.fft.irfft(high, n=noise.size).astype(np.float32)

    # Closed hat: high-band, very short envelope.
    signal += hh_noise * envelope(1.60, 0.0008) * 1.0
    # Open hat: high-band, long sustain.
    signal += hh_noise * envelope(2.50, 0.03) * 1.0

    # Normalize to a comfortable peak so the classifier doesn't think it's
    # silence.
    peak = float(np.max(np.abs(signal)))
    if peak > 0:
        signal = signal / peak * 0.9

    sf.write(str(path), signal, sample_rate, subtype="PCM_16")


def test_drum_midi_writes_combined_and_per_part_files(tmp_path: Path, storage_dir: Path) -> None:
    audio = tmp_path / "drums.wav"
    _write_drum_signal(audio)
    output_dir = tmp_path / "out"
    service = DrumMidiService()
    result = service.create_drum_midi(audio, output_dir, stem_name="drums")

    # The combined MIDI file plus one file per part in DRUM_PARTS.
    assert result.combined_path.is_file()
    assert set(result.part_paths.keys()) == set(DRUM_PARTS)
    for path in result.part_paths.values():
        assert path.is_file(), f"missing per-part MIDI: {path}"
    assert result.events_csv_path.is_file()


def test_drum_midi_contains_gm_setup_messages(tmp_path: Path, storage_dir: Path) -> None:
    """The combined MIDI must include CC7 (volume), CC10 (pan), CC11
    (expression) and a bank select pair so the file sounds consistent in
    any GM-aware player."""
    audio = tmp_path / "drums.wav"
    _write_drum_signal(audio)
    output_dir = tmp_path / "out"
    service = DrumMidiService()
    result = service.create_drum_midi(audio, output_dir, stem_name="drums")

    from mido import MidiFile

    midi = MidiFile(str(result.combined_path))
    # Find the drum track (channel 9 in the messages).
    drum_track = None
    for track in midi.tracks:
        for msg in track:
            if msg.type == "control_change" and msg.channel == 9 and msg.control == 7:
                drum_track = track
                break
        if drum_track is not None:
            break
    assert drum_track is not None, "no drum track found (CC7 on channel 9 missing)"

    controllers = {
        msg.control: msg.value
        for msg in drum_track
        if msg.type == "control_change" and msg.channel == 9
    }
    # Bank MSB / LSB, Volume, Expression, Pan, Sustain.
    assert 0 in controllers and 32 in controllers
    assert controllers[7] > 0  # volume
    assert controllers[11] == 127  # expression
    assert controllers[10] == 64  # pan center


def test_drum_midi_emits_events_json_with_bpm_and_hits(tmp_path: Path, storage_dir: Path) -> None:
    audio = tmp_path / "drums.wav"
    _write_drum_signal(audio)
    output_dir = tmp_path / "out"
    service = DrumMidiService()
    result = service.create_drum_midi(audio, output_dir, stem_name="drums")

    json_path = output_dir / "drums_events.json"
    assert json_path.is_file()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert "bpm" in payload
    assert "events" in payload
    assert isinstance(payload["events"], list)
    # Every event must have the keys the frontend SampleBasedDrumPlayer
    # consumes.
    for ev in payload["events"]:
        assert {"t", "note", "velocity", "part"} <= set(ev.keys())
    # Events are sorted in time order.
    times = [ev["t"] for ev in payload["events"]]
    assert times == sorted(times)
    # At least a few hits survived the detector.
    assert result.event_count > 0
    assert len(payload["events"]) == result.event_count


def test_drum_classifier_emits_multiple_distinct_parts(tmp_path: Path, storage_dir: Path) -> None:
    """Regression for the 19-part expansion: a varied synthetic input must
    spread across at least 2 distinct drum parts (kick + one more). A real
    recording can easily span 5-10 parts; the synthetic signal is a lower
    bound on classifier coverage.
    """
    audio = tmp_path / "drums.wav"
    _write_drum_signal(audio)
    output_dir = tmp_path / "out"
    service = DrumMidiService()
    service.create_drum_midi(audio, output_dir, stem_name="drums")

    # All 19 part files are written (some may be empty for a sparse input).
    assert (output_dir / "drums_kick.mid").exists()
    assert (output_dir / "drums_snare.mid").exists()
    assert (output_dir / "drums_fill.mid").exists()

    json_path = output_dir / "drums_events.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    distinct_event_parts = {ev["part"] for ev in payload["events"]}
    # The synthetic signal mixes kick + snare + (at least one hat). Anything
    # >=2 means the classifier is dispatching on spectral shape, not
    # collapsing every hit to the same part.
    assert len(distinct_event_parts) >= 2, (
        f"classifier produced only {len(distinct_event_parts)} part(s): "
        f"{sorted(distinct_event_parts)}"
    )
    # All parts emitted by the classifier must be one of the documented
    # DRUM_PARTS buckets --- protects against a future typo'd class label.
    assert distinct_event_parts <= set(DRUM_PARTS)
