"""
CI/CD Platform Command Center
==============================
Consolidated executive dashboard for the DevOps supervisor.

Design principles
-----------------
* **No sidebar.** All controls live in a slim command bar under the hero so the
  full width is reserved for data.
* **Cross-index first.** Every section joins at least two indices — the value of
  this view comes from correlating signals, not just counting rows.
* **Consolidated.** One screen, six dense sections, no duplicated content.
* **Professional aesthetic.** Refined dark theme, typographic hierarchy, glass
  cards, high-contrast status pills.

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

# Refined platform palette — professional, high-contrast on dark
C_SUCCESS = "#10b981"
C_DANGER  = "#f43f5e"
C_WARN    = "#f59e0b"
C_INFO    = "#60a5fa"
C_ACCENT  = "#a78bfa"
C_MUTED   = "#64748b"

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
    initial_sidebar_state="collapsed",
)

CUSTOM_CSS = """
<style>
/* -------- Layout -------- */
.main .block-container {
    padding-top: 1.4rem;
    padding-bottom: 3rem;
    max-width: 1680px;
}
h1, h2, h3, h4 {
    font-family: 'Inter', 'SF Pro Display', -apple-system, sans-serif;
    letter-spacing: -0.018em;
    font-feature-settings: "ss01", "cv11";
}

