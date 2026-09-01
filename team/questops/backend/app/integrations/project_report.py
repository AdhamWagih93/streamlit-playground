"""Per-project drill-down — the Projects page.

ONE cached report per (project, window) aggregating every system QuestOps
already knows about, without adding load on any of them:

  * inventory        — apps, envs, teams, approvers, configs   (5m cache)
  * platform DB      — devops_projects row + presence flags    (5m cache)
  * Azure DevOps     — repos/teams/url, from the Access page's cached
                       project sweep (15m) — NO extra ADO calls
  * commits          — ef-git-commits IN ELASTICSEARCH (the CI/CD platform
                       mirrors git there): rate, per-day histogram, top
                       contributors, repos, recent commits — one query
  * Jira             — ef-bs-jira-issues IN ELASTICSEARCH: states,
                       priorities, assignees, update history, recent
                       tickets — one query (never touches Jira itself)
  * security scans   — ef-cicd-{prismacloud,invicti,zap,trufflehog,fortify}:
                       latest scan per app with severity counts
  * logging          — the Logging page's cached analysis slice

Sections degrade independently: a dead system yields {"error": ...} for its
block, never a dead page. The report itself caches for 10 minutes.
"""

import datetime as dt
import re
import time

import requests

from ..config import settings

_CACHE: dict = {}
_TTL = 600

CLOSED_JIRA = ["Done", "Closed", "Resolved", "Cancelled", "Rejected"]

# scan index ↔ how to read its severity fields (see the CI/CD dashboard —
# prisma/invicti use V*, zap has no Vcritical + kw-typed extras, trufflehog
# is UPPERCASE with a `verified` flag, fortify carries no counts at all)
_SCANNERS = {
    "prismacloud": {"index": "ef-cicd-prismacloud", "label": "Prisma (image)",
                    "sev": ("Vcritical", "Vhigh", "Vmedium", "Vlow"),
                    "extra": ("Ccritical", "Chigh", "Cmedium", "Clow",
                              "imageName", "imageTag")},
    "invicti":     {"index": "ef-cicd-invicti", "label": "Invicti (DAST)",
                    "sev": ("Vcritical", "Vhigh", "Vmedium", "Vlow"),
                    "extra": ("BestPractice", "Informational", "url")},
    "zap":         {"index": "ef-cicd-zap", "label": "ZAP (DAST)",
                    "sev": (None, "Vhigh", "Vmedium", "Vlow"),
                    "extra": ("Informational", "url")},
    "trufflehog":  {"index": "ef-cicd-trufflehog", "label": "TruffleHog (secrets)",
                    "sev": ("CRITICAL", "HIGH", "MEDIUM", "LOW"),
                    "extra": ("verified", "findings_count", "detector")},
    "fortify":     {"index": "ef-cicd-fortify", "label": "Fortify (SAST)",
                    "sev": (None, None, None, None),   # status-only index
                    "extra": ("branch", "commitid")},
}


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _norm(v) -> str:
    return re.sub(r"[\s_\-]+", "_", str(v or "").strip().lower())


def _name_variants(name: str) -> list[str]:
    """Exact terms candidates for keyword fields whose casing/separator may
    drift from the inventory name."""
    out, seen = [], set()
    for v in (name, name.lower(), name.upper(),
              name.replace(" ", "_"), name.replace("_", "-"),
              name.replace("-", "_"), name.replace(" ", "-")):
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _es(index: str, body: dict) -> dict:
    """One prd-Elasticsearch search. Raises on transport/HTTP errors so each
    section can surface ITS error without killing the page.
    The index is searched as `<name>*` so backup/rollover copies matching the
    pattern (ef-git-commits-bkp-2026…, …) are included too."""
    if not (settings.es_url and settings.es_api_key):
        raise RuntimeError("Elasticsearch is not configured (ES_URL / ES_API_KEY)")
    pattern = index if index.endswith("*") else index + "*"
    r = requests.post(f"{settings.es_url.rstrip('/')}/{pattern}/_search?ignore_unavailable=true", json=body,
                      headers={"Authorization": f"ApiKey {settings.es_api_key}"},
                      timeout=30, verify=settings.es_verify_ssl)
    r.raise_for_status()
    return r.json()


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------- sections
def _sec_inventory(name: str) -> dict:
    from . import inventory
    inv = inventory.parse()
    p = next((x for x in inv.get("projects") or []
              if _norm(x["name"]) == _norm(name)), None)
    if not p:
        raise RuntimeError(f"project {name!r} not found in the inventory")
    pv = ((p.get("config") or {}).get("project_vars") or {})
    av = ((p.get("config") or {}).get("app_vars") or {})
    apps = []
    for a in (p.get("app_configs") or [{"name": x} for x in p.get("apps") or []]):
        vars_ = av.get(a["name"]) if isinstance(av.get(a["name"]), dict) else {}
        apps.append({"name": a["name"], "repository_name": a.get("repository_name"),
                     "deploy_platform": (vars_ or {}).get("deploy_platform")
                     or pv.get("deploy_platform"),
                     "deploy_technology": (vars_ or {}).get("deploy_technology")
                     or pv.get("deploy_technology")})
    from .approvers import _as_list
    return {"name": p["name"], "company": pv.get("company"),
            "teams": {"dev": p.get("dev_team"), "qc": p.get("qc_team"),
                      "prd": p.get("prd_team"), **(p.get("other_teams") or {})},
            "approvers": _as_list(pv.get("prd_approvers")),
            "deploy_platform": pv.get("deploy_platform"),
            "deploy_technology": pv.get("deploy_technology"),
            "envs": p.get("envs") or [], "hosts": p.get("hosts") or [],
            "vault_files": p.get("vault_files") or 0,
            "apps": apps, "pipeline_count": p.get("pipeline_count") or 0}


def _sec_platform_db(name: str) -> dict:
    from . import platformdb
    d = platformdb.crosscheck()
    if d.get("error"):
        raise RuntimeError(d["error"])
    if not d.get("configured"):
        return {"configured": False}
    row = next((r for r in d.get("rows") or []
                if _norm(r.get("project")) == _norm(name)), None)
    pres = next((x for x in d.get("presence") or []
                 if _norm(x.get("project")) == _norm(name)), None)
    return {"configured": True, "row": row,
            "presence": pres,
            "in_table": row is not None}


def _sec_ado(name: str) -> dict:
    from . import access
    d = access.ado_projects()
    if d.get("source") == "not configured":
        return {"configured": False}
    p = next((x for x in d.get("projects") or []
              if _norm(x.get("name")) == _norm(name)), None)
    if not p:
        return {"configured": True, "found": False}

    def _count(v):
        return v if isinstance(v, int) else len(v or [])
    return {"configured": True, "found": True, "collection": p.get("coll"),
            "url": p.get("url"), "last_update": p.get("last_update"),
            "description": p.get("description"),
            "repo_count": _count(p.get("repos")),
            "team_count": _count(p.get("teams")),
            "member_count": _count(p.get("members")),
            "score": p.get("score"), "grade": p.get("grade"),
            "team": p.get("team"), "team_ok": p.get("team_ok"),
            "pipelines": p.get("inv_pipelines"),
            "pipelines_matched": p.get("inv_pipelines_matched")}


def _win(field: str, days: int, prev: bool = False) -> dict:
    """Range filter for the selected window, or the SAME-LENGTH window just
    before it (prev=True) — used for like-for-like comparisons."""
    if prev:
        return {"range": {field: {"gte": f"now-{2 * days}d", "lt": f"now-{days}d"}}}
    return {"range": {field: {"gte": f"now-{days}d"}}}


def _sec_commits(name: str, repos: list[str], days: int, prev: bool = False) -> dict:
    should = [{"terms": {"project": _name_variants(name)}}]
    if repos:
        should.append({"terms": {"repository": repos[:64]}})
    all_time = not days
    body = {
        "query": {"bool": {
            "filter": [] if all_time else [_win("commitdate", days, prev)],
            "should": should, "minimum_should_match": 1}},
        "sort": [{"commitdate": {"order": "desc", "unmapped_type": "date"}}],
        "_source": ["commitdate", "repository", "branch", "authorname",
                    "authormail", "commitauthor", "commitmessage", "commitid",
                    "project", "insertedlines", "deletedlines"],
        "aggs": {
            "per_day": {"date_histogram": {
                "field": "commitdate",
                "calendar_interval": "month" if all_time else "day"},
                "aggs": {"authors": {"terms": {"field": "authorname", "size": 8,
                                               "missing": "(unknown)"}}}},
            "author_repo": {"terms": {"field": "authorname", "size": 10,
                                      "missing": "(unknown)"},
                            "aggs": {"repos": {"terms": {"field": "repository",
                                                         "size": 12}}}},
            "authors": {"terms": {"field": "authorname", "size": 30,
                                  "missing": "(unknown)"},
                        "aggs": {"ins": {"sum": {"field": "insertedlines"}},
                                 "dele": {"sum": {"field": "deletedlines"}}}},
            "repos": {"terms": {"field": "repository", "size": 30},
                      "aggs": {"ins": {"sum": {"field": "insertedlines"}},
                               "dele": {"sum": {"field": "deletedlines"}}}},
            "ins": {"sum": {"field": "insertedlines"}},
            "dele": {"sum": {"field": "deletedlines"}},
            "branches": {"terms": {"field": "branch", "size": 10}},
        },
        "track_total_hits": True, "size": 10000,
    }
    resp = _es("ef-git-commits", body)
    hits = resp.get("hits", {})
    total = (hits.get("total") or {}).get("value", 0)
    aggs = resp.get("aggregations") or {}

    def _buckets(key):
        return [{"key": b.get("key_as_string") or b.get("key"),
                 "count": b.get("doc_count", 0)}
                for b in (aggs.get(key) or {}).get("buckets", [])]

    recent = []
    for h in hits.get("hits", []):
        s = h.get("_source") or {}
        msg = (s.get("commitmessage") or "").strip().splitlines()
        author = (s.get("authorname") or "").strip() or re.sub(
            r"\s*<[^>]*>\s*$", "", (s.get("commitauthor") or "")).strip()
        recent.append({"when": (s.get("commitdate") or "")[:16].replace("T", " "),
                       "repo": s.get("repository") or "",
                       "branch": s.get("branch") or "",
                       "author": author,
                       "added": _int(s.get("insertedlines")),
                       "deleted": _int(s.get("deletedlines")),
                       "id": str(s.get("commitid") or "")[:10],
                       "id_full": str(s.get("commitid") or ""),
                       "message": (msg[0] if msg else "")[:140],
                       "message_full": (s.get("commitmessage") or "").strip()[:600]})
    per_day = []
    for b in (aggs.get("per_day") or {}).get("buckets", []):
        by_author: dict = {}
        for ab in (b.get("authors") or {}).get("buckets", []):
            disp = _user_display(ab.get("key") or "") or "(unknown)"
            by_author[disp] = by_author.get(disp, 0) + ab.get("doc_count", 0)
        per_day.append({"key": b.get("key_as_string") or b.get("key"),
                        "count": b.get("doc_count", 0), "by_author": by_author})
    author_repo = []
    _ar: dict = {}
    for ab in (aggs.get("author_repo") or {}).get("buckets", []):
        k = _user_key(ab.get("key") or "") or "(unknown)"
        slot = _ar.setdefault(k, {"author": _user_display(ab.get("key") or "") or "(unknown)",
                                  "total": 0, "repos": {}})
        slot["total"] += ab.get("doc_count", 0)
        for rb in (ab.get("repos") or {}).get("buckets", []):
            slot["repos"][rb["key"]] = slot["repos"].get(rb["key"], 0) + rb.get("doc_count", 0)
    author_repo = sorted(_ar.values(), key=lambda x: -x["total"])
    active_days = sum(1 for b in per_day if b["count"])
    if all_time and len(per_day) > 1:   # rate over the OBSERVED span
        span = max((dt.date.fromisoformat(per_day[-1]["key"][:10])
                    - dt.date.fromisoformat(per_day[0]["key"][:10])).days + 30, 1)
    else:
        span = max(days, 1)
    def _lines_rows(node, fold):
        rows: dict = {}
        for b in (node or {}).get("buckets", []):
            k = _user_key(b.get("key")) if fold else b.get("key")
            slot = rows.setdefault(k, {"key": _user_display(b.get("key")) if fold else b.get("key"),
                                       "count": 0, "added": 0, "deleted": 0})
            slot["count"] += b.get("doc_count", 0)
            slot["added"] += _int((b.get("ins") or {}).get("value"))
            slot["deleted"] += _int((b.get("dele") or {}).get("value"))
        return sorted(rows.values(), key=lambda x: -x["count"])
    lines = {"added": _int((aggs.get("ins") or {}).get("value")),
             "deleted": _int((aggs.get("dele") or {}).get("value"))}
    return {"total": total, "days": days, "unit": "month" if all_time else "day",
            "lines": lines,
            "per_day": [{"day": (b["key"] or "")[:10], "count": b["count"],
                         "by_author": b.get("by_author") or {}}
                        for b in per_day],
            "author_repo": author_repo,
            "rate": round(total / span, 2),
            "active_days": active_days,
            "authors": _lines_rows(aggs.get("authors"), fold=True),
            "repos": _lines_rows(aggs.get("repos"), fold=False),
            "branches": _buckets("branches"), "recent": recent}


