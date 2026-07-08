"""People insights — per-user activity counters aggregated from the event log."""
from __future__ import annotations

from datetime import timedelta

from ...auth.rbac import User
from .world import NOW, get_world

WINDOW_DAYS: dict[str, int | None] = {
    "7d": 7, "30d": 30, "90d": 90, "180d": 180, "1y": 365, "all": None,
}

COUNTER_KEYS = ["commits", "builds_authored", "deploys_requested",
                "releases_authored", "requests_made", "approvals"]


def summary(user: User, window: str = "90d", page: int = 1, size: int = 50) -> dict:
    w = get_world()
    days = WINDOW_DAYS.get(window, 90)
    cutoff = NOW - timedelta(days=days) if days else None

    by_email: dict[str, dict] = {}
    for e in w.events:
        if cutoff and e["when"] < cutoff:
            continue
        rec = by_email.setdefault(e["email"], dict(
            label=e["user"], **{k: 0 for k in COUNTER_KEYS}))
        t = e["type"]
        if t == "commit":
            rec["commits"] += 1
        elif t.startswith("build-"):
            rec["builds_authored"] += 1
        elif t == "deploy":
            rec["deploys_requested"] += 1
        elif t == "release":
            rec["releases_authored"] += 1
        elif t == "request":
            rec["approvals"] += 1
            if e["status"] == "approved":
                rec["requests_made"] += 1

    people = {p.email: p for p in w.people}
    rows = []
    for email, rec in by_email.items():
        p = people.get(email)
        total = (rec["commits"] + rec["builds_authored"] + rec["deploys_requested"]
                 + rec["releases_authored"] + rec["requests_made"])
        rows.append(dict(
            email=email,
            name=p.display_name if p else rec["label"],
            title=p.title if p else "",
            teams=list(p.teams) if p else [],
            unknown=p is None,
            total=total,
            **{k: rec[k] for k in COUNTER_KEYS},
        ))
    rows.sort(key=lambda r: (-r["total"], r["name"]))

    tiles = dict(
        users=len(rows),
        with_team=sum(1 for r in rows if r["teams"]),
        commits=sum(r["commits"] for r in rows),
        requests=sum(r["requests_made"] for r in rows),
        approvals=sum(r["approvals"] for r in rows),
    )

    rollup: dict[str, dict] = {}
    for r in rows:
        for t in r["teams"]:
            agg = rollup.setdefault(t, dict(
                team=t, members_active=0, commits=0, deploys=0, releases=0, total=0))
            agg["members_active"] += 1
            agg["commits"] += r["commits"]
            agg["deploys"] += r["deploys_requested"]
            agg["releases"] += r["releases_authored"]
            agg["total"] += r["total"]
    team_rollup = sorted(rollup.values(), key=lambda x: (-x["total"], x["team"]))

    total_rows = len(rows)
    size = max(1, min(size, 200))
    pages = max(1, -(-total_rows // size))
    page = min(max(1, page), pages)
    return dict(
        window=window,
        tiles=tiles,
        rows=rows[(page - 1) * size: page * size],
        total=total_rows, page=page, pages=pages, size=size,
        team_rollup=team_rollup,
    )
