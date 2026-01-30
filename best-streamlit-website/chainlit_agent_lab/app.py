from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import chainlit as cl
from chainlit.input_widget import Select, Slider, Switch, Tags, TextInput

# Ensure the repo root (parent of this folder) is on sys.path so `import src.*` works.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.mcp_client import get_mcp_client, get_server_url

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


APP_TITLE = "Agent Lab (Chainlit)"


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


def _resolve_server_url(server_key: str) -> str:
    # Prefer StreamlitAppConfig (supports STREAMLIT_* overrides) when available.
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
                texts: List[str] = []
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


async def _to_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _get_cfg() -> Dict[str, Any]:
    cfg = cl.user_session.get("cfg")
    if not isinstance(cfg, dict):
        cfg = {}
    return cfg


def _set_cfg(cfg: Dict[str, Any]) -> None:
    cl.user_session.set("cfg", cfg)


def _get_runtime() -> Optional[AgentRuntime]:
    rt = cl.user_session.get("runtime")
    return rt if isinstance(rt, AgentRuntime) else None


def _reset_runtime() -> None:
    cl.user_session.set("runtime", None)
    cl.user_session.set("tool_events", [])
    cl.user_session.set("last_build_fingerprint", None)


async def _build_runtime_if_needed(*, force: bool = False) -> Tuple[Optional[AgentRuntime], Optional[str]]:
    cfg = _get_cfg()
    selected_servers = list(cfg.get("servers") or [])
    if not selected_servers:
        _reset_runtime()
        return None, "No MCP servers selected"

    fingerprint = _agent_config_fingerprint(cfg)
    if not force and cl.user_session.get("last_build_fingerprint") == fingerprint and _get_runtime() is not None:
        return _get_runtime(), None

    if not cfg.get("auto_build", True) and not force:
        return _get_runtime(), None

    tool_events: List[ToolCallEvent] = []
    cl.user_session.set("tool_events", tool_events)

    agent_type = str(cfg.get("agent_type") or "Normal")

    try:
        if agent_type == "RAG":
            rt = await _to_thread(
                build_rag_agent,
                selected_servers=selected_servers,
                model_name=str(cfg.get("model") or "llama3.2"),
                embedding_model=str(cfg.get("embedding_model") or "nomic-embed-text"),
                ollama_base_url=str(cfg.get("ollama_url") or "http://ollama:11434"),
                temperature=float(cfg.get("temperature") or 0.1),
                system_prompt=str(cfg.get("system_prompt") or ""),
                tool_call_events=tool_events,
                session_id=str(cl.user_session.get("session_id") or ""),
                source="chainlit_agent_lab",
            )
        elif agent_type == "Deep":
            rt = await _to_thread(
                build_deep_agent,
                selected_servers=selected_servers,
                model_name=str(cfg.get("model") or "llama3.2"),
                ollama_base_url=str(cfg.get("ollama_url") or "http://ollama:11434"),
                temperature=float(cfg.get("temperature") or 0.1),
                system_prompt=str(cfg.get("system_prompt") or ""),
                tool_call_events=tool_events,
                session_id=str(cl.user_session.get("session_id") or ""),
                source="chainlit_agent_lab",
            )
        else:
            rt = await _to_thread(
                build_normal_agent,
                selected_servers=selected_servers,
                model_name=str(cfg.get("model") or "llama3.2"),
                ollama_base_url=str(cfg.get("ollama_url") or "http://ollama:11434"),
                temperature=float(cfg.get("temperature") or 0.1),
                system_prompt=str(cfg.get("system_prompt") or ""),
                tool_call_events=tool_events,
                session_id=str(cl.user_session.get("session_id") or ""),
                source="chainlit_agent_lab",
            )

        cl.user_session.set("runtime", rt)
        cl.user_session.set("last_build_fingerprint", fingerprint)
        return rt, None
    except Exception as exc:
        _reset_runtime()
        cl.user_session.set("last_build_fingerprint", fingerprint)
        return None, f"{type(exc).__name__}: {exc}"


