from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy import inspect as sa_inspect

from src.theme import set_theme


set_theme(page_title="Database", page_icon="🗄️")


@dataclass(frozen=True)
class DbTarget:
    key: str
    label: str
    url: str
    notes: str


def _redact_url(url: str) -> str:
    # Avoid leaking credentials in the UI.
    # This is intentionally conservative and simple.
    u = (url or "").strip()
    if not u:
        return ""

    if "://" not in u:
        return u

    scheme, rest = u.split("://", 1)
    if "@" not in rest:
        return u

    creds, hostpart = rest.split("@", 1)
    if ":" in creds:
        user = creds.split(":", 1)[0]
        creds = f"{user}:***"
    else:
        creds = "***"
    return f"{scheme}://{creds}@{hostpart}"


def _sqlite_file_from_url(url: str) -> Optional[Path]:
    u = (url or "").strip()
    if not u.startswith("sqlite:///"):
        return None
    # sqlite:////absolute/path or sqlite:///relative/path
    p_raw = u.replace("sqlite:///", "", 1)
    try:
        return Path(p_raw)
    except Exception:
        return None


def _db_kind_from_url(url: str) -> str:
    u = (url or "").strip().lower()
    if u.startswith("sqlite:"):
        return "sqlite"
    if u.startswith("postgresql") or u.startswith("postgres"):
        return "postgres"
    if u.startswith("mysql"):
        return "mysql"
    if u.startswith("mssql"):
        return "mssql"
    return "unknown"


@st.cache_resource(show_spinner=False)
def _engine_for_url(url: str) -> Engine:
    # Use the same semantics as scheduler DB: pool_pre_ping helps avoid stale conns.
    from sqlalchemy import create_engine

    connect_args: Dict[str, Any] = {}
    if str(url).startswith("sqlite:"):
        connect_args = {"check_same_thread": False}

    return create_engine(
        url,
        future=True,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


def _health_check(engine: Engine) -> Dict[str, Any]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _db_version(engine: Engine, kind: str) -> Dict[str, Any]:
    try:
        with engine.connect() as conn:
            if kind == "sqlite":
                v = conn.execute(text("select sqlite_version()"))
                return {"ok": True, "version": v.scalar()}
            if kind == "postgres":
                v = conn.execute(text("select version()"))
                return {"ok": True, "version": v.scalar()}
        return {"ok": True, "version": None}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _quote_ident(engine: Engine, ident: str) -> str:
    try:
        prep = engine.dialect.identifier_preparer
        return prep.quote(ident)
    except Exception:
        # Fallback: not perfect, but good enough for simple table names.
        return '"' + ident.replace('"', '""') + '"'


def _list_tables(engine: Engine) -> List[str]:
    insp = sa_inspect(engine)
    return sorted(insp.get_table_names())


def _table_counts(engine: Engine, tables: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": True, "counts": {}}
    try:
        with engine.connect() as conn:
            for t in tables:
                q = text(f"SELECT COUNT(*) AS c FROM {_quote_ident(engine, t)}")
                out["counts"][t] = int(conn.execute(q).scalar() or 0)
        return out
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "counts": out.get("counts", {})}


def _table_preview(engine: Engine, table: str, limit: int = 50) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    try:
        lim = max(1, min(int(limit), 500))
        with engine.connect() as conn:
            q = text(f"SELECT * FROM {_quote_ident(engine, table)} LIMIT {lim}")
            res = conn.execute(q)
            rows = res.fetchall()
            cols = list(res.keys())
        df = pd.DataFrame(rows, columns=cols)
        return df, {"ok": True, "rows": int(len(df))}
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), {"ok": False, "error": str(exc)}


def _is_safe_readonly_sql(sql: str) -> bool:
    s = (sql or "").strip().lower()
    if not s:
        return False

    # Disallow obvious multi-statement / comment tricks.
    if ";" in s:
        return False

    # Allow SELECT / WITH by default.
    return s.startswith("select") or s.startswith("with")


