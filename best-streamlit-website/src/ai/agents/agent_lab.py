"""Agent Lab: sample LangChain/LangGraph agents wired to MCP servers.

This module provides three ready-to-use agent styles:
- Normal MCP tool agent (ReAct)
- RAG + MCP tool agent (adds a retriever tool over local repo files)
- "Deep" agent (plan -> tool-use -> answer)

The implementations intentionally reuse the repo's existing MCP integration
stack (langchain-mcp-adapters + LangGraph create_react_agent).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain_ollama.chat_models import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
try:
    # Newer LangGraph exposes create_agent()
    from langgraph.prebuilt import create_agent as _lg_create_agent  # type: ignore
except Exception:  # noqa: BLE001
    _lg_create_agent = None

from langgraph.prebuilt import create_react_agent

from src.mcp_log import create_logging_interceptor
from src.ai.agents.dynamic_agent import MCP_SERVERS
from src.streamlit_config import StreamlitAppConfig
from src.ai.mcp_specs import build_server_specs
from src.ai.agents.skills import (
    load_skill,
    list_available_skills,
    namespace_tools,
    extract_streamlit_code,
    validate_streamlit_code,
    wrap_streamlit_code,
    STREAMLIT_DEVELOPER_SKILL,
)


@dataclass
class ToolCallEvent:
    server: str
    tool: str
    args: Dict[str, Any]
    started_at: str
    finished_at: Optional[str] = None
    ok: bool = False
    result_preview: str = ""
    error: Optional[str] = None


@dataclass
class AgentRuntime:
    """Common runtime used by all sample agents."""

    client: MultiServerMCPClient
    tools: List[Any]
    agent: Any  # LangGraph compiled agent
    tool_call_events: List[ToolCallEvent]
    selected_servers: List[str]
    system_prompt: str
    model_name: str

    ollama_base_url: str = "http://ollama:11434"
    temperature: float = 0.1

    # Optional for Deep/RAG
    last_plan: Optional[str] = None
    rag_enabled: bool = False
    rag_index_summary: Optional[str] = None

    # Optional: LangChain deep agent executor/runnable (when available)
    deep_agent: Optional[Any] = None


def get_available_servers() -> Dict[str, Dict[str, Any]]:
    """Expose the same MCP server catalog as the Dynamic Agent Builder."""

    return MCP_SERVERS.copy()


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _iter_leaf_exceptions(exc: BaseException) -> Iterable[BaseException]:
    sub = getattr(exc, "exceptions", None)
    if isinstance(sub, list) and sub:
        for e in sub:
            if isinstance(e, BaseException):
                yield from _iter_leaf_exceptions(e)
        return
    yield exc


def _format_exception_summary(exc: BaseException, *, max_leaves: int = 6) -> str:
    leaves = list(_iter_leaf_exceptions(exc))
    head = f"{type(exc).__name__}: {exc}"
    if len(leaves) <= 1:
        return head

    lines = [head, "Underlying exceptions:"]
    for i, leaf in enumerate(leaves[:max_leaves], start=1):
        msg = str(leaf).strip() or repr(leaf)
        lines.append(f"{i}. {type(leaf).__name__}: {msg}")
    if len(leaves) > max_leaves:
        lines.append(f"... ({len(leaves) - max_leaves} more)")
    return "\n".join(lines)


def _get_transport(config: Any) -> str:
    """Normalize transport values into what langchain-mcp-adapters expects."""

    t = (getattr(config, "mcp_transport", "stdio") or "stdio").lower().strip()
    # FastMCP HTTP transport maps to streamable-http protocol on the /mcp endpoint.
    if t in {"http", "streamable-http", "streamable_http"}:
        return "streamable-http"
    return t


def _build_connection(server_key: str, config: Any) -> Dict[str, Any]:
    """Build a MultiServerMCPClient connection dict for one server."""

    transport = _get_transport(config)
    module = MCP_SERVERS[server_key]["module"]

    if transport == "stdio":
        env = {**os.environ}
        if hasattr(config, "to_env_overrides"):
            env.update(config.to_env_overrides())

        repo_root = str(Path(__file__).resolve().parents[4])
        env["PYTHONPATH"] = repo_root + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")

        return {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", module],
            "env": env,
        }

    url = (getattr(config, "mcp_url", "") or "").strip()
    if not url:
        upper = server_key.upper()
        raise ValueError(
            f"{server_key} MCP URL is empty. Set {upper}_MCP_URL (server) or STREAMLIT_{upper}_MCP_URL (UI), "
            "or choose stdio transport."
        )
    if transport == "streamable-http":
        base = url.rstrip("/")
        if not base.endswith("/mcp"):
            url = base + "/mcp"
    return {"transport": transport, "url": url}


def _transport_to_langchain(transport: str) -> str:
    t = (transport or "").lower().strip()
    if t in {"http", "streamable-http", "streamable_http"}:
        return "streamable-http"
    return t or "stdio"


def _build_connections(selected_servers: List[str]) -> Dict[str, Dict[str, Any]]:
    """Build connections using StreamlitAppConfig when possible.

    This matters because Streamlit config normalizes streamable-http URLs to include
    the /mcp suffix and supports STREAMLIT_* override env vars.
    """

    specs: Dict[str, Any] = {}
    try:
        cfg = StreamlitAppConfig.load()
        specs = build_server_specs(cfg)
    except Exception:
        specs = {}

    connections: Dict[str, Dict[str, Any]] = {}
    for server_key in selected_servers:
        # Prefer Streamlit-normalized specs when available.
        if server_key in specs:
            spec = specs[server_key]
            t = _transport_to_langchain(getattr(spec, "transport", ""))
            if t == "stdio":
                connections[server_key] = {
                    "transport": "stdio",
                    "command": getattr(spec, "python_executable", sys.executable),
                    "args": ["-m", getattr(spec, "module")],
                    "env": getattr(spec, "env", dict(os.environ)),
                }
            else:
                url = (getattr(spec, "url", "") or "").strip()
                if not url:
                    upper = server_key.upper()
                    raise ValueError(
                        f"{server_key} MCP URL is empty. Set STREAMLIT_{upper}_MCP_URL or {upper}_MCP_URL."
                    )
                connections[server_key] = {"transport": t, "url": url}
            continue

        # Fallback: use per-server config classes like the Dynamic Agent Builder.
        if server_key not in MCP_SERVERS:
            raise ValueError(f"Unknown server: {server_key}")
        cfg_cls = MCP_SERVERS[server_key]["config_class"]
        cfg = cfg_cls.from_env()
        connections[server_key] = _build_connection(server_key, cfg)

    return connections


def _default_system_prompt() -> str:
    return (
        "You are a helpful DevOps assistant. "
        "You can call tools provided by connected MCP servers when needed. "
        "Be explicit about what you did and what you found."
    )


def _deep_system_prompt() -> str:
    return (
        "You are a careful, senior DevOps assistant. "
        "Before using tools, write a short plan (3-6 bullets). "
        "Then use tools to validate assumptions and answer with evidence."
    )


def _create_langgraph_agent(llm: Any, tools: List[Any], system_prompt: str) -> Any:
    """Create an agent across LangGraph versions.

    - Some versions support create_react_agent(..., state_modifier=...)
    - Others use create_react_agent(..., prompt=ChatPromptTemplate)
    - Some expose create_agent(...)
    """

    # LangChain prompt templates treat `{...}` as format variables.
    # User-provided system prompts often include JSON examples like `{ "status": ... }`.
    # Escape braces to force literal rendering and avoid runtime errors like:
    # "Input to ChatPromptTemplate is missing variables {'status'}".
    safe_system_prompt = (system_prompt or "").replace("{", "{{").replace("}", "}}")

    if _lg_create_agent is not None:
        try:
            return _lg_create_agent(llm, tools, system_prompt=safe_system_prompt)  # type: ignore[call-arg]
        except TypeError:
            # Fall through to create_react_agent compat.
            pass

    try:
        sig = inspect.signature(create_react_agent)
        if "state_modifier" in sig.parameters:
            return create_react_agent(llm, tools, state_modifier=safe_system_prompt)
        if "prompt" in sig.parameters:
            from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

            prompt = ChatPromptTemplate.from_messages(
                [
                    SystemMessage(content=safe_system_prompt),
                    MessagesPlaceholder("messages"),
                ]
            )
            return create_react_agent(llm, tools, prompt=prompt)
    except Exception:
        # Last resort: call without any system prompt customization.
        pass

    return create_react_agent(llm, tools)


def _try_build_langchain_deep_agent(llm: Any, tools: List[Any], system_prompt: str) -> Optional[Any]:
    """Best-effort integration with LangChain's deep agent factory.

    The exact import path/signature varies across LangChain versions, so this
    function tries a few common locations and adapts kwargs by signature.
    """

    try:
        import importlib
    except Exception:
        return None

    candidates = [
        ("langchain.agents", "deep_agent"),
        ("langchain.agents", "create_deep_agent"),
        ("langchain.agents.deep_agent", "deep_agent"),
        ("langchain.agents.deep_agent", "create_deep_agent"),
    ]

    value_by_param = {
        "llm": llm,
        "model": llm,
        "chat_model": llm,
        "tools": tools,
        "toolkit": tools,
        "system_prompt": system_prompt,
        "instructions": system_prompt,
    }

    for module_name, attr in candidates:
        try:
            mod = importlib.import_module(module_name)
            factory = getattr(mod, attr, None)
            if not callable(factory):
                continue

            sig = None
            try:
                sig = inspect.signature(factory)
            except Exception:
                sig = None

            if sig is None:
                # Best guess call
                try:
                    return factory(llm=llm, tools=tools, system_prompt=system_prompt)
                except Exception:
                    continue

            kwargs: Dict[str, Any] = {}
            for name, param in sig.parameters.items():
                if name in value_by_param:
                    kwargs[name] = value_by_param[name]
                    continue
                if param.kind in (param.VAR_KEYWORD, param.VAR_POSITIONAL):
                    continue

            try:
                return factory(**kwargs)
            except Exception:
                continue
        except Exception:
            continue

    return None


def _build_tool_logging_interceptor(
    tool_call_events: List[ToolCallEvent],
    *,
    source: str,
    session_id: Optional[str],
) -> List[Any]:
    async def memory_interceptor(request: MCPToolCallRequest, handler):
        started = _utc_now()

        safe_args: Dict[str, Any] = {}
        try:
            safe_args = dict(request.args or {})
            for k in ["_client_token", "password", "token", "api_token", "secret"]:
                if k in safe_args:
                    safe_args[k] = "***redacted***"
        except Exception:
            safe_args = {}

        event = ToolCallEvent(
            server=str(getattr(request, "server_name", "")),
            tool=str(getattr(request, "name", "")),
            args=safe_args,
            started_at=started,
        )

        try:
            result = await handler(request)
            event.ok = True
            event.result_preview = str(getattr(result, "content", result))[:1000]
            return result
        except Exception as exc:  # noqa: BLE001
            event.ok = False
            event.error = str(exc)
            event.result_preview = f"ERROR: {exc}"[:1000]
            raise
        finally:
            event.finished_at = _utc_now()
            tool_call_events.append(event)

    db_interceptor = create_logging_interceptor(source=source, session_id=session_id)
    return [memory_interceptor, db_interceptor]


def _messages_from_history(chat_history: Optional[List[Dict[str, str]]]) -> List[Any]:
    messages: List[Any] = []
    for msg in (chat_history or []):
        role = (msg.get("role") or "user").lower().strip()
        content = msg.get("content", "")
        if role == "assistant":
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))
    return messages


def build_normal_agent(
    *,
    selected_servers: List[str],
    model_name: str = "llama3.2",
    ollama_base_url: str = "http://ollama:11434",
    temperature: float = 0.1,
    system_prompt: str = "",
    enable_rag: bool = True,
    enable_skills: bool = True,
    enable_streamlit_developer: bool = True,
    namespace_mcp_tools: bool = True,
    embedding_model: str = "nomic-embed-text",
    repo_root: Optional[Path] = None,
    tool_call_events: Optional[List[ToolCallEvent]] = None,
    session_id: Optional[str] = None,
    source: str = "agent_lab",
) -> AgentRuntime:
    """Normal ReAct agent with tools from selected MCP servers.

    By default, this also adds a lightweight repo-context retrieval tool
    (vector-based when available, lexical fallback otherwise).

    Args:
        selected_servers: List of MCP server keys to enable
        model_name: Ollama model name
        ollama_base_url: Ollama server URL
        temperature: LLM temperature
        system_prompt: Custom system prompt (optional)
        enable_rag: Enable RAG retrieval tool (default True)
        enable_skills: Enable skill loading tools (default True)
        enable_streamlit_developer: Enable built-in Streamlit developer mode (default True)
        namespace_mcp_tools: Prefix MCP tools with server namespace (default True)
        embedding_model: Embedding model for RAG
        repo_root: Root path for RAG indexing
        tool_call_events: List to collect tool call events
        session_id: Session ID for logging
        source: Source identifier for logging
    """

    if not selected_servers:
        raise ValueError("At least one MCP server must be selected")

    tool_call_events = tool_call_events if tool_call_events is not None else []

    interceptors = _build_tool_logging_interceptor(tool_call_events, source=source, session_id=session_id)

    # Tool discovery across multiple servers can raise ExceptionGroup when one
    # server is down. Prefer a partial-success path to keep the agent usable.
    effective_servers = list(selected_servers)
    tools_by_server: Dict[str, List[Any]] = {}

    try:
        connections = _build_connections(effective_servers)
        client = MultiServerMCPClient(connections, tool_interceptors=interceptors)
        tools = asyncio.run(client.get_tools())

        # If namespacing enabled, try to identify tools by server
        if namespace_mcp_tools:
            # Tools from MultiServerMCPClient may have server info in metadata
            for t in tools:
                # Try to extract server from tool name pattern or metadata
                server_key = None
                tool_name = getattr(t, 'name', '')
                for srv in effective_servers:
                    if tool_name.startswith(f"{srv}_") or tool_name.startswith(f"{srv}."):
                        server_key = srv
                        break
                if server_key:
                    if server_key not in tools_by_server:
                        tools_by_server[server_key] = []
                    tools_by_server[server_key].append(t)

    except BaseException as exc:  # noqa: BLE001
        failures: Dict[str, str] = {}
        ok_servers: List[str] = []

        for srv in selected_servers:
            try:
                one_conn = _build_connections([srv])
                one_client = MultiServerMCPClient(one_conn, tool_interceptors=interceptors)
                srv_tools = asyncio.run(one_client.get_tools())
                ok_servers.append(srv)
                if namespace_mcp_tools:
                    tools_by_server[srv] = list(srv_tools)
            except BaseException as sub_exc:  # noqa: BLE001
                failures[srv] = _format_exception_summary(sub_exc)

        if not ok_servers:
            details = "\n".join([f"- {srv}: {msg}" for srv, msg in failures.items()])
            raise RuntimeError(
                "Failed to connect to any selected MCP servers during tool discovery.\n" + details
            ) from exc

        effective_servers = ok_servers
        connections = _build_connections(effective_servers)
        client = MultiServerMCPClient(connections, tool_interceptors=interceptors)
        try:
            tools = asyncio.run(client.get_tools())
        except BaseException as exc2:  # noqa: BLE001
            raise RuntimeError(_format_exception_summary(exc2)) from exc2

    llm = ChatOllama(model=model_name, base_url=ollama_base_url, temperature=temperature)

    # Build system prompt with Streamlit developer capability
    base_system = system_prompt.strip() or _default_system_prompt()

    if enable_streamlit_developer:
        streamlit_hint = (
            "\n\n## Streamlit Developer Mode\n"
            "You have built-in Streamlit development capabilities. When the user asks for UI components, "
            "dashboards, visualizations, or any Streamlit-related code, generate complete, runnable code.\n"
            "IMPORTANT: Wrap your Streamlit code in ```streamlit code blocks for automatic rendering.\n"
            "Example:\n```streamlit\nimport streamlit as st\nst.title('Hello')\n```\n"
        )
        base_system = base_system + streamlit_hint

    final_system = base_system

    # Apply namespace prefix to MCP tools if enabled
    combined_tools: List[Any] = []
    if namespace_mcp_tools and tools_by_server:
        for srv, srv_tools in tools_by_server.items():
            namespaced = namespace_tools(srv_tools, srv)
            combined_tools.extend(namespaced)
        # Add any tools not assigned to a server
        assigned_tools = set()
        for srv_tools in tools_by_server.values():
            for t in srv_tools:
                assigned_tools.add(id(t))
        for t in tools:
            if id(t) not in assigned_tools:
                combined_tools.append(t)
    else:
        combined_tools = list(tools)

    # Add skill tools if enabled
    if enable_skills:
        combined_tools.append(load_skill)
        combined_tools.append(list_available_skills)
    rag_summary: Optional[str] = None
    rag_enabled = False

    if enable_rag:
        root = repo_root or Path(__file__).resolve().parents[4]
        retriever = None
        rag_mode = "vector"
        try:
            retriever, rag_summary = _build_inmemory_retriever(
                root,
                ollama_base_url=ollama_base_url,
                embedding_model=embedding_model,
            )
        except Exception as rag_exc:  # noqa: BLE001
            rag_mode = "lexical"
            rag_summary = f"Lexical search fallback (RAG unavailable): {rag_exc}"

        @tool("retrieve_repo_context")
        def retrieve_repo_context(query: str) -> str:
            """Search this repo's code/docs and return relevant excerpts with file paths."""

            if rag_mode != "vector" or retriever is None:
                return _fallback_lexical_search(root, query)

            docs: List[Document]
            try:
                docs = retriever.invoke(query)  # type: ignore[assignment]
            except Exception:
                docs = retriever.get_relevant_documents(query)  # type: ignore[attr-defined]

            parts: List[str] = []
            for d in docs:
                path = (d.metadata or {}).get("path", "")
                snippet = (d.page_content or "").strip()
                if len(snippet) > 1200:
                    snippet = snippet[:1200] + "..."
                parts.append(f"FILE: {path}\n{snippet}")
            return "\n\n---\n\n".join(parts) if parts else "(no matches)"

        combined_tools.append(retrieve_repo_context)
        rag_enabled = True

        rag_hint = (
            "If the user's question relates to this codebase, first call retrieve_repo_context to gather relevant files."
        )
        if rag_mode != "vector":
            rag_hint += " (This session is using lexical search fallback.)"
        final_system = final_system + "\n\n" + rag_hint

    agent = _create_langgraph_agent(llm, combined_tools, final_system)

    return AgentRuntime(
        client=client,
        tools=combined_tools,
        agent=agent,
        tool_call_events=tool_call_events,
        selected_servers=list(effective_servers),
        system_prompt=final_system,
        model_name=model_name,
        ollama_base_url=ollama_base_url,
        temperature=temperature,
        rag_enabled=rag_enabled,
        rag_index_summary=rag_summary,
    )


