"""Meta Demucs source-separation service (Milestone 2).

Demucs splits a mixed track into stems (vocals / drums / bass / other). This
module is a thin wrapper that the worker will call from Milestone 2 onwards.
"""
from __future__ import annotations

from pathlib import Path


class DemucsService:
    """Wraps Meta's `demucs` model.

    Milestone 2: implement by shelling out to the `demucs` CLI or calling
    `demucs.api.Separator` on a saved audio file. Stems land next to the input
    file under a `<basename>/` subdirectory.
    """

    def separate(self, audio_path: Path, output_dir: Path) -> dict[str, Path]:
        """Return paths for each separated stem (vocals/drums/bass/other)."""
        raise NotImplementedError(
            "Demucs integration lands in Milestone 2"
        )