async def _render_new_tool_calls(events: List[ToolCallEvent], *, start_index: int) -> None:
    for ev in events[start_index:]:
        title = f"{ev.server}.{ev.tool}"
        async with cl.Step(name=title, type="tool") as step:
            step.input = ev.args
            if ev.ok:
                step.output = ev.result_preview
            else:
                step.output = ev.error or ev.result_preview or "Tool call failed"


async def _server_inventory(server_key: str) -> Dict[str, Any]:
    url = _resolve_server_url(server_key)
    client = get_mcp_client(server_key, url=url, timeout=10.0, force_new=True, source="chainlit_agent_lab")
    tools: List[Any] = []
    prompts: List[Any] = []
    resources: List[Any] = []
    try:
        tools = await _to_thread(client.list_tools, force_refresh=False)
    except Exception:
        tools = []
    try:
        prompts = await _to_thread(client.list_prompts, force_refresh=False)
    except Exception:
        prompts = []
    try:
        resources = await _to_thread(client.list_resources, force_refresh=False)
    except Exception:
        resources = []
    return {"tools": tools, "prompts": prompts, "resources": resources, "url": url}


async def _send_home() -> None:
    actions = [
        cl.Action(name="al_inventory", payload={}, label="Inventory", icon="list"),
        cl.Action(name="al_tools", payload={}, label="Tools", icon="wrench"),
        cl.Action(name="al_prompts", payload={}, label="Prompts", icon="sparkles"),
        cl.Action(name="al_resources", payload={}, label="Resources", icon="database"),
        cl.Action(name="al_system_prompt", payload={}, label="Generate system", icon="sparkle"),
        cl.Action(name="al_rebuild", payload={}, label="Rebuild agent", icon="refresh-cw"),
    ]
    await cl.Message(
        content=(
            f"# {APP_TITLE}\n"
            "A separate Chainlit deployment of Agent Lab.\n\n"
            "Use the settings panel to pick servers/model/agent type, then chat normally.\n\n"
            "Quick actions:"
        ),
        actions=actions,
    ).send()


@cl.on_chat_start
async def on_chat_start() -> None:
    cl.user_session.set("session_id", str(uuid.uuid4())[:8])

    servers = get_available_servers()
    server_keys = list(servers.keys())

    default_cfg: Dict[str, Any] = {
        "agent_type": "Normal",
        "servers": [k for k in ["kubernetes", "docker"] if k in server_keys],
        "model": os.environ.get("OLLAMA_MODEL", "llama3.2"),
        "embedding_model": "nomic-embed-text",
        "ollama_url": os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434"),
        "temperature": 0.1,
        "system_prompt": "",
        "auto_build": True,
    }
    _set_cfg(default_cfg)

    await cl.ChatSettings(
        [
            Select(
                id="agent_type",
                label="Agent type",
                values=["Normal", "RAG", "Deep"],
                initial_index=0,
            ),
            Tags(
                id="servers",
                label="MCP servers (tags)",
                initial=list(default_cfg["servers"]),
                description="Type server keys (e.g. docker, kubernetes, git, local, trivy, websearch).",
            ),
            TextInput(
                id="model",
                label="Ollama model",
                initial=str(default_cfg["model"]),
            ),
            TextInput(
                id="ollama_url",
                label="Ollama base URL",
                initial=str(default_cfg["ollama_url"]),
            ),
            Slider(
                id="temperature",
                label="Temperature",
                initial=float(default_cfg["temperature"]),
                min=0.0,
                max=1.0,
                step=0.1,
            ),
            Switch(
                id="auto_build",
                label="Auto-build agent",
                initial=True,
            ),
            TextInput(
                id="system_prompt",
                label="System prompt (single line; use /system for long text)",
                initial="",
                placeholder="Optional",
            ),
        ]
    ).send()

    await _send_home()

    rt, err = await _build_runtime_if_needed(force=True)
    if err:
        await cl.Message(content=f"Agent not ready: {err}").send()
    elif rt is not None:
        await cl.Message(content=f"Agent ready with servers: {', '.join(rt.selected_servers)}").send()


