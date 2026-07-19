"""Jenkins signals: recent failures + long-running builds.

Live mode reads the root api/json tree; demo mode keeps a small mutable
job table so 'claim' / 'fixed' flows are exercisable offline."""

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from ..config import settings

# job -> username, survives per-process (a claim is a social signal, not a record)
CLAIMS: dict[str, str] = {}


def _now_ms() -> int:
    return int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)


def is_live() -> bool:
    return bool(settings.jenkins_url and not settings.demo_mode)


_DEMO_JOBS = [
    {"name": "payments-service/main", "result": "FAILURE", "building": False,
     "ago_min": 18, "duration_min": 11, "number": 481, "recent_builds": 14},  # < 30m → 'at risk' in demo
    {"name": "checkout-service/main", "result": "FAILURE", "building": False,
     "ago_min": 130, "duration_min": 8, "number": 902, "recent_builds": 11},
    {"name": "platform-terraform/apply", "result": "UNSTABLE", "building": False,
     "ago_min": 300, "duration_min": 22, "number": 233, "recent_builds": 4},
    # latest run green, but an EARLIER run in the window failed (another
    # project on the same pipeline) — must still show as a red pipeline
    {"name": "inventory-service/main", "result": "SUCCESS", "building": False,
     "ago_min": 55, "duration_min": 9, "number": 764, "recent_builds": 9,
     "recent_failures": [{"number": 762, "result": "FAILURE",
                          "ago_min": 180, "duration_min": 4}]},
    {"name": "data-warehouse/nightly-etl", "result": None, "building": True,
     "ago_min": 95, "duration_min": None, "number": 1201, "avg_min": 38.0,
     "recent_builds": 7},   # 95m vs ~38m avg → stuck
    {"name": "monolith/regression-suite", "result": None, "building": True,
     "ago_min": 61, "duration_min": None, "number": 3391, "avg_min": 52.0,
     "recent_builds": 5},   # within normal range
    {"name": "auth-service/main", "result": "SUCCESS", "building": False,
     "ago_min": 20, "duration_min": 6, "number": 512, "recent_builds": 12},
    {"name": "DevOps_Test/sandbox-pipeline", "result": "FAILURE", "building": False,
     "ago_min": 10, "duration_min": 2, "number": 77},  # filtered out by JENKINS_IGNORE
    {"name": "notifications-service/main", "result": "SUCCESS", "building": False,
     "ago_min": 400, "duration_min": 7, "number": 289, "recent_builds": 3},
]


def _demo_overview() -> dict:
    failures, long_running, jobs = [], [], []
    for j in _DEMO_JOBS:
        if any(tok in j["name"].lower() for tok in settings.jenkins_ignore_tokens):
            continue
        started = _now_ms() - j["ago_min"] * 60_000
        url = f"#demo/jenkins/{j['name']}/{j['number']}"
        jobs.append({"name": j["name"], "result": j["result"], "building": j["building"],
                     "number": j["number"], "url": url,
                     "started": started, "duration_min": j["duration_min"],
                     "recent_builds": j.get("recent_builds", 0)})
        if j["building"] and _is_long_running(j["ago_min"], j.get("avg_min") or 0):
            long_running.append({"job": j["name"], "number": j["number"], "url": url,
                                 "running_min": j["ago_min"],
                                 "avg_min": j.get("avg_min") or 0,
                                 "claimed_by": CLAIMS.get(j["name"])})
        elif j["result"] in ("FAILURE", "UNSTABLE"):
            failures.append({"job": j["name"], "number": j["number"], "url": url,
                             "result": j["result"], "ago_min": j["ago_min"],
                             "duration_min": j["duration_min"], "latest_ok": False,
                             "claimed_by": CLAIMS.get(j["name"])})
        for f in j.get("recent_failures", []):  # earlier failed runs in the window
            if f["ago_min"] <= settings.jenkins_failure_window_days * 24 * 60:
                failures.append({"job": j["name"], "number": f["number"],
                                 "url": f"#demo/jenkins/{j['name']}/{f['number']}",
                                 "result": f.get("result", "FAILURE"),
                                 "ago_min": f["ago_min"],
                                 "duration_min": f.get("duration_min"),
                                 "latest_ok": j["result"] == "SUCCESS",
                                 "claimed_by": CLAIMS.get(j["name"])})
    failures.sort(key=lambda f: f["ago_min"])
    return {"failures": failures, "long_running": long_running,
            "failure_window_days": settings.jenkins_failure_window_days,
            "jobs": jobs, "source": "demo"}