def _sec_jira(name: str, days: int) -> dict:
    variants = _name_variants(name)
    body = {
        "query": {"bool": {"filter": [{"bool": {"should": [
            {"terms": {"project": variants}},
            {"terms": {"projectkey": variants}},
        ], "minimum_should_match": 1}}]}},
        "sort": [{"updated": {"order": "desc", "unmapped_type": "date"}}],
        "_source": ["issuekey", "issueurl", "summary", "priority", "status",
                    "issuetype", "assignee", "created", "updated", "resolved"],
        "aggs": {
            "open": {"filter": {"bool": {"must_not": [
                {"terms": {"status": CLOSED_JIRA}}]}},
                "aggs": {"by_status": {"terms": {"field": "status", "size": 20}},
                         "by_priority": {"terms": {"field": "priority", "size": 10}},
                         "matrix": {"terms": {"field": "priority", "size": 8,
                                              "missing": "(none)"},
                                    "aggs": {"by_status": {"terms": {
                                        "field": "status", "size": 15}}}}}},
            "done": {"filter": {"bool": {
                "filter": [{"terms": {"status": CLOSED_JIRA}}]
                + ([{"range": {"resolved": {"gte": f"now-{days}d"}}}] if days else [])}},
                "aggs": {"by_assignee": {"terms": {"field": "assignee", "size": 12,
                                                   "missing": "(unassigned)"}},
                         "per_period": {"date_histogram": {
                             "field": "resolved",
                             "calendar_interval": "week" if days else "month"}},
                         "recent": {"top_hits": {"size": 50,
                             "_source": ["issuekey", "issueurl", "summary",
                                         "priority", "issuetype", "assignee",
                                         "created", "resolved"],
                             "sort": [{"resolved": {"order": "desc",
                                                    "unmapped_type": "date"}}]}}}},
            "workload": {"terms": {"field": "assignee", "size": 10,
                                   "missing": "(unassigned)"},
                         "aggs": {"open": {"filter": {"bool": {"must_not": [
                             {"terms": {"status": CLOSED_JIRA}}]}},
                             "aggs": {"by_priority": {"terms": {
                                 "field": "priority", "size": 8,
                                 "missing": "(none)"}}}},
                             "done": {"filter": {"bool": {
                                 "filter": [{"terms": {"status": CLOSED_JIRA}}]
                                 + ([{"range": {"resolved": {"gte": f"now-{days}d"}}}]
                                    if days else [])}}}}},
            "by_status": {"terms": {"field": "status", "size": 25}},
            "by_type": {"terms": {"field": "issuetype", "size": 15}},
            "by_assignee": {"terms": {"field": "assignee", "size": 10,
                                      "missing": "(unassigned)"}},
            "updates": {"filter": ({"range": {"updated": {"gte": f"now-{days}d"}}}
                                   if days else {"match_all": {}}),
                        "aggs": {"per_week": {"date_histogram": {
                            "field": "updated",
                            "calendar_interval": "week" if days else "month"}}}},
        },
        "track_total_hits": True, "size": 200,
    }
    resp = _es("ef-bs-jira-issues", body)
    hits = resp.get("hits", {})
    total = (hits.get("total") or {}).get("value", 0)
    aggs = resp.get("aggregations") or {}

    def _b(node):
        return [{"key": b.get("key"), "count": b.get("doc_count", 0)}
                for b in (node or {}).get("buckets", [])]

    recent = [{"key": (h.get("_source") or {}).get("issuekey") or h.get("_id"),
               "url": (h.get("_source") or {}).get("issueurl") or "",
               "summary": ((h.get("_source") or {}).get("summary") or "")[:140],
               "status": (h.get("_source") or {}).get("status") or "",
               "priority": (h.get("_source") or {}).get("priority") or "",
               "type": (h.get("_source") or {}).get("issuetype") or "",
               "assignee": (h.get("_source") or {}).get("assignee") or "",
               "updated": ((h.get("_source") or {}).get("updated") or "")[:16].replace("T", " "),
               "resolved": bool((h.get("_source") or {}).get("resolved"))}
              for h in hits.get("hits", [])]
    open_node = aggs.get("open") or {}
    done_node = aggs.get("done") or {}
    # priority × status heatmap of OPEN tickets
    _PRIO_RANK = {"blocker": 0, "critical": 1, "highest": 2, "high": 3,
                  "medium": 4, "low": 5, "lowest": 6}
    matrix_rows = []
    statuses_seen: dict = {}
    for pb in (open_node.get("matrix") or {}).get("buckets", []):
        cells = {sb["key"]: sb["doc_count"]
                 for sb in (pb.get("by_status") or {}).get("buckets", [])}
        for st, n in cells.items():
            statuses_seen[st] = statuses_seen.get(st, 0) + n
        matrix_rows.append({"priority": pb.get("key"), "cells": cells,
                            "total": pb.get("doc_count", 0)})
    matrix_rows.sort(key=lambda r: _PRIO_RANK.get(str(r["priority"]).lower(), 8))
    matrix_statuses = [k for k, _ in sorted(statuses_seen.items(),
                                            key=lambda kv: -kv[1])]
    # who completed the most + how fast (details from the 50 freshest)
    done_recent = []
    res_days = []
    for h in (((done_node.get("recent") or {}).get("hits") or {}).get("hits") or []):
        src = h.get("_source") or {}
        took = None
        try:
            c0 = dt.datetime.fromisoformat((src.get("created") or "")[:19])
            r0 = dt.datetime.fromisoformat((src.get("resolved") or "")[:19])
            took = max(round((r0 - c0).total_seconds() / 86400, 1), 0)
            res_days.append(took)
        except ValueError:
            pass
        done_recent.append({"key": src.get("issuekey") or "",
                            "url": src.get("issueurl") or "",
                            "summary": (src.get("summary") or "")[:140],
                            "priority": src.get("priority") or "",
                            "type": src.get("issuetype") or "",
                            "assignee": _user_display(src.get("assignee") or ""),
                            "resolved": (src.get("resolved") or "")[:10],
                            "took_days": took})
    workload = []
    for wb in (aggs.get("workload") or {}).get("buckets", []):
        wo = (wb.get("open") or {})
        workload.append({
            "assignee": _user_display(wb.get("key") or "") or "(unassigned)",
            "open": wo.get("doc_count", 0),
            "open_by_priority": {b["key"]: b["doc_count"]
                                 for b in (wo.get("by_priority") or {}).get("buckets", [])},
            "done": (wb.get("done") or {}).get("doc_count", 0)})
    workload.sort(key=lambda w: -(w["open"] + w["done"]))
    return {"total": total, "matched": total > 0,
            "open": open_node.get("doc_count", 0),
            "matrix": {"rows": matrix_rows, "statuses": matrix_statuses},
            "done": {"total": done_node.get("doc_count", 0),
                     "by_assignee": _fold_users(_b(done_node.get("by_assignee"))),
                     "per_period": [{"week": (b.get("key_as_string") or "")[:10],
                                     "count": b.get("doc_count", 0)}
                                    for b in (done_node.get("per_period") or {})
                                    .get("buckets", [])],
                     "avg_days": round(sum(res_days) / len(res_days), 1) if res_days else None,
                     "recent": done_recent},
            "workload": workload,
            "open_by_status": _b(open_node.get("by_status")),
            "open_by_priority": _b(open_node.get("by_priority")),
            "by_status": _b(aggs.get("by_status")),
            "by_type": _b(aggs.get("by_type")),
            "by_assignee": _fold_users(_b(aggs.get("by_assignee"))),
            "updates_per_week": [
                {"week": (b.get("key_as_string") or "")[:10],
                 "count": b.get("doc_count", 0)}
                for b in ((aggs.get("updates") or {}).get("per_week") or {})
                .get("buckets", [])],
            "recent": recent}


def _user_key(u) -> str:
    """Identity key for user names across systems: case-insensitive, any
    @domain tail dropped, and '.', '_' and SPACES all equivalent — so
    Alice Nasr, Alice.Nasr, alice_nasr and alice.nasr@corp.com are all
    ONE person."""
    s = str(u or "").strip().lower()
    s = _strip_domain(s)
    return re.sub(r"[.\s]+", "_", s)


def _strip_domain(s: str) -> str:
    """'CORP\\alice.nasr' / 'alice.nasr@corp.com' → 'alice.nasr' (Windows
    DOMAIN\\user prefix and @domain tail both dropped)."""
    s = s.rsplit("\\", 1)[-1] if "\\" in s else s
    return s.split("@", 1)[0].strip()


def _user_display(u) -> str:
    """Canonical display form: domain stripped, dots/spaces shown as
    underscores (original casing kept)."""
    s = _strip_domain(str(u or "").strip())
    return re.sub(r"[.\s]+", "_", s)


def _fold_users(buckets: list[dict]) -> list[dict]:
    """Merge {key, count} rows that are the same person under _user_key."""
    merged: dict = {}
    for b in buckets or []:
        k = _user_key(b.get("key"))
        if not k:
            continue
        slot = merged.setdefault(k, {"key": _user_display(b.get("key")), "count": 0})
        slot["count"] += b.get("count", 0)
    return sorted(merged.values(), key=lambda x: -x["count"])


def _jira_change_author(a) -> str:
    """`author` is unmapped (object OR plain string depending on the feeder)."""
    if isinstance(a, dict):
        return str(a.get("displayName") or a.get("name") or a.get("key") or "")
    return str(a or "")


def _jira_change_items(items) -> list[dict]:
    """`items` mirrors Jira's changelog items — [{field, fromString, toString}]
    — but is unmapped, so every shape is handled defensively."""
    out = []
    if isinstance(items, dict):
        items = [items]
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        field = str(it.get("field") or it.get("fieldId") or "?")
        frm = str(it.get("fromString") or it.get("from") or "")[:80]
        to = str(it.get("toString") or it.get("to") or "")[:80]
        out.append({"field": field, "from": frm, "to": to})
    return out


def _sec_jira_changes(name: str, days: int) -> dict:
    """ef-bs-jira-changes — one row per Jira changelog entry. projectname /
    projectkey / issuekey are TEXT (no keyword mapping), so matching uses
    match_phrase, never terms. One query: weekly histogram + up to 200 recent
    change docs; authors and changed-field stats are folded from those docs
    (author/items are unmapped, so ES can't aggregate them)."""
    should = []
    for v in _name_variants(name):
        should += [{"match_phrase": {"projectname": v}},
                   {"match_phrase": {"projectkey": v}}]
    body = {
        "query": {"bool": {
            "filter": [] if not days else
            [{"range": {"created": {"gte": f"now-{days}d"}}}],
            "should": should, "minimum_should_match": 1}},
        "sort": [{"created": {"order": "desc", "unmapped_type": "date"}}],
        "_source": ["created", "issuekey", "issueurl", "author", "items"],
        "aggs": {"per_week": {"date_histogram": {
            "field": "created",
            "calendar_interval": "week" if days else "month"}}},
        "track_total_hits": True, "size": 10000,
    }
    resp = _es("ef-bs-jira-changes", body)
    hits = (resp.get("hits") or {}).get("hits") or []
    total = ((resp.get("hits") or {}).get("total") or {}).get("value", 0)
    authors: dict = {}
    fields: dict = {}
    recent = []
    for h in hits:
        s = h.get("_source") or {}
        raw = _jira_change_author(s.get("author"))
        who = _user_display(raw) or "(unknown)"
        akey = _user_key(raw) or "(unknown)"
        slot = authors.setdefault(akey, {"key": who, "count": 0})
        slot["count"] += 1
        items = _jira_change_items(s.get("items"))
        for it in items:
            fields[it["field"]] = fields.get(it["field"], 0) + 1
        recent.append({"when": (s.get("created") or "")[:16].replace("T", " "),
                       "key": s.get("issuekey") or "",
                       "url": s.get("issueurl") or "",
                       "author": who, "items": items[:4]})
    rank = lambda d_: sorted(({"key": k, "count": v} for k, v in d_.items()),  # noqa: E731
                             key=lambda x: -x["count"])
    ranked_authors = sorted(authors.values(), key=lambda x: -x["count"])

    # ---- ALL-TIME view: no date filter — monthly histogram + per-author
    # fold from up to 2000 newest docs (author is unmapped, ES can't agg it)
    resp_all = _es("ef-bs-jira-changes", {
        "query": {"bool": {"should": should, "minimum_should_match": 1}},
        "sort": [{"created": {"order": "desc", "unmapped_type": "date"}}],
        "_source": ["created", "author"],
        "aggs": {"per_month": {"date_histogram": {"field": "created",
                                                  "calendar_interval": "month"}}},
        "track_total_hits": True, "size": 2000})
    at_hits = (resp_all.get("hits") or {}).get("hits") or []
    at_total = ((resp_all.get("hits") or {}).get("total") or {}).get("value", 0)
    at_authors: dict = {}
    for h in at_hits:
        raw = _jira_change_author((h.get("_source") or {}).get("author"))
        akey = _user_key(raw) or "(unknown)"
        slot = at_authors.setdefault(akey, {"key": _user_display(raw) or "(unknown)",
                                            "count": 0})
        slot["count"] += 1
    alltime = {"total": at_total, "sampled": len(at_hits),
               "per_month": [{"month": (b.get("key_as_string") or "")[:7],
                              "count": b.get("doc_count", 0)}
                             for b in ((resp_all.get("aggregations") or {})
                                       .get("per_month") or {}).get("buckets", [])],
               "authors": sorted(at_authors.values(),
                                 key=lambda x: -x["count"])[:15]}

    return {"total": total, "sampled": len(hits),
            "per_week": [{"week": (b.get("key_as_string") or "")[:10],
                          "count": b.get("doc_count", 0)}
                         for b in ((resp.get("aggregations") or {})
                                   .get("per_week") or {}).get("buckets", [])],
            "authors": ranked_authors[:12], "fields": rank(fields)[:12],
            "alltime": alltime, "recent": recent}


def _team_fold(authors: list[dict], inv: dict) -> list[dict]:
    """Contributor counts → the project's TEAMS, via LDAP group membership
    (same resolver + sAMAccountName↔CN matcher the approvers section uses).
    Contributors matching no team land in '(outside teams)'."""
    from . import approvers as ap
    keysets: dict = {}
    for team in dict.fromkeys(t for t in (inv.get("teams") or {}).values() if t):
        ldap = ap._ldap_lookup(team)
        keysets[team] = ap._member_keys(ldap["members"]) if ldap["found"] else set()
    counts: dict = {}
    for a in authors or []:
        k = ap._ukey(a.get("key"))
        team = next((t for t, ks in keysets.items() if k and k in ks), None)
        label = team or "(outside teams)"
        counts[label] = counts.get(label, 0) + (a.get("count") or 0)
    return sorted(({"key": t, "count": c} for t, c in counts.items()),
                  key=lambda x: -x["count"])


def _norm_branch(b) -> str:
    return re.sub(r"^refs/heads/", "", str(b or "").strip().lower())


def _git_author(s: dict) -> str:
    return _user_display((s.get("authorname") or "").strip()
                         or re.sub(r"\s*<[^>]*>\s*$", "",
                                   (s.get("commitauthor") or "")).strip())


