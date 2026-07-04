"""Pytest configuration and shared fixtures.

Strategy: keep the test DB on an in-memory SQLite. The production app targets
PostgreSQL but every business-logic test in this repo only exercises portable
SQLAlchemy constructs (BigInteger, JSON, native enum via CHECK constraint,
DateTime). The Alembic migrations themselves are not run here — the test
session creates the schema straight from the model metadata.
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
    """In-memory SQLite session bound to a fresh schema for each test."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    SessionTesting = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = SessionTesting()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
