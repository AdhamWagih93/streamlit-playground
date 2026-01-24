from __future__ import annotations

from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


_ENGINE: Optional[Engine] = None
_SESSIONMAKER = None


def get_engine(database_url: str) -> Engine:
    global _ENGINE, _SESSIONMAKER

    if _ENGINE is not None:
        return _ENGINE

    # For SQLite, allow cross-thread use because the scheduler loop
    # runs in a background thread.
    connect_args = {}
    if database_url.startswith("sqlite:"):
        connect_args = {"check_same_thread": False}

    _ENGINE = create_engine(
        database_url,
        future=True,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    _SESSIONMAKER = sessionmaker(bind=_ENGINE, autoflush=False, expire_on_commit=False, future=True)
    return _ENGINE


def get_sessionmaker(database_url: str):
    if _SESSIONMAKER is None:
        get_engine(database_url)
    return _SESSIONMAKER
