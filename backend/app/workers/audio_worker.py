"""Audio task worker.

Runs the processing pipeline for one `audio_tasks` row:

    upload -> source separation -> audio-to-MIDI -> GM/XG mapping -> analysis

Demucs is attempted first. If it is not installed or fails, the worker falls
back to the original mix plus placeholder stems so the app remains usable in
lightweight development environments.

Heavy services (Demucs / Basic Pitch / drum-MIDI / MIDI mapping / analysis)
hold no per-call state, so they are instantiated once at module load instead
of being recreated for every task.
"""
from __future__ import annotations

import logging
import wave
from pathlib import Path

from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.services import (
    basic_pitch_service,
    demucs_service,
    drum_midi_service,
    file_service,
    midi_mapping_service,
    music_analysis_service,
    task_service,
)

logger = logging.getLogger(__name__)

# Module-level service singletons. Safe to share across tasks within one
# worker process; each call takes a fresh audio path so state cannot leak.
_DEMUCS = demucs_service.DemucsService()
_BASIC_PITCH = basic_pitch_service.BasicPitchService()
_DRUM_MIDI = drum_midi_service.DrumMidiService()
_MIDI_MAPPING = midi_mapping_service.MidiMappingService()
_MUSIC_ANALYSIS = music_analysis_service.MusicAnalysisService()

_PLACEHOLDER_STEMS: tuple[str, ...] = demucs_service.EXPECTED_STEMS
_MELODIC_STEMS: tuple[str, ...] = ("bass", "other", "vocals")


