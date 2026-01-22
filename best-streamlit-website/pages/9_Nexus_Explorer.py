from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from langchain_mcp_adapters.client import MultiServerMCPClient

from src.streamlit_config import StreamlitAppConfig
from src.theme import set_theme


set_theme(page_title="Nexus Explorer", page_icon="🗄️")


def _safe_json_loads(raw: str) -> Optional[Any]:
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _normalize_mcp_result(result: Any) -> Any:
    if isinstance(result, dict):
        return result

    # LangChain MCP adapters sometimes return objects with a `.content` list.
    content = getattr(result, "content", None)
    if isinstance(content, list) and content:
        # Prefer first text block.
        text = None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                break
        if isinstance(text, str):
            parsed = _safe_json_loads(text)
            return parsed if parsed is not None else {"ok": False, "text": text}

    # Fallback: stringify
    try:
        return {"ok": False, "text": str(result)}
    except Exception:
        return {"ok": False, "text": "(unprintable result)"}


def _get_nexus_mcp_mtime() -> float:
    try:
        p = Path(__file__).resolve().parent.parent / "src" / "ai" / "mcp_servers" / "nexus" / "mcp.py"
        return p.stat().st_mtime
    except Exception:
        return 0.0


def _build_stdio_env(cfg: StreamlitAppConfig, overrides: Dict[str, str]) -> Dict[str, str]:
    # Start with the server's env defaults.
    env = {**os.environ, **cfg.nexus.to_env_overrides()}

    # Apply per-page overrides.
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        env[str(k)] = str(v)

    return env


def _get_nexus_tools(*, force_reload: bool = False, overrides: Optional[Dict[str, str]] = None):
    cfg = StreamlitAppConfig.from_env()
    transport = (cfg.nexus.mcp_transport or "stdio").lower().strip()

    # Include server code mtime so stdio subprocess reloads on code edits.
    mtime = _get_nexus_mcp_mtime()

    # Signature includes connection mode + target + relevant override knobs.
    sig_parts = [
        transport,
        cfg.nexus.mcp_url,
        str(mtime),
        overrides.get("NEXUS_BASE_URL", "") if overrides else "",
        overrides.get("NEXUS_USERNAME", "") if overrides else "",
        "has_password" if (overrides and overrides.get("NEXUS_PASSWORD")) else "no_password",
        "has_token" if (overrides and overrides.get("NEXUS_TOKEN")) else "no_token",
        overrides.get("NEXUS_VERIFY_SSL", "") if overrides else "",
        overrides.get("NEXUS_ALLOW_RAW", "") if overrides else "",
        "has_mcp_token" if (overrides and overrides.get("NEXUS_MCP_CLIENT_TOKEN")) else "no_mcp_token",
    ]
    sig = "|".join(sig_parts)

    if force_reload or st.session_state.get("_nexus_tools_sig") != sig or "_nexus_tools" not in st.session_state:
        if transport == "stdio":
            conn = {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "src.ai.mcp_servers.nexus.mcp"],
                "env": _build_stdio_env(cfg, overrides or {}),
            }
        else:
            conn = {"transport": transport, "url": cfg.nexus.mcp_url}

        client = MultiServerMCPClient(connections={"nexus": conn})
        st.session_state["_nexus_tools"] = asyncio.run(client.get_tools())
        st.session_state["_nexus_tools_sig"] = sig

    return st.session_state["_nexus_tools"]


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


st.title("Nexus Explorer")
st.caption("Explore Sonatype Nexus Repository Manager via an MCP server (local-first).")

cfg = StreamlitAppConfig.from_env()

with st.expander("Connection (current config)", expanded=False):
    st.json(cfg.nexus.to_dict())

transport = (cfg.nexus.mcp_transport or "stdio").lower().strip()
if transport == "stdio":
    # Simple local dependency hint.
    if importlib.util.find_spec("requests") is None:
        st.warning(
            "Local stdio mode requires the Python package 'requests'. "
            "Install dependencies with: python -m pip install -r requirements.txt"
        )

st.sidebar.header("Nexus connection")
st.sidebar.caption("Overrides apply only in local stdio mode.")

base_url = st.sidebar.text_input("Base URL", value=cfg.nexus.base_url)
username = st.sidebar.text_input("Username", value=cfg.nexus.username or "")
password = st.sidebar.text_input("Password", value=cfg.nexus.password or "", type="password")
token = st.sidebar.text_input("Bearer token", value=cfg.nexus.token or "", type="password")
verify_ssl = st.sidebar.checkbox("Verify SSL", value=bool(cfg.nexus.verify_ssl))
allow_raw = st.sidebar.checkbox("Enable raw request tool", value=bool(cfg.nexus.allow_raw))
mcp_client_token = st.sidebar.text_input("MCP client token", value=cfg.nexus.mcp_client_token or "", type="password")

