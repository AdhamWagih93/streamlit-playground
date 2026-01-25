"""Central MCP Server Management Page - Simplified."""

import asyncio
import os
from datetime import datetime

import streamlit as st

from src.theme import set_theme
from src.mcp_health import (
    add_mcp_status_styles,
    check_mcp_server_simple,
    get_status_badge_class,
    get_status_icon,
)


set_theme(page_title="MCP Servers", page_icon="🔌")
add_mcp_status_styles()

st.markdown(
    """
    <style>
    .mcp-hero {
        background: linear-gradient(135deg, #7c3aed 0%, #2563eb 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(124, 58, 237, 0.3);
    }
    .mcp-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
    }
    .mcp-server-card {
        background: white;
        border-radius: 12px;
        padding: 1.25rem;
        border: 2px solid #e2e8f0;
        margin-bottom: 1rem;
        transition: all 0.2s;
    }
    .mcp-server-card:hover {
        border-color: #7c3aed;
        box-shadow: 0 4px 16px rgba(124, 58, 237, 0.15);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="mcp-hero">
        <h1>🔌 MCP Server Management</h1>
        <p>Monitor and test all Model Context Protocol servers</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# Define MCP servers with their Docker internal URLs
MCP_SERVERS = [
    {
        "id": "docker",
        "name": "Docker MCP",
        "icon": "🐳",
        "url": os.getenv("STREAMLIT_DOCKER_MCP_URL", "http://docker-mcp:8000"),
        "description": "Container and image management",
    },
    {
        "id": "jenkins",
        "name": "Jenkins MCP",
        "icon": "🔧",
        "url": os.getenv("STREAMLIT_JENKINS_MCP_URL", "http://jenkins-mcp:8000"),
        "description": "CI/CD pipeline integration",
    },
    {
        "id": "kubernetes",
        "name": "Kubernetes MCP",
        "icon": "☸️",
        "url": os.getenv("STREAMLIT_KUBERNETES_MCP_URL", "http://kubernetes-mcp:8000"),
        "description": "Cluster and workload management",
    },
    {
        "id": "scheduler",
        "name": "Scheduler MCP",
        "icon": "⏱️",
        "url": os.getenv("STREAMLIT_SCHEDULER_MCP_URL", "http://scheduler:8010"),
        "description": "Background job orchestration",
    },
    {
        "id": "nexus",
        "name": "Nexus MCP",
        "icon": "📦",
        "url": os.getenv("STREAMLIT_NEXUS_MCP_URL", "http://nexus-mcp:8000"),
        "description": "Artifact repository (optional)",
    },
]

# Auto-refresh
col1, col2 = st.columns([3, 1])
with col1:
    st.subheader("📊 Server Health Dashboard")
with col2:
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)

if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()

# Check health button
if st.button("🔄 Check All Servers", use_container_width=True, type="primary"):
    st.session_state.mcp_health_results = {}

    with st.spinner("Checking all MCP servers..."):
        for server in MCP_SERVERS:
            try:
                health = asyncio.run(check_mcp_server_simple(server["id"], server["url"], timeout=5.0))
                st.session_state.mcp_health_results[server["id"]] = health
            except Exception as e:
                st.session_state.mcp_health_results[server["id"]] = {
                    "status": "unhealthy",
                    "message": f"Error: {str(e)[:80]}",
                    "response_time_ms": 0,
                    "tool_count": 0,
                    "last_checked": datetime.now().isoformat(),
                }

        st.session_state.mcp_last_check = datetime.now()
        st.success("Health check complete!")
        st.rerun()

# Display results
if "mcp_health_results" not in st.session_state:
    st.info("Click **Check All Servers** to test connectivity to all MCP servers.")
else:
    health_results = st.session_state.mcp_health_results
    last_check = st.session_state.get("mcp_last_check", datetime.now())

    # Summary metrics
    total = len(health_results)
    healthy = sum(1 for h in health_results.values() if h.get("status") == "healthy")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Servers", total)
    col2.metric("Healthy", healthy, delta=f"{healthy/total*100:.0f}%" if total > 0 else "0%")
    col3.metric("Unhealthy", total - healthy)
    col4.metric("Last Check", last_check.strftime("%H:%M:%S"))

    st.divider()

    # Server cards
    for server in MCP_SERVERS:
        health = health_results.get(server["id"], {})
        status = health.get("status", "unknown")

        status_class = get_status_badge_class(status)
        status_icon = get_status_icon(status)

        with st.container(border=True):
            col_info, col_status = st.columns([3, 1])

            with col_info:
                st.markdown(f"### {server['icon']} {server['name']}")
                st.caption(server['description'])
                st.markdown(f"**URL:** `{server['url']}`")

                if health.get("tool_count", 0) > 0:
                    st.markdown(f"**Tools:** {health['tool_count']}")

                if health.get("response_time_ms"):
                    st.markdown(f"**Response Time:** {health['response_time_ms']}ms")

            with col_status:
                st.markdown(
                    f'<span class="status-badge {status_class}">{status_icon} {status.capitalize()}</span>',
                    unsafe_allow_html=True,
                )
                st.caption(health.get("message", "Unknown"))

    # Warning if any unhealthy
    if healthy < total:
        st.warning(
            f"⚠️ **{total - healthy} server(s) are not healthy.**\n\n"
            "**Quick Fix:**\n"
            "1. Check services are running: `docker-compose ps`\n"
            "2. Restart services: `./scripts/dev-stop.ps1 -Remove` then `./scripts/dev-start.ps1`\n"
            "3. Check logs: `./scripts/dev-logs.ps1 <service-name>`"
        )

st.divider()

# ==============================================================================
# TOOL EXPLORER SECTION
# ==============================================================================
st.subheader("🔧 Tool Explorer")
st.caption("View and test tools available on each MCP server")

# Server selector for tool exploration
tool_server = st.selectbox(
    "Select Server",
    options=[s["id"] for s in MCP_SERVERS],
    format_func=lambda x: next((s["name"] for s in MCP_SERVERS if s["id"] == x), x),
)

if st.button("Load Tools", use_container_width=True):
    selected_server = next((s for s in MCP_SERVERS if s["id"] == tool_server), None)
    if selected_server:
        with st.spinner(f"Loading tools from {selected_server['name']}..."):
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient

                client = MultiServerMCPClient({
                    tool_server: {
                        "transport": "sse",
                        "url": selected_server["url"],
                    }
                })
                tools = asyncio.run(client.get_tools())
                st.session_state[f"_mcp_tools_{tool_server}"] = list(tools or [])
                st.success(f"Loaded {len(list(tools or []))} tools!")
            except Exception as e:
                st.error(f"Failed to load tools: {e}")

# Display loaded tools
loaded_tools = st.session_state.get(f"_mcp_tools_{tool_server}", [])
if loaded_tools:
    st.markdown(f"**{len(loaded_tools)} tools available:**")

    for tool in loaded_tools:
        with st.expander(f"🔧 {tool.name}", expanded=False):
            st.markdown(f"**Description:** {tool.description[:200] if tool.description else 'No description'}...")
            if hasattr(tool, "args_schema") and tool.args_schema:
                st.markdown("**Parameters:**")
                schema = tool.args_schema.schema() if hasattr(tool.args_schema, "schema") else {}
                if schema.get("properties"):
                    for param, details in schema.get("properties", {}).items():
                        required = param in schema.get("required", [])
                        st.markdown(f"- `{param}` ({details.get('type', 'any')}) {'*required*' if required else ''}")
else:
    st.info("Click **Load Tools** to see available tools for the selected server.")

st.divider()

# ==============================================================================
# HEALTH HISTORY
# ==============================================================================
st.subheader("📈 Health History")

# Store health history
if "mcp_health_history" not in st.session_state:
    st.session_state.mcp_health_history = []

# Add current results to history if available
if "mcp_health_results" in st.session_state and st.session_state.get("mcp_last_check"):
    last_entry = st.session_state.mcp_health_history[-1] if st.session_state.mcp_health_history else None
    current_check = st.session_state.mcp_last_check

    if not last_entry or last_entry.get("timestamp") != current_check.isoformat():
        entry = {
            "timestamp": current_check.isoformat(),
            "healthy_count": sum(1 for h in st.session_state.mcp_health_results.values() if h.get("status") == "healthy"),
            "total_count": len(st.session_state.mcp_health_results),
        }
        st.session_state.mcp_health_history.append(entry)
        # Keep only last 50 entries
        st.session_state.mcp_health_history = st.session_state.mcp_health_history[-50:]

# Display history chart
if st.session_state.mcp_health_history:
    import pandas as pd
    import plotly.graph_objects as go

    df = pd.DataFrame(st.session_state.mcp_health_history)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["health_pct"] = (df["healthy_count"] / df["total_count"] * 100).round(1)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["timestamp"],
        y=df["health_pct"],
        mode='lines+markers',
        name='Health %',
        line=dict(color='#10B981', width=3),
        fill='tozeroy',
        fillcolor='rgba(16, 185, 129, 0.2)',
    ))

    fig.update_layout(
        title="MCP Server Health Over Time",
        xaxis_title="Time",
        yaxis_title="Health %",
        yaxis=dict(range=[0, 105]),
        height=250,
        margin=dict(l=20, r=20, t=40, b=20),
        hovermode='x unified',
    )

    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Health history will appear here after running health checks.")

st.divider()

# Configuration info
st.subheader("⚙️ Connection Information")
st.caption("These are the URLs Streamlit uses to connect to MCP servers (inside Docker network)")

for server in MCP_SERVERS:
    with st.expander(f"{server['icon']} {server['name']}", expanded=False):
        st.code(f"URL: {server['url']}\nTransport: SSE (Server-Sent Events)", language="text")

st.caption(
    "💡 **Tip:** Use auto-refresh to monitor server health in real-time. "
    "Visit individual pages (Docker MCP Test, Kubernetes, etc.) for full functionality."
)
