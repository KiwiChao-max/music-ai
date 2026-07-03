"""Audio-to-MIDI service with Basic Pitch first and a local fallback.

Basic Pitch remains the preferred polyphonic transcription engine. In local
Python 3.12 environments it can be unavailable because its TensorFlow range is
not compatible, so this module falls back to a lightweight librosa.pyin based
monophonic transcription. The fallback is useful for bass, vocal and simple
melodic stems; production polyphonic quality still comes from Basic Pitch.
"""
from __future__ import annotations

import csv
import logging
import math
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
    """Wrap audio-to-MIDI transcription behind a stable worker API."""

    DEFAULT_ONSET_THRESHOLD = 0.5
    DEFAULT_FRAME_THRESHOLD = 0.3
    DEFAULT_MIN_NOTE_LENGTH_MS = 58.0
    DEFAULT_MIN_FREQUENCY = 27.5
    DEFAULT_MAX_FREQUENCY = 4186.0

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
        """Run audio-to-MIDI and write MIDI + CSV note sidecar."""
        try:
            return self._transcribe_with_basic_pitch(
                audio_path,
                output_dir,
                onset_threshold=onset_threshold,
                frame_threshold=frame_threshold,
                min_note_length_ms=min_note_length_ms,
                min_frequency=min_frequency,
                max_frequency=max_frequency,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            logger.warning("basic-pitch unavailable; using librosa fallback: %s", exc)
            return self._transcribe_with_librosa(
                audio_path,
                output_dir,
                min_note_length_ms=min_note_length_ms,
                min_frequency=min_frequency,
                max_frequency=max_frequency,
            )

    def _transcribe_with_basic_pitch(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        onset_threshold: float,
        frame_threshold: float,
        min_note_length_ms: float,
        min_frequency: float | None,
        max_frequency: float | None,
    ) -> BasicPitchResult:
        from basic_pitch.inference import predict

        output_dir.mkdir(parents=True, exist_ok=True)
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

        _, _midi_data, note_events = predict(
            str(audio_path),
            onset_threshold=onset_threshold,
            frame_threshold=frame_threshold,
            minimum_note_length=min_note_length_ms,
            minimum_frequency=min_frequency,
            maximum_frequency=max_frequency,
            save_midi=True,
            midi_path=str(midi_path),
            sonify_midi=False,
            save_model_outputs=False,
            save_notes=False,
        )
        note_count = self._write_notes_csv(note_events, notes_csv_path)

        # Inject GM setup + expressive CCs into the Basic Pitch output.
        # Basic Pitch itself only writes raw note_on/note_off — without
        # this post-processing the melodic MIDI files are missing bank
        # select, program change, volume/expression/pan, and per-stem
        # expressive controllers (brightness, reverb, chorus).
        stem_key = audio_path.stem.lower()
        self._inject_gm_setup(midi_path, stem_key)

        logger.info("basic-pitch: wrote %s (%d notes)", midi_path.name, note_count)
        return BasicPitchResult(midi_path=midi_path, notes_csv_path=notes_csv_path, note_count=note_count)

    def _transcribe_with_librosa(
        self,
        audio_path: Path,
        output_dir: Path,
        *,
        min_note_length_ms: float,
        min_frequency: float | None,
        max_frequency: float | None,
    ) -> BasicPitchResult:
        import librosa
        import numpy as np

        output_dir.mkdir(parents=True, exist_ok=True)
        basename = audio_path.stem
        midi_path = output_dir / f"{basename}.mid"
        notes_csv_path = output_dir / f"{basename}_notes.csv"

        sr = 22050
        hop_length = 256
        y, sr = librosa.load(str(audio_path), sr=sr, mono=True)
        if y.size == 0 or float(np.max(np.abs(y))) < 1e-5:
            note_events: list[tuple[float, float, int, int]] = []
        else:
            fmin = max(20.0, float(min_frequency or self.DEFAULT_MIN_FREQUENCY))
            fmax = min(float(max_frequency or 2093.0), (sr / 2.0) * 0.95)
            f0, voiced_flag, voiced_prob = librosa.pyin(
                y,
                fmin=fmin,
                fmax=fmax,
                sr=sr,
                frame_length=2048,
                hop_length=hop_length,
                fill_na=float("nan"),
            )
            times = librosa.frames_to_time(range(len(f0)), sr=sr, hop_length=hop_length)
            rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop_length)[0]
            note_events = _notes_from_f0(
                f0=f0,
                voiced_flag=voiced_flag,
                voiced_prob=voiced_prob,
                times=times,
                rms=rms,
                min_note_length_s=max(0.04, min_note_length_ms / 1000.0),
            )

        self._write_midi(note_events, midi_path)
        note_count = self._write_notes_csv(note_events, notes_csv_path)
        logger.info("librosa-midi: wrote %s (%d notes)", midi_path.name, note_count)
        return BasicPitchResult(midi_path=midi_path, notes_csv_path=notes_csv_path, note_count=note_count)

    @staticmethod
    def _write_midi(note_events, midi_path: Path, *, stem_name: str = "librosa transcription") -> None:
        from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo, second2tick

        from app.services.midi_cc import gm_setup_messages, pitch_bend_message

        ticks_per_beat = 480
        tempo = bpm2tempo(120)
        midi = MidiFile(type=1, ticks_per_beat=ticks_per_beat)

        meta_track = MidiTrack()
        meta_track.append(MetaMessage("track_name", name="tempo", time=0))
        meta_track.append(MetaMessage("set_tempo", tempo=tempo, time=0))
        meta_track.append(MetaMessage("end_of_track", time=0))
        midi.tracks.append(meta_track)

        note_track = MidiTrack()
        note_track.append(MetaMessage("track_name", name=stem_name, time=0))
        # Channel 0 for the default Acoustic Grand Piano program. The setup
        # messages write the full GM control set (volume/expression/pan) so
        # the file plays back identically in any GM-aware DAW.
        for message in gm_setup_messages(
            channel=0,
            program=0,
            volume=100,
            expression=127,
            pan=64,
        ):
            note_track.append(message)

        events = []
        for start, end, pitch, velocity in note_events:
            start_tick = int(round(second2tick(float(start), ticks_per_beat, tempo)))
            end_tick = max(
                start_tick + 1, int(round(second2tick(float(end), ticks_per_beat, tempo)))
            )
            # note_on, note_off, plus a pitch-bend zero (defensive: some
            # players retain the last bend value, so we explicitly reset it
            # at the start of each note).
            events.append(
                (
                    start_tick,
                    0,
                    pitch_bend_message(0, 0),
                )
            )
            events.append(
                (
                    start_tick,
                    1,
                    Message(
                        "note_on",
                        channel=0,
                        note=int(pitch),
                        velocity=int(velocity),
                        time=0,
                    ),
                )
            )
            events.append(
                (
                    end_tick,
                    2,
                    Message(
                        "note_off",
                        channel=0,
                        note=int(pitch),
                        velocity=0,
                        time=0,
                    ),
                )
            )

        last_tick = 0
        for tick, _order, message in sorted(events, key=lambda item: (item[0], item[1])):
            message.time = max(0, tick - last_tick)
            note_track.append(message)
            last_tick = tick

        note_track.append(MetaMessage("end_of_track", time=0))
        midi.tracks.append(note_track)
        midi_path.parent.mkdir(parents=True, exist_ok=True)
        midi.save(str(midi_path))

    @staticmethod
    def _write_notes_csv(note_events, csv_path: Path) -> int:
        rows = []
        for ev in note_events:
            try:
                start, end, pitch, vel = ev[0], ev[1], ev[2], ev[3]
            except (IndexError, TypeError, ValueError):
                continue
            rows.append((f"{float(start):.6f}", f"{float(end):.6f}", int(pitch), int(vel)))

        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["start_time_s", "end_time_s", "pitch_midi", "velocity"])
            writer.writerows(rows)
        return len(rows)

    # -- GM setup injection ---------------------------------------------------

    # Per-stem expressive CC defaults.  Brightness (CC74) opens the filter
    # for brighter tone; reverb (CC91) and chorus (CC93) add spatial depth.
    # These are conservative defaults that work well in any GM player.
    _STEM_CC_CONFIG: dict[str, dict] = {
        "piano": {
            "program": 0,
            "brightness": 64,
            "reverb": 40,
            "chorus": 0,
            "modulation": 0,
        },
        "bass": {
            "program": 33,
            "brightness": 64,
            "reverb": 15,
            "chorus": 0,
            "modulation": 0,
        },
        "guitar": {
            "program": 24,
            "brightness": 64,
            "reverb": 30,
            "chorus": 20,
            "modulation": 0,
        },
        "strings": {
            "program": 48,
            "brightness": 72,
            "reverb": 55,
            "chorus": 35,
            "modulation": 0,
        },
        "vocals": {
            "program": 53,
            "brightness": 64,
            "reverb": 50,
            "chorus": 30,
            "modulation": 0,
        },
        "other": {
            "program": 89,
            "brightness": 64,
            "reverb": 35,
            "chorus": 15,
            "modulation": 0,
        },
    }

    @staticmethod
    def _inject_gm_setup(midi_path: Path, stem_key: str) -> None:
        """Read back a Basic Pitch MIDI and inject GM setup + expressive CCs.

        Basic Pitch outputs raw note_on / note_off events without any
        controller setup.  This method prepends bank-select, program-change,
        volume/expression/pan, and per-stem expressive controllers
        (brightness, reverb, chorus) into every note track so the file
        plays back identically in any GM-aware player.
        """
        from mido import MidiFile

        from app.services.midi_cc import gm_setup_messages

        cc = BasicPitchService._STEM_CC_CONFIG.get(stem_key, BasicPitchService._STEM_CC_CONFIG["other"])

        try:
            midi = MidiFile(str(midi_path))
        except Exception:
            logger.warning("basic-pitch: cannot read back %s for CC injection", midi_path.name)
            return

        for track in midi.tracks:
            if not _track_has_notes(track):
                continue
            setup = gm_setup_messages(
                channel=0,
                program=cc["program"],
                brightness=cc.get("brightness"),
                reverb=cc.get("reverb"),
                chorus=cc.get("chorus"),
                modulation=cc.get("modulation"),
            )
            # Insert setup messages right after the track_name meta event.
            new_msgs: list = []
            inserted = False
            for msg in track:
                new_msgs.append(msg)
                if not inserted and msg.is_meta and msg.type == "track_name":
                    new_msgs.extend(setup)
                    inserted = True
            track.clear()
            for msg in new_msgs:
                track.append(msg)

        midi.save(str(midi_path))
        logger.debug("basic-pitch: injected GM setup for stem=%s", stem_key)


