"""Parse the cloned `inventories` Ansible repo into a per-project config model.

Layout (per project directory):
  <project>/<app>.yml                      app definitions
  <project>/group_vars/all/*.yml           project-wide vars (dev_team/qc_team/ops_team, …)
  <project>/group_vars/<app>               app group vars
  <project>/group_vars/<env>_<app>         per-env app group vars
  <project>/host_vars/<host>/vars.yml      host vars (plaintext)
  <project>/host_vars/<host>/vault.yml     host secrets (ansible-vault — NOT decrypted)

Team ownership (dev_team/qc_team/ops_team, and any other <role>_team) lives in
the plaintext group_vars/all, so we read that directly. Vault files are detected
and counted only — secrets are never decrypted into the UI."""

import re
import time

import yaml

from ..config import settings

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 300

PRIMARY_ROLES = ("dev", "qc", "ops")   # dev_team / qc_team / ops_team
_TEAM_RE = re.compile(r"^([A-Za-z][\w-]*)_team$")
# directories at the repo root that are not projects
_SKIP_DIRS = {".git", ".github", "group_vars", "host_vars", "roles", "playbooks",
              "inventory", "inventories", "docs", ".vault", "molecule"}


# ------------------------------------------------------------- YAML loading
class _Loader(yaml.SafeLoader):
    pass


def _ignore_tag(loader, tag_suffix, node):  # !vault, !unsafe, custom tags -> None
    return None


_Loader.add_multi_constructor("", _ignore_tag)
_Loader.add_multi_constructor("!", _ignore_tag)