async def _run_agent_query_async(
    runtime: AgentRuntime,
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[ToolCallEvent]]:
    """Async implementation of agent query execution."""
    messages = _messages_from_history(chat_history)
    messages.append(HumanMessage(content=query))

    events_before = len(runtime.tool_call_events)

    # Use ainvoke for async tool support
    result = await runtime.agent.ainvoke({"messages": messages})

    output = ""
    for msg in reversed(result.get("messages", []) or []):
        if isinstance(msg, AIMessage) and getattr(msg, "content", None):
            output = str(msg.content)
            break

    return output, runtime.tool_call_events[events_before:]


def run_agent_query(
    runtime: AgentRuntime,
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[ToolCallEvent]]:
    """Run agent query - handles async tools properly."""
    # Check if we're already in an event loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # We're in an async context, create a new thread to run the coroutine
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                _run_agent_query_async(runtime, query, chat_history)
            )
            return future.result()
    else:
        # No running loop, use asyncio.run directly
        return asyncio.run(_run_agent_query_async(runtime, query, chat_history))


def _iter_repo_files(repo_root: Path) -> Iterable[Path]:
    include_dirs = [
        repo_root / "best-streamlit-website" / "src",
        repo_root / "best-streamlit-website" / "pages",
    ]
    include_files = [repo_root / "README.md", repo_root / "best-streamlit-website" / "README.md"]

    for f in include_files:
        if f.exists() and f.is_file():
            yield f

    for d in include_dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if not p.is_file():
                continue
            if any(part.startswith("__pycache__") for part in p.parts):
                continue
            if p.suffix.lower() not in {".py", ".md", ".txt", ".toml", ".yml", ".yaml", ".json", ".css"}:
                continue
            yield p


