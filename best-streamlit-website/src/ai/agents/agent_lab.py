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


def get_available_servers() -> Dict[str, Dict[str, Any]]:
    """Expose the same MCP server catalog as the Dynamic Agent Builder."""

    return MCP_SERVERS.copy()


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


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
    tool_call_events: Optional[List[ToolCallEvent]] = None,
    session_id: Optional[str] = None,
    source: str = "agent_lab",
) -> AgentRuntime:
    """Normal ReAct agent with tools from selected MCP servers."""

    if not selected_servers:
        raise ValueError("At least one MCP server must be selected")

    tool_call_events = tool_call_events if tool_call_events is not None else []

    connections = _build_connections(selected_servers)

    interceptors = _build_tool_logging_interceptor(tool_call_events, source=source, session_id=session_id)
    client = MultiServerMCPClient(connections, tool_interceptors=interceptors)
    tools = asyncio.run(client.get_tools())

    llm = ChatOllama(model=model_name, base_url=ollama_base_url, temperature=temperature)
    final_system = system_prompt.strip() or _default_system_prompt()
    agent = _create_langgraph_agent(llm, list(tools), final_system)

    return AgentRuntime(
        client=client,
        tools=list(tools),
        agent=agent,
        tool_call_events=tool_call_events,
        selected_servers=list(selected_servers),
        system_prompt=final_system,
        model_name=model_name,
        ollama_base_url=ollama_base_url,
        temperature=temperature,
    )


def run_agent_query(
    runtime: AgentRuntime,
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[ToolCallEvent]]:
    messages = _messages_from_history(chat_history)
    messages.append(HumanMessage(content=query))

    events_before = len(runtime.tool_call_events)
    result = runtime.agent.invoke({"messages": messages})

    output = ""
    for msg in reversed(result.get("messages", []) or []):
        if isinstance(msg, AIMessage) and getattr(msg, "content", None):
            output = str(msg.content)
            break

    return output, runtime.tool_call_events[events_before:]


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
        tool_call_events=tool_call_events,
        session_id=session_id,
        source=source,
    )

    root = repo_root or Path(__file__).resolve().parents[4]
    retriever = None
    summary = ""
    rag_mode = "vector"
    try:
        retriever, summary = _build_inmemory_retriever(
            root,
            ollama_base_url=ollama_base_url,
            embedding_model=embedding_model,
        )
    except Exception as exc:  # noqa: BLE001
        rag_mode = "lexical"
        summary = f"Lexical search fallback (RAG unavailable): {exc}"

    @tool("retrieve_repo_context")
    def retrieve_repo_context(query: str) -> str:
        """Search this repo's code/docs and return relevant excerpts with file paths."""

        if rag_mode != "vector" or retriever is None:
            return _fallback_lexical_search(root, query)

        docs: List[Document]
        try:
            # Newer LC retrievers are Runnables.
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

    # Rebuild the agent to include the retriever tool.
    llm = ChatOllama(model=model_name, base_url=ollama_base_url, temperature=temperature)
    rag_hint = "When answering questions about this codebase, first call retrieve_repo_context."
    if rag_mode != "vector":
        rag_hint += " (This session is using lexical search fallback.)"

    final_system = (system_prompt.strip() or _default_system_prompt()) + "\n\n" + rag_hint

    combined_tools = list(runtime.tools) + [retrieve_repo_context]
    runtime.tools = combined_tools
    runtime.system_prompt = final_system
    runtime.agent = _create_langgraph_agent(llm, combined_tools, final_system)
    runtime.rag_enabled = True
    runtime.rag_index_summary = summary
    runtime.ollama_base_url = ollama_base_url
    runtime.temperature = temperature
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
    """"Deep" agent: generates a plan, then uses tools to execute."""

    final_system = system_prompt.strip() or _deep_system_prompt()
    runtime = build_normal_agent(
        selected_servers=selected_servers,
        model_name=model_name,
        ollama_base_url=ollama_base_url,
        temperature=temperature,
        system_prompt=final_system,
        tool_call_events=tool_call_events,
        session_id=session_id,
        source=source,
    )
    return runtime


def run_deep_agent_query(
    runtime: AgentRuntime,
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, str, List[ToolCallEvent]]:
    """Run a deep query: create plan, then answer using tools."""

    llm = ChatOllama(model=runtime.model_name, base_url=runtime.ollama_base_url, temperature=runtime.temperature)

    plan_prompt = (
        "Write a short plan (3-6 bullets) to answer the user's request using available tools. "
        "Do NOT call tools yet. Keep it practical.\n\nUser request:\n" + query
    )
    try:
        plan_msg = llm.invoke([HumanMessage(content=plan_prompt)])
        plan = str(getattr(plan_msg, "content", "")).strip()
    except Exception:
        plan = "(plan unavailable)"

    runtime.last_plan = plan
    augmented_query = f"PLAN:\n{plan}\n\nREQUEST:\n{query}"
    answer, events = run_agent_query(runtime, augmented_query, chat_history=chat_history)
    return plan, answer, events
