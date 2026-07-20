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
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
GITSRV = os.path.join(HERE, "gitsrv")


def _days_ago_iso(days: float) -> str:
    """Git-friendly ISO timestamp N days before now (for backdated commits)."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S")

# (project, app, company, dev_team, qc_team, ops_team, platform)
# (project, app, company, dev_team, qc_team, ops_team, platform, build_tech,
#  deploy_tech). Build/deploy technologies + platforms are deliberately varied
# (a dominant value, a couple of mid-tier ones, and a few singletons) so the
# Tech & Platforms tab has a realistic "most/least used" distribution to rank
# and cross-reference against teams + projects.
INV_APPS = [
    ("payments",  "api",      "ACME",   "DEVJAVA",   "QCJAVA", "OPS", "ocp", "maven",  "helm"),
    ("payments",  "checkout", "ACME",   "DEVJAVA",   "QCJAVA", "OPS", "ocp", "maven",  "helm"),
    ("payments",  "ledger",   "ACME",   "DEVJAVA",   "QCJAVA", "OPS", "k8s", "gradle", "ansible"),
    ("billing",   "worker",   "GLOBEX", "DEVDOTNET", "QCNET",  "OPS", "k8s", "dotnet", "helm"),
    ("billing",   "invoice",  "GLOBEX", "DEVDOTNET", "QCNET",  "OPS", "ocp", "dotnet", "ansible"),
    ("billing",   "settle",   "GLOBEX", "DEVDOTNET", "QCNET",  "OPS", "vm",  "dotnet", "ansible"),
    ("portal",    "web",      "ACME",   "DEVJAVA",   "QCJAVA", "OPS", "k8s", "npm",    "helm"),
    ("portal",    "admin",    "ACME",   "DEVJAVA",   "QCJAVA", "OPS", "ocp", "maven",  "helm"),
    ("portal",    "gateway",  "ACME",   "DEVJAVA",   "QCJAVA", "OPS", "ocp", "maven",  "helm"),
    ("analytics", "etl",      "GLOBEX", "DEVDOTNET", "QCNET",  "OPS", "k8s", "python", "kustomize"),
    ("analytics", "report",   "GLOBEX", "DEVDOTNET", "QCNET",  "OPS", "ocp", "gradle", "helm"),
    ("analytics", "stream",   "GLOBEX", "DEVDOTNET", "QCNET",  "OPS", "vm",  "maven",  "helm"),
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


def _commit_all_dated(path: str, msg: str, days_ago: float,
                      author: str = "Config Bot <config@acme.local>") -> None:
    """Commit with a backdated author+committer date and a specific author, so
    the Architecture tab's config-commit vs deployment provenance is testable."""
    _when = _days_ago_iso(days_ago)
    _name, _email = author.rsplit("<", 1)
    _name = _name.strip()
    _email = _email.rstrip(">").strip()
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = _name
    env["GIT_AUTHOR_EMAIL"] = _email
    env["GIT_COMMITTER_NAME"] = _name
    env["GIT_COMMITTER_EMAIL"] = _email
    env["GIT_AUTHOR_DATE"] = _when
    env["GIT_COMMITTER_DATE"] = _when
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=path, check=True,
                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    for proj, app, co, dev, qc, ops, plat, build, dtech in INV_APPS:
        # app definition file (its presence defines the app)
        _write(repo, f"{proj}/{app}.yml", f"# {proj}/{app} inventory\n")
        # project baseline
        _write(repo, f"{proj}/group_vars/all/vars.yml", f"company: {co}\n")
        # app vars
        _write(repo, f"{proj}/group_vars/{app}/vars.yml",
               f"app_type: service\nrepository_name: {proj}-{app}\n"
               f"build_technology: {build}\n"
               f"deploy_technology: {dtech}\ndeploy_platform: {plat}\n"
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
    variables so the Architecture tab has nodes + edges to draw.

    URL convention (matches the real fleet): a call to an app's service uses
    host ``<image>-service`` where image = app name lowercased with . / _ → -.
    So ``http://worker-service:9090`` resolves to the app ``worker``. This lets
    the arch tab draw INTERNAL app→app edges (including to OUT-OF-SCOPE apps,
    which get their own project box) and reserve external endpoints for hosts
    that match no app. ``*.svc.cluster.local`` hosts that match no app are
    grouped into a single "Cluster Services" box. Also seeds a ``_bkp`` folder
    (must be ignored) and commented-out lines (must be ignored)."""
    # team → (project, app)
    layout = {
        "DEVJAVA":   ("payments", "api"),
        "QCJAVA":    ("payments", "checkout"),
        "DEVDOTNET": ("billing",  "worker"),
        "QCNET":     ("portal",   "web"),
        "OPS":       ("platform", "gateway"),
    }
    # Per-app extra connection block — references OTHER apps by the -service
    # convention so cross-project (out-of-scope) edges appear.
    _refs = {
        "api": (
            "siblings:\n"
            "  checkout_url: http://checkout-service:8080/api     # in-scope (payments)\n"
            "  worker_grpc: grpc://worker-service:9090            # OUT of scope (billing), gRPC\n"
            "cluster:\n"
            "  # gateway reached via its cluster DNS — still resolves to the app\n"
            "  gateway_via_cluster: http://gateway-service.platform.svc.cluster.local:8080\n"
            "  session_store: redis://sessionstore.default.svc.cluster.local:6379  # → Cluster Services\n"
            "  metrics_sink: prometheus-pushgateway.monitoring.svc.cluster.local:9091\n"
            "legacy_host:\n"
            "  # same IP, three different ports → ONE grouped IP box w/ port + type\n"
            "  rest_api: http://10.20.30.40:8080/v1\n"
            "  grpc_api: grpc://10.20.30.40:9090\n"
            "  metrics: tcp://10.20.30.40:9100\n"
            "messaging:\n"
            "  events: kafka://kafka-broker.acme.local:9092   # Kafka (queue type)\n"
            "namespaced:\n"
            "  # <image>-<namespace> service form (namespace = project) → app checkout\n"
            "  checkout_ns: http://checkout-payments:8080/api\n"
            "external:\n"
            "  auth_url: https://auth.acme.local/oauth\n"
            "#  old_worker_url: http://legacy-worker.acme.local:9090   # COMMENTED — must be ignored\n"
        ),
        "checkout": (
            "siblings:\n"
            "  api_url: http://api-service:8080/checkout   # in-scope internal edge\n"
        ),
        "worker": (
            "siblings:\n"
            "  gateway_url: http://gateway-service:8080/route   # → platform (out of scope)\n"
        ),
        "web": (
            "siblings:\n"
            "  api_url: http://api-service:8080   # → payments (out of scope)\n"
        ),
        "gateway": (
            "cluster:\n"
            "  shared_cache: shared-redis.platform.svc.cluster.local:6379   # → Cluster Services\n"
        ),
    }
    for team in TEAMS:
        proj, app = layout.get(team, ("platform", team.lower()))
        repo = os.path.join(GITSRV, "DevOps", "Control", "_git", team)
        _init_repo(repo)
        for env in ("dev", "qc", "prd"):
            # api also carries an <image>-<environment> reference (per env) so
            # the web-<env> service form resolves to the portal/web app.
            _envform = (f"envform:\n  web_env: http://web-{env}:8080   "
                        f"# <image>-<environment> service form\n"
                        if app == "api" else "")
            _write(repo, f"{proj}/{env}_{app}/config.yml",
                   f"# {proj}/{env}_{app} — service config\n"
                   f"service:\n"
                   f"  name: {app}\n"
                   f"  port: 8080\n"
                   f"database:\n"
                   f"  url: postgresql://{proj}-db.acme.local:5432/{proj}\n"
                   + _refs.get(app, "") + _envform)
        # ── Env-specific apps (exist in only ONE environment) so the
        #    "compare only common apps" mode has something to exclude. ──
        if team == "DEVJAVA":
            _write(repo, f"{proj}/dev_canary/config.yml",
                   "service:\n  name: canary\n  port: 8080\n"
                   "siblings:\n  api_url: http://api-service:8080\n")   # dev only
            _write(repo, f"{proj}/qc_sandbox/config.yml",
                   "service:\n  name: sandbox\n  port: 8080\n"
                   "siblings:\n  api_url: http://api-service:8080\n")   # qc only
        # ── Backup folders that MUST be ignored (end with _bkp) ──
        if team == "DEVJAVA":
            # env_app-level backup folder
            _write(repo, f"{proj}/prd_{app}_bkp/config.yml",
                   "service:\n  name: api-bkp\n"
                   "external:\n  bad_url: http://bkp-should-not-appear.acme.local:1234\n")
            # project-level backup folder
            _write(repo, f"{proj}_bkp/prd_{app}/config.yml",
                   "service:\n  name: api-oldproj\n"
                   "external:\n  bad_url: http://alsobad-bkp.acme.local:1234\n")
        # Base commit is BACKDATED 30 days so the (1–4 day-old) deployments in
        # ef-cicd-deployments land AFTER it → these configs read as "deployed".
        _commit_all_dated(repo, f"seed control config for {team}", days_ago=30)

    # A RECENT, un-deployed change to payments/api (all envs) → HEAD is newer
    # than the last successful deploy, so the tab shows the deployed revision
    # and flags "repo ahead (undeployed change)".
    _devjava = os.path.join(GITSRV, "DevOps", "Control", "_git", "DEVJAVA")
    for env in ("dev", "qc", "prd"):
        _write(_devjava, f"payments/{env}_api/config.yml",
               "# payments/{0}_api — service config (edited, NOT yet deployed)\n"
               "service:\n  name: api\n  port: 8080\n"
               "database:\n  url: postgresql://payments-db.acme.local:5432/payments\n"
               "siblings:\n"
               "  checkout_url: http://checkout-service:8080/api\n"
               "  worker_grpc: grpc://worker-service:9090\n"
               "newfeature:\n"
               "  flags_api: http://feature-flags.acme.local:8080   # NEW undeployed dep\n"
               .format(env))
    _commit_all_dated(_devjava, "add feature-flags dependency to api (undeployed)",
                      days_ago=0, author="Alice Dev <alice.dev@acme.local>")


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
