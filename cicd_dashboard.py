"""
CI/CD Platform Command Center
==============================
Helicopter-view Streamlit dashboard for the DevOps supervisor.

What this page gives you
------------------------
* Real-time platform KPIs (builds, deployments, requests, releases, commits, JIRA)
  with period-over-period deltas against the prior equivalent window.
* Actionable alerts — stuck approvals, failed prod deploys, abnormal failure rate,
  aged tickets, commit spikes.
* Cleanup recommendations — dormant projects, long-running requests, aged JIRA.
* "What's new" feed — latest prod deployments, releases and commits.
* Learn panels for new joiners that explain each index and metric in plain English.

Performance notes
-----------------
* Every ES call is wrapped in a ``@st.cache_data`` layer keyed on the serialized
  query body; a 5 minute TTL keeps the dashboard fresh without hammering the cluster.
* All heavy queries use ``size=0`` and lean on aggregations — large indices are
  summarized server-side, never pulled into the browser.
* The date-histogram bucket is chosen automatically from the time window so we never
  ask the cluster for more than a few hundred buckets in a single chart.
* Non-essential sections are isolated: a single failing query falls back to an empty
  result and an "info" message instead of taking down the whole page.

Deployment
----------
Adjust the import below to match your repo layout. The page expects a module that
exposes an ``es_prd`` Elasticsearch client already wired to Vault credentials — the
same object you use in the rest of your tooling.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

# -----------------------------------------------------------------------------
# Elasticsearch client
# -----------------------------------------------------------------------------
# IMPORTANT: update this import to match where your workspace keeps the clients.
# The module is expected to export an ``es_prd`` (and optionally ``es_dev``)
# instance that has already been authenticated via Vault — see the snippet the
# DevOps team shared as the canonical init code.
from utils.elasticsearch_client import es_prd  # type: ignore  # noqa: F401


# =============================================================================
# CONSTANTS
# =============================================================================

IDX = {
    "inventory":   "ef-devops-inventory",
    "versions":    "ef-cicd-versions-lookup",
    "commits":     "ef-git-commits",
    "jira":        "ef-bs-jira-issues",
    "approval":    "ef-cicd-approval",      # legacy queue, still active
    "requests":    "ef-devops-requests",    # new queue
    "builds":      "ef-cicd-builds",
    "deployments": "ef-cicd-deployments",
    "releases":    "ef-cicd-releases",
}

CACHE_TTL = 300  # seconds — 5 minutes balances freshness vs cluster load
ES_TIMEOUT = 60  # seconds for individual search calls

# Platform color palette
C_SUCCESS = "#10b981"
C_DANGER  = "#ef4444"
C_WARN    = "#f59e0b"
C_INFO    = "#4f8cff"
C_ACCENT  = "#7c5cff"
C_MUTED   = "#6b7280"

STATUS_COLORS = {
    "SUCCESS":    C_SUCCESS, "SUCCEEDED": C_SUCCESS, "Success":   C_SUCCESS,
    "COMPLETED":  C_SUCCESS, "Approved":  C_SUCCESS, "APPROVED":  C_SUCCESS,
    "FAILED":     C_DANGER,  "FAILURE":   C_DANGER,  "Failed":    C_DANGER,
    "Rejected":   C_DANGER,  "REJECTED":  C_DANGER,
    "ABORTED":    C_MUTED,   "CANCELLED": C_MUTED,   "Cancelled": C_MUTED,
    "UNSTABLE":   C_WARN,    "Unstable":  C_WARN,
    "RUNNING":    C_INFO,    "IN_PROGRESS": C_INFO,  "Running":   C_INFO,
    "PENDING":    C_WARN,    "Pending":   C_WARN,
}

FAILED_STATUSES = ["FAILED", "FAILURE", "Failed", "failed"]
CLOSED_JIRA = ["Done", "Closed", "Resolved", "Cancelled", "Rejected"]
PENDING_STATUSES = ["Pending", "PENDING", "pending"]


# =============================================================================
# PAGE CONFIG & CUSTOM THEME
# =============================================================================

st.set_page_config(
    page_title="CI/CD Command Center",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
.main .block-container {
    padding-top: 1.2rem;
    padding-bottom: 3rem;
    max-width: 1600px;
}
h1, h2, h3, h4 {
    font-family: 'Inter', 'SF Pro Display', -apple-system, sans-serif;
    letter-spacing: -0.015em;
}

/* -------- Hero header -------- */
.hero {
    background: linear-gradient(135deg, #1e3a8a 0%, #4c1d95 55%, #831843 100%);
    padding: 28px 34px;
    border-radius: 18px;
    margin-bottom: 24px;
    color: #fff;
    box-shadow: 0 12px 40px rgba(0,0,0,0.35);
    position: relative;
    overflow: hidden;
}
.hero::after {
    content: ''; position: absolute; right: -80px; top: -80px;
    width: 280px; height: 280px;
    background: radial-gradient(circle, rgba(255,255,255,0.14), transparent 70%);
    border-radius: 50%;
}
.hero h1 { margin: 0; font-size: 2.1rem; font-weight: 700; color: #fff; }
.hero .subtitle { opacity: 0.85; margin-top: 6px; font-size: 1rem; }
.hero .meta { margin-top: 14px; font-size: 0.82rem; opacity: 0.7; }

/* -------- KPI cards -------- */
.kpi {
    background: rgba(255,255,255,0.03);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 14px;
    padding: 18px 22px;
    height: 100%;
    transition: transform .15s ease, border-color .15s ease;
}
.kpi:hover { transform: translateY(-2px); border-color: rgba(124,92,255,0.5); }
.kpi .label {
    font-size: .72rem; text-transform: uppercase; letter-spacing: .08em;
    color: #9ca3af; font-weight: 600;
}
.kpi .value { font-size: 2rem; font-weight: 700; line-height: 1.1; margin-top: 4px; }
.kpi .delta { font-size: .82rem; margin-top: 6px; }
.kpi .delta.up   { color: #10b981; }
.kpi .delta.dn   { color: #ef4444; }
.kpi .delta.flat { color: #9ca3af; }

/* -------- Section headers -------- */
.section {
    margin-top: 32px; margin-bottom: 8px;
    display: flex; align-items: baseline; justify-content: space-between;
    padding-bottom: 8px; border-bottom: 1px solid rgba(255,255,255,0.09);
}
.section h2 { margin: 0; font-size: 1.2rem; font-weight: 600; color: #e5e7eb; }
.section .hint { font-size: .78rem; color: #9ca3af; }

/* -------- Alert cards -------- */
.alert {
    padding: 12px 16px; border-radius: 10px; margin-bottom: 10px;
    border-left: 4px solid #f59e0b;
    background: rgba(245,158,11,0.08);
    font-size: .92rem;
}
.alert.danger  { border-color: #ef4444; background: rgba(239,68,68,.10); }
.alert.info    { border-color: #4f8cff; background: rgba(79,140,255,.09); }
.alert.success { border-color: #10b981; background: rgba(16,185,129,.09); }
.alert b { font-weight: 600; }
.alert .sub { opacity: 0.8; font-size: .85rem; display: block; margin-top: 3px; }

/* -------- Learn boxes -------- */
.learn {
    background: rgba(124,92,255,0.07);
    border-left: 3px solid #7c5cff;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: .85rem; color: #c7c9d3;
    margin: 6px 0 16px 0;
}
.learn b { color: #e5e7eb; }

.pill {
    display: inline-block;
    background: rgba(255,255,255,0.06);
    color: #d1d5db;
    font-size: .72rem;
    padding: 3px 10px;
    border-radius: 999px;
    margin-right: 6px;
}
.pill.green { background: rgba(16,185,129,.15);  color: #6ee7b7; }
.pill.red   { background: rgba(239,68,68,.15);   color: #fca5a5; }
.pill.amber { background: rgba(245,158,11,.15);  color: #fcd34d; }
.pill.blue  { background: rgba(79,140,255,.15);  color: #93c5fd; }

footer, #MainMenu { visibility: hidden; }
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


# =============================================================================
# ES HELPERS (cached)
# =============================================================================

def _run_search(index: str, body_json: str, size: int) -> dict:
    """Execute one search. Isolated so the caller can cache on JSON-serializable args."""
    body = json.loads(body_json)
    try:
        res = es_prd.search(index=index, body=body, size=size, request_timeout=ES_TIMEOUT)
        # ES 8.x returns an ObjectApiResponse; 7.x returns a dict-like Response.
        return res.body if hasattr(res, "body") else dict(res)
    except Exception as exc:
        return {
            "_error": str(exc),
            "hits": {"hits": [], "total": {"value": 0}},
            "aggregations": {},
        }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def cached_search(index: str, body_json: str, size: int = 0) -> dict:
    return _run_search(index, body_json, size)


def es_search(index: str, body: dict, size: int = 0) -> dict:
    """Search wrapper — serializes body for cache-friendliness, then delegates."""
    return cached_search(index, json.dumps(body, default=str, sort_keys=True), size)


def es_count(index: str, body: dict) -> int:
    res = es_search(index, body, size=0)
    return int(res.get("hits", {}).get("total", {}).get("value", 0) or 0)


def bucket_rows(res: dict, agg_name: str) -> list[dict]:
    return res.get("aggregations", {}).get(agg_name, {}).get("buckets", []) or []


# =============================================================================
# TIME WINDOW
# =============================================================================

PRESETS: dict[str, timedelta | None] = {
    "Last 1 hour":   timedelta(hours=1),
    "Last 6 hours":  timedelta(hours=6),
    "Last 24 hours": timedelta(days=1),
    "Last 7 days":   timedelta(days=7),
    "Last 30 days":  timedelta(days=30),
    "Last 90 days":  timedelta(days=90),
    "Custom":        None,
}


def pick_interval(delta: timedelta) -> str:
    """Choose a histogram bucket that keeps every chart under ~200 buckets."""
    hrs = delta.total_seconds() / 3600
    if hrs <= 6:      return "5m"
    if hrs <= 24:     return "30m"
    if hrs <= 24 * 7: return "3h"
    if hrs <= 24 * 30: return "1d"
    return "1d"


def range_filter(field: str, start: datetime, end: datetime) -> dict:
    return {"range": {field: {"gte": start.isoformat(), "lte": end.isoformat()}}}


# -----------------------------------------------------------------------------
# Sidebar controls
# -----------------------------------------------------------------------------

with st.sidebar:
    st.markdown("### Time window")
    preset = st.selectbox(
        "Range", list(PRESETS.keys()), index=3, label_visibility="collapsed"
    )
    if preset == "Custom":
        today = datetime.now(timezone.utc).date()
        c1, c2 = st.columns(2)
        d_start = c1.date_input("From", today - timedelta(days=7))
        d_end   = c2.date_input("To",   today)
        start_dt = datetime.combine(d_start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt   = datetime.combine(d_end,   datetime.max.time(), tzinfo=timezone.utc)
    else:
        end_dt   = datetime.now(timezone.utc)
        start_dt = end_dt - PRESETS[preset]  # type: ignore[operator]

    delta       = end_dt - start_dt
    prior_end   = start_dt
    prior_start = start_dt - delta
    interval    = pick_interval(delta)

    st.caption(f"From: `{start_dt:%Y-%m-%d %H:%M}` UTC")
    st.caption(f"To:   `{end_dt:%Y-%m-%d %H:%M}` UTC")
    st.caption(f"Histogram bucket: **{interval}**")

    st.markdown("---")
    st.markdown("### Controls")
    auto_refresh = st.toggle(
        "Auto-refresh (60s)", value=False,
        help="Adds a browser-level meta-refresh. Cached ES queries still honor the TTL.",
    )
    if st.button("Clear cache & reload", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("### Scope filters")
    company_filter = st.text_input("Company", value="", placeholder="optional").strip()
    project_filter = st.text_input("Project", value="", placeholder="optional").strip()
    st.caption("Filters apply to activity indices (builds, deploys, commits, JIRA).")

    st.markdown("---")
    with st.expander("About this dashboard"):
        st.markdown(
            "- **Source:** production Elasticsearch cluster (`es_prd`).\n"
            "- **Caching:** 5 minute TTL per unique query. Click *Clear cache* to bypass.\n"
            "- **New joiners:** every section has a **Learn** panel explaining the data.\n"
            "- **Issues?** confirm the `utils.elasticsearch_client` import path is correct."
        )


def scope_filters() -> list[dict]:
    """Filters reused across activity-index queries.

    The ``company`` field is mapped as ``text`` in several activity indices so we
    target the dynamic ``.keyword`` subfield. ``project`` is already ``keyword`` in
    every activity index we care about.
    """
    fs: list[dict] = []
    if company_filter:
        fs.append({"term": {"company.keyword": company_filter}})
    if project_filter:
        fs.append({"term": {"project": project_filter}})
    return fs


def scope_filters_inv() -> list[dict]:
    """Scope filters for ef-devops-inventory (text fields → .keyword subfields)."""
    fs: list[dict] = []
    if company_filter:
        fs.append({"term": {"company.keyword": company_filter}})
    if project_filter:
        fs.append({"term": {"project.keyword": project_filter}})
    return fs


# =============================================================================
# HERO HEADER
# =============================================================================

st.markdown(
    f"""
    <div class="hero">
        <h1>CI/CD Platform Command Center</h1>
        <div class="subtitle">
            A single pane of glass across builds, deployments, requests,
            commits, releases and tickets.
        </div>
        <div class="meta">
            Window: <b>{preset}</b> &nbsp;·&nbsp;
            {start_dt:%Y-%m-%d %H:%M} → {end_dt:%Y-%m-%d %H:%M} UTC &nbsp;·&nbsp;
            Bucket: {interval} &nbsp;·&nbsp;
            Refreshed: {datetime.now(timezone.utc):%H:%M:%S} UTC
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# KPI ROW
# =============================================================================

