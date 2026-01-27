"""Dynamic multi-MCP agent builder.

This module provides utilities for creating agents that can use tools
from multiple MCP servers dynamically at runtime.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_ollama.chat_models import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langgraph.prebuilt import create_react_agent

from src.mcp_log import create_logging_interceptor
from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig
from src.ai.mcp_servers.kubernetes.config import KubernetesMCPServerConfig
from src.ai.mcp_servers.docker.config import DockerMCPServerConfig
from src.ai.mcp_servers.nexus.config import NexusMCPServerConfig
from src.ai.mcp_servers.git.config import GitMCPServerConfig
from src.ai.mcp_servers.trivy.config import TrivyMCPServerConfig
from src.ai.mcp_servers.playwright.config import PlaywrightMCPServerConfig
from src.ai.mcp_servers.websearch.config import WebSearchMCPServerConfig


# Available MCP servers
MCP_SERVERS = {
    "jenkins": {
        "name": "Jenkins",
        "description": "CI/CD pipeline management, job builds, and automation",
        "icon": "🔧",
        "module": "src.ai.mcp_servers.jenkins.mcp",
        "config_class": JenkinsMCPServerConfig,
    },
    "kubernetes": {
        "name": "Kubernetes",
        "description": "Container orchestration, pods, deployments, and services",
        "icon": "☸️",
        "module": "src.ai.mcp_servers.kubernetes.mcp",
        "config_class": KubernetesMCPServerConfig,
    },
    "docker": {
        "name": "Docker",
        "description": "Container management, images, and container lifecycle",
        "icon": "🐳",
        "module": "src.ai.mcp_servers.docker.mcp",
        "config_class": DockerMCPServerConfig,
    },
    "nexus": {
        "name": "Nexus",
        "description": "Artifact repository management and package publishing",
        "icon": "📦",
        "module": "src.ai.mcp_servers.nexus.mcp",
        "config_class": NexusMCPServerConfig,
    },
    "git": {
        "name": "Git",
        "description": "Git repository operations, commits, branches, and diffs",
        "icon": "📂",
        "module": "src.ai.mcp_servers.git.mcp",
        "config_class": GitMCPServerConfig,
    },
    "trivy": {
        "name": "Trivy",
        "description": "Security scanning for containers, filesystems, and IaC",
        "icon": "🔒",
        "module": "src.ai.mcp_servers.trivy.mcp",
        "config_class": TrivyMCPServerConfig,
    },
    "playwright": {
        "name": "Playwright",
        "description": "Browser automation, web scraping, screenshots, and page interaction",
        "icon": "🎭",
        "module": "src.ai.mcp_servers.playwright.mcp",
        "config_class": PlaywrightMCPServerConfig,
    },
    "websearch": {
        "name": "Web Search",
        "description": "Search the web, news, images, videos using DuckDuckGo",
        "icon": "🔍",
        "module": "src.ai.mcp_servers.websearch.mcp",
        "config_class": WebSearchMCPServerConfig,
    },
}


@dataclass
class ToolCallEvent:
    """Record of a tool call made by the agent."""
    server: str
    tool: str
    args: Dict[str, Any]
    started_at: str
    finished_at: Optional[str] = None
    ok: bool = False
    result_preview: str = ""
    error: Optional[str] = None


@dataclass
class DynamicAgentRuntime:
    """Runtime for a dynamically created multi-MCP agent."""
    client: MultiServerMCPClient
    tools: List[Any]
    agent: Any  # LangGraph compiled agent
    tool_call_events: List[ToolCallEvent]
    selected_servers: List[str]
    system_prompt: str
    model_name: str


def _get_transport(config) -> str:
    """Normalize transport type."""
    t = getattr(config, "mcp_transport", "stdio") or "stdio"
    t = t.lower().strip()
    return "sse" if t == "http" else t


def _build_connection(server_key: str, config) -> Dict[str, Any]:
    """Build a LangChain MCP connection dict for a server."""
    transport = _get_transport(config)
    module = MCP_SERVERS[server_key]["module"]

    if transport == "stdio":
        # Build environment with PYTHONPATH for subprocess
        env = {**os.environ}
        if hasattr(config, "to_env_overrides"):
            env.update(config.to_env_overrides())

        # Ensure repo root is in PYTHONPATH
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = repo_root + (os.pathsep + existing_pp if existing_pp else "")

        return {
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-m", module],
            "env": env,
        }
    else:
        url = getattr(config, "mcp_url", "")
        return {"transport": transport, "url": url}


def get_available_servers() -> Dict[str, Dict[str, Any]]:
    """Get metadata about all available MCP servers."""
    return MCP_SERVERS.copy()


def build_dynamic_agent(
    selected_servers: List[str],
    system_prompt: str = "",
    model_name: str = "llama3.2",
    ollama_base_url: str = "http://localhost:11434",
    temperature: float = 0.1,
    tool_call_events: Optional[List[ToolCallEvent]] = None,
    session_id: Optional[str] = None,
    source: str = "agent_builder",
) -> DynamicAgentRuntime:
    """Build a dynamic agent with tools from selected MCP servers.

    Args:
        selected_servers: List of server keys to include (e.g., ["kubernetes", "docker"])
        system_prompt: Custom system prompt for the agent
        model_name: Ollama model name
        ollama_base_url: Ollama server URL
        temperature: LLM temperature
        tool_call_events: Optional list to collect tool call events
        session_id: Optional session ID for correlating log entries
        source: Source identifier for log entries (default: "agent_builder")

    Returns:
        DynamicAgentRuntime with the configured agent
    """
    if not selected_servers:
        raise ValueError("At least one MCP server must be selected")

    tool_call_events = tool_call_events if tool_call_events is not None else []

    # Build interceptor for logging tool calls
    async def logging_interceptor(request: MCPToolCallRequest, handler):
        started = datetime.utcnow().isoformat() + "Z"

        # Sanitize args for logging
        safe_args: Dict[str, Any] = {}
        try:
            safe_args = dict(request.args or {})
            # Redact sensitive fields
            for key in ["_client_token", "password", "token", "api_token", "secret"]:
                if key in safe_args:
                    safe_args[key] = "***redacted***"
        except Exception:
            pass

        event = ToolCallEvent(
            server=request.server_name,
            tool=request.name,
            args=safe_args,
            started_at=started,
        )

        try:
            result = await handler(request)
            event.ok = True
            event.result_preview = str(getattr(result, "content", result))[:1000]
        except Exception as exc:
            event.ok = False
            event.error = str(exc)
            event.result_preview = f"ERROR: {exc}"[:1000]
            raise
        finally:
            event.finished_at = datetime.utcnow().isoformat() + "Z"
            tool_call_events.append(event)

        return result

    # Build connections for selected servers
    connections: Dict[str, Dict[str, Any]] = {}

    for server_key in selected_servers:
        if server_key not in MCP_SERVERS:
            raise ValueError(f"Unknown server: {server_key}")

        config_class = MCP_SERVERS[server_key]["config_class"]
        config = config_class.from_env()
        connections[server_key] = _build_connection(server_key, config)

    # Create database logging interceptor for persistent logging
    db_logging_interceptor = create_logging_interceptor(
        source=source,
        session_id=session_id,
    )

    # Create multi-server client with both interceptors
    # Memory interceptor captures events for UI, DB interceptor persists to database
    client = MultiServerMCPClient(
        connections,
        tool_interceptors=[logging_interceptor, db_logging_interceptor],
    )

    # Get tools from all servers
    tools = asyncio.run(client.get_tools())

    # Build LLM
    llm = ChatOllama(
        model=model_name,
        base_url=ollama_base_url,
        temperature=temperature,
    )

    # Build system prompt
    default_system = (
        "You are a helpful DevOps assistant with access to various infrastructure tools. "
        "Use the available tools to help the user with their requests. "
        "Always explain what you're doing and report the results clearly."
    )

    final_system_prompt = system_prompt or default_system

    # Create LangGraph react agent
    agent = create_react_agent(
        llm,
        tools,
        state_modifier=final_system_prompt,
    )

    return DynamicAgentRuntime(
        client=client,
        tools=list(tools),
        agent=agent,
        tool_call_events=tool_call_events,
        selected_servers=selected_servers,
        system_prompt=final_system_prompt,
        model_name=model_name,
    )


def run_agent_query(
    runtime: DynamicAgentRuntime,
    query: str,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Tuple[str, List[ToolCallEvent]]:
    """Run a query against the dynamic agent.

    Args:
        runtime: The agent runtime
        query: User's query
        chat_history: List of previous messages [{"role": "user/assistant", "content": "..."}]

    Returns:
        Tuple of (response_text, new_tool_calls)
    """
    # Convert chat history to LangChain messages
    messages = []
    for msg in (chat_history or []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    # Add the current query
    messages.append(HumanMessage(content=query))

    # Track tool calls for this query
    events_before = len(runtime.tool_call_events)

    # Run the LangGraph agent
    result = runtime.agent.invoke({"messages": messages})

    # Extract the final response from the messages
    output = ""
    result_messages = result.get("messages", [])
    if result_messages:
        # Get the last AI message
        for msg in reversed(result_messages):
            if isinstance(msg, AIMessage) and msg.content:
                output = msg.content
                break

    # Get new tool calls
    new_events = runtime.tool_call_events[events_before:]

    return output, new_events


def list_agent_tools(runtime: DynamicAgentRuntime) -> List[Dict[str, Any]]:
    """List all tools available to the agent.

    Returns:
        List of tool info dicts with name, description, and server
    """
    tools_info = []

    for tool in runtime.tools:
        name = getattr(tool, "name", str(tool))
        description = getattr(tool, "description", "")

        # Try to determine which server this tool belongs to
        server = "unknown"
        for server_key in runtime.selected_servers:
            # Tools are often prefixed with server name
            if name.startswith(f"{server_key}_") or name.startswith(f"{server_key}."):
                server = server_key
                break

        tools_info.append({
            "name": name,
            "description": description[:200] if description else "",
            "server": server,
        })

    return tools_info
