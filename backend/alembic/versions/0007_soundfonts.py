"""add soundfonts and soundfont_presets tables

Revision ID: 0007_soundfonts
Revises: 0006_commentary
Create Date: 2026-07-01

Adds two new tables for SoundFont management:

* ``soundfonts`` --- one row per imported SoundFont file or CSV preset table.
  Tracks name, description, type (sf2/preset_table), file path, preset count,
  and active state.
* ``soundfont_presets`` --- individual presets within a SoundFont, keyed by
  bank_msb/bank_lsb/program per the MIDI MTC spec.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_soundfonts"
down_revision: Union[str, None] = "0006_commentary"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "soundfonts",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(length=32), server_default="sf2", nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=True),
        sa.Column("preset_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "soundfont_presets",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False),
        sa.Column("soundfont_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("bank_msb", sa.Integer(), server_default="0", nullable=False),
        sa.Column("bank_lsb", sa.Integer(), server_default="0", nullable=False),
        sa.Column("program", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("instrument_type", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "soundfont_id", "bank_msb", "bank_lsb", "program",
            name="ux_sf_preset_bank_program",
        ),
    )
    op.create_index("ix_soundfont_presets_soundfont_id", "soundfont_presets", ["soundfont_id"])
    op.create_index("ix_soundfont_presets_program", "soundfont_presets", ["program"])


def downgrade() -> None:
    op.drop_index("ix_soundfont_presets_program", table_name="soundfont_presets")
    op.drop_index("ix_soundfont_presets_soundfont_id", table_name="soundfont_presets")
    op.drop_table("soundfont_presets")
    op.drop_table("soundfonts")
