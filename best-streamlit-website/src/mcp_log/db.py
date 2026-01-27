"""MCP Log database engine and session management."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session

from .config import get_config


# Module-level cache for engine
_engine: Optional[Engine] = None


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Get or create the SQLAlchemy engine.

    Args:
        database_url: Optional override for the database URL.
                     If not provided, uses config.

    Returns:
        SQLAlchemy Engine instance.
    """
    global _engine

    if database_url is None:
        database_url = get_config().database_url

    # Return cached engine if URL matches
    if _engine is not None:
        if str(_engine.url) == database_url:
            return _engine

    # Handle SQLite cross-thread issue
    connect_args = {}
    if database_url.startswith("sqlite:"):
        connect_args = {"check_same_thread": False}

    _engine = create_engine(
        database_url,
        connect_args=connect_args,
        echo=False,
        pool_pre_ping=True,  # Handle connection drops
    )

    return _engine


def get_session(database_url: Optional[str] = None) -> Session:
    """Create a new database session.

    Args:
        database_url: Optional override for the database URL.

    Returns:
        SQLAlchemy Session instance.
    """
    engine = get_engine(database_url)
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return SessionLocal()


def get_backend_name(database_url: Optional[str] = None) -> str:
    """Get the database backend name (sqlite, postgresql, etc.)."""
    engine = get_engine(database_url)
    return engine.url.get_backend_name()
