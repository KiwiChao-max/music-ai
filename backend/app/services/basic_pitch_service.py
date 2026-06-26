"""Spotify Basic Pitch audio-to-MIDI service.

Basic Pitch is a polyphonic note tracker (Spotify, Apache-2.0) that runs an
ONNX model on a mono audio signal and returns:

  * a `.mid` Standard MIDI File (Format 1) with one note per detected event
  * a `note_events` array of `(start_time_s, end_time_s, pitch_midi, velocity, [pitch_bend])`

This module is a thin wrapper around `basic_pitch.inference.predict`. The
heavy lifting (ONNX inference, MIDI quantization) happens in the library;
we only deal with file paths and progress-friendly parameters.

Output layout (per task):
    <output_dir>/
        <basename>.mid         # the MIDI file
        <basename>_notes.csv   # one row per note: start,end,pitch,velocity,bend
        <basename>.npz         # raw model output (onset/frame/contour), optional

The CSV sidecar is what the e2e test reads to assert "yes, notes were
detected" without having to parse the .mid binary.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BasicPitchResult:
    """Paths produced by `transcribe`. All under `output_dir`."""

    midi_path: Path
    notes_csv_path: Path
    note_count: int


class BasicPitchService:
    """Wraps Spotify's `basic-pitch` model.

    The model is shipped as an ONNX file inside the `basic-pitch` package and
    is loaded on first use. A successful run writes the MIDI file plus a
    CSV sidecar to `output_dir`.
    """

    # Default transcription parameters. Tuned for clean polyphonic piano /
    # guitar-ish material. Drums need separate handling (use the Demucs
    # `drums` stem + a post-processing pass that snaps to GM kit pitches).
    DEFAULT_ONSET_THRESHOLD = 0.5
    DEFAULT_FRAME_THRESHOLD = 0.3
    DEFAULT_MIN_NOTE_LENGTH_MS = 58.0
    DEFAULT_MIN_FREQUENCY = 27.5   # A0 — anything below is sub-audio
    DEFAULT_MAX_FREQUENCY = 4186.0  # C8 — cover the piano range

    def transcribe(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        onset_threshold: float = DEFAULT_ONSET_THRESHOLD,
        frame_threshold: float = DEFAULT_FRAME_THRESHOLD,
        min_note_length_ms: float = DEFAULT_MIN_NOTE_LENGTH_MS,
        min_frequency: float | None = DEFAULT_MIN_FREQUENCY,
        max_frequency: float | None = DEFAULT_MAX_FREQUENCY,
    ) -> BasicPitchResult:
        """Run Basic Pitch on `audio_path` and write MIDI + CSV to `output_dir`.

        `audio_path` is typically a stem WAV produced by Demucs, or the
        original mix if Demucs is not used. Basic Pitch will resample /
        downmix internally; the caller does not need to pre-process.

        Returns a `BasicPitchResult` pointing at the produced files.
        Raises whatever the underlying library raises (file not found,
        unsupported codec, ONNX runtime errors, ...).
        """
        # Imported lazily so the rest of the app can still import this module
        # in environments where `basic-pitch` is not installed (e.g. CI for
        # non-M2 jobs). The worker is the only place that calls transcribe().
        from basic_pitch.inference import predict

        output_dir.mkdir(parents=True, exist_ok=True)
        # Use a single basename for all artifacts so the relation is obvious
        # on disk: `piano.mid`, `piano_notes.csv`, `piano.npz`.
        basename = audio_path.stem
        midi_path = output_dir / f"{basename}.mid"
        notes_csv_path = output_dir / f"{basename}_notes.csv"

        logger.info(
            "basic-pitch: transcribing %s -> %s (onset=%.2f frame=%.2f min_len=%.0fms)",
            audio_path.name,
            midi_path.name,
            onset_threshold,
            frame_threshold,
            min_note_length_ms,
        )

        # `predict` returns (model_output, midi_data, note_events).
        # We only need the side effects (midi file written) and note_events
        # for the CSV. midi_data is the pretty_midi.PrettyMIDI object.
        _, _midi_data, note_events = predict(
            str(audio_path),
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            minimum_note_length=min_note_length_ms,
            minimum_frequency=min_frequency,
            maximum_frequency=max_frequency,
            # Disable CLI-style side artifacts; we want control of paths.
            save_midi=True,
            midi_path=str(midi_path),
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False,
        )

        # Note: `note_events` is a list of (start, end, pitch, velocity, [bend])
        # tuples (numpy array-like). Write a CSV so the e2e / frontend can
        # inspect note timings without parsing the .mid binary.
        note_count = self._write_notes_csv(note_events, notes_csv_path)

        logger.info(
            "basic-pitch: wrote %s (%d notes) and %s",
            midi_path.name,
            note_count,
            notes_csv_path.name,
        )
        return BasicPitchResult(
            midi_path=midi_path,
            notes_csv_path=notes_csv_path,
            note_count=note_count,
        )

    @staticmethod
    def _write_notes_csv(note_events, csv_path: Path) -> int:
        """Write one CSV row per detected note. Returns the note count.

        `note_events` is a numpy structured array with fields
        (start_time_s, end_time_s, pitch_midi, velocity, pitch_bend).
        We only persist the first 5 columns; pitch_bend is per-frame and
        is already baked into the .mid as pitch wheel events.
        """
        # Convert to a plain Python iterable of tuples. The shape varies
        # slightly between basic-pitch versions, so be defensive.
        rows = []
        for ev in note_events:
            # Newer versions: (start_time_s, end_time_s, pitch_midi, velocity, pitch_bend)
            # Older versions: (start_time_s, end_time_s, pitch_midi, velocity)
            try:
                start, end, pitch, vel = ev[0], ev[1], ev[2], ev[3]
            except (IndexError, TypeError, ValueError):
                continue
            rows.append(
                (
                    f"{float(start):.6f}",
                    f"{float(end):.6f}",
                    int(pitch),
                    int(vel),
                )
            )

        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["start_time_s", "end_time_s", "pitch_midi", "velocity"])
            writer.writerows(rows)
        return len(rows)
