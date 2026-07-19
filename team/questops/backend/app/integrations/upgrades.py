"""Upgrade checker: detect the running version of each integrated tool and
compare it against the latest released version.

Latest-version data comes from Docker Hub image tags, GitHub releases/tags, and
Artifact Hub package versions (endoflife.date is intentionally NOT used — it is
unreachable in some regions). Cached in-process for 6h. When the server has no
internet a bundled snapshot is used and clearly labeled as possibly stale."""

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
              "corporate proxy, or point DOCKERHUB_API_BASE / GITHUB_API_BASE / "
              "ARTIFACTHUB_API_BASE at internal mirrors")


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
            "sources": (f"{settings.dockerhub_api_base} · {settings.github_api_base} "
                        f"· {settings.artifacthub_api_base}")}


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

# offline fallback — approximate, from build time; the UI labels it stale.
# Keyed by tool key (see TOOLS).
BUNDLED = {
    "jenkins": {"recommended": "2.516", "latest": "2.516.3", "lts": True},
    "elasticsearch": {"recommended": "9.1", "latest": "9.1.3", "lts": False},
    "jira": {"recommended": "10.3", "latest": "10.3.6", "lts": True},
    "postgresql": {"recommended": "17", "latest": "17.6", "lts": False},
    "ollama": {"recommended": "0.11", "latest": "0.11.10", "lts": False},
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
def _major_minor(v: str) -> str:
    """First two numeric components of a version, e.g. '10.3.6' -> '10.3'."""
    nums = [p for p in re.split(r"[.\-+_]", (v or "").strip())
            if p and p[0].isdigit()][:2]
    return ".".join(nums) if nums else (v or "")


def _semver_tags(tags: list[str], suffix: str | None = None) -> list[tuple]:
    """[(version_tuple, version_str)] for tags that are a plain dotted version
    (major.minor[.patch]), optionally requiring an exact suffix like '-lts'.
    Rejects qualified tags (alpine/rc/jdk/latest/…), so only real releases win."""
    pat = (re.compile(rf"^(\d+(?:\.\d+)+){re.escape(suffix)}$") if suffix
           else re.compile(r"^v?(\d+(?:\.\d+)+)$"))
    out = []
    for t in tags:
        m = pat.match((t or "").strip())
        if m:
            out.append((_ver(m.group(1)), m.group(1)))
    return out


def _rec_from(latest: str, lts: bool = False) -> dict:
    return {"recommended": _major_minor(latest), "latest": latest,
            "lts": lts, "eol": None}


def _from_dockerhub(repo: str, suffix: str | None = None) -> dict:
    """Highest version among a Docker Hub image's tags. `suffix` (e.g. '-lts')
    restricts to that release line. `repo` is namespace/name ('library/postgres',
    'jenkins/jenkins', 'atlassian/jira-software')."""
    data = _lookup_get(f"{settings.dockerhub_api_base.rstrip('/')}"
                       f"/repositories/{repo}/tags?page_size=100&ordering=last_updated")
    tags = [t.get("name", "") for t in (data.get("results") or [])]
    vers = _semver_tags(tags, suffix)
    if not vers:
        raise ValueError(f"no version tags on Docker Hub for {repo}")
    return _rec_from(max(vers)[1], lts=bool(suffix))


def _from_github(repo: str) -> dict:
    """Latest GitHub release (falling back to the highest semver tag)."""
    base = settings.github_api_base.rstrip("/")
    tag = ""
    try:
        data = _lookup_get(f"{base}/repos/{repo}/releases/latest")
        tag = (data.get("tag_name") or "").lstrip("v")
    except Exception:  # noqa: BLE001 — repo may publish tags but no 'releases'
        tag = ""
    if not tag:
        tags = _lookup_get(f"{base}/repos/{repo}/tags?per_page=100")
        vers = _semver_tags([t.get("name", "") for t in (tags or [])])
        if not vers:
            raise ValueError(f"no releases/tags on GitHub for {repo}")
        tag = max(vers)[1]
    return _rec_from(tag)


def _from_artifacthub(repo: str, package: str) -> dict:
    """App version of an Artifact Hub Helm package (repo/package)."""
    data = _lookup_get(f"{settings.artifacthub_api_base.rstrip('/')}"
                       f"/packages/helm/{repo}/{package}")
    ver = (data.get("app_version") or data.get("version") or "").lstrip("v")
    if not ver:
        raise ValueError(f"no version from Artifact Hub for {repo}/{package}")
    return _rec_from(ver)


_SOURCES = {"dockerhub": ("Docker Hub", lambda s: _from_dockerhub(s["repo"], s.get("suffix"))),
            "github": ("GitHub releases", lambda s: _from_github(s["repo"])),
            "artifacthub": ("Artifact Hub", lambda s: _from_artifacthub(s["repo"], s["package"]))}


# ------------------------------------------------------------- status verdict
def _status(current: str | None, rec: dict | None) -> str:
    """Compare the running version to the latest released line: behind a whole
    release line -> 'upgrade'; only behind on the patch -> 'patch'; else 'ok'."""
    if not current or not rec:
        return "unknown"
    cv = _ver(current)
    rv = _ver(rec["recommended"])
    if rv > cv[:len(rv)]:
        return "upgrade"
    if rec.get("latest") and _ver(rec["latest"]) > cv:
        return "patch"
    return "ok"


TOOLS = [
    {"key": "jenkins", "name": "Jenkins", "icon": "⚙", "detect": _jenkins_version,
     "source": {"type": "dockerhub", "repo": "jenkins/jenkins", "suffix": "-lts"},
     "page": "https://hub.docker.com/r/jenkins/jenkins/tags"},
    {"key": "elasticsearch", "name": "Elasticsearch", "icon": "🔍", "detect": _es_version,
     "source": {"type": "github", "repo": "elastic/elasticsearch"},
     "page": "https://github.com/elastic/elasticsearch/releases"},
    {"key": "jira", "name": "Jira Data Center", "icon": "🎫", "detect": _jira_version,
     "source": {"type": "dockerhub", "repo": "atlassian/jira-software"},
     "page": "https://hub.docker.com/r/atlassian/jira-software/tags"},
    {"key": "postgresql", "name": "PostgreSQL", "icon": "🐘", "detect": _pg_version,
     "source": {"type": "dockerhub", "repo": "library/postgres"},
     "page": "https://hub.docker.com/_/postgres/tags"},
    {"key": "ollama", "name": "Ollama", "icon": "✦", "detect": _ollama_version,
     "source": {"type": "github", "repo": "ollama/ollama"},
     "page": "https://github.com/ollama/ollama/releases"},
]


def _check_tool(t: dict) -> dict:
    """Detect + lookup for ONE tool — runs in a worker thread so five slow
    lookups cost one timeout, not five stacked (the tab used to hang)."""
    try:
        current, detect_error = t["detect"]()
    except Exception as exc:  # noqa: BLE001 — a broken detector never kills the tab
        current, detect_error = None, str(exc)[:120]
    rec, source, lookup_error = None, "", None
    label, fetch = _SOURCES[t["source"]["type"]]
    try:
        rec, source = fetch(t["source"]), label
    except Exception as exc:  # noqa: BLE001 — offline / API down / blocked
        lookup_error = _short_err(exc)
        bundled = BUNDLED.get(t["key"])
        if bundled:
            rec = {**bundled, "eol": None}
            source = "bundled snapshot (offline — may be stale)"
    return {
        "key": t["key"], "name": t["name"], "icon": t["icon"],
        "current": current, "detect_error": detect_error,
        "recommended": rec["recommended"] if rec else None,
        "latest": rec["latest"] if rec else None,
        "lts": rec["lts"] if rec else False,
        "eol_date": None,
        "status": _status(current, rec),
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
