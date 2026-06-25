"""Spotify Basic Pitch audio-to-MIDI service (placeholder, Milestone 2)."""

from pathlib import Path


class BasicPitchService:
    """Wraps Spotify's `basic-pitch` model.

    Milestone 2: implement by shelling out to the `basic-pitch` CLI or calling
    the Python API (`basic_pitch.inference.predict`) on a saved audio file.
    """

    def transcribe(self, audio_path: Path) -> Path:
        """Return the path to the generated MIDI file."""
        raise NotImplementedError
