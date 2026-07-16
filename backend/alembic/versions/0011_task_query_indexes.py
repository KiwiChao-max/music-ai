"""composite indexes for audio_tasks query performance

Adds:

1. ``idx_audio_tasks_user_created`` on ``(user_id, created_at DESC)``
   Speeds up the ``WHERE user_id = ? ORDER BY created_at DESC`` query
   that every non-admin user hits on the task list page.  Without this
   index PostgreSQL either:
   - Uses ``ix_audio_tasks_user_id`` to filter by user, then does a
     full sort on ``created_at`` (expensive for large result sets).
   - Uses ``idx_audio_tasks_created_at`` to scan in date order, then
     filters by ``user_id`` (expensive when a user has few tasks
     scattered across a large table).
   The composite index allows both filtering and ordering in a single
   index scan, eliminating the sort step.

2. ``idx_audio_tasks_user_status_created`` on ``(user_id, status, created_at DESC)``
   Supports the ``WHERE user_id = ? AND status = ? ORDER BY created_at DESC``
   pattern when the API adds optional status filtering.  The leading
   ``user_id`` column also serves as a fallback for the pure user-id
   query if the first index is not chosen by the planner.

3. ``idx_audio_tasks_status_created`` on ``(status, created_at DESC)``
   Speeds up admin queries that filter by status (e.g. "show all
   FAILED tasks") and the ``count_tasks_by_status`` aggregation.
   Without this, ``GROUP BY status`` must scan the entire table or
   the single-column ``idx_audio_tasks_status`` index.

These indexes are especially valuable when the audio_tasks table grows
to thousands of rows and the pagination queries (``ORDER BY ...
LIMIT ... OFFSET ...``) need to skip large offsets efficiently.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-16
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_tasks_user_created "
        "ON audio_tasks (user_id, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_tasks_user_status_created "
        "ON audio_tasks (user_id, status, created_at DESC);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audio_tasks_status_created "
        "ON audio_tasks (status, created_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audio_tasks_status_created;")
    op.execute("DROP INDEX IF EXISTS idx_audio_tasks_user_status_created;")
    op.execute("DROP INDEX IF EXISTS idx_audio_tasks_user_created;")