def fmt_delta(cur: int, prev: int) -> tuple[str, str]:
    if prev == 0:
        return ("new", "up") if cur else ("—", "flat")
    diff = cur - prev
    pct  = diff / prev * 100
    sign = "+" if diff >= 0 else ""
    direction = "up" if diff > 0 else ("dn" if diff < 0 else "flat")
    return f"{sign}{diff:,} ({sign}{pct:.1f}%)", direction


def kpi_block(col, label: str, value: Any, delta_text: str = "",
              direction: str = "flat", hint: str = "") -> None:
    dhtml = f'<div class="delta {direction}">{delta_text}</div>' if delta_text else ""
    title = f' title="{hint}"' if hint else ""
    col.markdown(
        f'<div class="kpi"{title}>'
        f'  <div class="label">{label}</div>'
        f'  <div class="value">{value}</div>'
        f'  {dhtml}'
        f'</div>',
        unsafe_allow_html=True,
    )


def count_with_range(
    index: str, field: str, s: datetime, e: datetime,
    extra: list[dict] | None = None,
) -> int:
    filters = [range_filter(field, s, e)] + scope_filters() + (extra or [])
    return es_count(index, {"query": {"bool": {"filter": filters}}})


# Look back 30 days for "currently pending" requests so we don't do a full-index scan.
pending_window_start = datetime.now(timezone.utc) - timedelta(days=30)
now_utc = datetime.now(timezone.utc)

