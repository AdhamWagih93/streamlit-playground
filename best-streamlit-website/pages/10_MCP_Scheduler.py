from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st

from langchain_mcp_adapters.client import MultiServerMCPClient

from src.ai.mcp_langchain_tools import invoke_tool
from src.ai.mcp_specs import build_server_specs
from src.streamlit_config import get_app_config
from src.theme import set_theme


# Repo root (used by stdio subprocess env PYTHONPATH).
ROOT = Path(__file__).resolve().parents[1]


set_theme(page_title="MCP Scheduler", page_icon="⏱️")

st.title("MCP Scheduling Assistant")
st.caption(
    "Manage the external scheduler service via MCP. "
    "Scheduling runs continuously in the scheduler service (not in Streamlit)."
)


DEFAULT_TOOL_BY_SERVER: Dict[str, str] = {
    "docker": "health_check",
    "kubernetes": "health_check",
    "jenkins": "get_server_info",
    "nexus": "nexus_health_check",
}


# --------------------------------------------------------------------------------------
# New architecture: this page is a UI-only MCP client.
# The scheduler itself runs out-of-process (see src/scheduler/).
# --------------------------------------------------------------------------------------


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


def _scheduler_langchain_conn_from_spec(spec: Any) -> Dict[str, Any]:
    transport = str(getattr(spec, "transport", "http") or "http").lower().strip()
    if transport == "http":
        transport = "sse"

    if transport == "stdio":
        env = {**os.environ, **dict(getattr(spec, "env", {}) or {})}
        return {
            "transport": "stdio",
            "command": getattr(spec, "python_executable", None) or os.environ.get("PYTHON") or "python",
            "args": ["-m", str(getattr(spec, "module", ""))],
            "env": env,
        }

    return {"transport": transport, "url": str(getattr(spec, "url", ""))}


def _scheduler_list_tools_cached(defs: Dict[str, Any], *, force_reload: bool = False) -> List[Any]:
    spec = defs.get("scheduler")
    if spec is None:
        return []

    cache_key = "scheduler_tools"
    sig_key = "scheduler_tools_sig"
    sig = _scheduler_spec_sig(spec)
    if force_reload or st.session_state.get(sig_key) != sig or cache_key not in st.session_state:
        try:
            conn = _scheduler_langchain_conn_from_spec(spec)
            client = MultiServerMCPClient(connections={"scheduler": conn})
            st.session_state[cache_key] = asyncio.run(client.get_tools())
            st.session_state[sig_key] = sig
            st.session_state.pop("scheduler_last_error", None)
        except Exception as exc:  # noqa: BLE001
            st.session_state[cache_key] = []
            st.session_state[sig_key] = sig
            st.session_state["scheduler_last_error"] = (
                "Failed to connect to the scheduler MCP server. "
                "Is the scheduler service running and reachable? "
                f"Details: {exc}"
            )
    return list(st.session_state.get(cache_key) or [])


def _call_scheduler_tool(tool: str, args: Dict[str, Any], defs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        tools = _scheduler_list_tools_cached(defs)
        if not tools:
            msg = st.session_state.get("scheduler_last_error") or "Scheduler tools are not available."
            return {"ok": False, "error": "scheduler_unavailable", "details": msg}

        res = invoke_tool(tools, tool, args)
        if isinstance(res, dict) and "ok" in res:
            return res
        return {"ok": True, "result": res}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "scheduler_tool_call_failed", "details": str(exc)}


cfg = get_app_config()
defs = build_server_specs(cfg)

if "scheduler" not in defs:
    st.error("Scheduler MCP server is disabled (admin config).")
    st.stop()


with st.sidebar:
    st.subheader("Scheduler")
    st.caption("Connects to the scheduler MCP server.")

    connect = st.checkbox(
        "Connect to scheduler",
        value=False,
        help="Off by default so the app can start even if the scheduler service isn't running.",
    )

    if st.button("Reload scheduler tools", type="secondary", disabled=not connect):
        _scheduler_list_tools_cached(defs, force_reload=True)
        st.rerun()

    last_err = st.session_state.get("scheduler_last_error")
    if last_err:
        st.warning(last_err)


col_left, col_right = st.columns([2, 3])

