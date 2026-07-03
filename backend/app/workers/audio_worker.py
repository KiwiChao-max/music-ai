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
import time
import wave
from pathlib import Path

from app.config import settings
from app.db.models import AudioTaskStatus
from app.db.session import SessionLocal
from app.services import (
    basic_pitch_service,
    demucs_service,
    drum_midi_service,
    file_service,
    instrument_classifier_service,
    llm_service,
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
_INSTRUMENT_CLASSIFIER = instrument_classifier_service.InstrumentClassifierService(
    basic_pitch_service=_BASIC_PITCH,
)

_PLACEHOLDER_STEMS: tuple[str, ...] = demucs_service.EXPECTED_STEMS
# Stems that Basic Pitch should transcribe directly. With the 6-stem
# Demucs model, piano and guitar come out as their own stems, so
# transcribing them individually is far cleaner than running them
# through the rule-based instrument classifier.
_MELODIC_STEMS: tuple[str, ...] = ("bass", "piano", "guitar", "other", "vocals")


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
    output_dir: Path,
    fallback_midi_path: Path,
    db=None,
) -> midi_mapping_service.MidiMappingResult:
    source_paths = midi_mapping_service.collect_raw_midi_sources(
        output_dir,
        fallback=fallback_midi_path,
    )
    if not source_paths:
        raise RuntimeError("no raw MIDI files found to map")
    overrides = _collect_soundfont_overrides(db)
    return _MIDI_MAPPING.create_variants_for_sources(
        source_paths, output_dir, soundfont_overrides=overrides,
    )


def _collect_soundfont_overrides(db) -> list[midi_mapping_service.SoundfontOverride]:
    """If a user has an active SoundFont, build stem -> preset overrides.

    Returns an empty list when no DB session is available, no SoundFont is
    active, or the active SoundFont has no presets — in all of those cases
    the mapper falls back to the default GM voices.
    """
    if db is None:
        return []
    try:
        from app.services.soundfont_service import SoundFontService

        svc = SoundFontService()
        active = svc.get_active_soundfont(db)
    except Exception as exc:  # noqa: BLE001 - never fail the worker for this
        logger.debug("soundfont overrides: lookup failed: %s", exc)
        return []
    if not active:
        return []
    presets = active.get("presets") or []
    if not presets:
        return []
    # `active["presets"]` is a list of dicts from `_preset_row_to_dict`.
    # Convert to the lightweight PresetInfo shape that
    # `build_soundfont_overrides` expects.
    from app.services.soundfont_service import PresetInfo

    preset_infos = [
        PresetInfo(
            bank_msb=int(p.get("bank_msb", 0)),
            bank_lsb=int(p.get("bank_lsb", 0)),
            program=int(p["program"]),
            name=str(p.get("name", "")),
            instrument_type=p.get("instrument_type"),
        )
        for p in presets
    ]
    return midi_mapping_service.build_soundfont_overrides(
        preset_infos, soundfont_name=active.get("name"),
    )


def _analyze_music(output_dir: Path) -> Path:
    return _MUSIC_ANALYSIS.analyze_and_write(output_dir)


def _publish_progress(task, progress: int, step: str) -> None:
    """Fan out a progress event on Redis so WebSocket clients can pick
    it up in real time. Best-effort: if Redis is down we just log and
    continue — the DB row is still the source of truth for the polling
    fallback.
    """
    try:
        import json

        import redis

        client = redis.Redis.from_url(settings.redis_url)
        payload = json.dumps(
            {
                "type": "progress",
                "task_id": task.id,
                "status": task.status.value if task.status else None,
                "progress": progress,
                "current_step": step,
                "ts": time.time(),
            },
            ensure_ascii=False,
        )
        client.publish(f"task:{task.id}", payload)
        client.close()
    except Exception as exc:  # noqa: BLE001 - never fail the worker for pub/sub
        logger.debug("publish_progress: redis unavailable: %s", exc)


