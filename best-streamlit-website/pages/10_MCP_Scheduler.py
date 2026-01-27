"""MCP Scheduler - Professional job scheduling and monitoring dashboard."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.ai.mcp_specs import build_server_specs
from src.mcp_client import get_mcp_client
from src.streamlit_config import get_app_config
from src.theme import set_theme


ROOT = Path(__file__).resolve().parents[1]

set_theme(page_title="MCP Scheduler", page_icon="⏱️")


# =============================================================================
# STYLES
# =============================================================================

st.markdown(
    """
    <style>
    .scheduler-hero {
        background: linear-gradient(135deg, #7c3aed 0%, #6366f1 50%, #8b5cf6 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(124, 58, 237, 0.3);
    }
    .scheduler-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
    }
    .scheduler-hero p {
        opacity: 0.9;
        margin: 0;
    }
    .stat-card {
        background: white;
        border-radius: 16px;
        padding: 1.25rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        text-align: center;
        transition: all 0.3s ease;
        height: 100%;
    }
    .stat-card:hover {
        transform: translateY(-4px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.12);
    }
    .stat-value {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #7c3aed, #6366f1);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stat-value.success {
        background: linear-gradient(135deg, #059669, #10b981);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .stat-value.danger {
        background: linear-gradient(135deg, #dc2626, #ef4444);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .stat-value.warning {
        background: linear-gradient(135deg, #d97706, #f59e0b);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .stat-label {
        color: #64748b;
        font-size: 0.85rem;
        margin-top: 0.25rem;
        font-weight: 500;
    }
    .job-card {
        background: white;
        border-radius: 12px;
        padding: 1rem 1.25rem;
        border: 2px solid #e2e8f0;
        margin-bottom: 0.75rem;
        transition: all 0.2s;
    }
    .job-card:hover {
        border-color: #7c3aed;
        box-shadow: 0 4px 16px rgba(124, 58, 237, 0.15);
    }
    .job-card.enabled {
        border-left: 4px solid #10b981;
    }
    .job-card.disabled {
        border-left: 4px solid #94a3b8;
        opacity: 0.7;
    }
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 600;
    }
    .status-enabled {
        background: #dcfce7;
        color: #166534;
    }
    .status-disabled {
        background: #f1f5f9;
        color: #64748b;
    }
    .status-ok {
        background: #dcfce7;
        color: #166534;
    }
    .status-failed {
        background: #fee2e2;
        color: #991b1b;
    }
    .status-running {
        background: #dbeafe;
        color: #1e40af;
    }
    .next-run-card {
        background: linear-gradient(135deg, #f0fdf4 0%, #ecfeff 100%);
        border: 1px solid #86efac;
        border-radius: 12px;
        padding: 1rem;
        margin: 0.5rem 0;
    }
    .next-run-card.warning {
        background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
        border-color: #fcd34d;
    }
    .timeline-item {
        border-left: 3px solid #7c3aed;
        padding-left: 1rem;
        margin-left: 0.5rem;
        margin-bottom: 1rem;
    }
    .timeline-time {
        font-size: 0.8rem;
        color: #7c3aed;
        font-weight: 600;
    }
    .connection-status {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.5rem 1rem;
        border-radius: 8px;
        font-weight: 500;
    }
    .connection-status.connected {
        background: #dcfce7;
        color: #166534;
    }
    .connection-status.disconnected {
        background: #fee2e2;
        color: #991b1b;
    }
    .form-section {
        background: #f8fafc;
        border-radius: 12px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


DEFAULT_TOOL_BY_SERVER: Dict[str, str] = {
    "docker": "health_check",
    "kubernetes": "health_check",
    "jenkins": "get_server_info",
    "nexus": "nexus_health_check",
    "git": "git_health_check",
    "trivy": "trivy_health_check",
    "playwright": "playwright_health_check",
    "websearch": "websearch_health_check",
}


def _scheduler_spec_sig(spec: Any) -> str:
    safe_env = {k: ("***" if "TOKEN" in k else v) for k, v in sorted(dict(getattr(spec, "env", {}) or {}).items())}
    payload = {
        "server": getattr(spec, "server_name", ""),
        "transport": getattr(spec, "transport", ""),
        "module": getattr(spec, "module", ""),
        "url": getattr(spec, "url", ""),
        "python": getattr(spec, "python_executable", ""),
        "env": safe_env,
    }
    return json.dumps(payload, sort_keys=True)


def _normalise_streamable_http_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    base = raw.rstrip("/")
    if base.endswith("/mcp"):
        return base
    return base + "/mcp"


def _get_scheduler_client(force_new: bool = False):
    """Get the Scheduler MCP client."""
    return get_mcp_client("scheduler", force_new=force_new)


def _scheduler_list_tools_cached(defs: Dict[str, Any], *, force_reload: bool = False) -> List[Any]:
    """Get scheduler tools using unified client."""
    try:
        client = _get_scheduler_client(force_new=force_reload)
        tools = client.list_tools(force_refresh=force_reload)
        st.session_state["scheduler_tools"] = tools
        st.session_state.pop("scheduler_last_error", None)
        st.session_state["scheduler_connected"] = True
        return tools
    except Exception as exc:
        st.session_state["scheduler_tools"] = []
        st.session_state["scheduler_last_error"] = str(exc)
        st.session_state["scheduler_connected"] = False
        return []


def _call_scheduler_tool(tool: str, args: Dict[str, Any], defs: Dict[str, Any]) -> Dict[str, Any]:
    """Call a scheduler MCP tool using unified client."""
    try:
        client = _get_scheduler_client()
        res = client.invoke(tool, args)
        if isinstance(res, dict) and "ok" in res:
            return res
        return {"ok": True, "result": res}
    except Exception as exc:
        return {"ok": False, "error": "scheduler_tool_call_failed", "details": str(exc)}


def _format_interval(seconds: int) -> str:
    """Format interval in human-readable form."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        mins = seconds // 60
        return f"{mins}m"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        if mins > 0:
            return f"{hours}h {mins}m"
        return f"{hours}h"


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string."""
    if not dt_str:
        return None
    try:
        # Handle various formats
        dt_str = dt_str.replace("Z", "+00:00")
        if "+" in dt_str:
            dt_str = dt_str.split("+")[0]
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def _time_until(dt: Optional[datetime]) -> str:
    """Get human-readable time until a datetime."""
    if not dt:
        return "Unknown"
    now = datetime.utcnow()
    diff = dt - now
    if diff.total_seconds() < 0:
        return "Overdue"
    elif diff.total_seconds() < 60:
        return f"{int(diff.total_seconds())}s"
    elif diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds() / 60)}m"
    else:
        hours = int(diff.total_seconds() / 3600)
        mins = int((diff.total_seconds() % 3600) / 60)
        return f"{hours}h {mins}m"


# =============================================================================
# UI COMPONENTS
# =============================================================================


def render_hero():
    """Render the hero section."""
    st.markdown(
        """
        <div class="scheduler-hero">
            <h1>⏱️ MCP Job Scheduler</h1>
            <p>Automated task scheduling and monitoring for your MCP infrastructure</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_stat_card(value: Any, label: str, variant: str = ""):
    """Render a statistics card."""
    variant_class = f" {variant}" if variant else ""
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value{variant_class}">{value}</div>
            <div class="stat-label">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_connection_status(connected: bool, error: Optional[str] = None):
    """Render connection status indicator."""
    if connected:
        st.markdown(
            '<div class="connection-status connected">✓ Connected to Scheduler</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="connection-status disconnected">✗ Disconnected</div>',
            unsafe_allow_html=True,
        )
        if error:
            st.caption(f"Error: {error[:100]}...")


def render_jobs_dashboard(jobs: List[Dict[str, Any]]):
    """Render the jobs dashboard with stats and visualizations."""
    if not jobs:
        st.info("No jobs configured. Create your first scheduled job below.")
        return

    # Calculate stats
    total = len(jobs)
    enabled = sum(1 for j in jobs if j.get("enabled"))
    disabled = total - enabled

    # Jobs by server
    servers = {}
    for j in jobs:
        srv = j.get("server", "unknown")
        servers[srv] = servers.get(srv, 0) + 1

    # Find next scheduled run
    next_runs = []
    for j in jobs:
        if j.get("enabled") and j.get("next_run_at"):
            dt = _parse_datetime(j["next_run_at"])
            if dt:
                next_runs.append((dt, j))
    next_runs.sort(key=lambda x: x[0])

    # Stats row
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_stat_card(total, "Total Jobs")
    with col2:
        render_stat_card(enabled, "Active", "success")
    with col3:
        render_stat_card(disabled, "Paused", "warning")
    with col4:
        if next_runs:
            next_dt, next_job = next_runs[0]
            time_str = _time_until(next_dt)
            render_stat_card(time_str, "Next Run")
        else:
            render_stat_card("-", "Next Run")

    st.markdown("")

    # Charts row
    col1, col2 = st.columns(2)

    with col1:
        if servers:
            df = pd.DataFrame([{"server": k, "count": v} for k, v in servers.items()])
            fig = px.pie(
                df,
                values="count",
                names="server",
                title="Jobs by MCP Server",
                hole=0.4,
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig.update_layout(
                height=280,
                margin=dict(t=40, b=20, l=20, r=20),
                legend=dict(orientation="h", yanchor="bottom", y=-0.15),
            )
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        # Next runs timeline
        st.markdown("#### Upcoming Runs")
        if next_runs[:5]:
            for dt, job in next_runs[:5]:
                time_str = _time_until(dt)
                st.markdown(
                    f"""
                    <div class="timeline-item">
                        <div class="timeline-time">{time_str}</div>
                        <div><strong>{job.get('label', 'Unnamed')}</strong></div>
                        <div style="font-size: 0.8rem; color: #64748b;">{job.get('server', '')} / {job.get('tool', '')}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.caption("No upcoming runs scheduled")


def render_jobs_table(jobs: List[Dict[str, Any]], defs: Dict[str, Any]):
    """Render the jobs table with actions."""
    if not jobs:
        return

    st.markdown("### All Jobs")

    for job in jobs:
        enabled = job.get("enabled", False)
        status_class = "enabled" if enabled else "disabled"
        status_badge = "status-enabled" if enabled else "status-disabled"
        status_text = "Active" if enabled else "Paused"

        next_run = job.get("next_run_at", "")
        next_dt = _parse_datetime(next_run)
        time_until = _time_until(next_dt) if enabled else "-"

        col1, col2, col3 = st.columns([3, 1, 1])

        with col1:
            st.markdown(
                f"""
                <div class="job-card {status_class}">
                    <div style="display: flex; justify-content: space-between; align-items: start;">
                        <div>
                            <div style="font-weight: 600; font-size: 1.1rem;">{job.get('label', 'Unnamed Job')}</div>
                            <div style="color: #64748b; font-size: 0.85rem; margin-top: 0.25rem;">
                                {job.get('server', '')} / {job.get('tool', '')}
                            </div>
                        </div>
                        <span class="status-badge {status_badge}">{status_text}</span>
                    </div>
                    <div style="margin-top: 0.75rem; display: flex; gap: 1.5rem; font-size: 0.85rem; color: #64748b;">
                        <span>Every {_format_interval(job.get('interval_seconds', 60))}</span>
                        <span>Next: {time_until}</span>
                        <span style="font-family: monospace; font-size: 0.75rem;">ID: {job.get('id', '')[:8]}...</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        with col2:
            if st.button("Toggle" if enabled else "Enable", key=f"toggle_{job.get('id')}", use_container_width=True):
                _call_scheduler_tool(
                    "scheduler_upsert_job",
                    {"job_id": job.get("id"), "enabled": not enabled},
                    defs,
                )
                st.rerun()

        with col3:
            if st.button("Delete", key=f"delete_{job.get('id')}", type="secondary", use_container_width=True):
                _call_scheduler_tool("scheduler_delete_job", {"job_id": job.get("id")}, defs)
                st.rerun()


def render_runs_dashboard(runs: List[Dict[str, Any]], *, key_prefix: str = "runs"):
    """Render the runs dashboard with stats and history."""
    if not runs:
        st.info("No job runs recorded yet. Runs will appear here after jobs execute.")
        return

    # Calculate stats
    total = len(runs)
    successful = sum(1 for r in runs if r.get("ok") is True)
    failed = sum(1 for r in runs if r.get("ok") is False)
    success_rate = round(successful / total * 100, 1) if total > 0 else 0

    # Stats row
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        render_stat_card(total, "Total Runs")
    with col2:
        render_stat_card(successful, "Successful", "success")
    with col3:
        render_stat_card(failed, "Failed", "danger")
    with col4:
        render_stat_card(f"{success_rate}%", "Success Rate")

    st.markdown("")

    # Charts
    col1, col2 = st.columns(2)

    with col1:
        # Success rate gauge
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=success_rate,
                title={"text": "Success Rate"},
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": "#7c3aed"},
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
            height=250,
            margin=dict(t=40, b=20, l=20, r=20),
        )
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_success_gauge")

    with col2:
        # Runs by status
        status_data = [
            {"status": "Successful", "count": successful, "color": "#10b981"},
            {"status": "Failed", "count": failed, "color": "#ef4444"},
        ]
        df = pd.DataFrame(status_data)
        fig = px.bar(
            df,
            x="status",
            y="count",
            title="Runs by Status",
            color="status",
            color_discrete_map={"Successful": "#10b981", "Failed": "#ef4444"},
        )
        fig.update_layout(
            height=250,
            margin=dict(t=40, b=20, l=20, r=20),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True, key=f"{key_prefix}_status_bar")


def render_runs_table(runs: List[Dict[str, Any]], only_failures: bool = False):
    """Render the runs history table."""
    if not runs:
        return

    if only_failures:
        runs = [r for r in runs if r.get("ok") is False]

    if not runs:
        st.info("No failures to show.")
        return

    st.markdown("### Run History")

    df = pd.DataFrame(runs)
    if df.empty:
        return

    df["status"] = df["ok"].map({True: "Success", False: "Failed"}).fillna("Unknown")
    df["error"] = df["error"].fillna("").apply(
        lambda v: (v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v
    )

    # Select and order columns
    display_cols = ["status", "job_id", "started_at", "finished_at", "error", "id"]
    df = df[[c for c in display_cols if c in df.columns]]

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "status": st.column_config.TextColumn("Status", width="small"),
            "job_id": st.column_config.TextColumn("Job ID", width="medium"),
            "started_at": st.column_config.TextColumn("Started", width="medium"),
            "finished_at": st.column_config.TextColumn("Finished", width="medium"),
            "error": st.column_config.TextColumn("Error", width="large"),
            "id": st.column_config.TextColumn("Run ID", width="small"),
        },
    )


def render_create_job_form(defs: Dict[str, Any], connected: bool):
    """Render the job creation form."""
    st.markdown("### Create New Job")

    with st.container():
        st.markdown('<div class="form-section">', unsafe_allow_html=True)

        col1, col2 = st.columns(2)

        with col1:
            label = st.text_input("Job Name", placeholder="e.g., Docker Health Check")
            server = st.selectbox(
                "Target Server",
                options=list(DEFAULT_TOOL_BY_SERVER.keys()),
                format_func=lambda x: f"{x.capitalize()} MCP",
            )

        with col2:
            default_tool = DEFAULT_TOOL_BY_SERVER.get(server, "health_check")
            tool = st.text_input("Tool Name", value=default_tool)
            interval = st.selectbox(
                "Run Interval",
                options=[30, 60, 300, 600, 1800, 3600],
                index=1,
                format_func=lambda x: _format_interval(x),
            )

        args_json = st.text_area(
            "Arguments (JSON)",
            value="{}",
            height=80,
            help="JSON object with tool arguments",
        )

        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            enabled = st.checkbox("Enable immediately", value=True)
        with col3:
            submitted = st.button(
                "Create Job",
                type="primary",
                disabled=not connected or not label,
                use_container_width=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

        if submitted and label:
            try:
                args_obj = json.loads(args_json or "{}")
                if not isinstance(args_obj, dict):
                    raise ValueError("Args must be a JSON object")

                payload = {
                    "enabled": enabled,
                    "label": label,
                    "server": server,
                    "tool": tool,
                    "args": args_obj,
                    "interval_seconds": interval,
                }

                res = _call_scheduler_tool("scheduler_upsert_job", payload, defs)
                if res.get("ok"):
                    st.success(f"Job '{label}' created successfully!")
                    st.rerun()
                else:
                    st.error(f"Failed to create job: {res.get('details', res.get('error', 'Unknown error'))}")
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")


# =============================================================================
# MAIN PAGE
# =============================================================================


def main():
    render_hero()

    # Get config and definitions
    cfg = get_app_config()
    defs = build_server_specs(cfg)

    if "scheduler" not in defs:
        st.error(
            "Scheduler MCP server is not configured. Please check your admin configuration."
        )
        st.stop()

    # Sidebar - Connection management
    with st.sidebar:
        st.markdown("### Connection")

        connect = st.checkbox(
            "Connect to Scheduler",
            value=st.session_state.get("scheduler_auto_connect", False),
            help="Connect to the scheduler MCP service",
        )

        if connect:
            _scheduler_list_tools_cached(defs)

        connected = st.session_state.get("scheduler_connected", False)
        error = st.session_state.get("scheduler_last_error")

        render_connection_status(connected, error)

        if connected:
            if st.button("Refresh Data", use_container_width=True):
                _scheduler_list_tools_cached(defs, force_reload=True)
                st.session_state.pop("scheduler_jobs", None)
                st.session_state.pop("scheduler_runs", None)
                st.rerun()

        st.divider()

        st.markdown("### Quick Actions")
        if st.button("Health Check", disabled=not connected, use_container_width=True):
            result = _call_scheduler_tool("scheduler_health", {}, defs)
            st.session_state["scheduler_health"] = result

        if "scheduler_health" in st.session_state:
            health = st.session_state["scheduler_health"]
            if health.get("ok"):
                st.success("Scheduler is healthy")
            else:
                st.error("Scheduler unhealthy")

        st.divider()
        st.caption("Auto-refresh every 30s")
        auto_refresh = st.checkbox("Enable auto-refresh", value=False)

    # Main content
    if not connect:
        st.info(
            "Connect to the scheduler service using the checkbox in the sidebar to view jobs and runs."
        )
        st.stop()

    if not connected:
        st.warning(
            "Could not connect to the scheduler service. Make sure it's running and try again."
        )
        if error:
            with st.expander("Error Details"):
                st.code(error)
        st.stop()

    # Load data
    if "scheduler_jobs" not in st.session_state:
        st.session_state["scheduler_jobs"] = _call_scheduler_tool("scheduler_list_jobs", {}, defs)

    if "scheduler_runs" not in st.session_state:
        st.session_state["scheduler_runs"] = _call_scheduler_tool("scheduler_list_runs", {"limit": 100}, defs)

    jobs_res = st.session_state.get("scheduler_jobs", {})
    runs_res = st.session_state.get("scheduler_runs", {})

    jobs = list(jobs_res.get("jobs", [])) if jobs_res.get("ok") else []
    runs = list(runs_res.get("runs", [])) if runs_res.get("ok") else []

    # Tabs
    tab_dashboard, tab_jobs, tab_runs, tab_create = st.tabs([
        "📊 Dashboard",
        "📋 Jobs",
        "📈 Run History",
        "➕ Create Job",
    ])

    with tab_dashboard:
        st.markdown("## Overview")
        render_jobs_dashboard(jobs)

        if runs:
            st.divider()
            st.markdown("## Recent Activity")
            render_runs_dashboard(runs[:50], key_prefix="dashboard_runs")

    with tab_jobs:
        st.markdown("## Scheduled Jobs")

        col1, col2 = st.columns([3, 1])
        with col2:
            if st.button("Reload Jobs", use_container_width=True):
                st.session_state["scheduler_jobs"] = _call_scheduler_tool("scheduler_list_jobs", {}, defs)
                st.rerun()

        if jobs_res.get("ok"):
            render_jobs_table(jobs, defs)
        else:
            st.error(f"Failed to load jobs: {jobs_res.get('details', jobs_res.get('error', 'Unknown'))}")

    with tab_runs:
        st.markdown("## Execution History")

        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            limit = st.selectbox("Show last", options=[25, 50, 100, 200], index=1)
        with col2:
            only_failures = st.checkbox("Failures only", value=False)
        with col3:
            if st.button("Reload Runs", use_container_width=True):
                st.session_state["scheduler_runs"] = _call_scheduler_tool(
                    "scheduler_list_runs", {"limit": limit}, defs
                )
                st.rerun()

        if runs_res.get("ok"):
            render_runs_dashboard(runs[:limit], key_prefix="history_runs")
            st.divider()
            render_runs_table(runs[:limit], only_failures)
        else:
            st.error(f"Failed to load runs: {runs_res.get('details', runs_res.get('error', 'Unknown'))}")

    with tab_create:
        render_create_job_form(defs, connected)

    # Footer
    st.divider()
    st.caption(
        f"⏱️ MCP Scheduler | {len(jobs)} jobs configured | {len(runs)} runs recorded | "
        f"Last updated: {datetime.now().strftime('%H:%M:%S')}"
    )

    # Auto-refresh
    if auto_refresh:
        import time
        time.sleep(30)
        st.session_state["scheduler_jobs"] = _call_scheduler_tool("scheduler_list_jobs", {}, defs)
        st.session_state["scheduler_runs"] = _call_scheduler_tool("scheduler_list_runs", {"limit": 100}, defs)
        st.rerun()


if __name__ == "__main__":
    main()
else:
    main()
