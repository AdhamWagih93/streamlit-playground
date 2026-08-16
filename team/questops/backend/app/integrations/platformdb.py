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


def _demo_rows() -> list[dict]:
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


def _live_rows() -> list[dict]:
    from sqlalchemy import create_engine, text
    table = (settings.platform_projects_table or "devops_projects").strip()
    if not _IDENT_RE.match(table):
        raise RuntimeError(f"invalid platform projects table name {table!r}")
    engine = create_engine(settings.platform_database_url, pool_pre_ping=True)
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
    return {**base,
            "inventory_projects": len(inv_by_key), "db_projects": len(db_by_key),
            "db_rows": len(rows), "matched": matched,
            "missing_in_db": missing_in_db,
            "missing_in_inventory": missing_in_inventory,
            "duplicates": sorted(duplicates, key=lambda x: -x["count"]),
            "mismatches": mismatches,
            "ok": not (missing_in_db or missing_in_inventory
                       or duplicates or mismatches)}
