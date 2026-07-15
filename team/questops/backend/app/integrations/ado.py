"""Shared Azure DevOps helpers — instance-scoped (ADO_URL is now the instance
root, not a collection). Collections are enumerated and every collection-scoped
call is prefixed with its collection. Used by both the Repositories page and
Access Management."""

import time

import requests

from ..config import settings

HTTP_TIMEOUT = (5, 20)
_COLL_CACHE: dict = {"at": 0.0, "data": None}
COLL_TTL = 900


def instance() -> str:
    return settings.ado_url.rstrip("/")


def _auth():
    return (settings.ado_user, settings.ado_rest_password)


def get(path: str, params: dict | None = None):
    """GET a path relative to the instance root (path starts with '/').
    ALWAYS returns a dict/list, never None: some ADO endpoints answer 200
    with a `null` body (no ACL, empty result, deleted resource) or with a
    non-JSON auth page — callers then did None.get() and crashed. Coerce
    those to {} so `.get('value', [])` is always safe."""
    r = requests.get(f"{instance()}{path}",
                     params={"api-version": "6.0", **(params or {})},
                     auth=_auth(), timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        return {}
    return {} if data is None else data


def collections(force: bool = False) -> list[str]:
    """Collection names across the whole instance. On-prem ADO Server exposes
    /_apis/projectCollections; Azure DevOps Services has a single implicit
    collection (the org) — we fall back to '' meaning 'no collection prefix'."""
    if (not force and _COLL_CACHE["data"] is not None
            and time.time() - _COLL_CACHE["at"] < COLL_TTL):
        return _COLL_CACHE["data"]
    names: list[str] = []
    try:
        data = get("/_apis/projectCollections", {"$top": 500})
        names = sorted(c.get("name", "") for c in ((data or {}).get("value") or [])
                       if c and c.get("name"))
    except requests.RequestException:
        names = []
    if not names:
        names = [""]  # single implicit collection / legacy collection-root URL
    _COLL_CACHE.update(at=time.time(), data=names)
    return names


def coll_get(collection: str, path: str, params: dict | None = None):
    """GET a collection-scoped path, e.g. coll_get('DefaultCollection',
    '/_apis/projects'). Empty collection = instance root (implicit)."""
    prefix = f"/{collection}" if collection else ""
    return get(f"{prefix}{path}", params)


def project_url(collection: str, project: str) -> str:
    prefix = f"/{collection}" if collection else ""
    return f"{instance()}{prefix}/{project}"


def repo_url(collection: str, project: str, repo: str) -> str:
    return f"{project_url(collection, project)}/_git/{repo}"
