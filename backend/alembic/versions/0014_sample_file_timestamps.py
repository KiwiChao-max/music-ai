"""add created_at / updated_at to sample_files

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-01

Adds ``created_at`` and ``updated_at`` timestamp columns to ``sample_files``
so that sample metadata carries audit timestamps consistent with every other
table in the schema. Existing rows are backfilled with the current time.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns as nullable first so we can backfill existing rows.
    op.add_column(
        "sample_files",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "sample_files",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )
    # Backfill any existing rows that were inserted before this migration
    # (paranoia: with server_default in place, new rows already get NOW();
    #  old rows inserted before the column existed have NULL).
    op.execute(
        "UPDATE sample_files SET created_at = NOW() WHERE created_at IS NULL;"
    )
    op.execute(
        "UPDATE sample_files SET updated_at = NOW() WHERE updated_at IS NULL;"
    )
    # Now make the columns NOT NULL.
    op.alter_column(
        "sample_files",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_server_default=sa.func.now(),
        nullable=False,
    )
    op.alter_column(
        "sample_files",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        existing_server_default=sa.func.now(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("sample_files", "updated_at")
    op.drop_column("sample_files", "created_at")
