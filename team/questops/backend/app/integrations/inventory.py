"""Parse the cloned `inventories` Ansible repo into a per-project config model.

Layout (per project directory):
  <project>/<app>.yml                      app inventory (envs/hosts)
  <project>/group_vars/all/*.yml           project-wide vars (dev_team/qc_team/prd_team, …)
  <project>/group_vars/<app>               app group vars (repository_name → ADO repo)
  <project>/group_vars/<env>_<app>         per-env app group vars
  <project>/host_vars/<host>/vars.yml      host vars (plaintext)
  <project>/host_vars/<host>/vault.yml     host secrets (ansible-vault — NOT decrypted)

Team ownership (dev_team/qc_team/prd_team [== ops], and any other <role>_team)
lives in the plaintext group_vars/all. Each app's `repository_name` (in its
group_vars/<app>) names the ADO repo hosting the app's pipeline, so the Access
page can tie inventory pipelines to real ADO repos. Vault files are detected and
counted only — secrets are never decrypted into the UI."""

import json
import re
import time

import yaml

from ..config import settings

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 300

# the primary per-environment teams: dev_team (development), qc_team (quality),
# prd_team (== the ops team). uat_team and any other <role>_team are secondary.
PRIMARY_ROLES = ("dev", "qc", "prd")
ROLE_LABEL = {"dev": "dev", "qc": "qc", "prd": "prd/ops"}
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


def _walk_inventory(node, envs: set, hosts: set) -> None:
    """Walk an Ansible inventory tree collecting env prefixes and host names.
    A group that directly holds `hosts` and is named `<env>_<app>` contributes
    its `<env>` prefix (dev/qc/uat/prd); every host key is collected."""
    if not isinstance(node, dict):
        return
    for hname in (node.get("hosts") or {}):
        if hname:
            hosts.add(str(hname))
    children = node.get("children")
    if isinstance(children, dict):
        for gname, gnode in children.items():
            if isinstance(gnode, dict) and (gnode.get("hosts")) and "_" in gname:
                envs.add(gname.split("_", 1)[0])
            _walk_inventory(gnode, envs, hosts)