def _write_silent_wav(path: Path, *, seconds: float = 0.1, rate: int = 8000) -> None:
    """Write a minimal valid silent mono 8-bit WAV. Used as a fallback stem."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(b"\x80" * int(rate * seconds))


def _emit_placeholder_stems(output_dir: Path) -> dict[str, Path]:
    stems: dict[str, Path] = {}
    for stem in _PLACEHOLDER_STEMS:
        path = output_dir / f"{stem}.wav"
        _write_silent_wav(path)
        stems[stem] = path
    return stems


def _separate_stems(audio_path: Path, output_dir: Path) -> dict[str, Path]:
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
    except Exception as exc:  # noqa: BLE001 - fallback keeps local dev usable
        logger.warning("demucs unavailable; using placeholder stems: %s", exc)
        return _emit_placeholder_stems(output_dir)


def _run_basic_pitch(audio_path: Path, output_dir: Path) -> Path:
    result = _BASIC_PITCH.transcribe(audio_path, output_dir)
    return result.midi_path


def _run_drum_midi(stem_path: Path, output_dir: Path) -> Path | None:
    result = _DRUM_MIDI.create_drum_midi(stem_path, output_dir)
    if result.event_count == 0:
        logger.warning("drum-midi produced no hits for %s", stem_path.name)
    return result.combined_path


def _transcribe_stems_or_mix(
    audio_path: Path,
    output_dir: Path,
    stems: dict[str, Path],
) -> list[Path]:
    """Transcribe drums separately, then melodic stems or the original mix."""
    real_stems = [path for path in stems.values() if path.stat().st_size > 1024]
    if not real_stems:
        return [_run_basic_pitch(audio_path, output_dir)]

    midi_paths: list[Path] = []
    drum_stem = stems.get("drums")
    if drum_stem is not None and drum_stem.is_file() and drum_stem.stat().st_size > 1024:
        try:
            drum_midi_path = _run_drum_midi(drum_stem, output_dir)
            if drum_midi_path is not None:
                midi_paths.append(drum_midi_path)
        except Exception as exc:  # noqa: BLE001 - keep melodic stems usable
            logger.warning("drum-midi failed for stem drums: %s", exc)

    for stem_name in _MELODIC_STEMS:
        stem_path = stems.get(stem_name)
        if stem_path is None or not stem_path.is_file() or stem_path.stat().st_size <= 1024:
            continue
        try:
            midi_paths.append(_run_basic_pitch(stem_path, output_dir))
        except Exception as exc:  # noqa: BLE001 - keep other stems usable
            logger.warning("audio-to-midi failed for stem %s: %s", stem_name, exc)

    if not midi_paths:
        midi_paths.append(_run_basic_pitch(audio_path, output_dir))
    return midi_paths


def _map_midi(
    output_dir: Path, fallback_midi_path: Path
) -> midi_mapping_service.MidiMappingResult:
    source_paths = midi_mapping_service.collect_raw_midi_sources(
        output_dir,
        fallback=fallback_midi_path,
    )
    if not source_paths:
        raise RuntimeError("no raw MIDI files found to map")
    return _MIDI_MAPPING.create_variants_for_sources(source_paths, output_dir)


def _analyze_music(output_dir: Path) -> Path:
    return _MUSIC_ANALYSIS.analyze_and_write(output_dir)


def _report(db, task, progress: int, step: str) -> None:
    """Commit a progress update. Kept in one helper so we don't sprinkle
    `db.commit()` calls throughout the pipeline — every step ends with this
    call so the API can observe fresh state on the next /status poll.
    """
    task_service.set_progress(db, task, progress, step)
    db.commit()
    logger.info("task %s: %s%% %s", task.id, progress, step)


def _run_pipeline(
    db,
    task,
    audio_path: Path,
    output_dir: Path,
) -> None:
    """Run the pipeline as a sequence of explicit steps.

    Each step calls `_report` once with its starting progress percentage; the
    actual work runs *before* the next step's `_report`, so the UI sees the
    previous value until the new one lands. No more `time.sleep` padding —
    progress is real work, real progress.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    _report(db, task, 10, "Preparing audio...")
    output_dir.mkdir(parents=True, exist_ok=True)

    _report(db, task, 30, "Separating stems...")
    stems = _separate_stems(audio_path, output_dir)

    _report(db, task, 72, "Transcribing to MIDI...")
    midi_paths = _transcribe_stems_or_mix(audio_path, output_dir, stems)
    logger.info("task %s: wrote %d midi file(s)", task.id, len(midi_paths))

    _report(db, task, 88, "Mapping GM/XG MIDI...")
    if not midi_paths:
        raise RuntimeError("cannot map MIDI before transcription completes")
    mapping = _map_midi(output_dir, midi_paths[0])
    logger.info(
        "task %s: mapped %d midi source(s) to %s and %s",
        task.id,
        len(mapping.source_paths),
        mapping.gm_path,
        mapping.xg_path,
    )

    _report(db, task, 94, "Analyzing music...")
    analysis_path = _analyze_music(output_dir)
    logger.info("task %s: analysis written to %s", task.id, analysis_path)

    _report(db, task, 100, "Done")

    if not stems:
        _emit_placeholder_stems(output_dir)


def process_task(task_id: int) -> None:
    """Run the pipeline for one task. Always transitions to FINISHED or FAILED."""
    with SessionLocal() as db:
        task = task_service.get_task(db, task_id)
        if task is None:
            logger.warning("process_task: task %s not found", task_id)
            return

        try:
            task_service.set_status(db, task, AudioTaskStatus.PROCESSING)
            task_service.set_progress(db, task, 0, "Starting...")
            task.error_message = None
            db.commit()

            audio_path = file_service.storage_path(task.id, task.filename)
            output_dir = Path(task.output_dir) if task.output_dir else audio_path.parent
            _run_pipeline(db, task, audio_path, output_dir)

            task_service.mark_finished(db, task, success=True)
            db.commit()
            logger.info("task %s finished", task_id)

        except Exception as exc:  # noqa: BLE001 - we want to catch everything
            db.rollback()
            logger.exception("task %s failed: %s", task_id, exc)
            task = task_service.get_task(db, task_id)
            if task is not None:
                task_service.mark_finished(
                    db,
                    task,
                    success=False,
                    error_message=str(exc),
                )
                db.commit()
            raise


if __name__ == "__main__":  # pragma: no cover - manual entry point
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Process a single audio task.")
    parser.add_argument("task_id", type=int, help="audio_tasks.id to process")
    args = parser.parse_args()

    try:
        process_task(args.task_id)
    except Exception:
        sys.exit(1)
