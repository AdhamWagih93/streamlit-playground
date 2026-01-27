from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.mcp_health import check_mcp_server_simple
from src.tasks_repo import get_all_tasks, init_db
from src.theme import set_theme


set_theme(page_title="BSW Platform", page_icon="🚀")


# ==============================================================================
# Data Loading Functions
# ==============================================================================


@st.cache_data(ttl=30)
def _get_task_kpis() -> Dict[str, Any]:
    """Get task metrics for quick KPIs."""
    try:
        init_db()
        tasks = get_all_tasks()
    except Exception:
        tasks = []

    total = len(tasks)
    by_status = {}
    for t in tasks:
        s = (t.get("status") or "Unknown").strip() or "Unknown"
        by_status[s] = by_status.get(s, 0) + 1

    return {
        "total": total,
        "done": by_status.get("Done", 0),
        "in_progress": by_status.get("In-Progress", 0) + by_status.get("In Progress", 0),
        "review": by_status.get("Review", 0),
        "backlog": by_status.get("Backlog", 0),
        "todo": by_status.get("To-Do", 0) + by_status.get("To Do", 0),
    }


def _get_mcp_servers() -> List[Dict[str, str]]:
    """Get list of MCP servers to monitor."""
    return [
        {
            "id": "docker",
            "name": "Docker MCP",
            "icon": "🐳",
            "url": os.getenv("STREAMLIT_DOCKER_MCP_URL", "http://docker-mcp:8000"),
        },
        {
            "id": "jenkins",
            "name": "Jenkins MCP",
            "icon": "🔧",
            "url": os.getenv("STREAMLIT_JENKINS_MCP_URL", "http://jenkins-mcp:8000"),
        },
        {
            "id": "kubernetes",
            "name": "Kubernetes MCP",
            "icon": "☸️",
            "url": os.getenv("STREAMLIT_KUBERNETES_MCP_URL", "http://kubernetes-mcp:8000"),
        },
        {
            "id": "scheduler",
            "name": "Scheduler MCP",
            "icon": "⏱️",
            "url": os.getenv("STREAMLIT_SCHEDULER_MCP_URL", "http://scheduler:8010"),
        },
        {
            "id": "nexus",
            "name": "Nexus MCP",
            "icon": "📦",
            "url": os.getenv("STREAMLIT_NEXUS_MCP_URL", "http://nexus-mcp:8000"),
        },
    ]


@st.cache_data(ttl=60)
def _check_all_mcp_health() -> Dict[str, Dict[str, Any]]:
    """Check health of all MCP servers."""
    servers = _get_mcp_servers()
    results = {}

    for server in servers:
        try:
            health = asyncio.run(check_mcp_server_simple(server["id"], server["url"], timeout=3.0))
            results[server["id"]] = health
        except Exception as e:
            results[server["id"]] = {
                "status": "unhealthy",
                "message": f"Error: {str(e)[:50]}",
                "response_time_ms": 0,
            }

    return results


@st.cache_data(ttl=30)
def _get_recent_activity() -> List[Dict[str, Any]]:
    """Get recent activity across the system."""
    try:
        init_db()
        tasks = get_all_tasks()
    except Exception:
        tasks = []

    # Sort by updated_at descending
    tasks_sorted = sorted(
        tasks,
        key=lambda t: t.get("updated_at") or t.get("created_at") or "",
        reverse=True,
    )

    # Get last 10 tasks
    recent = []
    for task in tasks_sorted[:10]:
        recent.append({
            "type": "task",
            "icon": "📋",
            "title": task.get("title", "Untitled"),
            "status": task.get("status", "Unknown"),
            "timestamp": task.get("updated_at") or task.get("created_at") or "Unknown",
        })

    return recent


