"""fix is_active columns: Integer -> Boolean

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-01

Changes ``sample_libraries.is_active`` and ``soundfonts.is_active`` from
``INTEGER`` (0/1) to proper ``BOOLEAN`` columns. Also updates the partial
unique index on sample_libraries to use ``WHERE is_active = true`` instead
of ``WHERE is_active = 1``.

SQLite is type-flexible so no explicit cast is needed; PostgreSQL uses
``USING (is_active::boolean)`` to convert existing 0/1 values to false/true.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Drop the partial unique index first (it references the old column).
        op.execute("DROP INDEX IF EXISTS ux_sample_libraries_one_active;")

        # Alter columns: INTEGER -> BOOLEAN with explicit cast.
        op.execute(
            "ALTER TABLE sample_libraries "
            "ALTER COLUMN is_active DROP DEFAULT,"
            "ALTER COLUMN is_active TYPE boolean USING (is_active::boolean),"
            "ALTER COLUMN is_active SET DEFAULT false;"
        )
        op.execute(
            "ALTER TABLE soundfonts "
            "ALTER COLUMN is_active DROP DEFAULT,"
            "ALTER COLUMN is_active TYPE boolean USING (is_active::boolean),"
            "ALTER COLUMN is_active SET DEFAULT false;"
        )

        # Recreate partial unique index with boolean semantics.
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_libraries_one_active "
            "ON sample_libraries (is_active) "
            "WHERE is_active = true;"
        )
    else:
        # SQLite: type affinity means INTEGER 0/1 works as BOOLEAN already;
        # just ensure server_default is correct.
        with op.batch_alter_table("sample_libraries") as batch_op:
            batch_op.alter_column(
                "is_active",
                existing_type=sa.Integer(),
                type_=sa.Boolean(),
                existing_nullable=False,
                server_default=sa.false(),
            )
        with op.batch_alter_table("soundfonts") as batch_op:
            batch_op.alter_column(
                "is_active",
                existing_type=sa.Integer(),
                type_=sa.Boolean(),
                existing_nullable=False,
                server_default=sa.false(),
            )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("DROP INDEX IF EXISTS ux_sample_libraries_one_active;")

        op.execute(
            "ALTER TABLE sample_libraries "
            "ALTER COLUMN is_active DROP DEFAULT,"
            "ALTER COLUMN is_active TYPE integer USING (is_active::integer),"
            "ALTER COLUMN is_active SET DEFAULT 0;"
        )
        op.execute(
            "ALTER TABLE soundfonts "
            "ALTER COLUMN is_active DROP DEFAULT,"
            "ALTER COLUMN is_active TYPE integer USING (is_active::integer),"
            "ALTER COLUMN is_active SET DEFAULT 0;"
        )

        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_sample_libraries_one_active "
            "ON sample_libraries (is_active) "
            "WHERE is_active = 1;"
        )
    else:
        with op.batch_alter_table("sample_libraries") as batch_op:
            batch_op.alter_column(
                "is_active",
                existing_type=sa.Boolean(),
                type_=sa.Integer(),
                existing_nullable=False,
                server_default=sa.text("0"),
            )
        with op.batch_alter_table("soundfonts") as batch_op:
            batch_op.alter_column(
                "is_active",
                existing_type=sa.Boolean(),
                type_=sa.Integer(),
                existing_nullable=False,
                server_default=sa.text("0"),
            )
