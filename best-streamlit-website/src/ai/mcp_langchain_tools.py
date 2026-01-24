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
    tool = next((t for t in tools if matches_tool_name(str(getattr(t, "name", "")), name)), None)
    if tool is None:
        available = tool_names(tools)
        raise ValueError(f"Tool {name} not found. Available: {available}")

    if hasattr(tool, "ainvoke"):
        return normalise_mcp_result(asyncio.run(tool.ainvoke(args)))

    return normalise_mcp_result(tool.invoke(args))