@cl.on_settings_update
async def on_settings_update(settings: Dict[str, Any]) -> None:
    cfg = _get_cfg()

    # Normalize server tags -> only known keys, but keep unknown for power users.
    known = set(get_available_servers().keys())
    servers = settings.get("servers")
    if isinstance(servers, list):
        normalized = [str(s).strip() for s in servers if str(s).strip()]
        # Keep known first for nicer UX.
        normalized = [s for s in normalized if s in known] + [s for s in normalized if s not in known]
        cfg["servers"] = normalized

    for k in ["agent_type", "model", "ollama_url", "temperature", "system_prompt", "auto_build"]:
        if k in settings:
            cfg[k] = settings[k]

    _set_cfg(cfg)

    rt, err = await _build_runtime_if_needed(force=False)
    if err:
        await cl.Message(content=f"Agent build error: {err}").send()
    elif rt is not None:
        await cl.Message(content=f"Updated agent: {rt.model_name} on {', '.join(rt.selected_servers)}").send()


@cl.action_callback("al_rebuild")
async def al_rebuild(action: cl.Action) -> None:
    await action.remove()
    rt, err = await _build_runtime_if_needed(force=True)
    if err:
        await cl.Message(content=f"Rebuild failed: {err}").send()
    else:
        await cl.Message(content="Rebuilt agent for current settings.").send()


@cl.action_callback("al_inventory")
async def al_inventory(action: cl.Action) -> None:
    await action.remove()
    cfg = _get_cfg()
    servers = list(cfg.get("servers") or [])
    if not servers:
        await cl.Message(content="No servers selected.").send()
        return

    lines: List[str] = ["## Inventory"]
    for srv in servers:
        try:
            inv = await _server_inventory(srv)
            tools = inv.get("tools") or []
            prompts = inv.get("prompts") or []
            resources = inv.get("resources") or []
            lines.append(
                f"- **{srv}** @ `{inv.get('url')}`: {len(tools)} tools, {len(prompts)} prompts, {len(resources)} resources"
            )
        except Exception as exc:
            lines.append(f"- **{srv}**: error: {type(exc).__name__}: {exc}")

    await cl.Message(content="\n".join(lines)).send()


async def _send_tools(server: str, *, query: str = "") -> None:
    inv = await _server_inventory(server)
    tools = inv.get("tools") or []
    rows: List[Dict[str, Any]] = []
    for t in tools:
        if isinstance(t, dict):
            name = t.get("name")
            desc = t.get("description")
            schema = t.get("inputSchema")
        else:
            name = getattr(t, "name", None)
            desc = getattr(t, "description", None)
            schema = getattr(t, "inputSchema", None)
        if not name:
            continue
        if query and query.lower() not in str(name).lower() and query.lower() not in str(desc or "").lower():
            continue
        rows.append({"name": name, "description": desc, "inputSchema": schema})

    await cl.Message(
        content=f"## Tools on **{server}** ({len(rows)})",
        elements=[cl.Json(name="tools.json", content=rows[:250])],
    ).send()


@cl.action_callback("al_tools")
async def al_tools(action: cl.Action) -> None:
    await action.remove()
    cfg = _get_cfg()
    servers = list(cfg.get("servers") or [])
    if not servers:
        await cl.Message(content="No servers selected.").send()
        return

    # Show per-server buttons.
    actions = [cl.Action(name="al_tools_server", payload={"server": s}, label=s, icon="wrench") for s in servers]
    await cl.Message(content="Pick a server to list tools:", actions=actions).send()


@cl.action_callback("al_tools_server")
async def al_tools_server(action: cl.Action) -> None:
    server = str((action.payload or {}).get("server") or "").strip()
    await action.remove()
    if not server:
        return
    await _send_tools(server)


