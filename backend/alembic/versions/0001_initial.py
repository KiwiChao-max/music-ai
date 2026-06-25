"""initial schema: audio_tasks + audio_task_status enum + updated_at trigger

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-25

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Single DDL block keeps enum/table/trigger creation atomic on a clean DB.
    # It is also safe to re-run via `alembic upgrade head` -> `alembic downgrade base`
    # -> `alembic upgrade head` because the type and table are dropped in downgrade.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'audio_task_status') THEN
                CREATE TYPE audio_task_status AS ENUM (
                    'UPLOADED', 'PROCESSING', 'FINISHED', 'FAILED'
                );
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE TABLE audio_tasks (
            id          BIGSERIAL PRIMARY KEY,
            filename    VARCHAR(512) NOT NULL,
            status      audio_task_status NOT NULL DEFAULT 'UPLOADED',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """
    )

    op.execute(
        "CREATE INDEX idx_audio_tasks_status ON audio_tasks (status);"
    )
    op.execute(
        "CREATE INDEX idx_audio_tasks_created_at ON audio_tasks (created_at DESC);"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS set_audio_tasks_updated_at ON audio_tasks;
        CREATE TRIGGER set_audio_tasks_updated_at
        BEFORE UPDATE ON audio_tasks
        FOR EACH ROW
        EXECUTE FUNCTION trg_set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS set_audio_tasks_updated_at ON audio_tasks;")
    op.execute("DROP FUNCTION IF EXISTS trg_set_updated_at();")
    op.execute("DROP INDEX IF EXISTS idx_audio_tasks_created_at;")
    op.execute("DROP INDEX IF EXISTS idx_audio_tasks_status;")
    op.execute("DROP TABLE IF EXISTS audio_tasks;")
    op.execute("DROP TYPE IF EXISTS audio_task_status;")
