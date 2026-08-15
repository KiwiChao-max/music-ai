"""Meta Demucs source-separation service.

The default model is the 6-stem variant (``htdemucs_6s``), which natively
outputs vocals, drums, bass, piano, guitar and other --- the piano/guitar
stems replace the rule-based instrument classifier for those two
instruments, giving much cleaner separation when several melodic
instruments overlap. The classifier still runs on the residual
``other`` stem to pull out strings / synth / other_melodic.

The service prefers a pure Python path for local WAV processing so the
app can run the model without a system FFmpeg install. The CLI path is
retained as a fallback for formats that soundfile cannot decode.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

EXPECTED_STEMS: tuple[str, ...] = (
    "vocals",
    "drums",
    "bass",
    "piano",
    "guitar",
    "other",
)

# Module-level model cache: (model_name, device) -> loaded Demucs model.
# ``get_model()`` rebuilds the full network from the checkpoint on every
# call (~2 GiB RSS and tens of seconds), so we keep the model resident
# for the lifetime of the worker child and reuse it across tasks.
# Celery prefork children process one task at a time, so contention is
# not an issue; the lock only guards against a threaded caller racing
# the initial load. The worker memory gates (``worker_max_memory_per_child``
# etc.) still bound the worst-case footprint and recycle bloated children.
_MODEL_CACHE: dict[tuple[str, str], Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _get_cached_model(model_name: str, device: object) -> Any:
    """Return a cached Demucs model, loading it once per (name, device)."""
    from demucs.pretrained import get_model

    key = (model_name, str(device))
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
        if cached is None:
            logger.info("demucs: loading model %s on %s (first use)", model_name, device)
            cached = get_model(model_name).to(device)
            _MODEL_CACHE[key] = cached
    return cached


def _resolve_torch_device():
    """Pick the torch device for Demucs.

    ``DEMUCS_DEVICE`` accepts ``auto`` (default: use CUDA when present),
    ``cuda`` (fall back to CPU with a warning when unavailable), or
    ``cpu`` (force CPU, e.g. to reserve the GPU for another model).
    """
    import torch

    from app.config import settings

    requested = settings.demucs_device
    if requested == "cuda":
        if torch.cuda.is_available():
            return torch.device("cuda")
        logger.warning("demucs: DEMUCS_DEVICE=cuda but CUDA is unavailable; using CPU")
        return torch.device("cpu")
    if requested == "cpu":
        return torch.device("cpu")
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


@dataclass(frozen=True)
class DemucsResult:
    stems: dict[str, Path]
    model_name: str


class DemucsService:
    """Runs Meta's Demucs model and normalizes its output layout."""

    def __init__(self, *, model_name: str = "htdemucs_6s", timeout_seconds: int = 900) -> None:
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds

    def separate(self, audio_path: Path, output_dir: Path) -> DemucsResult:
        """Return paths for each separated stem under `output_dir`."""
        output_dir.mkdir(parents=True, exist_ok=True)
        if not audio_path.is_file():
            raise FileNotFoundError(f"audio file not found: {audio_path}")

        try:
            return self._separate_with_python_api(audio_path, output_dir)
        except Exception as exc:
            logger.warning("demucs python-api path failed; trying CLI fallback: %s", exc)
            from app.pipeline_metrics import PIPELINE_MODEL_FALLBACK_TOTAL

            PIPELINE_MODEL_FALLBACK_TOTAL.labels(
                model="demucs",
                fallback_reason="python_api_to_cli",
            ).inc()
            return self._separate_with_cli(audio_path, output_dir)

    def _separate_with_python_api(self, audio_path: Path, output_dir: Path) -> DemucsResult:
        import numpy as np
        import soundfile as sf
        import torch
        from demucs.apply import apply_model
        from demucs.audio import convert_audio

        data, sample_rate = sf.read(str(audio_path), always_2d=True, dtype="float32")
        if data.size == 0:
            raise RuntimeError("audio file is empty")

        device = _resolve_torch_device()
        model = _get_cached_model(self.model_name, device)
        wav = torch.from_numpy(data.T).float()
        wav = convert_audio(wav, sample_rate, model.samplerate, model.audio_channels)

        ref_mean = wav.mean()
        ref_std = wav.std()
        wav = (wav - ref_mean) / ref_std if float(ref_std) > 1e-08 else wav - ref_mean

        with torch.no_grad():
            sources = apply_model(
                model,
                wav.unsqueeze(0),
                shifts=0,
                split=True,
                overlap=0.25,
                progress=False,
                device=device,
                num_workers=0,
            )[0]

        sources = sources * ref_std + ref_mean if float(ref_std) > 1e-08 else sources + ref_mean

        stems: dict[str, Path] = {}
        sources_names = tuple(getattr(model, "sources", EXPECTED_STEMS))
        for source, name in zip(sources, sources_names, strict=False):
            stem_name = str(name).lower()
            if stem_name not in EXPECTED_STEMS:
                continue
            target = output_dir / f"{stem_name}.wav"
            audio = source.detach().cpu().numpy().T
            audio = np.clip(audio, -1.0, 1.0)
            sf.write(str(target), audio, int(model.samplerate), subtype="PCM_16")
            stems[stem_name] = target

        missing = [stem for stem in EXPECTED_STEMS if stem not in stems]
        if missing:
            raise RuntimeError(f"demucs python output missing stems: {', '.join(missing)}")

        logger.info("demucs: separated %s using local Python API on %s", audio_path.name, device)
        return DemucsResult(stems=stems, model_name=self.model_name)

    def _separate_with_cli(self, audio_path: Path, output_dir: Path) -> DemucsResult:
        with tempfile.TemporaryDirectory(prefix="demucs_", dir=str(output_dir)) as temp_dir:
            temp_path = Path(temp_dir)
            command = [
                sys.executable,
                "-m",
                "demucs.separate",
                "-n",
                self.model_name,
                "--out",
                str(temp_path),
                str(audio_path),
            ]
            logger.info("demucs: running %s", " ".join(command))
            try:
                completed = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=_subprocess_env_with_ffmpeg(),
                )
            except ModuleNotFoundError as exc:
                raise RuntimeError("demucs is not installed") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("demucs separation timed out") from exc
            except subprocess.CalledProcessError as exc:
                detail = (exc.stderr or exc.stdout or str(exc)).strip()
                raise RuntimeError(f"demucs separation failed: {detail}") from exc

            if completed.stderr:
                logger.debug("demucs stderr: %s", completed.stderr.strip())
            stems = self._find_stems(temp_path)
            missing = [stem for stem in EXPECTED_STEMS if stem not in stems]
            if missing:
                raise RuntimeError(f"demucs output missing stems: {', '.join(missing)}")

            normalized: dict[str, Path] = {}
            for stem in EXPECTED_STEMS:
                target = output_dir / f"{stem}.wav"
                shutil.copy2(stems[stem], target)
                normalized[stem] = target

        return DemucsResult(stems=normalized, model_name=self.model_name)

    @staticmethod
    def _find_stems(search_root: Path) -> dict[str, Path]:
        stems: dict[str, Path] = {}
        for wav_path in search_root.rglob("*.wav"):
            stem_name = wav_path.stem.lower()
            if stem_name in EXPECTED_STEMS and stem_name not in stems:
                stems[stem_name] = wav_path
        return stems


def _subprocess_env_with_ffmpeg() -> dict[str, str]:
    """Pass a PATH that includes common system FFmpeg install locations."""
    env = os.environ.copy()
    path_entries = [
        r"C:\ProgramData\chocolatey\bin",
        r"C:\tools\ffmpeg\bin",
    ]
    existing = env.get("PATH", "")
    env["PATH"] = os.pathsep.join([*path_entries, existing])
    return env
