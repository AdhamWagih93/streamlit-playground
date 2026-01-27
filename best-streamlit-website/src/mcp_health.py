"""MCP server health utilities with comprehensive debugging."""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse

import requests
import streamlit as st


def _get_base_url(url: str) -> str:
    """Extract base URL without /mcp suffix for HTTP health checks.

    MCP servers expose:
    - HTTP endpoints at base URL (e.g., http://host:8000/health)
    - MCP protocol at /mcp endpoint (e.g., http://host:8000/mcp)

    This function returns the base URL for HTTP health checks.
    """
    base = (url or "").strip().rstrip("/")
    if base.endswith("/mcp"):
        base = base[:-4]  # Remove /mcp suffix
    return base


def _get_mcp_url(url: str) -> str:
    """Get the MCP protocol URL (with /mcp suffix).

    MCP protocol connections need the /mcp endpoint.
    """
    base = _get_base_url(url)
    if not base:
        return base
    return base + "/mcp"


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


def _mcp_protocol_version() -> str:
    try:
        from mcp import types as mcp_types  # type: ignore

        return (
            getattr(mcp_types, "LATEST_PROTOCOL_VERSION", None)
            or getattr(mcp_types, "PROTOCOL_VERSION", None)
            or "2025-11-25"
        )
    except Exception:
        return "2025-11-25"


def _extract_sse_json(text: str) -> Optional[Dict[str, Any]]:
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


