"""Agent Lab - Test sample agents against your MCP servers."""

from __future__ import annotations

import traceback
import uuid
from typing import Any, Dict, List, Optional

import os
import json

import requests

import streamlit as st

from src.admin_config import load_admin_config
from src.mcp_health import add_mcp_status_styles, get_status_icon
from src.mcp_client import get_mcp_client, get_server_url
from src.theme import set_theme


set_theme(page_title="Agent Lab", page_icon="🧪")

admin = load_admin_config()
if not admin.is_agent_enabled("agent_lab", default=True):
    st.info("Agent Lab is disabled by Admin.")
    st.stop()

add_mcp_status_styles()


def _stable_hash(obj: Any) -> str:
    try:
        return json.dumps(obj, sort_keys=True, default=str)
    except Exception:
        return str(obj)


def _agent_config_fingerprint(cfg: Dict[str, Any]) -> str:
    relevant = {
        "agent_type": cfg.get("agent_type"),
        "servers": list(cfg.get("servers") or []),
        "model": cfg.get("model"),
        "embedding_model": cfg.get("embedding_model"),
        "ollama_url": cfg.get("ollama_url"),
        "temperature": cfg.get("temperature"),
        "system_prompt": cfg.get("system_prompt"),
    }
    return _stable_hash(relevant)


def _safe_str(x: Any, limit: int = 500) -> str:
    s = "" if x is None else str(x)
    return s if len(s) <= limit else (s[:limit] + "…")


def _schema_arg_summary(schema: Any) -> str:
    if not isinstance(schema, dict):
        return ""
    props = schema.get("properties")
    required = schema.get("required")
    if not isinstance(props, dict):
        return ""
    required_set = set(required) if isinstance(required, list) else set()

    parts: List[str] = []
    for name, prop in props.items():
        if not isinstance(prop, dict):
            continue
        t = prop.get("type")
        is_req = name in required_set
        parts.append(f"{name}{'*' if is_req else ''}:{t or 'any'}")

    return ", ".join(parts[:10]) + ("" if len(parts) <= 10 else ", …")


def _tool_to_row(server_key: str, tool: Any) -> Dict[str, Any]:
    if isinstance(tool, dict):
        name = tool.get("name")
        desc = tool.get("description")
        schema = tool.get("inputSchema")
    else:
        name = getattr(tool, "name", None)
        desc = getattr(tool, "description", None)
        schema = getattr(tool, "inputSchema", None)
    return {
        "server": server_key,
        "name": str(name or ""),
        "description": _safe_str(desc, 200),
        "args": _schema_arg_summary(schema),
        "_schema": schema,
    }


def _prompt_to_row(server_key: str, prompt: Any) -> Dict[str, Any]:
    if isinstance(prompt, dict):
        name = prompt.get("name")
        title = prompt.get("title")
        desc = prompt.get("description")
        args = prompt.get("arguments") or prompt.get("inputSchema")
    else:
        name = getattr(prompt, "name", None)
        title = getattr(prompt, "title", None)
        desc = getattr(prompt, "description", None)
        args = getattr(prompt, "arguments", None)
    return {
        "server": server_key,
        "name": str(name or ""),
        "title": str(title or ""),
        "description": _safe_str(desc, 200),
        "args": _schema_arg_summary(args),
        "_schema": args,
    }


def _resource_to_row(server_key: str, resource: Any) -> Dict[str, Any]:
    if isinstance(resource, dict):
        uri = resource.get("uri")
        name = resource.get("name")
        desc = resource.get("description")
        mime = resource.get("mimeType") or resource.get("mime_type")
    else:
        uri = getattr(resource, "uri", None)
        name = getattr(resource, "name", None)
        desc = getattr(resource, "description", None)
        mime = getattr(resource, "mimeType", None)
    return {
        "server": server_key,
        "uri": str(uri or ""),
        "name": str(name or ""),
        "mime": str(mime or ""),
        "description": _safe_str(desc, 200),
    }


def _extract_prompt_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""

    # FastMCP often returns {result: {messages:[{role,content}]}} or similar.
    messages = payload.get("messages") or payload.get("message")
    if isinstance(messages, list) and messages:
        msg0 = messages[0]
        if isinstance(msg0, dict):
            content = msg0.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                # Content blocks
                texts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        texts.append(str(c.get("text") or ""))
                return "\n".join([t for t in texts if t])

    # Sometimes it's a plain string in result.
    result = payload.get("result")
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        maybe = result.get("text") or result.get("content")
        if isinstance(maybe, str):
            return maybe
    return ""


