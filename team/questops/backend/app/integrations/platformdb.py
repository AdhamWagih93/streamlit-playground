"""Platform Postgres cross-check — inventory ⇄ devops_projects.

A SEPARATE platform database (QO_PLATFORM_DATABASE_URL) holds useful platform
tables; the first one used is `devops_projects` (project, company, dev_team,
qc_team, ops_team). This module cross-checks it against the cloned Ansible
INVENTORY (the Access page's source of truth for who owns what):

  * projects present in the inventory but missing from the table (and vice versa)
  * duplicated project rows in the table
  * team assignment mismatches — inventory dev/qc/prd_team vs the table's
    dev/qc/ops_team (prd ↔ ops are the same role) — and company mismatches

Comparisons are separator- and case-insensitive ("SRE Core" == "SRE_Core"),
so the usual LDAP-spaces vs inventory-underscores drift never false-flags.
Demo mode fabricates rows with one of each discrepancy.
"""

import re
import time

from ..config import settings

_CACHE: dict = {"at": 0.0, "payload": None}
_TTL = 300

_IDENT_RE = re.compile(r"^[A-Za-z0-9_.]+$")

# inventory field ↔ devops_projects column (prd and ops are the same role)
_TEAM_FIELDS = (("dev_team", "dev_team"), ("qc_team", "qc_team"),
                ("prd_team", "ops_team"))


def _norm(v) -> str:
    """Case/separator-insensitive comparison key (spaces == underscores == dashes)."""
    return re.sub(r"[\s_\-]+", "_", str(v or "").strip().lower())


_DEMO_STATE: list | None = None


def _demo_seed() -> list[dict]:
    """One of each discrepancy, plus a separator-drift value that must NOT flag."""
    return [
        # Platform: qc team WRONG; ops team only differs by separator (no flag);
        # company matches
        {"project": "Platform", "company": "Acme Retail", "dev_team": "Platform_Devs",
         "qc_team": "Platform_QA", "ops_team": "SRE Core"},
        # Control: DUPLICATED row (second copy also carries a bad dev team)
        {"project": "Control", "company": None, "dev_team": "Control_Devs",
         "qc_team": None, "ops_team": None},
        {"project": "control", "company": None, "dev_team": "Platform_Devs",
         "qc_team": None, "ops_team": None},
        # Payments-Hub: exists ONLY in the table (not in inventory)
        {"project": "Payments-Hub", "company": "Acme Retail", "dev_team": "Payments_SRE",
         "qc_team": "Payments_QC", "ops_team": "Payments_SRE"},
        # Research is deliberately MISSING from the table
    ]


def _demo_rows() -> list[dict]:
    global _DEMO_STATE
    if _DEMO_STATE is None:
        _DEMO_STATE = _demo_seed()
    return [dict(r) for r in _DEMO_STATE]


_COLUMNS = ("company", "dev_team", "qc_team", "ops_team")


def _table() -> str:
    table = (settings.platform_projects_table or "devops_projects").strip()
    if not _IDENT_RE.match(table):
        raise RuntimeError(f"invalid platform projects table name {table!r}")
    return table


def _engine():
    from sqlalchemy import create_engine
    if not settings.platform_database_url:
        raise RuntimeError("platform DB is not configured — set QO_PLATFORM_DATABASE_URL")
    return create_engine(settings.platform_database_url, pool_pre_ping=True)


def update_project(project: str, fields: dict) -> int:
    """UPDATE company/dev_team/qc_team/ops_team on every row of `project`
    (case-insensitive). Returns rows affected."""
    sets = {k: fields[k] for k in _COLUMNS if k in fields}
    if not (project or "").strip():
        raise ValueError("project is required")
    if not sets:
        raise ValueError("nothing to update — pass at least one of "
                         + ", ".join(_COLUMNS))
    if settings.demo_mode:
        _demo_rows()
        n = 0
        for r in _DEMO_STATE:
            if _norm(r.get("project")) == _norm(project):
                r.update({k: (v or None) for k, v in sets.items()})
                n += 1
        invalidate()
        return n
    from sqlalchemy import text
    table = _table()
    assign = ", ".join(f"{k} = :{k}" for k in sets)
    eng = _engine()
    try:
        with eng.begin() as conn:
            res = conn.execute(text(
                f"UPDATE {table} SET {assign} WHERE lower(project) = lower(:_p)"),
                {**{k: (v or None) for k, v in sets.items()}, "_p": project.strip()})
            n = res.rowcount or 0
    finally:
        eng.dispose()
    invalidate()
    return n


