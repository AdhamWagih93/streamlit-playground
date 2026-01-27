"""Unified MCP client for connecting to MCP servers.

This module provides a consistent way to connect to MCP servers across
all Streamlit pages, using the same reliable approach as the MCP health checks.

Usage:
    from src.mcp_client import MCPClient, get_mcp_client

    # Get a client for a specific server
    client = get_mcp_client("kubernetes")

    # List available tools
    tools = client.list_tools()

    # Invoke a tool
    result = client.invoke("health_check", {})
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st


def _mcp_protocol_version() -> str:
    """Get the MCP protocol version to use."""
    try:
        from mcp import types as mcp_types

        return (
            getattr(mcp_types, "LATEST_PROTOCOL_VERSION", None)
            or getattr(mcp_types, "PROTOCOL_VERSION", None)
            or "2025-11-25"
        )
    except Exception:
        return "2025-11-25"


def _get_base_url(url: str) -> str:
    """Extract base URL without /mcp suffix."""
    base = (url or "").strip().rstrip("/")
    if base.endswith("/mcp"):
        base = base[:-4]
    return base


def _get_mcp_url(url: str) -> str:
    """Get the MCP protocol URL (with /mcp suffix)."""
    base = _get_base_url(url)
    if not base:
        return base
    return base + "/mcp"


def _extract_sse_json(text: str) -> Optional[Dict[str, Any]]:
    """Extract JSON from SSE-formatted response."""
    if not text:
        return None
    data_lines = []
    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if not data_lines:
        return None
    payload = "\n".join(data_lines).strip()
    try:
        return json.loads(payload)
    except Exception:
        return None


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""

    name: str
    url: str
    timeout: float = 10.0

    @property
    def base_url(self) -> str:
        return _get_base_url(self.url)

    @property
    def mcp_url(self) -> str:
        return _get_mcp_url(self.url)


# Server URL environment variable mappings
MCP_SERVER_ENV_VARS = {
    "docker": "STREAMLIT_DOCKER_MCP_URL",
    "jenkins": "STREAMLIT_JENKINS_MCP_URL",
    "kubernetes": "STREAMLIT_KUBERNETES_MCP_URL",
    "scheduler": "STREAMLIT_SCHEDULER_MCP_URL",
    "nexus": "STREAMLIT_NEXUS_MCP_URL",
    "git": "STREAMLIT_GIT_MCP_URL",
    "trivy": "STREAMLIT_TRIVY_MCP_URL",
    "playwright": "STREAMLIT_PLAYWRIGHT_MCP_URL",
    "websearch": "STREAMLIT_WEBSEARCH_MCP_URL",
}

# Default URLs for each server (used when env var not set)
MCP_SERVER_DEFAULTS = {
    "docker": "http://docker-mcp:8000",
    "jenkins": "http://jenkins-mcp:8000",
    "kubernetes": "http://kubernetes-mcp:8000",
    "scheduler": "http://scheduler:8010",
    "nexus": "http://nexus-mcp:8000",
    "git": "http://git-mcp:8000",
    "trivy": "http://trivy-mcp:8000",
    "playwright": "http://playwright-mcp:8000",
    "websearch": "http://websearch-mcp:8000",
}


def get_server_url(server_name: str) -> str:
    """Get the URL for an MCP server from environment or defaults."""
    env_var = MCP_SERVER_ENV_VARS.get(server_name.lower())
    if env_var:
        url = os.getenv(env_var)
        if url:
            return url
    return MCP_SERVER_DEFAULTS.get(server_name.lower(), f"http://{server_name}-mcp:8000")


class MCPClient:
    """Client for interacting with MCP servers using streamable-http protocol.

    This client uses direct HTTP requests to the /mcp endpoint, which is
    more reliable than the langchain_mcp_adapters library for health checks
    and tool invocations.
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._session_id: Optional[str] = None
        self._tools_cache: Optional[List[Dict[str, Any]]] = None
        self._initialized = False

    def _make_request(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        request_id: int = 1,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Make a JSON-RPC request to the MCP server.

        Returns:
            Tuple of (success, response_dict)
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        try:
            resp = requests.post(
                self.config.mcp_url,
                json=payload,
                headers=headers,
                timeout=self.config.timeout,
            )

            # Extract session ID from response if present
            new_session_id = resp.headers.get("mcp-session-id")
            if new_session_id:
                self._session_id = new_session_id

            if not (200 <= resp.status_code < 400):
                return False, {
                    "error": f"HTTP {resp.status_code}",
                    "body": resp.text[:500],
                }

            # Parse response (may be JSON or SSE)
            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                result = _extract_sse_json(resp.text)
                if result is None:
                    return False, {"error": "Failed to parse SSE response", "body": resp.text[:500]}
                return True, result
            else:
                try:
                    return True, resp.json()
                except Exception as e:
                    return False, {"error": f"JSON parse error: {e}", "body": resp.text[:500]}

        except requests.exceptions.ConnectionError as e:
            return False, {"error": f"Connection failed: {e}"}
        except requests.exceptions.Timeout:
            return False, {"error": f"Timeout after {self.config.timeout}s"}
        except Exception as e:
            return False, {"error": str(e)}

    def initialize(self) -> Tuple[bool, Dict[str, Any]]:
        """Initialize the MCP session.

        This must be called before invoking tools.
        """
        success, response = self._make_request(
            "initialize",
            {
                "protocolVersion": _mcp_protocol_version(),
                "capabilities": {},
                "clientInfo": {"name": "bsw-client", "version": "1.0"},
            },
            request_id=1,
        )

        if success:
            self._initialized = True

        return success, response

    def list_tools(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """List available tools from the MCP server.

        Args:
            force_refresh: If True, refresh the tools cache

        Returns:
            List of tool definitions
        """
        if self._tools_cache is not None and not force_refresh:
            return self._tools_cache

        # Initialize if not already done
        if not self._initialized:
            success, init_response = self.initialize()
            if not success:
                return []

        success, response = self._make_request("tools/list", {}, request_id=2)

        if not success:
            return []

        # Extract tools from response
        result = response.get("result", response)
        tools = result.get("tools", [])

        if isinstance(tools, list):
            self._tools_cache = tools
            return tools

        return []

    def invoke(
        self,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Invoke a tool on the MCP server.

        Args:
            tool_name: Name of the tool to invoke
            arguments: Arguments to pass to the tool

        Returns:
            Tool result dict with 'ok' key indicating success
        """
        # Initialize if not already done
        if not self._initialized:
            success, init_response = self.initialize()
            if not success:
                return {"ok": False, "error": f"Failed to initialize: {init_response.get('error')}"}

        success, response = self._make_request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments or {},
            },
            request_id=3,
        )

        if not success:
            return {"ok": False, "error": response.get("error", "Unknown error")}

        # Extract result from response
        result = response.get("result", response)

        # Handle text content blocks
        if isinstance(result, dict) and "content" in result:
            content = result.get("content", [])
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                if text_parts:
                    text = "\n".join(text_parts)
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        return {"ok": True, "text": text}

        # If result is already a dict with ok key, return it
        if isinstance(result, dict):
            if "ok" not in result:
                result["ok"] = True
            return result

        return {"ok": True, "result": result}

    def health_check(self) -> Dict[str, Any]:
        """Check if the MCP server is healthy.

        Returns:
            Dict with 'ok' key and health status
        """
        started = datetime.now()

        # Try to initialize and list tools
        success, init_response = self.initialize()

        if not success:
            return {
                "ok": False,
                "status": "unhealthy",
                "message": init_response.get("error", "Failed to connect"),
                "response_time_ms": int((datetime.now() - started).total_seconds() * 1000),
            }

        tools = self.list_tools()
        response_time = int((datetime.now() - started).total_seconds() * 1000)

        return {
            "ok": True,
            "status": "healthy",
            "message": f"{len(tools)} tools available",
            "tool_count": len(tools),
            "response_time_ms": response_time,
        }


# Session state cache for MCP clients
_CLIENT_CACHE_KEY = "_mcp_clients_cache"


def get_mcp_client(
    server_name: str,
    url: Optional[str] = None,
    timeout: float = 10.0,
    force_new: bool = False,
) -> MCPClient:
    """Get an MCP client for a server.

    Clients are cached in Streamlit session state.

    Args:
        server_name: Name of the MCP server (e.g., "kubernetes", "docker")
        url: Optional URL override (defaults to env var or built-in default)
        timeout: Request timeout in seconds
        force_new: If True, create a new client even if one is cached

    Returns:
        MCPClient instance
    """
    if _CLIENT_CACHE_KEY not in st.session_state:
        st.session_state[_CLIENT_CACHE_KEY] = {}

    cache = st.session_state[_CLIENT_CACHE_KEY]

    # Resolve URL
    resolved_url = url or get_server_url(server_name)
    cache_key = f"{server_name}:{resolved_url}"

    if not force_new and cache_key in cache:
        return cache[cache_key]

    config = MCPServerConfig(
        name=server_name,
        url=resolved_url,
        timeout=timeout,
    )

    client = MCPClient(config)
    cache[cache_key] = client

    return client


def invoke_mcp_tool(
    server_name: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    url: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Convenience function to invoke an MCP tool.

    Args:
        server_name: Name of the MCP server
        tool_name: Name of the tool to invoke
        arguments: Tool arguments
        url: Optional URL override
        timeout: Request timeout

    Returns:
        Tool result dict
    """
    client = get_mcp_client(server_name, url=url, timeout=timeout)
    return client.invoke(tool_name, arguments)


def list_mcp_tools(
    server_name: str,
    url: Optional[str] = None,
    timeout: float = 10.0,
    force_refresh: bool = False,
) -> List[Dict[str, Any]]:
    """Convenience function to list tools from an MCP server.

    Args:
        server_name: Name of the MCP server
        url: Optional URL override
        timeout: Request timeout
        force_refresh: If True, refresh the tools cache

    Returns:
        List of tool definitions
    """
    client = get_mcp_client(server_name, url=url, timeout=timeout)
    return client.list_tools(force_refresh=force_refresh)


def check_mcp_health(
    server_name: str,
    url: Optional[str] = None,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Convenience function to check MCP server health.

    Args:
        server_name: Name of the MCP server
        url: Optional URL override
        timeout: Request timeout

    Returns:
        Health status dict
    """
    client = get_mcp_client(server_name, url=url, timeout=timeout, force_new=True)
    return client.health_check()
