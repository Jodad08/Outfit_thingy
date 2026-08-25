"""SQLAlchemy engine / session setup for the local SQLite database."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config

# check_same_thread=False lets FastAPI's threadpool share the SQLite connection.
engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False}
    if config.DATABASE_URL.startswith("sqlite")
    else {},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all ORM models."""


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a scoped database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables and seed the singleton preferences row."""
    from . import models  # noqa: F401  (register models on Base)

    config.ensure_dirs()
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        if db.get(models.Preferences, 1) is None:
            db.add(models.Preferences(id=1))
            db.commit()