# leaf fields we need per runnable job; folders/multibranch expose 'jobs' instead.
# builds{0,20} = recent history: average runtime AND the failure scan — a red
# pipeline is any job with a failed run in the window, not just a red LAST run
# (the same pipeline serves multiple projects, so later runs can mask failures).
_LEAF = ("fullName,name,url,"
         "lastBuild[number,building,timestamp,duration,result,url],"
         "lastCompletedBuild[number,timestamp,duration,result,url],"
         "builds[number,timestamp,duration,result,building,url]{0,20}")


def _tree_query(depth: int = 5) -> str:
    """Nested tree so jobs inside folders / multibranch pipelines are included."""
    tree = _LEAF
    for _ in range(depth):
        tree = f"{_LEAF},jobs[{tree}]"
    return f"jobs[{tree}]"


def _flatten(items: list, out: list) -> list:
    for j in items or []:
        if j.get("jobs") is not None:  # folder or multibranch — descend
            _flatten(j["jobs"], out)
        if j.get("lastBuild") or j.get("lastCompletedBuild"):  # runnable job
            out.append(j)
    return out


def _avg_duration_min(builds: list) -> float:
    """Average runtime of recent builds; successful ones are the baseline
    (failed builds often die early and would skew the average down)."""
    done = [b["duration"] for b in builds
            if not b.get("building") and (b.get("duration") or 0) > 0]
    ok = [b["duration"] for b in builds
          if b.get("result") == "SUCCESS" and (b.get("duration") or 0) > 0]
    sample = ok or done
    return round(sum(sample) / len(sample) / 60_000, 1) if sample else 0.0


def _is_long_running(running_min: float, avg_min: float) -> bool:
    if avg_min > 0:
        return running_min > avg_min * settings.jenkins_long_running_factor
    return running_min >= settings.jenkins_long_running_minutes  # no history fallback


def _live_overview() -> dict:
    auth = (settings.jenkins_user, settings.jenkins_token) if settings.jenkins_user else None
    r = requests.get(f"{settings.jenkins_url}/api/json",
                     params={"tree": _tree_query()}, auth=auth, timeout=30)
    r.raise_for_status()
    now = _now_ms()
    failures, long_running, jobs = [], [], []
    for j in _flatten(r.json().get("jobs", []), []):
        name = j.get("fullName") or j.get("name") or ""
        if any(tok in name.lower() for tok in settings.jenkins_ignore_tokens):
            continue
        last, completed = j.get("lastBuild") or {}, j.get("lastCompletedBuild") or {}
        window_ms = settings.jenkins_failure_window_days * 24 * 60 * 60_000
        jobs.append({"name": name, "result": completed.get("result"),
                     "building": last.get("building", False),
                     "number": last.get("number"), "url": last.get("url") or j.get("url"),
                     "started": last.get("timestamp"),
                     "duration_min": round((completed.get("duration") or 0) / 60_000, 1),
                     # activity = builds inside the window (drives the top-10 view)
                     "recent_builds": sum(1 for b in (j.get("builds") or [])
                                          if b.get("timestamp")
                                          and now - b["timestamp"] <= window_ms)})
        if last.get("building"):
            running_min = (now - last.get("timestamp", now)) / 60_000
            avg_min = _avg_duration_min(j.get("builds") or [])
            if _is_long_running(running_min, avg_min):
                long_running.append({"job": name, "number": last.get("number"),
                                     "url": last.get("url"),
                                     "running_min": int(running_min),
                                     "avg_min": avg_min,
                                     "claimed_by": CLAIMS.get(name)})
        # EVERY failed run in the window counts, not just the last one —
        # a later green run (often a different project on the same pipeline)
        # must not hide an earlier failure
        latest_ok = completed.get("result") == "SUCCESS"
        window_min = settings.jenkins_failure_window_days * 24 * 60
        for b in (j.get("builds") or []):
            if b.get("building") or b.get("result") not in ("FAILURE", "UNSTABLE"):
                continue
            ago_min = int((now - b.get("timestamp", now)) / 60_000)
            if ago_min > window_min:
                continue
            failures.append({"job": name, "number": b.get("number"),
                             "url": b.get("url")
                                    or f"{(j.get('url') or '').rstrip('/')}/{b.get('number')}/",
                             "result": b.get("result"), "ago_min": ago_min,
                             "duration_min": round((b.get("duration") or 0) / 60_000, 1),
                             "latest_ok": latest_ok,
                             "claimed_by": CLAIMS.get(name)})
    failures.sort(key=lambda f: f["ago_min"])
    return {"failures": failures, "long_running": long_running,
            "failure_window_days": settings.jenkins_failure_window_days,
            "jobs": jobs, "source": "live"}


