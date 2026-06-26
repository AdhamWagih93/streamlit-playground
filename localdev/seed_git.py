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

TEAMS = ["DEVJAVA", "DEVDOTNET"]
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
    """Best-effort inventory tree. Adjust to match your real inventories repo
    structure if rows don't parse — the page renders regardless (graceful)."""
    repo = os.path.join(GITSRV, "DevOps", "Platform", "_git", "inventories")
    _init_repo(repo)
    proj = "payments"
    # Per-app file
    _write(repo, f"{proj}/api.yml",
           "company: ACME\n"
           "app_type: service\n"
           "repository_name: payments-api\n"
           "build_technology: maven\n"
           "deploy_technology: helm\n"
           "deploy_platform: ocp\n")
    # group_vars per env (team ownership lives here as <env>_team / dev_team)
    for env, team in (("dev", "DEVJAVA"), ("qc", "DEVJAVA"), ("prd", "DEVOPS")):
        _write(repo, f"{proj}/group_vars/{env}/vars.yml",
               f"dev_team: DEVJAVA\nqc_team: DEVJAVA\n{env}_team: {team}\n")
    # host_vars per project (namespace for OCP)
    _write(repo, f"{proj}/host_vars/prd_ocp/vars.yml",
           "ocp_namespace:\n  name: payments-prd\n")
    _write(repo, f"{proj}/host_vars/dev_ocp/vars.yml",
           "ocp_namespace:\n  name: payments-dev\n")
    _commit_all(repo, "seed inventory")


def _seed_mirrors() -> None:
    for name in MIRRORS:
        repo = os.path.join(GITSRV, "DevOps", "Platform", "_git", name)
        _init_repo(repo)
        _write(repo, "README.md", f"# {name}\nlocaldev seed mirror repo.\n")
        _commit_all(repo, f"seed {name}")


def _seed_control() -> None:
    """Per-team config repos: <project>/<env>_<app>/config.yml with connection
    variables so the Architecture tab has nodes + edges to draw."""
    for team in TEAMS:
        repo = os.path.join(GITSRV, "DevOps", "Control", "_git", team)
        _init_repo(repo)
        app = "api" if team == "DEVJAVA" else "worker"
        proj = "payments" if team == "DEVJAVA" else "billing"
        for env in ("dev", "qc", "prd"):
            _write(repo, f"{proj}/{env}_{app}/config.yml",
                   f"# {proj}/{env}_{app}\n"
                   f"service:\n"
                   f"  name: {proj}-{app}\n"
                   f"  port: 8080\n"
                   f"database:\n"
                   f"  db_hostname: {proj}-db-{env}\n"
                   f"  db_port: 5432\n"
                   f"  url: postgresql://{proj}-db-{env}:5432/{proj}\n"
                   f"upstream:\n"
                   f"  auth_url: https://auth-{env}.acme.local/oauth\n"
                   f"  billing_api: http://billing-{app}:8080/v1\n"
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