def _track_has_notes(track) -> bool:
    """Return True if the track contains at least one note_on or note_off."""
    return any(
        not msg.is_meta and msg.type in {"note_on", "note_off"}
        for msg in track
    )


def _notes_from_f0(
    *,
    f0,
    voiced_flag,
    voiced_prob,
    times,
    rms,
    min_note_length_s: float,
) -> list[tuple[float, float, int, int]]:
    import numpy as np

    if len(f0) == 0:
        return []

    pitches = np.full(len(f0), np.nan)
    valid = voiced_flag & np.isfinite(f0)
    pitches[valid] = 69.0 + 12.0 * np.log2(f0[valid] / 440.0)

    events: list[tuple[float, float, int, int]] = []
    start_index: int | None = None
    segment_pitches: list[float] = []
    segment_rms: list[float] = []
    current_pitch: float | None = None

    for index, pitch in enumerate(pitches):
        is_voiced = bool(np.isfinite(pitch) and voiced_prob[index] >= 0.35)
        if not is_voiced:
            if start_index is not None:
                _append_segment(events, start_index, index, times, segment_pitches, segment_rms, min_note_length_s)
            start_index = None
            segment_pitches = []
            segment_rms = []
            current_pitch = None
            continue

        rounded_pitch = float(round(float(pitch)))
        if start_index is not None and current_pitch is not None and abs(rounded_pitch - current_pitch) > 1.5:
            _append_segment(events, start_index, index, times, segment_pitches, segment_rms, min_note_length_s)
            start_index = index
            segment_pitches = []
            segment_rms = []

        if start_index is None:
            start_index = index
        current_pitch = rounded_pitch
        segment_pitches.append(float(pitch))
        if index < len(rms):
            segment_rms.append(float(rms[index]))

    if start_index is not None:
        _append_segment(events, start_index, len(pitches) - 1, times, segment_pitches, segment_rms, min_note_length_s)

    return events


def _append_segment(
    events: list[tuple[float, float, int, int]],
    start_index: int,
    end_index: int,
    times,
    segment_pitches: list[float],
    segment_rms: list[float],
    min_note_length_s: float,
) -> None:
    import numpy as np

    if not segment_pitches:
        return
    safe_end_index = min(max(end_index, start_index + 1), len(times) - 1)
    start_time = float(times[start_index])
    end_time = float(times[safe_end_index])
    if end_time - start_time < min_note_length_s:
        return
    pitch = int(max(0, min(127, round(float(np.median(segment_pitches))))))
    level = float(np.mean(segment_rms)) if segment_rms else 0.25
    velocity = max(35, min(118, int(round(45 + 73 * math.sqrt(min(1.0, max(0.0, level * 8.0)))))))
    events.append((start_time, end_time, pitch, velocity))
