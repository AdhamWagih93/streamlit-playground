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

/* -------- Hide Streamlit chrome — keep header bar visible but unused -------- */
footer, #MainMenu { visibility: hidden; }
/* header stays visible — do NOT hide header[data-testid="stHeader"] */

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


def composite_unique_versions(
    index: str,
    field: str,
    query: dict,
    page_size: int = COMPOSITE_PAGE,
) -> dict[str, int]:
    """Like composite_terms but counts distinct ``codeversion`` values per key.

    Returns ``{key: unique_codeversion_count}`` — eliminates re-deployments /
    re-builds of the same version so the lifecycle funnel reflects real
    progression of code rather than repeated CI runs.
    """
    result: dict[str, int] = {}
    after: dict | None = None
    for _ in range(COMPOSITE_MAX_PAGES):
        comp: dict[str, Any] = {
            "size": page_size,
            "sources": [{"k": {"terms": {"field": field}}}],
        }
        if after:
            comp["after"] = after
        body = {
            "query": query,
            "aggs": {
                "groups": {
                    "composite": comp,
                    "aggs": {
                        "uv": {"cardinality": {"field": "codeversion"}}
                    },
                }
            },
        }
        res = es_search(index, body, size=0)
        groups = res.get("aggregations", {}).get("groups", {}) or {}
        buckets = groups.get("buckets", []) or []
        if not buckets:
            break
        for b in buckets:
            key = b.get("key", {}).get("k")
            if key is not None:
                result[key] = int(b.get("uv", {}).get("value", 0) or 0)
        after = groups.get("after_key")
        if not after:
            break
    return result


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

    Tries multiple strategies in order so that every common ES date format
    succeeds rather than silently returning None:

    1. Numeric (int/float) → epoch-milliseconds.  If the value looks like
       epoch-seconds (≤ 13 digits, < 1e11) we also try that unit.
    2. All-digit string → same epoch-ms / epoch-s logic.
    3. String with pd.to_datetime(utc=True) — works for tz-aware ISO strings.
    4. String with pd.to_datetime() (no utc flag) then manual tz_localize /
       tz_convert — handles naive ISO strings that pandas 2.x rejects with
       utc=True.
    5. Explicit ISO 8601 stripping of the trailing 'Z' for environments where
       pandas still chokes on that suffix.
    """
    if value is None:
        return None

    def _to_utc(ts: "pd.Timestamp") -> "pd.Timestamp":
        if ts.tzinfo is None:
            return ts.tz_localize("UTC")
        return ts.tz_convert("UTC")

    # ── 1. Numeric ────────────────────────────────────────────────────────────
    if isinstance(value, (int, float)):
        n = float(value)
        # Epoch-seconds if small enough (before year 5138 in ms = 1e11 ms)
        unit = "s" if n < 1e11 else "ms"
        try:
            return pd.Timestamp(int(n), unit=unit, tz="UTC")
        except Exception:
            pass

    s = str(value).strip()
    if not s or s.lower() in ("none", "null", "nan", "-"):
        return None

    # ── 2. All-digit string ───────────────────────────────────────────────────
    if s.lstrip("-").isdigit():
        n = int(s)
        unit = "s" if abs(n) < 1e11 else "ms"
        try:
            return pd.Timestamp(n, unit=unit, tz="UTC")
        except Exception:
            pass

    # ── 3. pd.to_datetime with utc=True (tz-aware strings, e.g. "…Z") ────────
    try:
        return _to_utc(pd.to_datetime(s, utc=True))
    except Exception:
        pass

    # ── 4. pd.to_datetime without utc flag, then localise ────────────────────
    try:
        return _to_utc(pd.to_datetime(s))
    except Exception:
        pass

    # ── 5. Strip trailing Z and retry (some older ES mappings) ───────────────
    if s.endswith("Z"):
        try:
            ts = pd.to_datetime(s[:-1])
            return _to_utc(ts)
        except Exception:
            pass

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
    "1h":       timedelta(hours=1),
    "6h":       timedelta(hours=6),
    "12h":      timedelta(hours=12),
    "1d":       timedelta(days=1),
    "3d":       timedelta(days=3),
    "7d":       timedelta(days=7),
    "14d":      timedelta(days=14),
    "30d":      timedelta(days=30),
    "90d":      timedelta(days=90),
    "180d":     timedelta(days=180),
    "1y":       timedelta(days=365),
    "All-time": None,   # no lower bound — ES will scan all docs
    "Custom":   None,
}

_PRESET_GROUPS = [
    ["1h", "6h", "12h", "1d"],
    ["3d", "7d", "14d", "30d"],
    ["90d", "180d", "1y", "Custom"],
]


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
# COMMAND BAR
# =============================================================================

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

# ── Row 1: title + company/project visual selectors + toggles ───────────────
_cb1 = st.columns([1.8, 2, 2, 0.7, 0.7, 0.7])

with _cb1[0]:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:8px;padding-top:8px;">'
        '<span style="width:8px;height:8px;border-radius:50%;background:#10b981;'
        'box-shadow:0 0 6px #10b981;display:inline-block;flex-shrink:0;"></span>'
        '<span style="font-size:1.05rem;font-weight:700;color:#0f172a;letter-spacing:-0.01em;">'
        'CI/CD Command Center</span></div>',
        unsafe_allow_html=True,
    )

with _cb1[1]:
    _company_options = [_ALL] + _all_companies
    _co_idx = st.session_state.get("_co_idx", 0)
    company_pick = st.selectbox(
        "Company",
        _company_options,
        index=_co_idx,
        key="company_pick",
        help=f"{len(_all_companies)} companies in inventory",
    )
    company_filter = "" if company_pick == _ALL else company_pick
    # Visual badge row beneath
    if _all_companies:
        _badge_co = "".join(
            f'<span style="display:inline-block;margin:2px 3px 0 0;padding:1px 8px;'
            f'border-radius:999px;font-size:0.68rem;font-weight:600;cursor:pointer;'
            f'background:{"#ede9fe" if company_pick==c else "#f1f5f9"};'
            f'color:{"#6d28d9" if company_pick==c else "#64748b"};'
            f'border:1px solid {"#ddd6fe" if company_pick==c else "#e2e8f0"}">{c}</span>'
            for c in _all_companies[:8]
        )
        st.markdown(f'<div style="line-height:1.6">{_badge_co}</div>', unsafe_allow_html=True)

with _cb1[2]:
    # Filter projects by chosen company using inventory
    if company_filter:
        _proj_options = [_ALL] + [
            p for p in _all_projects
            # keep all — inventory may not have perfect company→project join at this point
        ]
    else:
        _proj_options = [_ALL] + _all_projects
    project_pick = st.selectbox(
        "Project",
        _proj_options,
        index=0,
        key="project_pick",
        help=f"{len(_all_projects)} projects in inventory",
    )
    project_filter = "" if project_pick == _ALL else project_pick
    if _all_projects:
        _badge_pr = "".join(
            f'<span style="display:inline-block;margin:2px 3px 0 0;padding:1px 8px;'
            f'border-radius:999px;font-size:0.68rem;font-weight:600;'
            f'background:{"#ede9fe" if project_pick==p else "#f1f5f9"};'
            f'color:{"#6d28d9" if project_pick==p else "#64748b"};'
            f'border:1px solid {"#ddd6fe" if project_pick==p else "#e2e8f0"}">{p}</span>'
            for p in _all_projects[:6]
        )
        st.markdown(f'<div style="line-height:1.6">{_badge_pr}</div>', unsafe_allow_html=True)

with _cb1[3]:
    auto_refresh = st.toggle("Auto", value=False, help="Auto-refresh every 60s", key="auto_refresh")

with _cb1[4]:
    exclude_svc = st.toggle(
        "Excl. svc",
        value=True,
        help="Exclude service account 'azure_sql' from commit counts",
        key="exclude_svc",
    )

with _cb1[5]:
    if st.button("↻", help="Clear cache & reload", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── Row 2: time window segmented button group ────────────────────────────────
_TW_LABELS = list(PRESETS.keys())
_preset_default_idx = _TW_LABELS.index("7d")

# Use a radio rendered as segmented buttons via CSS
st.markdown("""
<style>
div[data-testid="stRadio"] > div { flex-wrap: wrap; gap: 4px; }
div[data-testid="stRadio"] label {
    background: #f1f5f9 !important;
    border: 1px solid #e2e8f0 !important;
    border-radius: 8px !important;
    padding: 4px 12px !important;
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    color: #475569 !important;
    cursor: pointer !important;
    transition: all .12s ease;
}
div[data-testid="stRadio"] label:has(input:checked) {
    background: #ede9fe !important;
    border-color: #a78bfa !important;
    color: #6d28d9 !important;
}
div[data-testid="stRadio"] label span { display: none !important; }
div[data-testid="stRadio"] label p { margin: 0 !important; font-size: 0.78rem !important; }
</style>
""", unsafe_allow_html=True)

preset = st.radio(
    "Time window",
    _TW_LABELS,
    index=_preset_default_idx,
    horizontal=True,
    label_visibility="collapsed",
    key="time_preset",
)

# Custom range or All-time — revealed only when needed
if preset == "Custom":
    dr = st.columns([1, 1, 4])
    today = datetime.now(timezone.utc).date()
    d_start = dr[0].date_input("From", today - timedelta(days=7))
    d_end   = dr[1].date_input("To",   today)
    start_dt = datetime.combine(d_start, datetime.min.time(), tzinfo=timezone.utc)
    end_dt   = datetime.combine(d_end,   datetime.max.time(), tzinfo=timezone.utc)
elif preset == "All-time":
    end_dt   = datetime.now(timezone.utc)
    start_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)   # epoch-like lower bound
else:
    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - PRESETS[preset]  # type: ignore[operator]

delta       = end_dt - start_dt
prior_end   = start_dt
prior_start = start_dt - delta
interval    = pick_interval(delta)
now_utc     = datetime.now(timezone.utc)
pending_window_start = now_utc - timedelta(days=30)

_window_label = "All-time" if preset == "All-time" else f"{start_dt:%Y-%m-%d %H:%M} → {end_dt:%Y-%m-%d %H:%M} UTC"
st.caption(
    f"{_window_label}  ·  bucket {interval}  ·  vs prior equal window  ·  {now_utc:%H:%M} UTC"
    + ("  ·  ⊘ azure_sql excluded" if exclude_svc else "")
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


def commit_scope_filters() -> list[dict]:
    """scope_filters() + optional service-account exclusion for commit queries."""
    fs = list(scope_filters())
    if exclude_svc:
        fs.append({"bool": {"must_not": [{"term": {"authorname": "azure_sql"}}]}})
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
    use_commit_scope: bool = False,
) -> int:
    base = commit_scope_filters() if use_commit_scope else scope_filters()
    filters = [range_filter(field, s, e)] + base + (extra or [])
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

# -- Commits (respects service-account exclusion toggle) ---------------------
commits_now  = count_with_range(IDX["commits"], "commitdate", start_dt, end_dt,     use_commit_scope=True)
commits_prev = count_with_range(IDX["commits"], "commitdate", prior_start, prior_end, use_commit_scope=True)

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

# Active applications in window (via cardinality on builds.application)
active_res = es_search(
    IDX["builds"],
    {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "aggs": {"apps": {"cardinality": {"field": "application"}}},
    },
    size=0,
)
active_projs = int(
    active_res.get("aggregations", {}).get("apps", {}).get("value", 0) or 0
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
# EVENT TICKER — subtle always-visible live feed at top of content
# =============================================================================

@st.cache_data(ttl=60, show_spinner=False)   # short TTL — meant to feel live
def _ticker_events(scope_json: str, excl_svc: bool) -> list[dict]:
    """Fetch the 12 most-recent events (any type) for the ticker strip."""
    _sf = json.loads(scope_json)
    _commit_f = _sf + ([{"bool": {"must_not": [{"term": {"authorname": "azure_sql"}}]}}] if excl_svc else [])
    _now = datetime.now(timezone.utc)
    _win = _now - timedelta(hours=48)
    _evts: list[dict] = []

    # Prod deploys
    _r = _run_search(IDX["deployments"], json.dumps({
        "query": {"bool": {"filter": [
            range_filter("startdate", _win, _now),
            {"term": {"environment": "prd"}},
        ] + _sf}},
        "sort": [{"startdate": "desc"}], "track_total_hits": True,
    }, default=str, sort_keys=True), 6)
    for _h in _r.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _ok = (_s.get("status") or "").upper() not in ("FAILED", "FAILURE", "FAILED")
        _evts.append({
            "ts": parse_dt(_s.get("startdate")),
            "type": "prd-deploy",
            "label": f'PRD deploy · {_s.get("project","")} v{_s.get("codeversion","")}',
            "ok": _ok,
        })

    # Releases
    _r = _run_search(IDX["releases"], json.dumps({
        "query": {"bool": {"filter": [range_filter("releasedate", _win, _now)] + _sf}},
        "sort": [{"releasedate": "desc"}], "track_total_hits": True,
    }, default=str, sort_keys=True), 6)
    for _h in _r.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _evts.append({
            "ts": parse_dt(_s.get("releasedate")),
            "type": "release",
            "label": f'Release · {_s.get("application","")} v{_s.get("codeversion","")}',
            "ok": True,
        })

    # Failed builds
    _r = _run_search(IDX["builds"], json.dumps({
        "query": {"bool": {"filter": [
            range_filter("startdate", _win, _now),
            {"terms": {"status": FAILED_STATUSES}},
        ] + _sf}},
        "sort": [{"startdate": "desc"}], "track_total_hits": True,
    }, default=str, sort_keys=True), 4)
    for _h in _r.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _evts.append({
            "ts": parse_dt(_s.get("startdate")),
            "type": "fail",
            "label": f'Build failed · {_s.get("project","")} {_s.get("branch","")}',
            "ok": False,
        })

    _evts.sort(key=lambda e: e["ts"] or pd.Timestamp("1970-01-01", tz="UTC"), reverse=True)
    return _evts[:14]


_tick_scope = json.dumps(scope_filters(), sort_keys=True)
_tick_evts = _ticker_events(_tick_scope, exclude_svc)

if _tick_evts:
    _TYPE_CHIP = {
        "prd-deploy": ("PRD", "#16a34a", "#f0fdf4"),
        "release":    ("REL", "#7c3aed", "#f5f3ff"),
        "fail":       ("FAIL", "#dc2626", "#fff1f2"),
    }
    _ticker_html_items = []
    for _te in _tick_evts:
        _ch_lbl, _ch_clr, _ch_bg = _TYPE_CHIP.get(_te["type"], ("EVT", "#64748b", "#f8fafc"))
        _age_h = age_hours(_te["ts"]) or 0
        _age_str = f"{_age_h}h ago" if _age_h < 24 else f"{_age_h//24}d ago"
        _item_bg = "#fff1f2" if not _te["ok"] else "#f8fafc"
        _ticker_html_items.append(
            f'<span style="display:inline-flex;align-items:center;gap:6px;'
            f'padding:3px 10px 3px 4px;margin:0 6px 0 0;'
            f'background:{_item_bg};border:1px solid #e2e8f0;border-radius:20px;'
            f'white-space:nowrap;font-size:0.73rem;">'
            f'  <span style="background:{_ch_clr};color:#fff;font-size:0.63rem;font-weight:700;'
            f'  padding:1px 6px;border-radius:999px">{_ch_lbl}</span>'
            f'  <span style="color:#334155">{_te["label"]}</span>'
            f'  <span style="color:#94a3b8">{_age_str}</span>'
            f'</span>'
        )
    st.markdown(
        '<div style="overflow-x:auto;white-space:nowrap;padding:6px 0 8px;'
        'border-bottom:1px solid #e2e8f0;margin-bottom:8px">'
        + "".join(_ticker_html_items) + "</div>",
        unsafe_allow_html=True,
    )


# =============================================================================
# ALERTS — compact chips, vivid colors
# =============================================================================

# _lc_classified is populated later in the lifecycle section; pre-init so
# alert popovers that reference it don't raise NameError on first render.
_lc_classified: dict[str, str] = {}

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
        f"{dormant_pct:.0f}% of inventory applications had no builds in the window",
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
    # Each alert has an inline "View" popover that routes straight to the breakdown
    for _ai, (_sev, _icon, _title, _detail) in enumerate(alerts):
        _al_c1, _al_c2 = st.columns([5, 0.9])
        with _al_c1:
            st.markdown(
                f'<div class="alert {_sev}" style="margin-bottom:4px">'
                f'  <div class="icon">{_icon}</div>'
                f'  <div><b>{_title}</b><span class="sub"> — {_detail}</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _al_c2:
            with st.popover(f"View [{_ai+1}]", use_container_width=True):
                st.markdown(f"**{_title}**")
                # Route by severity/content
                if "approval" in _title.lower() or "pending" in _title.lower():
                    _ar = es_search(IDX["requests"], {**stuck_body, "sort": [{"RequestDate": "asc"}]}, size=100)
                    _ah = _ar.get("hits", {}).get("hits", [])
                    if _ah:
                        st.dataframe(pd.DataFrame([{
                            "#": h["_source"].get("RequestNumber"),
                            "Type": h["_source"].get("RequestType"),
                            "Requester": h["_source"].get("Requester"),
                            "Team": h["_source"].get("RequesterTeam"),
                            "Age (h)": age_hours(h["_source"].get("RequestDate"), now_utc),
                        } for h in _ah]), use_container_width=True, hide_index=True, height=420)
                    else:
                        inline_note("No stuck approvals.", "success")

                elif "production deployment" in _title.lower():
                    _ar = es_search(IDX["deployments"], {
                        "query": {"bool": {"filter": [
                            range_filter("startdate", start_dt, end_dt),
                            {"term": {"environment": "prd"}},
                            {"terms": {"status": FAILED_STATUSES}},
                        ] + scope_filters()}},
                        "sort": [{"startdate": "desc"}]}, size=100)
                    _ah = _ar.get("hits", {}).get("hits", [])
                    if _ah:
                        st.dataframe(pd.DataFrame([{
                            "When":        fmt_dt(h["_source"].get("startdate"), "%Y-%m-%d %H:%M"),
                            "Application": h["_source"].get("application") or h["_source"].get("project"),
                            "Project":     h["_source"].get("project"),
                            "Version":     h["_source"].get("codeversion"),
                            "Status":      h["_source"].get("status"),
                        } for h in _ah]), use_container_width=True, hide_index=True, height=420)
                    else:
                        inline_note("No failed PRD deploys.", "success")

                elif "build success" in _title.lower() or "build failure" in _title.lower():
                    _ar = es_search(IDX["builds"], {
                        "query": {"bool": {"filter": [
                            range_filter("startdate", start_dt, end_dt),
                            {"terms": {"status": FAILED_STATUSES}},
                        ] + scope_filters()}},
                        "sort": [{"startdate": "desc"}]}, size=100)
                    _ah = _ar.get("hits", {}).get("hits", [])
                    if _ah:
                        st.dataframe(pd.DataFrame([{
                            "When":        fmt_dt(h["_source"].get("startdate"), "%Y-%m-%d %H:%M"),
                            "Application": h["_source"].get("application") or h["_source"].get("project"),
                            "Project":     h["_source"].get("project"),
                            "Branch":      h["_source"].get("branch"),
                            "Version":     h["_source"].get("codeversion"),
                            "Tech":        h["_source"].get("technology"),
                        } for h in _ah]), use_container_width=True, hide_index=True, height=420)
                    else:
                        inline_note("No build failures.", "success")

                elif "jira" in _title.lower():
                    _ar = es_search(IDX["jira"], {
                        "query": {"bool": {
                            "filter": [{"range": {"updated": {"lte": (now_utc - timedelta(days=30)).isoformat()}}}] + scope_filters(),
                            "must_not": [{"terms": {"status": CLOSED_JIRA}}],
                        }}, "sort": [{"updated": "asc"}]}, size=100)
                    _ah = _ar.get("hits", {}).get("hits", [])
                    if _ah:
                        st.dataframe(pd.DataFrame([{
                            "Key": h["_source"].get("issuekey"),
                            "Priority": h["_source"].get("priority"),
                            "Status": h["_source"].get("status"),
                            "Assignee": h["_source"].get("assignee"),
                            "Updated": fmt_dt(h["_source"].get("updated"), "%Y-%m-%d"),
                        } for h in _ah]), use_container_width=True, hide_index=True, height=420)
                    else:
                        inline_note("No aged JIRA.", "success")

                elif "commit" in _title.lower():
                    _ar = es_search(IDX["commits"], {
                        "query": {"bool": {"filter": [
                            range_filter("commitdate", start_dt, end_dt),
                        ] + commit_scope_filters()}},
                        "aggs": {"authors": {"terms": {"field": "authorname", "size": 20}}},
                        "sort": [{"commitdate": "desc"}]}, size=30)
                    _ah = _ar.get("hits", {}).get("hits", [])
                    if _ah:
                        st.dataframe(pd.DataFrame([{
                            "When":    fmt_dt(h["_source"].get("commitdate"), "%Y-%m-%d %H:%M"),
                            "Author":  h["_source"].get("authorname"),
                            "Project": h["_source"].get("project"),
                            "Branch":  h["_source"].get("branch"),
                        } for h in _ah]), use_container_width=True, hide_index=True, height=360)
                        _top_auth = bucket_rows(_ar, "authors")
                        if _top_auth:
                            st.caption("Top contributors: " + ", ".join(
                                f"{b['key']} ({b['doc_count']})" for b in _top_auth[:5]
                            ))
                    else:
                        inline_note("No commits.", "info")

                else:
                    # Generic: dormant applications
                    _dormant_list = sorted(p for p, v in _lc_classified.items() if v in ("Dark", "Dead in Dev"))[:50]
                    if _dormant_list:
                        st.dataframe(pd.DataFrame({"Application": _dormant_list}),
                                     use_container_width=True, hide_index=True)
                    else:
                        inline_note("No dormant applications in window.", "info")


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
# Project landscape treemap — ALL-TIME data (ignores time filter)
# Hierarchy: status → project → application
# "project" field in builds == application name; inventory gives us the
# parent project grouping via project.keyword → application.keyword mapping.
# ---------------------------------------------------------------------------

# ── Inventory: project → [applications] mapping (all-time, scope-filtered) ──
_tm_inv_query = {"bool": {"filter": scope_filters_inv()}} if scope_filters_inv() else {"match_all": {}}
_tm_inv_map   = composite_terms(IDX["inventory"], "project.keyword", _tm_inv_query)

_tm_proj_to_apps: dict[str, list[str]] = {}
_inv_app_res = es_search(
    IDX["inventory"],
    {
        "query": _tm_inv_query,
        "aggs": {
            "projs": {
                "terms": {"field": "project.keyword", "size": 500},
                "aggs": {"apps": {"terms": {"field": "application.keyword", "size": 200}}},
            }
        },
    },
)
for _pb in bucket_rows(_inv_app_res, "projs"):
    _tm_proj_to_apps[_pb["key"]] = [
        _ab["key"] for _ab in (_pb.get("apps", {}).get("buckets") or [])
    ]

# application → parent_project reverse lookup (from inventory)
_app_to_parent: dict[str, str] = {}
for _parent, _apps in _tm_proj_to_apps.items():
    for _app in _apps:
        _app_to_parent[_app] = _parent

# ── All-time builds per application (NOT time-filtered) ─────────────────────
_tm_scope_only = {"bool": {"filter": scope_filters()}} if scope_filters() else {"match_all": {}}
_tm_active_map  = composite_terms(IDX["builds"],      "project",  _tm_scope_only)  # app → build_count
_tm_prd_map     = composite_terms(                                                  # app → prd_deploy_count
    IDX["deployments"], "project",
    {"bool": {"filter": [{"term": {"environment": "prd"}}] + scope_filters()}} if scope_filters()
    else {"bool": {"filter": [{"term": {"environment": "prd"}}]}},
)
# Unique versions built all-time per application
_tm_uv_map = composite_unique_versions(IDX["builds"], "project", _tm_scope_only)

# All-time fails per application
_tm_fail_res = es_search(IDX["builds"], {
    "query": _tm_scope_only,
    "aggs": {"apps": {"terms": {"field": "project", "size": 500},
                      "aggs": {"fails": {"filter": {"terms": {"status": FAILED_STATUSES}}}}}},
})
_tm_fail_map = {b["key"]: b.get("fails", {}).get("doc_count", 0) for b in bucket_rows(_tm_fail_res, "apps")}

# Open JIRA per application
_jira_map_tm = {b["key"]: b["doc_count"] for b in bucket_rows(
    es_search(IDX["jira"], {
        "query": {"bool": {"filter": scope_filters(), "must_not": [{"terms": {"status": CLOSED_JIRA}}]}},
        "aggs": {"apps": {"terms": {"field": "project", "size": 500}}},
    }), "apps",
)}

# ── Build per-application rows for the treemap ──────────────────────────────
_all_apps = set(_tm_active_map) | set(_tm_prd_map) | {
    app for apps in _tm_proj_to_apps.values() for app in apps
}

_tm_rows = []
for _app in _all_apps:
    _builds_all = _tm_active_map.get(_app, 0)
    _uv         = _tm_uv_map.get(_app, 0)
    _in_inv     = _app in _app_to_parent or any(_app in apps for apps in _tm_proj_to_apps.values())
    _fails      = _tm_fail_map.get(_app, 0)
    _jira_open  = _jira_map_tm.get(_app, 0)
    _in_prd     = _tm_prd_map.get(_app, 0) > 0
    _parent     = _app_to_parent.get(_app, "(ungrouped)")

    if _builds_all == 0 and _in_inv:
        _status = "Archival candidate"
    elif _builds_all > 0 and _in_prd:
        _succ_pct = (_builds_all - _fails) / _builds_all * 100 if _builds_all else 100
        _status = "Live · healthy" if _succ_pct >= 80 else "Live · at-risk"
    elif _builds_all > 0:
        _status = "Building · not in PRD"
    else:
        _status = "Unknown"

    _score = min(100, max(0, int(
        ((_builds_all - _fails) / _builds_all * 100 if _builds_all else 50) - _jira_open * 1.5
    )))
    _tm_rows.append({
        "application": _app,
        "project":     _parent,
        "builds":      max(_builds_all, 1),
        "uniq_ver":    _uv,
        "status":      _status,
        "score":       _score,
        "fails":       _fails,
        "open_jira":   _jira_open,
        "live":        "Yes" if _in_prd else "No",
    })

if _tm_rows:
    _df_tm = pd.DataFrame(_tm_rows)
    _color_map = {
        "Live · healthy":        "#16a34a",
        "Live · at-risk":        "#d97706",
        "Building · not in PRD": "#3b82f6",
        "Archival candidate":    "#94a3b8",
        "Unknown":               "#cbd5e1",
    }
    # Treemap: status → project → application  (3-level hierarchy)
    _tm_fig = px.treemap(
        _df_tm,
        path=["status", "project", "application"],
        values="builds",
        color="status",
        color_discrete_map=_color_map,
        custom_data=["fails", "open_jira", "score", "uniq_ver", "live"],
        title="Application landscape · all-time  (size = total builds · color = live/dormant status)",
    )
    _tm_fig.update_traces(
        hovertemplate=(
            "<b>%{label}</b><br>"
            "Builds (all-time): %{value:,}<br>"
            "Unique versions: %{customdata[3]}<br>"
            "Failures: %{customdata[0]}<br>"
            "Open JIRA: %{customdata[1]}<br>"
            "Health score: %{customdata[2]}/100<br>"
            "Live (in PRD): %{customdata[4]}"
            "<extra></extra>"
        ),
        textinfo="label+value",
        insidetextfont=dict(size=11, color="white"),
    )
    _tm_fig.update_layout(
        height=420,
        margin=dict(l=0, r=0, t=36, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#374151", family="Inter, sans-serif"),
    )
    st.plotly_chart(_tm_fig, use_container_width=True)

    # Status summary pills
    _status_counts = _df_tm["status"].value_counts().to_dict()
    _pills = ""
    for _s, _c_clr in _color_map.items():
        _n = _status_counts.get(_s, 0)
        if _n:
            _pills += (
                f'<span style="display:inline-flex;align-items:center;gap:5px;'
                f'margin:0 6px 4px 0;padding:3px 10px;border-radius:999px;'
                f'font-size:0.73rem;font-weight:600;'
                f'background:{_c_clr}22;color:{_c_clr};border:1px solid {_c_clr}55">'
                f'{_n} {_s}</span>'
            )
    st.markdown(f'<div style="margin-top:4px">{_pills}</div>', unsafe_allow_html=True)

    # Archival candidates alert
    _archival = _df_tm[_df_tm["status"] == "Archival candidate"].sort_values("application")
    if not _archival.empty:
        _arc_cols = st.columns([3, 1])
        with _arc_cols[0]:
            st.markdown(
                f'<div class="alert warning" style="margin-bottom:4px;">'
                f'<div class="icon">⚠</div>'
                f'<div><b>{len(_archival)} archival candidate(s)</b>'
                f'<span class="sub">— in inventory but no builds ever recorded</span></div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _arc_cols[1]:
            with st.popover("View list", use_container_width=True):
                st.markdown("**Applications with no builds — archival candidates**")
                _arc_d = _archival[["project", "application", "open_jira"]].copy()
                _arc_d.columns = ["Project", "Application", "Open JIRA"]
                st.dataframe(_arc_d, use_container_width=True, hide_index=True, height=400)
else:
    inline_note("No application data available.", "info")

# =============================================================================
# APP LIFECYCLE — pipeline stage funnel per project + bottleneck finder
# =============================================================================

st.markdown(
    '<div class="section">'
    '<div class="title-wrap">'
    '  <h2>App lifecycle &amp; bottlenecks</h2>'
    '  <span class="badge">Build → Dev → QC → Release → UAT → PRD</span>'
    '</div>'
    '<span class="hint">unique versions per application at each stage — where does each application stall?</span>'
    '</div>',
    unsafe_allow_html=True,
)

# The correct pipeline is:  Builds → Deploy Dev → Deploy QC → Deploy UAT → Deploy PRD
# field for builds/deployments: "project"  (NOT "project.keyword" — that sub-field may not exist)
# field for releases: "application"

_LC_STAGES   = ["Builds", "Deploy Dev", "Deploy QC", "Release", "Deploy UAT", "Deploy PRD"]
_LC_COLORS   = ["#6366f1", "#0ea5e9", "#8b5cf6", "#ec4899", "#f59e0b", "#16a34a"]
# "dropout" node color — neutral gray
_LC_DROPOUT  = "#e2e8f0"


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _lifecycle_data(
    s: str, e: str,
    company: str, project: str,
    excl_svc: bool,
) -> dict:
    """
    Per-stage unique-version counts (Builds → Deploy Dev → QC → Release → UAT → PRD)
    aggregated by application.  Returns:
      stage_maps    — dict[stage -> dict[application -> count]]
      applications  — sorted list of all application names seen
      totals        — dict[stage -> int]  aggregate across all applications
    """
    _s = datetime.fromisoformat(s)
    _e = datetime.fromisoformat(e)

    _scope: list[dict] = []
    if company:
        _scope.append({"term": {"company.keyword": company}})
    if project:
        _scope.append({"term": {"project": project}})
    # service-account exclusion applies to commits only (handled in callers)

    def _uv_by_app(index: str, date_field: str,
                   app_field: str = "application",
                   extra: list[dict] | None = None) -> dict[str, int]:
        """Unique codeversion count per application — eliminates re-runs."""
        _f = [range_filter(date_field, _s, _e)] + _scope + (extra or [])
        return composite_unique_versions(index, app_field, {"bool": {"filter": _f}})

    builds_by_app  = _uv_by_app(IDX["builds"],      "startdate")
    dep_dev_by_app = _uv_by_app(IDX["deployments"],  "startdate", extra=[{"term": {"environment": "dev"}}])
    dep_qc_by_app  = _uv_by_app(IDX["deployments"],  "startdate", extra=[{"term": {"environment": "qc"}}])
    rel_by_app     = _uv_by_app(IDX["releases"],      "releasedate", app_field="application")
    dep_uat_by_app = _uv_by_app(IDX["deployments"],  "startdate", extra=[{"term": {"environment": "uat"}}])
    dep_prd_by_app = _uv_by_app(IDX["deployments"],  "startdate", extra=[{"term": {"environment": "prd"}}])

    stage_maps = {
        "Builds":      builds_by_app,
        "Deploy Dev":  dep_dev_by_app,
        "Deploy QC":   dep_qc_by_app,
        "Release":     rel_by_app,
        "Deploy UAT":  dep_uat_by_app,
        "Deploy PRD":  dep_prd_by_app,
    }

    all_apps = sorted(
        set(builds_by_app) | set(dep_dev_by_app) | set(dep_qc_by_app)
        | set(rel_by_app) | set(dep_uat_by_app) | set(dep_prd_by_app)
    )

    totals = {st_: sum(stage_maps[st_].values()) for st_ in _LC_STAGES}

    return {
        "stage_maps":    stage_maps,
        "applications":  all_apps,
        "totals":        totals,
    }


_lc = _lifecycle_data(
    start_dt.isoformat(), end_dt.isoformat(),
    company_filter or "", project_filter or "",
    excl_svc=exclude_svc,
)
_stage_maps  = _lc["stage_maps"]
_lc_apps     = _lc["applications"]
_lc_totals   = _lc["totals"]


# ── Row 1: Funnel Sankey (with drop-off nodes) + stage drop table ────────────
_lc_col1, _lc_col2 = st.columns([1.6, 1])

with _lc_col1:
    _any_data = any(_lc_totals[s] > 0 for s in _LC_STAGES)
    if _any_data:
        # Build Sankey: stage nodes + one shared "Dropped" sink
        # Nodes 0..N-1 are stages; node N is the "Dropped" sink
        _sk_labels = _LC_STAGES + ["⬤ Dropped"]
        _sk_node_colors = _LC_COLORS + ["#fca5a5"]
        _DROP_NODE = len(_LC_STAGES)

        def _hex_rgba(hex6: str, alpha: float = 0.55) -> str:
            """Convert '#rrggbb' + alpha float → 'rgba(r,g,b,a)' for Plotly."""
            h = hex6.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r},{g},{b},{alpha})"

        _sk_source, _sk_target, _sk_value, _sk_link_colors = [], [], [], []

        for _i, _st in enumerate(_LC_STAGES):
            _cnt = _lc_totals[_st]
            if _cnt == 0:
                continue
            if _i < len(_LC_STAGES) - 1:
                _next_cnt = _lc_totals[_LC_STAGES[_i + 1]]
                _passed   = min(_cnt, _next_cnt)
                _dropped  = max(0, _cnt - _next_cnt)
                if _passed > 0:
                    _sk_source.append(_i)
                    _sk_target.append(_i + 1)
                    _sk_value.append(_passed)
                    _sk_link_colors.append(_hex_rgba(_LC_COLORS[_i]))
                if _dropped > 0:
                    _sk_source.append(_i)
                    _sk_target.append(_DROP_NODE)
                    _sk_value.append(_dropped)
                    _sk_link_colors.append(_hex_rgba("#fca5a5", 0.7))
            # last stage: all go to "in production"
        if _sk_source:
            _sk_fig = go.Figure(go.Sankey(
                arrangement="snap",
                node=dict(
                    pad=20,
                    thickness=24,
                    line=dict(color="#e2e8f0", width=0.5),
                    label=_sk_labels,
                    color=_sk_node_colors,
                    hovertemplate="<b>%{label}</b><br>Volume: %{value:,}<extra></extra>",
                ),
                link=dict(
                    source=_sk_source,
                    target=_sk_target,
                    value=_sk_value,
                    color=_sk_link_colors,
                    hovertemplate="%{source.label} → %{target.label}<br>%{value:,} items<extra></extra>",
                ),
            ))
            _sk_fig.update_layout(
                title=dict(
                    text="Pipeline flow · Build → Dev → QC → UAT → PRD  (red = dropped at stage)",
                    font=dict(size=13, color="#1e293b"), x=0,
                ),
                font=dict(size=11, color="#334155", family="inherit"),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=0, r=0, t=40, b=0),
                height=320,
            )
            st.plotly_chart(_sk_fig, use_container_width=True)
        else:
            inline_note("No flow data to display.", "info")
    else:
        inline_note("No pipeline activity found for the selected window / filters.", "info")

with _lc_col2:
    _bn_rows = []
    for _i in range(len(_LC_STAGES) - 1):
        _a, _b = _LC_STAGES[_i], _LC_STAGES[_i + 1]
        _va, _vb = _lc_totals[_a], _lc_totals[_b]
        if _va > 0:
            _drop = max(0.0, (_va - _vb) / _va * 100)
            _bn_rows.append({"Stage": f"{_a} → {_b}", "In": _va, "Out": _vb, "Drop": _drop})

    if _bn_rows:
        st.markdown(
            '<p style="font-size:0.82rem;font-weight:700;color:#1e293b;margin:4px 0 8px">'
            'Stage conversion — biggest bottlenecks first</p>',
            unsafe_allow_html=True,
        )
        _bn_html = []
        for _r in sorted(_bn_rows, key=lambda x: x["Drop"], reverse=True):
            _d = _r["Drop"]
            _bg = "#fff1f2" if _d >= 70 else "#fffbeb" if _d >= 40 else "#f0fdf4"
            _fg = "#991b1b" if _d >= 70 else "#92400e" if _d >= 40 else "#166534"
            _bar_bg = "#dc2626" if _d >= 70 else "#d97706" if _d >= 40 else "#16a34a"
            _bar_w = max(3, int(_d * 0.9))
            _rate_w = max(3, int((100 - _d) * 0.9))
            _bn_html.append(
                f'<div style="margin-bottom:8px">'
                f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px">'
                f'    <span style="font-size:0.77rem;color:#334155;font-weight:600">{_r["Stage"]}</span>'
                f'    <span style="font-size:0.75rem;color:#64748b">'
                f'      {int(_r["In"]):,} → {int(_r["Out"]):,}'
                f'    </span>'
                f'  </div>'
                f'  <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;background:#f1f5f9">'
                f'    <div style="width:{_rate_w}%;background:{_bar_bg};opacity:0.8"></div>'
                f'    <div style="width:{_bar_w}%;background:#fca5a5"></div>'
                f'  </div>'
                f'  <div style="margin-top:2px;text-align:right">'
                f'    <span style="font-size:0.73rem;font-weight:700;color:{_fg};'
                f'    background:{_bg};padding:1px 6px;border-radius:4px">'
                f'    {_d:.1f}% dropped</span>'
                f'  </div>'
                f'</div>'
            )
        st.markdown("".join(_bn_html), unsafe_allow_html=True)
    else:
        inline_note("No stage data to compare.", "info")


# ── Row 2: Live / Dormant classification + per-project heatmap ──────────────
st.markdown(
    '<p style="font-size:0.85rem;font-weight:700;color:#1e293b;margin:18px 0 4px">'
    'Project status — live vs dormant (classified by pipeline position)</p>',
    unsafe_allow_html=True,
)

# Classify each project into one of 5 buckets:
# Classify each application through the pipeline.
# _lc_apps contains application names keyed from the "application" field.
# _app_to_parent maps application → parent project from inventory.

_inv_apps = set(_app_to_parent.keys()) if "_app_to_parent" in dir() else set()

_lc_classified: dict[str, str] = {}
for _app in _lc_apps:
    _b   = _stage_maps["Builds"].get(_app, 0)
    _dev = _stage_maps["Deploy Dev"].get(_app, 0)
    _qc  = _stage_maps["Deploy QC"].get(_app, 0)
    _rel = _stage_maps["Release"].get(_app, 0)
    _uat = _stage_maps["Deploy UAT"].get(_app, 0)
    _prd = _stage_maps["Deploy PRD"].get(_app, 0)
    if _prd > 0:
        _lc_classified[_app] = "Live (in PRD)"
    elif _uat > 0:
        _lc_classified[_app] = "Stuck in UAT"
    elif _rel > 0 or _qc > 0:
        _lc_classified[_app] = "Dead in Quality"
    elif _b > 0:
        _lc_classified[_app] = "Dead in Dev"
    else:
        _lc_classified[_app] = "Dark"

# Inventory-only applications with no pipeline activity
for _app in _inv_apps:
    if _app not in _lc_classified:
        _lc_classified[_app] = "Dark"

_STATUS_ORDER = ["Live (in PRD)", "Stuck in UAT", "Dead in Quality", "Dead in Dev", "Dark"]
_STATUS_COLORS_MAP = {
    "Live (in PRD)":   "#16a34a",
    "Stuck in UAT":    "#d97706",
    "Dead in Quality": "#7c3aed",
    "Dead in Dev":     "#dc2626",
    "Dark":            "#94a3b8",
}
_STATUS_ICONS = {
    "Live (in PRD)":   "✓",
    "Stuck in UAT":    "⏸",
    "Dead in Quality": "⚗",
    "Dead in Dev":     "⚠",
    "Dark":            "○",
}
_STATUS_DESC = {
    "Live (in PRD)":   "has PRD deployments in window — actively in production",
    "Stuck in UAT":    "UAT deployments exist but nothing reached PRD — blocked before ops",
    "Dead in Quality": "reached QC/release but no UAT — quality gate is holding",
    "Dead in Dev":     "has builds but never deployed anywhere — dev-only loop",
    "Dark":            "no builds in window — archival candidate or genuinely inactive",
}

_counts = {s: sum(1 for v in _lc_classified.values() if v == s) for s in _STATUS_ORDER}
_total_classified = sum(_counts.values())

# Summary pill bar
if _total_classified:
    _pill_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px">'
    for _s in _STATUS_ORDER:
        _c = _counts[_s]
        if _c == 0:
            continue
        _col = _STATUS_COLORS_MAP[_s]
        _pct = _c / _total_classified * 100
        _pill_html += (
            f'<div style="display:flex;align-items:center;gap:6px;'
            f'background:#f8fafc;border:1px solid #e2e8f0;border-left:3px solid {_col};'
            f'border-radius:8px;padding:6px 12px;" title="{_STATUS_DESC[_s]}">'
            f'  <span style="font-size:1rem;font-weight:700;color:{_col}">{_STATUS_ICONS[_s]}</span>'
            f'  <div>'
            f'    <div style="font-size:1.0rem;font-weight:700;color:#0f172a;line-height:1">{_c}</div>'
            f'    <div style="font-size:0.68rem;color:#64748b;font-weight:600">{_s}</div>'
            f'  </div>'
            f'  <div style="font-size:0.72rem;color:#94a3b8;margin-left:4px">{_pct:.0f}%</div>'
            f'</div>'
        )
    _pill_html += "</div>"
    st.markdown(_pill_html, unsafe_allow_html=True)

# Alert rows with per-application breakdown
for _s, _desc in [
    ("Stuck in UAT",    "application(s) have UAT deploys but never reached PRD"),
    ("Dead in Quality", "application(s) reached QC/release but were never promoted to UAT"),
    ("Dead in Dev",     "application(s) have builds but were never deployed anywhere"),
    ("Dark",            "application(s) have no builds in window — review for archival"),
]:
    _n = _counts[_s]
    if _n == 0:
        continue
    _kind = "warning" if _s in ("Stuck in UAT", "Dead in Quality") else "danger" if _s == "Dead in Dev" else "info"
    _app_list = sorted(a for a, v in _lc_classified.items() if v == _s)
    _preview = ", ".join(_app_list[:5]) + (f" +{len(_app_list)-5} more" if len(_app_list) > 5 else "")
    _a_col1, _a_col2 = st.columns([4, 1])
    with _a_col1:
        inline_note(f"{_n} {_desc}  ·  {_preview}", _kind)
    with _a_col2:
        with st.popover("View all", use_container_width=True):
            st.markdown(f"**{_s}** — {_STATUS_DESC[_s]}")
            _pl_df = pd.DataFrame([{
                "Application": _a,
                "Project":     _app_to_parent.get(_a, "—") if "_app_to_parent" in dir() else "—",
                "Builds":      _stage_maps["Builds"].get(_a, 0),
                "Dev":         _stage_maps["Deploy Dev"].get(_a, 0),
                "QC":          _stage_maps["Deploy QC"].get(_a, 0),
                "Release":     _stage_maps["Release"].get(_a, 0),
                "UAT":         _stage_maps["Deploy UAT"].get(_a, 0),
                "PRD":         _stage_maps["Deploy PRD"].get(_a, 0),
            } for _a in _app_list])
            st.dataframe(_pl_df, use_container_width=True, hide_index=True)

# ── Row 3: Per-application pipeline heatmap ──────────────────────────────────
if _lc_apps:
    _app_activity = {
        a: sum(_stage_maps[s].get(a, 0) for s in _LC_STAGES)
        for a in _lc_apps
    }
    _top_apps = sorted(_app_activity, key=_app_activity.get, reverse=True)[:35]  # type: ignore[arg-type]

    # Y-axis: "icon AppName [Project]"
    _y_labels = [
        f"{_STATUS_ICONS.get(_lc_classified.get(a,'Dark'), '○')} {a}"
        + (f" [{_app_to_parent[a]}]" if "_app_to_parent" in dir() and a in _app_to_parent else "")
        for a in _top_apps
    ]

    _hm_z, _hm_text = [], []
    for _a in _top_apps:
        _row_z, _row_text = [], []
        _builds = _stage_maps["Builds"].get(_a, 0)
        _ref = _builds if _builds else 1
        for _s in _LC_STAGES:
            _v = _stage_maps[_s].get(_a, 0)
            _rate = min(100.0, _v / _ref * 100) if _s != "Builds" else (100.0 if _v else 0.0)
            _row_z.append(_rate)
            _row_text.append(str(_v) if _v else "—")
        _hm_z.append(_row_z)
        _hm_text.append(_row_text)

    _hm_fig = go.Figure(go.Heatmap(
        z=_hm_z,
        x=_LC_STAGES,
        y=_y_labels,
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
            title=dict(text="% of builds", side="right", font=dict(size=11, color="#64748b")),
            thickness=12, len=0.85,
            tickfont=dict(size=10, color="#64748b"),
            outlinewidth=0,
        ),
        hovertemplate=(
            "<b>%{y}</b><br>Stage: %{x}<br>"
            "Unique versions: %{text}<br>Conv: %{z:.1f}%<extra></extra>"
        ),
    ))
    _hm_fig.update_layout(
        title=dict(
            text="Pipeline conversion per application · % of built versions that reached each stage",
            font=dict(size=13, color="#1e293b"), x=0,
        ),
        xaxis=dict(
            side="top", tickfont=dict(size=12, color="#334155", family="inherit"),
            showgrid=False, zeroline=False,
        ),
        yaxis=dict(
            tickfont=dict(size=10, color="#334155", family="inherit"),
            autorange="reversed", showgrid=False, zeroline=False,
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=70, t=56, b=0),
        height=max(300, len(_top_apps) * 26),
        font=dict(family="inherit"),
    )
    st.plotly_chart(_hm_fig, use_container_width=True)
    st.caption(
        f"✓ = Live in PRD  ·  ⏸ = Stuck in UAT  ·  ⚗ = Dead in QC  ·  ⚠ = Dead in Dev  ·  ○ = Dark  "
        f"·  Showing top {len(_top_apps)} by build volume  ·  color = % of builds that reached each stage"
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
    st.markdown("**Application health scoreboard** — top 15 most active applications, joined across indices")

    # Pull per-application builds with success/fail breakdown, and per-application deploys
    body_b = {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + scope_filters()
            }
        },
        "aggs": {
            "apps": {
                "terms": {"field": "application", "size": 50},
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
        "aggs": {"apps": {"terms": {"field": "application", "size": 200}}},
    }
    res_d = es_search(IDX["deployments"], body_d)
    prd_map = {b["key"]: b["doc_count"] for b in bucket_rows(res_d, "apps")}

    # JIRA open — per application
    body_j = {
        "query": {
            "bool": {
                "filter": scope_filters(),
                "must_not": [{"terms": {"status": CLOSED_JIRA}}],
            }
        },
        "aggs": {"apps": {"terms": {"field": "application", "size": 500}}},
    }
    res_j = es_search(IDX["jira"], body_j)
    jira_map = {b["key"]: b["doc_count"] for b in bucket_rows(res_j, "apps")}

    # Pending requests — per application (best effort — falls back to 0 if field missing)
    body_r = {
        "query": {
            "bool": {
                "filter": [
                    range_filter("RequestDate", pending_window_start, now_utc),
                    {"terms": {"Status": PENDING_STATUSES}},
                ]
            }
        },
        "aggs": {"apps": {"terms": {"field": "application", "size": 500}}},
    }
    res_r = es_search(IDX["requests"], body_r)
    pend_map = {b["key"]: b["doc_count"] for b in bucket_rows(res_r, "apps")}

    rows = []
    for bk in bucket_rows(res_b, "apps")[:15]:
        app = bk["key"]
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
        score -= min(jira_map.get(app, 0), 20) * 1.5  # jira drag
        score -= min(pend_map.get(app, 0), 10) * 3    # pending requests drag
        score = max(0, min(100, int(round(score))))
        rows.append({
            "Application": app,
            "Builds":      total,
            "Fails":       fails,
            "Succ %":      f"{succ_pct:.0f}%",
            "Prod dep":    prd_map.get(app, 0),
            "Open JIRA":   jira_map.get(app, 0),
            "Pending req": pend_map.get(app, 0),
            "Last build":  last,
            "Score":       score,
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
    '⚠ Risk spotlight — applications failing multiple signals simultaneously'
    '</div>',
    unsafe_allow_html=True,
)

# Reuse the maps from above (if present) to flag cross-signal risk.
try:
    risk_rows = []
    _all_apps_risk = set(prd_map) | set(jira_map) | set(pend_map)
    for bk in bucket_rows(res_b, "apps"):
        _all_apps_risk.add(bk["key"])

    # Build a quick lookup for build stats
    build_stats = {
        bk["key"]: (
            bk["doc_count"],
            bk.get("fails", {}).get("doc_count", 0),
        )
        for bk in bucket_rows(res_b, "apps")
    }
    for app in _all_apps_risk:
        builds_t, fails_t = build_stats.get(app, (0, 0))
        oj   = jira_map.get(app, 0)
        pr   = pend_map.get(app, 0)
        pd_d = prd_map.get(app, 0)
        flags = []
        if builds_t and fails_t / max(builds_t, 1) > 0.2: flags.append("build-fail>20%")
        if oj >= 5:  flags.append(f"{oj} open JIRA")
        if pr >= 2:  flags.append(f"{pr} pending req")
        if pd_d and fails_t and builds_t and fails_t / builds_t > 0.3:
            flags.append("prod + failing")
        if len(flags) >= 2:
            risk_rows.append({
                "Application": app,
                "Signals":     " · ".join(flags),
                "Builds":      builds_t,
                "Fails":       fails_t,
                "JIRA":        oj,
                "Pending":     pr,
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
            '<div><b>No applications trigger multiple risk signals.</b>'
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
                    "When":        fmt_dt(_s.get("startdate"), "%m-%d %H:%M"),
                    "Application": _s.get("application") or _s.get("project"),
                    "Project":     _s.get("project"),
                    "Branch":      _s.get("branch"),
                    "Status":      _s.get("status"),
                    "Version":     _s.get("codeversion"),
                    "Tech":        _s.get("technology"),
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
                    "When":        fmt_dt(_s.get("startdate"), "%m-%d %H:%M"),
                    "Application": _s.get("application") or _s.get("project"),
                    "Project":     _s.get("project"),
                    "Env":         _s.get("environment"),
                    "Status":      _s.get("status"),
                    "Version":     _s.get("codeversion"),
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
            "top_apps": {"terms": {"field": "application", "size": 10}},
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

    tops = bucket_rows(res, "top_apps")
    if tops:
        df_top = pd.DataFrame(
            [{"application": b["key"], "builds": b["doc_count"]} for b in tops]
        ).sort_values("builds")
        fig2 = px.bar(
            df_top, x="builds", y="application", orientation="h",
            title="Top applications by build count",
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
        inline_note("No application data.", "info", c2)

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

# Dormant applications — cross-joins inventory × builds (composite-paginated → exhaustive)
with wp_bot[0]:
    st.markdown("**Dormant applications** — no builds in 90 days")
    ninety_ago = now_utc - timedelta(days=90)

    inv_query = (
        {"bool": {"filter": scope_filters_inv()}}
        if scope_filters_inv() else {"match_all": {}}
    )
    inv_apps_90 = set(composite_terms(IDX["inventory"], "application.keyword", inv_query).keys())

    act_query = {
        "bool": {
            "filter": [range_filter("startdate", ninety_ago, now_utc)] + scope_filters()
        }
    }
    active_apps_90 = set(composite_terms(IDX["builds"], "application", act_query).keys())

    dormant_apps = sorted(inv_apps_90 - active_apps_90)
    if dormant_apps:
        st.dataframe(
            pd.DataFrame({"Application": dormant_apps[:50]}),
            use_container_width=True, hide_index=True, height=260,
        )
        st.caption(
            f"Found **{len(dormant_apps):,}** dormant. Candidates for archival."
        )
    else:
        inline_note("No dormant applications detected.", "success")

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
                "Who":    _s.get("application") or _s.get("project", ""),
                "Detail": f'{_s.get("environment","?")} · v{_s.get("codeversion","")} [{_s.get("project","")}]',
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
                "Who":    _s.get("project", _s.get("repository", "")),  # commits use project (parent)
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
            '<th style="padding:6px 4px;color:#64748b;font-size:0.75rem;font-weight:600">APPLICATION / PROJECT</th>'
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
* **Platform health** — `active / inventory`, where *active* is `cardinality(application)`
  on `ef-cicd-builds` within the window.
* **Application health score** — `build_success − (open_jira × 1.5) − (pending_req × 3)`,
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
