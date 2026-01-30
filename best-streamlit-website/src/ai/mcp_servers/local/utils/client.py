from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests


def _get_mcp_url(url: str) -> str:
    base = (url or "").strip().rstrip("/")
    if base.endswith("/mcp"):
        return base
    return base + "/mcp"


@dataclass
class LocalMCPClient:
    """Minimal HTTP client for the Local MCP server (streamable-http)."""

    url: Optional[str] = None
    timeout: float = 10.0

    def __post_init__(self) -> None:
        base = self.url or os.environ.get("LOCAL_MCP_URL", "http://local-mcp:8000")
        self._mcp_url = _get_mcp_url(base)
        self._session_id: Optional[str] = None

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None, request_id: int = 1) -> Tuple[bool, Dict[str, Any]]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id

        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        try:
            resp = requests.post(self._mcp_url, json=payload, headers=headers, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            return False, {"error": str(exc)}

        new_session_id = resp.headers.get("mcp-session-id")
        if new_session_id:
            self._session_id = new_session_id

        if resp.status_code >= 400:
            return False, {"error": f"HTTP {resp.status_code}", "body": resp.text[:500]}

        try:
            data = resp.json()
        except Exception:
            return False, {"error": "Invalid JSON response", "body": resp.text[:500]}
        return True, data

    def initialize(self) -> bool:
        ok, _ = self._request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "local-mcp-client", "version": "1.0"},
            },
        )
        return ok

    def list_tools(self) -> List[Dict[str, Any]]:
        if not self._session_id:
            self.initialize()
        ok, data = self._request("tools/list", request_id=2)
        if not ok:
            return []
        result = data.get("result", data)
        tools = result.get("tools", result if isinstance(result, list) else [])
        return list(tools or [])

    def invoke(self, tool: str, args: Dict[str, Any]) -> Any:
        if not self._session_id:
            self.initialize()
        ok, data = self._request("tools/call", {"name": tool, "arguments": args}, request_id=3)
        if not ok:
            return {"ok": False, "error": data.get("error")}
        return data.get("result", data)