def _resolve_db_targets() -> List[DbTarget]:
    targets: List[DbTarget] = []

    # Tasks DB (Team Task Manager)
    try:
        from src import tasks_repo

        tasks_engine = tasks_repo.get_engine()
        tasks_url = str(tasks_engine.url)
        targets.append(
            DbTarget(
                key="tasks",
                label="Tasks DB",
                url=tasks_url,
                notes="Used by Team Task Manager (SQLAlchemy, env: DATABASE_URL; default: data/tasks.db).",
            )
        )
    except Exception as exc:  # noqa: BLE001
        targets.append(
            DbTarget(
                key="tasks",
                label="Tasks DB",
                url="",
                notes=f"Unable to resolve Tasks DB engine: {exc}",
            )
        )

    # Scheduler DB (external scheduler service)
    try:
        from src.scheduler.config import SchedulerConfig

        cfg = SchedulerConfig.from_env()
        targets.append(
            DbTarget(
                key="scheduler",
                label="Scheduler DB",
                url=str(cfg.database_url),
                notes="Used by the external scheduler service (env: PLATFORM_DATABASE_URL or SCHEDULER_DATABASE_URL; default: data/scheduler.db).",
            )
        )
    except Exception as exc:  # noqa: BLE001
        targets.append(
            DbTarget(
                key="scheduler",
                label="Scheduler DB",
                url="",
                notes=f"Unable to resolve Scheduler DB URL: {exc}",
            )
        )

    # Convenience: show the shared platform DB env if present
    platform_url = (os.environ.get("PLATFORM_DATABASE_URL") or "").strip()
    if platform_url:
        targets.append(
            DbTarget(
                key="platform",
                label="PLATFORM_DATABASE_URL (raw env)",
                url=platform_url,
                notes="Shared platform DB env var (often points at Postgres in Kubernetes).",
            )
        )

    # De-dup by (key, url)
    seen: set[tuple[str, str]] = set()
    deduped: List[DbTarget] = []
    for t in targets:
        k = (t.key, t.url)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(t)

    return deduped


st.title("Database")
st.caption(
    "Inspect the application databases (type, health, tables, and content). "
    "By default this page is read-only."
)


targets = _resolve_db_targets()

with st.expander("Detected DB configuration", expanded=True):
    for t in targets:
        kind = _db_kind_from_url(t.url) if t.url else "unknown"
        st.markdown(f"**{t.label}**")
        st.write(t.notes)
        st.write({"kind": kind, "url": _redact_url(t.url) if t.url else None})

targets_by_key = {t.key: t for t in targets}
available_keys = [t.key for t in targets if t.url]

# Default behavior requested:
# - Scheduling defaults to Scheduler DB
# - Tasks defaults to Tasks DB
tab_labels: List[str] = []
tab_to_key: Dict[str, str] = {}
if "scheduler" in available_keys:
    tab_labels.append("Scheduler DB")
    tab_to_key["Scheduler DB"] = "scheduler"
if "tasks" in available_keys:
    tab_labels.append("Tasks DB")
    tab_to_key["Tasks DB"] = "tasks"
tab_labels.append("Other")

tabs = st.tabs(tab_labels)

