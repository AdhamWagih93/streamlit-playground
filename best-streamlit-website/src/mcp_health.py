"""Simplified MCP server health utilities."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

import streamlit as st


def get_status_badge_class(status: str) -> str:
    """Get CSS class for status badge."""
    return {
        "healthy": "status-healthy",
        "degraded": "status-unknown",
        "unhealthy": "status-unhealthy",
        "unknown": "status-unknown",
    }.get(status, "status-unknown")


def get_status_icon(status: str) -> str:
    """Get icon for status."""
    return {
        "healthy": "✓",
        "degraded": "⚠",
        "unhealthy": "✗",
        "unknown": "?",
    }.get(status, "?")


async def check_mcp_server_simple(server_name: str, url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Simple MCP health check by trying to connect and list tools.

    Args:
        server_name: Name of the MCP server
        url: URL to connect to (e.g., http://docker-mcp:8000)
        timeout: Timeout in seconds

    Returns:
        Dict with status, message, response_time_ms, tool_count
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    started = datetime.now()

    try:
        client = MultiServerMCPClient({
            server_name: {
                "transport": "sse",
                "url": url,
            }
        })

        tools = await asyncio.wait_for(client.get_tools(), timeout=timeout)

        response_time = int((datetime.now() - started).total_seconds() * 1000)
        tool_count = len(list(tools or []))

        return {
            "status": "healthy",
            "message": f"{tool_count} tools available",
            "response_time_ms": response_time,
            "tool_count": tool_count,
            "last_checked": datetime.now().isoformat(),
        }

    except asyncio.TimeoutError:
        response_time = int((datetime.now() - started).total_seconds() * 1000)
        return {
            "status": "unhealthy",
            "message": f"Timeout after {timeout}s",
            "response_time_ms": response_time,
            "tool_count": 0,
            "last_checked": datetime.now().isoformat(),
        }
    except Exception as e:
        response_time = int((datetime.now() - started).total_seconds() * 1000)
        error_msg = str(e)
        if "Connection refused" in error_msg or "Failed to connect" in error_msg:
            message = "Connection refused - service not running"
        elif "404" in error_msg:
            message = "Service not found (404)"
        else:
            message = f"Error: {error_msg[:80]}"

        return {
            "status": "unhealthy",
            "message": message,
            "response_time_ms": response_time,
            "tool_count": 0,
            "last_checked": datetime.now().isoformat(),
        }


def add_mcp_status_styles() -> None:
    """Add common status badge styles to the page."""
    st.markdown(
        """
        <style>
        .status-badge {
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 12px;
            font-weight: 600;
            font-size: 0.85rem;
            white-space: nowrap;
        }
        .status-healthy {
            background: #dcfce7;
            color: #166534;
            border: 1px solid #86efac;
        }
        .status-unhealthy {
            background: #fee2e2;
            color: #991b1b;
            border: 1px solid #fca5a5;
        }
        .status-unknown {
            background: #fef3c7;
            color: #92400e;
            border: 1px solid #fcd34d;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def show_mcp_connection_warning(server_name: str, service_url: Optional[str] = None) -> None:
    """Show a warning about MCP server connectivity issues."""
    extra_info = f" at `{service_url}`" if service_url else ""

    st.warning(
        f"⚠️ **{server_name} MCP server is not responding{extra_info}.**\n\n"
        f"Make sure the service is running:\n"
        f"- Check `docker-compose ps` to see if all services are up\n"
        f"- Run `./scripts/dev-logs.ps1 {server_name.lower().replace(' ', '-')}` to view logs\n"
        f"- Visit the [System Status](/System_Status) page for a full overview"
    )
