"""Build-time warm-up for the Basic Pitch model.

Basic Pitch lazily downloads its ONNX model the first time `predict()` is
called. On a fresh container that's a network round-trip *during* the
first user request --- slow, and the first request might time out.

Run this once at image build time (see the Dockerfile `RUN` step) to
force the model to land in the package's `model/` directory. After that
the runtime import is offline.
"""
from __future__ import annotations

import logging
import sys
import tempfile
import wave
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("warmup")


def _build_tiny_wav(path: Path) -> None:
    """Write a 0.5 s silent 16 kHz mono WAV --- the model only needs *some*
    audio to load; the content is irrelevant for warm-up."""
    rate = 16000
    seconds = 0.5
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


def main() -> int:
    from basic_pitch.inference import predict  # noqa: WPS433 --- lazy import on purpose

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "warmup.wav"
        out_dir = Path(td) / "out"
        out_dir.mkdir()
        _build_tiny_wav(wav)

        log.info("running a no-op predict() to trigger model download")
        # `predict` writes a midi file as a side effect; we don't care about
        # the result. We DO care that no exception is raised --- that's the
        # signal that the model is on disk and the ONNX runtime is happy.
        predict(
            str(wav),
            onset_threshold=0.5,
            frame_threshold=0.3,
            minimum_note_length=58.0,
            minimum_frequency=None,
            maximum_frequency=None,
            save_midi=True,
            midi_path=str(out_dir / "warmup.mid"),
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False,
        )
    log.info("basic-pitch model is ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
