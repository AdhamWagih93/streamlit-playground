from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_client import get_mcp_client, get_server_url
from src.theme import set_theme


set_theme(page_title="Local Files", page_icon="🗂️")

admin = load_admin_config()
if not admin.is_mcp_enabled("local", default=True):
    st.info("Local MCP is disabled by Admin.")
    st.stop()


def _get_local_client(force_new: bool = False):
    return get_mcp_client("local", force_new=force_new)


def _get_local_tools(*, force_reload: bool = False) -> List[Dict[str, Any]]:
    client = _get_local_client(force_new=force_reload)
    tools = client.list_tools(force_refresh=force_reload)
    st.session_state["_local_tools"] = tools
    st.session_state["_local_tools_sig"] = get_server_url("local")
    return tools


def _invoke(name: str, args: Dict[str, Any]) -> Any:
    client = _get_local_client()
    return client.invoke(name, args)


st.title("Local File System")
st.caption("Browse, read, and search files via the Local MCP server.")

local_url = get_server_url("local")

if st.session_state.get("_local_tools_sig") != local_url:
    st.session_state.pop("_local_tools", None)
    st.session_state["_local_tools_sig"] = local_url

with st.expander("Connection info", expanded=False):
    st.write("**Transport:** streamable-http")
    st.write(f"**MCP URL:** `{local_url}`")

with st.sidebar:
    st.header("Local MCP connection")
    st.caption(f"URL: {local_url}")

    st.divider()
    if "local_auto_load_tools" not in st.session_state:
        st.session_state.local_auto_load_tools = False
    st.session_state.local_auto_load_tools = st.toggle(
        "Auto-load tools on open",
        value=bool(st.session_state.local_auto_load_tools),
        help="When enabled, the page will discover tools automatically on open. Leave off for fastest loads.",
    )
    load_clicked = st.button("Load/refresh tools", use_container_width=True)

should_load = bool(load_clicked) or (
    bool(st.session_state.get("local_auto_load_tools")) and "_local_tools" not in st.session_state
)

if should_load:
    try:
        _get_local_tools(force_reload=bool(load_clicked))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Failed to load Local MCP tools: {exc}")
        st.info("Ensure the Local MCP server is running and reachable.")

tools = st.session_state.get("_local_tools")
if not tools:
    st.info("Local tools are not loaded yet. Click **Load/refresh tools** in the sidebar.")
    st.stop()

c1, c2, c3 = st.columns([1, 2, 2])
with c1:
    if st.button("Refresh tools", use_container_width=True):
        tools = _get_local_tools(force_reload=True)
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


health_tab, browse_tab, read_tab, write_tab, search_tab, tree_tab, stat_tab = st.tabs([
    "Health",
    "Browse",
    "Read",
    "Write",
    "Search",
    "Tree",
    "Stat",
])

with health_tab:
    st.subheader("Health")
    if st.button("Run health check", use_container_width=True):
        st.json(_invoke("local_health_check", {}))

with browse_tab:
    st.subheader("Browse directory")
    path = st.text_input("Relative path", value="")
    if st.button("List directory", use_container_width=True):
        res = _invoke("local_list_dir", {"path": path})
        st.session_state["_local_list_dir"] = res

    res = st.session_state.get("_local_list_dir")
    if isinstance(res, dict) and res.get("ok") and isinstance(res.get("entries"), list):
        st.dataframe(pd.DataFrame(res["entries"]), use_container_width=True, hide_index=True)
    elif res:
        st.json(res)

with read_tab:
    st.subheader("Read file")
    path = st.text_input("File path", value="", key="local_read_path")
    max_bytes = st.number_input("Max bytes", min_value=1024, max_value=2_000_000, value=200_000, step=1024)
    if st.button("Read file", use_container_width=True):
        res = _invoke("local_read_file", {"path": path, "max_bytes": int(max_bytes)})
        st.session_state["_local_read_file"] = res

    res = st.session_state.get("_local_read_file")
    if isinstance(res, dict) and res.get("ok"):
        st.caption(f"Path: {res.get('path')}")
        if res.get("truncated"):
            st.warning("Output truncated")
        st.code(res.get("text", ""), language="text")
    elif res:
        st.json(res)