/* -------- Hero header -------- */
.hero {
    background:
        radial-gradient(1200px 300px at 10% -20%, rgba(124,92,255,0.35), transparent 60%),
        radial-gradient(800px 300px at 100% 0%, rgba(244,63,94,0.25), transparent 55%),
        linear-gradient(135deg, #0b1220 0%, #111827 55%, #1e1b4b 100%);
    padding: 30px 38px;
    border-radius: 20px;
    margin-bottom: 18px;
    color: #fff;
    border: 1px solid rgba(148,163,184,0.12);
    box-shadow: 0 18px 60px rgba(0,0,0,0.45);
    position: relative;
    overflow: hidden;
}
.hero::before {
    content: ''; position: absolute; inset: 0;
    background: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='60' height='60'><circle cx='1' cy='1' r='1' fill='white' fill-opacity='0.025'/></svg>");
}
.hero .eyebrow {
    display: inline-flex; align-items: center; gap: 8px;
    font-size: .72rem; letter-spacing: .18em; text-transform: uppercase;
    color: #c4b5fd; font-weight: 600;
    padding: 5px 12px;
    background: rgba(167,139,250,0.10);
    border: 1px solid rgba(167,139,250,0.25);
    border-radius: 999px;
}
.hero .eyebrow .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #10b981;
    box-shadow: 0 0 12px #10b981;
    animation: pulse 2.2s ease-in-out infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50%      { opacity: 0.35; }
}
.hero h1 {
    margin: 14px 0 6px 0;
    font-size: 2.25rem;
    font-weight: 700;
    color: #f8fafc;
    position: relative;
}
.hero .subtitle {
    color: #cbd5e1; opacity: 0.9;
    font-size: 1.02rem; max-width: 780px;
    position: relative;
}
.hero .meta {
    margin-top: 18px; font-size: .82rem;
    color: #94a3b8; position: relative;
    display: flex; flex-wrap: wrap; gap: 18px;
}
.hero .meta b { color: #e2e8f0; }

/* -------- Command bar -------- */
.cmdbar-label {
    font-size: .70rem; letter-spacing: .12em;
    text-transform: uppercase; color: #94a3b8;
    font-weight: 600; margin-bottom: 4px;
}

/* -------- KPI cards -------- */
.kpi {
    background: linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.015) 100%);
    border: 1px solid rgba(148,163,184,0.12);
    border-radius: 14px;
    padding: 18px 22px;
    height: 100%;
    backdrop-filter: blur(8px);
    transition: all .18s ease;
    position: relative;
    overflow: hidden;
}
.kpi::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
    background: linear-gradient(90deg, transparent, rgba(167,139,250,0.6), transparent);
    opacity: 0; transition: opacity .18s ease;
}
.kpi:hover {
    transform: translateY(-2px);
    border-color: rgba(167,139,250,0.45);
    box-shadow: 0 12px 32px rgba(0,0,0,0.35);
}
.kpi:hover::before { opacity: 1; }
.kpi .label {
    font-size: .70rem; text-transform: uppercase; letter-spacing: .10em;
    color: #94a3b8; font-weight: 600;
    display: flex; align-items: center; gap: 6px;
}
.kpi .value {
    font-size: 2.05rem; font-weight: 700; line-height: 1.1; margin-top: 6px;
    color: #f1f5f9;
    font-variant-numeric: tabular-nums;
}
.kpi .delta { font-size: .80rem; margin-top: 6px; font-weight: 500; }
.kpi .delta.up   { color: #34d399; }
.kpi .delta.dn   { color: #fb7185; }
.kpi .delta.flat { color: #94a3b8; }
.kpi .delta .arrow { display: inline-block; margin-right: 3px; }

/* -------- Section headers -------- */
.section {
    margin-top: 34px; margin-bottom: 10px;
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 10px;
    border-bottom: 1px solid rgba(148,163,184,0.12);
}
.section .title-wrap { display: flex; align-items: center; gap: 12px; }
.section h2 {
    margin: 0; font-size: 1.18rem; font-weight: 650;
    color: #f1f5f9;
}
.section .badge {
    font-size: .68rem; letter-spacing: .12em; text-transform: uppercase;
    padding: 3px 9px; border-radius: 6px;
    background: rgba(167,139,250,0.12);
    color: #c4b5fd; font-weight: 600;
    border: 1px solid rgba(167,139,250,0.25);
}
.section .hint { font-size: .78rem; color: #94a3b8; }

/* -------- Alert ribbon -------- */
.alert {
    padding: 11px 15px; border-radius: 10px; margin-bottom: 8px;
    border-left: 3px solid #f59e0b;
    background: linear-gradient(90deg, rgba(245,158,11,0.12), rgba(245,158,11,0.03));
    font-size: .90rem;
    display: flex; align-items: center; gap: 12px;
}
.alert .icon {
    width: 26px; height: 26px; border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 700; font-size: .85rem; flex-shrink: 0;
}
.alert.danger  { border-color: #f43f5e; background: linear-gradient(90deg, rgba(244,63,94,0.14), rgba(244,63,94,0.03)); }
.alert.danger .icon { background: rgba(244,63,94,0.25); color: #fda4af; }
.alert.info    { border-color: #60a5fa; background: linear-gradient(90deg, rgba(96,165,250,0.12), rgba(96,165,250,0.03)); }
.alert.info .icon { background: rgba(96,165,250,0.25); color: #93c5fd; }
.alert.success { border-color: #10b981; background: linear-gradient(90deg, rgba(16,185,129,0.12), rgba(16,185,129,0.03)); }
.alert.success .icon { background: rgba(16,185,129,0.25); color: #6ee7b7; }
.alert.warning .icon { background: rgba(245,158,11,0.25); color: #fcd34d; }
.alert b { font-weight: 650; color: #f1f5f9; }
.alert .sub { opacity: 0.78; font-size: .82rem; color: #cbd5e1; margin-left: 6px; }

/* -------- Insight / learn panel -------- */
.learn {
    background: linear-gradient(90deg, rgba(167,139,250,0.08), rgba(167,139,250,0.02));
    border-left: 3px solid #a78bfa;
    border-radius: 10px;
    padding: 11px 16px;
    font-size: .86rem; color: #cbd5e1;
    margin: 4px 0 18px 0;
}
.learn b { color: #f1f5f9; }

/* -------- Funnel visual -------- */
.funnel-wrap {
    background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
    border: 1px solid rgba(148,163,184,0.12);
    border-radius: 14px;
    padding: 20px 24px;
    height: 100%;
}
.funnel-stage {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 10px 0;
    border-bottom: 1px dashed rgba(148,163,184,0.12);
}
.funnel-stage:last-child { border-bottom: none; }
.funnel-stage .name { color: #cbd5e1; font-size: .90rem; font-weight: 500; }
.funnel-stage .value {
    font-size: 1.35rem; font-weight: 700; color: #f1f5f9;
    font-variant-numeric: tabular-nums;
}
.funnel-stage .conv { font-size: .75rem; color: #94a3b8; margin-left: 8px; }
.funnel-bar {
    height: 6px; border-radius: 3px; margin-top: 6px;
    background: linear-gradient(90deg, #a78bfa, #60a5fa);
    opacity: 0.85;
}

/* -------- Pills -------- */
.pill {
    display: inline-block;
    background: rgba(148,163,184,0.10);
    color: #e2e8f0;
    font-size: .70rem;
    padding: 3px 10px;
    border-radius: 999px;
    margin-right: 6px;
    font-weight: 500;
    border: 1px solid rgba(148,163,184,0.18);
}
.pill.green { background: rgba(16,185,129,.14);  color: #6ee7b7; border-color: rgba(16,185,129,.28); }
.pill.red   { background: rgba(244,63,94,.14);   color: #fda4af; border-color: rgba(244,63,94,.28); }
.pill.amber { background: rgba(245,158,11,.14);  color: #fcd34d; border-color: rgba(245,158,11,.28); }
.pill.blue  { background: rgba(96,165,250,.14);  color: #93c5fd; border-color: rgba(96,165,250,.28); }

/* -------- Streamlit widget overrides -------- */
div[data-testid="stSelectbox"] label,
div[data-testid="stTextInput"] label,
div[data-testid="stDateInput"] label {
    font-size: .70rem !important;
    text-transform: uppercase;
    letter-spacing: .10em;
    color: #94a3b8 !important;
    font-weight: 600 !important;
}
.stDataFrame { border-radius: 10px; overflow: hidden; }

/* -------- Hide Streamlit chrome -------- */
footer, #MainMenu, header[data-testid="stHeader"] { visibility: hidden; }
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
    """Search wrapper.

    Always enables ``track_total_hits`` so ``hits.total.value`` reflects the
    real cardinality — without this, Elasticsearch caps the count at 10,000
    and ``es_count`` would silently undercount large indices.
    """
    body = {**body, "track_total_hits": True}
    return cached_search(index, json.dumps(body, default=str, sort_keys=True), size)


def es_count(index: str, body: dict) -> int:
    res = es_search(index, body, size=0)
    return int(res.get("hits", {}).get("total", {}).get("value", 0) or 0)


def bucket_rows(res: dict, agg_name: str) -> list[dict]:
    return res.get("aggregations", {}).get(agg_name, {}).get("buckets", []) or []


# -----------------------------------------------------------------------------
# Composite aggregation paginator
# -----------------------------------------------------------------------------
# Elasticsearch ``terms`` aggregations force a fixed ``size``; any value above
# that is dropped. For queries that must be **exhaustive** (e.g. "every project
# in the inventory", "every project with at least one build in the last 90 days")
# we can't rely on ``terms`` — we use a composite aggregation and paginate with
# ``after_key``. This is the ES-native way to walk an entire cardinality.

COMPOSITE_PAGE = 1000      # buckets pulled per request
COMPOSITE_MAX_PAGES = 200  # safety brake: 200 × 1000 = 200k keys max


def composite_terms(
    index: str,
    field: str,
    query: dict,
    page_size: int = COMPOSITE_PAGE,
) -> dict[str, int]:
    """Walk a composite aggregation on ``field`` and return ``{key: doc_count}``.

    Parameters
    ----------
    index : str
        Elasticsearch index to target.
    field : str
        Keyword field to bucket on (must be aggregatable).
    query : dict
        The ``query`` clause (not a full body) — applied as a filter.
    page_size : int
        Buckets per page. 1000 is a safe default per ES docs.
    """
    result: dict[str, int] = {}
    after: dict | None = None
    for _ in range(COMPOSITE_MAX_PAGES):
        sources = [{"k": {"terms": {"field": field}}}]
        comp: dict[str, Any] = {"size": page_size, "sources": sources}
        if after:
            comp["after"] = after
        body = {
            "query": query,
            "aggs": {"groups": {"composite": comp}},
        }
        res = es_search(index, body, size=0)
        groups = res.get("aggregations", {}).get("groups", {}) or {}
        buckets = groups.get("buckets", []) or []
        if not buckets:
            break
        for b in buckets:
            key = b.get("key", {}).get("k")
            if key is not None:
                result[key] = b.get("doc_count", 0)
        after = groups.get("after_key")
        if not after:
            break
    return result


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
    hrs = delta.total_seconds() / 3600
    if hrs <= 6:       return "5m"
    if hrs <= 24:      return "30m"
    if hrs <= 24 * 7:  return "3h"
    if hrs <= 24 * 30: return "1d"
    return "1d"


def range_filter(field: str, start: datetime, end: datetime) -> dict:
    return {"range": {field: {"gte": start.isoformat(), "lte": end.isoformat()}}}


# =============================================================================
# HERO HEADER
# =============================================================================

st.markdown(
    f"""
    <div class="hero">
        <div class="eyebrow"><span class="dot"></span> LIVE · PRODUCTION CLUSTER</div>
        <h1>CI/CD Platform Command Center</h1>
        <div class="subtitle">
            A single pane of glass correlating builds, deployments, requests,
            commits, releases and tickets across the DevOps platform.
        </div>
        <div class="meta">
            <span>Refreshed · <b>{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC</b></span>
            <span>Cache TTL · <b>{CACHE_TTL // 60} min</b></span>
            <span>Indices tracked · <b>{len(IDX)}</b></span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# =============================================================================
# COMMAND BAR — inline controls replace the sidebar
# =============================================================================

cb = st.columns([2, 2, 2, 1.2, 1.2])

with cb[0]:
    preset = st.selectbox(
        "Time window", list(PRESETS.keys()), index=3,
    )

with cb[1]:
    company_filter = st.text_input(
        "Company scope", value="", placeholder="e.g. acme — optional"
    ).strip()

with cb[2]:
    project_filter = st.text_input(
        "Project scope", value="", placeholder="e.g. payments — optional"
    ).strip()

with cb[3]:
    st.markdown('<div class="cmdbar-label">Auto-refresh</div>', unsafe_allow_html=True)
    auto_refresh = st.toggle("60s", value=False, label_visibility="collapsed")

with cb[4]:
    st.markdown('<div class="cmdbar-label">&nbsp;</div>', unsafe_allow_html=True)
    if st.button("↻  Clear cache", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Custom range — revealed only when needed
if preset == "Custom":
    dr = st.columns([1, 1, 4])
    today = datetime.now(timezone.utc).date()
    d_start = dr[0].date_input("From", today - timedelta(days=7))
    d_end   = dr[1].date_input("To",   today)
    start_dt = datetime.combine(d_start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt   = datetime.combine(d_end,   datetime.max.time(), tzinfo=timezone.utc)
else:
    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - PRESETS[preset]  # type: ignore[operator]

delta       = end_dt - start_dt
prior_end   = start_dt
prior_start = start_dt - delta
interval    = pick_interval(delta)
now_utc     = datetime.now(timezone.utc)
pending_window_start = now_utc - timedelta(days=30)

st.caption(
    f"Showing **{start_dt:%Y-%m-%d %H:%M}** → **{end_dt:%Y-%m-%d %H:%M}** UTC "
    f"· histogram bucket **{interval}** "
    f"· compared against the equivalent prior window"
)


def scope_filters() -> list[dict]:
    fs: list[dict] = []
    if company_filter:
        fs.append({"term": {"company.keyword": company_filter}})
    if project_filter:
        fs.append({"term": {"project": project_filter}})
    return fs


def scope_filters_inv() -> list[dict]:
    fs: list[dict] = []
    if company_filter:
        fs.append({"term": {"company.keyword": company_filter}})
    if project_filter:
        fs.append({"term": {"project.keyword": project_filter}})
    return fs


# =============================================================================
# DATA — PLATFORM COUNTS
# =============================================================================

def fmt_delta(cur: int, prev: int) -> tuple[str, str]:
    if prev == 0:
        return ("new", "up") if cur else ("—", "flat")
    diff = cur - prev
    pct  = diff / prev * 100
    sign = "+" if diff >= 0 else ""
    direction = "up" if diff > 0 else ("dn" if diff < 0 else "flat")
    arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "→")
    return f'<span class="arrow">{arrow}</span>{sign}{diff:,} ({sign}{pct:.1f}%)', direction


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
# Change Failure Rate (DORA) — prd_fail / prd_deploys
cfr = (prd_fail / prd_deploys * 100) if prd_deploys else 0.0

# Deployment Frequency (DORA) — prod deploys per day across the window
days_in_window = max(delta.total_seconds() / 86400, 1/24)
deploy_freq_per_day = prd_deploys / days_in_window

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


# =============================================================================
# SECTION 1 — EXECUTIVE KPIs
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Executive KPIs</h2><span class="badge">DORA + Volume</span></div>'
    '<span class="hint">values vs prior equal window</span>'
    '</div>',
    unsafe_allow_html=True,
)

# Row 1 — four DORA-flavored cards (the headline story)
r1 = st.columns(4)
d, dn = fmt_delta(prd_deploys, count_with_range(
    IDX["deployments"], "startdate", prior_start, prior_end,
    extra=[{"term": {"environment": "prd"}}],
))
kpi_block(
    r1[0], "Deployment frequency",
    f"{deploy_freq_per_day:.1f} <span style='font-size:.95rem;color:#94a3b8;'>/ day</span>",
    d, dn,
    "DORA · prod deploys / day in window",
)
kpi_block(
    r1[1], "Change failure rate",
    f"{cfr:.1f}%",
    f"{prd_fail} failed / {prd_deploys} prod" if prd_deploys else "no prod deploys",
    "dn" if cfr > 15 else ("up" if prd_deploys else "flat"),
    "DORA · failed prod deploys / prod deploys",
)
kpi_block(
    r1[2], "Build success",
    f"{success_rate:.1f}%",
    f"{builds_fail:,} failed" if builds_fail else "all green",
    "dn" if builds_fail else "up",
    "(builds − failed) / builds",
)
kpi_block(
    r1[3], "Platform health",
    f"{active_projs}/{inv_count}" if inv_count else "—",
    f"{100 - dormant_pct:.0f}% active" if inv_count else "",
    "up" if dormant_pct < 30 else ("dn" if dormant_pct > 60 else "flat"),
    "active / inventory",
)

# Row 2 — volume row
r2 = st.columns(4)
d, dn = fmt_delta(builds_now, builds_prev)
kpi_block(r2[0], "Builds", f"{builds_now:,}", d, dn, "ef-cicd-builds")
d, dn = fmt_delta(deploys_now, deploys_prev)
kpi_block(r2[1], "Deployments", f"{deploys_now:,}", d, dn, "all environments")
d, dn = fmt_delta(commits_now, commits_prev)
kpi_block(r2[2], "Commits", f"{commits_now:,}", d, dn, "ef-git-commits")
d, dn = fmt_delta(rel_now, rel_prev)
kpi_block(r2[3], "Releases", f"{rel_now:,}", d, dn, "qc → uat promotions")

# Row 3 — queues / inventory row
r3 = st.columns(4)
d, dn = fmt_delta(reqs_now, reqs_prev)
kpi_block(r3[0], "Requests", f"{reqs_now:,}", d, dn, "ef-devops-requests")
kpi_block(
    r3[1], "Pending approvals", f"{pending_now:,}",
    "needs action" if pending_now else "clear",
    "dn" if pending_now else "up",
    "Pending in last 30 days",
)
kpi_block(r3[2], "Open JIRA", f"{open_jira:,}", "all-time open", "flat", "ef-bs-jira-issues")
kpi_block(
    r3[3], "Dormant projects",
    f"{dormant_pct:.0f}%" if inv_count else "—",
    f"{inv_count - active_projs:,} no activity" if inv_count else "",
    "dn" if dormant_pct > 40 else "flat",
    "(inventory − active) / inventory",
)


# =============================================================================
# SECTION 1b — TREND INSIGHTS (WoW / MoM / YoY)
# =============================================================================
# Independent of the user-selected time window: always shows rolling
# 7d / 30d / 365d against the equivalent immediately-prior period. This gives
# the supervisor a consistent macro view regardless of what they're drilling
# into above.

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Trend insights</h2><span class="badge">WoW · MoM · YoY</span></div>'
    '<span class="hint">rolling periods vs prior equal period</span>'
    '</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="learn">'
    '<b>How to read this:</b> each cell shows the count for the rolling period '
    '(last 7 / 30 / 365 days) followed by the percentage change versus the '
    '<i>immediately prior</i> equivalent period (the 7 days before the last 7, '
    'and so on). Green = up, red = down — <i>direction only</i>, read the metric '
    'before judging sentiment (more failures is not good).'
    '</div>',
    unsafe_allow_html=True,
)


def _trend_count(
    index: str, date_field: str, cur_start: datetime, cur_end: datetime,
    prev_start: datetime, prev_end: datetime,
    extra: list[dict] | None = None,
) -> tuple[int, int]:
    cur  = count_with_range(index, date_field, cur_start, cur_end, extra=extra)
    prev = count_with_range(index, date_field, prev_start, prev_end, extra=extra)
    return cur, prev


def _cell(cur: int, prev: int) -> str:
    """Render a compact "value · delta%" HTML cell."""
    if prev == 0 and cur == 0:
        return '<span style="color:#64748b;">—</span>'
    if prev == 0:
        return f'<b style="color:#f1f5f9;">{cur:,}</b> <span style="color:#34d399;">new</span>'
    diff = cur - prev
    pct  = diff / prev * 100
    direction = "#34d399" if diff > 0 else ("#fb7185" if diff < 0 else "#94a3b8")
    arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "→")
    sign = "+" if diff >= 0 else ""
    return (
        f'<b style="color:#f1f5f9;">{cur:,}</b> '
        f'<span style="color:{direction};font-size:.80rem;"> {arrow} {sign}{pct:.1f}%</span>'
    )


# Windows — relative to "now" so this section is stable regardless of the
# command-bar selection above.
_periods: list[tuple[str, timedelta]] = [
    ("WoW (7d)",   timedelta(days=7)),
    ("MoM (30d)",  timedelta(days=30)),
    ("YoY (365d)", timedelta(days=365)),
]

def _trend_windows(td: timedelta) -> tuple[datetime, datetime, datetime, datetime]:
    cur_end   = now_utc
    cur_start = cur_end - td
    prev_end  = cur_start
    prev_start = prev_end - td
    return cur_start, cur_end, prev_start, prev_end


# Metric definitions — (label, index, date_field, extra filters, good_direction)
_metrics = [
    ("Builds",             IDX["builds"],       "startdate",   None),
    ("Build failures",     IDX["builds"],       "startdate",   [{"terms": {"status": FAILED_STATUSES}}]),
    ("Deployments (all)",  IDX["deployments"],  "startdate",   None),
    ("Prod deployments",   IDX["deployments"],  "startdate",   [{"term":  {"environment": "prd"}}]),
    ("Prod failures",      IDX["deployments"],  "startdate",   [{"term":  {"environment": "prd"}}, {"terms": {"status": FAILED_STATUSES}}]),
    ("Commits",            IDX["commits"],      "commitdate",  None),
    ("Releases",           IDX["releases"],     "releasedate", None),
    ("Requests",           IDX["requests"],     "RequestDate", None),
]

trend_rows = []
for label, idx, dfield, extra in _metrics:
    row: dict[str, Any] = {"Metric": label}
    for period_label, td in _periods:
        cs, ce, ps, pe = _trend_windows(td)
        cur, prev = _trend_count(idx, dfield, cs, ce, ps, pe, extra=extra)
        row[period_label] = _cell(cur, prev)
    trend_rows.append(row)

# Render as a styled HTML table (st.dataframe would strip the HTML).
headers = ["Metric"] + [p[0] for p in _periods]
html = [
    '<div style="background:linear-gradient(180deg,rgba(255,255,255,0.03),rgba(255,255,255,0.01));'
    'border:1px solid rgba(148,163,184,0.12);border-radius:14px;padding:6px 4px;overflow:hidden;">',
    '<table style="width:100%;border-collapse:collapse;font-size:.90rem;">',
    '<thead><tr>',
]
for i, h in enumerate(headers):
    align = "left" if i == 0 else "right"
    html.append(
        f'<th style="text-align:{align};padding:12px 18px;color:#94a3b8;'
        f'font-size:.70rem;letter-spacing:.10em;text-transform:uppercase;'
        f'font-weight:600;border-bottom:1px solid rgba(148,163,184,0.12);">{h}</th>'
    )
html.append('</tr></thead><tbody>')

for row in trend_rows:
    html.append('<tr>')
    html.append(
        f'<td style="padding:12px 18px;color:#e2e8f0;border-bottom:1px solid rgba(148,163,184,0.06);">{row["Metric"]}</td>'
    )
    for period_label, _ in _periods:
        html.append(
            f'<td style="text-align:right;padding:12px 18px;font-variant-numeric:tabular-nums;'
            f'border-bottom:1px solid rgba(148,163,184,0.06);">{row[period_label]}</td>'
        )
    html.append('</tr>')
html.append('</tbody></table></div>')
st.markdown("".join(html), unsafe_allow_html=True)


# =============================================================================
# SECTION 2 — ALERT RIBBON
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Actionable alerts</h2><span class="badge">Live triage</span></div>'
    '<span class="hint">curated, high-signal conditions</span>'
    '</div>',
    unsafe_allow_html=True,
)

alerts: list[tuple[str, str, str, str]] = []  # (severity, icon, title, detail)

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
        "danger", "!",
        f"{stuck} approval request(s) pending for more than 24 hours",
        "Expedite, reassign or reject — see Workflow pulse below.",
    ))

# 2) Prod deploy failures in window
if prd_fail:
    alerts.append((
        "danger", "✕",
        f"{prd_fail} failed production deployment(s) in window",
        "Confirm rollback status in the Pipeline section below.",
    ))

# 3) Build success rate below 80%
if builds_now >= 20 and success_rate < 80:
    alerts.append((
        "warning", "▼",
        f"Build success rate is {success_rate:.1f}% (below 80% threshold)",
        "Inspect the builds-over-time chart for the drop.",
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
        "warning", "◷",
        f"{aged_jira} open JIRA issue(s) not updated in 30+ days",
        "Triage candidates for reassignment or closure.",
    ))

# 5) Commit spike — > 3× prior window
if commits_prev >= 20 and commits_now > 3 * commits_prev:
    alerts.append((
        "info", "↑",
        f"Commit spike: {commits_now:,} this window vs {commits_prev:,} prior",
        "Usually a release wave — cross-check with Top committers.",
    ))

# 6) Dormant ratio high
if inv_count and dormant_pct > 40:
    alerts.append((
        "info", "◌",
        f"{dormant_pct:.0f}% of inventory projects had no builds in the window",
        "Review the Operational hygiene section for cleanup candidates.",
    ))

if not alerts:
    st.markdown(
        '<div class="alert success">'
        '<div class="icon">✓</div>'
        '<div><b>All clear.</b><span class="sub">No actionable alerts in the current window.</span></div>'
        '</div>',
        unsafe_allow_html=True,
    )
else:
    for sev, icon, title, detail in alerts:
        css_cls = sev
        st.markdown(
            f'<div class="alert {css_cls}">'
            f'  <div class="icon">{icon}</div>'
            f'  <div><b>{title}</b><span class="sub">{detail}</span></div>'
            f'</div>',
            unsafe_allow_html=True,
        )


# =============================================================================
# SECTION 3 — CROSS-INDEX INSIGHTS
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Cross-index insights</h2><span class="badge">Joined signals</span></div>'
    '<span class="hint">correlating inventory × builds × deploys × jira × requests</span>'
    '</div>',
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="learn">'
    '<b>How to read this:</b> Each panel joins at least two indices. '
    'The <b>Delivery funnel</b> tracks attrition from code change to production. '
    'The <b>Project health scoreboard</b> aggregates every signal on a per-project basis so '
    'you can spot the worst-off projects at a glance. The <b>Risk spotlight</b> flags '
    'projects that fail multiple hygiene checks simultaneously.'
    '</div>',
    unsafe_allow_html=True,
)

ci1, ci2 = st.columns([1.1, 2])

# ---- Delivery funnel ------------------------------------------------------
with ci1:
    st.markdown(
        '<div class="funnel-wrap">'
        '<div style="font-size:.95rem;color:#e2e8f0;font-weight:600;margin-bottom:4px;">Delivery funnel</div>'
        '<div style="font-size:.78rem;color:#94a3b8;margin-bottom:14px;">code → build → prod deploy in window</div>',
        unsafe_allow_html=True,
    )

    stages = [
        ("Commits",            commits_now,             C_ACCENT),
        ("Builds",              builds_now,              C_INFO),
        ("Successful builds",   builds_now - builds_fail, C_SUCCESS),
        ("Deployments (all)",   deploys_now,             C_INFO),
        ("Production deploys",  prd_deploys,             C_SUCCESS),
    ]
    top = max(stages[0][1], 1)
    prev_val = None
    funnel_html = ""
    for name, val, color in stages:
        pct_of_top = (val / top * 100) if top else 0
        conv = ""
        if prev_val is not None and prev_val > 0:
            ratio = val / prev_val * 100
            conv = f'<span class="conv">· {ratio:.0f}% of prev</span>'
        funnel_html += (
            f'<div class="funnel-stage">'
            f'  <div><div class="name">{name}{conv}</div>'
            f'  <div class="funnel-bar" style="width:{pct_of_top:.0f}%;background:linear-gradient(90deg,{color},{C_INFO});"></div></div>'
            f'  <div class="value">{val:,}</div>'
            f'</div>'
        )
    st.markdown(funnel_html + "</div>", unsafe_allow_html=True)

# ---- Project health scoreboard --------------------------------------------
with ci2:
    st.markdown("**Project health scoreboard** — top 15 most active projects, joined across indices")

    # Pull per-project builds with success/fail breakdown, and per-project deploys
    body_b = {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "aggs": {
            "projs": {
                "terms": {"field": "project", "size": 50},
                "aggs": {
                    "fails": {"filter": {"terms": {"status": FAILED_STATUSES}}},
                    "last":  {"max": {"field": "startdate"}},
                },
            }
        },
    }
    res_b = es_search(IDX["builds"], body_b)

    body_d = {
        "query": {
            "bool": {
                "filter": [
                    range_filter("startdate", start_dt, end_dt),
                    {"term": {"environment": "prd"}},
                ] + scope_filters()
            }
        },
        "aggs": {"projs": {"terms": {"field": "project", "size": 200}}},
    }
    res_d = es_search(IDX["deployments"], body_d)
    prd_map = {b["key"]: b["doc_count"] for b in bucket_rows(res_d, "projs")}

    # JIRA open — per project
    body_j = {
        "query": {
            "bool": {
                "filter": scope_filters(),
                "must_not": [{"terms": {"status": CLOSED_JIRA}}],
            }
        },
        "aggs": {"projs": {"terms": {"field": "project", "size": 500}}},
    }
    res_j = es_search(IDX["jira"], body_j)
    jira_map = {b["key"]: b["doc_count"] for b in bucket_rows(res_j, "projs")}

    # Pending requests — per project (best effort — falls back to 0 if field missing)
    body_r = {
        "query": {
            "bool": {
                "filter": [
                    range_filter("RequestDate", pending_window_start, now_utc),
                    {"terms": {"Status": PENDING_STATUSES}},
                ]
            }
        },
        "aggs": {"projs": {"terms": {"field": "project", "size": 500}}},
    }
    res_r = es_search(IDX["requests"], body_r)
    pend_map = {b["key"]: b["doc_count"] for b in bucket_rows(res_r, "projs")}

    rows = []
    for bk in bucket_rows(res_b, "projs")[:15]:
        proj = bk["key"]
        total = bk["doc_count"]
        fails = bk.get("fails", {}).get("doc_count", 0)
        succ_pct = (total - fails) / total * 100 if total else 0
        last = bk.get("last", {}).get("value_as_string") or ""
        if last:
            try:
                last = pd.to_datetime(last).strftime("%m-%d %H:%M")
            except Exception:
                pass
        # Composite health score (0-100). Higher is better.
        score = succ_pct
        score -= min(jira_map.get(proj, 0), 20) * 1.5  # jira drag
        score -= min(pend_map.get(proj, 0), 10) * 3    # pending requests drag
        score = max(0, min(100, int(round(score))))
        rows.append({
            "Project":  proj,
            "Builds":   total,
            "Fails":    fails,
            "Succ %":   f"{succ_pct:.0f}%",
            "Prod dep": prd_map.get(proj, 0),
            "Open JIRA": jira_map.get(proj, 0),
            "Pending req": pend_map.get(proj, 0),
            "Last build": last,
            "Score":    score,
        })

    if rows:
        df_score = pd.DataFrame(rows).sort_values("Score", ascending=True)
        st.dataframe(
            df_score,
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={
                "Score": st.column_config.ProgressColumn(
                    "Health score", min_value=0, max_value=100, format="%d",
                ),
                "Builds":    st.column_config.NumberColumn(format="%d"),
                "Fails":     st.column_config.NumberColumn(format="%d"),
                "Prod dep":  st.column_config.NumberColumn(format="%d"),
                "Open JIRA": st.column_config.NumberColumn(format="%d"),
                "Pending req": st.column_config.NumberColumn(format="%d"),
            },
        )
        st.caption(
            "**Score** = build success − (open JIRA × 1.5) − (pending requests × 3), "
            "clamped 0–100. Lowest scores first."
        )
    else:
        st.info("No build activity in window.")

# ---- Risk spotlight — projects failing multiple hygiene checks -----------
st.markdown(
    '<div style="margin-top:18px;font-size:.95rem;color:#e2e8f0;font-weight:600;">'
    '⚠ Risk spotlight — projects failing multiple signals simultaneously'
    '</div>',
    unsafe_allow_html=True,
)

# Reuse the maps from above (if present) to flag cross-signal risk.
try:
    risk_rows = []
    _all_projs = set(prd_map) | set(jira_map) | set(pend_map)
    for bk in bucket_rows(res_b, "projs"):
        _all_projs.add(bk["key"])

    # Build a quick lookup for build stats
    build_stats = {
        bk["key"]: (
            bk["doc_count"],
            bk.get("fails", {}).get("doc_count", 0),
        )
        for bk in bucket_rows(res_b, "projs")
    }
    for proj in _all_projs:
        builds_t, fails_t = build_stats.get(proj, (0, 0))
        oj   = jira_map.get(proj, 0)
        pr   = pend_map.get(proj, 0)
        pd_d = prd_map.get(proj, 0)
        flags = []
        if builds_t and fails_t / max(builds_t, 1) > 0.2: flags.append("build-fail>20%")
        if oj >= 5:  flags.append(f"{oj} open JIRA")
        if pr >= 2:  flags.append(f"{pr} pending req")
        if pd_d and fails_t and builds_t and fails_t / builds_t > 0.3:
            flags.append("prod + failing")
        if len(flags) >= 2:
            risk_rows.append({
                "Project": proj,
                "Signals": " · ".join(flags),
                "Builds":  builds_t,
                "Fails":   fails_t,
                "JIRA":    oj,
                "Pending": pr,
            })
    if risk_rows:
        st.dataframe(
            pd.DataFrame(risk_rows).sort_values("Fails", ascending=False).head(10),
            use_container_width=True,
            hide_index=True,
            height=260,
        )
    else:
        st.markdown(
            '<div class="alert success">'
            '<div class="icon">✓</div>'
            '<div><b>No projects trigger multiple risk signals.</b>'
            '<span class="sub">Cross-signal hygiene is healthy.</span></div>'
            '</div>',
            unsafe_allow_html=True,
        )
except Exception as exc:
    st.info(f"Risk spotlight unavailable: {exc}")


# =============================================================================
# SECTION 4 — PIPELINE ACTIVITY
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Pipeline activity</h2><span class="badge">Time series</span></div>'
    '<span class="hint">builds &amp; deployments over time</span>'
    '</div>',
    unsafe_allow_html=True,
)

tab_builds, tab_deploys = st.tabs(["  Builds  ", "  Deployments  "])

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
            font=dict(color="#cbd5e1", family="Inter, sans-serif"),
            xaxis=dict(gridcolor="rgba(148,163,184,0.08)"),
            yaxis=dict(gridcolor="rgba(148,163,184,0.08)"),
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
            color_discrete_sequence=[C_ACCENT],
        )
        fig2.update_layout(
            height=380,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=40, b=0),
            font=dict(color="#cbd5e1", family="Inter, sans-serif"),
            xaxis=dict(gridcolor="rgba(148,163,184,0.08)"),
            yaxis=dict(gridcolor="rgba(148,163,184,0.08)"),
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
            font=dict(color="#cbd5e1", family="Inter, sans-serif"),
            xaxis=dict(gridcolor="rgba(148,163,184,0.08)"),
            yaxis=dict(gridcolor="rgba(148,163,184,0.08)"),
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
# SECTION 5 — WORKFLOW PULSE + OPERATIONAL HYGIENE (consolidated)
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Workflow pulse &amp; hygiene</h2><span class="badge">Who · What · Aging</span></div>'
    '<span class="hint">live queues and cleanup candidates</span>'
    '</div>',
    unsafe_allow_html=True,
)

wp_top = st.columns(3)

# ---- Pending requests ------------------------------------------------------
with wp_top[0]:
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
    res = es_search(IDX["requests"], body, size=12)
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
                "Age (h)":   age_h,
            })
        st.dataframe(
            pd.DataFrame(recs), use_container_width=True, hide_index=True, height=320
        )
    else:
        st.success("No pending requests.")

# ---- Top committers --------------------------------------------------------
with wp_top[1]:
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
                "Lines +": int(b.get("inserted", {}).get("value", 0) or 0),
            }
            for b in buckets
        ])
        st.dataframe(df, use_container_width=True, hide_index=True, height=320)
    else:
        st.info("No commits in window.")

# ---- Open JIRA by priority -------------------------------------------------
with wp_top[2]:
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
            df, names="Priority", values="Count", hole=0.62,
            color_discrete_sequence=["#f43f5e", "#f59e0b", "#a78bfa", "#60a5fa", "#10b981"],
        )
        fig.update_layout(
            height=320,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=0),
            font=dict(color="#cbd5e1", family="Inter, sans-serif"),
            legend=dict(orientation="v", x=1.02, y=0.5),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.success("No open JIRA issues.")

# ---- Hygiene row -----------------------------------------------------------
wp_bot = st.columns(3)

# Dormant projects — cross-joins inventory × builds (composite-paginated → exhaustive)
with wp_bot[0]:
    st.markdown("**Dormant projects** — no builds in 90 days")
    ninety_ago = now_utc - timedelta(days=90)

    inv_query = (
        {"bool": {"filter": scope_filters_inv()}}
        if scope_filters_inv() else {"match_all": {}}
    )
    inv_projs = set(composite_terms(IDX["inventory"], "project.keyword", inv_query).keys())

    act_query = {
        "bool": {
            "filter": [range_filter("startdate", ninety_ago, now_utc)] + scope_filters()
        }
    }
    active = set(composite_terms(IDX["builds"], "project", act_query).keys())

    dormant = sorted(inv_projs - active)
    if dormant:
        st.dataframe(
            pd.DataFrame({"project": dormant[:50]}),
            use_container_width=True, hide_index=True, height=260,
        )
        st.caption(
            f"Found **{len(dormant):,}** dormant. Candidates for archival."
        )
    else:
        st.success("No dormant projects detected.")

# Requests stuck > 7d
with wp_bot[1]:
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
    res = es_search(IDX["requests"], body, size=12)
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
                "Age (d)": age_d,
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=260
        )
    else:
        st.success("No long-running requests.")

# Aged JIRA issues
with wp_bot[2]:
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
    res = es_search(IDX["jira"], body, size=12)
    hits = res.get("hits", {}).get("hits", [])
    if hits:
        rows = []
        for h in hits:
            s = h["_source"]
            rows.append({
                "Key":      s.get("issuekey"),
                "Priority": s.get("priority"),
                "Assignee": s.get("assignee"),
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=260
        )
    else:
        st.success("No aged tickets.")


# =============================================================================
# SECTION 6 — LATEST ACTIVITY FEED
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Latest activity</h2><span class="badge">What changed</span></div>'
    '<span class="hint">newest events across deployments, releases and commits</span>'
    '</div>',
    unsafe_allow_html=True,
)

nw1, nw2, nw3 = st.columns(3)

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
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=320
        )
    else:
        st.info("No production deployments recorded.")

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
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=320
        )
    else:
        st.info("No releases found.")

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
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=320
        )
    else:
        st.info("No commits in window.")


# =============================================================================
# GLOSSARY
# =============================================================================

with st.expander("📖  Field guide · index reference · KPI formulas"):
    st.markdown(
        """
**ef-devops-inventory** — single source of truth for every project on the
CI/CD platform. Used as a lookup when enriching other events.

**ef-cicd-builds** — one document per CI build (Jenkins / GitHub Actions run).
Important fields: `status`, `duration`, `branch`, `codeversion`, `technology`,
`startdate`, `enddate`.

**ef-cicd-deployments** — one document per deployment attempt to an environment
(`dev`, `qc`, `uat`, `prd`). Production deployments drive DORA metrics here.

**ef-cicd-releases** — promotes a version from `qc` to `uat`. Tracks the RLM
status used by the release-management tooling.

**ef-devops-requests** — the **new** queue of approval / deployment requests.
`Status = Pending` is the actionable state.

**ef-cicd-approval** — the **legacy** queue, still active for historical data.

**ef-git-commits** — every commit that hits a tracked repo. Enrichments
include changed files, lines added/deleted and author details.

**ef-bs-jira-issues** — JIRA mirror for business/support tickets, letting us
join CI/CD events to business context.

**ef-cicd-versions-lookup** — auto-versioning lookup: given `project + branch`,
returns the next version to stamp on a build.

---

### Formulas

* **Deployment frequency** — prod deploys / days-in-window.
* **Change failure rate** — `prd_fail / prd_deploys`.
* **Build success %** — `(builds − failed) / builds`.
* **Platform health** — `active / inventory`, where *active* is `cardinality(project)`
  on `ef-cicd-builds` within the window.
* **Project health score** — `build_success − (open_jira × 1.5) − (pending_req × 3)`,
  clamped 0–100. Lower is worse.
* **Period-over-period delta** — same query on the immediately prior equal window.
        """
    )


# =============================================================================
# AUTO-REFRESH
# =============================================================================

if auto_refresh:
    st.markdown(
        '<meta http-equiv="refresh" content="60">',
        unsafe_allow_html=True,
    )