def _report(db, task, progress: int, step: str) -> None:
    """Commit a progress update. Kept in one helper so we don't sprinkle
    `db.commit()` calls throughout the pipeline — every step ends with this
    call so the API can observe fresh state on the next /status poll.
    """
    task_service.set_progress(db, task, progress, step)
    db.commit()
    logger.info("task %s: %s%% %s", task.id, progress, step)
    _publish_progress(task, progress, step)


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

    _report(db, task, 50, "Splitting instruments...")
    detection = _split_instruments(audio_path, output_dir, stems)
    logger.info(
        "task %s: instrument detection %s",
        task.id,
        {k: round(v, 3) for k, v in detection.probabilities.items()},
    )

    _report(db, task, 72, "Transcribing to MIDI...")
    midi_paths = _transcribe_stems_or_mix(audio_path, output_dir, stems)
    logger.info("task %s: wrote %d midi file(s)", task.id, len(midi_paths))

    _report(db, task, 88, "Mapping GM/XG MIDI...")
    if not midi_paths:
        raise RuntimeError("cannot map MIDI before transcription completes")
    mapping = _map_midi(output_dir, midi_paths[0], db=db)
    if mapping.applied_overrides:
        logger.info(
            "task %s: soundfont overrides applied: %s",
            task.id,
            [
                f"{o['stem']} -> {o['label']}"
                for o in mapping.applied_overrides
            ],
        )

    _report(db, task, 94, "Analyzing music...")
    analysis_path = _analyze_music(
        output_dir, detection=detection, mapping=mapping,
    )
    logger.info("task %s: analysis written to %s", task.id, analysis_path)

    _report(db, task, 98, "Writing commentary...")
    _generate_commentary(db, task, output_dir)

    _report(db, task, 100, "Done")

    if not stems:
        _emit_placeholder_stems(output_dir)


def _split_instruments(
    audio_path: Path,
    output_dir: Path,
    stems: dict[str, Path],
) -> instrument_classifier_service.InstrumentDetection:
    """Use the instrument classifier on the "other" stem.

    Falls back to the full mix if Demucs didn't produce a separate "other"
    (placeholder stem fallback) — the resulting split will be less clean
    but still gives the user a per-instrument view.
    """
    target = stems.get("other")
    if target is None or not target.is_file() or target.stat().st_size <= 1024:
        target = audio_path
    try:
        return _INSTRUMENT_CLASSIFIER.split_instrument_stem(target, output_dir)
    except Exception as exc:  # noqa: BLE001 - downstream steps must still run
        logger.warning("instrument-classifier failed; continuing without split: %s", exc)
        return instrument_classifier_service.InstrumentDetection(
            probabilities={name: 0.0 for name in instrument_classifier_service.INSTRUMENTS},
            dominant="other_melodic",
            total_frames=0,
        )


def _analyze_music(
    output_dir: Path,
    *,
    detection: instrument_classifier_service.InstrumentDetection,
    mapping: midi_mapping_service.MidiMappingResult | None = None,
) -> Path:
    analysis_path = _MUSIC_ANALYSIS.analyze_and_write(output_dir)
    # Attach instrument detection to the JSON so the frontend can render
    # the per-instrument breakdown without a second round-trip.
    import json
    with analysis_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["detected_instruments"] = list(detection.probabilities.items())
    data["dominant_instrument"] = detection.dominant
    if mapping is not None:
        data["soundfont_overrides"] = list(mapping.applied_overrides)
    with analysis_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return analysis_path


def _generate_commentary(
    db, task, output_dir: Path
) -> None:
    """Read `analysis.json` and ask the LLM service for a commentary.

    Best-effort: a failure here logs a warning and leaves the
    `commentary` column null. The pipeline must never fail because the
    LLM step hiccuped — the user still gets a perfectly useful
    analysis. The commentary can always be regenerated later via the
    admin endpoint.
    """
    if not settings.llm_enabled:
        return
    analysis_path = output_dir / "analysis.json"
    if not analysis_path.is_file():
        logger.warning("commentary: analysis.json missing for task %s", task.id)
        return
    import json
    try:
        with analysis_path.open("r", encoding="utf-8") as f:
            analysis = json.load(f)
        result = llm_service.generate_commentary(
            analysis, filename=task.filename
        )
    except Exception as exc:  # noqa: BLE001 - LLM step must never fail the pipeline
        logger.warning("commentary: llm step failed for task %s: %s", task.id, exc)
        return

    from datetime import datetime, timezone

    task.commentary = result.text
    task.commentary_model = result.model
    task.commentary_generated_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("task %s: commentary generated (%s)", task.id, result.model)


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
                # Publish the final "task_finished" event so WebSocket
                # clients don't sit on a stale PROCESSING state.
                _publish_progress(task, 0, f"FAILED: {exc}")
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