# -- Builds ------------------------------------------------------------------
builds_now  = count_with_range(IDX["builds"], "startdate", start_dt, end_dt)
builds_prev = count_with_range(IDX["builds"], "startdate", prior_start, prior_end)
builds_fail = count_with_range(
    IDX["builds"], "startdate", start_dt, end_dt,
    extra=[{"terms": {"status": FAILED_STATUSES}}],
)
success_rate = ((builds_now - builds_fail) / builds_now * 100) if builds_now else 0.0

# -- Deployments -------------------------------------------------------------
deploys_now  = count_with_range(IDX["deployments"], "startdate", start_dt, end_dt)
deploys_prev = count_with_range(IDX["deployments"], "startdate", prior_start, prior_end)
prd_deploys  = count_with_range(
    IDX["deployments"], "startdate", start_dt, end_dt,
    extra=[{"term": {"environment": "prd"}}],
)
prd_fail = count_with_range(
    IDX["deployments"], "startdate", start_dt, end_dt,
    extra=[{"term": {"environment": "prd"}},
           {"terms": {"status": FAILED_STATUSES}}],
)

# -- Requests ----------------------------------------------------------------
reqs_now  = count_with_range(IDX["requests"], "RequestDate", start_dt, end_dt)
reqs_prev = count_with_range(IDX["requests"], "RequestDate", prior_start, prior_end)
pending_now = es_count(
    IDX["requests"],
    {
        "query": {
            "bool": {
                "filter": [
                    range_filter("RequestDate", pending_window_start, now_utc),
                    {"terms": {"Status": PENDING_STATUSES}},
                ]
            }
        }
    },
)

