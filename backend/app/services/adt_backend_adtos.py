"""ADTOS backend for :class:`ADTDrumService`.

This is a thin wrapper around the ADTOS Python package and its
checkpoint. ADTOS is research code that is not on PyPI, so we lazy-
import and tolerate every failure mode — missing torch, missing
package, missing checkpoint — by raising a single
:class:`ADTUnavailable` that the worker catches and falls back from.

Activation sequence (only when ``Settings.adt_enabled`` is true):

    1. ``pip install torch torchaudio`` (CPU is fine; ~1.5 GB).
    2. Clone the ADTOS repo and add it to ``adt_python_path`` (or
       install the package into the venv).
    3. Download a checkpoint and point ``adt_model_path`` at it.

If any step is missing, the backend raises ``ADTUnavailable`` on first
``predict()`` and the audio worker logs one warning then stays on the
rule-based :class:`DrumMidiService` for the rest of the process
lifetime.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .adt_drum_service import ADTHit, ADTModelBackend

logger = logging.getLogger(__name__)


class ADTUnavailable(RuntimeError):
    """Raised when the ADTOS backend cannot be initialized."""


class ADTOSBackend:
    """ADTOS Python package + checkpoint loader.

    The actual model call is intentionally abstracted away from the
    service layer: the ADTOS API has shifted between releases, and we
    want the test suite to mock ``predict()`` without dragging in
    torch at collection time.
    """

    def __init__(self, *, model_path: Path | None = None, python_path: Path | None = None) -> None:
        self._model_path = model_path
        self._python_path = python_path
        self._model = None

    def predict(self, audio_path: Path) -> list[ADTHit]:
        if self._model is None:
            self._model = self._load_model()
        if self._model is None:
            return []

        try:
            raw_predictions = self._model.predict(str(audio_path))
        except Exception as exc:  # noqa: BLE001
            raise ADTUnavailable(f"ADTOS inference failed: {exc}") from exc

        return [
            ADTHit(time_s=float(item["time"]), label=str(item["label"]).upper(),
                   confidence=float(item.get("confidence", 1.0)))
            for item in raw_predictions
        ]

    # ---- internals ----------------------------------------------------------
    def _load_model(self):
        if self._python_path is not None:
            path_str = str(self._python_path)
            if path_str not in sys.path:
                sys.path.insert(0, path_str)

        try:
            import torch  # noqa: F401  (presence check)
        except Exception as exc:  # noqa: BLE001
            raise ADTUnavailable(f"torch is not installed: {exc}") from exc

        try:
            import adtos  # type: ignore[import-not-found]  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise ADTUnavailable(
                "ADTOS package not importable. Clone https://github.com/"
                "AMAAI-Lab/ADTOS and set adt_python_path, or install "
                "the package into the venv."
            ) from exc

        if self._model_path is None or not Path(self._model_path).is_file():
            raise ADTUnavailable(
                f"ADTOS checkpoint not found at {self._model_path!r}; "
                "set adt_model_path to a downloaded checkpoint."
            )

        # The actual loader is intentionally deferred to a small helper
        # so the import-error path stays short and easy to read.
        return _build_adtos_inference(self._model_path)


def _build_adtos_inference(model_path: Path):
    """Build the ADTOS inference callable.

    The ADTOS API has changed between releases; the loader below
    targets the most common pattern (a ``load_model`` factory + a
    ``predict(audio_path)`` method). Adjust to your installed
    version. If the runtime API differs, a future maintainer only
    has to update this function.
    """
    try:
        from adtos import load_model  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        raise ADTUnavailable(f"ADTOS load_model entry point missing: {exc}") from exc

    try:
        return load_model(str(model_path))
    except Exception as exc:  # noqa: BLE001
        raise ADTUnavailable(f"ADTOS failed to load checkpoint: {exc}") from exc


__all__ = ["ADTOSBackend", "ADTUnavailable"]
