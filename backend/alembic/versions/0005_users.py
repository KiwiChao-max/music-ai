"""add users table and audio_tasks.user_id

Revision ID: 0005_users
Revises: 0004_sample_libraries
Create Date: 2026-06-28

Adds a `users` table for the auth system and a nullable `user_id` FK on
`audio_tasks` so each task is owned by the user who created it. The
column is nullable on purpose: tasks created before this migration must
keep working, and the API will refuse to create a new task without a
user once auth is enabled.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_users"
down_revision: Union[str, None] = "0004_sample_libraries"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=128), nullable=True),
        sa.Column(
            "role",
            sa.String(length=32),
            nullable=False,
            server_default="user",
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "max_tasks",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_upload_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
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
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.UniqueConstraint("email", name="ux_users_email"),
        sa.UniqueConstraint("username", name="ux_users_username"),
    )
    # Case-insensitive uniqueness on email + username. PostgreSQL handles
    # the function index natively; the SQLite test path uses a no-op
    # secondary check (the unique constraint above is still enforced).
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower "
        "ON users (LOWER(email));"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_users_username_lower "
        "ON users (LOWER(username));"
    )

    op.add_column(
        "audio_tasks",
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_audio_tasks_user_id", "audio_tasks", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_audio_tasks_user_id", table_name="audio_tasks")
    op.drop_column("audio_tasks", "user_id")
    op.execute("DROP INDEX IF EXISTS ux_users_username_lower;")
    op.execute("DROP INDEX IF EXISTS ux_users_email_lower;")
    op.drop_table("users")
