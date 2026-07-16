"""add owner_id to sample_libraries and soundfonts

Revision ID: 0010_sample_library_owner
Revises: 0009_refresh_tokens
Create Date: 2026-07-16

Adds nullable owner_id FK columns to sample_libraries and soundfonts so
that each resource is tied to a user.  Only the owner (or an admin) may
modify or delete the resource.  Existing rows keep owner_id=NULL, which
is treated as "owned by nobody" --- admins can still manage them.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_sample_library_owner"
down_revision: Union[str, None] = "0009_refresh_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sample_libraries",
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_sample_libraries_owner_id", "sample_libraries", ["owner_id"])

    op.add_column(
        "soundfonts",
        sa.Column(
            "owner_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_soundfonts_owner_id", "soundfonts", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_soundfonts_owner_id", table_name="soundfonts")
    op.drop_column("soundfonts", "owner_id")
    op.drop_index("ix_sample_libraries_owner_id", table_name="sample_libraries")
    op.drop_column("sample_libraries", "owner_id")