# -- Commits -----------------------------------------------------------------
commits_now  = count_with_range(IDX["commits"], "commitdate", start_dt, end_dt)
commits_prev = count_with_range(IDX["commits"], "commitdate", prior_start, prior_end)

# -- Releases ----------------------------------------------------------------
rel_now  = count_with_range(IDX["releases"], "releasedate", start_dt, end_dt)
rel_prev = count_with_range(IDX["releases"], "releasedate", prior_start, prior_end)

# -- JIRA open ---------------------------------------------------------------
open_jira = es_count(
    IDX["jira"],
    {
        "query": {
            "bool": {
                "filter": scope_filters(),
                "must_not": [{"terms": {"status": CLOSED_JIRA}}],
            }
        }
    },
)

# -- Inventory ---------------------------------------------------------------
inv_count = es_count(
    IDX["inventory"],
    {"query": {"bool": {"filter": scope_filters_inv()}}} if scope_filters_inv()
    else {"query": {"match_all": {}}},
)

# Active projects in window (via cardinality on builds)
active_res = es_search(
    IDX["builds"],
    {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "aggs": {"projects": {"cardinality": {"field": "project"}}},
    },
    size=0,
)
active_projs = int(
    active_res.get("aggregations", {}).get("projects", {}).get("value", 0) or 0
)
dormant_pct = (1 - active_projs / inv_count) * 100 if inv_count else 0

# -- Render KPIs -------------------------------------------------------------
st.markdown(
    '<div class="section">'
    '<h2>Platform KPIs</h2>'
    '<span class="hint">values vs prior equal window</span>'
    '</div>',
    unsafe_allow_html=True,
)

k1 = st.columns(6)
d, dn = fmt_delta(builds_now, builds_prev)
kpi_block(k1[0], "Builds", f"{builds_now:,}", d, dn, "All CI build executions in window")
kpi_block(
    k1[1], "Build success", f"{success_rate:.1f}%",
    f"{builds_fail:,} failed" if builds_fail else "all green",
    "dn" if builds_fail else "up",
    "Success = (builds − failed) / builds",
)
d, dn = fmt_delta(deploys_now, deploys_prev)
kpi_block(k1[2], "Deployments", f"{deploys_now:,}", d, dn, "All environments")
kpi_block(
    k1[3], "Prod deployments", f"{prd_deploys:,}",
    f"{prd_fail} failed" if prd_fail else "healthy",
    "dn" if prd_fail else "up",
    "environment = prd",
)
d, dn = fmt_delta(reqs_now, reqs_prev)
kpi_block(k1[4], "Requests", f"{reqs_now:,}", d, dn, "ef-devops-requests")
kpi_block(
    k1[5], "Pending approvals", f"{pending_now:,}",
    "needs action" if pending_now else "clear",
    "dn" if pending_now else "up",
    "Pending in last 30 days",
)

k2 = st.columns(6)
d, dn = fmt_delta(commits_now, commits_prev)
kpi_block(k2[0], "Commits", f"{commits_now:,}", d, dn)
d, dn = fmt_delta(rel_now, rel_prev)
kpi_block(k2[1], "Releases", f"{rel_now:,}", d, dn, "qc → uat promotions")
kpi_block(k2[2], "Open JIRA", f"{open_jira:,}", "all time", "flat")
kpi_block(k2[3], "Inventory projects", f"{inv_count:,}", "tracked", "flat")
kpi_block(k2[4], "Active projects", f"{active_projs:,}", "built in window", "flat")
kpi_block(
    k2[5], "Dormant %",
    f"{dormant_pct:.1f}%" if inv_count else "—",
    "no activity", "dn" if dormant_pct > 30 else "flat",
    "(inventory − active) / inventory",
)


# =============================================================================
# ACTIONABLE ALERTS
# =============================================================================

st.markdown(
    '<div class="section">'
    '<h2>Actionable alerts</h2>'
    '<span class="hint">curated, high-signal conditions</span>'
    '</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="learn">'
    '<b>Learn:</b> this panel runs a handful of targeted checks across the same '
    'indices — stuck approvals, failed prod deploys, abnormal failure rates, aged '
    'open tickets and commit spikes. Each card names a section further down the '
    'page where you can drill in.'
    '</div>',
    unsafe_allow_html=True,
)

alerts: list[tuple[str, str, str]] = []  # (severity, title, detail)

