"""Architecture slice — per-env topology model, env diff, AI discovery stream (demo).

Data source: world.architecture  (env → {app → {project, connections, provenance}}).
All entry points scope through `visible_app_names(user)` — the router is admin-gated,
but scoping stays server-side regardless.
"""
from __future__ import annotations

import json
import time
from typing import Iterator

from ...auth.rbac import User
from .scope import visible_app_names
from .world import ENVS, get_world

NODE_CAP = 60
ASYNC_SCHEMES = ("kafka", "amqp")


# ---------------------------------------------------------------- internals
def _legacy_names() -> set[str]:
    return {a.application for a in get_world().apps if a.is_legacy}


def _filtered(env: str, user: User, projects: list[str] | None) -> dict:
    """The env's app entries the user may see, optionally narrowed to projects."""
    names = visible_app_names(user)
    pset = {p for p in (projects or []) if p}
    out = {}
    for app, entry in get_world().architecture.get(env, {}).items():
        if app not in names:
            continue
        if pset and entry["project"] not in pset:
            continue
        out[app] = entry
    return out


def _conn_str(c: dict) -> str:
    return f"{c['target']} · {c['scheme']}://{c['endpoint']}"


# ---------------------------------------------------------------- endpoints
def envs(user: User) -> dict:
    """Environments with app counts and per-project counts."""
    out = []
    for env in ENVS:
        model_ = _filtered(env, user, None)
        proj: dict[str, int] = {}
        for entry in model_.values():
            proj[entry["project"]] = proj.get(entry["project"], 0) + 1
        out.append(dict(
            env=env,
            apps=len(model_),
            projects=[dict(project=p, count=c)
                      for p, c in sorted(proj.items(), key=lambda kv: (-kv[1], kv[0]))],
        ))
    return {"envs": out}


def model(user: User, env: str, projects: list[str] | None = None,
          app: str | None = None) -> dict:
    """Filtered topology graph: app nodes (services) + connection-target nodes."""
    fm = _filtered(env, user, projects)
    legacy = _legacy_names()

    if app and app in fm:
        # Focus subgraph: the app + its direct connections + apps that connect TO it.
        svc_target = f"{app}-service"
        inbound = {a for a, e in fm.items() if a != app and any(
            c["kind"] == "service" and c["target"] == svc_target
            for c in e["connections"])}
        entries: dict[str, dict] = {}
        for a in {app} | inbound:
            e = fm[a]
            conns = e["connections"] if a == app else [
                c for c in e["connections"]
                if c["kind"] == "service" and c["target"] == svc_target]
            entries[a] = {**e, "connections": conns}
        fm = entries

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    seen_edges: set[tuple] = set()
    for a, e in fm.items():
        nodes[a] = dict(id=a, label=a, type="service", project=e["project"],
                        is_legacy=a in legacy, provenance=e["provenance"])
    for a, e in fm.items():
        for c in e["connections"]:
            target = c["target"]
            if c["kind"] == "service":
                peer = target[:-8] if target.endswith("-service") else target
                if peer in fm:
                    tid = peer
                else:
                    tid = target
                    nodes.setdefault(tid, dict(id=tid, label=tid, type="external",
                                               project="", is_legacy=False,
                                               provenance=None))
            else:
                tid = target
                nodes.setdefault(tid, dict(id=tid, label=tid, type=c["kind"],
                                           project="", is_legacy=False,
                                           provenance=None))
            key = (a, tid, c["scheme"], c["kind"])
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append({"source": a, "target": tid, "scheme": c["scheme"],
                          "kind": c["kind"],
                          "async": c["scheme"] in ASYNC_SCHEMES})

    stats = dict(
        services=sum(1 for n in nodes.values() if n["type"] == "service"),
        stores=sum(1 for n in nodes.values() if n["type"] == "db"),
        deps=len(edges),
        legacy=sum(1 for n in nodes.values() if n["is_legacy"]),
    )

    capped = len(nodes) > NODE_CAP
    if capped:
        degree: dict[str, int] = {nid: 0 for nid in nodes}
        for ed in edges:
            degree[ed["source"]] += 1
            degree[ed["target"]] += 1
        # Story-bearing nodes are never trimmed: the focused app, legacy systems
        # and their direct neighbours stay; everything else competes on degree.
        seed = {nid for nid, n in nodes.items() if n["is_legacy"]}
        if app:
            seed.add(app)
        pinned = set(seed)
        for ed in edges:
            if ed["source"] in seed:
                pinned.add(ed["target"])
            if ed["target"] in seed:
                pinned.add(ed["source"])
        ordered = sorted(nodes, key=lambda nid: (nid not in pinned, -degree[nid], nid))
        keep = set(ordered[:max(NODE_CAP, len(pinned))])
        nodes = {nid: n for nid, n in nodes.items() if nid in keep}
        edges = [ed for ed in edges if ed["source"] in nodes and ed["target"] in nodes]

    return {"nodes": list(nodes.values()), "edges": edges,
            "stats": stats, "capped": capped}


