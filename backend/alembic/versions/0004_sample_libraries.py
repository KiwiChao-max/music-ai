"""add sample_libraries and sample_files tables

Revision ID: 0004_sample_libraries
Revises: 0003_add_chinese_comments
Create Date: 2026-06-26

Adds the user sample-library feature: each user can upload a folder of
samples (e.g. one kick.wav, one snare.wav, ...) under a named library;
the worker / frontend then render drum MIDI with those samples instead of
the default GM bank.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_sample_libraries"
down_revision: Union[str, None] = "0003_add_chinese_comments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sample_libraries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "provider",
            sa.String(length=64),
            nullable=False,
            server_default="drum_kit",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Only one library can be active at a time.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_libraries_one_active
        ON sample_libraries (is_active)
        WHERE is_active = 1;
        """
    )

    op.create_table(
        "sample_files",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("library_id", sa.BigInteger(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("midi_note", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("velocity_offset", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_sample_files_library_id", "sample_files", ["library_id"])
    op.create_index("ix_sample_files_midi_note", "sample_files", ["midi_note"])
    # Range check on GM percussion note range. Drum samples only — melodic
    # samples use a separate provider key, not this table.
    op.execute(
        """
        ALTER TABLE sample_files
        ADD CONSTRAINT chk_sample_files_drum_note
        CHECK (midi_note BETWEEN 35 AND 81);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sample_files DROP CONSTRAINT IF EXISTS chk_sample_files_drum_note;")
    op.drop_index("ix_sample_files_midi_note", table_name="sample_files")
    op.drop_index("ix_sample_files_library_id", table_name="sample_files")
    op.drop_table("sample_files")
    op.execute("DROP INDEX IF EXISTS ux_sample_libraries_one_active;")
    op.drop_table("sample_libraries")
