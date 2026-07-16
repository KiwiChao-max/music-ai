"""add LLM commentary columns to audio_tasks

Revision ID: 0006_commentary
Revises: 0005_users
Create Date: 2026-06-28

Adds three nullable columns to ``audio_tasks``:

* ``commentary`` (TEXT) --- the LLM-generated human-readable blurb.
* ``commentary_model`` (VARCHAR(64)) --- which model produced it, e.g.
  ``mock`` or ``gpt-4o-mini``. Useful for both auditing and for
  telling users when the placeholder mock was used.
* ``commentary_generated_at`` (TIMESTAMPTZ) --- when the worker wrote
  the commentary. Lets the frontend show a "generated 3 minutes ago"
  hint and lets ops run a query for "tasks that finished but never
  got a commentary" so they can re-run the LLM pass.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_commentary"
down_revision: Union[str, None] = "0005_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audio_tasks",
        sa.Column("commentary", sa.Text(), nullable=True),
    )
    op.add_column(
        "audio_tasks",
        sa.Column("commentary_model", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "audio_tasks",
        sa.Column("commentary_generated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("audio_tasks", "commentary_generated_at")
    op.drop_column("audio_tasks", "commentary_model")
    op.drop_column("audio_tasks", "commentary")
