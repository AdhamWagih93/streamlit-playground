"""Upgrade checker: detect the running version of each integrated tool and
compare it against the latest LTS / supported line.

Latest-version data comes from endoflife.date (and GitHub releases for tools
it doesn't track), cached in-process for 6h. When the server has no internet
a bundled snapshot is used and clearly labeled as possibly stale."""

import datetime as dt
import re
import time

import requests

from ..config import settings

EOL_API = "https://endoflife.date/api/{product}.json"
HTTP_TIMEOUT = 8
CACHE_TTL = 6 * 3600
_CACHE: dict = {"at": 0.0, "payload": None}

# offline fallback — approximate, from build time; the UI labels it stale
BUNDLED = {
    "jenkins": {"recommended": "2.516", "latest": "2.516.3", "lts": True},
    "elasticsearch": {"recommended": "9.1", "latest": "9.1.3", "lts": False},
    "jira-software": {"recommended": "10.3", "latest": "10.3.6", "lts": True},
    "postgresql": {"recommended": "17", "latest": "17.6", "lts": False},
    "ollama/ollama": {"recommended": "0.11", "latest": "0.11.10", "lts": False},
}

# demo currents chosen to exercise every status color in the UI
DEMO_VERSIONS = {"jenkins": "2.440.3", "elasticsearch": "8.11.4",
                 "jira": "9.12.0", "postgresql": "16.2", "ollama": "0.5.4"}


def _ver(s: str) -> tuple:
    out = []
    for part in re.split(r"[.\-+_]", (s or "").strip()):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out) or (0,)


# ------------------------------------------------------------- detect current
def _jenkins_version() -> tuple[str | None, str | None]:
    if settings.demo_mode:
        return DEMO_VERSIONS["jenkins"], None
    if not settings.jenkins_url:
        return None, "not configured"
    try:
        auth = ((settings.jenkins_user, settings.jenkins_token)
                if settings.jenkins_user else None)
        r = requests.get(f"{settings.jenkins_url}/api/json",
                         params={"tree": "mode"}, auth=auth, timeout=HTTP_TIMEOUT)
        v = r.headers.get("X-Jenkins")
        return (v, None) if v else (None, "no X-Jenkins header in response")
    except requests.RequestException as exc:
        return None, str(exc)[:120]


def _es_version() -> tuple[str | None, str | None]:
    if settings.demo_mode:
        return DEMO_VERSIONS["elasticsearch"], None
    if not settings.es_url:
        return None, "not configured"
    try:
        headers = ({"Authorization": f"ApiKey {settings.es_api_key}"}
                   if settings.es_api_key else {})
        r = requests.get(settings.es_url, headers=headers,
                         timeout=HTTP_TIMEOUT, verify=settings.es_verify_ssl)
        r.raise_for_status()
        return r.json().get("version", {}).get("number"), None
    except (requests.RequestException, ValueError) as exc:
        return None, str(exc)[:120]


def _jira_version() -> tuple[str | None, str | None]:
    if settings.demo_mode:
        return DEMO_VERSIONS["jira"], None
    if not settings.jira_base_url:
        return None, "not configured"
    try:
        auth = ((settings.jira_user, settings.jira_password)
                if settings.jira_user else None)
        r = requests.get(f"{settings.jira_base_url}/rest/api/2/serverInfo",
                         auth=auth, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        return r.json().get("version"), None
    except (requests.RequestException, ValueError) as exc:
        return None, str(exc)[:120]


def _pg_version() -> tuple[str | None, str | None]:
    if settings.demo_mode:
        return DEMO_VERSIONS["postgresql"], None
    from ..db import engine
    if engine.dialect.name != "postgresql":
        return None, f"running on {engine.dialect.name} (dev database)"
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            v = conn.execute(text("SHOW server_version")).scalar() or ""
        return v.split()[0], None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:120]


def _ollama_version() -> tuple[str | None, str | None]:
    if settings.demo_mode:
        return DEMO_VERSIONS["ollama"], None
    try:
        r = requests.get(f"{settings.ollama_url}/api/version", timeout=3)
        r.raise_for_status()
        return r.json().get("version"), None
    except (requests.RequestException, ValueError) as exc:
        return None, str(exc)[:120]


# ------------------------------------------------------------- latest online
def _eol_passed(cycle: dict) -> bool:
    eol = cycle.get("eol")
    if eol is True:
        return True
    if isinstance(eol, str):
        try:
            return dt.date.fromisoformat(eol) < dt.date.today()
        except ValueError:
            return False
    return False


