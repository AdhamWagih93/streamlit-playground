"""Generate realistic ES fixtures the FakeES serves, so the dashboard's tiles,
charts and tables populate as if a real cluster were behind it.

Writes localdev/fixtures/<index>.json (a list of _source docs). Entity names
(companies / projects / apps / teams) match seed_git.py so the git-driven and
ES-driven surfaces line up. Dates are recent (relative to now) so they fall in
the default time windows. Re-run any time:  python localdev/seed_es_fixtures.py
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
NOW = datetime.now(timezone.utc)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S")


def _dump(index: str, docs: list) -> None:
    os.makedirs(FIX, exist_ok=True)
    with open(os.path.join(FIX, f"{index}.json"), "w", encoding="utf-8") as fh:
        json.dump(docs, fh, indent=1)
    print(f"  {index}.json  ({len(docs)} docs)")


# ── canonical entities (kept in sync with seed_git.py) ─────────────────────
APPS = [
    # project,   app,       company, dev,        qc,      ops,    jirakey, prd_ver, qc_ver, platform
    ("payments", "api",     "ACME",   "DEVJAVA",  "QCJAVA", "OPS", "PAY", "1.4.2", "1.5.0", "ocp"),
    ("payments", "gateway", "ACME",   "DEVJAVA",  "QCJAVA", "OPS", "PAY", "2.1.0", "2.2.0", "ocp"),
    ("billing",  "worker",  "GLOBEX", "DEVDOTNET","QCNET",  "OPS", "BIL", "3.0.7", "3.1.0", "k8s"),
    ("billing",  "portal",  "GLOBEX", "DEVDOTNET","QCNET",  "OPS", "BIL", "0.9.4", "0.10.0", "k8s"),
]
ENVS = ["dev", "qc", "uat", "prd"]
USERS = [
    ("Alice Dev",  "alice.dev@acme.local",   "DEVJAVA"),
    ("Bob Dev",    "bob.dev@acme.local",     "DEVJAVA"),
    ("Carol QC",   "carol.qc@acme.local",    "QCJAVA"),
    ("Dan Net",    "dan.net@globex.local",   "DEVDOTNET"),
    ("Eve Ops",    "eve.ops@acme.local",     "OPS"),
]


def gen_inventory():
    out = []
    for proj, app, co, dev, qc, ops, _jk, prd_ver, _qcv, plat in APPS:
        out.append({
            "id": f"{proj}-{app}",
            "application": app, "project": proj, "company": co,
            "app_type": "service",
            "dev_team": dev, "qc_team": qc, "uat_team": ops, "prd_team": ops,
            "build_technology": "maven" if co == "ACME" else "dotnet",
            "deploy_technology": "helm", "deploy_platform": plat,
            "build_image": {"name": f"{app}-build", "tag": prd_ver},
            "deploy_image": {"name": f"{app}", "tag": prd_ver},
            "repository_name": f"{proj}-{app}",
        })
    _dump("ef-devops-inventory", out)


def gen_devops_projects():
    out = []
    for proj, app, co, dev, qc, ops, jk, prd_ver, qcv, plat in APPS:
        out.append({
            "id": f"dp-{proj}-{app}",
            "App": app, "Project": proj, "Company": co, "AppType": "service",
            "DeployPlatform": "OCP" if plat == "ocp" else "K8s",
            "BuildTechnology": "maven" if co == "ACME" else "dotnet",
            "DeployTechnology": "helm",
            "BuildImageName": f"{app}-build", "BuildImageTag": prd_ver,
            "DeployImageName": app, "DeployImageTag": prd_ver,
            "BuildCurrentVer": prd_ver, "BuildRecommendationVer": qcv,
            "DeployCurrentVer": prd_ver, "DeployRecommendationVer": qcv,
            "DeployThroughInternet": "true",
            "qcRouteUrl": f"https://{app}-qc.{co.lower()}.local",
            "qcServiceUrl": f"http://{app}.svc.qc:8080",
            "DevTeam": dev, "QcTeam": qc, "PrdTeam": ops,
            "RemedyProductName": f"{proj.title()} Platform",
            "RemedyProductTier1": co, "RemedyProductTier2": proj.title(),
            "RemedyProductTier3": app,
            "JiraProjectKey": jk,
        })
    _dump("ef-devops-projects", out)


def gen_jira():
    prios = ["Highest", "High", "Medium", "Low", "Medium", "High"]
    types = ["Bug", "Story", "Task", "Bug", "Improvement", "Incident"]
    statuses = ["Open", "In Progress", "To Do", "Reopened"]
    out = []
    n = 1
    for proj, app, co, dev, qc, ops, jk, *_ in APPS:
        for i in range(4):
            out.append({
                "id": f"{jk}-{n}",
                "project": proj, "projectkey": jk,
                "issuekey": f"{jk}-{100 + n}",
                "issueurl": f"http://jira.local/browse/{jk}-{100 + n}",
                "summary": f"{app}: {types[(n) % len(types)]} in {proj}",
                "priority": prios[n % len(prios)],
                "status": statuses[i % len(statuses)],
                "issuetype": types[n % len(types)],
                "assignee": USERS[n % len(USERS)][0],
                "reporter": USERS[(n + 1) % len(USERS)][0],
                "creator": USERS[(n + 1) % len(USERS)][0],
                "reporterteam": dev,
                "created": _iso(3 + n), "updated": _iso(1 + (n % 3)),
            })
            n += 1
    # a few closed ones (exercises open vs closed split)
    for i in range(3):
        out.append({
            "id": f"CLOSED-{i}", "project": "payments", "projectkey": "PAY",
            "issuekey": f"PAY-{200 + i}", "issueurl": f"http://jira.local/browse/PAY-{200 + i}",
            "summary": "resolved item", "priority": "Low", "status": "Done",
            "issuetype": "Task", "assignee": "Alice Dev", "reporter": "Bob Dev",
            "creator": "Bob Dev", "reporterteam": "DEVJAVA",
            "created": _iso(20 + i), "updated": _iso(10 + i),
        })
    _dump("ef-bs-jira-issues", out)


def gen_builds():
    out = []
    n = 0
    for proj, app, co, dev, qc, ops, jk, prd_ver, qcv, plat in APPS:
        for i in range(6):
            user = USERS[(n) % len(USERS)]
            ok = (i % 5) != 0
            out.append({
                "id": f"build-{app}-{i}",
                "application": app, "project": proj,
                "codeversion": qcv if i < 3 else prd_ver,
                "status": "SUCCESS" if ok else "FAILED",
                "testflag": "Normal",
                "startdate": _iso(i * 2 + 1), "enddate": _iso(i * 2 + 1),
                "authorname": user[0], "authormail": user[1],
                "commitauthor": f"{user[0]} <{user[1]}>",
                "team": dev,
            })
            n += 1
    _dump("ef-cicd-builds", out)


def gen_deployments():
    out = []
    for proj, app, co, dev, qc, ops, jk, prd_ver, qcv, plat in APPS:
        for env in ENVS:
            ok = not (env == "prd" and app == "portal")
            out.append({
                "id": f"deploy-{app}-{env}",
                "application": app, "project": proj, "environment": env,
                "codeversion": prd_ver if env in ("prd", "uat") else qcv,
                "status": "SUCCESS" if ok else "FAILED",
                "testflag": "Normal",
                "startdate": _iso(ENVS.index(env) + 1),
                "requester": USERS[0][0], "approver": USERS[4][0],
                "team": ops if env in ("uat", "prd") else dev,
            })
    _dump("ef-cicd-deployments", out)


def gen_releases():
    out = []
    for proj, app, co, dev, qc, ops, jk, prd_ver, *_ in APPS:
        out.append({
            "id": f"rel-{app}", "application": app, "project": proj,
            "codeversion": prd_ver, "status": "SUCCESS",
            "releasedate": _iso(5),
            "commitauthor": f"{USERS[0][0]} <{USERS[0][1]}>",
        })
    _dump("ef-cicd-releases", out)


def gen_security():
    for index, scale in (("ef-cicd-prismacloud", 1.0),
                         ("ef-cicd-invicti", 0.6),
                         ("ef-cicd-zap", 0.4)):
        out = []
        for proj, app, co, dev, qc, ops, jk, prd_ver, qcv, plat in APPS:
            for ver in (prd_ver, qcv):
                seed = (len(app) + len(ver)) % 5
                out.append({
                    "id": f"{index}-{app}-{ver}",
                    "application": app, "codeversion": ver,
                    "project": proj, "environment": "prd" if ver == prd_ver else "qc",
                    "Vcritical": int(seed * scale), "Vhigh": int((seed + 2) * scale),
                    "Vmedium": int((seed + 4) * scale), "Vlow": int((seed + 6) * scale),
                    "Ccritical": int(seed * scale), "Chigh": int((seed + 1) * scale),
                    "Cmedium": int((seed + 3) * scale), "Clow": int((seed + 5) * scale),
                    "status": "Completed",
                    "imageName": app, "imageTag": ver,
                    "startdate": _iso(2), "enddate": _iso(2),
                })
        _dump(index, out)


def gen_versions():
    out = []
    for _i, (proj, app, co, dev, qc, ops, jk, prd_ver, qcv, plat) in enumerate(APPS):
        base = prd_ver.rsplit(".", 1)[0]
        # First app: PRD history but no next_hotfix → exercises the (hidden by
        # default) "missing next_hotfix" warning + its reveal toggle.
        _hot = "" if _i == 0 else f"{prd_ver}-hf1"
        out.append({
            "id": f"ver-{app}", "application": app, "project": proj,
            "next_develop": f"{qcv}", "next_release": f"{qcv}",
            "next_stress": "", "next_hotfix": _hot,
        })
    # Index-hygiene violations to exercise the version-lookup warnings:
    #  - ORPHAN: an app/project that exists in no inventory row.
    out.append({"id": "ver-ghost", "application": "ghost-service",
                "project": "retired", "next_develop": "9.9.9",
                "next_release": "9.9.9", "next_stress": "", "next_hotfix": ""})
    #  - DUPLICATE: a second document for an existing app (payments/api).
    out.append({"id": "ver-api-dup", "application": "api", "project": "payments",
                "next_develop": "1.5.1", "next_release": "1.5.1",
                "next_stress": "", "next_hotfix": "1.4.2-hf2"})
    _dump("ef-cicd-versions-lookup", out)


def gen_commits():
    out = []
    n = 0
    for proj, app, co, dev, qc, ops, *_ in APPS:
        for i in range(4):
            u = USERS[n % len(USERS)]
            out.append({
                "id": f"commit-{app}-{i}", "project": proj,
                "authorname": u[0], "authormail": u[1],
                "commitauthor": f"{u[0]} <{u[1]}>",
                "date": _iso(i + 1), "message": f"work on {app}",
            })
            n += 1
    _dump("ef-git-commits", out)


def gen_requests():
    reqs = []
    apprs = []
    for proj, app, co, dev, qc, ops, *_ in APPS:
        reqs.append({
            "id": f"req-{app}", "application": app, "project": proj,
            "Requester": USERS[0][0], "RequestDate": _iso(2),
            "environment": "prd", "status": "Pending",
        })
        apprs.append({
            "id": f"appr-{app}", "application": app, "project": proj,
            "Requester": USERS[0][0], "ApprovedBy": USERS[4][0],
            "RequestDate": _iso(3), "status": "Approved",
        })
    _dump("ef-devops-requests", reqs)
    _dump("ef-cicd-approval", apprs)


def main():
    print(f"Writing fixtures to {FIX} (now = {NOW.isoformat()})")
    gen_inventory()
    gen_devops_projects()
    gen_jira()
    gen_builds()
    gen_deployments()
    gen_releases()
    gen_security()
    gen_versions()
    gen_commits()
    gen_requests()
    print("Done. Re-run after changing entities; FakeES serves these live.")


if __name__ == "__main__":
    main()
