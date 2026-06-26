"""Seed local git repositories that the dashboard clones over http.

Creates normal git repos under ``localdev/gitsrv/`` mirroring the ADO server
layout. front_local.py / the smoke test redirect ``http://LOCALDEVHOST/<path>``
to ``localdev/gitsrv/<path>`` via GIT_CONFIG insteadOf, so the dashboard's
clones resolve here with no server.

Layout created (matches the URL templates in cicd_dashboard.py):
  gitsrv/DevOps/Platform/_git/inventories      ← inventory repo (best-effort)
  gitsrv/DevOps/Platform/_git/{Engine,...}     ← sibling mirror repos (minimal)
  gitsrv/DevOps/Control/_git/{team}            ← per-team config repos (full)

Run:  python localdev/seed_git.py
Idempotent: wipes and recreates gitsrv/ each run.
"""

from __future__ import annotations

import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
GITSRV = os.path.join(HERE, "gitsrv")

# (project, app, company, dev_team, qc_team, ops_team, platform)
INV_APPS = [
    ("payments", "api",    "ACME",   "DEVJAVA",   "QCJAVA", "OPS", "ocp"),
    ("billing",  "worker", "GLOBEX", "DEVDOTNET", "QCNET",  "OPS", "k8s"),
]
# Every team that owns inventory rows also owns a Control config repo, so the
# Architecture tab's repo discovery (which falls back to the inventory team set)
# finds a repo for each one.
TEAMS = ["DEVJAVA", "DEVDOTNET", "QCJAVA", "QCNET", "OPS"]
MIRRORS = ["Engine", "ocp-templates", "Tools", "UI", "DocMDs"]


def _git(repo: str, *args: str) -> None:
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "localdev")
    env.setdefault("GIT_AUTHOR_EMAIL", "localdev@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "localdev")
    env.setdefault("GIT_COMMITTER_EMAIL", "localdev@example.com")
    subprocess.run(["git", *args], cwd=repo, check=True, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write(repo: str, relpath: str, content: str) -> None:
    full = os.path.join(repo, relpath)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def _init_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")


def _commit_all(path: str, msg: str) -> None:
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", msg)


def _seed_inventories() -> None:
    """Inventory tree matching _load_inventory_from_git's layout:
      {project}/{app}.yml                         ← defines the app
      {project}/group_vars/all/vars.yml           ← project baseline (company)
      {project}/group_vars/{app}/vars.yml         ← app vars (build/deploy/repo)
      {project}/group_vars/{env}_{app}/vars.yml   ← per-env vars + {env}_team
      {project}/host_vars/{env}_{ocp|k8s}/vars.yml← namespace
    Team ownership is the dev_team/qc_team/uat_team/prd_team keys collected from
    these levels."""
    repo = os.path.join(GITSRV, "DevOps", "Platform", "_git", "inventories")
    _init_repo(repo)
    for proj, app, co, dev, qc, ops, plat in INV_APPS:
        # app definition file (its presence defines the app)
        _write(repo, f"{proj}/{app}.yml", f"# {proj}/{app} inventory\n")
        # project baseline
        _write(repo, f"{proj}/group_vars/all/vars.yml", f"company: {co}\n")
        # app vars
        _write(repo, f"{proj}/group_vars/{app}/vars.yml",
               f"app_type: service\nrepository_name: {proj}-{app}\n"
               f"build_technology: {'maven' if co == 'ACME' else 'dotnet'}\n"
               f"deploy_technology: helm\ndeploy_platform: {plat}\n"
               f"dev_team: {dev}\nqc_team: {qc}\n")
        # per-env team ownership + image tags
        for env, team in (("dev", dev), ("qc", qc), ("uat", ops), ("prd", ops)):
            _write(repo, f"{proj}/group_vars/{env}_{app}/vars.yml",
                   f"{env}_team: {team}\n"
                   f"build_image_name: {app}-build\nbuild_image_tag: 1.5.0\n"
                   f"deploy_image_name: {app}\ndeploy_image_tag: 1.4.2\n")
        # namespaces (OCP or K8s)
        for env in ("dev", "qc", "prd"):
            _write(repo, f"{proj}/host_vars/{env}_{plat}/vars.yml",
                   f"{plat}_namespace:\n  name: {proj}-{env}\n")
    _commit_all(repo, "seed inventory")


def _seed_mirrors() -> None:
    for name in MIRRORS:
        repo = os.path.join(GITSRV, "DevOps", "Platform", "_git", name)
        _init_repo(repo)
        _write(repo, "README.md", f"# {name}\nlocaldev seed mirror repo.\n")
        _commit_all(repo, f"seed {name}")


def _seed_control() -> None:
    """Per-team config repos: <project>/<env>_<app>/config.yml with connection
    variables so the Architecture tab has nodes + edges to draw. Connections
    reference sibling app names (e.g. `api` → `worker`) so some edges resolve
    to internal app→app links rather than external endpoints."""
    # team → (project, app)
    layout = {
        "DEVJAVA":   ("payments", "api"),
        "QCJAVA":    ("payments", "checkout"),
        "DEVDOTNET": ("billing",  "worker"),
        "QCNET":     ("billing",  "portal"),
        "OPS":       ("platform", "gateway"),
    }
    for team in TEAMS:
        proj, app = layout.get(team, ("platform", team.lower()))
        repo = os.path.join(GITSRV, "DevOps", "Control", "_git", team)
        _init_repo(repo)
        for env in ("dev", "qc", "prd"):
            _write(repo, f"{proj}/{env}_{app}/config.yml",
                   f"# {proj}/{env}_{app}\n"
                   f"service:\n"
                   f"  name: {app}\n"
                   f"  port: 8080\n"
                   f"database:\n"
                   f"  db_hostname: {proj}-db-{env}\n"
                   f"  db_port: 5432\n"
                   f"  url: postgresql://{proj}-db-{env}:5432/{proj}\n"
                   f"upstream:\n"
                   f"  # resolves to the gateway app (internal edge)\n"
                   f"  gateway_url: http://gateway:8080/route\n"
                   f"  worker_endpoint: http://worker:9090\n"
                   f"  auth_url: https://auth-{env}.acme.local/oauth\n"
                   f"messaging:\n"
                   f"  kafka_url: kafka://broker-{env}:9092\n"
                   f"cache:\n"
                   f"  redis_url: redis://cache-{env}:6379\n")
        _commit_all(repo, f"seed control config for {team}")


def main() -> None:
    if shutil.which("git") is None:
        raise SystemExit("git is not on PATH — install git first.")
    if os.path.isdir(GITSRV):
        shutil.rmtree(GITSRV, ignore_errors=True)
    os.makedirs(GITSRV, exist_ok=True)
    _seed_inventories()
    _seed_mirrors()
    _seed_control()
    print(f"Seeded local git under {GITSRV}")
    print("Repos:")
    for root, dirs, _files in os.walk(GITSRV):
        if root.endswith(".git"):
            dirs[:] = []
            print("  " + os.path.relpath(os.path.dirname(root), GITSRV))


if __name__ == "__main__":
    main()
