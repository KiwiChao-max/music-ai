"""add processing state columns to audio_tasks

Adds: progress, current_step, duration, output_dir, error_message, finished_at.

Revision ID: 0002_progress_fields
Revises: 0001_initial
Create Date: 2026-06-25

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_progress_fields"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audio_tasks",
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "audio_tasks",
        sa.Column("current_step", sa.Text(), nullable=True),
    )
    op.add_column(
        "audio_tasks",
        sa.Column("duration", sa.Float(), nullable=True),
    )
    op.add_column(
        "audio_tasks",
        sa.Column("output_dir", sa.Text(), nullable=True),
    )
    op.add_column(
        "audio_tasks",
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "audio_tasks",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Range check guard so the worker can't accidentally write 150%.
    op.execute(
        """
        ALTER TABLE audio_tasks
        ADD CONSTRAINT chk_audio_tasks_progress
        CHECK (progress BETWEEN 0 AND 100);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE audio_tasks DROP CONSTRAINT IF EXISTS chk_audio_tasks_progress;")
    op.drop_column("audio_tasks", "finished_at")
    op.drop_column("audio_tasks", "error_message")
    op.drop_column("audio_tasks", "output_dir")
    op.drop_column("audio_tasks", "duration")
    op.drop_column("audio_tasks", "current_step")
    op.drop_column("audio_tasks", "progress")