with write_tab:
    st.subheader("Write file")
    path = st.text_input("File path", value="", key="local_write_path")
    mode = st.selectbox("Mode", options=["overwrite", "append"], index=0)
    content = st.text_area("Content", height=200)
    if st.button("Write file", use_container_width=True):
        res = _invoke("local_write_file", {"path": path, "content": content, "mode": mode})
        st.session_state["_local_write_file"] = res

    res = st.session_state.get("_local_write_file")
    if res:
        st.json(res)

with search_tab:
    st.subheader("Search")
    search_path = st.text_input("Search root", value="", key="local_search_root")

    col_a, col_b = st.columns(2)
    with col_a:
        name_query = st.text_input("Filename query", value="", key="local_name_query")
        if st.button("Search filenames", use_container_width=True):
            res = _invoke("local_search_filenames", {"query": name_query, "path": search_path})
            st.session_state["_local_search_names"] = res

        res = st.session_state.get("_local_search_names")
        if isinstance(res, dict) and res.get("ok"):
            st.caption(f"Matches: {res.get('count', 0)}")
            if res.get("results"):
                st.dataframe(pd.DataFrame(res["results"]), use_container_width=True, hide_index=True)
        elif res:
            st.json(res)

    with col_b:
        content_query = st.text_input("Content query", value="", key="local_content_query")
        if st.button("Search contents", use_container_width=True):
            res = _invoke("local_search_contents", {"query": content_query, "path": search_path})
            st.session_state["_local_search_contents"] = res

        res = st.session_state.get("_local_search_contents")
        if isinstance(res, dict) and res.get("ok"):
            st.caption(f"Matches: {res.get('count', 0)}")
            if res.get("results"):
                st.dataframe(pd.DataFrame(res["results"]), use_container_width=True, hide_index=True)
        elif res:
            st.json(res)

with tree_tab:
    st.subheader("Tree")
    tree_path = st.text_input("Root path", value="", key="local_tree_path")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        max_depth = st.number_input("Max depth", min_value=0, max_value=12, value=3, step=1)
        max_entries = st.number_input("Max entries", min_value=50, max_value=5000, value=500, step=50)
    with col_b:
        include_hidden = st.toggle("Include hidden", value=False, key="local_tree_hidden")
        include_sizes = st.toggle("Include sizes", value=True, key="local_tree_sizes")
    with col_c:
        include_owners = st.toggle("Include owners", value=False, key="local_tree_owners")

    if st.button("Load tree", use_container_width=True):
        res = _invoke(
            "local_tree",
            {
                "path": tree_path,
                "max_depth": int(max_depth),
                "max_entries": int(max_entries),
                "include_hidden": bool(include_hidden),
                "include_sizes": bool(include_sizes),
                "include_owners": bool(include_owners),
            },
        )
        st.session_state["_local_tree"] = res

    res = st.session_state.get("_local_tree")
    if isinstance(res, dict) and res.get("ok"):
        if res.get("truncated"):
            st.warning("Results truncated by max entries")
        entries = res.get("entries") or []
        if entries:
            st.dataframe(pd.DataFrame(entries), use_container_width=True, hide_index=True)
        else:
            st.info("No entries found.")
    elif res:
        st.json(res)

with stat_tab:
    st.subheader("Stat")
    stat_path = st.text_input("Path", value="", key="local_stat_path")
    if st.button("Get metadata", use_container_width=True):
        res = _invoke("local_stat", {"path": stat_path})
        st.session_state["_local_stat"] = res

    res = st.session_state.get("_local_stat")
    if res:
        st.json(res)
