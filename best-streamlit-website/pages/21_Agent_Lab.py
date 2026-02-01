"""Agent Lab - Test sample agents against your MCP servers."""

from __future__ import annotations

import traceback
import uuid
from datetime import datetime
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


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS & PRESETS
# ─────────────────────────────────────────────────────────────────────────────

AGENT_PRESETS: Dict[str, Dict[str, Any]] = {
    "devops": {
        "name": "DevOps Engineer",
        "icon": "🔧",
        "description": "Full-stack DevOps with K8s, Docker, Jenkins, and Git",
        "servers": ["kubernetes", "docker", "jenkins", "git"],
        "system_prompt": (
            "You are an expert DevOps engineer assistant. Help users with:\n"
            "- Kubernetes cluster management and troubleshooting\n"
            "- Docker container operations and debugging\n"
            "- CI/CD pipeline management with Jenkins\n"
            "- Git operations and code review\n"
            "Always explain your actions and suggest best practices."
        ),
        "color": "#3b82f6",
    },
    "security": {
        "name": "Security Analyst",
        "icon": "🛡️",
        "description": "Security scanning with Trivy and code analysis",
        "servers": ["trivy", "git", "sonarqube"],
        "system_prompt": (
            "You are a security analyst assistant focused on:\n"
            "- Vulnerability scanning and assessment\n"
            "- Security best practices and compliance\n"
            "- Code security review\n"
            "- Remediation recommendations\n"
            "Always prioritize security findings by severity."
        ),
        "color": "#ef4444",
    },
    "infrastructure": {
        "name": "Infrastructure Manager",
        "icon": "🏗️",
        "description": "Infrastructure with K8s, Docker, and Nexus",
        "servers": ["kubernetes", "docker", "nexus"],
        "system_prompt": (
            "You are an infrastructure management assistant helping with:\n"
            "- Container orchestration and scaling\n"
            "- Artifact repository management\n"
            "- Infrastructure health monitoring\n"
            "- Resource optimization\n"
            "Provide actionable insights and automation suggestions."
        ),
        "color": "#8b5cf6",
    },
    "automation": {
        "name": "Automation Specialist",
        "icon": "🤖",
        "description": "Workflow automation with scheduling and CI/CD",
        "servers": ["scheduler", "jenkins", "docker"],
        "system_prompt": (
            "You are an automation specialist focused on:\n"
            "- Scheduled task management\n"
            "- CI/CD pipeline automation\n"
            "- Workflow optimization\n"
            "- Process automation patterns\n"
            "Help users automate repetitive tasks efficiently."
        ),
        "color": "#22c55e",
    },
    "explorer": {
        "name": "Code Explorer",
        "icon": "🔍",
        "description": "Code exploration with Git, filesystem, and web search",
        "servers": ["git", "local", "websearch"],
        "system_prompt": (
            "You are a code exploration assistant helping with:\n"
            "- Repository analysis and navigation\n"
            "- File system exploration\n"
            "- Web research for technical solutions\n"
            "- Code understanding and documentation\n"
            "Help users understand and navigate codebases effectively."
        ),
        "color": "#f59e0b",
    },
    "custom": {
        "name": "Custom Agent",
        "icon": "⚙️",
        "description": "Build your own agent with any servers",
        "servers": [],
        "system_prompt": "",
        "color": "#6b7280",
    },
}

QUICK_QUERIES: List[Dict[str, Any]] = [
    {"label": "Cluster Health", "query": "Check the overall health of the Kubernetes cluster", "icon": "💚", "servers": ["kubernetes"]},
    {"label": "Running Pods", "query": "List all running pods across namespaces", "icon": "📦", "servers": ["kubernetes"]},
    {"label": "Docker Status", "query": "Show all Docker containers with their status", "icon": "🐳", "servers": ["docker"]},
    {"label": "Recent Commits", "query": "Show the last 10 commits in the repository", "icon": "📝", "servers": ["git"]},
    {"label": "Security Scan", "query": "Run a security vulnerability scan on the current project", "icon": "🔒", "servers": ["trivy"]},
    {"label": "Jenkins Jobs", "query": "List all Jenkins jobs and their last build status", "icon": "🏗️", "servers": ["jenkins"]},
    {"label": "Failed Pods", "query": "Find any pods in error or CrashLoopBackOff state", "icon": "⚠️", "servers": ["kubernetes"]},
    {"label": "Resource Usage", "query": "Show resource usage (CPU/memory) across the cluster", "icon": "📊", "servers": ["kubernetes"]},
]


