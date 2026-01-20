from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncio
import sys

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

from src.ai.agents.jenkins_agent_config import JenkinsToolAgentConfig
from src.ai.agents.tool_agent_runner import build_tool_agent_runtime
from src.ai.agents.tool_agent_types import LLMConfig, ToolAgentConfig
from src.ai.mcp_servers.jenkins.utils.client import JenkinsAuthConfig, JenkinsMCPServer
from src.ai.mcp_servers.jenkins.config import JenkinsMCPServerConfig


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
        mcp_tools,
        tool_call_events: List[Dict[str, Any]],
        user_name: Optional[str] = None,
    ) -> None:
        # Kept for direct, non-MCP calls ("Test connection" button).
        self.server = server
        self._agent = lc_agent
        self._tools = list(mcp_tools or [])
        self._tool_call_events = tool_call_events
        self.user_name = user_name

    def call_tool(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """Call a Jenkins MCP tool directly (no LLM), using the same MCP client stack."""

        args = args or {}
        for t in self._tools:
            if getattr(t, "name", None) == tool_name:
                if hasattr(t, "ainvoke"):
                    return asyncio.run(t.ainvoke(args))
                return t.invoke(args)
        raise ValueError(f"Unknown Jenkins MCP tool: {tool_name}")

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
def build_jenkins_agent(
    base_url: Optional[str],
    username: Optional[str],
    api_token: Optional[str],
    verify_ssl: bool = True,
    model: Optional[str] = None,
    llm_base_url: str = "http://localhost:11434",
    user_name: Optional[str] = None,
) -> JenkinsAgent:
    """Factory helper that follows the official MCP + create_agent pattern."""

    # Env-first defaults for local dev
    defaults = JenkinsMCPServerConfig.from_env()
    effective_base_url = base_url or defaults.base_url
    effective_verify_ssl = verify_ssl if base_url is not None else defaults.verify_ssl

    # Credentials are intentionally *not* supplied by the UI/agent.
    # The Jenkins MCP server reads Jenkins auth from its own environment.
    # (username/api_token args are kept only for backward compatibility.)
    config = JenkinsAuthConfig(base_url=effective_base_url, username=None, api_token=None, verify_ssl=effective_verify_ssl)
    server = JenkinsMCPServer(config)

    # Tool agent config (supports stdio by default, remote SSE when configured).
    cfg = JenkinsToolAgentConfig.from_env()

    # If the caller supplies a base_url/verify_ssl override, ensure it is passed
    # into the stdio-launched MCP server env. For remote MCP, this doesn't
    # affect the connection.
    default_env = dict(getattr(cfg.tool_agent.mcp_server, "env_overrides", {}) or {})
    if effective_base_url:
        default_env["JENKINS_BASE_URL"] = effective_base_url
    default_env["JENKINS_VERIFY_SSL"] = "true" if effective_verify_ssl else "false"

    tool_agent = ToolAgentConfig.from_env(
        agent_name=cfg.tool_agent.agent_name,
        mcp_server_name="jenkins",
        mcp_module="src.ai.mcp_servers.jenkins.mcp",
        default_env=default_env,
        remote_url_env="JENKINS_MCP_URL",
        transport_env="JENKINS_MCP_TRANSPORT",
        default_remote_url=JenkinsMCPServerConfig.from_env().mcp_url,
    )

    # Allow explicit overrides from callers (e.g., agent API service), while
    # still keeping env-first defaults.
    if model or llm_base_url:
        effective_llm = LLMConfig(
            base_url=llm_base_url or tool_agent.llm.base_url,
            model=model or tool_agent.llm.model,
            temperature=tool_agent.llm.temperature,
        )
        tool_agent = ToolAgentConfig(
            agent_name=tool_agent.agent_name,
            llm=effective_llm,
            mcp_server=tool_agent.mcp_server,
        )

    tool_call_events: List[Dict[str, Any]] = []
    runtime = build_tool_agent_runtime(
        tool_agent,
        python_executable=sys.executable,
        tool_call_events=tool_call_events,
        mcp_client_token=cfg.mcp_client_token,
    )

    return JenkinsAgent(
        server=server,
        lc_agent=runtime.agent,
        mcp_tools=runtime.tools,
        tool_call_events=runtime.tool_call_events,
        user_name=user_name,
    )
