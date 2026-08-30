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


# ---------------------------------------------------------------- shared catalogue
# The Projects page (and anything not tied to a user's worktree) reads the
# SERVER copy of the repo named "Engine".
_ALL_CACHE: dict = {"at": 0.0, "payload": None}


def _engine_root():
    from .repos import _dir_for, configured
    for r in configured():
        if (r.get("name") or "").lower() == "engine":
            d = _dir_for(r)
            return d if d.exists() else None
    return None


def catalog_all(refresh: bool = False) -> dict:
    if not refresh and _ALL_CACHE["payload"] and time.time() - _ALL_CACHE["at"] < 300:
        return _ALL_CACHE["payload"]
    root = _engine_root()
    payload = parse_tree(root) if root else {"found": False, "role": str(ROLE), "changes": [],
                                             "anomalies": [], "summary": {},
                                             "note": "Engine repository is not cloned"}
    _ALL_CACHE.update(at=time.time(), payload=payload)
    return payload


# ---------------------------------------------------------------- data sources (opt-in)
# Reachability of a change's data sources needs host/port from the vaulted
# env files. Decryption is OFF unless STD_CHANGES_DECRYPT=true; even then only
# technology / host / port / db name (and the Oracle DR pair) leave this
# module — username and password are dropped immediately and never cached.
_REACH_CACHE: dict = {}
_SAFE_KEYS = ("db_technology", "db_hostname", "db_port", "db_name",
              "db_hostname_secondary", "db_port_secondary")


def _vault_password(root: Path) -> str:
    p = root / ".vault_pass.txt"
    try:
        return p.read_text(encoding="utf-8").strip() if p.is_file() else ""
    except OSError:
        return ""


def _decrypt_env(path: Path, password: str) -> dict | None:
    """Vault → dict of SAFE keys only. Any failure → None (reported, not fatal)."""
    try:
        from ansible.parsing.vault import VaultLib, VaultSecret
        vault = VaultLib([("default", VaultSecret(password.encode()))])
        text = vault.decrypt(path.read_bytes()).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — wrong password, missing ansible, bad file
        return None
    raw = _load_yaml(text) or {}
    return {k: raw.get(k) for k in _SAFE_KEYS if raw.get(k) not in (None, "")}


def _reach(host: str, port, timeout: float = 3.0) -> dict:
    """TCP connect probe, cached 5 min per host:port."""
    import socket
    key = f"{host}:{port}"
    hit = _REACH_CACHE.get(key)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    res = {"ok": False, "ms": None}
    try:
        t0 = time.time()
        with socket.create_connection((host, int(port)), timeout=timeout):
            res = {"ok": True, "ms": int((time.time() - t0) * 1000)}
    except Exception as exc:  # noqa: BLE001
        res = {"ok": False, "ms": None, "error": type(exc).__name__}
    _REACH_CACHE[key] = (time.time(), res)
    return res


def data_sources(root: Path, changes: list[dict], enabled: bool) -> dict:
    """{change: {env: {technology, host, port, name, secondary, reach}}} —
    only when decryption is enabled and the vault password is present."""
    if not enabled:
        return {"enabled": False, "note": "set STD_CHANGES_DECRYPT=true to resolve data sources "
                                          "(host/port only — credentials never leave the server)"}
    pw = _vault_password(root)
    if not pw:
        return {"enabled": True, "error": ".vault_pass.txt not found at the Engine root"}
    out: dict = {}
    hosts: set = set()
    for c in changes:
        for env in ENV_FILES:
            st = (c["env"].get(env) or {}).get("state")
            if st not in ("vaulted", "plaintext"):
                continue
            path = root / ROLE / "vars" / c["name"] / f"{env}.yml"
            safe = (_decrypt_env(path, pw) if st == "vaulted"
                    else {k: v for k, v in (_load_yaml(path.read_text(encoding="utf-8", errors="replace")) or {}).items()
                          if k in _SAFE_KEYS})
            if safe is None:
                out.setdefault(c["name"], {})[env] = {"error": "could not decrypt (wrong vault password?)"}
                continue
            ds = {"technology": safe.get("db_technology") or "", "host": safe.get("db_hostname") or "",
                  "port": safe.get("db_port"), "name": safe.get("db_name") or ""}
            if safe.get("db_hostname_secondary"):
                ds["secondary"] = {"host": safe["db_hostname_secondary"],
                                   "port": safe.get("db_port_secondary") or safe.get("db_port")}
            out.setdefault(c["name"], {})[env] = ds
            if ds["host"] and ds["port"]:
                hosts.add((ds["host"], ds["port"]))
            if ds.get("secondary", {}).get("host") and ds["secondary"].get("port"):
                hosts.add((ds["secondary"]["host"], ds["secondary"]["port"]))
    # probe distinct endpoints in parallel (bounded)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        probes = dict(zip(hosts, pool.map(lambda hp: _reach(*hp), hosts)))
    for envs in out.values():
        for ds in envs.values():
            if ds.get("host") and ds.get("port"):
                ds["reach"] = probes.get((ds["host"], ds["port"]))
            if ds.get("secondary"):
                ds["secondary"]["reach"] = probes.get((ds["secondary"]["host"], ds["secondary"]["port"]))
    return {"enabled": True, "sources": out,
            "endpoints": [{"host": h, "port": p, **probes[(h, p)]} for h, p in sorted(hosts)]}


def team_facets(changes: list[dict]) -> dict:
    """Owning / approving / notified team classifications for the admin view."""
    own: dict = {}
    for c in changes:
        v = c.get("vars") or {}
        for role, key in (("requester", "requester_team"), ("approver", "approver_team")):
            t = v.get(key)
            if t:
                own.setdefault(t, {"team": t, "requester": [], "approver": [], "notified": []})[role].append(c["name"])
        for t in v.get("notified_teams") or []:
            own.setdefault(t, {"team": t, "requester": [], "approver": [], "notified": []})["notified"].append(c["name"])
    rows = sorted(own.values(), key=lambda x: -(len(x["requester"]) + len(x["approver"]) + len(x["notified"])))
    for r in rows:
        r["total"] = len(set(r["requester"]) | set(r["approver"]) | set(r["notified"]))
    return {"teams": rows}