# 1) Approvals pending > 24h
stuck_cut = now_utc - timedelta(hours=24)
stuck_body = {
    "query": {
        "bool": {
            "filter": [
                {"range": {"RequestDate": {"gte": pending_window_start.isoformat(),
                                           "lte": stuck_cut.isoformat()}}},
                {"terms": {"Status": PENDING_STATUSES}},
            ]
        }
    }
}
stuck = es_count(IDX["requests"], stuck_body)
if stuck:
    alerts.append((
        "danger",
        f"{stuck} approval request(s) pending for more than 24 hours",
        "See the Workflow section below to expedite, reassign or reject.",
    ))

# 2) Prod deploy failures in window
if prd_fail:
    alerts.append((
        "danger",
        f"{prd_fail} failed production deployment(s) in window",
        "Jump to Pipeline activity → Deployments tab and confirm rollback status.",
    ))

# 3) Build success rate below 80%
if builds_now >= 20 and success_rate < 80:
    alerts.append((
        "warning",
        f"Build success rate is {success_rate:.1f}% (below 80% threshold)",
        "Inspect the builds-over-time chart and 'top projects' list below.",
    ))

# 4) JIRA not updated in 30d
aged_jira = es_count(
    IDX["jira"],
    {
        "query": {
            "bool": {
                "filter": [
                    {"range": {"updated": {"lte": (now_utc - timedelta(days=30)).isoformat()}}}
                ] + scope_filters(),
                "must_not": [{"terms": {"status": CLOSED_JIRA}}],
            }
        }
    },
)
if aged_jira:
    alerts.append((
        "warning",
        f"{aged_jira} open JIRA issue(s) not updated in 30+ days",
        "Triage candidates for reassignment or closure.",
    ))

# 5) Commit spike — > 3× prior window
if commits_prev >= 20 and commits_now > 3 * commits_prev:
    alerts.append((
        "info",
        f"Commit spike: {commits_now:,} this window vs {commits_prev:,} prior",
        "Usually a release wave — cross-check with Top committers below.",
    ))

# 6) Dormant ratio high
if inv_count and dormant_pct > 40:
    alerts.append((
        "info",
        f"{dormant_pct:.0f}% of inventory projects had no builds in the window",
        "Review the Cleanup recommendations section — candidates for archival.",
    ))

if not alerts:
    st.markdown(
        '<div class="alert success"><b>All clear.</b> '
        'No actionable alerts in the current window.</div>',
        unsafe_allow_html=True,
    )
else:
    for sev, title, detail in alerts:
        css_cls = "danger" if sev == "danger" else ("info" if sev == "info" else "")
        st.markdown(
            f'<div class="alert {css_cls}">'
            f'<b>{title}</b><span class="sub">{detail}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


# =============================================================================
# PIPELINE ACTIVITY — BUILDS & DEPLOYMENTS
# =============================================================================

st.markdown(
    '<div class="section">'
    '<h2>Pipeline activity</h2>'
    '<span class="hint">builds & deployments over time</span>'
    '</div>',
    unsafe_allow_html=True,
)

with st.expander("Learn: what is counted here", expanded=False):
    st.markdown(
        f"""
* **Builds** come from `ef-cicd-builds` — one document per CI build keyed on
  `startdate` and `status`. A build is *failed* when status ∈ {FAILED_STATUSES}.
* **Deployments** come from `ef-cicd-deployments` — one document per environment
  (`dev`, `qc`, `uat`, `prd`, …). Production deployments are broken out separately.
* The histogram bucket below is **{interval}**, chosen automatically from your
  time range so the chart stays readable.
* Aborted pipelines are **not** counted as failures — they are manual cancellations
  and appear in their own color.
        """
    )

tab_builds, tab_deploys = st.tabs(["Builds", "Deployments"])

# ---- Builds tab ------------------------------------------------------------
with tab_builds:
    body = {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "aggs": {
            "timeline": {
                "date_histogram": {
                    "field": "startdate",
                    "fixed_interval": interval,
                    "min_doc_count": 0,
                },
                "aggs": {"by_status": {"terms": {"field": "status", "size": 10}}},
            },
            "top_projects": {"terms": {"field": "project", "size": 10}},
            "by_tech":      {"terms": {"field": "technology", "size": 10}},
        },
    }
    res = es_search(IDX["builds"], body)

    rows = []
    for b in bucket_rows(res, "timeline"):
        for sb in b.get("by_status", {}).get("buckets", []):
            rows.append({
                "time": b["key_as_string"],
                "status": sb["key"],
                "count": sb["doc_count"],
            })

    c1, c2 = st.columns([2, 1])
    df_tl = pd.DataFrame(rows)
    if not df_tl.empty:
        df_tl["time"] = pd.to_datetime(df_tl["time"])
        fig = px.bar(
            df_tl, x="time", y="count", color="status",
            color_discrete_map=STATUS_COLORS,
            title=f"Builds over time ({interval} buckets)",
        )
        fig.update_layout(
            height=380,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.22),
            margin=dict(l=0, r=0, t=40, b=0),
            font=dict(color="#d1d5db"),
        )
        c1.plotly_chart(fig, use_container_width=True)
    else:
        c1.info("No builds in this window.")

    tops = bucket_rows(res, "top_projects")
    if tops:
        df_top = pd.DataFrame(
            [{"project": b["key"], "builds": b["doc_count"]} for b in tops]
        ).sort_values("builds")
        fig2 = px.bar(
            df_top, x="builds", y="project", orientation="h",
            title="Top projects by build count",
            color_discrete_sequence=[C_INFO],
        )
        fig2.update_layout(
            height=380,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=40, b=0),
            font=dict(color="#d1d5db"),
        )
        c2.plotly_chart(fig2, use_container_width=True)
    else:
        c2.info("No project data.")

    tech = bucket_rows(res, "by_tech")
    if tech:
        df_tech = pd.DataFrame(
            [{"technology": b["key"], "builds": b["doc_count"]} for b in tech]
        )
        st.markdown("**By technology**")
        st.dataframe(df_tech, use_container_width=True, hide_index=True)

