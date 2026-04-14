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

# Bright vivid palette — high contrast on white
C_SUCCESS = "#059669"
C_DANGER  = "#dc2626"
C_WARN    = "#d97706"
C_INFO    = "#2563eb"
C_ACCENT  = "#4f46e5"
C_MUTED   = "#8890a4"

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

# Projects permanently excluded from all views (test/noise projects)
EXCLUDED_PROJECTS = ["MAIKA_RegTst"]
SVC_ACCOUNT = "azure_sql"


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
/* -------- CSS custom properties — Bright ops palette -------- */
:root {
    --cc-surface:   #ffffff;
    --cc-surface2:  #f7f8fb;
    --cc-border:    #e3e6ee;
    --cc-border-hi: #c7cce0;
    --cc-text:      #1a1d2e;
    --cc-text-dim:  #4a5068;
    --cc-text-mute: #8890a4;
    --cc-accent:    #4f46e5;
    --cc-accent-lt: #eef2ff;
    --cc-accent-bg: rgba(79,70,229,.06);
    --cc-teal:      #0d9488;
    --cc-teal-lt:   #ccfbf1;
    --cc-teal-bg:   rgba(13,148,136,.07);
    --cc-green:     #059669;
    --cc-green-lt:  #d1fae5;
    --cc-green-bg:  rgba(5,150,105,.07);
    --cc-red:       #dc2626;
    --cc-red-lt:    #fee2e2;
    --cc-red-bg:    rgba(220,38,38,.06);
    --cc-amber:     #d97706;
    --cc-amber-lt:  #fef3c7;
    --cc-amber-bg:  rgba(217,119,6,.06);
    --cc-blue:      #2563eb;
    --cc-blue-lt:   #dbeafe;
    --cc-blue-bg:   rgba(37,99,235,.06);
    --cc-mono:      'SF Mono', 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    --cc-sans:      system-ui, -apple-system, 'Segoe UI', sans-serif;
}

/* -------- Layout — page content only, no sidebar/header overrides -------- */
.main .block-container {
    padding-top: 1.4rem;
    padding-bottom: 3rem;
    max-width: 1680px;
}

/* -------- Command bar -------- */
.cmdbar-label {
    font-size: .68rem; letter-spacing: .10em;
    text-transform: uppercase; color: var(--cc-text-mute);
    font-weight: 600; margin-bottom: 4px;
}

/* -------- KPI cards — bright, vivid top accent stripe -------- */
.kpi {
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 12px;
    padding: 18px 22px;
    height: 100%;
    box-shadow: 0 1px 3px rgba(0,0,0,.04), 0 4px 14px rgba(0,0,0,.03);
    transition: all .2s cubic-bezier(.4,0,.2,1);
    position: relative;
    overflow: hidden;
}
.kpi::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, var(--cc-accent), var(--cc-teal));
    opacity: 0; transition: opacity .2s ease;
}
.kpi:hover {
    transform: translateY(-2px);
    border-color: var(--cc-accent);
    box-shadow: 0 4px 20px rgba(79,70,229,.12), 0 1px 3px rgba(0,0,0,.04);
}
.kpi:hover::before { opacity: 1; }
.kpi .label {
    font-size: .68rem; text-transform: uppercase; letter-spacing: .10em;
    color: var(--cc-text-mute); font-weight: 600;
    display: flex; align-items: center; gap: 6px;
}
.kpi .value {
    font-size: 2.05rem; font-weight: 700; line-height: 1.1; margin-top: 6px;
    color: var(--cc-text) !important;
    font-variant-numeric: tabular-nums;
    font-family: var(--cc-mono);
}
.kpi .delta { font-size: .80rem; margin-top: 6px; font-weight: 500; }
.kpi .delta.up   { color: var(--cc-green) !important; }
.kpi .delta.dn   { color: var(--cc-red) !important; }
.kpi .delta.flat { color: var(--cc-text-mute) !important; }
.kpi .delta .arrow { display: inline-block; margin-right: 3px; }

/* -------- Section headers — colored left accent -------- */
.section {
    margin-top: 34px; margin-bottom: 10px;
    display: flex; align-items: center; justify-content: space-between;
    padding-bottom: 10px;
    border-bottom: 2px solid var(--cc-border);
    position: relative;
}
.section::after {
    content: ''; position: absolute; bottom: -2px; left: 0; width: 48px; height: 2px;
    background: var(--cc-accent);
}
.section .title-wrap { display: flex; align-items: center; gap: 12px; }
.section h2 {
    margin: 0; font-size: 1.15rem; font-weight: 700;
    color: var(--cc-text) !important;
    letter-spacing: -0.01em;
}
.section .badge {
    font-size: .66rem; letter-spacing: .10em; text-transform: uppercase;
    padding: 3px 10px; border-radius: 6px;
    background: var(--cc-accent-lt);
    color: var(--cc-accent); font-weight: 700;
    border: 1px solid rgba(79,70,229,.18);
}
.section .hint { font-size: .78rem; color: var(--cc-text-mute); }