def _sec_cicd(name: str, days: int, prev: bool = False) -> dict:
    """ef-cicd-{builds,deployments,releases} — ONE query per index gives both
    the SDLC board (latest per app / per app+env) and the raw recent rows for
    the unified event log. Test rows (testflag != Normal) are excluded from
    the BOARD but kept in the event log, flagged."""
    variants = _name_variants(name)
    tf = (settings.log_deploy_testflag or "Normal").strip()
    st_ok = (settings.log_deploy_status or "SUCCESS").strip() or "SUCCESS"
    # a "real" run: testflag matches OR the index has no testflag at all
    # (ef-cicd-releases carries none — a bare term filter would empty the
    # whole release column)
    realf = ({"bool": {"should": [
        {"term": {"testflag": tf}},
        {"bool": {"must_not": [{"exists": {"field": "testflag"}}]}}],
        "minimum_should_match": 1}} if tf else {"match_all": {}})
    # a SUCCESSFUL run — releases may carry it in RLM_STATUS instead
    okf = {"bool": {"should": [{"term": {"status": st_ok}},
                               {"term": {"RLM_STATUS": st_ok}}],
           "minimum_should_match": 1}}

    def q(index, date_field, aggs, src):
        # the window lives in post_filter: HITS (events, stage folds, totals)
        # honor the time filter while the board AGGS always see full history —
        # the SDLC board shows the latest state regardless of the window
        body = {
            "query": {"bool": {"filter": [{"terms": {"project": variants}}]}},
            "sort": [{date_field: {"order": "desc", "unmapped_type": "date"}}],
            "_source": src, "aggs": aggs, "track_total_hits": True, "size": 10000}
        if days:
            body["post_filter"] = _win(date_field, days, prev)
        return _es(index, body)

    def latest(index, date_field, src, by_env=False, ok_filter=None):
        th = {"top_hits": {"size": 1, "_source": src,
                           "sort": [{date_field: {"order": "desc",
                                                  "unmapped_type": "date"}}]}}
        # per cell: the LATEST real run + the last SUCCESSFUL one
        inner = {"latest": th, "ok": {"filter": ok_filter or okf, "aggs": {"latest": th}}}
        if by_env:
            inner = {"by_env": {"terms": {"field": "environment", "size": 12},
                                "aggs": inner}}
        return {"by_app": {"terms": {"field": "application", "size": 100},
                           "aggs": {"real": {          # board = real runs only
                               "filter": realf,
                               "aggs": inner}}}}

    b_src = ["startdate", "application", "branch", "technology", "status",
             "codeversion", "testflag", "authorname", "commitauthor",
             "commitid", "commitID", "CommitId", "commit"]
    d_src = ["startdate", "enddate", "application", "environment", "status",
             "codeversion", "testflag", "requester", "Requester", "triggeredby",
             "approver", "Approver", "technology", "reason", "Reason"]
    r_src = ["releasedate", "application", "status", "codeversion",
             "commitauthor", "RLM", "RLM_STATUS"]

    builds = q("ef-cicd-builds", "startdate", latest("ef-cicd-builds", "startdate", b_src), b_src)
    deploys = q("ef-cicd-deployments", "startdate",
                latest("ef-cicd-deployments", "startdate", d_src, by_env=True), d_src)
    # a release document IS a success by definition; RLM_STATUS only says
    # whether an ITSM ticket was opened for it
    releases = q("ef-cicd-releases", "releasedate",
                 latest("ef-cicd-releases", "releasedate", r_src,
                        ok_filter={"match_all": {}}), r_src)

    def _hit(node):
        hs = (((node or {}).get("latest") or {}).get("hits") or {}).get("hits") or []
        return (hs[0].get("_source") or {}) if hs else None

    def _bld(s):
        return {"when": (s.get("startdate") or "")[:16].replace("T", " "),
                "status": s.get("status") or "", "branch": s.get("branch") or "",
                "version": s.get("codeversion") or "",
                "tech": s.get("technology") or "", "author": _git_author(s)}

    def _rlm(s):
        """RLM_STATUS 'No error' → show the RLM ticket itself; anything else
        is shown verbatim (it means the release opened an ITSM ticket)."""
        st = (s.get("RLM_STATUS") or "").strip()
        ok = not st or st.lower() == "no error"
        return ((s.get("RLM") or "") if ok else st), ok

    def _rel(s):
        label, ok = _rlm(s)
        return {"when": (s.get("releasedate") or "")[:16].replace("T", " "),
                "status": "SUCCESS",
                "version": s.get("codeversion") or "", "rlm": s.get("RLM") or "",
                "rlm_status": s.get("RLM_STATUS") or "",
                "rlm_label": label, "rlm_ok": ok}

    def _entry(node, mk):
        """{latest-run fields, ok: last-success or None} for one cell."""
        s = _hit(node)
        if not s:
            return None
        ok = _hit(node.get("ok") or {})
        return {**mk(s), "ok": mk(ok) if ok else None}

    board: dict = {"builds": {}, "releases": {}, "deploys": {}}
    for b in ((builds.get("aggregations") or {}).get("by_app") or {}).get("buckets", []):
        e = _entry(b.get("real") or {}, _bld)
        if e:
            board["builds"][b["key"]] = e
    for b in ((releases.get("aggregations") or {}).get("by_app") or {}).get("buckets", []):
        e = _entry(b.get("real") or {}, _rel)
        if e:
            board["releases"][b["key"]] = e
    def _reason(s):
        return (s.get("reason") or s.get("Reason") or "").strip()[:160]

    def _dep(s):
        return {"when": (s.get("startdate") or s.get("enddate") or "")[:16].replace("T", " "),
                "status": s.get("status") or "",
                "version": s.get("codeversion") or "",
                "reason": _reason(s),
                "who": _user_display(s.get("requester") or s.get("Requester")
                                     or s.get("triggeredby") or "")}

    for b in ((deploys.get("aggregations") or {}).get("by_app") or {}).get("buckets", []):
        envs = {}
        for eb in (((b.get("real") or {}).get("by_env") or {}).get("buckets", [])):
            e = _entry(eb, _dep)
            if e:
                envs[eb["key"]] = e
        if envs:
            board["deploys"][b["key"]] = envs

    # ---- top active users per SDLC stage (real runs only) -----------------
    def _fold_stage(hits, who_fn):
        counts: dict = {}
        for h in hits:
            s_ = h.get("_source") or {}
            if (s_.get("testflag") or "Normal").strip().lower() != "normal":
                continue
            w = who_fn(s_)
            if not w:
                continue
            slot = counts.setdefault(_user_key(w), {"key": _user_display(w), "count": 0})
            slot["count"] += 1
        return sorted(counts.values(), key=lambda x: -x["count"])

    b_hits = (builds.get("hits") or {}).get("hits", [])
    d_hits = (deploys.get("hits") or {}).get("hits", [])
    r_hits = (releases.get("hits") or {}).get("hits", [])
    dep_who = lambda s_: (s_.get("requester") or s_.get("Requester")  # noqa: E731
                          or s_.get("triggeredby") or "")
    board["top_users"] = {
        "build": _fold_stage(b_hits, _git_author),
        # releases carry only the COMMIT author — that is not who released,
        # so the release stage shows no owner until the index says who did
        "release": [],
        "deploys": {}}
    for env in {(h.get("_source") or {}).get("environment") for h in d_hits} - {None, ""}:
        board["top_users"]["deploys"][env] = _fold_stage(
            [h for h in d_hits
             if (h.get("_source") or {}).get("environment") == env], dep_who)

    # ---- raw rows → events ------------------------------------------------
    events = []
    for h in (builds.get("hits") or {}).get("hits", []):
        s = h.get("_source") or {}
        events.append({"ts": s.get("startdate") or "", "type": "build",
                       "app": s.get("application") or "",
                       "env": "", "status": s.get("status") or "",
                       "version": s.get("codeversion") or "",
                       "who": _git_author(s),
                       "test": (s.get("testflag") or "Normal").strip().lower() != "normal",
                       "detail": " · ".join(x for x in (s.get("branch"),
                                                        s.get("technology")) if x)})
    for h in (deploys.get("hits") or {}).get("hits", []):
        s = h.get("_source") or {}
        events.append({"ts": s.get("startdate") or s.get("enddate") or "",
                       "type": "deploy", "app": s.get("application") or "",
                       "env": s.get("environment") or "",
                       "status": s.get("status") or "",
                       "version": s.get("codeversion") or "",
                       "who": _user_display(s.get("requester") or s.get("Requester")
                                            or s.get("triggeredby") or ""),
                       "test": (s.get("testflag") or "Normal").strip().lower() != "normal",
                       "reason": _reason(s),
                       "detail": " · ".join(x for x in (_reason(s), s.get("technology")) if x)})
    for h in (releases.get("hits") or {}).get("hits", []):
        s = h.get("_source") or {}
        label, rlm_ok = _rlm(s)
        events.append({"ts": s.get("releasedate") or "", "type": "release",
                       "app": s.get("application") or "", "env": "",
                       "status": "SUCCESS",
                       "version": s.get("codeversion") or "",
                       "who": "",   # commitauthor is not the releaser — no owner shown
                       "test": False,
                       "detail": label + ("" if rlm_ok else " (ITSM ticket opened)")})
    totals = {k: (((v.get("hits") or {}).get("total") or {}).get("value", 0))
              for k, v in (("builds", builds), ("deploys", deploys),
                           ("releases", releases))}
    reasons = {"all": {}, "prd": {}, "failed": {}}
    for h in d_hits:
        s_ = h.get("_source") or {}
        if (s_.get("testflag") or "Normal").strip().lower() != "normal":
            continue
        rs = _reason(s_) or "(no reason given)"
        reasons["all"][rs] = reasons["all"].get(rs, 0) + 1
        if _PRD.search(s_.get("environment") or ""):
            reasons["prd"][rs] = reasons["prd"].get(rs, 0) + 1
        if _BAD.search(s_.get("status") or ""):
            reasons["failed"][rs] = reasons["failed"].get(rs, 0) + 1
    reasons_out = {k: sorted(({"key": r, "count": n} for r, n in v.items()),
                             key=lambda x: -x["count"])[:12] for k, v in reasons.items()}
    # (commit id, branch) → built version. Branch matters: a release branch
    # builds the SAME commit under a different versioning convention than
    # develop, so the id alone would attach the wrong version.
    build_versions: dict = {}
    for h in b_hits:
        s_ = h.get("_source") or {}
        cid = str(s_.get("commitid") or s_.get("commitID") or s_.get("CommitId")
                  or s_.get("commit") or "").strip()
        ver = s_.get("codeversion") or ""
        br = _norm_branch(s_.get("branch"))
        if cid and ver:
            build_versions.setdefault(f"{cid}|{br}", ver)
    return {"board": board, "events": events, "totals": totals,
            "build_versions": build_versions, "reasons": reasons_out}


# ---------------------------------------------------------------- auto tests
def _sec_autotest(name: str, days: int) -> dict:
    """ef-autotest — one row per automated test run (date, duration,
    environment, requester, technology(text), company(text)). No status
    field exists, so this is a run/coverage view, not a pass/fail one."""
    variants = _name_variants(name)
    body = {
        "query": {"bool": {"filter": [{"terms": {"project": variants}}]
                  + ([] if not days else [{"range": {"date": {"gte": f"now-{days}d"}}}])}},
        "sort": [{"date": {"order": "desc", "unmapped_type": "date"}}],
        "_source": ["date", "duration", "environment", "requester", "technology"],
        "aggs": {
            "by_env": {"terms": {"field": "environment", "size": 12, "missing": "(none)"}},
            "by_requester": {"terms": {"field": "requester", "size": 15, "missing": "(unknown)"}},
            "per_period": {"date_histogram": {"field": "date",
                                              "calendar_interval": "day" if days else "month"}},
            "dur": {"stats": {"field": "duration"}},
        },
        "track_total_hits": True, "size": 1000,
    }
    resp = _es("ef-autotest", body)
    hits = (resp.get("hits") or {}).get("hits") or []
    total = ((resp.get("hits") or {}).get("total") or {}).get("value", 0)
    aggs = resp.get("aggregations") or {}

    def _b(node):
        return [{"key": b.get("key"), "count": b.get("doc_count", 0)}
                for b in (node or {}).get("buckets", [])]
    tech: dict = {}
    runs = []
    for h in hits:
        src = h.get("_source") or {}
        t = (src.get("technology") or "").strip() or "(unknown)"
        tech[t] = tech.get(t, 0) + 1
        runs.append({"when": (src.get("date") or "")[:16].replace("T", " "),
                     "duration": _int(src.get("duration")),
                     "env": src.get("environment") or "",
                     "requester": _user_display(src.get("requester") or ""),
                     "technology": t})
    dur = aggs.get("dur") or {}
    return {"total": total, "sampled": len(hits),
            "by_env": _b(aggs.get("by_env")),
            "by_requester": _fold_users(_b(aggs.get("by_requester"))),
            "by_technology": sorted(({"key": k, "count": v} for k, v in tech.items()),
                                    key=lambda x: -x["count"]),
            "per_period": [{"day": (b.get("key_as_string") or "")[:10],
                            "count": b.get("doc_count", 0)}
                           for b in (aggs.get("per_period") or {}).get("buckets", [])],
            "duration": {"avg": round(dur.get("avg") or 0, 1),
                         "max": _int(dur.get("max")), "sum": _int(dur.get("sum"))},
            "runs": runs}


# ---------------------------------------------------------------- platform usage
_USAGE_MIN = ("buildminutes", "deployminutes", "fortifyminutes", "prismacloudminutes",
              "qualitytestingminutes", "sonarqubeminutes", "standardchangeminutes")


def _sec_usage(name: str, days: int) -> dict:
    """ef-devops-usage — DevOps platform consumption per (application,
    repository, team) row: minutes per activity + storage snapshots.
    Minutes SUM over the window; storage is a snapshot, so the LATEST row per
    application is taken and summed across applications."""
    variants = _name_variants(name)
    rng = [] if not days else [{"range": {"startdate": {"gte": f"now-{days}d"}}}]
    sums = {f: {"sum": {"field": f}} for f in (*_USAGE_MIN, "totalminutes")}
    body = {
        "query": {"bool": {"filter": [{"terms": {"project": variants}}] + rng}},
        "size": 0, "track_total_hits": True,
        "aggs": {
            **sums,
            "by_app": {"terms": {"field": "application", "size": 100, "missing": "(none)"},
                       "aggs": {"minutes": {"sum": {"field": "totalminutes"}},
                                **{f: {"sum": {"field": f}} for f in _USAGE_MIN},
                                "latest": {"top_hits": {"size": 1,
                                    "_source": ["totalstorage", "gitstorage", "elkstorage",
                                                "startdate", "repository", "team"],
                                    "sort": [{"startdate": {"order": "desc",
                                                            "unmapped_type": "date"}}]}}}},
            "by_team": {"terms": {"field": "team", "size": 20, "missing": "(none)"},
                        "aggs": {"minutes": {"sum": {"field": "totalminutes"}}}},
            "per_period": {"date_histogram": {"field": "startdate",
                                              "calendar_interval": "day" if days else "month"},
                           "aggs": {"minutes": {"sum": {"field": "totalminutes"}}}},
        },
    }
    resp = _es("ef-devops-usage", body)
    aggs = resp.get("aggregations") or {}
    v = lambda node: _int((node or {}).get("value"))  # noqa: E731
    by_activity = {f.replace("minutes", ""): v(aggs.get(f)) for f in _USAGE_MIN}
    apps = []
    stor_total = stor_git = stor_elk = 0
    for b in (aggs.get("by_app") or {}).get("buckets", []):
        hs = (((b.get("latest") or {}).get("hits") or {}).get("hits") or [{}])
        src = (hs[0].get("_source") or {}) if hs else {}
        st, sg, se = _int(src.get("totalstorage")), _int(src.get("gitstorage")), _int(src.get("elkstorage"))
        stor_total += st
        stor_git += sg
        stor_elk += se
        apps.append({"app": b.get("key"), "minutes": v(b.get("minutes")),
                     "by_activity": {f.replace("minutes", ""): v(b.get(f)) for f in _USAGE_MIN},
                     "storage": st, "git": sg, "elk": se,
                     "repository": src.get("repository") or "",
                     "team": src.get("team") or "",
                     "snapshot": (src.get("startdate") or "")[:10]})
    apps.sort(key=lambda a: -a["minutes"])
    out = {"rows": ((resp.get("hits") or {}).get("total") or {}).get("value", 0),
           "total_minutes": v(aggs.get("totalminutes")),
           "by_activity": by_activity, "apps": apps,
           "teams": sorted(({"key": b.get("key"), "count": v(b.get("minutes"))}
                            for b in (aggs.get("by_team") or {}).get("buckets", [])),
                           key=lambda x: -x["count"]),
           "per_period": [{"day": (b.get("key_as_string") or "")[:10],
                           "count": v(b.get("minutes"))}
                          for b in (aggs.get("per_period") or {}).get("buckets", [])],
           "storage": {"total": stor_total, "git": stor_git, "elk": stor_elk}}
    if days:   # previous window for the delta
        try:
            prev = _es("ef-devops-usage", {
                "query": {"bool": {"filter": [{"terms": {"project": variants}},
                                              {"range": {"startdate": {"gte": f"now-{2 * days}d",
                                                                       "lt": f"now-{days}d"}}}]}},
                "size": 0, "aggs": {"m": {"sum": {"field": "totalminutes"}}}})
            out["prev_total_minutes"] = v((prev.get("aggregations") or {}).get("m"))
        except Exception:  # noqa: BLE001
            pass
    return out


