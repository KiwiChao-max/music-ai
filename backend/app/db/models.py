"""ORM models.

The `audio_task_status` Postgres enum is declared explicitly so the same name
matches the column type. We keep the trigger that auto-updates `updated_at` in
the initial Alembic migration (raw SQL) since SQLAlchemy does not manage DDL
triggers.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AudioTaskStatus(enum.StrEnum):
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
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Short human-readable description of the current step, e.g. "分离鼓组..."
    # Stored as TEXT to keep it free-form and localized in the future.
    current_step: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Audio length in seconds; populated on upload by probing the file.
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Where the worker writes its outputs (stems, MIDI, ...). Nullable until
    # the worker has actually created the directory.
    output_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Last failure message; cleared on the next successful run.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the task reaches FINISHED or FAILED. Nullable while in flight.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- ownership (Milestone 4) ---------------------------------------
    # Nullable so legacy tasks from before the auth migration keep working.
    # New tasks always have a non-null `user_id`; the API enforces this.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ---- LLM commentary (Milestone 5+) ---------------------------------
    # Filled in by the worker after the analysis step. Nullable so old
    # tasks / failed runs keep working. The string is meant to be
    # rendered as-is on the detail page; the LLM service is responsible
    # for keeping it human-readable.
    commentary: Mapped[str | None] = mapped_column(Text, nullable=True)
    commentary_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commentary_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<AudioTask id={self.id} filename={self.filename!r} status={self.status}>"


class User(Base):
    """Account that owns tasks, sample libraries and a quota.

    `password_hash` is bcrypt. `role` distinguishes regular users from
    admins; the API gates /api/admin/* on admin role. `is_active=False`
    soft-deletes the account without breaking historical FK references.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # Case-insensitive uniqueness is enforced via a functional index in the
    # migration; the column itself stores whatever the user typed at signup.
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="user", server_default="user"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    # Per-user quota knobs. 0 means "use the server default from settings".
    max_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_upload_bytes: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        nullable=False,
        default=0,
        server_default="0",
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
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("email", name="ux_users_email"),
        UniqueConstraint("username", name="ux_users_username"),
    )
    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<User id={self.id} email={self.email!r} role={self.role}>"


class SampleLibrary(Base):
    """User-uploaded sample library used to render drum MIDI with custom kits.

    One row per library. The library has a name and a root directory on
    disk; individual samples live in `SampleFile` rows keyed by GM drum
    note. A library is "active" when `is_active=True`; the worker / API
    picks the most-recently-activated library by default.

    `owner_id` ties the library to a specific user.  When non-null, only
    the owner (or an admin) may modify or delete the library.  Nullable
    for backward compatibility with libraries created before the
    ownership migration.
    """

    __tablename__ = "sample_libraries"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Provider label (e.g. "drum_kit", "soundfont") so the same table can
    # hold different sample types in the future (SoundFont, SFZ, custom).
    provider: Mapped[str] = mapped_column(
        String(64), nullable=False, default="drum_kit", server_default="drum_kit"
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
    # Owner of this library.  Nullable for backward compatibility.
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<SampleLibrary id={self.id} name={self.name!r} active={self.is_active}>"


class SampleFile(Base):
    """One sample in a library, keyed by GM drum note.

    ``midi_note`` is the GM percussion note (35..81) this sample plays for.
    Multiple samples can share a note --- the frontend player selects the
    best match by incoming MIDI velocity using ``velocity_min`` and
    ``velocity_max``, which define the velocity range this sample covers.
    Samples with the default range (1-127) are treated as full-range
    fallbacks.
    """

    __tablename__ = "sample_files"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    library_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("sample_libraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    midi_note: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Relative path under the library's storage directory.
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Default MIDI velocity offset added to the note's velocity on playback.
    velocity_offset: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Velocity layer range (1-127).  The player picks the sample whose
    # [velocity_min, velocity_max] contains the incoming note velocity.
    # Default range 1-127 means "full-range fallback".
    velocity_min: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    velocity_max: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=127,
        server_default="127",
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

    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<SampleFile id={self.id} library_id={self.library_id} note={self.midi_note} v={self.velocity_min}-{self.velocity_max}>"


class SoundFont(Base):
    """SoundFont 2 (.sf2) library or preset table.

    Stores metadata for imported SoundFont files and CSV preset tables so
    users can manage multiple soundbanks and activate one for MIDI playback.

    `owner_id` ties the SoundFont to a specific user.  When non-null, only
    the owner (or an admin) may modify or delete it.
    """

    __tablename__ = "soundfonts"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "sf2" for real SoundFont files, "preset_table" for CSV imports
    type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="sf2", server_default="sf2"
    )
    # Relative path under storage/soundfonts/ (null for CSV preset tables)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    preset_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
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
    # Owner of this SoundFont.  Nullable for backward compatibility.
    owner_id: Mapped[int | None] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<SoundFont id={self.id} name={self.name!r} type={self.type}>"


class SoundFontPreset(Base):
    """A single preset within a SoundFont or preset table.

    Presets are identified by bank_msb/bank_lsb/program (MIDI MTC spec).
    """

    __tablename__ = "soundfont_presets"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    soundfont_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("soundfonts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bank_msb: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    bank_lsb: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    program: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    instrument_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "soundfont_id", "bank_msb", "bank_lsb", "program", name="ux_sf_preset_bank_program"
        ),
    )
    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<SoundFontPreset id={self.id} sf_id={self.soundfont_id} prog={self.program}>"


class RefreshTokenStatus(enum.StrEnum):
    ACTIVE = "active"
    USED = "used"
    REVOKED = "revoked"


class RefreshToken(Base):
    """Server-side record of an issued refresh token.

    Tokens themselves are stateless JWTs, but we store their SHA-256
    hash here so we can:
      * enforce single-use (rotate on refresh)
      * detect token reuse (stolen token -> revoke entire family)
      * revoke all tokens for a user on logout / password change
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    # JWT ID ("jti" claim) --- unique per token, used as the stable
    # identifier for revocation even if we don't have the raw token.
    jti: Mapped[str] = mapped_column(String(36), nullable=False)
    # User who owns this token.
    user_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 of the raw refresh token.  We never store the token in
    # plaintext --- even if the DB is compromised, the attacker cannot
    # reconstruct the original JWT from the hash.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Tracks the token lifecycle: active -> used (rotated) or revoked.
    status: Mapped[RefreshTokenStatus] = mapped_column(
        SAEnum(
            RefreshTokenStatus,
            name="refresh_token_status",
            native_enum=True,
            create_constraint=False,
        ),
        nullable=False,
        default=RefreshTokenStatus.ACTIVE,
        server_default=RefreshTokenStatus.ACTIVE.value,
    )
    # Groups tokens from the same rotation chain.  When a token from
    # this family is reused (indicating theft), we revoke the entire
    # family so the legitimate owner is forced to re-authenticate.
    family_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # When this token record was created.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # When the underlying JWT expires.  Used for cleanup queries.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When this token was consumed (rotated).  Null for active tokens.
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("jti", name="ux_refresh_tokens_jti"),
        Index("ix_refresh_tokens_token_hash", "token_hash"),
        Index("ix_refresh_tokens_family_id", "family_id"),
    )
    __mapper_args__ = {"eager_defaults": True}

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<RefreshToken id={self.id} jti={self.jti!r} status={self.status}>"