def _generate_velocity_chart() -> go.Figure:
    """Generate team velocity chart showing task completion trends."""
    try:
        init_db()
        tasks = get_all_tasks()
    except Exception:
        tasks = []

    # Generate last 14 days of data
    today = datetime.now()
    dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]

    completed_by_day = {date: 0 for date in dates}
    created_by_day = {date: 0 for date in dates}

    for task in tasks:
        # Count completed tasks
        if task.get("status") == "Done":
            done_date = task.get("updated_at", "")[:10]
            if done_date in completed_by_day:
                completed_by_day[done_date] += 1

        # Count created tasks
        created_date = (task.get("created_at") or "")[:10]
        if created_date in created_by_day:
            created_by_day[created_date] += 1

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=dates,
        y=[completed_by_day[d] for d in dates],
        mode='lines+markers',
        name='Completed',
        line=dict(color='#10B981', width=3),
        marker=dict(size=8),
    ))

    fig.add_trace(go.Scatter(
        x=dates,
        y=[created_by_day[d] for d in dates],
        mode='lines+markers',
        name='Created',
        line=dict(color='#6366f1', width=3),
        marker=dict(size=8),
    ))

    fig.update_layout(
        title="Task Velocity (Last 14 Days)",
        xaxis_title="Date",
        yaxis_title="Tasks",
        height=300,
        margin=dict(l=20, r=20, t=40, b=20),
        hovermode='x unified',
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )

    return fig


# ==============================================================================
# Session State Initialization
# ==============================================================================

if "favorites" not in st.session_state:
    st.session_state.favorites = ["pages/1_Team_Task_Manager.py"]

if "show_activity_feed" not in st.session_state:
    st.session_state.show_activity_feed = True

if "show_health_overview" not in st.session_state:
    st.session_state.show_health_overview = True

if "show_velocity_chart" not in st.session_state:
    st.session_state.show_velocity_chart = True


# ==============================================================================
# Hero Section
# ==============================================================================

