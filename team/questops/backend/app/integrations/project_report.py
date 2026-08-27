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
    section can surface ITS error without killing the page."""
    if not (settings.es_url and settings.es_api_key):
        raise RuntimeError("Elasticsearch is not configured (ES_URL / ES_API_KEY)")
    r = requests.post(f"{settings.es_url.rstrip('/')}/{index}/_search", json=body,
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


def _sec_commits(name: str, repos: list[str], days: int) -> dict:
    should = [{"terms": {"project": _name_variants(name)}}]
    if repos:
        should.append({"terms": {"repository": repos[:64]}})
    all_time = not days
    body = {
        "query": {"bool": {
            "filter": [] if all_time else
            [{"range": {"commitdate": {"gte": f"now-{days}d"}}}],
            "should": should, "minimum_should_match": 1}},
        "sort": [{"commitdate": {"order": "desc", "unmapped_type": "date"}}],
        "_source": ["commitdate", "repository", "branch", "authorname",
                    "authormail", "commitauthor", "commitmessage", "commitid",
                    "project"],
        "aggs": {
            "per_day": {"date_histogram": {
                "field": "commitdate",
                "calendar_interval": "month" if all_time else "day"}},
            "authors": {"terms": {"field": "authorname", "size": 30,
                                  "missing": "(unknown)"}},
            "repos": {"terms": {"field": "repository", "size": 30}},
            "branches": {"terms": {"field": "branch", "size": 10}},
        },
        "track_total_hits": True, "size": 1000,
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
                       "id": str(s.get("commitid") or "")[:10],
                       "message": (msg[0] if msg else "")[:140],
                       "message_full": (s.get("commitmessage") or "").strip()[:600]})
    per_day = _buckets("per_day")
    active_days = sum(1 for b in per_day if b["count"])
    if all_time and len(per_day) > 1:   # rate over the OBSERVED span
        span = max((dt.date.fromisoformat(per_day[-1]["key"][:10])
                    - dt.date.fromisoformat(per_day[0]["key"][:10])).days + 30, 1)
    else:
        span = max(days, 1)
    return {"total": total, "days": days, "unit": "month" if all_time else "day",
            "per_day": [{"day": (b["key"] or "")[:10], "count": b["count"]}
                        for b in per_day],
            "rate": round(total / span, 2),
            "active_days": active_days,
            "authors": _fold_users(_buckets("authors")),
            "repos": _buckets("repos"),
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
    s = s.split("@", 1)[0].strip()
    return re.sub(r"[.\s]+", "_", s)


def _user_display(u) -> str:
    """Canonical display form: domain stripped, dots/spaces shown as
    underscores (original casing kept)."""
    s = str(u or "").strip().split("@", 1)[0].strip()
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
        "track_total_hits": True, "size": 1000,
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


def _git_author(s: dict) -> str:
    return _user_display((s.get("authorname") or "").strip()
                         or re.sub(r"\s*<[^>]*>\s*$", "",
                                   (s.get("commitauthor") or "")).strip())


def _sec_cicd(name: str, days: int) -> dict:
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
        return _es(index, {
            "query": {"bool": {"filter": [{"terms": {"project": variants}}]
                      + ([] if not days else
                         [{"range": {date_field: {"gte": f"now-{days}d"}}}])}},
            "sort": [{date_field: {"order": "desc", "unmapped_type": "date"}}],
            "_source": src, "aggs": aggs, "track_total_hits": True, "size": 1000})

    def latest(index, date_field, src, by_env=False):
        th = {"top_hits": {"size": 1, "_source": src,
                           "sort": [{date_field: {"order": "desc",
                                                  "unmapped_type": "date"}}]}}
        # per cell: the LATEST real run + the last SUCCESSFUL one
        inner = {"latest": th, "ok": {"filter": okf, "aggs": {"latest": th}}}
        if by_env:
            inner = {"by_env": {"terms": {"field": "environment", "size": 12},
                                "aggs": inner}}
        return {"by_app": {"terms": {"field": "application", "size": 100},
                           "aggs": {"real": {          # board = real runs only
                               "filter": realf,
                               "aggs": inner}}}}

    b_src = ["startdate", "application", "branch", "technology", "status",
             "codeversion", "testflag", "authorname", "commitauthor"]
    d_src = ["startdate", "enddate", "application", "environment", "status",
             "codeversion", "testflag", "requester", "Requester", "triggeredby",
             "approver", "Approver", "technology"]
    r_src = ["releasedate", "application", "status", "codeversion",
             "commitauthor", "RLM", "RLM_STATUS"]

    builds = q("ef-cicd-builds", "startdate", latest("ef-cicd-builds", "startdate", b_src), b_src)
    deploys = q("ef-cicd-deployments", "startdate",
                latest("ef-cicd-deployments", "startdate", d_src, by_env=True), d_src)
    releases = q("ef-cicd-releases", "releasedate",
                 latest("ef-cicd-releases", "releasedate", r_src), r_src)

    def _hit(node):
        hs = (((node or {}).get("latest") or {}).get("hits") or {}).get("hits") or []
        return (hs[0].get("_source") or {}) if hs else None

    def _bld(s):
        return {"when": (s.get("startdate") or "")[:16].replace("T", " "),
                "status": s.get("status") or "", "branch": s.get("branch") or "",
                "version": s.get("codeversion") or "",
                "tech": s.get("technology") or "", "author": _git_author(s)}

    def _rel(s):
        return {"when": (s.get("releasedate") or "")[:16].replace("T", " "),
                "status": s.get("status") or s.get("RLM_STATUS") or "",
                "version": s.get("codeversion") or "", "rlm": s.get("RLM") or ""}

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
    def _dep(s):
        return {"when": (s.get("startdate") or s.get("enddate") or "")[:16].replace("T", " "),
                "status": s.get("status") or "",
                "version": s.get("codeversion") or "",
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
        return sorted(counts.values(), key=lambda x: -x["count"])[:5]

    b_hits = (builds.get("hits") or {}).get("hits", [])
    d_hits = (deploys.get("hits") or {}).get("hits", [])
    r_hits = (releases.get("hits") or {}).get("hits", [])
    dep_who = lambda s_: (s_.get("requester") or s_.get("Requester")  # noqa: E731
                          or s_.get("triggeredby") or "")
    board["top_users"] = {
        "build": _fold_stage(b_hits, _git_author),
        "release": _fold_stage(r_hits, lambda s_: re.sub(
            r"\s*<[^>]*>\s*$", "", s_.get("commitauthor") or "")),
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
                       "detail": s.get("technology") or ""})
    for h in (releases.get("hits") or {}).get("hits", []):
        s = h.get("_source") or {}
        events.append({"ts": s.get("releasedate") or "", "type": "release",
                       "app": s.get("application") or "", "env": "",
                       "status": s.get("status") or s.get("RLM_STATUS") or "",
                       "version": s.get("codeversion") or "",
                       "who": _user_display(re.sub(r"\s*<[^>]*>\s*$", "",
                                                   s.get("commitauthor") or "")),
                       "test": False, "detail": s.get("RLM") or ""})
    totals = {k: (((v.get("hits") or {}).get("total") or {}).get("value", 0))
              for k, v in (("builds", builds), ("deploys", deploys),
                           ("releases", releases))}
    return {"board": board, "events": events, "totals": totals}


def _assemble_events(out: dict) -> list[dict]:
    """The unified event log: cicd events + commits + Jira updates + Jira
    changelog folded into one newest-first stream. NOT capped — every event
    each source query returned is kept (sources fetch up to 1000 docs each;
    events_meta says when a source had even more)."""
    days = out.get("days") or 0
    cutoff = ((_now() - dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
              if days else "")
    events = list(((out.get("cicd") or {}).get("events")) or [])
    for c in ((out.get("commits") or {}).get("recent")) or []:
        events.append({"ts": c.get("when") or "", "type": "commit",
                       "app": c.get("repo") or "", "env": "",
                       "status": "", "version": c.get("id") or "",
                       "who": _user_display(c.get("author") or ""),
                       "test": False,
                       "detail": " · ".join(x for x in (c.get("branch"),
                                                        c.get("message")) if x),
                       "tip": c.get("message_full") or ""})
    for t in ((out.get("jira") or {}).get("recent")) or []:
        if cutoff and (t.get("updated") or "") < cutoff:
            continue   # jira tickets aren't window-filtered at query time
        events.append({"ts": t.get("updated") or "", "type": "jira",
                       "app": t.get("key") or "", "env": "",
                       "status": t.get("status") or "", "version": "",
                       "who": _user_display(t.get("assignee") or ""),
                       "test": False, "url": t.get("url") or "",
                       "detail": t.get("summary") or ""})
    for c in ((out.get("jira_changes") or {}).get("recent")) or []:
        events.append({"ts": c.get("when") or "", "type": "change",
                       "app": c.get("key") or "", "env": "",
                       "status": "", "version": "",
                       "who": c.get("author") or "", "test": False,
                       "url": c.get("url") or "",
                       "detail": "; ".join(f"{i['field']}: {i['from'] or '—'} → {i['to'] or '—'}"
                                           for i in c.get("items") or [])})
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
def _demo_report(name: str, days: int) -> dict:
    import hashlib
    seed = int(hashlib.sha1(name.encode()).hexdigest()[:6], 16)
    authors = [("alice", 34), ("bob", 21), ("carol", 12), ("dave", 6)]
    if days:
        base_day = dt.date.today() - dt.timedelta(days=days - 1)
        per_day = [{"day": (base_day + dt.timedelta(days=i)).isoformat(),
                    "count": (seed + i * 7) % 9 if i % 3 else 0}
                   for i in range(days)]
    else:  # all time — monthly buckets over ~2 years
        per_day = [{"day": f"{2024 + (m + 8) // 12}-{(m + 8) % 12 + 1:02d}-01",
                    "count": (seed + m * 13) % 60 + 5} for m in range(24)]
    total = sum(b["count"] for b in per_day)
    inv = _sec_inventory(name)
    repos = [a["repository_name"] for a in inv["apps"] if a.get("repository_name")]
    commits = {
        "total": total, "days": days, "per_day": per_day,
        "unit": "day" if days else "month",
        "rate": round(total / max(days or 720, 1), 2),
        "active_days": sum(1 for b in per_day if b["count"]),
        "authors": [{"key": a, "count": c} for a, c in authors],
        "repos": [{"key": r, "count": max(3, (seed + i * 13) % 40)}
                  for i, r in enumerate(repos)] or [{"key": "main-repo", "count": total}],
        "branches": [{"key": "develop", "count": int(total * .6)},
                     {"key": "main", "count": int(total * .4)}],
        "recent": [{"when": f"{per_day[-1]['day']} 10:0{i}", "repo": (repos or ['main-repo'])[i % max(len(repos), 1)],
                    "branch": "develop", "author": authors[i % 4][0],
                    "id": f"a1b2c3d{i}",
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
                             "version": f"1.4.{i + 1}", "rlm": f"RLM-10{i}"}, f"1.4.{i}")
                     for i, a in enumerate(apps[:2])},
        "deploys": {a: {e: _ok({"when": f"2026-08-2{2 + (i + j) % 4} 1{j}:30",
                                "status": "SUCCESS" if (i + j) % 5 else "FAILURE",
                                "version": f"1.4.{max(1, i + (1 if e != 'prd' else 0))}",
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
             "who": "alice", "test": i == 2, "detail": "Docker"},
        ]
    cicd_events.append({"ts": "2026-08-24T15:00", "type": "release", "app": apps[0],
                        "env": "", "status": "SUCCESS", "version": "1.4.1",
                        "who": "alice", "test": False, "detail": "RLM-100"})
    board["top_users"] = {
        "build": [{"key": "alice", "count": 9}, {"key": "bob", "count": 6}],
        "release": [{"key": "alice", "count": 3}, {"key": "carol", "count": 2}],
        "deploys": {e: [{"key": "alice", "count": 5 - j}, {"key": "bob", "count": 3 - j % 3}]
                    for j, e in enumerate(envs)},
    }
    cicd = {"board": board, "events": cicd_events,
            "totals": {"builds": 18 + seed % 9, "deploys": 11 + seed % 7,
                       "releases": 3 + seed % 3}}
    prev = None if not days else {
        "commits": max(total - 15, 5), "changes": 28, "builds": 21,
        "deploys": 12, "releases": 4, "resolved": 9}
    return {"commits": commits, "jira": jira, "jira_changes": changes,
            "scans": scans, "cicd": cicd, "prev": prev}


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

    _CACHE[ck] = {"at": time.time(), "payload": out}
    return {**out, "cached": False}


def invalidate() -> None:
    _CACHE.clear()
