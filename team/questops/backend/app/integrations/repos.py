"""Per-user git workspaces for the Repositories page.

Repositories are DEFINED FROM THE UI (rows in the `repositories` table);
config carries only the shared ADO instance credentials. The clone under
REPOS_WORKDIR is the SERVER COPY (nobody edits it); every logged-in member
gets their own git worktree next to it ({id}-{name}.wt/{username}) so edits
never overlap. Edits stay LOCAL — nothing is ever pushed from this page.
Credentials are used only for browse/clone/fetch and are never written
into .git/config."""

import base64
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests
from sqlalchemy.orm import Session

from ..config import settings

MAX_FILE_BYTES = 512 * 1024


class RepoError(Exception):
    pass


def _workdir() -> Path:
    p = Path(settings.repos_workdir).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


DEMO_REPO_FILES = {
    "payments-service": {
        "README.md": "# payments-service\n\nHandles card and wallet payments.\n",
        "Dockerfile": "FROM eclipse-temurin:21-jre\nCOPY app.jar /app.jar\nENTRYPOINT [\"java\",\"-jar\",\"/app.jar\"]\n",
        "Jenkinsfile": "pipeline {\n  agent { label 'java' }\n  stages {\n    stage('Build') { steps { sh './gradlew build' } }\n    stage('Unit Tests') { steps { sh './gradlew test' } }\n  }\n}\n",
        "src/main.py": "def charge(amount: int) -> bool:\n    return amount > 0\n",
        "requirements.txt": "fastapi==0.110.0\nrequests==2.31.0\n",
        "helm/values.yaml": "replicaCount: 2\nimage:\n  repository: registry.local/payments\n  tag: 1.4.2\n",
    },
    "platform-helm": {
        "README.md": "# platform-helm\n\nShared helm charts for the platform.\n",
        "charts/app/Chart.yaml": "apiVersion: v2\nname: app\nversion: 0.1.0\n",
        "charts/app/values.yaml": "replicaCount: 1\nresources:\n  requests: { cpu: 100m, memory: 128Mi }\n",
        "charts/app/templates/deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Release.Name }}\n",
    },
    "Engine": {
        "README.md": "# Engine\n\nPipelines (groovy), playbooks+roles, and scripts.\n",
        # sourced by the Tools/LDAP scripts (and, in prod, by many others); lives
        # at the repo ROOT so it is reachable as $HOME/.prd when the script runs
        # with HOME pointed here. Sets the LDAP endpoint + bind creds.
        ".prd": "# prod profile — sourced as `. $HOME/.prd`\n"
                "export LDAP_HOST=ldaps://ldap.mycorp.local:636\n"
                "export LDAP_BASE=OU=Groups,DC=mycorp,DC=local\n"
                "export LDAP_BIND_DN=CN=svc-ldap,OU=Service Accounts,DC=mycorp,DC=local\n"
                "export LDAP_BIND_PW=__set_in_real_env__\n",
        # QuestOps runs this for Access Management [TEAM] validation:
        #   getTeamMembersCN.sh <team>  ->  one member per line
        "scripts/Tools/LDAP/getTeamMembersCN.sh":
            "#!/bin/bash\n# Print the members of an LDAP team group, one per line.\n"
            "set -euo pipefail\n"
            ". $HOME/.prd   # LDAP_HOST / LDAP_BASE / LDAP_BIND_DN / LDAP_BIND_PW\n"
            "TEAM=\"${1:?usage: getTeamMembersCN.sh <team>}\"\n"
            "ldapsearch -x -LLL -H \"$LDAP_HOST\" -D \"$LDAP_BIND_DN\" -w \"$LDAP_BIND_PW\" \\\n"
            "  -b \"$LDAP_BASE\" \"(cn=$TEAM)\" member \\\n"
            "  | sed -n 's/^member: CN=\\([^,]*\\).*/\\1/p'\n",
        # real caller conventions:
        # podman_run_script.sh   <env> <category> <script> <container> <args>
        # podman_run_playbook.sh <env> <inventory> <playbook> <path> <container> <args>
        "pipelines/payments-service.groovy":
            "pipeline {\n  agent { label 'java' }\n  stages {\n"
            "    stage('Build') { steps { sh './scripts/podman_run_script.sh prd java build_java.sh tools-java \"-Pservice=payments\"' } }\n"
            "    stage('Unit Tests') { steps { sh './gradlew test' } }  "
            "// testcontainers: needs the docker daemon\n"
            "    stage('Deploy') { steps { sh './scripts/podman_run_playbook.sh prd prod_inventory deploy_app.yml deploy tools-ansible \"-e app=payments\"' } }\n"
            "  }\n}\n",
        "pipelines/checkout-service.groovy":
            "pipeline {\n  agent { label 'java' }\n  stages {\n"
            "    stage('Build') { steps { sh './scripts/podman_run_script.sh prd java build_java.sh tools-java \"-Pservice=checkout\"' } }\n"
            "  }\n}\n",
        # arbitrary name, no extension, not wired to Jenkins — and calling
        # with env=dev, which must raise a flag
        "pipelines/nightly/db-maintenance":
            "pipeline {\n  agent any\n  stages {\n"
            "    stage('Vacuum') { steps { sh './scripts/podman_run_script.sh dev java build_java.sh tools-java' } }\n  }\n}\n",
        # scripts — incl. the standard callers and one orphan
        "scripts/podman_run_script.sh":
            "#!/bin/bash\n# standard caller: runs a script inside the tool container\n"
            "exec podman run --rm -v \"$PWD:/w\" tools bash \"/w/scripts/$1\" \"${@:2}\"\n",
        "scripts/podman_run_playbook.sh":
            "#!/bin/bash\n# standard caller: runs a playbook inside the tool container\n"
            "exec podman run --rm -v \"$PWD:/w\" tools ansible-playbook \"/w/playbooks/$1\"\n",
        "scripts/java/build_java.sh":
            "#!/bin/bash\nsource scripts/common/setup_env.sh\n./gradlew build \"$@\"\n",
        "scripts/common/setup_env.sh":
            "#!/bin/bash\nexport JAVA_HOME=/opt/java\nexport GRADLE_OPTS=-Xmx2g\n",
        "scripts/orphan_cleanup.sh":
            "#!/bin/bash\n# nothing references this script\nrm -rf /tmp/old-workspaces\n",
        "scripts/report.py":
            "#!/usr/bin/env python3\n# nothing references this either\nprint('report')\n",
        # playbooks — group 'deploy' with roles next to them, plus a legacy orphan
        "playbooks/deploy/deploy_app.yml":
            "---\n- hosts: app\n  roles:\n    - app_deploy\n"
            "- import_playbook: restart_services.yml\n",
        "playbooks/deploy/restart_services.yml":
            "---\n- hosts: app\n  tasks:\n    - name: warm env\n"
            "      shell: scripts/common/setup_env.sh && systemctl restart app\n",
        "playbooks/deploy/roles/app_deploy/tasks/main.yml":
            "---\n- include_role:\n    name: common_checks\n"
            "- include_tasks: deploy_steps.yml\n",
        "playbooks/deploy/roles/app_deploy/tasks/deploy_steps.yml":
            "---\n- name: deploy artifact\n  copy: src=app.jar dest=/opt/app\n",
        "playbooks/deploy/roles/app_deploy/tasks/old_steps.yml":
            "---\n- name: legacy steps nobody includes\n  debug: msg=old\n",
        "playbooks/deploy/roles/common_checks/tasks/main.yml":
            "---\n- name: check disk\n  shell: df -h\n",
        "playbooks/deploy/roles/unused_role/tasks/main.yml":
            "---\n- name: never referenced\n  debug: msg=unused\n",
        "playbooks/legacy/old_migration.yml":
            "---\n- hosts: db\n  tasks:\n    - shell: echo legacy migration\n",
        # ansible-vault password used by the inventories vault.yml files — a
        # single plaintext line (the password, nothing else)
        ".vault_pass.txt": "demo-vault-password\n",
    },
    # inventories: per-project <app>.yml ansible inventories (env groups + hosts),
    # group_vars/all (teams: dev_team/qc_team/prd_team) + host_vars
    "inventories": {
        "README.md": "# inventories\n\nAnsible inventories: apps, group_vars, host_vars per project.\n",
        # <app>.yml is an ansible inventory: all.children.<app>.children.<env>_<app>.hosts
        "Platform/payments.yml":
            "all:\n  children:\n    payments:\n      children:\n"
            "        dev_payments:\n          hosts:\n            dev_ocp:\n"
            "        qc_payments:\n          hosts:\n            qc_ocp:\n"
            "        uat_payments:\n          hosts:\n            uat_ocp:\n"
            "        prd_payments:\n          hosts:\n            prd_ocp:\n            prd_dr_ocp:\n",
        "Platform/checkout.yml":
            "all:\n  children:\n    checkout:\n      children:\n"
            "        dev_checkout:\n          hosts:\n            dev_ocp:\n"
            "        prd_checkout:\n          hosts:\n            prd_ocp:\n",
        "Platform/group_vars/all/teams.yml":
            "---\ndev_team: Platform_Devs\nqc_team: Platform_QC\nprd_team: SRE_Core\n"
            "uat_team: Platform_UAT\nsecurity_team: AppSec\n",
        "Platform/group_vars/all/common.yml":
            "---\ndomain: platform.corp.local\nregion: eu-west\nlog_level: info\n",
        # app-level group_vars carry repository_name — ties the app's pipeline to its ADO repo
        "Platform/group_vars/payments/main.yml": "---\nrepository_name: payments-svc\n",
        "Platform/group_vars/checkout/main.yml": "---\nrepository_name: checkout-svc\n",
        "Platform/group_vars/prd_payments/vars.yml": "---\nreplicas: 6\n",
        "Platform/host_vars/prd_ocp/vars.yml": "---\nansible_host: 10.0.0.11\n",
        "Platform/host_vars/prd_ocp/vault.yml":
            "$ANSIBLE_VAULT;1.1;AES256\n6162636465666768696a6b6c6d6e6f70\n",
        "Control/team-configs.yml":
            "all:\n  children:\n    team-configs:\n      children:\n"
            "        dev_team-configs:\n          hosts:\n            dev_ocp:\n"
            "        prd_team-configs:\n          hosts:\n            prd_ocp:\n",
        "Control/group_vars/all/teams.yml":
            "---\ndev_team: Control_Owners\nqc_team: Platform_QC\n",  # prd_team (ops) missing
        "Control/group_vars/team-configs/main.yml": "---\nrepository_name: team-configs\n",
        "Research/prototypes.yml":
            "all:\n  children:\n    prototypes:\n      children:\n"
            "        dev_prototypes:\n          hosts:\n            dev_ocp:\n",
        "Research/group_vars/all/teams.yml":
            "---\ndev_team: Research_Team\nqc_team: Research_Team\nprd_team: SRE_Core\n"
            "data_team: DataEng\n",
    },
}


