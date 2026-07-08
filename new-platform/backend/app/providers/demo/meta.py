from __future__ import annotations

from datetime import timedelta

from ...auth.rbac import User
from .world import NOW, get_world


def integrations(user: User) -> list[dict]:
    """Health strip — in demo mode everything reports its simulated state."""
    w = get_world()
    drift = len(w.drift["inventory_es"]["field_diffs"]) + \
        len(w.drift["inventory_es"]["only_git"]) + len(w.drift["inventory_es"]["only_es"])
    return [
        dict(key="orchestrator", label="Pipeline orchestrator", glyph="▶", state="ok",
             detail="3 pipelines · queue empty", tip="Build / Request_deploy / Request_promote"),
        dict(key="inventory", label="Source-of-truth inventory", glyph="❖", state="ok",
             detail=f"git · {len(w.apps)} apps", tip="Authoritative git checkout, ES fallback"),
        dict(key="configs", label="Per-team config repos", glyph="⚙", state="ok",
             detail="18 team repos cloned", tip="ADO DevOps/Control"),
        dict(key="search", label="Search & analytics store", glyph="◌", state="ok",
             detail="15 indices reachable", tip="Elasticsearch"),
        dict(key="relational", label="Relational store", glyph="▤", state="ok",
             detail="devops_projects · history mirrors", tip="Postgres"),
        dict(key="vault", label="Secrets vault", glyph="🔒", state="ok",
             detail="5 paths brokered", tip="All integration credentials vault-resolved"),
        dict(key="identity", label="Identity directory", glyph="◎", state="ok",
             detail=f"{len(w.people)} users · synced "
                    f"{w.drift['ldap']['last_sync'][:16].replace('T', ' ')}",
             tip="LDAP → Postgres roster cache"),
        dict(key="scanner", label="Container security scanner", glyph="⛨", state="warn",
             detail="2 apps with critical findings", tip="Prisma / Invicti / ZAP / TruffleHog"),
        dict(key="model", label="On-prem model runtime", glyph="✦", state="ok",
             detail="qwen3.5:9b · 3 AI services", tip="Nothing leaves the building"),
        dict(key="docs", label="Docs index", glyph="✧", state="ok",
             detail="1,384 documents grounded", tip="DocMDs corpus"),
        dict(key="drift", label="Store drift check", glyph="🔀",
             state="warn" if drift else "ok",
             detail=f"{drift} discrepancies" if drift else "0 drift",
             tip="git ↔ search ↔ relational reconciliation"),
    ]


def glossary(user: User) -> dict:
    return {
        "indices": [
            dict(key=k, index=v, purpose=p) for k, v, p in [
                ("inventory", "ef-devops-inventory", "App inventory (source-of-truth projection)"),
                ("versions", "ef-cicd-versions-lookup", "Next version per branch"),
                ("commits", "ef-git-commits", "Git commits"),
                ("jira", "ef-bs-jira-issues", "Jira issues"),
                ("requests", "ef-devops-requests", "Deploy/promote request queue"),
                ("builds", "ef-cicd-builds", "Builds"),
                ("deployments", "ef-cicd-deployments", "Deployments"),
                ("releases", "ef-cicd-releases", "Releases"),
                ("prismacloud", "ef-cicd-prismacloud", "Container image scan"),
                ("invicti", "ef-cicd-invicti", "DAST web scan"),
                ("zap", "ef-cicd-zap", "OWASP ZAP scan"),
                ("trufflehog", "ef-cicd-trufflehog", "Secret detection"),
                ("devops_projects", "ef-devops-projects", "Per-app metadata"),
                ("tools_access", "ef-devops-tools-access", "Tool access grants"),
            ]
        ],
        "roles": [
            dict(role="Admin", sees="everything", acts="everything"),
            dict(role="CLevel", sees="everything", acts="read-only executive view"),
            dict(role="Developer", sees="own teams (any team field)", acts="dev deploys, builds"),
            dict(role="QC", sees="own teams", acts="qc deploys, release promotion"),
            dict(role="Operations", sees="own teams", acts="uat/prd deploys"),
        ],
    }
