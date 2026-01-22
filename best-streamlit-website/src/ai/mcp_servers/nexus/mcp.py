from __future__ import annotations

import inspect
import os
from typing import Any, Dict, Optional

from fastmcp import FastMCP

from .config import NexusMCPServerConfig
from .utils.client import NexusClient


mcp = FastMCP("nexus-mcp")

_CLIENT: Optional[NexusClient] = None


def _client_from_env() -> NexusClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    cfg = NexusMCPServerConfig.from_env()
    _CLIENT = NexusClient(
        base_url=cfg.base_url,
        username=cfg.username,
        password=cfg.password,
        token=cfg.token,
        verify_ssl=cfg.verify_ssl,
    )
    return _CLIENT


def _auth_or_error(_client_token: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate client token for MCP tools.

    If NEXUS_MCP_CLIENT_TOKEN is not configured, tools are open (useful for
    local dev). When set, callers must pass matching `_client_token`.
    """

    expected = os.environ.get("NEXUS_MCP_CLIENT_TOKEN")
    if not expected:
        return None
    if _client_token != expected:
        return {
            "ok": False,
            "error": "unauthorized",
            "hint": "Invalid or missing client token.",
        }
    return None


def _require_raw_enabled() -> Optional[Dict[str, Any]]:
    cfg = NexusMCPServerConfig.from_env()
    if not cfg.allow_raw:
        return {
            "ok": False,
            "error": "nexus_raw_request is disabled. Set NEXUS_ALLOW_RAW=true to enable.",
        }
    return None


# ----------------------------- Core tools -----------------------------


@mcp.tool
def nexus_health_check(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Basic connectivity + version check."""

    err = _auth_or_error(_client_token)
    if err:
        return err

    c = _client_from_env()

    # Status is generally open; version may require auth depending on setup.
    status = c.request("GET", "/service/rest/v1/status")
    version = c.request("GET", "/service/rest/v1/status/writable")

    return {
        "ok": bool(status.ok),
        "status": status.to_dict(),
        "writable": version.to_dict(),
    }


@mcp.tool
def nexus_get_system_status(_client_token: Optional[str] = None) -> Dict[str, Any]:
    err = _auth_or_error(_client_token)
    if err:
        return err
    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/status").to_dict()


@mcp.tool
def nexus_list_repositories(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List repositories visible to the configured credentials."""

    err = _auth_or_error(_client_token)
    if err:
        return err
    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/repositories").to_dict()


@mcp.tool
def nexus_list_blobstores(_client_token: Optional[str] = None) -> Dict[str, Any]:
    err = _auth_or_error(_client_token)
    if err:
        return err
    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/blobstores").to_dict()


@mcp.tool
def nexus_search_components(
    q: Optional[str] = None,
    repository: Optional[str] = None,
    format: Optional[str] = None,
    group: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[str] = None,
    continuation_token: Optional[str] = None,
    _client_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Search components (REST v1 search API).

    This maps to GET /service/rest/v1/search.
    """

    err = _auth_or_error(_client_token)
    if err:
        return err

    params: Dict[str, Any] = {}
    if q:
        params["q"] = q
    if repository:
        params["repository"] = repository
    if format:
        params["format"] = format
    if group:
        params["group"] = group
    if name:
        params["name"] = name
    if version:
        params["version"] = version
    if continuation_token:
        params["continuationToken"] = continuation_token

    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/search", params=params).to_dict()


@mcp.tool
def nexus_list_assets(
    repository: Optional[str] = None,
    format: Optional[str] = None,
    group: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[str] = None,
    continuation_token: Optional[str] = None,
    _client_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List assets (REST v1 search/assets API).

    Maps to GET /service/rest/v1/search/assets.
    """

    err = _auth_or_error(_client_token)
    if err:
        return err

    params: Dict[str, Any] = {}
    if repository:
        params["repository"] = repository
    if format:
        params["format"] = format
    if group:
        params["group"] = group
    if name:
        params["name"] = name
    if version:
        params["version"] = version
    if continuation_token:
        params["continuationToken"] = continuation_token

    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/search/assets", params=params).to_dict()


@mcp.tool
def nexus_get_asset(asset_id: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Fetch asset metadata by ID."""

    err = _auth_or_error(_client_token)
    if err:
        return err

    c = _client_from_env()
    return c.request("GET", f"/service/rest/v1/assets/{asset_id}").to_dict()


@mcp.tool
def nexus_list_users(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List users (requires appropriate permissions)."""

    err = _auth_or_error(_client_token)
    if err:
        return err

    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/security/users").to_dict()


@mcp.tool
def nexus_list_roles(_client_token: Optional[str] = None) -> Dict[str, Any]:
    err = _auth_or_error(_client_token)
    if err:
        return err

    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/security/roles").to_dict()


@mcp.tool
def nexus_list_tasks(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List scheduled tasks."""

    err = _auth_or_error(_client_token)
    if err:
        return err

    c = _client_from_env()
    return c.request("GET", "/service/rest/v1/tasks").to_dict()


# ----------------------------- Raw passthrough -----------------------------


@mcp.tool
def nexus_raw_request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
    headers: Optional[Dict[str, str]] = None,
    _client_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Generic Nexus REST passthrough.

    Use this to reach endpoints not covered by dedicated tools.

    Safety:
    - Disabled by default. Enable with NEXUS_ALLOW_RAW=true.
    - Intended for API calls only (not for downloading large binaries).
    """

    err = _auth_or_error(_client_token)
    if err:
        return err

    raw_err = _require_raw_enabled()
    if raw_err:
        return raw_err

    c = _client_from_env()
    return c.request(method, path, params=params, json_body=json_body, headers=headers).to_dict()


def run_stdio() -> None:
    cfg = NexusMCPServerConfig.from_env()
    transport = (os.environ.get("MCP_TRANSPORT") or cfg.mcp_transport or "stdio").lower().strip()

    if transport == "stdio":
        mcp.run(transport="stdio")
        return

    host = os.environ.get("MCP_HOST") or cfg.mcp_host
    port_raw = os.environ.get("MCP_PORT")
    try:
        port = int(port_raw) if port_raw else int(cfg.mcp_port)
    except Exception:
        port = int(cfg.mcp_port)

    sig = inspect.signature(mcp.run)
    kwargs: Dict[str, Any] = {"transport": transport}
    if "host" in sig.parameters:
        kwargs["host"] = host
    if "port" in sig.parameters:
        kwargs["port"] = port

    mcp.run(**kwargs)


if __name__ == "__main__":
    run_stdio()