async def _send_prompts(server: str, *, query: str = "") -> None:
    inv = await _server_inventory(server)
    prompts = inv.get("prompts") or []
    rows: List[Dict[str, Any]] = []
    for p in prompts:
        if isinstance(p, dict):
            name = p.get("name")
            title = p.get("title")
            desc = p.get("description")
            args = p.get("arguments") or p.get("inputSchema")
        else:
            name = getattr(p, "name", None)
            title = getattr(p, "title", None)
            desc = getattr(p, "description", None)
            args = getattr(p, "arguments", None)
        if not name:
            continue
        hay = f"{name} {title or ''} {desc or ''}".lower()
        if query and query.lower() not in hay:
            continue
        rows.append({"name": name, "title": title, "description": desc, "args": args})

    actions = [
        cl.Action(name="al_prompt_choose", payload={"server": server, "prompt": r["name"]}, label=r["name"], icon="sparkles")
        for r in rows[:30]
    ]

    await cl.Message(
        content=(
            f"## Prompts on **{server}** ({len(rows)})\n"
            "Select one below (top 30 shown). Then you can preview/run/set-system."
        ),
        actions=actions if actions else None,
        elements=[cl.Json(name="prompts.json", content=rows[:250])],
    ).send()


@cl.action_callback("al_prompts")
async def al_prompts(action: cl.Action) -> None:
    await action.remove()
    cfg = _get_cfg()
    servers = list(cfg.get("servers") or [])
    if not servers:
        await cl.Message(content="No servers selected.").send()
        return

    actions = [cl.Action(name="al_prompts_server", payload={"server": s}, label=s, icon="sparkles") for s in servers]
    await cl.Message(content="Pick a server to list prompts:", actions=actions).send()


@cl.action_callback("al_prompts_server")
async def al_prompts_server(action: cl.Action) -> None:
    server = str((action.payload or {}).get("server") or "").strip()
    await action.remove()
    if not server:
        return
    await _send_prompts(server)


@cl.action_callback("al_prompt_choose")
async def al_prompt_choose(action: cl.Action) -> None:
    payload = action.payload or {}
    server = str(payload.get("server") or "").strip()
    prompt_name = str(payload.get("prompt") or "").strip()
    await action.remove()
    if not server or not prompt_name:
        return

    actions = [
        cl.Action(name="al_prompt_preview", payload={"server": server, "prompt": prompt_name}, label="Preview", icon="eye"),
        cl.Action(name="al_prompt_run", payload={"server": server, "prompt": prompt_name}, label="Run → chat", icon="play"),
        cl.Action(name="al_prompt_set_system", payload={"server": server, "prompt": prompt_name}, label="Set as system", icon="shield"),
    ]
    await cl.Message(content=f"Selected prompt **{server}.{prompt_name}**. Choose an action:", actions=actions).send()


async def _get_prompt_text(server: str, prompt_name: str, args: Dict[str, Any]) -> str:
    raise RuntimeError("Use _get_prompt_text_sync via to_thread")


def _get_prompt_text_sync(server: str, prompt_name: str, args: Dict[str, Any]) -> str:
    url = _resolve_server_url(server)
    client = get_mcp_client(server, url=url, timeout=12.0, force_new=True, source="chainlit_agent_lab")
    payload = client.get_prompt(prompt_name, args)
    text = _extract_prompt_text(payload)
    return text or json.dumps(payload, indent=2, default=str)


async def _ask_prompt_args() -> Dict[str, Any]:
    res = await cl.AskUserMessage(
        content="Provide prompt arguments as JSON (or `{}`)",
        timeout=120,
    ).send()
    raw = (res.get("output") if isinstance(res, dict) else None) or "{}"
    try:
        parsed = json.loads(str(raw))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


