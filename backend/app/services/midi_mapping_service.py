"""Stem-aware GM / XG mapping for MIDI files produced by the audio pipeline.

The mapper keeps the detected notes intact, but makes the MIDI predictable for
General MIDI and Yamaha XG players: it inserts reset, bank-select,
program-change, volume/expression/pan setup, and assigns stable channels per
stem. When several raw stem MIDI files exist, they are merged into one mapped
arrangement while preserving tempo and note timing.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class MidiProfile(str, Enum):
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


@dataclass(frozen=True)
class MidiMappingResult:
    """Paths written by `create_variants_for_sources`."""

    source_paths: tuple[Path, ...]
    gm_path: Path
    xg_path: Path

    @property
    def source_path(self) -> Path:
        """Backward-compatible shorthand for callers that map one MIDI file."""
        return self.source_paths[0]


_DRUM_CHANNEL = 9
_MIDI_SUFFIXES = {".mid", ".midi"}
_GENERATED_PROFILE_SUFFIXES = ("_gm", "_xg")
_GENERATED_DRUM_PART_SUFFIXES = tuple(
    f"_{part}" for part in ("kick", "snare", "hat", "tom", "cymbal", "fill")
)
_BANK_CONTROLLERS = {0, 32}

_STEM_ORDER: dict[str, int] = {
    "drums": 0,
    "bass": 1,
    "piano": 2,
    "other": 3,
    "vocals": 4,
    "guitar": 5,
    "strings": 6,
    "original": 7,
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
    ("other", "other"),
    ("accompaniment", "other"),
    ("original", "original"),
    ("mix", "original"),
)

# GM programs are zero-based here:
#   0  Acoustic Grand Piano
#   24 Acoustic Guitar (nylon)
#   33 Electric Bass (finger)
#   48 String Ensemble 1
#   53 Voice Oohs
#   89 Pad 2 (warm)
_BASE_VOICES: dict[str, VoiceMapping] = {
    "original": VoiceMapping("Acoustic Grand Piano", program=0, channel=0),
    "piano": VoiceMapping("Acoustic Grand Piano", program=0, channel=0),
    "bass": VoiceMapping("Electric Bass (finger)", program=33, channel=1, volume=108, pan=54),
    "other": VoiceMapping("Warm Pad", program=89, channel=2, volume=92, pan=74),
    "vocals": VoiceMapping("Voice Oohs", program=53, channel=3, volume=94, pan=64),
    "guitar": VoiceMapping("Acoustic Guitar (nylon)", program=24, channel=4, volume=98, pan=44),
    "strings": VoiceMapping("String Ensemble 1", program=48, channel=5, volume=96, pan=84),
    "drums": VoiceMapping(
        "Standard Drum Kit",
        program=0,
        channel=_DRUM_CHANNEL,
        is_drum=True,
        volume=112,
    ),
}


def midi_profile_from_name(name: str) -> str:
    """Infer the public profile label from an output filename stem."""
    lower = name.lower()
    if lower.endswith("_gm"):
        return MidiProfile.GM.value
    if lower.endswith("_xg"):
        return MidiProfile.XG.value
    return "raw"


def is_raw_midi_path(path: Path) -> bool:
    """Return true for source MIDI files, false for generated GM/XG outputs."""
    if path.suffix.lower() not in _MIDI_SUFFIXES:
        return False
    stem = path.stem.lower()
    if _strip_profile_suffix(path.stem).lower() != stem:
        return False
    return not stem.endswith(_GENERATED_DRUM_PART_SUFFIXES)


def collect_raw_midi_sources(output_dir: Path, fallback: Path | None = None) -> list[Path]:
    """Collect raw MIDI files from a task output directory in musical order."""
    sources = [p for p in output_dir.iterdir() if p.is_file() and is_raw_midi_path(p)]
    if fallback is not None and fallback.is_file() and fallback not in sources:
        sources.append(fallback)
    return sorted(sources, key=lambda p: (_STEM_ORDER.get(stem_key_from_name(p.stem), 99), p.name.lower()))


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
    ) -> MidiMappingResult:
        """Backward-compatible one-file mapping entry point."""
        return self.create_variants_for_sources([source_path], output_dir)

    def create_variants_for_sources(
        self,
        source_paths: Sequence[Path],
        output_dir: Path | None = None,
    ) -> MidiMappingResult:
        sources = tuple(source_paths)
        if not sources:
            raise ValueError("at least one source MIDI is required")

        output_dir = output_dir or sources[0].parent
        output_dir.mkdir(parents=True, exist_ok=True)
        output_stem = sources[0].stem if len(sources) == 1 else "stems"

        gm_path = output_dir / f"{output_stem}_{MidiProfile.GM.value}.mid"
        xg_path = output_dir / f"{output_stem}_{MidiProfile.XG.value}.mid"

        self.map_sources(sources, gm_path, MidiProfile.GM)
        self.map_sources(sources, xg_path, MidiProfile.XG)

        return MidiMappingResult(source_paths=sources, gm_path=gm_path, xg_path=xg_path)

    def map_file(self, source_path: Path, output_path: Path, profile: MidiProfile) -> Path:
        """Write one mapped MIDI file and return `output_path`."""
        return self.map_sources([source_path], output_path, profile)

    def map_sources(
        self,
        source_paths: Sequence[Path],
        output_path: Path,
        profile: MidiProfile,
    ) -> Path:
        """Merge and map raw MIDI sources into one profile-specific MIDI file.

        Import mido lazily so the API can serve task/stem metadata in light
        environments where only the worker has audio dependencies installed.
        """
        from mido import MidiFile

        if not source_paths:
            raise ValueError("at least one source MIDI is required")

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

        for source_index, (source_path, source_midi) in enumerate(source_midis):
            for track in source_midi.tracks:
                if not _has_channel_messages(track):
                    if source_index == 0:
                        mapped_tracks.append(self._copy_meta_track(track))
                    continue

                stem_key = _detect_stem_key(source_path.stem, track)
                voice = _voice_for_profile(_BASE_VOICES[stem_key], profile)
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
            "midi-mapping: wrote %s profile=%s sources=%d tracks=%d",
            output_path.name,
            profile.value,
            len(source_paths),
            len(target.tracks),
        )
        return output_path

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
                    Message("control_change", channel=channel, control=0, value=voice.bank_msb, time=0),
                    Message("control_change", channel=channel, control=32, value=voice.bank_lsb, time=0),
                    Message("program_change", channel=channel, program=voice.program, time=0),
                    Message("control_change", channel=channel, control=7, value=voice.volume, time=0),
                    Message("control_change", channel=channel, control=11, value=voice.expression, time=0),
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
            scaled_time = int(round(message.time * scale))
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


def _voice_for_profile(voice: VoiceMapping, profile: MidiProfile) -> VoiceMapping:
    if profile is MidiProfile.XG and voice.is_drum:
        return VoiceMapping(
            label=voice.label,
            program=voice.program,
            bank_msb=127,
            bank_lsb=0,
            channel=voice.channel,
            is_drum=True,
            volume=voice.volume,
            expression=voice.expression,
            pan=voice.pan,
        )
    return voice


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
    )


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
    if message.type == "control_change" and message.control in _BANK_CONTROLLERS:
        return True
    return False
