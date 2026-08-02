"""Stem separation --- Demucs or placeholder fallback."""

from __future__ import annotations

import logging
import wave
from pathlib import Path

from app.pipeline_metrics import PIPELINE_MODEL_FALLBACK_TOTAL
from app.services import demucs_service

logger = logging.getLogger(__name__)

_PLACEHOLDER_STEMS: tuple[str, ...] = demucs_service.EXPECTED_STEMS

# Module-level service singleton.
_DEMUCS = demucs_service.DemucsService()


def _write_silent_wav(path: Path, *, seconds: float = 0.1, rate: int = 8000) -> None:
    """Write a minimal valid silent mono 8-bit WAV."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(b"\x80" * int(rate * seconds))


def emit_placeholder_stems(output_dir: Path) -> dict[str, Path]:
    stems: dict[str, Path] = {}
    for stem in _PLACEHOLDER_STEMS:
        path = output_dir / f"{stem}.wav"
        _write_silent_wav(path)
        stems[stem] = path
    return stems


def separate_stems(audio_path: Path, output_dir: Path) -> dict[str, Path]:
    """Run Demucs if available, otherwise return placeholder stems."""
    try:
        result = _DEMUCS.separate(audio_path, output_dir)
        logger.info(
            "demucs: separated %s with %s into %s",
            audio_path.name,
            result.model_name,
            output_dir,
        )
        return result.stems
    except Exception as exc:
        logger.warning("demucs unavailable; using placeholder stems: %s", exc)
        PIPELINE_MODEL_FALLBACK_TOTAL.labels(
            model="demucs",
            fallback_reason="unavailable",
        ).inc()
        return emit_placeholder_stems(output_dir)