async def check_mcp_server_http_simple(url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Simple HTTP health check - just test if server responds.

    Args:
        url: Base URL of the MCP server (may include /mcp suffix)
        timeout: Timeout in seconds

    Returns:
        Dict with status and details
    """
    try:
        import aiohttp
    except ModuleNotFoundError:
        aiohttp = None

    # Get base URL (without /mcp) for HTTP endpoints
    base_url = _get_base_url(url)
    mcp_url = _get_mcp_url(url)

    # Try common MCP/FastMCP endpoints
    # HTTP health endpoints are on base URL, MCP protocol is on /mcp
    endpoints_to_try = [
        {"url": base_url + "/health", "method": "GET"},
        {"url": base_url + "/", "method": "GET"},
        {
            "url": mcp_url,
            "method": "POST",
            "json": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _mcp_protocol_version(),
                    "capabilities": {},
                    "clientInfo": {"name": "bsw-health", "version": "1.0"},
                },
            },
        },
        {"url": base_url + "/sse", "method": "GET"},
    ]

    started = datetime.now()
    results = []

    if aiohttp is None:
        for endpoint in endpoints_to_try:
            check_url = endpoint["url"]
            try:
                if endpoint.get("method") == "POST":
                        resp = requests.post(
                            check_url,
                            json=endpoint.get("json"),
                            headers={"Accept": "application/json, text/event-stream"},
                            timeout=timeout,
                        )
                else:
                    resp = requests.get(check_url, timeout=timeout)

                status_code = resp.status_code
                content_type = resp.headers.get("content-type", "")
                body_preview = (resp.text or "")[:500]

                results.append({
                    "endpoint": check_url,
                    "url": check_url,
                    "status_code": status_code,
                    "content_type": content_type,
                    "body_preview": body_preview,
                    "ok": 200 <= status_code < 400,
                })

                if 200 <= status_code < 400:
                    response_time = int((datetime.now() - started).total_seconds() * 1000)
                    return {
                        "status": "healthy",
                        "message": f"HTTP {status_code} on {check_url}",
                        "response_time_ms": response_time,
                        "endpoint": check_url,
                        "content_type": content_type,
                        "debug": results,
                    }
            except Exception as e:
                results.append({
                    "endpoint": check_url,
                    "url": check_url,
                    "error": str(e),
                })
    else:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                for endpoint in endpoints_to_try:
                    check_url = endpoint["url"]
                    try:
                        if endpoint.get("method") == "POST":
                            async with session.post(
                                check_url,
                                    json=endpoint.get("json"),
                                    headers={"Accept": "application/json, text/event-stream"},
                            ) as resp:
                                status_code = resp.status
                                content_type = resp.headers.get("content-type", "")
                                try:
                                    body = await resp.text()
                                    body_preview = body[:500] if body else ""
                                except Exception:
                                    body_preview = ""

                                results.append({
                                    "endpoint": check_url,
                                    "url": check_url,
                                    "status_code": status_code,
                                    "content_type": content_type,
                                    "body_preview": body_preview,
                                    "ok": 200 <= status_code < 400,
                                })

                                if 200 <= status_code < 400:
                                    response_time = int((datetime.now() - started).total_seconds() * 1000)
                                    return {
                                        "status": "healthy",
                                        "message": f"HTTP {status_code} on {check_url}",
                                        "response_time_ms": response_time,
                                        "endpoint": check_url,
                                        "content_type": content_type,
                                        "debug": results,
                                    }
                        else:
                            async with session.get(check_url) as resp:
                                status_code = resp.status
                                content_type = resp.headers.get("content-type", "")
                                try:
                                    body = await resp.text()
                                    body_preview = body[:500] if body else ""
                                except Exception:
                                    body_preview = ""

                                results.append({
                                    "endpoint": check_url,
                                    "url": check_url,
                                    "status_code": status_code,
                                    "content_type": content_type,
                                    "body_preview": body_preview,
                                    "ok": 200 <= status_code < 400,
                                })

                                if 200 <= status_code < 400:
                                    response_time = int((datetime.now() - started).total_seconds() * 1000)
                                    return {
                                        "status": "healthy",
                                        "message": f"HTTP {status_code} on {check_url}",
                                        "response_time_ms": response_time,
                                        "endpoint": check_url,
                                        "content_type": content_type,
                                        "debug": results,
                                    }

                    except asyncio.TimeoutError:
                        results.append({
                            "endpoint": check_url,
                            "url": check_url,
                            "error": "timeout",
                        })
                    except Exception as e:
                        results.append({
                            "endpoint": check_url,
                            "url": check_url,
                            "error": str(e),
                        })
        except Exception as e:
            results.append({"error": f"Session error: {e}"})

    response_time = int((datetime.now() - started).total_seconds() * 1000)
    return {
        "status": "unhealthy",
        "message": "No endpoints responded",
        "response_time_ms": response_time,
        "debug": results,
    }


async def check_mcp_server_with_client(
    server_name: str,
    url: str,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """Check MCP server using langchain_mcp_adapters client.

    Args:
        server_name: Name of the MCP server
        url: URL to connect to (may include /mcp suffix)
        timeout: Timeout in seconds

    Returns:
        Dict with status, message, tool_count, and debug info
    """
    started = datetime.now()
    base_url = _get_base_url(url)
    mcp_url = _get_mcp_url(url)

    debug_info: Dict[str, Any] = {
        "server_name": server_name,
        "original_url": url,
        "base_url": base_url,
        "mcp_url": mcp_url,
        "timeout": timeout,
        "attempts": [],
    }

    # Try direct streamable-http initialize + tools/list first to avoid adapter/session warnings.
    try:
        headers = {
            "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
        }
        init_resp = requests.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _mcp_protocol_version(),
                    "capabilities": {},
                    "clientInfo": {"name": "bsw-health", "version": "1.0"},
                },
            },
            headers=headers,
            timeout=timeout,
        )
        resp = init_resp
        attempt = {
            "config": {"transport": "streamable-http", "url": mcp_url},
            "started": datetime.now().isoformat(),
            "status_code": resp.status_code,
        }
        if 200 <= resp.status_code < 400:
            session_id = resp.headers.get("mcp-session-id")
            if not session_id:
                attempt["error"] = "Missing mcp-session-id"
                attempt["error_type"] = "protocol"
                debug_info["attempts"].append(attempt)
                raise ValueError("Missing mcp-session-id")
            tools_resp = requests.post(
                mcp_url,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                },
                headers={**headers, "mcp-session-id": session_id},
                timeout=timeout,
            )
            if 200 <= tools_resp.status_code < 400:
                content_type = tools_resp.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    payload = _extract_sse_json(tools_resp.text) or {}
                else:
                    try:
                        payload = tools_resp.json()
                    except Exception:
                        payload = {}
                result = payload.get("result", payload)
                tools_payload = result.get("tools", result if isinstance(result, list) else [])
                tool_names = [
                    (t.get("name") if isinstance(t, dict) else str(t)) for t in (tools_payload or [])
                ]
                tool_count = len(tool_names)
                response_time = int((datetime.now() - started).total_seconds() * 1000)
                attempt["success"] = True
                attempt["tool_count"] = tool_count
                attempt["tool_names"] = tool_names[:10]
                debug_info["attempts"].append(attempt)
                debug_info["successful_config"] = attempt.get("config")

                return {
                    "status": "healthy",
                    "message": f"{tool_count} tools available",
                    "response_time_ms": response_time,
                    "tool_count": tool_count,
                    "tool_names": tool_names[:10],
                    "transport": "streamable-http",
                    "last_checked": datetime.now().isoformat(),
                    "debug": debug_info,
                }
        else:
            attempt["error"] = f"HTTP {resp.status_code}"
            attempt["error_type"] = "http"
            debug_info["attempts"].append(attempt)
    except Exception as e:
        debug_info["attempts"].append({
            "config": {"transport": "streamable-http", "url": mcp_url},
            "started": datetime.now().isoformat(),
            "error": str(e),
            "error_type": type(e).__name__,
        })

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as e:
        return {
            "status": "unhealthy",
            "message": "langchain_mcp_adapters not installed",
            "response_time_ms": 0,
            "tool_count": 0,
            "last_checked": datetime.now().isoformat(),
            "debug": {"error": str(e)},
        }

    # Try different transport configurations
    # FastMCP with transport="http" uses streamable-http protocol at /mcp endpoint
    transport_configs = [
        # SSE fallback on base URL
        {"transport": "sse", "url": base_url},
        # SSE with /sse suffix
        {"transport": "sse", "url": base_url + "/sse"},
    ]

    last_error = None
    last_error_type = None

    for config in transport_configs:
        attempt = {
            "config": config,
            "started": datetime.now().isoformat(),
        }

        try:
            client = MultiServerMCPClient({server_name: config})

            tools = await asyncio.wait_for(
                client.get_tools(),
                timeout=timeout,
            )

            tool_list = list(tools or [])
            tool_count = len(tool_list)
            tool_names = [getattr(t, "name", str(t)) for t in tool_list[:10]]

            response_time = int((datetime.now() - started).total_seconds() * 1000)

            attempt["success"] = True
            attempt["tool_count"] = tool_count
            attempt["tool_names"] = tool_names
            debug_info["attempts"].append(attempt)
            debug_info["successful_config"] = config

            return {
                "status": "healthy",
                "message": f"{tool_count} tools available",
                "response_time_ms": response_time,
                "tool_count": tool_count,
                "tool_names": tool_names,
                "transport": config.get("transport"),
                "last_checked": datetime.now().isoformat(),
                "debug": debug_info,
            }

        except asyncio.TimeoutError:
            attempt["error"] = "timeout"
            attempt["error_type"] = "TimeoutError"
            debug_info["attempts"].append(attempt)
            last_error = f"Timeout after {timeout}s"
            last_error_type = "timeout"

        except asyncio.CancelledError:
            attempt["error"] = "cancelled"
            attempt["error_type"] = "CancelledError"
            debug_info["attempts"].append(attempt)
            last_error = "Request cancelled"
            last_error_type = "cancelled"

        except BaseException as e:
            # Catch BaseException to handle ExceptionGroup/TaskGroup errors
            error_msg = str(e)
            error_type = type(e).__name__
            error_tb = traceback.format_exc()

            attempt["error"] = error_msg
            attempt["error_type"] = error_type
            attempt["traceback"] = error_tb[:1000]
            debug_info["attempts"].append(attempt)
            last_error = error_msg
            last_error_type = error_type

            # For ExceptionGroup/TaskGroup errors, extract the underlying cause
            if hasattr(e, "exceptions"):
                sub_errors = []
                for sub_e in getattr(e, "exceptions", []):
                    sub_errors.append({
                        "type": type(sub_e).__name__,
                        "message": str(sub_e)[:200],
                    })
                attempt["sub_exceptions"] = sub_errors
                if sub_errors:
                    last_error = sub_errors[0].get("message", error_msg)

    # All attempts failed
    response_time = int((datetime.now() - started).total_seconds() * 1000)

    # Determine the best error message
    if last_error_type == "timeout":
        message = f"Timeout after {timeout}s"
    elif "Connection refused" in str(last_error):
        message = "Connection refused - service not running"
    elif "404" in str(last_error) or "Not Found" in str(last_error):
        message = "Service not found (404)"
    elif "TaskGroup" in str(last_error) or "ExceptionGroup" in str(last_error_type or ""):
        message = "Connection failed - check server logs"
    elif "connect" in str(last_error).lower():
        message = "Connection failed"
    else:
        message = f"Error: {str(last_error)[:80]}"

    return {
        "status": "unhealthy",
        "message": message,
        "response_time_ms": response_time,
        "tool_count": 0,
        "last_checked": datetime.now().isoformat(),
        "debug": debug_info,
    }


async def check_mcp_server_simple(
    server_name: str,
    url: str,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Simple MCP health check with fallbacks.

    First tries HTTP health check, then tries MCP client connection.

    Args:
        server_name: Name of the MCP server
        url: URL to connect to
        timeout: Timeout in seconds

    Returns:
        Dict with status, message, response_time_ms, tool_count
    """
    started = datetime.now()
    debug_info: Dict[str, Any] = {
        "server_name": server_name,
        "url": url,
        "checks": [],
    }

    # First, try simple HTTP check
    http_result = None
    try:
        http_result = await check_mcp_server_http_simple(url, timeout=timeout / 2)
        debug_info["checks"].append({"type": "http", "result": http_result})
        if http_result.get("status") == "healthy":
            return http_result
    except Exception as e:
        debug_info["checks"].append({"type": "http", "error": str(e)})

    # Try to connect with MCP client
    try:
        client_result = await check_mcp_server_with_client(
            server_name,
            url,
            timeout=timeout,
        )
        debug_info["checks"].append({"type": "mcp_client", "result": client_result})

        # Merge debug info
        final_debug = {**debug_info, **client_result.get("debug", {})}
        client_result["debug"] = final_debug

        return client_result

    except Exception as e:
        error_msg = str(e)
        debug_info["checks"].append({
            "type": "mcp_client",
            "error": error_msg,
            "traceback": traceback.format_exc()[:1000],
        })

    # All checks failed
    response_time = int((datetime.now() - started).total_seconds() * 1000)

    return {
        "status": "unhealthy",
        "message": "All health checks failed",
        "response_time_ms": response_time,
        "tool_count": 0,
        "last_checked": datetime.now().isoformat(),
        "debug": debug_info,
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


def show_debug_info(debug_data: Dict[str, Any]) -> None:
    """Display debug information in an expander."""
    with st.expander("Debug Information", expanded=False):
        st.json(debug_data)