@st.cache_data(ttl=30)
def _ollama_health(base_url: str) -> Dict[str, Any]:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return {"ok": False, "message": "No URL"}

    # Ollama commonly supports /api/tags and /api/version.
    try:
        resp = requests.get(url + "/api/tags", timeout=2)
        if 200 <= resp.status_code < 400:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            models = data.get("models") if isinstance(data, dict) else None
            model_count = len(models) if isinstance(models, list) else None
            return {"ok": True, "message": f"Reachable{f' ({model_count} models)' if model_count is not None else ''}"}
        return {"ok": False, "message": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


@st.cache_data(ttl=20)
def _server_snapshot(server_key: str, url: str) -> Dict[str, Any]:
    client = get_mcp_client(server_key, url=url, timeout=6.0, force_new=True, source="agent_lab")
    health = client.health_check()
    tool_count = int(health.get("tool_count") or 0) if health.get("ok") else 0
    prompt_count = int(health.get("prompt_count") or 0) if health.get("ok") else 0
    resource_count = int(health.get("resource_count") or 0) if health.get("ok") else 0

    tools = []
    prompts = []
    resources = []
    try:
        tools = client.list_tools(force_refresh=False)
    except Exception:
        tools = []
    try:
        prompts = client.list_prompts(force_refresh=False)
    except Exception:
        prompts = []
    try:
        resources = client.list_resources(force_refresh=False)
    except Exception:
        resources = []

    tool_names = [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]
    prompt_names = [p.get("name") for p in prompts if isinstance(p, dict) and p.get("name")]
    resource_uris = [r.get("uri") for r in resources if isinstance(r, dict) and r.get("uri")]

    status = health.get("status") if isinstance(health, dict) else "unknown"
    if not isinstance(status, str):
        status = "unknown"

    return {
        "server": server_key,
        "url": url,
        "ok": bool(health.get("ok")) if isinstance(health, dict) else False,
        "status": status,
        "message": health.get("message") if isinstance(health, dict) else None,
        "response_time_ms": health.get("response_time_ms") if isinstance(health, dict) else None,
        "tool_count": tool_count if tools else len(tool_names),
        "prompt_count": prompt_count if prompts else len(prompt_names),
        "resource_count": resource_count if resources else len(resource_uris),
        "sample_tools": tool_names[:8],
        "sample_prompts": prompt_names[:8],
        "sample_resources": resource_uris[:8],
    }


@st.cache_data(ttl=20)
def _server_inventory(server_key: str, url: str) -> Dict[str, Any]:
    client = get_mcp_client(server_key, url=url, timeout=8.0, force_new=True, source="agent_lab")
    tools: List[Any] = []
    prompts: List[Any] = []
    resources: List[Any] = []
    try:
        tools = client.list_tools(force_refresh=False)
    except Exception:
        tools = []
    try:
        prompts = client.list_prompts(force_refresh=False)
    except Exception:
        prompts = []
    try:
        resources = client.list_resources(force_refresh=False)
    except Exception:
        resources = []
    return {"tools": tools, "prompts": prompts, "resources": resources}


def _generate_system_prompt_with_ollama(
    *,
    ollama_url: str,
    model: str,
    temperature: float,
    selected_servers: List[str],
) -> str:
    try:
        from langchain_ollama.chat_models import ChatOllama
        from langchain_core.messages import HumanMessage
    except Exception:
        ChatOllama = None

    # Build a compact capability summary.
    tool_lines: List[str] = []
    prompt_lines: List[str] = []
    resource_lines: List[str] = []
    for srv in selected_servers[:10]:
        url = _resolve_server_url(srv)
        inv = _server_inventory(srv, url)

        tools = inv.get("tools") or []
        for t in tools[:40]:
            row = _tool_to_row(srv, t)
            if row.get("name"):
                tool_lines.append(f"- {srv}.{row['name']}: {row.get('description','')}")

        prompts = inv.get("prompts") or []
        for p in prompts[:40]:
            row = _prompt_to_row(srv, p)
            label = row.get("title") or row.get("name")
            if label:
                prompt_lines.append(f"- {srv}.{row.get('name')}: {row.get('description','')}")

        resources = inv.get("resources") or []
        for r in resources[:20]:
            rr = _resource_to_row(srv, r)
            if rr.get("uri"):
                resource_lines.append(f"- {srv}: {rr.get('uri')} ({rr.get('mime')})")

    capabilities = "\n".join((tool_lines[:250] + (["\nPrompts:"] + prompt_lines[:150] if prompt_lines else []) + (["\nResources:"] + resource_lines[:80] if resource_lines else [])))

    prompt_text = (
        "Write a strong SYSTEM PROMPT for a tool-using DevOps assistant. "
        "The agent can call MCP tools from the selected servers below. "
        "The system prompt should: (1) explain tool-use rules, (2) require planning + verification, "
        "(3) require redaction of secrets, (4) define response structure. "
        "Return ONLY the system prompt text.\n\n"
        f"Selected servers: {', '.join(selected_servers)}\n\n"
        f"Capabilities:\n{capabilities}\n"
    )

    if ChatOllama is not None:
        llm = ChatOllama(model=model, base_url=ollama_url, temperature=float(temperature))
        msg = llm.invoke([HumanMessage(content=prompt_text)])
        return str(getattr(msg, "content", "") or "").strip()

    # Fallback: Ollama HTTP API
    base = (ollama_url or "").rstrip("/")
    resp = requests.post(
        base + "/api/generate",
        json={"model": model, "prompt": prompt_text, "stream": False, "options": {"temperature": float(temperature)}},
        timeout=15,
    )
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return str(data.get("response") or "").strip()


def _resolve_server_url(server_key: str) -> str:
    try:
        from src.streamlit_config import StreamlitAppConfig
        from src.ai.mcp_specs import build_server_specs

        cfg = StreamlitAppConfig.load()
        specs = build_server_specs(cfg)
        spec = specs.get(server_key)
        if spec and getattr(spec, "url", None):
            return str(spec.url)
    except Exception:
        pass

    return get_server_url(server_key)


def _render_exception(title: str, exc: BaseException) -> None:
    st.error(f"{title}: {exc}")

    sub_excs: List[BaseException] = []
    if hasattr(exc, "exceptions") and isinstance(getattr(exc, "exceptions"), tuple):
        try:
            sub_excs = list(getattr(exc, "exceptions"))  # type: ignore[arg-type]
        except Exception:
            sub_excs = []

    if sub_excs:
        with st.expander(f"Details ({len(sub_excs)} sub-exception(s))", expanded=True):
            for i, sub in enumerate(sub_excs, start=1):
                st.markdown(f"**{i}. {type(sub).__name__}:** {sub}")
                st.code("".join(traceback.format_exception(sub)), language="text")
        return

    with st.expander("Details", expanded=True):
        st.code("".join(traceback.format_exception(exc)), language="text")


def _lazy_import() -> Any:
    from src.ai.agents.agent_lab import (
        AgentRuntime,
        ToolCallEvent,
        build_deep_agent,
        build_normal_agent,
        build_rag_agent,
        get_available_servers,
        run_agent_query,
        run_deep_agent_query,
    )

    return (
        AgentRuntime,
        ToolCallEvent,
        build_deep_agent,
        build_normal_agent,
        build_rag_agent,
        get_available_servers,
        run_agent_query,
        run_deep_agent_query,
    )


st.markdown(
    """
    <style>
    .agentlab-hero {
        background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 55%, #a855f7 100%);
        border-radius: 20px;
        padding: 1.8rem 2.2rem;
        margin-bottom: 1.25rem;
        color: white;
        box-shadow: 0 10px 40px rgba(99, 102, 241, 0.25);
    }
    .agentlab-hero h1 {
        font-size: 2.0rem;
        font-weight: 800;
        margin: 0 0 0.25rem 0;
    }
    .agentlab-hero p { margin: 0; opacity: 0.95; }

    .tool-call-card {
        background: #f8fafc;
        border-radius: 10px;
        padding: 0.85rem;
        margin: 0.5rem 0;
        border-left: 4px solid #6366f1;
        font-size: 0.9rem;
    }
    .tool-call-success { border-left-color: #22c55e; }
    .tool-call-error { border-left-color: #ef4444; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="agentlab-hero">
        <h1>Agent Lab</h1>
        <p>Try Normal / RAG / Deep agents against your running MCP servers using natural language.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


def _init_state() -> None:
    st.session_state.setdefault("agentlab_session_id", str(uuid.uuid4())[:8])
    st.session_state.setdefault("agentlab_runtime", None)
    st.session_state.setdefault("agentlab_messages", [])
    st.session_state.setdefault("agentlab_tool_calls", [])
    st.session_state.setdefault("agentlab_pending_user_message", None)
    st.session_state.setdefault("agentlab_auto_build", True)
    st.session_state.setdefault("agentlab_last_build_fingerprint", None)
    st.session_state.setdefault("agentlab_last_build_error", None)
    st.session_state.setdefault(
        "agentlab_config",
        {
            "agent_type": "Normal",
            "servers": ["kubernetes", "docker"],
            "system_prompt": "",
            "model": "llama3.2",
            "embedding_model": "nomic-embed-text",
            "ollama_url": os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
            "temperature": 0.1,
        },
    )


_init_state()

AGENTS_AVAILABLE = True
AGENT_IMPORT_ERROR: Optional[str] = None

try:
    (
        AgentRuntime,
        ToolCallEvent,
        build_deep_agent,
        build_normal_agent,
        build_rag_agent,
        get_available_servers,
        run_agent_query,
        run_deep_agent_query,
    ) = _lazy_import()
except Exception as exc:
    AGENTS_AVAILABLE = False
    AGENT_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
    AgentRuntime = Any  # type: ignore[assignment]
    ToolCallEvent = Any  # type: ignore[assignment]
    build_deep_agent = None  # type: ignore[assignment]
    build_normal_agent = None  # type: ignore[assignment]
    build_rag_agent = None  # type: ignore[assignment]
    get_available_servers = None  # type: ignore[assignment]
    run_agent_query = None  # type: ignore[assignment]
    run_deep_agent_query = None  # type: ignore[assignment]


def _fallback_server_catalog() -> Dict[str, Dict[str, Any]]:
    """Best-effort server catalog for browsing tools/prompts/resources without LangChain."""

    try:
        from src.streamlit_config import StreamlitAppConfig
        from src.ai.mcp_specs import build_server_specs

        cfg = StreamlitAppConfig.load()
        specs = build_server_specs(cfg)
        out: Dict[str, Dict[str, Any]] = {}
        for key, spec in specs.items():
            out[key] = {
                "name": getattr(spec, "server_name", None) or key,
                "description": "",
            }
        return out
    except Exception:
        # Minimal fallback: use admin config defaults.
        # (URLs still resolve via _resolve_server_url/get_server_url.)
        candidates = [
            "docker",
            "git",
            "jenkins",
            "kubernetes",
            "local",
            "nexus",
            "playwright",
            "scheduler",
            "sonarqube",
            "trivy",
            "websearch",
        ]
        return {k: {"name": k, "description": ""} for k in candidates if admin.is_mcp_enabled(k, default=True)}


servers = get_available_servers() if AGENTS_AVAILABLE and get_available_servers else _fallback_server_catalog()


def _render_tool_calls(tool_calls: List[ToolCallEvent]) -> None:
    if not tool_calls:
        st.caption("No tool calls yet")
        return

    for ev in reversed(tool_calls[-20:]):
        ok = bool(getattr(ev, "ok", False))
        cls = "tool-call-success" if ok else "tool-call-error"
        args = getattr(ev, "args", {}) or {}
        st.markdown(
            f"""
            <div class="tool-call-card {cls}">
                <div><b>{ev.server}</b> · <code>{ev.tool}</code></div>
                <div style="opacity:0.85; margin-top:0.35rem;"><code>{str(args)[:800]}</code></div>
                <div style="opacity:0.75; margin-top:0.35rem;">{(ev.result_preview or '')[:800]}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


with st.sidebar:
    st.markdown("### Configuration")

    if not AGENTS_AVAILABLE:
        st.warning(
            "Agent runtime is disabled (missing optional dependencies). "
            "You can still browse Tools/Prompts/Resources. "
            f"\n\nDetails: {AGENT_IMPORT_ERROR or 'unknown import error'}"
        )

    refresh = st.button("Refresh server info", use_container_width=True)
    if refresh:
        st.cache_data.clear()
        st.rerun()

    st.session_state.agentlab_config["agent_type"] = st.selectbox(
        "Agent type",
        options=["Normal", "RAG", "Deep"],
        index=["Normal", "RAG", "Deep"].index(st.session_state.agentlab_config.get("agent_type", "Normal")),
    )

    st.markdown("#### MCP servers")
    selected_servers: List[str] = []

    # Pre-fetch snapshots for nicer labels + metadata panel.
    server_rows: List[Dict[str, Any]] = []
    for key, info in servers.items():
        url = _resolve_server_url(key)
        snap = _server_snapshot(key, url)
        server_rows.append(
            {
                "server": key,
                "name": info.get("name", key),
                "url": url,
                "status": snap.get("status"),
                "tools": snap.get("tool_count"),
                "prompts": snap.get("prompt_count"),
                "latency_ms": snap.get("response_time_ms"),
                "message": snap.get("message"),
            }
        )

    with st.expander("Server overview", expanded=False):
        st.dataframe(server_rows, use_container_width=True, hide_index=True)

    for key, info in servers.items():
        url = _resolve_server_url(key)
        snap = _server_snapshot(key, url)
        icon = get_status_icon(str(snap.get("status") or "unknown"))
        label = f"{icon} {info['name']} ({snap.get('tool_count', 0)} tools, {snap.get('prompt_count', 0)} prompts)"
        checked = st.checkbox(
            label,
            value=key in st.session_state.agentlab_config.get("servers", []),
            help=info.get("description", ""),
        )
        if checked:
            selected_servers.append(key)
    st.session_state.agentlab_config["servers"] = selected_servers

    st.session_state.agentlab_auto_build = st.toggle(
        "Auto-build agent",
        value=bool(st.session_state.get("agentlab_auto_build", True)),
        help="When enabled, the agent rebuilds automatically when selections/config change.",
    )

    # Subtle Ollama health indicator.
    ollama_url_for_check = st.session_state.agentlab_config.get(
        "ollama_url", os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
    )
    _oll = _ollama_health(str(ollama_url_for_check))
    st.caption(f"Ollama: {'✓' if _oll.get('ok') else '✗'} {(_oll.get('message') or '').strip()}")

    if selected_servers:
        with st.expander("Selected servers (details)", expanded=False):
            for k in selected_servers:
                url = _resolve_server_url(k)
                snap = _server_snapshot(k, url)
                st.markdown(
                    f"**{k}** · {get_status_icon(str(snap.get('status') or 'unknown'))} {snap.get('status')} · {url}"
                )
                if snap.get("message"):
                    st.caption(str(snap.get("message")))
                if snap.get("sample_tools"):
                    st.caption("Tools: " + ", ".join([str(x) for x in snap.get("sample_tools")]))
                if snap.get("sample_prompts"):
                    st.caption("Prompts: " + ", ".join([str(x) for x in snap.get("sample_prompts")]))
                if snap.get("sample_resources"):
                    st.caption("Resources: " + ", ".join([str(x) for x in snap.get("sample_resources")]))

    st.divider()

    st.markdown("#### Model")
    st.session_state.agentlab_config["model"] = st.text_input(
        "Ollama model",
        value=st.session_state.agentlab_config.get("model", "llama3.2"),
    )
    st.session_state.agentlab_config["ollama_url"] = st.text_input(
        "Ollama URL",
        value=st.session_state.agentlab_config.get("ollama_url", os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")),
    )

    if st.session_state.agentlab_config.get("agent_type") == "RAG":
        st.session_state.agentlab_config["embedding_model"] = st.text_input(
            "Embedding model (Ollama)",
            value=st.session_state.agentlab_config.get("embedding_model", "nomic-embed-text"),
            help="Used only for vector RAG indexing; falls back to lexical search if unavailable.",
        )
    st.session_state.agentlab_config["temperature"] = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=float(st.session_state.agentlab_config.get("temperature", 0.1)),
        step=0.1,
    )

    st.divider()

    st.markdown("#### System prompt")
    st.session_state.agentlab_config["system_prompt"] = st.text_area(
        "System prompt",
        value=st.session_state.agentlab_config.get("system_prompt", ""),
        height=130,
        placeholder="Optional: override the agent's system prompt",
        label_visibility="collapsed",
    )

    gen_col1, gen_col2 = st.columns([1.0, 1.0])
    with gen_col1:
        gen_sys = st.button("Generate system prompt", use_container_width=True)
    with gen_col2:
        clear_sys = st.button("Clear", use_container_width=True)

    if clear_sys:
        st.session_state.agentlab_config["system_prompt"] = ""
        st.rerun()

    if gen_sys:
        if not selected_servers:
            st.error("Select at least one MCP server")
        else:
            with st.spinner("Generating system prompt with Ollama..."):
                try:
                    cfg = st.session_state.agentlab_config
                    sys_prompt = _generate_system_prompt_with_ollama(
                        ollama_url=str(cfg.get("ollama_url") or "http://ollama:11434"),
                        model=str(cfg.get("model") or "llama3.2"),
                        temperature=float(cfg.get("temperature") or 0.1),
                        selected_servers=list(selected_servers),
                    )
                    if sys_prompt:
                        st.session_state.agentlab_config["system_prompt"] = sys_prompt
                        st.success("System prompt generated")
                        st.rerun()
                    else:
                        st.warning("No system prompt returned")
                except Exception as exc:
                    _render_exception("Failed to generate system prompt", exc)

    st.divider()

    rebuild = st.button("Rebuild now", type="primary", use_container_width=True)
    clear = st.button("Clear chat", type="secondary", use_container_width=True)

    if clear:
        st.session_state.agentlab_messages = []
        st.session_state.agentlab_tool_calls = []
        st.session_state.agentlab_runtime = None
        st.session_state.agentlab_session_id = str(uuid.uuid4())[:8]
        st.rerun()

    if rebuild:
        st.session_state.agentlab_last_build_fingerprint = None
        st.rerun()


def _maybe_build_agent(selected_servers: List[str]) -> None:
    if not AGENTS_AVAILABLE:
        st.session_state.agentlab_runtime = None
        st.session_state.agentlab_last_build_fingerprint = None
        st.session_state.agentlab_last_build_error = (
            "Agent dependencies are not available in this environment. "
            + (AGENT_IMPORT_ERROR or "")
        ).strip()
        return

    if not selected_servers:
        st.session_state.agentlab_runtime = None
        st.session_state.agentlab_last_build_error = None
        st.session_state.agentlab_last_build_fingerprint = None
        return

    cfg = st.session_state.agentlab_config
    fingerprint = _agent_config_fingerprint(cfg)
    if not st.session_state.get("agentlab_auto_build", True):
        return
    if st.session_state.get("agentlab_last_build_fingerprint") == fingerprint and st.session_state.get("agentlab_runtime") is not None:
        return

    with st.spinner("Auto-building agent for current selections..."):
        st.session_state.agentlab_tool_calls = []
        agent_type = cfg.get("agent_type", "Normal")
        try:
            if agent_type == "RAG":
                runtime = build_rag_agent(
                    selected_servers=selected_servers,
                    model_name=cfg["model"],
                    embedding_model=cfg.get("embedding_model", "nomic-embed-text"),
                    ollama_base_url=cfg["ollama_url"],
                    temperature=float(cfg["temperature"]),
                    system_prompt=cfg.get("system_prompt", ""),
                    tool_call_events=st.session_state.agentlab_tool_calls,
                    session_id=st.session_state.agentlab_session_id,
                    source="agent_lab",
                )
            elif agent_type == "Deep":
                runtime = build_deep_agent(
                    selected_servers=selected_servers,
                    model_name=cfg["model"],
                    ollama_base_url=cfg["ollama_url"],
                    temperature=float(cfg["temperature"]),
                    system_prompt=cfg.get("system_prompt", ""),
                    tool_call_events=st.session_state.agentlab_tool_calls,
                    session_id=st.session_state.agentlab_session_id,
                    source="agent_lab",
                )
            else:
                runtime = build_normal_agent(
                    selected_servers=selected_servers,
                    model_name=cfg["model"],
                    ollama_base_url=cfg["ollama_url"],
                    temperature=float(cfg["temperature"]),
                    system_prompt=cfg.get("system_prompt", ""),
                    tool_call_events=st.session_state.agentlab_tool_calls,
                    session_id=st.session_state.agentlab_session_id,
                    source="agent_lab",
                )

            st.session_state.agentlab_runtime = runtime
            st.session_state.agentlab_last_build_error = None
            st.session_state.agentlab_last_build_fingerprint = fingerprint
        except Exception as exc:
            st.session_state.agentlab_runtime = None
            st.session_state.agentlab_last_build_error = str(exc)
            st.session_state.agentlab_last_build_fingerprint = fingerprint


_maybe_build_agent(selected_servers)


runtime: Optional[AgentRuntime] = st.session_state.agentlab_runtime

selected = st.session_state.agentlab_config.get("servers", [])
if selected:
    total_tools = 0
    total_prompts = 0
    for k in selected:
        url = _resolve_server_url(k)
        snap = _server_snapshot(k, url)
        total_tools += int(snap.get("tool_count") or 0)
        total_prompts += int(snap.get("prompt_count") or 0)

    m1, m2, m3 = st.columns(3)
    m1.metric("Selected servers", str(len(selected)))
    m2.metric("Total tools", str(total_tools))
    m3.metric("Total prompts", str(total_prompts))

status_line = "Ready" if runtime is not None else "Not ready"
if st.session_state.get("agentlab_last_build_error"):
    st.error(f"Agent build error: {st.session_state.get('agentlab_last_build_error')}")
else:
    st.caption(f"Agent status: {status_line}")

tab_chat, tab_tools, tab_prompts, tab_resources, tab_calls = st.tabs(
    ["Chat", "Tools", "Prompts", "Resources", "Tool calls"]
)

with tab_chat:
    if not AGENTS_AVAILABLE:
        st.info(
            "Chat is disabled in this environment because LangChain dependencies are missing. "
            "Run the app in the dev container/compose stack, or install `best-streamlit-website/requirements.txt` "
            "into the current Python environment."
        )
    elif runtime is None:
        st.info("Select servers in the sidebar — the agent auto-builds when enabled.")

    for msg in st.session_state.agentlab_messages:
        role = msg.get("role", "user")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    # Support one-click prompt execution (sets a pending message).
    pending = st.session_state.get("agentlab_pending_user_message")
    if pending and runtime is not None:
        st.session_state.agentlab_pending_user_message = None
        user_text = str(pending)
        st.session_state.agentlab_messages.append({"role": "user", "content": user_text})
        with st.chat_message("user"):
            st.markdown(user_text)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                cfg = st.session_state.agentlab_config
                agent_type = cfg.get("agent_type", "Normal")
                try:
                    if agent_type == "Deep":
                        plan, answer, _events = run_deep_agent_query(runtime, user_text, chat_history=st.session_state.agentlab_messages[:-1])
                        st.markdown("**Plan**")
                        st.markdown(plan or "")
                        st.markdown("**Answer**")
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": f"Plan:\n{plan}\n\nAnswer:\n{answer}"})
                    else:
                        answer, _events = run_agent_query(runtime, user_text, chat_history=st.session_state.agentlab_messages[:-1])
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": answer})
                except Exception as exc:
                    _render_exception("Agent error", exc)

        st.rerun()

    prompt = st.chat_input("Ask in natural language (or use Prompts tab buttons)")
    if prompt and runtime is not None:
        st.session_state.agentlab_messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                cfg = st.session_state.agentlab_config
                agent_type = cfg.get("agent_type", "Normal")
                try:
                    if agent_type == "Deep":
                        plan, answer, _events = run_deep_agent_query(runtime, prompt, chat_history=st.session_state.agentlab_messages[:-1])
                        st.markdown("**Plan**")
                        st.markdown(plan or "")
                        st.markdown("**Answer**")
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": f"Plan:\n{plan}\n\nAnswer:\n{answer}"})
                    else:
                        answer, _events = run_agent_query(runtime, prompt, chat_history=st.session_state.agentlab_messages[:-1])
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": answer})
                except Exception as exc:
                    _render_exception("Agent error", exc)

        st.rerun()


with tab_tools:
    st.markdown("### Tools")
    if not selected_servers:
        st.info("Select at least one server in the sidebar.")
    else:
        search = st.text_input("Search tools", value="", placeholder="e.g. health, list, scan, repo")
        show_schema = st.toggle("Show schemas", value=False)

        tool_rows: List[Dict[str, Any]] = []
        for srv in selected_servers:
            url = _resolve_server_url(srv)
            inv = _server_inventory(srv, url)
            for t in inv.get("tools") or []:
                tool_rows.append(_tool_to_row(srv, t))

        if search:
            q = search.lower().strip()
            tool_rows = [r for r in tool_rows if q in r.get("name", "").lower() or q in r.get("description", "").lower()]

        st.caption(f"{len(tool_rows)} tool(s) across selected servers")
        for r in tool_rows[:250]:
            title = f"{r['server']}.{r['name']}" + (f"  ·  {r['args']}" if r.get("args") else "")
            with st.expander(title, expanded=False):
                if r.get("description"):
                    st.write(r.get("description"))
                if show_schema and r.get("_schema"):
                    st.code(json.dumps(r.get("_schema"), indent=2, default=str)[:6000], language="json")
                st.code(json.dumps({"tool": r["name"], "args": {}}, indent=2), language="json")


with tab_prompts:
    st.markdown("### Prompts")
    if not selected_servers:
        st.info("Select at least one server in the sidebar.")
    else:
        pcol1, pcol2 = st.columns([1.0, 1.2])
        with pcol1:
            prompt_server = st.selectbox("Server", options=list(selected_servers), index=0)
        with pcol2:
            prompt_search = st.text_input("Search prompts", value="", placeholder="e.g. workflow, runbook")

        url = _resolve_server_url(prompt_server)
        inv = _server_inventory(prompt_server, url)
        prompt_rows = [_prompt_to_row(prompt_server, p) for p in (inv.get("prompts") or [])]
        if prompt_search:
            q = prompt_search.lower().strip()
            prompt_rows = [r for r in prompt_rows if q in (r.get("name", "").lower()) or q in (r.get("description", "").lower()) or q in (r.get("title", "").lower())]

        st.caption(f"{len(prompt_rows)} prompt(s) on {prompt_server}")

        args_json = st.text_area(
            "Prompt arguments (JSON)",
            value="{}",
            height=110,
            help="Most prompts accept named args. Provide a JSON object here.",
        )

        for r in prompt_rows[:200]:
            display = r.get("title") or r.get("name")
            header = f"{r['server']}.{r['name']}" + (f"  ·  {display}" if display and display != r.get("name") else "")
            with st.expander(header, expanded=False):
                if r.get("description"):
                    st.write(r.get("description"))
                if r.get("args"):
                    st.caption(f"Args: {r.get('args')}")
                if r.get("_schema"):
                    st.code(json.dumps(r.get("_schema"), indent=2, default=str)[:4000], language="json")

                b1, b2, b3 = st.columns([1.0, 1.0, 1.2])
                run_it = b1.button("Run → chat", key=f"run_prompt:{r['server']}:{r['name']}")
                set_sys = b2.button("Set as system", key=f"sys_prompt:{r['server']}:{r['name']}")
                preview = b3.button("Preview", key=f"preview_prompt:{r['server']}:{r['name']}")

                parsed_args: Dict[str, Any] = {}
                try:
                    parsed_args = json.loads(args_json or "{}")
                    if not isinstance(parsed_args, dict):
                        parsed_args = {}
                except Exception:
                    parsed_args = {}

                if run_it or set_sys or preview:
                    client = get_mcp_client(r["server"], url=_resolve_server_url(r["server"]), timeout=10.0, force_new=True, source="agent_lab")
                    try:
                        payload = client.get_prompt(r["name"], parsed_args)
                        text = _extract_prompt_text(payload)
                        if not text:
                            text = json.dumps(payload, indent=2, default=str)

                        if preview:
                            st.code(text[:8000], language="text")
                        if set_sys:
                            st.session_state.agentlab_config["system_prompt"] = text
                            st.success("System prompt set")
                        if run_it:
                            st.session_state.agentlab_pending_user_message = text
                            st.success("Queued for chat")
                    except Exception as exc:
                        _render_exception("Failed to run prompt", exc)

        st.divider()
        st.markdown("#### Custom prompt")
        custom = st.text_area("Write your own prompt", value="", height=120)
        send_custom = st.button("Send to chat", use_container_width=True)
        if send_custom and custom.strip():
            st.session_state.agentlab_pending_user_message = custom.strip()
            st.success("Queued for chat")


with tab_resources:
    st.markdown("### Resources")
    if not selected_servers:
        st.info("Select at least one server in the sidebar.")
    else:
        rcol1, rcol2 = st.columns([1.0, 1.2])
        with rcol1:
            res_server = st.selectbox("Server", options=list(selected_servers), index=0, key="res_server")
        with rcol2:
            res_search = st.text_input("Search resources", value="", placeholder="uri/name")

        url = _resolve_server_url(res_server)
        inv = _server_inventory(res_server, url)
        res_rows = [_resource_to_row(res_server, r) for r in (inv.get("resources") or [])]
        if res_search:
            q = res_search.lower().strip()
            res_rows = [r for r in res_rows if q in r.get("uri", "").lower() or q in r.get("name", "").lower() or q in r.get("description", "").lower()]

        st.caption(f"{len(res_rows)} resource(s) on {res_server}")
        if not res_rows:
            st.info("No resources reported by this server.")
        else:
            options = [r.get("uri") for r in res_rows if r.get("uri")]
            selected_uri = st.selectbox("Resource URI", options=options, index=0)
            read_btn = st.button("Read resource", type="primary")
            if read_btn and selected_uri:
                client = get_mcp_client(res_server, url=_resolve_server_url(res_server), timeout=10.0, force_new=True, source="agent_lab")
                try:
                    payload = client.read_resource(selected_uri)
                    st.code(json.dumps(payload, indent=2, default=str)[:12000], language="json")
                except Exception as exc:
                    _render_exception("Failed to read resource", exc)


with tab_calls:
    st.markdown("### Tool calls")
    _render_tool_calls(st.session_state.agentlab_tool_calls)
