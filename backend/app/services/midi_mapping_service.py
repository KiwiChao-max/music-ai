"""Stem-aware GM / XG mapping for MIDI files produced by the audio pipeline.

The mapper keeps the detected notes intact, but makes the MIDI predictable for
General MIDI and Yamaha XG players: it inserts reset, bank-select,
program-change, volume/expression/pan setup, and assigns stable channels per
stem. When several raw stem MIDI files exist, they are merged into one mapped
arrangement while preserving tempo and note timing.

If a user-supplied SoundFont (or preset table) is active, the mapper can
rewrite each stem's `program` / `bank_msb` / `bank_lsb` to point at the
user's chosen instrument via the `soundfont_overrides` argument. The notes
themselves are untouched --- only the voice selection is overridden.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


class MidiProfile(StrEnum):
    GM = "gm"
    XG = "xg"


@dataclass(frozen=True)
class VoiceMapping:
    """A concrete MIDI voice assignment.

    `program` is zero-based, as expected by the MIDI file format and mido.
    `channel` is also zero-based; channel 9 is the one-based MIDI channel 10
    that GM/XG reserve for drums.
    """

    label: str
    program: int
    bank_msb: int = 0
    bank_lsb: int = 0
    channel: int | None = None
    is_drum: bool = False
    volume: int = 100
    expression: int = 127
    pan: int = 64
    # Optional metadata about the override source. `None` means we used the
    # default GM voice. When a user-supplied SoundFont is active, this is
    # populated so the frontend can show "Stem X -> Custom voice Y" on the
    # task detail page.
    source: str | None = None
    source_detail: str | None = None


@dataclass(frozen=True)
class SoundfontOverride:
    """Per-stem voice override coming from a user-supplied SoundFont.

    Built by `SoundFontService.map_gm_to_custom`. The mapper will use these
    values in place of the default GM voice for the corresponding stem role.
    """

    stem_key: str
    label: str
    program: int
    bank_msb: int = 0
    bank_lsb: int = 0


@dataclass(frozen=True)
class MidiMappingResult:
    """Paths written by `create_variants_for_sources`."""

    source_paths: tuple[Path, ...]
    gm_path: Path
    xg_path: Path
    # Which stems ended up using a custom voice (label, program). Empty
    # tuple when no SoundFont override was active.
    applied_overrides: tuple[dict, ...] = field(default_factory=tuple)

    @property
    def source_path(self) -> Path:
        """Backward-compatible shorthand for callers that map one MIDI file."""
        return self.source_paths[0]


_DRUM_CHANNEL = 9
_MIDI_SUFFIXES = {".mid", ".midi"}
_GENERATED_PROFILE_SUFFIXES = ("_gm", "_xg")
_DRUM_PART_NAMES: frozenset[str] = frozenset(
    {
        "kick",
        "snare",
        "sidestick",
        "hihat_closed",
        "hihat_open",
        "tom_high",
        "tom_himid",
        "tom_lomid",
        "tom_low",
        "tom_floor",
        "crash",
        "ride",
        "china",
        "splash",
        "ride_bell",
        "tambourine",
        "cowbell",
        "percussion",
        "fill",
    }
)
_PER_INSTRUMENT_STEMS: frozenset[str] = frozenset(
    {
        "piano",
        "guitar",
        "strings",
        "synth",
        "other_melodic",
    }
)
_BANK_CONTROLLERS = {0, 32}

_STEM_ORDER: dict[str, int] = {
    "drums": 0,
    "bass": 1,
    "piano": 2,
    "guitar": 3,
    "strings": 4,
    "synth": 5,
    "other": 6,
    "vocals": 7,
    "original": 8,
}

_STEM_ALIASES: tuple[tuple[str, str], ...] = (
    ("drums", "drums"),
    ("drum", "drums"),
    ("percussion", "drums"),
    ("kick", "drums"),
    ("snare", "drums"),
    ("bass", "bass"),
    ("sub", "bass"),
    ("808", "bass"),
    ("piano", "piano"),
    ("keys", "piano"),
    ("keyboard", "piano"),
    ("vocal", "vocals"),
    ("voice", "vocals"),
    ("guitar", "guitar"),
    ("string", "strings"),
    ("violin", "strings"),
    ("cello", "strings"),
    ("synth", "synth"),
    ("lead", "synth"),
    ("pad", "synth"),
    ("other_melodic", "other"),
    ("other", "other"),
    ("accompaniment", "other"),
    ("original", "original"),
    ("mix", "original"),
)

# Path to the user-editable voice configuration file, relative to this module.
_VOICES_CONFIG_PATH: Path = Path(__file__).resolve().parent.parent / "config" / "voices.json"

# Default XG drum bank (MSB, LSB). Standard Kit = (127, 0).
# SFX Kit 1: bank MSB=127, LSB=0, program=56
# SFX Kit 2: bank MSB=126, LSB=0
_DEFAULT_XG_DRUM_BANK: tuple[int, int] = (127, 0)

# Built-in fallback voice mappings --- used when the config file is missing or
# a stem key is absent from the user config. All program values are zero-based.
#   0  Acoustic Grand Piano
#   24 Acoustic Guitar (nylon)
#   33 Electric Bass (finger)
#   48 String Ensemble 1
#   53 Voice Oohs
#   89 Pad 2 (warm)
_BUILTIN_VOICES: dict[str, VoiceMapping] = {
    "original": VoiceMapping("Acoustic Grand Piano", program=0, channel=0),
    "piano": VoiceMapping("Acoustic Grand Piano", program=0, channel=0),
    "bass": VoiceMapping("Electric Bass (finger)", program=33, channel=1, volume=108, pan=54),
    "guitar": VoiceMapping("Acoustic Guitar (nylon)", program=24, channel=2, volume=98, pan=44),
    "strings": VoiceMapping("String Ensemble 1", program=48, channel=3, volume=96, pan=84),
    "synth": VoiceMapping("Synth Lead (square)", program=80, channel=4, volume=92, pan=64),
    "other": VoiceMapping("Warm Pad", program=89, channel=5, volume=92, pan=74),
    "vocals": VoiceMapping("Voice Oohs", program=53, channel=6, volume=94, pan=64),
    "drums": VoiceMapping(
        "Standard Drum Kit",
        program=0,
        channel=_DRUM_CHANNEL,
        is_drum=True,
        volume=112,
    ),
}

# XG-specific melodic voice variations. XG uses bank MSB=0 for normal melodic
# voices and the LSB to select documented variations (0 = base voice = GM
# fallback, 1 = "Live!"/"Sweet!"/"Stereo" variation, etc.). Stems absent from
# this table fall through to the GM voice (bank 0:0), which is always a valid
# XG voice. Channel / volume / pan are inherited from the base voice at apply
# time, so only label / bank / program need to be set here.
# Reference: Yamaha XG Specification rev 1.97 voice tables.
_BUILTIN_XG_MELODIC_VOICES: dict[str, VoiceMapping] = {
    "original": VoiceMapping("Live! Grand Piano", program=0, bank_msb=0, bank_lsb=1),
    "piano": VoiceMapping("Live! Grand Piano", program=0, bank_msb=0, bank_lsb=1),
    "guitar": VoiceMapping("Nylon Guitar", program=24, bank_msb=0, bank_lsb=1),
    "strings": VoiceMapping("Stereo Strings", program=48, bank_msb=0, bank_lsb=1),
    # bass / synth / other / vocals: no widely-supported XG variation, keep
    # the GM voice (bank 0:0) --- XG players will still accept it.
}


def _load_voices_config() -> tuple[
    dict[str, VoiceMapping], tuple[int, int], dict[str, VoiceMapping]
]:
    """Load voice mappings, XG drum bank, and XG melodic voices from the
    user-editable JSON config.

    Returns ``(voices, xg_drum_bank, xg_melodic_voices)``. Missing keys fall
    back to `_BUILTIN_VOICES`, `_DEFAULT_XG_DRUM_BANK`, and
    `_BUILTIN_XG_MELODIC_VOICES`. If the config file is absent or unparseable,
    the built-in defaults are returned as-is.

    The result is cached for the lifetime of the process via
    `functools.lru_cache` --- the file is small and read on every request
    that maps a stem to a GM program, so hitting the disk each time is
    wasteful. Restart the process (or call `_load_voices_config.cache_clear()`)
    to pick up config edits.
    """
    return _load_voices_config_cached()


@lru_cache(maxsize=1)
def _load_voices_config_cached() -> tuple[
    dict[str, VoiceMapping], tuple[int, int], dict[str, VoiceMapping]
]:
    try:
        raw = json.loads(_VOICES_CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return (
            dict(_BUILTIN_VOICES),
            _DEFAULT_XG_DRUM_BANK,
            dict(_BUILTIN_XG_MELODIC_VOICES),
        )

    # --- XG drum bank ---
    drum_bank_raw = raw.get("xg_drum_bank", {}) if isinstance(raw, dict) else {}
    xg_drum_bank: tuple[int, int] = (
        int(drum_bank_raw.get("msb", _DEFAULT_XG_DRUM_BANK[0])),
        int(drum_bank_raw.get("lsb", _DEFAULT_XG_DRUM_BANK[1])),
    )

    # --- voice mappings ---
    voices: dict[str, VoiceMapping] = dict(_BUILTIN_VOICES)
    user_voices = raw.get("voices", {}) if isinstance(raw, dict) else {}
    if not isinstance(user_voices, dict):
        user_voices = {}

    for stem_key, entry in user_voices.items():
        if not isinstance(entry, dict):
            continue
        try:
            voices[stem_key] = VoiceMapping(
                label=str(entry.get("label", stem_key)),
                program=int(entry.get("program", 0)),
                bank_msb=int(entry.get("bank_msb", 0)),
                bank_lsb=int(entry.get("bank_lsb", 0)),
                channel=entry.get("channel") if "channel" in entry else None,
                is_drum=bool(entry.get("is_drum", False)),
                volume=int(entry.get("volume", 100)),
                expression=int(entry.get("expression", 127)),
                pan=int(entry.get("pan", 64)),
            )
        except (ValueError, TypeError):
            logger.warning("midi-mapping: skipping invalid voice entry for stem=%s", stem_key)
            continue

    # --- XG melodic voice variations ---
    xg_melodic: dict[str, VoiceMapping] = dict(_BUILTIN_XG_MELODIC_VOICES)
    user_xg_melodic = raw.get("xg_melodic_voices", {}) if isinstance(raw, dict) else {}
    if not isinstance(user_xg_melodic, dict):
        user_xg_melodic = {}

    for stem_key, entry in user_xg_melodic.items():
        if not isinstance(entry, dict):
            continue
        try:
            xg_melodic[stem_key] = VoiceMapping(
                label=str(entry.get("label", stem_key)),
                program=int(entry.get("program", 0)),
                bank_msb=int(entry.get("bank_msb", 0)),
                bank_lsb=int(entry.get("bank_lsb", 0)),
            )
        except (ValueError, TypeError):
            logger.warning(
                "midi-mapping: skipping invalid XG melodic voice entry for stem=%s",
                stem_key,
            )
            continue

    return voices, xg_drum_bank, xg_melodic


def _get_voices() -> dict[str, VoiceMapping]:
    """Return the active voice mappings (user config merged over built-in defaults)."""
    voices, _, _ = _load_voices_config()
    return voices


def _get_xg_drum_bank() -> tuple[int, int]:
    """Return the configured XG drum bank as (MSB, LSB)."""
    _, bank, _ = _load_voices_config()
    return bank


def _get_xg_melodic_voices() -> dict[str, VoiceMapping]:
    """Return the XG-specific melodic voice variations (user config merged
    over built-in defaults). Stems absent from this dict fall through to
    the GM voice when applying the XG profile."""
    _, _, xg_melodic = _load_voices_config()
    return xg_melodic


def midi_profile_from_name(name: str) -> str:
    """Infer the public profile label from an output filename stem."""
    lower = name.lower()
    if lower.endswith("_gm"):
        return MidiProfile.GM.value
    if lower.endswith("_xg"):
        return MidiProfile.XG.value
    return "raw"


def _is_drum_part_file(path: Path) -> bool:
    stem = path.stem.lower()
    for sep in ("_", "-"):
        if sep in stem:
            _, _, part = stem.rpartition(sep)
            if part in _DRUM_PART_NAMES:
                return True
    return False


def is_raw_midi_path(path: Path) -> bool:
    """Return true for source MIDI files, false for generated GM/XG outputs."""
    if path.suffix.lower() not in _MIDI_SUFFIXES:
        return False
    stem = path.stem.lower()
    if _strip_profile_suffix(path.stem).lower() != stem:
        return False
    return not _is_drum_part_file(path)


def collect_raw_midi_sources(output_dir: Path, fallback: Path | None = None) -> list[Path]:
    """Collect raw MIDI files from a task output directory in musical order."""
    sources = [p for p in output_dir.iterdir() if p.is_file() and is_raw_midi_path(p)]
    _dedupe_per_instrument_sources(sources)
    if fallback is not None and fallback.is_file() and fallback not in sources:
        sources.append(fallback)
    return sorted(
        sources, key=lambda p: (_STEM_ORDER.get(stem_key_from_name(p.stem), 99), p.name.lower())
    )


def _dedupe_per_instrument_sources(sources: list[Path]) -> None:
    per_stem_files: dict[str, list[Path]] = {}
    for p in sources:
        stem = p.stem.lower()
        if "_" in stem:
            parent, _, instrument = stem.rpartition("_")
            if instrument in _PER_INSTRUMENT_STEMS:
                per_stem_files.setdefault(parent, []).append(p)
    for parent, _instrument_files in per_stem_files.items():
        combined = next((p for p in sources if p.stem.lower() == parent), None)
        if combined is not None and combined in sources:
            sources.remove(combined)


def stem_key_from_name(name: str) -> str:
    """Classify a file or track name into the closest supported stem role."""
    clean_name = _strip_profile_suffix(name).replace("-", "_").lower()
    parts = clean_name.replace("_", " ")
    for keyword, stem_key in _STEM_ALIASES:
        if keyword in parts:
            return stem_key
    return "other"


class MidiMappingService:
    """Create GM and XG compatible variants for one or more MIDI sources."""

    def create_variants(
        self,
        source_path: Path,
        output_dir: Path | None = None,
        *,
        soundfont_overrides: Sequence[SoundfontOverride] | None = None,
    ) -> MidiMappingResult:
        """Backward-compatible one-file mapping entry point."""
        return self.create_variants_for_sources(
            [source_path],
            output_dir,
            soundfont_overrides=soundfont_overrides,
        )

    def create_variants_for_sources(
        self,
        source_paths: Sequence[Path],
        output_dir: Path | None = None,
        *,
        soundfont_overrides: Sequence[SoundfontOverride] | None = None,
    ) -> MidiMappingResult:
        sources = tuple(source_paths)
        if not sources:
            raise ValueError("at least one source MIDI is required")

        output_dir = output_dir or sources[0].parent
        output_dir.mkdir(parents=True, exist_ok=True)
        output_stem = sources[0].stem if len(sources) == 1 else "merged"

        gm_path = output_dir / f"{output_stem}_{MidiProfile.GM.value}.mid"
        xg_path = output_dir / f"{output_stem}_{MidiProfile.XG.value}.mid"

        applied = self.map_sources(
            sources, gm_path, MidiProfile.GM, soundfont_overrides=soundfont_overrides
        )
        applied = self.map_sources(
            sources, xg_path, MidiProfile.XG, soundfont_overrides=soundfont_overrides
        )

        return MidiMappingResult(
            source_paths=sources,
            gm_path=gm_path,
            xg_path=xg_path,
            applied_overrides=tuple(applied),
        )

    def map_file(
        self,
        source_path: Path,
        output_path: Path,
        profile: MidiProfile,
        *,
        soundfont_overrides: Sequence[SoundfontOverride] | None = None,
    ) -> Path:
        """Write one mapped MIDI file and return `output_path`."""
        return self.map_sources(
            [source_path],
            output_path,
            profile,
            soundfont_overrides=soundfont_overrides,
        )

    def map_sources(
        self,
        source_paths: Sequence[Path],
        output_path: Path,
        profile: MidiProfile,
        *,
        soundfont_overrides: Sequence[SoundfontOverride] | None = None,
    ) -> tuple[dict, ...]:
        """Merge and map raw MIDI sources into one profile-specific MIDI file.

        Import mido lazily so the API can serve task/stem metadata in light
        environments where only the worker has audio dependencies installed.

        Returns a tuple of dicts describing which stems used a custom
        SoundFont voice, so the caller can surface the override on the UI.
        """
        from mido import MidiFile

        if not source_paths:
            raise ValueError("at least one source MIDI is required")

        overrides_by_stem: Mapping[str, SoundfontOverride] = {
            o.stem_key: o for o in (soundfont_overrides or [])
        }

        source_midis = [(path, MidiFile(str(path))) for path in source_paths]
        first_midi = source_midis[0][1]
        target = MidiFile(
            type=1,
            ticks_per_beat=first_midi.ticks_per_beat,
            charset=first_midi.charset,
        )

        setup_assignments: dict[int, VoiceMapping] = {}
        voice_channels: dict[str, int] = {}
        used_channels: set[int] = set()
        mapped_tracks = []
        applied_overrides: list[dict] = []

        for source_index, (source_path, source_midi) in enumerate(source_midis):
            for track in source_midi.tracks:
                if not _has_channel_messages(track):
                    if source_index == 0:
                        mapped_tracks.append(self._copy_meta_track(track))
                    continue

                stem_key = _detect_stem_key(source_path.stem, track)
                base_voice = _get_voices()[stem_key]
                voice = _voice_for_profile(base_voice, stem_key, profile)
                voice = _apply_soundfont_override(voice, stem_key, overrides_by_stem)
                if voice.source == "soundfont":
                    applied_overrides.append(
                        {
                            "stem": stem_key,
                            "label": voice.label,
                            "program": voice.program,
                            "bank_msb": voice.bank_msb,
                            "bank_lsb": voice.bank_lsb,
                        }
                    )
                channel = _resolve_channel(stem_key, voice, voice_channels, used_channels)
                voice = _with_channel(voice, channel)
                setup_assignments[channel] = voice
                mapped_tracks.append(
                    self._copy_mapped_track(
                        track,
                        channel,
                        source_midi.ticks_per_beat,
                        target.ticks_per_beat,
                        track_name=f"{stem_key}: {voice.label}",
                    )
                )

        target.tracks.append(self._build_setup_track(profile, setup_assignments))
        target.tracks.extend(mapped_tracks)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        target.save(str(output_path))
        logger.info(
            "midi-mapping: wrote %s profile=%s sources=%d tracks=%d overrides=%d",
            output_path.name,
            profile.value,
            len(source_paths),
            len(target.tracks),
            len(applied_overrides),
        )
        return tuple(applied_overrides)

    def _build_setup_track(
        self,
        profile: MidiProfile,
        assignments: dict[int, VoiceMapping],
    ):
        from mido import Message, MetaMessage, MidiTrack

        track = MidiTrack()
        track.append(MetaMessage("track_name", name=f"{profile.value.upper()} setup", time=0))
        track.append(_reset_message(profile))

        for channel in sorted(assignments):
            voice = assignments[channel]
            track.extend(
                [
                    Message(
                        "control_change", channel=channel, control=0, value=voice.bank_msb, time=0
                    ),
                    Message(
                        "control_change", channel=channel, control=32, value=voice.bank_lsb, time=0
                    ),
                    Message("program_change", channel=channel, program=voice.program, time=0),
                    Message(
                        "control_change", channel=channel, control=7, value=voice.volume, time=0
                    ),
                    Message(
                        "control_change",
                        channel=channel,
                        control=11,
                        value=voice.expression,
                        time=0,
                    ),
                    Message("control_change", channel=channel, control=10, value=voice.pan, time=0),
                ]
            )

        track.append(MetaMessage("end_of_track", time=0))
        return track

    @staticmethod
    def _copy_meta_track(track):
        from mido import MidiTrack

        copied = MidiTrack()
        for message in track:
            copied.append(message.copy())
        return copied

    @staticmethod
    def _copy_mapped_track(
        track,
        channel: int,
        source_ticks_per_beat: int,
        target_ticks_per_beat: int,
        *,
        track_name: str,
    ):
        from mido import MetaMessage, MidiTrack

        copied = MidiTrack()
        copied.append(MetaMessage("track_name", name=track_name, time=0))
        carried_time = 0
        scale = target_ticks_per_beat / source_ticks_per_beat

        for message in track:
            scaled_time = round(message.time * scale)
            if _is_conflicting_setup_message(message):
                carried_time += scaled_time
                continue
            if message.is_meta and message.type in {"track_name", "instrument_name"}:
                carried_time += scaled_time
                continue

            new_message = message.copy()
            new_message.time = scaled_time + carried_time
            carried_time = 0

            if hasattr(new_message, "channel"):
                new_message.channel = channel
            copied.append(new_message)

        if carried_time:
            if copied:
                copied[-1].time += carried_time
            else:
                copied.append(MetaMessage("end_of_track", time=carried_time))

        return copied


def _strip_profile_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in _GENERATED_PROFILE_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _has_channel_messages(track) -> bool:
    return any((not message.is_meta) and hasattr(message, "channel") for message in track)


def _detect_stem_key(source_stem: str, track) -> str:
    text_parts = [source_stem]
    for message in track:
        if message.is_meta and message.type in {"track_name", "instrument_name"}:
            text_parts.append(getattr(message, "name", ""))

    for text in text_parts:
        stem_key = stem_key_from_name(text)
        if stem_key != "other" or "other" in text.lower():
            return stem_key
    return "other"


def _voice_for_profile(
    voice: VoiceMapping,
    stem_key: str,
    profile: MidiProfile,
) -> VoiceMapping:
    """Apply profile-specific bank/program selection on top of the base voice.

    - GM profile: returns the voice unchanged.
    - XG profile, drum stem: swaps in the configured XG drum bank
      (`_get_xg_drum_bank`).
    - XG profile, melodic stem: if a documented XG variation exists for this
      stem in `_get_xg_melodic_voices`, the bank/program/label are replaced
      while the channel / volume / expression / pan (mixer settings) are
      preserved from the base voice. Stems without an XG variation fall
      through to the GM voice (bank 0:0), which is always a valid XG voice.
    """
    if profile is not MidiProfile.XG:
        return voice

    if voice.is_drum:
        msb, lsb = _get_xg_drum_bank()
        return VoiceMapping(
            label=voice.label,
            program=voice.program,
            bank_msb=msb,
            bank_lsb=lsb,
            channel=voice.channel,
            is_drum=True,
            volume=voice.volume,
            expression=voice.expression,
            pan=voice.pan,
        )

    # XG melodic stem --- look up a documented XG variation voice.
    xg_voice = _get_xg_melodic_voices().get(stem_key)
    if xg_voice is None:
        return voice
    # Preserve the mixer settings (channel / volume / expression / pan) from
    # the base voice --- those are per-stem mixer decisions, not part of the
    # voice selection.
    return VoiceMapping(
        label=xg_voice.label,
        program=xg_voice.program,
        bank_msb=xg_voice.bank_msb,
        bank_lsb=xg_voice.bank_lsb,
        channel=voice.channel,
        is_drum=False,
        volume=voice.volume,
        expression=voice.expression,
        pan=voice.pan,
    )


def _resolve_channel(
    stem_key: str,
    voice: VoiceMapping,
    voice_channels: dict[str, int],
    used_channels: set[int],
) -> int:
    if stem_key in voice_channels:
        return voice_channels[stem_key]

    if voice.channel is not None and voice.channel not in used_channels:
        channel = voice.channel
    else:
        channel = 0
        while channel in used_channels or channel == _DRUM_CHANNEL:
            channel += 1
            if channel > 15:
                raise ValueError("no free MIDI channels available for stem mapping")

    voice_channels[stem_key] = channel
    used_channels.add(channel)
    return channel


def _with_channel(voice: VoiceMapping, channel: int) -> VoiceMapping:
    return VoiceMapping(
        label=voice.label,
        program=voice.program,
        bank_msb=voice.bank_msb,
        bank_lsb=voice.bank_lsb,
        channel=channel,
        is_drum=voice.is_drum,
        volume=voice.volume,
        expression=voice.expression,
        pan=voice.pan,
        source=voice.source,
        source_detail=voice.source_detail,
    )


def _apply_soundfont_override(
    voice: VoiceMapping,
    stem_key: str,
    overrides: Mapping[str, SoundfontOverride],
) -> VoiceMapping:
    """If a user-supplied SoundFont override exists for this stem, apply it.

    The default GM voice is preserved as a fallback. When a per-instrument
    match is found, we replace program + bank + label but keep the
    channel / volume / pan / expression from the default voice.
    """
    override = overrides.get(stem_key)
    if override is None:
        return voice
    return VoiceMapping(
        label=override.label,
        program=int(override.program),
        bank_msb=int(override.bank_msb),
        bank_lsb=int(override.bank_lsb),
        channel=voice.channel,
        is_drum=voice.is_drum,
        volume=voice.volume,
        expression=voice.expression,
        pan=voice.pan,
        source="soundfont",
        source_detail=f"bank {override.bank_msb}:{override.bank_lsb} program {override.program}",
    )


def build_soundfont_overrides(
    presets: Sequence,
    *,
    soundfont_name: str | None = None,
) -> list[SoundfontOverride]:
    """Build per-stem SoundFont overrides from a list of `PresetInfo`.

    For every base voice we know about (piano, bass, guitar, strings, ...),
    ask the SoundFont service for the closest preset of the same instrument
    family. Returns an empty list when no presets are supplied.
    """
    if not presets:
        return []
    # Import inside the function to avoid a hard dependency on the
    # SoundFont service at module import time (keeps the mapper usable
    # in environments where the SF service is not available).
    try:
        from app.services.soundfont_service import SoundFontService
    except Exception:
        return []

    svc = SoundFontService()
    overrides: list[SoundfontOverride] = []
    seen_stems: set[str] = set()
    for stem_key, voice in _get_voices().items():
        if voice.is_drum:
            # Drums are handled by the sample-library pipeline, not the
            # SoundFont presets. Skip them here.
            continue
        if stem_key in seen_stems:
            continue
        instrument_type = svc.get_instrument_type_for_gm_program(voice.program)
        mapping = svc.map_gm_to_custom(
            voice.program,
            list(presets),
            instrument_type=instrument_type,
        )
        if mapping is None:
            continue
        overrides.append(
            SoundfontOverride(
                stem_key=stem_key,
                label=mapping.target_name,
                program=mapping.target_program,
                bank_msb=mapping.target_bank_msb,
                bank_lsb=mapping.target_bank_lsb,
            )
        )
        seen_stems.add(stem_key)
    if soundfont_name:
        logger.info(
            "soundfont-mapping: %d stem overrides built from %s",
            len(overrides),
            soundfont_name,
        )
    return overrides


def _reset_message(profile: MidiProfile):
    from mido import Message

    if profile is MidiProfile.XG:
        # Yamaha XG System On.
        return Message("sysex", data=(0x43, 0x10, 0x4C, 0x00, 0x00, 0x7E, 0x00), time=0)
    # General MIDI System On.
    return Message("sysex", data=(0x7E, 0x7F, 0x09, 0x01), time=0)


def _is_conflicting_setup_message(message) -> bool:
    if message.is_meta:
        return False
    if message.type == "program_change":
        return True
    return bool(message.type == "control_change" and message.control in _BANK_CONTROLLERS)