# ─────────────────────────────────────────────────────────────────────────────
# STYLES
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    """
    <style>
    /* Enhanced Hero Section */
    .agentlab-hero {
        background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 40%, #a855f7 70%, #ec4899 100%);
        background-size: 200% 200%;
        animation: gradient-shift 8s ease infinite;
        border-radius: 24px;
        padding: 2rem 2.5rem;
        margin-bottom: 1.5rem;
        color: white;
        box-shadow: 0 12px 48px rgba(99, 102, 241, 0.3);
        position: relative;
        overflow: hidden;
    }
    .agentlab-hero::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background: url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23ffffff' fill-opacity='0.05'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E");
        opacity: 0.3;
    }
    @keyframes gradient-shift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }
    .agentlab-hero h1 {
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 0.5rem 0;
        position: relative;
        text-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .agentlab-hero p {
        margin: 0;
        opacity: 0.95;
        font-size: 1.05rem;
        position: relative;
    }
    .hero-stats {
        display: flex;
        gap: 1.5rem;
        margin-top: 1.25rem;
        position: relative;
    }
    .hero-stat {
        background: rgba(255,255,255,0.15);
        backdrop-filter: blur(10px);
        padding: 0.6rem 1rem;
        border-radius: 12px;
        font-size: 0.9rem;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .hero-stat strong {
        font-size: 1.1rem;
    }

    /* Preset Cards */
    .preset-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
        gap: 0.75rem;
        margin-bottom: 1rem;
    }
    .preset-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 14px;
        padding: 1rem;
        border: 2px solid #e2e8f0;
        cursor: pointer;
        transition: all 0.2s ease;
        text-align: center;
    }
    .preset-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(0,0,0,0.1);
    }
    .preset-card.selected {
        border-color: #6366f1;
        background: linear-gradient(145deg, #eef2ff, #e0e7ff);
    }
    .preset-icon {
        font-size: 2rem;
        margin-bottom: 0.5rem;
    }
    .preset-name {
        font-weight: 700;
        font-size: 0.9rem;
        color: #1e293b;
    }
    .preset-desc {
        font-size: 0.75rem;
        color: #64748b;
        margin-top: 0.25rem;
    }

    /* Quick Actions */
    .quick-actions {
        background: linear-gradient(135deg, rgba(14,165,233,0.06), rgba(99,102,241,0.06));
        border-radius: 16px;
        padding: 1rem 1.25rem;
        margin-bottom: 1rem;
        border: 1px solid rgba(99,102,241,0.15);
    }
    .quick-actions-title {
        font-size: 1rem;
        font-weight: 700;
        margin-bottom: 0.75rem;
        color: #4f46e5;
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }
    .quick-btn-grid {
        display: flex;
        flex-wrap: wrap;
        gap: 0.5rem;
    }

    /* Tool call cards */
    .tool-call-card {
        background: #f8fafc;
        border-radius: 12px;
        padding: 0.9rem;
        margin: 0.5rem 0;
        border-left: 4px solid #6366f1;
        font-size: 0.88rem;
        transition: all 0.2s ease;
    }
    .tool-call-card:hover {
        background: #f1f5f9;
    }
    .tool-call-success { border-left-color: #22c55e; }
    .tool-call-error { border-left-color: #ef4444; }

    /* Server Health Cards */
    .server-health-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 0.75rem;
        margin-top: 0.75rem;
    }
    .server-health-card {
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 12px;
        padding: 0.9rem;
        border: 1px solid #e2e8f0;
        transition: all 0.2s ease;
    }
    .server-health-card:hover {
        box-shadow: 0 4px 12px rgba(0,0,0,0.08);
    }
    .server-health-card.healthy {
        border-left: 3px solid #22c55e;
    }
    .server-health-card.unhealthy {
        border-left: 3px solid #ef4444;
    }
    .server-name {
        font-weight: 700;
        font-size: 0.95rem;
        margin-bottom: 0.25rem;
    }
    .server-tools {
        font-size: 0.8rem;
        color: #64748b;
    }

    /* Chat Enhancement */
    .chat-message-actions {
        display: flex;
        gap: 0.5rem;
        margin-top: 0.5rem;
        opacity: 0.6;
        transition: opacity 0.2s;
    }
    .chat-message-actions:hover {
        opacity: 1;
    }

    /* Metrics Row */
    .metrics-row {
        display: flex;
        gap: 1rem;
        margin-bottom: 1rem;
    }
    .metric-card {
        flex: 1;
        background: linear-gradient(145deg, #ffffff, #f8fafc);
        border-radius: 14px;
        padding: 1rem 1.25rem;
        border: 1px solid #e2e8f0;
        text-align: center;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 800;
        color: #1e293b;
    }
    .metric-label {
        font-size: 0.8rem;
        color: #64748b;
        margin-top: 0.2rem;
    }

    /* Timeline */
    .timeline-container {
        position: relative;
        padding-left: 1.5rem;
    }
    .timeline-container::before {
        content: '';
        position: absolute;
        left: 0.5rem;
        top: 0;
        bottom: 0;
        width: 2px;
        background: linear-gradient(to bottom, #6366f1, #a855f7);
    }
    .timeline-item {
        position: relative;
        padding: 0.75rem 0;
        padding-left: 1.5rem;
    }
    .timeline-item::before {
        content: '';
        position: absolute;
        left: -1.1rem;
        top: 1rem;
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #6366f1;
        border: 2px solid white;
        box-shadow: 0 0 0 2px #6366f1;
    }
    .timeline-item.success::before { background: #22c55e; box-shadow: 0 0 0 2px #22c55e; }
    .timeline-item.error::before { background: #ef4444; box-shadow: 0 0 0 2px #ef4444; }

    /* Conversation Export */
    .export-panel {
        background: #f8fafc;
        border-radius: 12px;
        padding: 1rem;
        margin-top: 1rem;
        border: 1px dashed #cbd5e1;
    }

    /* Empty State */
    .empty-state {
        text-align: center;
        padding: 3rem 2rem;
        color: #64748b;
    }
    .empty-state-icon {
        font-size: 3rem;
        margin-bottom: 1rem;
        opacity: 0.5;
    }

    /* Recent Queries */
    .recent-query {
        background: #f8fafc;
        border-radius: 8px;
        padding: 0.6rem 0.9rem;
        margin: 0.4rem 0;
        cursor: pointer;
        transition: all 0.15s;
        border: 1px solid transparent;
    }
    .recent-query:hover {
        background: #eef2ff;
        border-color: #c7d2fe;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

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

    messages = payload.get("messages") or payload.get("message")
    if isinstance(messages, list) and messages:
        msg0 = messages[0]
        if isinstance(msg0, dict):
            content = msg0.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        texts.append(str(c.get("text") or ""))
                return "\n".join([t for t in texts if t])

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

    try:
        resp = requests.get(url + "/api/tags", timeout=2)
        if 200 <= resp.status_code < 400:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            models = data.get("models") if isinstance(data, dict) else None
            model_count = len(models) if isinstance(models, list) else None
            model_names = [m.get("name", "") for m in models[:5]] if isinstance(models, list) else []
            return {
                "ok": True,
                "message": f"Reachable ({model_count} models)" if model_count else "Reachable",
                "model_count": model_count,
                "models": model_names,
            }
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
            sub_excs = list(getattr(exc, "exceptions"))
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


def _export_conversation_markdown(messages: List[Dict[str, Any]], tool_calls: List[Any]) -> str:
    """Export conversation to markdown format."""
    lines = ["# Agent Lab Conversation\n", f"Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"]

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        lines.append(f"## {role.title()}\n")
        lines.append(f"{content}\n\n")

    if tool_calls:
        lines.append("---\n\n## Tool Calls\n")
        for tc in tool_calls:
            status = "✅" if getattr(tc, "ok", False) else "❌"
            lines.append(f"- {status} **{getattr(tc, 'server', 'unknown')}**.{getattr(tc, 'tool', 'unknown')}\n")

    return "".join(lines)


def _export_conversation_json(messages: List[Dict[str, Any]], tool_calls: List[Any], config: Dict[str, Any]) -> str:
    """Export conversation to JSON format."""
    export_data = {
        "exported_at": datetime.now().isoformat(),
        "config": {
            "agent_type": config.get("agent_type"),
            "servers": config.get("servers", []),
            "model": config.get("model"),
            "temperature": config.get("temperature"),
        },
        "messages": messages,
        "tool_calls": [
            {
                "server": getattr(tc, "server", ""),
                "tool": getattr(tc, "tool", ""),
                "args": getattr(tc, "args", {}),
                "ok": getattr(tc, "ok", False),
                "result_preview": getattr(tc, "result_preview", "")[:500],
            }
            for tc in tool_calls
        ],
    }
    return json.dumps(export_data, indent=2, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# STATE INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

def _init_state() -> None:
    st.session_state.setdefault("agentlab_session_id", str(uuid.uuid4())[:8])
    st.session_state.setdefault("agentlab_runtime", None)
    st.session_state.setdefault("agentlab_messages", [])
    st.session_state.setdefault("agentlab_tool_calls", [])
    st.session_state.setdefault("agentlab_pending_user_message", None)
    st.session_state.setdefault("agentlab_auto_build", True)
    st.session_state.setdefault("agentlab_last_build_fingerprint", None)
    st.session_state.setdefault("agentlab_last_build_error", None)
    st.session_state.setdefault("agentlab_selected_preset", "custom")
    st.session_state.setdefault("agentlab_recent_queries", [])
    st.session_state.setdefault("agentlab_favorite_queries", [])
    st.session_state.setdefault("agentlab_processing", False)
    st.session_state.setdefault("agentlab_processing_response", None)
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
    AgentRuntime = Any
    ToolCallEvent = Any
    build_deep_agent = None
    build_normal_agent = None
    build_rag_agent = None
    get_available_servers = None
    run_agent_query = None
    run_deep_agent_query = None


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
        candidates = [
            "docker", "git", "jenkins", "kubernetes", "local",
            "nexus", "playwright", "scheduler", "sonarqube", "trivy", "websearch",
        ]
        return {k: {"name": k, "description": ""} for k in candidates if admin.is_mcp_enabled(k, default=True)}


servers = get_available_servers() if AGENTS_AVAILABLE and get_available_servers else _fallback_server_catalog()


# ─────────────────────────────────────────────────────────────────────────────
# HERO SECTION
# ─────────────────────────────────────────────────────────────────────────────

# Calculate stats for hero
total_servers = len(servers)
healthy_servers = 0
total_tools = 0
for k in servers.keys():
    url = _resolve_server_url(k)
    snap = _server_snapshot(k, url)
    if snap.get("ok"):
        healthy_servers += 1
        total_tools += int(snap.get("tool_count") or 0)

ollama_status = _ollama_health(st.session_state.agentlab_config.get("ollama_url", "http://ollama:11434"))

st.markdown(
    f"""
    <div class="agentlab-hero">
        <h1>🧪 Agent Lab</h1>
        <p>Build and test AI agents with Natural Language against your MCP servers</p>
        <div class="hero-stats">
            <div class="hero-stat">
                <span>🖥️</span>
                <span><strong>{healthy_servers}/{total_servers}</strong> Servers</span>
            </div>
            <div class="hero-stat">
                <span>🔧</span>
                <span><strong>{total_tools}</strong> Tools</span>
            </div>
            <div class="hero-stat">
                <span>{'✅' if ollama_status.get('ok') else '❌'}</span>
                <span>Ollama {ollama_status.get('model_count', 0) if ollama_status.get('ok') else 'Offline'}</span>
            </div>
            <div class="hero-stat">
                <span>💬</span>
                <span><strong>{len(st.session_state.agentlab_messages)}</strong> Messages</span>
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# ─────────────────────────────────────────────────────────────────────────────
# AGENT PRESETS SECTION
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("### 🎯 Quick Start: Agent Presets")
st.caption("Select a preset to quickly configure your agent, or choose Custom to build your own")