# ---------------------------------------------------------------- members
def _sec_members(inv: dict, out: dict) -> dict:
    """Who is actually active on this project vs. who the involved teams'
    LDAP groups say should be. Every identity seen in the window (commits,
    Jira assignees/finishers/changes, cicd stage users, test requesters) is
    classified: in one of the project's teams · active but in ANOTHER
    inventory team · in LDAP but in no team group · not in LDAP at all."""
    from . import approvers as ap, inventory
    from ..auth import ldap_user_exists
    teams = {}
    for role, t in (inv.get("teams") or {}).items():
        if t:
            teams.setdefault(t, []).append(role)
    rosters: dict = {}
    for t in teams:
        l = ap._ldap_lookup(t)
        rosters[t] = {"found": l["found"], "group": l["group"], "members": l["members"],
                      "keys": ap._member_keys(l["members"]) if l["found"] else set()}
    # ---- active identities in the window --------------------------------
    active: dict = {}

    def seen(name, src, n=1):
        disp = _user_display(name)
        if not disp or disp.startswith("("):
            return
        k = ap._ukey(disp)
        slot = active.setdefault(k, {"key": disp, "activity": 0, "sources": set()})
        slot["activity"] += n or 1
        slot["sources"].add(src)
    for a in (out.get("commits") or {}).get("authors") or []:
        seen(a["key"], "commits", a.get("count"))
    j = out.get("jira") or {}
    for a in j.get("by_assignee") or []:
        seen(a["key"], "jira", a.get("count"))
    for a in (j.get("done") or {}).get("by_assignee") or []:
        seen(a["key"], "jira", a.get("count"))
    for a in (out.get("jira_changes") or {}).get("authors") or []:
        seen(a["key"], "jira", a.get("count"))
    tu = ((out.get("cicd") or {}).get("board") or {}).get("top_users") or {}
    for lst in (tu.get("build") or [], tu.get("release") or [],
                *(tu.get("deploys") or {}).values()):
        for u in lst:
            seen(u["key"], "cicd", u.get("count"))
    for a in (out.get("autotest") or {}).get("by_requester") or []:
        seen(a["key"], "tests", a.get("count"))
    # ---- other inventory teams (for "active elsewhere") ------------------
    others: dict = {}
    try:
        for p in inventory.parse().get("projects") or []:
            for t in (p.get("teams") or {}).values():
                if t and t not in teams and t not in others and len(others) < 30:
                    l = ap._ldap_lookup(t)
                    others[t] = ap._member_keys(l["members"]) if l["found"] else set()
    except Exception:  # noqa: BLE001
        pass
    # ---- one person, one row: identities that resolve to the SAME roster
    # member ("alice" from commits, "Alice Nasr" from Jira) merge under the
    # member's display name; unmatched identities keep their own spelling
    member_index: list = []
    for r in rosters.values():
        for m in r["members"]:
            member_index.append((m.get("display_name") or m.get("username") or "", ap._member_keys([m])))
    merged: dict = {}
    for k, a in list(active.items()):
        canon = next((nm for nm, mk in member_index if k in mk), None)
        ck = ap._ukey(canon) if canon else k
        slot = merged.setdefault(ck, {"key": canon or a["key"], "activity": 0, "sources": set(),
                                      "aliases": set()})
        slot["activity"] += a["activity"]
        slot["sources"] |= a["sources"]
        if a["key"] != slot["key"]:
            slot["aliases"].add(a["key"])
    active = merged
    contributors = []
    for k, a in active.items():
        in_teams = [t for t, r in rosters.items() if k in r["keys"]]
        row = {"key": a["key"], "activity": a["activity"],
               "sources": sorted(a["sources"]), "aliases": sorted(a.get("aliases") or [])}
        if in_teams:
            row.update(status="team", team=in_teams[0])
        else:
            elsewhere = next((t for t, keys in others.items() if k in keys), None)
            if elsewhere:
                row.update(status="elsewhere", team=elsewhere)
            else:
                ex = ldap_user_exists(a["key"])
                row.update(status="ldap_only" if ex else "not_in_ldap" if ex is False else "unknown")
        contributors.append(row)
    contributors.sort(key=lambda r: ({"team": 0, "elsewhere": 1, "ldap_only": 2,
                                      "not_in_ldap": 3, "unknown": 4}[r["status"]], -r["activity"]))
    # ---- rosters with activity flags --------------------------------------
    teams_out = []
    for t, r in rosters.items():
        mem = []
        for m in r["members"]:
            mk = ap._member_keys([m])
            hit = next((a for k, a in active.items() if k in mk), None)
            mem.append({"name": m.get("display_name") or m.get("username") or "",
                        "username": m.get("username") or "",
                        "active": bool(hit), "activity": hit["activity"] if hit else 0})
        mem.sort(key=lambda x: (-x["activity"], x["name"]))
        teams_out.append({"team": t, "roles": teams[t], "group": r["group"],
                          "found": r["found"], "members": mem,
                          "active": sum(1 for m in mem if m["active"])})
    counts = {st: sum(1 for c in contributors if c["status"] == st)
              for st in ("team", "elsewhere", "ldap_only", "not_in_ldap", "unknown")}
    # every spelling the page may show → status, so any user chip anywhere
    # can carry the LDAP dot: contributor keys + aliases, and every roster
    # member's login/CN-derived patterns (first name, f.last, …)
    lookup: dict = {}
    for c in contributors:
        for sp in [c["key"], *c.get("aliases", [])]:
            lookup[ap._ukey(sp)] = {"status": c["status"], "team": c.get("team")}
    for t, r in rosters.items():
        for m in r["members"]:
            for k in ap._member_keys([m]):
                lookup.setdefault(k, {"status": "team", "team": t})
    lookup.pop("", None)
    return {"teams": teams_out, "contributors": contributors, "summary": counts,
            "lookup": lookup}


# ---------------------------------------------------------------- catalog
_CAT_CACHE: dict = {"at": 0.0, "payload": None}
_CAT_TTL = 300
# (index, date field) → ONE size-0 terms+max query each; no documents move
# key, index, date field, then how the LATEST doc is summarised for the
# catalog card: (_source fields, app field, who field, extra field)
_CAT_SOURCES = (("commits", "ef-git-commits", "commitdate",
                 ("repository", "authorname", "branch"), "repository", "authorname", "branch"),
                ("builds", "ef-cicd-builds", "startdate",
                 ("application", "requester", "codeversion", "status"), "application", "requester", "codeversion"),
                ("deploys", "ef-cicd-deployments", "startdate",
                 ("application", "requester", "environment", "status"), "application", "requester", "environment"),
                ("releases", "ef-cicd-releases", "releasedate",
                 ("application", "codeversion", "RLM"), "application", "", "codeversion"),
                ("tests", "ef-autotest", "date",
                 ("technology", "requester", "environment"), "technology", "requester", "environment"))


def _cat_stdchanges(out: dict, errors: list[str]) -> None:
    """Standard-change activity for the catalog. One RUN = a unique
    (service, environment, ChangeNumber) combination — the index holds one
    document per input parameter set of a run, so document counts lie.
    Environment / ChangeNumber are text (not aggregatable): the last-30-days
    window is fetched (tiny _source, search_after pages) and folded in
    Python; the all-time `last` + latest requester come from one terms agg."""
    try:
        from . import stdchanges
        job2proj: dict = {}
        for c in stdchanges.catalog_all().get("changes") or []:
            pn = (c.get("vars") or {}).get("project_name")
            if not pn:
                continue
            for k in (c["name"], *(sc["name"] for sc in c.get("scripts") or [])):
                job2proj[k] = _norm(pn)
        if not job2proj:
            return
        # all-time last + latest doc info (cheap agg)
        resp = _es("ef-ops-db-changes-standard", {"size": 0, "aggs": {"by": {
            "terms": {"field": "JobName", "size": 2000},
            "aggs": {"last": {"max": {"field": "Date"}},
                     "latest": {"top_hits": {"size": 1, "_source": ["Requester", "Environment", "ChangeNumber"],
                                             "sort": [{"Date": {"order": "desc", "unmapped_type": "date"}}]}}}}}})
        for b in ((resp.get("aggregations") or {}).get("by") or {}).get("buckets", []):
            pk = job2proj.get(b.get("key"))
            if not pk:
                continue
            last = ((b.get("last") or {}).get("value_as_string") or "")[:19]
            hit = (((b.get("latest") or {}).get("hits") or {}).get("hits") or [{}])[0]
            hs = hit.get("_source") or {}
            doc = {"app": str(b.get("key") or ""), "who": _user_display(hs.get("Requester") or ""),
                   "extra": str(hs.get("Environment") or hs.get("ChangeNumber") or "")} if hs else None
            slot = out.setdefault(pk, {})
            prev = slot.get("stdchanges")
            if not prev or last > prev["last"]:
                slot["stdchanges"] = {"last": last, "recent": 0, "hist": [0] * 30,
                                      "last_doc": doc or (prev or {}).get("last_doc")}
        # 30-day window → fold documents into RUNS (job, env, change number)
        body = {"query": {"bool": {"filter": [
                    {"terms": {"JobName": sorted(job2proj)}},
                    {"range": {"Date": {"gte": "now-30d"}}}]}},
                "sort": [{"Date": {"order": "asc", "unmapped_type": "date"}}, {"_doc": {"order": "asc"}}],
                "_source": ["JobName", "Environment", "ChangeNumber", "Date"],
                "size": _STD_PAGE}
        runs: dict = {}
        for _page in range(20):   # 100k docs sanity ceiling for a 30d window
            resp = _es("ef-ops-db-changes-standard", body)
            hits = (resp.get("hits") or {}).get("hits") or []
            for h in hits:
                s_ = h.get("_source") or {}
                job = s_.get("JobName") or ""
                key = (job, (s_.get("Environment") or "").strip().lower(),
                       str(s_.get("ChangeNumber") or "").strip() or f"doc:{h.get('_id')}")
                when = (s_.get("Date") or "")[:10]
                if key not in runs or when < runs[key]:
                    runs[key] = when                 # a run is dated by its FIRST document
            if len(hits) < _STD_PAGE:
                break
            body["search_after"] = hits[-1].get("sort")
        idx = {d: i for i, d in enumerate(_cat_days())}
        for (job, _env, _cn), day in runs.items():
            pk = job2proj.get(job)
            if not pk:
                continue
            slot = out.setdefault(pk, {}).setdefault("stdchanges", {"last": "", "recent": 0, "hist": [0] * 30, "last_doc": None})
            slot["recent"] += 1
            di = idx.get(day)
            if di is not None:
                slot["hist"][di] += 1
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ef-ops-db-changes-standard: {str(exc)[:80]}")


def _cat_activity() -> tuple[dict, list[str]]:
    """{norm(project): {source: {"last": iso, "recent": n}}} from tiny
    aggregation queries (terms on project · max date · 30-day count) — every
    event source of the report contributes to a project's last activity."""
    out: dict = {}
    errors: list[str] = []
    _cat_stdchanges(out, errors)
    for key, index, field, src, appf, whof, extraf in _CAT_SOURCES:
        try:
            resp = _es(index, {"size": 0, "aggs": {"by": {
                "terms": {"field": "project", "size": 1000},
                "aggs": {"last": {"max": {"field": field}},
                         "latest": {"top_hits": {"size": 1, "_source": list(src),
                                                 "sort": [{field: {"order": "desc", "unmapped_type": "date"}}]}},
                         "recent": {"filter": {"range": {field: {"gte": "now-30d"}}},
                                    "aggs": {"days": {"date_histogram": {"field": field, "calendar_interval": "day"}}}}}}}})
        except Exception as exc:  # noqa: BLE001 — one dead index hides only itself
            errors.append(f"{index}: {str(exc)[:80]}")
            continue
        for b in ((resp.get("aggregations") or {}).get("by") or {}).get("buckets", []):
            k = _norm(b.get("key"))
            last = (b.get("last") or {}).get("value_as_string") or ""
            slot = out.setdefault(k, {})
            prev = slot.get(key)
            hist = _hist30(b.get("recent") or {})
            hit = (((b.get("latest") or {}).get("hits") or {}).get("hits") or [{}])[0]
            hs = hit.get("_source") or {}
            doc = {"app": str(hs.get(appf) or ""), "who": _user_display(hs.get(whof) or "") if whof else "",
                   "extra": str(hs.get(extraf) or "")} if hs else None
            if not prev or last > prev["last"]:
                slot[key] = {"last": last[:19], "recent": (b.get("recent") or {}).get("doc_count", 0)
                             + (prev["recent"] if prev else 0),
                             "hist": [a + c for a, c in zip(hist, prev["hist"])] if prev else hist,
                             "last_doc": doc or (prev or {}).get("last_doc")}
            else:
                prev["recent"] += (b.get("recent") or {}).get("doc_count", 0)
                prev["hist"] = [a + c for a, c in zip(prev["hist"], hist)]
    return out, errors


def _cat_days() -> list[str]:
    """The 30 calendar days (UTC) the catalog histograms are aligned to, oldest first."""
    today = _now().date()
    return [(today - dt.timedelta(days=29 - i)).isoformat() for i in range(30)]


def _hist30(recent_agg: dict) -> list[int]:
    """date_histogram buckets → 30 aligned daily counts (missing days = 0)."""
    idx = {d: i for i, d in enumerate(_cat_days())}
    out = [0] * 30
    for b in (recent_agg.get("days") or {}).get("buckets", []):
        i = idx.get((b.get("key_as_string") or "")[:10])
        if i is not None:
            out[i] += b.get("doc_count", 0)
    return out


def _cat_extras() -> tuple[dict, list[str]]:
    """Per-project lightweight facts beyond the activity pulse — every source
    is either an already-cached analysis (ADO projects, Engine std-change
    catalogue, logging health) or ONE size-0 aggregation per index:
      security  latest scan per scanner per project (top_hits 1) → crit/high
      deploys   prd deployments in 30d: ok / failed
      usage     latest platform-usage snapshot (minutes / storage)
    {norm(project): {...}}; errors list the sources that were unavailable."""
    out: dict = {}
    errors: list[str] = []
    slot = lambda k: out.setdefault(k, {})  # noqa: E731
    # ADO description / grade
    try:
        from . import access
        for x in access.ado_projects().get("projects") or []:
            row = {"description": (x.get("description") or "").strip(), "url": x.get("url"),
                   "repos": x.get("repos") if isinstance(x.get("repos"), int) else len(x.get("repos") or []),
                   "grade": x.get("grade"), "score": x.get("score"), "collection": x.get("coll")}
            cur = slot(_norm(x.get("name"))).get("ado")
            # the same project name may exist in several collections — keep the
            # first one that has a description (the report's _sec_ado picks the same)
            if cur is None or (not cur.get("description") and row["description"]):
                slot(_norm(x.get("name")))["ado"] = row
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ado: {str(exc)[:80]}")
    # standard changes (Engine catalogue, parsed from the clone — no ES)
    try:
        from . import stdchanges
        for c in stdchanges.catalog_all().get("changes") or []:
            pn = _norm((c.get("vars") or {}).get("project_name"))
            if pn:
                st = slot(pn).setdefault("std", {"changes": 0, "issues": 0})
                st["changes"] += 1
                st["issues"] += len(c.get("issues") or [])
    except Exception as exc:  # noqa: BLE001
        errors.append(f"std changes: {str(exc)[:80]}")
    # logging health (cached analysis shared with the Logging page)
    try:
        from . import logstats
        for x in logstats.analyze().get("projects") or []:
            t = x.get("totals") or {}
            apps = x.get("apps") or []
            slot(_norm(x.get("name"))).update(logging={
                "score": x.get("score"), "size_h": t.get("size_h"), "indices": t.get("indices"),
                "silent": sum(1 for a in apps if any(e.get("no_logs") for e in a.get("env_stats") or []))})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"logging: {str(exc)[:80]}")
    # configurations coverage (Configurations page overview, cached 5 min)
    try:
        from . import archconfig
        for row in archconfig.overview().get("projects") or []:
            st = row.get("states") or {}
            slot(_norm(row.get("name"))).update(configs={
                "coverage": row.get("coverage"), "missing": st.get("missing", 0) + st.get("unparseable", 0),
                "stale": st.get("stale", 0), "duplicates": row.get("duplicates", 0),
                "extra": row.get("extra", 0), "issues": row.get("issues", 0),
                "cross": (row.get("edges") or {}).get("cross-project", 0)})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"configs: {str(exc)[:80]}")
    # security: freshest scan per project per scanner
    for key, cfg in _SCANNERS.items():
        sev = [f for f in cfg["sev"][:2] if f]
        try:
            resp = _es(cfg["index"], {"size": 0, "aggs": {"by": {
                "terms": {"field": "project", "size": 1000},
                "aggs": {"latest": {"top_hits": {"size": 1, "_source": [*sev, "enddate", "status"],
                                                 "sort": [{"enddate": {"order": "desc", "unmapped_type": "date"}}]}},
                         "recent": {"filter": {"range": {"enddate": {"gte": "now-90d"}}}}}}}})
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{cfg['index']}: {str(exc)[:80]}")
            continue
        for b in ((resp.get("aggregations") or {}).get("by") or {}).get("buckets", []):
            hit = (((b.get("latest") or {}).get("hits") or {}).get("hits") or [{}])[0]
            src = hit.get("_source") or {}
            sec = slot(_norm(b.get("key"))).setdefault("security", {"scanners": {}, "critical": 0, "high": 0, "last": ""})
            crit = _int(src.get(cfg["sev"][0])) if cfg["sev"][0] else 0
            high = _int(src.get(cfg["sev"][1])) if cfg["sev"][1] else 0
            when = (src.get("enddate") or "")[:10]
            sec["scanners"][key] = {"when": when, "critical": crit, "high": high,
                                    "recent": (b.get("recent") or {}).get("doc_count", 0)}
            sec["critical"] += crit
            sec["high"] += high
            sec["last"] = max(sec["last"], when)
    # prd deployments — 30 days, ok vs failed (real runs only)
    try:
        resp = _es("ef-cicd-deployments", {"size": 0, "query": {"bool": {"filter": [
            {"range": {"startdate": {"gte": "now-30d"}}}],
            "must_not": [{"term": {"testflag": True}}]}},
            "aggs": {"by": {"terms": {"field": "project", "size": 1000}, "aggs": {
                "prd": {"filter": {"bool": {"should": [{"term": {"environment": e}} for e in ("prd", "prod", "production", "PRD", "PROD")],
                                            "minimum_should_match": 1}},
                        "aggs": {"ok": {"filter": {"term": {"status": "SUCCESS"}}}}}}}}})
        for b in ((resp.get("aggregations") or {}).get("by") or {}).get("buckets", []):
            prd = b.get("prd") or {}
            n = prd.get("doc_count", 0)
            slot(_norm(b.get("key"))).update(deploys={"total": b.get("doc_count", 0), "prd": n,
                                                     "prd_ok": (prd.get("ok") or {}).get("doc_count", 0)})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ef-cicd-deployments: {str(exc)[:80]}")
    # platform usage — latest snapshot
    try:
        resp = _es("ef-devops-usage", {"size": 0, "aggs": {"by": {
            "terms": {"field": "project", "size": 1000},
            "aggs": {"latest": {"top_hits": {"size": 1, "_source": ["totalminutes", "totalstorage", "enddate"],
                                             "sort": [{"enddate": {"order": "desc", "unmapped_type": "date"}}]}}}}}})
        for b in ((resp.get("aggregations") or {}).get("by") or {}).get("buckets", []):
            hit = (((b.get("latest") or {}).get("hits") or {}).get("hits") or [{}])[0]
            src = hit.get("_source") or {}
            slot(_norm(b.get("key"))).update(usage={"minutes": _int(src.get("totalminutes")),
                                                    "storage": _int(src.get("totalstorage")),
                                                    "as_of": (src.get("enddate") or "")[:10]})
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ef-devops-usage: {str(exc)[:80]}")
    return out, errors


