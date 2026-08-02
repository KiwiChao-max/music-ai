"""Post-processing: MIDI mapping, instrument classification, analysis, commentary.

All steps that run *after* MIDI transcription --- the "heavy" audio
processing is done at this point, so these steps are mostly about
data transformation and enrichment.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from pathlib import Path

from app.config import settings
from app.services import (
    instrument_classifier_service,
    llm_service,
    midi_mapping_service,
    music_analysis_service,
)

logger = logging.getLogger(__name__)

# Module-level service singletons.
_MIDI_MAPPING = midi_mapping_service.MidiMappingService()
_MUSIC_ANALYSIS = music_analysis_service.MusicAnalysisService()
_INSTRUMENT_CLASSIFIER = instrument_classifier_service.InstrumentClassifierService(
    basic_pitch_service=None,  # not needed for classify-only usage
)


def _collect_soundfont_overrides(db) -> list[midi_mapping_service.SoundfontOverride]:
    """If a user has an active SoundFont, build stem -> preset overrides."""
    if db is None:
        return []
    try:
        from app.services.soundfont_service import SoundFontService

        svc = SoundFontService()
        active = svc.get_active_soundfont(db)
    except Exception as exc:
        logger.debug("soundfont overrides: lookup failed: %s", exc)
        return []
    if not active:
        return []
    presets = active.get("presets") or []
    if not presets:
        return []
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
        preset_infos,
        soundfont_name=active.get("name"),
    )


def map_midi(
    output_dir: Path,
    fallback_midi_path: Path,
    db=None,
) -> midi_mapping_service.MidiMappingResult:
    """Map raw MIDI files to GM/XG variants with optional SoundFont overrides."""
    source_paths = midi_mapping_service.collect_raw_midi_sources(
        output_dir,
        fallback=fallback_midi_path,
    )
    if not source_paths:
        raise RuntimeError("no raw MIDI files found to map")
    overrides = _collect_soundfont_overrides(db)
    return _MIDI_MAPPING.create_variants_for_sources(
        source_paths,
        output_dir,
        soundfont_overrides=overrides,
    )


def split_instruments(
    audio_path: Path,
    output_dir: Path,
    stems: dict[str, Path],
) -> instrument_classifier_service.InstrumentDetection:
    """Use the instrument classifier on the "other" stem.

    Falls back to the full mix if Demucs didn't produce a separate "other"
    stem --- the resulting split will be less clean but still functional.
    """
    target = stems.get("other")
    if target is None or not target.is_file() or target.stat().st_size <= 1024:
        target = audio_path
    try:
        return _INSTRUMENT_CLASSIFIER.split_instrument_stem(target, output_dir).detection
    except Exception as exc:
        logger.warning("instrument-classifier failed; continuing without split: %s", exc)
        return instrument_classifier_service.InstrumentDetection(
            probabilities={name: 0.0 for name in instrument_classifier_service.INSTRUMENTS},
            dominant="other_melodic",
            total_frames=0,
        )


def analyze_music(
    output_dir: Path,
    *,
    detection: instrument_classifier_service.InstrumentDetection,
    mapping: midi_mapping_service.MidiMappingResult | None = None,
) -> Path:
    """Run music analysis and attach instrument detection + soundfont overrides."""
    analysis_path = _MUSIC_ANALYSIS.analyze_and_write(output_dir)
    with analysis_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    data["detected_instruments"] = [
        {"instrument": k, "probability": v} for k, v in detection.probabilities.items()
    ]
    data["dominant_instrument"] = detection.dominant
    if mapping is not None:
        data["soundfont_overrides"] = list(mapping.applied_overrides)
    with analysis_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return analysis_path


def generate_commentary(db, task, output_dir: Path) -> None:
    """Read analysis.json and ask the LLM service for a commentary.

    Best-effort: a failure here logs a warning and leaves the
    ``commentary`` column null. The pipeline must never fail because the
    LLM step hiccuped.
    """
    if not settings.llm_enabled:
        return
    analysis_path = output_dir / "analysis.json"
    if not analysis_path.is_file():
        logger.warning("commentary: analysis.json missing for task %s", task.id)
        return
    try:
        with analysis_path.open("r", encoding="utf-8") as f:
            analysis = json.load(f)
        result = llm_service.generate_commentary(analysis, filename=task.filename)
    except Exception as exc:
        logger.warning("commentary: llm step failed for task %s: %s", task.id, exc)
        return

    from datetime import datetime

    task.commentary = result.text
    task.commentary_model = result.model
    task.commentary_generated_at = datetime.now(UTC)
    db.commit()
    logger.info("task %s: commentary generated (%s)", task.id, result.model)
