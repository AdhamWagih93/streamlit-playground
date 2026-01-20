from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain.agents import create_agent
from langchain_ollama.chat_models import ChatOllama
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest

from src.ai.agents.tool_agent_types import ToolAgentConfig


@dataclass
class ToolAgentRuntime:
    client: MultiServerMCPClient
    tools: List[Any]
    agent: Any
    tool_call_events: List[Dict[str, Any]]


def build_tool_agent_runtime(
    cfg: ToolAgentConfig,
    *,
    python_executable: str,
    tool_call_events: Optional[List[Dict[str, Any]]] = None,
    mcp_client_token: Optional[str] = None,
) -> ToolAgentRuntime:
    """Generic builder for agents that use MCP-discovered tools."""

    tool_call_events = tool_call_events or []

    async def logging_interceptor(request: MCPToolCallRequest, handler):
        if mcp_client_token:
            try:
                if request.args is None:
                    request.args = {}  # type: ignore[assignment]
                request.args["_client_token"] = mcp_client_token
            except Exception:
                pass

        started = datetime.utcnow().isoformat() + "Z"

        safe_args: Dict[str, Any]
        try:
            safe_args = dict(request.args or {})
            if "_client_token" in safe_args:
                safe_args["_client_token"] = "***redacted***"
        except Exception:
            safe_args = {}

        entry: Dict[str, Any] = {
            "server": request.server_name,
            "tool": request.name,
            "args": safe_args,
            "started_at": started,
        }
        try:
            result = await handler(request)
            entry["ok"] = True
            entry["result_preview"] = str(getattr(result, "content", result))[:800]
        except Exception as exc:  # noqa: BLE001
            entry["ok"] = False
            entry["result_preview"] = f"ERROR: {exc}"[:800]
        finally:
            entry["finished_at"] = datetime.utcnow().isoformat() + "Z"
            tool_call_events.append(entry)
        return result

    if hasattr(cfg.mcp_server, "to_connection"):
        # Remote config has to_connection() with no args.
        try:
            conn = cfg.mcp_server.to_connection(python_executable=python_executable)
        except TypeError:
            conn = cfg.mcp_server.to_connection()
    else:
        raise ValueError("Invalid MCP server config")

    connections = {
        cfg.mcp_server.server_name: conn,
    }

    client = MultiServerMCPClient(connections, tool_interceptors=[logging_interceptor])
    tools = asyncio.run(client.get_tools())

    llm = ChatOllama(
        model=cfg.llm.model,
        base_url=cfg.llm.base_url,
        temperature=cfg.llm.temperature,
    )

    agent = create_agent(llm, tools)

    return ToolAgentRuntime(client=client, tools=list(tools or []), agent=agent, tool_call_events=tool_call_events)