def overview() -> dict:
    if is_live():
        return _live_overview()
    if settings.demo_mode:
        return _demo_overview()
    return {"failures": [], "long_running": [], "jobs": [],
            "failure_window_days": settings.jenkins_failure_window_days,
            "source": "not configured"}


MAX_LOG_TAIL = 40_000  # chars; failures live at the end of the console log


def _job_path(name: str) -> str:
    from urllib.parse import quote
    return "".join(f"/job/{quote(seg, safe='')}" for seg in name.split("/"))


def _auth():
    return (settings.jenkins_user, settings.jenkins_token) if settings.jenkins_user else None


def _demo_console(job: str, number: int) -> str:
    entry = next((j for j in _DEMO_JOBS if j["name"] == job), None)
    script = f"pipelines/{job.split('/')[0]}.groovy"
    head = (f"Started by user alice\n"
            f"Obtained {script} from git https://git.example.local/platform/Engine.git\n"
            "[Pipeline] Start of Pipeline\n"
            "[Pipeline] stage\n[Pipeline] { (Build) }\n"
            "+ ./gradlew assemble\nBUILD SUCCESSFUL in 42s\n"
            "[Pipeline] stage\n[Pipeline] { (Unit Tests) }\n"
            "+ ./gradlew test\n")
    if entry is None or entry["result"] in ("FAILURE", "UNSTABLE"):
        return head + (
            "PaymentsServiceTest > chargeCard() STARTED\n"
            "ERROR: org.testcontainers.containers.ContainerLaunchException: "
            "Container startup failed for image postgres:16-alpine\n"
            "Caused by: java.net.SocketTimeoutException: timeout waiting for docker daemon\n"
            "\tat org.testcontainers.DockerClientFactory.client(DockerClientFactory.java:272)\n"
            "PaymentsServiceTest > chargeCard() FAILED\n"
            "3 tests completed, 1 failed\n"
            "> Task :test FAILED\n"
            "FAILURE: Build failed with an exception.\n"
            f"Finished: {entry['result'] if entry else 'FAILURE'}\n")
    if entry["building"]:
        return head + "PaymentsServiceTest > chargeCard() STARTED\n(…still running…)\n"
    return head + "BUILD SUCCESSFUL in 3m 12s\nFinished: SUCCESS\n"


def console_log(job: str, number: int) -> str:
    """Tail of the build's console log (the part that holds the failure)."""
    if not is_live():
        if settings.demo_mode:
            return _demo_console(job, number)
        raise ValueError("Jenkins is not configured")
    r = requests.get(f"{settings.jenkins_url}{_job_path(job)}/{int(number)}/consoleText",
                     auth=_auth(), timeout=30)
    r.raise_for_status()
    text = r.text
    if len(text) > MAX_LOG_TAIL:
        text = f"… (showing the last {MAX_LOG_TAIL} of {len(text)} chars)\n" + text[-MAX_LOG_TAIL:]
    return text


