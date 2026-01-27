"""Database Explorer - Beautiful analytics and insights for your data."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy import inspect as sa_inspect

from src.theme import set_theme


set_theme(page_title="Database Explorer", page_icon="🗄️")


# =============================================================================
# STYLES
# =============================================================================

st.markdown(
    """
    <style>
    .db-hero {
        background: linear-gradient(135deg, #059669 0%, #0d9488 50%, #0891b2 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(5, 150, 105, 0.3);
    }
    .db-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
    }
    .db-hero p {
        opacity: 0.9;
        margin: 0;
    }
    .stat-card {
        background: white;
        border-radius: 16px;
        padding: 1.5rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        text-align: center;
        transition: all 0.3s ease;
    }
    .stat-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.12);
    }
    .stat-value {
        font-size: 2.5rem;
        font-weight: 800;
        background: linear-gradient(135deg, #059669, #0891b2);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stat-label {
        color: #64748b;
        font-size: 0.9rem;
        margin-top: 0.5rem;
        font-weight: 500;
    }
    .table-card {
        background: white;
        border-radius: 12px;
        padding: 1.25rem;
        border: 2px solid #e2e8f0;
        margin-bottom: 1rem;
        transition: all 0.2s;
    }
    .table-card:hover {
        border-color: #059669;
        box-shadow: 0 4px 16px rgba(5, 150, 105, 0.15);
    }
    .insight-box {
        background: linear-gradient(135deg, #f0fdf4 0%, #ecfeff 100%);
        border: 1px solid #86efac;
        border-radius: 12px;
        padding: 1rem;
        margin: 0.5rem 0;
    }
    .insight-box.warning {
        background: linear-gradient(135deg, #fefce8 0%, #fff7ed 100%);
        border-color: #fcd34d;
    }
    .insight-box.error {
        background: linear-gradient(135deg, #fef2f2 0%, #fff1f2 100%);
        border-color: #fca5a5;
    }
    .db-status-healthy {
        color: #059669;
        font-weight: 600;
    }
    .db-status-unhealthy {
        color: #dc2626;
        font-weight: 600;
    }
    .quick-action {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 0.75rem 1rem;
        cursor: pointer;
        transition: all 0.2s;
    }
    .quick-action:hover {
        background: #f1f5f9;
        border-color: #059669;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# DATABASE UTILITIES
# =============================================================================


@dataclass(frozen=True)
class DbTarget:
    key: str
    label: str
    url: str
    notes: str


def _redact_url(url: str) -> str:
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


def _db_kind_from_url(url: str) -> str:
    u = (url or "").strip().lower()
    if u.startswith("sqlite:"):
        return "sqlite"
    if u.startswith("postgresql") or u.startswith("postgres"):
        return "postgres"
    if u.startswith("mysql"):
        return "mysql"
    return "unknown"


@st.cache_resource(show_spinner=False)
def _engine_for_url(url: str) -> Engine:
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
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _db_version(engine: Engine, kind: str) -> str:
    try:
        with engine.connect() as conn:
            if kind == "sqlite":
                v = conn.execute(text("select sqlite_version()"))
                return v.scalar()
            if kind == "postgres":
                v = conn.execute(text("select version()"))
                full = v.scalar() or ""
                return full.split(",")[0] if "," in full else full[:50]
        return "Unknown"
    except Exception:
        return "Error"


def _quote_ident(engine: Engine, ident: str) -> str:
    try:
        prep = engine.dialect.identifier_preparer
        return prep.quote(ident)
    except Exception:
        return '"' + ident.replace('"', '""') + '"'


def _list_tables(engine: Engine) -> List[str]:
    insp = sa_inspect(engine)
    return sorted(insp.get_table_names())


def _table_counts(engine: Engine, tables: List[str]) -> Dict[str, int]:
    counts = {}
    try:
        with engine.connect() as conn:
            for t in tables:
                q = text(f"SELECT COUNT(*) AS c FROM {_quote_ident(engine, t)}")
                counts[t] = int(conn.execute(q).scalar() or 0)
    except Exception:
        pass
    return counts


def _get_db_url() -> str:
    """Get the primary database URL."""
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("PLATFORM_DATABASE_URL")
    if not db_url:
        user = os.environ.get("POSTGRES_USER", "bsw")
        password = os.environ.get("POSTGRES_PASSWORD", "bsw")
        host = os.environ.get("POSTGRES_HOST", "postgres")
        port = os.environ.get("POSTGRES_PORT", "5432")
        name = os.environ.get("POSTGRES_DB", "bsw")
        db_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"
    return db_url


# =============================================================================
# DATA FETCHING FUNCTIONS
# =============================================================================


@st.cache_data(ttl=30, show_spinner=False)
def fetch_tasks_stats(db_url: str) -> Dict[str, Any]:
    """Fetch task statistics."""
    try:
        engine = _engine_for_url(db_url)
        with engine.connect() as conn:
            # Total tasks
            total = conn.execute(text("SELECT COUNT(*) FROM tasks")).scalar() or 0

            # By status
            status_df = pd.read_sql(
                "SELECT status, COUNT(*) as count FROM tasks GROUP BY status",
                conn,
            )

            # By priority
            priority_df = pd.read_sql(
                "SELECT priority, COUNT(*) as count FROM tasks GROUP BY priority",
                conn,
            )

            # By team
            team_df = pd.read_sql(
                "SELECT COALESCE(team, 'Unassigned') as team, COUNT(*) as count FROM tasks GROUP BY team",
                conn,
            )

            # Recent tasks (last 7 days)
            recent = conn.execute(
                text("SELECT COUNT(*) FROM tasks WHERE created_at > :cutoff"),
                {"cutoff": (datetime.utcnow() - timedelta(days=7)).isoformat()},
            ).scalar() or 0

            # Completed this week
            completed = conn.execute(
                text("SELECT COUNT(*) FROM tasks WHERE status = 'Done' AND done_at > :cutoff"),
                {"cutoff": (datetime.utcnow() - timedelta(days=7)).isoformat()},
            ).scalar() or 0

            return {
                "ok": True,
                "total": total,
                "by_status": status_df.to_dict("records"),
                "by_priority": priority_df.to_dict("records"),
                "by_team": team_df.to_dict("records"),
                "recent": recent,
                "completed_week": completed,
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_scheduler_stats(db_url: str) -> Dict[str, Any]:
    """Fetch scheduler job statistics."""
    try:
        engine = _engine_for_url(db_url)
        with engine.connect() as conn:
            # Total jobs
            total_jobs = conn.execute(text("SELECT COUNT(*) FROM scheduler_jobs")).scalar() or 0

            # Enabled jobs
            enabled = conn.execute(
                text("SELECT COUNT(*) FROM scheduler_jobs WHERE enabled = true")
            ).scalar() or 0

            # Jobs by server
            server_df = pd.read_sql(
                "SELECT server, COUNT(*) as count FROM scheduler_jobs GROUP BY server ORDER BY count DESC",
                conn,
            )

            # Total runs
            total_runs = conn.execute(text("SELECT COUNT(*) FROM scheduler_runs")).scalar() or 0

            # Success rate
            successful = conn.execute(
                text("SELECT COUNT(*) FROM scheduler_runs WHERE ok = true")
            ).scalar() or 0

            # Recent runs (last 24h)
            recent_runs = conn.execute(
                text("SELECT COUNT(*) FROM scheduler_runs WHERE started_at > :cutoff"),
                {"cutoff": (datetime.utcnow() - timedelta(hours=24)).isoformat()},
            ).scalar() or 0

            # Recent failures
            recent_failures = conn.execute(
                text("SELECT COUNT(*) FROM scheduler_runs WHERE started_at > :cutoff AND ok = false"),
                {"cutoff": (datetime.utcnow() - timedelta(hours=24)).isoformat()},
            ).scalar() or 0

            return {
                "ok": True,
                "total_jobs": total_jobs,
                "enabled_jobs": enabled,
                "by_server": server_df.to_dict("records"),
                "total_runs": total_runs,
                "successful_runs": successful,
                "success_rate": round(successful / total_runs * 100, 1) if total_runs > 0 else 0,
                "recent_runs_24h": recent_runs,
                "recent_failures_24h": recent_failures,
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_mcp_stats(db_url: str) -> Dict[str, Any]:
    """Fetch MCP tool call statistics."""
    try:
        engine = _engine_for_url(db_url)
        with engine.connect() as conn:
            # Total calls
            total = conn.execute(text("SELECT COUNT(*) FROM mcp_tool_calls")).scalar() or 0

            # Success rate
            successful = conn.execute(
                text("SELECT COUNT(*) FROM mcp_tool_calls WHERE success = true")
            ).scalar() or 0

            # By server
            server_df = pd.read_sql(
                "SELECT server_name, COUNT(*) as count, "
                "SUM(CASE WHEN success THEN 1 ELSE 0 END) as successes, "
                "AVG(duration_ms) as avg_duration "
                "FROM mcp_tool_calls GROUP BY server_name ORDER BY count DESC",
                conn,
            )

            # By tool (top 10)
            tool_df = pd.read_sql(
                "SELECT tool_name, server_name, COUNT(*) as count "
                "FROM mcp_tool_calls GROUP BY tool_name, server_name ORDER BY count DESC LIMIT 10",
                conn,
            )

            # Recent calls (last hour)
            recent = conn.execute(
                text("SELECT COUNT(*) FROM mcp_tool_calls WHERE started_at > :cutoff"),
                {"cutoff": (datetime.utcnow() - timedelta(hours=1)).isoformat()},
            ).scalar() or 0

            # Average duration
            avg_duration = conn.execute(
                text("SELECT AVG(duration_ms) FROM mcp_tool_calls WHERE duration_ms IS NOT NULL")
            ).scalar() or 0

            return {
                "ok": True,
                "total": total,
                "successful": successful,
                "success_rate": round(successful / total * 100, 1) if total > 0 else 0,
                "by_server": server_df.to_dict("records"),
                "top_tools": tool_df.to_dict("records"),
                "recent_1h": recent,
                "avg_duration_ms": round(avg_duration, 2) if avg_duration else 0,
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


@st.cache_data(ttl=30, show_spinner=False)
def fetch_recent_data(db_url: str, table: str, limit: int = 10) -> pd.DataFrame:
    """Fetch recent records from a table."""
    try:
        engine = _engine_for_url(db_url)
        with engine.connect() as conn:
            if table == "tasks":
                return pd.read_sql(
                    f"SELECT id, title, status, priority, assignee, team, created_at FROM tasks ORDER BY created_at DESC LIMIT {limit}",
                    conn,
                )
            elif table == "scheduler_jobs":
                return pd.read_sql(
                    f"SELECT id, label, server, tool, enabled, interval_seconds, next_run_at FROM scheduler_jobs ORDER BY created_at DESC LIMIT {limit}",
                    conn,
                )
            elif table == "scheduler_runs":
                return pd.read_sql(
                    f"SELECT id, job_id, started_at, finished_at, ok, error FROM scheduler_runs ORDER BY started_at DESC LIMIT {limit}",
                    conn,
                )
            elif table == "mcp_tool_calls":
                return pd.read_sql(
                    f"SELECT id, server_name, tool_name, success, duration_ms, started_at, source FROM mcp_tool_calls ORDER BY started_at DESC LIMIT {limit}",
                    conn,
                )
            elif table == "mcp_server_health":
                return pd.read_sql(
                    f"SELECT id, server_name, checked_at, healthy, response_time_ms, error_message FROM mcp_server_health ORDER BY checked_at DESC LIMIT {limit}",
                    conn,
                )
            else:
                return pd.read_sql(f"SELECT * FROM {table} LIMIT {limit}", conn)
    except Exception:
        return pd.DataFrame()


# =============================================================================
# UI COMPONENTS
# =============================================================================


def render_hero():
    """Render the hero section."""
    st.markdown(
        """
        <div class="db-hero">
            <h1>🗄️ Database Explorer</h1>
            <p>Real-time analytics, insights, and data exploration for your platform databases</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_stat_card(value: Any, label: str, icon: str = ""):
    """Render a statistics card."""
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value">{icon}{value}</div>
            <div class="stat-label">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_db_health_section(db_url: str):
    """Render database health overview."""
    engine = _engine_for_url(db_url)
    health = _health_check(engine)
    kind = _db_kind_from_url(db_url)
    version = _db_version(engine, kind)
    tables = _list_tables(engine)
    counts = _table_counts(engine, tables)
    total_rows = sum(counts.values())

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        status_class = "db-status-healthy" if health.get("ok") else "db-status-unhealthy"
        status_text = "Connected" if health.get("ok") else "Error"
        st.markdown(
            f"""
            <div class="stat-card">
                <div class="stat-value" style="font-size: 1.5rem;">{'✓' if health.get('ok') else '✗'}</div>
                <div class="{status_class}" style="font-size: 1.2rem;">{status_text}</div>
                <div class="stat-label">{kind.upper()}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        render_stat_card(len(tables), "Tables")

    with col3:
        render_stat_card(f"{total_rows:,}", "Total Rows")

    with col4:
        st.markdown(
            f"""
            <div class="stat-card">
                <div style="font-size: 0.85rem; color: #64748b; padding: 0.5rem;">
                    <strong>Version</strong><br/>
                    <span style="font-size: 0.75rem;">{version[:40]}...</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_tasks_dashboard(stats: Dict[str, Any]):
    """Render the tasks analytics dashboard."""
    if not stats.get("ok"):
        st.error(f"Could not load task stats: {stats.get('error', 'Unknown error')}")
        return

    st.markdown("### 📋 Task Management")

    # Quick stats
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_stat_card(stats["total"], "Total Tasks")
    with col2:
        render_stat_card(stats["recent"], "Created This Week")
    with col3:
        render_stat_card(stats["completed_week"], "Completed This Week")
    with col4:
        completion_rate = round(stats["completed_week"] / stats["recent"] * 100, 1) if stats["recent"] > 0 else 0
        render_stat_card(f"{completion_rate}%", "Weekly Completion")

    # Charts
    col1, col2 = st.columns(2)

    with col1:
        if stats["by_status"]:
            df = pd.DataFrame(stats["by_status"])
            # Define status colors
            status_colors = {
                "Backlog": "#94a3b8",
                "Todo": "#60a5fa",
                "In Progress": "#fbbf24",
                "In Review": "#a78bfa",
                "Done": "#34d399",
            }
            colors = [status_colors.get(s, "#94a3b8") for s in df["status"]]

            fig = px.pie(
                df,
                values="count",
                names="status",
                title="Tasks by Status",
                color_discrete_sequence=colors,
                hole=0.4,
            )
            fig.update_layout(
                height=300,
                margin=dict(t=40, b=20, l=20, r=20),
                legend=dict(orientation="h", yanchor="bottom", y=-0.2),
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        if stats["by_priority"]:
            df = pd.DataFrame(stats["by_priority"])
            priority_colors = {
                "Critical": "#ef4444",
                "High": "#f97316",
                "Medium": "#eab308",
                "Low": "#22c55e",
            }
            colors = [priority_colors.get(p, "#94a3b8") for p in df["priority"]]

            fig = px.bar(
                df,
                x="priority",
                y="count",
                title="Tasks by Priority",
                color="priority",
                color_discrete_map=priority_colors,
            )
            fig.update_layout(
                height=300,
                margin=dict(t=40, b=20, l=20, r=20),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)

    # Team breakdown
    if stats["by_team"]:
        df = pd.DataFrame(stats["by_team"])
        fig = px.bar(
            df,
            x="team",
            y="count",
            title="Tasks by Team",
            color="count",
            color_continuous_scale="Teal",
        )
        fig.update_layout(
            height=250,
            margin=dict(t=40, b=20, l=20, r=20),
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig, use_container_width=True)


def render_scheduler_dashboard(stats: Dict[str, Any]):
    """Render the scheduler analytics dashboard."""
    if not stats.get("ok"):
        st.error(f"Could not load scheduler stats: {stats.get('error', 'Unknown error')}")
        return

    st.markdown("### ⏱️ Job Scheduler")

    # Quick stats
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_stat_card(stats["total_jobs"], "Total Jobs")
    with col2:
        render_stat_card(stats["enabled_jobs"], "Active Jobs")
    with col3:
        render_stat_card(f"{stats['success_rate']}%", "Success Rate")
    with col4:
        render_stat_card(stats["recent_runs_24h"], "Runs (24h)")

    # Insights
    if stats["recent_failures_24h"] > 0:
        st.markdown(
            f"""
            <div class="insight-box warning">
                ⚠️ <strong>{stats['recent_failures_24h']} failed job runs</strong> in the last 24 hours.
                Check the scheduler runs for details.
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Charts
    col1, col2 = st.columns(2)

    with col1:
        if stats["by_server"]:
            df = pd.DataFrame(stats["by_server"])
            fig = px.pie(
                df,
                values="count",
                names="server",
                title="Jobs by MCP Server",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set3,
            )
            fig.update_layout(
                height=300,
                margin=dict(t=40, b=20, l=20, r=20),
                legend=dict(orientation="h", yanchor="bottom", y=-0.2),
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Success/failure gauge
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number+delta",
                value=stats["success_rate"],
                title={"text": "Overall Success Rate"},
                delta={"reference": 95},
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": "#059669"},
                    "steps": [
                        {"range": [0, 50], "color": "#fee2e2"},
                        {"range": [50, 80], "color": "#fef3c7"},
                        {"range": [80, 100], "color": "#dcfce7"},
                    ],
                    "threshold": {
                        "line": {"color": "#dc2626", "width": 4},
                        "thickness": 0.75,
                        "value": 90,
                    },
                },
            )
        )
        fig.update_layout(
            height=300,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True)


def render_mcp_dashboard(stats: Dict[str, Any]):
    """Render the MCP tool calls dashboard."""
    if not stats.get("ok"):
        st.error(f"Could not load MCP stats: {stats.get('error', 'Unknown error')}")
        return

    st.markdown("### 🔌 MCP Tool Calls")

    # Quick stats
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_stat_card(f"{stats['total']:,}", "Total Calls")
    with col2:
        render_stat_card(f"{stats['success_rate']}%", "Success Rate")
    with col3:
        render_stat_card(f"{stats['avg_duration_ms']:.0f}ms", "Avg Duration")
    with col4:
        render_stat_card(stats["recent_1h"], "Last Hour")

    # Server breakdown
    col1, col2 = st.columns(2)

    with col1:
        if stats["by_server"]:
            df = pd.DataFrame(stats["by_server"])
            fig = px.bar(
                df,
                x="server_name",
                y="count",
                title="Calls by Server",
                color="count",
                color_continuous_scale="Teal",
            )
            fig.update_layout(
                height=300,
                margin=dict(t=40, b=20, l=20, r=20),
                coloraxis_showscale=False,
                xaxis_tickangle=-45,
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        if stats["by_server"]:
            df = pd.DataFrame(stats["by_server"])
            df["success_rate"] = (df["successes"] / df["count"] * 100).round(1)

            fig = px.bar(
                df,
                x="server_name",
                y="avg_duration",
                title="Avg Response Time by Server",
                color="success_rate",
                color_continuous_scale="RdYlGn",
            )
            fig.update_layout(
                height=300,
                margin=dict(t=40, b=20, l=20, r=20),
                xaxis_tickangle=-45,
            )
            st.plotly_chart(fig, use_container_width=True)

    # Top tools
    if stats["top_tools"]:
        st.markdown("#### Most Used Tools")
        df = pd.DataFrame(stats["top_tools"])
        st.dataframe(
            df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "tool_name": st.column_config.TextColumn("Tool", width="medium"),
                "server_name": st.column_config.TextColumn("Server", width="small"),
                "count": st.column_config.ProgressColumn(
                    "Calls",
                    format="%d",
                    min_value=0,
                    max_value=df["count"].max() if len(df) > 0 else 1,
                ),
            },
        )


def render_table_explorer(db_url: str):
    """Render the table data explorer."""
    engine = _engine_for_url(db_url)
    tables = _list_tables(engine)
    counts = _table_counts(engine, tables)

    st.markdown("### 🔍 Data Explorer")

    # Table selection
    col1, col2 = st.columns([2, 1])
    with col1:
        selected_table = st.selectbox(
            "Select Table",
            options=tables,
            format_func=lambda t: f"{t} ({counts.get(t, 0):,} rows)",
        )
    with col2:
        limit = st.selectbox("Rows to display", options=[10, 25, 50, 100, 200], index=1)

    if selected_table:
        df = fetch_recent_data(db_url, selected_table, limit)
        if not df.empty:
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Download button
            csv = df.to_csv(index=False)
            st.download_button(
                label="📥 Download CSV",
                data=csv,
                file_name=f"{selected_table}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )
        else:
            st.info("No data available or table is empty.")


def render_custom_query(db_url: str):
    """Render custom SQL query interface."""
    st.markdown("### 💻 Custom Query")
    st.caption("Execute read-only SQL queries (SELECT/WITH only)")

    kind = _db_kind_from_url(db_url)
    default_sql = (
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        if kind == "sqlite"
        else "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    )

    sql = st.text_area("SQL Query", value=default_sql, height=120)

    if st.button("▶️ Run Query", type="primary"):
        s = (sql or "").strip().lower()
        if not s:
            st.warning("Enter a query.")
            return

        if not (s.startswith("select") or s.startswith("with")):
            st.error("Only SELECT/WITH queries are allowed for safety.")
            return

        if ";" in s:
            st.error("Multiple statements not allowed.")
            return

        try:
            engine = _engine_for_url(db_url)
            with engine.connect() as conn:
                df = pd.read_sql(sql, conn)
                st.success(f"Returned {len(df)} row(s)")
                st.dataframe(df, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Query failed: {e}")


# =============================================================================
# MAIN PAGE
# =============================================================================


def main():
    render_hero()

    # Get database URL
    db_url = _get_db_url()

    # Database health overview
    st.markdown("## 📊 Database Overview")
    render_db_health_section(db_url)

    st.divider()

    # Main content tabs
    tab_dashboard, tab_tasks, tab_scheduler, tab_mcp, tab_explorer, tab_query = st.tabs([
        "📈 Dashboard",
        "📋 Tasks",
        "⏱️ Scheduler",
        "🔌 MCP Logs",
        "🔍 Explorer",
        "💻 Query",
    ])

    with tab_dashboard:
        st.markdown("## Quick Insights")

        # Load all stats
        with st.spinner("Loading statistics..."):
            task_stats = fetch_tasks_stats(db_url)
            scheduler_stats = fetch_scheduler_stats(db_url)
            mcp_stats = fetch_mcp_stats(db_url)

        # Summary cards
        col1, col2, col3 = st.columns(3)

        with col1:
            st.markdown(
                """
                <div class="table-card">
                    <h4>📋 Tasks</h4>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if task_stats.get("ok"):
                st.metric("Total", task_stats["total"])
                st.metric("Created This Week", task_stats["recent"])
            else:
                st.warning("Could not load task stats")

        with col2:
            st.markdown(
                """
                <div class="table-card">
                    <h4>⏱️ Scheduler</h4>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if scheduler_stats.get("ok"):
                st.metric("Active Jobs", scheduler_stats["enabled_jobs"])
                st.metric("Success Rate", f"{scheduler_stats['success_rate']}%")
            else:
                st.warning("Could not load scheduler stats")

        with col3:
            st.markdown(
                """
                <div class="table-card">
                    <h4>🔌 MCP Calls</h4>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if mcp_stats.get("ok"):
                st.metric("Total Calls", f"{mcp_stats['total']:,}")
                st.metric("Success Rate", f"{mcp_stats['success_rate']}%")
            else:
                st.warning("Could not load MCP stats")

        # Insights
        st.markdown("### 💡 Insights")

        insights = []

        if task_stats.get("ok"):
            if task_stats["recent"] > 10:
                insights.append(("success", f"📈 {task_stats['recent']} new tasks created this week - team is active!"))
            if task_stats["completed_week"] > 5:
                insights.append(("success", f"✅ {task_stats['completed_week']} tasks completed this week - great progress!"))

        if scheduler_stats.get("ok"):
            if scheduler_stats["recent_failures_24h"] > 0:
                insights.append(("warning", f"⚠️ {scheduler_stats['recent_failures_24h']} scheduler failures in last 24h"))
            if scheduler_stats["success_rate"] >= 95:
                insights.append(("success", f"🎯 Scheduler success rate at {scheduler_stats['success_rate']}% - excellent!"))

        if mcp_stats.get("ok"):
            if mcp_stats["avg_duration_ms"] > 5000:
                insights.append(("warning", f"🐢 Average MCP call duration is {mcp_stats['avg_duration_ms']:.0f}ms - consider optimization"))
            if mcp_stats["success_rate"] < 90:
                insights.append(("error", f"⚠️ MCP success rate at {mcp_stats['success_rate']}% - investigate failures"))

        if not insights:
            insights.append(("success", "✨ Everything looks healthy! Keep up the good work."))

        for insight_type, message in insights:
            st.markdown(
                f'<div class="insight-box {insight_type}">{message}</div>',
                unsafe_allow_html=True,
            )

    with tab_tasks:
        task_stats = fetch_tasks_stats(db_url)
        render_tasks_dashboard(task_stats)

        st.divider()
        st.markdown("#### Recent Tasks")
        df = fetch_recent_data(db_url, "tasks", 15)
        if not df.empty:
            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "status": st.column_config.TextColumn("Status", width="small"),
                    "priority": st.column_config.TextColumn("Priority", width="small"),
                },
            )

    with tab_scheduler:
        scheduler_stats = fetch_scheduler_stats(db_url)
        render_scheduler_dashboard(scheduler_stats)

        st.divider()

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### Active Jobs")
            df = fetch_recent_data(db_url, "scheduler_jobs", 10)
            if not df.empty:
                st.dataframe(df, hide_index=True, use_container_width=True)

        with col2:
            st.markdown("#### Recent Runs")
            df = fetch_recent_data(db_url, "scheduler_runs", 10)
            if not df.empty:
                st.dataframe(
                    df,
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "ok": st.column_config.CheckboxColumn("Success"),
                    },
                )

    with tab_mcp:
        mcp_stats = fetch_mcp_stats(db_url)
        render_mcp_dashboard(mcp_stats)

        st.divider()
        st.markdown("#### Recent Tool Calls")
        df = fetch_recent_data(db_url, "mcp_tool_calls", 20)
        if not df.empty:
            st.dataframe(
                df,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "success": st.column_config.CheckboxColumn("Success"),
                    "duration_ms": st.column_config.NumberColumn("Duration (ms)", format="%.0f"),
                },
            )

    with tab_explorer:
        render_table_explorer(db_url)

    with tab_query:
        render_custom_query(db_url)

    # Footer
    st.divider()
    st.caption(
        f"🗄️ Connected to **{_db_kind_from_url(db_url).upper()}** at `{_redact_url(db_url)}` | "
        f"Last refreshed: {datetime.now().strftime('%H:%M:%S')}"
    )

    # Auto-refresh option
    if st.sidebar.checkbox("Auto-refresh (30s)", value=False):
        import time
        time.sleep(30)
        st.rerun()


if __name__ == "__main__":
    main()
else:
    main()
