"""Non-destructive, additive schema reconciliation.

``Base.metadata.create_all`` creates *missing tables* but never alters existing
ones, so adding a column to a model would otherwise require dropping the
database. This module closes that gap for the common, safe case: it inspects
the live database and issues ``ALTER TABLE ... ADD COLUMN`` for any column that
exists in the ORM metadata but not yet in the table.

New columns are always added as NULLable (even if the model marks them NOT
NULL) so the statement can never fail against a table that already has rows —
existing rows simply get NULL and the application's defaults apply to new rows.
This means routine "add a field" changes deploy with zero data loss; genuinely
destructive changes (drops, type changes, renames) still call for a real
Alembic migration.
"""
from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.core.database import Base

log = logging.getLogger("trackly.schema")


def reconcile_schema(engine: Engine) -> list[str]:
    """Add any ORM columns missing from existing tables. Returns the DDL run."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                # create_all handles brand-new tables; nothing to reconcile.
                continue
            db_columns = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in db_columns:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN IF NOT EXISTS "{column.name}" {col_type}'
                conn.execute(text(ddl))
                applied.append(ddl)
                log.info("Schema reconcile: added %s.%s", table.name, column.name)

    if applied:
        log.info("Schema reconcile applied %d additive change(s)", len(applied))
    return applied