with col_left:
    st.subheader("Service")
    if st.button("Health check", type="primary", disabled=not connect):
        st.session_state["scheduler_health"] = _call_scheduler_tool("scheduler_health", {}, defs)
    if "scheduler_health" in st.session_state:
        st.json(st.session_state["scheduler_health"])

    st.subheader("Jobs")
    if st.button("Load jobs", disabled=not connect):
        st.session_state["scheduler_jobs"] = _call_scheduler_tool("scheduler_list_jobs", {}, defs)
    jobs_res = st.session_state.get("scheduler_jobs")
    if isinstance(jobs_res, dict):
        if not jobs_res.get("ok"):
            st.error(jobs_res)
        else:
            st.dataframe(list(jobs_res.get("jobs") or []), use_container_width=True)

    with st.expander("Delete job", expanded=False):
        job_id = st.text_input("Job ID", key="scheduler_delete_job_id")
        if st.button("Delete", type="secondary", key="scheduler_delete_job_btn", disabled=not connect):
            st.json(_call_scheduler_tool("scheduler_delete_job", {"job_id": job_id}, defs))


with col_right:
    st.subheader("Create / update job")

    if "scheduler_job_server" not in st.session_state:
        st.session_state["scheduler_job_server"] = "jenkins"
    if "scheduler_job_tool" not in st.session_state:
        st.session_state["scheduler_job_tool"] = DEFAULT_TOOL_BY_SERVER.get("jenkins", "health_check")

    def _sync_default_tool() -> None:
        srv = str(st.session_state.get("scheduler_job_server") or "").lower().strip()
        st.session_state["scheduler_job_tool"] = DEFAULT_TOOL_BY_SERVER.get(srv, "health_check")

    # IMPORTANT: Streamlit forbids widget callbacks inside a form.
    # Keep the server selector outside the form so we can update the
    # default tool name immediately when the target server changes.
    st.selectbox(
        "Target MCP server",
        ["jenkins", "kubernetes", "docker", "nexus"],
        index=0,
        key="scheduler_job_server",
        on_change=_sync_default_tool,
    )

    with st.form("scheduler_upsert_job_form"):
        job_id = st.text_input("Job ID (optional)", value="")
        enabled = st.checkbox("Enabled", value=True)
        label = st.text_input("Label", value="")
        server = str(st.session_state.get("scheduler_job_server") or "jenkins")
        tool = st.text_input("Tool name", key="scheduler_job_tool")
        interval_seconds = st.number_input("Interval seconds", min_value=5, value=60, step=5)
        args_json = st.text_area("Args (JSON)", value="{}", height=140)
        submitted = st.form_submit_button("Save", disabled=not connect)

    if submitted:
        try:
            args_obj = json.loads(args_json or "{}")
            if not isinstance(args_obj, dict):
                raise ValueError("Args JSON must be an object")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Invalid args JSON: {exc}")
        else:
            payload: Dict[str, Any] = {
                "enabled": bool(enabled),
                "label": str(label),
                "server": str(server),
                "tool": str(tool),
                "args": args_obj,
                "interval_seconds": int(interval_seconds),
            }
            if job_id.strip():
                payload["job_id"] = job_id.strip()

            res = _call_scheduler_tool("scheduler_upsert_job", payload, defs)
            st.json(res)
            if res.get("ok"):
                st.success("Saved.")


st.divider()
st.subheader("Runs")

col_a, col_b = st.columns(2)
with col_a:
    limit = st.number_input("Limit", min_value=1, max_value=500, value=50)
with col_b:
    filter_job_id = st.text_input("Filter by job_id (optional)", value="")

if st.button("Load runs"):
    payload = {"limit": int(limit)}
    if filter_job_id.strip():
        payload["job_id"] = filter_job_id.strip()
    st.session_state["scheduler_runs"] = _call_scheduler_tool("scheduler_list_runs", payload, defs)

runs_res = st.session_state.get("scheduler_runs")
if isinstance(runs_res, dict):
    if not runs_res.get("ok"):
        st.error(runs_res)
    else:
        st.dataframe(list(runs_res.get("runs") or []), use_container_width=True)


# Prevent the legacy in-page scheduler code from running.
st.stop()