/* -------- Alert ribbon -------- */
.alert {
    padding: 10px 14px; border-radius: 10px; margin-bottom: 7px;
    border-left: 4px solid var(--cc-amber);
    background: var(--cc-amber-lt);
    font-size: .88rem;
    display: flex; align-items: center; gap: 12px;
    color: var(--cc-text);
    box-shadow: 0 1px 3px rgba(0,0,0,.05);
}
.alert .icon {
    width: 28px; height: 28px; border-radius: 7px;
    display: flex; align-items: center; justify-content: center;
    font-weight: 800; font-size: .82rem; flex-shrink: 0;
    background: var(--cc-amber) !important; color: #fff !important;
}
/* danger */
.alert.danger  { border-left-color: var(--cc-red) !important; background: var(--cc-red-lt) !important; }
.alert.danger .icon { background: var(--cc-red) !important; color: #fff !important; }
.alert.danger b  { color: #991b1b !important; }
/* warning */
.alert.warning { border-left-color: var(--cc-amber) !important; background: var(--cc-amber-lt) !important; }
.alert.warning .icon { background: var(--cc-amber) !important; color: #fff !important; }
.alert.warning b { color: #92400e !important; }
/* info */
.alert.info    { border-left-color: var(--cc-blue) !important; background: var(--cc-blue-lt) !important; }
.alert.info .icon { background: var(--cc-blue) !important; color: #fff !important; }
.alert.info b  { color: #1e40af !important; }
/* success */
.alert.success { border-left-color: var(--cc-green) !important; background: var(--cc-green-lt) !important; }
.alert.success .icon { background: var(--cc-green) !important; color: #fff !important; }
.alert.success b { color: #065f46 !important; }
/* shared */
.alert b   { font-weight: 700; }
.alert .sub { font-size: .82rem; color: var(--cc-text-dim) !important; margin-left: 4px; }

/* -------- Insight / learn panel -------- */
.learn {
    background: var(--cc-accent-lt);
    border-left: 3px solid var(--cc-accent);
    border-radius: 10px;
    padding: 11px 16px;
    font-size: .86rem; color: var(--cc-text-dim);
    margin: 4px 0 18px 0;
}
.learn b { color: var(--cc-text); }

/* -------- Funnel visual -------- */
.funnel-wrap {
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 12px;
    padding: 20px 24px;
    height: 100%;
    box-shadow: 0 1px 3px rgba(0,0,0,.04);
}
.funnel-stage {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 10px 0;
    border-bottom: 1px dashed var(--cc-border);
}
.funnel-stage:last-child { border-bottom: none; }
.funnel-stage .name { color: var(--cc-text-dim); font-size: .90rem; font-weight: 500; }
.funnel-stage .value {
    font-size: 1.35rem; font-weight: 700; color: var(--cc-text);
    font-variant-numeric: tabular-nums;
    font-family: var(--cc-mono);
}
.funnel-stage .conv { font-size: .75rem; color: var(--cc-text-mute); margin-left: 8px; }
.funnel-bar {
    height: 6px; border-radius: 3px; margin-top: 6px;
    background: linear-gradient(90deg, var(--cc-accent), var(--cc-teal));
    opacity: 0.7;
}

/* -------- Pills -------- */
.pill {
    display: inline-block;
    background: var(--cc-surface2);
    color: var(--cc-text-dim);
    font-size: .70rem;
    padding: 3px 10px;
    border-radius: 999px;
    margin-right: 6px;
    font-weight: 500;
    border: 1px solid var(--cc-border);
}
.pill.green { background: var(--cc-green-lt) !important;  color: var(--cc-green) !important; border-color: rgba(5,150,105,.25) !important; }
.pill.red   { background: var(--cc-red-lt) !important;    color: var(--cc-red) !important;   border-color: rgba(220,38,38,.25) !important; }
.pill.amber { background: var(--cc-amber-lt) !important;  color: var(--cc-amber) !important; border-color: rgba(217,119,6,.25) !important; }
.pill.blue  { background: var(--cc-blue-lt) !important;   color: var(--cc-blue) !important;  border-color: rgba(37,99,235,.25) !important; }

/* -------- Streamlit widget label overrides -------- */
div[data-testid="stSelectbox"] label,
div[data-testid="stTextInput"] label,
div[data-testid="stDateInput"] label {
    font-size: .70rem !important;
    text-transform: uppercase;
    letter-spacing: .10em;
    color: var(--cc-text-mute) !important;
    font-weight: 600 !important;
}
.stDataFrame { border-radius: 10px; overflow: hidden; }

/* -------- Hide Streamlit footer -------- */
footer { visibility: hidden; }

/* =============================================================== *
 *  COLOR FIDELITY OVERRIDES                                        *
 * =============================================================== */

/* KPI deltas */
.kpi .delta.up   { color: var(--cc-green) !important; }
.kpi .delta.dn   { color: var(--cc-red) !important; }
.kpi .delta.flat { color: var(--cc-text-mute) !important; }
.kpi .value      { color: var(--cc-text) !important; }
.kpi .label      { color: var(--cc-text-mute) !important; }

/* Alert ribbon */
.alert          { color: var(--cc-text) !important; }
.alert b        { font-weight: 700 !important; }
.alert .sub     { color: var(--cc-text-dim) !important; }

.alert.success       { border-left-color: var(--cc-green) !important; background: var(--cc-green-lt) !important; }
.alert.success .icon { background: var(--cc-green) !important; color: #fff !important; }
.alert.success b     { color: #065f46 !important; }

.alert.danger        { border-left-color: var(--cc-red) !important; background: var(--cc-red-lt) !important; }
.alert.danger .icon  { background: var(--cc-red) !important; color: #fff !important; }
.alert.danger b      { color: #991b1b !important; }

.alert.warning       { border-left-color: var(--cc-amber) !important; background: var(--cc-amber-lt) !important; }
.alert.warning .icon { background: var(--cc-amber) !important; color: #fff !important; }
.alert.warning b     { color: #92400e !important; }

.alert.info          { border-left-color: var(--cc-blue) !important; background: var(--cc-blue-lt) !important; }
.alert.info .icon    { background: var(--cc-blue) !important; color: #fff !important; }
.alert.info b        { color: #1e40af !important; }

/* Pills */
.pill.green { background: var(--cc-green-lt) !important;  color: var(--cc-green) !important; border-color: rgba(5,150,105,.25) !important; }
.pill.red   { background: var(--cc-red-lt) !important;    color: var(--cc-red) !important;   border-color: rgba(220,38,38,.25) !important; }
.pill.amber { background: var(--cc-amber-lt) !important;  color: var(--cc-amber) !important; border-color: rgba(217,119,6,.25) !important; }
.pill.blue  { background: var(--cc-blue-lt) !important;   color: var(--cc-blue) !important;  border-color: rgba(37,99,235,.25) !important; }

/* Streamlit native alerts */
div[data-testid="stAlert"][data-baseweb="notification"] { border-radius: 10px !important; }
div[data-testid="stAlertContentSuccess"],
div[data-baseweb="notification"][kind="positive"] {
    background: var(--cc-green-lt) !important;
    border: 1px solid rgba(5,150,105,.25) !important;
    color: #065f46 !important;
}
div[data-testid="stAlertContentInfo"],
div[data-baseweb="notification"][kind="info"] {
    background: var(--cc-blue-lt) !important;
    border: 1px solid rgba(37,99,235,.25) !important;
    color: #1e40af !important;
}
div[data-testid="stAlertContentWarning"],
div[data-baseweb="notification"][kind="warning"] {
    background: var(--cc-amber-lt) !important;
    border: 1px solid rgba(217,119,6,.25) !important;
    color: #92400e !important;
}
div[data-testid="stAlertContentError"],
div[data-baseweb="notification"][kind="negative"] {
    background: var(--cc-red-lt) !important;
    border: 1px solid rgba(220,38,38,.25) !important;
    color: #991b1b !important;
}

/* Popover trigger buttons */
div[data-testid="stPopover"] button {
    background: var(--cc-accent-lt) !important;
    border: 1px solid rgba(79,70,229,.18) !important;
    color: var(--cc-accent) !important;
    font-weight: 600 !important;
}
div[data-testid="stPopover"] button:hover {
    background: rgba(79,70,229,.12) !important;
    border-color: var(--cc-accent) !important;
}

/* -------- Section nav chip strip -------- */
.navchips {
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
    padding: 12px 0 14px;
    margin: 6px 0 10px;
    border-top: 1px solid var(--cc-border);
    border-bottom: 1px solid var(--cc-border);
}
.navchips .navlbl {
    font-size: .62rem; text-transform: uppercase; letter-spacing: .12em;
    color: var(--cc-text-mute); font-weight: 700; margin-right: 4px;
}
.navchips a {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 13px;
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 999px;
    font-size: .76rem; font-weight: 600;
    color: var(--cc-text-dim) !important;
    text-decoration: none !important;
    transition: all .14s ease;
}
.navchips a:hover {
    background: var(--cc-accent-lt);
    border-color: var(--cc-accent);
    color: var(--cc-accent) !important;
    transform: translateY(-1px);
}
.navchips a .num {
    background: var(--cc-surface2); color: var(--cc-text-dim);
    font-size: .68rem; padding: 0 6px; border-radius: 999px;
    font-weight: 700;
}
.navchips a.crit { background: var(--cc-red-lt); border-color: rgba(220,38,38,.35); color: var(--cc-red) !important; }
.navchips a.crit .num { background: var(--cc-red); color: #fff; }
.navchips a.warn { background: var(--cc-amber-lt); border-color: rgba(217,119,6,.35); color: var(--cc-amber) !important; }
.navchips a.warn .num { background: var(--cc-amber); color: #fff; }

/* -------- Anchor scroll offset -------- */
.anchor { display: block; position: relative; top: -12px; visibility: hidden; }

/* -------- Pulse animation for status dot -------- */
@keyframes cc-pulse {
    0%, 100% { box-shadow: 0 0 4px var(--cc-green); }
    50%      { box-shadow: 0 0 10px var(--cc-green), 0 0 20px rgba(5,150,105,0.25); }
}

/* -------- HUD: Health ring (SVG-based circular progress) -------- */
.hud-ring {
    display: flex; align-items: center; gap: 16px;
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 14px;
    padding: 14px 18px;
    box-shadow: 0 1px 4px rgba(0,0,0,.04);
}
.hud-ring svg { flex-shrink: 0; }
.hud-ring .score-value {
    font-size: 1.7rem; font-weight: 800; font-family: var(--cc-mono);
    color: var(--cc-text);
}
.hud-ring .score-label {
    font-size: .70rem; text-transform: uppercase; letter-spacing: .08em;
    color: var(--cc-text-mute); font-weight: 600; margin-top: 2px;
}

/* -------- HUD: Stat bar (mini KPI row inside a card) -------- */
.hud-stat {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid var(--cc-border);
}
.hud-stat:last-child { border-bottom: none; }
.hud-stat .stat-icon {
    width: 24px; height: 24px; border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    font-size: .72rem; font-weight: 700;
}
.hud-stat .stat-label {
    flex: 1; font-size: .80rem; color: var(--cc-text-dim);
}
.hud-stat .stat-val {
    font-size: .95rem; font-weight: 700; color: var(--cc-text);
    font-family: var(--cc-mono); font-variant-numeric: tabular-nums;
}

/* -------- HUD: Streak counter -------- */
.hud-streak {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px;
    border-radius: 999px;
    font-size: .78rem; font-weight: 700;
    background: var(--cc-green-lt);
    color: var(--cc-green);
    border: 1px solid rgba(5,150,105,.2);
}
.hud-streak.warn { background: var(--cc-amber-lt); color: var(--cc-amber); border-color: rgba(217,119,6,.2); }
.hud-streak.bad  { background: var(--cc-red-lt);   color: var(--cc-red);   border-color: rgba(220,38,38,.2); }

/* -------- HUD: XP / progress bar -------- */
.hud-xp {
    height: 8px; border-radius: 4px; overflow: hidden;
    background: var(--cc-surface2);
}
.hud-xp .fill {
    height: 100%; border-radius: 4px;
    transition: width .4s ease;
}

/* -------- HUD: Role mission card -------- */
.hud-mission {
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 12px;
    padding: 16px 18px;
    box-shadow: 0 1px 4px rgba(0,0,0,.04);
}
.hud-mission .mission-title {
    font-size: .72rem; text-transform: uppercase; letter-spacing: .08em;
    color: var(--cc-text-mute); font-weight: 700; margin-bottom: 10px;
}

/* -------- HUD: Quest / action item -------- */
.hud-quest {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 12px; margin-bottom: 6px;
    border-radius: 8px;
    background: var(--cc-surface2);
    border-left: 3px solid var(--cc-border);
    transition: all .15s ease;
}
.hud-quest:hover { border-left-color: var(--cc-accent); background: var(--cc-accent-lt); }
.hud-quest .quest-prio {
    width: 22px; height: 22px; border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    font-size: .65rem; font-weight: 800; color: #fff;
}
.hud-quest .quest-text { flex: 1; font-size: .82rem; color: var(--cc-text-dim); }
.hud-quest .quest-text b { color: var(--cc-text); }
.hud-quest .quest-val {
    font-size: .85rem; font-weight: 700; font-family: var(--cc-mono);
    color: var(--cc-text);
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


# Painless script for composite artifact identity (company/project/application/codeversion)
_ARTIFACT_SCRIPT = (
    "def _f(f) { return doc.containsKey(f) && doc[f].size() > 0 ? doc[f].value : '' } "
    "return _f('company.keyword') + '/' + _f('project') + '/' + _f('application') + '/' + _f('codeversion')"
)


def composite_unique_versions(
    index: str,
    field: str,
    query: dict,
    page_size: int = COMPOSITE_PAGE,
) -> dict[str, int]:
    """Like composite_terms but counts distinct artifacts per key.

    An artifact = unique company/project/application/codeversion combination.
    Returns ``{key: unique_artifact_count}`` — eliminates re-deployments /
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
                        "uv": {"cardinality": {
                            "script": {"source": _ARTIFACT_SCRIPT, "lang": "painless"},
                        }}
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

    # ── 6. Try common non-ISO strptime patterns ───────────────────────────────
    for _fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z",   # with ms + tz offset
        "%Y-%m-%dT%H:%M:%S%z",       # no ms + tz offset
        "%Y-%m-%d %H:%M:%S",         # space-separated naive
        "%d/%m/%Y %H:%M:%S",         # DD/MM/YYYY
        "%m/%d/%Y %H:%M:%S",         # MM/DD/YYYY
        "%d-%b-%Y %H:%M:%S",         # DD-Mon-YYYY
    ):
        try:
            from datetime import datetime as _dt
            return _to_utc(pd.Timestamp(_dt.strptime(s, _fmt)))
        except Exception:
            pass

    # ── 7. dateutil catch-all — handles almost any human-readable format ──────
    try:
        from dateutil import parser as _dup  # type: ignore[import]
        return _to_utc(pd.Timestamp(_dup.parse(s)))
    except Exception:
        pass

    return None


def fmt_dt(value: Any, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Parse and format a date value; returns "" on failure."""
    ts = parse_dt(value)
    return ts.strftime(fmt) if ts is not None else ""


# Date field candidates per index family — ES source field names can vary
_DATE_CANDIDATES = {
    "build":   ["startdate", "StartDate", "start_date", "created", "timestamp", "@timestamp"],
    "deploy":  ["startdate", "StartDate", "start_date", "created", "timestamp", "@timestamp"],
    "release": ["releasedate", "ReleaseDate", "release_date", "created", "timestamp", "@timestamp"],
    "commit":  ["commitdate", "CommitDate", "commit_date", "created", "timestamp", "@timestamp"],
    "request": ["RequestDate", "requestdate", "request_date", "Created", "CreatedDate", "timestamp", "@timestamp"],
}


def _pick_date(source: dict, family: str) -> Any:
    """Return the first non-None date value from ``source`` for the given index family."""
    for fname in _DATE_CANDIDATES.get(family, ["timestamp", "@timestamp"]):
        v = source.get(fname)
        if v is not None:
            return v
    return None


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

ROLES = ["Admin", "Developer", "QC", "Operator"]
ROLE_ICONS = {"Admin": "🛡", "Developer": "⌨", "QC": "🔬", "Operator": "🚀"}
ROLE_COLORS = {"Admin": "#4f46e5", "Developer": "#2563eb", "QC": "#7c3aed", "Operator": "#059669"}
# Map role → inventory team field(s) used to filter projects
ROLE_TEAM_FIELDS: dict[str, list[str]] = {
    "Admin":     [],                                          # sees everything
    "Developer": ["dev_team.keyword"],
    "QC":        ["qc_team.keyword"],
    "Operator":  ["uat_team.keyword", "prd_team.keyword"],    # usually both
}


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


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_teams_for_role(role: str) -> list[str]:
    """Return sorted unique team names for the given role from inventory."""
    fields = ROLE_TEAM_FIELDS.get(role, [])
    if not fields:
        return []
    teams: set[str] = set()
    for f in fields:
        try:
            teams.update(composite_terms(IDX["inventory"], f, {"match_all": {}}).keys())
        except Exception:
            pass
    return sorted(t for t in teams if t)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_team_applications(role: str, team: str) -> list[str]:
    """Return list of application names assigned to this team for this role."""
    fields = ROLE_TEAM_FIELDS.get(role, [])
    if not fields or not team:
        return []
    # OR across the role's team fields (Operator has uat_team + prd_team)
    should_clauses = [{"term": {f: team}} for f in fields]
    query = {"bool": {"should": should_clauses, "minimum_should_match": 1}}
    try:
        return sorted(composite_terms(IDX["inventory"], "application.keyword", query).keys())
    except Exception:
        return []


_all_companies, _all_projects = _load_inventory_choices()
_ALL = "— All —"

# ── Detect role & teams from session state (set by the parent multipage app) ─
_session_roles: list[str] = st.session_state.get("roles") or []
_session_teams: list[str] = st.session_state.get("teams") or []

# Map session roles to our 4 canonical roles (case-insensitive partial match)
_ROLE_ALIASES: dict[str, str] = {
    "admin": "Admin", "devops": "Admin", "administrator": "Admin",
    "developer": "Developer", "dev": "Developer",
    "qc": "QC", "quality": "QC", "quality-control": "QC", "quality_control": "QC",
    "operator": "Operator", "ops": "Operator", "operations": "Operator",
}
_detected_roles: list[str] = []
for _sr in _session_roles:
    _norm = _sr.strip().lower().replace(" ", "_").replace("-", "_")
    if _norm in _ROLE_ALIASES:
        _detected_roles.append(_ROLE_ALIASES[_norm])
    elif _sr in ROLES:
        _detected_roles.append(_sr)
# Deduplicate while preserving order
_detected_roles = list(dict.fromkeys(_detected_roles)) or ["Admin"]  # fallback


# ── Row 1: role view + team + company/project + controls ─────────────────────
_cb1 = st.columns([1.2, 1.2, 1.5, 1.5, 0.6, 0.6, 0.6])

with _cb1[0]:
    # If user has multiple roles, let them pick; otherwise show the one they have
    if len(_detected_roles) > 1:
        role_pick = st.selectbox("View as", _detected_roles, index=0, key="role_pick",
                                 help="Switch between your assigned role views")
    else:
        role_pick = _detected_roles[0]
        st.markdown(f'<div style="padding-top:6px;font-size:.68rem;text-transform:uppercase;'
                    f'letter-spacing:.10em;color:var(--cc-text-mute);font-weight:600">Role</div>'
                    f'<div style="font-size:.90rem;font-weight:600;color:var(--cc-text)">'
                    f'{ROLE_ICONS[role_pick]} {role_pick}</div>', unsafe_allow_html=True)

with _cb1[1]:
    # Teams come from session state; for non-Admin roles we filter by team
    if _session_teams and role_pick != "Admin":
        if len(_session_teams) > 1:
            team_pick = st.selectbox("Team", _session_teams, index=0, key="team_pick")
        else:
            team_pick = _session_teams[0]
            st.markdown(f'<div style="padding-top:6px;font-size:.68rem;text-transform:uppercase;'
                        f'letter-spacing:.10em;color:var(--cc-text-mute);font-weight:600">Team</div>'
                        f'<div style="font-size:.90rem;font-weight:600;color:var(--cc-text)">'
                        f'{team_pick}</div>', unsafe_allow_html=True)
        team_filter = team_pick
    elif role_pick == "Admin" and _session_teams:
        # Admin can optionally scope to a team
        team_pick = st.selectbox("Team", [_ALL] + _session_teams, index=0, key="team_pick",
                                 help="Admin: optionally filter to a specific team")
        team_filter = "" if team_pick == _ALL else team_pick
    else:
        team_filter = ""
        st.markdown('<div style="padding-top:6px;font-size:.68rem;text-transform:uppercase;'
                    'letter-spacing:.10em;color:var(--cc-text-mute);font-weight:600">Team</div>'
                    '<div style="font-size:.90rem;color:var(--cc-text-mute)">All teams</div>',
                    unsafe_allow_html=True)

# Resolve team → application list for scope filtering
if team_filter:
    # When Admin scopes to a team, query all team fields (any role assignment counts)
    _team_role_for_query = role_pick if role_pick != "Admin" else "Admin"
    _team_apps: list[str] = _load_team_applications(role_pick, team_filter) if role_pick != "Admin" else []
    if role_pick == "Admin" and team_filter:
        # Admin + team selected: find apps where this team appears in ANY team field
        _admin_team_apps: set[str] = set()
        for _r in ["Developer", "QC", "Operator"]:
            _admin_team_apps.update(_load_team_applications(_r, team_filter))
        _team_apps = sorted(_admin_team_apps)
else:
    _team_apps = []  # no team-based restriction

with _cb1[2]:
    _company_options = [_ALL] + _all_companies
    company_pick = st.selectbox("Company", _company_options, index=0, key="company_pick",
                                help=f"{len(_all_companies)} companies in inventory")
    company_filter = "" if company_pick == _ALL else company_pick

with _cb1[3]:
    _proj_options = [_ALL] + _all_projects
    project_pick = st.selectbox("Project", _proj_options, index=0, key="project_pick",
                                help=f"{len(_all_projects)} projects in inventory")
    project_filter = "" if project_pick == _ALL else project_pick

with _cb1[4]:
    auto_refresh = st.toggle("Auto", value=False, help="Auto-refresh every 60s", key="auto_refresh")

with _cb1[5]:
    exclude_svc = st.toggle("Excl. svc", value=True,
                            help="Exclude service account 'azure_sql' from all commit displays",
                            key="exclude_svc")

with _cb1[6]:
    if st.button("↻", help="Clear cache & reload", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ── Role-scoped visibility flags — relied on by KPI row + section skips ────
_ROLE_SHOWS_JIRA: dict[str, bool] = {
    "Admin": True, "Developer": True, "QC": True, "Operator": False,
}
_ROLE_SHOWS_BUILDS: dict[str, bool] = {
    "Admin": True, "Developer": True, "QC": False, "Operator": False,
}
_ROLE_EVENT_TYPES: dict[str, list[str]] = {
    "Admin":     ["Builds", "Deployments", "Releases", "Requests", "Commits"],
    "Developer": ["Builds", "Commits", "Requests"],
    "QC":        ["Deployments", "Releases", "Requests"],
    "Operator":  ["Deployments", "Releases", "Requests"],
}
_ROLE_ENVS: dict[str, list[str]] = {
    "Admin":     ["prd", "uat", "qc", "dev"],
    "Developer": ["dev", "qc", "uat", "prd"],
    "QC":        ["qc"],
    "Operator":  ["uat", "prd"],
}
_ROLE_APPROVAL_STAGES: dict[str, list[str]] = {
    "Admin":     [],
    "Developer": ["build"],
    "QC":        ["qc", "request_deploy_qc", "request_promote"],
    "Operator":  ["uat", "prd", "request_deploy_uat", "request_deploy_prd"],
}

# ── Role HUD banner ──────────────────────────────────────────────────────────
_role_clr = ROLE_COLORS[role_pick]
_role_icon = ROLE_ICONS[role_pick]
_team_label = f" · {team_filter}" if team_filter else ""
_apps_label = f"{len(_team_apps)} applications" if _team_apps else "all applications"

_banner_cols = st.columns([5, 1.5]) if role_pick == "Admin" else [st.container(), None]
with _banner_cols[0]:
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:10px;padding:8px 16px;margin:2px 0 6px;'
        f'border-radius:10px;border:1px solid {_role_clr}20;'
        f'background:linear-gradient(90deg,{_role_clr}08,transparent 60%);">'
        f'<span style="font-size:1.3rem">{_role_icon}</span>'
        f'<span style="font-size:.95rem;font-weight:700;color:{_role_clr}">{role_pick}</span>'
        f'<span style="font-size:.82rem;color:var(--cc-text-dim)">{_team_label}</span>'
        f'<span style="margin-left:auto;font-size:.72rem;color:var(--cc-text-mute)">'
        f'{_apps_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
if role_pick == "Admin" and _banner_cols[1] is not None:
    with _banner_cols[1]:
        _admin_view_options = ["Admin"] + [r for r in ROLES if r != "Admin"]
        st.selectbox("View as role", _admin_view_options, index=0, key="admin_role_view",
                     help="Preview dashboard sections as another role")

# ── Row 2: time window segmented button group ────────────────────────────────
_TW_LABELS = list(PRESETS.keys())
_preset_default_idx = _TW_LABELS.index("7d")

# Use a radio rendered as segmented buttons via CSS
st.markdown("""
<style>
div[data-testid="stRadio"] > div { flex-wrap: wrap; gap: 4px; }
div[data-testid="stRadio"] label {
    background: var(--cc-surface2) !important;
    border: 1px solid var(--cc-border) !important;
    border-radius: 8px !important;
    padding: 4px 12px !important;
    font-size: 0.78rem !important;
    font-weight: 600 !important;
    color: var(--cc-text-dim) !important;
    cursor: pointer !important;
    transition: all .12s ease;
}
div[data-testid="stRadio"] label:has(input:checked) {
    background: var(--cc-accent-lt) !important;
    border-color: var(--cc-accent) !important;
    color: var(--cc-accent) !important;
}
div[data-testid="stRadio"] label:hover {
    border-color: var(--cc-border-hi) !important;
    color: var(--cc-text) !important;
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
    """Base filters for operational indices (builds, deployments, commits, etc.)."""
    fs: list[dict] = []
    if company_filter:
        fs.append({"term": {"company.keyword": company_filter}})
    if project_filter:
        fs.append({"term": {"project": project_filter}})
    # Team-based application restriction
    if _team_apps:
        fs.append({"terms": {"application": _team_apps}})
    # Always exclude noise/test projects
    fs.append({"bool": {"must_not": [{"terms": {"project": EXCLUDED_PROJECTS}}]}})
    return fs


def scope_filters_inv() -> list[dict]:
    """Filters for the inventory index (uses .keyword sub-fields)."""
    fs: list[dict] = []
    if company_filter:
        fs.append({"term": {"company.keyword": company_filter}})
    if project_filter:
        fs.append({"term": {"project.keyword": project_filter}})
    # Team-based application restriction
    if _team_apps:
        fs.append({"terms": {"application.keyword": _team_apps}})
    # Always exclude noise/test projects
    fs.append({"bool": {"must_not": [{"terms": {"project.keyword": EXCLUDED_PROJECTS}}]}})
    return fs


def commit_scope_filters() -> list[dict]:
    """scope_filters() + optional service-account exclusion for commit queries."""
    fs = list(scope_filters())
    if exclude_svc:
        fs.append({"bool": {"must_not": [{"term": {"authorname": SVC_ACCOUNT}}]}})
    return fs


def build_scope_filters() -> list[dict]:
    """scope_filters() + release-branch only (production pipeline builds)."""
    return scope_filters() + [{"term": {"branch": "release"}}]


def deploy_scope_filters() -> list[dict]:
    """scope_filters() + exclude pre-release/test versions (codeversion 0.*)."""
    return scope_filters() + [{"bool": {"must_not": [{"prefix": {"codeversion": "0."}}]}}]


def idx_scope(index: str) -> list[dict]:
    """Return the appropriate scope filters for the given index."""
    if index == IDX["builds"]:
        return build_scope_filters()
    if index == IDX["deployments"]:
        return deploy_scope_filters()
    if index == IDX["commits"]:
        return commit_scope_filters()
    return scope_filters()


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
    # use_commit_scope overrides auto-detection (for backward compat)
    base = commit_scope_filters() if use_commit_scope else idx_scope(index)
    filters = [range_filter(field, s, e)] + base + (extra or [])
    return es_count(index, {"query": {"bool": {"filter": filters}}})


def unique_versions_in_range(
    index: str, date_field: str, s: datetime, e: datetime,
    extra: list[dict] | None = None,
    scope: list[dict] | None = None,
) -> int:
    """Count distinct artifacts (company/project/application/codeversion) in a window."""
    base = scope if scope is not None else idx_scope(index)
    filters = [range_filter(date_field, s, e)] + base + (extra or [])
    res = es_search(index, {
        "query": {"bool": {"filter": filters}},
        "aggs": {"uv": {"cardinality": {
            "script": {"source": _ARTIFACT_SCRIPT, "lang": "painless"},
        }}},
    }, size=0)
    return int(res.get("aggregations", {}).get("uv", {}).get("value", 0) or 0)


# -- Builds ------------------------------------------------------------------
builds_now  = count_with_range(IDX["builds"], "startdate", start_dt, end_dt)
builds_prev = count_with_range(IDX["builds"], "startdate", prior_start, prior_end)
builds_fail = count_with_range(
    IDX["builds"], "startdate", start_dt, end_dt,
    extra=[{"terms": {"status": FAILED_STATUSES}}],
)
success_rate = ((builds_now - builds_fail) / builds_now * 100) if builds_now else 0.0

# -- Artifacts (unique code_versions) ----------------------------------------
# Artifacts are the real unit of value — a code_version built, deployed, released.
# Raw event counts inflate with retries; artifact counts show true throughput.
art_built      = unique_versions_in_range(IDX["builds"], "startdate", start_dt, end_dt,
                                          scope=build_scope_filters())
art_built_ok   = unique_versions_in_range(IDX["builds"], "startdate", start_dt, end_dt,
                                          extra=[{"bool": {"must_not": [{"terms": {"status": FAILED_STATUSES}}]}}],
                                          scope=build_scope_filters())
art_deployed   = unique_versions_in_range(IDX["deployments"], "startdate", start_dt, end_dt,
                                          scope=deploy_scope_filters())
art_dep_dev    = unique_versions_in_range(IDX["deployments"], "startdate", start_dt, end_dt,
                                          extra=[{"term": {"environment": "dev"}}],
                                          scope=deploy_scope_filters())
art_dep_qc     = unique_versions_in_range(IDX["deployments"], "startdate", start_dt, end_dt,
                                          extra=[{"term": {"environment": "qc"}}],
                                          scope=deploy_scope_filters())
art_released   = unique_versions_in_range(IDX["releases"], "releasedate", start_dt, end_dt,
                                          scope=scope_filters())
art_dep_uat    = unique_versions_in_range(IDX["deployments"], "startdate", start_dt, end_dt,
                                          extra=[{"term": {"environment": "uat"}}],
                                          scope=deploy_scope_filters())
art_dep_prd    = unique_versions_in_range(IDX["deployments"], "startdate", start_dt, end_dt,
                                          extra=[{"term": {"environment": "prd"}}],
                                          scope=deploy_scope_filters())
# Prior-window artifact counts for delta comparison
art_built_prev = unique_versions_in_range(IDX["builds"], "startdate", prior_start, prior_end,
                                          scope=build_scope_filters())
art_dep_prd_prev = unique_versions_in_range(IDX["deployments"], "startdate", prior_start, prior_end,
                                            extra=[{"term": {"environment": "prd"}}],
                                            scope=deploy_scope_filters())

# Artifact conversion rates
art_build_to_prd = (art_dep_prd / art_built * 100) if art_built else 0.0

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
# Unified function that merges ef-devops-requests + ef-cicd-approval (legacy).
# ef-devops-requests: Status, RequestDate, RequestNumber, RequestType, Requester, RequesterTeam, application
# ef-cicd-approval:   status (lower), RequestDate or Created or SubmittedDate, id/ApprovalId, Type

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_unified_pending(window_start_iso: str, window_end_iso: str) -> list[dict]:
    """Return normalised pending request dicts from both request indices.

    ef-cicd-approval uses a ``stage`` field to distinguish document types:
      - ``build``              → running build pipeline
      - ``<env>``              → running deployment on environment <env>
      - ``request_deploy_<env>`` → pending deployment request for <env>
      - ``request_promote``    → pending release request (QC management approval)

    Only ``request_deploy_*`` and ``request_promote`` are pending requests;
    ``build`` and bare env names are running pipelines (not actionable queue items).
    """
    _w0 = datetime.fromisoformat(window_start_iso)
    _w1 = datetime.fromisoformat(window_end_iso)
    _results: list[dict] = []

    # ── ef-devops-requests ──────────────────────────────────────────────────
    _r1 = es_search(IDX["requests"], {
        "query": {"bool": {"filter": [
            range_filter("RequestDate", _w0, _w1),
            {"terms": {"Status": PENDING_STATUSES}},
        ]}},
        "sort": [{"RequestDate": "asc"}],
    }, size=200)
    for _h in _r1.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _results.append({
            "_idx":    "requests",
            "#":       _s.get("RequestNumber") or _s.get("id") or _h.get("_id"),
            "Type":    _s.get("RequestType") or _s.get("Type") or "—",
            "Stage":   "—",
            "Requester": _s.get("Requester") or _s.get("requestedBy") or "—",
            "Team":    _s.get("RequesterTeam") or "—",
            "Application": _s.get("application") or _s.get("project") or "—",
            "Date":    _s.get("RequestDate") or "",
            "Age (h)": None,  # filled below
        })

    # ── ef-cicd-approval — use the stage field ─────────────────────────────
    # Pending requests have stage matching request_deploy_* or request_promote.
    _r2 = es_search(IDX["approval"], {
        "query": {"bool": {"filter": [
            {"bool": {"should": [
                {"prefix": {"stage": "request_deploy_"}},
                {"term": {"stage": "request_promote"}},
            ], "minimum_should_match": 1}},
        ]}},
        "sort": [{"RequestDate": {"order": "asc", "unmapped_type": "date"}}],
    }, size=200)
    for _h in _r2.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _stage = _s.get("stage") or "—"
        # Derive a human-readable type from the stage
        if _stage == "request_promote":
            _type_label = "Release request"
        elif _stage.startswith("request_deploy_"):
            _env = _stage.replace("request_deploy_", "")
            _type_label = f"Deploy request ({_env})"
        else:
            _type_label = _s.get("ApprovalType") or _s.get("Type") or "approval"
        _date_val = (_s.get("RequestDate") or _s.get("Created")
                     or _s.get("CreatedDate") or _s.get("SubmittedDate") or "")
        _results.append({
            "_idx":    "approval",
            "#":       _s.get("ApprovalId") or _s.get("RequestNumber") or _s.get("id") or _h.get("_id"),
            "Type":    _type_label,
            "Stage":   _stage,
            "Requester": _s.get("RequestedBy") or _s.get("Requester") or _s.get("requestedBy") or "—",
            "Team":    _s.get("Team") or _s.get("RequesterTeam") or "—",
            "Application": _s.get("application") or _s.get("project") or "—",
            "Date":    _date_val,
            "Age (h)": None,
        })

    # Fill Age (h)
    _now_ref = datetime.now(timezone.utc)
    for _item in _results:
        _item["Age (h)"] = age_hours(_item["Date"], _now_ref) or 0

    return _results


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_running_pipelines() -> list[dict]:
    """Return currently running build/deployment pipelines from ef-cicd-approval.

    Running pipelines have ``stage`` = ``build`` (running build) or a bare
    environment name like ``dev``, ``qc``, ``uat``, ``prd`` (running deployment).
    """
    _results: list[dict] = []
    # stage values for running pipelines: "build" or any env name (not prefixed with "request_")
    _r = es_search(IDX["approval"], {
        "query": {"bool": {
            "filter": [{"exists": {"field": "stage"}}],
            "must_not": [
                {"prefix": {"stage": "request_"}},
            ],
        }},
        "sort": [{"RequestDate": {"order": "desc", "unmapped_type": "date"}}],
    }, size=100)
    for _h in _r.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _stage = _s.get("stage") or "—"
        if _stage == "build":
            _type_label = "Running build"
        else:
            _type_label = f"Running deploy ({_stage})"
        _results.append({
            "_idx":       "approval",
            "#":          _s.get("ApprovalId") or _s.get("id") or _h.get("_id"),
            "Type":       _type_label,
            "Stage":      _stage,
            "Application": _s.get("application") or _s.get("project") or "—",
            "Date":       _s.get("RequestDate") or _s.get("Created") or "",
        })
    return _results


def pending_unified_counts() -> dict[str, int]:
    """Return {application: pending_count} across both request indices."""
    _rows = _fetch_unified_pending(
        pending_window_start.isoformat(), now_utc.isoformat()
    )
    _counts: dict[str, int] = {}
    for _r in _rows:
        _a = _r.get("Application") or "—"
        _counts[_a] = _counts.get(_a, 0) + 1
    return _counts


reqs_now  = count_with_range(IDX["requests"], "RequestDate", start_dt, end_dt)
reqs_prev = count_with_range(IDX["requests"], "RequestDate", prior_start, prior_end)
_all_pending = _fetch_unified_pending(
    pending_window_start.isoformat(), now_utc.isoformat()
)
pending_now = len(_all_pending)

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
                "filter": [range_filter("startdate", start_dt, end_dt)] + build_scope_filters()
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


# Placeholder for alerts ribbon — actually filled by the ALERTS block further
# down, but rendered at THIS position in the page (above KPIs) so the most
# actionable items always sit at the top of the viewport.
_alerts_ph = st.container()


# =============================================================================
# HUD — Gamified team health panel
# =============================================================================

def _svg_ring(score: float, color: str, size: int = 64) -> str:
    """Return an SVG circular progress ring for the given score (0-100)."""
    r = (size - 6) // 2
    circ = 2 * 3.14159 * r
    offset = circ * (1 - score / 100)
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{size//2}" cy="{size//2}" r="{r}" fill="none" stroke="var(--cc-border)" stroke-width="5"/>'
        f'<circle cx="{size//2}" cy="{size//2}" r="{r}" fill="none" stroke="{color}" stroke-width="5"'
        f' stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.1f}"'
        f' transform="rotate(-90 {size//2} {size//2})"'
        f' style="transition:stroke-dashoffset .6s ease"/>'
        f'</svg>'
    )


# Compute team health score (composite: build success 30%, artifact throughput 25%, CFR 25%, queue 20%)
_health_build = min(success_rate, 100)
_health_throughput = min(art_build_to_prd * 2, 100) if art_built else 50  # 50% conv = 100 score
_health_cfr = max(0, 100 - cfr * 5)  # 0% CFR = 100, 20% CFR = 0
_health_queue = 100 if not pending_now else max(0, 100 - pending_now * 15)
_health_score = int(_health_build * 0.3 + _health_throughput * 0.25 + _health_cfr * 0.25 + _health_queue * 0.2)
_health_color = "var(--cc-green)" if _health_score >= 75 else ("var(--cc-amber)" if _health_score >= 50 else "var(--cc-red)")

# Streak: consecutive days with at least one successful build
_streak_days = 0
if builds_now > builds_fail:
    _streak_days = min(int(days_in_window), 30)  # simplified estimate

# Velocity: deployments per day
_velocity = deploy_freq_per_day

# Role-specific quest items
_quests: list[tuple[str, str, str, str]] = []  # (priority_color, icon, text, value)

if role_pick == "Admin":
    if builds_fail:
        _quests.append((C_DANGER, "!", f"<b>{builds_fail}</b> build failures to investigate", f"{builds_fail}"))
    if pending_now:
        _quests.append((C_WARN, "⏳", f"<b>{pending_now}</b> requests awaiting approval", f"{pending_now}"))
    _inv_total = count_with_range(IDX["inventory"], "startdate", start_dt, end_dt) if False else inv_count
    _quests.append((C_INFO, "📋", f"<b>{inv_count}</b> inventory pipelines, <b>{active_projs}</b> active", ""))
elif role_pick == "Developer":
    if builds_fail:
        _quests.append((C_DANGER, "🔴", f"<b>{builds_fail}</b> failed builds need fixing", f"{builds_fail}"))
    _quests.append((C_SUCCESS if success_rate >= 90 else C_WARN, "🏗",
                    f"Build success rate: <b>{success_rate:.0f}%</b>", f"{success_rate:.0f}%"))
    _quests.append((C_INFO, "📦", f"<b>{commits_now:,}</b> commits pushed this window", f"{commits_now:,}"))
elif role_pick == "QC":
    # QC cares about release requests (stage=request_promote)
    _qc_pending = sum(1 for r in _all_pending
                       if (r.get("Stage") or "") == "request_promote")
    if _qc_pending:
        _quests.append((C_WARN, "🔬", f"<b>{_qc_pending}</b> release requests awaiting QC approval", f"{_qc_pending}"))
    _quests.append((C_INFO, "📊", f"<b>{deploys_now:,}</b> total deployments this window", f"{deploys_now:,}"))
    _quests.append((C_SUCCESS, "✓", f"Change failure rate: <b>{cfr:.1f}%</b>",
                    f"{cfr:.1f}%"))
elif role_pick == "Operator":
    # Operator cares about deployment requests (stage=request_deploy_*)
    _ops_pending = sum(1 for r in _all_pending
                        if (r.get("Stage") or "").startswith("request_deploy_"))
    if _ops_pending:
        _quests.append((C_WARN, "🚀", f"<b>{_ops_pending}</b> deployment requests pending", f"{_ops_pending}"))
    _quests.append((C_SUCCESS, "🎯", f"<b>{prd_deploys}</b> prod deploys ({deploy_freq_per_day:.1f}/day)",
                    f"{prd_deploys}"))
    if prd_fail:
        _quests.append((C_DANGER, "💥", f"<b>{prd_fail}</b> prod deploy failures", f"{prd_fail}"))

# ── Render HUD ────────────────────────────────────────────────────────────────
_hud_c1, _hud_c2, _hud_c3 = st.columns([1, 1.5, 2])

with _hud_c1:
    _ring_svg = _svg_ring(_health_score, _health_color, 80)
    st.markdown(
        f'<div class="hud-ring">'
        f'  {_ring_svg}'
        f'  <div>'
        f'    <div class="score-value">{_health_score}</div>'
        f'    <div class="score-label">Team health</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

with _hud_c2:
    _streak_cls = "" if _streak_days >= 3 else ("warn" if _streak_days >= 1 else "bad")
    st.markdown(
        f'<div class="hud-mission">'
        f'  <div class="mission-title">Artifact flow &amp; velocity</div>'
        f'  <div class="hud-stat">'
        f'    <div class="stat-icon" style="background:var(--cc-accent-lt);color:var(--cc-accent)">📦</div>'
        f'    <div class="stat-label">Artifacts built</div>'
        f'    <div class="stat-val">{art_built}</div>'
        f'  </div>'
        f'  <div class="hud-stat">'
        f'    <div class="stat-icon" style="background:var(--cc-green-lt);color:var(--cc-green)">🚀</div>'
        f'    <div class="stat-label">Artifacts → PRD</div>'
        f'    <div class="stat-val">{art_dep_prd}</div>'
        f'  </div>'
        f'  <div class="hud-stat">'
        f'    <div class="stat-icon" style="background:var(--cc-blue-lt);color:var(--cc-blue)">⚡</div>'
        f'    <div class="stat-label">Deploy velocity</div>'
        f'    <div class="stat-val">{_velocity:.1f}/d</div>'
        f'  </div>'
        f'  <div class="hud-stat">'
        f'    <div class="stat-icon" style="background:{"var(--cc-green-lt)" if art_build_to_prd >= 50 else "var(--cc-amber-lt)"};'
        f'color:{"var(--cc-green)" if art_build_to_prd >= 50 else "var(--cc-amber)"}">📈</div>'
        f'    <div class="stat-label">Build→PRD rate</div>'
        f'    <div class="stat-val">{art_build_to_prd:.0f}%</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

with _hud_c3:
    _quest_html = ""
    for _q_color, _q_icon, _q_text, _q_val in _quests[:5]:
        _quest_html += (
            f'<div class="hud-quest">'
            f'  <div class="quest-prio" style="background:{_q_color}">{_q_icon}</div>'
            f'  <div class="quest-text">{_q_text}</div>'
            f'  <div class="quest-val">{_q_val}</div>'
            f'</div>'
        )
    if not _quest_html:
        _quest_html = '<div style="font-size:.85rem;color:var(--cc-text-mute);padding:12px 0">All clear — no action items</div>'
    st.markdown(
        f'<div class="hud-mission">'
        f'  <div class="mission-title">{ROLE_ICONS[role_pick]} {role_pick} missions</div>'
        f'  {_quest_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# =============================================================================
# KPIs  (2 rows × 4)
# =============================================================================

# Anchor for in-page navigation
st.markdown('<a class="anchor" id="sec-kpis"></a>', unsafe_allow_html=True)

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
        return '<span style="color:var(--cc-text-mute);">—</span>'
    if prev == 0:
        return f'<b style="color:var(--cc-text);">{cur:,}</b> <span style="color:var(--cc-green);">new</span>'
    diff = cur - prev
    pct  = diff / prev * 100
    direction = "var(--cc-green)" if diff > 0 else ("var(--cc-red)" if diff < 0 else "var(--cc-text-mute)")
    arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "→")
    sign = "+" if diff >= 0 else ""
    return (
        f'<b style="color:var(--cc-text);">{cur:,}</b> '
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
    ("Builds",            IDX["builds"],       "startdate",   None,   False),
    ("Build failures",    IDX["builds"],       "startdate",   [{"terms": {"status": FAILED_STATUSES}}], False),
    ("Deployments",       IDX["deployments"],  "startdate",   None,   False),
    ("Prod deployments",  IDX["deployments"],  "startdate",   [{"term": {"environment": "prd"}}], False),
    ("Prod failures",     IDX["deployments"],  "startdate",   [{"term": {"environment": "prd"}}, {"terms": {"status": FAILED_STATUSES}}], False),
    ("Commits",           IDX["commits"],      "commitdate",  None,   True),   # use commit scope
    ("Releases",          IDX["releases"],     "releasedate", None,   False),
    ("Requests",          IDX["requests"],     "RequestDate", None,   False),
]

# Row 1 — Artifact-centric headline (5 cards)
r1 = st.columns(5)
d, dn = fmt_delta(art_built, art_built_prev)
kpi_block(r1[0], "📦 Artifacts built", f"{art_built:,}", d, dn,
    "Unique code versions built in window")
kpi_block(r1[1], "🚀 Artifacts → PRD", f"{art_dep_prd:,}",
    f"{art_build_to_prd:.0f}% conversion" if art_built else "—",
    "up" if art_build_to_prd >= 50 else ("dn" if art_built and art_build_to_prd < 20 else "flat"),
    "Unique versions reaching production")
kpi_block(r1[2], "Deploy freq", f"{deploy_freq_per_day:.1f}/day",
    f"{prd_deploys:,} prod deploys",
    "up" if deploy_freq_per_day >= 1 else "flat", "DORA · prod deploys / day")
kpi_block(r1[3], "Change fail rate", f"{cfr:.1f}%",
    f"{prd_fail} / {prd_deploys} prod" if prd_deploys else "no prod deploys",
    "dn" if cfr > 15 else ("up" if prd_deploys else "flat"), "DORA · failed prod / prod deploys")
kpi_block(r1[4], "Build success", f"{success_rate:.1f}%",
    f"{builds_fail:,} failed of {builds_now:,}" if builds_fail else "all green",
    "dn" if builds_fail else "up", "(builds − failed) / builds")

# Row 2 — volume + health (5 cards) + trend popover, role-tailored
r2c = st.columns([1, 1, 1, 1, 1, 1.4])

# Slot 0: Builds (Developer/Admin) or PRD deploys (Operator) or QC deploys (QC)
if _ROLE_SHOWS_BUILDS.get(role_pick, True):
    d, dn = fmt_delta(builds_now, builds_prev)
    kpi_block(r2c[0], "Builds", f"{builds_now:,}", d, dn)
elif role_pick == "Operator":
    d, dn = fmt_delta(art_dep_prd, art_dep_prd_prev)
    kpi_block(r2c[0], "PRD artifacts", f"{art_dep_prd:,}", d, dn,
              "Unique artifacts deployed to PRD")
else:  # QC
    kpi_block(r2c[0], "QC artifacts", f"{art_dep_qc:,}", "this window", "flat",
              "Unique artifacts deployed to QC")

# Slot 1: Commits (Developer/Admin) or Releases (QC/Operator)
if role_pick in ("Admin", "Developer"):
    d, dn = fmt_delta(commits_now, commits_prev)
    kpi_block(r2c[1], "Commits", f"{commits_now:,}", d, dn)
else:
    kpi_block(r2c[1], "Releases", f"{rel_now:,}",
              f"{rel_now - rel_prev:+d} vs prior" if rel_prev else "this window",
              "up" if rel_now >= rel_prev else "dn")

# Slot 2: Pending — always relevant (role-scoped queue shown in Workflow section)
kpi_block(r2c[2], "Pending", f"{pending_now:,}",
    "needs action" if pending_now else "clear",
    "dn" if pending_now else "up", "Pending approvals (last 30d)")

# Slot 3: JIRA (Admin/Dev/QC) or Deploy freq-focused metric (Operator)
if _ROLE_SHOWS_JIRA.get(role_pick, True):
    kpi_block(r2c[3], "Open JIRA", f"{open_jira:,}", "all-time", "flat")
else:
    kpi_block(r2c[3], "UAT artifacts", f"{art_dep_uat:,}", "this window", "flat",
              "Unique artifacts deployed to UAT")

kpi_block(r2c[4], "Platform health",
    f"{active_projs}/{inv_count}" if inv_count else "—",
    f"{100 - dormant_pct:.0f}% active" if inv_count else "",
    "up" if dormant_pct < 30 else ("dn" if dormant_pct > 60 else "flat"), "active / inventory")

with r2c[5]:
    with st.popover("📈  WoW / MoM / YoY trends", use_container_width=True):
        st.markdown("**Rolling period comparisons** — independent of the window selector above")
        _trend_rows = []
        for _lbl, _idx, _df, _ex, _use_cs in _trend_metrics:
            _row: dict[str, Any] = {"Metric": _lbl}
            for _pl, _td in _periods:
                _cs, _ce, _ps, _pe = _trend_windows(_td)
                _cur  = count_with_range(_idx, _df, _cs, _ce, extra=_ex, use_commit_scope=_use_cs)
                _prev = count_with_range(_idx, _df, _ps, _pe, extra=_ex, use_commit_scope=_use_cs)
                _row[_pl] = _cell(_cur, _prev)
            _trend_rows.append(_row)
        _hdrs = ["Metric"] + [p[0] for p in _periods]
        _html = [
            '<div style="background:var(--cc-surface);border:1px solid var(--cc-border);border-radius:10px;overflow:hidden;">',
            '<table style="width:100%;border-collapse:collapse;font-size:.88rem;">',
            '<thead><tr>',
        ]
        for _i, _h in enumerate(_hdrs):
            _align = "left" if _i == 0 else "right"
            _html.append(
                f'<th style="text-align:{_align};padding:10px 14px;color:var(--cc-text-mute);font-size:.68rem;'
                f'letter-spacing:.10em;text-transform:uppercase;font-weight:600;'
                f'border-bottom:1px solid var(--cc-border);background:var(--cc-surface2);">{_h}</th>'
            )
        _html.append('</tr></thead><tbody>')
        for _row in _trend_rows:
            _html.append('<tr>')
            _html.append(f'<td style="padding:9px 14px;color:var(--cc-text);font-weight:500;border-bottom:1px solid var(--cc-border);">{_row["Metric"]}</td>')
            for _pl, _ in _periods:
                _html.append(f'<td style="text-align:right;padding:9px 14px;font-variant-numeric:tabular-nums;border-bottom:1px solid var(--cc-border);">{_row[_pl]}</td>')
            _html.append('</tr>')
        _html.append('</tbody></table></div>')
        st.markdown("".join(_html), unsafe_allow_html=True)


# ── Contextual digest — one-liner translating the numbers into meaning ──────
_stuck_count_early = sum(1 for r in _all_pending if (r.get("Age (h)") or 0) >= 24)
_digest_parts: list[str] = []
if prd_fail:
    _digest_parts.append(f"<b>{prd_fail}</b> prod deploy failure(s) need attention")
if _stuck_count_early:
    _digest_parts.append(f"<b>{_stuck_count_early}</b> approval(s) stuck > 24h")
if builds_now and success_rate < 80:
    _digest_parts.append(f"build success rate <b>{success_rate:.0f}%</b> (below 80%)")
if art_built and art_build_to_prd < 30:
    _digest_parts.append(f"only <b>{art_build_to_prd:.0f}%</b> of artifacts reaching PRD")
if not _digest_parts and builds_now:
    # positive summary
    if art_built and art_build_to_prd >= 50:
        _digest_parts.append(f"<b>{art_dep_prd}</b> of <b>{art_built}</b> artifacts reached PRD ({art_build_to_prd:.0f}%)")
    if success_rate >= 95:
        _digest_parts.append(f"build pipeline healthy at <b>{success_rate:.0f}%</b> success")
    if deploy_freq_per_day >= 1:
        _digest_parts.append(f"shipping <b>{deploy_freq_per_day:.1f}</b> prod deploys / day")
    if not pending_now:
        _digest_parts.append("no pending approvals")
if _digest_parts:
    _has_issues = bool(prd_fail or _stuck_count_early)
    st.markdown(
        f'<div class="learn">'
        f'  <b>TL;DR</b> &mdash; {" &middot; ".join(_digest_parts)}'
        f'</div>',
        unsafe_allow_html=True,
    )


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

    _deploy_extra = [{"bool": {"must_not": [{"prefix": {"codeversion": "0."}}]}}]
    # Prod deploys
    _r = _run_search(IDX["deployments"], json.dumps({
        "query": {"bool": {"filter": [
            range_filter("startdate", _win, _now),
            {"term": {"environment": "prd"}},
        ] + _sf + _deploy_extra}},
        "sort": [{"startdate": "desc"}], "track_total_hits": True,
    }, default=str, sort_keys=True), 6)
    for _h in _r.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _ok = (_s.get("status") or "").upper() not in ("FAILED", "FAILURE", "FAILED")
        _evts.append({
            "ts": parse_dt(_pick_date(_s, "deploy")),
            "type": "prd-deploy",
            "label": f'PRD deploy · {_s.get("application") or _s.get("project","")} v{_s.get("codeversion","")}',
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
            "ts": parse_dt(_pick_date(_s, "release")),
            "type": "release",
            "label": f'Release · {_s.get("application","")} v{_s.get("codeversion","")}',
            "ok": True,
        })

    _build_extra = [{"term": {"branch": "release"}}]
    # Failed builds (release branch only)
    _r = _run_search(IDX["builds"], json.dumps({
        "query": {"bool": {"filter": [
            range_filter("startdate", _win, _now),
            {"terms": {"status": FAILED_STATUSES}},
        ] + _sf + _build_extra}},
        "sort": [{"startdate": "desc"}], "track_total_hits": True,
    }, default=str, sort_keys=True), 4)
    for _h in _r.get("hits", {}).get("hits", []):
        _s = _h["_source"]
        _evts.append({
            "ts": parse_dt(_pick_date(_s, "build")),
            "type": "fail",
            "label": f'Build failed · {_s.get("application") or _s.get("project","")} {_s.get("branch","")}',
            "ok": False,
        })

    _evts.sort(key=lambda e: e["ts"] or pd.Timestamp("1970-01-01", tz="UTC"), reverse=True)
    return _evts[:14]


_tick_scope = json.dumps(scope_filters(), sort_keys=True)
_tick_evts = _ticker_events(_tick_scope, exclude_svc)

if _tick_evts:
    _TYPE_CHIP = {
        "prd-deploy": ("PRD", "#059669", "#d1fae5"),
        "release":    ("REL", "#4f46e5", "#eef2ff"),
        "fail":       ("FAIL", "#dc2626", "#fee2e2"),
    }
    _ticker_html_items = []
    for _te in _tick_evts:
        _ch_lbl, _ch_clr, _ch_bg = _TYPE_CHIP.get(_te["type"], ("EVT", "#8890a4", "#f7f8fb"))
        _age_h = age_hours(_te["ts"]) or 0
        _age_str = f"{_age_h}h ago" if _age_h < 24 else f"{_age_h//24}d ago"
        _item_bg = "#fee2e2" if not _te["ok"] else "#f7f8fb"
        _ticker_html_items.append(
            f'<span style="display:inline-flex;align-items:center;gap:6px;'
            f'padding:3px 10px 3px 4px;margin:0 6px 0 0;'
            f'background:{_item_bg};border:1px solid var(--cc-border);border-radius:20px;'
            f'white-space:nowrap;font-size:0.73rem;">'
            f'  <span style="background:{_ch_clr};color:#fff;font-size:0.63rem;font-weight:700;'
            f'  padding:1px 6px;border-radius:999px">{_ch_lbl}</span>'
            f'  <span style="color:var(--cc-text-dim)">{_te["label"]}</span>'
            f'  <span style="color:var(--cc-text-mute)">{_age_str}</span>'
            f'</span>'
        )
    st.markdown(
        '<div style="overflow-x:auto;white-space:nowrap;padding:6px 0 8px;'
        'border-bottom:1px solid var(--cc-border);margin-bottom:8px">'
        + "".join(_ticker_html_items) + "</div>",
        unsafe_allow_html=True,
    )


# ── Role-based section emphasis ────────────────────────────────────────────
# Each role sees only the sections relevant to them. Admin sees everything.
# Priority order matters: used for nav chip ordering too.
_ROLE_PRIORITY_SECTIONS: dict[str, list[str]] = {
    "Admin":     ["alerts", "landscape", "lifecycle", "pipeline", "workflow", "eventlog"],
    "Developer": ["alerts", "pipeline", "lifecycle", "workflow", "eventlog"],
    "QC":        ["alerts", "workflow", "lifecycle", "pipeline", "eventlog"],
    "Operator":  ["alerts", "workflow", "pipeline", "lifecycle", "eventlog"],
}
# Effective role for rendering — Admin can view-as another role
_effective_role = role_pick
if role_pick == "Admin":
    _admin_view = st.session_state.get("admin_role_view", "Admin")
    if _admin_view != "Admin" and _admin_view in _ROLE_PRIORITY_SECTIONS:
        _effective_role = _admin_view

_visible = set(_ROLE_PRIORITY_SECTIONS.get(_effective_role, _ROLE_PRIORITY_SECTIONS["Admin"]))


def _show(section: str) -> bool:
    """Return True if the current role should see this section."""
    return section in _visible


# ── Role-scoped event type / env / stage helpers ──────────────────────────
# The dicts themselves (_ROLE_EVENT_TYPES, _ROLE_ENVS, _ROLE_APPROVAL_STAGES,
# _ROLE_SHOWS_JIRA, _ROLE_SHOWS_BUILDS) are defined near the top of the page
# so the KPI row can use them.


def _role_allows_type(t: str) -> bool:
    return t in _ROLE_EVENT_TYPES.get(_effective_role, _ROLE_EVENT_TYPES["Admin"])


def _role_allows_env(env: str) -> bool:
    return env in _ROLE_ENVS.get(_effective_role, _ROLE_ENVS["Admin"])


def _role_stage_filter() -> dict | None:
    """Return an ES filter that restricts approval stages to the role's scope,
    or None for Admin (no restriction).
    """
    stages = _ROLE_APPROVAL_STAGES.get(_effective_role, [])
    if not stages:
        return None
    shoulds: list[dict] = []
    for s in stages:
        shoulds.append({"prefix": {"stage": s}})
    return {"bool": {"should": shoulds, "minimum_should_match": 1}}

# Role-specific nav chip labels
_NAV_LABELS: dict[str, dict[str, str]] = {
    "Developer": {"pipeline": "My builds", "workflow": "Requests"},
    "QC":        {"workflow": "Release queue", "lifecycle": "Quality gates"},
    "Operator":  {"workflow": "Deploy queue", "pipeline": "Deployments"},
}
_rl = _NAV_LABELS.get(role_pick, {})

# ── Section navigation — quick-jump chip strip (role-ordered, role-filtered) ─
_all_nav = [
    ("alerts", "Alerts", "#sec-alerts"),
    ("landscape", _rl.get("landscape", "Landscape"), "#sec-landscape"),
    ("lifecycle", _rl.get("lifecycle", "Lifecycle"), "#sec-lifecycle"),
    ("pipeline", _rl.get("pipeline", "Pipeline"), "#sec-pipeline"),
    ("workflow", _rl.get("workflow", "Workflow"), "#sec-workflow"),
    ("eventlog", "Event log", "#sec-eventlog"),
]
# Filter to visible sections only, then order by role priority
_priority_order = _ROLE_PRIORITY_SECTIONS.get(_effective_role, _ROLE_PRIORITY_SECTIONS["Admin"])
_ordered_nav = sorted(
    [n for n in _all_nav if _show(n[0])],
    key=lambda x: _priority_order.index(x[0]) if x[0] in _priority_order else 99,
)

_nav_html = '<div class="navchips"><span class="navlbl">Jump to</span>'
for _sec_id, _nl, _nh in _ordered_nav:
    # Highlight the top-2 role-priority sections
    _is_primary = _priority_order.index(_sec_id) <= 2 if _sec_id in _priority_order else False
    _extra_cls = "" if not _is_primary else ' style="border-color:var(--cc-accent);color:var(--cc-accent) !important;font-weight:700"'
    _nav_html += f'<a href="{_nh}"{_extra_cls}>{_nl}</a>'
_nav_html += '</div>'
st.markdown(_nav_html, unsafe_allow_html=True)


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
stuck = sum(
    1 for r in _all_pending
    if (r.get("Age (h)") or 0) >= 24
)
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

# Render the alerts ribbon into the placeholder above the KPIs so the most
# actionable items are always at the top of the page.
with _alerts_ph:
    st.markdown('<a class="anchor" id="sec-alerts"></a>', unsafe_allow_html=True)
    if not alerts:
        st.markdown(
            '<div class="alert success">'
            '<div class="icon">✓</div>'
            '<div><b>All clear.</b><span class="sub">No actionable alerts in the current window.</span></div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        # Sort by severity so danger items always render first
        _sev_order = {"danger": 0, "warning": 1, "info": 2, "success": 3}
        _sorted_alerts = sorted(alerts, key=lambda a: _sev_order.get(a[0], 9))
        # Each alert has an inline "View" popover that routes straight to the breakdown
        for _ai, (_sev, _icon, _title, _detail) in enumerate(_sorted_alerts):
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
                        _stuck_rows = [r for r in _all_pending if (r.get("Age (h)") or 0) >= 24]
                        if _stuck_rows:
                            st.dataframe(pd.DataFrame([{
                                "#":           r["#"],
                                "Type":        r["Type"],
                                "Requester":   r["Requester"],
                                "Application": r["Application"],
                                "Age (h)":     r["Age (h)"],
                                "Queue":       r["_idx"],
                            } for r in _stuck_rows]), use_container_width=True, hide_index=True, height=420)
                        else:
                            inline_note("No stuck approvals.", "success")

                    elif "production deployment" in _title.lower():
                        _ar = es_search(IDX["deployments"], {
                            "query": {"bool": {"filter": [
                                range_filter("startdate", start_dt, end_dt),
                                {"term": {"environment": "prd"}},
                                {"terms": {"status": FAILED_STATUSES}},
                            ] + deploy_scope_filters()}},
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
                            ] + build_scope_filters()}},
                            "sort": [{"startdate": "desc"}]}, size=100)
                        _ah = _ar.get("hits", {}).get("hits", [])
                        if _ah:
                            st.dataframe(pd.DataFrame([{
                                "When":        fmt_dt(h["_source"].get("startdate"), "%Y-%m-%d %H:%M"),
                                "Application": h["_source"].get("application") or h["_source"].get("project"),
                                "Project":     h["_source"].get("project"),
                                "Branch":      h["_source"].get("branch"),
                                "Version":     h["_source"].get("codeversion"),
                                "Build tech":  h["_source"].get("technology"),
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
                        # Dormant applications: in inventory but no builds in this window
                        _dorm_inv_q = {"bool": {"filter": scope_filters_inv()}} if scope_filters_inv() else {"match_all": {}}
                        _dorm_inv   = set(composite_terms(IDX["inventory"], "application.keyword", _dorm_inv_q).keys())
                        _dorm_act_q = {"bool": {"filter": [range_filter("startdate", start_dt, end_dt)] + build_scope_filters()}}
                        _dorm_act   = set(composite_terms(IDX["builds"], "application", _dorm_act_q).keys())
                        _dormant_list = sorted(_dorm_inv - _dorm_act)[:50]
                        if _dormant_list:
                            st.dataframe(pd.DataFrame({"Application": _dormant_list}),
                                         use_container_width=True, hide_index=True)
                        else:
                            inline_note("No dormant applications in window.", "info")


# =============================================================================
# SECTION 3 — CROSS-INDEX INSIGHTS  (Admin, Developer)
# =============================================================================

st.markdown('<a class="anchor" id="sec-landscape"></a>', unsafe_allow_html=True)
st.markdown(
    '<div class="section">'
    '<div class="title-wrap"><h2>Project landscape</h2><span class="badge">Inventory x Activity</span></div>'
    '<span class="hint">active / at-risk / archival candidates &mdash; joined across all indices</span>'
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
            _pf     = [{"term": {"project": _dd_proj}}]
            _pf_b   = _pf + [{"term": {"branch": "release"}}]                                           # build filter
            _pf_d   = _pf + [{"bool": {"must_not": [{"prefix": {"codeversion": "0."}}]}}]               # deploy filter
            _pf_inv = [{"term": {"project.keyword": _dd_proj}}]

            # Aggregate per-project stats across all indices
            _b_all   = es_count(IDX["builds"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf_b}}})
            _b_fail  = es_count(IDX["builds"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end), {"terms": {"status": FAILED_STATUSES}}] + _pf_b}}})
            _d_all   = es_count(IDX["deployments"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf_d}}})
            _d_prd   = es_count(IDX["deployments"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end), {"term": {"environment": "prd"}}] + _pf_d}}})
            _d_fail  = es_count(IDX["deployments"], {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end), {"term": {"environment": "prd"}}, {"terms": {"status": FAILED_STATUSES}}] + _pf_d}}})
            _c_all   = es_count(IDX["commits"], {"query": {"bool": {"filter": [range_filter("commitdate", _dd_start, _dd_end)] + _pf + ([{"bool": {"must_not": [{"term": {"authorname": SVC_ACCOUNT}}]}}] if exclude_svc else [])}}})
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
                    {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf_b}},
                     "sort": [{"startdate": "desc"}]},
                    size=50,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    _rows = [
                        {
                            "When":       fmt_dt(_pick_date(_s, "build"), "%Y-%m-%d %H:%M"),
                            "Branch":     _s.get("branch"),
                            "Version":    _s.get("codeversion"),
                            "Status":     _s.get("status"),
                            "Build tech": _s.get("technology"),
                        }
                        for _h in _hits for _s in [_h.get("_source", {})]
                    ]
                    st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True, height=360)
                else:
                    inline_note("No builds in window.", "info")

            with dd_tabs[1]:
                _r = es_search(
                    IDX["deployments"],
                    {"query": {"bool": {"filter": [range_filter("startdate", _dd_start, _dd_end)] + _pf_d}},
                     "sort": [{"startdate": "desc"}]},
                    size=50,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    _rows = [
                        {
                            "When":        fmt_dt(_pick_date(_s, "deploy"), "%Y-%m-%d %H:%M"),
                            "Env":         _s.get("environment"),
                            "Version":     _s.get("codeversion"),
                            "Status":      _s.get("status"),
                            "Deploy tech": _s.get("technology"),
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
                _dd_commit_f = [range_filter("commitdate", _dd_start, _dd_end)] + _pf
                if exclude_svc:
                    _dd_commit_f.append({"bool": {"must_not": [{"term": {"authorname": SVC_ACCOUNT}}]}})
                _r = es_search(
                    IDX["commits"],
                    {"query": {"bool": {"filter": _dd_commit_f}},
                     "sort": [{"commitdate": "desc"}]},
                    size=50,
                )
                _hits = _r.get("hits", {}).get("hits", [])
                if _hits:
                    _rows = [
                        {
                            "When":   fmt_dt(_pick_date(_s, "commit"), "%Y-%m-%d %H:%M"),
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
        _cdr_f = [range_filter("commitdate", _cs, _ce)]
        if exclude_svc:
            _cdr_f.append({"bool": {"must_not": [{"term": {"authorname": SVC_ACCOUNT}}]}})
        _cr = es_search(
            IDX["commits"],
            {
                "query": {"bool": {"filter": _cdr_f}},
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

# application → (build_technology, deploy_technology) from inventory
_app_build_tech: dict[str, str] = {}
_app_deploy_tech: dict[str, str] = {}
_inv_tech_res = es_search(
    IDX["inventory"],
    {
        "query": _tm_inv_query,
        "aggs": {
            "apps": {
                "terms": {"field": "application.keyword", "size": 1000},
                "aggs": {
                    "bt": {"terms": {"field": "build_technology.keyword", "size": 1}},
                    "dt": {"terms": {"field": "deploy_technology.keyword", "size": 1}},
                },
            }
        },
    },
)
for _tb in bucket_rows(_inv_tech_res, "apps"):
    _app_name = _tb["key"]
    _bt_bkts = _tb.get("bt", {}).get("buckets") or []
    _dt_bkts = _tb.get("dt", {}).get("buckets") or []
    if _bt_bkts:
        _app_build_tech[_app_name] = _bt_bkts[0]["key"]
    if _dt_bkts:
        _app_deploy_tech[_app_name] = _dt_bkts[0]["key"]


# ── All-time activity per application (NOT time-filtered) ───────────────────
# Use "application" field in operational indices (inventory is the master reference).
_tm_build_q   = {"bool": {"filter": build_scope_filters()}} if build_scope_filters() else {"match_all": {}}
_tm_deploy_prd_q = {"bool": {"filter": [{"term": {"environment": "prd"}}] + deploy_scope_filters()}}
_tm_active_map = composite_terms(IDX["builds"], "application", _tm_build_q)            # app → build_count
_tm_prd_map    = composite_terms(IDX["deployments"], "application", _tm_deploy_prd_q)  # app → prd_deploy_count
# Unique versions built all-time per application
_tm_uv_map = composite_unique_versions(IDX["builds"], "application", _tm_build_q)

# All-time fails per application
_tm_fail_res = es_search(IDX["builds"], {
    "query": _tm_build_q,
    "aggs": {"apps": {"terms": {"field": "application", "size": 500},
                      "aggs": {"fails": {"filter": {"terms": {"status": FAILED_STATUSES}}}}}},
})
_tm_fail_map = {b["key"]: b.get("fails", {}).get("doc_count", 0) for b in bucket_rows(_tm_fail_res, "apps")}

# Open JIRA per application (JIRA uses "project" field — map via inventory lookup)
_jira_proj_map = {b["key"]: b["doc_count"] for b in bucket_rows(
    es_search(IDX["jira"], {
        "query": {"bool": {"filter": scope_filters_inv(), "must_not": [{"terms": {"status": CLOSED_JIRA}}]}},
        "aggs": {"apps": {"terms": {"field": "project", "size": 500}}},
    }), "apps",
)}
# Distribute JIRA counts down to applications via the inventory hierarchy
_jira_map_tm: dict[str, int] = {}
for _jp, _jcnt in _jira_proj_map.items():
    _japps = _tm_proj_to_apps.get(_jp, [])
    if _japps:
        _per_app = max(1, _jcnt // len(_japps))
        for _ja in _japps:
            _jira_map_tm[_ja] = _jira_map_tm.get(_ja, 0) + _per_app
    else:
        _jira_map_tm[_jp] = _jira_map_tm.get(_jp, 0) + _jcnt

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
        "application":      _app,
        "project":          _parent,
        "builds":           max(_builds_all, 1),
        "uniq_ver":         _uv,
        "status":           _status,
        "score":            _score,
        "fails":            _fails,
        "open_jira":        _jira_open,
        "live":             "Yes" if _in_prd else "No",
        "build_tech":       _app_build_tech.get(_app, "—"),
        "deploy_tech":      _app_deploy_tech.get(_app, "—"),
    })

if _tm_rows:
    _df_tm = pd.DataFrame(_tm_rows)
    _color_map = {
        "Live · healthy":        "#059669",
        "Live · at-risk":        "#d97706",
        "Building · not in PRD": "#2563eb",
        "Archival candidate":    "#9ca3af",
        "Unknown":               "#d1d5db",
    }
    # Treemap: status → project → application  (3-level hierarchy)
    _tm_fig = px.treemap(
        _df_tm,
        path=["status", "project", "application"],
        values="builds",
        color="status",
        color_discrete_map=_color_map,
        custom_data=["fails", "open_jira", "score", "uniq_ver", "live", "deploy_tech", "build_tech"],
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
            "Live (in PRD): %{customdata[4]}<br>"
            "Deploy tech: %{customdata[5]}<br>"
            "Build tech: %{customdata[6]}"
            "<extra></extra>"
        ),
        textinfo="label+value",
        insidetextfont=dict(size=11, color="white"),
    )
    _tm_fig.update_layout(
        height=420,
        margin=dict(l=0, r=0, t=36, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#4a5068", family="system-ui, sans-serif"),
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
                f'background:{_c_clr}18;color:{_c_clr};border:1px solid {_c_clr}40">'
                f'{_n} {_s}</span>'
            )
    st.markdown(f'<div style="margin-top:6px">{_pills}</div>', unsafe_allow_html=True)

    # Archival candidates — collapsed to reduce noise
    _archival = _df_tm[_df_tm["status"] == "Archival candidate"].sort_values("application")
    if not _archival.empty:
        with st.expander(f"⚠ {len(_archival)} archival candidate(s) — in inventory but no builds ever recorded"):
            _arc_d = _archival[["project", "application", "deploy_tech", "build_tech", "open_jira"]].copy()
            _arc_d.columns = ["Project", "Application", "Deploy tech", "Build tech", "Open JIRA"]
            st.dataframe(_arc_d, use_container_width=True, hide_index=True, height=400)
else:
    inline_note("No application data available.", "info")

# =============================================================================
# APP LIFECYCLE — pipeline stage funnel per project + bottleneck finder
# =============================================================================

st.markdown('<a class="anchor" id="sec-lifecycle"></a>', unsafe_allow_html=True)
st.markdown(
    '<div class="section">'
    '<div class="title-wrap">'
    '  <h2>Artifact lifecycle &amp; bottlenecks</h2>'
    '  <span class="badge">Build &rarr; Dev &rarr; QC &rarr; Release &rarr; UAT &rarr; PRD</span>'
    '</div>'
    '<span class="hint">unique code versions (artifacts) at each pipeline stage &mdash; where do artifacts stall?</span>'
    '</div>',
    unsafe_allow_html=True,
)

# The correct pipeline is:  Builds → Deploy Dev → Deploy QC → Deploy UAT → Deploy PRD
# field for builds/deployments: "project"  (NOT "project.keyword" — that sub-field may not exist)
# field for releases: "application"

_LC_STAGES   = ["Builds", "Deploy Dev", "Deploy QC", "Release", "Deploy UAT", "Deploy PRD"]
_LC_COLORS   = ["#6366f1", "#0ea5e9", "#8b5cf6", "#ec4899", "#f59e0b", "#16a34a"]
# "dropout" node color — neutral gray
_LC_DROPOUT  = "#e3e6ee"


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

    _build_f   = _scope + [{"term": {"branch": "release"}}]
    _deploy_f  = _scope + [{"bool": {"must_not": [{"prefix": {"codeversion": "0."}}]}}]

    def _uv_by_app(index: str, date_field: str,
                   app_field: str = "application",
                   extra: list[dict] | None = None,
                   base_scope: list[dict] | None = None) -> dict[str, int]:
        """Unique codeversion count per application — eliminates re-runs."""
        _base = base_scope if base_scope is not None else _scope
        _f = [range_filter(date_field, _s, _e)] + _base + (extra or [])
        return composite_unique_versions(index, app_field, {"bool": {"filter": _f}})

    builds_by_app  = _uv_by_app(IDX["builds"],      "startdate",   base_scope=_build_f)
    dep_dev_by_app = _uv_by_app(IDX["deployments"],  "startdate",  extra=[{"term": {"environment": "dev"}}], base_scope=_deploy_f)
    dep_qc_by_app  = _uv_by_app(IDX["deployments"],  "startdate",  extra=[{"term": {"environment": "qc"}}],  base_scope=_deploy_f)
    rel_by_app     = _uv_by_app(IDX["releases"],      "releasedate", app_field="application")
    dep_uat_by_app = _uv_by_app(IDX["deployments"],  "startdate",  extra=[{"term": {"environment": "uat"}}], base_scope=_deploy_f)
    dep_prd_by_app = _uv_by_app(IDX["deployments"],  "startdate",  extra=[{"term": {"environment": "prd"}}], base_scope=_deploy_f)

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
                    line=dict(color="#e3e6ee", width=0.5),
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
                    font=dict(size=13, color="#1a1d2e"), x=0,
                ),
                font=dict(size=11, color="#4a5068", family="system-ui, sans-serif"),
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
            '<p style="font-size:0.82rem;font-weight:700;color:var(--cc-text);margin:4px 0 8px">'
            'Stage conversion — biggest bottlenecks first</p>',
            unsafe_allow_html=True,
        )
        _bn_html = []
        for _r in sorted(_bn_rows, key=lambda x: x["Drop"], reverse=True):
            _d = _r["Drop"]
            _bg = "#fee2e2" if _d >= 70 else "#fef3c7" if _d >= 40 else "#d1fae5"
            _fg = "#991b1b" if _d >= 70 else "#92400e" if _d >= 40 else "#065f46"
            _bar_bg = "#dc2626" if _d >= 70 else "#d97706" if _d >= 40 else "#059669"
            _bar_w = max(3, int(_d * 0.9))
            _rate_w = max(3, int((100 - _d) * 0.9))
            _bn_html.append(
                f'<div style="margin-bottom:8px">'
                f'  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:3px">'
                f'    <span style="font-size:0.77rem;color:var(--cc-text-dim);font-weight:600">{_r["Stage"]}</span>'
                f'    <span style="font-size:0.75rem;color:var(--cc-text-mute)">'
                f'      {int(_r["In"]):,} → {int(_r["Out"]):,}'
                f'    </span>'
                f'  </div>'
                f'  <div style="display:flex;height:8px;border-radius:4px;overflow:hidden;background:var(--cc-surface2)">'
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
    '<p style="font-size:0.85rem;font-weight:700;color:var(--cc-text);margin:18px 0 4px">'
    'Project status — live vs dormant (classified by pipeline position)</p>',
    unsafe_allow_html=True,
)

# Classify each project into one of 5 buckets:
# Classify each application through the pipeline.
# _lc_apps contains application names keyed from the "application" field.
# _app_to_parent maps application → parent project from inventory.

_inv_apps = set(_app_to_parent.keys())

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
    "Live (in PRD)":   "#059669",
    "Stuck in UAT":    "#d97706",
    "Dead in Quality": "#7c3aed",
    "Dead in Dev":     "#dc2626",
    "Dark":            "#9ca3af",
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
            f'background:var(--cc-surface);border:1px solid var(--cc-border);border-left:3px solid {_col};'
            f'border-radius:8px;padding:6px 12px;" title="{_STATUS_DESC[_s]}">'
            f'  <span style="font-size:1rem;font-weight:700;color:{_col}">{_STATUS_ICONS[_s]}</span>'
            f'  <div>'
            f'    <div style="font-size:1.0rem;font-weight:700;color:var(--cc-text);line-height:1;font-family:var(--cc-mono)">{_c}</div>'
            f'    <div style="font-size:0.68rem;color:var(--cc-text-dim);font-weight:600">{_s}</div>'
            f'  </div>'
            f'  <div style="font-size:0.72rem;color:var(--cc-text-mute);margin-left:4px">{_pct:.0f}%</div>'
            f'</div>'
        )
    _pill_html += "</div>"
    st.markdown(_pill_html, unsafe_allow_html=True)

# Consolidated status breakdown — single expander instead of individual alerts
_bottleneck_statuses = [s for s in ["Stuck in UAT", "Dead in Quality", "Dead in Dev", "Dark"] if _counts.get(s, 0)]
if _bottleneck_statuses:
    _bn_summary = " · ".join(f"{_counts[s]} {s}" for s in _bottleneck_statuses)
    with st.expander(f"🔍 Pipeline bottlenecks — {_bn_summary}", expanded=False):
        for _s, _desc in [
            ("Stuck in UAT",    "UAT deploys exist but nothing reached PRD"),
            ("Dead in Quality", "reached QC/release but never promoted to UAT"),
            ("Dead in Dev",     "builds exist but never deployed anywhere"),
            ("Dark",            "no builds in window — archival candidates"),
        ]:
            _n = _counts.get(_s, 0)
            if _n == 0:
                continue
            _app_list = sorted(a for a, v in _lc_classified.items() if v == _s)
            st.markdown(f"**{_STATUS_ICONS[_s]} {_s}** — {_n} application(s): {_desc}")
            _pl_df = pd.DataFrame([{
                "Application": _a,
                "Project":     _app_to_parent.get(_a, "—"),
                "Builds":      _stage_maps["Builds"].get(_a, 0),
                "Dev":         _stage_maps["Deploy Dev"].get(_a, 0),
                "QC":          _stage_maps["Deploy QC"].get(_a, 0),
                "UAT":         _stage_maps["Deploy UAT"].get(_a, 0),
                "PRD":         _stage_maps["Deploy PRD"].get(_a, 0),
            } for _a in _app_list])
            st.dataframe(_pl_df, use_container_width=True, hide_index=True, height=min(200, 35 * len(_app_list) + 40))

# ── Row 3: Per-application pipeline heatmap (collapsed to save space) ───────
if _lc_apps:
    _app_activity = {
        a: sum(_stage_maps[s].get(a, 0) for s in _LC_STAGES)
        for a in _lc_apps
    }
    _top_apps = sorted(_app_activity, key=_app_activity.get, reverse=True)[:35]  # type: ignore[arg-type]

    # Y-axis: "icon AppName [Project]"
    _y_labels = [
        f"{_STATUS_ICONS.get(_lc_classified.get(a,'Dark'), '○')} {a}"
        + (f" [{_app_to_parent[a]}]" if a in _app_to_parent else "")
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
            [1.0,  "#059669"],
        ],
        zmin=0, zmax=100,
        colorbar=dict(
            title=dict(text="% of builds", side="right", font=dict(size=11, color="#8890a4")),
            thickness=12, len=0.85,
            tickfont=dict(size=10, color="#8890a4"),
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
            font=dict(size=13, color="#1a1d2e"), x=0,
        ),
        xaxis=dict(
            side="top", tickfont=dict(size=12, color="#4a5068", family="system-ui, sans-serif"),
            showgrid=False, zeroline=False,
        ),
        yaxis=dict(
            tickfont=dict(size=10, color="#4a5068", family="system-ui, sans-serif"),
            autorange="reversed", showgrid=False, zeroline=False,
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=70, t=56, b=0),
        height=max(300, len(_top_apps) * 26),
        font=dict(family="system-ui, sans-serif", color="#4a5068"),
    )
    with st.expander(f"📊 Per-application pipeline heatmap — top {len(_top_apps)} by build volume", expanded=False):
        st.plotly_chart(_hm_fig, use_container_width=True)
        st.caption(
            f"✓ = Live in PRD  ·  ⏸ = Stuck in UAT  ·  ⚗ = Dead in QC  ·  ⚠ = Dead in Dev  ·  ○ = Dark  "
            f"·  color = % of builds that reached each stage"
        )

ci1, ci2 = st.columns([1.1, 2])

# ---- Delivery funnel ------------------------------------------------------
with ci1:
    st.markdown(
        '<div class="funnel-wrap">'
        '<div style="font-size:.95rem;color:var(--cc-text);font-weight:600;margin-bottom:4px;">Delivery funnel</div>'
        '<div style="font-size:.78rem;color:var(--cc-text-mute);margin-bottom:14px;">code → build → prod deploy in window</div>',
        unsafe_allow_html=True,
    )

    # Artifact-centric funnel: unique code_versions at each pipeline stage
    _funnel_tab1, _funnel_tab2 = st.tabs(["Artifact flow", "Raw counts"])
    with _funnel_tab1:
        st.markdown(
            '<div style="font-size:.72rem;color:var(--cc-text-mute);margin-bottom:8px">'
            'Unique code versions (artifacts) reaching each stage</div>',
            unsafe_allow_html=True,
        )
        art_stages = [
            ("🏗 Built",              art_built,       "#6366f1"),
            ("✓ Built (success)",     art_built_ok,    "#059669"),
            ("→ Deployed to Dev",     art_dep_dev,     "#0ea5e9"),
            ("→ Deployed to QC",      art_dep_qc,      "#8b5cf6"),
            ("📦 Released",           art_released,    "#ec4899"),
            ("→ Deployed to UAT",     art_dep_uat,     "#f59e0b"),
            ("🚀 Deployed to PRD",    art_dep_prd,     "#16a34a"),
        ]
        _art_top = max(art_stages[0][1], 1)
        _art_prev = None
        _art_html = ""
        for _aname, _aval, _acolor in art_stages:
            _apct = (_aval / _art_top * 100) if _art_top else 0
            _aconv = ""
            if _art_prev is not None and _art_prev > 0:
                _aratio = _aval / _art_prev * 100
                _aconv = f'<span class="conv">· {_aratio:.0f}% of prev</span>'
            _art_html += (
                f'<div class="funnel-stage">'
                f'  <div><div class="name">{_aname}{_aconv}</div>'
                f'  <div class="funnel-bar" style="width:{_apct:.0f}%;background:linear-gradient(90deg,{_acolor},var(--cc-blue));"></div></div>'
                f'  <div class="value">{_aval:,}</div>'
                f'</div>'
            )
            _art_prev = _aval
        st.markdown(_art_html, unsafe_allow_html=True)
        if art_built:
            st.caption(
                f"**{art_build_to_prd:.0f}%** of built artifacts reached production"
                + (f" · {art_built - art_dep_prd} version(s) didn't make it to PRD" if art_built > art_dep_prd else "")
            )
    with _funnel_tab2:
        st.markdown(
            '<div style="font-size:.72rem;color:var(--cc-text-mute);margin-bottom:8px">'
            'Raw event counts (includes retries/re-runs)</div>',
            unsafe_allow_html=True,
        )
        raw_stages = [
            ("Commits",            commits_now,             C_ACCENT),
            ("Builds",             builds_now,              C_INFO),
            ("Successful builds",  builds_now - builds_fail, C_SUCCESS),
            ("Deployments (all)",  deploys_now,             C_INFO),
            ("Production deploys", prd_deploys,             C_SUCCESS),
        ]
        _raw_top = max(raw_stages[0][1], 1)
        _raw_prev = None
        _raw_html = ""
        for name, val, color in raw_stages:
            pct_of_top = (val / _raw_top * 100) if _raw_top else 0
            conv = ""
            if _raw_prev is not None and _raw_prev > 0:
                ratio = val / _raw_prev * 100
                conv = f'<span class="conv">· {ratio:.0f}% of prev</span>'
            _raw_html += (
                f'<div class="funnel-stage">'
                f'  <div><div class="name">{name}{conv}</div>'
                f'  <div class="funnel-bar" style="width:{pct_of_top:.0f}%;background:linear-gradient(90deg,{color},{C_INFO});"></div></div>'
                f'  <div class="value">{val:,}</div>'
                f'</div>'
            )
            _raw_prev = val
        st.markdown(_raw_html, unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

# ---- Project health scoreboard --------------------------------------------
with ci2:
    st.markdown("**Application health scoreboard** — top 15 most active applications, joined across indices")

    # Pull per-application builds with success/fail breakdown, and per-application deploys
    body_b = {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + build_scope_filters()
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
                ] + deploy_scope_filters()
            }
        },
        "aggs": {"apps": {"terms": {"field": "application", "size": 200}}},
    }
    res_d = es_search(IDX["deployments"], body_d)
    prd_map = {b["key"]: b["doc_count"] for b in bucket_rows(res_d, "apps")}

    # JIRA open — per application (via project field, distributed to apps via inventory)
    body_j = {
        "query": {
            "bool": {
                "filter": scope_filters_inv(),
                "must_not": [{"terms": {"status": CLOSED_JIRA}}],
            }
        },
        "aggs": {"apps": {"terms": {"field": "project", "size": 500}}},
    }
    res_j = es_search(IDX["jira"], body_j)
    _jira_proj_raw = {b["key"]: b["doc_count"] for b in bucket_rows(res_j, "apps")}
    # Distribute to apps via inventory hierarchy
    jira_map: dict[str, int] = {}
    for _jp2, _jcnt2 in _jira_proj_raw.items():
        _japps2 = _tm_proj_to_apps.get(_jp2, [])
        if _japps2:
            _per = max(1, _jcnt2 // len(_japps2))
            for _ja2 in _japps2:
                jira_map[_ja2] = jira_map.get(_ja2, 0) + _per
        else:
            jira_map[_jp2] = jira_map.get(_jp2, 0) + _jcnt2

    # Pending requests — per application (unified across both request indices)
    _pend_agg = pending_unified_counts()  # defined below
    pend_map = _pend_agg

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
            "Application":  app,
            "Project":      _app_to_parent.get(app, "—"),
            "Deploy tech":  _app_deploy_tech.get(app, "—"),
            "Build tech":   _app_build_tech.get(app, "—"),
            "Builds":       total,
            "Fails":        fails,
            "Succ %":       f"{succ_pct:.0f}%",
            "Prod dep":     prd_map.get(app, 0),
            "Open JIRA":    jira_map.get(app, 0),
            "Pending req":  pend_map.get(app, 0),
            "Last build":   last,
            "Score":        score,
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
# Collapsed by default to reduce page density — expand to investigate.
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
                "Application":  app,
                "Project":      _app_to_parent.get(app, "—"),
                "Deploy tech":  _app_deploy_tech.get(app, "—"),
                "Signals":      " · ".join(flags),
                "Builds":       builds_t,
                "Fails":        fails_t,
                "JIRA":         oj,
                "Pending":      pr,
            })
    if risk_rows:
        with st.expander(f"⚠ Risk spotlight — {len(risk_rows)} application(s) failing multiple signals", expanded=False):
            st.dataframe(
                pd.DataFrame(risk_rows).sort_values("Fails", ascending=False).head(10),
                use_container_width=True,
                hide_index=True,
                height=260,
            )
    # If no risk rows, just skip silently (no need for a success banner — reduces clutter)
except Exception as exc:
    inline_note(f"Risk spotlight unavailable: {exc}", "info")


# =============================================================================
# SECTION 4 — PIPELINE ACTIVITY
# =============================================================================

st.markdown('<a class="anchor" id="sec-pipeline"></a>', unsafe_allow_html=True)
_pipe_hint = {
    "Developer": "your builds and CI activity",
    "Operator": "deployment pipelines and environment health",
    "QC": "deployment flow across environments",
    "Admin": "builds &amp; deployments over time",
}
st.markdown(
    f'<div class="section">'
    f'<div class="title-wrap"><h2>Pipeline activity</h2>'
    f'<span class="badge">{ROLE_ICONS[role_pick]} {role_pick}</span></div>'
    f'<span class="hint">{_pipe_hint.get(role_pick, "builds &amp; deployments over time")}</span>'
    f'</div>',
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
        _filter: list[dict] = [range_filter("startdate", start_dt, end_dt)] + build_scope_filters()
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
                    "When":        fmt_dt(_pick_date(_s, "build"), "%m-%d %H:%M"),
                    "Application": _s.get("application") or _s.get("project"),
                    "Project":     _s.get("project"),
                    "Branch":      _s.get("branch"),
                    "Status":      _s.get("status"),
                    "Version":     _s.get("codeversion"),
                    "Build tech":  _s.get("technology"),
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
        _filter = [range_filter("startdate", start_dt, end_dt)] + deploy_scope_filters()
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
                    "When":        fmt_dt(_pick_date(_s, "deploy"), "%m-%d %H:%M"),
                    "Application": _s.get("application") or _s.get("project"),
                    "Project":     _s.get("project"),
                    "Env":         _s.get("environment"),
                    "Status":      _s.get("status"),
                    "Version":     _s.get("codeversion"),
                    "Deploy tech": _s.get("technology"),
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
                "filter": [range_filter("startdate", start_dt, end_dt)] + build_scope_filters()
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
            "by_tech":  {"terms": {"field": "technology", "size": 10}},
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
            font=dict(color="#4a5068", family="system-ui, sans-serif"),
            xaxis=dict(gridcolor="#e3e6ee"),
            yaxis=dict(gridcolor="#e3e6ee"),
        )
        c1.plotly_chart(fig, use_container_width=True)
    else:
        inline_note("No builds in this window.", "info", c1)

    tops = bucket_rows(res, "top_apps")
    if tops:
        df_top = pd.DataFrame(
            [{"application": b["key"],
              "project": _app_to_parent.get(b["key"], "—"),
              "builds": b["doc_count"]} for b in tops]
        ).sort_values("builds")
        # Y-axis label: "app [project]"
        df_top["label"] = df_top.apply(
            lambda r: f'{r["application"]} [{r["project"]}]' if r["project"] != "—" else r["application"],
            axis=1,
        )
        fig2 = px.bar(
            df_top, x="builds", y="label", orientation="h",
            title="Top applications by build count",
            color_discrete_sequence=[C_ACCENT],
        )
        fig2.update_layout(
            height=380,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=40, b=0),
            font=dict(color="#4a5068", family="system-ui, sans-serif"),
            xaxis=dict(gridcolor="#e3e6ee"),
            yaxis=dict(gridcolor="#e3e6ee"),
        )
        c2.plotly_chart(fig2, use_container_width=True)
    else:
        inline_note("No application data.", "info", c2)

    tech = bucket_rows(res, "by_tech")
    if tech:
        df_tech = pd.DataFrame(
            [{"build_technology": b["key"], "builds": b["doc_count"]} for b in tech]
        )
        st.markdown("**By build technology**")
        st.dataframe(df_tech, use_container_width=True, hide_index=True)

# ---- Deployments tab -------------------------------------------------------
with tab_deploys:
    body = {
        "query": {
            "bool": {
                "filter": [range_filter("startdate", start_dt, end_dt)] + deploy_scope_filters()
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
            "by_tech": {"terms": {"field": "technology", "size": 15}},
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
            font=dict(color="#4a5068", family="system-ui, sans-serif"),
            xaxis=dict(gridcolor="#e3e6ee"),
            yaxis=dict(gridcolor="#e3e6ee"),
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

    dep_tech = bucket_rows(res, "by_tech")
    if dep_tech:
        df_dep_tech = pd.DataFrame(
            [{"deploy_technology": b["key"], "deployments": b["doc_count"]} for b in dep_tech]
        )
        st.markdown("**By deploy technology**")
        st.dataframe(df_dep_tech, use_container_width=True, hide_index=True)


# =============================================================================
# SECTION 5 — WORKFLOW PULSE + OPERATIONAL HYGIENE (consolidated)
# =============================================================================

st.markdown('<a class="anchor" id="sec-workflow"></a>', unsafe_allow_html=True)
_wf_role_hint = {
    "Admin": "all queues and cleanup candidates",
    "Developer": "your pending requests and commit activity",
    "QC": "release approval queue and quality metrics",
    "Operator": "deployment request queue and ops health",
}
st.markdown(
    f'<div class="section">'
    f'<div class="title-wrap"><h2>Workflow pulse &amp; hygiene</h2>'
    f'<span class="badge">{ROLE_ICONS[role_pick]} {role_pick}</span></div>'
    f'<span class="hint">{_wf_role_hint.get(role_pick, "live queues and cleanup candidates")}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

# ── Role-aware pending request split ────────────────────────────────────────
# Split pending items by stage so each role sees what matters to them.
_pending_deploy_reqs = [r for r in _all_pending if (r.get("Stage") or "").startswith("request_deploy_")]
_pending_release_reqs = [r for r in _all_pending if (r.get("Stage") or "") == "request_promote"]
_pending_other = [r for r in _all_pending if r not in _pending_deploy_reqs and r not in _pending_release_reqs]

# Running pipelines (build / deployment in progress)
_running_pipelines = _fetch_running_pipelines()

wp_top = st.columns(3)


# ---- Pending requests — role-contextualized (fragment for independent rerun) -
@st.fragment
def _render_pending_queue() -> None:
    if _effective_role == "Operator":
        st.markdown(f"**🚀 Deployment requests** — {len(_pending_deploy_reqs)} pending")
        _pend_rows = _pending_deploy_reqs[:12]
    elif _effective_role == "QC":
        st.markdown(f"**🔬 Release requests** — {len(_pending_release_reqs)} pending")
        _pend_rows = _pending_release_reqs[:12]
    elif _effective_role == "Developer":
        st.markdown(f"**⌨ Pending requests** — {len(_pending_other) + len(_pending_deploy_reqs)} items")
        _pend_rows = (_pending_other + _pending_deploy_reqs)[:12]
    else:  # Admin — all queues
        st.markdown(f"**🛡 All pending requests** — {len(_all_pending)} total")
        _pend_rows = _all_pending[:12]
    if _pend_rows:
        st.dataframe(
            pd.DataFrame([{
                "#":           r["#"],
                "Type":        r["Type"],
                "Stage":       r.get("Stage", "—"),
                "Requester":   r["Requester"],
                "Application": r["Application"],
                "Age (h)":     r["Age (h)"],
            } for r in _pend_rows]),
            use_container_width=True, hide_index=True, height=320,
        )
        st.caption(
            f"🚀 {len(_pending_deploy_reqs)} deploy request(s) · "
            f"🔬 {len(_pending_release_reqs)} release request(s) · "
            f"📋 {len(_pending_other)} other"
        )
    else:
        inline_note("No pending requests for your role.", "success")

    # Running pipelines popover (useful for all roles)
    if _running_pipelines:
        with st.popover(f"⚡ {len(_running_pipelines)} running pipeline(s)", use_container_width=True):
            st.dataframe(
                pd.DataFrame([{
                    "#":          r["#"],
                    "Type":       r["Type"],
                    "Application": r["Application"],
                    "Started":    fmt_dt(r["Date"], "%m-%d %H:%M") if r["Date"] else "—",
                } for r in _running_pipelines[:20]]),
                use_container_width=True, hide_index=True, height=360,
            )


with wp_top[0]:
    _render_pending_queue()

# ---- Top committers --------------------------------------------------------
with wp_top[1]:
    st.markdown("**Top committers**")
    body = {
        "query": {
            "bool": {
                "filter": [range_filter("commitdate", start_dt, end_dt)] + commit_scope_filters()
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
            color_discrete_sequence=["#dc2626", "#d97706", "#0d9488", "#2563eb", "#059669"],
        )
        fig.update_layout(
            height=320,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=0, r=0, t=10, b=0),
            font=dict(color="#4a5068", family="system-ui, sans-serif"),
            legend=dict(orientation="v", x=1.02, y=0.5),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        inline_note("No open JIRA issues.", "success")

# ---- Hygiene row (Admin, Operator — collapsed for Developer/QC) ------------
_show_hygiene = role_pick in ("Admin", "Operator")
with st.expander("🧹 Operational hygiene — dormant apps, stuck requests, aged JIRA",
                  expanded=_show_hygiene):
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
                "filter": [range_filter("startdate", ninety_ago, now_utc)] + build_scope_filters()
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

    # Requests stuck > 7d (both queues)
    with wp_bot[1]:
        st.markdown("**Requests stuck > 7 days** — both queues")
        _stuck7 = [r for r in _all_pending if (r.get("Age (h)") or 0) >= 7 * 24]
        if _stuck7:
            st.dataframe(
                pd.DataFrame([{
                    "#":           r["#"],
                    "Type":        r["Type"],
                    "Application": r["Application"],
                    "Age (d)":     (r.get("Age (h)") or 0) // 24,
                    "Queue":       r["_idx"],
                } for r in _stuck7[:12]]),
                use_container_width=True, hide_index=True, height=260,
            )
        else:
            inline_note("No long-running requests.", "success")

    # Aged JIRA issues — hidden for Operator role (not relevant)
    with wp_bot[2]:
        if _ROLE_SHOWS_JIRA.get(_effective_role, True):
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
        else:
            # Operator: replace JIRA with a deployment-focused summary
            st.markdown("**🚀 Recent PRD activity** — last 7d")
            _prd7 = es_count(IDX["deployments"], {"query": {"bool": {"filter": [
                range_filter("startdate", now_utc - timedelta(days=7), now_utc),
                {"term": {"environment": "prd"}},
            ] + scope_filters()}}})
            _uat7 = es_count(IDX["deployments"], {"query": {"bool": {"filter": [
                range_filter("startdate", now_utc - timedelta(days=7), now_utc),
                {"term": {"environment": "uat"}},
            ] + scope_filters()}}})
            st.metric("PRD deploys (7d)", f"{_prd7:,}")
            st.metric("UAT deploys (7d)", f"{_uat7:,}")


# =============================================================================
# SECTION 6 — EVENT LOG (inline, all event types, role-filtered, fragment)
# =============================================================================

# ── styling helpers — module-level so the fragment re-uses them cheaply ────
_TYPE_BADGE = {
    "build":   ('<span style="background:#eef2ff;color:#6366f1;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">BUILD</span>'),
    "deploy":  ('<span style="background:#dbeafe;color:#1d4ed8;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">DEPLOY</span>'),
    "release": ('<span style="background:#fce7f3;color:#be185d;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">RELEASE</span>'),
    "request": ('<span style="background:#fef3c7;color:#92400e;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">REQUEST</span>'),
    "commit":  ('<span style="background:#d1fae5;color:#065f46;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">COMMIT</span>'),
}
_STATUS_CHIP = {
    "SUCCESS": ('<span style="background:#059669;color:#fff;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">OK</span>'),
    "FAILED":  ('<span style="background:#dc2626;color:#fff;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">FAIL</span>'),
    "RUNNING": ('<span style="background:#d97706;color:#fff;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">RUN</span>'),
    "PENDING": ('<span style="background:#d97706;color:#fff;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">PEND</span>'),
}


def _status_chip(raw: str | None) -> str:
    if raw is None or raw == "":
        return ""
    up = (raw or "").upper()
    if up in _STATUS_CHIP:
        return _STATUS_CHIP[up]
    if any(f in up for f in ("FAIL", "ERROR", "ABORT")):
        return _STATUS_CHIP["FAILED"]
    if up in ("SUCCESS", "SUCCEEDED", "PASSED", "OK", "APPROVED"):
        return _STATUS_CHIP["SUCCESS"]
    if up in ("PENDING", "WAITING", "OPEN", "NEW"):
        return _STATUS_CHIP["PENDING"]
    return (f'<span style="background:var(--cc-surface2);color:var(--cc-text-dim);border-radius:4px;'
            f'padding:1px 7px;font-size:0.72rem;font-weight:600">{raw}</span>')


@st.fragment
def _render_event_log() -> None:
    """Inline event log — role-scoped types/envs/stages. Reruns independently."""
    # Role-allowed event type options
    _allowed_types = _ROLE_EVENT_TYPES.get(_effective_role, _ROLE_EVENT_TYPES["Admin"])
    _type_options = ["All"] + _allowed_types
    _allowed_envs = _ROLE_ENVS.get(_effective_role, _ROLE_ENVS["Admin"])
    _env_options = ["(all)"] + _allowed_envs

    _el_c1, _el_c2, _el_c3 = st.columns([1.5, 1.2, 1.2])
    with _el_c1:
        el_type = st.selectbox("Type", _type_options, key="el_type_v2")
    with _el_c2:
        # QC role only has one env — show it read-only
        if len(_env_options) == 2:
            el_env = _env_options[1]
            st.markdown(
                f'<div style="padding-top:6px;font-size:.68rem;text-transform:uppercase;'
                f'letter-spacing:.10em;color:var(--cc-text-mute);font-weight:600">Env</div>'
                f'<div style="font-size:.90rem;font-weight:600;color:var(--cc-text);'
                f'text-transform:uppercase">{el_env}</div>',
                unsafe_allow_html=True,
            )
        else:
            el_env = st.selectbox("Env", _env_options, key="el_env_v2")
    with _el_c3:
        el_limit = st.selectbox("Show", [50, 100, 250], key="el_limit_v2")

    events: list[dict] = []

    # ── builds (Developer/Admin) ────────────────────────────────────────────
    if el_type in ("All", "Builds") and _role_allows_type("Builds"):
        _bld_f = [range_filter("startdate", start_dt, end_dt)] + list(scope_filters())
        _bld_r = es_search(
            IDX["builds"],
            {"query": {"bool": {"filter": _bld_f}},
             "sort": [{"startdate": {"order": "desc", "unmapped_type": "date"}}]},
            size=int(el_limit),
        )
        for _h in _bld_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _dv = _pick_date(_s, "build")
            events.append({
                "_ts":     parse_dt(_dv),
                "type":    "build",
                "When":    fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":     _s.get("application") or _s.get("project", ""),
                "Version": _s.get("codeversion", ""),
                "Detail":  f'{_s.get("branch","")} · {_s.get("technology","")}',
                "Status":  _s.get("status", ""),
                "Extra":   _s.get("project", ""),
            })

    # ── deployments (role-filtered env) ─────────────────────────────────────
    if el_type in ("All", "Deployments") and _role_allows_type("Deployments"):
        _dep_f = [range_filter("startdate", start_dt, end_dt)] + list(scope_filters())
        if el_env != "(all)":
            _dep_f.append({"term": {"environment": el_env}})
        else:
            # Restrict to role-allowed envs
            _dep_f.append({"terms": {"environment": _allowed_envs}})
        _dep_r = es_search(
            IDX["deployments"],
            {"query": {"bool": {"filter": _dep_f}},
             "sort": [{"startdate": {"order": "desc", "unmapped_type": "date"}}]},
            size=int(el_limit),
        )
        for _h in _dep_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _dv = _pick_date(_s, "deploy")
            events.append({
                "_ts":     parse_dt(_dv),
                "type":    "deploy",
                "When":    fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":     _s.get("application") or _s.get("project", ""),
                "Version": _s.get("codeversion", ""),
                "Detail":  f'{_s.get("environment","?")} · {_s.get("technology","")} [{_s.get("project","")}]',
                "Status":  _s.get("status", ""),
                "Extra":   _s.get("triggeredby", ""),
            })

    # ── releases ────────────────────────────────────────────────────────────
    if el_type in ("All", "Releases") and _role_allows_type("Releases"):
        _rel_f = [range_filter("releasedate", start_dt, end_dt)] + list(scope_filters())
        _rel_r = es_search(
            IDX["releases"],
            {"query": {"bool": {"filter": _rel_f}},
             "sort": [{"releasedate": {"order": "desc", "unmapped_type": "date"}}]},
            size=int(el_limit),
        )
        for _h in _rel_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _dv = _pick_date(_s, "release")
            events.append({
                "_ts":     parse_dt(_dv),
                "type":    "release",
                "When":    fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":     _s.get("application", ""),
                "Version": _s.get("codeversion", ""),
                "Detail":  f'RLM: {_s.get("RLM_STATUS","")}',
                "Status":  _s.get("RLM_STATUS", ""),
                "Extra":   "",
            })

    # ── requests / approvals (role-filtered by stage) ───────────────────────
    if el_type in ("All", "Requests") and _role_allows_type("Requests"):
        # ef-devops-requests
        _rq_f = [range_filter("RequestDate", start_dt, end_dt)] + list(scope_filters())
        _rq_r = es_search(
            IDX["requests"],
            {"query": {"bool": {"filter": _rq_f}},
             "sort": [{"RequestDate": {"order": "desc", "unmapped_type": "date"}}]},
            size=int(el_limit),
        )
        for _h in _rq_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            # Role-filter by target environment if applicable
            _rq_env = (_s.get("TargetEnvironment") or _s.get("environment") or "").lower()
            if _rq_env and not _role_allows_env(_rq_env):
                continue
            _dv = _pick_date(_s, "request")
            events.append({
                "_ts":     parse_dt(_dv),
                "type":    "request",
                "When":    fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":     _s.get("application") or _s.get("project", ""),
                "Version": _s.get("codeversion", ""),
                "Detail":  f'{_s.get("RequestType","")} · {_s.get("Requester","")}',
                "Status":  _s.get("Status", ""),
                "Extra":   _s.get("RequestNumber") or _s.get("id") or "",
            })
        # ef-cicd-approval (stage-based, role-scoped)
        _ap_f: list[dict] = list(scope_filters())
        _ap_f.append({"bool": {"should": [
            range_filter("RequestDate", start_dt, end_dt),
            range_filter("Created", start_dt, end_dt),
            range_filter("CreatedDate", start_dt, end_dt),
        ], "minimum_should_match": 1}})
        _rsf = _role_stage_filter()
        if _rsf is not None:
            _ap_f.append(_rsf)
        _ap_r = es_search(
            IDX["approval"],
            {"query": {"bool": {"filter": _ap_f}},
             "sort": [{"RequestDate": {"order": "desc", "unmapped_type": "date"}}]},
            size=int(el_limit),
        )
        for _h in _ap_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _dv = _pick_date(_s, "request")
            _stage = _s.get("stage") or ""
            if _stage == "build":
                _detail = "Running build"
            elif _stage.startswith("request_deploy_"):
                _detail = f'Deploy request ({_stage.replace("request_deploy_", "")})'
            elif _stage == "request_promote":
                _detail = "Release request (promote)"
            elif _stage:
                _detail = f'Running deploy ({_stage})'
            else:
                _detail = _s.get("ApprovalType") or ""
            events.append({
                "_ts":     parse_dt(_dv),
                "type":    "request",
                "When":    fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":     _s.get("application") or _s.get("project", ""),
                "Version": _s.get("codeversion", ""),
                "Detail":  f'{_detail} · {_s.get("RequestedBy") or _s.get("Requester", "")}',
                "Status":  _stage or _s.get("Status", ""),
                "Extra":   _s.get("ApprovalId") or _s.get("id") or "",
            })

    # ── commits (Developer/Admin) ───────────────────────────────────────────
    if el_type in ("All", "Commits") and _role_allows_type("Commits"):
        _com_f = [range_filter("commitdate", start_dt, end_dt)] + list(commit_scope_filters())
        _com_r = es_search(
            IDX["commits"],
            {"query": {"bool": {"filter": _com_f}},
             "sort": [{"commitdate": {"order": "desc", "unmapped_type": "date"}}]},
            size=int(el_limit),
        )
        for _h in _com_r.get("hits", {}).get("hits", []):
            _s = _h["_source"]
            _dv = _pick_date(_s, "commit")
            events.append({
                "_ts":     parse_dt(_dv),
                "type":    "commit",
                "When":    fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":     _s.get("project", _s.get("repository", "")),
                "Version": "",
                "Detail":  f'{_s.get("branch","")} · {_s.get("authorname","")}',
                "Status":  "",
                "Extra":   (_s.get("commitmessage") or "")[:80],
            })

    # ── sort & render inline ────────────────────────────────────────────────
    events.sort(key=lambda e: e["_ts"] or pd.Timestamp("1970-01-01", tz="UTC"), reverse=True)
    events = events[:int(el_limit)]

    if not events:
        inline_note("No events match the current filters.", "info")
        return

    _rows_html = []
    for ev in events:
        _ver_cell = (
            f'<span style="font-family:var(--cc-mono);font-size:0.73rem;color:var(--cc-accent);'
            f'background:var(--cc-accent-lt);padding:1px 6px;border-radius:4px">{ev["Version"]}</span>'
            if ev.get("Version") else '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        )
        _rows_html.append(
            f"<tr>"
            f'<td style="white-space:nowrap;color:var(--cc-text-mute);font-size:0.78rem;padding:5px 4px">{ev["When"]}</td>'
            f'<td style="padding:5px 6px">{_TYPE_BADGE.get(ev["type"], "")}</td>'
            f'<td style="font-weight:600;color:var(--cc-text);font-size:0.82rem;padding:5px 4px">{ev["Who"]}</td>'
            f'<td style="padding:5px 4px">{_ver_cell}</td>'
            f'<td style="color:var(--cc-text-dim);font-size:0.8rem;padding:5px 4px">{ev["Detail"]}</td>'
            f'<td style="padding:5px 6px">{_status_chip(ev["Status"])}</td>'
            f'<td style="color:var(--cc-text-mute);font-size:0.75rem;max-width:220px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:5px 4px">{ev["Extra"]}</td>'
            f"</tr>"
        )
    _th_style = 'style="padding:6px 4px;color:var(--cc-text-mute);font-size:0.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase"'
    _table_html = (
        '<div style="overflow-y:auto;max-height:60vh;border:1px solid var(--cc-border);border-radius:10px">'
        '<table style="width:100%;border-collapse:collapse;font-family:inherit">'
        f'<thead><tr style="border-bottom:2px solid var(--cc-border);text-align:left;background:var(--cc-surface2)">'
        f'<th {_th_style}>Time</th>'
        f'<th {_th_style}>Type</th>'
        f'<th {_th_style}>Application</th>'
        f'<th {_th_style}>Artifact</th>'
        f'<th {_th_style}>Detail</th>'
        f'<th {_th_style}>Status</th>'
        f'<th {_th_style}>Note</th>'
        f'</tr></thead>'
        '<tbody>' + "".join(_rows_html) + "</tbody>"
        "</table></div>"
    )
    _type_counts: dict[str, int] = {}
    for ev in events:
        _type_counts[ev["type"]] = _type_counts.get(ev["type"], 0) + 1
    _type_summary = " · ".join(
        f"{_type_counts.get(t, 0)} {t}s"
        for t in ["build", "deploy", "release", "request", "commit"]
        if _type_counts.get(t, 0)
    )
    st.markdown(
        f'<p style="font-size:0.8rem;color:var(--cc-text-mute);margin:0 0 8px">'
        f'Showing {len(events)} events · {_type_summary} · sorted newest first</p>'
        + _table_html,
        unsafe_allow_html=True,
    )


if _show("eventlog"):
    st.markdown('<a class="anchor" id="sec-eventlog"></a>', unsafe_allow_html=True)
    _el_hint = {
        "Admin": "builds · deployments · releases · requests · commits",
        "Developer": "your builds, commits, and build-stage requests",
        "QC": "QC deployments, release requests, and releases",
        "Operator": "UAT/PRD deployments, deploy requests, and releases",
    }.get(_effective_role, "all event types")
    st.markdown(
        f'<div class="section">'
        f'<div class="title-wrap"><h2>Event log</h2><span class="badge">{ROLE_ICONS[_effective_role]} Live · {_effective_role}</span></div>'
        f'<span class="hint">{_el_hint} &mdash; newest first</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    _render_event_log()


# =============================================================================
# GLOSSARY
# =============================================================================

with st.expander("📖  Field guide · index reference · KPI formulas"):
    st.markdown(
        """
**ef-devops-inventory** — single source of truth for every application on the
CI/CD platform. Each document represents one application; `project.keyword`
names the parent project. Key fields: `build_technology`, `deploy_technology`.

**ef-cicd-builds** — one document per CI build (Jenkins / GitHub Actions run).
Important fields: `status`, `duration`, `branch`, `codeversion`, `technology`
(= inventory `build_technology`), `startdate`, `enddate`.

**ef-cicd-deployments** — one document per deployment attempt to an environment
(`dev`, `qc`, `uat`, `prd`). `technology` field = inventory `deploy_technology`.
Production deployments drive DORA metrics here.

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
