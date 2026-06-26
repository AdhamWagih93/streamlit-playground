"""Fake LDAP module for local/CI testing.

The dashboard's LDAP→Postgres sync (which auto-runs when the Sync Check tab is
opened) reads team rosters + per-user directory info from ``utils.ldap`` and
writes them into Postgres (``ldap_users`` / ``ldap_team_members``). With a no-op
shim that returns nothing, the sync WIPES any seeded rows — so this shim returns
a real fake directory instead. Members / teams / companies match seed_pg.py and
seed_es_fixtures.py so every surface lines up.

Contract used by cicd_dashboard.py:
  get_team_members(team_cn)      -> [sAMAccountName, ...]
  get_user_info(sAMAccountName)  -> {username(=display), email, title,
                                     department, ldapcompany, manager,
                                     whenCreated, whenChanged}
  get_user_info_by_email(email)  -> {username(=sAMAccountName), title,
                                     department, ldapcompany, manager}
"""

from __future__ import annotations

# sAMAccountName -> directory record (display / email / company / title / teams)
_MEMBERS = {
    "alice.dev": {"display": "Alice Dev", "email": "alice.dev@acme.local",
                  "company": "ACME", "title": "Senior Engineer", "teams": ["DEVJAVA"]},
    "bob.dev":   {"display": "Bob Dev", "email": "bob.dev@acme.local",
                  "company": "ACME", "title": "Engineer", "teams": ["DEVJAVA"]},
    "carol.qc":  {"display": "Carol QC", "email": "carol.qc@acme.local",
                  "company": "ACME", "title": "QA Engineer", "teams": ["QCJAVA"]},
    "dan.net":   {"display": "Dan Net", "email": "dan.net@globex.local",
                  "company": "GLOBEX", "title": "Engineer", "teams": ["DEVDOTNET"]},
    "nina.net":  {"display": "Nina Net", "email": "nina.net@globex.local",
                  "company": "GLOBEX", "title": "QA Engineer", "teams": ["QCNET"]},
    "eve.ops":   {"display": "Eve Ops", "email": "eve.ops@acme.local",
                  "company": "ACME", "title": "Operations Lead", "teams": ["OPS"]},
    "omar.ops":  {"display": "Omar Ops", "email": "omar.ops@globex.local",
                  "company": "GLOBEX", "title": "SRE", "teams": ["OPS"]},
}

_WHEN_CREATED = "20250115090000.0Z"   # LDAP GeneralizedTime
_WHEN_CHANGED = "20260601090000.0Z"


def get_team_members(team_cn: str) -> list:
    tc = (team_cn or "").strip()
    return [un for un, m in _MEMBERS.items() if tc in m["teams"]]


def get_user_info(username: str) -> dict:
    m = _MEMBERS.get((username or "").strip().lower())
    if not m:
        return {}
    # NOTE: the sync uses info["username"] as the DISPLAY label (mirrors the
    # platform LDAP module's shape), with the sAMAccountName carried separately.
    return {
        "username": m["display"],
        "email": m["email"],
        "title": m["title"],
        "department": "Engineering",
        "ldapcompany": m["company"],
        "company": m["company"],
        "manager": "Eve Ops",
        "whenCreated": _WHEN_CREATED,
        "whenChanged": _WHEN_CHANGED,
    }


def get_user_info_by_email(email: str) -> dict:
    e = (email or "").strip().lower()
    for un, m in _MEMBERS.items():
        if m["email"].lower() == e:
            # Here "username" must be the sAMAccountName (ldap_username slot).
            return {
                "username": un,
                "title": m["title"],
                "department": "Engineering",
                "ldapcompany": m["company"],
                "company": m["company"],
                "manager": "Eve Ops",
            }
    return {}


# Legacy/no-op helpers kept for compatibility.
def search_users(*args, **kwargs):
    return []


def get_user(*args, **kwargs):
    return None