def _render_db_inspector(selected: DbTarget) -> None:
    kind = _db_kind_from_url(selected.url)
    st.markdown("---")

    cols = st.columns([1, 1, 2])
    with cols[0]:
        do_connect = st.button("Connect + inspect", type="primary", key=f"db_connect_{selected.key}")
    with cols[1]:
        refresh = st.button("Refresh", type="secondary", key=f"db_refresh_{selected.key}")
    with cols[2]:
        st.caption("Connect/inspect is manual to avoid rerun storms.")

    refresh_key = f"db_refresh_token_{selected.key}"
    if refresh:
        st.session_state[refresh_key] = str(pd.Timestamp.utcnow())
    refresh_token = st.session_state.get(refresh_key, "")

    tab_health, tab_tables, tab_query = st.tabs(["Health", "Tables", "Query"])

    with tab_health:
        st.subheader("Health & version")
        st.write({"selected": selected.label, "kind": kind, "url": _redact_url(selected.url)})

        sqlite_file = _sqlite_file_from_url(selected.url)
        if sqlite_file is not None:
            try:
                resolved = sqlite_file.resolve()
                exists = resolved.exists()
                size = resolved.stat().st_size if exists else None
                st.write({"sqlite_file": str(resolved), "exists": bool(exists), "bytes": size})
            except Exception as exc:  # noqa: BLE001
                st.write({"sqlite_file": str(sqlite_file), "error": str(exc)})

        if not do_connect:
            st.info("Click ‘Connect + inspect’ to run health/version checks.")
            st.stop()

        engine = _engine_for_url(selected.url)

        hc = _health_check(engine)
        st.write({"health": hc})

        ver = _db_version(engine, kind)
        st.write({"version": ver})

    with tab_tables:
        st.subheader("Tables & content")

        engine = _engine_for_url(selected.url)

        @st.cache_data(show_spinner=False)
        def _inspect_tables(url: str, token: str) -> Dict[str, Any]:
            e = _engine_for_url(url)
            tables = _list_tables(e)
            counts = _table_counts(e, tables)
            return {"tables": tables, "counts": counts}

        info = _inspect_tables(selected.url, refresh_token)
        tables = list(info.get("tables") or [])
        counts_info = info.get("counts") or {}

        if not tables:
            st.info("No tables found (or connection failed).")
            if counts_info and not counts_info.get("ok", True):
                st.error(counts_info.get("error"))
            st.stop()

        counts = (counts_info.get("counts") or {}) if isinstance(counts_info, dict) else {}
        rows = [{"table": t, "rows": int(counts.get(t, 0))} for t in tables]
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        st.markdown("---")
        st.subheader("Preview table")
        chosen_table = st.selectbox("Table", options=tables, key=f"db_table_{selected.key}")
        preview_limit = st.slider("Rows", min_value=5, max_value=200, value=50, step=5, key=f"db_preview_{selected.key}")

        df, meta = _table_preview(engine, chosen_table, limit=int(preview_limit))
        if not meta.get("ok"):
            st.error(meta.get("error"))
        else:
            st.caption(f"Returned {meta.get('rows')} row(s).")
            st.dataframe(df, use_container_width=True)

    with tab_query:
        st.subheader("Run a query")
        st.caption("Default mode only allows a single read-only SELECT/WITH query.")

        allow_write = st.checkbox(
            "Allow non-SELECT queries (unsafe)",
            value=False,
            help="Enables INSERT/UPDATE/DELETE/DDL. Use with caution.",
            key=f"db_allow_write_{selected.key}",
        )

        default_sql = (
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            if kind == "sqlite"
            else "SELECT 1"
        )
        sql = st.text_area(
            "SQL",
            value=default_sql,
            height=160,
            key=f"db_sql_{selected.key}",
        )

        run = st.button("Run query", type="primary", key=f"db_run_{selected.key}")

        if run:
            s = (sql or "").strip()
            if not s:
                st.warning("Enter a query.")
                st.stop()

            if not allow_write and not _is_safe_readonly_sql(s):
                st.error(
                    "Blocked: only a single read-only SELECT/WITH query is allowed by default. "
                    "Enable ‘Allow non-SELECT queries’ to run unsafe statements."
                )
                st.stop()

            try:
                engine = _engine_for_url(selected.url)
                with engine.begin() as conn:
                    res = conn.execute(text(s))
                    if res.returns_rows:
                        rows = res.fetchall()
                        cols = list(res.keys())
                        df = pd.DataFrame(rows, columns=cols)
                        st.caption(f"Returned {len(df)} row(s).")
                        st.dataframe(df, use_container_width=True)
                    else:
                        st.success({"ok": True, "rowcount": int(res.rowcount or 0)})
            except Exception as exc:  # noqa: BLE001
                st.error(f"Query failed: {exc}")

st.markdown("---")


with tabs[0]:
    # Scheduler DB should be the default when present.
    if tab_to_key.get(tab_labels[0]) == "scheduler":
        _render_db_inspector(targets_by_key["scheduler"])
    elif tab_to_key.get(tab_labels[0]) == "tasks":
        _render_db_inspector(targets_by_key["tasks"])
    else:
        st.info("No Scheduler/Tasks DB detected.")

if len(tabs) > 1:
    with tabs[1]:
        k = tab_to_key.get(tab_labels[1])
        if k and k in targets_by_key:
            _render_db_inspector(targets_by_key[k])
        else:
            st.info("No additional DB detected.")

with tabs[-1]:
    st.subheader("Pick another DB")
    st.caption("Useful if you want to inspect PLATFORM_DATABASE_URL or other detected DBs.")
    other_keys = [k for k in available_keys if k not in {"scheduler", "tasks"}]
    if not other_keys:
        st.info("No other DB targets detected.")
    else:
        selected_key = st.selectbox(
            "Database target",
            options=other_keys,
            format_func=lambda k: targets_by_key.get(k, DbTarget(k, k, "", "")).label,
            key="db_other_target",
        )
        _render_db_inspector(targets_by_key[selected_key])
