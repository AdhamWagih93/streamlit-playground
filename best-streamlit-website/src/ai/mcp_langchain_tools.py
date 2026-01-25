from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Iterable, List


def matches_tool_name(tool_name: str, desired: str) -> bool:
    """Best-effort matching for tool names across adapters.

    Some adapters prefix tool names with server identifiers or separators.
    """

    if tool_name == desired:
        return True

    for sep in ("__", ".", ":"):
        if sep in tool_name and tool_name.rsplit(sep, 1)[-1] == desired:
            return True

    if tool_name.endswith("_" + desired):
        return True

    return False


def tool_names(tools: Iterable[Any]) -> List[str]:
    return sorted({str(getattr(t, "name", "")) for t in (tools or []) if getattr(t, "name", None)})


def normalise_mcp_result(value: Any) -> Any:
    """Normalise LangChain MCP adapter tool results into plain Python data.

    Depending on adapter/version, results may come back as:
    - a plain dict
    - a list of content blocks: [{"type": "text", "text": "{...json...}"}, ...]
    - an object with a `.content` attribute containing those blocks
    """

    if isinstance(value, dict):
        return value

    content = getattr(value, "content", None)
    if content is not None:
        try:
            return normalise_mcp_result(content)
        except Exception:  # noqa: BLE001
            pass

    if isinstance(value, list):
        text_parts: List[str] = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text_parts.append(item["text"])

        if text_parts:
            text = "\n".join(text_parts).strip()
            try:
                return json.loads(text)
            except Exception:  # noqa: BLE001
                return {"ok": True, "text": text}

    return value


def invoke_tool(tools: List[Any], name: str, args: Dict[str, Any]) -> Any:
    """Invoke an MCP tool and normalise its result.

    This helper deliberately catches all exceptions (including network
    failures and TaskGroup errors raised by underlying MCP transports)
    and returns a structured error payload instead. This prevents
    unhandled exceptions from crashing Streamlit pages.
    """

    tool = next((t for t in tools if matches_tool_name(str(getattr(t, "name", "")), name)), None)
    if tool is None:
        available = tool_names(tools)
        # Return a structured error instead of raising, so callers can
        # surface this cleanly in the UI.
        return {
            "ok": False,
            "error": f"Tool {name} not found.",
            "available_tools": available,
        }

    try:
        if hasattr(tool, "ainvoke"):
            raw = asyncio.run(tool.ainvoke(args))
        else:
            raw = tool.invoke(args)
        return normalise_mcp_result(raw)
    except Exception as exc:  # noqa: BLE001
        # Normalise all transport/adapter errors into a simple dict so
        # Streamlit pages and background jobs can handle failures
        # gracefully without exposing low-level TaskGroup tracebacks.
        return {
            "ok": False,
            "error": str(exc),
            "tool": str(getattr(tool, "name", name)),
        }
