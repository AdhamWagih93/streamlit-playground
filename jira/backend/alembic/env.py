"""Alembic migration environment for Trackly.

Trackly creates its schema on first boot via SQLAlchemy ``create_all`` in the
FastAPI lifespan, so Alembic is NOT required to get started. It is wired up
here for *future* schema changes: once the app is in production and you can no
longer drop/recreate tables, generate versioned migrations with::

    alembic revision --autogenerate -m "describe the change"
    alembic upgrade head

The database URL and target metadata are sourced from the application itself
(``settings.sqlalchemy_database_uri`` and ``app.models`` -> ``Base.metadata``)
so there is a single source of truth shared with the running service.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the backend package importable when alembic is invoked from any cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the app's settings, declarative Base, and (crucially) every model so
# that Base.metadata is fully populated for autogenerate.
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402
import app.models  # noqa: E402,F401  (registers all tables on Base.metadata)

# Alembic Config object — provides access to alembic.ini values.
config = context.config

# Inject the runtime database URL from app settings.
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_uri)

# Configure Python logging from alembic.ini (if present).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for 'autogenerate' support.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect and apply against the DB)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
