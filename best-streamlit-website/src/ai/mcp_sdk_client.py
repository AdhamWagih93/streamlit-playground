from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client


@dataclass(frozen=True)
class MCPServerSpec:
    """Connection details for a single MCP server."""

    server_name: str
    transport: str  # stdio|sse|http

    # stdio
    module: Optional[str] = None
    python_executable: str = "python"
    env: Optional[Dict[str, str]] = None

    # sse/http
    url: Optional[str] = None

    # tool auth convention used in this repo
    client_token: Optional[str] = None

    def normalised_transport(self) -> str:
        t = (self.transport or "").lower().strip()
        # In this repo, "http" is used as a synonym for SSE transport.
        if t == "http":
            return "sse"
        return t


def _to_plain(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_to_plain(v) for v in value]

    # pydantic v2
    if hasattr(value, "model_dump"):
        try:
            return _to_plain(value.model_dump())
        except Exception:  # noqa: BLE001
            pass

    # pydantic v1
    if hasattr(value, "dict"):
        try:
            return _to_plain(value.dict())
        except Exception:  # noqa: BLE001
            pass

    # dataclasses
    if hasattr(value, "__dataclass_fields__"):
        try:
            import dataclasses

            return _to_plain(dataclasses.asdict(value))
        except Exception:  # noqa: BLE001
            pass

    # fallback
    try:
        return str(value)
    except Exception:  # noqa: BLE001
        return "(unserializable)"


def _content_to_text(content: Any) -> Optional[str]:
    """Extract text from MCP content blocks (best-effort)."""

    if content is None:
        return None

    # Already a string.
    if isinstance(content, str):
        return content

    blocks: List[Any]
    if isinstance(content, list):
        blocks = content
    else:
        blocks = [content]

    parts: List[str] = []

    for b in blocks:
        # mcp.types.TextContent
        if getattr(b, "type", None) == "text" and isinstance(getattr(b, "text", None), str):
            parts.append(str(getattr(b, "text")))
            continue

        # dict-form
        if isinstance(b, dict) and isinstance(b.get("text"), str):
            parts.append(b["text"])
            continue

    return "\n".join(parts).strip() if parts else None


def _open_session(spec: MCPServerSpec):
    t = spec.normalised_transport()

    if t == "stdio":
        if not spec.module:
            raise ValueError("stdio transport requires spec.module")

        env = dict(spec.env or {})
        # Ensure stdio subprocess can import this repo when running from elsewhere.
        repo_root = os.getcwd()
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = repo_root + (os.pathsep + existing_pp if existing_pp else "")

        params = StdioServerParameters(
            command=spec.python_executable,
            args=["-m", spec.module],
            env=env,
        )

        return stdio_client(params)

    if t == "sse":
        if not spec.url:
            raise ValueError("sse transport requires spec.url")
        return sse_client(spec.url)

    raise ValueError(f"Unsupported transport: {spec.transport}")


async def list_tools(spec: MCPServerSpec) -> List[Dict[str, Any]]:
    async with _open_session(spec) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            return list(_to_plain(tools) or [])


async def list_prompts(spec: MCPServerSpec) -> List[Dict[str, Any]]:
    async with _open_session(spec) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            prompts = await session.list_prompts()
            return list(_to_plain(prompts) or [])


async def get_prompt(spec: MCPServerSpec, name: str, arguments: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    async with _open_session(spec) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.get_prompt(name, arguments=arguments or None)
            return dict(_to_plain(res) or {})


async def call_tool(spec: MCPServerSpec, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    args = dict(arguments or {})
    if spec.client_token and "_client_token" not in args:
        args["_client_token"] = spec.client_token

    async with _open_session(spec) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(name, arguments=args)

    # Try to decode JSON from returned text content for convenience.
    plain = _to_plain(res)

    # Typical structure: {"content": [...]} or {"content": [{"type":"text","text":"..."}]}
    content = None
    if isinstance(plain, dict):
        content = plain.get("content")

    text = _content_to_text(content)
    if text:
        try:
            return json.loads(text)
        except Exception:  # noqa: BLE001
            return {"ok": True, "text": text, "raw": plain}

    return plain


def list_tools_sync(spec: MCPServerSpec) -> List[Dict[str, Any]]:
    return asyncio.run(list_tools(spec))


def list_prompts_sync(spec: MCPServerSpec) -> List[Dict[str, Any]]:
    return asyncio.run(list_prompts(spec))


def get_prompt_sync(spec: MCPServerSpec, name: str, arguments: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return asyncio.run(get_prompt(spec, name, arguments=arguments))


def call_tool_sync(spec: MCPServerSpec, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
    return asyncio.run(call_tool(spec, name, arguments=arguments))
