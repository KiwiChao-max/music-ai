"""Pytest configuration and shared fixtures.

Strategy: default to an in-memory SQLite for fast local runs. When the
env var ``TEST_DATABASE_URL`` is set (e.g. to a Postgres URL in CI), the
fixtures connect to that database instead, so Postgres-specific SQL
issues are caught before merge. The Alembic migrations themselves are
not run here — the test session creates the schema straight from the
model metadata.
"""
from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Force a known storage dir *before* the app's settings module is imported
# anywhere in the test process. The default would resolve to `<repo>/storage`
# which then leaks test artifacts into the repo tree.
os.environ.setdefault("STORAGE_DIR", str(Path(__file__).resolve().parent / ".tmp-storage"))
# Disable rate limiting for the full test suite (many auth/login calls).
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from app.config import settings  # noqa: E402  (imports must come after env setup)
from app.db.base import Base  # noqa: E402
from app.db import models  # noqa: E402, F401  -- ensure models register on Base.metadata

# When set, the test suite connects to this database instead of in-memory
# SQLite. Used by CI to run the suite against real Postgres.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture()
def storage_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the app's storage at a per-test temp directory.

    `settings` is a module-level singleton, so we mutate the underlying
    `Path` objects directly (pydantic models expose mutable fields) and
    restore them after the test.
    """
    real_upload = settings.upload_dir
    real_output = settings.output_dir
    real_storage = settings.storage_dir

    settings.storage_dir = tmp_path
    settings.upload_dir = None
    settings.output_dir = None
    yield tmp_path

    settings.storage_dir = real_storage
    settings.upload_dir = real_upload
    settings.output_dir = real_output


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    """Per-test DB session.

    * Local dev (no ``TEST_DATABASE_URL``): in-memory SQLite with
      ``StaticPool`` — fast, no setup, isolated per test.
    * CI (``TEST_DATABASE_URL`` set): a real Postgres database. Each
      test drops and recreates the schema from the model metadata so
      tests stay isolated. Slower, but catches Postgres-specific SQL
      issues (enum vs CHECK, array columns, timestamptz, etc.).
    """
    if TEST_DATABASE_URL:
        engine = create_engine(TEST_DATABASE_URL, future=True)
        # Drop + recreate the schema for each test so tests don't leak
        # rows into each other. `drop_all` is safe here because the
        # Postgres DB is dedicated to the test run.
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
    else:
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        Base.metadata.create_all(engine)

    SessionTesting = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = SessionTesting()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()

