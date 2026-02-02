from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.express as px
import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.mcp_health import add_mcp_status_styles
from src.theme import set_theme


set_theme(page_title="Docker Control Center", page_icon="🐳")

admin = load_admin_config()
if not admin.is_mcp_enabled("docker", default=True):
    st.info("Docker MCP is disabled by Admin.")
    st.stop()

add_mcp_status_styles()


st.markdown(
    """
    <style>
    .docker-hero {
        background: linear-gradient(135deg, #2563eb 0%, #0ea5e9 45%, #22d3ee 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(37, 99, 235, 0.3);
    }
    .docker-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .docker-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .stat-card {
        background: white;
        border-radius: 16px;
        padding: 1.2rem 1.5rem;
        box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        border: 1px solid #e2e8f0;
        text-align: center;
        transition: all 0.3s ease;
    }
    .stat-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 8px 28px rgba(0,0,0,0.12);
    }
    .stat-value {
        font-size: 2.2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #2563eb, #0ea5e9);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stat-label {
        color: #64748b;
        font-size: 0.85rem;
        margin-top: 0.4rem;
        font-weight: 600;
    }
    .panel-card {
        background: white;
        border-radius: 12px;
        padding: 1.25rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06);
        margin-bottom: 1rem;
    }
    .status-running { color: #059669; font-weight: 700; }
    .status-stopped { color: #dc2626; font-weight: 700; }
    .status-paused { color: #f59e0b; font-weight: 700; }
    .status-other { color: #6366f1; font-weight: 700; }
    .tag-pill {
        display: inline-block;
        padding: 0.1rem 0.5rem;
        border-radius: 999px;
        background: #e0f2fe;
        color: #0c4a6e;
        font-size: 0.75rem;
        font-weight: 600;
        margin-right: 0.25rem;
        margin-bottom: 0.25rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


st.markdown(
    """
    <div class="docker-hero">
        <h1>🐳 Docker Control Center</h1>
        <p>Comprehensive, MCP-powered Docker management with containers, images, networks, volumes, and builds.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def _get_docker_client(force_new: bool = False):
    return get_mcp_client("docker", force_new=force_new)


def _get_docker_tools(force_reload: bool = False) -> List[Dict[str, Any]]:
    client = _get_docker_client(force_new=force_reload)
    tools = client.list_tools(force_refresh=force_reload)
    st.session_state["_docker_tools"] = tools
    st.session_state["_docker_tools_sig"] = get_server_url("docker")
    return tools


def _invoke(name: str, args: Dict[str, Any]) -> Any:
    client = _get_docker_client()
    return client.invoke(name, args)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _human_bytes(value: Any) -> str:
    try:
        num = float(value)
    except Exception:
        return str(value) if value is not None else "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(num) < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def _render_stat_card(value: str, label: str) -> None:
    st.markdown(
        f"""
        <div class="stat-card">
            <div class="stat-value">{value}</div>
            <div class="stat-label">{label}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _refresh_inventory(show_spinner: bool = True) -> None:
    def _run():
        containers_res = _invoke("list_containers", {"all": True})
        images_res = _invoke("list_images", {})
        networks_res = _invoke("list_networks", {})
        volumes_res = _invoke("list_volumes", {})

        if isinstance(containers_res, dict) and containers_res.get("ok"):
            st.session_state["_docker_containers"] = containers_res.get("containers") or []
            st.session_state["_docker_containers_backend"] = containers_res.get("backend")
        else:
            st.session_state["_docker_containers"] = []

        if isinstance(images_res, dict) and images_res.get("ok"):
            st.session_state["_docker_images"] = images_res.get("images") or []
            st.session_state["_docker_images_backend"] = images_res.get("backend")
        else:
            st.session_state["_docker_images"] = []

        if isinstance(networks_res, dict) and networks_res.get("ok"):
            st.session_state["_docker_networks"] = networks_res.get("networks") or []
        else:
            st.session_state["_docker_networks"] = []

        if isinstance(volumes_res, dict) and volumes_res.get("ok"):
            st.session_state["_docker_volumes"] = volumes_res.get("volumes") or []
        else:
            st.session_state["_docker_volumes"] = []

    if show_spinner:
        with st.spinner("Refreshing Docker inventory..."):
            _run()
    else:
        _run()


docker_url = get_server_url("docker")
if st.session_state.get("_docker_tools_sig") != docker_url:
    st.session_state.pop("_docker_tools", None)
    st.session_state["_docker_tools_sig"] = docker_url


with st.sidebar:
    st.markdown("### 🎛️ Docker Controls")

    if "docker_auto_load_tools" not in st.session_state:
        st.session_state.docker_auto_load_tools = True

    if "docker_auto_refresh" not in st.session_state:
        st.session_state.docker_auto_refresh = False

    st.session_state.docker_auto_load_tools = st.toggle(
        "Auto-load tools on open",
        value=bool(st.session_state.docker_auto_load_tools),
        help="Fetch tool metadata automatically when the page loads.",
    )

    st.session_state.docker_auto_refresh = st.toggle(
        "Auto-refresh inventory",
        value=bool(st.session_state.docker_auto_refresh),
        help="Fetch containers/images/networks/volumes when tools load.",
    )

    load_clicked = st.button("🔄 Load/refresh tools", use_container_width=True)
    refresh_clicked = st.button("📦 Refresh inventory", use_container_width=True)

    st.divider()
    st.caption("Docker MCP URL")
    st.code(docker_url)


should_load = bool(load_clicked) or (
    bool(st.session_state.get("docker_auto_load_tools")) and "_docker_tools" not in st.session_state
)

if should_load:
    try:
        _get_docker_tools(force_reload=bool(load_clicked))
        st.success("✓ Tools loaded")
        if st.session_state.get("docker_auto_refresh"):
            _refresh_inventory(show_spinner=True)
    except Exception as exc:
        st.error(f"Failed to load Docker MCP tools: {exc}")

tools = st.session_state.get("_docker_tools")
if not tools:
    st.info("🔧 Docker tools are not loaded yet. Click **Load/refresh tools** in the sidebar to begin.")
    st.stop()

if refresh_clicked:
    _refresh_inventory(show_spinner=True)


containers = st.session_state.get("_docker_containers", [])
images = st.session_state.get("_docker_images", [])
networks = st.session_state.get("_docker_networks", [])
volumes = st.session_state.get("_docker_volumes", [])


st.markdown("## 📊 Docker Overview")

health_col, stats_col = st.columns([1.2, 3])

with health_col:
    st.markdown('<div class="panel-card">', unsafe_allow_html=True)
    st.markdown("### 🩺 Health Check")
    if st.button("Run Health Check", use_container_width=True):
        with st.spinner("Checking Docker..."):
            health = _invoke("health_check", {})
            st.session_state["_docker_health"] = health
    health = st.session_state.get("_docker_health")
    if isinstance(health, dict):
        if health.get("ok"):
            st.success("Docker is reachable")
        else:
            st.error("Docker is unreachable")
        st.json(health)
    else:
        st.caption("Run a quick health check to confirm connectivity.")
    st.markdown("</div>", unsafe_allow_html=True)

with stats_col:
    st.markdown("### Snapshot")
    stat_cols = st.columns(5)
    running = sum(1 for c in containers if "running" in str(c.get("status", "")).lower())
    stopped = sum(1 for c in containers if "exited" in str(c.get("status", "")).lower())
    with stat_cols[0]:
        _render_stat_card(str(len(containers)), "Containers")
    with stat_cols[1]:
        _render_stat_card(str(running), "Running")
    with stat_cols[2]:
        _render_stat_card(str(stopped), "Stopped")
    with stat_cols[3]:
        _render_stat_card(str(len(images)), "Images")
    with stat_cols[4]:
        _render_stat_card(str(len(networks) + len(volumes)), "Networks + Volumes")

    if containers:
        df = pd.DataFrame(containers)
        df["status_bucket"] = df["status"].fillna("").apply(
            lambda s: "running" if "running" in s.lower() else "stopped" if "exited" in s.lower() else "other"
        )
        status_counts = df.groupby("status_bucket", as_index=False).size()
        fig = px.pie(status_counts, names="status_bucket", values="size", title="Container Status Mix")
        fig.update_layout(height=260, margin=dict(t=40, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)


st.divider()


tab_containers, tab_images, tab_assets, tab_builds, tab_tools = st.tabs(
    ["🐳 Containers", "🖼️ Images", "🌐 Networks & Volumes", "🏗️ Build & Registry", "🧰 Tools"]
)


with tab_containers:
    st.markdown("### Container Inventory")
    col_filter, col_refresh = st.columns([3, 1])
    with col_filter:
        filter_text = st.text_input("Filter containers (name or id)")
        show_all = st.checkbox("Include stopped containers", value=True)
    with col_refresh:
        if st.button("Refresh Containers", use_container_width=True):
            with st.spinner("Loading containers..."):
                res = _invoke("list_containers", {"all": bool(show_all)})
                if isinstance(res, dict) and res.get("ok"):
                    st.session_state["_docker_containers"] = res.get("containers") or []
                    st.success("Updated!")
                else:
                    st.error(f"Failed: {res}")

    filtered = containers
    if filter_text.strip():
        filtered = [
            c for c in filtered
            if filter_text.lower() in (c.get("name") or "").lower()
            or filter_text.lower() in (c.get("id") or "").lower()
        ]

    if not filtered:
        st.info("No containers found. Refresh to load your inventory.")
    else:
        for container in filtered:
            container_id = (container.get("id") or "")[:12]
            name = container.get("name") or "unknown"
            status = container.get("status") or "unknown"
            image = container.get("image") or []
            status_class = "status-other"
            if "running" in status.lower():
                status_class = "status-running"
            elif "exited" in status.lower():
                status_class = "status-stopped"
            elif "paused" in status.lower():
                status_class = "status-paused"

            with st.expander(f"{name} ({container_id})", expanded=False):
                st.markdown(
                    f"**Status:** <span class='{status_class}'>{status}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**Image:** {', '.join(image) if isinstance(image, list) else image}")
                st.markdown(f"**ID:** {container_id}")

                action_cols = st.columns(4)
                with action_cols[0]:
                    if st.button("▶️ Start", key=f"start_{container_id}"):
                        res = _invoke("start_container", {"container_id": container_id})
                        st.success("Started" if isinstance(res, dict) and res.get("ok") else f"Failed: {res}")
                with action_cols[1]:
                    if st.button("⏹ Stop", key=f"stop_{container_id}"):
                        res = _invoke("stop_container", {"container_id": container_id, "timeout": 10})
                        st.success("Stopped" if isinstance(res, dict) and res.get("ok") else f"Failed: {res}")
                with action_cols[2]:
                    if st.button("🔄 Restart", key=f"restart_{container_id}"):
                        res = _invoke("restart_container", {"container_id": container_id, "timeout": 10})
                        st.success("Restarted" if isinstance(res, dict) and res.get("ok") else f"Failed: {res}")
                with action_cols[3]:
                    if st.button("🗑 Remove", key=f"remove_{container_id}"):
                        res = _invoke("remove_container", {"container_id": container_id, "force": True, "remove_volumes": False})
                        st.success("Removed" if isinstance(res, dict) and res.get("ok") else f"Failed: {res}")

                st.markdown("**Logs**")
                tail = st.slider("Tail lines", 10, 1000, 200, key=f"tail_{container_id}")
                if st.button("Fetch Logs", key=f"logs_{container_id}"):
                    res = _invoke("container_logs", {"container_id": container_id, "tail": int(tail), "timestamps": True})
                    if isinstance(res, dict) and res.get("ok"):
                        st.code(res.get("text") or "(no logs)")
                    else:
                        st.error(f"Failed: {res}")


with tab_images:
    st.markdown("### Image Registry")
    img_col, img_refresh = st.columns([3, 1])
    with img_col:
        img_filter = st.text_input("Filter images (tag or id)")
    with img_refresh:
        if st.button("Refresh Images", use_container_width=True):
            res = _invoke("list_images", {})
            if isinstance(res, dict) and res.get("ok"):
                st.session_state["_docker_images"] = res.get("images") or []
                st.success("Updated!")
            else:
                st.error(f"Failed: {res}")

    filtered_images = images
    if img_filter.strip():
        filtered_images = [
            img for img in filtered_images
            if img_filter.lower() in str(img.get("tags", [])).lower()
            or img_filter.lower() in (img.get("id") or "").lower()
        ]

    if filtered_images:
        df = pd.DataFrame(filtered_images)
        df["size_human"] = df.get("size").apply(_human_bytes) if "size" in df else ""
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No images found. Refresh to load images.")

    st.divider()
    st.markdown("### Image Actions")

    pull_ref = st.text_input("Pull image", value="hello-world:latest")
    if st.button("⬇️ Pull Image", type="primary"):
        res = _invoke("pull_image", {"ref": pull_ref.strip()})
        if isinstance(res, dict) and res.get("ok"):
            st.success("Pulled successfully")
            _refresh_inventory(show_spinner=False)
        else:
            st.error(f"Failed: {res}")

    col_remove, col_tag = st.columns(2)
    with col_remove:
        image_to_remove = st.text_input("Remove image (id or tag)")
        if st.button("🗑 Remove Image"):
            res = _invoke("remove_image", {"image_id": image_to_remove.strip(), "force": True})
            st.success("Removed" if isinstance(res, dict) and res.get("ok") else f"Failed: {res}")

    with col_tag:
        source = st.text_input("Source image (id or tag)")
        target = st.text_input("New tag (repo:tag)")
        if st.button("🏷 Tag Image"):
            res = _invoke("tag_image", {"source": source.strip(), "target": target.strip()})
            st.success("Tagged" if isinstance(res, dict) and res.get("ok") else f"Failed: {res}")


with tab_assets:
    st.markdown("### Networks")
    if networks:
        st.dataframe(pd.DataFrame(networks), use_container_width=True, hide_index=True)
    else:
        st.info("No networks found. Refresh inventory to load networks.")

    st.markdown("### Volumes")
    if volumes:
        st.dataframe(pd.DataFrame(volumes), use_container_width=True, hide_index=True)
    else:
        st.info("No volumes found. Refresh inventory to load volumes.")


with tab_builds:
    st.markdown("### Registry Login")
    reg_col1, reg_col2 = st.columns(2)
    with reg_col1:
        registry = st.text_input("Registry", value="")
        username = st.text_input("Username", value="")
    with reg_col2:
        password = st.text_input("Password", value="", type="password")
        if st.button("🔐 Login", use_container_width=True):
            res = _invoke("docker_login", {"registry": registry, "username": username, "password": password})
            st.success("Logged in" if isinstance(res, dict) and res.get("ok") else f"Failed: {res}")

    st.divider()
    st.markdown("### Build Image")
    build_col1, build_col2 = st.columns(2)
    with build_col1:
        context_path = st.text_input("Context path", value=".")
        dockerfile = st.text_input("Dockerfile", value="Dockerfile")
        tag = st.text_input("Tag", value="my-image:latest")
    with build_col2:
        build_args_raw = st.text_area("Build args (JSON)", value="{}", height=100)
        target = st.text_input("Target stage (optional)", value="")
        nocache = st.checkbox("No cache", value=False)
        if st.button("🏗️ Build", type="primary"):
            try:
                build_args = json.loads(build_args_raw or "{}")
                if not isinstance(build_args, dict):
                    raise ValueError("Build args must be a JSON object")
            except Exception as exc:
                st.error(f"Invalid build args: {exc}")
            else:
                res = _invoke(
                    "build_image",
                    {
                        "context_path": context_path,
                        "tag": tag,
                        "dockerfile": dockerfile,
                        "build_args": build_args,
                        "target": target or None,
                        "nocache": bool(nocache),
                    },
                )
                if isinstance(res, dict) and res.get("ok"):
                    st.success("Build complete")
                    if res.get("log_tail"):
                        st.code("\n".join([str(line) for line in res.get("log_tail")]))
                    _refresh_inventory(show_spinner=False)
                else:
                    st.error(f"Failed: {res}")

    st.divider()
    st.markdown("### Push Image")
    push_ref = st.text_input("Image reference to push", value="")
    if st.button("🚀 Push", use_container_width=True):
        res = _invoke("push_image", {"ref": push_ref.strip()})
        if isinstance(res, dict) and res.get("ok"):
            st.success("Push completed")
            if res.get("output_tail"):
                st.json(res.get("output_tail"))
        else:
            st.error(f"Failed: {res}")


with tab_tools:
    st.markdown("### Tool Inventory")
    tool_names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
    if tool_names:
        st.write(", ".join(sorted(tool_names)))
    else:
        st.info("No tools found.")

    st.divider()
    st.markdown("### Advanced Tool Runner")
    selected_tool = st.selectbox("Tool", options=tool_names)
    args_raw = st.text_area("Arguments (JSON)", value="{}", height=120)
    if st.button("Run Tool"):
        try:
            payload = json.loads(args_raw or "{}")
            if not isinstance(payload, dict):
                raise ValueError("Arguments must be a JSON object")
        except Exception as exc:
            st.error(f"Invalid JSON: {exc}")
        else:
            result = _invoke(selected_tool, payload)
            st.json(result)


st.divider()
st.caption("💡 Tip: Use the sidebar to auto-load tools and refresh the inventory on page open.")
import importlib.util
from typing import Any, Dict, List

import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.mcp_health import add_mcp_status_styles
from src.streamlit_config import get_app_config
from src.theme import set_theme


set_theme(page_title="Docker MCP Test", page_icon="🐳")

admin = load_admin_config()
if not admin.is_mcp_enabled("docker", default=True):
    st.info("Docker MCP is disabled by Admin.")
    st.stop()

# Add status badge styles
add_mcp_status_styles()

# Modern styling
st.markdown(
    """
    <style>
    .docker-hero {
        background: linear-gradient(135deg, #2563eb 0%, #0891b2 100%);
        border-radius: 20px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 10px 40px rgba(37, 99, 235, 0.3);
    }
    .docker-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        letter-spacing: 0.5px;
    }
    .docker-hero p {
        margin: 0;
        font-size: 1.05rem;
        opacity: 0.95;
    }
    .docker-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 16px;
        padding: 1.5rem;
        border: 1px solid #e2e8f0;
        box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
        margin-bottom: 1rem;
    }
    .docker-card h3 {
        font-size: 1.2rem;
        font-weight: 700;
        margin: 0 0 1rem 0;
        color: #1e293b;
    }
    .container-status-running {
        color: #059669;
        font-weight: 600;
    }
    .container-status-exited {
        color: #dc2626;
        font-weight: 600;
    }
    .container-status-paused {
        color: #f59e0b;
        font-weight: 600;
    }
    .container-status-created {
        color: #6366f1;
        font-weight: 600;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
        gap: 1rem;
        margin: 1rem 0;
    }
    .metric-box {
        background: #f1f5f9;
        border-radius: 12px;
        padding: 1rem;
        text-align: center;
        border: 1px solid #e2e8f0;
    }
    .metric-box-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #0f172a;
    }
    .metric-box-label {
        font-size: 0.85rem;
        color: #64748b;
        margin-top: 0.25rem;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


st.markdown(
    """
    <div class="docker-hero">
        <h1>🐳 Docker Container Management</h1>
        <p>Monitor and manage Docker containers via MCP server • No Docker CLI required</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def _get_docker_client(force_new: bool = False):
    """Get the Docker MCP client."""
    return get_mcp_client("docker", force_new=force_new)


def _get_docker_tools(force_reload: bool = False) -> List[Dict[str, Any]]:
    """Get Docker MCP tools using the unified client."""
    client = _get_docker_client(force_new=force_reload)
    tools = client.list_tools(force_refresh=force_reload)
    st.session_state["_docker_tools"] = tools
    st.session_state["_docker_tools_sig"] = get_server_url("docker")
    return tools


def _invoke(tools, name: str, args: Dict[str, Any]) -> Any:
    """Invoke a Docker MCP tool."""
    client = _get_docker_client()
    return client.invoke(name, args)


# Connection status info
st.subheader("🔍 Connection Status")

docker_url = get_server_url("docker")

# Invalidate cached tools if the target URL changes
if st.session_state.get("_docker_tools_sig") != docker_url:
    st.session_state.pop("_docker_tools", None)
    st.session_state["_docker_tools_sig"] = docker_url

st.markdown(
    f"""
    <div class="docker-card" style="padding: 1rem;">
        <div style="color: #64748b;">Transport: <strong>streamable-http</strong> &nbsp;|&nbsp; URL: <code>{docker_url}</code></div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.divider()

# Sidebar controls
with st.sidebar:
    st.markdown("### 🎛️ Controls")

    if "docker_auto_load_tools" not in st.session_state:
        st.session_state.docker_auto_load_tools = False

    st.session_state.docker_auto_load_tools = st.toggle(
        "Auto-load tools on open",
        value=bool(st.session_state.docker_auto_load_tools),
        help="When enabled, the page will discover tools automatically on open.",
    )

    load_clicked = st.button("🔄 Load/refresh tools", use_container_width=True)

    st.divider()

    st.markdown("### 📊 Quick Stats")
    containers_count = len(st.session_state.get("_docker_containers_list", []))
    images_count = len(st.session_state.get("_docker_images_list", []))

    st.metric("Containers", containers_count)
    st.metric("Images", images_count)

# Tool loading
should_load = bool(load_clicked) or (
    bool(st.session_state.get("docker_auto_load_tools")) and "_docker_tools" not in st.session_state
)

if should_load:
    try:
        with st.spinner("Loading Docker MCP tools..."):
            _get_docker_tools(force_reload=bool(load_clicked))
            st.success("✓ Tools loaded successfully")
    except Exception as exc:
        st.error(f"Failed to load Docker MCP tools: {exc}")
        st.info(
            "**Troubleshooting:**\n"
            "- Ensure Docker Desktop/daemon is running\n"
            "- For remote connections, verify DOCKER_HOST, DOCKER_TLS_VERIFY, and DOCKER_CERT_PATH\n"
            "- Check `docker info` in your terminal"
        )

tools = st.session_state.get("_docker_tools")
if not tools:
    st.info("🔧 Docker tools are not loaded yet. Click **Load/refresh tools** in the sidebar to begin.")
    st.stop()

# ==============================================================================
# QUICK ACTIONS PANEL
# ==============================================================================
st.markdown("### Quick Actions")

qa_cols = st.columns(5)

with qa_cols[0]:
    if st.button("List All Containers", use_container_width=True, type="primary"):
        with st.spinner("Loading..."):
            result = _invoke(tools, "list_containers", {"all": True})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_docker_containers_list"] = result.get("containers") or []
                st.success(f"Found {len(result.get('containers', []))} containers")
            else:
                st.error(f"Failed: {result}")

with qa_cols[1]:
    if st.button("List Images", use_container_width=True):
        with st.spinner("Loading..."):
            result = _invoke(tools, "list_images", {"all": False})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_docker_images_list"] = result.get("images") or []
                st.success(f"Found {len(result.get('images', []))} images")
            else:
                st.error(f"Failed: {result}")

with qa_cols[2]:
    if st.button("Stop All Running", use_container_width=True):
        containers = st.session_state.get("_docker_containers_list", [])
        running = [c for c in containers if c.get("status", "").lower().startswith("running")]
        if running:
            with st.spinner(f"Stopping {len(running)} containers..."):
                stopped = 0
                for c in running:
                    res = _invoke(tools, "stop_container", {"container_id": c.get("id")[:12], "timeout": 10})
                    if isinstance(res, dict) and res.get("ok"):
                        stopped += 1
                st.success(f"Stopped {stopped}/{len(running)} containers")
                st.rerun()
        else:
            st.info("No running containers to stop")

with qa_cols[3]:
    if st.button("System Prune", use_container_width=True):
        st.session_state["_docker_show_prune_confirm"] = True

with qa_cols[4]:
    if st.button("Refresh All", use_container_width=True):
        with st.spinner("Refreshing..."):
            # Refresh containers
            result = _invoke(tools, "list_containers", {"all": True})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_docker_containers_list"] = result.get("containers") or []
            # Refresh images
            result = _invoke(tools, "list_images", {"all": False})
            if isinstance(result, dict) and result.get("ok"):
                st.session_state["_docker_images_list"] = result.get("images") or []
            st.success("Refreshed!")
            st.rerun()

# System Prune Confirmation
if st.session_state.get("_docker_show_prune_confirm"):
    st.warning("**System Prune** will remove stopped containers, unused networks, dangling images, and build cache.")
    col_confirm, col_cancel = st.columns(2)
    with col_confirm:
        if st.button("Confirm Prune", type="primary", use_container_width=True):
            with st.spinner("Pruning..."):
                result = _invoke(tools, "docker_prune", {"prune_volumes": False})
                if isinstance(result, dict) and result.get("ok"):
                    st.success(f"Pruned! Reclaimed: {result.get('space_reclaimed', 'unknown')} bytes")
                else:
                    st.error(f"Failed: {result}")
            st.session_state["_docker_show_prune_confirm"] = False
            st.rerun()
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.session_state["_docker_show_prune_confirm"] = False
            st.rerun()

st.divider()

# Main content tabs
tabs = st.tabs(["📦 Containers", "💿 Images", "🔧 Tools & Debug"])

# --- CONTAINERS TAB ---
with tabs[0]:
    st.markdown('<div class="docker-card">', unsafe_allow_html=True)
    st.markdown("### Container Management")

    col_filter, col_action = st.columns([2, 1])

    with col_filter:
        show_all = st.checkbox("Show all containers (including stopped)", value=True)
        search_filter = st.text_input("🔍 Filter by name or ID", placeholder="Search containers...")

    with col_action:
        if st.button("🔄 Refresh Containers", use_container_width=True):
            with st.spinner("Loading containers..."):
                result = _invoke(tools, "list_containers", {"all": bool(show_all)})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_docker_containers_list"] = result.get("containers") or []
                    st.success("Refreshed!")
                else:
                    st.error(f"Failed to list containers: {result}")

    containers_list: List[Dict[str, Any]] = st.session_state.get("_docker_containers_list", [])

    if containers_list:
        # Apply search filter
        if search_filter.strip():
            containers_list = [
                c for c in containers_list
                if search_filter.lower() in (c.get("name") or "").lower()
                or search_filter.lower() in (c.get("id") or "").lower()
            ]

        # Summary metrics
        running = sum(1 for c in containers_list if c.get("status", "").lower().startswith("running"))
        stopped = sum(1 for c in containers_list if c.get("status", "").lower().startswith("exited"))
        other = len(containers_list) - running - stopped

        st.markdown(
            f"""
            <div class="metric-grid">
                <div class="metric-box">
                    <div class="metric-box-value" style="color: #059669;">{running}</div>
                    <div class="metric-box-label">Running</div>
                </div>
                <div class="metric-box">
                    <div class="metric-box-value" style="color: #dc2626;">{stopped}</div>
                    <div class="metric-box-label">Stopped</div>
                </div>
                <div class="metric-box">
                    <div class="metric-box-value" style="color: #6366f1;">{other}</div>
                    <div class="metric-box-label">Other</div>
                </div>
                <div class="metric-box">
                    <div class="metric-box-value">{len(containers_list)}</div>
                    <div class="metric-box-label">Total</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Container list
        for container in containers_list:
            container_id = container.get("id", "")[:12]
            container_name = container.get("name", "Unknown")
            status = container.get("status", "unknown")
            image = container.get("image", "")

            # Determine status class
            status_class = "container-status-created"
            if "running" in status.lower():
                status_class = "container-status-running"
            elif "exited" in status.lower():
                status_class = "container-status-exited"
            elif "paused" in status.lower():
                status_class = "container-status-paused"

            with st.expander(f"🐳 {container_name} ({container_id})", expanded=False):
                col_info, col_actions = st.columns([2, 1])

                with col_info:
                    st.markdown(f"**Status:** <span class='{status_class}'>{status}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Image:** `{image}`")
                    st.markdown(f"**ID:** `{container_id}`")

                with col_actions:
                    action_col1, action_col2 = st.columns(2)

                    with action_col1:
                        if st.button("▶️ Start", key=f"start_{container_id}", use_container_width=True):
                            with st.spinner("Starting..."):
                                res = _invoke(tools, "start_container", {"container_id": container_id})
                                if isinstance(res, dict) and res.get("ok"):
                                    st.success("Started!")
                                    st.rerun()
                                else:
                                    st.error(f"Failed: {res}")

                        if st.button("⏸️ Stop", key=f"stop_{container_id}", use_container_width=True):
                            with st.spinner("Stopping..."):
                                res = _invoke(tools, "stop_container", {"container_id": container_id, "timeout": 10})
                                if isinstance(res, dict) and res.get("ok"):
                                    st.success("Stopped!")
                                    st.rerun()
                                else:
                                    st.error(f"Failed: {res}")

                    with action_col2:
                        if st.button("🔄 Restart", key=f"restart_{container_id}", use_container_width=True):
                            with st.spinner("Restarting..."):
                                res = _invoke(tools, "restart_container", {"container_id": container_id, "timeout": 10})
                                if isinstance(res, dict) and res.get("ok"):
                                    st.success("Restarted!")
                                    st.rerun()
                                else:
                                    st.error(f"Failed: {res}")

                        # Destructive action with confirmation
                        if st.button("🗑️ Remove", key=f"remove_{container_id}", use_container_width=True, type="secondary"):
                            if f"confirm_remove_{container_id}" not in st.session_state:
                                st.session_state[f"confirm_remove_{container_id}"] = True
                                st.warning("⚠️ Click again to confirm removal")
                            else:
                                with st.spinner("Removing..."):
                                    res = _invoke(tools, "remove_container", {
                                        "container_id": container_id,
                                        "force": True,
                                        "remove_volumes": False
                                    })
                                    if isinstance(res, dict) and res.get("ok"):
                                        st.success("Removed!")
                                        del st.session_state[f"confirm_remove_{container_id}"]
                                        st.rerun()
                                    else:
                                        st.error(f"Failed: {res}")

                # Logs section
                st.markdown("---")
                st.markdown("**📋 Container Logs**")

                log_tail = st.slider("Number of lines", 10, 1000, 200, key=f"tail_{container_id}")

                if st.button("View Logs", key=f"logs_{container_id}"):
                    with st.spinner("Fetching logs..."):
                        res = _invoke(tools, "container_logs", {
                            "container_id": container_id,
                            "tail": int(log_tail),
                            "timestamps": True
                        })
                        if isinstance(res, dict) and res.get("ok"):
                            logs_text = res.get("text") or ""
                            if logs_text:
                                st.code(logs_text, language="text", line_numbers=True)
                            else:
                                st.info("No logs available")
                        else:
                            st.error(f"Failed to fetch logs: {res}")
    else:
        st.info("No containers found. Click **Refresh Containers** to load the container list.")

    st.markdown('</div>', unsafe_allow_html=True)

# --- IMAGES TAB ---
with tabs[1]:
    st.markdown('<div class="docker-card">', unsafe_allow_html=True)
    st.markdown("### Image Management")

    col_search, col_refresh = st.columns([3, 1])

    with col_search:
        image_search = st.text_input("🔍 Filter images", placeholder="Search by name or tag...")

    with col_refresh:
        if st.button("🔄 Refresh Images", use_container_width=True):
            with st.spinner("Loading images..."):
                result = _invoke(tools, "list_images", {})
                if isinstance(result, dict) and result.get("ok"):
                    st.session_state["_docker_images_list"] = result.get("images") or []
                    st.success("Refreshed!")
                else:
                    st.error(f"Failed to list images: {result}")

    images_list: List[Dict[str, Any]] = st.session_state.get("_docker_images_list", [])

    if images_list:
        # Apply search filter
        if image_search.strip():
            images_list = [
                img for img in images_list
                if image_search.lower() in str(img.get("tags", [])).lower()
                or image_search.lower() in (img.get("id") or "").lower()
            ]

        st.markdown(f"**Total Images:** {len(images_list)}")

        # Display images in a table
        if images_list:
            st.dataframe(
                images_list,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "id": st.column_config.TextColumn("ID", width="small"),
                    "tags": st.column_config.ListColumn("Tags"),
                    "size": st.column_config.NumberColumn("Size", format="%d bytes"),
                }
            )
    else:
        st.info("No images found. Click **Refresh Images** to load the image list.")

    st.divider()

    # Pull image section
    with st.expander("⬇️ Pull New Image", expanded=False):
        st.markdown("Pull a Docker image from a registry")

        image_ref = st.text_input(
            "Image reference",
            value="hello-world:latest",
            placeholder="e.g., nginx:latest, python:3.11-slim",
            help="Specify image name and optional tag"
        )

        if st.button("Pull Image", use_container_width=True, type="primary"):
            if image_ref.strip():
                with st.spinner(f"Pulling {image_ref}..."):
                    res = _invoke(tools, "pull_image", {"ref": image_ref.strip()})
                    if isinstance(res, dict) and res.get("ok"):
                        st.success(f"✓ Successfully pulled {image_ref}")
                        # Refresh images list
                        result = _invoke(tools, "list_images", {})
                        if isinstance(result, dict) and result.get("ok"):
                            st.session_state["_docker_images_list"] = result.get("images") or []
                        st.rerun()
                    else:
                        st.error(f"Failed to pull image: {res}")
            else:
                st.warning("Please enter an image reference")

    st.markdown('</div>', unsafe_allow_html=True)

# --- TOOLS & DEBUG TAB ---
with tabs[2]:
    st.markdown('<div class="docker-card">', unsafe_allow_html=True)
    st.markdown("### Available MCP Tools")

    col_info, col_refresh = st.columns([3, 1])

    with col_info:
        st.markdown(f"**Loaded Tools:** {len(tools)}")

    with col_refresh:
        if st.button("🔄 Reload Tools", use_container_width=True):
            tools = _get_docker_tools(force_reload=True)
            st.success("Tools reloaded!")
            st.rerun()

    # List all available tools
    with st.expander("📋 Show All Tool Names", expanded=False):
        if tools:
            for idx, tool in enumerate(tools, 1):
                tool_name = tool.get("name", "unknown") if isinstance(tool, dict) else str(tool)
                st.markdown(f"{idx}. `{tool_name}`")
        else:
            st.info("No tools available")

    st.divider()

    # Health check
    st.markdown("### 🏥 Docker Health Check")

    if st.button("Run Health Check", use_container_width=True):
        with st.spinner("Checking Docker daemon health..."):
            health_result = _invoke(tools, "health_check", {})
            if isinstance(health_result, dict):
                if health_result.get("ok"):
                    st.success("✓ Docker daemon is healthy")
                else:
                    st.error("✗ Docker daemon reported issues")
                st.json(health_result)
            else:
                st.error("Unexpected health check response format")
                st.code(str(health_result))

    st.markdown('</div>', unsafe_allow_html=True)

# Footer
st.divider()
st.caption(
    "💡 **Tip:** Use the sidebar to enable auto-load for automatic tool discovery. "
    "Destructive operations (like container removal) require confirmation."
)
