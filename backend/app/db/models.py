"""ORM models.

The `audio_task_status` Postgres enum is declared explicitly so the same name
matches the column type. We keep the trigger that auto-updates `updated_at` in
the initial Alembic migration (raw SQL) since SQLAlchemy does not manage DDL
triggers.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    Float,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AudioTaskStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class AudioTask(Base):
    __tablename__ = "audio_tasks"

    # PostgreSQL uses `BIGSERIAL` for autoincrement. SQLite's rowid-based
    # autoincrement only fires on `INTEGER PRIMARY KEY`, so the test setup
    # (which runs against an in-memory SQLite) needs the column typed as
    # plain `Integer` there. The variant keeps the production schema on
    # BigInteger while making the model portable.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[AudioTaskStatus] = mapped_column(
        SAEnum(
            AudioTaskStatus,
            name="audio_task_status",
            native_enum=True,
            create_constraint=False,
        ),
        nullable=False,
        default=AudioTaskStatus.UPLOADED,
        server_default=AudioTaskStatus.UPLOADED.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # ---- processing state (Milestone 1+) ---------------------------------
    # 0-100 percent; default 0 so a row freshly inserted reads as "not started".
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Short human-readable description of the current step, e.g. "分离鼓组..."
    # Stored as TEXT to keep it free-form and localized in the future.
    current_step: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Audio length in seconds; populated on upload by probing the file.
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Where the worker writes its outputs (stems, MIDI, ...). Nullable until
    # the worker has actually created the directory.
    output_dir: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Last failure message; cleared on the next successful run.
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Set when the task reaches FINISHED or FAILED. Nullable while in flight.
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<AudioTask id={self.id} filename={self.filename!r} status={self.status}>"
