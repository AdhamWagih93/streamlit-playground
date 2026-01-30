"""Nexus core tools implementation."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .client_factory import auth_or_error, client_from_env, require_raw_enabled


def nexus_health_check(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Basic connectivity + version check."""
    err = auth_or_error(_client_token)
    if err:
        return err

    c = client_from_env()
    status = c.request("GET", "/service/rest/v1/status")
    version = c.request("GET", "/service/rest/v1/status/writable")

    return {
        "ok": bool(status.ok),
        "status": status.to_dict(),
        "writable": version.to_dict(),
    }


def nexus_get_system_status(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """Get Nexus system status."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = client_from_env()
    return c.request("GET", "/service/rest/v1/status").to_dict()


def nexus_list_repositories(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List repositories visible to the configured credentials."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = client_from_env()
    return c.request("GET", "/service/rest/v1/repositories").to_dict()


def nexus_list_blobstores(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List blob stores."""
    err = auth_or_error(_client_token)
    if err:
        return err
    c = client_from_env()
    return c.request("GET", "/service/rest/v1/blobstores").to_dict()


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
    """Search components (REST v1 search API)."""
    err = auth_or_error(_client_token)
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

    c = client_from_env()
    return c.request("GET", "/service/rest/v1/search", params=params).to_dict()


def nexus_list_assets(
    repository: Optional[str] = None,
    format: Optional[str] = None,
    group: Optional[str] = None,
    name: Optional[str] = None,
    version: Optional[str] = None,
    continuation_token: Optional[str] = None,
    _client_token: Optional[str] = None,
) -> Dict[str, Any]:
    """List assets (REST v1 search/assets API)."""
    err = auth_or_error(_client_token)
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

    c = client_from_env()
    return c.request("GET", "/service/rest/v1/search/assets", params=params).to_dict()


def nexus_get_asset(asset_id: str, _client_token: Optional[str] = None) -> Dict[str, Any]:
    """Fetch asset metadata by ID."""
    err = auth_or_error(_client_token)
    if err:
        return err

    c = client_from_env()
    return c.request("GET", f"/service/rest/v1/assets/{asset_id}").to_dict()


def nexus_list_users(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List users (requires appropriate permissions)."""
    err = auth_or_error(_client_token)
    if err:
        return err

    c = client_from_env()
    return c.request("GET", "/service/rest/v1/security/users").to_dict()


def nexus_list_roles(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List roles."""
    err = auth_or_error(_client_token)
    if err:
        return err

    c = client_from_env()
    return c.request("GET", "/service/rest/v1/security/roles").to_dict()


def nexus_list_tasks(_client_token: Optional[str] = None) -> Dict[str, Any]:
    """List scheduled tasks."""
    err = auth_or_error(_client_token)
    if err:
        return err

    c = client_from_env()
    return c.request("GET", "/service/rest/v1/tasks").to_dict()


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
    Disabled by default. Enable with NEXUS_ALLOW_RAW=true.
    """
    err = auth_or_error(_client_token)
    if err:
        return err

    raw_err = require_raw_enabled()
    if raw_err:
        return raw_err

    c = client_from_env()
    return c.request(method, path, params=params, json_body=json_body, headers=headers).to_dict()
