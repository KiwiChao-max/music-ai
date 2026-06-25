"""Meta / source-separation service: Demucs (placeholder, Milestone 2)."""

from pathlib import Path


class DemucsService:
    """Wraps Meta's `demucs` model for vocal/instrument source separation.

    Milestone 2: implement by shelling out to the `demucs` CLI or calling
    `demucs.api.Separator` on a saved audio file.
    """

    def separate(self, audio_path: Path) -> dict[str, Path]:
        """Return paths for each separated stem (e.g. vocals/drums/bass/other)."""
        raise NotImplementedError