# demo: pipelines wired to a few different SCM hosts so the by-host grouping is
# visible offline (one has no SCM — an inline Jenkinsfile)
_DEMO_SCM = {
    "payments-service/main": "https://ado.corp.local/DefaultCollection/Platform/_git/payments-service",
    "checkout-service/main": "https://ado.corp.local/DefaultCollection/Platform/_git/checkout-service",
    "monolith/regression-suite": "https://ado.corp.local/DefaultCollection/Legacy/_git/monolith",
    "platform-terraform/apply": "https://github.corp.local/platform/terraform.git",
    "data-warehouse/nightly-etl": "https://github.corp.local/data/warehouse.git",
    "auth-service/main": "https://github.corp.local/platform/auth-service.git",
    "inventory-service/main": "git@bitbucket.corp.local:inv/inventory-service.git",
    "notifications-service/main": "",  # inline Jenkinsfile — no pipeline-from-SCM
}


def job_definition(job: str) -> dict:
    """Where this job's pipeline lives: scriptPath + SCM url from config.xml
    (pipeline-from-SCM). For multibranch branch jobs the definition sits on
    the parent folder, so we walk up when the job itself has none."""
    if not is_live():
        if settings.demo_mode:
            return {"script_path": f"pipelines/{job.split('/')[0]}.groovy",
                    "scm_url": _DEMO_SCM.get(job, "https://git.example.local/platform/Engine.git"),
                    "source": "demo"}
        raise ValueError("Jenkins is not configured")
    import xml.etree.ElementTree as ET
    segments = job.split("/")
    while segments:
        r = requests.get(f"{settings.jenkins_url}{_job_path('/'.join(segments))}/config.xml",
                         auth=_auth(), timeout=20)
        if r.ok:
            try:
                root = ET.fromstring(r.text)
            except ET.ParseError:
                root = None
            if root is not None:
                script_path = root.findtext(".//scriptPath") or ""
                scm_url = ""
                for tag in (".//hudson.plugins.git.UserRemoteConfig/url",
                            ".//scm//url", ".//source/remote"):
                    scm_url = root.findtext(tag) or ""
                    if scm_url:
                        break
                if script_path:
                    return {"script_path": script_path, "scm_url": scm_url,
                            "source": "live", "defined_on": "/".join(segments)}
        segments.pop()  # try the parent (multibranch folder)
    return {"script_path": "", "scm_url": "", "source": "live",
            "note": "no pipeline-from-SCM definition found in the job or its parents"}


def all_job_names() -> list[str]:
    """EVERY runnable job on the instance — JENKINS_IGNORE is deliberately
    NOT applied: it filters the failure feed, but the dependency wiring
    check must see excluded jobs too."""
    if not is_live():
        if settings.demo_mode:
            return [j["name"] for j in _DEMO_JOBS]
        return []
    r = requests.get(f"{settings.jenkins_url}/api/json",
                     params={"tree": _tree_query()}, auth=_auth(), timeout=30)
    r.raise_for_status()
    return [j.get("fullName") or j.get("name") or ""
            for j in _flatten(r.json().get("jobs", []), [])]


_SCRIPT_PATHS_CACHE: dict = {"at": 0.0, "data": None}


def invalidate_script_paths() -> None:
    _SCRIPT_PATHS_CACHE.update(at=0.0, data=None)


def pipeline_script_paths(ttl: int = 300) -> dict[str, list[str]]:
    """scriptPath -> [job full names] across the whole Jenkins instance —
    which repo pipeline files are ACTUALLY wired to jobs. Includes jobs
    matching JENKINS_IGNORE (wiring is wiring). Cached (each job costs a
    config.xml fetch)."""
    import time
    if (_SCRIPT_PATHS_CACHE["data"] is not None
            and time.time() - _SCRIPT_PATHS_CACHE["at"] < ttl):
        return _SCRIPT_PATHS_CACHE["data"]
    out: dict[str, list[str]] = {}
    if settings.demo_mode and not is_live():
        for name in all_job_names():
            sp = f"pipelines/{name.split('/')[0]}.groovy"
            out.setdefault(sp, []).append(name)
    elif is_live():
        for name in all_job_names():
            try:
                d = job_definition(name)
            except Exception:  # noqa: BLE001 — one broken job never blocks the map
                continue
            sp = (d.get("script_path") or "").lstrip("./")
            if sp:
                out.setdefault(sp, []).append(name)
    _SCRIPT_PATHS_CACHE.update(at=time.time(), data=out)
    return out


