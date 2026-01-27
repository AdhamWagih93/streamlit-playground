"""Central MCP Server Management Page - With debugging."""

import asyncio
import json
import os
import traceback
from datetime import datetime
from typing import Any, Dict, Set

import streamlit as st

from src.theme import set_theme
from src.mcp_health import (
    add_mcp_status_styles,
    check_mcp_server_simple,
    check_mcp_server_http_simple,
    get_status_badge_class,
    get_status_icon,
    show_debug_info,
    _get_base_url,
    _get_mcp_url,
)


def _to_json_safe(value: Any, seen: Set[int] | None = None) -> Any:
    if seen is None:
        seen = set()

    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    obj_id = id(value)
    if obj_id in seen:
        return "[circular]"
    seen.add(obj_id)

    if isinstance(value, dict):
        return {str(k): _to_json_safe(v, seen) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_to_json_safe(v, seen) for v in value]

    if hasattr(value, "model_dump"):
        try:
            return _to_json_safe(value.model_dump(), seen)
        except Exception:  # noqa: BLE001
            pass

    if hasattr(value, "dict"):
        try:
            return _to_json_safe(value.dict(), seen)
        except Exception:  # noqa: BLE001
            pass

    if hasattr(value, "__dict__"):
        try:
            return _to_json_safe(vars(value), seen)
        except Exception:  # noqa: BLE001
            pass

    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return "(unserializable)"


def _normalise_streamable_http_url(raw_url: str) -> str:
    """Normalize URL for streamable-http transport (deprecated, use _get_mcp_url)."""
    return _get_mcp_url(raw_url)


def _extract_tool_schema(tool: Any) -> Dict[str, Any]:
    """Best-effort extraction of tool input schema."""
    if hasattr(tool, "args_schema") and tool.args_schema:
        schema_src = tool.args_schema
        if hasattr(schema_src, "model_json_schema"):
            try:
                return schema_src.model_json_schema()  # pydantic v2
            except Exception:  # noqa: BLE001
                pass
        if hasattr(schema_src, "schema"):
            try:
                return schema_src.schema()  # pydantic v1
            except Exception:  # noqa: BLE001
                pass

    if hasattr(tool, "schema"):
        try:
            return tool.schema()  # langchain tools may expose schema()
        except Exception:  # noqa: BLE001
            pass

    return {}


