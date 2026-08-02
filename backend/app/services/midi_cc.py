"""Shared MIDI control-change helpers.

The basic-pitch and drum writers both need to emit the same GM/XG setup
controllers (CC7 volume, CC10 pan, CC11 expression, CC64 sustain, CC74
brightness, CC91 reverb, CC93 chorus) so that mapped MIDI files sound
consistent in any player. Centralizing the helper here keeps the per-writer
code short and makes it easy to add new CCs without touching every writer.

CC reference:
  CC1   Modulation wheel
  CC7   Channel volume
  CC10  Pan
  CC11  Expression
  CC64  Sustain pedal (on/off)
  CC74  Brightness / filter cutoff (Timbre/Harmonic Content)
  CC91  Reverb send level
  CC93  Chorus send level
"""

from __future__ import annotations

import math

from mido import Message


def gm_setup_messages(
    channel: int,
    *,
    program: int,
    bank_msb: int = 0,
    bank_lsb: int = 0,
    volume: int = 100,
    expression: int = 127,
    pan: int = 64,
    sustain: int = 0,
    brightness: int | None = None,
    reverb: int | None = None,
    chorus: int | None = None,
    modulation: int | None = None,
) -> list[Message]:
    """Return the standard GM reset/setup messages for one channel.

    The returned messages are in delta-time-zero form; the caller is
    responsible for threading them into a track with the correct
    accumulated times.

    Optional expressive CCs (brightness, reverb, chorus, modulation)
    are only emitted when a non-None value is supplied, so drum tracks
    and other non-melodic stems can skip them.
    """
    msgs: list[Message] = [
        Message("control_change", channel=channel, control=0, value=bank_msb, time=0),
        Message("control_change", channel=channel, control=32, value=bank_lsb, time=0),
        Message("program_change", channel=channel, program=program, time=0),
        Message("control_change", channel=channel, control=7, value=volume, time=0),
        Message("control_change", channel=channel, control=11, value=expression, time=0),
        Message("control_change", channel=channel, control=10, value=pan, time=0),
        Message("control_change", channel=channel, control=64, value=sustain, time=0),
    ]
    if brightness is not None:
        msgs.append(
            Message("control_change", channel=channel, control=74, value=brightness, time=0)
        )
    if reverb is not None:
        msgs.append(Message("control_change", channel=channel, control=91, value=reverb, time=0))
    if chorus is not None:
        msgs.append(Message("control_change", channel=channel, control=93, value=chorus, time=0))
    if modulation is not None:
        msgs.append(Message("control_change", channel=channel, control=1, value=modulation, time=0))
    return msgs


def pitch_bend_message(channel: int, value: int = 0) -> Message:
    """Wrap a pitch-bend message. `value` is the 14-bit signed bend value."""
    return Message("pitchwheel", channel=channel, pitch=value, time=0)


def sustain_messages(channel: int, *, down_at: int, up_at: int) -> list[Message]:
    """Emit a CC64 (sustain pedal) on/off pair at the given tick times.

    Pass `down_at` for the start of the held note and `up_at` for the end.
    The caller is responsible for threading the actual delta times.
    """
    return [
        Message("control_change", channel=channel, control=64, value=127, time=down_at),
        Message("control_change", channel=channel, control=64, value=0, time=up_at),
    ]


# Velocity curve: maps a [0, 1] normalized signal to a 1..127 velocity.
def velocity_from_strength(normalized: float) -> int:
    normalized = max(0.0, min(1.0, normalized))
    return max(35, min(127, round(40 + 87 * math.sqrt(normalized))))