def _load_yaml(text: str) -> dict:
    """Top-level dict from a (possibly jinja/vault-laden) Ansible YAML file.
    Falls back to a flat `key: value` line parse when strict YAML can't cope
    (unquoted jinja `{{ }}`, tabs, etc.) — enough for the team/var extraction."""
    try:
        d = yaml.load(text, Loader=_Loader)
        if isinstance(d, dict):
            return d
    except yaml.YAMLError:
        pass
    out: dict = {}
    for line in text.splitlines():
        if not line or line[0] in " #\t-":
            continue
        m = re.match(r"^([A-Za-z_][\w.-]*)\s*:\s*(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if v.startswith(("|", ">", "!")):
                v = "<multiline>"
            out[k] = v.strip("\"'")
    return out


def _is_vault(text: str) -> bool:
    return text.lstrip().startswith("$ANSIBLE_VAULT")


def _stringify(v) -> str:
    if isinstance(v, (dict, list)):
        return f"<{type(v).__name__}:{len(v)}>"
    return str(v)


# ------------------------------------------------------------- repo location
def _inventory_dir():
    from . import repos
    try:
        inv = next((r for r in repos.configured()
                    if (r.get("name") or "").lower() == "inventories"), None)
        if not inv:
            return None
        d = repos._dir_for(inv)
        return d if d.exists() else None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------- parse one project
def _parse_project(pdir) -> dict:
    name = pdir.name
    apps = sorted(f.stem for f in pdir.glob("*.yml") if f.is_file())
    app_set = {a.lower() for a in apps}

    # group_vars/all -> project-wide vars (teams live here)
    all_vars: dict = {}
    for gv_all in (pdir / "group_vars" / "all", ):
        if gv_all.is_dir():
            for f in sorted(gv_all.glob("*.yml")):
                try:
                    all_vars.update(_load_yaml(f.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
    all_yml = pdir / "group_vars" / "all.yml"
    if all_yml.is_file():
        try:
            all_vars.update(_load_yaml(all_yml.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            pass

    teams: dict = {}          # role -> team, primary roles (dev/qc/ops)
    other_teams: dict = {}    # any other <role>_team
    other_vars: dict = {}     # everything else in group_vars/all
    for k, v in all_vars.items():
        m = _TEAM_RE.match(k)
        if m:
            role = m.group(1).lower()
            (teams if role in PRIMARY_ROLES else other_teams)[role] = _stringify(v)
        else:
            other_vars[k] = _stringify(v)

    # group_vars/<app> and group_vars/<env>_<app> -> envs + per-app config presence
    envs: set = set()
    app_group_vars: set = set()
    gv = pdir / "group_vars"
    if gv.is_dir():
        for f in gv.iterdir():
            base = f.name[:-4] if f.name.endswith(".yml") else f.name
            if base in ("all",):
                continue
            low = base.lower()
            if low in app_set:
                app_group_vars.add(base)
                continue
            # <env>_<app>
            for a in apps:
                if low.endswith("_" + a.lower()) and len(low) > len(a) + 1:
                    envs.add(base[: -(len(a) + 1)])
                    app_group_vars.add(base)
                    break

    # host_vars/<host>/{vars.yml, vault.yml}
    hosts: list = []
    vault_files = 0
    hv = pdir / "host_vars"
    if hv.is_dir():
        for hdir in sorted(hv.iterdir()):
            if not hdir.is_dir():
                continue
            has_vars = (hdir / "vars.yml").is_file()
            vaulted = False
            vpath = hdir / "vault.yml"
            if vpath.is_file():
                try:
                    vaulted = _is_vault(vpath.read_text(encoding="utf-8", errors="replace")[:64])
                except OSError:
                    vaulted = True
                vault_files += 1
            hosts.append({"host": hdir.name, "vars": has_vars, "vault": vaulted})

    return {
        "name": name, "apps": apps, "app_count": len(apps),
        "teams": teams, "other_teams": other_teams,
        "dev_team": teams.get("dev"), "qc_team": teams.get("qc"), "ops_team": teams.get("ops"),
        "vars": other_vars, "var_count": len(other_vars),
        "envs": sorted(envs), "hosts": hosts, "host_count": len(hosts),
        "vault_files": vault_files,
    }


# ------------------------------------------------------------- public
def parse(force: bool = False) -> dict:
    if not force and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    if settings.demo_mode:
        payload = _demo()
    else:
        d = _inventory_dir()
        if d is None:
            return {"source": "not cloned", "projects": [], "summary": {},
                    "cached": False,
                    "note": "the 'inventories' repo isn't defined/cloned — add & clone it on the Repositories page"}
        projects = []
        for child in sorted(d.iterdir()):
            if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_DIRS:
                continue
            # a project has app ymls or a group_vars/host_vars dir
            if not (any(child.glob("*.yml")) or (child / "group_vars").exists()
                    or (child / "host_vars").exists()):
                continue
            projects.append(_parse_project(child))
        payload = {"source": "live", "projects": projects, "summary": _summary(projects)}
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)


def _summary(projects: list[dict]) -> dict:
    all_teams: dict = {}
    for p in projects:
        for role in ("dev", "qc", "ops"):
            t = (p.get("teams") or {}).get(role)
            if t:
                all_teams.setdefault(t, set()).add(f"{p['name']}:{role}")
        for role, t in (p.get("other_teams") or {}).items():
            if t:
                all_teams.setdefault(t, set()).add(f"{p['name']}:{role}")
    return {
        "projects": len(projects),
        "apps": sum(p["app_count"] for p in projects),
        "hosts": sum(p["host_count"] for p in projects),
        "vault_files": sum(p["vault_files"] for p in projects),
        "distinct_teams": len(all_teams),
        "teams": sorted(({"name": t, "usages": len(u)} for t, u in all_teams.items()),
                        key=lambda x: (-x["usages"], x["name"])),
        "missing_primary": sum(1 for p in projects
                               if not all((p.get("teams") or {}).get(r) for r in PRIMARY_ROLES)),
    }


# ------------------------------------------------------------- demo
def _demo() -> dict:
    projects = [
        {"name": "Platform", "apps": ["payments", "checkout", "notifications"], "app_count": 3,
         "teams": {"dev": "Platform_Devs", "qc": "Platform_QC", "ops": "SRE_Core"},
         "other_teams": {"security": "AppSec"},
         "dev_team": "Platform_Devs", "qc_team": "Platform_QC", "ops_team": "SRE_Core",
         "vars": {"domain": "platform.corp.local", "region": "eu-west",
                  "log_level": "info", "vault_addr": "<vault-encrypted>"},
         "var_count": 4, "envs": ["dev", "qa", "prod"],
         "hosts": [{"host": "plat-app-01", "vars": True, "vault": True},
                   {"host": "plat-app-02", "vars": True, "vault": True}],
         "host_count": 2, "vault_files": 2},
        {"name": "Control", "apps": ["team-configs"], "app_count": 1,
         "teams": {"dev": "Control_Owners", "qc": "Platform_QC"},   # ops_team missing
         "other_teams": {},
         "dev_team": "Control_Owners", "qc_team": "Platform_QC", "ops_team": None,
         "vars": {"domain": "control.corp.local", "region": "eu-west"},
         "var_count": 2, "envs": ["dev", "prod"],
         "hosts": [{"host": "ctl-01", "vars": True, "vault": False}],
         "host_count": 1, "vault_files": 0},
        {"name": "Research", "apps": ["prototypes"], "app_count": 1,
         "teams": {"dev": "Research_Team", "qc": "Research_Team", "ops": "SRE_Core"},
         "other_teams": {"data": "DataEng"},
         "dev_team": "Research_Team", "qc_team": "Research_Team", "ops_team": "SRE_Core",
         "vars": {"domain": "research.corp.local", "experimental": "true"},
         "var_count": 2, "envs": ["dev"],
         "hosts": [{"host": "res-lab-01", "vars": True, "vault": True}],
         "host_count": 1, "vault_files": 1},
    ]
    return {"source": "demo", "projects": projects, "summary": _summary(projects)}
