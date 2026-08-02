"""Audio metadata validation --- prevent decompression-bomb attacks.

A 10 MiB FLAC file can decode to 500+ MiB of PCM data once the
worker loads it into memory.  Likewise, a 44.1 kHz / 16-bit / stereo
WAV that is 6 hours long weighs ~3.8 GiB decoded even though the
upload size is modest.  This module validates the *decoded* properties
(duration, sample rate, channel count, PCM byte count) at upload time
so the worker never has to reject a task after the fact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import soundfile as sf

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Map soundfile ``subtype`` strings to bytes-per-sample.  Anything not in
# this table is capped at 4 bytes per sample (worst-case 32-bit float).
# Derivation: ``sf.info().subtype`` returns one of the keys below.
# ---------------------------------------------------------------------------
_BYTES_PER_SAMPLE: dict[str, int] = {
    "PCM_S8": 1,
    "PCM_U8": 1,
    "PCM_16": 2,
    "PCM_24": 3,
    "PCM_32": 4,
    "FLOAT": 4,
    "DOUBLE": 8,
    "ULAW": 1,
    "ALAW": 1,
}


def _bytes_per_sample(subtype: str) -> int:
    """Return bytes per sample for a soundfile subtype string."""
    key = subtype.upper()
    return _BYTES_PER_SAMPLE.get(key, 4)


@dataclass
class AudioMetadata:
    """Extracted audio properties used for validation."""

    path: Path
    # soundfile.info() fields (all None if the file couldn't be probed).
    frames: int | None = None
    samplerate: int | None = None
    channels: int | None = None
    subtype: str | None = None
    # Derived.
    duration_seconds: float | None = None
    pcm_bytes: int | None = None
    # Validation results.
    violations: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.violations) == 0


def probe_metadata(path: Path) -> AudioMetadata:
    """Read audio metadata from an on-disk file and validate it against
    the configured limits.

    This function is designed to be called *after* the file has been
    streamed to disk (inside ``save_upload``).  It is best-effort: if
    ``soundfile`` cannot decode the format (e.g. MP3 with a libsndfile
    build that lacks MPEG support), no violations are raised and the
    upload is accepted.  The worker will re-probe later and can fail
    the task if the audio is truly malformed.

    Parameters
    ----------
    path:
        Path to the saved audio file.

    Returns
    -------
    AudioMetadata
        Struct with extracted properties and any validation violations.
    """
    meta = AudioMetadata(path=path)

    # --- probe ----------------------------------------------------------
    try:
        info = sf.info(str(path))
    except (OSError, RuntimeError):
        logger.debug(
            "audio_validation: soundfile cannot decode %s; skipping checks",
            path.name,
        )
        return meta

    if info.samplerate <= 0 or info.frames <= 0:
        logger.debug(
            "audio_validation: invalid metadata for %s (sr=%d, frames=%d)",
            path.name,
            info.samplerate,
            info.frames,
        )
        return meta

    meta.frames = info.frames
    meta.samplerate = info.samplerate
    meta.channels = info.channels
    meta.subtype = info.subtype

    meta.duration_seconds = round(info.frames / float(info.samplerate), 2)
    meta.pcm_bytes = info.frames * info.channels * _bytes_per_sample(info.subtype)

    # --- validate against limits ----------------------------------------
    _check_duration(meta)
    _check_sample_rate(meta)
    _check_channels(meta)
    _check_pcm_size(meta)

    if meta.violations:
        logger.warning(
            "audio_validation: %s rejected: %s",
            path.name,
            "; ".join(meta.violations),
        )

    return meta


def _check_duration(meta: AudioMetadata) -> None:
    limit = settings.max_audio_duration_seconds
    if limit <= 0 or meta.duration_seconds is None:
        return
    if meta.duration_seconds > limit:
        meta.violations.append(f"duration {meta.duration_seconds:.0f}s exceeds limit of {limit}s")


def _check_sample_rate(meta: AudioMetadata) -> None:
    limit = settings.max_audio_sample_rate
    if limit <= 0 or meta.samplerate is None:
        return
    if meta.samplerate > limit:
        meta.violations.append(f"sample rate {meta.samplerate} Hz exceeds limit of {limit} Hz")


def _check_channels(meta: AudioMetadata) -> None:
    limit = settings.max_audio_channels
    if limit <= 0 or meta.channels is None:
        return
    if meta.channels > limit:
        meta.violations.append(f"channel count {meta.channels} exceeds limit of {limit}")


def _check_pcm_size(meta: AudioMetadata) -> None:
    limit = settings.max_audio_pcm_bytes
    if limit <= 0 or meta.pcm_bytes is None:
        return
    if meta.pcm_bytes > limit:
        meta.violations.append(
            f"decoded PCM size {_human_bytes(meta.pcm_bytes)} "
            f"exceeds limit of {_human_bytes(limit)}"
        )


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KiB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MiB"
    return f"{n / (1024 * 1024 * 1024):.1f} GiB"
