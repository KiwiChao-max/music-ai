"""add FK constraints and missing indexes

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-01

Adds:
1. Foreign key constraint ``sample_files.library_id -> sample_libraries.id``
   with ON DELETE CASCADE so deleting a library automatically cleans up
   its sample files.
2. Foreign key constraint ``soundfont_presets.soundfont_id -> soundfonts.id``
   with ON DELETE CASCADE for the same reason.
3. Index ``ix_refresh_tokens_expires_at`` to speed up the periodic
   ``purge_expired_tokens`` cleanup query.
4. Index ``ix_audio_tasks_finished_at`` to support TTL-based cleanup of
   old finished tasks.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Foreign keys --- SQLite needs batch_alter_table for FK additions.
    if dialect == "postgresql":
        op.create_foreign_key(
            "fk_sample_files_library_id",
            "sample_files",
            "sample_libraries",
            ["library_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_foreign_key(
            "fk_soundfont_presets_soundfont_id",
            "soundfont_presets",
            "soundfonts",
            ["soundfont_id"],
            ["id"],
            ondelete="CASCADE",
        )
    else:
        with op.batch_alter_table("sample_files") as batch_op:
            batch_op.create_foreign_key(
                "fk_sample_files_library_id",
                "sample_libraries",
                ["library_id"],
                ["id"],
                ondelete="CASCADE",
            )
        with op.batch_alter_table("soundfont_presets") as batch_op:
            batch_op.create_foreign_key(
                "fk_soundfont_presets_soundfont_id",
                "soundfonts",
                ["soundfont_id"],
                ["id"],
                ondelete="CASCADE",
            )

    # Missing indexes for cleanup/TTL queries.
    op.create_index(
        "ix_refresh_tokens_expires_at",
        "refresh_tokens",
        ["expires_at"],
    )
    op.create_index(
        "ix_audio_tasks_finished_at",
        "audio_tasks",
        ["finished_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audio_tasks_finished_at", table_name="audio_tasks")
    op.drop_index("ix_refresh_tokens_expires_at", table_name="refresh_tokens")

    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.drop_constraint("fk_sample_files_library_id", "sample_files", type_="foreignkey")
        op.drop_constraint(
            "fk_soundfont_presets_soundfont_id", "soundfont_presets", type_="foreignkey"
        )
    else:
        with op.batch_alter_table("sample_files") as batch_op:
            batch_op.drop_constraint("fk_sample_files_library_id", type_="foreignkey")
        with op.batch_alter_table("soundfont_presets") as batch_op:
            batch_op.drop_constraint(
                "fk_soundfont_presets_soundfont_id", type_="foreignkey"
            )
