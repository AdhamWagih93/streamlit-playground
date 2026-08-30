"""Standard changes — the Engine repo's self-service DB-script catalogue.

Layout under playbooks/SelfServices/roles/run_db_script_on_local:
  files/<db_technology>/<change_name>/*.sql   the scripts of one standard change
  vars/<change_name>/vars.yml                 project_name, csv_fields, actual_fields,
                                              default_values, m2m_flag, requester_team,
                                              approver_team, notified_teams, rbac
  vars/<change_name>/uat.yml | prd.yml        ansible-vault: connection strings

<change_name> follows <category>_<service>.

SECURITY: uat.yml / prd.yml are DETECTED and classified only (vaulted /
plaintext / missing) — they are never decrypted and their contents never
leave the server. A plaintext env file is reported as an anomaly.
"""

import time
from pathlib import Path

from .inventory import _is_vault, _load_yaml
from .repos import _repo_by_slot, _workspace

ROLE = Path("playbooks/SelfServices/roles/run_db_script_on_local")
VARS_KEYS = ("project_name", "csv_fields", "actual_fields", "default_values", "m2m_flag",
             "requester_team", "approver_team", "notified_teams", "rbac")
LIST_KEYS = ("csv_fields", "actual_fields", "default_values", "notified_teams", "rbac")
ENV_FILES = ("uat", "prd")
_CACHE: dict = {}
_TTL = 120


def _as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).replace(";", ",").split(",") if x.strip()]


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_state(path: Path) -> dict:
    """Vault status of one env file — never parsed beyond the header."""
    if not path.is_file():
        return {"state": "missing"}
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:200]
    except OSError:
        return {"state": "unreadable"}
    return {"state": "vaulted" if _is_vault(head) else "plaintext",
            "size": path.stat().st_size}


def parse_tree(root: Path) -> dict:
    base = root / ROLE
    if not base.is_dir():
        return {"found": False, "role": str(ROLE), "changes": [], "anomalies": [],
                "summary": {}}
    files_dir, vars_dir = base / "files", base / "vars"
    changes: dict = {}
    # ---- scripts: files/<tech>/<change>/*.sql -----------------------------
    if files_dir.is_dir():
        for tech in sorted(p for p in files_dir.iterdir() if p.is_dir()):
            for ch in sorted(p for p in tech.iterdir() if p.is_dir()):
                scripts = sorted(p for p in ch.iterdir() if p.is_file())
                c = changes.setdefault(ch.name, {"name": ch.name, "technologies": [],
                                                 "scripts": [], "vars": None, "env": {}})
                c["technologies"].append(tech.name)
                c["scripts"] += [{"name": s.name, "technology": tech.name,
                                  "size": s.stat().st_size,
                                  "sql": s.suffix.lower() == ".sql"} for s in scripts]
    # ---- vars/<change>/{vars.yml,uat.yml,prd.yml} ---------------------------
    if vars_dir.is_dir():
        for vd in sorted(p for p in vars_dir.iterdir() if p.is_dir()):
            c = changes.setdefault(vd.name, {"name": vd.name, "technologies": [],
                                             "scripts": [], "vars": None, "env": {}})
            vf = vd / "vars.yml"
            if not vf.is_file():
                vf = vd / "vars.yaml"
            if vf.is_file():
                raw = _load_yaml(vf.read_text(encoding="utf-8", errors="replace")) or {}
                v = {k: raw.get(k) for k in VARS_KEYS}
                for k in LIST_KEYS:
                    v[k] = _as_list(v.get(k))
                v["m2m_flag"] = _truthy(v.get("m2m_flag"))
                v["extra_keys"] = sorted(k for k in raw if k not in VARS_KEYS)
                c["vars"] = v
            for env in ENV_FILES:
                c["env"][env] = _env_state(vd / f"{env}.yml")
    # ---- derive + anomalies -----------------------------------------------
    out, anomalies = [], []
    for name, c in sorted(changes.items()):
        cat, _, svc = name.partition("_")
        c["category"] = cat if svc else "(uncategorized)"
        c["service"] = svc or name
        c["sql_files"] = sum(1 for s in c["scripts"] if s["sql"])
        c["script_bytes"] = sum(s["size"] for s in c["scripts"])
        v = c["vars"]
        issues = []
        if not c["technologies"]:
            issues.append("no scripts folder under files/<technology>/")
        elif not c["sql_files"]:
            issues.append("scripts folder holds no .sql file")
        if len(c["technologies"]) > 1:
            issues.append(f"defined under {len(c['technologies'])} technologies")
        if v is None:
            issues.append("vars.yml missing")
        else:
            for k in ("project_name", "requester_team", "approver_team"):
                if not v.get(k):
                    issues.append(f"{k} empty")
            if v["csv_fields"] and v["actual_fields"] and len(v["csv_fields"]) != len(v["actual_fields"]):
                issues.append(f"csv_fields ({len(v['csv_fields'])}) ≠ actual_fields ({len(v['actual_fields'])})")
            if v.get("requester_team") and v.get("requester_team") == v.get("approver_team"):
                issues.append("requester_team equals approver_team — no separation of duties")
        for env in ENV_FILES:
            st = (c["env"].get(env) or {}).get("state", "missing")
            if st == "missing":
                issues.append(f"{env}.yml missing")
            elif st == "plaintext":
                issues.append(f"{env}.yml is NOT vault-encrypted — connection secrets in plaintext")
        c["issues"] = issues
        for i in issues:
            anomalies.append({"change": name, "detail": i,
                              "severity": "high" if "plaintext" in i or "missing" in i and "vars" in i else "warn"})
        out.append(c)
    techs = sorted({t for c in out for t in c["technologies"]})
    cats = sorted({c["category"] for c in out})
    return {"found": True, "role": str(ROLE), "changes": out, "anomalies": anomalies,
            "summary": {"changes": len(out), "technologies": techs, "categories": cats,
                        "sql_files": sum(c["sql_files"] for c in out),
                        "m2m": sum(1 for c in out if c["vars"] and c["vars"]["m2m_flag"]),
                        "with_issues": sum(1 for c in out if c["issues"]),
                        "plaintext_env": sum(1 for c in out for e in c["env"].values()
                                             if e.get("state") == "plaintext"),
                        "vaulted_env": sum(1 for c in out for e in c["env"].values()
                                           if e.get("state") == "vaulted")}}


def analyze(slot: int, username: str | None = None, refresh: bool = False) -> dict:
    key = (slot, username)
    hit = _CACHE.get(key)
    if hit and not refresh and time.time() - hit["at"] < _TTL:
        return {**hit["payload"], "cached": True}
    repo = _repo_by_slot(slot)
    payload = parse_tree(_workspace(repo, username))
    _CACHE[key] = {"at": time.time(), "payload": payload}
    return {**payload, "cached": False}
