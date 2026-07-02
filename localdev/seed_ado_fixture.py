"""Seed a fake ADO snapshot for the ADO Coverage tab.

There's no live ADO REST server in the harness, so `_ado_pipeline_coverage`
reads this fixture when ``LOCALDEV_ADO_FIXTURE`` points at it (set by the
runners). Shape matches what the real walk returns: repos keyed to inventory
apps (project + `<project>-<app>` repo name), with deliberate variations so the
reconcile exercises every warning path:

  - most apps: matched repo, all 4 branch hooks, matching team  → clean
  - checkout: missing hotfixes/stress hooks                     → missing hooks
  - ledger:   has an Azure Pipeline                             → azure warning
  - invoice:  ADO team OPS ≠ inventory dev_team DEVDOTNET       → team mismatch
  - settle:   NO ADO repo                                        → not pipelined
  - legacy-tool: ADO repo with no inventory app                 → orphan
"""

from __future__ import annotations

import json
import os

from seed_git import INV_APPS

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
REQUIRED = ["develop", "release", "hotfixes", "stress"]


def main() -> None:
    repos = []
    project_teams: dict = {}
    for proj, app, co, dev, qc, ops, plat, build, dtech in INV_APPS:
        if app == "settle":
            # no ADO repo → this app reads as "not pipelined"
            project_teams.setdefault(proj.lower(), {"project": proj, "team": dev})
            continue
        hooks = list(REQUIRED)
        azure = False
        ado_team = dev
        if app == "checkout":
            hooks = ["develop", "release"]      # missing hotfixes + stress
        if app == "ledger":
            azure = True                        # Azure Pipeline present
        if app == "invoice":
            ado_team = "OPS"                    # team mismatch vs dev_team
        repos.append({
            "project": proj, "repo": f"{proj}-{app}", "id": f"r-{app}",
            "azure_pipeline": azure, "hook_branches": hooks, "ado_team": ado_team,
        })
        project_teams.setdefault(proj.lower(), {"project": proj, "team": dev})
    # An ADO repo with no matching inventory app → orphan.
    repos.append({"project": "legacy", "repo": "legacy-tool", "id": "r-orphan",
                  "azure_pipeline": False, "hook_branches": [], "ado_team": "OPS"})
    project_teams.setdefault("legacy", {"project": "legacy", "team": "OPS"})

    snap = {
        "ok": True, "error": "", "capped": 0,
        "repos": repos, "project_teams": project_teams,
        "totals_ado": {
            "collections": 1, "projects": len(project_teams), "repos": len(repos),
            "azure_pipeline": sum(1 for r in repos if r["azure_pipeline"]),
            "with_hooks": sum(1 for r in repos if r["hook_branches"]),
        },
    }
    os.makedirs(FIX, exist_ok=True)
    with open(os.path.join(FIX, "ado_snapshot.json"), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)
    print(f"[seed_ado_fixture] wrote {len(repos)} repos → fixtures/ado_snapshot.json")


if __name__ == "__main__":
    main()