# ---- Deployments tab -------------------------------------------------------
with tab_deploys:
    body = {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "aggs": {
            "timeline": {
                "date_histogram": {
                    "field": "startdate",
                    "fixed_interval": interval,
                    "min_doc_count": 0,
                },
                "aggs": {"by_env": {"terms": {"field": "environment", "size": 10}}},
            },
            "by_env_status": {
                "terms": {"field": "environment", "size": 10},
                "aggs": {"status": {"terms": {"field": "status", "size": 10}}},
            },
            "avg_duration": {
                "terms": {"field": "environment", "size": 10},
                "aggs": {"avg": {"avg": {"field": "hq_image_duration"}}},
            },
        },
    }
    res = es_search(IDX["deployments"], body)

    rows = []
    for b in bucket_rows(res, "timeline"):
        for sb in b.get("by_env", {}).get("buckets", []):
            rows.append({
                "time": b["key_as_string"],
                "environment": sb["key"],
                "count": sb["doc_count"],
            })
    df_tl = pd.DataFrame(rows)
    if not df_tl.empty:
        df_tl["time"] = pd.to_datetime(df_tl["time"])
        fig = px.area(
            df_tl, x="time", y="count", color="environment",
            title=f"Deployments over time ({interval} buckets)",
        )
        fig.update_layout(
            height=380,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            legend=dict(orientation="h", y=-0.22),
            margin=dict(l=0, r=0, t=40, b=0),
            font=dict(color="#d1d5db"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No deployments in this window.")

    env_rows = []
    for eb in bucket_rows(res, "by_env_status"):
        env = eb["key"]
        total = eb["doc_count"]
        statuses = {sb["key"]: sb["doc_count"] for sb in eb.get("status", {}).get("buckets", [])}
        failed = sum(v for k, v in statuses.items() if "FAIL" in str(k).upper())
        env_rows.append({
            "environment": env,
            "total": total,
            "failed": failed,
            "success_rate": f"{(total - failed) / total * 100:.1f}%" if total else "—",
        })
    if env_rows:
        st.markdown("**Per-environment health**")
        st.dataframe(pd.DataFrame(env_rows), use_container_width=True, hide_index=True)


# =============================================================================
# WORKFLOW & CONTRIBUTIONS
# =============================================================================

st.markdown(
    '<div class="section">'
    '<h2>Workflow &amp; contributions</h2>'
    '<span class="hint">who is pushing what, and what is waiting</span>'
    '</div>',
    unsafe_allow_html=True,
)

cq, cc, cj = st.columns(3)

# ---- Pending requests ------------------------------------------------------
with cq:
    st.markdown("**Pending approval requests**")
    body = {
        "query": {
            "bool": {
                "filter": [
                    range_filter("RequestDate", pending_window_start, now_utc),
                    {"terms": {"Status": PENDING_STATUSES}},
                ]
            }
        },
        "sort": [{"RequestDate": "asc"}],
    }
    res = es_search(IDX["requests"], body, size=15)
    hits = res.get("hits", {}).get("hits", [])
    if hits:
        recs = []
        for h in hits:
            s = h.get("_source", {})
            rd = s.get("RequestDate")
            age_h = None
            if rd:
                try:
                    age_h = int((now_utc - pd.to_datetime(rd)).total_seconds() / 3600)
                except Exception:
                    age_h = None
            recs.append({
                "#":         s.get("RequestNumber"),
                "Type":      s.get("RequestType"),
                "Requester": s.get("Requester"),
                "Team":      s.get("RequesterTeam"),
                "Age (h)":   age_h,
            })
        st.dataframe(
            pd.DataFrame(recs), use_container_width=True, hide_index=True, height=360
        )
    else:
        st.success("No pending requests.")

# ---- Top committers --------------------------------------------------------
with cc:
    st.markdown("**Top committers**")
    body = {
        "query": {
            "bool": {
                "filter": [range_filter("commitdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "aggs": {
            "top": {
                "terms": {"field": "authorname", "size": 10},
                "aggs": {"inserted": {"sum": {"field": "insertedlines"}}},
            }
        },
    }
    res = es_search(IDX["commits"], body)
    buckets = bucket_rows(res, "top")
    if buckets:
        df = pd.DataFrame([
            {
                "Author": b["key"],
                "Commits": b["doc_count"],
                "Lines added": int(b.get("inserted", {}).get("value", 0) or 0),
            }
            for b in buckets
        ])
        st.dataframe(df, use_container_width=True, hide_index=True, height=360)
    else:
        st.info("No commits in window.")

# ---- Open JIRA by priority -------------------------------------------------
with cj:
    st.markdown("**Open JIRA by priority**")
    body = {
        "query": {
            "bool": {
                "filter": scope_filters(),
                "must_not": [{"terms": {"status": CLOSED_JIRA}}],
            }
        },
        "aggs": {"prio": {"terms": {"field": "priority", "size": 10}}},
    }
    res = es_search(IDX["jira"], body)
    buckets = bucket_rows(res, "prio")
    if buckets:
        df = pd.DataFrame(
            [{"Priority": b["key"], "Count": b["doc_count"]} for b in buckets]
        )
        fig = px.pie(
            df, names="Priority", values="Count", hole=0.55,
            color_discrete_sequence=px.colors.sequential.Plasma_r,
        )
        fig.update_layout(
            height=360,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=0),
            font=dict(color="#d1d5db"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.success("No open JIRA issues.")


# =============================================================================
# CLEANUP RECOMMENDATIONS
# =============================================================================

st.markdown(
    '<div class="section">'
    '<h2>Cleanup recommendations</h2>'
    '<span class="hint">hygiene opportunities</span>'
    '</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="learn">'
    '<b>Learn:</b> this section cross-references the <b>inventory</b> against '
    'recent activity to flag projects that look dormant, requests that never '
    'closed, and JIRA that aged out. Act on them to keep Jenkins jobs and '
    'indices lean.'
    '</div>',
    unsafe_allow_html=True,
)

cu1, cu2, cu3 = st.columns(3)

# ---- Dormant projects ------------------------------------------------------
with cu1:
    st.markdown("**Dormant projects** — no builds in 90 days")
    ninety_ago = now_utc - timedelta(days=90)

    inv_body = {
        "query": {"bool": {"filter": scope_filters_inv()}} if scope_filters_inv()
                 else {"match_all": {}},
        "aggs": {"projs": {"terms": {"field": "project.keyword", "size": 1000}}},
    }
    inv_res = es_search(IDX["inventory"], inv_body)
    inv_projs = {b["key"] for b in bucket_rows(inv_res, "projs")}

    act_body = {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", ninety_ago, now_utc)] + scope_filters()
            }
        },
        "aggs": {"projs": {"terms": {"field": "project", "size": 2000}}},
    }
    act_res = es_search(IDX["builds"], act_body)
    active  = {b["key"] for b in bucket_rows(act_res, "projs")}

    dormant = sorted(inv_projs - active)
    if dormant:
        st.dataframe(
            pd.DataFrame({"project": dormant[:50]}),
            use_container_width=True, hide_index=True, height=300,
        )
        st.caption(
            f"Found **{len(dormant):,}** dormant — showing first 50. "
            "Candidates for archival or decommission."
        )
    else:
        st.success("No dormant projects detected.")

# ---- Long-running requests -------------------------------------------------
with cu2:
    st.markdown("**Requests stuck > 7 days**")
    week_ago = now_utc - timedelta(days=7)
    body = {
        "query": {
            "bool": {
                "filter": [
                    {"range": {"RequestDate": {"lte": week_ago.isoformat(),
                                               "gte": (now_utc - timedelta(days=120)).isoformat()}}},
                    {"terms": {"Status": PENDING_STATUSES + ["InProgress", "IN_PROGRESS"]}},
                ]
            }
        },
        "sort": [{"RequestDate": "asc"}],
    }
    res = es_search(IDX["requests"], body, size=15)
    hits = res.get("hits", {}).get("hits", [])
    if hits:
        rows = []
        for h in hits:
            s = h["_source"]
            age_d = None
            if s.get("RequestDate"):
                try:
                    age_d = (now_utc - pd.to_datetime(s["RequestDate"])).days
                except Exception:
                    age_d = None
            rows.append({
                "#":       s.get("RequestNumber"),
                "Type":    s.get("RequestType"),
                "Requester": s.get("Requester"),
                "Age (d)": age_d,
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=300
        )
    else:
        st.success("No long-running requests.")

# ---- Aged JIRA issues ------------------------------------------------------
with cu3:
    st.markdown("**Aged open JIRA** — created > 90 days ago")
    body = {
        "query": {
            "bool": {
                "filter": [
                    {"range": {"created": {"lte": (now_utc - timedelta(days=90)).isoformat()}}}
                ] + scope_filters(),
                "must_not": [{"terms": {"status": CLOSED_JIRA}}],
            }
        },
        "sort": [{"created": "asc"}],
    }
    res = es_search(IDX["jira"], body, size=15)
    hits = res.get("hits", {}).get("hits", [])
    if hits:
        rows = []
        for h in hits:
            s = h["_source"]
            rows.append({
                "Key":      s.get("issuekey"),
                "Priority": s.get("priority"),
                "Status":   s.get("status"),
                "Assignee": s.get("assignee"),
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=300
        )
    else:
        st.success("No aged tickets.")


# =============================================================================
# WHAT'S NEW — LATEST ACTIVITY FEED
# =============================================================================

st.markdown(
    '<div class="section">'
    '<h2>What\'s new</h2>'
    '<span class="hint">latest events across the platform</span>'
    '</div>',
    unsafe_allow_html=True,
)

nw1, nw2, nw3 = st.columns(3)

# ---- Latest prod deployments ----------------------------------------------
with nw1:
    st.markdown("**Latest production deployments**")
    body = {
        "query": {
            "bool": {
                "filter": [{"term": {"environment": "prd"}}] + scope_filters()
            }
        },
        "sort": [{"startdate": "desc"}],
    }
    res = es_search(IDX["deployments"], body, size=8)
    hits = res.get("hits", {}).get("hits", [])
    if hits:
        rows = []
        for h in hits:
            s = h["_source"]
            when = ""
            if s.get("startdate"):
                try:
                    when = pd.to_datetime(s["startdate"]).strftime("%m-%d %H:%M")
                except Exception:
                    pass
            rows.append({
                "Project": s.get("project"),
                "Version": s.get("codeversion"),
                "Status":  s.get("status"),
                "When":    when,
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=340
        )
    else:
        st.info("No production deployments recorded.")

# ---- Latest releases -------------------------------------------------------
with nw2:
    st.markdown("**Latest releases (qc → uat)**")
    body = {
        "query": {"bool": {"filter": scope_filters()}} if scope_filters()
                 else {"match_all": {}},
        "sort": [{"releasedate": "desc"}],
    }
    res = es_search(IDX["releases"], body, size=8)
    hits = res.get("hits", {}).get("hits", [])
    if hits:
        rows = []
        for h in hits:
            s = h["_source"]
            when = ""
            if s.get("releasedate"):
                try:
                    when = pd.to_datetime(s["releasedate"]).strftime("%m-%d %H:%M")
                except Exception:
                    pass
            rows.append({
                "App":     s.get("application"),
                "Version": s.get("codeversion"),
                "RLM":     s.get("RLM_STATUS"),
                "When":    when,
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=340
        )
    else:
        st.info("No releases found.")

# ---- Latest commits --------------------------------------------------------
with nw3:
    st.markdown("**Latest commits**")
    body = {
        "query": {
            "bool": {
                "filter": [range_filter("commitdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "sort": [{"commitdate": "desc"}],
    }
    res = es_search(IDX["commits"], body, size=8)
    hits = res.get("hits", {}).get("hits", [])
    if hits:
        rows = []
        for h in hits:
            s = h["_source"]
            when = ""
            if s.get("commitdate"):
                try:
                    when = pd.to_datetime(s["commitdate"]).strftime("%m-%d %H:%M")
                except Exception:
                    pass
            rows.append({
                "Repo":   s.get("repository"),
                "Branch": s.get("branch"),
                "Author": s.get("authorname"),
                "When":   when,
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=340
        )
    else:
        st.info("No commits in window.")


# =============================================================================
# GLOSSARY — FOR NEW JOINERS
# =============================================================================

st.markdown(
    '<div class="section">'
    '<h2>Glossary &amp; index reference</h2>'
    '<span class="hint">for new joiners</span>'
    '</div>',
    unsafe_allow_html=True,
)

with st.expander("Show the field guide", expanded=False):
    st.markdown(
        """
**ef-devops-inventory** — the single source of truth for every project on the
CI/CD platform. Each row is a project and its configuration (datasources, hosts,
deploy technology, JVM knobs, …). Used as a **lookup** table when enriching
other events.

**ef-cicd-builds** — one document per CI build (Jenkins / GitHub Actions run).
Important fields: `status`, `duration`, `branch`, `codeversion`, `technology`,
`startdate`, `enddate`. This is the table we lean on to answer
*"is the pipeline healthy?"*.

**ef-cicd-deployments** — one document per deployment attempt to an environment
(`dev`, `qc`, `uat`, `prd`). The `environment` field is the most important
dimension; production deployments are special-cased throughout this dashboard.

**ef-cicd-releases** — promotes a version from `qc` to `uat`. Tracks the RLM
status used by the release-management tooling.

**ef-devops-requests** — the **new** queue of approval / deployment requests
coming into the platform. `Status = Pending` is the actionable state. Nested
`RequestParams` holds the per-request payload.

**ef-cicd-approval** — the **legacy** queue, still active for historical data.

**ef-git-commits** — every commit that hits a tracked repo. Enrichments include
changed files, lines added/deleted and author details.

**ef-bs-jira-issues** — JIRA mirror for business/support tickets, letting us
join CI/CD events to business context.

**ef-cicd-versions-lookup** — auto-versioning lookup: given `project + branch`,
returns the next version to stamp on a build. Read-only from this dashboard.
        """
    )

with st.expander("How the KPIs are computed", expanded=False):
    st.markdown(
        """
* **Builds / Deployments / Commits / Requests / Releases** — `count()` on the
  matching index, scoped to `startdate` / `commitdate` / `RequestDate` /
  `releasedate` within the time window.
* **Build success %** — `(builds − failed) / builds` where *failed* is any
  status in `FAILED | FAILURE | Failed | failed`.
* **Pending approvals** — requests with `Status ∈ {Pending}` looked back 30
  days (to avoid a full-index scan) at query time, not in the selected window.
* **Active projects** — `cardinality(project)` on `ef-cicd-builds` within the
  window.
* **Dormant %** — `1 − (active / inventory)`. Dormant projects are listed
  (capped at 50) in *Cleanup recommendations*.
* **Period-over-period delta** — the same query run on the immediately prior
  equal window (`start - delta → start`).
        """
    )


# =============================================================================
# AUTO-REFRESH
# =============================================================================

if auto_refresh:
    # Browser-level refresh — keeps the page responsive and avoids a blocking sleep.
    st.markdown(
        '<meta http-equiv="refresh" content="60">',
        unsafe_allow_html=True,
    )
