from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.streamlit_config import get_app_config
from src.theme import set_theme


set_theme(page_title="Nexus Explorer", page_icon="🗄️")

admin = load_admin_config()
if not admin.is_mcp_enabled("nexus", default=True):
    st.info("Nexus MCP is disabled by Admin.")
    st.stop()


def _safe_json_loads(raw: str) -> Optional[Any]:
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _get_nexus_client(force_new: bool = False):
    """Get the Nexus MCP client."""
    return get_mcp_client("nexus", force_new=force_new)


def _get_nexus_tools(*, force_reload: bool = False) -> List[Dict[str, Any]]:
    """Get Nexus MCP tools using the unified client."""
    client = _get_nexus_client(force_new=force_reload)
    tools = client.list_tools(force_refresh=force_reload)
    st.session_state["_nexus_tools"] = tools
    st.session_state["_nexus_tools_sig"] = get_server_url("nexus")
    return tools


def _invoke(tools, name: str, args: Dict[str, Any]) -> Any:
    """Invoke a Nexus MCP tool."""
    client = _get_nexus_client()
    return client.invoke(name, args)


st.title("Nexus Explorer")
st.caption("Explore Sonatype Nexus Repository Manager via MCP server.")

nexus_url = get_server_url("nexus")

# Invalidate cached tools if the target URL changes
if st.session_state.get("_nexus_tools_sig") != nexus_url:
    st.session_state.pop("_nexus_tools", None)
    st.session_state["_nexus_tools_sig"] = nexus_url

with st.expander("Connection info", expanded=False):
    st.write(f"**Transport:** streamable-http")
    st.write(f"**MCP URL:** `{nexus_url}`")

with st.sidebar:
    st.header("Nexus connection")
    st.caption(f"URL: {nexus_url}")

    st.divider()
    if "nexus_auto_load_tools" not in st.session_state:
        st.session_state.nexus_auto_load_tools = False
    st.session_state.nexus_auto_load_tools = st.toggle(
        "Auto-load tools on open",
        value=bool(st.session_state.nexus_auto_load_tools),
        help="When enabled, the page will discover tools automatically on open. Leave off for fastest loads.",
    )
    load_clicked = st.button("Load/refresh tools", use_container_width=True)

should_load = bool(load_clicked) or (
    bool(st.session_state.get("nexus_auto_load_tools")) and "_nexus_tools" not in st.session_state
)

if should_load:
    try:
        _get_nexus_tools(force_reload=bool(load_clicked))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load Nexus MCP tools: {exc}")
        st.info(
            "Ensure the Nexus MCP server is running and reachable. "
            f"Expected URL: {nexus_url}"
        )

tools = st.session_state.get("_nexus_tools")
if not tools:
    st.info("Nexus tools are not loaded yet. Click **Load/refresh tools** in the sidebar.")
    st.stop()


c1, c2, c3 = st.columns([1, 2, 2])
with c1:
    if st.button("Refresh tools", use_container_width=True):
        tools = _get_nexus_tools(force_reload=True)
        st.success("Reloaded")
with c2:
    st.metric("Tools", len(tools))
with c3:
    st.write("Transport: **streamable-http**")

with st.expander("Show loaded tool names", expanded=False):
    tool_names = sorted({
        t.get("name", "") if isinstance(t, dict) else str(getattr(t, "name", ""))
        for t in (tools or [])
        if (t.get("name") if isinstance(t, dict) else getattr(t, "name", None))
    })
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
        res = _invoke(tools, "nexus_health_check", {})
        st.json(res)

# -------------------- Repositories --------------------
with tabs[1]:
    st.subheader("Repositories")
    col_a, col_b = st.columns([1, 3])
    with col_a:
        if st.button("List repositories", use_container_width=True):
            st.session_state["_nexus_repos"] = _invoke(tools, "nexus_list_repositories", {})

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
        st.session_state["_nexus_search_components"] = _invoke(
            tools, "nexus_search_components", args
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
        st.session_state["_nexus_search_assets"] = _invoke(
            tools, "nexus_list_assets", args
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
                st.json(_invoke(tools, "nexus_get_asset", {"asset_id": picked}))
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
            st.session_state["_nexus_users"] = _invoke(tools, "nexus_list_users", {})
    with a2:
        if st.button("List roles", use_container_width=True):
            st.session_state["_nexus_roles"] = _invoke(tools, "nexus_list_roles", {})
    with a3:
        if st.button("List tasks", use_container_width=True):
            st.session_state["_nexus_tasks"] = _invoke(tools, "nexus_list_tasks", {})

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

        res = _invoke(
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
        st.json(res)