preset_cols = st.columns(len(AGENT_PRESETS))
for col, (preset_key, preset_info) in zip(preset_cols, AGENT_PRESETS.items()):
    with col:
        is_selected = st.session_state.get("agentlab_selected_preset") == preset_key
        if st.button(
            f"{preset_info['icon']}\n\n**{preset_info['name']}**",
            key=f"preset_{preset_key}",
            use_container_width=True,
            type="primary" if is_selected else "secondary",
        ):
            st.session_state.agentlab_selected_preset = preset_key
            if preset_key != "custom":
                st.session_state.agentlab_config["servers"] = preset_info["servers"]
                st.session_state.agentlab_config["system_prompt"] = preset_info["system_prompt"]
            st.session_state.agentlab_last_build_fingerprint = None  # Force rebuild
            st.rerun()

# Show preset description
current_preset = AGENT_PRESETS.get(st.session_state.get("agentlab_selected_preset", "custom"), AGENT_PRESETS["custom"])
st.caption(f"**{current_preset['name']}:** {current_preset['description']}")

st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# TOOL CALL RENDERER
# ─────────────────────────────────────────────────────────────────────────────

def _render_tool_calls(tool_calls: List[ToolCallEvent]) -> None:
    if not tool_calls:
        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-state-icon">🔧</div>
                <div>No tool calls yet</div>
                <div style="font-size: 0.85rem; margin-top: 0.5rem;">
                    Start a conversation to see tool usage here
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    st.markdown(f"**{len(tool_calls)} tool call(s)** in this session")

    # Timeline view
    st.markdown('<div class="timeline-container">', unsafe_allow_html=True)
    for ev in reversed(tool_calls[-20:]):
        ok = bool(getattr(ev, "ok", False))
        cls = "success" if ok else "error"
        args = getattr(ev, "args", {}) or {}
        started = getattr(ev, "started_at", "")[:19] if hasattr(ev, "started_at") else ""

        st.markdown(
            f"""
            <div class="timeline-item {cls}">
                <div style="font-weight: 600; color: #1e293b;">
                    {ev.server} · <code style="background: #e2e8f0; padding: 0.1rem 0.4rem; border-radius: 4px;">{ev.tool}</code>
                    {'✅' if ok else '❌'}
                </div>
                <div style="font-size: 0.8rem; color: #64748b; margin-top: 0.25rem;">
                    {started}
                </div>
                <div style="font-size: 0.85rem; margin-top: 0.35rem;">
                    <code style="word-break: break-all;">{str(args)[:400]}</code>
                </div>
                <div style="font-size: 0.8rem; color: #64748b; margin-top: 0.35rem;">
                    {(getattr(ev, 'result_preview', '') or '')[:300]}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("### ⚙️ Configuration")

    if not AGENTS_AVAILABLE:
        st.warning(
            "Agent runtime is disabled (missing optional dependencies). "
            "You can still browse Tools/Prompts/Resources. "
            f"\n\nDetails: {AGENT_IMPORT_ERROR or 'unknown import error'}"
        )

    # Quick refresh button
    col_refresh, col_clear = st.columns(2)
    with col_refresh:
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_clear:
        if st.button("🗑️ Clear", use_container_width=True):
            st.session_state.agentlab_messages = []
            st.session_state.agentlab_tool_calls = []
            st.session_state.agentlab_runtime = None
            st.session_state.agentlab_session_id = str(uuid.uuid4())[:8]
            st.rerun()

    st.divider()

    # Agent Type Selection
    st.markdown("#### Agent Type")
    agent_type_help = {
        "Normal": "Standard tool-calling agent",
        "RAG": "Retrieval-augmented with vector search",
        "Deep": "Plan-then-execute with reasoning",
    }
    st.session_state.agentlab_config["agent_type"] = st.selectbox(
        "Agent type",
        options=["Normal", "RAG", "Deep"],
        index=["Normal", "RAG", "Deep"].index(st.session_state.agentlab_config.get("agent_type", "Normal")),
        help=agent_type_help.get(st.session_state.agentlab_config.get("agent_type", "Normal")),
        label_visibility="collapsed",
    )

    st.divider()

    # MCP Servers with Health Status
    st.markdown("#### 🖥️ MCP Servers")

    selected_servers: List[str] = []
    server_rows: List[Dict[str, Any]] = []

    for key, info in servers.items():
        url = _resolve_server_url(key)
        snap = _server_snapshot(key, url)
        server_rows.append({
            "server": key,
            "name": info.get("name", key),
            "url": url,
            "status": snap.get("status"),
            "ok": snap.get("ok"),
            "tools": snap.get("tool_count"),
            "prompts": snap.get("prompt_count"),
            "latency_ms": snap.get("response_time_ms"),
        })

    # Compact server selection
    for key, info in servers.items():
        url = _resolve_server_url(key)
        snap = _server_snapshot(key, url)
        icon = "✅" if snap.get("ok") else "❌"
        tool_count = snap.get("tool_count", 0)

        checked = st.checkbox(
            f"{icon} **{info['name']}** ({tool_count} tools)",
            value=key in st.session_state.agentlab_config.get("servers", []),
            key=f"srv_{key}",
        )
        if checked:
            selected_servers.append(key)

    st.session_state.agentlab_config["servers"] = selected_servers

    # Server health expander
    with st.expander("📊 Server Health", expanded=False):
        for row in server_rows:
            status_icon = "🟢" if row.get("ok") else "🔴"
            latency = f"{row.get('latency_ms', 0):.0f}ms" if row.get("latency_ms") else "N/A"
            st.markdown(f"{status_icon} **{row['name']}** - {row['tools']} tools - {latency}")

    st.divider()

    # Model Settings
    st.markdown("#### 🤖 Model")

    st.session_state.agentlab_config["model"] = st.text_input(
        "Model name",
        value=st.session_state.agentlab_config.get("model", "llama3.2"),
        placeholder="llama3.2, mistral, codellama...",
    )

    st.session_state.agentlab_config["ollama_url"] = st.text_input(
        "Ollama URL",
        value=st.session_state.agentlab_config.get("ollama_url", os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")),
    )

    # Show available models from Ollama
    if ollama_status.get("ok") and ollama_status.get("models"):
        st.caption(f"Available: {', '.join(ollama_status.get('models', []))}")

    st.session_state.agentlab_config["temperature"] = st.slider(
        "Temperature",
        min_value=0.0,
        max_value=1.0,
        value=float(st.session_state.agentlab_config.get("temperature", 0.1)),
        step=0.1,
    )

    if st.session_state.agentlab_config.get("agent_type") == "RAG":
        st.session_state.agentlab_config["embedding_model"] = st.text_input(
            "Embedding model",
            value=st.session_state.agentlab_config.get("embedding_model", "nomic-embed-text"),
        )

    st.divider()

    # System Prompt
    st.markdown("#### 📝 System Prompt")
    st.session_state.agentlab_config["system_prompt"] = st.text_area(
        "System prompt",
        value=st.session_state.agentlab_config.get("system_prompt", ""),
        height=120,
        placeholder="Optional: override the agent's system prompt",
        label_visibility="collapsed",
    )

    col_gen, col_clr = st.columns(2)
    with col_gen:
        if st.button("✨ Generate", use_container_width=True):
            if not selected_servers:
                st.error("Select servers first")
            else:
                with st.spinner("Generating..."):
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
                            st.success("Generated!")
                            st.rerun()
                    except Exception as exc:
                        st.error(str(exc)[:100])
    with col_clr:
        if st.button("Clear", use_container_width=True):
            st.session_state.agentlab_config["system_prompt"] = ""
            st.rerun()

    st.divider()

    # Auto-build toggle and rebuild
    st.session_state.agentlab_auto_build = st.toggle(
        "Auto-build agent",
        value=bool(st.session_state.get("agentlab_auto_build", True)),
    )

    if st.button("🔨 Rebuild Agent", type="primary", use_container_width=True):
        st.session_state.agentlab_last_build_fingerprint = None
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# AGENT BUILD
# ─────────────────────────────────────────────────────────────────────────────

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

    with st.spinner("Building agent..."):
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


# ─────────────────────────────────────────────────────────────────────────────
# METRICS ROW
# ─────────────────────────────────────────────────────────────────────────────

runtime: Optional[AgentRuntime] = st.session_state.agentlab_runtime

selected = st.session_state.agentlab_config.get("servers", [])
if selected:
    sel_tools = 0
    sel_prompts = 0
    for k in selected:
        url = _resolve_server_url(k)
        snap = _server_snapshot(k, url)
        sel_tools += int(snap.get("tool_count") or 0)
        sel_prompts += int(snap.get("prompt_count") or 0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Selected Servers", str(len(selected)))
    m2.metric("Available Tools", str(sel_tools))
    m3.metric("Total Prompts", str(sel_prompts))
    m4.metric("Agent Status", "✅ Ready" if runtime else "⚠️ Not Built")

if st.session_state.get("agentlab_last_build_error"):
    st.error(f"Build error: {st.session_state.get('agentlab_last_build_error')}")


# ─────────────────────────────────────────────────────────────────────────────
# QUICK ACTIONS
# ─────────────────────────────────────────────────────────────────────────────

if runtime and selected:
    st.markdown(
        """
        <div class="quick-actions">
            <div class="quick-actions-title">
                ⚡ Quick Actions
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Filter quick queries by available servers
    available_queries = [
        q for q in QUICK_QUERIES
        if any(s in selected for s in q.get("servers", []))
    ]

    if available_queries:
        cols = st.columns(min(4, len(available_queries)))
        for i, query in enumerate(available_queries[:8]):
            with cols[i % 4]:
                if st.button(
                    f"{query['icon']} {query['label']}",
                    key=f"quick_{i}",
                    use_container_width=True,
                ):
                    st.session_state.agentlab_pending_user_message = query["query"]
                    # Add to recent queries
                    recent = st.session_state.get("agentlab_recent_queries", [])
                    if query["query"] not in recent:
                        recent.insert(0, query["query"])
                        st.session_state.agentlab_recent_queries = recent[:10]
                    st.rerun()
    else:
        st.caption("Select servers to see relevant quick actions")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN TABS
# ─────────────────────────────────────────────────────────────────────────────

tab_chat, tab_tools, tab_prompts, tab_resources, tab_calls, tab_export = st.tabs(
    ["💬 Chat", "🔧 Tools", "📋 Prompts", "📁 Resources", "📊 Tool Calls", "💾 Export"]
)

with tab_chat:
    if not AGENTS_AVAILABLE:
        st.info(
            "Chat is disabled in this environment because LangChain dependencies are missing. "
            "Run the app in the dev container/compose stack, or install `best-streamlit-website/requirements.txt`."
        )
    elif runtime is None:
        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-state-icon">🤖</div>
                <div style="font-size: 1.1rem; font-weight: 600;">No Agent Built</div>
                <div style="margin-top: 0.5rem;">
                    Select servers in the sidebar to build an agent and start chatting
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Recent queries sidebar
    recent_queries = st.session_state.get("agentlab_recent_queries", [])
    if recent_queries and runtime:
        with st.expander("📜 Recent Queries", expanded=False):
            for rq in recent_queries[:5]:
                if st.button(rq[:50] + "..." if len(rq) > 50 else rq, key=f"recent_{hash(rq)}"):
                    st.session_state.agentlab_pending_user_message = rq
                    st.rerun()

    # Render chat messages
    for msg in st.session_state.agentlab_messages:
        role = msg.get("role", "user")
        with st.chat_message(role):
            st.markdown(msg.get("content", ""))

    # Check if we have a pending response (from background processing)
    if st.session_state.get("agentlab_processing_response"):
        response_data = st.session_state.agentlab_processing_response
        st.session_state.agentlab_processing_response = None

        if response_data.get("error"):
            st.error(f"Agent error: {response_data['error']}")
        else:
            st.session_state.agentlab_messages.append({
                "role": "assistant",
                "content": response_data.get("content", "")
            })
        st.rerun()

    # Show processing indicator if query is in progress
    if st.session_state.get("agentlab_processing"):
        with st.chat_message("assistant"):
            st.markdown(
                """
                <div style="display: flex; align-items: center; gap: 0.75rem;">
                    <div class="processing-dot"></div>
                    <span style="color: #64748b;">Processing your request...</span>
                </div>
                <style>
                .processing-dot {
                    width: 12px;
                    height: 12px;
                    background: #6366f1;
                    border-radius: 50%;
                    animation: pulse 1.5s ease-in-out infinite;
                }
                @keyframes pulse {
                    0%, 100% { opacity: 0.4; transform: scale(0.8); }
                    50% { opacity: 1; transform: scale(1.2); }
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
        # Auto-refresh to check for completion
        import time
        time.sleep(0.5)
        st.rerun()

    # Helper function to run agent query in thread
    def _execute_agent_query(rt, query_text, history, agent_type_val):
        """Execute agent query and store result in session state."""
        try:
            if agent_type_val == "Deep":
                plan, answer, _events = run_deep_agent_query(rt, query_text, chat_history=history)
                content = f"**Plan**\n{plan or ''}\n\n**Answer**\n{answer}"
            else:
                answer, _events = run_agent_query(rt, query_text, chat_history=history)
                content = answer
            return {"content": content, "error": None}
        except Exception as exc:
            return {"content": "", "error": str(exc)}

    # Handle pending message
    pending = st.session_state.get("agentlab_pending_user_message")
    if pending and runtime is not None and not st.session_state.get("agentlab_processing"):
        st.session_state.agentlab_pending_user_message = None
        user_text = str(pending)
        st.session_state.agentlab_messages.append({"role": "user", "content": user_text})

        # Add to recent queries
        recent = st.session_state.get("agentlab_recent_queries", [])
        if user_text not in recent:
            recent.insert(0, user_text)
            st.session_state.agentlab_recent_queries = recent[:10]

        with st.chat_message("user"):
            st.markdown(user_text)

        # Execute query with status
        with st.chat_message("assistant"):
            with st.status("Processing...", expanded=True) as status:
                st.write("🤔 Analyzing your request...")
                cfg = st.session_state.agentlab_config
                agent_type = cfg.get("agent_type", "Normal")

                try:
                    st.write("🔧 Calling tools...")
                    if agent_type == "Deep":
                        plan, answer, _events = run_deep_agent_query(runtime, user_text, chat_history=st.session_state.agentlab_messages[:-1])
                        status.update(label="Complete!", state="complete")
                        st.markdown("**Plan**")
                        st.markdown(plan or "")
                        st.markdown("**Answer**")
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": f"Plan:\n{plan}\n\nAnswer:\n{answer}"})
                    else:
                        answer, _events = run_agent_query(runtime, user_text, chat_history=st.session_state.agentlab_messages[:-1])
                        status.update(label="Complete!", state="complete")
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": answer})
                except Exception as exc:
                    status.update(label="Error", state="error")
                    _render_exception("Agent error", exc)

        st.rerun()

    # Chat input
    prompt = st.chat_input("Ask in natural language...", disabled=(runtime is None or st.session_state.get("agentlab_processing")))
    if prompt and runtime is not None and not st.session_state.get("agentlab_processing"):
        st.session_state.agentlab_messages.append({"role": "user", "content": prompt})

        # Add to recent queries
        recent = st.session_state.get("agentlab_recent_queries", [])
        if prompt not in recent:
            recent.insert(0, prompt)
            st.session_state.agentlab_recent_queries = recent[:10]

        with st.chat_message("user"):
            st.markdown(prompt)

        # Execute query with status
        with st.chat_message("assistant"):
            with st.status("Processing...", expanded=True) as status:
                st.write("🤔 Analyzing your request...")
                cfg = st.session_state.agentlab_config
                agent_type = cfg.get("agent_type", "Normal")

                try:
                    st.write("🔧 Calling tools...")
                    if agent_type == "Deep":
                        plan, answer, _events = run_deep_agent_query(runtime, prompt, chat_history=st.session_state.agentlab_messages[:-1])
                        status.update(label="Complete!", state="complete")
                        st.markdown("**Plan**")
                        st.markdown(plan or "")
                        st.markdown("**Answer**")
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": f"Plan:\n{plan}\n\nAnswer:\n{answer}"})
                    else:
                        answer, _events = run_agent_query(runtime, prompt, chat_history=st.session_state.agentlab_messages[:-1])
                        status.update(label="Complete!", state="complete")
                        st.markdown(answer)
                        st.session_state.agentlab_messages.append({"role": "assistant", "content": answer})
                except Exception as exc:
                    status.update(label="Error", state="error")
                    _render_exception("Agent error", exc)

        st.rerun()


with tab_tools:
    st.markdown("### 🔧 Available Tools")

    if not selected_servers:
        st.info("Select at least one server in the sidebar to browse tools.")
    else:
        col_search, col_toggle = st.columns([3, 1])
        with col_search:
            search = st.text_input("🔍 Search tools", value="", placeholder="e.g. health, list, scan, deploy")
        with col_toggle:
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

        st.caption(f"**{len(tool_rows)}** tool(s) across {len(selected_servers)} server(s)")

        # Group by server
        tools_by_server: Dict[str, List[Dict[str, Any]]] = {}
        for r in tool_rows:
            srv = r.get("server", "unknown")
            if srv not in tools_by_server:
                tools_by_server[srv] = []
            tools_by_server[srv].append(r)

        for srv, tools in tools_by_server.items():
            with st.expander(f"**{srv}** ({len(tools)} tools)", expanded=True):
                for r in tools[:50]:
                    st.markdown(f"**`{r['name']}`** - {r.get('description', 'No description')[:100]}")
                    if r.get("args"):
                        st.caption(f"Args: `{r.get('args')}`")
                    if show_schema and r.get("_schema"):
                        st.code(json.dumps(r.get("_schema"), indent=2, default=str)[:3000], language="json")


with tab_prompts:
    st.markdown("### 📋 Server Prompts")

    if not selected_servers:
        st.info("Select at least one server in the sidebar.")
    else:
        pcol1, pcol2 = st.columns([1, 2])
        with pcol1:
            prompt_server = st.selectbox("Server", options=list(selected_servers), index=0)
        with pcol2:
            prompt_search = st.text_input("🔍 Search prompts", value="", placeholder="workflow, runbook...")

        url = _resolve_server_url(prompt_server)
        inv = _server_inventory(prompt_server, url)
        prompt_rows = [_prompt_to_row(prompt_server, p) for p in (inv.get("prompts") or [])]

        if prompt_search:
            q = prompt_search.lower().strip()
            prompt_rows = [r for r in prompt_rows if q in (r.get("name", "").lower()) or q in (r.get("description", "").lower()) or q in (r.get("title", "").lower())]

        st.caption(f"**{len(prompt_rows)}** prompt(s) on {prompt_server}")

        args_json = st.text_area(
            "Prompt arguments (JSON)",
            value="{}",
            height=80,
            help="Most prompts accept named args. Provide a JSON object here.",
        )

        for r in prompt_rows[:50]:
            display = r.get("title") or r.get("name")
            with st.expander(f"**{r['name']}** - {display}", expanded=False):
                if r.get("description"):
                    st.write(r.get("description"))
                if r.get("args"):
                    st.caption(f"Args: {r.get('args')}")

                b1, b2, b3 = st.columns(3)
                run_it = b1.button("▶️ Run", key=f"run_prompt:{r['server']}:{r['name']}")
                set_sys = b2.button("📝 Set System", key=f"sys_prompt:{r['server']}:{r['name']}")
                preview = b3.button("👁️ Preview", key=f"preview_prompt:{r['server']}:{r['name']}")

                parsed_args: Dict[str, Any] = {}
                try:
                    parsed_args = json.loads(args_json or "{}")
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
                            st.success("System prompt set!")
                        if run_it:
                            st.session_state.agentlab_pending_user_message = text
                            st.success("Queued for chat")
                    except Exception as exc:
                        _render_exception("Failed to run prompt", exc)

        st.divider()
        st.markdown("#### ✏️ Custom Prompt")
        custom = st.text_area("Write your own prompt", value="", height=100)
        if st.button("Send to Chat", use_container_width=True) and custom.strip():
            st.session_state.agentlab_pending_user_message = custom.strip()
            st.success("Queued for chat")


with tab_resources:
    st.markdown("### 📁 Resources")

    if not selected_servers:
        st.info("Select at least one server in the sidebar.")
    else:
        rcol1, rcol2 = st.columns([1, 2])
        with rcol1:
            res_server = st.selectbox("Server", options=list(selected_servers), index=0, key="res_server")
        with rcol2:
            res_search = st.text_input("🔍 Search resources", value="", placeholder="uri, name...")

        url = _resolve_server_url(res_server)
        inv = _server_inventory(res_server, url)
        res_rows = [_resource_to_row(res_server, r) for r in (inv.get("resources") or [])]

        if res_search:
            q = res_search.lower().strip()
            res_rows = [r for r in res_rows if q in r.get("uri", "").lower() or q in r.get("name", "").lower()]

        st.caption(f"**{len(res_rows)}** resource(s) on {res_server}")

        if not res_rows:
            st.markdown(
                """
                <div class="empty-state">
                    <div class="empty-state-icon">📁</div>
                    <div>No resources reported by this server</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            options = [r.get("uri") for r in res_rows if r.get("uri")]
            selected_uri = st.selectbox("Resource URI", options=options, index=0)

            if st.button("📖 Read Resource", type="primary") and selected_uri:
                client = get_mcp_client(res_server, url=_resolve_server_url(res_server), timeout=10.0, force_new=True, source="agent_lab")
                try:
                    payload = client.read_resource(selected_uri)
                    st.code(json.dumps(payload, indent=2, default=str)[:12000], language="json")
                except Exception as exc:
                    _render_exception("Failed to read resource", exc)


with tab_calls:
    st.markdown("### 📊 Tool Call History")
    _render_tool_calls(st.session_state.agentlab_tool_calls)


with tab_export:
    st.markdown("### 💾 Export Conversation")

    messages = st.session_state.agentlab_messages
    tool_calls = st.session_state.agentlab_tool_calls
    config = st.session_state.agentlab_config

    if not messages:
        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-state-icon">💬</div>
                <div>No conversation to export</div>
                <div style="font-size: 0.85rem; margin-top: 0.5rem;">
                    Start a conversation in the Chat tab first
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.info(f"**{len(messages)}** messages, **{len(tool_calls)}** tool calls")

        col_md, col_json = st.columns(2)

        with col_md:
            md_content = _export_conversation_markdown(messages, tool_calls)
            st.download_button(
                "📄 Download Markdown",
                data=md_content,
                file_name=f"agent_lab_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown",
                use_container_width=True,
            )

        with col_json:
            json_content = _export_conversation_json(messages, tool_calls, config)
            st.download_button(
                "📋 Download JSON",
                data=json_content,
                file_name=f"agent_lab_conversation_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                use_container_width=True,
            )

        st.divider()

        with st.expander("Preview Export", expanded=False):
            st.code(md_content[:5000], language="markdown")


# ─────────────────────────────────────────────────────────────────────────────
# FOOTER
# ─────────────────────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "**Tips:** Select a preset to quick-start, use Quick Actions for common queries, "
    "or type your own questions. The agent can chain multiple tools to complete complex tasks. "
    f"Session ID: `{st.session_state.agentlab_session_id}`"
)
