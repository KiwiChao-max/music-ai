"""add velocity_min / velocity_max to sample_files

Revision ID: 0008_sample_velocity_layers
Revises: 0007_soundfonts
Create Date: 2026-07-03

Adds velocity-layer support to sample_files so a single GM drum note can
map to multiple samples --- one per dynamic range (pp / mf / ff). The
frontend sample player selects the best-matching sample by incoming MIDI
velocity, falling back to the full-range default when no layer is defined.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_sample_velocity_layers"
down_revision: Union[str, None] = "0007_soundfonts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sample_files",
        sa.Column(
            "velocity_min",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "sample_files",
        sa.Column(
            "velocity_max",
            sa.Integer(),
            nullable=False,
            server_default="127",
        ),
    )
    op.execute(
        """
        ALTER TABLE sample_files
        ADD CONSTRAINT chk_sample_files_velocity_range
        CHECK (velocity_min BETWEEN 1 AND 127 AND velocity_max BETWEEN 1 AND 127 AND velocity_min <= velocity_max);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE sample_files DROP CONSTRAINT IF EXISTS chk_sample_files_velocity_range;")
    op.drop_column("sample_files", "velocity_max")
    op.drop_column("sample_files", "velocity_min")