def _example_from_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Create a minimal example payload from a JSON schema."""
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", [])) if isinstance(schema, dict) else set()
    example: Dict[str, Any] = {}

    def _placeholder(value_schema: Dict[str, Any]) -> Any:
        if "default" in value_schema:
            return value_schema["default"]
        if "examples" in value_schema and value_schema["examples"]:
            return value_schema["examples"][0]
        value_type = value_schema.get("type")
        if value_type == "string":
            return ""
        if value_type == "integer":
            return 0
        if value_type == "number":
            return 0.0
        if value_type == "boolean":
            return False
        if value_type == "array":
            return []
        if value_type == "object":
            return {}
        return None

    for key, value_schema in properties.items():
        if key in required:
            example[key] = _placeholder(value_schema if isinstance(value_schema, dict) else {})

    return example


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
    .debug-box {
        background: #fef3c7;
        border: 1px solid #fcd34d;
        border-radius: 8px;
        padding: 0.75rem;
        font-size: 0.8rem;
        margin-top: 0.5rem;
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
        "optional": True,
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
    {
        "id": "git",
        "name": "Git MCP",
        "icon": "📂",
        "url": os.getenv("STREAMLIT_GIT_MCP_URL", "http://git-mcp:8000"),
        "description": "Git repository operations",
    },
    {
        "id": "trivy",
        "name": "Trivy MCP",
        "icon": "🔒",
        "url": os.getenv("STREAMLIT_TRIVY_MCP_URL", "http://trivy-mcp:8000"),
        "description": "Security vulnerability scanning",
    },
    {
        "id": "playwright",
        "name": "Playwright MCP",
        "icon": "🎭",
        "url": os.getenv("STREAMLIT_PLAYWRIGHT_MCP_URL", "http://playwright-mcp:8000"),
        "description": "Browser automation and web scraping",
    },
    {
        "id": "websearch",
        "name": "Web Search MCP",
        "icon": "🔍",
        "url": os.getenv("STREAMLIT_WEBSEARCH_MCP_URL", "http://websearch-mcp:8000"),
        "description": "Web search via DuckDuckGo",
    },
]

# Header with options
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
with col1:
    st.subheader("📊 Server Health Dashboard")
with col2:
    debug_mode = st.checkbox("Debug Mode", value=False, help="Show detailed debug information")
with col3:
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=False)
with col4:
    include_optional = st.checkbox("Include optional", value=False)

visible_servers = [s for s in MCP_SERVERS if include_optional or not s.get("optional")]

if auto_refresh:
    import time
    time.sleep(30)
    st.rerun()

# Check health buttons
col_check, col_http = st.columns(2)

with col_check:
    check_all = st.button("🔄 Check All Servers (MCP)", use_container_width=True, type="primary")

with col_http:
    check_http = st.button("🌐 HTTP Health Check Only", use_container_width=True)

if check_all or check_http:
    st.session_state.mcp_health_results = {}
    st.session_state.mcp_debug_info = {}

    progress = st.progress(0)
    status_text = st.empty()

    for i, server in enumerate(visible_servers):
        status_text.text(f"Checking {server['name']}...")
        progress.progress((i + 1) / len(visible_servers))

        try:
            if check_http:
                # HTTP-only check
                health = asyncio.run(check_mcp_server_http_simple(server["url"], timeout=5.0))
                health["tool_count"] = 0  # HTTP check doesn't get tools
            else:
                # Full MCP check
                health = asyncio.run(check_mcp_server_simple(server["id"], server["url"], timeout=10.0))

            st.session_state.mcp_health_results[server["id"]] = health
            st.session_state.mcp_debug_info[server["id"]] = health.get("debug", {})

        except Exception as e:
            error_tb = traceback.format_exc()
            st.session_state.mcp_health_results[server["id"]] = {
                "status": "unhealthy",
                "message": f"Exception: {str(e)[:80]}",
                "response_time_ms": 0,
                "tool_count": 0,
                "last_checked": datetime.now().isoformat(),
            }
            st.session_state.mcp_debug_info[server["id"]] = {
                "exception": str(e),
                "traceback": error_tb,
            }

    progress.empty()
    status_text.empty()
    st.session_state.mcp_last_check = datetime.now()
    st.success("Health check complete!")
    st.rerun()

# Display results
if "mcp_health_results" not in st.session_state:
    st.info("Click **Check All Servers** to test connectivity to all MCP servers.")
else:
    health_results = st.session_state.mcp_health_results
    debug_info = st.session_state.get("mcp_debug_info", {})
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
    for server in visible_servers:
        health = health_results.get(server["id"], {})
        status = health.get("status", "unknown")
        server_debug = debug_info.get(server["id"], {})

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
                    if health.get("tool_names"):
                        st.caption(f"Sample: {', '.join(health['tool_names'][:5])}")

                if health.get("response_time_ms"):
                    st.markdown(f"**Response Time:** {health['response_time_ms']}ms")

                if health.get("transport"):
                    st.markdown(f"**Transport:** {health['transport']}")

            with col_status:
                st.markdown(
                    f'<span class="status-badge {status_class}">{status_icon} {status.capitalize()}</span>',
                    unsafe_allow_html=True,
                )
                st.caption(health.get("message", "Unknown"))

            # Debug info for this server
            if debug_mode and (server_debug or status == "unhealthy"):
                with st.expander(f"🔍 Debug Info: {server['name']}", expanded=status == "unhealthy"):
                    if server_debug:
                        st.json(_to_json_safe(server_debug))
                    else:
                        st.json(_to_json_safe(health))

    # Warning if any unhealthy
    if healthy < total:
        st.warning(
            f"⚠️ **{total - healthy} server(s) are not healthy.**\n\n"
            "**Troubleshooting:**\n"
            "1. Check services are running: `docker-compose ps`\n"
            "2. Restart services: `./scripts/dev-stop.ps1 -Remove` then `./scripts/dev-start.ps1`\n"
            "3. Check logs: `./scripts/dev-logs.ps1 <service-name>`\n"
            "4. Enable **Debug Mode** above to see detailed error information"
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
    options=[s["id"] for s in visible_servers],
    format_func=lambda x: next((s["name"] for s in visible_servers if s["id"] == x), x),
)

# Transport selection
transport_option = st.selectbox(
    "Transport",
    options=["streamable-http", "sse"],
    help="FastMCP servers typically use streamable-http transport",
)

if st.button("Load Tools", use_container_width=True):
    selected_server = next((s for s in visible_servers if s["id"] == tool_server), None)
    if selected_server:
        with st.spinner(f"Loading tools from {selected_server['name']}..."):
            try:
                from src.mcp_client import get_mcp_client

                # Use unified client for tool loading
                client = get_mcp_client(tool_server, url=selected_server["url"], force_new=True)
                tools = client.list_tools(force_refresh=True)
                tool_list = list(tools or [])
                st.session_state[f"_mcp_tools_{tool_server}"] = tool_list
                st.success(f"Loaded {len(tool_list)} tools!")

            except BaseException as e:
                # Catch BaseException to handle ExceptionGroup/TaskGroup errors
                error_msg = str(e)
                if hasattr(e, "exceptions"):
                    # Extract first sub-exception message
                    for sub_e in getattr(e, "exceptions", []):
                        error_msg = f"{type(sub_e).__name__}: {str(sub_e)[:100]}"
                        break
                st.error(f"Failed to load tools: {error_msg}")
                if debug_mode:
                    st.code(traceback.format_exc(), language="text")

# Display loaded tools
loaded_tools = st.session_state.get(f"_mcp_tools_{tool_server}", [])
if loaded_tools:
    st.markdown(f"**{len(loaded_tools)} tools available:**")

    # Handle both dict and object formats for tools
    def _get_tool_name(t):
        return t.get("name", "") if isinstance(t, dict) else getattr(t, "name", "")

    def _get_tool_desc(t):
        return t.get("description", "") if isinstance(t, dict) else getattr(t, "description", "")

    def _get_tool_schema(t):
        if isinstance(t, dict):
            return t.get("inputSchema", {})
        return _extract_tool_schema(t)

    tool_map = {_get_tool_name(tool): tool for tool in loaded_tools}

    for tool in loaded_tools:
        tool_name = _get_tool_name(tool)
        tool_desc = _get_tool_desc(tool)
        with st.expander(f"🔧 {tool_name}", expanded=False):
            st.markdown(f"**Description:** {tool_desc[:200] if tool_desc else 'No description'}...")

            schema = _get_tool_schema(tool)
            properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
            required = set(schema.get("required", [])) if isinstance(schema, dict) else set()

            if properties:
                st.markdown("**Parameters:**")
                for param, details in properties.items():
                    details = details if isinstance(details, dict) else {}
                    required_flag = "*required*" if param in required else ""
                    param_type = details.get("type", "any")
                    description = details.get("description", "")
                    if description:
                        st.markdown(f"- `{param}` ({param_type}) {required_flag} — {description}")
                    else:
                        st.markdown(f"- `{param}` ({param_type}) {required_flag}")
            else:
                st.info("No parameter schema provided for this tool.")

            if schema:
                st.markdown("**Raw schema:**")
                st.json(schema)
else:
    st.info("Click **Load Tools** to see available tools for the selected server.")

# Tool runner
if loaded_tools:
    st.subheader("▶️ Tool Runner")
    st.caption("Call a tool with custom JSON arguments")

    selected_tool_name = st.selectbox(
        "Tool",
        options=sorted(tool_map.keys()),
    )
    selected_tool = tool_map.get(selected_tool_name)
    selected_schema = _get_tool_schema(selected_tool) if selected_tool else {}
    selected_example = _example_from_schema(selected_schema) if selected_schema else {}

    if selected_schema:
        st.markdown("**Input schema:**")
        st.json(selected_schema)

    default_payload = json.dumps(selected_example or {}, indent=2)
    payload_text = st.text_area(
        "Arguments (JSON)",
        value=default_payload,
        height=180,
        help="Provide a JSON object with tool parameters. Leave empty for {}.",
    )

    call_tool_button = st.button("Run Tool", type="primary", use_container_width=True)
    if call_tool_button and selected_tool_name:
        try:
            from src.mcp_client import get_mcp_client

            payload = json.loads(payload_text.strip() or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Payload must be a JSON object.")

            with st.spinner(f"Running {selected_tool_name}..."):
                # Get the server info to find URL
                selected_server = next((s for s in visible_servers if s["id"] == tool_server), None)
                if not selected_server:
                    raise ValueError(f"Server {tool_server} not found")

                # Use unified client to invoke tool
                client = get_mcp_client(tool_server, url=selected_server["url"])
                result = client.invoke(selected_tool_name, payload)

            st.success("Tool completed.")
            st.json(_to_json_safe(result))
        except Exception as e:
            st.error(f"Tool call failed: {e}")
            if debug_mode:
                st.code(traceback.format_exc(), language="text")

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
st.caption("MCP server URLs and transport configuration")

for server in visible_servers:
    with st.expander(f"{server['icon']} {server['name']}", expanded=False):
        raw_url = server['url']
        base_url = _get_base_url(raw_url)
        mcp_url = _get_mcp_url(raw_url)
        st.code(f"""Original URL: {raw_url}
Base URL: {base_url}
MCP URL: {mcp_url}

Transport: streamable-http (FastMCP HTTP)
Fallback: sse (Server-Sent Events)

Environment Variable:
STREAMLIT_{server['id'].upper()}_MCP_URL""", language="text")

st.caption(
    "💡 **Tip:** FastMCP servers run with `transport=\"http\"` which exposes a streamable-http endpoint. "
    "Enable **Debug Mode** above to see detailed connection attempts and errors."
)
