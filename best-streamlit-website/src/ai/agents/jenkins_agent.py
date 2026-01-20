from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncio

from langchain_ollama.chat_models import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage, BaseMessage
from langchain_core.tools import tool
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest

from src.ai.mcp_servers.jenkins_server import JenkinsAuthConfig, JenkinsMCPServer


@dataclass
class ToolCallRecord:
    name: str
    args: Dict[str, Any]
    started_at: datetime
    finished_at: datetime
    ok: bool
    result_preview: str


class JenkinsAgent:
    """Thin wrapper around a LangChain MCP-powered agent for Jenkins.

    Internally this uses ``MultiServerMCPClient.get_tools()`` together with
    ``create_agent`` from LangChain, following the official MCP
    documentation. Tool invocations are tracked via an MCP interceptor so
    the Streamlit UI can visualise them.
    """

    def __init__(
        self,
        server: JenkinsMCPServer,
        lc_agent,
        tool_call_events: List[Dict[str, Any]],
        user_name: Optional[str] = None,
    ) -> None:
        # Kept for direct, non-MCP calls ("Test connection" button).
        self.server = server
        self._agent = lc_agent
        self._tool_call_events = tool_call_events
        self.user_name = user_name

    def _build_history_messages(self, history: Optional[List[Dict[str, str]]]) -> List[BaseMessage]:
        messages: List[BaseMessage] = []
        if not history:
            return messages

        for msg in history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not content:
                continue
            if role == "assistant":
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=content))
        return messages

    def _serialise_tool_calls(self) -> List[Dict[str, Any]]:
        serialised: List[Dict[str, Any]] = []
        for ev in self._tool_call_events:
            serialised.append(
                {
                    "name": ev.get("tool"),
                    "args": ev.get("args", {}),
                    "started_at": ev.get("started_at"),
                    "finished_at": ev.get("finished_at"),
                    "ok": ev.get("ok", False),
                    "result_preview": ev.get("result_preview", ""),
                }
            )
        return serialised

    def run(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Invoke the Jenkins MCP agent for a single user request.

        Returns a dict compatible with the previous custom implementation:
        - plan: kept as an empty object for backward compatibility
        - raw_plan: empty string (no custom planning JSON)
        - tool_calls: list of MCP tool call records from the interceptor
        - final_response: natural-language answer from the LLM
        """

        # Clear previous call history for this run
        self._tool_call_events.clear()

        messages: List[BaseMessage] = self._build_history_messages(history)
        messages.append(HumanMessage(content=user_input))

        # Per LangChain + MCP docs, use the async ``ainvoke`` API for
        # agents backed by MCP tools. We bridge this into the sync
        # Streamlit world via ``asyncio.run``.
        result = asyncio.run(self._agent.ainvoke({"messages": messages}))

        # LangChain's create_agent returns a dict with a "messages" list; the
        # last AIMessage is the final answer we want to display.
        final_text = ""
        result_messages = result.get("messages", [])
        if result_messages:
            # Prefer the last AIMessage if present
            ai_messages = [m for m in result_messages if isinstance(m, AIMessage)]
            final_msg = ai_messages[-1] if ai_messages else result_messages[-1]
            final_text = getattr(final_msg, "content", str(final_msg))

        tool_calls = self._serialise_tool_calls()

        return {
            "plan": {},
            "raw_plan": "",
            "tool_calls": tool_calls,
            "final_response": final_text,
        }

    def run_with_stream(
        self,
        user_input: str,
        on_token: Optional[Any] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """Compatibility wrapper that reuses ``run``.

        The full response is generated in one shot; if ``on_token`` is
        provided it receives the final answer as a single chunk so the
        existing Streamlit UI remains functional.
        """

        result = self.run(user_input, history=history)
        answer = result.get("final_response", "")
        if on_token is not None and answer:
            on_token(str(answer))
        return result


def _build_mcp_connections(config: JenkinsAuthConfig, tool_call_events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build MultiServerMCPClient config and interceptors per official docs."""

    env: Dict[str, str] = {}
    if config.base_url:
        env["JENKINS_BASE_URL"] = config.base_url
    if config.username:
        env["JENKINS_USERNAME"] = config.username
    if config.api_token:
        env["JENKINS_API_TOKEN"] = config.api_token
    env["JENKINS_VERIFY_SSL"] = "true" if config.verify_ssl else "false"

    async def logging_interceptor(
        request: MCPToolCallRequest,
        handler,
    ):
        started = datetime.utcnow().isoformat() + "Z"
        entry: Dict[str, Any] = {
            "server": request.server_name,
            "tool": request.name,
            "args": request.args,
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

    connections: Dict[str, Any] = {
        "jenkins": {
            "transport": "stdio",
            "command": "python",
            "args": [
                # Path is resolved relative to the ai package root when
                # invoked via MultiServerMCPClient.
                str(Path(__file__).resolve().parent.parent / "mcp_servers" / "jenkins_server.py"),
            ],
            "env": env,
        }
    }

    return {
        "connections": connections,
        "tool_interceptors": [logging_interceptor],
    }


def build_jenkins_agent(
    base_url: str,
    username: Optional[str],
    api_token: Optional[str],
    verify_ssl: bool = True,
    model: Optional[str] = None,
    llm_base_url: str = "http://localhost:11434",
    user_name: Optional[str] = None,
) -> JenkinsAgent:
    """Factory helper that follows the official MCP + create_agent pattern."""

    config = JenkinsAuthConfig(
        base_url=base_url,
        username=username or None,
        api_token=api_token or None,
        verify_ssl=verify_ssl,
    )
    server = JenkinsMCPServer(config)

    tool_call_events: List[Dict[str, Any]] = []
    mcp_cfg = _build_mcp_connections(config, tool_call_events)

    client = MultiServerMCPClient(
        mcp_cfg["connections"],
        tool_interceptors=mcp_cfg["tool_interceptors"],
    )

    # Load MCP tools from the Jenkins server (official pattern)
    import asyncio

    tools = asyncio.run(client.get_tools())

    llm = ChatOllama(
        model=model or "qwen2.5:7b-instruct-q6_K",
        base_url=llm_base_url,
        temperature=0,
    )

    lc_agent = create_agent(llm, tools)

    return JenkinsAgent(server=server, lc_agent=lc_agent, tool_call_events=tool_call_events, user_name=user_name)
