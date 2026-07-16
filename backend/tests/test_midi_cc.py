"""Tests for `app.services.midi_cc` and the GM setup messages it produces.

The MIDI writers in this project all funnel through `gm_setup_messages` so
that every generated MIDI file ships the same controller reset
(CC0/CC32/program/CC7/CC11/CC10/CC64). These tests pin the exact messages
so that a change to the setup sequence is a deliberate, reviewable event.
"""
from __future__ import annotations

from mido import Message

from app.services.midi_cc import (
    gm_setup_messages,
    pitch_bend_message,
    sustain_messages,
    velocity_from_strength,
)


def test_gm_setup_messages_returns_controller_reset_in_canonical_order() -> None:
    messages = gm_setup_messages(channel=0, program=4)
    types_and_controls = [
        (msg.type, getattr(msg, "control", None), getattr(msg, "program", None))
        for msg in messages
    ]
    # Bank MSB, Bank LSB, Program, Volume, Expression, Pan, Sustain.
    assert types_and_controls == [
        ("control_change", 0, None),
        ("control_change", 32, None),
        ("program_change", None, 4),
        ("control_change", 7, None),
        ("control_change", 11, None),
        ("control_change", 10, None),
        ("control_change", 64, None),
    ]
    # Every message must be on the requested channel with delta time 0 ---
    # the caller is responsible for threading accumulated times.
    for msg in messages:
        assert msg.channel == 0
        assert msg.time == 0


def test_gm_setup_messages_honors_custom_values() -> None:
    messages = gm_setup_messages(
        channel=3,
        program=24,
        bank_msb=121,
        bank_lsb=2,
        volume=80,
        expression=100,
        pan=32,
        sustain=127,
    )
    by_control = {msg.control: msg.value for msg in messages if msg.type == "control_change"}
    assert by_control[0] == 121
    assert by_control[32] == 2
    assert by_control[7] == 80
    assert by_control[11] == 100
    assert by_control[10] == 32
    assert by_control[64] == 127
    program = next(msg for msg in messages if msg.type == "program_change")
    assert program.program == 24
    assert program.channel == 3


def test_pitch_bend_message_zero_is_default() -> None:
    msg = pitch_bend_message(channel=1)
    assert isinstance(msg, Message)
    assert msg.type == "pitchwheel"
    assert msg.channel == 1
    assert msg.pitch == 0


def test_sustain_messages_emits_pedal_down_then_up() -> None:
    messages = sustain_messages(channel=2, down_at=100, up_at=500)
    assert [msg.control for msg in messages] == [64, 64]
    assert [msg.value for msg in messages] == [127, 0]
    # The caller is responsible for threading the absolute tick offsets; we
    # only assert that the helper itself emits both events.
    assert messages[0].time == 100
    assert messages[1].time == 500


def test_velocity_from_strength_clamps_to_midi_range() -> None:
    # The curve is sqrt-shaped: floor is the formula at strength=0.0
    # (40 + 87*0 = 40) and the ceiling is the formula at strength=1.0
    # (40 + 87*1 = 127, then clamped).
    assert velocity_from_strength(0.0) == 40
    # Negative input is clamped to 0 by the implementation.
    assert velocity_from_strength(-1.0) == 40
    # Loudest possible input caps at 127.
    assert velocity_from_strength(1.0) == 127
    assert velocity_from_strength(2.0) == 127
    # Mid-range produces a value strictly above the floor and below the
    # ceiling --- useful for the per-frame strength -> velocity curve.
    assert 40 < velocity_from_strength(0.5) < 127
