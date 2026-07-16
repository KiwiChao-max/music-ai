"""ADTOS-style automatic drum transcription service.

This is an opt-in alternative to the rule-based ``DrumMidiService``. It
delegates onset detection + per-class probabilities to a trained drum
transcription model (ADTOS, https://github.com/AMAAI-Lab/ADTOS) and
then maps the model's coarse 9-class output to our internal 19-part
``DRUM_PARTS`` layout. Cymbal hits (the ADTOS ``CY`` / ``RD`` labels)
are further sub-classified into crash / ride / china / splash /
ride_bell via a light spectral pass --- that is the only place spectral
heuristics remain in this path, and it is much smaller than the full
19-way rule chain in ``DrumMidiService``.

Why a sub-classifier for cymbals? ADTOS only distinguishes
crash-vs-ride at the coarse level. Drummers expect ``china``,
``splash`` and ``ride_bell`` to be editable in a DAW, so the existing
``DRUM_PARTS`` (and the frontend drum split UI) keep all 19 buckets.
Sub-classifying with a 5-way spectral pass on the high-band content
of a cymbal onset is a good trade-off: it preserves the existing
file layout, the e2e tests, and the frontend, while ADTOS handles
the hard problem (kick vs snare vs toms vs coarse cymbal in the
face of overlapping spectra).

Activation is feature-flagged: ``Settings.adt_enabled`` (default
``False``). When the flag is off, callers fall back to
``DrumMidiService``. The model is loaded lazily on first use; any
failure (missing checkpoint, missing torch, inference exception)
propagates so the worker can fall back and log a single warning.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .drum_midi_service import (
    DRUM_PARTS,
    DrumHit,
    DrumMidiResult,
    DrumMidiService,
    _GM_DRUM_NOTES,
    _NOTE_LENGTHS_SECONDS,
)
from .midi_cc import velocity_from_strength

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ADTOS 9-class label set (paper standard).
# Mapped to our 19-part layout below; CY/RD are further sub-classified.
# ---------------------------------------------------------------------------
ADTOS_LABELS: tuple[str, ...] = (
    "KD",  # Kick
    "SD",  # Snare
    "HH",  # Hi-Hat closed
    "OH",  # Open Hi-Hat
    "HT",  # High Tom
    "MT",  # Mid Tom
    "FT",  # Floor Tom
    "CY",  # Crash (coarse --- refined to crash/china/splash)
    "RD",  # Ride  (coarse --- refined to ride/ride_bell)
)

# Direct KD/SD/HH/OH/HT/MT/FT mapping. Cymbal labels are handled by
# ``_subclassify_cymbal`` below.
_ADTOS_DIRECT_MAP: dict[str, str] = {
    "KD": "kick",
    "SD": "snare",
    "HH": "hihat_closed",
    "OH": "hihat_open",
    "HT": "tom_high",
    "MT": "tom_lomid",
    "FT": "tom_floor",
}

# Cymbal sub-classifier output space. Crash-group and ride-group are
# decided by the coarse ADTOS label; the spectral pass only picks
# within the group.
_CRASH_GROUP: tuple[str, ...] = ("crash", "china", "splash")
_RIDE_GROUP: tuple[str, ...] = ("ride", "ride_bell")


@dataclass(frozen=True)
class ADTHit:
    """A single onset predicted by the ADTOS-style model.

    ``label`` is one of ``ADTOS_LABELS``; ``confidence`` is the model's
    top-1 probability for that label (0..1).
    """

    time_s: float
    label: str
    confidence: float


class ADTModelBackend(Protocol):
    """Pluggable backend for the ADTOS-style model.

    The default ``ADTOSBackend`` tries to import the ``adtos`` package
    and load a checkpoint. Tests can drop in a ``MockADTBackend`` that
    returns canned predictions.
    """

    def predict(self, audio_path: Path) -> list[ADTHit]:
        ...


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
class ADTDrumService:
    """End-to-end drum MIDI generator backed by an ADT-style model."""

    def __init__(
        self,
        *,
        backend: ADTModelBackend | None = None,
        model_path: Path | None = None,
        sample_rate: int = 22050,
        default_bpm: float = 120.0,
        cymbal_confidence_threshold: float = 0.6,
    ) -> None:
        self.sample_rate = sample_rate
        self.default_bpm = default_bpm
        self.cymbal_confidence_threshold = cymbal_confidence_threshold
        # Lazy default backend --- only constructed on first use so a
        # worker that never enables ADT never imports torch.
        self._backend: ADTModelBackend | None = backend
        self._model_path = model_path
        self._warned = False

    def _get_backend(self) -> ADTModelBackend:
        if self._backend is not None:
            return self._backend
        # Local import keeps torch out of the cold path.
        from .adt_backend_adtos import ADTOSBackend

        self._backend = ADTOSBackend(model_path=self._model_path)
        return self._backend

    def create_drum_midi(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        stem_name: str = "drums",
    ) -> DrumMidiResult:
        """Run the model and write the same files ``DrumMidiService`` does."""
        output_dir.mkdir(parents=True, exist_ok=True)
        y, sr = self._load_audio(audio_path)
        bpm = DrumMidiService()._estimate_bpm(y, sr)

        backend = self._get_backend()
        adt_hits = backend.predict(audio_path)

        # Map ADTOS labels to our 19-part layout. Cymbal hits are refined
        # with a spectral pass when the model confidence is below the
        # configured threshold (or unconditionally --- the sub-classifier
        # is cheap and gives china/splash/ride_bell coverage).
        drum_hits: list[DrumHit] = []
        for adt in adt_hits:
            if adt.label not in ADTOS_LABELS:
                if not self._warned:
                    logger.warning(
                        "ADTOS returned unknown label %r --- ignoring", adt.label,
                    )
                    self._warned = True
                continue
            part = self._resolve_part(y, sr, adt)
            midi_note = _GM_DRUM_NOTES.get(part, _GM_DRUM_NOTES["snare"])
            duration = _NOTE_LENGTHS_SECONDS.get(part, 0.09)
            # Velocity is not part of the ADTOS contract; we derive it
            # from the model's confidence so soft predictions produce
            # softer hits in the GM velocity curve.
            velocity = velocity_from_strength(adt.confidence)
            drum_hits.append(
                DrumHit(
                    time_s=float(adt.time_s),
                    part=part,
                    midi_note=midi_note,
                    velocity=velocity,
                    confidence=float(adt.confidence),
                )
            )

        # Fill overlay is model-agnostic --- reuse the rule-based helper
        # so a dense ADTOS burst still shows up as an editable fill bus.
        drum_hits = DrumMidiService._derive_fills(drum_hits)

        combined_path = output_dir / f"{stem_name}.mid"
        part_paths = {part: output_dir / f"{stem_name}_{part}.mid" for part in DRUM_PARTS}
        events_csv_path = output_dir / f"{stem_name}_events.csv"
        events_json_path = output_dir / f"{stem_name}_events.json"

        DrumMidiService._write_midi(
            combined_path, drum_hits, bpm=bpm, track_name=f"{stem_name}: GM drum kit (ADTOS)"
        )
        part_counts: dict[str, int] = {part: 0 for part in DRUM_PARTS}
        for part, part_path in part_paths.items():
            if part == "fill":
                part_hits = [hit for hit in drum_hits if hit.part == "fill"]
            else:
                part_hits = [hit for hit in drum_hits if hit.part == part]
            part_counts[part] = len(part_hits)
            DrumMidiService._write_midi(
                part_path,
                part_hits,
                bpm=bpm,
                track_name=f"{stem_name}: {part}",
            )

        DrumMidiService._write_events_csv(events_csv_path, drum_hits)
        DrumMidiService._write_events_json(events_json_path, drum_hits, bpm=bpm)

        logger.info(
            "adt-drum-midi: wrote %s plus %d part MIDI file(s), hits=%d bpm=%.1f",
            combined_path.name,
            len(part_paths),
            len(drum_hits),
            bpm,
        )
        return DrumMidiResult(
            combined_path=combined_path,
            part_paths=part_paths,
            events_csv_path=events_csv_path,
            event_count=len(drum_hits),
            bpm=bpm,
            part_counts=part_counts,
        )

    # ---- internal helpers ---------------------------------------------------
    def _load_audio(self, audio_path: Path):
        import librosa

        if not audio_path.is_file():
            raise FileNotFoundError(f"drum audio file not found: {audio_path}")
        y, sr = librosa.load(str(audio_path), sr=self.sample_rate, mono=True)
        return y, sr

    def _resolve_part(self, y, sr: int, adt: ADTHit) -> str:
        """Map an ADTOS hit to a 19-part bucket, refining cymbals via
        spectral features when needed."""
        if adt.label in _ADTOS_DIRECT_MAP:
            return _ADTOS_DIRECT_MAP[adt.label]

        # Coarse cymbal: refine into the appropriate group. When the
        # model is highly confident, we still want china/splash/
        # ride_bell coverage --- the sub-classifier is cheap and the
        # existing 19-part layout depends on it.
        try:
            return self._subclassify_cymbal(y, sr, adt.time_s, coarse=adt.label)
        except Exception as exc:  # noqa: BLE001 - keep the pipeline alive
            logger.debug("cymbal sub-classifier failed at t=%.3fs: %s", adt.time_s, exc)
            return "crash" if adt.label == "CY" else "ride"

    def _subclassify_cymbal(
        self,
        y,
        sr: int,
        time_s: float,
        *,
        coarse: str,
    ) -> str:
        """Refine a coarse ``CY``/``RD`` ADTOS label into one of
        ``crash`` / ``china`` / ``splash`` / ``ride`` / ``ride_bell``.

        The classifier is intentionally simple --- three spectral cues
        (centroid, sustain_ratio, very-high ratio) and a coarse lookup.
        ADTOS does the hard work (onset detection, kick/snare/hat/tom
        separation); this pass only differentiates among cymbal types
        that share the same high-band onset.
        """
        import numpy as np

        group = _CRASH_GROUP if coarse == "CY" else _RIDE_GROUP
        # Default --- collapses to the most common cymbal in the group.
        default = "crash" if coarse == "CY" else "ride"

        if y.size == 0:
            return default

        start = max(0, int(time_s * sr))
        end = min(len(y), start + int(0.20 * sr))
        window = y[start:end]
        if window.size < 16:
            return default

        shaped = window * np.hanning(window.size)
        spectrum = np.abs(np.fft.rfft(shaped))
        freqs = np.fft.rfftfreq(shaped.size, d=1.0 / sr)
        total = float(np.sum(spectrum)) + 1e-9

        centroid = float(np.sum(freqs * spectrum) / total)
        very_high_ratio = float(np.sum(spectrum[freqs >= 7500.0]) / total)
        high_ratio = float(np.sum(spectrum[(freqs >= 3500.0)]) / total)

        early_end = min(window.size, max(1, int(0.04 * sr)))
        early_energy = float(np.sqrt(np.mean(np.square(window[:early_end]))))
        late_start = min(window.size, int(0.08 * sr))
        late = window[late_start:]
        late_energy = float(np.sqrt(np.mean(np.square(late))) if late.size else 0.0)
        sustain_ratio = late_energy / (early_energy + 1e-9)

        if coarse == "CY":
            # Splash: short and bright, decay within ~150ms.
            if 0.10 <= sustain_ratio <= 0.32 and centroid > 5500.0:
                return "splash"
            # China: very high centroid + long sustain.
            if centroid > 6000.0 and sustain_ratio > 0.45:
                return "china"
            # Crash: default for the crash group.
            return "crash"

        # coarse == "RD": pick between ride and ride_bell.
        # ride_bell is a short burst centred around 3-5kHz with very
        # little sustain (the bell dies fast, no wash); ride is a
        # longer wash above 3kHz.
        peak_freq = float(freqs[int(np.argmax(spectrum))]) if spectrum.size else 0.0
        if 3000.0 < peak_freq < 5500.0 and sustain_ratio < 0.18 and high_ratio > 0.4:
            return "ride_bell"
        return "ride"


__all__ = [
    "ADTOS_LABELS",
    "ADTHit",
    "ADTModelBackend",
    "ADTDrumService",
]
