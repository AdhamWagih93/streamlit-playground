from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import asyncio
import sys

from langchain_core.messages import HumanMessage, AIMessage, BaseMessage

from src.ai.agents.kubernetes_agent_config import KubernetesToolAgentConfig
from src.ai.agents.tool_agent_runner import build_tool_agent_runtime


@dataclass
class ToolCallRecord:
    name: str
    args: Dict[str, Any]
    started_at: datetime
    finished_at: datetime
    ok: bool
    result_preview: str


class KubernetesAgent:
    """LangChain agent that uses Kubernetes MCP tools (stdio)."""

    def __init__(self, lc_agent: Any, mcp_tools: List[Any], tool_call_events: List[Dict[str, Any]]):
        self._agent = lc_agent
        self._tools = list(mcp_tools or [])
        self._tool_call_events = tool_call_events

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
        return [
            {
                "name": ev.get("tool"),
                "args": ev.get("args", {}),
                "started_at": ev.get("started_at"),
                "finished_at": ev.get("finished_at"),
                "ok": ev.get("ok", False),
                "result_preview": ev.get("result_preview", ""),
            }
            for ev in self._tool_call_events
        ]

    def run(self, user_input: str, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        self._tool_call_events.clear()

        messages: List[BaseMessage] = self._build_history_messages(history)
        messages.append(HumanMessage(content=user_input))

        result = asyncio.run(self._agent.ainvoke({"messages": messages}))

        final_text = ""
        result_messages = result.get("messages", [])
        if result_messages:
            ai_messages = [m for m in result_messages if isinstance(m, AIMessage)]
            final_msg = ai_messages[-1] if ai_messages else result_messages[-1]
            final_text = getattr(final_msg, "content", str(final_msg))

        return {
            "plan": {},
            "raw_plan": "",
            "tool_calls": self._serialise_tool_calls(),
            "final_response": final_text,
        }


def build_kubernetes_agent() -> KubernetesAgent:
    """Factory for the Kubernetes MCP tool agent (env-first defaults)."""

    cfg = KubernetesToolAgentConfig.from_env()
    runtime = build_tool_agent_runtime(
        cfg.tool_agent,
        python_executable=sys.executable,
        tool_call_events=[],
        mcp_client_token=None,
    )
    return KubernetesAgent(lc_agent=runtime.agent, mcp_tools=runtime.tools, tool_call_events=runtime.tool_call_events)
