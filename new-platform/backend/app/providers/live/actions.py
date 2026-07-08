"""Actions slice — live mode: real Jenkins over its JSON API.

Mirrors app/providers/demo/actions.py signatures exactly. Credentials resolve via
clients.jenkins_creds() (Vault path with env fallback). Anything that cannot be
implemented faithfully (candidates need the ES inventory) raises
IntegrationUnavailable — never fake data in live mode.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from fastapi import HTTPException

from ...auth.rbac import User
from .clients import IntegrationUnavailable, jenkins_creds

ENVS = ["dev", "qc", "uat", "prd"]

PIPELINES: dict[str, dict] = {
    "build": dict(folder="CICD", name="Build", label="Build"),
    "deploy_request": dict(folder="CICD", name="Request_deploy", label="Request deploy"),
    "release_request": dict(folder="CICD", name="Request_promote", label="Request promote"),
}

ROLE_DEPLOY_ENVS = {
    "Admin": list(ENVS),
    "CLevel": list(ENVS),
    "Developer": ["dev"],
    "QC": ["qc"],
    "Operations": ["uat", "prd"],
}

_TIMEOUT = 15


def _creds() -> dict[str, str]:
    c = jenkins_creds()
    if not c.get("host"):
        raise IntegrationUnavailable("Jenkins", "host not configured (Vault/JENKINS_HOSTNAME)")
    return c


def _auth_header(c: dict[str, str]) -> str:
    token = base64.b64encode(f"{c['username']}:{c['api_token']}".encode()).decode()
    return f"Basic {token}"


def _request(c: dict[str, str], path: str, data: bytes | None = None,
             headers: dict[str, str] | None = None):
    host = c["host"].rstrip("/")
    req = urllib.request.Request(f"{host}{path}", data=data, method="POST" if data is not None else "GET")
    req.add_header("Authorization", _auth_header(c))
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=_TIMEOUT)  # noqa: S310 — host from Vault config


def _get_json(c: dict[str, str], path: str) -> dict:
    try:
        with _request(c, path) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise IntegrationUnavailable("Jenkins", f"GET {path} → HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise IntegrationUnavailable("Jenkins", f"GET {path} failed: {exc}")


def jenkins_status(user: User) -> dict:
    c = _creds()
    root = _get_json(c, "/api/json?tree=mode,numExecutors")
    version = ""
    try:
        with _request(c, "/api/json?tree=mode") as resp:
            version = resp.headers.get("X-Jenkins", "")
    except Exception:
        pass
    pipelines = []
    for key, spec in PIPELINES.items():
        job_path = f"/job/{spec['folder']}/job/{spec['name']}"
        job = _get_json(c, f"{job_path}/api/json"
                           "?tree=buildable,inQueue,queueItem[why],"
                           "builds[number,building,actions[parameters[name,value]],timestamp]")
        last = _get_json(c, f"{job_path}/lastCompletedBuild/api/json"
                            "?tree=number,result,timestamp,duration")
        last_build = None
        if last.get("number"):
            last_build = dict(
                number=last["number"], result=last.get("result") or "UNKNOWN",
                when=_ms_to_iso(last.get("timestamp")),
                duration_s=int((last.get("duration") or 0) / 1000),
            )
        running = []
        for b in job.get("builds") or []:
            if not b.get("building"):
                continue
            params = {}
            for a in b.get("actions") or []:
                for p in a.get("parameters") or []:
                    params[p.get("name", "")] = p.get("value", "")
            running.append(dict(number=b.get("number"), pipeline=key, params=params,
                                since=_ms_to_iso(b.get("timestamp"))))
        pipelines.append(dict(
            key=key, label=spec["label"], folder=spec["folder"], name=spec["name"],
            path=f"{spec['folder']}/{spec['name']}", params=[],
            ready=bool(job.get("buildable")), last_build=last_build,
            running=running, queue=1 if job.get("inQueue") else 0,
        ))
    return dict(version=version or "unknown", ready=bool(root is not None), pipelines=pipelines)


def _ms_to_iso(ms) -> str:
    from datetime import datetime, timezone
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def candidates(user: User) -> list[dict]:
    # Candidates derive from the ES inventory stage matrix, which the live
    # inventory slice owns; wiring it here is not yet implemented.
    raise IntegrationUnavailable(
        "Actions", "live action candidates require the ES inventory (not implemented)")


def trigger(user: User, pipeline: str, params: dict) -> dict:
    spec = PIPELINES.get(pipeline)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline {pipeline!r}")
    params = dict(params or {})

    # Role gates that need no inventory data (team/stage checks live in ES).
    if pipeline == "deploy_request":
        env = (params.get("targetEnv") or "").strip().lower()
        if env not in ENVS:
            raise HTTPException(status_code=400, detail=f"Unknown target environment {env!r}")
        allowed: set[str] = set()
        for r in user.roles:
            allowed.update(ROLE_DEPLOY_ENVS.get(r, []))
        if env not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Role(s) {', '.join(user.roles)} may not deploy to {env.upper()}")
    elif pipeline == "release_request":
        if not (user.is_admin or "QC" in user.roles):
            raise HTTPException(status_code=403,
                                detail="Release/promote requires QC or Admin role")
    elif pipeline == "build":
        if not (user.is_admin or "Developer" in user.roles):
            raise HTTPException(status_code=403,
                                detail=f"Role(s) {', '.join(user.roles)} may not trigger builds")

    c = _creds()
    crumb = _get_json(c, "/crumbIssuer/api/json")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if crumb.get("crumbRequestField"):
        headers[crumb["crumbRequestField"]] = crumb.get("crumb", "")
    body = urllib.parse.urlencode({k: str(v) for k, v in params.items()}).encode()
    path = f"/job/{spec['folder']}/job/{spec['name']}/buildWithParameters"
    try:
        with _request(c, path, data=body, headers=headers) as resp:
            if resp.status not in (200, 201, 302):
                raise IntegrationUnavailable("Jenkins", f"trigger → HTTP {resp.status}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(status_code=403, detail=f"Jenkins rejected the trigger ({exc.code})")
        raise IntegrationUnavailable("Jenkins", f"trigger failed: HTTP {exc.code}")
    except (urllib.error.URLError, OSError) as exc:
        raise IntegrationUnavailable("Jenkins", f"trigger failed: {exc}")

    nxt = _get_json(c, f"/job/{spec['folder']}/job/{spec['name']}/api/json?tree=nextBuildNumber")
    return {"queued": True, "build_number": int(nxt.get("nextBuildNumber") or 0)}