def _demo_cat_extras(inv: dict) -> dict:
    from . import access, stdchanges, logstats
    out, _ = {}, None
    try:
        out, _ = _cat_extras_from_cached(inv)
    except Exception:  # noqa: BLE001
        out = {}
    for i, p in enumerate(inv.get("projects") or []):
        k = _norm(p["name"])
        d = out.setdefault(k, {})
        d.setdefault("security", {"scanners": {"prismacloud": {"when": "2026-08-27", "critical": i, "high": 3 + i, "recent": 6},
                                               "invicti": {"when": "2026-08-20", "critical": 0, "high": i % 2, "recent": 2}},
                                  "critical": i, "high": 3 + i + i % 2, "last": "2026-08-27"} if i < 3 else None)
        if d["security"] is None:
            d.pop("security")
        d.setdefault("deploys", {"total": 24 - i * 6, "prd": 6 - i, "prd_ok": 5 - i} if i < 4 else None)
        if d["deploys"] is None:
            d.pop("deploys")
        d.setdefault("usage", {"minutes": 4200 - i * 900, "storage": (38 - i * 7) * 1024 ** 3, "as_of": "2026-08-28"})
    return out


def _cat_extras_from_cached(inv: dict) -> tuple[dict, list[str]]:
    """Demo mode: the cached-analysis parts of _cat_extras (ADO, std changes,
    logging) work against demo data too — only the ES aggregations are faked."""
    real_es = globals()["_es"]
    def _no_es(index, body):
        raise RuntimeError("demo")
    globals()["_es"] = _no_es
    try:
        return _cat_extras()
    finally:
        globals()["_es"] = real_es


def catalog(refresh: bool = False) -> dict:
    """The project landing: every inventory project with its facets and a
    LIGHTWEIGHT activity pulse — no per-project report is built."""
    if not refresh and _CAT_CACHE["payload"] and time.time() - _CAT_CACHE["at"] < _CAT_TTL:
        return {**_CAT_CACHE["payload"], "cached": True}
    from . import inventory
    inv = inventory.parse()
    if settings.demo_mode:
        act, errors = _demo_cat_activity(inv), []
        extras = _demo_cat_extras(inv)
    else:
        act, errors = _cat_activity()
        extras, more = _cat_extras()
        errors += more
    projects = []
    for p in inv.get("projects") or []:
        pv = ((p.get("config") or {}).get("project_vars") or {})
        a = act.get(_norm(p["name"]), {})
        x = extras.get(_norm(p["name"]), {})
        last = max(((v["last"], k) for k, v in a.items() if v.get("last")), default=("", ""))
        projects.append({
            "name": p["name"], "company": pv.get("company") or "",
            "teams": {"dev": p.get("dev_team"), "qc": p.get("qc_team"), "prd": p.get("prd_team")},
            "deploy_platform": pv.get("deploy_platform") or "",
            "deploy_technology": pv.get("deploy_technology") or "",
            "apps": p.get("app_count") or len(p.get("apps") or []),
            "envs": p.get("envs") or [], "pipelines": p.get("pipeline_count") or 0,
            "activity": a,
            "last_activity": last[0], "last_source": last[1],
            "recent_30d": sum(v.get("recent", 0) for v in a.values()),
            "description": ((x.get("ado") or {}).get("description") or pv.get("description") or ""),
            "ado": x.get("ado"), "std": x.get("std"), "logging": x.get("logging"), "configs": x.get("configs"),
            "security": x.get("security"), "deploys": x.get("deploys"), "usage": x.get("usage"),
        })
    projects.sort(key=lambda x: x["last_activity"], reverse=True)
    payload = {"source": inv.get("source"), "projects": projects, "errors": errors, "days": _cat_days(),
               "generated_at": _now().replace(microsecond=0).isoformat() + "Z"}
    _CAT_CACHE.update(at=time.time(), payload=payload)
    return {**payload, "cached": False}


def _demo_cat_activity(inv: dict) -> dict:
    now = _now()
    out = {}
    for i, p in enumerate(inv.get("projects") or []):
        k = _norm(p["name"])
        ages = {"commits": 1 + i * 9, "builds": 2 + i * 12, "deploys": 3 + i * 20,
                "releases": 6 + i * 30, "tests": 1 + i * 15, "stdchanges": 0.5 + i * 40}
        out[k] = {}
        for j, (src, h) in enumerate(ages.items()):
            recent = max(0, 40 - i * 15 - j * 4)
            # a plausible daily shape: busier on weekdays, most recent days heaviest, some quiet days
            weights = [0 if (d + i + j) % 7 in (5, 6) else (1 + ((d * 7 + j * 3 + i) % 5)) * (1 + d / 30) for d in range(30)]
            tot = sum(weights) or 1
            hist = [int(round(recent * w / tot)) for w in weights]
            demo_docs = {"commits": ("payments-svc", "alice", "develop"), "builds": ("payments", "bob", "1.20.0"),
                         "deploys": ("payments", "carol", "prd"), "releases": ("payments", "", "1.19.0"),
                         "tests": ("selenium", "dave", "uat"), "stdchanges": ("Finance_AddBranch", "carol", "prd")}
            da, dw, dx = demo_docs.get(src, ("", "", ""))
            out[k][src] = {"last": (now - dt.timedelta(hours=h)).replace(microsecond=0).isoformat(),
                           "recent": sum(hist), "hist": hist,
                           "last_doc": {"app": da, "who": dw, "extra": dx}}
    return out


# ---------------------------------------------------------------- configurations (Control repos — reuses the cached Configurations analysis)
def _sec_configs(name: str) -> dict:
    """This project's row from the Configurations overview — coverage,
    per-env states, duplicates, extras, cross-project targets. Reuses the
    5-minute cached archconfig analysis; no repo scan happens here."""
    from . import archconfig
    o = archconfig.overview()
    row = next((p for p in o.get("projects") or [] if _norm(p.get("name")) == _norm(name)), None)
    if row is None:
        unknown = name in (o.get("unknown_projects") or [])
        return {"found": False, "repos_defined": bool(o.get("repos")),
                "note": "project folder exists in the Control repos but not in the inventory"
                        if unknown else "no Control team config repos cloned"
                        if not o.get("repos") else "no configs and no inventory row for this project"}
    keep = ("coverage", "expected", "present", "states", "per_env", "extra", "extra_items",
            "duplicates", "duplicate_items", "issues", "edges", "cross_targets",
            "config_teams", "last_change")
    return {"found": True, "deployments_ok": (o.get("deployments") or {}).get("available", False),
            **{k: row.get(k) for k in keep}}


# ---------------------------------------------------------------- pipeline configuration (git history of inventories / ocp-templates)
_PCFG_REPOS = ("inventories", "ocp-templates")
_PCFG_MAX = 5000
_ENV_SEG = re.compile(r"^(dev|qc|uat|prd|prod|production|test|stg|staging)(?:[_\-]|$)", re.I)


