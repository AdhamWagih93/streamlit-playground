"""MCP logging interceptor for universal tool call logging.

This module provides an interceptor that can be added to any MultiServerMCPClient
to automatically log all tool calls to the database.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from langchain_mcp_adapters.client import MultiServerMCPClient


def create_logging_interceptor(
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    database_url: Optional[str] = None,
) -> Callable:
    """Create a logging interceptor for MCP tool calls.

    This interceptor logs all tool calls to the MCP log database,
    capturing timing, arguments, results, and errors.

    Args:
        source: Source identifier (e.g., "agent_builder", "kubernetes_page")
        session_id: Session ID for correlating calls
        user_id: Optional user ID
        database_url: Optional database URL override

    Returns:
        An async interceptor function compatible with MultiServerMCPClient.

    Example:
        ```python
        from src.mcp_log import create_logging_interceptor

        interceptor = create_logging_interceptor(source="my_page")
        client = MultiServerMCPClient(
            connections={"kubernetes": conn},
            tool_interceptors=[interceptor]
        )
        ```
    """
    # Generate session ID if not provided
    _session_id = session_id or str(uuid.uuid4())[:8]

    # Lazy import to avoid circular dependencies
    from .config import get_config
    from .repo import log_tool_call, init_db

    # Initialize database on first use
    config = get_config()
    if config.enabled:
        try:
            init_db(database_url)
        except Exception:
            pass  # Don't fail if DB init fails

    async def logging_interceptor(request, handler):
        """Intercept and log tool calls."""
        started_at = datetime.utcnow()
        request_id = str(uuid.uuid4())[:8]

        # Extract request details
        server_name = getattr(request, "server_name", "unknown")
        tool_name = getattr(request, "name", "unknown")
        args = dict(getattr(request, "args", {}) or {})

        success = False
        result_preview = None
        error_message = None
        error_type = None

        try:
            # Call the actual tool
            result = await handler(request)

            success = True

            # Extract result preview
            try:
                content = getattr(result, "content", result)
                result_preview = str(content)[:2000]
            except Exception:
                result_preview = str(result)[:2000]

            return result

        except Exception as exc:
            success = False
            error_message = str(exc)
            error_type = type(exc).__name__
            raise

        finally:
            finished_at = datetime.utcnow()
            duration_ms = (finished_at - started_at).total_seconds() * 1000

            # Log to database (fire and forget)
            try:
                log_tool_call(
                    server_name=server_name,
                    tool_name=tool_name,
                    args=args,
                    success=success,
                    result_preview=result_preview,
                    error_message=error_message,
                    error_type=error_type,
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    source=source,
                    request_id=request_id,
                    session_id=_session_id,
                    user_id=user_id,
                    database_url=database_url,
                )
            except Exception:
                # Never let logging break the main application
                pass

    return logging_interceptor


def get_logged_mcp_client(
    connections: Dict[str, Dict[str, Any]],
    source: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    additional_interceptors: Optional[List[Callable]] = None,
    database_url: Optional[str] = None,
) -> MultiServerMCPClient:
    """Create a MultiServerMCPClient with logging enabled.

    This is a convenience function that creates a client with the
    logging interceptor pre-configured.

    Args:
        connections: MCP server connection configurations
        source: Source identifier for logging
        session_id: Session ID for correlating calls
        user_id: Optional user ID
        additional_interceptors: Additional interceptors to include
        database_url: Optional database URL override

    Returns:
        A MultiServerMCPClient with logging enabled.

    Example:
        ```python
        from src.mcp_log import get_logged_mcp_client

        client = get_logged_mcp_client(
            connections={"kubernetes": conn, "docker": conn2},
            source="agent_builder"
        )
        tools = asyncio.run(client.get_tools())
        ```
    """
    interceptors = []

    # Add logging interceptor
    logging_interceptor = create_logging_interceptor(
        source=source,
        session_id=session_id,
        user_id=user_id,
        database_url=database_url,
    )
    interceptors.append(logging_interceptor)

    # Add any additional interceptors
    if additional_interceptors:
        interceptors.extend(additional_interceptors)

    return MultiServerMCPClient(
        connections=connections,
        tool_interceptors=interceptors,
    )


def build_logged_connection(
    server_key: str,
    module: str,
    config,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a connection dict with logging-friendly metadata.

    This helper builds the connection configuration for an MCP server
    that will be used with the logging interceptor.

    Args:
        server_key: Server identifier (e.g., "kubernetes")
        module: Python module path for the MCP server
        config: Server configuration object with to_env_overrides()
        source: Source identifier for logging

    Returns:
        Connection configuration dict for MultiServerMCPClient.
    """
    transport = getattr(config, "mcp_transport", "stdio") or "stdio"
    transport = transport.lower().strip()

    if transport == "http":
        transport = "streamable-http"

    def _normalise_streamable_http_url(url: str) -> str:
        raw = (url or "").strip()
        if not raw:
            return raw
        base = raw.rstrip("/")
        if base.endswith("/mcp"):
            return base
        return base + "/mcp"

    if transport == "stdio":
        env = {**os.environ}
        if hasattr(config, "to_env_overrides"):
            env.update(config.to_env_overrides())

        # Ensure repo root is in PYTHONPATH
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
        if transport == "streamable-http":
            url = _normalise_streamable_http_url(str(url))
        return {"transport": transport, "url": url}
