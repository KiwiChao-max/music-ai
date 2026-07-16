"""MIDI transcription --- drums + melodic stems.

Routes drum transcription through ADTOS when enabled, falling back to
the rule-based ``DrumMidiService``.  Melodic stems are transcribed with
Basic Pitch.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.services import (
    adt_drum_service,
    basic_pitch_service,
    drum_midi_service,
)
from app.services.adt_backend_adtos import ADTUnavailable

logger = logging.getLogger(__name__)

# Module-level service singletons.
_BASIC_PITCH = basic_pitch_service.BasicPitchService()
_DRUM_MIDI_RULE = drum_midi_service.DrumMidiService()
_ADT_DRUM: adt_drum_service.ADTDrumService | None = None
_ADT_DRUM_WARNED = False

# Stems that Basic Pitch should transcribe directly.
_MELODIC_STEMS: tuple[str, ...] = ("bass", "piano", "guitar", "other", "vocals")


def _get_drum_midi_service():
    """Return the ADTOS-backed service if enabled, else the rule-based one."""
    global _ADT_DRUM, _ADT_DRUM_WARNED
    if not settings.adt_enabled:
        return _DRUM_MIDI_RULE
    if _ADT_DRUM is None:
        _ADT_DRUM = adt_drum_service.ADTDrumService(
            model_path=settings.adt_model_path,
            cymbal_confidence_threshold=settings.adt_cymbal_confidence_threshold,
        )
    return _ADT_DRUM


def _disable_adt_drum(message: str) -> None:
    global _ADT_DRUM, _ADT_DRUM_WARNED
    if not _ADT_DRUM_WARNED:
        logger.warning(message)
        _ADT_DRUM_WARNED = True
    _ADT_DRUM = None


def _run_basic_pitch(audio_path: Path, output_dir: Path) -> Path:
    result = _BASIC_PITCH.transcribe(audio_path, output_dir)
    return result.midi_path


def _run_drum_midi(stem_path: Path, output_dir: Path) -> Path | None:
    """Generate GM drum MIDI for the drum stem."""
    service = _get_drum_midi_service()
    try:
        result = service.create_drum_midi(stem_path, output_dir, stem_name="drums")
    except ADTUnavailable as exc:
        _disable_adt_drum(f"ADTOS unavailable, falling back to rule-based: {exc}")
        result = _DRUM_MIDI_RULE.create_drum_midi(
            stem_path, output_dir, stem_name="drums"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "drum-midi (%s) failed for %s: %s",
            type(service).__name__,
            stem_path.name,
            exc,
        )
        result = _DRUM_MIDI_RULE.create_drum_midi(
            stem_path, output_dir, stem_name="drums"
        )
    if result.event_count == 0:
        logger.warning("drum-midi produced no hits for %s", stem_path.name)
    return result.combined_path


def transcribe_stems_or_mix(
    audio_path: Path,
    output_dir: Path,
    stems: dict[str, Path],
) -> list[Path]:
    """Transcribe drums separately, then melodic stems or the original mix."""
    real_stems = [path for path in stems.values() if path.stat().st_size > 1024]
    if not real_stems:
        return [_run_basic_pitch(audio_path, output_dir)]

    midi_paths: list[Path] = []
    drum_stem = stems.get("drums")
    if (
        drum_stem is not None
        and drum_stem.is_file()
        and drum_stem.stat().st_size > 1024
    ):
        try:
            drum_midi_path = _run_drum_midi(drum_stem, output_dir)
            if drum_midi_path is not None:
                midi_paths.append(drum_midi_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("drum-midi failed for stem drums: %s", exc)

    for stem_name in _MELODIC_STEMS:
        stem_path = stems.get(stem_name)
        if (
            stem_path is None
            or not stem_path.is_file()
            or stem_path.stat().st_size <= 1024
        ):
            continue
        try:
            midi_paths.append(_run_basic_pitch(stem_path, output_dir))
        except Exception as exc:  # noqa: BLE001
            logger.warning("audio-to-midi failed for stem %s: %s", stem_name, exc)

    if not midi_paths:
        midi_paths.append(_run_basic_pitch(audio_path, output_dir))
    return midi_paths