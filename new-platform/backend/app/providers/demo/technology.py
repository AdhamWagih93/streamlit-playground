"""Tech & Platforms analytics — usage ranks, cross-ref matrix, consolidation (demo)."""
from __future__ import annotations

from collections import Counter

from ...auth.rbac import User
from .scope import visible_apps

DIMS = ["build_technology", "deploy_technology", "deploy_platform"]
MATRIX_ROW_CAP = 20
MATRIX_COL_CAP = 14
UNSET_APPS_CAP = 20


def summary(user: User, dim: str = "build_technology", by: str = "team") -> dict:
    if dim not in DIMS:
        dim = "build_technology"
    if by not in ("team", "project"):
        by = "team"

    apps = visible_apps(user)
    total = len(apps)

    kpis = {d: len({getattr(a, d) for a in apps if getattr(a, d)}) for d in DIMS}
    fully = sum(1 for a in apps if all(getattr(a, d) for d in DIMS))

    counts = Counter(getattr(a, dim) for a in apps if getattr(a, dim))
    ranked = [dict(value=v, count=c, pct=round(100 * c / max(1, total), 1))
              for v, c in counts.most_common()]
    unset_apps = sorted(a.application for a in apps if not getattr(a, dim))

    # cross-reference matrix: dim values × team/project
    def col_key(a) -> str:
        if by == "team":
            return (a.teams.get("dev_team") or ["—"])[0]
        return a.project

    col_counts = Counter(col_key(a) for a in apps if getattr(a, dim))
    cols = [c for c, _ in col_counts.most_common(MATRIX_COL_CAP)]
    rows = []
    for r in ranked[:MATRIX_ROW_CAP]:
        v = r["value"]
        cells = [sum(1 for a in apps if getattr(a, dim) == v and col_key(a) == c)
                 for c in cols]
        rows.append(dict(value=v, cells=cells, total=r["count"]))
    col_totals = [sum(row["cells"][j] for row in rows) for j in range(len(cols))]

    singletons = [
        dict(value=r["value"],
             app=next(a.application for a in apps if getattr(a, dim) == r["value"]))
        for r in ranked if r["count"] == 1
    ]

    return dict(
        dim=dim,
        by=by,
        kpis=dict(
            build_technology=kpis["build_technology"],
            deploy_technology=kpis["deploy_technology"],
            deploy_platform=kpis["deploy_platform"],
            apps_total=total,
            fully_specified_pct=round(100 * fully / max(1, total), 1),
        ),
        ranked=ranked,
        unset=len(unset_apps),
        most=ranked[0] if ranked else None,
        least=ranked[-1] if ranked else None,
        matrix=dict(cols=cols, rows=rows, col_totals=col_totals),
        consolidation=dict(unset_apps=unset_apps[:UNSET_APPS_CAP],
                           singletons=singletons),
    )
