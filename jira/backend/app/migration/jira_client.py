"""Thin Jira REST client built on httpx.

Supports both Jira Cloud (HTTP Basic with email + API token) and Jira
Server/Data Center (Bearer Personal Access Token). Paginated iterators
transparently handle ``startAt``/``maxResults``/``isLast``/``total`` and the
newer token-based ``/search/jql`` cursor. Requests retry with backoff on 429
(honouring ``Retry-After``) and transient 5xx responses.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

log = logging.getLogger("trackly.migration.jira")

DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 5
BACKOFF_BASE = 1.5
PAGE_SIZE = 100


class JiraClient:
    """Minimal Jira REST v3/v2 client with pagination and retry support."""

    def __init__(
        self,
        base_url: str,
        email: str = "",
        api_token: str = "",
        verify: bool = True,
        server_token: bool = False,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.email = (email or "").strip()
        self.api_token = api_token
        # Server/DC PAT mode: no email -> Bearer auth.
        self.server_token = server_token or not self.email

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        auth: tuple[str, str] | None = None
        if self.server_token:
            headers["Authorization"] = f"Bearer {api_token}"
        else:
            auth = (self.email, api_token)

        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            auth=auth,
            verify=verify,
            timeout=timeout,
            follow_redirects=True,
        )

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level request with retry/backoff ------------------------------
    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self._client.request(method, path, **kwargs)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                wait = BACKOFF_BASE ** attempt
                log.warning("Network error on %s %s (attempt %s/%s): %s; retrying in %.1fs",
                            method, path, attempt, MAX_RETRIES, exc, wait)
                time.sleep(wait)
                continue

            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                wait = float(retry_after) if retry_after and retry_after.isdigit() else BACKOFF_BASE ** attempt
                log.warning("Rate limited on %s %s; sleeping %.1fs (attempt %s/%s)",
                            method, path, wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue

            if 500 <= resp.status_code < 600:
                wait = BACKOFF_BASE ** attempt
                log.warning("Server error %s on %s %s; retrying in %.1fs (attempt %s/%s)",
                            resp.status_code, method, path, wait, attempt, MAX_RETRIES)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Exhausted retries for {method} {path}")

    def _get(self, path: str, params: dict | None = None) -> Any:
        return self._request("GET", path, params=params).json()

    def _post(self, path: str, json: dict | None = None, params: dict | None = None) -> Any:
        return self._request("POST", path, json=json, params=params).json()

    def _try_get(self, paths: list[str], params: dict | None = None) -> Any:
        """GET the first path that does not 404, for v3/v2 fallbacks."""
        last_exc: httpx.HTTPStatusError | None = None
        for path in paths:
            try:
                return self._get(path, params=params)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    last_exc = exc
                    continue
                raise
        if last_exc:
            raise last_exc
        raise RuntimeError("No paths supplied")

    # -- identity ----------------------------------------------------------
    def get_myself(self) -> dict:
        return self._try_get(["/rest/api/3/myself", "/rest/api/2/myself"])

    # -- projects ----------------------------------------------------------
    def iter_projects(self) -> Iterator[dict]:
        """Yield every visible project (paginated v3, v2 fallback)."""
        start_at = 0
        used_search = True
        while True:
            try:
                page = self._get(
                    "/rest/api/3/project/search",
                    params={"startAt": start_at, "maxResults": PAGE_SIZE},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    used_search = False
                    break
                raise
            values = page.get("values", [])
            for proj in values:
                yield proj
            if page.get("isLast") or not values:
                break
            start_at += len(values)
        if not used_search:
            # Server/DC: GET /rest/api/2/project returns a flat list.
            for proj in self._try_get(["/rest/api/2/project", "/rest/api/3/project"]):
                yield proj

    def get_project(self, key: str) -> dict:
        return self._try_get([f"/rest/api/3/project/{key}", f"/rest/api/2/project/{key}"])

    # -- users -------------------------------------------------------------
    def iter_users(self) -> Iterator[dict]:
        start_at = 0
        while True:
            try:
                page = self._get(
                    "/rest/api/3/users/search",
                    params={"startAt": start_at, "maxResults": PAGE_SIZE},
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 404):
                    log.warning("User listing unavailable (%s); users will be created lazily from issues",
                                exc.response.status_code)
                    return
                raise
            if not page:
                break
            for user in page:
                yield user
            if len(page) < PAGE_SIZE:
                break
            start_at += len(page)

    # -- field metadata ----------------------------------------------------
    def get_fields(self) -> list[dict]:
        return self._try_get(["/rest/api/3/field", "/rest/api/2/field"])

    def get_statuses(self) -> list[dict]:
        return self._try_get(["/rest/api/3/status", "/rest/api/2/status"])

    def get_priorities(self) -> list[dict]:
        return self._try_get(["/rest/api/3/priority", "/rest/api/2/priority"])

    def get_issue_types(self) -> list[dict]:
        return self._try_get(["/rest/api/3/issuetype", "/rest/api/2/issuetype"])

    # -- issues ------------------------------------------------------------
    def get_issue(self, key: str, fields: str | None = None, expand: str | None = None) -> dict:
        params: dict[str, Any] = {}
        if fields:
            params["fields"] = fields
        if expand:
            params["expand"] = expand
        return self._try_get(
            [f"/rest/api/3/issue/{key}", f"/rest/api/2/issue/{key}"],
            params=params or None,
        )

    def iter_issues(
        self,
        jql: str,
        fields: list[str] | str | None = None,
        expand: str | None = None,
    ) -> Iterator[dict]:
        """Yield issues matching *jql*.

        Prefers the modern token-paginated ``POST /rest/api/3/search/jql`` and
        falls back to classic ``startAt`` pagination on ``/search``.
        """
        field_list: list[str] | None
        if isinstance(fields, str):
            field_list = [f.strip() for f in fields.split(",") if f.strip()]
        else:
            field_list = list(fields) if fields else None

        try:
            yield from self._iter_issues_token(jql, field_list, expand)
            return
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (404, 410):
                raise
            log.info("/search/jql unavailable (%s); falling back to classic /search",
                     exc.response.status_code)
        yield from self._iter_issues_classic(jql, field_list, expand)

    def _iter_issues_token(
        self, jql: str, fields: list[str] | None, expand: str | None
    ) -> Iterator[dict]:
        next_token: str | None = None
        while True:
            body: dict[str, Any] = {"jql": jql, "maxResults": PAGE_SIZE}
            if fields:
                body["fields"] = fields
            if expand:
                body["expand"] = [e.strip() for e in expand.split(",")]
            if next_token:
                body["nextPageToken"] = next_token
            page = self._post("/rest/api/3/search/jql", json=body)
            issues = page.get("issues", [])
            for issue in issues:
                yield issue
            next_token = page.get("nextPageToken")
            if page.get("isLast") or not next_token or not issues:
                break

    def _iter_issues_classic(
        self, jql: str, fields: list[str] | None, expand: str | None
    ) -> Iterator[dict]:
        start_at = 0
        while True:
            body: dict[str, Any] = {"jql": jql, "startAt": start_at, "maxResults": PAGE_SIZE}
            if fields:
                body["fields"] = fields
            if expand:
                body["expand"] = [e.strip() for e in expand.split(",")]
            try:
                page = self._post("/rest/api/3/search", json=body)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    page = self._post("/rest/api/2/search", json=body)
                else:
                    raise
            issues = page.get("issues", [])
            for issue in issues:
                yield issue
            total = page.get("total", 0)
            start_at += len(issues)
            if not issues or start_at >= total:
                break

    # -- comments / worklogs ----------------------------------------------
    def iter_comments(self, issue_key: str) -> Iterator[dict]:
        start_at = 0
        while True:
            page = self._try_get(
                [f"/rest/api/3/issue/{issue_key}/comment",
                 f"/rest/api/2/issue/{issue_key}/comment"],
                params={"startAt": start_at, "maxResults": PAGE_SIZE},
            )
            comments = page.get("comments", [])
            for comment in comments:
                yield comment
            total = page.get("total", 0)
            start_at += len(comments)
            if not comments or start_at >= total:
                break

    def iter_worklogs(self, issue_key: str) -> Iterator[dict]:
        start_at = 0
        while True:
            page = self._try_get(
                [f"/rest/api/3/issue/{issue_key}/worklog",
                 f"/rest/api/2/issue/{issue_key}/worklog"],
                params={"startAt": start_at, "maxResults": PAGE_SIZE},
            )
            worklogs = page.get("worklogs", [])
            for worklog in worklogs:
                yield worklog
            total = page.get("total", 0)
            start_at += len(worklogs)
            if not worklogs or start_at >= total:
                break