def _load_documents(repo_root: Path, *, max_file_bytes: int = 250_000) -> List[Document]:
    docs: List[Document] = []
    for p in _iter_repo_files(repo_root):
        try:
            if p.stat().st_size > max_file_bytes:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        docs.append(Document(page_content=text, metadata={"path": rel}))
    return docs


def _split_documents(docs: List[Document]) -> List[Document]:
    # Prefer the new splitters package when available; fallback for older LangChain.
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter  # type: ignore

        splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150)
        return splitter.split_documents(docs)
    except Exception:
        try:
            from langchain.text_splitter import RecursiveCharacterTextSplitter  # type: ignore

            splitter = RecursiveCharacterTextSplitter(chunk_size=1200, chunk_overlap=150)
            return splitter.split_documents(docs)
        except Exception:
            # Minimal fallback: no splitting.
            return docs


def _build_inmemory_retriever(
    repo_root: Path,
    *,
    ollama_base_url: str,
    embedding_model: str,
) -> Tuple[Any, str]:
    """Build an in-memory retriever over the local repository content."""

    # Embeddings: prefer Ollama embeddings (repo already uses Ollama for LLMs).
    # If embeddings/vectorstore aren't available, callers should fall back.
    from langchain_ollama import OllamaEmbeddings

    InMemoryVectorStore = None
    try:
        from langchain_core.vectorstores import InMemoryVectorStore as _IMVS  # type: ignore

        InMemoryVectorStore = _IMVS
    except Exception:
        try:
            from langchain.vectorstores import InMemoryVectorStore as _IMVS  # type: ignore

            InMemoryVectorStore = _IMVS
        except Exception:
            InMemoryVectorStore = None

    if InMemoryVectorStore is None:
        raise RuntimeError("InMemoryVectorStore is not available; install a supported LangChain vectorstore.")

    docs = _load_documents(repo_root)
    chunks = _split_documents(docs)

    embeddings = OllamaEmbeddings(model=embedding_model, base_url=ollama_base_url)
    store = InMemoryVectorStore(embeddings)
    store.add_documents(chunks)
    retriever = store.as_retriever(search_kwargs={"k": 6})
    summary = f"Indexed {len(docs)} files into {len(chunks)} chunks"
    return retriever, summary