DEMO_DISCOVERABLE = ["Engine", "UI", "inventories", "ocp-templates",
                     "payments-service", "platform-helm", "checkout-service",
                     "notifications-service"]


def _ado_location(url: str) -> tuple[str, str]:
    """Derive (collection, project) from an ADO git URL by parsing the path
    around the `_git` segment: …/<collection>/<project>/_git/<repo>. Falls back
    to ('', '') when the URL doesn't follow the pattern (grouped as ungrouped)."""
    from urllib.parse import unquote, urlparse
    try:
        parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    except ValueError:
        return "", ""
    if "_git" in parts:
        i = parts.index("_git")
        project = parts[i - 1] if i >= 1 else ""
        collection = parts[i - 2] if i >= 2 else ""
        return collection, project
    # no _git segment (plain git host): last part is the repo, the container
    # before it is the project/group; a further parent (if any) is the collection
    if len(parts) >= 2:
        project = parts[-2]
        collection = parts[-3] if len(parts) >= 3 else ""
        return collection, project
    return "", ""


def configured() -> list[dict]:
    """The UI-defined repositories; every one clones with the ADO creds."""
    from ..db import Repository, SessionLocal
    db = SessionLocal()
    try:
        rows = db.query(Repository).order_by(Repository.id).all()
    finally:
        db.close()
    out = []
    for r in rows:
        coll, proj = _ado_location(r.url)
        out.append({"slot": r.id, "name": r.name, "url": r.url, "added_by": r.added_by,
                    "collection": coll, "project": proj,
                    "user": settings.ado_user, "password": settings.ado_git_password})
    return out