def _pick(cycles: list[dict]) -> dict:
    """Newest LTS line when the product marks LTS; else newest supported line."""
    def key(c):
        return _ver(str(c.get("cycle", "")))
    lts = [c for c in cycles if c.get("lts")]  # bool True or 'became LTS' date
    pool = [c for c in lts if not _eol_passed(c)] or lts \
        or [c for c in cycles if not _eol_passed(c)] or cycles
    best = max(pool, key=key)
    return {"recommended": str(best.get("cycle", "")),
            "latest": str(best.get("latest") or ""),
            "lts": bool(lts), "eol": best.get("eol")}


def _from_eol_api(product: str) -> tuple[dict, list[dict]]:
    r = requests.get(EOL_API.format(product=product), timeout=HTTP_TIMEOUT,
                     headers={"Accept": "application/json"})
    r.raise_for_status()
    cycles = r.json()
    return _pick(cycles), cycles


def _from_github(repo: str) -> tuple[dict, list[dict]]:
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest",
                     timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    tag = (r.json().get("tag_name") or "").lstrip("v")
    return {"recommended": ".".join(tag.split(".")[:2]), "latest": tag,
            "lts": False, "eol": None}, []


# ------------------------------------------------------------- status verdict
def _status(current: str | None, cycles: list[dict], rec: dict | None) -> str:
    if not current or not rec:
        return "unknown"
    cv = _ver(current)
    mine = None
    for c in cycles or []:
        cyc = _ver(str(c.get("cycle", "")))
        if cv[:len(cyc)] == cyc and (mine is None
                                     or cyc > _ver(str(mine.get("cycle", "")))):
            mine = c
    if mine is not None and _eol_passed(mine):
        return "eol"
    rv = _ver(rec["recommended"])
    if rv > cv[:len(rv)]:
        return "upgrade"
    if rec.get("latest") and _ver(rec["latest"]) > cv:
        return "patch"
    return "ok"


TOOLS = [
    {"key": "jenkins", "name": "Jenkins", "icon": "⚙",
     "product": "jenkins", "detect": _jenkins_version,
     "page": "https://endoflife.date/jenkins"},
    {"key": "elasticsearch", "name": "Elasticsearch", "icon": "🔍",
     "product": "elasticsearch", "detect": _es_version,
     "page": "https://endoflife.date/elasticsearch"},
    {"key": "jira", "name": "Jira Data Center", "icon": "🎫",
     "product": "jira-software", "detect": _jira_version,
     "page": "https://endoflife.date/jira-software"},
    {"key": "postgresql", "name": "PostgreSQL", "icon": "🐘",
     "product": "postgresql", "detect": _pg_version,
     "page": "https://endoflife.date/postgresql"},
    {"key": "ollama", "name": "Ollama", "icon": "✦",
     "product": None, "github": "ollama/ollama", "detect": _ollama_version,
     "page": "https://github.com/ollama/ollama/releases"},
]


def check(force: bool = False) -> dict:
    if not force and _CACHE["payload"] and time.time() - _CACHE["at"] < CACHE_TTL:
        return {**_CACHE["payload"], "cached": True}

    rows = []
    for t in TOOLS:
        current, detect_error = t["detect"]()
        rec, cycles, source, lookup_error = None, [], "endoflife.date", None
        try:
            if t.get("product"):
                rec, cycles = _from_eol_api(t["product"])
            else:
                rec, cycles = _from_github(t["github"])
                source = "GitHub releases"
        except Exception as exc:  # noqa: BLE001 — offline / API down
            lookup_error = str(exc)[:120]
            bundled = BUNDLED.get(t.get("product") or t.get("github", ""))
            if bundled:
                rec = {**bundled, "eol": None}
                source = "bundled snapshot (offline — may be stale)"
        rows.append({
            "key": t["key"], "name": t["name"], "icon": t["icon"],
            "current": current, "detect_error": detect_error,
            "recommended": rec["recommended"] if rec else None,
            "latest": rec["latest"] if rec else None,
            "lts": rec["lts"] if rec else False,
            "eol_date": (rec or {}).get("eol") if isinstance((rec or {}).get("eol"), str) else None,
            "status": _status(current, cycles, rec),
            "source": source, "lookup_error": lookup_error, "page": t["page"],
        })

    order = {"eol": 0, "upgrade": 1, "patch": 2, "unknown": 3, "ok": 4}
    rows.sort(key=lambda r: order.get(r["status"], 5))
    payload = {"rows": rows,
               "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "demo_versions": settings.demo_mode}
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}
