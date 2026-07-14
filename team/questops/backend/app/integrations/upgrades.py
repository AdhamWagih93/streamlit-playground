"""Upgrade checker: detect the running version of each integrated tool and
compare it against the latest LTS / supported line.

Latest-version data comes from endoflife.date (and GitHub releases for tools
it doesn't track), cached in-process for 6h. When the server has no internet
a bundled snapshot is used and clearly labeled as possibly stale."""

import datetime as dt
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from ..config import settings

HTTP_TIMEOUT = 8
CACHE_TTL = 6 * 3600
CACHE_TTL_DEGRADED = 600  # failed lookups retry soon (self-heals after a proxy fix)
_CACHE: dict = {"at": 0.0, "payload": None, "ttl": CACHE_TTL}

PROXY_HINT = ("no direct internet? set UPGRADES_PROXY (QO_UPGRADES_PROXY) to your "
              "corporate proxy, or point EOL_API_BASE at an internal mirror")


def _lookup_get(url: str) -> dict | list:
    """The ONLY outbound-internet call in QuestOps — honors the dedicated
    proxy so internal Jira/Jenkins/ES traffic is never routed through it.
    Tight connect timeout: black-holing firewalls must fail fast."""
    proxies = ({"http": settings.upgrades_proxy, "https": settings.upgrades_proxy}
               if settings.upgrades_proxy else None)
    r = requests.get(url, timeout=(4, HTTP_TIMEOUT), proxies=proxies,
                     verify=settings.upgrades_verify_ssl,
                     headers={"Accept": "application/json"})
    r.raise_for_status()
    return r.json()


def _mask_userinfo(url: str) -> str:
    return re.sub(r"(https?://)[^/@\s]+@", r"\1***@", url or "")


def lookup_config() -> dict:
    """What the server ACTUALLY uses for lookups — shown in the UI so a
    config value that never reached the container is spotted instantly."""
    env_proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
                 or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "")
    return {"proxy": _mask_userinfo(settings.upgrades_proxy) or None,
            "env_proxy": _mask_userinfo(env_proxy) or None,
            "verify_ssl": settings.upgrades_verify_ssl,
            "eol_api_base": settings.eol_api_base,
            "github_api_base": settings.github_api_base}


def _short_err(exc: Exception) -> str:
    s = str(exc)
    for marker, human in (("NameResolutionError", "DNS lookup failed"),
                          ("NewConnectionError", "connection refused/blocked"),
                          ("ConnectTimeoutError", "connect timeout"),
                          ("ProxyError", "proxy error"),
                          ("SSLError", "TLS error (proxy re-signing? set UPGRADES_VERIFY_SSL=false or trust the CA)")):
        if marker in s:
            return human
    return s[:120]

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
    cycles = _lookup_get(f"{settings.eol_api_base.rstrip('/')}/{product}.json")
    return _pick(cycles), cycles


def _from_github(repo: str) -> tuple[dict, list[dict]]:
    data = _lookup_get(f"{settings.github_api_base.rstrip('/')}/repos/{repo}/releases/latest")
    tag = (data.get("tag_name") or "").lstrip("v")
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


def _check_tool(t: dict) -> dict:
    """Detect + lookup for ONE tool — runs in a worker thread so five slow
    lookups cost one timeout, not five stacked (the tab used to hang)."""
    try:
        current, detect_error = t["detect"]()
    except Exception as exc:  # noqa: BLE001 — a broken detector never kills the tab
        current, detect_error = None, str(exc)[:120]
    rec, cycles, source, lookup_error = None, [], "endoflife.date", None
    try:
        if t.get("product"):
            rec, cycles = _from_eol_api(t["product"])
        else:
            rec, cycles = _from_github(t["github"])
            source = "GitHub releases"
    except Exception as exc:  # noqa: BLE001 — offline / API down
        lookup_error = _short_err(exc)
        bundled = BUNDLED.get(t.get("product") or t.get("github", ""))
        if bundled:
            rec = {**bundled, "eol": None}
            source = "bundled snapshot (offline — may be stale)"
    return {
        "key": t["key"], "name": t["name"], "icon": t["icon"],
        "current": current, "detect_error": detect_error,
        "recommended": rec["recommended"] if rec else None,
        "latest": rec["latest"] if rec else None,
        "lts": rec["lts"] if rec else False,
        "eol_date": (rec or {}).get("eol") if isinstance((rec or {}).get("eol"), str) else None,
        "status": _status(current, cycles, rec),
        "source": source, "lookup_error": lookup_error, "page": t["page"],
    }


def check(force: bool = False) -> dict:
    if not force and _CACHE["payload"] and time.time() - _CACHE["at"] < _CACHE["ttl"]:
        return {**_CACHE["payload"], "cached": True}

    with ThreadPoolExecutor(max_workers=len(TOOLS)) as pool:
        rows = list(pool.map(_check_tool, TOOLS))

    order = {"eol": 0, "upgrade": 1, "patch": 2, "unknown": 3, "ok": 4}
    rows.sort(key=lambda r: order.get(r["status"], 5))
    degraded = any(r["lookup_error"] for r in rows)
    payload = {"rows": rows,
               "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "demo_versions": settings.demo_mode,
               "degraded": degraded,
               "hint": PROXY_HINT if degraded else None,
               "lookup_config": lookup_config()}
    # failed lookups get a short cache so a proxy fix takes effect quickly
    _CACHE.update(at=time.time(), payload=payload,
                  ttl=CACHE_TTL_DEGRADED if degraded else CACHE_TTL)
    return {**payload, "cached": False}
