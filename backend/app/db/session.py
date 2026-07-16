"""SQLAlchemy 2.x engine, session factory and FastAPI dependency."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def _build_engine():
    """Construct the SQLAlchemy engine with pool sizing for Postgres.

    SQLite (used by the test suite and the CI smoke run) ignores
    `pool_size` / `max_overflow` / `pool_recycle` --- those options are
    Postgres-only and would raise if passed to the SQLite default
    `SingletonThreadPool` / `StaticPool`. We branch on the URL scheme so
    the same module works for both.
    """
    url = settings.sqlalchemy_url
    kwargs: dict = {
        "future": True,
    }
    # `pool_pre_ping` works for both backends and cheaply guards against
    # stale connections (the server restarted, the network blipped).
    kwargs["pool_pre_ping"] = True

    if not url.startswith("sqlite"):
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        # Recycle connections before the server-side idle timeout fires.
        # Postgres doesn't have one by default, but managed offerings
        # (RDS, Cloud SQL) and intermediate proxies (PgBouncer) often do.
        kwargs["pool_recycle"] = settings.db_pool_recycle_seconds

    return create_engine(url, **kwargs)


# `future=True` is the default in SQLAlchemy 2.x but we set it explicitly for
# readability. `pool_pre_ping` drops dead connections on next checkout.
engine = _build_engine()

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    class_=Session,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session and closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