def delete_project(project: str) -> int:
    """DELETE every row of `project` (case-insensitive). Returns rows removed."""
    if not (project or "").strip():
        raise ValueError("project is required")
    if settings.demo_mode:
        _demo_rows()
        global _DEMO_STATE
        before = len(_DEMO_STATE)
        _DEMO_STATE = [r for r in _DEMO_STATE
                       if _norm(r.get("project")) != _norm(project)]
        invalidate()
        return before - len(_DEMO_STATE)
    from sqlalchemy import text
    table = _table()
    eng = _engine()
    try:
        with eng.begin() as conn:
            res = conn.execute(text(
                f"DELETE FROM {table} WHERE lower(project) = lower(:_p)"),
                {"_p": project.strip()})
            n = res.rowcount or 0
    finally:
        eng.dispose()
    invalidate()
    return n


def insert_project(project: str, fields: dict) -> int:
    """INSERT one row (used by the 'add missing inventory project' action)."""
    if not (project or "").strip():
        raise ValueError("project is required")
    row = {k: (fields.get(k) or None) for k in _COLUMNS}
    if settings.demo_mode:
        _demo_rows()
        if any(_norm(r.get("project")) == _norm(project) for r in _DEMO_STATE):
            raise ValueError(f"{project} already exists in the table")
        _DEMO_STATE.append({"project": project.strip(), **row})
        invalidate()
        return 1
    from sqlalchemy import text
    table = _table()
    eng = _engine()
    try:
        with eng.begin() as conn:
            dup = conn.execute(text(
                f"SELECT count(*) FROM {table} WHERE lower(project) = lower(:_p)"),
                {"_p": project.strip()}).scalar()
            if dup:
                raise ValueError(f"{project} already exists in the table")
            conn.execute(text(
                f"INSERT INTO {table} (project, company, dev_team, qc_team, ops_team) "
                f"VALUES (:_p, :company, :dev_team, :qc_team, :ops_team)"),
                {"_p": project.strip(), **row})
    finally:
        eng.dispose()
    invalidate()
    return 1


def dedupe_project(project: str) -> int:
    """Remove duplicate rows of `project`, KEEPING the first. Returns removed."""
    if not (project or "").strip():
        raise ValueError("project is required")
    if settings.demo_mode:
        _demo_rows()
        global _DEMO_STATE
        kept, removed, seen = [], 0, False
        for r in _DEMO_STATE:
            if _norm(r.get("project")) == _norm(project):
                if seen:
                    removed += 1
                    continue
                seen = True
            kept.append(r)
        _DEMO_STATE = kept
        invalidate()
        return removed
    from sqlalchemy import text
    table = _table()
    eng = _engine()
    try:
        with eng.begin() as conn:
            res = conn.execute(text(
                f"DELETE FROM {table} WHERE ctid IN ("
                f"  SELECT ctid FROM ("
                f"    SELECT ctid, row_number() OVER (ORDER BY ctid) AS rn "
                f"    FROM {table} WHERE lower(project) = lower(:_p)) x "
                f"  WHERE x.rn > 1)"), {"_p": project.strip()})
            n = res.rowcount or 0
    finally:
        eng.dispose()
    invalidate()
    return n


def _live_rows() -> list[dict]:
    from sqlalchemy import text
    table = _table()
    engine = _engine()
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                f"SELECT project, company, dev_team, qc_team, ops_team FROM {table}"
            )).mappings().all()
        return [dict(r) for r in rows]
    finally:
        engine.dispose()


def crosscheck(refresh: bool = False) -> dict:
    if not refresh and _CACHE["payload"] and time.time() - _CACHE["at"] < _TTL:
        return {**_CACHE["payload"], "cached": True}
    payload = _crosscheck()
    _CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def invalidate() -> None:
    _CACHE.update(at=0.0, payload=None)