def add_repo(db: Session, url: str, name: str, username: str) -> dict:
    from ..db import Repository
    url = url.strip()
    name = name.strip()
    if not re.match(r"^https?://\S+$", url):
        raise RepoError("repository URL must be http(s)")
    if not name:
        raise RepoError("repository name is required "
                        "(e.g. inventories, Engine, UI, ocp-templates)")
    if db.query(Repository).filter(Repository.url == url).first():
        raise RepoError("this repository is already defined")
    if db.query(Repository).filter(Repository.name.ilike(name)).first():
        raise RepoError(f"a repository named '{name}' is already defined")
    row = Repository(name=name, url=url, added_by=username)
    db.add(row)
    db.commit()
    return {"slot": row.id, "name": row.name, "url": row.url}


def remove_repo(db: Session, slot: int) -> None:
    from ..db import Repository
    row = db.get(Repository, slot)
    if row is None:
        raise RepoError("repository not found")
    base = _workdir() / f"{row.id:02d}-{row.name}"
    worktrees = _workdir() / f"{row.id:02d}-{row.name}.wt"
    db.delete(row)
    db.commit()
    shutil.rmtree(worktrees, ignore_errors=True)  # members' workspaces too
    shutil.rmtree(base, ignore_errors=True)


def discover(collection: str = "") -> dict:
    """Browse the ADO instance for repositories to add. ADO_URL is the
    INSTANCE root now, so we enumerate collections; an optional `collection`
    filter narrows the browse (and the query — sparing the instance)."""
    from . import ado as _ado
    if settings.demo_mode:
        colls = ["DefaultCollection", "Research"]
        rows = [{"name": n, "collection": "DefaultCollection", "project": "Platform",
                 "url": f"https://ado.demo/DefaultCollection/Platform/_git/{n}"}
                for n in DEMO_DISCOVERABLE]
        rows += [{"name": "prototypes", "collection": "Research", "project": "Sandbox",
                  "url": "https://ado.demo/Research/Sandbox/_git/prototypes"}]
        if collection:
            rows = [r for r in rows if r["collection"] == collection]
        return {"collections": colls, "repos": rows}
    if not settings.ado_url:
        raise RepoError("ADO_URL is not configured (set it to your ADO INSTANCE root)")
    try:
        all_colls = _ado.collections()
    except requests.RequestException as exc:
        raise RepoError(f"ADO browse failed listing collections: {_scrub(str(exc))[:200]}")
    target_colls = [collection] if collection else all_colls

    def browse(coll: str) -> list[dict]:
        prefix = f"/{coll}" if coll else ""
        try:
            r = requests.get(f"{_ado.instance()}{prefix}/_apis/git/repositories",
                             params={"api-version": "6.0"},
                             auth=(settings.ado_user, settings.ado_rest_password),
                             timeout=20)
        except requests.RequestException:
            return []
        if r.status_code in (203, 302, 401, 403):
            raise RepoError(f"ADO browse failed: HTTP {r.status_code} — authentication "
                            "rejected. The browse uses ADO_PAT (falling back to "
                            "ADO_PASSWORD) — has the PAT expired or been rotated?")
        if not r.ok:
            return []
        try:
            items = r.json().get("value", [])
        except ValueError:
            raise RepoError("ADO browse failed: ADO answered with a non-JSON page — "
                            "usually an auth redirect (expired PAT?) or ADO_URL not "
                            "pointing at the INSTANCE root")
        return [{"name": i.get("name", ""), "collection": coll,
                 "project": (i.get("project") or {}).get("name", ""),
                 "url": i.get("remoteUrl") or i.get("webUrl", "")}
                for i in items if not i.get("isDisabled")]

    rows = [r for coll in target_colls for r in browse(coll)]
    rows.sort(key=lambda x: (x["collection"].lower(), x["project"].lower(),
                             x["name"].lower()))
    return {"collections": all_colls, "repos": rows}


def _repo_by_slot(slot: int) -> dict:
    for r in configured():
        if r["slot"] == slot:
            return r
    raise RepoError(f"repo slot {slot} is not configured")


def _dir_for(repo: dict) -> Path:
    """The server copy — the plain clone nobody edits directly."""
    return _workdir() / f"{repo['slot']:02d}-{repo['name']}"


