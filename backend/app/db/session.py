"""SQLAlchemy 2.x engine, session factory and FastAPI dependency."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# `future=True` is the default in SQLAlchemy 2.x but we set it explicitly for
# readability. `pool_pre_ping` drops dead connections on next checkout.
engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    future=True,
)

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