def _crosscheck() -> dict:
    from . import inventory
    inv = inventory.parse()
    inv_projects = inv.get("projects") or []

    base = {"table": settings.platform_projects_table or "devops_projects",
            "configured": bool(settings.platform_database_url) or settings.demo_mode,
            "actions_enabled": bool(settings.platform_db_actions),
            "reachable": False, "error": None, "source": "demo" if settings.demo_mode else "live"}
    if not base["configured"]:
        return {**base, "note": "not configured — set QO_PLATFORM_DATABASE_URL in .env "
                                "to cross-check the devops_projects table"}
    try:
        rows = _demo_rows() if settings.demo_mode else _live_rows()
    except Exception as exc:  # noqa: BLE001 — a broken DB must not break the page
        return {**base, "error": str(exc)[:300]}
    base["reachable"] = True

    # ---- normalize both sides -------------------------------------------
    inv_by_key: dict = {}
    for p in inv_projects:
        pv = ((p.get("config") or {}).get("project_vars") or {})
        inv_by_key[_norm(p["name"])] = {
            "project": p["name"], "company": pv.get("company"),
            "dev_team": p.get("dev_team"), "qc_team": p.get("qc_team"),
            "prd_team": p.get("prd_team")}

    db_by_key: dict = {}
    duplicates: list = []
    for r in rows:
        k = _norm(r.get("project"))
        if not k:
            continue
        if k in db_by_key:
            db_by_key[k]["_count"] += 1
        else:
            db_by_key[k] = {**r, "_count": 1}
    duplicates = [{"project": v.get("project"), "count": v["_count"]}
                  for v in db_by_key.values() if v["_count"] > 1]

    missing_in_db = [v for k, v in sorted(inv_by_key.items()) if k not in db_by_key]
    missing_in_inventory = [
        {"project": v.get("project"), "company": v.get("company"),
         "dev_team": v.get("dev_team"), "qc_team": v.get("qc_team"),
         "ops_team": v.get("ops_team")}
        for k, v in sorted(db_by_key.items()) if k not in inv_by_key]

    # ---- team / company mismatches on the intersection ------------------
    mismatches: list = []
    for k in sorted(set(inv_by_key) & set(db_by_key)):
        iv, dv = inv_by_key[k], db_by_key[k]
        for inv_f, db_f in _TEAM_FIELDS:
            a, b = iv.get(inv_f), dv.get(db_f)
            if a and b and _norm(a) != _norm(b):
                mismatches.append({"project": iv["project"], "field": db_f,
                                   "inventory_field": inv_f,
                                   "inventory": a, "db": b})
            elif a and not b:
                mismatches.append({"project": iv["project"], "field": db_f,
                                   "inventory_field": inv_f,
                                   "inventory": a, "db": None})
        ic, dc = iv.get("company"), dv.get("company")
        if ic and dc and _norm(ic) != _norm(dc):
            mismatches.append({"project": iv["project"], "field": "company",
                               "inventory_field": "company",
                               "inventory": ic, "db": dc})

    matched = len(set(inv_by_key) & set(db_by_key))
    # matched-project details for the breakdown visuals — the TABLE's values,
    # falling back to the inventory where the table has NULLs
    matched_projects = []
    for k in sorted(set(inv_by_key) & set(db_by_key)):
        iv, dv = inv_by_key[k], db_by_key[k]
        matched_projects.append({
            "project": iv["project"],
            "company": dv.get("company") or iv.get("company"),
            "dev_team": dv.get("dev_team") or iv.get("dev_team"),
            "qc_team": dv.get("qc_team") or iv.get("qc_team"),
            "ops_team": dv.get("ops_team") or iv.get("prd_team")})
    # enrich every distinct team with its LDAP members — the SAME resolver the
    # Azure access page uses (Engine repo getTeamMembersCN.sh, 1h-cached).
    # Inventory teams are often underscored while LDAP CNs are dashed, so
    # separator variants are tried until one resolves.
    def _members(team: str) -> dict:
        from ..auth import ldap_group_members
        seen_c = set()
        for cand in (team, team.replace("_", "-"), team.replace(" ", "-"),
                     team.replace(" ", "_"), team.replace("-", "_")):
            if cand.lower() in seen_c:
                continue
            seen_c.add(cand.lower())
            res = ldap_group_members(cand)
            if res.get("found"):
                return {"found": True, "group": cand,
                        "members": [m.get("display_name") or m.get("username") or ""
                                    for m in (res.get("members") or [])]}
        return {"found": False, "group": team, "members": []}

    # ---- APP-specific access: team.yml under group_vars/<app>/ ----------
    # some inventories assign teams per APP instead of (or on top of) the
    # project-wide group_vars/all — the devops_projects table is project-level
    # only, so these can never map to it and are surfaced as their own block
    app_access = []
    for p in inv_projects:
        for app, vars_ in sorted(((p.get("config") or {}).get("app_vars") or {}).items()):
            if not isinstance(vars_, dict):
                continue
            teams = {k: str(v).strip() for k, v in vars_.items()
                     if k.endswith("_team") and isinstance(v, str) and str(v).strip()}
            if teams:
                app_access.append({"project": p["name"], "app": app, "teams": teams})

    team_members = {}
    for m in matched_projects:
        for f in ("dev_team", "qc_team", "ops_team"):
            t = m.get(f)
            if t and t not in team_members:
                team_members[t] = _members(t)
    for aa in app_access:
        for t in aa["teams"].values():
            if t and t not in team_members:
                team_members[t] = _members(t)

    editor_rows = [{"project": v.get("project"), "company": v.get("company"),
                    "dev_team": v.get("dev_team"), "qc_team": v.get("qc_team"),
                    "ops_team": v.get("ops_team"), "count": v["_count"]}
                   for _k, v in sorted(db_by_key.items())]
    return {**base, "rows": editor_rows, "matched_projects": matched_projects,
            "app_access": app_access, "team_members": team_members,
            "inventory_projects": len(inv_by_key), "db_projects": len(db_by_key),
            "db_rows": len(rows), "matched": matched,
            "missing_in_db": missing_in_db,
            "missing_in_inventory": missing_in_inventory,
            "duplicates": sorted(duplicates, key=lambda x: -x["count"]),
            "mismatches": mismatches,
            "ok": not (missing_in_db or missing_in_inventory
                       or duplicates or mismatches)}
