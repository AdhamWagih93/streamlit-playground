import asyncio
import importlib.util
import os
import sys
from typing import Any, Dict, List

import streamlit as st
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.theme import set_theme
from src.streamlit_config import StreamlitAppConfig


set_theme(page_title="Docker MCP Test", page_icon="🐳")


def _get_docker_tools(force_reload: bool = False):
    cfg = StreamlitAppConfig.from_env()
    transport = (cfg.docker.mcp_transport or "stdio").lower().strip()

    sig = f"{transport}|{cfg.docker.mcp_url}"
    if force_reload or st.session_state.get("_docker_tools_sig") != sig or "_docker_tools" not in st.session_state:
        if transport == "stdio":
            conn = {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "src.ai.mcp_servers.docker.mcp"],
                "env": {**os.environ, **cfg.docker.to_env_overrides()},
            }
        else:
            conn = {"transport": transport, "url": cfg.docker.mcp_url}

        client = MultiServerMCPClient(connections={"docker": conn})
        st.session_state["_docker_tools"] = asyncio.run(client.get_tools())
        st.session_state["_docker_tools_sig"] = sig

    return st.session_state["_docker_tools"]


def _invoke(tools, name: str, args: Dict[str, Any]) -> Any:
    def _matches(tool_name: str, desired: str) -> bool:
        if tool_name == desired:
            return True
        for sep in ("__", ".", ":"):
            if sep in tool_name and tool_name.rsplit(sep, 1)[-1] == desired:
                return True
        if tool_name.endswith("_" + desired):
            return True
        return False

    tool = next((t for t in tools if _matches(str(getattr(t, "name", "")), name)), None)
    if tool is None:
        available = sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})
        raise ValueError(f"Tool {name} not found. Available: {available}")
    if hasattr(tool, "ainvoke"):
        return asyncio.run(tool.ainvoke(args))
    return tool.invoke(args)


st.title("Docker MCP Tools")
st.caption("Uses the Python docker SDK via an MCP server. No docker CLI required.")

cfg_for_hint = StreamlitAppConfig.from_env()
transport_for_hint = (cfg_for_hint.docker.mcp_transport or "stdio").lower().strip()
if transport_for_hint == "stdio":
    if importlib.util.find_spec("docker") is None:
        st.warning(
            "Local stdio mode requires the Python package 'docker'. "
            "Install dependencies with: python -m pip install -r requirements.txt"
        )
    if cfg_for_hint.docker.docker_tls_verify and not cfg_for_hint.docker.docker_cert_path:
        st.warning(
            "DOCKER_TLS_VERIFY is enabled but DOCKER_CERT_PATH is not set. "
            "Either set DOCKER_CERT_PATH to a folder containing ca.pem/cert.pem/key.pem, or unset DOCKER_TLS_VERIFY for local Docker Desktop."
        )

try:
    tools = _get_docker_tools()
except Exception as exc:  # noqa: BLE001
    st.error(f"Failed to load Docker MCP tools: {exc}")
    st.info("For local dev, ensure Docker Desktop/daemon is running. For remote, set DOCKER_HOST/DOCKER_TLS_VERIFY/DOCKER_CERT_PATH or use STREAMLIT_DOCKER_MCP_URL with SSE.")
    st.stop()

col1, col2 = st.columns([1, 2])
with col1:
    if st.button("Refresh tools", use_container_width=True):
        tools = _get_docker_tools(force_reload=True)
        st.success("Reloaded")
with col2:
    st.write(f"Loaded {len(tools)} tools")

with st.expander("Show loaded tool names", expanded=False):
    tool_names = sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})
    if not tool_names:
        st.write("No tool names found.")
    else:
        st.code("\n".join(tool_names), language="text")

st.markdown("---")

st.subheader("Health")
if st.button("Health check", use_container_width=True):
    st.json(_invoke(tools, "health_check", {}))

st.subheader("Containers")
cc1, cc2 = st.columns([1, 3])
with cc1:
    show_all = st.checkbox("Show all", value=True)
    if st.button("List containers", use_container_width=True):
        st.session_state["_docker_containers"] = _invoke(tools, "list_containers", {"all": bool(show_all)})

containers_res = st.session_state.get("_docker_containers")
containers: List[Dict[str, Any]] = []
if isinstance(containers_res, dict) and containers_res.get("ok"):
    containers = containers_res.get("containers") or []

with cc2:
    if containers:
        st.dataframe(containers, use_container_width=True, hide_index=True)

picked = st.selectbox(
    "Container (id)",
    options=[""] + [c.get("id") for c in containers if c.get("id")],
    index=0,
)

if picked:
    a1, a2, a3, a4 = st.columns(4)
    with a1:
        if st.button("Start", use_container_width=True):
            st.json(_invoke(tools, "start_container", {"container_id": picked}))
    with a2:
        if st.button("Stop", use_container_width=True):
            st.json(_invoke(tools, "stop_container", {"container_id": picked, "timeout": 10}))
    with a3:
        if st.button("Restart", use_container_width=True):
            st.json(_invoke(tools, "restart_container", {"container_id": picked, "timeout": 10}))
    with a4:
        if st.button("Remove", use_container_width=True):
            st.json(_invoke(tools, "remove_container", {"container_id": picked, "force": True, "remove_volumes": False}))

    st.markdown("##### Logs")
    tail = st.number_input("Tail", min_value=10, max_value=5000, value=200, step=10)
    if st.button("Get logs", use_container_width=True):
        res = _invoke(tools, "container_logs", {"container_id": picked, "tail": int(tail), "timestamps": True})
        if isinstance(res, dict) and res.get("ok"):
            st.code(res.get("text") or "", language="text")
        else:
            st.json(res)

st.subheader("Images")
if st.button("List images", use_container_width=True):
    st.session_state["_docker_images"] = _invoke(tools, "list_images", {})

images_res = st.session_state.get("_docker_images")
if isinstance(images_res, dict) and images_res.get("ok"):
    st.dataframe(images_res.get("images") or [], use_container_width=True, hide_index=True)

with st.expander("Pull image", expanded=False):
    ref = st.text_input("Image ref", value="hello-world:latest")
    if st.button("Pull", use_container_width=True) and ref.strip():
        st.json(_invoke(tools, "pull_image", {"ref": ref.strip()}))