def _pcfg_project_paths(root, name: str) -> list[str]:
    """Folders (up to two levels deep) whose name is this project — both repos
    are structured per project, but ocp-templates may nest them one level down."""
    import subprocess
    try:
        p = subprocess.run(["git", "ls-files", "-z"], cwd=str(root), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    want = _norm(name)
    paths: set = set()
    for f in p.stdout.split("\0"):
        segs = f.split("/")
        for depth in (1, 2):
            if len(segs) > depth and _norm(segs[depth - 1]) == want:
                paths.add("/".join(segs[:depth]))
                break
    return sorted(paths)


def _pcfg_log(root, paths: list[str], days: int) -> list[dict]:
    """Commits touching `paths`, newest first, with author + changed files —
    read from the clone's history (never from ef-git-commits)."""
    import subprocess
    cmd = ["git", "log", f"--max-count={_PCFG_MAX}", "--format=%x01%H|%an|%ae|%cI|%s", "--name-only", "--no-renames"]
    if days:
        cmd.append(f"--since={days} days ago")
    cmd += ["--", *paths]
    try:
        p = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    out: list = []
    cur = None
    for line in p.stdout.splitlines():
        if line.startswith("\x01"):
            sha, an, ae, when, subj = (line[1:].split("|", 4) + ["", "", "", "", ""])[:5]
            cur = {"sha": sha[:10], "sha_full": sha, "who": _user_display(an or ae), "email": ae,
                   "when": when[:16].replace("T", " "), "subject": subj, "files": []}
            out.append(cur)
        elif line.strip() and cur is not None:
            f = line.strip()
            if any(f == pp or f.startswith(pp + "/") for pp in paths):
                cur["files"].append(f)
    return [c for c in out if c["files"]]


def _pcfg_envs(files: list[str]) -> list[str]:
    envs: set = set()
    for f in files:
        for seg in f.split("/")[1:]:
            m = _ENV_SEG.match(seg)
            if m:
                envs.add(m.group(1).lower())
    return sorted(envs, key=lambda e: ({"dev": 1, "qc": 2, "uat": 3, "prd": 4}.get(e, 9), e))


def _sec_configcommits(name: str, days: int) -> dict:
    """Edits to this project's folder in the CONTROL team config repos —
    from the clones' git history (authors + commit messages), for the event
    log's 'config change' tab."""
    from . import archconfig
    commits: list = []
    repos_out: list = []
    for r in archconfig.control_repos():
        from . import repos as repos_mod
        root = repos_mod._dir_for(r)
        row = {"name": r.get("name"), "cloned": root.exists(), "commits": 0}
        if root.exists():
            paths = _pcfg_project_paths(root, name)
            if paths:
                for c in _pcfg_log(root, paths, days):
                    c["repo"] = r.get("name")
                    c["envs"] = _pcfg_envs(c["files"])
                    commits.append(c)
                    row["commits"] += 1
        repos_out.append(row)
    commits.sort(key=lambda c: c["when"], reverse=True)
    return {"repos": repos_out, "commits": len(commits), "recent": commits[:_PCFG_MAX]}


def _sec_pipelinecfg(name: str, days: int) -> dict:
    """Edits to this project's folder in the cloned `inventories` and
    `ocp-templates` repos = pipeline configuration changes (Ansible
    inventories, group/host vars, OpenShift templates). Detected from the
    clones' git history — authors, files, and env hints from path segments."""
    from . import repos as repos_mod
    cfg = {(r.get("name") or "").lower(): r for r in repos_mod.configured()}
    out_repos: list = []
    commits: list = []
    for key in _PCFG_REPOS:
        r = cfg.get(key)
        row = {"name": key, "defined": r is not None, "cloned": False, "paths": [], "commits": 0}
        if r is not None:
            root = repos_mod._dir_for(r)
            row["cloned"] = root.exists()
            if row["cloned"]:
                row["paths"] = _pcfg_project_paths(root, name)
                if row["paths"]:
                    for c in _pcfg_log(root, row["paths"], days):
                        c["repo"] = r.get("name") or key
                        c["envs"] = _pcfg_envs(c["files"])
                        commits.append(c)
                        row["commits"] += 1
        out_repos.append(row)
    commits.sort(key=lambda c: c["when"], reverse=True)
    by_author: dict = {}
    by_repo: dict = {}
    per_period: dict = {}
    for c in commits:
        by_author[c["who"]] = by_author.get(c["who"], 0) + 1
        by_repo[c["repo"]] = by_repo.get(c["repo"], 0) + 1
        pk = c["when"][:10] if days else c["when"][:7] + "-01"
        per_period[pk] = per_period.get(pk, 0) + 1
    top = lambda d: sorted(({"key": k, "count": v} for k, v in d.items()), key=lambda x: -x["count"])  # noqa: E731
    note = "" if any(r["cloned"] for r in out_repos) else "inventories / ocp-templates not defined or not cloned (Repositories page)"
    if not note and not any(r["paths"] for r in out_repos):
        note = "no folder named after this project in the cloned inventories / ocp-templates repos"
    return {"repos": out_repos, "commits": len(commits), "recent": commits[:_PCFG_MAX],
            "files_changed": sum(len(c["files"]) for c in commits),
            "by_author": _fold_users(top(by_author)), "by_repo": top(by_repo),
            "per_period": [{"day": k, "count": v} for k, v in sorted(per_period.items())],
            "note": note}


# ---------------------------------------------------------------- standard changes
_STD_PAGE = 5000   # ef-ops-db-changes-standard search_after page size


def _sec_stdchanges(name: str, days: int) -> dict:
    """Standard changes whose vars.project_name is this project (from the
    Engine catalogue) + their run history from ef-ops-db-changes-standard,
    linked by JobName / ScriptName (the index has no project field)."""
    from . import stdchanges
    cat = stdchanges.catalog_all()
    mine = [c for c in cat.get("changes") or []
            if _norm(((c.get("vars") or {}).get("project_name"))) == _norm(name)]
    out = {"engine_found": cat.get("found", False), "changes": [
        {"name": c["name"], "category": c["category"], "service": c["service"],
         "technologies": c["technologies"], "sql_files": c["sql_files"],
         "requester_team": (c.get("vars") or {}).get("requester_team"),
         "approver_team": (c.get("vars") or {}).get("approver_team"),
         "m2m": bool((c.get("vars") or {}).get("m2m_flag")),
         "env": {e: (c["env"].get(e) or {}).get("state") for e in ("uat", "prd")},
         "issues": c["issues"]} for c in mine]}
    if not mine:
        out.update(runs=0, note=cat.get("note") or "no standard change declares this project_name")
        return out
    keys = sorted({v for c in mine for v in (c["name"], *(s["name"] for s in c["scripts"]))})
    # One standard-change RUN is spread over several documents (one per input
    # parameter set) — the fingerprint is service (JobName) + environment +
    # ChangeNumber: the same change number executes once per environment
    # (uat, then prd), and those are DIFFERENT runs.
    # The index is paged with search_after so every run is seen (no 1000 cap).
    body = {
        "query": {"bool": {"filter": [{"bool": {"should": [
            {"terms": {"JobName": keys}}, {"terms": {"ScriptName": keys}}],
            "minimum_should_match": 1}}] + ([] if not days else [{"range": {"Date": {"gte": f"now-{days}d"}}}])}},
        "sort": [{"Date": {"order": "desc", "unmapped_type": "date"}}, {"_doc": {"order": "asc"}}],
        "_source": ["ChangeNumber", "Date", "Environment", "JobName", "NumberOfChangedRows",
                    "Requester", "ScriptName", "Status", "UpdatedDate"],
        "track_total_hits": True, "size": _STD_PAGE}
    docs = 0
    folded: dict = {}      # (service, change_number) -> run
    order: list = []
    for _page in range(200):   # 1M docs sanity ceiling
        resp = _es("ef-ops-db-changes-standard", body)
        hits = (resp.get("hits") or {}).get("hits") or []
        if not hits:
            break
        for h in hits:
            docs += 1
            s_ = h.get("_source") or {}
            service = (s_.get("JobName") or s_.get("ScriptName") or "").strip()
            cn = str(s_.get("ChangeNumber") or "").strip()
            env = (s_.get("Environment") or "").strip().lower() or "(none)"
            key = (service, env, cn or f"doc:{h.get('_id')}")   # no change number → the doc is its own run
            st = s_.get("Status") or ""
            when = (s_.get("Date") or "")[:16]
            r = folded.get(key)
            if r is None:
                r = folded[key] = {
                    "when": when, "last": (s_.get("UpdatedDate") or s_.get("Date") or "")[:16],
                    "env": env,
                    "job": s_.get("JobName") or "", "script": s_.get("ScriptName") or "", "service": service,
                    "change_number": cn, "status": st, "rows": 0, "docs": 0,
                    "who": _user_display(s_.get("Requester") or "") or "(unknown)", "statuses": {}}
                order.append(key)
            r["docs"] += 1
            r["rows"] += _int(s_.get("NumberOfChangedRows"))
            r["when"] = min(r["when"], when) if when else r["when"]           # run starts at its first document
            r["last"] = max(r["last"], (s_.get("UpdatedDate") or s_.get("Date") or "")[:16])
            r["statuses"][st] = r["statuses"].get(st, 0) + 1
            if _BAD.search(st) and not _BAD.search(r["status"]):
                r["status"] = st                                             # any failed parameter fails the run
            if r["who"] == "(unknown)" and s_.get("Requester"):
                r["who"] = _user_display(s_["Requester"]) or "(unknown)"
        if len(hits) < _STD_PAGE:
            break
        body["search_after"] = hits[-1].get("sort")
    runs = []
    for key in order:
        r = folded[key]
        r["when"] = r["when"].replace("T", " ")
        r["params"] = r.pop("docs")                                      # parameter sets in this run
        r["status_mix"] = r.pop("statuses")
        runs.append(r)
    runs.sort(key=lambda r: r["when"], reverse=True)
    envs: dict = {}      # Environment is TEXT → folded from the runs
    by_req: dict = {}
    by_status: dict = {}
    by_job: dict = {}
    per_period: dict = {}
    for r in runs:
        e = envs.setdefault(r["env"], {"env": r["env"], "runs": 0, "failed": 0, "rows": 0, "users": {}, "last": ""})
        e["runs"] += 1
        e["failed"] += 1 if _BAD.search(r["status"]) else 0
        e["rows"] += r["rows"]
        e["users"][r["who"]] = e["users"].get(r["who"], 0) + 1
        e["last"] = max(e["last"], r["when"][:16].replace(" ", "T"))
        by_req[r["who"]] = by_req.get(r["who"], 0) + 1
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        by_job[r["job"]] = by_job.get(r["job"], 0) + 1
        pk = r["when"][:10] if days else r["when"][:7] + "-01"
        per_period[pk] = per_period.get(pk, 0) + 1
    for e in envs.values():
        e["users"] = sorted(({"key": k, "count": v} for k, v in e["users"].items()), key=lambda x: -x["count"])
    top = lambda d, n: sorted(({"key": k, "count": v} for k, v in d.items()), key=lambda x: -x["count"])[:n]  # noqa: E731
    out.update(runs=len(runs), documents=docs,
               by_requester=_fold_users(top(by_req, 15)), by_status=top(by_status, 10), by_job=top(by_job, 50),
               rows_changed=sum(r["rows"] for r in runs),
               per_period=[{"day": k, "count": v} for k, v in sorted(per_period.items())],
               environments=sorted(envs.values(), key=lambda e: ({"dev": 1, "qc": 2, "uat": 3, "prd": 4}.get(e["env"], 9), e["env"])),
               recent=runs)
    return out


# ---------------------------------------------------------------- DORA
def _ts(v) -> dt.datetime | None:
    """Tolerant timestamp parse for event ts strings."""
    if not v:
        return None
    x = str(v).strip().replace(" ", "T").rstrip("Z")[:19]
    try:
        return dt.datetime.fromisoformat(x)
    except ValueError:
        return None


_PRD = re.compile(r"pr(o?)d", re.I)
_OK = re.compile(r"succ", re.I)
_BAD = re.compile(r"fail|abort|error|cancel", re.I)


def _dora(out: dict) -> dict:
    """The four DORA metrics from what the report already holds:
      deployment frequency  — successful PRODUCTION deploys per week
      lead time for changes — effective commit (or its build) → prd deploy
      change failure rate   — failed prd deploys / all prd deploys
      time to restore       — failed prd deploy → next successful prd deploy
    Ratings follow the DORA bands (elite / high / medium / low)."""
    ev = out.get("events") or []
    days = out.get("days") or 0
    prd = [e for e in ev if e.get("type") == "deploy" and not e.get("test")
           and _PRD.search(e.get("env") or "") and _ts(e.get("ts"))]
    succ = [e for e in prd if _OK.search(e.get("status") or "")]
    fail = [e for e in prd if _BAD.search(e.get("status") or "")]
    res = {"available": bool(prd), "prd_deploys": len(prd),
           "prd_success": len(succ), "prd_failed": len(fail)}
    if not prd:
        return res
    # ---- deployment frequency ------------------------------------------
    if days:
        span = days
    else:
        tss = sorted(_ts(e["ts"]) for e in prd)
        span = max((tss[-1] - tss[0]).days + 1, 1)
    per_week = len(succ) / span * 7
    res["deploy_freq_week"] = round(per_week, 2)
    res["deploy_freq_rating"] = ("elite" if per_week >= 7 else "high" if per_week >= 1
                                 else "medium" if per_week * 4.3 >= 1 else "low")
    # ---- lead time for changes -------------------------------------------
    first_commit: dict = {}     # built version → earliest effective-commit time
    first_build: dict = {}      # (app, version) → earliest build time
    for e in ev:
        t = _ts(e.get("ts"))
        if not t:
            continue
        if e.get("type") == "ecommit" and e.get("version"):
            v = e["version"]
            if v not in first_commit or t < first_commit[v]:
                first_commit[v] = t
        elif e.get("type") == "build" and e.get("version"):
            k = (e.get("app"), e["version"])
            if k not in first_build or t < first_build[k]:
                first_build[k] = t
    leads = []
    for e in succ:
        v = e.get("version")
        if not v:
            continue
        start = first_commit.get(v) or first_build.get((e.get("app"), v))
        if start:
            h = (_ts(e["ts"]) - start).total_seconds() / 3600
            if h >= 0:
                leads.append(h)
    if leads:
        leads.sort()
        med = leads[len(leads) // 2]
        res["lead_time_h"] = round(med, 1)
        res["lead_time_samples"] = len(leads)
        res["lead_time_rating"] = ("elite" if med < 24 else "high" if med < 168
                                   else "medium" if med < 720 else "low")
    # ---- change failure rate ---------------------------------------------
    cfr = len(fail) / len(prd) * 100
    res["cfr_pct"] = round(cfr, 1)
    res["cfr_rating"] = ("elite" if cfr <= 15 else "high" if cfr <= 30
                         else "medium" if cfr <= 45 else "low")
    # ---- time to restore -------------------------------------------------
    restores = []
    by_app: dict = {}
    for e in prd:
        by_app.setdefault(e.get("app"), []).append(e)
    for app, lst in by_app.items():
        lst.sort(key=lambda e: _ts(e["ts"]))
        for i, e in enumerate(lst):
            if not _BAD.search(e.get("status") or ""):
                continue
            nxt = next((x for x in lst[i + 1:] if _OK.search(x.get("status") or "")), None)
            if nxt:
                restores.append((_ts(nxt["ts"]) - _ts(e["ts"])).total_seconds() / 3600)
    if restores:
        mttr = sum(restores) / len(restores)
        res["mttr_h"] = round(mttr, 1)
        res["mttr_samples"] = len(restores)
        res["mttr_rating"] = ("elite" if mttr < 1 else "high" if mttr < 24
                              else "medium" if mttr < 168 else "low")
    elif fail:
        res["mttr_open"] = len(fail)   # failures with no recovery yet
    return res


def _assemble_events(out: dict) -> list[dict]:
    """The unified event log: cicd events + commits + Jira updates + Jira
    changelog folded into one newest-first stream. NOT capped — every event
    each source query returned is kept (sources fetch up to 10000 docs each;
    events_meta says when a source had even more)."""
    events = list(((out.get("cicd") or {}).get("events")) or [])
    bv = ((out.get("cicd") or {}).get("build_versions")) or {}

    def _built_version(cid, branch):
        """Version built from THIS commit on THIS branch. Ids may be truncated
        on either side; a build without a branch field can only match by id."""
        if not cid:
            return ""
        br = _norm_branch(branch)
        exact = bv.get(f"{cid}|{br}")
        if exact:
            return exact
        for k, v in bv.items():
            kc, kb = k.split("|", 1)
            if (kb == br or not kb) and (kc == cid or kc.startswith(cid) or cid.startswith(kc)):
                return v
        return ""

    for c in ((out.get("commits") or {}).get("recent")) or []:
        branch = (c.get("branch") or "").lower()
        eff = branch == "develop" or branch.startswith("release")
        built = _built_version(c.get("id_full") or c.get("id") or "", c.get("branch")) if eff else ""
        events.append({"ts": c.get("when") or "", "type": "ecommit" if eff else "commit",
                       "app": c.get("repo") or "", "env": "",
                       "added": c.get("added"), "deleted": c.get("deleted"),
                       "status": "", "version": built or (c.get("id") or ""),
                       "who": _user_display(c.get("author") or ""),
                       "test": False,
                       "detail": " · ".join(x for x in (c.get("branch"),
                                                        c.get("message")) if x)
                       + (f" · commit {c.get('id')}" if built else ""),
                       "tip": (c.get("message_full") or "")
                       + (f"\nbuilt version: {built}" if built else "")})
    for c in ((out.get("jira_changes") or {}).get("recent")) or []:
        events.append({"ts": c.get("when") or "", "type": "change",
                       "app": c.get("key") or "", "env": "",
                       "status": "", "version": "",
                       "who": c.get("author") or "", "test": False,
                       "url": c.get("url") or "",
                       "detail": "; ".join(f"{i['field']}: {i['from'] or '—'} → {i['to'] or '—'}"
                                           for i in c.get("items") or [])})
    for c in ((out.get("configcommits") or {}).get("recent")) or []:
        events.append({"ts": c.get("when") or "", "type": "configchange",
                       "app": c.get("repo") or "", "env": ", ".join(c.get("envs") or []),
                       "status": "", "version": c.get("sha") or "",
                       "who": c.get("who") or "", "test": False,
                       "detail": " · ".join(x for x in (c.get("subject"), f"{len(c.get('files') or [])} file(s): " + ", ".join(
                           f.split("/", 1)[-1] for f in (c.get("files") or [])[:4]) + (" …" if len(c.get("files") or []) > 4 else "")) if x)})
    for c in ((out.get("pipelinecfg") or {}).get("recent")) or []:
        events.append({"ts": c.get("when") or "", "type": "pipelinecfg",
                       "app": c.get("repo") or "", "env": ", ".join(c.get("envs") or []),
                       "status": "", "version": c.get("sha") or "",
                       "who": c.get("who") or "", "test": False,
                       "detail": " · ".join(x for x in (c.get("subject"), f"{len(c.get('files') or [])} file(s): " + ", ".join(
                           f.split("/", 1)[-1] for f in (c.get("files") or [])[:4]) + (" …" if len(c.get("files") or []) > 4 else "")) if x)})
    for r in ((out.get("stdchanges") or {}).get("recent")) or []:
        events.append({"ts": r.get("when") or "", "type": "stdchange",
                       "app": r.get("job") or r.get("script") or "", "env": r.get("env") or "",
                       "status": r.get("status") or "", "version": r.get("change_number") or "",
                       "who": r.get("who") or "", "test": False,
                       "detail": " · ".join(x for x in (
                           r.get("script"), f"{r.get('rows') or 0} rows",
                           f"{r['params']} parameter set(s)" if (r.get("params") or 0) > 1 else "") if x)})
    for r in ((out.get("autotest") or {}).get("runs")) or []:
        events.append({"ts": r.get("when") or "", "type": "autotest",
                       "app": r.get("technology") or "", "env": r.get("env") or "",
                       "status": "", "version": "",
                       "who": r.get("requester") or "", "test": False,
                       "detail": f"{r.get('duration') or 0}s"})
    events.sort(key=lambda e: e.get("ts") or "", reverse=True)
    com = out.get("commits") or {}
    jc = out.get("jira_changes") or {}
    cc = out.get("cicd") or {}
    truncated = {}
    if (com.get("total") or 0) > len(com.get("recent") or []):
        truncated["commits"] = com["total"]
    if (jc.get("total") or 0) > len(jc.get("recent") or []):
        truncated["jira changes"] = jc["total"]
    for k, tot in (cc.get("totals") or {}).items():
        fetched = sum(1 for e in (cc.get("events") or [])
                      if e.get("type") == {"builds": "build", "deploys": "deploy",
                                           "releases": "release"}.get(k))
        if tot > fetched:
            truncated[k] = tot
    out["events_meta"] = {"shown": len(events), "truncated": truncated}
    return events


def _sec_prev(name: str, repos: list[str], days: int) -> dict | None:
    """The PREVIOUS window's headline counts (now-2w .. now-w) so every visual
    can say current-vs-previous. Six size-0 count queries; None on all-time."""
    if not days:
        return None
    variants = _name_variants(name)
    rng = {"gte": f"now-{2 * days}d", "lt": f"now-{days}d"}

    def count(index, date_field, extra=None, should=None):
        q = {"bool": {"filter": [{"range": {date_field: rng}}] + (extra or [])}}
        if should:
            q["bool"]["should"] = should
            q["bool"]["minimum_should_match"] = 1
        else:
            q["bool"]["filter"].append({"terms": {"project": variants}})
        r = _es(index, {"query": q, "size": 0, "track_total_hits": True})
        return ((r.get("hits") or {}).get("total") or {}).get("value", 0)

    com_should = [{"terms": {"project": variants}}]
    if repos:
        com_should.append({"terms": {"repository": repos[:64]}})
    chg_should = []
    for v in variants:
        chg_should += [{"match_phrase": {"projectname": v}},
                       {"match_phrase": {"projectkey": v}}]
    jira_should = [{"terms": {"project": variants}},
                   {"terms": {"projectkey": variants}}]
    out = {}
    for key, args in (
            ("commits", ("ef-git-commits", "commitdate", None, com_should)),
            ("changes", ("ef-bs-jira-changes", "created", None, chg_should)),
            ("builds", ("ef-cicd-builds", "startdate", None, None)),
            ("deploys", ("ef-cicd-deployments", "startdate", None, None)),
            ("releases", ("ef-cicd-releases", "releasedate", None, None)),
            ("resolved", ("ef-bs-jira-issues", "resolved",
                          [{"terms": {"status": CLOSED_JIRA}}], jira_should))):
        try:
            out[key] = count(*args)
        except Exception:  # noqa: BLE001 — a failed count just hides its delta
            out[key] = None
    return out


def _sec_scans(name: str) -> dict:
    out: dict = {}
    variants = _name_variants(name)
    for key, cfg in _SCANNERS.items():
        try:
            src = [f for f in (*(s for s in cfg["sev"] if s), *cfg["extra"],
                               "status", "enddate", "startdate", "codeversion",
                               "application")]
            resp = _es(cfg["index"], {
                "query": {"bool": {"filter": [{"terms": {"project": variants}}]}},
                "aggs": {"by_app": {"terms": {"field": "application", "size": 100},
                                    "aggs": {"latest": {"top_hits": {
                                        "size": 1, "_source": src,
                                        "sort": [{"enddate": {
                                            "order": "desc",
                                            "unmapped_type": "date"}}]}}}}},
                "size": 0, "track_total_hits": True})
        except Exception as exc:  # noqa: BLE001 — per-scanner degradation
            out[key] = {"label": cfg["label"], "error": str(exc)[:200]}
            continue
        apps = []
        worst = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for b in ((resp.get("aggregations") or {}).get("by_app") or {}).get("buckets", []):
            hit = (((b.get("latest") or {}).get("hits") or {}).get("hits") or [{}])[0]
            s = hit.get("_source") or {}
            crit, high, med, low = (
                _int(s.get(f)) if f else None for f in cfg["sev"])
            row = {"app": b.get("key"),
                   "when": (s.get("enddate") or s.get("startdate") or "")[:10],
                   "status": s.get("status") or "",
                   "version": s.get("codeversion") or "",
                   "critical": crit, "high": high, "medium": med, "low": low}
            if key == "trufflehog":
                row["verified"] = bool(s.get("verified"))
                row["findings"] = _int(s.get("findings_count"))
            if key == "prismacloud":
                row["compliance"] = {k: _int(s.get(f)) for k, f in
                                     (("critical", "Ccritical"), ("high", "Chigh"),
                                      ("medium", "Cmedium"), ("low", "Clow"))}
                row["image"] = " : ".join(x for x in (s.get("imageName"),
                                                      s.get("imageTag")) if x)
            if key in ("invicti", "zap"):
                row["url"] = s.get("url") or ""
            apps.append(row)
            for sev in worst:
                worst[sev] = max(worst[sev], row.get(sev) or 0)
        total = ((resp.get("hits") or {}).get("total") or {}).get("value", 0)
        out[key] = {"label": cfg["label"], "scans": total, "apps": apps,
                    "worst": worst if any(s for s in cfg["sev"] if s) else None}
    return out


def _sec_logging(name: str) -> dict:
    from . import logstats
    d = logstats.analyze()
    p = next((x for x in d.get("projects") or []
              if _norm(x.get("name")) == _norm(name)), None)
    if not p:
        return {"found": False}
    apps = [{"app": a.get("app"), "score": a.get("score"),
             "size_h": a.get("size_h"), "docs": a.get("docs"),
             "indices": a.get("indices"), "issues": a.get("issues") or [],
             "envs": [{"env": e.get("env"), "deployed": e.get("deployed"),
                       "score": e.get("score"), "no_logs": e.get("no_logs"),
                       "size_h": e.get("size_h"),
                       "last_deploy": (e.get("last_deploy") or "")[:10]}
                      for e in a.get("env_stats") or []]}
            for a in p.get("apps") or []]
    t = p.get("totals") or {}
    return {"found": True, "score": p.get("score"), "size_h": t.get("size_h"),
            "size_bytes": t.get("size_bytes"), "docs": t.get("docs"),
            "indices": t.get("indices"), "apps": apps,
            "analyzed_at": d.get("analyzed_at")}


# ---------------------------------------------------------------- demo data
def _demo_configs(name: str) -> dict:
    try:
        return _sec_configs(name)      # archconfig works in demo mode too
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "error": str(exc)[:200]}


def _demo_configcommits(name: str, days: int) -> dict:
    try:
        return _sec_configcommits(name, days)   # the demo Control repos are real clones
    except Exception as exc:  # noqa: BLE001
        return {"repos": [], "commits": 0, "recent": [], "error": str(exc)[:200]}


def _demo_pipelinecfg(name: str, days: int) -> dict:
    """Demo: the real git walk when the demo repos are cloned, else a story."""
    live = _sec_pipelinecfg(name, days)
    if live.get("commits"):
        return live
    now = _now()
    mk = lambda h, who, repo, subj, files: {"sha": f"{abs(hash((name, h))) % 0xfffffff:07x}", "who": who, "repo": repo,  # noqa: E731
                                            "when": (now - dt.timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"), "subject": subj,
                                            "files": files, "envs": _pcfg_envs(files)}
    commits = [mk(5, "alice", "inventories", f"{name}: bump prd replicas for payments", [f"{name}/group_vars/prd_payments/vars.yml"]),
               mk(30, "bob", "ocp-templates", f"{name}: add readiness probe to checkout route", [f"{name}/checkout/deploymentconfig.yml", f"{name}/checkout/route.yml"]),
               mk(70, "carol", "inventories", f"{name}: new uat hosts", [f"{name}/payments.yml", f"{name}/host_vars/uat-pay-01.yml"])]
    commits = [c for c in commits if not days or (now - dt.datetime.strptime(c["when"], "%Y-%m-%d %H:%M")).days <= days]
    ba: dict = {}
    for c in commits:
        ba[c["who"]] = ba.get(c["who"], 0) + 1
    return {"repos": [{"name": "inventories", "defined": True, "cloned": True, "paths": [name], "commits": sum(1 for c in commits if c["repo"] == "inventories")},
                      {"name": "ocp-templates", "defined": True, "cloned": True, "paths": [name], "commits": sum(1 for c in commits if c["repo"] == "ocp-templates")}],
            "commits": len(commits), "recent": commits, "files_changed": sum(len(c["files"]) for c in commits),
            "by_author": [{"key": k, "count": v} for k, v in sorted(ba.items(), key=lambda x: -x[1])],
            "by_repo": [{"key": "inventories", "count": 2}, {"key": "ocp-templates", "count": 1}],
            "per_period": [{"day": c["when"][:10], "count": 1} for c in commits], "note": ""}


def _demo_report(name: str, days: int) -> dict:
    import hashlib
    seed = int(hashlib.sha1(name.encode()).hexdigest()[:6], 16)
    authors = [("alice", 34), ("bob", 21), ("carol", 12), ("dave", 6), ("ext.contractor", 3)]
    def _stack(total_, i):
        names = [a for a, _ in authors]
        parts = {}
        left = total_
        for j, nm in enumerate(names):
            take = (total_ * (4 - j) // 10) if j < 3 else left
            take = max(0, min(take, left))
            if take:
                parts[nm] = take
            left -= take
            if left <= 0:
                break
        return parts

    if days:
        base_day = dt.date.today() - dt.timedelta(days=days - 1)
        per_day = [{"day": (base_day + dt.timedelta(days=i)).isoformat(),
                    "count": (c := ((seed + i * 7) % 9 if i % 3 else 0)),
                    "by_author": _stack(c, i)}
                   for i in range(days)]
    else:  # all time — monthly buckets over ~2 years
        per_day = [{"day": f"{2024 + (m + 8) // 12}-{(m + 8) % 12 + 1:02d}-01",
                    "count": (c := ((seed + m * 13) % 60 + 5)),
                    "by_author": _stack(c, m)} for m in range(24)]
    total = sum(b["count"] for b in per_day)
    inv = _sec_inventory(name)
    repos = [a["repository_name"] for a in inv["apps"] if a.get("repository_name")]
    commits = {
        "total": total, "days": days, "per_day": per_day,
        "unit": "day" if days else "month",
        "rate": round(total / max(days or 720, 1), 2),
        "active_days": sum(1 for b in per_day if b["count"]),
        "lines": {"added": 4820, "deleted": 2135},
        "authors": [{"key": a, "count": c, "added": c * 61, "deleted": c * 27} for a, c in authors],
        "author_repo": [
            {"author": "alice", "total": 34, "repos": {(repos or ["main-repo"])[0]: 20,
                                                       (repos or ["main-repo"])[-1]: 14}},
            {"author": "bob", "total": 21, "repos": {(repos or ["main-repo"])[0]: 21}},
            {"author": "carol", "total": 12, "repos": {r: 4 for r in (repos or ["main-repo"])[:3]}},
            {"author": "dave", "total": 6, "repos": {(repos or ["main-repo"])[-1]: 6}}],
        "repos": [{"key": r, "count": max(3, (seed + i * 13) % 40),
                   "added": max(3, (seed + i * 13) % 40) * 70, "deleted": max(3, (seed + i * 13) % 40) * 31}
                  for i, r in enumerate(repos)] or [{"key": "main-repo", "count": total}],
        "branches": [{"key": "develop", "count": int(total * .6)},
                     {"key": "main", "count": int(total * .4)}],
        "recent": [{"when": f"{per_day[-1]['day']} 10:0{i}", "repo": (repos or ['main-repo'])[i % max(len(repos), 1)],
                    "branch": ["develop", "feature/checkout-e2e", "develop", "release/1.4"][i % 4],
                    "author": authors[i % 4][0],
                    "added": [38, 412, 9, 120][i], "deleted": [12, 5, 210, 60][i],
                    "id": f"a1b2c3d{i}", "id_full": f"a1b2c3d{i}e4f5a6b7c8",
                    "message": m,
                    "message_full": m + "\n\n- reviewed by the team\n- refs DEVOPS-14" + str(i)}
                   for i, m in enumerate([
                       "fix payment retry loop on gateway timeout",
                       "bump base image to patch CVE-2026-1337",
                       "add e2e coverage for checkout edge cases",
                       "refactor notification templating"])],
    }
    jira = {
        "total": 42 + seed % 20, "matched": True, "open": 9 + seed % 5,
        "open_by_status": [{"key": "Open", "count": 4}, {"key": "In Progress", "count": 3},
                           {"key": "Reopened", "count": 2}],
        "open_by_priority": [{"key": "Critical", "count": 1}, {"key": "High", "count": 3},
                             {"key": "Medium", "count": 5}],
        "by_status": [{"key": "Closed", "count": 28}, {"key": "Open", "count": 4},
                      {"key": "In Progress", "count": 3}, {"key": "Resolved", "count": 5},
                      {"key": "Reopened", "count": 2}],
        "by_type": [{"key": "Bug", "count": 17}, {"key": "Task", "count": 15},
                    {"key": "Story", "count": 10}],
        "by_assignee": [{"key": "alice", "count": 12}, {"key": "bob", "count": 9},
                        {"key": "(unassigned)", "count": 4}],
        "matrix": {"statuses": ["Open", "In Progress", "Reopened"],
                   "rows": [
                       {"priority": "Critical", "total": 1, "cells": {"In Progress": 1}},
                       {"priority": "High", "total": 3, "cells": {"Open", "In Progress"} and {"Open": 2, "In Progress": 1}},
                       {"priority": "Medium", "total": 5, "cells": {"Open": 2, "In Progress": 1, "Reopened": 2}}]},
        "workload": [
            {"assignee": "alice", "open": 5, "done": 12,
             "open_by_priority": {"Critical": 1, "High": 2, "Medium": 2}},
            {"assignee": "bob", "open": 3, "done": 8,
             "open_by_priority": {"High": 1, "Medium": 2}},
            {"assignee": "carol", "open": 1, "done": 6, "open_by_priority": {"Medium": 1}},
            {"assignee": "(unassigned)", "open": 3, "done": 0,
             "open_by_priority": {"Medium": 2, "Low": 1}}],
        "done": {"total": 11 + seed % 6, "avg_days": 4.6,
                 "by_assignee": [{"key": "alice", "count": 12}, {"key": "bob", "count": 8},
                                 {"key": "carol", "count": 6}],
                 "per_period": [{"week": (dt.date.today() - dt.timedelta(weeks=w)).isoformat(),
                                 "count": (seed + w * 7) % 6}
                                for w in range(min(days // 7 + 1, 12) if days else 24)][::-1],
                 "recent": [
                     {"key": "DEVOPS-137", "url": "", "summary": "Rotate notification service secrets",
                      "priority": "Medium", "type": "Task", "assignee": "carol",
                      "resolved": "2026-08-22", "took_days": 3.2},
                     {"key": "DEVOPS-133", "url": "", "summary": "Fix flaky checkout e2e on slow CI agents",
                      "priority": "High", "type": "Bug", "assignee": "alice",
                      "resolved": "2026-08-20", "took_days": 6.5},
                     {"key": "DEVOPS-129", "url": "", "summary": "Upgrade payments base image",
                      "priority": "Medium", "type": "Task", "assignee": "bob",
                      "resolved": "2026-08-18", "took_days": 1.8}]},
        "updates_per_week": [{"week": (dt.date.today() - dt.timedelta(weeks=w)).isoformat(),
                              "count": (seed + w * 5) % 14}
                             for w in range(min(days // 7 + 1, 12) if days else 24)][::-1],
        "recent": [
            {"key": "DEVOPS-142", "url": "", "summary": "Payment gateway retries exhaust pool",
             "status": "In Progress", "priority": "Critical", "type": "Bug",
             "assignee": "alice", "updated": "2026-08-26 14:02", "resolved": False},
            {"key": "DEVOPS-141", "url": "", "summary": "Add checkout smoke tests to release gate",
             "status": "Open", "priority": "High", "type": "Task",
             "assignee": "bob", "updated": "2026-08-25 09:41", "resolved": False},
            {"key": "DEVOPS-137", "url": "", "summary": "Rotate notification service secrets",
             "status": "Resolved", "priority": "Medium", "type": "Task",
             "assignee": "carol", "updated": "2026-08-22 16:20", "resolved": True},
        ],
    }
    scans = {
        "prismacloud": {"label": "Prisma (image)", "scans": 12, "worst":
                        {"critical": 0, "high": 2, "medium": 5, "low": 9},
                        "apps": [{"app": a["name"], "when": "2026-08-24", "status": "SUCCESS",
                                  "version": "1.4.2", "critical": 0, "high": 2 if i == 0 else 0,
                                  "medium": 3, "low": 4,
                                  "compliance": {"critical": 0, "high": 1, "medium": 2, "low": 0},
                                  "image": f"{a['name']} : 1.4.2"}
                                 for i, a in enumerate(inv["apps"][:3])]},
        "invicti": {"label": "Invicti (DAST)", "scans": 4, "worst":
                    {"critical": 0, "high": 1, "medium": 2, "low": 6},
                    "apps": [{"app": inv["apps"][0]["name"], "when": "2026-08-20",
                              "status": "SUCCESS", "version": "1.4.2", "critical": 0,
                              "high": 1, "medium": 2, "low": 6, "url": "https://demo.app"}]
                    if inv["apps"] else []},
        "zap": {"label": "ZAP (DAST)", "scans": 3, "worst":
                {"critical": 0, "high": 0, "medium": 3, "low": 11},
                "apps": [{"app": inv["apps"][0]["name"], "when": "2026-08-19",
                          "status": "SUCCESS", "version": "1.4.2", "critical": None,
                          "high": 0, "medium": 3, "low": 11, "url": "https://demo.app"}]
                if inv["apps"] else []},
        "trufflehog": {"label": "TruffleHog (secrets)", "scans": 6, "worst":
                       {"critical": 1 if seed % 2 else 0, "high": 0, "medium": 0, "low": 2},
                       "apps": [{"app": inv["apps"][0]["name"], "when": "2026-08-25",
                                 "status": "SUCCESS", "version": "", "critical": 1 if seed % 2 else 0,
                                 "high": 0, "medium": 0, "low": 2,
                                 "verified": bool(seed % 2), "findings": 3}]
                       if inv["apps"] else []},
        "fortify": {"label": "Fortify (SAST)", "scans": 5, "worst": None,
                    "apps": [{"app": a["name"], "when": "2026-08-23", "status": "SUCCESS",
                              "version": "1.4.2", "critical": None, "high": None,
                              "medium": None, "low": None}
                             for a in inv["apps"][:2]]},
    }
    changes = {
        "total": 31 + seed % 10, "sampled": 31 + seed % 10,
        "per_week": [{"week": (dt.date.today() - dt.timedelta(weeks=w)).isoformat(),
                      "count": (seed + w * 3) % 9}
                     for w in range(min(days // 7 + 1, 12) if days else 24)][::-1],
        "authors": [{"key": "Alice Nasr", "count": 11}, {"key": "Bob Farid", "count": 8},
                    {"key": "Carol Adel", "count": 6}],
        "fields": [{"key": "status", "count": 14}, {"key": "assignee", "count": 7},
                   {"key": "Fix Version", "count": 4}, {"key": "priority", "count": 3}],
        "recent": [
            {"when": "2026-08-26 14:02", "key": "DEVOPS-142", "url": "",
             "author": "Alice Nasr",
             "items": [{"field": "status", "from": "Open", "to": "In Progress"},
                       {"field": "assignee", "from": "", "to": "alice"}]},
            {"when": "2026-08-25 09:41", "key": "DEVOPS-141", "url": "",
             "author": "Bob Farid",
             "items": [{"field": "priority", "from": "Medium", "to": "High"}]},
            {"when": "2026-08-22 16:20", "key": "DEVOPS-137", "url": "",
             "author": "Carol Adel",
             "items": [{"field": "status", "from": "In Progress", "to": "Resolved"},
                       {"field": "resolution", "from": "", "to": "Fixed"}]},
        ],
        "alltime": {
            "total": 480 + seed % 90, "sampled": 480 + seed % 90,
            "per_month": [{"month": f"{2024 + (m + 6) // 12}-{(m + 6) % 12 + 1:02d}",
                           "count": (seed + m * 11) % 40 + 4} for m in range(20)],
            "authors": [{"key": "Alice Nasr", "count": 208}, {"key": "Bob Farid", "count": 141},
                        {"key": "Carol Adel", "count": 84}, {"key": "Dave Samir", "count": 47}],
        },
    }
    apps = [a["name"] for a in inv["apps"]][:4] or ["main-app"]
    envs = inv.get("envs") or ["dev", "prd"]
    def _ok(entry, ver):
        """demo cell: latest run + last success (older version when failed)."""
        ok = {**entry, "status": "SUCCESS"}
        if entry["status"] != "SUCCESS":
            ok["version"] = ver
            ok["when"] = "2026-08-2" + str(int(entry["when"][9]) - 1 if entry["when"][9] > "0" else 0) + entry["when"][10:]
        return {**entry, "ok": ok}

    board = {
        "builds": {a: _ok({"when": "2026-08-26 09:1" + str(i), "status": "SUCCESS" if i != 1 else "FAILURE",
                           "branch": "develop", "version": f"1.4.{i + 2}",
                           "tech": "Docker", "author": ["alice", "bob", "carol", "dave"][i % 4]},
                          f"1.4.{i + 1}")
                   for i, a in enumerate(apps)},
        "releases": {a: _ok({"when": "2026-08-24 15:0" + str(i), "status": "SUCCESS",
                             "version": f"1.4.{i + 1}", "rlm": f"RLM-10{i}",
                             "rlm_status": "No error" if i == 0 else "Change opened: CHG-42",
                             "rlm_label": f"RLM-10{i}" if i == 0 else "Change opened: CHG-42",
                             "rlm_ok": i == 0}, f"1.4.{i}")
                     for i, a in enumerate(apps[:2])},
        "deploys": {a: {e: _ok({"when": f"2026-08-2{2 + (i + j) % 4} 1{j}:30",
                                "status": "SUCCESS" if (i + j) % 5 else "FAILURE",
                                "version": f"1.4.{max(1, i + (1 if e != 'prd' else 0))}",
                                "reason": ["Scheduled release", "Hotfix: payment retries",
                                           "Config change", "Rollback after incident"][(i + j) % 4],
                                "who": ["alice", "bob"][j % 2]},
                               f"1.4.{max(0, i - 1)}")
                        for j, e in enumerate(envs)}
                    for i, a in enumerate(apps[:3])},
    }
    cicd_events = []
    for i, a in enumerate(apps[:3]):
        cicd_events += [
            {"ts": f"2026-08-26T09:1{i}", "type": "build", "app": a, "env": "",
             "status": "SUCCESS" if i != 1 else "FAILURE", "version": f"1.4.{i + 2}",
             "who": ["alice", "bob", "carol"][i], "test": False,
             "detail": "develop · Docker"},
            {"ts": f"2026-08-25T14:2{i}", "type": "deploy", "app": a, "env": "prd" if i == 0 else "dev",
             "status": "SUCCESS", "version": f"1.4.{i + 1}",
             "reason": ["Scheduled release", "Feature rollout", "Config change"][i],
             "who": "alice", "test": i == 2,
             "detail": ["Scheduled release", "Feature rollout", "Config change"][i] + " · Docker"},
        ]
    cicd_events += [
        {"ts": "2026-08-24T09:00", "type": "build", "app": apps[0], "env": "",
         "status": "SUCCESS", "version": "1.4.1", "who": "alice", "test": False,
         "detail": "develop · Docker"},
        {"ts": "2026-08-23T10:00", "type": "deploy", "app": apps[1] if len(apps) > 1 else apps[0],
         "env": "prd", "status": "FAILURE", "version": "1.4.0", "who": "bob",
         "test": False, "reason": "Hotfix: payment retries", "detail": "Hotfix: payment retries · Docker"},
        {"ts": "2026-08-23T12:30", "type": "deploy", "app": apps[1] if len(apps) > 1 else apps[0],
         "env": "prd", "status": "SUCCESS", "version": "1.4.0", "who": "bob",
         "test": False, "reason": "Hotfix: payment retries", "detail": "Hotfix: payment retries · Docker"},
    ]
    cicd_events.append({"ts": "2026-08-24T15:00", "type": "release", "app": apps[0],
                        "env": "", "status": "SUCCESS", "version": "1.4.1",
                        "who": "", "test": False, "detail": "RLM-100"})
    board["top_users"] = {
        "build": [{"key": "alice", "count": 9}, {"key": "bob", "count": 6}],
        "release": [],
        "deploys": {e: [{"key": "alice", "count": 5 - j}, {"key": "bob", "count": 3 - j % 3}]
                    for j, e in enumerate(envs)},
    }
    build_versions = {"a1b2c3d0e4f5a6b7c8|develop": "1.4.2", "a1b2c3d2e4f5a6b7c8|develop": "1.4.3",
                      "a1b2c3d3e4f5a6b7c8|release/1.4": "R1.4-rc2",
                      "a1b2c3d3e4f5a6b7c8|develop": "1.5.0-SNAPSHOT"}
    cicd = {"board": board, "events": cicd_events, "build_versions": build_versions,
            "reasons": {"all": [{"key": "Scheduled release", "count": 6}, {"key": "Hotfix: payment retries", "count": 4},
                                {"key": "Config change", "count": 3}, {"key": "Rollback after incident", "count": 1}],
                        "prd": [{"key": "Scheduled release", "count": 3}, {"key": "Hotfix: payment retries", "count": 2}],
                        "failed": [{"key": "Hotfix: payment retries", "count": 2}]},
            "totals": {"builds": 18 + seed % 9, "deploys": 11 + seed % 7,
                       "releases": 3 + seed % 3}}
    envs_d = inv.get("envs") or ["dev", "prd"]
    autotest = {
        "total": 22 + seed % 9, "sampled": 22 + seed % 9,
        "by_env": [{"key": e, "count": 12 - 3 * i} for i, e in enumerate(envs_d[:3])],
        "by_requester": [{"key": "carol", "count": 11}, {"key": "alice", "count": 7},
                         {"key": "bob", "count": 4}, {"key": "grace", "count": 3}],
        "by_technology": [{"key": "Selenium", "count": 14}, {"key": "Postman", "count": 8}],
        "per_period": [{"day": b["day"], "count": (seed + i * 5) % 3 if i % 2 else 0}
                       for i, b in enumerate(per_day)],
        "duration": {"avg": 412.5, "max": 1290, "sum": 9075},
        "runs": [{"when": "2026-08-27 08:30", "duration": 410, "env": envs_d[0],
                  "requester": "carol", "technology": "Selenium"},
                 {"when": "2026-08-26 17:05", "duration": 1290, "env": envs_d[-1],
                  "requester": "alice", "technology": "Postman"},
                 {"when": "2026-08-25 09:12", "duration": 380, "env": envs_d[0],
                  "requester": "carol", "technology": "Selenium"}],
    }
    usage_apps = [{"app": a, "minutes": 900 - i * 210,
                   "by_activity": {"build": 380 - i * 80, "deploy": 220 - i * 50,
                                   "fortify": 90, "prismacloud": 70, "qualitytesting": 60,
                                   "sonarqube": 50, "standardchange": 30},
                   "storage": (3 - i) * 4_800_000_000 + 900_000_000,
                   "git": (3 - i) * 700_000_000 + 100_000_000,
                   "elk": (3 - i) * 4_100_000_000 + 800_000_000,
                   "repository": f"{a}-svc", "team": "Platform_Devs", "snapshot": "2026-08-27"}
                  for i, a in enumerate(apps[:4])]
    usage = {
        "rows": 96, "total_minutes": sum(a["minutes"] for a in usage_apps),
        "by_activity": {k: sum(a["by_activity"][k] for a in usage_apps)
                        for k in usage_apps[0]["by_activity"]},
        "apps": usage_apps,
        "teams": [{"key": "Platform_Devs", "count": sum(a["minutes"] for a in usage_apps)}],
        "per_period": [{"day": b["day"], "count": ((seed + i * 17) % 90 + 10) if b["count"] else 0}
                       for i, b in enumerate(per_day)],
        "storage": {"total": sum(a["storage"] for a in usage_apps),
                    "git": sum(a["git"] for a in usage_apps),
                    "elk": sum(a["elk"] for a in usage_apps)},
    }
    if days:
        usage["prev_total_minutes"] = int(usage["total_minutes"] * 0.86)
    prev = None if not days else {
        "commits": max(total - 15, 5), "changes": 28, "builds": 21,
        "deploys": 12, "releases": 4, "resolved": 9}
    std = {"engine_found": True, "runs": 14 + seed % 5, "rows_changed": 3120 + seed,
           "changes": [{"name": "Finance_AddBranch", "category": "Finance", "service": "AddBranch",
                        "technologies": ["Oracle"], "sql_files": 2, "requester_team": "Finance_Ops",
                        "approver_team": "SRE_Core", "m2m": False, "env": {"uat": "vaulted", "prd": "vaulted"}, "issues": []}]
           if name == "Platform" else [],
           "by_requester": [{"key": "carol", "count": 8}, {"key": "bob", "count": 5}, {"key": "grace", "count": 2}],
           "by_status": [{"key": "SUCCESS", "count": 13}, {"key": "FAILED", "count": 2}],
           "by_job": [{"key": "Finance_AddBranch", "count": 15}],
           "per_period": [{"day": b["day"], "count": (seed + i * 3) % 2 if i % 4 == 0 else 0} for i, b in enumerate(per_day)],
           "environments": [
               {"env": "uat", "runs": 9, "failed": 1, "rows": 1800, "last": "2026-08-26T11:02",
                "users": [{"key": "carol", "count": 6}, {"key": "bob", "count": 3}]},
               {"env": "prd", "runs": 6, "failed": 1, "rows": 1320, "last": "2026-08-25T16:40",
                "users": [{"key": "bob", "count": 2}, {"key": "carol", "count": 2}, {"key": "grace", "count": 2}]}],
           "recent": [
               {"when": "2026-08-26 11:02", "env": "uat", "job": "Finance_AddBranch", "script": "01_insert_branch.sql",
                "change_number": "CHG0041234", "status": "SUCCESS", "rows": 120, "who": "carol", "params": 4},
               {"when": "2026-08-25 16:40", "env": "prd", "job": "Finance_AddBranch", "script": "01_insert_branch.sql",
                "change_number": "CHG0041201", "status": "FAILED", "rows": 0, "who": "bob", "params": 2},
               {"when": "2026-08-24 09:15", "env": "prd", "job": "Finance_AddBranch", "script": "02_audit.sql",
                "change_number": "CHG0041188", "status": "SUCCESS", "rows": 1200, "who": "grace", "params": 1}],
           "documents": 41}
    if name != "Platform":
        std.update(runs=0, note="no standard change declares this project_name", environments=[], recent=[],
                   by_requester=[], by_status=[], by_job=[], per_period=[])
    return {"commits": commits, "jira": jira, "jira_changes": changes,
            "scans": scans, "cicd": cicd, "prev": prev,
            "autotest": autotest, "usage": usage, "stdchanges": std,
            "pipelinecfg": _demo_pipelinecfg(name, days),
            "configs": _demo_configs(name),
            "configcommits": _demo_configcommits(name, days)}


# ---------------------------------------------------------------- public API
def list_projects() -> dict:
    from . import inventory
    inv = inventory.parse()
    return {"source": inv.get("source"),
            "projects": [{"name": p["name"],
                          "company": ((p.get("config") or {}).get("project_vars")
                                      or {}).get("company"),
                          "apps": p.get("app_count") or len(p.get("apps") or []),
                          "envs": p.get("envs") or [],
                          "dev_team": p.get("dev_team"),
                          "prd_team": p.get("prd_team")}
                         for p in inv.get("projects") or []]}


def report(name: str, days: int = 30, refresh: bool = False) -> dict:
    days = int(days or 0)
    days = 0 if days <= 0 else max(7, min(days, 365))   # 0 = ALL TIME
    ck = (_norm(name), days)
    ent = _CACHE.get(ck)
    if not refresh and ent and time.time() - ent["at"] < _TTL:
        return {**ent["payload"], "cached": True}

    out: dict = {"project": name, "days": days,
                 "generated_at": _now().replace(microsecond=0).isoformat() + "Z",
                 "source": "demo" if settings.demo_mode else "live"}

    def guard(key, fn, *a):
        try:
            out[key] = fn(*a)
        except Exception as exc:  # noqa: BLE001 — sections degrade alone
            out[key] = {"error": str(exc)[:300]}

    guard("inventory", _sec_inventory, name)
    inv = out["inventory"] if not out["inventory"].get("error") else {}
    out["project"] = inv.get("name") or name
    repos = [a.get("repository_name") for a in inv.get("apps") or []
             if a.get("repository_name")]
    guard("platform_db", _sec_platform_db, name)
    guard("ado", _sec_ado, name)
    if settings.demo_mode:
        demo = _demo_report(out["project"], days)
        out.update(demo)
    else:
        guard("commits", _sec_commits, name, repos, days)
        guard("jira", _sec_jira, name, days)
        guard("jira_changes", _sec_jira_changes, name, days)
        guard("scans", _sec_scans, name)
        guard("cicd", _sec_cicd, name, days)
        guard("prev", _sec_prev, name, repos, days)
        guard("autotest", _sec_autotest, name, days)
        guard("usage", _sec_usage, name, days)
        guard("stdchanges", _sec_stdchanges, name, days)
        guard("pipelinecfg", _sec_pipelinecfg, name, days)
        guard("configs", _sec_configs, name)
        guard("configcommits", _sec_configcommits, name, days)
    guard("logging", _sec_logging, name)
    # contributor → TEAM folds for the Jira change log (window + all time)
    jc = out.get("jira_changes") or {}
    if inv and not jc.get("error") and (jc.get("authors") or (jc.get("alltime") or {}).get("authors")):
        try:
            jc["teams"] = _team_fold(jc.get("authors") or [], inv)
            jc["teams_alltime"] = _team_fold((jc.get("alltime") or {}).get("authors") or [], inv)
        except Exception:  # noqa: BLE001 — LDAP trouble must not kill the section
            pass
    out["events"] = _assemble_events(out)
    if inv:
        try:
            out["members"] = _sec_members(inv, out)
        except Exception as exc:  # noqa: BLE001
            out["members"] = {"error": str(exc)[:200]}
    try:
        out["dora"] = _dora(out)
    except Exception as exc:  # noqa: BLE001
        out["dora"] = {"available": False, "error": str(exc)[:200]}
    # the SAME window length just before → like-for-like DORA comparison
    if days and not settings.demo_mode:
        try:
            pv = {"days": days,
                  "cicd": _sec_cicd(name, days, prev=True),
                  "commits": _sec_commits(name, repos, days, prev=True),
                  "jira": {}, "jira_changes": {}, "autotest": {}}
            pv["events"] = _assemble_events(pv)
            out["dora"]["prev"] = _dora(pv)
        except Exception as exc:  # noqa: BLE001 — comparison is optional
            out["dora"]["prev"] = {"available": False, "error": str(exc)[:200]}
    elif days and settings.demo_mode and out.get("dora", {}).get("available"):
        out["dora"]["prev"] = {"available": True, "prd_deploys": 5, "prd_success": 4,
                               "prd_failed": 1, "deploy_freq_week": 0.93,
                               "lead_time_h": 41.0, "cfr_pct": 20.0, "mttr_h": 6.2}

    _CACHE[ck] = {"at": time.time(), "payload": out}
    return {**out, "cached": False}


def invalidate() -> None:
    _CACHE.clear()
    _CAT_CACHE.update(at=0.0, payload=None)