def _fallback_lexical_search(repo_root: Path, query: str, *, max_hits: int = 6) -> str:
    tokens = [t for t in (query or "").lower().split() if len(t) >= 3]
    if not tokens:
        return "(no query tokens)"

    hits: List[Tuple[int, str, str]] = []
    for p in _iter_repo_files(repo_root):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        lower = text.lower()
        score = sum(lower.count(t) for t in tokens)
        if score <= 0:
            continue

        rel = str(p.relative_to(repo_root)).replace("\\", "/")
        # Snippet: first match location
        idx = min([lower.find(t) for t in tokens if lower.find(t) >= 0] or [0])
        start = max(0, idx - 300)
        end = min(len(text), idx + 900)
        snippet = text[start:end].strip()
        hits.append((score, rel, snippet))

    hits.sort(key=lambda x: x[0], reverse=True)
    parts: List[str] = []
    for score, rel, snippet in hits[:max_hits]:
        if len(snippet) > 1200:
            snippet = snippet[:1200] + "..."
        parts.append(f"FILE: {rel} (score={score})\n{snippet}")

    return "\n\n---\n\n".join(parts) if parts else "(no matches)"


def build_rag_agent(
    *,
    selected_servers: List[str],
    model_name: str = "llama3.2",
    ollama_base_url: str = "http://ollama:11434",
    temperature: float = 0.1,
    system_prompt: str = "",
    tool_call_events: Optional[List[ToolCallEvent]] = None,
    session_id: Optional[str] = None,
    source: str = "agent_lab",
    repo_root: Optional[Path] = None,
    embedding_model: str = "nomic-embed-text",
) -> AgentRuntime:
    """RAG+Tools agent: can retrieve repo context and call MCP tools."""

    runtime = build_normal_agent(
        selected_servers=selected_servers,
        model_name=model_name,
        ollama_base_url=ollama_base_url,
        temperature=temperature,
        system_prompt=system_prompt.strip() or _default_system_prompt(),
        enable_rag=True,
        embedding_model=embedding_model,
        repo_root=repo_root,
        tool_call_events=tool_call_events,
        session_id=session_id,
        source=source,
    )

    # Strengthen the hint for the explicit RAG mode.
    llm = ChatOllama(model=model_name, base_url=ollama_base_url, temperature=temperature)
    runtime.system_prompt = runtime.system_prompt + "\n\n" + "When in doubt, call retrieve_repo_context first."
    runtime.agent = _create_langgraph_agent(llm, runtime.tools, runtime.system_prompt)
    return runtime


