from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


@dataclass(frozen=True)
class NexusResponse:
    ok: bool
    status_code: int
    url: str
    method: str
    data: Any = None
    text: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "url": self.url,
            "method": self.method,
            "data": self.data,
            "text": self.text,
            "error": self.error,
        }


class NexusClient:
    def __init__(
        self,
        *,
        base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        token: Optional[str] = None,
        verify_ssl: bool = True,
        timeout_seconds: int = 30,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.token = token
        self.verify_ssl = bool(verify_ssl)
        self.timeout_seconds = int(timeout_seconds)

        self._session = requests.Session()

    def _build_headers(self, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        merged: Dict[str, str] = {
            "Accept": "application/json",
        }
        if self.token:
            merged["Authorization"] = f"Bearer {self.token}"
        if headers:
            merged.update({k: str(v) for k, v in headers.items()})
        return merged

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Any = None,
        data: Any = None,
        headers: Optional[Dict[str, str]] = None,
        timeout_seconds: Optional[int] = None,
    ) -> NexusResponse:
        method_u = (method or "GET").upper().strip()
        path = path or ""
        if not path.startswith("/"):
            path = "/" + path
        url = f"{self.base_url}{path}"

        auth = None
        if self.username and self.password and not self.token:
            auth = (self.username, self.password)

        try:
            resp = self._session.request(
                method_u,
                url,
                params=params,
                json=json_body,
                data=data,
                headers=self._build_headers(headers),
                auth=auth,
                verify=self.verify_ssl,
                timeout=(timeout_seconds or self.timeout_seconds),
            )
        except Exception as exc:  # noqa: BLE001
            return NexusResponse(
                ok=False,
                status_code=0,
                url=url,
                method=method_u,
                error=str(exc),
            )

        content_type = (resp.headers.get("Content-Type") or "").lower()
        text = None
        parsed: Any = None

        try:
            text = resp.text
        except Exception:
            text = None

        if "application/json" in content_type:
            try:
                parsed = resp.json()
            except Exception:
                parsed = None
        else:
            # Some endpoints return text/plain even when JSON-ish.
            if text:
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None

        ok = 200 <= int(resp.status_code) < 300
        if ok:
            return NexusResponse(
                ok=True,
                status_code=int(resp.status_code),
                url=url,
                method=method_u,
                data=parsed if parsed is not None else text,
            )

        # Error case: return best available detail
        error_detail = None
        if isinstance(parsed, dict):
            error_detail = parsed
        elif text:
            error_detail = text[:2000]

        return NexusResponse(
            ok=False,
            status_code=int(resp.status_code),
            url=url,
            method=method_u,
            data=parsed,
            text=text[:2000] if text else None,
            error=str(error_detail) if error_detail is not None else f"HTTP {resp.status_code}",
        )