def diff(user: User, env_a: str, env_b: str,
         projects: list[str] | None = None) -> dict:
    """Structural env comparison + environment-isolation (repeated URL) check."""
    a = _filtered(env_a, user, projects)
    b = _filtered(env_b, user, projects)
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    changed: list[dict] = []
    repeated: list[dict] = []
    for app in sorted(set(a) & set(b)):
        ca = {_conn_str(c) for c in a[app]["connections"]}
        cb = {_conn_str(c) for c in b[app]["connections"]}
        removed, added = sorted(ca - cb), sorted(cb - ca)
        if removed or added:
            changed.append(dict(app=app, removed=removed, added=added))
        # Same endpoint string in BOTH envs (dev↔prd leaked-DB story). The corporate
        # directory is deliberately env-agnostic, so ldap endpoints are exempt.
        ea = {c["endpoint"] for c in a[app]["connections"] if c["kind"] != "ldap"}
        eb = {c["endpoint"] for c in b[app]["connections"] if c["kind"] != "ldap"}
        for ep in sorted(ea & eb):
            repeated.append(dict(app=app, endpoint=ep, envs=[env_a, env_b]))
    return dict(only_a=only_a, only_b=only_b, changed=changed,
                repeated_urls=repeated)


# ---------------------------------------------------------------- discovery
def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def discover(user: User, env: str = "prd",
             projects: list[str] | None = None) -> Iterator[str]:
    """Staged AI discovery stream — steps → findings → roadmap → done."""
    m = model(user, env, projects)
    s = m["stats"]
    nodes, edges = m["nodes"], m["edges"]
    n_projects = len({n["project"] for n in nodes if n["type"] == "service"})
    n_queues = sum(1 for n in nodes if n["type"] == "queue")
    n_ldap = sum(1 for n in nodes if n["type"] == "ldap")

    steps = [
        ("Clone repositories from source-of-truth", [
            f"$ meridian discover --env {env} --source git@corp:source-of-truth",
            f"cloning {s['services']} repositories @ HEAD (depth=1)…",
            f"✓ {s['services']} working trees ready",
        ]),
        ("Parse build manifests & config trees", [
            "parsing pom.xml · build.gradle · package.json · *.csproj · Chart.yaml",
            f"✓ {s['services']} build manifests · {s['services'] * 2} config files walked",
        ]),
        ("Extract services, stores & dependencies", [
            f"✓ {s['services']} services · {s['stores']} data stores · "
            f"{n_queues} broker(s) · {n_ldap} directory bind(s)",
            f"✓ {s['deps']} dependency edges resolved from connection strings",
        ]),
        ("Reconstruct topology", [
            f"layered {len(nodes)} nodes / {len(edges)} edges across "
            f"{n_projects} project(s)",
            "grouped by project · classified by connection kind (db/queue/service/ldap)",
        ]),
        ("Compare against engineering standards", [
            "checking ENG-SEC-04 (service-layer data access) · ENG-ARC-11 "
            "(async boundaries) · runtime support matrix",
            "⚠ 4 deviations found — ranking by severity",
        ]),
    ]
    total = len(steps)
    for i, (title, lines) in enumerate(steps):
        yield _sse("step", dict(index=i, total=total, title=title,
                                console_lines=lines))
        time.sleep(0.55)

    # a real discovered-but-boring dependency for the LOW finding
    ldap_edge = next((e for e in edges if e["kind"] == "ldap"), None)
    low_app = ldap_edge["source"] if ldap_edge else (nodes[0]["id"] if nodes else "n/a")
    low_target = ldap_edge["target"] if ldap_edge else "corp-ldap"

    findings = [
        dict(severity="HIGH", title="Direct database coupling",
             app="legacy-batch-core",
             detail="legacy-batch-core writes straight to ledger-db "
                    "(oracle://ledger-db-prd.corp:1521), bypassing the ledger "
                    "service layer — violates ENG-SEC-04."),
        dict(severity="HIGH", title="End-of-life runtime",
             app="legacy-batch-core",
             detail="Runtime has been out of vendor support for 14 months — no "
                    "security patches; CVE backlog is unbounded."),
        dict(severity="MED", title="Missing async boundary",
             app="payments-gateway",
             detail="Synchronous gateway→connector path: a slow downstream "
                    "connector back-pressures the edge. Recommend event-driven "
                    "handoff via the platform broker (ENG-ARC-11)."),
        dict(severity="LOW", title="Undocumented dependency",
             app=low_app,
             detail=f"{low_app} → {low_target} discovered in config but absent "
                    "from the architecture docs — docs updated automatically."),
    ]
    yield _sse("findings", findings)
    time.sleep(0.45)

    roadmap = [
        dict(phase=1, name="DE-RISK", horizon="0–3 months", actions=[
            "Stand up an API façade in front of ledger-db",
            "Route all writes through ledger-service",
            "Leave batch reads on a read-only replica credential",
        ]),
        dict(phase=2, name="DECOUPLE", horizon="3–9 months", actions=[
            "Move batch triggers onto the platform scheduler",
            "Retire direct DB credentials from legacy-batch-core",
            "Introduce async handoff on the gateway→connector path",
        ]),
        dict(phase=3, name="REPLACE", horizon="9–18 months", actions=[
            "Strangle batch jobs into owned services, one domain at a time",
            "Decommission the end-of-life runtime",
            "Archive the legacy-batch-core repository",
        ]),
    ]
    yield _sse("roadmap", roadmap)
    yield _sse("done", {"ok": True})