def build_deep_agent(
    *,
    selected_servers: List[str],
    model_name: str = "llama3.2",
    ollama_base_url: str = "http://ollama:11434",
    temperature: float = 0.1,
    system_prompt: str = "",
    tool_call_events: Optional[List[ToolCallEvent]] = None,
    session_id: Optional[str] = None,
    source: str = "agent_lab",
) -> AgentRuntime:
    """"Deep" agent.

    Prefer LangChain's deep agent factory when available; otherwise fall back
    to the existing plan→ReAct flow in run_deep_agent_query().
    """

    final_system = system_prompt.strip() or _deep_system_prompt()
    runtime = build_normal_agent(
        selected_servers=selected_servers,
        model_name=model_name,
        ollama_base_url=ollama_base_url,
        temperature=temperature,
        system_prompt=final_system,
        enable_rag=True,
        tool_call_events=tool_call_events,
        session_id=session_id,
        source=source,
    )

    llm = ChatOllama(model=model_name, base_url=ollama_base_url, temperature=temperature)
    runtime.deep_agent = _try_build_langchain_deep_agent(llm, runtime.tools, runtime.system_prompt)
    return runtime


async def _run_deep_agent_query_async(
    runtime: AgentRuntime,
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, str, List[ToolCallEvent]]:
    """Async implementation of deep agent query execution."""
    events_before = len(runtime.tool_call_events)

    if runtime.deep_agent is not None:
        deep = runtime.deep_agent
        try:
            # Try async invocation first
            try:
                if hasattr(deep, "ainvoke"):
                    result = await deep.ainvoke({"input": query})
                else:
                    result = deep.invoke({"input": query})
            except Exception:
                if hasattr(deep, "ainvoke"):
                    result = await deep.ainvoke(query)
                else:
                    result = deep.invoke(query)

            plan = "(generated by deep agent)"
            answer = ""
            if isinstance(result, dict):
                answer = str(result.get("output") or result.get("answer") or result.get("result") or "")
                plan = str(result.get("plan") or plan)
            else:
                answer = str(result)

            runtime.last_plan = plan
            return plan, answer, runtime.tool_call_events[events_before:]
        except Exception:
            # Fall back to legacy flow if deep agent invocation fails.
            pass

    llm = ChatOllama(model=runtime.model_name, base_url=runtime.ollama_base_url, temperature=runtime.temperature)

    plan_prompt = (
        "Write a short plan (3-6 bullets) to answer the user's request using available tools. "
        "Do NOT call tools yet. Keep it practical.\n\nUser request:\n" + query
    )
    try:
        if hasattr(llm, "ainvoke"):
            plan_msg = await llm.ainvoke([HumanMessage(content=plan_prompt)])
        else:
            plan_msg = llm.invoke([HumanMessage(content=plan_prompt)])
        plan = str(getattr(plan_msg, "content", "")).strip()
    except Exception:
        plan = "(plan unavailable)"

    runtime.last_plan = plan
    augmented_query = f"PLAN:\n{plan}\n\nREQUEST:\n{query}"
    answer, _events = await _run_agent_query_async(runtime, augmented_query, chat_history=chat_history)
    return plan, answer, runtime.tool_call_events[events_before:]


def run_deep_agent_query(
    runtime: AgentRuntime,
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, str, List[ToolCallEvent]]:
    """Run a deep query - handles async tools properly.

    If a LangChain deep agent is available, use it directly. Otherwise, fall
    back to the internal plan→ReAct flow.
    """
    # Check if we're already in an event loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # We're in an async context, create a new thread to run the coroutine
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(
                asyncio.run,
                _run_deep_agent_query_async(runtime, query, chat_history)
            )
            return future.result()
    else:
        # No running loop, use asyncio.run directly
        return asyncio.run(_run_deep_agent_query_async(runtime, query, chat_history))
