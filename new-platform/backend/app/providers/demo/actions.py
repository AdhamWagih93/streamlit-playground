"""Actions slice — Jenkins trigger surface over the demo world.

Module-level mutable Jenkins state (seeded once from the world) so triggered
builds show up as "running" for the rest of the process lifetime, and every
trigger appends a synthetic event to world.events so the Event Log ticks.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from fastapi import HTTPException

from ...auth.rbac import User, team_match_key
from .scope import visible_apps
from .world import ENVS, App, get_world

JENKINS_VERSION = "lts-2.462.3"

PIPELINES: dict[str, dict] = {
    "build": dict(
        folder="CICD", name="Build", label="Build",
        params=[
            dict(name="projectName", kind="choice"),
            dict(name="applicationName", kind="choice"),
            dict(name="branchName", kind="string", default="release"),
        ],
    ),
    "deploy_request": dict(
        folder="CICD", name="Request_deploy", label="Request deploy",
        params=[
            dict(name="projectName", kind="choice"),
            dict(name="applicationName", kind="choice"),
            dict(name="targetEnv", kind="choice", options=ENVS),
            dict(name="codeVersion", kind="choice"),
        ],
    ),
    "release_request": dict(
        folder="CICD", name="Request_promote", label="Request promote",
        params=[
            dict(name="projectName", kind="choice"),
            dict(name="applicationName", kind="choice"),
            dict(name="codeVersion", kind="choice"),
            dict(name="qccomments", kind="string", default=""),
        ],
    ),
}

# deploy chain: source stage → target env
DEPLOY_CHAIN = [("release", "dev"), ("dev", "qc"), ("qc", "uat"), ("uat", "prd")]

# Envs each role may DEPLOY to (actions are stricter than visibility).
ROLE_DEPLOY_ENVS = {
    "Admin": list(ENVS),
    "CLevel": list(ENVS),
    "Developer": ["dev"],
    "QC": ["qc"],
    "Operations": ["uat", "prd"],
}

_state: dict | None = None


def _seed_last_build(events: list[dict], types: tuple[str, ...], rng: random.Random) -> dict:
    ev = next((e for e in events if e["type"] in types), None)
    if ev is None:
        return dict(number=rng.randint(1200, 4500), result="SUCCESS",
                    when=datetime.now(timezone.utc).isoformat(), duration_s=rng.randint(120, 700))
    result = "SUCCESS" if ev["status"] in ("ok", "approved") else \
             "FAILURE" if ev["status"] == "failed" else "SUCCESS"
    return dict(
        number=rng.randint(1200, 4500),
        result=result,
        when=ev["when"].isoformat(),
        duration_s=int(ev.get("duration_s") or rng.randint(120, 700)),
    )


def _jenkins() -> dict:
    """Mutable module-level Jenkins state, seeded deterministically from the world."""
    global _state
    if _state is None:
        w = get_world()
        rng = random.Random(hash("demo-jenkins") & 0xFFFF)
        seeds = {
            "build": ("build-develop", "build-release"),
            "deploy_request": ("deploy",),
            "release_request": ("release", "request"),
        }
        pipes = {}
        for key, types in seeds.items():
            last = _seed_last_build(w.events, types, rng)
            pipes[key] = dict(last_build=last, running=[], queue=0,
                              next_number=last["number"] + 1)
        _state = dict(version=JENKINS_VERSION, ready=True, pipelines=pipes)
    return _state


# ------------------------------------------------------------------ shared RBAC
def _deployable_envs(user: User) -> set[str]:
    out: set[str] = set()
    for r in user.roles:
        out.update(ROLE_DEPLOY_ENVS.get(r, []))
    return out


def _can_build(user: User) -> bool:
    return user.is_admin or "Developer" in user.roles


def _can_release(user: User, app: App) -> bool:
    if user.is_admin:
        return True
    if "QC" not in user.roles:
        return False
    qc_teams = {team_match_key(t) for t in (app.teams.get("qc_team") or [])}
    return bool(qc_teams & user.team_keys)


def _stage_version(app: App, stage: str) -> str:
    return (app.stages.get(stage) or {}).get("version") or ""


# ------------------------------------------------------------------ endpoints
def jenkins_status(user: User) -> dict:
    st = _jenkins()
    pipelines = []
    for key, spec in PIPELINES.items():
        p = st["pipelines"][key]
        pipelines.append(dict(
            key=key, label=spec["label"], folder=spec["folder"], name=spec["name"],
            path=f"{spec['folder']}/{spec['name']}", params=spec["params"],
            ready=True, last_build=p["last_build"], running=p["running"],
            queue=p["queue"] + len(p["running"]),
        ))
    return dict(version=st["version"], ready=st["ready"], pipelines=pipelines)


def candidates(user: User) -> list[dict]:
    apps = visible_apps(user)
    deploy_envs = _deployable_envs(user)
    can_build = _can_build(user)
    out: list[dict] = []
    for app in apps:
        next_rel = app.next_versions.get("release")
        if can_build and next_rel:
            out.append(dict(
                pipeline="build", section="Build",
                app=app.application, project=app.project,
                params=dict(projectName=app.project, applicationName=app.application,
                            branchName="release"),
                reason=f"release branch → {next_rel}",
            ))
        for src, tgt in DEPLOY_CHAIN:
            if tgt not in deploy_envs:
                continue
            src_v, tgt_v = _stage_version(app, src), _stage_version(app, tgt)
            if src_v and src_v != tgt_v:
                out.append(dict(
                    pipeline="deploy_request", section=f"Deploy → {tgt.upper()}",
                    app=app.application, project=app.project,
                    params=dict(projectName=app.project, applicationName=app.application,
                                targetEnv=tgt, codeVersion=src_v),
                    reason=f"{src} {src_v} → {tgt} {tgt_v or '—'}",
                ))
        if _can_release(user, app):
            qc_v = _stage_version(app, "qc")
            if qc_v:
                out.append(dict(
                    pipeline="release_request", section="Release",
                    app=app.application, project=app.project,
                    params=dict(projectName=app.project, applicationName=app.application,
                                codeVersion=qc_v, qccomments=""),
                    reason=f"promote {qc_v} (qc-verified)",
                ))
    return out


def trigger(user: User, pipeline: str, params: dict) -> dict:
    """Server-side re-validation of the SAME rules the candidates were computed with."""
    spec = PIPELINES.get(pipeline)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unknown pipeline {pipeline!r}")
    params = dict(params or {})
    project = (params.get("projectName") or "").strip()
    app_name = (params.get("applicationName") or "").strip()
    app = next((a for a in visible_apps(user)
                if a.project == project and a.application == app_name), None)
    if app is None:
        raise HTTPException(status_code=403,
                            detail=f"Application {project}/{app_name} is not in your scope")

    now = datetime.now(timezone.utc)
    ev_env, ev_branch = "", ""

    if pipeline == "build":
        if not _can_build(user):
            raise HTTPException(status_code=403,
                                detail=f"Role(s) {', '.join(user.roles)} may not trigger builds")
        if not app.next_versions.get("release"):
            raise HTTPException(status_code=403,
                                detail=f"{app.application} has no next release version to build")
        branch = (params.get("branchName") or "release").strip() or "release"
        params["branchName"] = branch
        version = app.next_versions.get(branch) or app.next_versions["release"]
        ev_type, ev_branch = "build-release", branch
        detail = f"Build {branch} queued via platform"

    elif pipeline == "deploy_request":
        env = (params.get("targetEnv") or "").strip().lower()
        if env not in ENVS:
            raise HTTPException(status_code=400, detail=f"Unknown target environment {env!r}")
        if env not in _deployable_envs(user):
            raise HTTPException(
                status_code=403,
                detail=f"Role(s) {', '.join(user.roles)} may not deploy to {env.upper()}")
        src = next(s for s, t in DEPLOY_CHAIN if t == env)
        src_v, tgt_v = _stage_version(app, src), _stage_version(app, env)
        if not src_v or src_v == tgt_v:
            raise HTTPException(
                status_code=403,
                detail=f"Nothing to deploy: {src} stage is "
                       f"{src_v or 'empty'}, {env} already at {tgt_v or '—'}")
        version = (params.get("codeVersion") or "").strip() or src_v
        if version != src_v:
            raise HTTPException(
                status_code=403,
                detail=f"codeVersion {version} does not match the {src} stage ({src_v})")
        params["codeVersion"] = version
        ev_type, ev_env = "deploy", env
        detail = f"Deploy {version} → {env.upper()} queued via platform"

    elif pipeline == "release_request":
        if not _can_release(user, app):
            raise HTTPException(
                status_code=403,
                detail="Release/promote requires QC role with a matching qc_team (or Admin)")
        qc_v = _stage_version(app, "qc")
        if not qc_v:
            raise HTTPException(status_code=403,
                                detail=f"{app.application} has no qc-verified version to promote")
        version = (params.get("codeVersion") or "").strip() or qc_v
        if version != qc_v:
            raise HTTPException(
                status_code=403,
                detail=f"codeVersion {version} does not match the qc stage ({qc_v})")
        params["codeVersion"] = version
        ev_type = "release"
        detail = f"Promote {version} requested via platform"

    else:  # pragma: no cover — PIPELINES keys are exhaustive
        raise HTTPException(status_code=400, detail=f"Unknown pipeline {pipeline!r}")

    # ---- queue it: mutate jenkins state + append a synthetic world event ----
    st = _jenkins()
    p = st["pipelines"][pipeline]
    number = p["next_number"]
    p["next_number"] += 1
    p["running"].append(dict(number=number, pipeline=pipeline,
                             params=params, since=now.isoformat()))

    w = get_world()
    eid = max((e["id"] for e in w.events), default=1000) + 1
    w.events.insert(0, dict(
        id=eid, type=ev_type, app=app.application, project=app.project,
        company=app.company, version=version, status="running", when=now,
        user=user.display_name, email=user.email, detail=detail,
        env=ev_env, branch=ev_branch,
    ))
    return {"queued": True, "build_number": number}