if transport != "stdio":
    st.sidebar.info(
        "Running in remote mode; connection overrides must be configured on the Nexus MCP server.",
    )

overrides_env: Dict[str, str] = {
    "NEXUS_BASE_URL": base_url.strip(),
    "NEXUS_VERIFY_SSL": "true" if verify_ssl else "false",
    "NEXUS_ALLOW_RAW": "true" if allow_raw else "false",
}
if username.strip():
    overrides_env["NEXUS_USERNAME"] = username.strip()
if password:
    overrides_env["NEXUS_PASSWORD"] = password
if token:
    overrides_env["NEXUS_TOKEN"] = token
if mcp_client_token:
    overrides_env["NEXUS_MCP_CLIENT_TOKEN"] = mcp_client_token

try:
    tools = _get_nexus_tools(overrides=overrides_env)
except Exception as exc:  # noqa: BLE001
    st.error(f"Failed to load Nexus MCP tools: {exc}")
    st.info(
        "For local dev, ensure Nexus is running and reachable at the Base URL. "
        "Defaults assume http://localhost:8081"
    )
    st.stop()


c1, c2, c3 = st.columns([1, 2, 2])
with c1:
    if st.button("Refresh tools", use_container_width=True):
        tools = _get_nexus_tools(force_reload=True, overrides=overrides_env)
        st.success("Reloaded")
with c2:
    st.metric("Tools", len(tools))
with c3:
    st.write(f"Transport: **{transport}**")

with st.expander("Show loaded tool names", expanded=False):
    tool_names = sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})
    st.code("\n".join(tool_names) if tool_names else "(no tools)", language="text")


tabs = st.tabs([
    "Health",
    "Repositories",
    "Search components",
    "Search assets",
    "Admin",
    "Raw",
])

# -------------------- Health --------------------
with tabs[0]:
    st.subheader("Health")
    if st.button("Run health check", use_container_width=True):
        res = _normalize_mcp_result(_invoke(tools, "nexus_health_check", {}))
        st.json(res)

# -------------------- Repositories --------------------
with tabs[1]:
    st.subheader("Repositories")
    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("List repositories", use_container_width=True):
            st.session_state["_nexus_repos"] = _normalize_mcp_result(_invoke(tools, "nexus_list_repositories", {}))

    repos_res = st.session_state.get("_nexus_repos")
    repos: List[Dict[str, Any]] = []
    if isinstance(repos_res, dict) and repos_res.get("ok") and isinstance(repos_res.get("data"), list):
        repos = repos_res.get("data")

    with col_b:
        if repos:
            st.dataframe(pd.DataFrame(repos), use_container_width=True, hide_index=True)
        elif repos_res:
            st.json(repos_res)

# -------------------- Search components --------------------
with tabs[2]:
    st.subheader("Search components")

    left, right = st.columns([2, 1])
    with left:
        q = st.text_input("Query (q)", value="")
        repo = st.text_input("Repository", value="")
    with right:
        format_ = st.text_input("Format", value="")

    g1, g2, g3 = st.columns(3)
    with g1:
        group = st.text_input("Group", value="")
    with g2:
        name = st.text_input("Name", value="")
    with g3:
        version = st.text_input("Version", value="")

    cont = st.text_input("Continuation token (optional)", value="")

    if st.button("Search", use_container_width=True):
        args = {
            "q": q or None,
            "repository": repo or None,
            "format": format_ or None,
            "group": group or None,
            "name": name or None,
            "version": version or None,
            "continuation_token": cont or None,
        }
        st.session_state["_nexus_search_components"] = _normalize_mcp_result(
            _invoke(tools, "nexus_search_components", args)
        )

    res = st.session_state.get("_nexus_search_components")
    if isinstance(res, dict) and res.get("ok") and isinstance(res.get("data"), dict):
        data = res["data"]
        items = data.get("items") or []
        nxt = data.get("continuationToken")
        st.caption(f"Items: {len(items)}")
        if nxt:
            st.info(f"Next continuationToken: {nxt}")
        if items:
            st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)
        else:
            st.json(res)
    elif res:
        st.json(res)