@cl.action_callback("al_prompt_preview")
async def al_prompt_preview(action: cl.Action) -> None:
    payload = action.payload or {}
    server = str(payload.get("server") or "").strip()
    prompt_name = str(payload.get("prompt") or "").strip()
    await action.remove()
    args = await _ask_prompt_args()
    text = await _to_thread(_get_prompt_text_sync, server, prompt_name, args)
    await cl.Message(content=f"### Preview: {server}.{prompt_name}\n\n```\n{text[:8000]}\n```"
    ).send()


@cl.action_callback("al_prompt_set_system")
async def al_prompt_set_system(action: cl.Action) -> None:
    payload = action.payload or {}
    server = str(payload.get("server") or "").strip()
    prompt_name = str(payload.get("prompt") or "").strip()
    await action.remove()
    args = await _ask_prompt_args()
    text = await _to_thread(_get_prompt_text_sync, server, prompt_name, args)
    cfg = _get_cfg()
    cfg["system_prompt"] = text
    _set_cfg(cfg)
    await cl.Message(content="System prompt set from MCP prompt.").send()
    await _build_runtime_if_needed(force=True)


@cl.action_callback("al_prompt_run")
async def al_prompt_run(action: cl.Action) -> None:
    payload = action.payload or {}
    server = str(payload.get("server") or "").strip()
    prompt_name = str(payload.get("prompt") or "").strip()
    await action.remove()
    args = await _ask_prompt_args()
    text = await _to_thread(_get_prompt_text_sync, server, prompt_name, args)
    # Re-inject as a user message (single source of truth in the handler).
    await _handle_user_message(text)


@cl.action_callback("al_resources")
async def al_resources(action: cl.Action) -> None:
    await action.remove()
    cfg = _get_cfg()
    servers = list(cfg.get("servers") or [])
    if not servers:
        await cl.Message(content="No servers selected.").send()
        return

    actions = [cl.Action(name="al_resources_server", payload={"server": s}, label=s, icon="database") for s in servers]
    await cl.Message(content="Pick a server to list resources:", actions=actions).send()


@cl.action_callback("al_resources_server")
async def al_resources_server(action: cl.Action) -> None:
    server = str((action.payload or {}).get("server") or "").strip()
    await action.remove()
    if not server:
        return

    inv = await _server_inventory(server)
    resources = inv.get("resources") or []
    rows: List[Dict[str, Any]] = []
    for r in resources:
        if isinstance(r, dict):
            uri = r.get("uri")
            name = r.get("name")
            desc = r.get("description")
            mime = r.get("mimeType") or r.get("mime_type")
        else:
            uri = getattr(r, "uri", None)
            name = getattr(r, "name", None)
            desc = getattr(r, "description", None)
            mime = getattr(r, "mimeType", None)
        if not uri:
            continue
        rows.append({"uri": uri, "name": name, "description": desc, "mime": mime})

    actions = [
        cl.Action(name="al_resource_read", payload={"server": server, "uri": row["uri"]}, label=row["uri"], icon="file-text")
        for row in rows[:30]
    ]

    await cl.Message(
        content=f"## Resources on **{server}** ({len(rows)})\nSelect one to read (top 30 shown).",
        actions=actions if actions else None,
        elements=[cl.Json(name="resources.json", content=rows[:250])],
    ).send()


@cl.action_callback("al_resource_read")
async def al_resource_read(action: cl.Action) -> None:
    payload = action.payload or {}
    server = str(payload.get("server") or "").strip()
    uri = str(payload.get("uri") or "").strip()
    await action.remove()
    if not server or not uri:
        return

    url = _resolve_server_url(server)
    client = get_mcp_client(server, url=url, timeout=12.0, force_new=True, source="chainlit_agent_lab")
    data = await _to_thread(client.read_resource, uri)
    await cl.Message(
        content=f"### Resource: {server} `{uri}`",
        elements=[cl.Json(name="resource.json", content=data)],
    ).send()


