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
import plotly.graph_objects as go
import streamlit as st

# -----------------------------------------------------------------------------
# Elasticsearch client
# -----------------------------------------------------------------------------
from utils.elasticsearch import es_prd  # type: ignore  # noqa: F401


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

/* -------- KPI cards — light-theme-safe -------- */
.kpi {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    padding: 18px 22px;
    height: 100%;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06), 0 4px 12px rgba(0,0,0,0.04);
    transition: all .18s ease;
    position: relative;
    overflow: hidden;
}
.kpi::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, #a78bfa, #60a5fa);
    opacity: 0; transition: opacity .18s ease;
}
.kpi:hover {
    transform: translateY(-2px);
    border-color: #a78bfa;
    box-shadow: 0 4px 20px rgba(167,139,250,0.18), 0 1px 4px rgba(0,0,0,0.06);
}
.kpi:hover::before { opacity: 1; }
.kpi .label {
    font-size: .70rem; text-transform: uppercase; letter-spacing: .10em;
    color: #64748b; font-weight: 600;
    display: flex; align-items: center; gap: 6px;
}
.kpi .value {
    font-size: 2.05rem; font-weight: 700; line-height: 1.1; margin-top: 6px;
    color: #0f172a;
    font-variant-numeric: tabular-nums;
}
.kpi .delta { font-size: .80rem; margin-top: 6px; font-weight: 500; }
.kpi .delta.up   { color: #059669 !important; }
.kpi .delta.dn   { color: #dc2626 !important; }
.kpi .delta.flat { color: #94a3b8 !important; }
.kpi .delta .arrow { display: inline-block; margin-right: 3px; }

/* -------- Section headers -------- */
.section {
    margin-top: 34px; margin-bottom: 10px;
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 10px;
    border-bottom: 2px solid #e2e8f0;
}
.section .title-wrap { display: flex; align-items: center; gap: 12px; }
.section h2 {
    margin: 0; font-size: 1.18rem; font-weight: 650;
    color: #0f172a;
}
.section .badge {
    font-size: .68rem; letter-spacing: .12em; text-transform: uppercase;
    padding: 3px 9px; border-radius: 6px;
    background: #ede9fe;
    color: #6d28d9; font-weight: 600;
    border: 1px solid #ddd6fe;
}
.section .hint { font-size: .78rem; color: #64748b; }

/* -------- Alert ribbon — vivid, solid icon chips -------- */
.alert {
    padding: 10px 14px; border-radius: 10px; margin-bottom: 7px;
    border-left: 4px solid #d97706;
    background: #fffbeb;
    font-size: .88rem;
    display: flex; align-items: center; gap: 12px;
    color: #1e293b;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}
.alert .icon {
    width: 28px; height: 28px; border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: .82rem; flex-shrink: 0;
    /* default: amber solid */
    background: #d97706 !important; color: #ffffff !important;
}
/* danger — vivid red */
.alert.danger  { border-left-color: #dc2626 !important; background: #fff1f2 !important; }
.alert.danger .icon { background: #dc2626 !important; color: #ffffff !important; }
.alert.danger b  { color: #7f1d1d !important; }
/* warning — vivid amber */
.alert.warning { border-left-color: #d97706 !important; background: #fffbeb !important; }
.alert.warning .icon { background: #d97706 !important; color: #ffffff !important; }
.alert.warning b { color: #78350f !important; }
/* info — vivid blue */
.alert.info    { border-left-color: #2563eb !important; background: #eff6ff !important; }
.alert.info .icon { background: #2563eb !important; color: #ffffff !important; }
.alert.info b  { color: #1e3a8a !important; }
/* success — vivid green */
.alert.success { border-left-color: #16a34a !important; background: #f0fdf4 !important; }
.alert.success .icon { background: #16a34a !important; color: #ffffff !important; }
.alert.success b { color: #14532d !important; }
/* shared text */
.alert b   { font-weight: 700; }
.alert .sub { font-size: .82rem; color: #475569 !important; margin-left: 4px; }

/* -------- Insight / learn panel -------- */
.learn {
    background: #f5f3ff;
    border-left: 3px solid #7c3aed;
    border-radius: 10px;
    padding: 11px 16px;
    font-size: .86rem; color: #374151;
    margin: 4px 0 18px 0;
}
.learn b { color: #1e293b; }

/* -------- Funnel visual -------- */
.funnel-wrap {
    background: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 14px;
    padding: 20px 24px;
    height: 100%;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}
.funnel-stage {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 10px 0;
    border-bottom: 1px dashed #e2e8f0;
}
.funnel-stage:last-child { border-bottom: none; }
.funnel-stage .name { color: #374151; font-size: .90rem; font-weight: 500; }
.funnel-stage .value {
    font-size: 1.35rem; font-weight: 700; color: #0f172a;
    font-variant-numeric: tabular-nums;
}
.funnel-stage .conv { font-size: .75rem; color: #64748b; margin-left: 8px; }
.funnel-bar {
    height: 6px; border-radius: 3px; margin-top: 6px;
    background: linear-gradient(90deg, #7c3aed, #2563eb);
    opacity: 0.75;
}

/* -------- Pills -------- */
.pill {
    display: inline-block;
    background: #f1f5f9;
    color: #334155;
    font-size: .70rem;
    padding: 3px 10px;
    border-radius: 999px;
    margin-right: 6px;
    font-weight: 500;
    border: 1px solid #e2e8f0;
}
.pill.green { background: #dcfce7 !important;  color: #065f46 !important; border-color: #86efac !important; }
.pill.red   { background: #fee2e2 !important;  color: #991b1b !important; border-color: #fca5a5 !important; }
.pill.amber { background: #fef3c7 !important;  color: #92400e !important; border-color: #fcd34d !important; }
.pill.blue  { background: #dbeafe !important;  color: #1e40af !important; border-color: #93c5fd !important; }

/* -------- Streamlit widget overrides -------- */
div[data-testid="stSelectbox"] label,
div[data-testid="stTextInput"] label,
div[data-testid="stDateInput"] label {
    font-size: .70rem !important;
    text-transform: uppercase;
    letter-spacing: .10em;
    color: #64748b !important;
    font-weight: 600 !important;
}
.stDataFrame { border-radius: 10px; overflow: hidden; }

/* -------- Hide Streamlit chrome -------- */
footer, #MainMenu, header[data-testid="stHeader"] { visibility: hidden; }

/* =============================================================== *
 *  COLOR FIDELITY OVERRIDES                                        *
 *  A custom Streamlit theme can clobber reds / greens via base     *
 *  color variables and .stAlert defaults. Everything below is      *
 *  forced with !important so our palette wins regardless of what   *
 *  config.toml declares.                                           *
 * =============================================================== */

/* KPI deltas */
.kpi .delta.up   { color: #059669 !important; }
.kpi .delta.dn   { color: #dc2626 !important; }
.kpi .delta.flat { color: #94a3b8 !important; }
.kpi .value      { color: #0f172a !important; }
.kpi .label      { color: #64748b !important; }

/* Alert ribbon — enforce vivid solid icons + saturated backgrounds */
.alert          { color: #1e293b !important; }
.alert b        { font-weight: 700 !important; }
.alert .sub     { color: #475569 !important; }

.alert.success       { border-left-color: #16a34a !important; background: #f0fdf4 !important; }
.alert.success .icon { background: #16a34a !important; color: #ffffff !important; }
.alert.success b     { color: #14532d !important; }

.alert.danger        { border-left-color: #dc2626 !important; background: #fff1f2 !important; }
.alert.danger .icon  { background: #dc2626 !important; color: #ffffff !important; }
.alert.danger b      { color: #7f1d1d !important; }

.alert.warning       { border-left-color: #d97706 !important; background: #fffbeb !important; }
.alert.warning .icon { background: #d97706 !important; color: #ffffff !important; }
.alert.warning b     { color: #78350f !important; }

.alert.info          { border-left-color: #2563eb !important; background: #eff6ff !important; }
.alert.info .icon    { background: #2563eb !important; color: #ffffff !important; }
.alert.info b        { color: #1e3a8a !important; }

/* Pills */
.pill.green { background: rgba(16,185,129,.16) !important;  color: #6ee7b7 !important; border-color: rgba(16,185,129,.32) !important; }
.pill.red   { background: rgba(244,63,94,.16) !important;   color: #fda4af !important; border-color: rgba(244,63,94,.32) !important; }
.pill.amber { background: rgba(245,158,11,.16) !important;  color: #fcd34d !important; border-color: rgba(245,158,11,.32) !important; }
.pill.blue  { background: rgba(96,165,250,.16) !important;  color: #93c5fd !important; border-color: rgba(96,165,250,.32) !important; }

/* Neutralize Streamlit's own st.success / st.info / st.warning / st.error
   — the theme often remaps their accent colors. We repaint them to match. */
div[data-testid="stAlert"][data-baseweb="notification"] { border-radius: 10px !important; }
div[data-testid="stAlertContentSuccess"],
div[data-baseweb="notification"][kind="positive"] {
    background: #f0fdf4 !important;
    border: 1px solid #86efac !important;
    color: #065f46 !important;
}
div[data-testid="stAlertContentInfo"],
div[data-baseweb="notification"][kind="info"] {
    background: #eff6ff !important;
    border: 1px solid #93c5fd !important;
    color: #1e40af !important;
}
div[data-testid="stAlertContentWarning"],
div[data-baseweb="notification"][kind="warning"] {
    background: #fffbeb !important;
    border: 1px solid #fcd34d !important;
    color: #92400e !important;
}
div[data-testid="stAlertContentError"],
div[data-baseweb="notification"][kind="negative"] {
    background: #fff1f2 !important;
    border: 1px solid #fca5a5 !important;
    color: #991b1b !important;
}

/* Popover trigger buttons */
div[data-testid="stPopover"] button {
    background: #f5f3ff !important;
    border: 1px solid #ddd6fe !important;
    color: #6d28d9 !important;
    font-weight: 500 !important;
}
div[data-testid="stPopover"] button:hover {
    background: #ede9fe !important;
    border-color: #a78bfa !important;
    color: #4c1d95 !important;
}
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
# DATE HELPERS
# =============================================================================
# Elasticsearch returns dates in multiple formats depending on the index mapping:
#   • ISO 8601 with UTC offset   →  "2024-01-15T12:30:00.000Z"
#   • ISO 8601 without offset    →  "2024-01-15T12:30:00.000"  (treat as UTC)
#   • ISO 8601 with +00:00       →  "2024-01-15T12:30:00+00:00"
#   • Epoch milliseconds (int)   →  1705318200000
#   • Epoch milliseconds (str)   →  "1705318200000"
#   • Empty string / None        →  (treated as missing)
#
# Mixing any of the above with tz-aware ``now_utc`` when computing age deltas
# raises TypeError.  All callers go through ``parse_dt`` which always returns
# a tz-aware UTC Timestamp or None.

def parse_dt(value: Any) -> "pd.Timestamp | None":
    """Parse a date value from Elasticsearch into a tz-aware UTC Timestamp.

    Handles ISO strings (with or without timezone), epoch milliseconds as int
    or str, and any other value that pandas can interpret.  Always returns UTC.
    Returns ``None`` on any failure or if the input is empty/None.
    """
    if value is None:
        return None
    try:
        # Numeric epoch-ms (ES sometimes stores as int or numeric string)
        if isinstance(value, (int, float)):
            return pd.Timestamp(value, unit="ms", tz="UTC")
        s = str(value).strip()
        if not s:
            return None
        if s.isdigit() or (s.lstrip("-").isdigit()):
            return pd.Timestamp(int(s), unit="ms", tz="UTC")
        # pandas handles ISO8601, RFC822, and many other text formats
        ts = pd.to_datetime(s, utc=True)   # utc=True converts any offset to UTC
        if ts.tzinfo is None:
            # Fallback: pandas failed to attach tz — assume UTC
            ts = ts.tz_localize("UTC")
        return ts
    except Exception:
        return None


def fmt_dt(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Parse and format a date value; returns "" on failure."""
    ts = parse_dt(value)
    return ts.strftime(fmt) if ts is not None else ""


def age_hours(value: Any, reference: datetime | None = None) -> int | None:
    """Return elapsed hours between *value* and *reference* (defaults to now UTC)."""
    ts = parse_dt(value)
    if ts is None:
        return None
    _ref = reference or datetime.now(timezone.utc)
    ref = pd.Timestamp(_ref) if _ref.tzinfo is not None else pd.Timestamp(_ref, tz="UTC")
    try:
        return max(0, int((ref - ts).total_seconds() / 3600))
    except Exception:
        return None


def age_days(value: Any, reference: datetime | None = None) -> int | None:
    """Return elapsed days between *value* and *reference* (defaults to now UTC)."""
    h = age_hours(value, reference)
    return h // 24 if h is not None else None


# =============================================================================
# UI HELPERS
# =============================================================================

def inline_note(text: str, kind: str = "info", container: Any = None) -> None:
    """Render a themed inline note (immune to the user's custom theme).

    Replaces ``st.info`` / ``st.success`` / ``st.warning`` which some custom
    themes repaint with their own accent — we want the dashboard's reds and
    greens to render consistently regardless of ``config.toml``.
    """
    icons = {"info": "i", "success": "✓", "warning": "!", "danger": "✕"}
    kind = kind if kind in icons else "info"
    target = container if container is not None else st
    target.markdown(
        f'<div class="alert {kind}">'
        f'  <div class="icon">{icons[kind]}</div>'
        f'  <div><b>{text}</b></div>'
        f'</div>',
        unsafe_allow_html=True,
    )


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
# COMMAND BAR — title inline with controls, no separate hero block
# =============================================================================

# Populate dropdowns from the inventory — composite_terms already uses the
# cached ES layer, so this is a single cold hit at startup then free for 5 min.
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_inventory_choices() -> tuple[list[str], list[str]]:
    try:
        companies = sorted(
            composite_terms(IDX["inventory"], "company.keyword", {"match_all": {}}).keys()
        )
    except Exception:
        companies = []
    try:
        projects = sorted(
            composite_terms(IDX["inventory"], "project.keyword", {"match_all": {}}).keys()
        )
    except Exception:
        projects = []
    return companies, projects


_all_companies, _all_projects = _load_inventory_choices()
_ALL = "— All —"

# Title + controls in one row
cb = st.columns([2.2, 1.8, 1.8, 1.8, 0.9, 0.9])

with cb[0]:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;padding-top:6px;">'
        '<span style="width:8px;height:8px;border-radius:50%;background:#10b981;'
        'box-shadow:0 0 6px #10b981;display:inline-block;flex-shrink:0;"></span>'
        '<span style="font-size:1.1rem;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">'
        'CI/CD Command Center</span></div>',
        unsafe_allow_html=True,
    )

with cb[1]:
    preset = st.selectbox("Window", list(PRESETS.keys()), index=3, label_visibility="collapsed")

with cb[2]:
    company_pick = st.selectbox("Company", [_ALL] + _all_companies, index=0, label_visibility="collapsed")
    company_filter = "" if company_pick == _ALL else company_pick

with cb[3]:
    project_pick = st.selectbox("Project", [_ALL] + _all_projects, index=0, label_visibility="collapsed")
    project_filter = "" if project_pick == _ALL else project_pick

with cb[4]:
    auto_refresh = st.toggle("Auto", value=False, help="Auto-refresh every 60s")

with cb[5]:
    if st.button("↻", help="Clear cache & reload", use_container_width=True):
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
    f"{start_dt:%Y-%m-%d %H:%M} → {end_dt:%Y-%m-%d %H:%M} UTC  ·  bucket {interval}  ·  vs prior equal window  ·  {now_utc:%H:%M} UTC"
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
# KPIs  (2 rows × 4)
# =============================================================================

# Helper functions for WoW/MoM/YoY (used inside the trend popover below)
def _trend_count(
    index: str, date_field: str, cur_start: datetime, cur_end: datetime,
    prev_start: datetime, prev_end: datetime,
    extra: list[dict] | None = None,
) -> tuple[int, int]:
    cur  = count_with_range(index, date_field, cur_start, cur_end, extra=extra)
    prev = count_with_range(index, date_field, prev_start, prev_end, extra=extra)
    return cur, prev


def _cell(cur: int, prev: int) -> str:
    if prev == 0 and cur == 0:
        return '<span style="color:#94a3b8;">—</span>'
    if prev == 0:
        return f'<b style="color:#0f172a;">{cur:,}</b> <span style="color:#059669;">new</span>'
    diff = cur - prev
    pct  = diff / prev * 100
    direction = "#059669" if diff > 0 else ("#dc2626" if diff < 0 else "#94a3b8")
    arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "→")
    sign = "+" if diff >= 0 else ""
    return (
        f'<b style="color:#0f172a;">{cur:,}</b> '
        f'<span style="color:{direction};font-size:.80rem;"> {arrow} {sign}{pct:.1f}%</span>'
    )


_periods: list[tuple[str, timedelta]] = [
    ("WoW (7d)", timedelta(days=7)),
    ("MoM (30d)", timedelta(days=30)),
    ("YoY (365d)", timedelta(days=365)),
]


def _trend_windows(td: timedelta) -> tuple[datetime, datetime, datetime, datetime]:
    cur_end    = now_utc
    cur_start  = cur_end - td
    prev_end   = cur_start
    prev_start = prev_end - td
    return cur_start, cur_end, prev_start, prev_end


_trend_metrics = [
    ("Builds",            IDX["builds"],       "startdate",   None),
    ("Build failures",    IDX["builds"],       "startdate",   [{"terms": {"status": FAILED_STATUSES}}]),
    ("Deployments",       IDX["deployments"],  "startdate",   None),
    ("Prod deployments",  IDX["deployments"],  "startdate",   [{"term": {"environment": "prd"}}]),
    ("Prod failures",     IDX["deployments"],  "startdate",   [{"term": {"environment": "prd"}}, {"terms": {"status": FAILED_STATUSES}}]),
    ("Commits",           IDX["commits"],      "commitdate",  None),
    ("Releases",          IDX["releases"],     "releasedate", None),
    ("Requests",          IDX["requests"],     "RequestDate", None),
]

# Row 1 — DORA headline (4 cards)
r1 = st.columns(4)
d, dn = fmt_delta(prd_deploys, count_with_range(
    IDX["deployments"], "startdate", prior_start, prior_end,
    extra=[{"term": {"environment": "prd"}}],
))
kpi_block(r1[0], "Deploy freq", f"{deploy_freq_per_day:.1f}/day", d, dn, "Prod deploys / day")
kpi_block(r1[1], "Change fail rate", f"{cfr:.1f}%",
    f"{prd_fail} / {prd_deploys} prod" if prd_deploys else "no prod deploys",
    "dn" if cfr > 15 else ("up" if prd_deploys else "flat"), "DORA · failed prod / prod deploys")
kpi_block(r1[2], "Build success", f"{success_rate:.1f}%",
    f"{builds_fail:,} failed" if builds_fail else "all green",
    "dn" if builds_fail else "up", "(builds − failed) / builds")
kpi_block(r1[3], "Platform health",
    f"{active_projs}/{inv_count}" if inv_count else "—",
    f"{100 - dormant_pct:.0f}% active" if inv_count else "",
    "up" if dormant_pct < 30 else ("dn" if dormant_pct > 60 else "flat"), "active / inventory")

# Row 2 — volume (4 cards) + trend popover trigger at end
r2c = st.columns([1, 1, 1, 1, 1.6])
d, dn = fmt_delta(builds_now, builds_prev)
kpi_block(r2c[0], "Builds", f"{builds_now:,}", d, dn)
d, dn = fmt_delta(commits_now, commits_prev)
kpi_block(r2c[1], "Commits", f"{commits_now:,}", d, dn)
kpi_block(r2c[2], "Pending", f"{pending_now:,}",
    "needs action" if pending_now else "clear",
    "dn" if pending_now else "up", "Pending approvals (last 30d)")
kpi_block(r2c[3], "Open JIRA", f"{open_jira:,}", "all-time", "flat")

with r2c[4]:
    with st.popover("📈  WoW / MoM / YoY trends", use_container_width=True):
        st.markdown("**Rolling period comparisons** — independent of the window selector above")
        _trend_rows = []
        for _lbl, _idx, _df, _ex in _trend_metrics:
            _row: dict[str, Any] = {"Metric": _lbl}
            for _pl, _td in _periods:
                _cs, _ce, _ps, _pe = _trend_windows(_td)
                _cur, _prev = _trend_count(_idx, _df, _cs, _ce, _ps, _pe, extra=_ex)
                _row[_pl] = _cell(_cur, _prev)
            _trend_rows.append(_row)
        _hdrs = ["Metric"] + [p[0] for p in _periods]
        _html = [
            '<div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">',
            '<table style="width:100%;border-collapse:collapse;font-size:.88rem;">',
            '<thead><tr>',
        ]
        for _i, _h in enumerate(_hdrs):
            _align = "left" if _i == 0 else "right"
            _html.append(
                f'<th style="text-align:{_align};padding:10px 14px;color:#64748b;font-size:.68rem;'
                f'letter-spacing:.10em;text-transform:uppercase;font-weight:600;'
                f'border-bottom:1px solid #e2e8f0;background:#f8fafc;">{_h}</th>'
            )
        _html.append('</tr></thead><tbody>')
        for _row in _trend_rows:
            _html.append('<tr>')
            _html.append(f'<td style="padding:9px 14px;color:#1e293b;font-weight:500;border-bottom:1px solid #f1f5f9;">{_row["Metric"]}</td>')
            for _pl, _ in _periods:
                _html.append(f'<td style="text-align:right;padding:9px 14px;font-variant-numeric:tabular-nums;border-bottom:1px solid #f1f5f9;">{_row[_pl]}</td>')
            _html.append('</tr>')
        _html.append('</tbody></table></div>')
        st.markdown("".join(_html), unsafe_allow_html=True)


# =============================================================================
# ALERTS — compact chips, vivid colors
# =============================================================================

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

# ---- Alert drill-down popover ---------------------------------------------
_ap = st.columns([1.2, 4])
with _ap[0]:
    with st.popover("🔬  Drill into alerts", use_container_width=True):
        _which = st.selectbox(
            "Alert category",
            [
                "Stuck approvals (>24h)",
                "Failed prod deploys",
                "Aged open JIRA (>30d)",
                "Build failures in window",
            ],
            index=0,
            key="alert_drill",
        )
        if _which == "Stuck approvals (>24h)":
            _r = es_search(
                IDX["requests"],
                {**stuck_body, "sort": [{"RequestDate": "asc"}]},
                size=100,
            )
            _hits = _r.get("hits", {}).get("hits", [])
            if _hits:
                _rows = [
                    {
                        "#":         h["_source"].get("RequestNumber"),
                        "Type":      h["_source"].get("RequestType"),
                        "Requester": h["_source"].get("Requester"),
                        "Team":      h["_source"].get("RequesterTeam"),
                        "Age (h)":   age_hours(h["_source"].get("RequestDate"), now_utc),
                    }
                    for h in _hits
                ]
                st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=440)
            else:
                inline_note("No stuck approvals.", "success")

        elif _which == "Failed prod deploys":
            _r = es_search(
                IDX["deployments"],
                {"query": {"bool": {"filter": [
                    range_filter("startdate", start_dt, end_dt),
                    {"term": {"environment": "prd"}},
                    {"terms": {"status": FAILED_STATUSES}},
                ] + scope_filters()}},
                 "sort": [{"startdate": "desc"}]},
                size=100,
            )
            _hits = _r.get("hits", {}).get("hits", [])
            if _hits:
                _rows = [
                    {
                        "When":    fmt_dt(h["_source"].get("startdate"), "%Y-%m-%d %H:%M"),
                        "Project": h["_source"].get("project"),
                        "Version": h["_source"].get("codeversion"),
                        "Status":  h["_source"].get("status"),
                    }
                    for h in _hits
                ]
                st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=440)
            else:
                inline_note("No failed prod deploys.", "success")

        elif _which == "Aged open JIRA (>30d)":
            _r = es_search(
                IDX["jira"],
                {"query": {"bool": {
                    "filter": [{"range": {"updated": {"lte": (now_utc - timedelta(days=30)).isoformat()}}}] + scope_filters(),
                    "must_not": [{"terms": {"status": CLOSED_JIRA}}],
                }}, "sort": [{"updated": "asc"}]},
                size=100,
            )
            _hits = _r.get("hits", {}).get("hits", [])
            if _hits:
                _rows = [
                    {
                        "Key":      h["_source"].get("issuekey"),
                        "Priority": h["_source"].get("priority"),
                        "Status":   h["_source"].get("status"),
                        "Assignee": h["_source"].get("assignee"),
                        "Updated":  fmt_dt(h["_source"].get("updated"), "%Y-%m-%d"),
                    }
                    for h in _hits
                ]
                st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=440)
            else:
                inline_note("No aged JIRA.", "success")

        else:  # Build failures in window
            _r = es_search(
                IDX["builds"],
                {"query": {"bool": {"filter": [
                    range_filter("startdate", start_dt, end_dt),
                    {"terms": {"status": FAILED_STATUSES}},
                ] + scope_filters()}},
                 "sort": [{"startdate": "desc"}]},
                size=100,
            )
            _hits = _r.get("hits", {}).get("hits", [])
            if _hits:
                _rows = [
                    {
                        "When":    fmt_dt(h["_source"].get("startdate"), "%Y-%m-%d %H:%M"),
                        "Project": h["_source"].get("project"),
                        "Branch":  h["_source"].get("branch"),
                        "Version": h["_source"].get("codeversion"),
                        "Tech":    h["_source"].get("technology"),
                    }
                    for h in _hits
                ]
                st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=440)
            else:
                inline_note("No build failures.", "success")


# =============================================================================
# SECTION 3 — CROSS-INDEX INSIGHTS
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Project landscape</h2><span class="badge">Inventory × Activity</span></div>'
    '<span class="hint">active · at-risk · archival candidates — joined across all indices</span>'
    '</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Project deep-dive popover — pick a project and see every cross-index signal
# ---------------------------------------------------------------------------
pop_cols = st.columns([1, 1, 4])
with pop_cols[0]:
    with st.popover("🔎  Project deep dive", use_container_width=True):
        st.markdown("**Inspect a single project across every index**")
        _dd_proj = st.selectbox(
            "Project",
            _all_projects if _all_projects else ["(no projects found)"],
            key="dd_project",
        )
        _dd_window = st.selectbox(
            "Window",
            ["Last 7 days", "Last 30 days", "Last 90 days", "Last 365 days"],
            index=1,
            key="dd_window",
        )
        _dd_delta = {
            "Last 7 days":   timedelta(days=7),
            "Last 30 days":  timedelta(days=30),
            "Last 90 days":  timedelta(days=90),
            "Last 365 days": timedelta(days=365),
        }[_dd_window]
        _dd_end   = now_utc
        _dd_start = _dd_end - _dd_delta

        if _dd_proj and _dd_proj != "(no projects found)":
            _pf = [{"term": {"project": _dd_proj}}]
            _pf_inv = [{"term": {"project.keyword": _dd_proj}}]

            # Aggregate per-project stats across all indices
            _b_all   = es_count(IDX["builds"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf}}})
            _b_fail  = es_count(IDX["builds"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end), {"terms": {"status": FAILED_STATUSES}}] + _pf}}})
            _d_all   = es_count(IDX["deployments"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf}}})
            _d_prd   = es_count(IDX["deployments"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end), {"term": {"environment": "prd"}}] + _pf}}})
            _d_fail  = es_count(IDX["deployments"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end), {"term": {"environment": "prd"}}, {"terms": {"status": FAILED_STATUSES}}] + _pf}}})
            _c_all   = es_count(IDX["commits"], {"query": {"bool": {"filter": [range_filter("commitdate", _dd_start, _dd_end)] + _pf}}})
            _r_pend  = es_count(IDX["requests"], {"query": {"bool": {"filter": [range_filter("RequestDate", pending_window_start, now_utc), {"terms": {"Status": PENDING_STATUSES}}] + _pf}}})
            _j_open  = es_count(IDX["jira"], {"query": {"bool": {"filter": _pf, "must_not": [{"terms": {"status": CLOSED_JIRA}}]}}})
            _succ    = ((_b_all - _b_fail) / _b_all * 100) if _b_all else 0.0
            _cfr     = (_d_fail / _d_prd * 100) if _d_prd else 0.0

            # Pills summary
            st.markdown(
                f"""
                <div style="margin:6px 0 10px 0;">
                    <span class="pill blue">{_b_all:,} builds</span>
                    <span class="pill {'green' if _succ >= 80 else 'red'}">{_succ:.0f}% success</span>
                    <span class="pill blue">{_d_all:,} deploys</span>
                    <span class="pill {'red' if _cfr > 15 else 'green'}">{_cfr:.0f}% CFR</span>
                    <span class="pill blue">{_c_all:,} commits</span>
                    <span class="pill {'amber' if _r_pend else 'green'}">{_r_pend} pending req</span>
                    <span class="pill {'amber' if _j_open else 'green'}">{_j_open} open JIRA</span>
                </div>
                """,
                unsafe_allow_html=True,
            )

            dd_tabs = st.tabs(["Recent builds", "Recent deploys", "Open JIRA", "Recent commits", "Inventory"])

            with dd_tabs[0]:
                _r = es_search(
                    IDX["builds"],
                    {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf}},
                     "sort": [{"startdate": "desc"}]},
                    size=50,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    _rows = [
                        {
                            "When":    fmt_dt(_s.get("startdate"), "%Y-%m-%d %H:%M"),
                            "Branch":  _s.get("branch"),
                            "Version": _s.get("codeversion"),
                            "Status":  _s.get("status"),
                            "Tech":    _s.get("technology"),
                        }
                        for _h in _hits for _s in [_h.get("_source", {})]
                    ]
                    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=360)
                else:
                    inline_note("No builds in window.", "info")

            with dd_tabs[1]:
                _r = es_search(
                    IDX["deployments"],
                    {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf}},
                     "sort": [{"startdate": "desc"}]},
                    size=50,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    _rows = [
                        {
                            "When":    fmt_dt(_s.get("startdate"), "%Y-%m-%d %H:%M"),
                            "Env":     _s.get("environment"),
                            "Version": _s.get("codeversion"),
                            "Status":  _s.get("status"),
                        }
                        for _h in _hits for _s in [_h.get("_source", {})]
                    ]
                    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=360)
                else:
                    inline_note("No deployments in window.", "info")

            with dd_tabs[2]:
                _r = es_search(
                    IDX["jira"],
                    {"query": {"bool": {"filter": _pf, "must_not": [{"terms": {"status": CLOSED_JIRA}}]}},
                     "sort": [{"priority": "asc"}]},
                    size=50,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    _rows = [
                        {
                            "Key":      _s.get("issuekey"),
                            "Priority": _s.get("priority"),
                            "Status":   _s.get("status"),
                            "Assignee": _s.get("assignee"),
                            "Summary":  (_s.get("summary") or "")[:80],
                        }
                        for _h in _hits for _s in [_h.get("_source", {})]
                    ]
                    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=360)
                else:
                    inline_note("No open JIRA for this project.", "success")

            with dd_tabs[3]:
                _r = es_search(
                    IDX["commits"],
                    {"query": {"bool": {"filter": [range_filter("commitdate", _dd_start, _dd_end)] + _pf}},
                     "sort": [{"commitdate": "desc"}]},
                    size=50,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    _rows = [
                        {
                            "When":   fmt_dt(_s.get("commitdate"), "%Y-%m-%d %H:%M"),
                            "Author": _s.get("authorname"),
                            "Branch": _s.get("branch"),
                            "Repo":   _s.get("repository"),
                        }
                        for _h in _hits for _s in [_h.get("_source", {})]
                    ]
                    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=360)
                else:
                    inline_note("No commits in window.", "info")

            with dd_tabs[4]:
                _r = es_search(
                    IDX["inventory"],
                    {"query": {"bool": {"filter": _pf_inv}}},
                    size=5,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    st.json(_hits[0].get("_source", {}), expanded=False)
                else:
                    inline_note("Project not found in inventory.", "info")

with pop_cols[1]:
    with st.popover("👤  Committer deep dive", use_container_width=True):
        st.markdown("**Committer activity across the window**")
        _cw = st.selectbox("Window", ["Last 7 days", "Last 30 days", "Last 90 days"], index=1, key="dd_cw")
        _cd = {"Last 7 days": timedelta(days=7), "Last 30 days": timedelta(days=30), "Last 90 days": timedelta(days=90)}[_cw]
        _cs, _ce = now_utc - _cd, now_utc
        _cr = es_search(
            IDX["commits"],
            {
                "query": {"bool": {"filter": [range_filter("commitdate", _cs, _ce)]}},
                "aggs": {
                    "top": {
                        "terms": {"field": "authorname", "size": 30},
                        "aggs": {
                            "ins": {"sum": {"field": "insertedlines"}},
                            "dlt": {"sum": {"field": "deletedlines"}},
                            "projs": {"cardinality": {"field": "project"}},
                        },
                    }
                },
            },
        )
        _buckets = bucket_rows(_cr, "top")
        if _buckets:
            _df = pd.DataFrame([
                {
                    "Author":   b["key"],
                    "Commits":  b["doc_count"],
                    "Lines +":  int(b.get("ins", {}).get("value", 0) or 0),
                    "Lines −":  int(b.get("dlt", {}).get("value", 0) or 0),
                    "Projects": int(b.get("projs", {}).get("value", 0) or 0),
                }
                for b in _buckets
            ])
            st.dataframe(_df, use_container_width=True, hide_index=True, height=440)
        else:
            inline_note("No commits in the selected window.", "info")

# ---------------------------------------------------------------------------
# Project landscape treemap — active vs dormant vs archival, health-colored
# ---------------------------------------------------------------------------
_tm_ninety = now_utc - timedelta(days=90)
_tm_act_query = {"bool": {"filter": [range_filter("startdate", _tm_ninety, now_utc)] + scope_filters()}}
_tm_active_map = composite_terms(IDX["builds"], "project", _tm_act_query)

_tm_inv_query = {"bool": {"filter": scope_filters_inv()}} if scope_filters_inv() else {"match_all": {}}
_tm_inv_map   = composite_terms(IDX["inventory"], "project.keyword", _tm_inv_query)

# For top active projects pull fail counts too
_tm_body = {
    "query": {"bool": {"filter": [range_filter("startdate", start_dt, end_dt)] + scope_filters()}},
    "aggs": {
        "projs": {
            "terms": {"field": "project", "size": 200},
            "aggs": {"fails": {"filter": {"terms": {"status": FAILED_STATUSES}}}},
        }
    },
}
_tm_res  = es_search(IDX["builds"], _tm_body)
_tm_fail_map = {
    b["key"]: b.get("fails", {}).get("doc_count", 0)
    for b in bucket_rows(_tm_res, "projs")
}
_jira_map_tm = {b["key"]: b["doc_count"] for b in bucket_rows(
    es_search(IDX["jira"], {
        "query": {"bool": {"filter": scope_filters(), "must_not": [{"terms": {"status": CLOSED_JIRA}}]}},
        "aggs": {"projs": {"terms": {"field": "project", "size": 500}}},
    }),
    "projs",
)}

_tm_rows = []
_all_proj_names = set(_tm_inv_map.keys()) | set(_tm_active_map.keys())
for _p in _all_proj_names:
    _builds_90 = _tm_active_map.get(_p, 0)
    _in_inv    = _p in _tm_inv_map
    _fails     = _tm_fail_map.get(_p, 0)
    _builds_w  = sum(1 for b in bucket_rows(_tm_res, "projs") if b["key"] == _p and b["doc_count"] > 0) or _builds_90
    _jira_open = _jira_map_tm.get(_p, 0)

    if _builds_90 == 0 and _in_inv:
        _status = "Archival candidate"
    elif _builds_90 > 0:
        _succ_pct = (_builds_90 - _fails) / _builds_90 * 100 if _builds_90 else 100
        _status = "Active · healthy" if _succ_pct >= 80 else "Active · at-risk"
    else:
        _status = "Unknown"

    # Health score 0-100 for color scale
    _score = min(100, max(0, int(
        ((_builds_90 - _fails) / _builds_90 * 100 if _builds_90 else 50)
        - _jira_open * 1.5
    )))
    _tm_rows.append({
        "project": _p,
        "builds_90d": max(_builds_90, 1),
        "status": _status,
        "score": _score,
        "fails": _fails,
        "open_jira": _jira_open,
    })

if _tm_rows:
    _df_tm = pd.DataFrame(_tm_rows)
    _color_map = {
        "Active · healthy":   "#16a34a",
        "Active · at-risk":   "#d97706",
        "Archival candidate": "#94a3b8",
        "Unknown":            "#cbd5e1",
    }
    _tm_fig = px.treemap(
        _df_tm,
        path=["status", "project"],
        values="builds_90d",
        color="status",
        color_discrete_map=_color_map,
        custom_data=["fails", "open_jira", "score"],
        title="Project landscape · last 90 days  (size = build count, color = health status)",
    )
    _tm_fig.update_traces(
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Builds (90d): %{value}<br>"
            "Failures: %{customdata[0]}<br>"
            "Open JIRA: %{customdata[1]}<br>"
            "Health score: %{customdata[2]}/100"
            "<extra></extra>"
        ),
        textinfo="label+value",
        insidetextfont=dict(size=11, color="white"),
    )
    _tm_fig.update_layout(
        height=340,
        margin=dict(l=0, r=0, t=36, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#374151", family="Inter, sans-serif"),
    )
    st.plotly_chart(_tm_fig, use_container_width=True)

    # Archival candidates inline (compact)
    _archival = _df_tm[_df_tm["status"] == "Archival candidate"].sort_values("project")
    if not _archival.empty:
        _arc_cols = st.columns([3, 1])
        with _arc_cols[0]:
            st.markdown(
                f'<div class="alert warning" style="margin-bottom:4px;">'
                f'<div class="icon">⚠</div>'
                f'<div><b>{len(_archival)} archival candidates</b>'
                f'<span class="sub">— no builds in 90 days, still in inventory</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _arc_cols[1]:
            with st.popover("View list", use_container_width=True):
                st.markdown("**Projects with no builds in 90 days**")
                _arc_display = _archival[["project", "open_jira"]].copy()
                _arc_display.columns = ["Project", "Open JIRA"]
                st.dataframe(_arc_display, use_container_width=True, hide_index=True, height=400)
                st.caption(f"{len(_archival)} candidates — review for archival or decommission.")
else:
    inline_note("No project data available.", "info")

# =============================================================================
# APP LIFECYCLE — pipeline stage funnel per project + bottleneck finder
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap">'
    '  <h2>App lifecycle &amp; bottlenecks</h2>'
    '  <span class="badge">per-project pipeline</span>'
    '</div>'
    '<span class="hint">where does each project stall between commit → build → deploy → release?</span>'
    '</div>',
    unsafe_allow_html=True,
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _lifecycle_data(
    s: str, e: str, company: str, project: str
) -> dict:
    """
    Returns a dict with keys:
      projects  — list[str]  all projects seen across any stage
      stages    — dict[stage_name -> dict[project -> count]]
      sankey    — dict with lists: labels, source, target, value, color
    """
    _s = datetime.fromisoformat(s)
    _e = datetime.fromisoformat(e)

    _scope: list[dict] = []
    if company:
        _scope.append({"term": {"company.keyword": company}})
    if project:
        _scope.append({"term": {"project": project}})

    def _agg_by_project(index: str, date_field: str,
                        extra_filters: list[dict] | None = None,
                        page_size: int = 500) -> dict[str, int]:
        _f = [range_filter(date_field, _s, _e)] + _scope + (extra_filters or [])
        return composite_terms(index, "project.keyword", {"bool": {"filter": _f}}, page_size)

    def _agg_by_app(index: str, date_field: str,
                    extra_filters: list[dict] | None = None,
                    page_size: int = 500) -> dict[str, int]:
        """Releases index uses `application` not `project`."""
        _f = [range_filter(date_field, _s, _e)] + _scope + (extra_filters or [])
        return composite_terms(index, "application.keyword", {"bool": {"filter": _f}}, page_size)

    commits_by_proj    = _agg_by_project(IDX["commits"],     "commitdate")
    builds_by_proj     = _agg_by_project(IDX["builds"],      "startdate")
    build_ok_by_proj   = _agg_by_project(IDX["builds"],      "startdate",
                             [{"terms": {"status": ["SUCCESS", "SUCCEEDED", "success"]}}])
    dep_dev_by_proj    = _agg_by_project(IDX["deployments"], "startdate", [{"term": {"environment": "dev"}}])
    dep_qc_by_proj     = _agg_by_project(IDX["deployments"], "startdate", [{"term": {"environment": "qc"}}])
    dep_uat_by_proj    = _agg_by_project(IDX["deployments"], "startdate", [{"term": {"environment": "uat"}}])
    dep_prd_by_proj    = _agg_by_project(IDX["deployments"], "startdate", [{"term": {"environment": "prd"}}])
    releases_by_proj   = _agg_by_app(IDX["releases"],        "releasedate")

    # Merge all project keys
    all_projects = sorted(
        set(commits_by_proj) | set(builds_by_proj) | set(dep_dev_by_proj)
        | set(dep_qc_by_proj) | set(dep_uat_by_proj) | set(dep_prd_by_proj)
        | set(releases_by_proj)
    )

    stage_maps = {
        "Commits":        commits_by_proj,
        "Builds":         builds_by_proj,
        "Build OK":       build_ok_by_proj,
        "Deploy Dev":     dep_dev_by_proj,
        "Deploy QC":      dep_qc_by_proj,
        "Deploy UAT":     dep_uat_by_proj,
        "Deploy PRD":     dep_prd_by_proj,
        "Releases":       releases_by_proj,
    }

    # ── Sankey aggregate ──────────────────────────────────────────────────
    STAGE_ORDER = ["Commits", "Builds", "Build OK", "Deploy Dev",
                   "Deploy QC", "Deploy UAT", "Deploy PRD", "Releases"]
    stage_totals = {s: sum(stage_maps[s].values()) for s in STAGE_ORDER}

    # Build Sankey edges: consecutive pairs with non-zero flow
    sk_labels = list(STAGE_ORDER)
    sk_source, sk_target, sk_value, sk_colors = [], [], [], []
    STAGE_COLORS = ["#6366f1","#3b82f6","#10b981","#0ea5e9",
                    "#8b5cf6","#f59e0b","#ef4444","#ec4899"]
    for i in range(len(STAGE_ORDER) - 1):
        a, b = STAGE_ORDER[i], STAGE_ORDER[i + 1]
        flow = min(stage_totals[a], stage_totals[b])
        if flow > 0:
            sk_source.append(i)
            sk_target.append(i + 1)
            sk_value.append(flow)
            sk_colors.append(STAGE_COLORS[i] + "88")  # semi-transparent

    return {
        "projects":   all_projects,
        "stage_maps": stage_maps,
        "stage_order": STAGE_ORDER,
        "sankey": {
            "labels":  sk_labels,
            "source":  sk_source,
            "target":  sk_target,
            "value":   sk_value,
            "colors":  sk_colors,
            "node_colors": STAGE_COLORS,
            "totals":  stage_totals,
        },
    }


_lc = _lifecycle_data(
    start_dt.isoformat(), end_dt.isoformat(),
    company_filter or "", project_filter or "",
)
_stage_maps  = _lc["stage_maps"]
_stage_order = _lc["stage_order"]
_lc_projects = _lc["projects"]
_sk          = _lc["sankey"]

# ── Row 1: Sankey + bottleneck table ────────────────────────────────────────
_lc_col1, _lc_col2 = st.columns([1.6, 1])

with _lc_col1:
    if _sk["source"]:
        _sk_fig = go.Figure(go.Sankey(
            arrangement="snap",
            node=dict(
                pad=18,
                thickness=22,
                line=dict(color="#e2e8f0", width=0.5),
                label=_sk["labels"],
                color=_sk["node_colors"],
                hovertemplate="<b>%{label}</b><br>Volume: %{value:,}<extra></extra>",
            ),
            link=dict(
                source=_sk["source"],
                target=_sk["target"],
                value=_sk["value"],
                color=_sk["colors"],
                hovertemplate=(
                    "%{source.label} → %{target.label}<br>"
                    "Flow: %{value:,}<extra></extra>"
                ),
            ),
        ))
        _sk_fig.update_layout(
            title=dict(
                text="Pipeline flow · aggregate volume through each stage",
                font=dict(size=13, color="#1e293b"),
                x=0,
            ),
            font=dict(size=11, color="#334155", family="inherit"),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=36, b=0),
            height=300,
        )
        st.plotly_chart(_sk_fig, use_container_width=True)
    else:
        inline_note("Not enough cross-stage data to build the flow diagram.", "info")

with _lc_col2:
    # ── Bottleneck finder: largest relative drop between consecutive stages
    _bn_rows = []
    _totals = _sk["totals"]
    for _i in range(len(_stage_order) - 1):
        _a, _b = _stage_order[_i], _stage_order[_i + 1]
        _va, _vb = _totals[_a], _totals[_b]
        if _va > 0:
            _drop = (_va - _vb) / _va * 100
            _bn_rows.append({"Transition": f"{_a} → {_b}",
                             "In": _va, "Out": _vb, "Drop %": round(_drop, 1)})
    if _bn_rows:
        _bn_df = pd.DataFrame(_bn_rows).sort_values("Drop %", ascending=False)

        def _color_drop(val: float) -> str:
            if val >= 70: return "background:#fff1f2;color:#991b1b;font-weight:700"
            if val >= 40: return "background:#fffbeb;color:#92400e;font-weight:600"
            return "color:#166534"

        st.markdown(
            '<p style="font-size:0.82rem;font-weight:700;color:#1e293b;margin:0 0 6px">'
            'Stage drop-off · biggest leaks first</p>',
            unsafe_allow_html=True,
        )
        _bn_html_rows = []
        for _, _r in _bn_df.iterrows():
            _style = _color_drop(_r["Drop %"])
            _bar_w = max(4, int(_r["Drop %"]))
            _bn_html_rows.append(
                f'<tr>'
                f'<td style="font-size:0.78rem;color:#334155;padding:5px 4px">{_r["Transition"]}</td>'
                f'<td style="font-size:0.78rem;color:#475569;text-align:right;padding:5px 4px">'
                f'{int(_r["In"]):,} → {int(_r["Out"]):,}</td>'
                f'<td style="padding:5px 4px;min-width:120px">'
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<div style="width:{_bar_w}px;height:8px;border-radius:4px;'
                f'background:{"#dc2626" if _r["Drop %"]>=70 else "#d97706" if _r["Drop %"]>=40 else "#16a34a"}'
                f'"></div>'
                f'<span style="{_style};font-size:0.78rem">{_r["Drop %"]:.1f}%</span>'
                f'</div></td>'
                f'</tr>'
            )
        st.markdown(
            '<table style="width:100%;border-collapse:collapse;font-family:inherit">'
            '<thead><tr style="border-bottom:2px solid #e2e8f0">'
            '<th style="font-size:0.72rem;color:#64748b;padding:4px;text-align:left">STAGE</th>'
            '<th style="font-size:0.72rem;color:#64748b;padding:4px;text-align:right">VOLUME</th>'
            '<th style="font-size:0.72rem;color:#64748b;padding:4px">DROP</th>'
            '</tr></thead><tbody>' + "".join(_bn_html_rows) + "</tbody></table>",
            unsafe_allow_html=True,
        )
    else:
        inline_note("No stage data to compare.", "info")

# ── Row 2: per-project heatmap ──────────────────────────────────────────────
if _lc_projects:
    # Limit to top-N projects by total activity to keep heatmap readable
    _proj_activity = {
        p: sum(_stage_maps[s].get(p, 0) for s in _stage_order)
        for p in _lc_projects
    }
    _top_projects = sorted(_proj_activity, key=_proj_activity.get, reverse=True)[:30]  # type: ignore[arg-type]

    # Build matrix: rows=projects, cols=stages, cell=conversion rate vs commits (or raw count)
    _hm_z, _hm_text, _hm_customdata = [], [], []
    for _p in _top_projects:
        _row_z, _row_text, _row_cd = [], [], []
        _commits = _stage_maps["Commits"].get(_p, 0)
        _ref = _commits if _commits else None  # conversion base
        for _s in _stage_order:
            _v = _stage_maps[_s].get(_p, 0)
            if _ref and _s != "Commits":
                _rate = min(100, _v / _ref * 100)
            elif _s == "Commits":
                _rate = 100.0 if _v else 0.0
            else:
                _rate = 0.0 if _v == 0 else 50.0  # some activity but no commit baseline
            _row_z.append(_rate)
            _row_text.append(f"{_v:,}")
            _row_cd.append(_v)
        _hm_z.append(_row_z)
        _hm_text.append(_row_text)
        _hm_customdata.append(_row_cd)

    _hm_fig = go.Figure(go.Heatmap(
        z=_hm_z,
        x=_stage_order,
        y=_top_projects,
        text=_hm_text,
        texttemplate="%{text}",
        textfont=dict(size=10),
        colorscale=[
            [0.0,  "#fef2f2"],
            [0.15, "#fca5a5"],
            [0.35, "#fb923c"],
            [0.6,  "#facc15"],
            [0.8,  "#86efac"],
            [1.0,  "#16a34a"],
        ],
        zmin=0, zmax=100,
        colorbar=dict(
            title=dict(text="Conv. %", side="right", font=dict(size=11)),
            thickness=12,
            len=0.9,
            tickfont=dict(size=10),
        ),
        hovertemplate=(
            "<b>%{y}</b> · %{x}<br>"
            "Count: %{text}<br>"
            "Conv. rate: %{z:.1f}%<extra></extra>"
        ),
    ))
    _hm_fig.update_layout(
        title=dict(
            text="Per-project pipeline conversion (% of commits that reached each stage)",
            font=dict(size=13, color="#1e293b"),
            x=0,
        ),
        xaxis=dict(side="top", tickfont=dict(size=11, color="#334155"),
                   showgrid=False, zeroline=False),
        yaxis=dict(tickfont=dict(size=10, color="#334155"), autorange="reversed",
                   showgrid=False, zeroline=False),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=60, t=60, b=0),
        height=max(280, len(_top_projects) * 28),
        font=dict(family="inherit"),
    )
    st.plotly_chart(_hm_fig, use_container_width=True)
    st.caption(
        "Green = healthy conversion · Red = stage not reached. "
        "A column that's all-red is the pipeline bottleneck for those projects. "
        f"Showing top {len(_top_projects)} projects by total activity."
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
                last = fmt_dt(last, "%m-%d %H:%M")
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
        inline_note("No build activity in window.", "info")

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
    inline_note(f"Risk spotlight unavailable: {exc}", "info")


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

# --- Raw-data popover: filtered list of last N pipeline executions ---------
_pa_pop = st.columns([1.2, 1.2, 3])
with _pa_pop[0]:
    with st.popover("📄  Raw builds (last 200)", use_container_width=True):
        _st = st.selectbox(
            "Status filter", ["Any", "SUCCESS", "FAILED", "ABORTED", "RUNNING"],
            index=0, key="raw_builds_status",
        )
        _filter: list[dict] = [range_filter("startdate", start_dt, end_dt)] + scope_filters()
        if _st == "FAILED":
            _filter.append({"terms": {"status": FAILED_STATUSES}})
        elif _st != "Any":
            _filter.append({"term": {"status": _st}})
        _r = es_search(
            IDX["builds"],
            {"query": {"bool": {"filter": _filter}}, "sort": [{"startdate": "desc"}]},
            size=200,
        )
        _hits = _r.get("hits", {}).get("hits", [])
        if _hits:
            _rows = [
                {
                    "When":    fmt_dt(_s.get("startdate"), "%m-%d %H:%M"),
                    "Project": _s.get("project"),
                    "Branch":  _s.get("branch"),
                    "Status":  _s.get("status"),
                    "Version": _s.get("codeversion"),
                    "Tech":    _s.get("technology"),
                }
                for _h in _hits for _s in [_h.get("_source", {})]
            ]
            st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=500)
            st.caption(f"Showing {len(_rows)} of up to 200 most recent builds.")
        else:
            inline_note("No builds match the filter.", "info")

with _pa_pop[1]:
    with st.popover("📄  Raw deployments (last 200)", use_container_width=True):
        _env = st.selectbox(
            "Environment filter", ["Any", "prd", "uat", "qc", "dev"],
            index=0, key="raw_dep_env",
        )
        _dst = st.selectbox(
            "Status filter", ["Any", "SUCCESS", "FAILED"],
            index=0, key="raw_dep_status",
        )
        _filter = [range_filter("startdate", start_dt, end_dt)] + scope_filters()
        if _env != "Any":
            _filter.append({"term": {"environment": _env}})
        if _dst == "FAILED":
            _filter.append({"terms": {"status": FAILED_STATUSES}})
        elif _dst != "Any":
            _filter.append({"term": {"status": _dst}})
        _r = es_search(
            IDX["deployments"],
            {"query": {"bool": {"filter": _filter}}, "sort": [{"startdate": "desc"}]},
            size=200,
        )
        _hits = _r.get("hits", {}).get("hits", [])
        if _hits:
            _rows = [
                {
                    "When":    fmt_dt(_s.get("startdate"), "%m-%d %H:%M"),
                    "Project": _s.get("project"),
                    "Env":     _s.get("environment"),
                    "Status":  _s.get("status"),
                    "Version": _s.get("codeversion"),
                }
                for _h in _hits for _s in [_h.get("_source", {})]
            ]
            st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=500)
            st.caption(f"Showing {len(_rows)} of up to 200 most recent deployments.")
        else:
            inline_note("No deployments match the filter.", "info")

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
        df_tl["time"] = pd.to_datetime(df_tl["time"], utc=True)
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
            font=dict(color="#374151", family="Inter, sans-serif"),
            xaxis=dict(gridcolor="#e5e7eb"),
            yaxis=dict(gridcolor="#e5e7eb"),
        )
        c1.plotly_chart(fig, use_container_width=True)
    else:
        inline_note("No builds in this window.", "info", c1)

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
            font=dict(color="#374151", family="Inter, sans-serif"),
            xaxis=dict(gridcolor="#e5e7eb"),
            yaxis=dict(gridcolor="#e5e7eb"),
        )
        c2.plotly_chart(fig2, use_container_width=True)
    else:
        inline_note("No project data.", "info", c2)

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
        df_tl["time"] = pd.to_datetime(df_tl["time"], utc=True)
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
            font=dict(color="#374151", family="Inter, sans-serif"),
            xaxis=dict(gridcolor="#e5e7eb"),
            yaxis=dict(gridcolor="#e5e7eb"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        inline_note("No deployments in this window.", "info")

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
            recs.append({
                "#":         s.get("RequestNumber"),
                "Type":      s.get("RequestType"),
                "Requester": s.get("Requester"),
                "Age (h)":   age_hours(s.get("RequestDate"), now_utc),
            })
        st.dataframe(
            pd.DataFrame(recs), use_container_width=True, hide_index=True, height=320
        )
    else:
        inline_note("No pending requests.", "success")

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
        inline_note("No commits in window.", "info")

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
        inline_note("No open JIRA issues.", "success")

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
        inline_note("No dormant projects detected.", "success")

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
            rows.append({
                "#":       s.get("RequestNumber"),
                "Type":    s.get("RequestType"),
                "Age (d)": age_days(s.get("RequestDate"), now_utc),
            })
        st.dataframe(
            pd.DataFrame(rows), use_container_width=True, hide_index=True, height=260
        )
    else:
        inline_note("No long-running requests.", "success")

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
        inline_note("No aged tickets.", "success")


# =============================================================================
# SECTION 6 — EVENT LOG (on-demand)
# =============================================================================

_el_c1, _el_c2, _el_c3, _el_c4 = st.columns([3, 1.2, 1.2, 1.2])
with _el_c1:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:10px;padding:6px 0">'
        '<span style="font-size:1.05rem;font-weight:700;color:#1e293b">Event log</span>'
        '<span style="font-size:0.72rem;background:#e2e8f0;color:#475569;'
        'border-radius:12px;padding:2px 10px;font-weight:600">on demand</span>'
        '<span style="font-size:0.8rem;color:#94a3b8">deployments · releases · commits — newest first</span>'
        '</div>',
        unsafe_allow_html=True,
    )

# --- filter controls always visible so the log opens pre-filtered ----------
with _el_c2:
    _el_type = st.selectbox(
        "Type", ["All", "Deployments", "Releases", "Commits"], key="el_type"
    )
with _el_c3:
    _el_env = st.selectbox(
        "Env", ["(all)", "prd", "uat", "qc", "dev"], key="el_env"
    )
with _el_c4:
    _el_limit = st.selectbox(
        "Show", [50, 100, 250], key="el_limit"
    )

with st.popover("Open event log", use_container_width=True):
    # ── helpers ──────────────────────────────────────────────────────────────
    _TYPE_BADGE = {
        "deploy":  ('<span style="background:#dbeafe;color:#1d4ed8;border-radius:4px;'
                    'padding:1px 7px;font-size:0.72rem;font-weight:700">DEPLOY</span>'),
        "release": ('<span style="background:#fce7f3;color:#9d174d;border-radius:4px;'
                    'padding:1px 7px;font-size:0.72rem;font-weight:700">RELEASE</span>'),
        "commit":  ('<span style="background:#dcfce7;color:#166534;border-radius:4px;'
                    'padding:1px 7px;font-size:0.72rem;font-weight:700">COMMIT</span>'),
    }
    _STATUS_CHIP = {
        "SUCCESS": ('<span style="background:#16a34a;color:#fff;border-radius:4px;'
                    'padding:1px 7px;font-size:0.72rem;font-weight:700">OK</span>'),
        "FAILED":  ('<span style="background:#dc2626;color:#fff;border-radius:4px;'
                    'padding:1px 7px;font-size:0.72rem;font-weight:700">FAIL</span>'),
        "RUNNING": ('<span style="background:#d97706;color:#fff;border-radius:4px;'
                    'padding:1px 7px;font-size:0.72rem;font-weight:700">RUN</span>'),
    }

    def _status_chip(raw: str | None) -> str:
        if raw is None:
            return ""
        up = (raw or "").upper()
        if up in _STATUS_CHIP:
            return _STATUS_CHIP[up]
        if any(f in up for f in ("FAIL", "ERROR", "ABORT")):
            return _STATUS_CHIP["FAILED"]
        if up in ("SUCCESS", "PASSED", "OK"):
            return _STATUS_CHIP["SUCCESS"]
        return (f'<span style="background:#e2e8f0;color:#334155;border-radius:4px;'
                f'padding:1px 7px;font-size:0.72rem;font-weight:600">{raw}</span>')

    events: list[dict] = []

    # ── deployments ──────────────────────────────────────────────────────────
    if _el_type in ("All", "Deployments"):
        _dep_f = list(scope_filters())
        if _el_env != "(all)":
            _dep_f.append({"term": {"environment": _el_env}})
        _dep_r = es_search(
            IDX["deployments"],
            {"query": {"bool": {"filter": _dep_f}} if _dep_f else {"match_all": {}},
             "sort": [{"startdate": "desc"}]},
            size=int(_el_limit),
        )
        for _h in _dep_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _ts = parse_dt(_s.get("startdate"))
            events.append({
                "_ts":    _ts,
                "type":   "deploy",
                "When":   fmt_dt(_s.get("startdate"), "%Y-%m-%d %H:%M"),
                "Who":    _s.get("project", ""),
                "Detail": f'{_s.get("environment","?")} · v{_s.get("codeversion","")}',
                "Status": _s.get("status", ""),
                "Extra":  _s.get("triggeredby", ""),
            })

    # ── releases ─────────────────────────────────────────────────────────────
    if _el_type in ("All", "Releases"):
        _rel_f = list(scope_filters())
        _rel_r = es_search(
            IDX["releases"],
            {"query": {"bool": {"filter": _rel_f}} if _rel_f else {"match_all": {}},
             "sort": [{"releasedate": "desc"}]},
            size=int(_el_limit),
        )
        for _h in _rel_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _ts = parse_dt(_s.get("releasedate"))
            events.append({
                "_ts":    _ts,
                "type":   "release",
                "When":   fmt_dt(_s.get("releasedate"), "%Y-%m-%d %H:%M"),
                "Who":    _s.get("application", ""),
                "Detail": f'v{_s.get("codeversion","")} → RLM: {_s.get("RLM_STATUS","")}',
                "Status": _s.get("RLM_STATUS", ""),
                "Extra":  "",
            })

    # ── commits ──────────────────────────────────────────────────────────────
    if _el_type in ("All", "Commits"):
        _com_f = [range_filter("commitdate", start_dt, end_dt)] + list(scope_filters())
        _com_r = es_search(
            IDX["commits"],
            {"query": {"bool": {"filter": _com_f}},
             "sort": [{"commitdate": "desc"}]},
            size=int(_el_limit),
        )
        for _h in _com_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _ts = parse_dt(_s.get("commitdate"))
            events.append({
                "_ts":    _ts,
                "type":   "commit",
                "When":   fmt_dt(_s.get("commitdate"), "%Y-%m-%d %H:%M"),
                "Who":    _s.get("project", _s.get("repository", "")),
                "Detail": f'{_s.get("branch","")} · {_s.get("authorname","")}',
                "Status": "",
                "Extra":  (_s.get("commitmessage") or "")[:80],
            })

    # ── sort & render ─────────────────────────────────────────────────────────
    events.sort(key=lambda e: e["_ts"] or pd.Timestamp("1970-01-01", tz="UTC"), reverse=True)
    events = events[:int(_el_limit)]

    if not events:
        inline_note("No events match the current filters.", "info")
    else:
        # Render as a styled HTML table for density + badges
        _rows_html = []
        for ev in events:
            _rows_html.append(
                f"<tr>"
                f'<td style="white-space:nowrap;color:#64748b;font-size:0.78rem">{ev["When"]}</td>'
                f'<td style="padding:0 6px">{_TYPE_BADGE[ev["type"]]}</td>'
                f'<td style="font-weight:600;color:#1e293b;font-size:0.82rem">{ev["Who"]}</td>'
                f'<td style="color:#475569;font-size:0.8rem">{ev["Detail"]}</td>'
                f'<td style="padding:0 6px">{_status_chip(ev["Status"])}</td>'
                f'<td style="color:#94a3b8;font-size:0.75rem;max-width:260px;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{ev["Extra"]}</td>'
                f"</tr>"
            )
        _table_html = (
            '<div style="overflow-y:auto;max-height:72vh">'
            '<table style="width:100%;border-collapse:collapse;font-family:inherit">'
            '<thead><tr style="border-bottom:2px solid #e2e8f0;text-align:left">'
            '<th style="padding:6px 4px;color:#64748b;font-size:0.75rem;font-weight:600">TIME</th>'
            '<th style="padding:6px 4px;color:#64748b;font-size:0.75rem;font-weight:600">TYPE</th>'
            '<th style="padding:6px 4px;color:#64748b;font-size:0.75rem;font-weight:600">PROJECT</th>'
            '<th style="padding:6px 4px;color:#64748b;font-size:0.75rem;font-weight:600">DETAIL</th>'
            '<th style="padding:6px 4px;color:#64748b;font-size:0.75rem;font-weight:600">STATUS</th>'
            '<th style="padding:6px 4px;color:#64748b;font-size:0.75rem;font-weight:600">NOTE</th>'
            '</tr></thead>'
            '<tbody>' + "".join(_rows_html) + "</tbody>"
            "</table></div>"
        )
        st.markdown(
            f'<p style="font-size:0.8rem;color:#64748b;margin:0 0 8px">'
            f'Showing {len(events)} events · sorted newest first</p>'
            + _table_html,
            unsafe_allow_html=True,
        )


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