# -------------------- Search assets --------------------
with tabs[3]:
    st.subheader("Search assets")

    left, right = st.columns([2, 1])
    with left:
        repo = st.text_input("Repository", value="", key="assets_repo")
        group = st.text_input("Group", value="", key="assets_group")
    with right:
        format_ = st.text_input("Format", value="", key="assets_format")
        name = st.text_input("Name", value="", key="assets_name")

    version = st.text_input("Version", value="", key="assets_version")
    cont = st.text_input("Continuation token (optional)", value="", key="assets_cont")

    if st.button("List assets", use_container_width=True):
        args = {
            "repository": repo or None,
            "format": format_ or None,
            "group": group or None,
            "name": name or None,
            "version": version or None,
            "continuation_token": cont or None,
        }
        st.session_state["_nexus_search_assets"] = _normalize_mcp_result(
            _invoke(tools, "nexus_list_assets", args)
        )

    res = st.session_state.get("_nexus_search_assets")
    if isinstance(res, dict) and res.get("ok") and isinstance(res.get("data"), dict):
        data = res["data"]
        items = data.get("items") or []
        nxt = data.get("continuationToken")
        st.caption(f"Items: {len(items)}")
        if nxt:
            st.info(f"Next continuationToken: {nxt}")
        if items:
            st.dataframe(pd.DataFrame(items), use_container_width=True, hide_index=True)

            # Quick metadata lookup
            asset_ids = [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]
            picked = st.selectbox("Asset id (metadata)", options=[""] + asset_ids, index=0)
            if picked and st.button("Get asset metadata", use_container_width=True):
                st.json(_normalize_mcp_result(_invoke(tools, "nexus_get_asset", {"asset_id": picked})))
        else:
            st.json(res)
    elif res:
        st.json(res)

# -------------------- Admin --------------------
with tabs[4]:
    st.subheader("Admin")

    a1, a2, a3 = st.columns(3)
    with a1:
        if st.button("List users", use_container_width=True):
            st.session_state["_nexus_users"] = _normalize_mcp_result(_invoke(tools, "nexus_list_users", {}))
    with a2:
        if st.button("List roles", use_container_width=True):
            st.session_state["_nexus_roles"] = _normalize_mcp_result(_invoke(tools, "nexus_list_roles", {}))
    with a3:
        if st.button("List tasks", use_container_width=True):
            st.session_state["_nexus_tasks"] = _normalize_mcp_result(_invoke(tools, "nexus_list_tasks", {}))

    users = st.session_state.get("_nexus_users")
    roles = st.session_state.get("_nexus_roles")
    tasks = st.session_state.get("_nexus_tasks")

    st.markdown("---")
    col_u, col_r = st.columns(2)
    with col_u:
        st.markdown("#### Users")
        if isinstance(users, dict) and users.get("ok") and isinstance(users.get("data"), list):
            st.dataframe(pd.DataFrame(users["data"]), use_container_width=True, hide_index=True)
        elif users:
            st.json(users)

    with col_r:
        st.markdown("#### Roles")
        if isinstance(roles, dict) and roles.get("ok") and isinstance(roles.get("data"), list):
            st.dataframe(pd.DataFrame(roles["data"]), use_container_width=True, hide_index=True)
        elif roles:
            st.json(roles)

    st.markdown("#### Tasks")
    if isinstance(tasks, dict) and tasks.get("ok") and isinstance(tasks.get("data"), list):
        st.dataframe(pd.DataFrame(tasks["data"]), use_container_width=True, hide_index=True)
    elif tasks:
        st.json(tasks)

# -------------------- Raw --------------------
with tabs[5]:
    st.subheader("Raw request")
    st.caption("Disabled by default. Toggle 'Enable raw request tool' in the sidebar.")

    method = st.selectbox("Method", ["GET", "POST", "PUT", "DELETE", "PATCH"], index=0)
    path = st.text_input("Path", value="/service/rest/v1/repositories")

    col_p, col_h = st.columns(2)
    with col_p:
        params_json = st.text_area("Query params (JSON)", value="{}", height=120)
    with col_h:
        headers_json = st.text_area("Headers (JSON)", value="{}", height=120)

    body_json = st.text_area("JSON body (JSON)", value="null", height=140)

    if st.button("Send", use_container_width=True):
        params = _safe_json_loads(params_json)
        headers = _safe_json_loads(headers_json)
        body = _safe_json_loads(body_json)

        if params is None:
            params = {}
        if headers is None:
            headers = {}

        res = _normalize_mcp_result(
            _invoke(
                tools,
                "nexus_raw_request",
                {
                    "method": method,
                    "path": path,
                    "params": params,
                    "json_body": body,
                    "headers": headers,
                },
            )
        )
        st.json(res)