async def _generate_system_prompt_with_ollama(*, selected_servers: List[str]) -> str:
    cfg = _get_cfg()
    ollama_url = str(cfg.get("ollama_url") or os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")).rstrip("/")
    model = str(cfg.get("model") or os.environ.get("OLLAMA_MODEL", "llama3.2"))
    temperature = float(cfg.get("temperature") or 0.1)

    # Build a compact capability summary.
    tool_lines: List[str] = []
    prompt_lines: List[str] = []
    resource_lines: List[str] = []

    for srv in selected_servers[:10]:
        inv = await _server_inventory(srv)

        tools = inv.get("tools") or []
        for t in tools[:40]:
            if isinstance(t, dict):
                name = t.get("name")
                desc = t.get("description")
            else:
                name = getattr(t, "name", None)
                desc = getattr(t, "description", None)
            if name:
                tool_lines.append(f"- {srv}.{name}: {desc or ''}")

        prompts = inv.get("prompts") or []
        for p in prompts[:40]:
            if isinstance(p, dict):
                name = p.get("name")
                desc = p.get("description")
            else:
                name = getattr(p, "name", None)
                desc = getattr(p, "description", None)
            if name:
                prompt_lines.append(f"- {srv}.{name}: {desc or ''}")

        resources = inv.get("resources") or []
        for r in resources[:20]:
            if isinstance(r, dict):
                uri = r.get("uri")
                mime = r.get("mimeType") or r.get("mime_type")
            else:
                uri = getattr(r, "uri", None)
                mime = getattr(r, "mimeType", None)
            if uri:
                resource_lines.append(f"- {srv}: {uri} ({mime or ''})")

    capabilities = "\n".join(
        tool_lines[:250]
        + (["\nPrompts:"] + prompt_lines[:150] if prompt_lines else [])
        + (["\nResources:"] + resource_lines[:80] if resource_lines else [])
    )

    prompt_text = (
        "Write a strong SYSTEM PROMPT for a tool-using DevOps assistant. "
        "The agent can call MCP tools from the selected servers below. "
        "The system prompt should: (1) explain tool-use rules, (2) require planning + verification, "
        "(3) require redaction of secrets, (4) define response structure. "
        "Return ONLY the system prompt text.\n\n"
        f"Selected servers: {', '.join(selected_servers)}\n\n"
        f"Capabilities:\n{capabilities}\n"
    )

    # Prefer LangChain ChatOllama if present.
    try:
        from langchain_ollama.chat_models import ChatOllama
        from langchain_core.messages import HumanMessage

        llm = ChatOllama(model=model, base_url=ollama_url, temperature=float(temperature))
        msg = await _to_thread(llm.invoke, [HumanMessage(content=prompt_text)])
        return str(getattr(msg, "content", "") or "").strip()
    except Exception:
        pass

    # HTTP fallback.
    import requests

    resp = requests.post(
        ollama_url + "/api/generate",
        json={"model": model, "prompt": prompt_text, "stream": False, "options": {"temperature": float(temperature)}},
        timeout=20,
    )
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return str(data.get("response") or "").strip()


@cl.action_callback("al_system_prompt")
async def al_system_prompt(action: cl.Action) -> None:
    await action.remove()
    cfg = _get_cfg()
    servers = list(cfg.get("servers") or [])
    if not servers:
        await cl.Message(content="No servers selected.").send()
        return

    async with cl.Step(name="Generate system prompt", type="tool"):
        text = await _generate_system_prompt_with_ollama(selected_servers=servers)

    if not text:
        await cl.Message(content="No system prompt returned.").send()
        return

    cfg["system_prompt"] = text
    _set_cfg(cfg)
    await cl.Message(content="System prompt generated and applied.").send()
    await _build_runtime_if_needed(force=True)


async def _handle_user_message(user_text: str) -> None:
    cfg = _get_cfg()
    rt, err = await _build_runtime_if_needed(force=False)
    if err or rt is None:
        await cl.Message(content=f"Agent not ready: {err or 'unknown error'}").send()
        return

    history: List[Dict[str, str]] = cl.user_session.get("history") or []
    if not isinstance(history, list):
        history = []

    tool_events: List[ToolCallEvent] = cl.user_session.get("tool_events") or []
    if not isinstance(tool_events, list):
        tool_events = []

    events_before = len(tool_events)

    agent_type = str(cfg.get("agent_type") or "Normal")

    async with cl.Step(name="Agent", type="run"):
        try:
            if agent_type == "Deep":
                plan, answer, _events = await _to_thread(run_deep_agent_query, rt, user_text, chat_history=history)
                await _render_new_tool_calls(tool_events, start_index=events_before)
                content = f"**Plan**\n{plan or ''}\n\n**Answer**\n{answer}"
                await cl.Message(content=content).send()
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": content})
            else:
                answer, _events = await _to_thread(run_agent_query, rt, user_text, chat_history=history)
                await _render_new_tool_calls(tool_events, start_index=events_before)
                await cl.Message(content=answer).send()
                history.append({"role": "user", "content": user_text})
                history.append({"role": "assistant", "content": answer})
        except Exception as exc:
            await cl.Message(content=f"Agent error: {type(exc).__name__}: {exc}").send()

    cl.user_session.set("history", history)


@cl.on_message
async def on_message(message: cl.Message) -> None:
    text = (message.content or "").strip()
    if not text:
        return

    # Slash commands for power users.
    if text.startswith("/help"):
        await cl.Message(
            content=(
                "### Commands\n"
                "- `/help`\n"
                "- `/home`\n"
                "- `/rebuild`\n"
                "- `/inventory`\n"
                "- `/tools <server> [query]`\n"
                "- `/prompts <server> [query]`\n"
                "- `/resources <server>`\n"
                "- `/system <text>` (sets system prompt + rebuild)\n"
            )
        ).send()
        return

    if text.startswith("/home"):
        await _send_home()
        return

    if text.startswith("/rebuild"):
        rt, err = await _build_runtime_if_needed(force=True)
        await cl.Message(content=("Rebuilt." if rt is not None and not err else f"Rebuild failed: {err}")).send()
        return

    if text.startswith("/inventory"):
        cfg = _get_cfg()
        servers = list(cfg.get("servers") or [])
        if not servers:
            await cl.Message(content="No servers selected.").send()
            return
        lines: List[str] = ["## Inventory"]
        for srv in servers:
            try:
                inv = await _server_inventory(srv)
                tools = inv.get("tools") or []
                prompts = inv.get("prompts") or []
                resources = inv.get("resources") or []
                lines.append(
                    f"- **{srv}** @ `{inv.get('url')}`: {len(tools)} tools, {len(prompts)} prompts, {len(resources)} resources"
                )
            except Exception as exc:
                lines.append(f"- **{srv}**: error: {type(exc).__name__}: {exc}")

        await cl.Message(content="\n".join(lines)).send()
        return

    if text.startswith("/system "):
        cfg = _get_cfg()
        cfg["system_prompt"] = text[len("/system ") :].strip()
        _set_cfg(cfg)
        await cl.Message(content="System prompt set. Rebuilding agent...").send()
        await _build_runtime_if_needed(force=True)
        return

    if text.startswith("/tools "):
        parts = text.split(maxsplit=2)
        if len(parts) >= 2:
            server = parts[1]
            query = parts[2] if len(parts) == 3 else ""
            await _send_tools(server, query=query)
            return

    if text.startswith("/prompts "):
        parts = text.split(maxsplit=2)
        if len(parts) >= 2:
            server = parts[1]
            query = parts[2] if len(parts) == 3 else ""
            await _send_prompts(server, query=query)
            return

    if text.startswith("/resources "):
        parts = text.split(maxsplit=2)
        if len(parts) >= 2:
            server = parts[1]
            await al_resources_server(cl.Action(name="al_resources_server", payload={"server": server}, label=""))
            return

    await _handle_user_message(text)