def _safe_user(username: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", username or "").strip("._-") or "user"


def _worktree_root(repo: dict) -> Path:
    return _workdir() / f"{repo['slot']:02d}-{repo['name']}.wt"


def _ensure_worktree(repo: dict, username: str) -> Path:
    """Each member works in their own detached worktree (shared objects,
    isolated files) so teammates never step on each other's edits."""
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    wt = _worktree_root(repo) / _safe_user(username)
    if not wt.exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        _git(base, "worktree", "add", "--detach", str(wt), "HEAD")
    return wt


def _workspace(repo: dict, username: str | None) -> Path:
    """username=None -> the server copy (read-only callers like the Failure
    Dive); a username -> that member's own worktree (created on demand)."""
    if username:
        return _ensure_worktree(repo, username)
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    return base


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _scrub(text: str) -> str:
    """Credentials must never reach the UI: mask every configured secret
    (raw, percent-encoded and base64 header forms) and strip any url
    userinfo (user:pass@host) git may echo back."""
    secrets = [settings.ado_password, settings.ado_pat]
    if settings.ado_pat:
        secrets.append(_b64(":" + settings.ado_pat))
    if settings.ado_password:
        secrets.append(_b64(f"{settings.ado_user}:{settings.ado_password}"))
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
            text = text.replace(quote(secret, safe=""), "***")
    return re.sub(r"(https?://)[^/@\s]+@", r"\1", text)


# never let git prompt for credentials: there is no terminal in the container,
# so prompting surfaces as "could not read Username ... No such device or
# address". With prompts off, git fails fast with a message we can hint on.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}

_AUTH_HINT = (" — git asked for credentials: check ADO_USER / ADO_PASSWORD in "
              "config (compose: QO_ADO_USER / QO_ADO_PASSWORD). git cloning "
              "uses the PASSWORD; the PAT (ADO_PAT) is for the ADO REST browse. "
              "QuestOps injects them into http(s) URLs per command")


def _with_hint(msg: str) -> str:
    if ("could not read Username" in msg or "could not read Password" in msg
            or "Authentication failed" in msg or "terminal prompts disabled" in msg):
        msg += _AUTH_HINT
    return msg


def _git(repo_dir: Path, *args: str, ok_fail: bool = False) -> str:
    p = subprocess.run(["git", *args], cwd=repo_dir, env=_GIT_ENV,
                       capture_output=True, text=True, timeout=120)
    if p.returncode != 0 and not ok_fail:
        raise RepoError(_with_hint(_scrub((p.stderr or p.stdout).strip())[:400]))
    return p.stdout


def _inject(url: str, user: str, password: str) -> str:
    """Put credentials into the URL for one command. Handles http and https
    (on-prem ADO is often plain http) and URLs that already embed a username
    (ADO's remoteUrl usually does: https://user@host/...)."""
    m = re.match(r"^(https?://)(?:[^/@]+@)?(.+)$", url)
    if not m or not user:
        return url
    scheme, rest = m.groups()
    return f"{scheme}{quote(user, safe='')}:{quote(password or '', safe='')}@{rest}"


# which credential strategy last worked — tried first on subsequent commands
_WORKING = {"label": None}

_AUTHISH = re.compile(r"Authentication failed|could not read (Username|Password)"
                      r"|HTTP.*40[13]|401|403|access denied|terminal prompts disabled",
                      re.IGNORECASE)

_ONPREM_HINTS = (" — on-prem ADO hints: the git endpoint often accepts different "
                 "auth than the REST API. Enable IIS Basic auth or use a PAT for "
                 "git; domain accounts may need ADO_USER=DOMAIN\\user; verify the "
                 "account has Code>Read on the repository")


def _cred_candidates(url: str) -> list[tuple[str, list[str], str]]:
    """(label, extra `git -c` args, url) strategies, most likely first.
    Preemptive Basic headers matter on IIS/NTLM setups that never offer a
    Basic challenge — git would otherwise pick NTLM/Negotiate and fail."""
    user, pw, pat = settings.ado_user, settings.ado_password, settings.ado_pat
    out: list[tuple[str, list[str], str]] = []
    if pw:
        out.append(("password-in-url", [], _inject(url, user, pw)))
    if pat:
        out.append(("pat-in-url", [], _inject(url, user or "pat", pat)))
        out.append(("pat-basic-header",
                    ["-c", f"http.extraHeader=Authorization: Basic {_b64(':' + pat)}"],
                    url))
    if pw and user:
        out.append(("password-basic-header",
                    ["-c", f"http.extraHeader=Authorization: Basic {_b64(f'{user}:{pw}')}"],
                    url))
    if not out:
        out.append(("no-credentials", [], url))
    if _WORKING["label"]:
        out.sort(key=lambda c: c[0] != _WORKING["label"])
    return out


def _git_authed(cwd: Path, repo: dict, *args_template: str, timeout: int = 300) -> str:
    """Run a git command that talks to the remote, trying every credential
    strategy; '{URL}' in args is replaced per attempt. Non-auth failures
    raise immediately; auth failures accumulate into one actionable error."""
    attempts = []
    for label, extra, url in _cred_candidates(repo["url"]):
        argv = [a.replace("{URL}", url) for a in args_template]
        p = subprocess.run(["git", *extra, *argv], cwd=cwd, env=_GIT_ENV,
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode == 0:
            _WORKING["label"] = label
            return p.stdout
        msg = _scrub((p.stderr or p.stdout).strip())
        attempts.append(f"[{label}] {(msg.splitlines()[-1] if msg else 'failed')[:140]}")
        if not _AUTHISH.search(msg):
            raise RepoError(_with_hint(msg[:400]))
    raise RepoError("git rejected every configured credential: "
                    + " · ".join(attempts) + _ONPREM_HINTS)


def _safe(repo_dir: Path, rel: str) -> Path:
    target = (repo_dir / rel).resolve() if rel else repo_dir.resolve()
    root = repo_dir.resolve()
    if target != root and root not in target.parents:
        raise RepoError("path escapes the repository")
    if target != root and ".git" in target.relative_to(root).parts:
        raise RepoError(".git is off limits")
    return target


# ---------------------------------------------------------------- lifecycle
def _seed_demo_repo(repo: dict, repo_dir: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    files = DEMO_REPO_FILES.get(repo["name"]) or {
        "README.md": f"# {repo['name']}\n\nDemo repository added from the UI.\n",
        "src/app.py": "def main():\n    print('hello')\n",
    }
    for rel, content in files.items():
        f = repo_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    ident = ["-c", "user.email=demo@questops", "-c", "user.name=QuestOps Demo"]
    try:  # a real git history makes status/diff work in the demo too
        _git(repo_dir, "init")
        _git(repo_dir, "add", "-A")
        _git(repo_dir, *ident, "commit", "-m", "seed demo repository")
        # a diverging feature branch so the branch/compare view has a real delta
        default = _git(repo_dir, "rev-parse", "--abbrev-ref", "HEAD", ok_fail=True).strip() or "master"
        _git(repo_dir, "checkout", "-q", "-b", "feature/demo")
        (repo_dir / "CHANGES.md").write_text(
            "# Changes\n\n- add demo feature scaffolding\n- tweak configuration\n")
        readme = repo_dir / "README.md"
        if readme.exists():
            readme.write_text(readme.read_text() + "\n## Feature work\n\nOn the feature/demo branch.\n")
        _git(repo_dir, "add", "-A")
        _git(repo_dir, *ident, "commit", "-m", "feature/demo: scaffolding + README note")
        (repo_dir / "CHANGES.md").write_text(
            "# Changes\n\n- add demo feature scaffolding\n- tweak configuration\n- second pass\n")
        _git(repo_dir, "add", "-A")
        _git(repo_dir, *ident, "commit", "-m", "feature/demo: second pass")
        _git(repo_dir, "checkout", "-q", default)   # leave the default branch checked out
    except (RepoError, FileNotFoundError):
        pass  # git missing: page still works, just without status/diff


def clone(slot: int, branch: str = "") -> None:
    branch = (branch or "").strip()
    if branch and (branch.startswith("-")
                   or not re.fullmatch(r"[\w\-./]{1,200}", branch)):
        raise RepoError(f"invalid branch name: '{branch}'")
    repo = _repo_by_slot(slot)
    repo_dir = _dir_for(repo)
    if repo_dir.exists():
        raise RepoError("already cloned — use pull to update")
    if settings.demo_mode:
        _seed_demo_repo(repo, repo_dir)
        if branch:  # demo parity: show the requested branch in the UI
            _git(repo_dir, "checkout", "-b", branch, ok_fail=True)
        return
    branch_args = ["--branch", branch] if branch else []
    attempts = []
    for label, extra, url in _cred_candidates(repo["url"]):
        p = subprocess.run(["git", *extra, "clone", *branch_args, url, str(repo_dir)],
                           env=_GIT_ENV, capture_output=True, text=True, timeout=600)
        if p.returncode == 0:
            _WORKING["label"] = label
            _git(repo_dir, "remote", "set-url", "origin", repo["url"])  # keep creds out
            return
        shutil.rmtree(repo_dir, ignore_errors=True)  # clean slate per attempt
        msg = _scrub((p.stderr or p.stdout).strip())
        attempts.append(f"[{label}] {(msg.splitlines()[-1] if msg else 'failed')[:140]}")
        if not _AUTHISH.search(msg):
            raise RepoError(_with_hint(msg[:400]))
    raise RepoError("git rejected every configured credential: "
                    + " · ".join(attempts) + _ONPREM_HINTS)


def clone_project(collection: str, project: str, branch: str = "") -> dict:
    """Clone every UI-defined repo of one ADO project that isn't cloned yet —
    SEQUENTIALLY (one at a time), so a bulk action never fans out concurrent
    clones at the ADO instance. Returns a per-repo outcome summary."""
    coll = (collection or "").strip()
    proj = (project or "").strip()
    matches = [r for r in configured()
               if r.get("collection", "") == coll and r.get("project", "") == proj]
    if not matches:
        raise RepoError("no defined repositories match that project")
    cloned, skipped, errors = [], [], []
    for repo in matches:
        if _dir_for(repo).exists():
            skipped.append(repo["name"])
            continue
        try:
            clone(repo["slot"], branch)          # sequential = naturally throttled
            cloned.append(repo["name"])
        except RepoError as exc:
            errors.append({"name": repo["name"], "error": str(exc)[:200]})
    return {"collection": coll, "project": proj,
            "requested": len(matches), "cloned": cloned, "skipped": skipped,
            "errors": errors, "cloned_count": len(cloned),
            "skipped_count": len(skipped), "error_count": len(errors)}


def pull(slot: int, username: str | None = None) -> str:
    """Update the server copy from origin, then fast-forward the member's
    worktree to it (git refuses if their local edits would be clobbered)."""
    repo = _repo_by_slot(slot)
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    out = ""
    if not settings.demo_mode:
        # pull the CHECKED-OUT branch explicitly — a bare `git pull <url>`
        # pulls the remote's default branch, wrong for branch-pinned clones
        current = _git(base, "rev-parse", "--abbrev-ref", "HEAD", ok_fail=True).strip()
        branch_arg = [current] if current and current != "HEAD" else []
        out = _scrub(_git_authed(base, repo, "pull", "--ff-only", "{URL}",
                                 *branch_arg)).strip()
    if username:
        wt = _ensure_worktree(repo, username)
        base_head = _git(base, "rev-parse", "HEAD").strip()
        wt_head = _git(wt, "rev-parse", "HEAD").strip()
        if wt_head != base_head:
            try:
                _git(wt, "checkout", "--detach", base_head)
                out += f"\nyour workspace moved to {base_head[:8]}"
            except RepoError as exc:
                raise RepoError("server copy updated, but your workspace has "
                                f"local edits that conflict: {exc}")
        else:
            out += "\nyour workspace is already at the server copy"
    return out.strip() or "up to date"


def discard(slot: int, username: str | None = None) -> None:
    """Throw away the member's local edits (checkout + clean in THEIR worktree)."""
    wt = _workspace(_repo_by_slot(slot), username)
    _git(wt, "checkout", "--", ".", ok_fail=True)
    _git(wt, "clean", "-fd", ok_fail=True)


_FETCH_AT: dict[int, float] = {}
_FETCH_TTL = 90  # seconds between real fetches per repo


def remote_status(slot: int, username: str | None = None) -> dict:
    """What changed on the server: throttled fetch + behind counts + the
    incoming commits. Cheap enough for the page to poll."""
    repo = _repo_by_slot(slot)
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    fetch_error = None
    if not settings.demo_mode and time.time() - _FETCH_AT.get(slot, 0) > _FETCH_TTL:
        try:  # creds only on the command line; origin's config stays cred-free
            _git_authed(base, repo, "fetch", "{URL}",
                        "+refs/heads/*:refs/remotes/origin/*")
            _FETCH_AT[slot] = time.time()
        except RepoError as exc:
            fetch_error = _scrub(str(exc))[:200]
    branch = _git(base, "rev-parse", "--abbrev-ref", "HEAD", ok_fail=True).strip()
    upstream = f"origin/{branch}" if branch and branch != "HEAD" else ""
    behind, incoming = 0, []
    if upstream and _git(base, "rev-parse", "--verify", upstream, ok_fail=True).strip():
        behind = int(_git(base, "rev-list", "--count", f"HEAD..{upstream}",
                          ok_fail=True).strip() or 0)
        if behind:
            raw = _git(base, "log", "--format=%h\x1f%an\x1f%ct\x1f%s", "-10",
                       f"HEAD..{upstream}", ok_fail=True)
            for line in raw.splitlines():
                p = line.split("\x1f", 3)
                if len(p) == 4:
                    incoming.append({"short": p[0], "author": p[1],
                                     "at": int(p[2]), "subject": p[3]})
    wt_pending = 0
    if username:
        wt = _worktree_root(repo) / _safe_user(username)
        if wt.exists():
            base_head = _git(base, "rev-parse", "HEAD").strip()
            wt_pending = int(_git(wt, "rev-list", "--count",
                                  f"HEAD..{base_head}", ok_fail=True).strip() or 0)
    return {"branch": branch, "behind": behind, "incoming": incoming,
            "wt_pending": wt_pending, "fetch_error": fetch_error,
            "checked_at": time.time()}


def history(slot: int, username: str | None = None, path: str = "",
            limit: int = 30) -> dict:
    """Commit history (optionally for one path), from the member's workspace."""
    repo = _repo_by_slot(slot)
    d = _workspace(repo, username)
    if path:
        _safe(d, path)
    args = ["log", "--format=%h\x1f%H\x1f%an\x1f%ct\x1f%s", f"-{min(int(limit), 100)}"]
    if path:
        args += ["--", path]
    commits = []
    for line in _git(d, *args, ok_fail=True).splitlines():
        p = line.split("\x1f", 4)
        if len(p) == 5:
            commits.append({"short": p[0], "sha": p[1], "author": p[2],
                            "at": int(p[3]), "subject": p[4]})
    return {"commits": commits, "path": path}


_PATHS_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
               "build", "target", ".terraform", "vendor", ".idea", ".vscode"}


def list_paths(slot: int, username: str | None = None,
               limit: int = 4000) -> list[dict]:
    """Flat file/folder list of the member's workspace — feeds the agent
    chat's '@' path autocomplete."""
    root = _workspace(_repo_by_slot(slot), username)
    out: list[dict] = []
    stack = [root]
    while stack and len(out) < limit:
        d = stack.pop()
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for p in children:
            if len(out) >= limit:
                break
            if p.name in _PATHS_SKIP:  # note: .git is a FILE inside a worktree
                continue
            if p.is_dir():
                out.append({"path": str(p.relative_to(root)), "type": "dir"})
                stack.append(p)
            else:
                out.append({"path": str(p.relative_to(root)), "type": "file"})
    out.sort(key=lambda e: e["path"].lower())
    return out


MAX_DIFF_BYTES = 60_000


def commit_diff(slot: int, sha: str, username: str | None = None) -> str:
    """Full patch for one commit — fetched on demand from the UI."""
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise RepoError("invalid commit id")
    d = _workspace(_repo_by_slot(slot), username)
    out = _git(d, "show", "--stat", "--patch",
               "--format=commit %H%nAuthor: %an <%ae>%nDate:   %ci%n%n    %s%n", sha)
    if len(out) > MAX_DIFF_BYTES:
        out = out[:MAX_DIFF_BYTES] + f"\n… (truncated at {MAX_DIFF_BYTES} chars)"
    return out


# ---------------------------------------------------------------- branches
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def branches(slot: int, username: str | None = None) -> dict:
    """Local + remote-tracking branches of the server copy, newest first, each
    with its tip commit — feeds the branch/compare view."""
    repo = _repo_by_slot(slot)
    base = _dir_for(repo)
    if not base.exists():
        raise RepoError("not cloned yet")
    cur = _git(base, "rev-parse", "--abbrev-ref", "HEAD", ok_fail=True).strip()
    fmt = "%(refname:short)\x1f%(objectname:short)\x1f%(committerdate:unix)\x1f%(committerdate:relative)\x1f%(authorname)\x1f%(contents:subject)"

    def collect(ref_ns: str, remote: bool) -> list[dict]:
        out = _git(base, "for-each-ref", "--sort=-committerdate",
                   f"--format={fmt}", ref_ns, ok_fail=True)
        rows = []
        for line in out.splitlines():
            p = line.split("\x1f", 5)
            if len(p) < 6 or p[0].endswith("/HEAD"):     # skip origin/HEAD pointer
                continue
            rows.append({"name": p[0], "sha": p[1], "at": int(p[2] or 0),
                         "rel": p[3], "author": p[4], "subject": p[5],
                         "remote": remote, "current": p[0] == cur})
        return rows

    local = collect("refs/heads", False)
    remote = collect("refs/remotes", True)
    branches_all = local + remote
    return {"current": cur, "local": local, "remote": remote,
            "branches": branches_all,
            "local_count": len(local), "remote_count": len(remote),
            "count": len(branches_all)}


def branch_delta(slot: int, base_ref: str, compare_ref: str,
                 username: str | None = None) -> dict:
    """Delta between two branches: ahead/behind commit counts, the commits
    unique to `compare` (base..compare), and a per-file added/deleted diffstat
    (symmetric base...compare, like a compare view)."""
    repo = _repo_by_slot(slot)
    d = _dir_for(repo)
    if not d.exists():
        raise RepoError("not cloned yet")
    if not (_REF_RE.match(base_ref or "") and _REF_RE.match(compare_ref or "")):
        raise RepoError("invalid branch name")
    known = {b["name"] for b in branches(slot, username)["branches"]}
    for r in (base_ref, compare_ref):
        if r not in known:
            raise RepoError(f"unknown branch: {r}")

    def count(rng: str) -> int:
        try:
            return int(_git(d, "rev-list", "--count", rng, ok_fail=True).strip() or 0)
        except ValueError:
            return 0

    ahead = count(f"{base_ref}..{compare_ref}")     # commits only on compare
    behind = count(f"{compare_ref}..{base_ref}")    # commits only on base

    commits = []
    for line in _git(d, "log", "--format=%h\x1f%an\x1f%cr\x1f%s",
                     f"{base_ref}..{compare_ref}", "-n", "200", ok_fail=True).splitlines():
        p = line.split("\x1f", 3)
        if len(p) == 4:
            commits.append({"short": p[0], "author": p[1], "rel": p[2], "subject": p[3]})

    files, add_tot, del_tot, bin_ct = [], 0, 0, 0
    for line in _git(d, "diff", "--numstat", f"{base_ref}...{compare_ref}",
                     ok_fail=True).splitlines():
        p = line.split("\t", 2)
        if len(p) != 3:
            continue
        a, dele, path = p
        binary = (a == "-" or dele == "-")
        ai = 0 if binary else int(a or 0)
        di = 0 if binary else int(dele or 0)
        if binary:
            bin_ct += 1
        files.append({"path": path, "added": ai, "deleted": di, "binary": binary,
                      "total": ai + di})
        add_tot += ai
        del_tot += di
    files.sort(key=lambda f: (-f["total"], f["path"].lower()))
    truncated = len(files) > 400
    return {
        "base": base_ref, "compare": compare_ref,
        "ahead": ahead, "behind": behind,
        "commits": commits, "commit_count": ahead,
        "commits_shown": len(commits),
        "files": files[:400], "file_count": len(files),
        "additions": add_tot, "deletions": del_tot, "binary_files": bin_ct,
        "identical": ahead == 0 and behind == 0 and not files,
        "truncated": truncated,
    }


# ---------------------------------------------------------------- inspection
def _dirty_paths(repo_dir: Path) -> list[str]:
    out = _git(repo_dir, "status", "--porcelain", ok_fail=True)
    return [line[3:].strip().strip('"') for line in out.splitlines() if line.strip()]


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{int(f)} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def _dir_size(path: Path) -> int:
    """On-disk size of a repo's server copy (working tree + .git). Uses `du`
    when available (fast, metadata-based), falling back to an os.walk sum."""
    try:
        p = subprocess.run(["du", "-sk", str(path)], capture_output=True,
                           text=True, timeout=20)
        if p.returncode == 0 and p.stdout.split():
            return int(p.stdout.split()[0]) * 1024
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def list_repos(username: str | None = None) -> list[dict]:
    rows = []
    for repo in configured():
        base = _dir_for(repo)
        row = {"slot": repo["slot"], "name": repo["name"], "url": repo["url"],
               "collection": repo.get("collection", ""), "project": repo.get("project", ""),
               "cloned": base.exists(), "branch": "", "last_commit": "",
               "dirty": 0, "size": 0, "size_h": "", "branch_count": 0}
        if row["cloned"]:
            row["branch"] = _git(base, "rev-parse", "--abbrev-ref", "HEAD",
                                 ok_fail=True).strip()
            row["last_commit"] = _git(base, "log", "-1", "--format=%s · %an · %cr",
                                      ok_fail=True).strip()
            row["size"] = _dir_size(base)
            row["size_h"] = _human_size(row["size"])
            row["branch_count"] = len([
                b for b in _git(base, "for-each-ref", "--format=%(refname)",
                                "refs/heads", "refs/remotes", ok_fail=True).splitlines()
                if b.strip() and not b.strip().endswith("/HEAD")])
            if username:  # dirty = THIS member's local edits, in their worktree
                wt = _worktree_root(repo) / _safe_user(username)
                row["dirty"] = len(_dirty_paths(wt)) if wt.exists() else 0
        rows.append(row)
    return rows


def tree(slot: int, rel: str = "", username: str | None = None) -> dict:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    target = _safe(repo_dir, rel)
    if not target.is_dir():
        raise RepoError(f"not a directory: {rel}")
    dirty = set(_dirty_paths(repo_dir))
    entries = []
    for p in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if p.name == ".git":
            continue
        rp = str(p.relative_to(repo_dir))
        entries.append({
            "name": p.name, "path": rp,
            "type": "dir" if p.is_dir() else "file",
            "size": p.stat().st_size if p.is_file() else None,
            "dirty": rp in dirty or (p.is_dir() and any(d.startswith(rp + "/") for d in dirty)),
        })
    return {"path": rel, "entries": entries}


def read_file(slot: int, rel: str, username: str | None = None) -> dict:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    target = _safe(repo_dir, rel)
    if not target.is_file():
        raise RepoError(f"not a file: {rel}")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise RepoError(f"file larger than {MAX_FILE_BYTES // 1024}KB — edit it outside QuestOps")
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise RepoError("binary file — cannot edit here")
    return {"path": rel, "content": content}


def write_file(slot: int, rel: str, content: str,
               username: str | None = None) -> None:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    target = _safe(repo_dir, rel)
    if len(content.encode()) > MAX_FILE_BYTES:
        raise RepoError("content too large")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def diff(slot: int, rel: str = "", username: str | None = None) -> str:
    repo_dir = _workspace(_repo_by_slot(slot), username)
    args = ["diff"] + (["--", rel] if rel else [])
    return _git(repo_dir, *args, ok_fail=True)


# ---------------------------------------------------------------- code search
SEARCH_MIN_LEN = 2
SEARCH_HITS_PER_FILE = 60       # cap lines shown per file
SEARCH_FILES_PER_REPO = 300     # cap files listed per repo
SEARCH_HITS_PER_REPO = 800      # cap total lines scanned into a repo's result
SEARCH_LINE_CHARS = 400         # trim very long matched lines
# `git grep -z -n` emits one record per line: path\0lineno\0content
_SEARCH_LINE = re.compile(r"^(.*?)\x00(\d+)\x00(.*)$")


def _search_one(repo: dict, argv: list[str]) -> dict:
    """Run one `git grep` in a repo's server copy and shape its hits, grouped
    by file. Distinguishes 'no matches' (exit 1) from a real error (exit ≥2)."""
    row = {"slot": repo["slot"], "name": repo["name"], "url": repo["url"],
           "cloned": False, "files": [], "match_count": 0, "file_count": 0,
           "truncated": False, "error": None}
    base = _dir_for(repo)
    if not base.exists():
        return row
    row["cloned"] = True
    try:
        p = subprocess.run(["git", *argv], cwd=base, env=_GIT_ENV,
                           capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        row["error"] = "search timed out"
        return row
    if p.returncode >= 2:                       # 1 == no matches (not an error)
        err = _scrub((p.stderr or p.stdout).strip())
        row["error"] = (err.splitlines()[-1] if err else "search failed")[:160]
        return row

    by_file: dict[str, list] = {}
    hits = 0
    for line in p.stdout.splitlines():
        m = _SEARCH_LINE.match(line)
        if not m:
            continue
        path, lineno, text = m.group(1), int(m.group(2)), m.group(3)
        hits += 1
        if hits > SEARCH_HITS_PER_REPO:
            row["truncated"] = True
            break
        fl = by_file.setdefault(path, [])
        if len(fl) < SEARCH_HITS_PER_FILE:
            fl.append({"line": lineno, "text": text[:SEARCH_LINE_CHARS]})
        row["match_count"] += 1

    row["file_count"] = len(by_file)
    files = [{"path": path, "hits": h, "hit_count": len(h)}
             for path, h in by_file.items()]
    files.sort(key=lambda f: (-f["hit_count"], f["path"].lower()))
    if len(files) > SEARCH_FILES_PER_REPO:
        row["truncated"] = True
        files = files[:SEARCH_FILES_PER_REPO]
    row["files"] = files
    return row


def search(query: str, regex: bool = False, case_sensitive: bool = False,
           whole_word: bool = False, slot: int | None = None,
           path_glob: str = "") -> dict:
    """Search a string/regex across every cloned repo's server copy via
    `git grep` (tracked files, binaries skipped). Read-only; no worktree
    needed since the server copy is always clean."""
    q = (query or "").strip()
    if len(q) < SEARCH_MIN_LEN:
        raise RepoError(f"enter at least {SEARCH_MIN_LEN} characters to search")

    targets = configured()
    if slot is not None:
        targets = [r for r in targets if r["slot"] == slot]

    flags = ["grep", "-I", "--no-color", "-n", "-z", "--full-name"]
    if not case_sensitive:
        flags.append("-i")
    if whole_word:
        flags.append("-w")
    if not regex:
        flags.append("-F")                       # literal string
    else:
        flags.append("-E")                       # extended regex
    argv = [*flags, "-e", q]
    if path_glob.strip():
        # limit to a pathspec (e.g. "*.py"); git treats it as a glob
        argv += ["--", path_glob.strip()]

    started = time.time()
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda r: _search_one(r, argv), targets))

    cloned = [r for r in rows if r["cloned"]]
    return {
        "query": q, "regex": regex, "case_sensitive": case_sensitive,
        "whole_word": whole_word, "slot": slot, "path_glob": path_glob.strip(),
        "repos": rows,
        "repos_searched": len(cloned),
        "repos_not_cloned": len(rows) - len(cloned),
        "total_matches": sum(r["match_count"] for r in rows),
        "total_files": sum(r["file_count"] for r in rows),
        "matched_repos": sum(1 for r in cloned if r["match_count"]),
        "errors": [{"name": r["name"], "error": r["error"]} for r in rows if r["error"]],
        "elapsed_ms": round((time.time() - started) * 1000),
    }
