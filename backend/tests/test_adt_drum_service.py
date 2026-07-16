"""Tests for ``app.services.adt_drum_service``.

The ADTOS model itself is heavy (torch + a checkpoint) and is not
available in the test environment. We exercise the service by
injecting a tiny in-memory ``ADTModelBackend`` that returns canned
``ADTHit`` predictions, then assert:

  * the 9-class output maps onto the expected ``DRUM_PARTS`` bucket
    (KD -> kick, SD -> snare, HT -> tom_high, ...);
  * cymbal hits (``CY`` / ``RD``) are sub-classified into
    crash / china / splash / ride / ride_bell based on the spectral
    content of the audio around the onset;
  * the combined MIDI and per-part files are written using the same
    filenames / paths as the rule-based ``DrumMidiService`` so the
    e2e pipeline and frontend keep working unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

from app.services.adt_drum_service import (
    ADTOS_LABELS,
    ADTHit,
    ADTDrumService,
)
from app.services.drum_midi_service import DRUM_PARTS


class _ScriptedBackend:
    """In-memory ADTOS backend for tests.

    ``hits`` is a list of (time_s, label, confidence) tuples that the
    service will see as the model's prediction. Tests build the audio
    first so the cymbal sub-classifier has something to look at.
    """

    def __init__(self, hits: list[ADTHit]) -> None:
        self.hits = list(hits)
        self.calls: list[Path] = []

    def predict(self, audio_path: Path) -> list[ADTHit]:
        self.calls.append(audio_path)
        return list(self.hits)


def _build_audio_with_bursts(
    path: Path,
    *,
    bursts: list[tuple[float, float, float, float | None]],
    seconds: float = 3.0,
    sample_rate: int = 22050,
) -> None:
    """Build a synthetic audio file with timed noise bursts.

    ``bursts`` is a list of ``(center_s, cutoff_hz, envelope_width, highpass_hz)``
    tuples. ``highpass_hz`` may be ``None`` for a flat spectrum from 0 to
    ``cutoff_hz``; when set, only frequencies between ``highpass_hz`` and
    ``cutoff_hz`` are kept (useful for pushing the centroid above the
    flat-spectrum value of ``cutoff/2``).
    """
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    rng = np.random.default_rng(42)
    noise = rng.standard_normal(t.shape).astype(np.float32)
    signal = np.zeros_like(t, dtype=np.float32)

    for center, cutoff, width, highpass in bursts:
        spectrum = np.fft.rfft(noise)
        freqs = np.fft.rfftfreq(noise.size, d=1.0 / sample_rate)
        spectrum[freqs > cutoff] = 0.0
        if highpass is not None:
            spectrum[freqs < highpass] = 0.0
        band = np.fft.irfft(spectrum, n=noise.size).astype(np.float32)
        envelope = np.exp(-((t - center) ** 2) / max(width, 1e-6)).astype(np.float32)
        signal += band * envelope

    peak = float(np.max(np.abs(signal)))
    if peak > 0:
        signal = signal / peak * 0.9
    sf.write(str(path), signal, sample_rate, subtype="PCM_16")


def test_adtos_direct_mapping_covers_eight_classes(tmp_path: Path) -> None:
    """KD/SD/HH/OH/HT/MT/FT must each map to the expected DRUM_PARTS
    bucket --- no spectral pass should touch them."""
    audio = tmp_path / "drums.wav"
    _build_audio_with_bursts(audio, bursts=[(0.10, 8000.0, 0.001, None)])
    out = tmp_path / "out"

    backend = _ScriptedBackend(
        hits=[
            ADTHit(time_s=0.10, label="KD", confidence=0.92),
            ADTHit(time_s=0.40, label="SD", confidence=0.85),
            ADTHit(time_s=0.70, label="HH", confidence=0.78),
            ADTHit(time_s=1.00, label="OH", confidence=0.65),
            ADTHit(time_s=1.30, label="HT", confidence=0.60),
            ADTHit(time_s=1.60, label="MT", confidence=0.55),
            ADTHit(time_s=1.90, label="FT", confidence=0.55),
        ]
    )
    service = ADTDrumService(backend=backend)
    result = service.create_drum_midi(audio, out, stem_name="drums")

    events = json.loads((out / "drums_events.json").read_text(encoding="utf-8"))
    by_part = {ev["part"] for ev in events["events"]}
    # KD -> kick, SD -> snare, HH -> hihat_closed, OH -> hihat_open,
    # HT -> tom_high, MT -> tom_lomid, FT -> tom_floor.
    expected = {
        "kick", "snare", "hihat_closed", "hihat_open",
        "tom_high", "tom_lomid", "tom_floor",
    }
    assert expected <= by_part, f"missing parts: {expected - by_part}"
    # No spurious cymbal sub-classification for non-cymbal ADTOS hits.
    for part in ("crash", "china", "splash", "ride", "ride_bell"):
        assert part not in by_part, f"unexpected cymbal part: {part}"

    # Per-part files are written for every DRUM_PARTS bucket --- keeps
    # the e2e pipeline and frontend intact.
    assert set(result.part_paths.keys()) == set(DRUM_PARTS)
    assert result.combined_path.is_file()


def test_adtos_cymbal_subclassifier_picks_china_vs_ride(tmp_path: Path) -> None:
    """A CY hit on a very-high-centroid long-sustain burst -> china.
    A RD hit on a 3-5 kHz short-sustain burst -> ride_bell.
    """
    audio = tmp_path / "drums.wav"
    # Center 0.30: bright, long tail (china-like).  The envelope width
    # must be wide enough that the sustain_ratio clears the 0.45
    # threshold in _subclassify_cymbal (0.10 gives ~0.53 at 0.08s).
    # Center 1.00: short burst at 3-5 kHz (ride bell-like).
    _build_audio_with_bursts(
        audio,
        bursts=[
            (0.30, 12000.0, 0.10, 5000.0),
            (1.00, 5000.0, 0.001, 3500.0),
        ],
    )
    out = tmp_path / "out"

    backend = _ScriptedBackend(
        hits=[
            ADTHit(time_s=0.30, label="CY", confidence=0.45),
            ADTHit(time_s=1.00, label="RD", confidence=0.40),
        ]
    )
    service = ADTDrumService(backend=backend)
    service.create_drum_midi(audio, out, stem_name="drums")

    events = json.loads((out / "drums_events.json").read_text(encoding="utf-8"))
    by_time = {round(ev["t"], 2): ev["part"] for ev in events["events"]}
    assert by_time[0.30] == "china", f"expected china, got {by_time[0.30]}"
    assert by_time[1.00] == "ride_bell", f"expected ride_bell, got {by_time[1.00]}"


def test_adtos_cymbal_subclassifier_falls_back_to_default(
    tmp_path: Path,
) -> None:
    """When the cymbal sub-classifier can't pick a refinement, the
    part must collapse to the default of the coarse group (crash for
    CY, ride for RD) --- never a non-cymbal bucket."""
    audio = tmp_path / "drums.wav"
    # Build a low-energy cymbal-ish hit that has ambiguous features.
    _build_audio_with_bursts(audio, bursts=[(0.50, 5000.0, 0.01, None)])
    out = tmp_path / "out"

    backend = _ScriptedBackend(
        hits=[
            ADTHit(time_s=0.50, label="CY", confidence=0.30),
            ADTHit(time_s=1.20, label="RD", confidence=0.30),
        ]
    )
    service = ADTDrumService(backend=backend)
    service.create_drum_midi(audio, out, stem_name="drums")

    events = json.loads((out / "drums_events.json").read_text(encoding="utf-8"))
    by_time = {round(ev["t"], 2): ev["part"] for ev in events["events"]}
    assert by_time[0.50] == "crash"
    assert by_time[1.20] == "ride"


def test_adtos_service_writes_same_file_layout_as_rule_based(
    tmp_path: Path,
) -> None:
    """File names and layout must match the rule-based service so the
    e2e pipeline (frontend, download endpoints) keeps working when
    the ADTOS path is enabled."""
    audio = tmp_path / "drums.wav"
    _build_audio_with_bursts(audio, bursts=[(0.20, 6000.0, 0.001, None)])
    out = tmp_path / "out"

    backend = _ScriptedBackend(
        hits=[ADTHit(time_s=0.20, label="SD", confidence=0.7)],
    )
    service = ADTDrumService(backend=backend)
    service.create_drum_midi(audio, out, stem_name="drums")

    assert (out / "drums.mid").is_file()
    assert (out / "drums_kick.mid").is_file()
    assert (out / "drums_snare.mid").is_file()
    assert (out / "drums_events.csv").is_file()
    assert (out / "drums_events.json").is_file()


def test_adtos_label_set_is_stable() -> None:
    """Regression guard: the public ADTOS label set is part of the
    contract with the ADTOS checkpoint loader. Adding or removing a
    label requires a coordinated service update."""
    expected = ("KD", "SD", "HH", "OH", "HT", "MT", "FT", "CY", "RD")
    assert ADTOS_LABELS == expected


def test_adtos_backend_predict_is_called_with_audio_path(
    tmp_path: Path,
) -> None:
    """The service must hand the audio path to the backend verbatim
    so the backend can decide whether to load the audio or stream
    spectrograms --- we don't want the service layer to pre-process."""
    audio = tmp_path / "drums.wav"
    _build_audio_with_bursts(audio, bursts=[(0.20, 4000.0, 0.001, None)])
    out = tmp_path / "out"
    backend = _ScriptedBackend(
        hits=[ADTHit(time_s=0.20, label="KD", confidence=0.7)],
    )
    service = ADTDrumService(backend=backend)
    service.create_drum_midi(audio, out, stem_name="drums")
    assert backend.calls == [audio]