st.markdown(
    """
    <div class="st-hero">
        <h1>🚀 BSW Platform</h1>
        <p>Your unified platform for team management, DevOps automation, and AI-powered workflows.
        Manage tasks, monitor infrastructure, orchestrate deployments, and build intelligent agents.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.write("")  # Spacing


# ==============================================================================
# Quick Actions Panel
# ==============================================================================

st.markdown("### ⚡ Quick Actions")

action_col1, action_col2, action_col3, action_col4 = st.columns(4)

with action_col1:
    if st.button("📋 Create Task", use_container_width=True, type="primary"):
        st.switch_page("pages/1_Team_Task_Manager.py")

with action_col2:
    if st.button("🔍 Search Everything", use_container_width=True):
        st.session_state["global_search_open"] = True
        st.info("Global search coming soon! Will search across tasks, containers, pods, and artifacts.")

with action_col3:
    if st.button("🔧 Check Jenkins", use_container_width=True):
        st.switch_page("pages/5_Agent_Management.py")

with action_col4:
    if st.button("☸️ View Cluster", use_container_width=True):
        st.switch_page("pages/6_Kubernetes.py")

st.divider()


# ==============================================================================
# System Health Overview
# ==============================================================================

health_header = st.columns([3, 1])
with health_header[0]:
    st.markdown("### 🏥 System Health Overview")
    st.caption("Real-time health checks for all MCP servers")

with health_header[1]:
    if st.button("Refresh Health", use_container_width=True):
        _check_all_mcp_health.clear()  # type: ignore[attr-defined]
        st.rerun()

health_results = _check_all_mcp_health()
servers = _get_mcp_servers()

health_cols = st.columns(len(servers))

for idx, server in enumerate(servers):
    with health_cols[idx]:
        health = health_results.get(server["id"], {})
        status = health.get("status", "unknown")
        response_time = health.get("response_time_ms", 0)
        message = health.get("message", "")

        # Determine badge class
        if status == "healthy":
            badge_class = "st-badge-success"
            status_text = "✓ Healthy"
        else:
            badge_class = "st-badge-danger"
            status_text = "✗ Unhealthy"

        st.markdown(
            f"""
            <div class="st-card">
                <div style="font-size: 2rem; text-align: center; margin-bottom: 0.5rem;">{server["icon"]}</div>
                <div style="font-size: 0.875rem; font-weight: 600; text-align: center; margin-bottom: 0.5rem;">
                    {server["name"]}
                </div>
                <div style="text-align: center; margin-bottom: 0.5rem;">
                    <span class="{badge_class} st-badge">{status_text}</span>
                </div>
                <div style="font-size: 0.75rem; color: #64748B; text-align: center;">
                    {response_time}ms
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if message and status != "healthy":
            st.caption(f"⚠️ {message[:40]}...")

st.divider()


# ==============================================================================
# Dashboard Grid (KPIs + Velocity Chart)
# ==============================================================================

st.markdown("### 📊 Dashboard")

# KPIs Row
kpi_cols = st.columns(6)
kpis = _get_task_kpis()

with kpi_cols[0]:
    st.markdown(
        f"""
        <div class="st-metric">
            <div class="st-metric-value">{kpis['total']}</div>
            <div class="st-metric-label">Total Tasks</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[1]:
    st.markdown(
        f"""
        <div class="st-metric">
            <div class="st-metric-value" style="color: #F59E0B;">{kpis['in_progress']}</div>
            <div class="st-metric-label">In Progress</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[2]:
    st.markdown(
        f"""
        <div class="st-metric">
            <div class="st-metric-value" style="color: #8b5cf6;">{kpis['review']}</div>
            <div class="st-metric-label">In Review</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[3]:
    st.markdown(
        f"""
        <div class="st-metric">
            <div class="st-metric-value" style="color: #10B981;">{kpis['done']}</div>
            <div class="st-metric-label">Done</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[4]:
    st.markdown(
        f"""
        <div class="st-metric">
            <div class="st-metric-value" style="color: #3B82F6;">{kpis['todo']}</div>
            <div class="st-metric-label">To Do</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[5]:
    st.markdown(
        f"""
        <div class="st-metric">
            <div class="st-metric-value" style="color: #64748B;">{kpis['backlog']}</div>
            <div class="st-metric-label">Backlog</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

st.write("")  # Spacing

# Velocity Chart
chart_col, activity_col = st.columns([2, 1])

with chart_col:
    try:
        fig = _generate_velocity_chart()
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.error(f"Failed to generate velocity chart: {e}")

# Recent Activity Feed
with activity_col:
    st.markdown("#### 📰 Recent Activity")
    st.caption("Latest updates across the system")

    activity = _get_recent_activity()

    if activity:
        for item in activity[:5]:
            # Determine status badge
            status = item.get("status", "Unknown")
            if status == "Done":
                badge = "st-badge-success"
            elif status in ("In-Progress", "In Progress"):
                badge = "st-badge-warning"
            elif status == "Review":
                badge = "st-badge-info"
            else:
                badge = "st-badge"

            st.markdown(
                f"""
                <div style="padding: 0.5rem; border-bottom: 1px solid #E2E8F0; margin-bottom: 0.5rem;">
                    <div style="font-size: 0.875rem; font-weight: 600; margin-bottom: 0.25rem;">
                        {item['icon']} {item['title'][:30]}...
                    </div>
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span class="{badge} st-badge">{status}</span>
                        <span style="font-size: 0.75rem; color: #94A3B8;">{item['timestamp'][:16]}</span>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("No recent activity")

st.divider()


# ==============================================================================
# Favorite Pages
# ==============================================================================

st.markdown("### ⭐ Favorite Pages")
st.caption("Quick access to your most-used pages")

fav_cols = st.columns(4)

page_info = {
    "pages/1_Team_Task_Manager.py": {"name": "Task Manager", "icon": "📋"},
    "pages/5_Agent_Management.py": {"name": "Agent Management", "icon": "🧠"},
    "pages/6_Kubernetes.py": {"name": "Kubernetes", "icon": "☸️"},
    "pages/8_Docker_MCP_Test.py": {"name": "Docker MCP", "icon": "🐳"},
}

for idx, page_path in enumerate(st.session_state.favorites[:4]):
    with fav_cols[idx]:
        info = page_info.get(page_path, {"name": "Unknown", "icon": "📄"})
        if st.button(f"{info['icon']} {info['name']}", use_container_width=True):
            st.switch_page(page_path)

st.divider()


# ==============================================================================
# Explore All Pages
# ==============================================================================

st.markdown("### 🗺️ Explore All Pages")
st.caption("Browse all available pages by category")

team_tab, devops_tab, ai_tab = st.tabs(["Team Management", "DevOps & Infrastructure", "AI & Automation"])

with team_tab:
    c1, c2, c3 = st.columns(3)

    with c1:
        with st.container(border=True):
            st.markdown("#### 📋 Team Task Manager")
            st.caption("Plan, track, and ship work with a rich Kanban + analytics UI.")
            st.page_link("pages/1_Team_Task_Manager.py", label="Open →", icon="📋")

    with c2:
        with st.container(border=True):
            st.markdown("#### 🧑‍💼 DevOps Referral Agent")
            st.caption("Parse CVs and generate structured signals for referral decisions.")
            st.page_link("pages/2_DevOps_Referral_Agent.py", label="Open →", icon="🧑‍💼")

    with c3:
        with st.container(border=True):
            st.markdown("#### 📅 WFH Schedule")
            st.caption("Two-week rotation planner with validations + full-year grids.")
            st.page_link("pages/3_WFH_Schedule.py", label="Open →", icon="📅")

with devops_tab:
    d1, d2, d3 = st.columns(3)

    with d1:
        with st.container(border=True):
            st.markdown("#### ☸️ Kubernetes")
            st.caption("Cluster insights and Helm operations via MCP.")
            st.page_link("pages/6_Kubernetes.py", label="Open →", icon="☸️")

    with d2:
        with st.container(border=True):
            st.markdown("#### 🐳 Docker MCP Test")
            st.caption("Test Docker MCP connectivity and basic operations.")
            st.page_link("pages/8_Docker_MCP_Test.py", label="Open →", icon="🐳")

    with d3:
        with st.container(border=True):
            st.markdown("#### 📦 Nexus Explorer")
            st.caption("Explore Nexus repositories and artifacts.")
            st.page_link("pages/9_Nexus_Explorer.py", label="Open →", icon="📦")

    d4, d5, d6 = st.columns(3)

    with d4:
        with st.container(border=True):
            st.markdown("#### 📊 System Status")
            st.caption("Monitor system health and service status.")
            st.page_link("pages/12_System_Status.py", label="Open →", icon="📊")

    with d5:
        with st.container(border=True):
            st.markdown("#### 🔌 MCP Servers")
            st.caption("Central management for all MCP servers.")
            st.page_link("pages/13_MCP_Servers.py", label="Open →", icon="🔌")

    with d6:
        st.empty()

with ai_tab:
    a1, a2, a3 = st.columns(3)

    with a1:
        with st.container(border=True):
            st.markdown("#### 🧪 DataGen Agent")
            st.caption("Generate synthetic datasets with an agent workflow.")
            st.page_link("pages/4_DataGen_Agent.py", label="Open →", icon="🧪")

    with a2:
        with st.container(border=True):
            st.markdown("#### 🧠 Agent Management")
            st.caption("Inspect, run, and debug your agents and tool calls.")
            st.page_link("pages/5_Agent_Management.py", label="Open →", icon="🧠")

    with a3:
        with st.container(border=True):
            st.markdown("#### ⏱️ MCP Scheduler")
            st.caption("Schedule MCP health checks and tool runs.")
            st.page_link("pages/10_MCP_Scheduler.py", label="Open →", icon="⏱️")


# ==============================================================================
# Footer
# ==============================================================================

st.divider()
st.caption("💡 **Tip**: Use the sidebar to navigate between pages. All pages feature hot-reload and real-time updates.")
