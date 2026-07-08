"""Teams & Members — LDAP-roster views over the demo world."""
from __future__ import annotations

from datetime import timedelta

from ...auth.rbac import ROLE_TEAM_FIELDS, User, team_match_key
from .world import NOW, TEAMS, get_world

# Reverse the role→fields map: dev_team→Developer, qc_team→QC, uat/prd_team→Operations.
FIELD_ROLE: dict[str, str] = {
    f: role for role, fields in ROLE_TEAM_FIELDS.items() for f in fields
}
ROLE_ORDER = ["Developer", "QC", "Operations"]


def _people_by_team() -> dict[str, list]:
    out: dict[str, list] = {}
    for p in get_world().people:
        for t in p.teams:
            out.setdefault(team_match_key(t), []).append(p)
    return out


def summary(user: User) -> dict:
    w = get_world()
    by_team = _people_by_team()
    cards = []
    for team in TEAMS:
        key = team_match_key(team)
        members = by_team.get(key, [])
        roles: set[str] = set()
        projects: set[str] = set()
        for a in w.apps:
            for f, names in a.teams.items():
                if any(team_match_key(n) == key for n in names or []):
                    projects.add(a.project)
                    role = FIELD_ROLE.get(f)
                    if role:
                        roles.add(role)
        cards.append(dict(
            team=team,
            members=len(members),
            roles=[r for r in ROLE_ORDER if r in roles],
            n_new=sum(1 for p in members
                      if p.when_created > NOW - timedelta(days=90)),
            n_updated=sum(1 for p in members
                          if p.when_changed > NOW - timedelta(days=14)),
            projects=len(projects),
            companies=sorted({p.company for p in members}),
        ))
    return dict(
        tiles=dict(
            teams=len(TEAMS),
            members=len(w.people),
            departments=len({p.department for p in w.people}),
            companies=len({p.company for p in w.people}),
        ),
        last_sync=w.drift["ldap"]["last_sync"],
        teams=cards,
    )


def team_detail(user: User, team: str) -> dict | None:
    w = get_world()
    key = team_match_key(team)
    canonical = next((t for t in TEAMS if team_match_key(t) == key), None)
    if canonical is None:
        return None
    members = sorted(
        (p for p in w.people if any(team_match_key(t) == key for t in p.teams)),
        key=lambda p: p.display_name,
    )
    rows = [dict(
        display_name=p.display_name, username=p.username, email=p.email,
        title=p.title, department=p.department, company=p.company,
        manager=p.manager,
        when_created=p.when_created.isoformat(),
        when_changed=p.when_changed.isoformat(),
        other_teams=[t for t in p.teams if team_match_key(t) != key],
    ) for p in members]
    comp_counts: dict[str, int] = {}
    for p in members:
        comp_counts[p.company] = comp_counts.get(p.company, 0) + 1
    companies = [dict(company=c, count=n)
                 for c, n in sorted(comp_counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    apps_owned: dict[str, list[str]] = {}
    for a in w.apps:
        for f, names in a.teams.items():
            if any(team_match_key(n) == key for n in names or []):
                apps_owned.setdefault(f, []).append(a.application)
    return dict(
        team=canonical,
        members=rows,
        companies=companies,
        apps_owned={f: sorted(v) for f, v in apps_owned.items()},
    )


def members_all(user: User, q: str = "", team: str = "",
                page: int = 1, size: int = 50) -> dict:
    w = get_world()
    ql = (q or "").strip().lower()
    tkey = team_match_key(team) if team else ""
    rows = []
    for p in sorted(w.people, key=lambda p: p.display_name):
        if tkey and not any(team_match_key(t) == tkey for t in p.teams):
            continue
        hay = f"{p.display_name} {p.username} {p.email} {p.title} {p.department} {p.company}".lower()
        if ql and ql not in hay:
            continue
        rows.append(dict(
            display_name=p.display_name, username=p.username, email=p.email,
            title=p.title, department=p.department, company=p.company,
            teams=list(p.teams), multi_team=len(p.teams) > 1,
        ))
    total = len(rows)
    size = max(1, min(size, 200))
    pages = max(1, -(-total // size))
    page = min(max(1, page), pages)
    return dict(rows=rows[(page - 1) * size: page * size],
                total=total, page=page, pages=pages, size=size, teams=TEAMS)
