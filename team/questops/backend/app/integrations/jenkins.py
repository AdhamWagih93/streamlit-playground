"""Jenkins signals: recent failures + long-running builds.

Live mode reads the root api/json tree; demo mode keeps a small mutable
job table so 'claim' / 'fixed' flows are exercisable offline."""

import datetime as dt

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
     "ago_min": 18, "duration_min": 11, "number": 481},  # < 30m → 'at risk' in demo
    {"name": "checkout-service/main", "result": "FAILURE", "building": False,
     "ago_min": 130, "duration_min": 8, "number": 902},
    {"name": "platform-terraform/apply", "result": "UNSTABLE", "building": False,
     "ago_min": 300, "duration_min": 22, "number": 233},
    {"name": "inventory-service/main", "result": "SUCCESS", "building": False,
     "ago_min": 55, "duration_min": 9, "number": 764},
    {"name": "data-warehouse/nightly-etl", "result": None, "building": True,
     "ago_min": 95, "duration_min": None, "number": 1201, "avg_min": 38.0},   # 95m vs ~38m avg → stuck
    {"name": "monolith/regression-suite", "result": None, "building": True,
     "ago_min": 61, "duration_min": None, "number": 3391, "avg_min": 52.0},   # within normal range
    {"name": "auth-service/main", "result": "SUCCESS", "building": False,
     "ago_min": 20, "duration_min": 6, "number": 512},
    {"name": "DevOps_Test/sandbox-pipeline", "result": "FAILURE", "building": False,
     "ago_min": 10, "duration_min": 2, "number": 77},  # filtered out by JENKINS_IGNORE
    {"name": "notifications-service/main", "result": "SUCCESS", "building": False,
     "ago_min": 400, "duration_min": 7, "number": 289},
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
                     "started": started, "duration_min": j["duration_min"]})
        if j["building"] and _is_long_running(j["ago_min"], j.get("avg_min") or 0):
            long_running.append({"job": j["name"], "number": j["number"], "url": url,
                                 "running_min": j["ago_min"],
                                 "avg_min": j.get("avg_min") or 0,
                                 "claimed_by": CLAIMS.get(j["name"])})
        elif j["result"] in ("FAILURE", "UNSTABLE"):
            failures.append({"job": j["name"], "number": j["number"], "url": url,
                             "result": j["result"], "ago_min": j["ago_min"],
                             "duration_min": j["duration_min"],
                             "claimed_by": CLAIMS.get(j["name"])})
    return {"failures": failures, "long_running": long_running,
            "failure_window_days": settings.jenkins_failure_window_days,
            "jobs": jobs, "source": "demo"}


# leaf fields we need per runnable job; folders/multibranch expose 'jobs' instead.
# builds{0,20} = recent history used to compute the job's average runtime.
_LEAF = ("fullName,name,url,"
         "lastBuild[number,building,timestamp,duration,result,url],"
         "lastCompletedBuild[number,timestamp,duration,result,url],"
         "builds[duration,result,building]{0,20}")


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
        jobs.append({"name": name, "result": completed.get("result"),
                     "building": last.get("building", False),
                     "number": last.get("number"), "url": last.get("url") or j.get("url"),
                     "started": last.get("timestamp"),
                     "duration_min": round((completed.get("duration") or 0) / 60_000, 1)})
        if last.get("building"):
            running_min = (now - last.get("timestamp", now)) / 60_000
            avg_min = _avg_duration_min(j.get("builds") or [])
            if _is_long_running(running_min, avg_min):
                long_running.append({"job": name, "number": last.get("number"),
                                     "url": last.get("url"),
                                     "running_min": int(running_min),
                                     "avg_min": avg_min,
                                     "claimed_by": CLAIMS.get(name)})
        if completed.get("result") in ("FAILURE", "UNSTABLE"):
            ago_min = int((now - completed.get("timestamp", now)) / 60_000)
            if ago_min <= settings.jenkins_failure_window_days * 24 * 60:
                failures.append({"job": name, "number": completed.get("number"),
                                 "url": completed.get("url"),
                                 "result": completed.get("result"), "ago_min": ago_min,
                                 "duration_min": round((completed.get("duration") or 0) / 60_000, 1),
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


def claim(job: str, username: str) -> None:
    CLAIMS[job] = username


def verify_fixed(job: str) -> bool:
    """A 'fixed' claim only pays out if Jenkins agrees (live mode)."""
    if not is_live():
        if not settings.demo_mode:
            return False  # unconfigured live mode: nothing to verify against
        for j in _DEMO_JOBS:
            if j["name"] == job and j["result"] in ("FAILURE", "UNSTABLE"):
                j["result"] = "SUCCESS"
                j["ago_min"] = 1
                return True
        return False
    data = _live_overview()
    return all(f["job"] != job for f in data["failures"])