def _scm_host(url: str) -> str:
    """Hostname of a git remote across the forms Jenkins stores: https/http/ssh
    URLs, scp-style git@host:group/repo.git, and bare host/path."""
    url = (url or "").strip()
    if not url:
        return ""
    m = re.match(r"^[\w.+-]+@([^:/]+):", url)          # scp-like: git@host:path
    if m:
        return m.group(1).lower()
    m = re.match(r"^[a-zA-Z][\w+.-]*://(?:[^@/]+@)?([^:/]+)", url)  # scheme://[user@]host
    if m:
        return m.group(1).lower()
    m = re.match(r"^([^:/]+)/", url)                   # bare host/path
    return m.group(1).lower() if m else ""


_SCM_INDEX_CACHE: dict = {"at": 0.0, "data": None}


def invalidate_scm_index() -> None:
    _SCM_INDEX_CACHE.update(at=0.0, data=None)


def pipeline_scm_index(ttl: int = 300) -> dict[str, dict]:
    """job full name -> {scm_url, scm_host, defined_on} for every pipeline
    (JENKINS_IGNORE applied, matching the failure feed). Each job costs a
    config.xml fetch, so this is parallelized and cached."""
    import time
    if (_SCM_INDEX_CACHE["data"] is not None
            and time.time() - _SCM_INDEX_CACHE["at"] < ttl):
        return _SCM_INDEX_CACHE["data"]
    names = [n for n in all_job_names()
             if not any(tok in n.lower() for tok in settings.jenkins_ignore_tokens)]

    def one(name):
        try:
            d = job_definition(name)
        except Exception:  # noqa: BLE001 — one broken job never blocks the map
            d = {}
        url = (d.get("scm_url") or "").strip()
        return name, {"scm_url": url, "scm_host": _scm_host(url),
                      "defined_on": d.get("defined_on") or ""}

    out: dict[str, dict] = {}
    if names:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for name, info in pool.map(one, names):
                out[name] = info
    _SCM_INDEX_CACHE.update(at=time.time(), data=out)
    return out


def pipeline_scm_groups() -> dict:
    """Every pipeline with its SCM URL, grouped by SCM hostname (largest group
    first). Pipelines with no pipeline-from-SCM definition list separately."""
    if not (is_live() or settings.demo_mode):
        return {"groups": [], "no_scm": [], "total": 0, "host_count": 0,
                "source": "not configured"}
    idx = pipeline_scm_index()
    groups: dict[str, list] = {}
    no_scm: list[dict] = []
    for job in sorted(idx):
        info = idx[job]
        if info["scm_url"]:
            host = info["scm_host"] or "(unknown host)"
            groups.setdefault(host, []).append(
                {"job": job, "scm_url": info["scm_url"],
                 "defined_on": info["defined_on"]})
        else:
            no_scm.append({"job": job})
    out = [{"host": h, "count": len(v), "pipelines": v} for h, v in groups.items()]
    out.sort(key=lambda g: (-g["count"], g["host"]))
    return {"groups": out, "no_scm": no_scm, "total": len(idx),
            "host_count": len(out),
            "source": "live" if is_live() else "demo"}


def claim(job: str, username: str) -> None:
    CLAIMS[job] = username


def verify_fixed(job: str) -> bool:
    """A 'fixed' claim only pays out if Jenkins agrees (live mode).
    'Fixed' means the job's LATEST completed run is green — past failures
    stay listed in the window (they may belong to other projects), so mere
    absence from the failure list can never be the test."""
    if not is_live():
        if not settings.demo_mode:
            return False  # unconfigured live mode: nothing to verify against
        for j in _DEMO_JOBS:
            if j["name"] == job and (j["result"] in ("FAILURE", "UNSTABLE")
                                     or j.get("recent_failures")):
                j["result"] = "SUCCESS"
                j["ago_min"] = 1
                j["recent_failures"] = []
                return True
        return False
    data = _live_overview()
    entry = next((x for x in data["jobs"] if x["name"] == job), None)
    return bool(entry and entry.get("result") == "SUCCESS")