def _apps_inventory(pdir, apps: list[str]) -> tuple[set, set]:
    """(envs, inventory-hosts) parsed from each <app>.yml inventory in a project."""
    envs: set = set()
    hosts: set = set()
    for app in apps:
        f = pdir / f"{app}.yml"
        try:
            data = _load_yaml(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        root = data.get("all", data) if isinstance(data, dict) else {}
        _walk_inventory(root, envs, hosts)
    return envs, hosts


def _stringify(v) -> str:
    if isinstance(v, (dict, list)):
        return f"<{type(v).__name__}:{len(v)}>"
    return str(v)


def _app_var(pdir, app: str, key: str):
    """Look up a single app-level var (e.g. repository_name) from the app's
    group_vars — `group_vars/<app>.yml`, `group_vars/<app>` (file), or the
    files under `group_vars/<app>/`. Returns the stringified value or None."""
    gv = pdir / "group_vars"
    candidates = [gv / f"{app}.yml", gv / app]
    d = gv / app
    if d.is_dir():
        candidates = [gv / f"{app}.yml"] + sorted(d.glob("*.yml"))
    for c in candidates:
        if not c.is_file():
            continue
        try:
            data = _load_yaml(c.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if key in data and data[key] not in (None, ""):
            return _stringify(data[key])
    return None


# ---------------------------------------------------- full config-layer parse
def _val(v) -> str:
    """A value rendered for the config viewer/diff: scalars as-is, dict/list as
    canonical JSON (stable so equality == a real diff), None as ''."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        try:
            return json.dumps(v, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(v)
    return str(v)


def _read_vars_file(path) -> dict:
    """One YAML vars file as {key: value_str}. Vault-encrypted files are NEVER
    decrypted — they come back as {'__vault__': True}. Non-dict/parse-fail → {}."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    if _is_vault(text):
        return {"__vault__": True}
    data = _load_yaml(text)
    if not isinstance(data, dict):
        return {}
    return {k: _val(v) for k, v in data.items()}


def _group_files(gv, name: str) -> list:
    """Every file backing a group_vars entry `name` — Ansible allows any of:
    `<name>.yml`, a plain `<name>` file, or `*.yml` under a `<name>/` dir."""
    files = []
    for cand in (gv / f"{name}.yml", gv / name):
        if cand.is_file():
            files.append(cand)
    d = gv / name
    if d.is_dir():
        files.extend(sorted(d.glob("*.yml")))
    return files


def _merge_files(files) -> dict:
    """Merge a group's backing files into one {key: value}. A vault-encrypted
    file contributes a `__vault__` flag (its keys stay opaque, never decrypted)."""
    out: dict = {}
    for f in files:
        d = _read_vars_file(f)
        if d.get("__vault__"):
            out["__vault__"] = True
            continue
        out.update(d)
    return out


def _project_config(pdir, apps: list[str], envs: set) -> dict:
    """The four config layers of a project, values included, for the config
    viewer + diff: project (group_vars/all) · application (group_vars/<app>) ·
    env+app (group_vars/<env>_<app>) · host/environment (host_vars/<host>)."""
    app_set = set(apps)
    gv = pdir / "group_vars"
    project_vars = _merge_files(_group_files(gv, "all")) if gv.exists() else {}
    app_vars: dict = {}
    env_app_vars: dict = {}
    other_groups: dict = {}
    if gv.is_dir():
        seen: set = set()
        for entry in sorted(gv.iterdir()):
            gname = entry.name[:-4] if entry.name.endswith(".yml") else entry.name
            if gname == "all" or gname in seen:
                continue
            seen.add(gname)
            vars_ = _merge_files(_group_files(gv, gname))
            if gname in app_set:
                app_vars[gname] = vars_
            else:
                head, _, tail = gname.partition("_")
                if tail and head in envs and tail in app_set:
                    env_app_vars[gname] = vars_
                else:
                    other_groups[gname] = vars_
    host_vars: dict = {}
    hv = pdir / "host_vars"
    if hv.is_dir():
        for hdir in sorted(hv.iterdir()):
            if hdir.is_dir():
                vf = hdir / "vars.yml"
                host_vars[hdir.name] = _read_vars_file(vf) if vf.is_file() else {}
            elif hdir.suffix == ".yml":
                host_vars[hdir.stem] = _read_vars_file(hdir)
    return {"project_vars": project_vars, "app_vars": app_vars,
            "env_app_vars": env_app_vars, "other_groups": other_groups,
            "host_vars": host_vars}


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

    # deploy_platform (project-wide, in group_vars/all) — drives the log index
    # prefix on the Logging Health page (OCP→oc, LinuxVM→vmlin, …)
    deploy_platform = None
    for k, v in all_vars.items():
        if k.lower() == "deploy_platform" and v not in (None, ""):
            deploy_platform = _stringify(v)
            break

    teams: dict = {}          # role -> team, primary roles (dev/qc/prd)
    other_teams: dict = {}    # any other <role>_team (incl. uat_team)
    other_vars: dict = {}     # everything else in group_vars/all
    for k, v in all_vars.items():
        m = _TEAM_RE.match(k)
        if m:
            role = m.group(1).lower()
            (teams if role in PRIMARY_ROLES else other_teams)[role] = _stringify(v)
        else:
            other_vars[k] = _stringify(v)

    # per-app config: repository_name ties each app's pipeline to its ADO repo
    app_configs = []
    repository_names = []
    for app in apps:
        rn = _app_var(pdir, app, "repository_name")
        app_configs.append({"name": app, "repository_name": rn})
        if rn:
            repository_names.append(rn)

    # environments + inventory hosts come from the <app>.yml inventory trees
    # (all.children.<app>.children.<env>_<app>.hosts.<host>)
    envs, inv_hosts = _apps_inventory(pdir, apps)

    # full four-layer config (values included) for the config viewer + diff
    config = _project_config(pdir, apps, envs)

    # host_vars/<host>/{vars.yml, vault.yml} — flags per host
    host_flags: dict = {}
    vault_files = 0
    hv = pdir / "host_vars"
    if hv.is_dir():
        for hdir in sorted(hv.iterdir()):
            if not hdir.is_dir():
                continue
            vaulted = False
            vpath = hdir / "vault.yml"
            if vpath.is_file():
                try:
                    vaulted = _is_vault(vpath.read_text(encoding="utf-8", errors="replace")[:64])
                except OSError:
                    vaulted = True
                vault_files += 1
            host_flags[hdir.name] = {"vars": (hdir / "vars.yml").is_file(), "vault": vaulted}

    # the host list = inventory hosts ∪ host_vars dirs, with vars/vault flags
    hosts = [{"host": h, "vars": host_flags.get(h, {}).get("vars", False),
              "vault": host_flags.get(h, {}).get("vault", False)}
             for h in sorted(inv_hosts | set(host_flags))]

    return {
        "name": name, "apps": apps, "app_count": len(apps),
        "app_configs": app_configs,               # [{name, repository_name}]
        "repository_names": sorted(set(repository_names)),
        "pipeline_count": len(repository_names),   # apps tying to an ADO repo
        "teams": teams, "other_teams": other_teams,
        "dev_team": teams.get("dev"), "qc_team": teams.get("qc"),
        "prd_team": teams.get("prd"), "ops_team": teams.get("prd"),  # prd_team == ops team
        "deploy_platform": deploy_platform,
        "vars": other_vars, "var_count": len(other_vars),
        "envs": sorted(envs), "hosts": hosts, "host_count": len(hosts),
        "vault_files": vault_files,
        "config": config,
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
        for role in PRIMARY_ROLES:
            t = (p.get("teams") or {}).get(role)
            if t:
                all_teams.setdefault(t, set()).add(f"{p['name']}:{role}")
        for role, t in (p.get("other_teams") or {}).items():
            if t:
                all_teams.setdefault(t, set()).add(f"{p['name']}:{role}")
    return {
        "projects": len(projects),
        "apps": sum(p["app_count"] for p in projects),
        "pipelines": sum(p.get("pipeline_count", 0) for p in projects),
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
    def proj(name, app_repos, teams, other, vars_, envs, hosts, vault, config):
        apps = [a for a, _ in app_repos]
        app_configs = [{"name": a, "repository_name": r} for a, r in app_repos]
        rns = [r for _, r in app_repos if r]
        return {"name": name, "apps": apps, "app_count": len(apps),
                "app_configs": app_configs, "repository_names": sorted(set(rns)),
                "pipeline_count": len(rns),
                "teams": teams, "other_teams": other,
                "dev_team": teams.get("dev"), "qc_team": teams.get("qc"),
                "prd_team": teams.get("prd"), "ops_team": teams.get("prd"),
                "deploy_platform": (config.get("project_vars") or {}).get("deploy_platform"),
                "vars": vars_, "var_count": len(vars_), "envs": envs,
                "hosts": hosts, "host_count": len(hosts), "vault_files": vault,
                "config": config}
    projects = [
        # Platform: the reference story. payments vs checkout differ on timeout_s,
        # feature_flags and prd replica counts; prd_payments also overrides log_level.
        proj("Platform", [("payments", "payments-svc"), ("checkout", "checkout-svc"),
                          ("notifications", "notify-svc")],
             {"dev": "Platform_Devs", "qc": "Platform_QC", "prd": "SRE_Core"},
             {"uat": "Platform_UAT", "security": "AppSec"},
             {"domain": "platform.corp.local", "region": "eu-west", "log_level": "info"},
             ["dev", "qc", "uat", "prd"],
             [{"host": "dev_ocp", "vars": True, "vault": False},
              {"host": "qc_ocp", "vars": True, "vault": False},
              {"host": "uat_ocp", "vars": True, "vault": True},
              {"host": "prd_ocp", "vars": True, "vault": True},
              {"host": "prd_dr_ocp", "vars": True, "vault": True}], 3,
             {"project_vars": {"dev_team": "Platform_Devs", "qc_team": "Platform_QC",
                               "prd_team": "SRE_Core", "uat_team": "Platform_UAT",
                               "security_team": "AppSec", "domain": "platform.corp.local",
                               "region": "eu-west", "log_level": "info", "tls_enabled": "true",
                               "deploy_platform": "OCP"},
              "app_vars": {
                  # app-specific deploy_platform (group_vars/payments/cicd.yml) —
                  # K8s, which CLASHES with the project-global OCP → highlighted
                  "payments": {"repository_name": "payments-svc", "replicas": "2",
                               "timeout_s": "30", "feature_flags": "wallet,card",
                               "deploy_platform": "K8s"},
                  "checkout": {"repository_name": "checkout-svc", "replicas": "2",
                               "timeout_s": "45", "feature_flags": "card"},
                  "notifications": {"repository_name": "notify-svc", "replicas": "2",
                                    "channels": "email,sms"}},
              "env_app_vars": {
                  "dev_payments": {"replicas": "1", "debug": "true"},
                  "prd_payments": {"replicas": "6", "log_level": "warn"},
                  "prd_checkout": {"replicas": "4"}},
              "other_groups": {},
              "host_vars": {
                  "dev_ocp": {"ansible_host": "10.0.0.5"},
                  "qc_ocp": {"ansible_host": "10.0.0.7"},
                  "uat_ocp": {"ansible_host": "10.0.0.9", "__vault__": True},
                  "prd_ocp": {"ansible_host": "10.0.0.11", "__vault__": True},
                  "prd_dr_ocp": {"ansible_host": "10.0.0.12", "__vault__": True}}}),
        # Control: same region as Platform but log_level=debug, no tls_enabled,
        # prd_team (ops) missing — a project-level mismatch to catch.
        proj("Control", [("team-configs", "team-configs")],
             {"dev": "Control_Owners", "qc": "Platform_QC"},   # prd_team (ops) missing
             {}, {"domain": "control.corp.local", "region": "eu-west"},
             ["dev", "prd"],
             [{"host": "dev_ocp", "vars": True, "vault": False},
              {"host": "prd_ocp", "vars": True, "vault": False}], 0,
             {"project_vars": {"dev_team": "Control_Owners", "qc_team": "Platform_QC",
                               "domain": "control.corp.local", "region": "eu-west",
                               "log_level": "debug", "deploy_platform": "LinuxVM"},
              "app_vars": {"team-configs": {"repository_name": "team-configs",
                                            "replicas": "1", "timeout_s": "30"}},
              "env_app_vars": {"prd_team-configs": {"replicas": "2"}},
              "other_groups": {},
              "host_vars": {"dev_ocp": {"ansible_host": "10.0.0.5"},
                            "prd_ocp": {"ansible_host": "10.0.0.11"}}}),
        # Research: region drifts to us-east — the odd one out across projects.
        proj("Research", [("prototypes", None)],
             {"dev": "Research_Team", "qc": "Research_Team", "prd": "SRE_Core"},
             {"data": "DataEng"}, {"domain": "research.corp.local", "experimental": "true"},
             ["dev"], [{"host": "dev_ocp", "vars": True, "vault": True}], 1,
             {"project_vars": {"dev_team": "Research_Team", "qc_team": "Research_Team",
                               "prd_team": "SRE_Core", "data_team": "DataEng",
                               "domain": "research.corp.local", "region": "us-east",
                               "experimental": "true", "deploy_platform": "WindowsVM"},
              "app_vars": {"prototypes": {"experimental": "true", "replicas": "1"}},
              "env_app_vars": {},
              "other_groups": {},
              "host_vars": {"dev_ocp": {"ansible_host": "10.0.0.5", "__vault__": True}}}),
    ]
    return {"source": "demo", "projects": projects, "summary": _summary(projects)}
