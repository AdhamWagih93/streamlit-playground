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
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore

# All user-facing timestamps render in this zone. Internal math/storage remain UTC.
DISPLAY_TZ = ZoneInfo("Africa/Cairo")
DISPLAY_TZ_LABEL = "Cairo"

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
    "prismacloud": "ef-cicd-prismacloud",
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
SUCCESS_STATUSES = ["SUCCESS", "SUCCEEDED", "Success", "Succeeded", "COMPLETED", "Completed"]
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
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=IBM+Plex+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

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

/* -------- Sticky unified filter rail -------- */
.st-key-cc_filter_rail {
    position: sticky;
    top: 0;
    z-index: 900;
    background: rgba(255, 255, 255, 0.88);
    backdrop-filter: saturate(140%) blur(10px);
    -webkit-backdrop-filter: saturate(140%) blur(10px);
    border: 1px solid var(--cc-border);
    border-radius: 14px;
    padding: 10px 14px 8px 14px;
    margin: 6px 0 14px 0;
    box-shadow: 0 6px 20px rgba(10, 14, 30, 0.06),
                0 1px 3px rgba(10, 14, 30, 0.04);
}
.st-key-cc_filter_rail [data-testid="stSelectbox"] label,
.st-key-cc_filter_rail [data-testid="stTextInput"] label,
.st-key-cc_filter_rail [data-testid="stToggle"] label {
    font-size: 0.62rem !important;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--cc-text-mute) !important;
    font-weight: 700 !important;
    margin-bottom: 2px !important;
}
.st-key-cc_filter_rail [data-testid="stSelectbox"] > div > div,
.st-key-cc_filter_rail [data-testid="stTextInput"] > div > div {
    min-height: 36px;
}
.cc-rail-id {
    display: flex; flex-direction: column; gap: 4px;
    padding: 2px 0 0 0;
}
.cc-rail-id-role {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 10px;
    border: 1px solid;
    border-radius: 999px;
    font-weight: 700; font-size: 0.78rem;
    letter-spacing: 0.02em;
    width: fit-content;
}
.cc-rail-id-team {
    font-size: 0.72rem;
    color: var(--cc-text-dim);
    font-weight: 500;
    max-width: 100%;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    padding-left: 2px;
}
.cc-rail-readonly {
    padding-top: 2px;
    font-size: 0.62rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 700;
}
.cc-rail-readonly em {
    display: block;
    font-style: normal;
    font-size: 0.82rem;
    letter-spacing: 0;
    text-transform: none;
    color: var(--cc-text-mute);
    font-weight: 400;
    margin-top: 2px;
}
.cc-rail-meta {
    font-size: 0.68rem;
    color: var(--cc-text-mute);
    letter-spacing: 0.04em;
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px dashed var(--cc-border);
    display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
}
.cc-rail-meta b { color: var(--cc-text-dim); font-weight: 700; }

/* -------- Inventory stats tiles (big numbers) -------- */
.iv-stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 10px;
    margin: 12px 0 14px 0;
}
.iv-stat {
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 12px;
    padding: 12px 14px;
    position: relative;
    overflow: hidden;
    transition: transform .15s ease, border-color .15s ease, box-shadow .15s ease;
}
.iv-stat::before {
    content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--iv-stat-accent, var(--cc-accent));
    opacity: .8;
}
.iv-stat:hover {
    transform: translateY(-1px);
    border-color: var(--iv-stat-accent, var(--cc-accent));
    box-shadow: 0 6px 18px rgba(10, 14, 30, 0.06);
}
.iv-stat-label {
    font-size: .66rem;
    letter-spacing: .09em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 700;
    margin-bottom: 6px;
    display: flex; align-items: center; gap: 6px;
}
.iv-stat-label .iv-stat-glyph {
    font-size: .85rem;
    color: var(--iv-stat-accent, var(--cc-accent));
}
.iv-stat-number {
    font-size: 1.85rem;
    font-weight: 800;
    line-height: 1;
    color: var(--cc-text);
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
}
.iv-stat-sub {
    margin-top: 6px;
    font-size: .7rem;
    color: var(--cc-text-dim);
    font-weight: 500;
    line-height: 1.35;
}
.iv-stat-sub b {
    color: var(--iv-stat-accent, var(--cc-accent));
    font-weight: 700;
}

/* -------- Inventory dimensional filters -------- */
.iv-pill-caption {
    font-size: 0.66rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 700;
    margin: 6px 0 4px 0;
}
.iv-active-chips {
    display: flex; flex-wrap: wrap; gap: 6px;
    align-items: center;
    padding: 2px 4px;
}
.iv-active-chip {
    display: inline-flex; align-items: center;
    padding: 3px 10px;
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    border: 1px solid rgba(79, 70, 229, 0.25);
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.005em;
}
.iv-active-chip.iv-active-chip-sess {
    background: var(--cc-surface2);
    color: var(--cc-text-dim);
    border-color: var(--cc-border-hi);
    font-style: italic;
}
.iv-active-chip.iv-active-chip-sort {
    background: transparent;
    color: var(--cc-text-mute);
    border-color: var(--cc-border);
    font-weight: 500;
}
.iv-filter-hint {
    font-size: 0.74rem;
    color: var(--cc-text-mute);
    font-style: italic;
    padding: 4px 6px;
}

/* -------- Primary panel header (replaces expanders) -------- */
.cc-panel-head {
    display: flex; align-items: baseline; justify-content: space-between;
    gap: 12px;
    margin: 18px 0 8px 0;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--cc-border);
}
.cc-panel-head h2 {
    margin: 0;
    font-size: 1.05rem;
    font-weight: 800;
    letter-spacing: -0.015em;
    color: var(--cc-text);
}
.cc-panel-head .cc-panel-tag {
    font-size: 0.66rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 700;
}
.cc-panel-sub {
    font-size: 0.76rem;
    color: var(--cc-text-mute);
    margin: -4px 0 10px 0;
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

/* -------- Event-log application popover — native [popover] API -------- */
.el-app-trigger {
    all: unset;
    cursor: pointer;
    font-weight: 600;
    color: var(--cc-text);
    font-size: 0.82rem;
    border-bottom: 1px dashed var(--cc-text-mute);
    padding: 0 2px;
    transition: color .12s, border-color .12s;
}
.el-app-trigger:hover {
    color: var(--cc-accent);
    border-bottom-color: var(--cc-accent);
}
.el-app-trigger:focus-visible {
    outline: 2px solid var(--cc-accent);
    outline-offset: 2px;
    border-radius: 2px;
}

/* Native popover element — unaffected by parent overflow:hidden */
.el-app-pop {
    /* start with no box defaults from UA */
    border: none;
    padding: 0;
    margin: 0;
    background: transparent;
    /* visible styling */
    width: min(420px, 92vw);
    border-radius: 14px;
    overflow: hidden;
    box-shadow:
        0 1px 2px rgba(26, 29, 46, .05),
        0 20px 50px -10px rgba(26, 29, 46, .25),
        0 0 0 1px rgba(79, 70, 229, .08);
    color: var(--cc-text);
    font-family: var(--cc-sans);
    /* subtle fade-in */
    animation: el-pop-in .18s ease-out;
}
.el-app-pop::backdrop {
    background: rgba(26, 29, 46, 0.28);
    backdrop-filter: blur(3px);
    -webkit-backdrop-filter: blur(3px);
}
@keyframes el-pop-in {
    from { opacity: 0; transform: translateY(6px) scale(.98); }
    to   { opacity: 1; transform: translateY(0)  scale(1); }
}
.el-app-pop .ap-head {
    position: relative;
    padding: 18px 20px 14px;
    background:
        radial-gradient(120% 120% at 0% 0%, rgba(79,70,229,.14), transparent 60%),
        linear-gradient(135deg, #ffffff, #fafbff);
    border-bottom: 1px solid var(--cc-border);
    display: flex; align-items: center; gap: 12px;
}
.el-app-pop .ap-icon {
    width: 36px; height: 36px;
    border-radius: 10px;
    display: flex; align-items: center; justify-content: center;
    background: linear-gradient(135deg, var(--cc-accent), #7c3aed);
    color: #fff; font-size: 1.1rem;
    box-shadow: 0 6px 16px -4px rgba(79, 70, 229, .4);
    flex-shrink: 0;
}
.el-app-pop .ap-title-wrap { flex: 1; min-width: 0; }
.el-app-pop .ap-kicker {
    font-size: .64rem; font-weight: 700; letter-spacing: .12em;
    text-transform: uppercase; color: var(--cc-accent);
}
.el-app-pop .ap-title {
    font-size: 1.02rem; font-weight: 700; color: var(--cc-text);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    margin-top: 1px;
}
.el-app-pop .ap-close {
    all: unset; cursor: pointer;
    width: 28px; height: 28px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    color: var(--cc-text-mute); font-size: 1.3rem; line-height: 1;
    transition: background .12s, color .12s;
}
.el-app-pop .ap-close:hover {
    background: var(--cc-surface2); color: var(--cc-red);
}
.el-app-pop .ap-body {
    background: var(--cc-surface);
    padding: 14px 18px 18px;
    display: grid;
    grid-template-columns: minmax(120px, max-content) 1fr;
    gap: 10px 16px;
    font-size: .85rem;
}
.el-app-pop .ap-section {
    grid-column: 1 / -1;
    font-size: .64rem; font-weight: 700; letter-spacing: .1em;
    text-transform: uppercase; color: var(--cc-text-mute);
    margin: 6px 0 -2px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--cc-border);
}
.el-app-pop .ap-section:first-child { margin-top: 0; }
.el-app-pop .ap-k {
    color: var(--cc-text-mute);
    font-size: .74rem; font-weight: 600; letter-spacing: .04em;
    padding-top: 2px;
}
.el-app-pop .ap-v {
    color: var(--cc-text);
    font-family: var(--cc-mono);
    font-size: .78rem;
    word-break: break-word;
}
.el-app-pop .ap-v.empty {
    color: var(--cc-text-mute);
    font-family: var(--cc-sans);
    font-style: italic;
}
.el-app-pop .ap-chip {
    display: inline-block;
    padding: 2px 8px;
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    border-radius: 5px;
    font-family: var(--cc-mono);
    font-size: .72rem;
    font-weight: 600;
}
.el-app-pop .ap-foot {
    background: var(--cc-surface2);
    padding: 8px 18px;
    font-size: .68rem;
    color: var(--cc-text-mute);
    border-top: 1px solid var(--cc-border);
    text-align: right;
}

/* -------- Project popover — reuses .el-app-pop skeleton with a teal accent -------- */
.el-app-pop.is-project .ap-head {
    background:
        radial-gradient(120% 120% at 0% 0%, rgba(5,150,105,.14), transparent 60%),
        linear-gradient(135deg, #ffffff, #f5fbf8);
}
.el-app-pop.is-project .ap-icon {
    background: linear-gradient(135deg, #059669, #0d9488);
    box-shadow: 0 6px 16px -4px rgba(5,150,105,.45);
}
.el-app-pop.is-project .ap-kicker { color: #059669; }
.el-app-pop.is-project {
    box-shadow:
        0 1px 2px rgba(26,29,46,.05),
        0 20px 50px -10px rgba(26,29,46,.25),
        0 0 0 1px rgba(5,150,105,.12);
}

/* Applications grid inside a project popover — spans the full row  */
.el-app-pop .ap-applist {
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    padding-top: 2px;
}
.el-app-pop .ap-applist:empty::after {
    content: "no applications in inventory";
    font-family: var(--cc-sans);
    font-style: italic;
    color: var(--cc-text-mute);
    font-size: .76rem;
}
.el-app-pop .ap-app-chip {
    all: unset;
    cursor: pointer;
    display: inline-block;
    padding: 3px 9px;
    background: var(--cc-surface2);
    color: var(--cc-text);
    border: 1px solid var(--cc-border);
    border-radius: 6px;
    font-family: var(--cc-mono);
    font-size: .74rem;
    font-weight: 600;
    transition: border-color .12s, color .12s, background .12s, transform .12s;
}
.el-app-pop .ap-app-chip:hover {
    border-color: var(--cc-accent);
    color: var(--cc-accent);
    background: var(--cc-accent-lt);
    transform: translateY(-1px);
}
.el-app-pop .ap-app-chip:focus-visible {
    outline: 2px solid var(--cc-accent);
    outline-offset: 2px;
}
.el-app-pop .ap-app-chip.static {
    cursor: default;
    color: var(--cc-text-mute);
}
.el-app-pop .ap-app-chip.static:hover {
    border-color: var(--cc-border);
    color: var(--cc-text-mute);
    background: var(--cc-surface2);
    transform: none;
}

/* -------- Version popover — amber accent for the “where is it live?” lens -------- */
.el-app-pop.is-version .ap-head {
    background:
        radial-gradient(120% 120% at 0% 0%, rgba(217,119,6,.14), transparent 60%),
        linear-gradient(135deg, #ffffff, #fffaf0);
}
.el-app-pop.is-version .ap-icon {
    background: linear-gradient(135deg, #d97706, #b45309);
    box-shadow: 0 6px 16px -4px rgba(217,119,6,.45);
}
.el-app-pop.is-version .ap-kicker { color: #b45309; }
.el-app-pop.is-version {
    box-shadow:
        0 1px 2px rgba(26,29,46,.05),
        0 20px 50px -10px rgba(26,29,46,.25),
        0 0 0 1px rgba(217,119,6,.12);
}

/* Live / offline status banner inside the version popover  */
.el-app-pop .ap-live {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 10px;
    font-size: .82rem;
    font-weight: 600;
    margin-top: -4px;
}
.el-app-pop .ap-live.is-live {
    background: rgba(5,150,105,.08);
    color: #047857;
    border: 1px solid rgba(5,150,105,.25);
}
.el-app-pop .ap-live.is-offline {
    background: rgba(220,38,38,.06);
    color: #b91c1c;
    border: 1px solid rgba(220,38,38,.22);
}
.el-app-pop .ap-live .dot {
    width: 9px; height: 9px; border-radius: 50%;
    box-shadow: 0 0 0 3px rgba(255,255,255,.6);
}
.el-app-pop .ap-live.is-live .dot {
    background: #10b981;
    animation: ap-pulse 1.8s ease-in-out infinite;
}
.el-app-pop .ap-live.is-offline .dot { background: #dc2626; }
@keyframes ap-pulse {
    0%,100% { box-shadow: 0 0 0 3px rgba(16,185,129,.25); }
    50%     { box-shadow: 0 0 0 6px rgba(16,185,129,.05); }
}

/* Prismacloud severity strip — four tiles (critical / high / medium / low) */
.el-app-pop .ap-sev {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    margin-top: 2px;
}
.el-app-pop .ap-sev-tile {
    position: relative;
    border-radius: 10px;
    padding: 9px 8px 8px;
    text-align: center;
    background: var(--cc-surface2);
    border: 1px solid var(--cc-border);
    overflow: hidden;
    transition: transform .14s;
}
.el-app-pop .ap-sev-tile::before {
    content: "";
    position: absolute; left: 0; top: 0; bottom: 0;
    width: 3px;
    background: var(--sev-accent, var(--cc-border));
}
.el-app-pop .ap-sev-tile .sev-num {
    font-family: var(--cc-mono);
    font-size: 1.15rem; font-weight: 800;
    color: var(--sev-accent, var(--cc-text));
    line-height: 1;
    letter-spacing: -.02em;
}
.el-app-pop .ap-sev-tile .sev-label {
    font-size: .58rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .11em;
    color: var(--cc-text-mute);
    margin-top: 4px;
}
.el-app-pop .ap-sev-tile .sev-delta {
    font-family: var(--cc-mono);
    font-size: .62rem;
    font-weight: 700;
    margin-top: 3px;
    letter-spacing: 0;
}
.el-app-pop .ap-sev-tile .sev-delta.up   { color: #b91c1c; }
.el-app-pop .ap-sev-tile .sev-delta.down { color: #047857; }
.el-app-pop .ap-sev-tile .sev-delta.eq   { color: var(--cc-text-mute); }
.el-app-pop .ap-sev-tile.critical { --sev-accent: #dc2626; }
.el-app-pop .ap-sev-tile.critical.nonzero {
    background: linear-gradient(180deg, rgba(220,38,38,.10), rgba(220,38,38,.04));
    border-color: rgba(220,38,38,.35);
    box-shadow: 0 0 0 3px rgba(220,38,38,.06);
    animation: ap-crit-glow 2.2s ease-in-out infinite;
}
.el-app-pop .ap-sev-tile.high    { --sev-accent: #d97706; }
.el-app-pop .ap-sev-tile.high.nonzero {
    background: linear-gradient(180deg, rgba(217,119,6,.09), rgba(217,119,6,.03));
    border-color: rgba(217,119,6,.32);
}
.el-app-pop .ap-sev-tile.medium  { --sev-accent: #ca8a04; }
.el-app-pop .ap-sev-tile.medium.nonzero {
    background: linear-gradient(180deg, rgba(202,138,4,.07), rgba(202,138,4,.02));
    border-color: rgba(202,138,4,.25);
}
.el-app-pop .ap-sev-tile.low     { --sev-accent: #475569; }
.el-app-pop .ap-sev-tile.low.nonzero {
    background: linear-gradient(180deg, rgba(71,85,105,.06), transparent);
}
@keyframes ap-crit-glow {
    0%, 100% { box-shadow: 0 0 0 3px rgba(220,38,38,.06); }
    50%      { box-shadow: 0 0 0 6px rgba(220,38,38,.03); }
}

.el-app-pop .ap-sev-subhead {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: .66rem;
    font-weight: 700;
    letter-spacing: .11em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    margin-top: 4px;
}
.el-app-pop .ap-sev-subhead .sev-sum {
    font-family: var(--cc-mono);
    font-weight: 700;
    letter-spacing: 0;
    text-transform: none;
    color: var(--cc-text-dim);
}
.el-app-pop .ap-sev-empty {
    grid-column: 1 / -1;
    padding: 12px;
    font-size: .78rem;
    color: var(--cc-text-mute);
    font-style: italic;
    text-align: center;
    background: var(--cc-surface2);
    border: 1px dashed var(--cc-border);
    border-radius: 8px;
}

.el-app-pop .ap-compare-head {
    grid-column: 1 / -1;
    display: flex; align-items: baseline; gap: 6px;
    margin-top: 10px; padding-top: 8px;
    border-top: 1px dashed var(--cc-border);
    font-size: .66rem;
    font-weight: 700;
    letter-spacing: .11em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
}
.el-app-pop .ap-compare-head .cmp-pill {
    font-family: var(--cc-mono);
    font-size: .68rem;
    letter-spacing: 0;
    text-transform: none;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 4px;
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
}

/* Version trigger in the event-log Version column — chip-styled button  */
.el-ver-trigger {
    all: unset;
    cursor: pointer;
    font-family: var(--cc-mono);
    font-size: 0.73rem;
    color: var(--cc-accent);
    background: var(--cc-accent-lt);
    padding: 1px 6px;
    border-radius: 4px;
    font-weight: 600;
    border: 1px solid transparent;
    transition: border-color .12s, color .12s, background .12s;
}
.el-ver-trigger:hover {
    border-color: #d97706;
    color: #b45309;
    background: rgba(217,119,6,.10);
}
.el-ver-trigger:focus-visible {
    outline: 2px solid #d97706;
    outline-offset: 2px;
}

/* Project trigger in the event-log Project column  */
.el-proj-trigger {
    all: unset;
    cursor: pointer;
    color: var(--cc-text-dim);
    font-size: 0.78rem;
    font-weight: 500;
    border-bottom: 1px dotted var(--cc-text-mute);
    padding: 0 2px;
    transition: color .12s, border-color .12s;
}
.el-proj-trigger:hover {
    color: #059669;
    border-bottom-color: #059669;
}
.el-proj-trigger:focus-visible {
    outline: 2px solid #059669;
    outline-offset: 2px;
    border-radius: 2px;
}

/* ── Per-project event-log sections ──────────────────────────────────────── */
.el-proj-stack {
    display: flex;
    flex-direction: column;
    gap: 14px;
}
.el-proj-section {
    border: 1px solid var(--cc-border);
    border-radius: 12px;
    background: linear-gradient(180deg, var(--cc-surface2) 0%, transparent 42%);
    padding: 10px 12px 12px;
    position: relative;
}
.el-proj-section::before {
    content: "";
    position: absolute;
    left: 0; top: 14px; bottom: 14px;
    width: 3px;
    border-radius: 2px;
    background: linear-gradient(180deg, #059669 0%, #0ea5e9 100%);
}
.el-proj-section-head {
    display: flex;
    align-items: baseline;
    gap: 10px;
    padding: 4px 2px 10px 8px;
    border-bottom: 1px dashed var(--cc-border);
    margin-bottom: 8px;
}
.el-proj-section-kicker {
    text-transform: uppercase;
    letter-spacing: .14em;
    font-size: 0.62rem;
    font-weight: 800;
    color: var(--cc-text-mute);
}
.el-proj-section-title {
    font-size: 0.98rem;
    font-weight: 700;
    color: var(--cc-text);
    letter-spacing: -.005em;
}
.el-proj-section-title .el-proj-trigger {
    font-size: 0.98rem;
    font-weight: 700;
    color: var(--cc-text);
    border-bottom: 2px solid transparent;
}
.el-proj-section-title .el-proj-trigger:hover {
    color: #047857;
    border-bottom-color: #059669;
}
.el-proj-section-count {
    margin-left: auto;
    font-family: var(--cc-mono);
    font-size: 0.70rem;
    font-weight: 700;
    color: var(--cc-text-dim);
    background: var(--cc-accent-lt);
    padding: 2px 8px;
    border-radius: 999px;
    letter-spacing: .03em;
}

/* ── Event-log stats + type-pill filter card ─────────────────────────────── */
.el-typefilter-head {
    display: flex;
    align-items: stretch;
    gap: 20px;
    background:
        radial-gradient(circle at top right, var(--cc-accent-lt), transparent 55%),
        linear-gradient(135deg, var(--cc-surface2) 0%, var(--cc-surface) 100%);
    border: 1px solid var(--cc-border);
    border-radius: 14px;
    padding: 14px 18px;
    margin: 4px 0 10px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 1px 2px rgba(0,0,0,.04), 0 4px 18px -8px rgba(0,0,0,.12);
}
.el-typefilter-head::after {
    content: "";
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 4px;
    background: linear-gradient(180deg, var(--cc-accent) 0%, #0ea5e9 100%);
    border-radius: 4px 0 0 4px;
}
.el-tf-left {
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 0 18px 0 6px;
    border-right: 1px dashed var(--cc-border);
    min-width: 140px;
}
.el-tf-total {
    font-size: 2.4rem;
    font-weight: 800;
    color: var(--cc-text);
    font-family: var(--cc-mono);
    line-height: 1;
    letter-spacing: -0.03em;
}
.el-tf-total-label {
    margin-top: 6px;
    font-size: 0.66rem;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--cc-text-mute);
    font-weight: 700;
}
.el-tf-mid {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: center;
    min-width: 0;
}
.el-tf-kicker {
    font-size: 0.70rem;
    text-transform: uppercase;
    letter-spacing: 0.18em;
    color: var(--cc-accent);
    font-weight: 800;
}
.el-tf-hint {
    margin-top: 3px;
    font-size: 0.78rem;
    color: var(--cc-text-dim);
    line-height: 1.35;
}
.el-tf-right {
    display: flex;
    flex-direction: column;
    gap: 6px;
    justify-content: center;
    align-items: flex-end;
    min-width: 120px;
}
.el-tf-badge {
    font-family: var(--cc-mono);
    font-size: 0.64rem;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    padding: 3px 11px;
    border-radius: 999px;
    white-space: nowrap;
}
.el-tf-badge.layout {
    background: var(--cc-accent);
    color: #fff;
    box-shadow: 0 2px 6px -1px rgba(0,0,0,0.15);
}
.el-tf-badge.sort {
    background: var(--cc-surface2);
    color: var(--cc-text-mute);
    border: 1px solid var(--cc-border);
}

/* Polish Streamlit's st.pills so it reads like a deliberate pill bar    */
/* and responds to hover / selected states with our accent palette.     */
div[data-testid="stPills"],
div[data-testid="stPillsContainer"] {
    margin: -4px 0 6px;
}
div[data-testid="stPills"] button,
div[data-testid="stPillsContainer"] button {
    font-family: var(--cc-mono) !important;
    font-weight: 700 !important;
    font-size: 0.78rem !important;
    letter-spacing: 0.01em !important;
    border-radius: 999px !important;
    padding: 5px 14px !important;
    border: 1px solid var(--cc-border) !important;
    background: var(--cc-surface) !important;
    color: var(--cc-text-dim) !important;
    transition: transform .14s ease, box-shadow .14s ease,
                background .14s ease, color .14s ease, border-color .14s ease !important;
}
div[data-testid="stPills"] button:hover,
div[data-testid="stPillsContainer"] button:hover {
    transform: translateY(-1px);
    border-color: var(--cc-accent) !important;
    color: var(--cc-accent) !important;
    box-shadow: 0 4px 12px -4px rgba(0,0,0,0.18);
}
div[data-testid="stPills"] button[aria-pressed="true"],
div[data-testid="stPillsContainer"] button[aria-pressed="true"],
div[data-testid="stPills"] button[data-selected="true"],
div[data-testid="stPillsContainer"] button[data-selected="true"] {
    background: linear-gradient(135deg, var(--cc-accent) 0%, #0ea5e9 100%) !important;
    color: #fff !important;
    border-color: transparent !important;
    box-shadow: 0 3px 10px -2px rgba(14,165,233,0.5) !important;
}

/* Caption under the pill row */
.el-tf-caption {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.76rem;
    color: var(--cc-text-mute);
    margin: 2px 0 8px;
    padding-left: 4px;
}
.el-tf-caption-count {
    font-family: var(--cc-mono);
    font-weight: 700;
    color: var(--cc-text-dim);
    background: var(--cc-accent-lt);
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.70rem;
    letter-spacing: 0.04em;
}
.el-tf-caption-sep { color: var(--cc-border); font-weight: 700; }
.el-tf-caption b { color: var(--cc-accent); font-weight: 700; }

/* ── Inventory stage cell — version chip + date stacked vertically ─────── */
.iv-stage-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;
    align-items: flex-start;
    line-height: 1.15;
}
.iv-stage-ver {
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    border: 1px solid rgba(79,70,229,.22);
    border-radius: 4px;
    padding: 1px 7px;
    font-family: var(--cc-mono);
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.01em;
    cursor: pointer;
    transition: background .12s, color .12s, border-color .12s, transform .12s;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.iv-stage-ver:hover {
    background: var(--cc-accent);
    color: #fff;
    border-color: var(--cc-accent);
    transform: translateY(-1px);
}
.iv-stage-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--cc-green);
    box-shadow: 0 0 4px var(--cc-green);
    flex-shrink: 0;
}
.iv-stage-dot.is-fail {
    background: var(--cc-red);
    box-shadow: none;
}
.iv-stage-when {
    font-family: var(--cc-mono);
    font-size: 0.64rem;
    color: var(--cc-text-mute);
    font-weight: 500;
    letter-spacing: 0.02em;
    white-space: nowrap;
}
.iv-stage-rel {
    color: var(--cc-text-dim);
    font-weight: 600;
}
/* Inline per-row prisma posture chips (sit under the app name) */
.iv-app-cell {
    display: flex;
    flex-direction: column;
    gap: 3px;
    align-items: flex-start;
    line-height: 1.15;
}
.iv-sec-row {
    display: inline-flex;
    gap: 4px;
    flex-wrap: wrap;
    align-items: center;
}
.iv-sec-chip {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-family: var(--cc-mono);
    font-size: 0.60rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding: 1px 5px;
    border-radius: 3px;
    border: 1px solid transparent;
    cursor: help;
    line-height: 1.25;
}
.iv-sec-chip .iv-sec-label {
    opacity: 0.65;
    font-weight: 800;
    margin-right: 1px;
}
.iv-sec-chip.iv-sec-crit {
    background: rgba(220, 38, 38, 0.12);
    color: #b91c1c;
    border-color: rgba(220, 38, 38, 0.28);
}
.iv-sec-chip.iv-sec-high {
    background: rgba(234, 88, 12, 0.10);
    color: #c2410c;
    border-color: rgba(234, 88, 12, 0.25);
}
.iv-sec-chip.iv-sec-med {
    background: rgba(217, 119, 6, 0.08);
    color: #a16207;
    border-color: rgba(217, 119, 6, 0.22);
}
.iv-sec-chip.iv-sec-low {
    background: rgba(101, 163, 13, 0.08);
    color: #4d7c0f;
    border-color: rgba(101, 163, 13, 0.20);
}
.iv-sec-chip.iv-sec-clean {
    background: rgba(5, 150, 105, 0.06);
    color: #047857;
    border-color: rgba(5, 150, 105, 0.20);
}
.iv-sec-chip.iv-sec-na {
    background: var(--cc-surface2);
    color: var(--cc-text-mute);
    border-color: var(--cc-border);
    opacity: 0.75;
}
/* Aggregate posture strip — subtle full-width ribbon above the table */
.iv-posture-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    padding: 8px 14px;
    margin: 8px 0 10px 0;
    border-radius: 8px;
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-left-width: 3px;
    font-size: 0.78rem;
}
.iv-posture-strip.is-crit   { border-left-color: #b91c1c; }
.iv-posture-strip.is-high   { border-left-color: #c2410c; }
.iv-posture-strip.is-med    { border-left-color: #a16207; }
.iv-posture-strip.is-low    { border-left-color: #4d7c0f; }
.iv-posture-strip.is-clean  { border-left-color: #047857; }
.iv-posture-strip.is-na     { border-left-color: var(--cc-border); }
.iv-ps-label {
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.iv-ps-glyph {
    font-size: 0.95rem;
    line-height: 1;
}
.iv-ps-glyph.is-crit  { color: #b91c1c; }
.iv-ps-glyph.is-high  { color: #c2410c; }
.iv-ps-glyph.is-med   { color: #a16207; }
.iv-ps-glyph.is-low   { color: #4d7c0f; }
.iv-ps-glyph.is-clean { color: #047857; }
.iv-ps-glyph.is-na    { color: var(--cc-text-mute); }
.iv-ps-group {
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.iv-ps-kicker {
    font-size: 0.62rem;
    font-weight: 800;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--cc-text-dim);
}
.iv-ps-tier {
    font-family: var(--cc-mono);
    font-size: 0.72rem;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 3px;
    background: var(--cc-surface2);
    color: var(--cc-text-dim);
    border: 1px solid var(--cc-border);
}
.iv-ps-tier.is-crit  { color: #b91c1c; background: rgba(220, 38, 38, 0.10); border-color: rgba(220, 38, 38, 0.25); }
.iv-ps-tier.is-high  { color: #c2410c; background: rgba(234, 88, 12, 0.08); border-color: rgba(234, 88, 12, 0.22); }
.iv-ps-tier.is-med   { color: #a16207; background: rgba(217, 119, 6, 0.07); border-color: rgba(217, 119, 6, 0.20); }
.iv-ps-tier.is-low   { color: #4d7c0f; background: rgba(101, 163, 13, 0.07); border-color: rgba(101, 163, 13, 0.18); }
.iv-ps-tier.is-zero  { opacity: 0.55; }
.iv-ps-coverage {
    margin-left: auto;
    font-size: 0.68rem;
    color: var(--cc-text-mute);
    font-weight: 600;
    letter-spacing: 0.04em;
}
/* Project-health ribbon — one subtle chip per project, colored by the worst
 * security tier across its applications. Replaces the old landscape treemap
 * with a compact always-visible alternative that sits above the inventory
 * table. */
.iv-proj-ribbon {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin: 0 0 8px 0;
    padding: 0;
}
.iv-proj-ribbon .iv-pr-lbl {
    font-size: 0.62rem;
    color: var(--cc-text-mute);
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    margin-right: 2px;
}
.iv-proj-ribbon .iv-pr-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 10px;
    border-radius: 14px;
    font-size: 0.72rem;
    font-weight: 600;
    background: var(--cc-surface2);
    color: var(--cc-text-dim);
    border: 1px solid var(--cc-border);
    cursor: pointer;
    transition: transform .12s ease, box-shadow .12s ease;
}
.iv-proj-ribbon .iv-pr-chip:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 6px rgba(15, 23, 42, 0.08);
}
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    display: inline-block;
    flex: none;
}
.iv-proj-ribbon .iv-pr-chip .iv-pr-n {
    font-size: 0.64rem;
    font-weight: 500;
    opacity: 0.65;
    margin-left: 2px;
}
.iv-proj-ribbon .iv-pr-chip.is-crit  { border-color:#fecaca; background:#fef2f2; color:#991b1b; }
.iv-proj-ribbon .iv-pr-chip.is-high  { border-color:#fed7aa; background:#fff7ed; color:#9a3412; }
.iv-proj-ribbon .iv-pr-chip.is-med   { border-color:#fde68a; background:#fffbeb; color:#854d0e; }
.iv-proj-ribbon .iv-pr-chip.is-low   { border-color:#bbf7d0; background:#f0fdf4; color:#166534; }
.iv-proj-ribbon .iv-pr-chip.is-clean { border-color:#d1fae5; background:#ecfdf5; color:#065f46; }
.iv-proj-ribbon .iv-pr-chip.is-na    { border-color:var(--cc-border); background:var(--cc-surface2); color:var(--cc-text-mute); }
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot.is-crit  { background:#dc2626; }
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot.is-high  { background:#ea580c; }
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot.is-med   { background:#d97706; }
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot.is-low   { background:#65a30d; }
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot.is-clean { background:#10b981; }
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot.is-na    { background:var(--cc-text-mute); }
/* "Not needed" — positive chip for Lib apps' post-build stages */
.iv-stage-nn {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-family: var(--cc-mono);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--cc-green);
    background: color-mix(in srgb, var(--cc-green) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--cc-green) 35%, transparent);
    border-radius: 3px;
    padding: 2px 6px;
    white-space: nowrap;
}
/* "Not reached" — subtle warning for App apps that haven't hit this stage */
.iv-stage-gap {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-family: var(--cc-mono);
    font-size: 0.66rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    color: var(--cc-amber, #d97706);
    background: color-mix(in srgb, var(--cc-amber, #d97706) 8%, transparent);
    border: 1px dashed color-mix(in srgb, var(--cc-amber, #d97706) 45%, transparent);
    border-radius: 3px;
    padding: 2px 6px;
    white-space: nowrap;
    opacity: 0.9;
}
/* app_type pill — distinguishes App vs Lib in identity section */
.ap-type-pill {
    display: inline-flex;
    align-items: center;
    font-family: var(--cc-mono);
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid transparent;
}
.ap-type-pill.is-app {
    color: var(--cc-blue, #3b82f6);
    background: color-mix(in srgb, var(--cc-blue, #3b82f6) 12%, transparent);
    border-color: color-mix(in srgb, var(--cc-blue, #3b82f6) 35%, transparent);
}
.ap-type-pill.is-lib {
    color: #8b5cf6;
    background: color-mix(in srgb, #8b5cf6 12%, transparent);
    border-color: color-mix(in srgb, #8b5cf6 40%, transparent);
}
.ap-type-pill.is-other {
    color: var(--cc-text-dim);
    background: var(--cc-surface2);
    border-color: var(--cc-border);
}

/* ==========================================================================
   PRECISION OPS TERMINAL — typographic + atmospheric uplift applied to the
   Pipelines inventory section and its embedded event log.  Layered on top of
   the existing style system; no existing rules are removed.  The aim is a
   premium ops-terminal feel: editorial serif for monumental numbers & titles,
   IBM Plex for body, JetBrains Mono for data, atmospheric gradient mesh on
   the sticky rail, staggered reveals on the stat tiles, and a live-signal
   pulse on the embedded event log heading.
   ========================================================================== */
:root {
    --cc-display: 'Fraunces', 'IBM Plex Serif', Georgia, serif;
    --cc-body:    'IBM Plex Sans', system-ui, -apple-system, 'Segoe UI', sans-serif;
    --cc-data:    'JetBrains Mono', 'SF Mono', 'Cascadia Code', ui-monospace, monospace;
    --cc-ink:     #0a0d1e;
    --cc-signal:  #f59e0b;
    --cc-signal-soft: rgba(245,158,11,.35);
}

/* Inherit the refined body font across the Streamlit app surface so labels,
   captions, and widget text all read consistently.  Targets the outermost
   container; generic enough to cascade but not aggressive enough to fight
   Streamlit's internal component styles. */
[data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"] .main,
.st-key-cc_filter_rail {
    font-family: var(--cc-body);
}

/* ----- Atmospheric sticky rail ----- */
.st-key-cc_filter_rail {
    background:
        radial-gradient(120% 160% at 0% 0%, rgba(79,70,229,.10) 0%, transparent 55%),
        radial-gradient(110% 140% at 100% 0%, rgba(13,148,136,.08) 0%, transparent 58%),
        radial-gradient(100% 120% at 55% 100%, rgba(245,158,11,.05) 0%, transparent 62%),
        rgba(255,255,255,.94) !important;
    border: 1px solid rgba(15,13,38,.10) !important;
    border-radius: 18px !important;
    padding: 16px 20px 12px 20px !important;
    box-shadow:
        0 1px 0 rgba(255,255,255,.9) inset,
        0 22px 48px -26px rgba(15,13,38,.22),
        0 1px 2px rgba(15,13,38,.03) !important;
    overflow: hidden;
}
.st-key-cc_filter_rail::before {
    content: '';
    position: absolute;
    inset: 0;
    background-image:
        linear-gradient(rgba(15,13,38,.045) 1px, transparent 1px),
        linear-gradient(90deg, rgba(15,13,38,.045) 1px, transparent 1px);
    background-size: 28px 28px;
    background-position: -1px -1px;
    -webkit-mask-image: radial-gradient(140% 100% at 50% 0%, black 30%, transparent 88%);
            mask-image: radial-gradient(140% 100% at 50% 0%, black 30%, transparent 88%);
    opacity: .55;
    pointer-events: none;
    z-index: 0;
}
.st-key-cc_filter_rail > * { position: relative; z-index: 1; }

/* ----- Display heading — Fraunces serif, tight optical tracking ----- */
.cc-panel-head {
    border-bottom: 1px solid transparent !important;
    background:
        linear-gradient(90deg,
            var(--cc-accent) 0 44px,
            rgba(15,13,38,.12) 44px 100%) bottom / 100% 1px no-repeat;
    padding-bottom: 10px !important;
}
.cc-panel-head h2 {
    font-family: var(--cc-display) !important;
    font-variation-settings: "opsz" 120, "SOFT" 50;
    font-size: 1.55rem !important;
    font-weight: 500 !important;
    letter-spacing: -0.015em !important;
    color: var(--cc-ink) !important;
    line-height: 1.05;
    display: inline-flex;
    align-items: baseline;
    gap: 12px;
}
.cc-panel-head h2::before {
    content: attr(data-section-num);
    font-family: var(--cc-data);
    font-size: 0.42em;
    font-weight: 500;
    letter-spacing: 0.08em;
    color: var(--cc-accent);
    padding: 3px 7px 2px 7px;
    border: 1px solid var(--cc-accent);
    border-radius: 5px;
    background: rgba(79,70,229,.07);
    position: relative;
    top: -4px;
    line-height: 1;
}
.cc-panel-head h2:not([data-section-num])::before { display: none; }

.cc-panel-head .cc-panel-tag {
    font-family: var(--cc-body);
    font-size: 0.62rem !important;
    letter-spacing: 0.14em !important;
    color: var(--cc-text-mute);
    font-weight: 600 !important;
    padding: 4px 9px 3px 9px;
    border: 1px solid var(--cc-border-hi);
    border-radius: 999px;
    background: rgba(255,255,255,.65);
    text-transform: uppercase;
    white-space: nowrap;
}
.cc-panel-sub {
    font-family: var(--cc-body) !important;
    font-size: 0.78rem !important;
    color: var(--cc-text-dim);
    font-weight: 400;
    letter-spacing: 0.005em;
}

/* ----- Secondary heading (embedded event log) — teal numeral + live dot ----- */
.cc-panel-head--live {
    margin-top: 26px !important;
    background:
        linear-gradient(90deg,
            var(--cc-teal) 0 44px,
            rgba(15,13,38,.12) 44px 100%) bottom / 100% 1px no-repeat !important;
}
.cc-panel-head--live h2::before {
    color: var(--cc-teal);
    border-color: var(--cc-teal);
    background: rgba(13,148,136,.07);
}
.cc-panel-head--live .cc-panel-tag::before {
    content: '';
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--cc-signal);
    margin-right: 8px;
    vertical-align: middle;
    box-shadow: 0 0 0 0 var(--cc-signal-soft);
    animation: cc-live-pulse 1.6s ease-out infinite;
}
@keyframes cc-live-pulse {
    0%   { box-shadow: 0 0 0 0 var(--cc-signal-soft); }
    80%  { box-shadow: 0 0 0 10px rgba(245,158,11,0); }
    100% { box-shadow: 0 0 0 0 rgba(245,158,11,0); }
}

/* ----- Monumental stat tiles — serif numerals + atmospheric accent ----- */
.iv-stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(164px, 1fr));
    gap: 12px;
    margin: 16px 0 18px 0;
}
.iv-stat {
    background:
        radial-gradient(140% 100% at 0% 0%, color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 8%, transparent) 0%, transparent 55%),
        var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 14px;
    padding: 14px 16px 13px 20px;
    position: relative;
    overflow: hidden;
    transition:
        transform .25s cubic-bezier(.2,.7,.2,1),
        border-color .2s ease,
        box-shadow .25s ease;
    opacity: 0;
    animation: iv-stat-in .6s cubic-bezier(.2,.7,.2,1) forwards;
}
.iv-stat::before {
    content: '';
    position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--iv-stat-accent, var(--cc-accent));
    box-shadow: 0 0 14px 0 color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 45%, transparent);
    opacity: .92;
}
.iv-stat::after {
    content: '';
    position: absolute; right: -70px; top: -70px;
    width: 180px; height: 180px;
    background: radial-gradient(circle,
        color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 14%, transparent) 0%,
        transparent 62%);
    pointer-events: none;
    transition: transform .45s cubic-bezier(.2,.7,.2,1);
}
.iv-stat:hover {
    transform: translateY(-2px);
    border-color: var(--iv-stat-accent, var(--cc-accent));
    box-shadow:
        0 16px 32px -18px color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 35%, transparent),
        0 0 0 1px color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 18%, transparent);
}
.iv-stat:hover::after { transform: translate(-14px, 14px) scale(1.12); }

.iv-stat:nth-child(1) { animation-delay: .00s; }
.iv-stat:nth-child(2) { animation-delay: .06s; }
.iv-stat:nth-child(3) { animation-delay: .12s; }
.iv-stat:nth-child(4) { animation-delay: .18s; }
.iv-stat:nth-child(5) { animation-delay: .24s; }
.iv-stat:nth-child(6) { animation-delay: .30s; }
.iv-stat:nth-child(7) { animation-delay: .36s; }
.iv-stat:nth-child(8) { animation-delay: .42s; }
@keyframes iv-stat-in {
    from { opacity: 0; transform: translateY(10px) scale(.985); }
    to   { opacity: 1; transform: translateY(0)    scale(1); }
}

.iv-stat-label {
    font-family: var(--cc-body);
    font-size: 0.60rem !important;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 600;
    margin-bottom: 6px;
    display: flex; align-items: center; gap: 7px;
}
.iv-stat-label .iv-stat-glyph {
    color: var(--iv-stat-accent, var(--cc-accent));
    font-size: 0.90rem;
    opacity: .88;
}
.iv-stat-number {
    font-family: var(--cc-display) !important;
    font-variation-settings: "opsz" 144, "SOFT" 90;
    font-size: 2.45rem !important;
    font-weight: 500 !important;
    line-height: 1.0 !important;
    color: var(--cc-ink) !important;
    letter-spacing: -0.028em !important;
    font-variant-numeric: tabular-nums lining-nums;
    padding: 4px 0 2px 0;
    position: relative;
}
.iv-stat-number::after {
    content: '';
    display: block;
    width: 22px;
    height: 2px;
    background: var(--iv-stat-accent, var(--cc-accent));
    margin-top: 6px;
    opacity: .55;
    border-radius: 2px;
    transition: width .22s ease, opacity .22s ease;
}
.iv-stat:hover .iv-stat-number::after {
    width: 42px;
    opacity: 1;
}
.iv-stat-sub {
    font-family: var(--cc-body);
    margin-top: 8px;
    font-size: 0.68rem;
    color: var(--cc-text-dim);
    font-weight: 500;
    line-height: 1.4;
    font-variant-numeric: tabular-nums;
}
.iv-stat-sub b {
    color: var(--iv-stat-accent, var(--cc-accent));
    font-family: var(--cc-data);
    font-weight: 600;
    letter-spacing: 0.01em;
}

/* ----- Refined caption above the inventory table ----- */
.el-tf-caption {
    font-family: var(--cc-body) !important;
    font-size: 0.72rem !important;
    letter-spacing: 0.01em !important;
    color: var(--cc-text-mute);
    margin: 16px 0 6px 0 !important;
    display: flex; align-items: center; gap: 10px;
    padding-left: 2px;
}
.el-tf-caption-count {
    font-family: var(--cc-data) !important;
    font-weight: 600;
    font-size: 0.74rem !important;
    color: var(--cc-ink) !important;
    padding: 2px 9px 1px 9px;
    background: var(--cc-accent-lt);
    border-radius: 5px;
    letter-spacing: 0.01em;
    font-variant-numeric: tabular-nums;
    border: 1px solid color-mix(in srgb, var(--cc-accent) 18%, transparent);
}
.el-tf-caption-sep {
    color: var(--cc-border-hi);
    font-weight: 300;
}

/* ----- Version chips, date cells → JetBrains Mono for tabular rhythm ----- */
.ap-v, .ap-chip {
    font-family: var(--cc-data) !important;
    font-variant-numeric: tabular-nums lining-nums;
}
.ap-k {
    font-family: var(--cc-body) !important;
    letter-spacing: 0.005em;
}

/* ----- Rail meta strip — ultra-fine all-caps + mono accents ----- */
.cc-rail-meta {
    font-family: var(--cc-body) !important;
    font-size: 0.62rem !important;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    margin-top: 10px !important;
    padding-top: 10px !important;
    border-top-style: dashed !important;
    border-top-color: rgba(15,13,38,.10) !important;
}
.cc-rail-meta b {
    font-family: var(--cc-body) !important;
    font-size: 0.7rem;
    letter-spacing: 0.12em;
    color: var(--cc-text-dim) !important;
    font-weight: 700;
    margin-right: 2px;
}
.cc-rail-meta span:not(:first-child)::before {
    content: '';
    display: inline-block;
    width: 3px;
    height: 3px;
    background: var(--cc-border-hi);
    border-radius: 50%;
    margin-right: 10px;
    vertical-align: middle;
}

/* Inline mono on the meta range/bucket values — they read as data, not copy */
.cc-rail-meta span > b + :is(:not(span)) { font-family: var(--cc-data); }

/* ----- Rail identity badge — editorial serif on role name ----- */
.cc-rail-id-role {
    font-family: var(--cc-display) !important;
    font-variation-settings: "opsz" 60;
    font-weight: 600 !important;
    letter-spacing: -0.005em !important;
    font-size: 0.82rem !important;
    padding: 4px 12px !important;
}
.cc-rail-id-team {
    font-family: var(--cc-body) !important;
    font-size: 0.70rem !important;
    letter-spacing: 0.04em;
}

/* Rail widget labels a touch tighter + finer */
.st-key-cc_filter_rail [data-testid="stSelectbox"] label,
.st-key-cc_filter_rail [data-testid="stTextInput"] label,
.st-key-cc_filter_rail [data-testid="stToggle"] label {
    font-family: var(--cc-body) !important;
    font-size: 0.58rem !important;
    letter-spacing: 0.16em !important;
}

/* ----- Active filter chips — refined hairline, micro-mono counts ----- */
.iv-active-chip {
    font-family: var(--cc-body) !important;
    border-radius: 6px !important;
    font-size: 0.68rem !important;
    letter-spacing: 0.01em;
    border: 1px solid var(--cc-border-hi);
    padding: 3px 9px 2px 9px;
}
.iv-active-chip-sess {
    background:
        repeating-linear-gradient(
            45deg,
            var(--cc-surface2),
            var(--cc-surface2) 6px,
            rgba(15,13,38,.03) 6px,
            rgba(15,13,38,.03) 8px
        ) !important;
}

/* ----- Inventory table — row hover, head typography, subtle grid ----- */
.el-tf {
    border-radius: 12px !important;
    border: 1px solid rgba(15,13,38,.08) !important;
    overflow: hidden;
}
.el-tf thead th {
    font-family: var(--cc-body) !important;
    font-size: 0.60rem !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase;
    color: var(--cc-text-mute) !important;
    font-weight: 600 !important;
    background:
        linear-gradient(180deg, rgba(247,248,251,.85), rgba(247,248,251,.65)) !important;
    border-bottom: 1px solid rgba(15,13,38,.08) !important;
    padding: 12px 10px !important;
}
.el-tf tbody td {
    font-family: var(--cc-body) !important;
    transition: background .14s ease;
}
.el-tf tbody tr:hover td {
    background: color-mix(in srgb, var(--cc-accent) 3%, transparent) !important;
}
.el-tf tbody tr:hover td:first-child {
    box-shadow: inset 3px 0 0 0 var(--cc-accent);
}

/* The "showing N" count badge above the table — mono treatment ---- */
.el-tf-caption b { font-family: var(--cc-data); font-variant-numeric: tabular-nums; }

/* Fine-tune existing KPI + section styles (unused in inventory view but keeps
   typography consistent across any admin-drawer content that might share the
   page).  Scoped so existing rules remain authoritative. */
.kpi .value { font-family: var(--cc-display); font-variation-settings: "opsz" 120; }
.kpi .label, .section, .section-label { font-family: var(--cc-body); }

/* Popover inner cards (project/app detail) — editorial headline for titles */
.el-app-pop .ap-title {
    font-family: var(--cc-display) !important;
    font-variation-settings: "opsz" 96, "SOFT" 40;
    font-weight: 500 !important;
    letter-spacing: -0.015em;
}
.el-app-pop .ap-kicker {
    font-family: var(--cc-body) !important;
    letter-spacing: 0.18em;
    font-size: 0.56rem;
}

/* ----- Inventory fleet pulse strip — 4 compact visualizations ----- */
.iv-pulse-strip {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 12px;
    margin: 14px 0 20px 0;
}
.iv-pulse-tile {
    background:
        linear-gradient(180deg, rgba(255,255,255,.94) 0%, rgba(247,248,251,.90) 100%),
        radial-gradient(120% 140% at 0% 0%, rgba(79,70,229,.06), transparent 55%);
    border: 1px solid var(--cc-border);
    border-radius: 10px;
    padding: 10px 13px 11px 13px;
    position: relative;
    overflow: hidden;
    transition: border-color .14s ease, transform .14s ease;
}
.iv-pulse-tile:hover {
    border-color: var(--cc-border-hi);
    transform: translateY(-1px);
}
.iv-pulse-tile::before {
    content: '';
    position: absolute;
    top: 0; left: 0; right: 0; height: 2px;
    background: var(--iv-pulse-accent, linear-gradient(90deg, var(--cc-accent), var(--cc-teal)));
    opacity: .70;
}
.iv-pulse-label {
    font-family: var(--cc-data);
    font-size: .56rem;
    letter-spacing: .16em;
    color: var(--cc-text-mute);
    font-weight: 600;
    text-transform: uppercase;
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
}
.iv-pulse-label .iv-pulse-tag {
    font-family: var(--cc-data);
    font-size: .54rem;
    letter-spacing: .10em;
    color: var(--cc-accent);
    font-weight: 700;
    padding: 1px 6px;
    border: 1px solid var(--cc-accent);
    border-radius: 3px;
    background: rgba(79,70,229,.06);
    text-transform: uppercase;
}
.iv-pulse-label .iv-pulse-tag.ok   { color: var(--cc-green); border-color: var(--cc-green); background: rgba(5,150,105,.06); }
.iv-pulse-label .iv-pulse-tag.warn { color: var(--cc-amber); border-color: var(--cc-amber); background: rgba(217,119,6,.06); }
.iv-pulse-label .iv-pulse-tag.crit { color: var(--cc-red);   border-color: var(--cc-red);   background: rgba(220,38,38,.06); }
.iv-pulse-value {
    font-family: var(--cc-display);
    font-variation-settings: "opsz" 120, "SOFT" 50;
    font-size: 2.0rem;
    font-weight: 500;
    color: var(--cc-ink, var(--cc-text));
    letter-spacing: -.022em;
    line-height: 1.0;
    margin-top: 4px;
    display: flex;
    align-items: baseline;
    gap: 6px;
}
.iv-pulse-value .iv-pulse-unit {
    font-family: var(--cc-data);
    font-size: .78rem;
    color: var(--cc-text-mute);
    font-weight: 500;
    letter-spacing: .02em;
}
.iv-pulse-sub {
    font-family: var(--cc-body);
    font-size: .70rem;
    color: var(--cc-text-dim);
    letter-spacing: .005em;
    margin: 2px 0 8px 0;
}
.iv-pulse-sub b {
    font-family: var(--cc-data);
    font-weight: 600;
    color: var(--cc-text);
}
.iv-pulse-spark {
    width: 100%;
    height: 38px;
    display: block;
    overflow: visible;
}
.iv-pulse-bar {
    width: 100%;
    height: 9px;
    display: block;
    border-radius: 3px;
    overflow: hidden;
    margin-top: 2px;
    background: color-mix(in srgb, var(--cc-border) 50%, transparent);
}
.iv-pulse-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 10px;
    margin-top: 6px;
    line-height: 1.2;
}
.iv-pulse-leg {
    font-family: var(--cc-data);
    font-size: .58rem;
    letter-spacing: .03em;
    color: var(--cc-text-mute);
    white-space: nowrap;
}
.iv-pulse-leg .iv-pulse-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 2px;
    vertical-align: middle;
    margin-right: 4px;
    box-shadow: 0 0 0 1px rgba(0,0,0,.04);
}
.iv-pulse-leg b {
    font-family: var(--cc-data);
    font-weight: 600;
    color: var(--cc-text);
}
.iv-pulse-empty {
    font-family: var(--cc-body);
    font-size: .70rem;
    color: var(--cc-text-mute);
    text-align: center;
    padding: 10px 0;
    letter-spacing: .04em;
}
.iv-pulse-axis {
    display: flex;
    justify-content: space-between;
    margin-top: 2px;
    font-family: var(--cc-data);
    font-size: .52rem;
    color: var(--cc-text-mute);
    letter-spacing: .08em;
    text-transform: uppercase;
}

/* ----- Event-log activity ribbon — stacked histogram above the table ----- */
.el-ribbon {
    margin: 10px 0 14px 0;
    padding: 10px 12px 10px 12px;
    background:
        linear-gradient(180deg, rgba(255,255,255,.96) 0%, rgba(247,248,251,.92) 100%),
        radial-gradient(80% 140% at 100% 0%, rgba(13,148,136,.05), transparent 60%);
    border: 1px solid var(--cc-border);
    border-radius: 10px;
    position: relative;
    overflow: hidden;
}
.el-ribbon::before {
    content: '';
    position: absolute;
    left: 0; top: 0; bottom: 0; width: 2px;
    background: linear-gradient(180deg, var(--cc-teal), var(--cc-accent));
    opacity: .55;
}
.el-ribbon-head {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: baseline;
    gap: 10px;
    margin-bottom: 5px;
}
.el-ribbon-title {
    font-family: var(--cc-data);
    font-size: .58rem;
    letter-spacing: .14em;
    color: var(--cc-text-mute);
    font-weight: 600;
    text-transform: uppercase;
}
.el-ribbon-title b {
    font-family: var(--cc-display);
    font-variation-settings: "opsz" 96, "SOFT" 40;
    font-size: .96rem;
    font-weight: 500;
    color: var(--cc-ink, var(--cc-text));
    letter-spacing: -.01em;
    margin-right: 6px;
    text-transform: none;
}
.el-ribbon-legend {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 2px 12px;
}
.el-rib-leg {
    font-family: var(--cc-data);
    font-size: .58rem;
    letter-spacing: .04em;
    color: var(--cc-text-mute);
    white-space: nowrap;
}
.el-rib-leg b {
    font-family: var(--cc-data);
    font-weight: 600;
    color: var(--cc-text);
    margin-left: 2px;
}
.el-rib-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 2px;
    vertical-align: middle;
    margin-right: 4px;
    box-shadow: 0 0 0 1px rgba(0,0,0,.04);
}
.el-ribbon-svg {
    display: block;
    width: 100%;
    height: 52px;
    overflow: visible;
}
.el-ribbon-axis {
    display: flex;
    justify-content: space-between;
    margin-top: 3px;
    font-family: var(--cc-data);
    font-size: .52rem;
    color: var(--cc-text-mute);
    letter-spacing: .08em;
    text-transform: uppercase;
}
.el-ribbon-axis span + span { text-align: right; }
.el-ribbon-empty {
    font-family: var(--cc-body);
    font-size: .74rem;
    color: var(--cc-text-mute);
    text-align: center;
    padding: 8px 0 4px 0;
    letter-spacing: .02em;
}

/* ==========================================================================
   OPS TERMINAL — SECOND-PASS UI/UX BOOST
   Layered above the earlier Precision Ops Terminal block. Scoped tightly to
   the Pipelines-inventory panel and the embedded Event Log so the rest of
   the dashboard keeps its existing voice. Themes:
     · monumental section framing with corner bracket registration marks
     · ticker-style live indicator on the event-log heading
     · per-project sections rendered as timeline nodes with a vertical spine
     · each event row carries a status-tinted left gutter that bleeds on hover
     · a radar sweep accent on the caption count badge
   ========================================================================== */

/* ── Shared decorative variables ─────────────────────────────────────────── */
.st-key-cc_filter_rail,
[data-testid="stAppViewContainer"] {
    --ot-bracket: rgba(15,13,38,.22);
    --ot-bracket-hi: var(--cc-accent);
    --ot-scan: rgba(245,158,11,.55);
    --ot-spine: linear-gradient(180deg,
        rgba(79,70,229,.55) 0%,
        rgba(13,148,136,.55) 55%,
        rgba(245,158,11,.55) 100%);
}

/* ── Registration-mark corner brackets on the sticky rail ────────────────── */
.st-key-cc_filter_rail::after {
    content: '';
    position: absolute;
    inset: 8px;
    border: 0 solid var(--ot-bracket);
    border-radius: 14px;
    background:
        /* top-left corner */
        linear-gradient(to right, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) top left / 14px 1px no-repeat,
        linear-gradient(to bottom, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) top left / 1px 14px no-repeat,
        /* top-right corner */
        linear-gradient(to left, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) top right / 14px 1px no-repeat,
        linear-gradient(to bottom, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) top right / 1px 14px no-repeat,
        /* bottom-left corner */
        linear-gradient(to right, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) bottom left / 14px 1px no-repeat,
        linear-gradient(to top, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) bottom left / 1px 14px no-repeat,
        /* bottom-right corner */
        linear-gradient(to left, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) bottom right / 14px 1px no-repeat,
        linear-gradient(to top, var(--ot-bracket-hi) 0, var(--ot-bracket-hi) 14px, transparent 14px) bottom right / 1px 14px no-repeat;
    pointer-events: none;
    opacity: .38;
    z-index: 2;
    mix-blend-mode: multiply;
}

/* ── Section-head: animated underline sweep on first paint ──────────────── */
.cc-panel-head {
    position: relative;
}
.cc-panel-head::after {
    content: '';
    position: absolute;
    left: 0; right: 0; bottom: 0;
    height: 1px;
    background: linear-gradient(90deg,
        transparent 0%,
        color-mix(in srgb, var(--cc-accent) 65%, transparent) 20%,
        color-mix(in srgb, var(--cc-accent) 90%, transparent) 48%,
        color-mix(in srgb, var(--cc-accent) 65%, transparent) 76%,
        transparent 100%);
    transform: scaleX(0);
    transform-origin: left center;
    animation: ot-head-sweep 1.1s cubic-bezier(.2,.7,.2,1) .18s forwards;
    opacity: .55;
    pointer-events: none;
}
.cc-panel-head--live::after {
    background: linear-gradient(90deg,
        transparent 0%,
        color-mix(in srgb, var(--cc-teal) 70%, transparent) 20%,
        color-mix(in srgb, var(--cc-teal) 95%, transparent) 48%,
        color-mix(in srgb, var(--cc-teal) 70%, transparent) 76%,
        transparent 100%);
    animation: ot-head-sweep 1.1s cubic-bezier(.2,.7,.2,1) .30s forwards;
}
@keyframes ot-head-sweep {
    from { transform: scaleX(0); }
    to   { transform: scaleX(1); }
}

/* Numeral chip: subtle embossed shadow + breathing glow */
.cc-panel-head h2::before {
    box-shadow:
        0 0 0 1px color-mix(in srgb, var(--cc-accent) 14%, transparent),
        inset 0 -4px 10px -6px color-mix(in srgb, var(--cc-accent) 40%, transparent);
    transition: transform .3s ease, box-shadow .3s ease;
}
.cc-panel-head:hover h2::before {
    transform: translateY(-1px);
    box-shadow:
        0 0 0 1px color-mix(in srgb, var(--cc-accent) 30%, transparent),
        0 6px 14px -8px color-mix(in srgb, var(--cc-accent) 60%, transparent);
}
.cc-panel-head--live h2::before {
    box-shadow:
        0 0 0 1px color-mix(in srgb, var(--cc-teal) 14%, transparent),
        inset 0 -4px 10px -6px color-mix(in srgb, var(--cc-teal) 40%, transparent);
}
.cc-panel-head--live:hover h2::before {
    box-shadow:
        0 0 0 1px color-mix(in srgb, var(--cc-teal) 30%, transparent),
        0 6px 14px -8px color-mix(in srgb, var(--cc-teal) 60%, transparent);
}

/* ── Live tag: ticker-style marquee pulse under the pill ─────────────────── */
.cc-panel-head--live .cc-panel-tag {
    position: relative;
    overflow: hidden;
    isolation: isolate;
}
.cc-panel-head--live .cc-panel-tag::after {
    content: '';
    position: absolute;
    left: -40%;
    bottom: 0;
    width: 40%;
    height: 1px;
    background: linear-gradient(90deg, transparent, var(--ot-scan), transparent);
    animation: ot-tag-ticker 3.2s linear infinite;
    z-index: -1;
}
@keyframes ot-tag-ticker {
    0%   { left: -40%; }
    100% { left: 140%; }
}

/* ── Sub-caption below the inventory title: typewriter cursor flick ─────── */
.st-key-cc_filter_rail ~ div .cc-panel-sub,
.st-key-cc_filter_rail + div .cc-panel-sub {
    position: relative;
}

/* ── Stat tiles: add a thin sparkline-style baseline shimmer on hover ────── */
.iv-stat {
    isolation: isolate;
}
.iv-stat::after {
    transition:
        transform .45s cubic-bezier(.2,.7,.2,1),
        opacity  .35s ease;
}
.iv-stat:hover {
    transform: translateY(-3px);
}
.iv-stat::before {
    transition: box-shadow .28s ease, width .28s ease;
}
.iv-stat:hover::before {
    width: 4px;
    box-shadow: 0 0 22px 0 color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 70%, transparent);
}

/* Give the stat number a subtle conic shimmer swatch on hover */
.iv-stat-number {
    background-clip: text;
    -webkit-background-clip: text;
    transition: color .24s ease;
}
.iv-stat:hover .iv-stat-number {
    color: color-mix(in srgb, var(--cc-ink) 88%, var(--iv-stat-accent, var(--cc-accent))) !important;
}

/* ── Refined caption count: radar-sweep highlight on the count chip ──────── */
.el-tf-caption-count {
    position: relative;
    overflow: hidden;
    isolation: isolate;
}
.el-tf-caption-count::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(115deg,
        transparent 0%,
        transparent 42%,
        rgba(255,255,255,.55) 50%,
        transparent 58%,
        transparent 100%);
    transform: translateX(-120%);
    animation: ot-count-sweep 4.8s ease-in-out infinite;
    pointer-events: none;
}
@keyframes ot-count-sweep {
    0%,  24% { transform: translateX(-120%); }
    48%      { transform: translateX(120%); }
    100%     { transform: translateX(120%); }
}

/* ── Per-project event-log sections: timeline-node treatment ─────────────── */
.el-proj-stack {
    position: relative;
    padding-left: 14px;
    margin-top: 6px;
}
.el-proj-stack::before {
    content: '';
    position: absolute;
    left: 4px;
    top: 18px;
    bottom: 18px;
    width: 1px;
    background: var(--ot-spine);
    opacity: .28;
    border-radius: 2px;
}
.el-proj-stack .el-proj-section {
    position: relative;
    transition:
        border-color .22s ease,
        transform    .22s cubic-bezier(.2,.7,.2,1),
        box-shadow   .22s ease;
}
.el-proj-stack .el-proj-section::after {
    content: '';
    position: absolute;
    left: -14px;
    top: 20px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background:
        radial-gradient(circle at 35% 30%,
            color-mix(in srgb, var(--cc-teal) 85%, #fff) 0%,
            var(--cc-teal) 60%,
            color-mix(in srgb, var(--cc-teal) 40%, #000) 100%);
    box-shadow:
        0 0 0 2px #fff,
        0 0 0 3px color-mix(in srgb, var(--cc-teal) 30%, transparent),
        0 0 10px 0 color-mix(in srgb, var(--cc-teal) 35%, transparent);
    z-index: 1;
}
.el-proj-stack .el-proj-section:hover {
    border-color: color-mix(in srgb, var(--cc-teal) 40%, var(--cc-border));
    transform: translateX(2px);
    box-shadow:
        0 14px 28px -20px color-mix(in srgb, var(--cc-teal) 35%, transparent),
        0 0 0 1px color-mix(in srgb, var(--cc-teal) 12%, transparent);
}
.el-proj-stack .el-proj-section:hover::after {
    box-shadow:
        0 0 0 2px #fff,
        0 0 0 3px color-mix(in srgb, var(--cc-teal) 60%, transparent),
        0 0 18px 2px color-mix(in srgb, var(--cc-teal) 55%, transparent);
}

/* Project kicker: upgrade to a notched label */
.el-proj-section-kicker {
    font-family: var(--cc-data) !important;
    letter-spacing: .18em !important;
    font-size: .58rem !important;
    color: color-mix(in srgb, var(--cc-teal) 75%, var(--cc-text-mute)) !important;
    padding: 2px 7px 1px 7px;
    border: 1px solid color-mix(in srgb, var(--cc-teal) 30%, var(--cc-border));
    border-radius: 3px;
    background: color-mix(in srgb, var(--cc-teal) 6%, transparent);
    font-weight: 700 !important;
}

/* Project count chip: mono + teal accent */
.el-proj-section-count {
    font-family: var(--cc-data) !important;
    font-weight: 700 !important;
    font-size: .66rem !important;
    letter-spacing: .04em;
    color: color-mix(in srgb, var(--cc-teal) 70%, var(--cc-ink)) !important;
    background: color-mix(in srgb, var(--cc-teal) 8%, var(--cc-surface)) !important;
    border: 1px solid color-mix(in srgb, var(--cc-teal) 25%, var(--cc-border));
    font-variant-numeric: tabular-nums;
    padding: 2px 10px !important;
}

/* ── Event-log table: refined row hover with a status-neutral left gutter ── */
.el-tf tbody tr {
    position: relative;
    transition: background .16s ease, box-shadow .16s ease;
}
.el-tf tbody tr:hover {
    background: color-mix(in srgb, var(--cc-teal) 4%, transparent) !important;
}
.el-tf tbody tr:hover td:first-child {
    box-shadow: inset 3px 0 0 0 var(--cc-teal) !important;
}
/* Align the baseline of every cell so the type badge, version chip, and
   person avatar all sit on the same optical rail */
.el-tf tbody td {
    vertical-align: middle !important;
    border-bottom: 1px solid color-mix(in srgb, var(--cc-border) 55%, transparent) !important;
}
.el-tf tbody tr:last-child td {
    border-bottom: none !important;
}

/* Mono-fy time + detail cells for data rhythm */
.el-tf tbody td:first-child {
    font-family: var(--cc-data) !important;
    font-variant-numeric: tabular-nums lining-nums;
    font-size: .76rem !important;
    letter-spacing: .01em;
    color: var(--cc-text-dim) !important;
}

/* ── Activity ribbon: a faint scanning glow on the head ──────────────────── */
.el-ribbon {
    transition: border-color .22s ease, box-shadow .22s ease;
}
.el-ribbon:hover {
    border-color: color-mix(in srgb, var(--cc-teal) 30%, var(--cc-border));
    box-shadow: 0 14px 30px -22px color-mix(in srgb, var(--cc-teal) 30%, transparent);
}
.el-ribbon-title b {
    background: linear-gradient(90deg,
        var(--cc-ink) 0%,
        color-mix(in srgb, var(--cc-ink) 80%, var(--cc-teal)) 100%);
    -webkit-background-clip: text;
            background-clip: text;
    -webkit-text-fill-color: transparent;
}

/* ── Empty/no-events inline note inside the inventory panel ──────────────── */
.st-key-cc_filter_rail ~ div [data-testid="stAlert"],
.st-key-cc_filter_rail + div [data-testid="stAlert"] {
    border-radius: 12px !important;
    border: 1px dashed color-mix(in srgb, var(--cc-accent) 28%, var(--cc-border)) !important;
    background:
        repeating-linear-gradient(45deg,
            rgba(79,70,229,.04) 0,
            rgba(79,70,229,.04) 8px,
            transparent 8px,
            transparent 14px),
        var(--cc-surface2) !important;
}

/* ── Micro-elevation on the inventory table's scrollable shell ──────────── */
.el-tf {
    background:
        linear-gradient(180deg, rgba(255,255,255,.96) 0%, rgba(247,248,251,.85) 100%) !important;
    box-shadow:
        0 1px 0 rgba(255,255,255,.9) inset,
        0 24px 44px -30px rgba(15,13,38,.22),
        0 0 0 1px rgba(15,13,38,.035);
}

/* ── "showing N of M" caption: add blinking terminal cursor after text ───── */
.el-tf-caption > span:last-child::after {
    content: '▍';
    display: inline-block;
    color: color-mix(in srgb, var(--cc-teal) 75%, transparent);
    margin-left: 4px;
    font-family: var(--cc-data);
    font-weight: 700;
    animation: ot-cursor-blink 1.1s steps(2, start) infinite;
    transform: translateY(-1px);
}
@keyframes ot-cursor-blink {
    0%,  49% { opacity: 1; }
    50%, 100% { opacity: 0; }
}

/* ── Reduced-motion: honor user preference ───────────────────────────────── */
@media (prefers-reduced-motion: reduce) {
    .cc-panel-head::after,
    .cc-panel-head--live::after { animation: none; transform: scaleX(1); }
    .cc-panel-head--live .cc-panel-tag::after { animation: none; display: none; }
    .el-tf-caption-count::after { animation: none; display: none; }
    .el-tf-caption > span:last-child::after { animation: none; opacity: 1; }
    .iv-stat { animation: none; opacity: 1; transform: none; }
}

/* ==========================================================================
   SLIM RAIL + STICKY SECONDARY FILTER BAR
   The top rail is now role-badge + search + settings-cog only. Below it the
   inventory's "Filters & sort" row pins sticky so users keep scope controls
   visible as they scroll through the table + event log.
   ========================================================================== */

/* Rail: tighter vertical rhythm + larger, hero-styled search */
.st-key-cc_filter_rail {
    padding: 10px 16px 8px 16px !important;
    margin: 4px 0 0 0 !important;
}

/* Kill the corner-bracket decoration from the second-pass boost now that
   the rail is slimmer — the brackets looked cramped at this height. */
.st-key-cc_filter_rail::after { display: none !important; }

/* Hero search: tall, crisp, with a soft inner glow on focus */
.st-key-cc_filter_rail [data-testid="stTextInput"] > div > div {
    min-height: 44px !important;
    border-radius: 10px !important;
    border: 1px solid var(--cc-border-hi) !important;
    background:
        linear-gradient(180deg, rgba(255,255,255,.98) 0%, rgba(249,250,253,.95) 100%) !important;
    box-shadow:
        inset 0 1px 0 rgba(255,255,255,.9),
        inset 0 0 0 1px rgba(15,13,38,.02);
    transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
}
.st-key-cc_filter_rail [data-testid="stTextInput"] input {
    font-family: var(--cc-body) !important;
    font-size: 0.92rem !important;
    letter-spacing: 0.005em !important;
    color: var(--cc-ink) !important;
    padding: 10px 14px !important;
}
.st-key-cc_filter_rail [data-testid="stTextInput"] input::placeholder {
    color: var(--cc-text-mute) !important;
    font-weight: 400;
    letter-spacing: 0.01em;
    opacity: 0.85;
}
.st-key-cc_filter_rail [data-testid="stTextInput"] > div > div:focus-within {
    border-color: var(--cc-accent) !important;
    background: #fff !important;
    box-shadow:
        0 0 0 3px color-mix(in srgb, var(--cc-accent) 16%, transparent),
        0 8px 18px -12px color-mix(in srgb, var(--cc-accent) 35%, transparent) !important;
}

/* Settings cog: pill-form, quiet, expands on hover */
.st-key-cc_filter_rail [data-testid="stPopover"] button,
.st-key-cc_filter_rail [data-testid="stPopoverButton"] button {
    font-family: var(--cc-data) !important;
    font-size: 1.05rem !important;
    padding: 9px 0 !important;
    border-radius: 10px !important;
    border: 1px solid var(--cc-border-hi) !important;
    background: rgba(255,255,255,.85) !important;
    color: var(--cc-text-dim) !important;
    transition: color .16s ease, border-color .16s ease, background .16s ease, transform .16s ease;
}
.st-key-cc_filter_rail [data-testid="stPopover"] button:hover,
.st-key-cc_filter_rail [data-testid="stPopoverButton"] button:hover {
    color: var(--cc-accent) !important;
    border-color: var(--cc-accent) !important;
    background: color-mix(in srgb, var(--cc-accent) 8%, #fff) !important;
    transform: translateY(-1px);
}

/* ── Secondary sticky bar: Filters & sort + active chips + Clear button ──── */
.st-key-cc_filter_secondary {
    position: sticky;
    top: 92px;               /* sits just below the slim rail */
    z-index: 800;
    margin: 0 0 12px 0;
    padding: 8px 14px 8px 14px;
    background: rgba(255,255,255,.82);
    -webkit-backdrop-filter: saturate(150%) blur(10px);
            backdrop-filter: saturate(150%) blur(10px);
    border: 1px solid color-mix(in srgb, var(--cc-border) 80%, transparent);
    border-radius: 12px;
    box-shadow:
        0 1px 0 rgba(255,255,255,.8) inset,
        0 10px 22px -18px rgba(15,13,38,.18),
        0 1px 2px rgba(15,13,38,.03);
    transition: box-shadow .22s ease, border-color .22s ease;
}
.st-key-cc_filter_secondary:hover {
    border-color: color-mix(in srgb, var(--cc-accent) 22%, var(--cc-border));
    box-shadow:
        0 1px 0 rgba(255,255,255,.8) inset,
        0 14px 28px -20px color-mix(in srgb, var(--cc-accent) 30%, transparent),
        0 1px 2px rgba(15,13,38,.03);
}

/* Filters & sort popover trigger — make it look like a primary action */
.st-key-cc_filter_secondary [data-testid="stPopover"] button,
.st-key-cc_filter_secondary [data-testid="stPopoverButton"] button {
    font-family: var(--cc-body) !important;
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.01em !important;
    padding: 8px 14px !important;
    border-radius: 10px !important;
    background:
        linear-gradient(180deg,
            color-mix(in srgb, var(--cc-accent) 95%, #fff) 0%,
            var(--cc-accent) 100%) !important;
    color: #fff !important;
    border: 1px solid color-mix(in srgb, var(--cc-accent) 80%, #000) !important;
    box-shadow:
        0 1px 0 rgba(255,255,255,.25) inset,
        0 6px 14px -6px color-mix(in srgb, var(--cc-accent) 60%, transparent) !important;
    transition: transform .16s ease, box-shadow .16s ease, filter .16s ease !important;
}
.st-key-cc_filter_secondary [data-testid="stPopover"] button:hover,
.st-key-cc_filter_secondary [data-testid="stPopoverButton"] button:hover {
    transform: translateY(-1px);
    filter: brightness(1.04);
    box-shadow:
        0 1px 0 rgba(255,255,255,.25) inset,
        0 10px 22px -8px color-mix(in srgb, var(--cc-accent) 70%, transparent) !important;
}

/* Clear button — quiet secondary treatment */
.st-key-cc_filter_secondary [data-testid="stButton"] button {
    font-family: var(--cc-body) !important;
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em !important;
    text-transform: uppercase;
    padding: 7px 12px !important;
    border-radius: 8px !important;
    color: var(--cc-text-mute) !important;
    background: transparent !important;
    border: 1px solid var(--cc-border-hi) !important;
    transition: color .15s ease, border-color .15s ease, background .15s ease;
}
.st-key-cc_filter_secondary [data-testid="stButton"] button:hover {
    color: var(--cc-red) !important;
    border-color: color-mix(in srgb, var(--cc-red) 35%, var(--cc-border-hi)) !important;
    background: color-mix(in srgb, var(--cc-red) 6%, transparent) !important;
}

/* Active-filter chips row — neat wrap, no scroll */
.st-key-cc_filter_secondary .iv-active-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 6px;
    align-items: center;
    max-height: 32px;
    overflow: hidden;
    mask-image: linear-gradient(90deg, black 0%, black 92%, transparent 100%);
    -webkit-mask-image: linear-gradient(90deg, black 0%, black 92%, transparent 100%);
}
.st-key-cc_filter_secondary .iv-filter-hint {
    font-size: 0.74rem;
    color: var(--cc-text-mute);
    letter-spacing: 0.01em;
    padding-left: 4px;
}

/* Role-identity cell: trim for the slim rail */
.st-key-cc_filter_rail .cc-rail-id-role {
    padding: 3px 10px !important;
    font-size: 0.74rem !important;
}
.st-key-cc_filter_rail .cc-rail-id-team {
    font-size: 0.66rem !important;
    letter-spacing: 0.01em;
}

/* Meta strip — keep on rail but more compact */
.cc-rail-meta {
    margin-top: 8px !important;
    padding-top: 7px !important;
}

/* Responsive: stack the rail's two columns on narrow viewports */
@media (max-width: 900px) {
    .st-key-cc_filter_secondary {
        top: 164px;
    }
}

/* ==========================================================================
   FILTERABLE STAT TILES (overlay pattern)
   Each tile renders a visual HTML card PLUS an absolutely-positioned,
   transparent popover button that covers the card. The HTML guarantees
   identical size + layout across tiles; the overlay makes the whole card
   clickable. :hover and :has([aria-expanded="true"]) on the wrapper apply
   lifted / expanded states to the card underneath.
   ========================================================================== */

/* Tile row container */
.st-key-cc_iv_tiles_row {
    margin: 18px 0 20px 0 !important;
    padding: 0 !important;
}
.st-key-cc_iv_tiles_row > div[data-testid="stHorizontalBlock"] {
    gap: 10px !important;
    align-items: stretch !important;
}
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"] {
    display: flex !important;
}

/* Per-tile wrapper — each wraps the HTML card + overlay popover.
   Gives the popover an anchor for absolute positioning and holds the
   per-dimension accent color. */
[class*="st-key-cc_tile_"] {
    position: relative !important;
    padding: 0 !important;
    margin: 0 !important;
    width: 100% !important;
    height: 100% !important;
    --iv-stat-accent: var(--cc-accent);
    display: flex !important;
    flex-direction: column !important;
}
.st-key-cc_tile_company  { --iv-stat-accent: var(--cc-accent); }
.st-key-cc_tile_team     { --iv-stat-accent: var(--cc-teal); }
.st-key-cc_tile_project  { --iv-stat-accent: var(--cc-blue); }
.st-key-cc_tile_app      { --iv-stat-accent: var(--cc-green); }
.st-key-cc_tile_build    { --iv-stat-accent: var(--cc-amber); }
.st-key-cc_tile_deploy   { --iv-stat-accent: var(--cc-teal); }
.st-key-cc_tile_platform { --iv-stat-accent: var(--cc-blue); }
.st-key-cc_tile_combo    { --iv-stat-accent: var(--cc-red); }

/* The visual HTML card — uniform size, all the atmosphere */
.iv-tile.iv-tile-click {
    position: relative;
    z-index: 1;
    pointer-events: none;                 /* clicks fall through to overlay */
    display: flex;
    flex-direction: column;
    background:
        radial-gradient(140% 100% at 0% 0%,
            color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 10%, transparent) 0%,
            transparent 55%),
        var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 14px;
    padding: 14px 18px 13px 20px;
    min-height: 148px;
    height: 100%;
    box-sizing: border-box;
    overflow: hidden;
    opacity: 0;
    animation: iv-stat-in .6s cubic-bezier(.2,.7,.2,1) forwards;
    transition:
        transform .25s cubic-bezier(.2,.7,.2,1),
        border-color .22s ease,
        box-shadow .25s ease,
        background .22s ease;
}
.iv-tile.iv-tile-click::before {
    content: '';
    position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--iv-stat-accent, var(--cc-accent));
    box-shadow: 0 0 14px 0
        color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 45%, transparent);
    opacity: .92;
    transition: box-shadow .28s ease, width .28s ease;
}
.iv-tile.iv-tile-click::after {
    content: '';
    position: absolute; right: -70px; top: -70px;
    width: 180px; height: 180px;
    background: radial-gradient(circle,
        color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 14%, transparent) 0%,
        transparent 62%);
    pointer-events: none;
    transition: transform .45s cubic-bezier(.2,.7,.2,1);
}

/* Stagger-in via nth-child on the column */
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(1) .iv-tile.iv-tile-click { animation-delay: .00s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2) .iv-tile.iv-tile-click { animation-delay: .06s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3) .iv-tile.iv-tile-click { animation-delay: .12s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(4) .iv-tile.iv-tile-click { animation-delay: .18s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(5) .iv-tile.iv-tile-click { animation-delay: .24s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(6) .iv-tile.iv-tile-click { animation-delay: .30s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(7) .iv-tile.iv-tile-click { animation-delay: .36s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(8) .iv-tile.iv-tile-click { animation-delay: .42s; }

/* Hover / expanded state propagates from the wrapper to the card */
[class*="st-key-cc_tile_"]:hover .iv-tile.iv-tile-click,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile.iv-tile-click {
    transform: translateY(-3px);
    border-color: var(--iv-stat-accent);
    box-shadow:
        0 18px 34px -20px color-mix(in srgb, var(--iv-stat-accent) 45%, transparent),
        0 0 0 1px color-mix(in srgb, var(--iv-stat-accent) 20%, transparent);
}
[class*="st-key-cc_tile_"]:hover .iv-tile.iv-tile-click::before,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile.iv-tile-click::before {
    width: 4px;
    box-shadow: 0 0 22px 0
        color-mix(in srgb, var(--iv-stat-accent) 70%, transparent);
}
[class*="st-key-cc_tile_"]:hover .iv-tile.iv-tile-click::after,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile.iv-tile-click::after {
    transform: translate(-14px, 14px) scale(1.12);
}
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile.iv-tile-click {
    border-color: var(--iv-stat-accent);
    box-shadow:
        0 20px 40px -22px color-mix(in srgb, var(--iv-stat-accent) 55%, transparent),
        0 0 0 2px color-mix(in srgb, var(--iv-stat-accent) 28%, transparent);
}

/* Card content: label row */
.iv-tile .iv-tile-head {
    font-family: var(--cc-body);
    font-size: 0.62rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 600;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    gap: 7px;
    min-height: 18px;
}
.iv-tile .iv-tile-glyph {
    color: var(--iv-stat-accent, var(--cc-accent));
    font-size: 0.95rem;
    opacity: .90;
    line-height: 1;
}
.iv-tile .iv-tile-label {
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
/* Active-selection badge (glowing pill in the top-right) */
.iv-tile .iv-tile-badge {
    font-family: var(--cc-data);
    font-size: 0.62rem;
    letter-spacing: 0.06em;
    font-weight: 700;
    color: #fff;
    background: var(--iv-stat-accent, var(--cc-accent));
    padding: 2px 8px 1px 7px;
    border-radius: 999px;
    box-shadow:
        0 0 0 2px color-mix(in srgb, var(--iv-stat-accent) 25%, transparent),
        0 4px 10px -4px color-mix(in srgb, var(--iv-stat-accent) 50%, transparent);
    font-variant-numeric: tabular-nums;
    animation: iv-tile-badge-pulse 2.8s ease-in-out infinite;
}
@keyframes iv-tile-badge-pulse {
    0%, 100% { box-shadow:
        0 0 0 2px color-mix(in srgb, var(--iv-stat-accent) 25%, transparent),
        0 4px 10px -4px color-mix(in srgb, var(--iv-stat-accent) 50%, transparent); }
    50% { box-shadow:
        0 0 0 4px color-mix(in srgb, var(--iv-stat-accent) 18%, transparent),
        0 6px 14px -4px color-mix(in srgb, var(--iv-stat-accent) 60%, transparent); }
}

/* Big number */
.iv-tile .iv-tile-number {
    font-family: var(--cc-display) !important;
    font-variation-settings: "opsz" 144, "SOFT" 90;
    font-size: 2.45rem !important;
    font-weight: 500 !important;
    line-height: 1.0 !important;
    color: var(--cc-ink) !important;
    letter-spacing: -0.028em !important;
    font-variant-numeric: tabular-nums lining-nums;
    padding: 2px 0 4px 0;
    position: relative;
    transition: color .24s ease;
}
.iv-tile .iv-tile-number::after {
    content: '';
    display: block;
    width: 22px;
    height: 2px;
    background: var(--iv-stat-accent, var(--cc-accent));
    margin-top: 6px;
    opacity: .55;
    border-radius: 2px;
    transition: width .22s ease, opacity .22s ease;
}
[class*="st-key-cc_tile_"]:hover .iv-tile .iv-tile-number::after,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile .iv-tile-number::after {
    width: 42px;
    opacity: 1;
}
[class*="st-key-cc_tile_"]:hover .iv-tile .iv-tile-number {
    color: color-mix(in srgb, var(--cc-ink) 88%, var(--iv-stat-accent)) !important;
}

/* Subtitle */
.iv-tile .iv-tile-sub {
    font-family: var(--cc-body);
    margin-top: 8px;
    font-size: 0.70rem;
    color: var(--cc-text-dim);
    font-weight: 500;
    line-height: 1.4;
    font-variant-numeric: tabular-nums;
    flex: 1;
}
.iv-tile .iv-tile-sub b {
    color: var(--iv-stat-accent, var(--cc-accent));
    font-family: var(--cc-data);
    font-weight: 700;
    letter-spacing: 0.01em;
    font-size: 0.76rem;
}

/* CTA strip at the bottom — reveals on hover/expand */
.iv-tile .iv-tile-cta {
    font-family: var(--cc-data);
    font-size: 0.58rem;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--iv-stat-accent, var(--cc-accent));
    font-weight: 700;
    margin-top: 10px;
    padding-top: 8px;
    border-top: 1px dashed
        color-mix(in srgb, var(--iv-stat-accent) 22%, transparent);
    opacity: .45;
    transition: opacity .22s ease, letter-spacing .22s ease;
}
[class*="st-key-cc_tile_"]:hover .iv-tile .iv-tile-cta,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile .iv-tile-cta {
    opacity: 1;
    letter-spacing: 0.22em;
}

/* Overlay popover — absolutely positioned, visually invisible, clickable.
   Scoped to tile wrappers so the Sort popover in the sticky bar keeps
   its normal styling. */
[class*="st-key-cc_tile_"] > div[data-testid="stPopover"],
[class*="st-key-cc_tile_"] > div[data-testid="stPopoverButton"] {
    position: absolute !important;
    inset: 0 !important;
    z-index: 2 !important;
    margin: 0 !important;
    padding: 0 !important;
}
[class*="st-key-cc_tile_"] > div[data-testid="stPopover"] > button,
[class*="st-key-cc_tile_"] > div[data-testid="stPopoverButton"] > button {
    all: unset !important;
    display: block !important;
    width: 100% !important;
    height: 100% !important;
    min-height: 100% !important;
    background: transparent !important;
    border: 1px solid transparent !important;
    border-radius: 14px !important;
    cursor: pointer !important;
    box-shadow: none !important;
    padding: 0 !important;
    margin: 0 !important;
    opacity: 0 !important;            /* text invisible; box-fill clickable */
}
[class*="st-key-cc_tile_"] > div[data-testid="stPopover"] > button:focus-visible,
[class*="st-key-cc_tile_"] > div[data-testid="stPopoverButton"] > button:focus-visible {
    opacity: 1 !important;            /* focus ring visible for a11y */
    outline: 2px solid var(--iv-stat-accent) !important;
    outline-offset: 2px !important;
    border-radius: 14px !important;
}
/* Kill the markdown container inside the button — tile HTML provides text */
[class*="st-key-cc_tile_"] > div[data-testid="stPopover"] > button [data-testid="stMarkdownContainer"],
[class*="st-key-cc_tile_"] > div[data-testid="stPopoverButton"] > button [data-testid="stMarkdownContainer"] {
    display: none !important;
}

/* Popover FLOATING content — the filter widget drawer */
[class*="st-key-cc_tile_"] [data-baseweb="popover"],
[class*="st-key-cc_tile_"] ~ [data-baseweb="popover"] {
    min-width: 320px;
}
.iv-tile-pop-head {
    font-family: var(--cc-body);
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 0 0 10px 2px;
    border-bottom: 1px solid
        color-mix(in srgb, var(--cc-border) 65%, transparent);
    margin-bottom: 12px;
}
.iv-tile-pop-glyph {
    font-size: 1.0rem;
    color: var(--cc-accent);
    line-height: 1;
}
.iv-tile-pop-title {
    color: var(--cc-ink);
    letter-spacing: 0.08em;
}

.iv-tile-hint {
    font-family: var(--cc-data);
    font-size: 0.66rem;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.04em;
    color: var(--cc-text-mute);
    padding: 0 0 8px 2px;
    text-transform: uppercase;
    font-weight: 600;
    margin-bottom: 6px;
}

/* Reduced-motion honors user preference */
@media (prefers-reduced-motion: reduce) {
    .iv-tile.iv-tile-click {
        animation: none;
        opacity: 1;
    }
    .iv-tile .iv-tile-badge { animation: none; }
}

/* Sort popover on the sticky bar — gentler than before so the tiles
   below feel like the primary action. Overrides the gradient treatment
   applied to .st-key-cc_filter_secondary popovers earlier. */
.st-key-cc_filter_secondary [data-testid="stPopover"] > button,
.st-key-cc_filter_secondary [data-testid="stPopoverButton"] > button {
    background: rgba(255,255,255,.94) !important;
    color: var(--cc-ink) !important;
    border: 1px solid var(--cc-border-hi) !important;
    box-shadow:
        0 1px 0 rgba(255,255,255,.9) inset,
        0 4px 10px -6px rgba(15,13,38,.12) !important;
}
.st-key-cc_filter_secondary [data-testid="stPopover"] > button:hover,
.st-key-cc_filter_secondary [data-testid="stPopoverButton"] > button:hover {
    border-color: var(--cc-accent) !important;
    color: var(--cc-accent) !important;
    background: color-mix(in srgb, var(--cc-accent) 6%, #fff) !important;
    box-shadow:
        0 1px 0 rgba(255,255,255,.9) inset,
        0 8px 18px -8px color-mix(in srgb, var(--cc-accent) 35%, transparent) !important;
}

/* ==========================================================================
   OPS TERMINAL — THIRD-PASS UI/UX BOOST
   Targeted refinements layered on top of the prior passes. The focus is on
   three high-signal wins:
     1. Sticky table headers on the event-log and inventory shells so the
        column rail stays visible while scrolling long event lists.
     2. A per-row freshness pulse-dot in the When column so event recency
        reads at a glance without hunting for the "5m ago" text.
     3. Micro-animations on the fleet-pulse sparkline endpoints + activity
        ribbon (weekend bands, peak marker) to give the ops-terminal feel
        more life without adding visual noise.
   Scoped through `.el-tf-shell` + the `.el-fresh-dot` / `.iv-pulse-spark-*`
   classes so no prior rules are overridden.
   ========================================================================== */

/* ── Table shell: sticky header, soft top glow, subtle column hairlines ── */
.el-tf-shell {
    position: relative;
    isolation: isolate;
    scrollbar-width: thin;
    scrollbar-color: color-mix(in srgb, var(--cc-teal) 45%, transparent) transparent;
}
.el-tf-shell::-webkit-scrollbar { width: 8px; height: 8px; }
.el-tf-shell::-webkit-scrollbar-thumb {
    background: color-mix(in srgb, var(--cc-teal) 35%, transparent);
    border-radius: 8px;
}
.el-tf-shell::-webkit-scrollbar-thumb:hover {
    background: color-mix(in srgb, var(--cc-teal) 65%, transparent);
}
.el-tf-shell > table {
    position: relative;
    z-index: 1;
}
.el-tf-shell thead th {
    position: sticky !important;
    top: 0 !important;
    z-index: 3 !important;
    backdrop-filter: saturate(160%) blur(6px);
    -webkit-backdrop-filter: saturate(160%) blur(6px);
    background:
        linear-gradient(180deg,
            rgba(247,248,251,.97) 0%,
            rgba(247,248,251,.82) 100%) !important;
    box-shadow:
        inset 0 -1px 0 0 color-mix(in srgb, var(--cc-border) 75%, transparent),
        0 6px 10px -8px rgba(15,13,38,.10);
    font-family: var(--cc-body) !important;
    font-size: 0.60rem !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase;
    color: var(--cc-text-mute) !important;
    font-weight: 700 !important;
    padding: 12px 10px !important;
}
.el-tf-shell thead th + th {
    border-left: 1px dashed color-mix(in srgb, var(--cc-border) 55%, transparent);
}
.el-tf-shell thead th:first-child { border-top-left-radius: 10px; }
.el-tf-shell thead th:last-child  { border-top-right-radius: 10px; }

/* Zebra striping with a teal warmth, kept very subtle */
.el-tf-shell tbody tr { position: relative; transition: background .14s ease; }
.el-tf-shell tbody tr:nth-child(even) > td {
    background: color-mix(in srgb, var(--cc-teal) 2%, transparent);
}
.el-tf-shell tbody tr:hover > td {
    background: color-mix(in srgb, var(--cc-teal) 6%, transparent) !important;
}
.el-tf-shell.is-inventory tbody tr:hover > td {
    background: color-mix(in srgb, var(--cc-accent) 5%, transparent) !important;
}
.el-tf-shell tbody tr:hover > td:first-child {
    box-shadow: inset 3px 0 0 0 var(--cc-teal) !important;
}
.el-tf-shell.is-inventory tbody tr:hover > td:first-child {
    box-shadow: inset 3px 0 0 0 var(--cc-accent) !important;
}
/* Row focus-beam — a thin underglow that animates on hover */
.el-tf-shell tbody tr::after {
    content: '';
    position: absolute;
    left: 0; right: 0; bottom: 0; height: 1px;
    background: linear-gradient(90deg,
        transparent 0%,
        color-mix(in srgb, var(--cc-teal) 55%, transparent) 50%,
        transparent 100%);
    transform: scaleX(0);
    transform-origin: left center;
    transition: transform .32s cubic-bezier(.2,.7,.2,1);
    pointer-events: none;
    z-index: 0;
}
.el-tf-shell.is-inventory tbody tr::after {
    background: linear-gradient(90deg,
        transparent 0%,
        color-mix(in srgb, var(--cc-accent) 55%, transparent) 50%,
        transparent 100%);
}
.el-tf-shell tbody tr:hover::after { transform: scaleX(1); }

/* ── Freshness dot in the When column — compact signal of recency ────── */
.el-fresh-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: baseline;
    position: relative;
    top: -1px;
    box-shadow: 0 0 0 1px rgba(255,255,255,.85);
    flex-shrink: 0;
}
.el-fresh-dot.is-live {
    background: var(--cc-green);
    box-shadow:
        0 0 0 1px #fff,
        0 0 0 3px rgba(5,150,105,.22),
        0 0 10px 0 rgba(5,150,105,.55);
    animation: el-fresh-pulse 1.7s ease-in-out infinite;
}
.el-fresh-dot.is-fresh {
    background: var(--cc-teal);
    box-shadow: 0 0 0 1px #fff, 0 0 6px 0 rgba(13,148,136,.45);
}
.el-fresh-dot.is-today {
    background: var(--cc-blue);
    box-shadow: 0 0 0 1px #fff, 0 0 4px 0 rgba(59,130,246,.30);
}
.el-fresh-dot.is-week {
    background: color-mix(in srgb, var(--cc-text-mute) 55%, var(--cc-blue));
    box-shadow: 0 0 0 1px #fff;
}
.el-fresh-dot.is-older {
    background: var(--cc-text-mute);
    opacity: .50;
}
@keyframes el-fresh-pulse {
    0%, 100% {
        transform: scale(1);
        box-shadow:
            0 0 0 1px #fff,
            0 0 0 3px rgba(5,150,105,.22),
            0 0 10px 0 rgba(5,150,105,.55);
    }
    50% {
        transform: scale(1.22);
        box-shadow:
            0 0 0 1px #fff,
            0 0 0 7px rgba(5,150,105,.12),
            0 0 16px 3px rgba(5,150,105,.60);
    }
}

/* When-column relative-age row — wraps the dot + text so alignment is
   predictable across event types. */
.el-when-rel {
    display: flex;
    align-items: center;
    gap: 0;
}

/* ── Pulse-tile area sparkline: endpoint gets a soft expanding ping ring ─ */
.iv-pulse-spark-dot {
    transform-box: fill-box;
    transform-origin: center;
    animation: iv-spark-endpoint-dot 2.6s ease-in-out infinite;
}
.iv-pulse-spark-ping {
    transform-box: fill-box;
    transform-origin: center;
    animation: iv-spark-endpoint-ping 2.6s ease-out infinite;
}
@keyframes iv-spark-endpoint-dot {
    0%, 100% { opacity: 1; }
    50%      { opacity: .85; }
}
@keyframes iv-spark-endpoint-ping {
    0%   { r: 2.4; opacity: .55; }
    70%  { r: 7;   opacity: 0;   }
    100% { r: 7;   opacity: 0;   }
}

/* ── Activity ribbon: weekend bands + peak marker ──────────────────────── */
.el-ribbon-weekend {
    opacity: .10;
    pointer-events: none;
}
.el-ribbon-peak { opacity: .95; }
.el-ribbon-peak-line {
    stroke: color-mix(in srgb, var(--cc-amber) 90%, transparent);
    stroke-width: 1;
    stroke-dasharray: 2 2;
    opacity: .55;
}
.el-ribbon-peak-label {
    font-family: var(--cc-data);
    font-size: 8px;
    fill: var(--cc-amber);
    letter-spacing: .04em;
    font-weight: 700;
    paint-order: stroke fill;
    stroke: rgba(255,255,255,.65);
    stroke-width: 2.5px;
    stroke-linejoin: round;
}

/* ── Project-timeline node: faint inner ring on the timeline dot when the
   section is the first in view — reads as "you're here" anchor ──────── */
.el-proj-stack .el-proj-section:first-child::after {
    box-shadow:
        0 0 0 2px #fff,
        0 0 0 4px color-mix(in srgb, var(--cc-teal) 40%, transparent),
        0 0 14px 1px color-mix(in srgb, var(--cc-teal) 50%, transparent);
}

/* ── Inventory table shell: stage-column header accent ─────────────────── */
.el-tf-shell.is-inventory thead th:nth-child(n+3) {
    background:
        linear-gradient(180deg,
            rgba(79,70,229,.06) 0%,
            rgba(247,248,251,.92) 18%,
            rgba(247,248,251,.82) 100%) !important;
}

/* ── Reduced motion: kill the new animations we added ──────────────────── */
@media (prefers-reduced-motion: reduce) {
    .el-fresh-dot.is-live   { animation: none; }
    .iv-pulse-spark-dot     { animation: none; }
    .iv-pulse-spark-ping    { animation: none; opacity: 0; }
    .el-tf-shell tbody tr::after {
        transition: none;
        transform: scaleX(1);
        opacity: .25;
    }
}

/* ==========================================================================
   STICKY RAIL HARDENING
   Streamlit wraps every container in nested `stVerticalBlock` flex parents.
   Any ancestor with `overflow: hidden/auto` breaks `position: sticky`, and
   any ancestor with a `transform`/`filter` creates a new containing block
   that re-anchors the sticky to the wrong scroll root. We explicitly target
   Streamlit's known wrappers to guarantee the rail pins to the viewport.
   ========================================================================== */

/* Force the rail to stick no matter how many CSS blocks above declared it.
   -webkit-sticky fallback for older Safari. */
.st-key-cc_filter_rail {
    position: -webkit-sticky !important;
    position: sticky !important;
    top: 0 !important;
    z-index: 950 !important;
}

/* Rail's direct + transitive ancestors up to the scroll root must not clip
   or transform. These are all Streamlit's own wrappers. */
[data-testid="stAppViewContainer"],
[data-testid="stMain"],
[data-testid="stMainBlockContainer"],
section.main,
.main,
.main .block-container,
[data-testid="stVerticalBlock"],
[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stElementContainer"] {
    overflow: visible !important;
    transform: none !important;
    filter: none !important;
}

/* The secondary filter bar sits below the rail. Its `top` must clear the
   rail's actual painted height (padding + borders). Re-pin it explicitly. */
.st-key-cc_filter_secondary {
    position: -webkit-sticky !important;
    position: sticky !important;
    top: 96px !important;
    z-index: 900 !important;
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
    """Parse a date value, convert to the configured display TZ, and format.

    Returns "" on failure. Internal time math still uses UTC; only the rendered
    output is shifted to Africa/Cairo (``DISPLAY_TZ``).
    """
    ts = parse_dt(value)
    if ts is None:
        return ""
    try:
        ts_local = ts.tz_convert(DISPLAY_TZ)
    except Exception:
        # Fall back to naive UTC if tz conversion fails for any reason
        ts_local = ts
    return ts_local.strftime(fmt)


def _relative_age(value: Any, *, now: datetime | None = None) -> str:
    """Short human-readable age: "12s", "5m", "3h", "2d", "3w", "4mo", "2y" ago.

    Returns "" when ``value`` can't be parsed. Negative deltas (future dates) are
    rendered with an "in …" prefix instead of " ago".
    """
    ts = parse_dt(value)
    if ts is None:
        return ""
    _now = now or datetime.now(timezone.utc)
    try:
        _delta_s = (_now - ts.to_pydatetime()).total_seconds()
    except Exception:
        return ""
    _future = _delta_s < 0
    _s = abs(_delta_s)
    if _s < 45:
        _tok = f"{int(_s)}s"
    elif _s < 60 * 45:
        _tok = f"{int(round(_s / 60))}m"
    elif _s < 3600 * 22:
        _tok = f"{int(round(_s / 3600))}h"
    elif _s < 86400 * 6:
        _tok = f"{int(round(_s / 86400))}d"
    elif _s < 86400 * 28:
        _tok = f"{int(round(_s / (86400 * 7)))}w"
    elif _s < 86400 * 330:
        _tok = f"{int(round(_s / (86400 * 30)))}mo"
    else:
        _tok = f"{int(round(_s / (86400 * 365)))}y"
    return f"in {_tok}" if _future else f"{_tok} ago"


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


def _hit_date(hit: dict, family: str) -> Any:
    """Best-effort date extraction from an ES hit.

    Prefers the ``sort`` value (epoch-ms when sorted by a date field — always
    parseable), then falls back to ``_pick_date`` on ``_source``, then scans
    every ``_source`` value for anything that looks like a date.
    """
    # 1. Sort value — ES returns epoch-ms for date sorts, guaranteed parseable
    sort_vals = hit.get("sort")
    if isinstance(sort_vals, list) and sort_vals:
        sv = sort_vals[0]
        # Skip ES sentinel for missing values (max long)
        if isinstance(sv, (int, float)) and sv not in (9223372036854775807, -9223372036854775808):
            return sv
    src = hit.get("_source", {}) or {}
    # 2. Known candidate fields
    v = _pick_date(src, family)
    if v is not None:
        return v
    # 3. Last-ditch scan: any ISO-8601-looking string or epoch-ms in the source
    for key, val in src.items():
        if val is None:
            continue
        if isinstance(val, (int, float)) and 1e9 < val < 4e12:
            return val  # plausible epoch-s or epoch-ms
        if isinstance(val, str) and len(val) >= 10 and val[0:4].isdigit() and val[4] == "-":
            return val
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


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_prd_status(apps: tuple[str, ...]) -> dict[str, dict]:
    """For each application, return the current prd deployment snapshot.

    ``live`` means the app has a SUCCESSFUL prd deployment on record (the app
    is actually running in prd). ``version`` is the latest successful prd
    version — what's actually live right now. ``status`` reports the most
    recent prd deployment status, which may differ (e.g. a newer deploy failed
    on top of an older successful one — we still consider the app live at the
    last-successful version).
    """
    if not apps:
        return {}
    try:
        resp = es_search(
            IDX["deployments"],
            {
                "query": {
                    "bool": {
                        "filter": [
                            {"terms": {"application": list(apps)}},
                            {"term": {"environment": "prd"}},
                        ]
                    }
                },
                "aggs": {
                    "by_app": {
                        "terms": {"field": "application", "size": len(apps)},
                        "aggs": {
                            # Absolute latest — reports last-attempted status
                            "latest": {
                                "top_hits": {
                                    "size": 1,
                                    "sort": [{"startdate": {"order": "desc", "unmapped_type": "date"}}],
                                    "_source": ["application", "codeversion", "status", "startdate"],
                                }
                            },
                            # Latest among successful-only — reports the version
                            # that is actually live in prd right now.
                            "latest_success": {
                                "filter": {"terms": {"status": SUCCESS_STATUSES}},
                                "aggs": {
                                    "hit": {
                                        "top_hits": {
                                            "size": 1,
                                            "sort": [{"startdate": {"order": "desc", "unmapped_type": "date"}}],
                                            "_source": ["application", "codeversion", "status", "startdate"],
                                        }
                                    }
                                }
                            },
                        }
                    }
                }
            },
            size=0,
        )
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for _b in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
        _app = _b.get("key")
        if not _app:
            continue
        _latest_hits  = _b.get("latest", {}).get("hits", {}).get("hits", [])
        _succ_hits    = _b.get("latest_success", {}).get("hit", {}).get("hits", {}).get("hits", [])
        _last_s  = (_latest_hits[0].get("_source") if _latest_hits else {}) or {}
        _succ_s  = (_succ_hits[0].get("_source")   if _succ_hits   else {}) or {}
        _live_version = _succ_s.get("codeversion", "") or ""
        out[_app] = {
            "live":           bool(_succ_s),
            "version":        _live_version,
            "when":           _succ_s.get("startdate", "") or "",
            "status":         _last_s.get("status", "") or "",
            # Extra context so popovers can show "last attempt failed" etc.
            "last_version":   _last_s.get("codeversion", "") or "",
            "last_when":      _last_s.get("startdate", "") or "",
            "last_succeeded": bool(_succ_s) and _succ_s.get("codeversion") == _last_s.get("codeversion"),
        }
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_prismacloud(app_versions: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], dict]:
    """Fetch the most-recent prismacloud scan for each ``(app, version)`` pair.

    Returns ``{(app, version): {Vcritical, Vhigh, Vmedium, Vlow, Ccritical,
    Chigh, Cmedium, Clow, status, when, imageName, imageTag}}``. Pairs with no
    matching scan are omitted — the caller treats that as "no prisma data".
    """
    if not app_versions:
        return {}
    # Unique apps → one aggregation per app that buckets by codeversion, then
    # top_hits for the freshest scan of each version.
    apps = sorted({_a for _a, _ in app_versions if _a})
    if not apps:
        return {}
    # The prismacloud index declares ``application`` and ``codeversion`` as
    # top-level ``keyword`` fields (no ``.keyword`` subfield), so the terms
    # query / agg has to target the bare names here.
    try:
        resp = es_search(
            IDX["prismacloud"],
            {
                "query": {"bool": {"filter": [{"terms": {"application": apps}}]}},
                "aggs": {
                    "by_app": {
                        "terms": {"field": "application", "size": len(apps)},
                        "aggs": {
                            "by_ver": {
                                "terms": {"field": "codeversion", "size": 200},
                                "aggs": {
                                    "latest": {
                                        "top_hits": {
                                            "size": 1,
                                            "sort": [{"enddate": {"order": "desc", "unmapped_type": "date"}}],
                                            "_source": [
                                                "application", "codeversion", "status",
                                                "Vcritical", "Vhigh", "Vmedium", "Vlow",
                                                "Ccritical", "Chigh", "Cmedium", "Clow",
                                                "imageName", "imageTag", "enddate", "startdate",
                                            ],
                                        }
                                    }
                                },
                            }
                        },
                    }
                },
            },
            size=0,
        )
    except Exception:
        return {}
    wanted = {(a, v) for a, v in app_versions if a and v}
    out: dict[tuple[str, str], dict] = {}
    for _ab in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
        _app = _ab.get("key")
        for _vb in _ab.get("by_ver", {}).get("buckets", []):
            _ver = _vb.get("key")
            _hits = _vb.get("latest", {}).get("hits", {}).get("hits", [])
            if not _hits:
                continue
            _s = _hits[0].get("_source", {}) or {}
            key = (_app, _ver)
            if wanted and key not in wanted:
                continue
            out[key] = {
                "Vcritical": int(_s.get("Vcritical") or 0),
                "Vhigh":     int(_s.get("Vhigh")     or 0),
                "Vmedium":   int(_s.get("Vmedium")   or 0),
                "Vlow":      int(_s.get("Vlow")      or 0),
                "Ccritical": int(_s.get("Ccritical") or 0),
                "Chigh":     int(_s.get("Chigh")     or 0),
                "Cmedium":   int(_s.get("Cmedium")   or 0),
                "Clow":      int(_s.get("Clow")      or 0),
                "status":    _s.get("status", "")    or "",
                "imageName": _s.get("imageName", "") or "",
                "imageTag":  _s.get("imageTag", "")  or "",
                "when":      _s.get("enddate") or _s.get("startdate") or "",
            }
    return out


# Stage ordering drives the inventory columns and the "previous stage" chain
# used for Δ-vs-previous-stage comparisons in stage popovers.
_STAGE_ORDER = ("build", "dev", "qc", "release", "uat", "prd")
_STAGE_PREV  = {"dev": "build", "qc": "dev", "release": "qc", "uat": "release", "prd": "uat"}
_STAGE_LABEL = {
    "build":   "Latest build",
    "dev":     "Latest dev deploy",
    "qc":      "Latest qc deploy",
    "release": "Latest release",
    "uat":     "Latest uat deploy",
    "prd":     "Latest prd deploy",
}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_latest_stages(apps: tuple[str, ...]) -> dict[str, dict[str, dict]]:
    """For each application, fetch the latest record at each stage.

    A "stage" is one of: build (ef-cicd-builds), release (ef-cicd-releases),
    or a deployment in a given environment (dev/qc/uat/prd on
    ef-cicd-deployments).

    Returns::

        {app: {stage: {"version": str, "when": iso-str, "status": str}}}

    Stages with no data are simply absent from the inner dict.
    """
    if not apps:
        return {}
    apps_list = list(apps)
    out: dict[str, dict[str, dict]] = {a: {} for a in apps_list}

    def _sort_by(date_field: str) -> list[dict]:
        return [{date_field: {"order": "desc", "unmapped_type": "date"}}]

    # Broader _source so _hit_date can fall back through alternative date fields.
    _BUILD_SRC   = ["application", "codeversion", "status",
                    "startdate", "StartDate", "start_date",
                    "enddate", "created", "timestamp", "@timestamp"]
    _RELEASE_SRC = ["application", "codeversion", "status",
                    "releasedate", "ReleaseDate", "release_date",
                    "created", "timestamp", "@timestamp"]
    _DEPLOY_SRC  = _BUILD_SRC + ["environment"]

    # ---- builds (startdate) ------------------------------------------------
    try:
        resp = es_search(
            IDX["builds"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": apps_list}},
                ]}},
                "aggs": {
                    "by_app": {
                        "terms": {"field": "application", "size": len(apps_list)},
                        "aggs": {"latest": {"top_hits": {
                            "size": 1, "sort": _sort_by("startdate"),
                            "_source": _BUILD_SRC,
                        }}},
                    }
                },
            },
            size=0,
        )
        for _b in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
            _hits = _b.get("latest", {}).get("hits", {}).get("hits", [])
            if not _hits:
                continue
            _h = _hits[0]
            _s = _h.get("_source", {}) or {}
            _app = _s.get("application") or _b.get("key")
            if _app in out:
                out[_app]["build"] = {
                    "version": _s.get("codeversion", "") or "",
                    "when":    _hit_date(_h, "build") or "",
                    "status":  _s.get("status", "") or "",
                }
    except Exception:
        pass

    # ---- releases (releasedate) -------------------------------------------
    try:
        resp = es_search(
            IDX["releases"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": apps_list}},
                ]}},
                "aggs": {
                    "by_app": {
                        "terms": {"field": "application", "size": len(apps_list)},
                        "aggs": {"latest": {"top_hits": {
                            "size": 1, "sort": _sort_by("releasedate"),
                            "_source": _RELEASE_SRC,
                        }}},
                    }
                },
            },
            size=0,
        )
        for _b in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
            _hits = _b.get("latest", {}).get("hits", {}).get("hits", [])
            if not _hits:
                continue
            _h = _hits[0]
            _s = _h.get("_source", {}) or {}
            _app = _s.get("application") or _b.get("key")
            if _app in out:
                out[_app]["release"] = {
                    "version": _s.get("codeversion", "") or "",
                    "when":    _hit_date(_h, "release") or "",
                    "status":  _s.get("status", "") or "",
                }
    except Exception:
        pass

    # ---- deployments split by environment (startdate) ---------------------
    try:
        resp = es_search(
            IDX["deployments"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": apps_list}},
                    {"terms": {"environment": ["dev", "qc", "uat", "prd"]}},
                ]}},
                "aggs": {
                    "by_app": {
                        "terms": {"field": "application", "size": len(apps_list)},
                        "aggs": {
                            "by_env": {
                                "terms": {"field": "environment", "size": 4},
                                "aggs": {"latest": {"top_hits": {
                                    "size": 1, "sort": _sort_by("startdate"),
                                    "_source": _DEPLOY_SRC,
                                }}},
                            }
                        },
                    }
                },
            },
            size=0,
        )
        for _b in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
            _app = _b.get("key")
            if _app not in out:
                continue
            for _eb in _b.get("by_env", {}).get("buckets", []):
                _env = _eb.get("key")
                _hits = _eb.get("latest", {}).get("hits", {}).get("hits", [])
                if not _env or not _hits:
                    continue
                _h = _hits[0]
                _s = _h.get("_source", {}) or {}
                out[_app][_env] = {
                    "version": _s.get("codeversion", "") or "",
                    "when":    _hit_date(_h, "deploy") or "",
                    "status":  _s.get("status", "") or "",
                }
    except Exception:
        pass

    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_version_meta(app_versions: tuple[tuple[str, str], ...]
                        ) -> dict[tuple[str, str], dict]:
    """For each ``(app, version)`` pair, return build-date, release-date, RLM.

    Returns ``{(app, ver): {"build_when": str, "release_when": str,
    "rlm": str, "rlm_status": str}}`` — missing lookups are simply absent
    (callers treat that as "no record").
    """
    if not app_versions:
        return {}
    apps = sorted({_a for _a, _ in app_versions if _a})
    if not apps:
        return {}
    wanted = {(a, v) for a, v in app_versions if a and v}
    out: dict[tuple[str, str], dict] = {}

    def _set(key: tuple[str, str], field: str, val: str) -> None:
        if not val or key not in wanted:
            return
        out.setdefault(key, {})[field] = val

    _BUILD_META_SRC = [
        "application", "codeversion",
        "startdate", "StartDate", "start_date",
        "enddate", "created", "timestamp", "@timestamp",
    ]
    _RELEASE_META_SRC = [
        "application", "codeversion", "RLM", "RLM_STATUS",
        "releasedate", "ReleaseDate", "release_date",
        "created", "timestamp", "@timestamp",
    ]

    # ---- builds: newest record per (app, codeversion) ---------------------
    try:
        resp = es_search(
            IDX["builds"],
            {
                "query": {"bool": {"filter": [{"terms": {"application": apps}}]}},
                "aggs": {
                    "by_app": {
                        "terms": {"field": "application", "size": len(apps)},
                        "aggs": {
                            "by_ver": {
                                "terms": {"field": "codeversion", "size": 300},
                                "aggs": {"latest": {"top_hits": {
                                    "size": 1,
                                    "sort": [{"startdate": {"order": "desc",
                                                            "unmapped_type": "date"}}],
                                    "_source": _BUILD_META_SRC,
                                }}},
                            }
                        },
                    }
                },
            },
            size=0,
        )
        for _ab in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
            _app = _ab.get("key")
            for _vb in _ab.get("by_ver", {}).get("buckets", []):
                _ver = _vb.get("key")
                _hits = _vb.get("latest", {}).get("hits", {}).get("hits", [])
                if not _app or not _ver or not _hits:
                    continue
                _h = _hits[0]
                _when = _hit_date(_h, "build")
                if _when:
                    _set((_app, _ver), "build_when", str(_when))
    except Exception:
        pass

    # ---- releases: newest record per (app, codeversion) -------------------
    try:
        resp = es_search(
            IDX["releases"],
            {
                "query": {"bool": {"filter": [{"terms": {"application": apps}}]}},
                "aggs": {
                    "by_app": {
                        "terms": {"field": "application", "size": len(apps)},
                        "aggs": {
                            "by_ver": {
                                "terms": {"field": "codeversion", "size": 300},
                                "aggs": {"latest": {"top_hits": {
                                    "size": 1,
                                    "sort": [{"releasedate": {"order": "desc",
                                                              "unmapped_type": "date"}}],
                                    "_source": _RELEASE_META_SRC,
                                }}},
                            }
                        },
                    }
                },
            },
            size=0,
        )
        for _ab in resp.get("aggregations", {}).get("by_app", {}).get("buckets", []):
            _app = _ab.get("key")
            for _vb in _ab.get("by_ver", {}).get("buckets", []):
                _ver = _vb.get("key")
                _hits = _vb.get("latest", {}).get("hits", {}).get("hits", [])
                if not _app or not _ver or not _hits:
                    continue
                _h = _hits[0]
                _s = _h.get("_source", {}) or {}
                _when = _hit_date(_h, "release")
                if _when:
                    _set((_app, _ver), "release_when", str(_when))
                _rlm = (_s.get("RLM") or "").strip()
                if _rlm:
                    _set((_app, _ver), "rlm", _rlm)
                _rst = (_s.get("RLM_STATUS") or "").strip()
                if _rst:
                    _set((_app, _ver), "rlm_status", _rst)
    except Exception:
        pass

    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_inventory_details(apps: tuple[str, ...]) -> dict[str, dict]:
    """Batch-fetch inventory records for the given applications.

    Returns ``{application_name: {build_technology, deploy_technology,
    deploy_platform, build_image_name, build_image_tag, deploy_image_name,
    deploy_image_tag, company, project}}``. Missing fields are omitted.
    """
    if not apps:
        return {}
    try:
        resp = es_search(
            IDX["inventory"],
            {
                "query": {"terms": {"application.keyword": list(apps)}},
                "_source": [
                    "application", "company", "project", "app_type",
                    "build_technology", "deploy_technology", "deploy_platform",
                    "build_image", "deploy_image",
                    "build_image.name", "build_image.tag",
                    "deploy_image.name", "deploy_image.tag",
                ],
            },
            size=len(apps),
        )
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for _h in resp.get("hits", {}).get("hits", []):
        _s = _h.get("_source", {}) or {}
        _app = _s.get("application")
        if not _app:
            continue
        _bi = _s.get("build_image") or {}
        _di = _s.get("deploy_image") or {}
        # ES may index either nested or flattened — fall back gracefully
        _bi_name = (_bi.get("name") if isinstance(_bi, dict) else None) or _s.get("build_image.name", "")
        _bi_tag  = (_bi.get("tag")  if isinstance(_bi, dict) else None) or _s.get("build_image.tag", "")
        _di_name = (_di.get("name") if isinstance(_di, dict) else None) or _s.get("deploy_image.name", "")
        _di_tag  = (_di.get("tag")  if isinstance(_di, dict) else None) or _s.get("deploy_image.tag", "")
        out[_app] = {
            "company":            _s.get("company", ""),
            "project":            _s.get("project", ""),
            "app_type":           (_s.get("app_type") or "").strip(),
            "build_technology":   _s.get("build_technology", ""),
            "deploy_technology":  _s.get("deploy_technology", ""),
            "deploy_platform":    _s.get("deploy_platform", ""),
            "build_image_name":   _bi_name or "",
            "build_image_tag":    _bi_tag  or "",
            "deploy_image_name":  _di_name or "",
            "deploy_image_tag":   _di_tag  or "",
        }
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_project_details(projects: tuple[str, ...]) -> dict[str, dict]:
    """Batch-fetch a summary record per project from the inventory.

    Returns ``{project: {"company": str, "teams": {field: [values]},
    "apps": [app names]}}`` where ``field`` is any inventory field ending in
    ``_team``. Company is picked from any matching inventory record (apps in
    the same project normally share a company). Missing values are omitted.
    """
    if not projects:
        return {}
    try:
        resp = es_search(
            IDX["inventory"],
            {
                "query": {"terms": {"project.keyword": list(projects)}},
                "_source": ["application", "project", "company", "*_team"],
            },
            size=2000,
        )
    except Exception:
        return {}
    out: dict[str, dict] = {
        p: {"teams": {}, "apps": set(), "companies": set()} for p in projects
    }
    for _h in resp.get("hits", {}).get("hits", []):
        _s = _h.get("_source", {}) or {}
        _p = _s.get("project")
        if not _p or _p not in out:
            continue
        _app = _s.get("application")
        if _app:
            out[_p]["apps"].add(_app)
        _co = _s.get("company")
        if _co:
            out[_p]["companies"].add(str(_co))
        for _k, _v in _s.items():
            if not _k.endswith("_team") or not _v:
                continue
            # Some indices may store arrays; normalise to a flat set
            if isinstance(_v, (list, tuple, set)):
                for _item in _v:
                    if _item:
                        out[_p]["teams"].setdefault(_k, set()).add(str(_item))
            else:
                out[_p]["teams"].setdefault(_k, set()).add(str(_v))
    # Normalise sets to sorted lists for deterministic rendering
    result: dict[str, dict] = {}
    for _p, _data in out.items():
        _cos = sorted(_data["companies"])
        result[_p] = {
            "company": ", ".join(_cos) if _cos else "",
            "teams":   {_f: sorted(_s) for _f, _s in _data["teams"].items() if _s},
            "apps":    sorted(_data["apps"]),
        }
    return result


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_projects_for_role_teams(role: str, teams: tuple[str, ...]) -> list[str]:
    """Return inventory projects where the role's team field(s) match any of ``teams``.

    Developer → ``dev_team``; QC → ``qc_team``; Operator → ``uat_team``/``prd_team``.
    Admin (or an empty team list) returns an empty list to signal "no scoping".
    """
    fields = ROLE_TEAM_FIELDS.get(role, [])
    if not fields or not teams:
        return []
    should = [{"terms": {f: list(teams)}} for f in fields]
    query = {"bool": {"should": should, "minimum_should_match": 1}}
    try:
        return sorted(composite_terms(IDX["inventory"], "project.keyword", query).keys())
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


# ── Resolve role early so the filter rail can style itself by role color ────
if "Admin" in _detected_roles:
    role_pick = "Admin"
else:
    role_pick = _detected_roles[0]

# Time-window presets — resolved before the rail so selectbox order is stable.
_TW_LABELS = list(PRESETS.keys())
_preset_default_idx = _TW_LABELS.index("7d")

# ── Role-scoped visibility flags — relied on by scope filters + sections ───
_ROLE_SHOWS_JIRA: dict[str, bool] = {
    "Admin": True, "Developer": True, "QC": True, "Operator": False,
}
_ROLE_SHOWS_BUILDS: dict[str, bool] = {
    "Admin": True, "Developer": True, "QC": False, "Operator": False,
}
_ROLE_EVENT_TYPES: dict[str, list[str]] = {
    "Admin":     ["Build-develop", "Build-release", "Deployments", "Releases", "Requests", "Commits"],
    "Developer": ["Commits", "Build-develop", "Build-release", "Deployments"],
    "QC":        ["Deployments", "Releases", "Requests"],
    "Operator":  ["Deployments", "Releases", "Requests"],
}
_ROLE_ENVS: dict[str, list[str]] = {
    "Admin":     ["prd", "uat", "qc", "dev"],
    "Developer": ["dev"],
    "QC":        ["qc"],
    "Operator":  ["uat", "prd"],
}
_ROLE_APPROVAL_STAGES: dict[str, list[str]] = {
    "Admin":     [],
    "Developer": [],
    "QC":        ["qc", "request_deploy_qc", "request_promote"],
    "Operator":  ["uat", "prd", "request_deploy_uat", "request_deploy_prd", "request_promote"],
}
_effective_role = role_pick
_is_admin = (_effective_role == "Admin")

# Team auto-detection (from st.session_state.teams) — resolves team_filter and
# the _active_teams list that drive project/company scope queries downstream.
if _session_teams:
    _active_teams: list[str] = list(_session_teams)
    if len(_session_teams) == 1:
        team_filter = _session_teams[0]
        _team_display = _session_teams[0]
    else:
        team_filter = ""  # union scope
        _team_display = " · ".join(_session_teams)
else:
    team_filter = ""
    _active_teams = []
    _team_display = "— no team —"

if team_filter:
    if role_pick == "Admin":
        _admin_team_apps: set[str] = set()
        for _r in ["Developer", "QC", "Operator"]:
            _admin_team_apps.update(_load_team_applications(_r, team_filter))
        _team_apps = sorted(_admin_team_apps)
    else:
        _team_apps = _load_team_applications(role_pick, team_filter)
elif role_pick != "Admin" and _active_teams:
    _union: set[str] = set()
    for _t in _active_teams:
        _union.update(_load_team_applications(role_pick, _t))
    _team_apps = sorted(_union)
else:
    _team_apps = []

# Resolve project scope before the rail so the project dropdown respects
# admin_view_all + team assignment without re-querying per widget.
admin_view_all = bool(st.session_state.get("admin_view_all", False))
if role_pick == "Admin":
    if admin_view_all:
        _proj_scoped = _all_projects
        _proj_help = f"{len(_all_projects)} projects · view-all ON"
    else:
        _candidate_teams = _active_teams or _session_teams
        if _candidate_teams:
            _proj_scoped = _load_projects_for_role_teams("Developer", tuple(_candidate_teams))
            _proj_help = (
                f"{len(_proj_scoped)} project(s) where dev_team ∈ "
                f"{', '.join(_candidate_teams)} — toggle 'view all' to lift"
            )
        else:
            _proj_scoped = _all_projects
            _proj_help = f"{len(_all_projects)} projects (no admin team)"
elif _active_teams:
    _proj_scoped = _load_projects_for_role_teams(role_pick, tuple(_active_teams))
    _proj_help = (
        f"{len(_proj_scoped)} project(s) where {role_pick.lower()} team ∈ "
        f"{', '.join(_active_teams)}"
    )
else:
    _proj_scoped = []
    _proj_help = "No projects visible — no team assigned"

_role_clr = ROLE_COLORS[role_pick]
_role_icon = ROLE_ICONS[role_pick]

# =============================================================================
# PIPELINES INVENTORY — unified filter bar (global + facet filters live here)
# =============================================================================
# Every pre-inventory filter, scope, and toggle lives inside this container.
# The .st-key-cc_filter_rail CSS rule pins it to the viewport top and styles
# it with a blurred surface so the stat tiles + table (and nested event log)
# flow beneath it as one continuous surface.
with st.container(key="cc_filter_rail"):
    # Minimal rail: role identity · persistent search · settings cog.
    # Company/Project live in the inventory's "Filters & sort" popover below,
    # which is pinned sticky just under this rail so both stay visible while
    # scrolling. Time window moved into the settings popover — the event log
    # carries its own window selector so the global one is rarely touched.
    _rail = st.columns(
        [1.4, 5.8, 0.7],
        vertical_alignment="bottom",
    )

    # ── Col 0: compact identity badge (role + team) ────────────────────────
    with _rail[0]:
        st.markdown(
            f'<div class="cc-rail-id">'
            f'<div class="cc-rail-id-role" '
            f'style="color:{_role_clr};border-color:{_role_clr}55;'
            f'background:{_role_clr}0F">{_role_icon} {role_pick}</div>'
            f'<div class="cc-rail-id-team" title="{_team_display}">{_team_display}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Col 1: persistent ops search (shared by event log + inventory) ─────
    with _rail[1]:
        st.text_input(
            "Search",
            key="shared_search_v1",
            placeholder="🔎  app · project · version · tech · person · detail…  (space-separated terms are AND)",
            help="Shared across event log and inventory · case-insensitive · "
                 "space-separated terms are AND",
            label_visibility="collapsed",
        )

    # ── Col 2: settings popover (window, toggles, reload) ─────────────────
    with _rail[2]:
        with st.popover("⚙", help="Time window · toggles · cache reload",
                        use_container_width=True):
            st.markdown(
                '<div class="iv-pill-caption">Global time window</div>',
                unsafe_allow_html=True,
            )
            preset = st.selectbox(
                "Window",
                _TW_LABELS,
                index=_preset_default_idx,
                key="time_preset",
                label_visibility="collapsed",
                help="Query time window for admin analytics · the event log "
                     "and inventory have their own scopes",
            )
            st.markdown(
                '<div style="border-top:1px solid var(--cc-border);margin:10px 0 6px"></div>',
                unsafe_allow_html=True,
            )
            st.toggle(
                "Per-project tables", value=False, key="shared_per_project_v1",
                help="Group rows into a separate table per project",
            )
            if role_pick == "Admin":
                st.toggle(
                    "Admin: view all projects", value=admin_view_all, key="admin_view_all",
                    help="Bypass the default dev_team scoping and see every project",
                )
            auto_refresh = st.toggle(
                "Auto-refresh (60s)", value=False, key="auto_refresh",
                help="Rerun the page every 60 seconds",
            )
            exclude_svc = st.toggle(
                "Exclude service accounts", value=True, key="exclude_svc",
                help="Hide 'azure_sql' service account commits",
            )
            st.markdown(
                '<div style="border-top:1px solid var(--cc-border);margin:6px 0 4px"></div>',
                unsafe_allow_html=True,
            )
            if st.button("↻ Clear cache & reload", key="settings_reload",
                         use_container_width=True):
                st.cache_data.clear()
                st.rerun()

    # Global company/project pickers were removed from the rail — the
    # inventory's "Filters & sort" popover (pinned below) is the canonical
    # place to narrow scope. We default both to empty so every ES query is
    # unscoped at this layer; the inventory popover applies its own multiselect
    # on top, and the event log inherits the inventory-filtered app set.
    company_filter = ""
    project_filter = ""

    # Resolve the selected window → start/end timestamps. `preset` is read from
    # the settings popover (defaults to 7d on first paint).
    if preset == "Custom":
        # Rail no longer exposes a Custom range picker — fall back to 7d.
        end_dt   = datetime.now(timezone.utc)
        start_dt = end_dt - PRESETS["7d"]
    elif preset == "All-time":
        end_dt   = datetime.now(timezone.utc)
        start_dt = datetime(2000, 1, 1, tzinfo=timezone.utc)
    else:
        end_dt   = datetime.now(timezone.utc)
        start_dt = end_dt - PRESETS[preset]  # type: ignore[operator]

    delta       = end_dt - start_dt
    prior_end   = start_dt
    prior_start = start_dt - delta
    interval    = pick_interval(delta)
    now_utc     = datetime.now(timezone.utc)
    pending_window_start = now_utc - timedelta(days=30)

    _start_local = start_dt.astimezone(DISPLAY_TZ)
    _end_local   = end_dt.astimezone(DISPLAY_TZ)
    _now_local   = now_utc.astimezone(DISPLAY_TZ)
    _window_label = (
        "All-time" if preset == "All-time"
        else f"{_start_local:%Y-%m-%d %H:%M} → {_end_local:%Y-%m-%d %H:%M} {DISPLAY_TZ_LABEL}"
    )
    # Inline meta row — date range, bucket, apps scope, service-account hint.
    _apps_label = f"{len(_team_apps)} apps" if _team_apps else "all apps"
    _meta_bits = [
        f'<span><b>Range</b> {_window_label}</span>',
        f'<span><b>Bucket</b> {interval}</span>',
        f'<span><b>Scope</b> {_apps_label}</span>',
        f'<span><b>Updated</b> {_now_local:%H:%M} {DISPLAY_TZ_LABEL}</span>',
    ]
    if exclude_svc:
        _meta_bits.append('<span>⊘ azure_sql excluded</span>')
    st.markdown(
        '<div class="cc-rail-meta">' + ''.join(_meta_bits) + '</div>',
        unsafe_allow_html=True,
    )

# For non-admin roles with no specific project picked, restrict queries to
# the role's visible projects. For Admin, scope the same unless view-all.
_scoped_projects: list[str] = []
if not project_filter:
    if role_pick != "Admin":
        _scoped_projects = _proj_scoped
    elif not admin_view_all:
        _scoped_projects = _proj_scoped


# ── Role-based section visibility — admins see everything; other roles stay
# confined to the event log + inventory which are their primary surface.
_ROLE_PRIORITY_SECTIONS: dict[str, list[str]] = {
    "Admin":     ["eventlog", "inventory", "alerts", "landscape", "lifecycle", "pipeline", "workflow"],
    "Developer": ["eventlog", "inventory"],
    "QC":        ["eventlog", "inventory"],
    "Operator":  ["eventlog", "inventory"],
}
_visible = set(_ROLE_PRIORITY_SECTIONS.get(_effective_role, _ROLE_PRIORITY_SECTIONS["Admin"]))


def _show(section: str) -> bool:
    return section in _visible


# ── Pipelines inventory panel anchor + slot ───────────────────────────────
# Fragment definitions live far below; the st.empty() slot lets us render the
# view at the top of the page without forward-declaring ~2000 lines.
# The event log is no longer a sibling panel — it renders inside the inventory
# fragment so it inherits every filter the user selects on the inventory.
_show_el  = _show("eventlog")
_show_inv = _show("inventory")

if _show_el or _show_inv:
    st.markdown('<a class="anchor" id="sec-inventory"></a>', unsafe_allow_html=True)
    st.markdown('<a class="anchor" id="sec-eventlog"></a>', unsafe_allow_html=True)
    _inventory_slot = st.empty() if _show_inv else None


def scope_filters() -> list[dict]:
    """Base filters for operational indices (builds, deployments, commits, etc.)."""
    fs: list[dict] = []
    if company_filter:
        fs.append({"term": {"company.keyword": company_filter}})
    if project_filter:
        fs.append({"term": {"project": project_filter}})
    elif _scoped_projects:
        # Non-admin roles with no specific project → confine to role's visible set
        fs.append({"terms": {"project": _scoped_projects}})
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
    elif _scoped_projects:
        fs.append({"terms": {"project.keyword": _scoped_projects}})
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
# actionable items always sit at the top of the viewport. Only created for
# admin — non-admin users see only the event log.
_alerts_ph = st.container() if _is_admin else None


# HUD gamification panel, KPI trend cards, and event ticker were removed in
# the event-log-first redesign. Event log and inventory are now the primary
# surface; admin analytics live in the collapsible insights drawer below.


# ── Role-scoped event type / env / stage helpers ──────────────────────────
# The dicts themselves (_ROLE_EVENT_TYPES, _ROLE_ENVS, _ROLE_APPROVAL_STAGES,
# _ROLE_SHOWS_JIRA, _ROLE_SHOWS_BUILDS) are defined near the top of the page
# so downstream filtering can reuse them.


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


# =============================================================================
# EVENT LOG — TOP OF PAGE — fragment, auto-refresh every 60s, expandable
# =============================================================================


# ── styling helpers — module-level so the fragment re-uses them cheaply ────
_TYPE_BADGE = {
    # Build is split by branch — develop vs release — with distinct chips so the
    # eye can separate "pipeline churn" from "production-bound builds".
    "build-develop": ('<span style="background:#eef2ff;color:#6366f1;border-radius:4px;'
                      'padding:1px 7px;font-size:0.70rem;font-weight:700;letter-spacing:.02em">'
                      'BUILD · DEV</span>'),
    "build-release": ('<span style="background:#e0e7ff;color:#3730a3;border-radius:4px;'
                      'padding:1px 7px;font-size:0.70rem;font-weight:700;letter-spacing:.02em">'
                      'BUILD · REL</span>'),
    "deploy":  ('<span style="background:#dbeafe;color:#1d4ed8;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">DEPLOY</span>'),
    "release": ('<span style="background:#fce7f3;color:#be185d;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">RELEASE</span>'),
    "request": ('<span style="background:#fef3c7;color:#92400e;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">REQUEST</span>'),
    "commit":  ('<span style="background:#d1fae5;color:#065f46;border-radius:4px;'
                'padding:1px 7px;font-size:0.72rem;font-weight:700">COMMIT</span>'),
}


def _build_subtype(branch: str) -> str:
    """Classify a build event as ``build-release`` when the source branch name
    contains ``release`` (release/*, hotfix/release-*, etc.); otherwise
    ``build-develop`` covers feature/develop/main branches."""
    b = (branch or "").strip().lower()
    return "build-release" if "release" in b else "build-develop"


# Event-log time-window presets — user-facing labels → timedelta from "now".
# ``None`` is the "All time" sentinel; handled at query time by substituting a
# distant past date so the ES range filter still has a lower bound.
_EL_TIME_WINDOWS: dict[str, timedelta | None] = {
    "Last 15 min": timedelta(minutes=15),
    "Last 1h":     timedelta(hours=1),
    "Last 6h":     timedelta(hours=6),
    "Last 24h":    timedelta(hours=24),
    "Last 3d":     timedelta(days=3),
    "Last 7d":     timedelta(days=7),
    "Last 14d":    timedelta(days=14),
    "Last 30d":    timedelta(days=30),
    "Last 90d":    timedelta(days=90),
    "Last 180d":   timedelta(days=180),
    "Last 1y":     timedelta(days=365),
    "All time":    None,
}
# Lower bound substituted for the "All time" window — far enough in the past to
# cover the entire dataset but a real date so ES range queries stay well-formed.
_EL_ALLTIME_FLOOR = datetime(2000, 1, 1, tzinfo=timezone.utc)
_EL_SIZE_CAP = 500  # safety bound so a wide window doesn't drag the cluster


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


@st.fragment(run_every="60s")
def _render_event_log() -> None:
    """Inline event log — role-scoped, auto-refreshes every 60s independently."""
    # Role-allowed environments for the Env selector.
    _allowed_envs = _ROLE_ENVS.get(_effective_role, _ROLE_ENVS["Admin"])
    _env_options = ["(all)"] + _allowed_envs

    # ── Shared controls (Project / Search / Per-project) live above the
    # combined panel; only the view-specific Env + Time window are rendered
    # locally alongside the live-refresh badge.
    el_project_filter = _shared_project_filter()
    el_search = _shared_search_query()
    el_per_project = _shared_per_project()

    _el_r1 = st.columns([1.0, 1.3, 1.0])
    with _el_r1[0]:
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
            el_env = st.selectbox("Env", _env_options, key="el_env_v3")
    with _el_r1[1]:
        _el_tw_label = st.selectbox(
            "Time window", list(_EL_TIME_WINDOWS.keys()), index=3, key="el_time_v3",
            help="How far back to pull events for the log (independent of the page-wide window)",
        )
        _el_delta = _EL_TIME_WINDOWS[_el_tw_label]
    with _el_r1[2]:
        st.markdown(
            f'<div style="font-size:.65rem;color:var(--cc-text-mute);letter-spacing:.06em;'
            f'text-transform:uppercase;font-weight:600;margin-top:26px;white-space:nowrap">'
            f'↻ {datetime.now(DISPLAY_TZ).strftime("%H:%M:%S")} {DISPLAY_TZ_LABEL} · auto 60s</div>',
            unsafe_allow_html=True,
        )

    # ── Compute the event-log's own time window (independent of global) ─────
    _now_utc = datetime.now(timezone.utc)
    _el_start = _EL_ALLTIME_FLOOR if _el_delta is None else (_now_utc - _el_delta)
    _el_end   = _now_utc
    _size     = _EL_SIZE_CAP

    # Inventory-driven scope override — when the event log is rendered inside
    # the pipelines inventory, the inventory stashes its fully-filtered app
    # list here so every event-log query inherits those filters. An empty list
    # (explicit) means "inventory returned zero apps"; None / missing means
    # "no inventory scope active — fall back to global scope only".
    _el_inv_apps: list[str] | None = st.session_state.get("_el_inv_scope_apps")

    # ── Helper: merge the global scope filters with the local project pick ──
    def _el_scope(base: list[dict]) -> list[dict]:
        fs = list(base)
        if el_project_filter:
            # Override any global project match with the event-log's own pick
            fs = [f for f in fs if not (
                isinstance(f, dict) and "term" in f and "project" in f["term"]
            )]
            fs = [f for f in fs if not (
                isinstance(f, dict) and "terms" in f and "project" in f["terms"]
            )]
            fs.append({"term": {"project": el_project_filter}})
        if _el_inv_apps is not None:
            # Inventory scope takes precedence — drop any global application
            # restriction and replace it with the inventory's filtered set so
            # the event log matches the table above row-for-row.
            fs = [f for f in fs if not (
                isinstance(f, dict) and "terms" in f and "application" in f["terms"]
            )]
            fs = [f for f in fs if not (
                isinstance(f, dict) and "term" in f and "application" in f["term"]
            )]
            if _el_inv_apps:
                fs.append({"terms": {"application": list(_el_inv_apps)}})
            else:
                # Zero apps in scope — short-circuit with an impossible match
                fs.append({"terms": {"application": ["__no_match__"]}})
        return fs

    # Which build subtypes is the role allowed to see?
    _builds_allowed_subtypes: list[str] = []
    if _role_allows_type("Build-develop"):
        _builds_allowed_subtypes.append("build-develop")
    if _role_allows_type("Build-release"):
        _builds_allowed_subtypes.append("build-release")

    events: list[dict] = []

    # ── builds (split into build-develop / build-release by branch) ─────────
    # Always fetch every allowed subtype so the pill counts above the table
    # reflect reality even when some types are filtered out of the view.
    if _builds_allowed_subtypes:
        _bld_f = _el_scope([range_filter("startdate", _el_start, _el_end)] + list(scope_filters()))
        _bld_r = es_search(
            IDX["builds"],
            {"query": {"bool": {"filter": _bld_f}},
             "sort": [{"startdate": {"order": "desc", "unmapped_type": "date"}}]},
            size=_size,
        )
        for _h in _bld_r.get("hits", {}).get("hits", []):
            _s = _h.get("_source", {})
            _sub = _build_subtype(_s.get("branch", ""))
            if _sub not in _builds_allowed_subtypes:
                continue
            _dv = _hit_date(_h, "build")
            events.append({
                "_ts":         parse_dt(_dv),
                "type":        _sub,
                "When":        fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":         _s.get("application") or _s.get("project", ""),
                "Project":     _s.get("project", ""),
                "Environment": "",
                "Version":     _s.get("codeversion", ""),
                "Detail":      f'{_s.get("branch","")} · {_s.get("technology","")}',
                "Status":      _s.get("status", ""),
                "Requester":   _s.get("requester", ""),
                "Approver":    _s.get("approver", ""),
                "Extra":       "",
            })

    # ── deployments (role-filtered env) ─────────────────────────────────────
    if _role_allows_type("Deployments"):
        _dep_f = _el_scope([range_filter("startdate", _el_start, _el_end)] + list(scope_filters()))
        if el_env != "(all)":
            _dep_f.append({"term": {"environment": el_env}})
        else:
            _dep_f.append({"terms": {"environment": _allowed_envs}})
        _dep_r = es_search(
            IDX["deployments"],
            {"query": {"bool": {"filter": _dep_f}},
             "sort": [{"startdate": {"order": "desc", "unmapped_type": "date"}}]},
            size=_size,
        )
        for _h in _dep_r.get("hits", {}).get("hits", []):
            _s = _h.get("_source", {})
            _dv = _hit_date(_h, "deploy")
            events.append({
                "_ts":         parse_dt(_dv),
                "type":        "deploy",
                "When":        fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":         _s.get("application") or _s.get("project", ""),
                "Project":     _s.get("project", ""),
                "Environment": (_s.get("environment", "") or "").lower(),
                "Version":     _s.get("codeversion", ""),
                "Detail":      _s.get("technology", ""),
                "Status":      _s.get("status", ""),
                "Requester":   _s.get("requester", ""),
                "Approver":    _s.get("approver", ""),
                "Extra":       _s.get("triggeredby", ""),
            })

    # ── releases ────────────────────────────────────────────────────────────
    if _role_allows_type("Releases"):
        _rel_f = _el_scope([range_filter("releasedate", _el_start, _el_end)] + list(scope_filters()))
        _rel_r = es_search(
            IDX["releases"],
            {"query": {"bool": {"filter": _rel_f}},
             "sort": [{"releasedate": {"order": "desc", "unmapped_type": "date"}}]},
            size=_size,
        )
        for _h in _rel_r.get("hits", {}).get("hits", []):
            _s = _h.get("_source", {})
            _dv = _hit_date(_h, "release")
            _rlm_status = _s.get("RLM_STATUS") or ""
            _rlm_detail = (
                (_s.get("RLM") or "")
                if _rlm_status.strip().lower() == "no error"
                else _rlm_status
            )
            events.append({
                "_ts":         parse_dt(_dv),
                "type":        "release",
                "When":        fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":         _s.get("application", ""),
                "Project":     _s.get("project", ""),
                "Environment": "",
                "Version":     _s.get("codeversion", ""),
                "Detail":      f'RLM: {_rlm_detail}' if _rlm_detail else "",
                "Status":      "SUCCESS",
                "Requester":   _s.get("requester", ""),
                "Approver":    _s.get("approver", ""),
                "Extra":       "",
            })

    # Helper: resolve application / project from request docs, which may use
    # any of three naming conventions depending on the request source.
    def _rq_app(_s: dict) -> str:
        return (_s.get("application")
                or _s.get("ado.application_name")
                or _s.get("application_name")
                or "")

    def _rq_proj(_s: dict) -> str:
        return (_s.get("project")
                or _s.get("ado.project_name")
                or _s.get("project_name")
                or "")

    # ── requests / approvals (role-filtered by stage) ───────────────────────
    if _role_allows_type("Requests"):
        _rq_f = _el_scope([range_filter("RequestDate", _el_start, _el_end)] + list(scope_filters()))
        _rq_r = es_search(
            IDX["requests"],
            {"query": {"bool": {"filter": _rq_f}},
             "sort": [{"RequestDate": {"order": "desc", "unmapped_type": "date"}}]},
            size=_size,
        )
        for _h in _rq_r.get("hits", {}).get("hits", []):
            _s = _h.get("_source", {})
            _rq_env = (_s.get("TargetEnvironment") or _s.get("environment") or "").lower()
            if _rq_env and not _role_allows_env(_rq_env):
                continue
            _dv = _hit_date(_h, "request")
            _rq_status = (_s.get("Status") or "").upper()
            if any(k in _rq_status for k in ("APPROV", "SUCCESS", "COMPLETE", "OK")):
                _rq_approver = _s.get("ApprovedBy", "") or ""
            elif any(k in _rq_status for k in ("REJECT", "DENY", "FAIL")):
                _rq_approver = _s.get("RejectedBy", "") or ""
            else:
                _rq_approver = ""
            events.append({
                "_ts":         parse_dt(_dv),
                "type":        "request",
                "When":        fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":         _rq_app(_s) or _rq_proj(_s),
                "Project":     _rq_proj(_s),
                "Environment": _rq_env,
                "Version":     _s.get("codeversion", ""),
                "Detail":      f'{_s.get("RequestType","")} · {_s.get("Requester","")}',
                "Status":      _s.get("Status", ""),
                "Requester":   _s.get("Requester", ""),
                "Approver":    _rq_approver,
                "Extra":       _s.get("RequestNumber") or _s.get("id") or "",
            })
        # ef-cicd-approval (stage-based, role-scoped)
        _ap_f: list[dict] = _el_scope(list(scope_filters()))
        _ap_f.append({"bool": {"should": [
            range_filter("RequestDate", _el_start, _el_end),
            range_filter("Created", _el_start, _el_end),
            range_filter("CreatedDate", _el_start, _el_end),
        ], "minimum_should_match": 1}})
        _rsf = _role_stage_filter()
        if _rsf is not None:
            _ap_f.append(_rsf)
        _ap_r = es_search(
            IDX["approval"],
            {"query": {"bool": {"filter": _ap_f}},
             "sort": [{"RequestDate": {"order": "desc", "unmapped_type": "date"}}]},
            size=_size,
        )
        for _h in _ap_r.get("hits", {}).get("hits", []):
            _s = _h.get("_source", {})
            _dv = _hit_date(_h, "request")
            _stage = _s.get("stage") or ""
            # Extract implied environment from the stage for the Environment column.
            _ap_env = ""
            if _stage in ("qc", "uat", "prd"):
                _ap_env = _stage
            elif _stage.startswith("request_deploy_"):
                _ap_env = _stage.replace("request_deploy_", "")
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
            _ap_status = ((_s.get("Status") or "") + " " + _stage).upper()
            if any(k in _ap_status for k in ("APPROV", "SUCCESS", "COMPLETE")):
                _ap_approver = _s.get("ApprovedBy", "") or ""
            elif any(k in _ap_status for k in ("REJECT", "DENY", "FAIL")):
                _ap_approver = _s.get("RejectedBy", "") or ""
            else:
                _ap_approver = ""
            events.append({
                "_ts":         parse_dt(_dv),
                "type":        "request",
                "When":        fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":         _rq_app(_s) or _rq_proj(_s),
                "Project":     _rq_proj(_s),
                "Environment": _ap_env,
                "Version":     _s.get("codeversion", ""),
                "Detail":      f'{_detail} · {_s.get("RequestedBy") or _s.get("Requester", "")}',
                "Status":      _stage or _s.get("Status", ""),
                "Requester":   _s.get("RequestedBy") or _s.get("Requester", ""),
                "Approver":    _ap_approver,
                "Extra":       _s.get("ApprovalId") or _s.get("id") or "",
            })

    # ── commits (Developer/Admin) ───────────────────────────────────────────
    if _role_allows_type("Commits"):
        _com_f = _el_scope([range_filter("commitdate", _el_start, _el_end)] + list(commit_scope_filters()))
        _com_r = es_search(
            IDX["commits"],
            {"query": {"bool": {"filter": _com_f}},
             "sort": [{"commitdate": {"order": "desc", "unmapped_type": "date"}}]},
            size=_size,
        )
        for _h in _com_r.get("hits", {}).get("hits", []):
            _s = _h.get("_source", {})
            _dv = _hit_date(_h, "commit")
            _cmsg = (_s.get("commitmessage") or "").strip().splitlines()
            _cmsg_first = _cmsg[0] if _cmsg else ""
            _a_name = _s.get("authorname", "") or ""
            _a_mail = _s.get("authormail", "") or ""
            if _a_name and _a_mail:
                _commit_person = f"{_a_name} / {_a_mail}"
            else:
                _commit_person = _a_name or _a_mail
            events.append({
                "_ts":         parse_dt(_dv),
                "type":        "commit",
                "When":        fmt_dt(_dv, "%Y-%m-%d %H:%M"),
                "Who":         _s.get("repository", ""),
                "Project":     _s.get("project", ""),
                "Environment": "",
                "Version":     "",
                "Detail":      (
                    f'{_s.get("branch","")} · {_s.get("authorname","")}'
                    + (f' — {_cmsg_first}' if _cmsg_first else "")
                ),
                "Status":      "SUCCESS",
                "Requester":   _commit_person,
                "Approver":    _commit_person,
                "Extra":       _cmsg_first,
            })

    # ── sort (time-window already bounded the queries; no row limit) ────────
    events.sort(key=lambda e: e["_ts"] or pd.Timestamp("1970-01-01", tz="UTC"), reverse=True)

    # ── New-event toasts ────────────────────────────────────────────────────
    # Strictly timestamp-based so scope/pill/search changes don't trigger
    # spurious notifications — only events with _ts > the previous refresh's
    # max timestamp count as "new". The first render is silent; we just seed
    # the watermark and start alerting on subsequent refreshes. Rate-limited
    # to avoid a wall of toasts when someone returns to an idle tab.
    _ev_max_ts = max(
        (ev["_ts"] for ev in events if ev.get("_ts") is not None),
        default=None,
    )
    _el_last_max = st.session_state.get("_el_last_max_ts")
    if (_el_last_max is not None and _ev_max_ts is not None
            and _ev_max_ts > _el_last_max):
        _new_evs = [
            ev for ev in events
            if ev.get("_ts") is not None and ev["_ts"] > _el_last_max
        ]
        _TYPE_SHORT = {
            "build-develop": "dev build",
            "build-release": "rel build",
            "deploy":        "deploy",
            "release":       "release",
            "request":       "request",
            "commit":        "commit",
        }
        if 1 <= len(_new_evs) <= 3:
            for _ev in _new_evs:
                _who = (_ev.get("Who") or "").strip() or "—"
                _env = (_ev.get("Environment") or "").strip()
                _ver = (_ev.get("Version") or "").strip()
                _status = (_ev.get("Status") or "").strip()
                _parts = [_TYPE_SHORT.get(_ev.get("type", ""), _ev.get("type", "")),
                          _who]
                if _env:
                    _parts.append(_env.lower())
                if _ver:
                    _parts.append(_ver)
                _msg = " · ".join(p for p in _parts if p)
                if _status and _status.upper() not in ("SUCCESS", "SUCCEEDED", "OK"):
                    _msg += f"  [{_status}]"
                st.toast(f"new · {_msg}", icon=":material/notifications_active:")
        elif len(_new_evs) > 3:
            st.toast(
                f"{len(_new_evs)} new events in the current scope",
                icon=":material/notifications_active:",
            )
    if _ev_max_ts is not None:
        st.session_state["_el_last_max_ts"] = _ev_max_ts

    # ── Stats / filter pill bar ─────────────────────────────────────────────
    # Counts reflect the full universe of events the window contains — so the
    # user can see at a glance what *is* available, even if they narrow the
    # view via the pills.
    _type_counts_full: dict[str, int] = {}
    for _ev in events:
        _type_counts_full[_ev["type"]] = _type_counts_full.get(_ev["type"], 0) + 1

    # Pill metadata — order is deliberate (left-to-right: build ladder →
    # deploys → releases → requests → commits).
    _TYPE_FILTER_META: list[tuple[str, str, str, str]] = [
        # (internal_type, display_label, icon, role-gate name for _role_allows_type)
        ("build-develop", "Dev builds",  "◇", "Build-develop"),
        ("build-release", "Rel builds",  "◆", "Build-release"),
        ("deploy",        "Deploys",     "⬢", "Deployments"),
        ("release",       "Releases",    "★", "Releases"),
        ("request",       "Requests",    "✦", "Requests"),
        ("commit",        "Commits",     "⎇", "Commits"),
    ]
    _pill_entries = [
        (_it, _lbl, _ico, _type_counts_full.get(_it, 0))
        for _it, _lbl, _ico, _gate in _TYPE_FILTER_META
        if _role_allows_type(_gate)
    ]

    _total_events_unfiltered = len(events)
    _layout_badge = "per-project" if el_per_project else "consolidated"

    # Stats card: left = big total, middle = kicker + hint, right = mode chips.
    st.markdown(
        f'<div class="el-typefilter-head">'
        f'  <div class="el-tf-left">'
        f'    <div class="el-tf-total">{_total_events_unfiltered}</div>'
        f'    <div class="el-tf-total-label">'
        f'event{"s" if _total_events_unfiltered != 1 else ""} · {_el_tw_label.lower()}'
        f'</div>'
        f'  </div>'
        f'  <div class="el-tf-mid">'
        f'    <div class="el-tf-kicker">Filter by event type</div>'
        f'    <div class="el-tf-hint">'
        f'Click any pill to include it · select multiple to combine · none selected = show all'
        f'    </div>'
        f'  </div>'
        f'  <div class="el-tf-right">'
        f'    <span class="el-tf-badge layout">{_layout_badge}</span>'
        f'    <span class="el-tf-badge sort">newest first</span>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Build pill options. Labels double as the selection keys so we can round-
    # trip them back to internal types.
    _pill_options: list[str] = []
    _pill_to_internal: dict[str, str] = {}
    for _it, _lbl, _ico, _cnt in _pill_entries:
        _opt = f"{_ico}  {_lbl} · {_cnt}"
        _pill_options.append(_opt)
        _pill_to_internal[_opt] = _it

    if _pill_options:
        _selected_opts = st.pills(
            "Event types",
            options=_pill_options,
            selection_mode="multi",
            default=None,
            key="el_type_pills_v1",
            label_visibility="collapsed",
        )
    else:
        _selected_opts = []
    _active_types = {_pill_to_internal[o] for o in (_selected_opts or [])}

    # Apply the pill filter — empty selection is treated as "show all".
    if _active_types:
        events = [ev for ev in events if ev["type"] in _active_types]

    # Apply the text search filter — matches against every visible string field
    # so users can narrow by person, version, detail substring, etc. Terms are
    # AND so "deploy prd 3.4" narrows progressively.
    if el_search:
        _el_terms = [_t for _t in el_search.split() if _t]

        def _el_haystack(_ev: dict) -> str:
            _parts: list[str] = [
                str(_ev.get("type", "") or ""),
                str(_ev.get("Who", "") or ""),
                str(_ev.get("Project", "") or ""),
                str(_ev.get("Environment", "") or ""),
                str(_ev.get("Version", "") or ""),
                str(_ev.get("Detail", "") or ""),
                str(_ev.get("Status", "") or ""),
                str(_ev.get("Requester", "") or ""),
                str(_ev.get("Approver", "") or ""),
                str(_ev.get("Extra", "") or ""),
            ]
            return " ".join(_parts).lower()

        events = [
            ev for ev in events
            if all(_t in _el_haystack(ev) for _t in _el_terms)
        ]

    if not events:
        if _total_events_unfiltered:
            inline_note(
                f"No events match the current filters. {_total_events_unfiltered} "
                f"events exist in this window — remove filters or deselect pills to show them.",
                "info",
            )
        else:
            inline_note("No events match the current filters.", "info")
        return

    # Types whose "Who" column carries a real application name (vs commits'
    # repository or requests' project). Keep this list in one place so the
    # type-gating stays consistent across popover wiring below.
    _APP_EVENT_TYPES = ("build-develop", "build-release", "deploy", "release")

    # Collect unique application names from build/deploy/release events (only
    # these carry reliable inventory identity) and fetch their inventory cards.
    _pop_apps_primary = sorted({
        ev["Who"] for ev in events
        if ev["type"] in _APP_EVENT_TYPES and ev.get("Who")
    })

    # Also collect unique projects from any event type so the Project column can
    # drill into teams + applications via a popover.
    _pop_projects = sorted({ev["Project"] for ev in events if ev.get("Project")})
    _proj_map = _fetch_project_details(tuple(_pop_projects)) if _pop_projects else {}

    # Extend the inventory fetch with every app discovered through a project so
    # that app-chips inside a project popover also resolve to a detail popover.
    _pop_apps_set = set(_pop_apps_primary)
    for _pdata in _proj_map.values():
        for _a in _pdata.get("apps", []):
            if _a:
                _pop_apps_set.add(_a)
    _pop_apps = sorted(_pop_apps_set)
    _inv_map = _fetch_inventory_details(tuple(_pop_apps)) if _pop_apps else {}

    # Current prd liveness per application — only need this for apps that
    # actually appear in a Version cell (build/deploy/release events).
    # Unique (app, version) pairs that show up in a Version cell — one popover
    # per pair so the same app can be inspected at different versions.
    _ver_apps_versions = sorted({
        (ev["Who"], ev["Version"]) for ev in events
        if ev["type"] in _APP_EVENT_TYPES and ev.get("Who") and ev.get("Version")
    })
    _ver_apps = sorted({_a for _a, _ in _ver_apps_versions})
    _prd_map = _fetch_prd_status(tuple(_ver_apps)) if _ver_apps else {}

    # Prismacloud lookup — query both the event's version AND the app's current
    # prd version so the popover can render a side-by-side delta.
    _prisma_keys: set[tuple[str, str]] = set(_ver_apps_versions)
    for _a, _prd in _prd_map.items():
        _pv = (_prd or {}).get("version") or ""
        if _pv:
            _prisma_keys.add((_a, _pv))
    _prisma_map = _fetch_prismacloud(tuple(sorted(_prisma_keys))) if _prisma_keys else {}
    # Per-version build/release provenance for the event-log version popovers.
    _ver_meta_map = _fetch_version_meta(tuple(sorted(_prisma_keys))) if _prisma_keys else {}

    def _slug(val: str, prefix: str) -> str:
        return prefix + "".join(c.lower() if c.isalnum() else "-" for c in val)[:80]

    def _pop_id(app: str) -> str:
        """Deterministic DOM id for an application popover."""
        return _slug(app, "el-app-pop-")

    def _proj_pop_id(project: str) -> str:
        """Deterministic DOM id for a project popover."""
        return _slug(project, "el-proj-pop-")

    def _ver_pop_id(app: str, version: str) -> str:
        """Deterministic DOM id for an app+version liveness/security popover."""
        return _slug(f"{app}--{version}", "el-ver-pop-")

    def _app_cell(ev: dict) -> str:
        """Render the Application column — clickable popover trigger when we
        have inventory data for it; otherwise plain text."""
        _name = ev.get("Who") or ""
        if not _name:
            return '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        if ev["type"] in _APP_EVENT_TYPES and _name in _inv_map:
            return (
                f'<button type="button" class="el-app-trigger" '
                f'popovertarget="{_pop_id(_name)}" '
                f'title="Click for inventory details">{_name}</button>'
            )
        # No inventory / non-inspectable event type → plain label
        return (
            f'<span style="font-weight:600;color:var(--cc-text);'
            f'font-size:0.82rem">{_name}</span>'
        )

    def _project_cell(ev: dict) -> str:
        """Render the Project column — clickable popover trigger when we have
        inventory data for the project; otherwise a plain label."""
        _proj = ev.get("Project") or ""
        if not _proj:
            return '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        if _proj in _proj_map:
            return (
                f'<button type="button" class="el-proj-trigger" '
                f'popovertarget="{_proj_pop_id(_proj)}" '
                f'title="Click for teams & applications">{_proj}</button>'
            )
        return f'<span style="color:var(--cc-text-dim);font-size:0.78rem">{_proj}</span>'

    def _version_cell(ev: dict) -> str:
        """Render the Version column — a clickable chip that pops the
        application's live-in-prd status. Plain chip when we can't key it to an
        application (commits, requests, empty versions)."""
        _ver = ev.get("Version") or ""
        if not _ver:
            return '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        _app = ev.get("Who") or ""
        if ev["type"] in _APP_EVENT_TYPES and _app:
            _title = (
                "Live in prd" if (_prd_map.get(_app) or {}).get("live")
                else ("Last prd deploy failed" if _app in _prd_map else "Not deployed to prd")
            )
            return (
                f'<button type="button" class="el-ver-trigger" '
                f'popovertarget="{_ver_pop_id(_app, _ver)}" '
                f'title="{_title} · click for details">{_ver}</button>'
            )
        return (
            f'<span style="font-family:var(--cc-mono);font-size:0.73rem;color:var(--cc-accent);'
            f'background:var(--cc-accent-lt);padding:1px 6px;border-radius:4px">{_ver}</span>'
        )

    # Environment chip — high-signal coloring so prd stands out at a glance.
    # prd=rose (danger), uat=amber (staging), qc=teal (pre-ship), dev=emerald.
    _ENV_CHIP = {
        "prd": ("#fecdd3", "#9f1239", "PRD"),
        "uat": ("#fde68a", "#92400e", "UAT"),
        "qc":  ("#cffafe", "#155e75", "QC"),
        "dev": ("#d1fae5", "#065f46", "DEV"),
    }

    def _env_cell(ev: dict) -> str:
        _env = (ev.get("Environment") or "").lower().strip()
        if not _env:
            return '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        _bg, _fg, _lbl = _ENV_CHIP.get(_env, ("var(--cc-surface2)", "var(--cc-text-dim)", _env.upper()))
        return (
            f'<span style="background:{_bg};color:{_fg};border-radius:4px;'
            f'padding:1px 7px;font-size:0.70rem;font-weight:800;letter-spacing:.04em;'
            f'font-family:var(--cc-mono)">{_lbl}</span>'
        )

    def _person_cell(val: str) -> str:
        if not val:
            return '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        return (
            f'<span style="color:var(--cc-text-dim);font-size:0.76rem;'
            f'max-width:180px;display:inline-block;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap;vertical-align:middle" '
            f'title="{val}">{val}</span>'
        )

    def _freshness_tier(_ts) -> str:
        """Classify an event's recency into a CSS-ready tier.

        live <5m · fresh <1h · today <24h · week <7d · older beyond that.
        Drives the colored pulse dot in the When column so scanning the
        table conveys recency at a glance.
        """
        if _ts is None:
            return "older"
        try:
            _pdt = _ts.to_pydatetime() if hasattr(_ts, "to_pydatetime") else _ts
            if _pdt.tzinfo is None:
                _pdt = _pdt.replace(tzinfo=timezone.utc)
            _delta_s = (datetime.now(timezone.utc) - _pdt).total_seconds()
        except Exception:
            return "older"
        if _delta_s < 0:         return "fresh"
        if _delta_s < 300:       return "live"
        if _delta_s < 3600:      return "fresh"
        if _delta_s < 86400:     return "today"
        if _delta_s < 86400 * 7: return "week"
        return "older"

    def _when_cell(ev: dict) -> str:
        """Render the When column as absolute timestamp + relative age.

        Two stacked lines: top = absolute (DISPLAY_TZ), bottom = "5h ago" /
        "3d ago" style tag so the reader sees recency at a glance without
        doing date-math in their head. A small colored pulse-dot prefixes
        the relative-age row, color-coded by freshness tier — live events
        (<5 minutes old) pulse to signal activity on the stream.
        """
        _abs = ev.get("When") or ""
        _rel = _relative_age(ev.get("_ts"))
        if not _abs and not _rel:
            return '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        _tier = _freshness_tier(ev.get("_ts"))
        _dot = f'<span class="el-fresh-dot is-{_tier}" aria-hidden="true"></span>'
        _rel_html = (
            f'<div class="el-when-rel" style="color:var(--cc-text-mute);'
            f'font-size:0.68rem;letter-spacing:.03em;margin-top:1px">'
            f'{_dot}{_rel}</div>'
            if _rel else ""
        )
        return (
            f'<div class="el-when-abs" style="color:var(--cc-text-dim);'
            f'font-size:0.78rem;font-family:var(--cc-mono);line-height:1.15">{_abs}</div>'
            f'{_rel_html}'
        )

    def _row_html(ev: dict, *, include_project: bool = True) -> str:
        """Render a single <tr> for an event.

        When ``include_project`` is False (per-project grouped view), the Project
        cell is suppressed because the project is already the table heading.
        """
        _proj_html = (
            f'<td style="padding:5px 4px">{_project_cell(ev)}</td>'
            if include_project else ""
        )
        return (
            f"<tr>"
            f'<td style="white-space:nowrap;padding:5px 4px;vertical-align:top">{_when_cell(ev)}</td>'
            f'<td style="padding:5px 6px">{_TYPE_BADGE.get(ev["type"], "")}</td>'
            f'{_proj_html}'
            f'<td style="padding:5px 4px">{_app_cell(ev)}</td>'
            f'<td style="padding:5px 6px">{_env_cell(ev)}</td>'
            f'<td style="padding:5px 4px">{_version_cell(ev)}</td>'
            f'<td style="color:var(--cc-text-dim);font-size:0.8rem;padding:5px 4px">{ev["Detail"]}</td>'
            f'<td style="padding:5px 6px">{_status_chip(ev["Status"])}</td>'
            f'<td style="padding:5px 4px">{_person_cell(ev.get("Requester", ""))}</td>'
            f'<td style="padding:5px 4px">{_person_cell(ev.get("Approver", ""))}</td>'
            f'<td style="color:var(--cc-text-mute);font-size:0.75rem;max-width:220px;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:5px 4px">{ev["Extra"]}</td>'
            f"</tr>"
        )

    # Build popover HTML once per unique application
    def _v(val: str) -> str:
        return (f'<span class="ap-v">{val}</span>'
                if val else '<span class="ap-v empty">—</span>')

    def _chip(val: str) -> str:
        return (f'<span class="ap-v"><span class="ap-chip">{val}</span></span>'
                if val else '<span class="ap-v empty">—</span>')

    def _app_type_pill(val: str) -> str:
        """Render app_type as a coloured pill — App (blue) / Lib (violet)."""
        _t = (val or "").strip()
        if not _t:
            return '<span class="ap-v empty">—</span>'
        _cls = "is-app" if _t.lower() == "app" else (
            "is-lib" if _t.lower() == "lib" else "is-other"
        )
        return (f'<span class="ap-v"><span class="ap-type-pill {_cls}">'
                f'{_t}</span></span>')

    _popovers_html: list[str] = []
    for _app in _pop_apps:
        _inv = _inv_map.get(_app)
        if not _inv:
            continue
        _pid = _pop_id(_app)
        _popovers_html.append(
            f'<div id="{_pid}" popover="auto" class="el-app-pop">'
            f'  <div class="ap-head">'
            f'    <div class="ap-icon">◆</div>'
            f'    <div class="ap-title-wrap">'
            f'      <div class="ap-kicker">Application</div>'
            f'      <div class="ap-title">{_app}</div>'
            f'    </div>'
            f'    <button class="ap-close" popovertarget="{_pid}" popovertargetaction="hide" aria-label="Close">×</button>'
            f'  </div>'
            f'  <div class="ap-body">'
            f'    <div class="ap-section">Identity</div>'
            f'    <span class="ap-k">Project</span>{_v(_inv.get("project", ""))}'
            f'    <span class="ap-k">Company</span>{_v(_inv.get("company", ""))}'
            f'    <span class="ap-k">Type</span>{_app_type_pill(_inv.get("app_type", ""))}'
            f'    <div class="ap-section">Build</div>'
            f'    <span class="ap-k">Technology</span>{_chip(_inv.get("build_technology", ""))}'
            f'    <span class="ap-k">Image name</span>{_v(_inv.get("build_image_name", ""))}'
            f'    <span class="ap-k">Image tag</span>{_v(_inv.get("build_image_tag", ""))}'
            f'    <div class="ap-section">Deploy</div>'
            f'    <span class="ap-k">Technology</span>{_chip(_inv.get("deploy_technology", ""))}'
            f'    <span class="ap-k">Platform</span>{_chip(_inv.get("deploy_platform", ""))}'
            f'    <span class="ap-k">Image name</span>{_v(_inv.get("deploy_image_name", ""))}'
            f'    <span class="ap-k">Image tag</span>{_v(_inv.get("deploy_image_tag", ""))}'
            f'  </div>'
            f'  <div class="ap-foot">Source: ef-devops-inventory · click outside to dismiss</div>'
            f'</div>'
        )

    # Pretty labels for the *_team inventory fields
    _TEAM_LABELS = {
        "dev_team": "Dev team",
        "qc_team":  "QC team",
        "uat_team": "UAT team",
        "prd_team": "PRD team",
    }

    def _team_label(field: str) -> str:
        if field in _TEAM_LABELS:
            return _TEAM_LABELS[field]
        # Fallback: pretty-print any *_team field we don't know yet
        _base = field[:-5] if field.endswith("_team") else field
        return _base.replace("_", " ").strip().upper() + " team"

    # Build one popover per unique project — lists team ownership + applications.
    for _proj in _pop_projects:
        _pdata = _proj_map.get(_proj)
        if not _pdata:
            continue
        _pid_p = _proj_pop_id(_proj)
        _teams = _pdata.get("teams", {}) or {}
        _apps  = _pdata.get("apps", []) or []
        _co_p  = _pdata.get("company", "") or ""

        # Teams rows — preserve logical dev→qc→uat→prd ordering, then any extras
        _ordered = [k for k in ("dev_team", "qc_team", "uat_team", "prd_team") if k in _teams]
        _extras  = sorted(k for k in _teams.keys() if k not in _ordered)
        _team_rows = []
        for _f in _ordered + _extras:
            _vals = _teams.get(_f) or []
            if not _vals:
                continue
            _chips = "".join(f'<span class="ap-chip">{_tv}</span>' for _tv in _vals)
            _team_rows.append(
                f'<span class="ap-k">{_team_label(_f)}</span>'
                f'<span class="ap-v" style="display:flex;flex-wrap:wrap;gap:4px">{_chips}</span>'
            )
        if not _team_rows:
            _team_rows.append(
                '<span class="ap-k">Teams</span>'
                '<span class="ap-v empty">none recorded</span>'
            )

        # Application chips — clickable if that app has an inventory popover,
        # otherwise rendered as static (still visible but non-interactive).
        _app_chips = []
        for _a in _apps:
            if _a in _inv_map:
                _app_chips.append(
                    f'<button type="button" class="ap-app-chip" '
                    f'popovertarget="{_pop_id(_a)}" '
                    f'title="Open application details">{_a}</button>'
                )
            else:
                _app_chips.append(f'<span class="ap-app-chip static">{_a}</span>')
        _apps_block = "".join(_app_chips)

        _company_block = (
            f'    <div class="ap-section">Company</div>'
            f'    <span class="ap-k">Name</span>{_chip(_co_p) if _co_p else _v("")}'
        )

        _popovers_html.append(
            f'<div id="{_pid_p}" popover="auto" class="el-app-pop is-project">'
            f'  <div class="ap-head">'
            f'    <div class="ap-icon">◇</div>'
            f'    <div class="ap-title-wrap">'
            f'      <div class="ap-kicker">Project</div>'
            f'      <div class="ap-title">{_proj}</div>'
            f'    </div>'
            f'    <button class="ap-close" popovertarget="{_pid_p}" popovertargetaction="hide" aria-label="Close">×</button>'
            f'  </div>'
            f'  <div class="ap-body">'
            f'    {_company_block}'
            f'    <div class="ap-section">Teams</div>'
            + "".join(_team_rows) +
            f'    <div class="ap-section">Applications <span style="text-transform:none;font-weight:600;color:var(--cc-text-mute);letter-spacing:0;margin-left:4px">· {len(_apps)}</span></div>'
            f'    <div class="ap-applist">{_apps_block}</div>'
            f'  </div>'
            f'  <div class="ap-foot">Source: ef-devops-inventory · click an app for build &amp; deploy details</div>'
            f'</div>'
        )

    # Severity-strip helpers ------------------------------------------------
    _SEV_KEYS = [
        ("critical", "Critical"),
        ("high",     "High"),
        ("medium",   "Medium"),
        ("low",      "Low"),
    ]

    def _sev_tile(level: str, label: str, count: int, delta: int | None) -> str:
        """One severity tile. ``delta`` may be None (no comparison), 0, or ±N."""
        _nz = "nonzero" if count > 0 else "zero"
        if delta is None:
            _delta_html = ""
        elif delta > 0:
            _delta_html = f'<div class="sev-delta up">▲ +{delta} vs prd</div>'
        elif delta < 0:
            _delta_html = f'<div class="sev-delta down">▼ {delta} vs prd</div>'
        else:
            _delta_html = '<div class="sev-delta eq">= vs prd</div>'
        return (
            f'<div class="ap-sev-tile {level} {_nz}">'
            f'  <div class="sev-num">{count}</div>'
            f'  <div class="sev-label">{label}</div>'
            f'  {_delta_html}'
            f'</div>'
        )

    def _sev_strip(prefix: str, scan: dict, baseline: dict | None) -> tuple[str, int]:
        """Four tiles for the V* or C* fields in ``scan``, optionally with a
        delta computed against the same fields in ``baseline``.

        Returns ``(tiles_html, total_count)``. Field names in the index are
        ``Vcritical``/``Vhigh``/``Vmedium``/``Vlow`` and the C* equivalents —
        uppercase prefix, lowercase level.
        """
        tiles: list[str] = []
        _total = 0
        for _lvl, _lbl in _SEV_KEYS:
            _fld = f"{prefix}{_lvl}"     # Vcritical, Chigh, …
            _n = int(scan.get(_fld, 0) or 0)
            _total += _n
            _delta: int | None = None
            if baseline is not None:
                _delta = _n - int(baseline.get(_fld, 0) or 0)
            tiles.append(_sev_tile(_lvl, _lbl, _n, _delta))
        return "".join(tiles), _total

    # One version popover per unique (app, version) pair in the event log.
    for _app, _ver in _ver_apps_versions:
        _prd = _prd_map.get(_app)
        _vid = _ver_pop_id(_app, _ver)
        _prd_ver = (_prd or {}).get("version", "") or ""
        _is_this_prd = bool(_prd_ver and _prd_ver == _ver)

        # Live banner — same logic as before, tailored for the current version.
        if _prd:
            _live = bool(_prd.get("live"))
            _prd_when   = fmt_dt(_prd.get("when"), "%Y-%m-%d %H:%M") or ""
            _prd_status = _prd.get("status", "") or ""
            if _live and _is_this_prd:
                _banner = (
                    f'<div class="ap-live is-live">'
                    f'  <span class="dot"></span>'
                    f'  <span>This version is live in prd · '
                    f'<span class="ap-chip">{_ver}</span></span>'
                    f'</div>'
                )
            elif _live:
                _banner = (
                    f'<div class="ap-live is-live">'
                    f'  <span class="dot"></span>'
                    f'  <span>App is live in prd · running '
                    f'<span class="ap-chip">{_prd_ver}</span> (not this version)</span>'
                    f'</div>'
                )
            else:
                _banner = (
                    f'<div class="ap-live is-offline">'
                    f'  <span class="dot"></span>'
                    f'  <span>Last prd deploy failed · {_prd_status or "FAILED"}</span>'
                    f'</div>'
                )
            _prd_block = (
                f'    <div class="ap-section">Current prd deploy</div>'
                f'    <span class="ap-k">Version</span>{_chip(_prd_ver)}'
                f'    <span class="ap-k">Status</span>{_v(_prd_status)}'
                f'    <span class="ap-k">When ({DISPLAY_TZ_LABEL})</span>{_v(_prd_when)}'
            )
        else:
            _banner = (
                f'<div class="ap-live is-offline">'
                f'  <span class="dot"></span>'
                f'  <span>App not deployed to prd</span>'
                f'</div>'
            )
            _prd_block = (
                f'    <div class="ap-section">Current prd deploy</div>'
                f'    <span class="ap-k">Version</span><span class="ap-v empty">none on record</span>'
            )

        # Prismacloud block ---------------------------------------------------
        _this_scan = _prisma_map.get((_app, _ver))
        _prd_scan  = _prisma_map.get((_app, _prd_ver)) if _prd_ver else None
        # Only compute deltas when this version != prd version AND prd scan exists.
        _baseline = _prd_scan if (_prd_ver and not _is_this_prd and _prd_scan) else None

        if _this_scan:
            _v_tiles, _v_total = _sev_strip("V", _this_scan, _baseline)
            _c_tiles, _c_total = _sev_strip("C", _this_scan, _baseline)
            _scan_when = fmt_dt(_this_scan.get("when"), "%Y-%m-%d %H:%M") or ""
            _scan_stat = _this_scan.get("status", "") or ""
            _sec_subhead_v = (
                f'<div class="ap-sev-subhead">'
                f'  <span>Vulnerabilities · this version</span>'
                f'  <span class="sev-sum">{_v_total} total</span>'
                f'</div>'
            )
            _sec_subhead_c = (
                f'<div class="ap-sev-subhead">'
                f'  <span>Compliance · this version</span>'
                f'  <span class="sev-sum">{_c_total} total</span>'
                f'</div>'
            )
            _prisma_block = (
                f'    <div class="ap-section">Prismacloud scan</div>'
                f'    <span class="ap-k">Scan status</span>{_v(_scan_stat)}'
                f'    <span class="ap-k">Scanned ({DISPLAY_TZ_LABEL})</span>{_v(_scan_when)}'
                f'    {_sec_subhead_v}'
                f'    <div class="ap-sev">{_v_tiles}</div>'
                f'    {_sec_subhead_c}'
                f'    <div class="ap-sev">{_c_tiles}</div>'
            )
            if _baseline is not None:
                _prisma_block += (
                    f'    <div class="ap-compare-head">'
                    f'      <span>Δ vs current prd</span>'
                    f'      <span class="cmp-pill">{_prd_ver}</span>'
                    f'    </div>'
                )
        else:
            _prisma_block = (
                f'    <div class="ap-section">Prismacloud scan</div>'
                f'    <div class="ap-sev-empty">No prismacloud scan on record for this version.</div>'
            )

        # Per-version provenance: always show build date; if released, show
        # release date + RLM.
        _vmeta = _ver_meta_map.get((_app, _ver)) or {}
        _build_when_disp = fmt_dt(_vmeta.get("build_when"), "%Y-%m-%d %H:%M") or ""
        _rel_when_disp   = fmt_dt(_vmeta.get("release_when"), "%Y-%m-%d %H:%M") or ""
        _rlm_id   = _vmeta.get("rlm", "")
        _prov_block = (
            f'    <div class="ap-section">Version provenance</div>'
            f'    <span class="ap-k">Built ({DISPLAY_TZ_LABEL})</span>{_v(_build_when_disp)}'
        )
        if _rel_when_disp or _rlm_id:
            _prov_block += (
                f'    <span class="ap-k">Released ({DISPLAY_TZ_LABEL})</span>{_v(_rel_when_disp)}'
            )
            if _rlm_id:
                _prov_block += f'    <span class="ap-k">RLM</span>{_chip(_rlm_id)}'

        _popovers_html.append(
            f'<div id="{_vid}" popover="auto" class="el-app-pop is-version">'
            f'  <div class="ap-head">'
            f'    <div class="ap-icon">▲</div>'
            f'    <div class="ap-title-wrap">'
            f'      <div class="ap-kicker">Version · {_ver}</div>'
            f'      <div class="ap-title">{_app}</div>'
            f'    </div>'
            f'    <button class="ap-close" popovertarget="{_vid}" popovertargetaction="hide" aria-label="Close">×</button>'
            f'  </div>'
            f'  <div class="ap-body">'
            f'    {_banner}'
            f'    {_prov_block}'
            f'    {_prd_block}'
            f'    {_prisma_block}'
            f'  </div>'
            f'  <div class="ap-foot">Sources: ef-cicd-builds · ef-cicd-releases · ef-cicd-deployments · ef-cicd-prismacloud</div>'
            f'</div>'
        )

    _th_style = 'style="padding:6px 4px;color:var(--cc-text-mute);font-size:0.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase"'

    def _thead_html(include_project: bool) -> str:
        _proj_th = f'<th {_th_style}>Project</th>' if include_project else ""
        return (
            f'<thead><tr style="border-bottom:2px solid var(--cc-border);text-align:left;background:var(--cc-surface2)">'
            f'<th {_th_style}>Time</th>'
            f'<th {_th_style}>Type</th>'
            f'{_proj_th}'
            f'<th {_th_style}>Application</th>'
            f'<th {_th_style}>Env</th>'
            f'<th {_th_style}>Version</th>'
            f'<th {_th_style}>Detail</th>'
            f'<th {_th_style}>Status</th>'
            f'<th {_th_style}>Requester</th>'
            f'<th {_th_style}>Approver</th>'
            f'<th {_th_style}>Note</th>'
            f'</tr></thead>'
        )

    def _table_shell(rows_html: str, *, include_project: bool, max_h: str = "60vh") -> str:
        return (
            f'<div class="el-tf el-tf-shell" style="overflow-y:auto;max-height:{max_h};'
            f'border:1px solid var(--cc-border);border-radius:10px">'
            '<table style="width:100%;border-collapse:collapse;font-family:inherit">'
            f'{_thead_html(include_project)}'
            f'<tbody>{rows_html}</tbody>'
            '</table></div>'
        )

    if el_per_project:
        # Group events by project, preserving the already newest-first ordering
        # from ``events.sort`` above. Projects appear in order of their most
        # recent activity (first-seen wins, dict preserves insertion order).
        _groups: dict[str, list[dict]] = {}
        for ev in events:
            _grp_key = ev.get("Project") or "(no project)"
            _groups.setdefault(_grp_key, []).append(ev)

        _sections_html: list[str] = []
        for _proj, _evs in _groups.items():
            _rows = "".join(_row_html(ev, include_project=False) for ev in _evs)
            # Per-project section: heading chip + count, then an embedded table.
            _proj_pid = _proj_pop_id(_proj) if _proj in _proj_map else ""
            _proj_heading = (
                f'<button type="button" class="el-proj-trigger" '
                f'popovertarget="{_proj_pid}" '
                f'title="Click for teams & applications">{_proj}</button>'
                if _proj_pid else
                f'<span style="font-weight:700;color:var(--cc-text);font-size:0.92rem">{_proj}</span>'
            )
            _sections_html.append(
                f'<section class="el-proj-section">'
                f'  <header class="el-proj-section-head">'
                f'    <span class="el-proj-section-kicker">Project</span>'
                f'    <span class="el-proj-section-title">{_proj_heading}</span>'
                f'    <span class="el-proj-section-count">{len(_evs)} event{"s" if len(_evs) != 1 else ""}</span>'
                f'  </header>'
                f'  {_table_shell(_rows, include_project=False, max_h="38vh")}'
                f'</section>'
            )
        _main_html = '<div class="el-proj-stack">' + "".join(_sections_html) + '</div>'
    else:
        _rows = "".join(_row_html(ev, include_project=True) for ev in events)
        _main_html = _table_shell(_rows, include_project=True, max_h="60vh")

    # Thin caption under the pill bar — reminds users about the interactive
    # popovers now that the type-count summary lives in the stats card.
    _visible_badge = (
        f"showing {len(events)} of {_total_events_unfiltered}"
        if _active_types else
        f"showing all {len(events)}"
    )
    st.markdown(
        f'<p class="el-tf-caption">'
        f'  <span class="el-tf-caption-count">{_visible_badge}</span>'
        f'  <span class="el-tf-caption-sep">·</span>'
        f'  <span>click any <b>project</b>, <b>application</b>, or <b>version</b> chip to open its detail popover</span>'
        f'</p>'
        + _main_html
        + "".join(_popovers_html),
        unsafe_allow_html=True,
    )


# ── Shared controls for the event log + inventory panel ──────────────────
# Both fragments read these out of session_state so users only set search /
# per-project once. Project is unified with the top filter strip; the two
# helpers below just reflect the already-set values back into each view.
def _shared_project_filter() -> str:
    """Reuse the top-bar project picker — no separate shared widget."""
    return project_filter


def _shared_search_query() -> str:
    """Resolve the shared search box to a lowercased, stripped query."""
    return (st.session_state.get("shared_search_v1", "") or "").strip().lower()


def _shared_per_project() -> bool:
    """Resolve the shared per-project-tables toggle."""
    return bool(st.session_state.get("shared_per_project_v1", False))


# =============================================================================
# PIPELINES INVENTORY — one row per registered pipeline, RBAC-scoped
# =============================================================================

@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_full_inventory(scope_json: str) -> list[dict]:
    """Return all inventory records matching *scope_json* with every field."""
    _sf = json.loads(scope_json)
    try:
        resp = es_search(
            IDX["inventory"],
            {"query": {"bool": {"filter": _sf}}, "_source": True},
            size=2000,
        )
    except Exception:
        return []
    rows: list[dict] = []
    for _h in resp.get("hits", {}).get("hits", []):
        _s = _h.get("_source", {}) or {}
        _app = _s.get("application") or ""
        if not _app:
            continue
        _bi = _s.get("build_image") or {}
        _di = _s.get("deploy_image") or {}
        _bi_name = (_bi.get("name") if isinstance(_bi, dict) else None) or _s.get("build_image.name", "")
        _bi_tag  = (_bi.get("tag")  if isinstance(_bi, dict) else None) or _s.get("build_image.tag", "")
        _di_name = (_di.get("name") if isinstance(_di, dict) else None) or _s.get("deploy_image.name", "")
        _di_tag  = (_di.get("tag")  if isinstance(_di, dict) else None) or _s.get("deploy_image.tag", "")
        # Collect all *_team fields
        _teams: dict[str, list[str]] = {}
        for _k, _v in _s.items():
            if not _k.endswith("_team") or not _v:
                continue
            if isinstance(_v, (list, tuple, set)):
                _teams[_k] = sorted(str(x) for x in _v if x)
            else:
                _teams[_k] = [str(_v)]
        rows.append({
            "application":       _app,
            "project":           _s.get("project", ""),
            "company":           _s.get("company", ""),
            "app_type":          (_s.get("app_type") or "").strip(),
            "build_technology":  _s.get("build_technology", ""),
            "deploy_technology": _s.get("deploy_technology", ""),
            "deploy_platform":   _s.get("deploy_platform", ""),
            "build_image_name":  _bi_name or "",
            "build_image_tag":   _bi_tag  or "",
            "deploy_image_name": _di_name or "",
            "deploy_image_tag":  _di_tag  or "",
            "teams":             _teams,
        })
    rows.sort(key=lambda r: (r["project"].lower(), r["application"].lower()))
    return rows


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_inv_pulse(apps_json: str, days: int = 14) -> dict:
    """Daily build + PRD-deploy activity for the given application scope.

    Returns ``{"build": [{"success", "failure", "other"}, ...],
    "deploy_prd": [counts]}`` with one entry per calendar day (oldest first).
    """
    _apps: list[str] = json.loads(apps_json)
    _empty = {"build": [0] * days, "deploy_prd": [0] * days,
              "build_success": [0] * days, "build_failure": [0] * days}
    if not _apps:
        return _empty
    _now = datetime.now(timezone.utc)
    _start = _now - timedelta(days=days)
    # Builds — daily bucket with status breakdown
    try:
        _br = es_search(
            IDX["builds"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": _apps}},
                    range_filter("startdate", _start, _now),
                ]}},
                "aggs": {
                    "tl": {
                        "date_histogram": {
                            "field": "startdate",
                            "fixed_interval": "1d",
                            "min_doc_count": 0,
                            "extended_bounds": {
                                "min": int(_start.timestamp() * 1000),
                                "max": int(_now.timestamp() * 1000),
                            },
                        },
                        "aggs": {"s": {"terms": {"field": "status", "size": 10}}},
                    },
                },
            },
            size=0,
        )
    except Exception:
        _br = {}
    _build_succ: list[int] = []
    _build_fail: list[int] = []
    _build_other: list[int] = []
    for _b in _br.get("aggregations", {}).get("tl", {}).get("buckets", []):
        _succ = _fail = _other = 0
        for _s in _b.get("s", {}).get("buckets", []):
            _k = _s.get("key") or ""
            _n = int(_s.get("doc_count") or 0)
            if _k in SUCCESS_STATUSES:
                _succ += _n
            elif _k in FAILED_STATUSES:
                _fail += _n
            else:
                _other += _n
        _build_succ.append(_succ)
        _build_fail.append(_fail)
        _build_other.append(_other)
    # PRD deploys — daily count
    try:
        _dr = es_search(
            IDX["deployments"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": _apps}},
                    {"term": {"environment": "prd"}},
                    range_filter("startdate", _start, _now),
                ]}},
                "aggs": {
                    "tl": {
                        "date_histogram": {
                            "field": "startdate",
                            "fixed_interval": "1d",
                            "min_doc_count": 0,
                            "extended_bounds": {
                                "min": int(_start.timestamp() * 1000),
                                "max": int(_now.timestamp() * 1000),
                            },
                        }
                    }
                },
            },
            size=0,
        )
    except Exception:
        _dr = {}
    _dep_counts = [
        int(_b.get("doc_count") or 0)
        for _b in _dr.get("aggregations", {}).get("tl", {}).get("buckets", [])
    ]
    # Pad to exactly ``days`` slots (histograms may return ±1 bucket depending
    # on bounds alignment).
    def _pad(xs: list[int]) -> list[int]:
        if len(xs) >= days:
            return xs[-days:]
        return [0] * (days - len(xs)) + xs
    return {
        "build_success": _pad(_build_succ),
        "build_failure": _pad(_build_fail),
        "build":         _pad([s + f + o for s, f, o in zip(_build_succ, _build_fail, _build_other)]),
        "deploy_prd":    _pad(_dep_counts),
    }


def _svg_stacked_spark(success: list[int], failure: list[int]) -> str:
    """Daily stacked bars — success (green) on bottom, failure (red) on top."""
    if not success and not failure:
        return '<div class="iv-pulse-empty">no builds in 14d</div>'
    _W, _H = 240.0, 38.0
    _n = max(len(success), len(failure))
    if _n == 0:
        return '<div class="iv-pulse-empty">no builds in 14d</div>'
    _max = max((s + f) for s, f in zip(success, failure)) or 1
    _slot = _W / _n
    _bw = _slot * 0.72
    _pad = (_slot - _bw) / 2
    _bars: list[str] = []
    for _i in range(_n):
        _s = success[_i] if _i < len(success) else 0
        _f = failure[_i] if _i < len(failure) else 0
        _x = _i * _slot + _pad
        _hs = (_s / _max) * (_H - 2)
        _hf = (_f / _max) * (_H - 2)
        # Track (faint)
        if _s == 0 and _f == 0:
            _bars.append(
                f'<rect x="{_x:.2f}" y="{_H - 2:.2f}" width="{_bw:.2f}" height="2" '
                f'fill="var(--cc-border)" opacity=".55"/>'
            )
            continue
        if _s > 0:
            _bars.append(
                f'<rect x="{_x:.2f}" y="{_H - _hs:.2f}" width="{_bw:.2f}" height="{_hs:.2f}" '
                f'fill="var(--cc-green)" opacity=".88"><title>{_s} ok</title></rect>'
            )
        if _f > 0:
            _bars.append(
                f'<rect x="{_x:.2f}" y="{_H - _hs - _hf:.2f}" width="{_bw:.2f}" height="{_hf:.2f}" '
                f'fill="var(--cc-red)"><title>{_f} fail</title></rect>'
            )
    return (
        f'<svg class="iv-pulse-spark" viewBox="0 0 {_W:.0f} {_H:.0f}" '
        f'preserveAspectRatio="none" aria-hidden="true">{"".join(_bars)}</svg>'
    )


def _svg_area_spark(values: list[int], color: str = "var(--cc-blue)") -> str:
    """Filled area sparkline with endpoint dot."""
    if not values or not any(v > 0 for v in values):
        return '<div class="iv-pulse-empty">no deploys in 14d</div>'
    _W, _H = 240.0, 38.0
    _n = len(values)
    _max = max(values) or 1
    _step = _W / max(_n - 1, 1)
    _pts = [
        f"{_i * _step:.2f},{(_H - 1.5 - (_v / _max) * (_H - 3)):.2f}"
        for _i, _v in enumerate(values)
    ]
    _line = " ".join(_pts)
    _area = f"0,{_H:.2f} " + _line + f" {_W:.2f},{_H:.2f}"
    _lx = (_n - 1) * _step
    _ly = _H - 1.5 - (values[-1] / _max) * (_H - 3)
    return (
        f'<svg class="iv-pulse-spark" viewBox="0 0 {_W:.0f} {_H:.0f}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        f'<polygon points="{_area}" fill="{color}" opacity=".16"/>'
        f'<polyline points="{_line}" fill="none" stroke="{color}" '
        f'stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle class="iv-pulse-spark-ping" cx="{_lx:.2f}" cy="{_ly:.2f}" '
        f'r="2.4" fill="none" stroke="{color}" stroke-width="1.2" opacity=".5"/>'
        f'<circle class="iv-pulse-spark-dot" cx="{_lx:.2f}" cy="{_ly:.2f}" '
        f'r="2.4" fill="{color}" stroke="#fff" stroke-width="1"/>'
        f'</svg>'
    )


def _svg_dist_bar(segments: list[tuple[int, str, str]]) -> str:
    """Horizontal stacked bar + legend row.

    ``segments`` = ``[(count, color, label), ...]``. Renders track + filled
    segments proportionally; empty segments are omitted from the legend.
    """
    _total = sum(s[0] for s in segments)
    if _total <= 0:
        return '<div class="iv-pulse-empty">no data</div>'
    _W, _H = 240.0, 9.0
    _x = 0.0
    _parts: list[str] = []
    for _cnt, _color, _label in segments:
        if _cnt <= 0:
            continue
        _w = (_cnt / _total) * _W
        _parts.append(
            f'<rect x="{_x:.2f}" y="0" width="{_w:.2f}" height="{_H:.0f}" '
            f'fill="{_color}"><title>{_label}: {_cnt}</title></rect>'
        )
        _x += _w
    _svg = (
        f'<svg class="iv-pulse-bar" viewBox="0 0 {_W:.0f} {_H:.0f}" '
        f'preserveAspectRatio="none" aria-hidden="true">{"".join(_parts)}</svg>'
    )
    _legend: list[str] = []
    for _cnt, _color, _label in segments:
        if _cnt <= 0:
            continue
        _legend.append(
            f'<span class="iv-pulse-leg">'
            f'<span class="iv-pulse-dot" style="background:{_color}"></span>'
            f'{_label} <b>{_cnt}</b></span>'
        )
    return _svg + '<div class="iv-pulse-legend">' + "".join(_legend) + '</div>'


def _build_event_ribbon(
    events: list[dict],
    start_utc: datetime,
    end_utc: datetime,
    window_label: str,
    n_buckets: int = 60,
) -> str:
    """Stacked histogram ribbon of ``events`` over the event-log time window.

    Events are bucketed by ``_ts`` and stacked by ``type``. Empty windows
    render a minimal placeholder so the slot doesn't collapse jarringly.
    """
    _types_order = ["build-develop", "build-release", "deploy",
                    "release", "request", "commit"]
    _type_colors = {
        "build-develop": "var(--cc-teal)",
        "build-release": "var(--cc-accent)",
        "deploy":        "var(--cc-green)",
        "release":       "var(--cc-amber)",
        "request":       "var(--cc-blue)",
        "commit":        "var(--cc-text-mute)",
    }
    _type_labels = {
        "build-develop": "dev build",
        "build-release": "rel build",
        "deploy":        "deploy",
        "release":       "release",
        "request":       "request",
        "commit":        "commit",
    }
    _duration = (end_utc - start_utc).total_seconds()
    if _duration <= 0:
        return ""
    _bucket_s = _duration / n_buckets
    _buckets: list[list[int]] = [[0] * len(_types_order) for _ in range(n_buckets)]
    _type_idx = {_t: _i for _i, _t in enumerate(_types_order)}
    _total_typed = 0
    for _ev in events:
        _ts = _ev.get("_ts")
        if _ts is None:
            continue
        _dt = _ts.to_pydatetime() if hasattr(_ts, "to_pydatetime") else _ts
        _off = (_dt - start_utc).total_seconds()
        if _off < 0 or _off > _duration:
            continue
        _bi = min(int(_off / _bucket_s), n_buckets - 1)
        _ti = _type_idx.get(_ev.get("type") or "")
        if _ti is None:
            continue
        _buckets[_bi][_ti] += 1
        _total_typed += 1

    if _total_typed == 0:
        return (
            '<div class="el-ribbon">'
            '<div class="el-ribbon-head">'
            f'<span class="el-ribbon-title"><b>Activity ribbon</b> · {window_label.lower()} · no events charted</span>'
            '</div>'
            '<div class="el-ribbon-empty">No events landed in the bucketed window.</div>'
            '</div>'
        )

    _W, _H = 1200.0, 52.0
    _slot = _W / n_buckets
    _bw = _slot * 0.82
    _pad = (_slot - _bw) / 2
    _max = max((sum(_b) for _b in _buckets), default=1) or 1
    _bars: list[str] = []
    for _i, _row in enumerate(_buckets):
        _tot = sum(_row)
        if _tot == 0:
            continue
        _x = _i * _slot + _pad
        _stacked = 0.0
        for _ti, _cnt in enumerate(_row):
            if _cnt <= 0:
                continue
            _h = (_cnt / _max) * (_H - 3)
            _y = _H - 1 - _stacked - _h
            _t = _types_order[_ti]
            _bars.append(
                f'<rect x="{_x:.2f}" y="{_y:.2f}" width="{_bw:.2f}" '
                f'height="{_h:.2f}" fill="{_type_colors[_t]}" opacity=".90">'
                f'<title>{_type_labels[_t]}: {_cnt}</title></rect>'
            )
            _stacked += _h
    _baseline = (
        f'<line x1="0" y1="{_H - 0.5:.2f}" x2="{_W:.0f}" y2="{_H - 0.5:.2f}" '
        f'stroke="var(--cc-border)" stroke-width=".6"/>'
    )

    # Weekend bands — pale vertical strips marking Saturday / Sunday within the
    # window, rendered behind the bars so they read as context, not foreground.
    # Only drawn for windows where weekends are semantically meaningful (< 90d)
    # and the bucket resolution is fine enough to resolve a day (~1d per bucket
    # or finer). Otherwise the bands would dominate the ribbon.
    _weekend_bands: list[str] = []
    if _duration <= 86400 * 90 and _bucket_s <= 86400 * 1.5:
        _day_cursor = start_utc.astimezone(DISPLAY_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        _end_local = end_utc.astimezone(DISPLAY_TZ)
        while _day_cursor < _end_local:
            if _day_cursor.weekday() >= 5:  # Sat=5, Sun=6
                _day_end = _day_cursor + timedelta(days=1)
                _wk_start = max(_day_cursor, start_utc.astimezone(DISPLAY_TZ))
                _wk_end = min(_day_end, _end_local)
                _wx0 = ((_wk_start.astimezone(timezone.utc)
                         - start_utc).total_seconds() / _duration) * _W
                _wx1 = ((_wk_end.astimezone(timezone.utc)
                         - start_utc).total_seconds() / _duration) * _W
                if _wx1 - _wx0 > 0.5:
                    _weekend_bands.append(
                        f'<rect class="el-ribbon-weekend" x="{_wx0:.2f}" y="0" '
                        f'width="{_wx1 - _wx0:.2f}" height="{_H:.0f}" '
                        f'fill="var(--cc-text-mute)"/>'
                    )
            _day_cursor = _day_cursor + timedelta(days=1)

    # Peak marker — find the single tallest bucket and draw a faint dashed
    # vertical rail + small label so users can locate the activity spike in
    # one glance without hunting through tooltips.
    _peak_bi = -1
    _peak_tot = 0
    for _i, _row in enumerate(_buckets):
        _t = sum(_row)
        if _t > _peak_tot:
            _peak_tot = _t
            _peak_bi = _i
    _peak_svg = ""
    if _peak_bi >= 0 and _peak_tot > 0 and _peak_tot >= 2:
        _px = _peak_bi * _slot + _slot / 2
        # Keep the label anchored inside the ribbon even near the left / right
        # edges so it doesn't get clipped by the SVG viewBox.
        _lbl_anchor = (
            "start" if _px < 40
            else "end" if _px > _W - 40
            else "middle"
        )
        _peak_svg = (
            f'<g class="el-ribbon-peak">'
            f'<line class="el-ribbon-peak-line" x1="{_px:.2f}" y1="2" '
            f'x2="{_px:.2f}" y2="{_H - 1:.2f}"/>'
            f'<text class="el-ribbon-peak-label" x="{_px:.2f}" y="10" '
            f'text-anchor="{_lbl_anchor}">▲ peak · {_peak_tot}</text>'
            f'<title>Peak bucket: {_peak_tot} events</title>'
            f'</g>'
        )

    _totals = {_t: 0 for _t in _types_order}
    for _row in _buckets:
        for _ti, _cnt in enumerate(_row):
            _totals[_types_order[_ti]] += _cnt
    _legend: list[str] = []
    for _t in _types_order:
        _c = _totals[_t]
        if _c <= 0:
            continue
        _legend.append(
            f'<span class="el-rib-leg">'
            f'<span class="el-rib-dot" style="background:{_type_colors[_t]}"></span>'
            f'{_type_labels[_t]} <b>{_c}</b></span>'
        )

    if _duration < 86400 * 2:
        _fmt = "%H:%M"
    elif _duration < 86400 * 30:
        _fmt = "%m-%d %H:%M"
    else:
        _fmt = "%m-%d"
    _sl = start_utc.astimezone(DISPLAY_TZ).strftime(_fmt)
    _el = end_utc.astimezone(DISPLAY_TZ).strftime(_fmt)
    # A middle tick helps anchor longer windows.
    _mid_utc = start_utc + (end_utc - start_utc) / 2
    _ml = _mid_utc.astimezone(DISPLAY_TZ).strftime(_fmt)
    return (
        '<div class="el-ribbon">'
        '<div class="el-ribbon-head">'
        f'<span class="el-ribbon-title"><b>Activity ribbon</b> · '
        f'{window_label.lower()} · {n_buckets} buckets · {_total_typed} events</span>'
        f'<span class="el-ribbon-legend">{"".join(_legend)}</span>'
        '</div>'
        f'<svg class="el-ribbon-svg" viewBox="0 0 {_W:.0f} {_H:.0f}" '
        f'preserveAspectRatio="none" aria-hidden="true">'
        f'{"".join(_weekend_bands)}{_baseline}{"".join(_bars)}{_peak_svg}'
        f'</svg>'
        '<div class="el-ribbon-axis">'
        f'<span>{_sl}</span><span>{_ml}</span><span>{_el}</span>'
        '</div>'
        '</div>'
    )


@st.fragment(run_every="300s")
def _render_inventory_view() -> None:
    """Pipelines inventory table — one row per registered pipeline."""

    # ── Controls ────────────────────────────────────────────────────────────
    # Sort choices: each key maps to (label, ordering_fn, descending_bool, badge_label).
    # Ordering functions return a (missing_flag, value) tuple so "no data" rows
    # always land at the end regardless of direction.
    _IV_SORT_OPTIONS = [
        "Application · A → Z",
        "Application · Z → A",
        "Latest activity · newest first",
        "Latest activity · oldest first",
        "Vulnerabilities · highest first",
        "Vulnerabilities · lowest first",
        "Latest PRD deploy · newest first",
        "Latest PRD deploy · oldest first",
        "Live in PRD first",
    ]
    _IV_SORT_BADGES = {
        "Application · A → Z":              "A → Z",
        "Application · Z → A":              "Z → A",
        "Latest activity · newest first":   "Activity ↓",
        "Latest activity · oldest first":   "Activity ↑",
        "Vulnerabilities · highest first":  "Vulns ↓",
        "Vulnerabilities · lowest first":   "Vulns ↑",
        "Latest PRD deploy · newest first": "PRD ↓",
        "Latest PRD deploy · oldest first": "PRD ↑",
        "Live in PRD first":                "Live ✓",
    }

    # Shared controls come from the global rail (project/search/per-project).
    iv_project_filter = _shared_project_filter()
    iv_search = _shared_search_query()
    iv_per_project = _shared_per_project()

    # ── Build scope filters (like the event log but for inventory) ──────────
    _iv_sf: list[dict] = list(scope_filters_inv())
    if iv_project_filter:
        _iv_sf = [f for f in _iv_sf if not (
            isinstance(f, dict) and "term" in f and "project.keyword" in f["term"]
        )]
        _iv_sf = [f for f in _iv_sf if not (
            isinstance(f, dict) and "terms" in f and "project.keyword" in f["terms"]
        )]
        _iv_sf.append({"term": {"project.keyword": iv_project_filter}})

    _iv_scope_key = json.dumps(_iv_sf, sort_keys=True, default=str)
    # Full scope rows — stable across search/pill/sort interactions so the
    # expensive ES fetches and popover HTML cache key on scope alone.
    _inv_rows_all = _fetch_full_inventory(_iv_scope_key)
    # Mutable view that search/pills/sort narrow. Popovers are always built
    # from _inv_rows_all, so cached HTML remains correct when filters change.
    _inv_rows = list(_inv_rows_all)

    # Apply text search filter client-side.
    if iv_search:
        _iv_terms = [_t for _t in iv_search.split() if _t]

        def _iv_haystack(_r: dict) -> str:
            _parts: list[str] = [
                _r.get("application", ""),
                _r.get("project", ""),
                _r.get("company", ""),
                _r.get("app_type", ""),
                _r.get("build_technology", ""),
                _r.get("deploy_technology", ""),
                _r.get("deploy_platform", ""),
                _r.get("build_image_name", ""),
                _r.get("build_image_tag", ""),
                _r.get("deploy_image_name", ""),
                _r.get("deploy_image_tag", ""),
            ]
            _teams = _r.get("teams") or {}
            for _tk, _tv in _teams.items():
                _parts.append(_tk)
                if isinstance(_tv, (list, tuple, set)):
                    _parts.extend(str(x) for x in _tv)
                else:
                    _parts.append(str(_tv))
            return " ".join(_parts).lower()

        _inv_rows = [
            r for r in _inv_rows
            if all(_t in _iv_haystack(r) for _t in _iv_terms)
        ]

    # ── Fetch PRD status + latest-at-each-stage + Prismacloud ───────────────
    # Fetches use the FULL scope so results are stable across search/pill
    # narrowing and the @st.cache_data caches hit across interactions.
    _iv_apps = tuple(sorted({r["application"] for r in _inv_rows_all}))
    _iv_prd_map    = _fetch_prd_status(_iv_apps)     if _iv_apps else {}
    _iv_stages_map = _fetch_latest_stages(_iv_apps)  if _iv_apps else {}

    _iv_prisma_keys: set[tuple[str, str]] = set()
    for _a, _prd in _iv_prd_map.items():
        _pv = (_prd or {}).get("version") or ""
        if _pv:
            _iv_prisma_keys.add((_a, _pv))
    for _a, _stages in _iv_stages_map.items():
        for _st_data in _stages.values():
            _v = (_st_data or {}).get("version") or ""
            if _v:
                _iv_prisma_keys.add((_a, _v))
    _iv_prisma_map = _fetch_prismacloud(tuple(sorted(_iv_prisma_keys))) if _iv_prisma_keys else {}
    _iv_vermeta_map = _fetch_version_meta(tuple(sorted(_iv_prisma_keys))) if _iv_prisma_keys else {}

    # ── Team extraction helper (inventory rows may carry multiple *_team fields) ─
    def _iv_row_teams(_r: dict) -> set[str]:
        """All team values across every *_team field on a row."""
        _out: set[str] = set()
        for _tv in (_r.get("teams") or {}).values():
            if isinstance(_tv, (list, tuple, set)):
                for _x in _tv:
                    if _x:
                        _out.add(str(_x))
            elif _tv:
                _out.add(str(_tv))
        return _out

    # ── Filter keys + non-admin lock rules ─────────────────────────────────
    # Non-admins: company auto-scopes to st.session_state.company. The
    # Companies tile is NOT shown in the stat row (the scope is implicit).
    # Team filter: hidden when the user has 0 or 1 session teams; when >1
    # the Teams tile renders with options restricted to those session teams.
    _iv_session_company: str = (st.session_state.get("company") or "").strip()
    _iv_session_teams: list[str] = [
        str(_t).strip() for _t in (st.session_state.get("teams") or []) if _t
    ]

    _iv_filter_keys = {
        "company": "iv_f_company_v1",
        "team":    "iv_f_team_v1",
        "project": "iv_f_project_v1",
        "app":     "iv_f_app_v1",
        "build":   "iv_tech_pills_v1",
        "deploy":  "iv_deploy_tech_pills_v1",
        "platform":"iv_deploy_platform_pills_v1",
        "combo":   "iv_f_combo_v1",
    }

    if not _is_admin and _iv_session_company:
        st.session_state[_iv_filter_keys["company"]] = [_iv_session_company]
    elif not _is_admin:
        st.session_state[_iv_filter_keys["company"]] = []
    if not _is_admin and len(_iv_session_teams) == 1:
        st.session_state[_iv_filter_keys["team"]] = list(_iv_session_teams)
    elif not _is_admin and len(_iv_session_teams) == 0:
        st.session_state[_iv_filter_keys["team"]] = []

    # ── Read current selections (before applying any filter) ──────────────
    _sel_company  = list(st.session_state.get(_iv_filter_keys["company"]) or [])
    _sel_team     = list(st.session_state.get(_iv_filter_keys["team"])    or [])
    _sel_project  = list(st.session_state.get(_iv_filter_keys["project"]) or [])
    _sel_app      = list(st.session_state.get(_iv_filter_keys["app"])     or [])
    _sel_build    = list(st.session_state.get(_iv_filter_keys["build"])   or [])
    _sel_deploy   = list(st.session_state.get(_iv_filter_keys["deploy"])  or [])
    _sel_platform = list(st.session_state.get(_iv_filter_keys["platform"]) or [])
    _sel_combo    = list(st.session_state.get(_iv_filter_keys["combo"])   or [])

    # Pill selections are "glyph value · count" strings — extract the raw value.
    def _pill_to_val(opt: str) -> str:
        _core = opt.split(" ", 1)[1] if " " in opt else opt
        if " · " in _core:
            _core = _core.rsplit(" · ", 1)[0]
        return _core
    _sel_build_vals    = {_pill_to_val(o) for o in _sel_build}
    _sel_deploy_vals   = {_pill_to_val(o) for o in _sel_deploy}
    _sel_platform_vals = {_pill_to_val(o) for o in _sel_platform}

    # Combo encoding: "⚙ {bt}  /  ⛭ {dt}  /  ☁ {dp}"  — empty field → "—".
    # Selection strings may carry a trailing " · <count>" annotation (the
    # same convention used for build/deploy/platform pills). Strip it for
    # canonical matching.
    def _combo_key(bt: str, dt: str, dp: str) -> str:
        return (
            f"⚙ {bt or '—'}  /  "
            f"⛭ {dt or '—'}  /  "
            f"☁ {dp or '—'}"
        )
    def _row_combo(r: dict) -> str | None:
        _bt = (r.get("build_technology") or "").strip()
        _dt = (r.get("deploy_technology") or "").strip()
        _dp = (r.get("deploy_platform") or "").strip()
        if not (_bt or _dt or _dp):
            return None
        return _combo_key(_bt, _dt, _dp)
    def _combo_to_key(opt: str) -> str:
        return opt.rsplit(" · ", 1)[0] if " · " in opt else opt
    _sel_combo_keys = {_combo_to_key(o) for o in _sel_combo}

    # ── Cross-filter helper (leave-one-out) ───────────────────────────────
    # Passing exclude="project" returns rows narrowed by every filter EXCEPT
    # project — so the Projects tile shows projects available under the
    # other active filters, not the already-selected projects.
    def _apply_iv_filters(rows: list[dict], *, exclude: str = "") -> list[dict]:
        out = rows
        if exclude != "company" and _sel_company:
            _s = set(_sel_company)
            out = [r for r in out if (r.get("company") or "") in _s]
        if exclude != "team" and _sel_team:
            _s = set(_sel_team)
            out = [r for r in out if _iv_row_teams(r) & _s]
        if exclude != "project" and _sel_project:
            _s = set(_sel_project)
            out = [r for r in out if (r.get("project") or "") in _s]
        if exclude != "app" and _sel_app:
            _s = set(_sel_app)
            out = [r for r in out if (r.get("application") or "") in _s]
        if exclude != "build" and _sel_build_vals:
            out = [r for r in out if (r.get("build_technology") or "") in _sel_build_vals]
        if exclude != "deploy" and _sel_deploy_vals:
            out = [r for r in out if (r.get("deploy_technology") or "") in _sel_deploy_vals]
        if exclude != "platform" and _sel_platform_vals:
            out = [r for r in out if (r.get("deploy_platform") or "") in _sel_platform_vals]
        if exclude != "combo" and _sel_combo_keys:
            out = [r for r in out if _row_combo(r) in _sel_combo_keys]
        return out

    # ── Leave-one-out option dicts for each dimension's tile popover ───────
    def _count_single(rows: list[dict], field: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            v = (r.get(field) or "").strip()
            if v:
                out[v] = out.get(v, 0) + 1
        return out

    def _count_teams(rows: list[dict]) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            for t in _iv_row_teams(r):
                out[t] = out.get(t, 0) + 1
        return out

    def _count_combos(rows: list[dict]) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in rows:
            c = _row_combo(r)
            if c:
                out[c] = out.get(c, 0) + 1
        return out

    _iv_companies_opts = _count_single(_apply_iv_filters(_inv_rows, exclude="company"), "company")
    _iv_teams_opts     = _count_teams(_apply_iv_filters(_inv_rows, exclude="team"))
    _iv_projects_opts  = _count_single(_apply_iv_filters(_inv_rows, exclude="project"), "project")
    _iv_apps_opts      = _count_single(_apply_iv_filters(_inv_rows, exclude="app"), "application")
    _iv_build_opts     = _count_single(_apply_iv_filters(_inv_rows, exclude="build"), "build_technology")
    _iv_deploy_opts    = _count_single(_apply_iv_filters(_inv_rows, exclude="deploy"), "deploy_technology")
    _iv_platform_opts  = _count_single(_apply_iv_filters(_inv_rows, exclude="platform"), "deploy_platform")
    _iv_combo_opts     = _count_combos(_apply_iv_filters(_inv_rows, exclude="combo"))

    # ── Active selection summary + sort badge ─────────────────────────────
    _iv_active_sel: dict[str, list[str]] = {
        _k: list(st.session_state.get(_key) or [])
        for _k, _key in _iv_filter_keys.items()
    }
    _iv_active_total = sum(len(v) for v in _iv_active_sel.values())
    if not _is_admin:
        _iv_active_total -= len(_iv_active_sel.get("company") or [])
        if len(_iv_session_teams) == 1:
            _iv_active_total -= len(_iv_active_sel.get("team") or [])
    _iv_active_total = max(_iv_active_total, 0)

    _iv_sort_badge = _IV_SORT_BADGES.get(
        st.session_state.get("iv_sort_v1", _IV_SORT_OPTIONS[0]), "A → Z",
    )

    # ── Apply every filter to produce the final scoped row list ───────────
    _inv_rows = _apply_iv_filters(_inv_rows)

    # ── Reactive aggregates (computed POST-filter) ────────────────────────
    _post_companies: set[str] = set()
    _post_teams: set[str] = set()
    _post_projects: set[str] = set()
    _post_apps: set[str] = set()
    _post_build: set[str] = set()
    _post_deploy: set[str] = set()
    _post_platform: set[str] = set()
    _post_pipelines: set[tuple[str, str, str]] = set()
    for _r in _inv_rows:
        _co = (_r.get("company") or "").strip()
        if _co: _post_companies.add(_co)
        for _t in _iv_row_teams(_r):
            _post_teams.add(_t)
        _pj = (_r.get("project") or "").strip()
        if _pj: _post_projects.add(_pj)
        _ap = (_r.get("application") or "").strip()
        if _ap: _post_apps.add(_ap)
        _bt = (_r.get("build_technology") or "").strip()
        if _bt: _post_build.add(_bt)
        _dt = (_r.get("deploy_technology") or "").strip()
        if _dt: _post_deploy.add(_dt)
        _dp = (_r.get("deploy_platform") or "").strip()
        if _dp: _post_platform.add(_dp)
        if _bt or _dt or _dp:
            _post_pipelines.add((_bt, _dt, _dp))

    _iv_total = len(_inv_rows)
    _live_apps: set[str] = set()
    _live_projects: set[str] = set()
    for _r in _inv_rows:
        _ap = _r.get("application") or ""
        if _ap and (_iv_prd_map.get(_ap) or {}).get("live"):
            _live_apps.add(_ap)
            _pj = (_r.get("project") or "").strip()
            if _pj:
                _live_projects.add(_pj)
    _iv_live = len(_live_apps)
    _iv_live_pct = f"{_iv_live / _iv_total * 100:.0f}%" if _iv_total else "—"
    _iv_layout = "per-project" if iv_per_project else "consolidated"
    _proj_live_pct = (
        f"{len(_live_projects) / len(_post_projects) * 100:.0f}%"
        if _post_projects else "—"
    )

    # ── Sticky secondary bar: Sort popover + active chips + Clear button ──
    # Dimensional filters now live inside the stat tiles (click any tile to
    # filter by that dimension). This bar keeps Sort + filter summary sticky
    # as the user scrolls.
    with st.container(key="cc_filter_secondary"):
        _iv_fb = st.columns([1.8, 5.0, 0.8], vertical_alignment="center")

    with _iv_fb[0]:
        with st.popover(
            f"↕ Sort · {_iv_sort_badge}",
            use_container_width=True,
            help="Change how pipelines are ordered",
        ):
            st.markdown(
                '<div class="iv-pill-caption">Sort order</div>',
                unsafe_allow_html=True,
            )
            st.selectbox(
                "Sort by", _IV_SORT_OPTIONS, index=0, key="iv_sort_v1",
                label_visibility="collapsed",
                help="Activity uses latest stage date · vulnerabilities are "
                     "weighted (critical ≫ high ≫ medium ≫ low) on the PRD version",
            )

    with _iv_fb[1]:
        _chip_specs: list[tuple[str, str]] = []
        if not _is_admin and _iv_session_company:
            _chip_specs.append((f"🏢 {_iv_session_company} (scoped)", "session"))
        if not _is_admin and len(_iv_session_teams) == 1:
            _chip_specs.append((f"👥 {_iv_session_teams[0]} (scoped)", "session"))
        if _is_admin:
            for _v in _iv_active_sel["company"]:
                _chip_specs.append((f"🏢 {_v}", "user"))
        _team_locked = (not _is_admin) and len(_iv_session_teams) == 1
        if not _team_locked:
            for _v in _iv_active_sel["team"]:
                _chip_specs.append((f"👥 {_v}", "user"))
        for _v in _iv_active_sel["project"]:
            _chip_specs.append((f"📁 {_v}", "user"))
        for _v in _iv_active_sel["app"]:
            _chip_specs.append((f"▣ {_v}", "user"))
        for _v in _iv_active_sel["build"]:
            _chip_specs.append((_v, "user"))
        for _v in _iv_active_sel["deploy"]:
            _chip_specs.append((_v, "user"))
        for _v in _iv_active_sel["platform"]:
            _chip_specs.append((_v, "user"))
        for _v in _iv_active_sel["combo"]:
            _chip_specs.append((f"⇋ {_combo_to_key(_v)}", "user"))
        _chip_specs.append((f"↕ Sort: {_iv_sort_badge}", "sort"))
        if _chip_specs:
            _chip_html = []
            for _txt, _kind in _chip_specs:
                _cls = (
                    "iv-active-chip" if _kind == "user"
                    else "iv-active-chip iv-active-chip-sess" if _kind == "session"
                    else "iv-active-chip iv-active-chip-sort"
                )
                _chip_html.append(f'<span class="{_cls}">{_txt}</span>')
            st.markdown(
                '<div class="iv-active-chips">' + "".join(_chip_html) + '</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="iv-filter-hint">No filters applied — click any tile '
                'below to narrow the scope.</div>',
                unsafe_allow_html=True,
            )

    with _iv_fb[2]:
        if _iv_active_total:
            if st.button("Clear", key="iv_filters_clear_v1",
                         use_container_width=True,
                         help="Clear all user-selected filters"):
                _clear_keys = [
                    _iv_filter_keys["project"],
                    _iv_filter_keys["app"],
                    _iv_filter_keys["build"],
                    _iv_filter_keys["deploy"],
                    _iv_filter_keys["platform"],
                    _iv_filter_keys["combo"],
                ]
                if _is_admin:
                    _clear_keys.append(_iv_filter_keys["company"])
                    _clear_keys.append(_iv_filter_keys["team"])
                elif len(_iv_session_teams) > 1:
                    _clear_keys.append(_iv_filter_keys["team"])
                for _k in _clear_keys:
                    st.session_state.pop(_k, None)
                st.rerun()

    iv_sort = st.session_state.get("iv_sort_v1", _IV_SORT_OPTIONS[0])

    # ── Filterable stat tiles — each is a visual HTML tile with an
    # invisible popover-button overlay. Clicking anywhere on the tile opens
    # that dimension's filter popover. Because every tile renders the same
    # HTML structure, heights are identical regardless of content length.
    _TILE_COLORS = {
        "company":  "var(--cc-accent)",
        "team":     "var(--cc-teal)",
        "project":  "var(--cc-blue)",
        "app":      "var(--cc-green)",
        "build":    "var(--cc-amber)",
        "deploy":   "var(--cc-teal)",
        "platform": "var(--cc-blue)",
        "combo":    "var(--cc-red)",
    }

    def _render_tile_ms(dim_key: str, opts: dict[str, int],
                        placeholder: str) -> None:
        ss_key = _iv_filter_keys[dim_key]
        _cur = list(st.session_state.get(ss_key) or [])
        _union = set(opts.keys()) | set(_cur)
        _sorted_vals = sorted(_union, key=lambda v: (-opts.get(v, 0), v.lower()))
        def _fmt(v: str) -> str:
            _c = opts.get(v, 0)
            return f"{v}  ·  {_c}" if _c else f"{v}  ·  (filtered out)"
        st.markdown(
            f'<div class="iv-tile-hint">{len(opts)} available · '
            f'{len(_cur)} selected</div>',
            unsafe_allow_html=True,
        )
        st.multiselect(
            placeholder, options=_sorted_vals, key=ss_key,
            label_visibility="collapsed", placeholder=placeholder,
            format_func=_fmt,
        )

    def _render_tile_pills(dim_key: str, opts: dict[str, int], glyph: str) -> None:
        ss_key = _iv_filter_keys[dim_key]
        _cur = list(st.session_state.get(ss_key) or [])
        _cur_vals = {_pill_to_val(o) for o in _cur}
        _all_vals = set(opts.keys()) | _cur_vals
        _sorted = sorted(_all_vals, key=lambda v: (-opts.get(v, 0), v.lower()))
        _options = [f"{glyph} {v} · {opts.get(v, 0)}" for v in _sorted]
        _new_cur = [o for o in _options if _pill_to_val(o) in _cur_vals]
        if _new_cur != _cur:
            st.session_state[ss_key] = _new_cur
        st.markdown(
            f'<div class="iv-tile-hint">{len(opts)} available · '
            f'{len(_cur_vals)} selected</div>',
            unsafe_allow_html=True,
        )
        st.pills(
            dim_key, options=_options, selection_mode="multi",
            default=None, key=ss_key, label_visibility="collapsed",
        )

    def _render_tile_combos(opts: dict[str, int]) -> None:
        """Multiselect for pipeline (build×deploy×platform) combinations.
        Selection strings include ` · <count>` for pill-style persistence;
        _combo_to_key normalizes them to canonical combo keys for matching."""
        ss_key = _iv_filter_keys["combo"]
        _cur = list(st.session_state.get(ss_key) or [])
        _cur_keys = {_combo_to_key(o) for o in _cur}
        _all_keys = set(opts.keys()) | _cur_keys
        _sorted = sorted(_all_keys, key=lambda v: (-opts.get(v, 0), v))
        _options = [f"{k} · {opts.get(k, 0)}" for k in _sorted]
        _new_cur = [o for o in _options if _combo_to_key(o) in _cur_keys]
        if _new_cur != _cur:
            st.session_state[ss_key] = _new_cur
        st.markdown(
            f'<div class="iv-tile-hint">{len(opts)} combinations available · '
            f'{len(_cur_keys)} selected</div>',
            unsafe_allow_html=True,
        )
        st.multiselect(
            "Pipeline combinations", options=_options, key=ss_key,
            label_visibility="collapsed",
            placeholder="Select build × deploy × platform combinations",
        )

    # Tile specs: (dim_key, glyph, label, number, sub_markdown)
    _tile_specs: list[tuple[str, str, str, int, str]] = []
    if _is_admin:
        _tile_specs.append(("company", "🏢", "Companies", len(_post_companies),
                            "Tenant boundaries in scope"))
        _tile_specs.append(("team", "👥", "Teams", len(_post_teams),
                            "Distinct owner teams"))
    elif len(_iv_session_teams) > 1:
        _tile_specs.append(("team", "👥", "Teams", len(_post_teams),
                            f"Across your {len(_iv_session_teams)} session teams"))
    _tile_specs.append(("project", "📁", "Projects", len(_post_projects),
                        f"<b>{len(_live_projects)}</b> live in PRD ({_proj_live_pct})"))
    _tile_specs.append(("app", "▣", "Applications", _iv_total,
                        f"<b>{_iv_live}</b> live in PRD ({_iv_live_pct})"))
    _tile_specs.append(("build", "⚙", "Build stacks", len(_post_build),
                        "Distinct build technologies"))
    _tile_specs.append(("deploy", "⛭", "Deploy stacks", len(_post_deploy),
                        "Distinct deployment tooling"))
    _tile_specs.append(("platform", "☁", "Deploy platforms", len(_post_platform),
                        "Distinct target platforms"))
    _tile_specs.append(("combo", "⇋", "Unique pipelines", len(_post_pipelines),
                        "build × deploy × platform"))

    with st.container(key="cc_iv_tiles_row"):
        _tile_cols = st.columns(len(_tile_specs), gap="small")
        for _idx, (_dk, _glyph, _tlabel, _tnum, _tsub_md) in enumerate(_tile_specs):
            with _tile_cols[_idx]:
                _nsel = len(_iv_active_sel.get(_dk, []))
                _accent = _TILE_COLORS[_dk]
                _badge_html = (
                    f'<span class="iv-tile-badge">✱ {_nsel}</span>'
                    if _nsel else ''
                )
                _tile_html = (
                    f'<div class="iv-tile iv-tile-click" '
                    f'style="--iv-stat-accent:{_accent}">'
                    f'<div class="iv-tile-head">'
                    f'<span class="iv-tile-glyph">{_glyph}</span>'
                    f'<span class="iv-tile-label">{_tlabel}</span>'
                    f'{_badge_html}'
                    f'</div>'
                    f'<div class="iv-tile-number">{_tnum}</div>'
                    f'<div class="iv-tile-sub">{_tsub_md}</div>'
                    f'<div class="iv-tile-cta">Click to filter ▸</div>'
                    f'</div>'
                )
                with st.container(key=f"cc_tile_{_dk}"):
                    st.markdown(_tile_html, unsafe_allow_html=True)
                    # Empty-label popover — becomes a transparent overlay on
                    # top of the tile via CSS. The tile HTML provides all
                    # visuals; the popover button provides clickability.
                    with st.popover(" ", use_container_width=True,
                                    help=f"Filter by {_tlabel.lower()}"):
                        st.markdown(
                            f'<div class="iv-tile-pop-head">'
                            f'<span class="iv-tile-pop-glyph">{_glyph}</span>'
                            f'<span class="iv-tile-pop-title">{_tlabel}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                        if _dk == "company":
                            if _is_admin and (_iv_companies_opts or _sel_company):
                                _render_tile_ms("company", _iv_companies_opts,
                                                "Select companies")
                            else:
                                st.caption("Company scope is implicit for your session.")
                        elif _dk == "team":
                            if _is_admin and (_iv_teams_opts or _sel_team):
                                _render_tile_ms("team", _iv_teams_opts,
                                                "Select teams")
                            elif (not _is_admin) and len(_iv_session_teams) > 1:
                                _sess_opts = {
                                    t: _iv_teams_opts.get(t, 0)
                                    for t in _iv_session_teams
                                }
                                _render_tile_ms("team", _sess_opts,
                                                "Narrow your session teams")
                            else:
                                st.caption("Team scope is locked to your session.")
                        elif _dk == "project":
                            _render_tile_ms("project", _iv_projects_opts,
                                            "Select projects")
                        elif _dk == "app":
                            _render_tile_ms("app", _iv_apps_opts,
                                            "Select applications")
                        elif _dk == "build":
                            _render_tile_pills("build", _iv_build_opts, "⚙")
                        elif _dk == "deploy":
                            _render_tile_pills("deploy", _iv_deploy_opts, "⛭")
                        elif _dk == "platform":
                            _render_tile_pills("platform", _iv_platform_opts, "☁")
                        elif _dk == "combo":
                            _render_tile_combos(_iv_combo_opts)

    # ── Fleet pulse strip — four subtle visualizations of scope state ──────
    # Two temporal sparklines (14d build success, PRD deploy cadence) + two
    # distribution bars (PRD freshness, security posture). Everything reflects
    # the current filtered scope so users can redirect the narrative by
    # changing any filter above.
    if _post_apps:
        _pulse = _fetch_inv_pulse(json.dumps(sorted(_post_apps)), days=14)
        _bs = _pulse.get("build_success", [])
        _bf = _pulse.get("build_failure", [])
        _dp = _pulse.get("deploy_prd", [])
        _bs_sum = sum(_bs)
        _bf_sum = sum(_bf)
        _b_total = _bs_sum + _bf_sum
        if _b_total:
            _rate_pct = _bs_sum / _b_total * 100
            _rate = f"{_rate_pct:.0f}"
            _rate_tag = (
                "ok" if _rate_pct >= 90
                else "warn" if _rate_pct >= 75
                else "crit"
            )
            _rate_tag_lbl = (
                "healthy" if _rate_pct >= 90
                else "watch" if _rate_pct >= 75
                else "degraded"
            )
        else:
            _rate = "—"
            _rate_tag = ""
            _rate_tag_lbl = "quiet"
        _dp_total = sum(_dp)
        _dp_peak = max(_dp) if _dp else 0
        _dp_active_days = sum(1 for _v in _dp if _v > 0)
        _dp_tag = "ok" if _dp_active_days else ""
        _dp_tag_lbl = (
            f"{_dp_active_days}/14d" if _dp_active_days else "idle"
        )

        # PRD freshness distribution
        _now_pulse = datetime.now(timezone.utc)
        _fresh = 0; _recent = 0; _stale = 0; _cold = 0; _never = 0
        for _ap in _post_apps:
            _prd = _iv_prd_map.get(_ap) or {}
            _ts_prd = parse_dt(_prd.get("when"))
            if _ts_prd is None:
                _never += 1
                continue
            _pdt = _ts_prd.to_pydatetime()
            if _pdt.tzinfo is None:
                _pdt = _pdt.replace(tzinfo=timezone.utc)
            _dage = (_now_pulse - _pdt).days
            if _dage < 365:    _fresh  += 1
            elif _dage < 730:  _recent += 1
            elif _dage < 1095: _stale  += 1
            else:              _cold   += 1
        _fresh_total = _fresh + _recent + _stale + _cold + _never
        _fresh_pct = (_fresh / _fresh_total * 100) if _fresh_total else 0
        _fresh_tag = (
            "ok" if _fresh_pct >= 60
            else "warn" if _fresh_pct >= 30
            else "crit" if _fresh_total else ""
        )
        _fresh_bar = _svg_dist_bar([
            (_fresh,  "var(--cc-green)",     "fresh <1y"),
            (_recent, "var(--cc-teal)",      "recent <2y"),
            (_stale,  "var(--cc-amber)",     "stale <3y"),
            (_cold,   "var(--cc-red)",       "cold ≥3y"),
            (_never,  "var(--cc-text-mute)", "never"),
        ])

        # Security posture — sum critical/high/medium/low across PRD versions
        _vc = 0; _vh = 0; _vm = 0; _vl = 0
        _apps_scanned = 0
        _apps_with_ver = 0
        for _ap in _post_apps:
            _prd = _iv_prd_map.get(_ap) or {}
            _pv = (_prd.get("version") or "")
            if not _pv:
                continue
            _apps_with_ver += 1
            _sc = _iv_prisma_map.get((_ap, _pv))
            if not _sc:
                continue
            _apps_scanned += 1
            _vc += int(_sc.get("Vcritical") or 0)
            _vh += int(_sc.get("Vhigh")     or 0)
            _vm += int(_sc.get("Vmedium")   or 0)
            _vl += int(_sc.get("Vlow")      or 0)
        _v_crit_high = _vc + _vh
        _sec_tag = (
            "crit" if _vc > 0
            else "warn" if _vh > 0
            else "ok" if _apps_scanned
            else ""
        )
        _sec_tag_lbl = (
            f"{_vc} crit" if _vc > 0
            else f"{_vh} high" if _vh > 0
            else "clean" if _apps_scanned
            else "unscanned"
        )
        _sec_bar = _svg_dist_bar([
            (_vc, "var(--cc-red)",       "critical"),
            (_vh, "var(--cc-amber)",     "high"),
            (_vm, "var(--cc-blue)",      "medium"),
            (_vl, "var(--cc-text-mute)", "low"),
        ])

        _spark_build = _svg_stacked_spark(_bs, _bf)
        _spark_dep = _svg_area_spark(_dp, color="var(--cc-blue)")

        _pulse_html = (
            '<div class="iv-pulse-strip">'
            # Tile 1: Build success rate
            '<div class="iv-pulse-tile" style="--iv-pulse-accent:'
            'linear-gradient(90deg,var(--cc-green),var(--cc-teal))">'
            '<div class="iv-pulse-label">'
            '<span>Build success · 14d</span>'
            + (f'<span class="iv-pulse-tag {_rate_tag}">{_rate_tag_lbl}</span>'
               if _rate_tag else '')
            + '</div>'
            + f'<div class="iv-pulse-value">{_rate}'
            + ('<span class="iv-pulse-unit">%</span>' if _b_total else '')
            + '</div>'
            + f'<div class="iv-pulse-sub"><b>{_b_total}</b> builds · '
              f'<b>{_bs_sum}</b> ok · <b>{_bf_sum}</b> fail</div>'
            + _spark_build
            + '<div class="iv-pulse-axis"><span>14d ago</span><span>today</span></div>'
            + '</div>'
            # Tile 2: PRD deploy cadence
            + '<div class="iv-pulse-tile" style="--iv-pulse-accent:'
              'linear-gradient(90deg,var(--cc-blue),var(--cc-accent))">'
            '<div class="iv-pulse-label">'
            '<span>PRD cadence · 14d</span>'
            + (f'<span class="iv-pulse-tag {_dp_tag}">{_dp_tag_lbl}</span>'
               if _dp_tag else f'<span class="iv-pulse-tag">{_dp_tag_lbl}</span>')
            + '</div>'
            + f'<div class="iv-pulse-value">{_dp_total}</div>'
            + f'<div class="iv-pulse-sub">peak <b>{_dp_peak}</b>/day · '
              f'active <b>{_dp_active_days}</b>/14d</div>'
            + _spark_dep
            + '<div class="iv-pulse-axis"><span>14d ago</span><span>today</span></div>'
            + '</div>'
            # Tile 3: PRD freshness
            + '<div class="iv-pulse-tile" style="--iv-pulse-accent:'
              'linear-gradient(90deg,var(--cc-green),var(--cc-amber))">'
            '<div class="iv-pulse-label">'
            '<span>PRD freshness</span>'
            + (f'<span class="iv-pulse-tag {_fresh_tag}">{_fresh_pct:.0f}% fresh</span>'
               if _fresh_tag else '')
            + '</div>'
            + f'<div class="iv-pulse-value">{_fresh}'
            + f'<span class="iv-pulse-unit">/ {_fresh_total}</span>'
            + '</div>'
            + f'<div class="iv-pulse-sub">apps deployed to PRD in the last year</div>'
            + _fresh_bar
            + '</div>'
            # Tile 4: Security posture
            + '<div class="iv-pulse-tile" style="--iv-pulse-accent:'
              'linear-gradient(90deg,var(--cc-red),var(--cc-amber))">'
            '<div class="iv-pulse-label">'
            '<span>Security posture</span>'
            + (f'<span class="iv-pulse-tag {_sec_tag}">{_sec_tag_lbl}</span>'
               if _sec_tag else '')
            + '</div>'
            + f'<div class="iv-pulse-value">{_v_crit_high}</div>'
            + f'<div class="iv-pulse-sub">crit + high · <b>{_apps_scanned}</b>/'
              f'<b>{_apps_with_ver}</b> PRD versions scanned</div>'
            + _sec_bar
            + '</div>'
            + '</div>'
        )
        st.markdown(_pulse_html, unsafe_allow_html=True)

    # ── Sort ────────────────────────────────────────────────────────────────
    # Pre-compute sort-aux maps so sorted() doesn't re-parse dates or walk
    # nested dicts on every key comparison. Each key tuple starts with a
    # "missing" flag so rows without data always land at the end regardless of
    # direction.
    _iv_activity_ts: dict[str, int] = {}
    for _ap, _sm in _iv_stages_map.items():
        _maxv: int | None = None
        for _sd in _sm.values():
            _ts = parse_dt((_sd or {}).get("when"))
            if _ts is not None:
                _v = _ts.value
                if _maxv is None or _v > _maxv:
                    _maxv = _v
        if _maxv is not None:
            _iv_activity_ts[_ap] = _maxv

    _iv_prd_ts_map: dict[str, int] = {}
    for _ap, _prd in _iv_prd_map.items():
        _ts = parse_dt((_prd or {}).get("when"))
        if _ts is not None:
            _iv_prd_ts_map[_ap] = _ts.value

    _iv_vuln_score_map: dict[str, int] = {}
    for _ap, _prd in _iv_prd_map.items():
        _pv = (_prd or {}).get("version") or ""
        if not _pv:
            continue
        _sc = _iv_prisma_map.get((_ap, _pv))
        if not _sc:
            continue
        # Weighted so one critical outranks many highs, etc.
        _iv_vuln_score_map[_ap] = (
            int(_sc.get("Vcritical", 0)) * 1000
            + int(_sc.get("Vhigh",    0)) * 100
            + int(_sc.get("Vmedium",  0)) * 10
            + int(_sc.get("Vlow",     0))
        )

    def _iv_sort_key(r: dict) -> tuple:
        _app = r.get("application") or ""
        _app_lc = _app.lower()
        _proj_lc = (r.get("project") or "").lower()
        if iv_sort in ("Application · A → Z", "Application · Z → A"):
            # Always ascending here; Z → A is handled via a post-reverse so
            # variable-length strings compare correctly.
            return (0, _app_lc, _proj_lc)
        if iv_sort in ("Latest activity · newest first",
                       "Latest activity · oldest first"):
            _v = _iv_activity_ts.get(_app)
            if _v is None:
                return (1, 0, _app_lc)
            if iv_sort == "Latest activity · newest first":
                _v = -_v
            return (0, _v, _app_lc)
        if iv_sort in ("Vulnerabilities · highest first",
                       "Vulnerabilities · lowest first"):
            _score = _iv_vuln_score_map.get(_app)
            if _score is None:
                return (1, 0, _app_lc)
            if iv_sort == "Vulnerabilities · highest first":
                _score = -_score
            return (0, _score, _app_lc)
        if iv_sort in ("Latest PRD deploy · newest first",
                       "Latest PRD deploy · oldest first"):
            _v = _iv_prd_ts_map.get(_app)
            if _v is None:
                return (1, 0, _app_lc)
            if iv_sort == "Latest PRD deploy · newest first":
                _v = -_v
            return (0, _v, _app_lc)
        if iv_sort == "Live in PRD first":
            _prd = _iv_prd_map.get(_app) or {}
            _live = 0 if _prd.get("live") else 1
            return (_live, _app_lc, _proj_lc)
        return (0, _app_lc, _proj_lc)

    _inv_rows = sorted(_inv_rows, key=_iv_sort_key)
    if iv_sort == "Application · Z → A":
        _inv_rows.reverse()

    if not _inv_rows:
        inline_note("No applications match the current filters.", "info")
        # Even on empty inventory, still render the event log below so users
        # see "no events" in the same scope — consistent with the filter-
        # inheritance contract.
        if _show_el:
            st.session_state["_el_inv_scope_apps"] = []
            st.markdown(
                '<div class="cc-panel-head cc-panel-head--numbered cc-panel-head--live" style="margin-top:22px">'
                '<h2 data-section-num="02">Event log</h2>'
                '<span class="cc-panel-tag">Live · 60s · no apps in scope</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            _render_event_log()
        return

    # ── Popover infrastructure (project + app popovers) ─────────────────────
    # Use the full scope set so popovers remain valid regardless of which
    # rows the current search/pill filters happen to show.
    _iv_pop_projects = sorted({r["project"] for r in _inv_rows_all if r.get("project")})
    _iv_proj_map = _fetch_project_details(tuple(_iv_pop_projects)) if _iv_pop_projects else {}

    def _iv_slug(val: str, prefix: str) -> str:
        return prefix + "".join(c.lower() if c.isalnum() else "-" for c in val)[:80]

    def _iv_app_pop_id(app: str) -> str:
        return _iv_slug(app, "iv-app-pop-")

    def _iv_proj_pop_id(proj: str) -> str:
        return _iv_slug(proj, "iv-proj-pop-")

    def _iv_ver_pop_id(app: str, stage: str, ver: str) -> str:
        """One popover per (app, stage, version). Stage is part of the id
        because the same version number can surface in multiple stages with
        different previous-stage baselines."""
        return _iv_slug(f"{app}--{stage}--{ver}", "iv-ver-pop-")

    def _iv_v(val: str) -> str:
        return (f'<span class="ap-v">{val}</span>'
                if val else '<span class="ap-v empty">—</span>')

    def _iv_chip(val: str) -> str:
        return (f'<span class="ap-v"><span class="ap-chip">{val}</span></span>'
                if val else '<span class="ap-v empty">—</span>')

    def _iv_app_type_pill(val: str) -> str:
        _t = (val or "").strip()
        if not _t:
            return '<span class="ap-v empty">—</span>'
        _cls = "is-app" if _t.lower() == "app" else (
            "is-lib" if _t.lower() == "lib" else "is-other"
        )
        return (f'<span class="ap-v"><span class="ap-type-pill {_cls}">'
                f'{_t}</span></span>')

    # Pre-compute app_type per application for stage-cell rendering logic.
    _iv_app_type_map = {
        r["application"]: (r.get("app_type") or "").strip().lower()
        for r in _inv_rows
    }

    # ── Stage cell — version chip popover trigger + compact date ───────────
    _iv_th = 'style="padding:6px 4px;color:var(--cc-text-mute);font-size:0.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase"'

    def _iv_stage_cell(app: str, stage: str) -> str:
        _data = (_iv_stages_map.get(app) or {}).get(stage) or {}
        _ver  = _data.get("version") or ""
        _when = fmt_dt(_data.get("when"), "%Y-%m-%d %H:%M") or ""
        _rel  = _relative_age(_data.get("when")) if _data.get("when") else ""
        _kind = _iv_app_type_map.get(app, "")
        # Lib applications are build-only — everything after "build" is N/A
        # and should read as a positive, not a gap.
        if not _ver and _kind == "lib" and stage != "build":
            return (
                '<span class="iv-stage-nn" title="Libraries do not progress '
                'past build">✓ Not needed</span>'
            )
        # App applications that haven't reached a stage get a subtle warning
        # so the gap is visible without being alarming.
        if not _ver and _kind == "app" and stage != "build":
            return (
                f'<span class="iv-stage-gap" '
                f'title="No {stage} record for this application yet">'
                f'△ Not reached</span>'
            )
        if not _ver:
            return '<span style="color:var(--cc-text-mute);font-size:.70rem">—</span>'
        # For prd stage, attach a live dot when this version matches what's
        # actually live in prd (per _iv_prd_map).
        _dot = ""
        if stage == "prd":
            _prd = _iv_prd_map.get(app) or {}
            if _prd.get("live") and _prd.get("version") == _ver:
                _dot = ('<span class="iv-stage-dot" '
                        'title="Currently live in prd"></span>')
            elif _prd and not _prd.get("live"):
                _dot = ('<span class="iv-stage-dot is-fail" '
                        'title="Last prd attempt failed"></span>')
        _btn = (
            f'<button type="button" class="iv-stage-ver" '
            f'popovertarget="{_iv_ver_pop_id(app, stage, _ver)}" '
            f'title="Click for version details">{_dot}{_ver}</button>'
        )
        # Table row shows the relative age only — the version popover carries
        # the exact absolute timestamp for anyone who needs it.
        if _rel:
            _date_html = (
                f'<div class="iv-stage-when" title="{_when}">'
                f'<span class="iv-stage-rel">{_rel}</span></div>'
            )
        else:
            _date_html = ""
        return f'<div class="iv-stage-cell">{_btn}{_date_html}</div>'

    # Severity tier → ("class", count) helper. Picks the worst non-zero tier
    # so each chip shows the most severe signal at a glance; tooltip carries
    # the full breakdown for detail-seekers.
    def _iv_sec_tier(sc: dict, prefix: str) -> tuple[str, int, int, int, int, int]:
        _c = int((sc.get(f"{prefix}critical") or 0))
        _h = int((sc.get(f"{prefix}high")     or 0))
        _m = int((sc.get(f"{prefix}medium")   or 0))
        _l = int((sc.get(f"{prefix}low")      or 0))
        if _c:   tier, n = "crit", _c
        elif _h: tier, n = "high", _h
        elif _m: tier, n = "med",  _m
        elif _l: tier, n = "low",  _l
        else:    tier, n = "clean", 0
        return (tier, n, _c, _h, _m, _l)

    def _iv_sec_chip(kind: str, sc: dict) -> str:
        """``kind`` is ``V`` (vulnerabilities) or ``C`` (compliance)."""
        _prefix = kind
        _tier, _n, _c, _h, _m, _l = _iv_sec_tier(sc, _prefix)
        _lbl = "Vulns" if kind == "V" else "Compliance"
        _title = f"{_lbl}: {_c} critical · {_h} high · {_m} medium · {_l} low"
        if _tier == "clean":
            return (f'<span class="iv-sec-chip iv-sec-clean" title="{_title}">'
                    f'<span class="iv-sec-label">{kind}</span>✓</span>')
        return (f'<span class="iv-sec-chip iv-sec-{_tier}" title="{_title}">'
                f'<span class="iv-sec-label">{kind}</span>{_n}</span>')

    def _iv_app_posture_html(app: str) -> str:
        """Render V + C chips side-by-side for *app*'s PRD-live scan.

        Returns an "N/A" chip when we don't have a PRD version or no scan for
        it — that way the column's visual rhythm stays even across the table.
        """
        _prd = _iv_prd_map.get(app) or {}
        _pv = _prd.get("version") or ""
        _sc = _iv_prisma_map.get((app, _pv)) if _pv else None
        if not _sc:
            _reason = ("no PRD version on record" if not _pv
                       else f"no Prismacloud scan for {app}@{_pv}")
            return (
                f'<span class="iv-sec-row">'
                f'<span class="iv-sec-chip iv-sec-na" title="{_reason}">'
                f'<span class="iv-sec-label">V</span>·</span>'
                f'<span class="iv-sec-chip iv-sec-na" title="{_reason}">'
                f'<span class="iv-sec-label">C</span>·</span>'
                f'</span>'
            )
        return (
            f'<span class="iv-sec-row">'
            f'{_iv_sec_chip("V", _sc)}{_iv_sec_chip("C", _sc)}'
            f'</span>'
        )

    def _iv_app_cell(app: str) -> str:
        return (
            f'<div class="iv-app-cell">'
            f'<button type="button" class="el-app-trigger" '
            f'popovertarget="{_iv_app_pop_id(app)}" '
            f'title="Click for full inventory details">{app}</button>'
            f'{_iv_app_posture_html(app)}'
            f'</div>'
        )

    def _iv_proj_cell(proj: str) -> str:
        if not proj:
            return '<span style="color:var(--cc-text-mute);font-size:.72rem">—</span>'
        if proj in _iv_proj_map:
            return (
                f'<button type="button" class="el-proj-trigger" '
                f'popovertarget="{_iv_proj_pop_id(proj)}" '
                f'title="Click for teams & applications">{proj}</button>'
            )
        return f'<span style="color:var(--cc-text-dim);font-size:.78rem">{proj}</span>'

    def _iv_row_html(r: dict, *, include_project: bool = True) -> str:
        _proj_td = (
            f'<td style="padding:5px 4px">{_iv_proj_cell(r["project"])}</td>'
            if include_project else ""
        )
        _app = r["application"]
        _stage_tds = "".join(
            f'<td style="padding:5px 6px">{_iv_stage_cell(_app, _s)}</td>'
            for _s in _STAGE_ORDER
        )
        return (
            f'<tr>'
            f'{_proj_td}'
            f'<td style="padding:5px 4px">{_iv_app_cell(_app)}</td>'
            f'{_stage_tds}'
            f'</tr>'
        )

    def _iv_thead(include_project: bool) -> str:
        _p_th = f'<th {_iv_th}>Project</th>' if include_project else ""
        _stage_th = "".join(
            f'<th {_iv_th}>{_STAGE_LABEL[_s]}</th>' for _s in _STAGE_ORDER
        )
        return (
            f'<thead><tr style="border-bottom:2px solid var(--cc-border);text-align:left;background:var(--cc-surface2)">'
            f'{_p_th}'
            f'<th {_iv_th}>Application</th>'
            f'{_stage_th}'
            f'</tr></thead>'
        )

    def _iv_table_shell(rows_html: str, *, include_project: bool, max_h: str = "60vh") -> str:
        return (
            f'<div class="el-tf el-tf-shell is-inventory" style="overflow-y:auto;'
            f'max-height:{max_h};border:1px solid var(--cc-border);border-radius:10px">'
            f'<table style="width:100%;border-collapse:collapse;font-family:inherit">'
            f'{_iv_thead(include_project)}'
            f'<tbody>{rows_html}</tbody>'
            f'</table></div>'
        )

    # ── Aggregate posture strip ─────────────────────────────────────────────
    # Sum V + C severities across every currently-filtered app using each
    # app's PRD-live version as the basis (same rule as the sort). Rendered
    # as a subtle ribbon above the table so users see the combined posture
    # of their selection change as pills and search narrow the list.
    _agg_v = {"crit": 0, "high": 0, "med": 0, "low": 0}
    _agg_c = {"crit": 0, "high": 0, "med": 0, "low": 0}
    _agg_covered = 0
    _agg_na = 0
    for _r in _inv_rows:
        _a = _r.get("application") or ""
        _prd = _iv_prd_map.get(_a) or {}
        _pv = _prd.get("version") or ""
        _sc = _iv_prisma_map.get((_a, _pv)) if _pv else None
        if not _sc:
            _agg_na += 1
            continue
        _agg_covered += 1
        _agg_v["crit"] += int(_sc.get("Vcritical") or 0)
        _agg_v["high"] += int(_sc.get("Vhigh")     or 0)
        _agg_v["med"]  += int(_sc.get("Vmedium")   or 0)
        _agg_v["low"]  += int(_sc.get("Vlow")      or 0)
        _agg_c["crit"] += int(_sc.get("Ccritical") or 0)
        _agg_c["high"] += int(_sc.get("Chigh")     or 0)
        _agg_c["med"]  += int(_sc.get("Cmedium")   or 0)
        _agg_c["low"]  += int(_sc.get("Clow")      or 0)

    _agg_total_rows = len(_inv_rows)
    # Worst tier across V + C drives the strip's left-border + glyph colour.
    if _agg_v["crit"] or _agg_c["crit"]:
        _agg_worst = "crit"
        _agg_glyph = "⚠"
    elif _agg_v["high"] or _agg_c["high"]:
        _agg_worst = "high"
        _agg_glyph = "⚠"
    elif _agg_v["med"] or _agg_c["med"]:
        _agg_worst = "med"
        _agg_glyph = "◉"
    elif _agg_v["low"] or _agg_c["low"]:
        _agg_worst = "low"
        _agg_glyph = "◉"
    elif _agg_covered:
        _agg_worst = "clean"
        _agg_glyph = "✓"
    else:
        _agg_worst = "na"
        _agg_glyph = "·"

    def _ps_tier_html(kind_label: str, bucket: dict) -> str:
        _parts = []
        for _t, _lbl in (("crit", "C"), ("high", "H"), ("med", "M"), ("low", "L")):
            _n = bucket[_t]
            _zero = " is-zero" if _n == 0 else ""
            _parts.append(
                f'<span class="iv-ps-tier is-{_t}{_zero}" '
                f'title="{_n} {_t} {kind_label.lower()}">'
                f'{_n}<span style="opacity:.55;font-weight:600;margin-left:2px">{_lbl}</span>'
                f'</span>'
            )
        return "".join(_parts)

    _ps_coverage = (
        f"{_agg_covered}/{_agg_total_rows} scanned"
        if _agg_total_rows else "no filtered apps"
    )
    st.markdown(
        f'<div class="iv-posture-strip is-{_agg_worst}">'
        f'  <div class="iv-ps-label">'
        f'    <span class="iv-ps-glyph is-{_agg_worst}">{_agg_glyph}</span>'
        f'    Security posture · {_agg_total_rows} '
        f'{"apps" if _agg_total_rows != 1 else "app"}'
        f'  </div>'
        f'  <div class="iv-ps-group">'
        f'    <span class="iv-ps-kicker">Vulns</span>'
        f'    {_ps_tier_html("vulnerabilities", _agg_v)}'
        f'  </div>'
        f'  <div class="iv-ps-group">'
        f'    <span class="iv-ps-kicker">Compliance</span>'
        f'    {_ps_tier_html("compliance issues", _agg_c)}'
        f'  </div>'
        f'  <div class="iv-ps-coverage">{_ps_coverage}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Project-health ribbon — subtle landscape replacement ────────────────
    # One chip per project in the filtered inventory, colored by the worst
    # security tier across its apps. Clicking a chip opens the existing
    # project popover (teams + applications). This is the compact successor
    # to the old landscape treemap.
    _pr_TIER_RANK = {"crit": 5, "high": 4, "med": 3, "low": 2, "clean": 1, "na": 0}
    _pr_by_proj: dict[str, dict] = {}
    for _r in _inv_rows:
        _pk = _r.get("project") or "(no project)"
        _p_bucket = _pr_by_proj.setdefault(_pk, {"count": 0, "worst": "na", "covered": 0})
        _p_bucket["count"] += 1
        _a = _r.get("application") or ""
        _prd = _iv_prd_map.get(_a) or {}
        _pv = _prd.get("version") or ""
        _sc = _iv_prisma_map.get((_a, _pv)) if _pv else None
        if not _sc:
            continue
        _p_bucket["covered"] += 1
        if int(_sc.get("Vcritical") or 0) or int(_sc.get("Ccritical") or 0):
            _t = "crit"
        elif int(_sc.get("Vhigh") or 0) or int(_sc.get("Chigh") or 0):
            _t = "high"
        elif int(_sc.get("Vmedium") or 0) or int(_sc.get("Cmedium") or 0):
            _t = "med"
        elif int(_sc.get("Vlow") or 0) or int(_sc.get("Clow") or 0):
            _t = "low"
        else:
            _t = "clean"
        if _pr_TIER_RANK[_t] > _pr_TIER_RANK[_p_bucket["worst"]]:
            _p_bucket["worst"] = _t

    if _pr_by_proj:
        _pr_sorted = sorted(
            _pr_by_proj.items(),
            key=lambda kv: (-_pr_TIER_RANK[kv[1]["worst"]], -kv[1]["count"], kv[0]),
        )
        _pr_chips: list[str] = []
        for _proj, _b in _pr_sorted:
            _pid_pr = _iv_proj_pop_id(_proj) if _proj in _iv_proj_map else ""
            _t = _b["worst"]
            _n = _b["count"]
            _tip = (
                f"{_proj} · {_n} app{'s' if _n != 1 else ''} · "
                f"{_b['covered']} scanned · worst tier: {_t}"
            )
            if _pid_pr:
                _pr_chips.append(
                    f'<button type="button" class="iv-pr-chip is-{_t}" '
                    f'popovertarget="{_pid_pr}" title="{_tip}">'
                    f'<span class="iv-pr-dot is-{_t}"></span>{_proj}'
                    f'<span class="iv-pr-n">{_n}</span></button>'
                )
            else:
                _pr_chips.append(
                    f'<span class="iv-pr-chip is-{_t}" title="{_tip}">'
                    f'<span class="iv-pr-dot is-{_t}"></span>{_proj}'
                    f'<span class="iv-pr-n">{_n}</span></span>'
                )
        st.markdown(
            '<div class="iv-proj-ribbon">'
            f'<span class="iv-pr-lbl">{len(_pr_by_proj)} project'
            f'{"s" if len(_pr_by_proj) != 1 else ""}</span>'
            + "".join(_pr_chips) +
            '</div>',
            unsafe_allow_html=True,
        )

    # ── Build table(s) ──────────────────────────────────────────────────────
    if iv_per_project:
        _iv_groups: dict[str, list[dict]] = {}
        for r in _inv_rows:
            _gk = r.get("project") or "(no project)"
            _iv_groups.setdefault(_gk, []).append(r)
        _iv_sections: list[str] = []
        for _proj, _apps in _iv_groups.items():
            _rows = "".join(_iv_row_html(r, include_project=False) for r in _apps)
            _proj_pid = _iv_proj_pop_id(_proj) if _proj in _iv_proj_map else ""
            _proj_heading = (
                f'<button type="button" class="el-proj-trigger" '
                f'popovertarget="{_proj_pid}" '
                f'title="Click for teams & applications">{_proj}</button>'
                if _proj_pid else
                f'<span style="font-weight:700;color:var(--cc-text);font-size:0.92rem">{_proj}</span>'
            )
            _iv_sections.append(
                f'<section class="el-proj-section">'
                f'  <header class="el-proj-section-head">'
                f'    <span class="el-proj-section-kicker">Project</span>'
                f'    <span class="el-proj-section-title">{_proj_heading}</span>'
                f'    <span class="el-proj-section-count">{len(_apps)} app{"s" if len(_apps) != 1 else ""}</span>'
                f'  </header>'
                f'  {_iv_table_shell(_rows, include_project=False, max_h="38vh")}'
                f'</section>'
            )
        _iv_main = '<div class="el-proj-stack">' + "".join(_iv_sections) + '</div>'
    else:
        _rows = "".join(_iv_row_html(r, include_project=True) for r in _inv_rows)
        _iv_main = _iv_table_shell(_rows, include_project=True, max_h="60vh")

    # ── Build popovers — app detail + project detail ────────────────────────
    # Popover HTML is stable across widget interactions (search / sort / pill
    # toggles don't change the underlying ES data), so we memoize the full
    # concatenated HTML in session_state keyed on scope + TTL bucket. The
    # bucket expires naturally every CACHE_TTL seconds, aligned with the ES
    # fetch caches, so stale data never lingers past a single refresh cycle.
    _iv_popovers: list[str] = []
    _IV_POP_SS = "_iv_pop_html_cache_v1"
    _iv_pop_store: dict = st.session_state.setdefault(_IV_POP_SS, {})
    _iv_pop_bucket = int(datetime.now(timezone.utc).timestamp() // CACHE_TTL)
    _iv_pop_cache_key = (_iv_scope_key, _iv_pop_bucket)
    _iv_cached_pop_html = _iv_pop_store.get(_iv_pop_cache_key)
    _build_popovers_flag = _iv_cached_pop_html is None

    # Team label helper (reuse same logic as event log)
    _TEAM_LABELS = {
        "dev_team": "Dev team", "qc_team": "QC team",
        "uat_team": "UAT team", "prd_team": "PRD team",
    }

    def _iv_team_label(field: str) -> str:
        if field in _TEAM_LABELS:
            return _TEAM_LABELS[field]
        _base = field[:-5] if field.endswith("_team") else field
        return _base.replace("_", " ").strip().upper() + " team"

    # App popovers (built for FULL scope so cached HTML works across filter
    # changes — hidden popovers are cheap in the DOM and this lets the
    # popover cache key on scope alone).
    for r in (_inv_rows_all if _build_popovers_flag else []):
        _app = r["application"]
        _pid = _iv_app_pop_id(_app)
        _prd = _iv_prd_map.get(_app)
        _prd_ver = (_prd or {}).get("version") or ""
        _live = (_prd or {}).get("live")

        # PRD banner
        if _prd:
            if _live:
                _banner = (
                    f'<div class="ap-live is-live">'
                    f'  <span class="dot"></span>'
                    f'  <span>Live in PRD · '
                    f'<span class="ap-chip">{_prd_ver}</span></span>'
                    f'</div>'
                )
            else:
                _prd_st = (_prd or {}).get("status", "") or ""
                _banner = (
                    f'<div class="ap-live is-offline">'
                    f'  <span class="dot"></span>'
                    f'  <span>Last PRD deploy failed · {_prd_st or "FAILED"}</span>'
                    f'</div>'
                )
        else:
            _banner = (
                f'<div class="ap-live is-offline">'
                f'  <span class="dot"></span>'
                f'  <span>Not deployed to PRD</span>'
                f'</div>'
            )

        # Prismacloud section
        _scan = _iv_prisma_map.get((_app, _prd_ver)) if _prd_ver else None
        if _scan:
            _SEV_KEYS_IV = [("critical", "Critical"), ("high", "High"),
                            ("medium", "Medium"), ("low", "Low")]
            def _mini_tiles(prefix: str, scan: dict) -> str:
                tiles: list[str] = []
                for _lvl, _lbl in _SEV_KEYS_IV:
                    _fld = f"{prefix}{_lvl}"
                    _n = int(scan.get(_fld, 0) or 0)
                    _nz = "nonzero" if _n > 0 else "zero"
                    tiles.append(
                        f'<div class="ap-sev-tile {_lvl} {_nz}">'
                        f'  <div class="sev-num">{_n}</div>'
                        f'  <div class="sev-label">{_lbl}</div>'
                        f'</div>'
                    )
                return "".join(tiles)
            _v_tiles = _mini_tiles("V", _scan)
            _c_tiles = _mini_tiles("C", _scan)
            _prisma_html = (
                f'    <div class="ap-section">Prismacloud scan · {_prd_ver}</div>'
                f'    <div class="ap-sev-subhead"><span>Vulnerabilities</span></div>'
                f'    <div class="ap-sev">{_v_tiles}</div>'
                f'    <div class="ap-sev-subhead"><span>Compliance</span></div>'
                f'    <div class="ap-sev">{_c_tiles}</div>'
            )
        else:
            _prisma_html = (
                f'    <div class="ap-section">Prismacloud scan</div>'
                f'    <div class="ap-sev-empty">No scan on record.</div>'
            )

        # Team rows intentionally omitted — ownership is surfaced by the
        # project popover, which the project chip in the Identity section
        # links into. Duplicating it here just clutters the app view.

        _iv_popovers.append(
            f'<div id="{_pid}" popover="auto" class="el-app-pop">'
            f'  <div class="ap-head">'
            f'    <div class="ap-icon">◆</div>'
            f'    <div class="ap-title-wrap">'
            f'      <div class="ap-kicker">Application</div>'
            f'      <div class="ap-title">{_app}</div>'
            f'    </div>'
            f'    <button class="ap-close" popovertarget="{_pid}" popovertargetaction="hide" aria-label="Close">×</button>'
            f'  </div>'
            f'  <div class="ap-body">'
            f'    {_banner}'
            f'    <div class="ap-section">Identity</div>'
            f'    <span class="ap-k">Project</span>{_iv_v(r.get("project", ""))}'
            f'    <span class="ap-k">Company</span>{_iv_v(r.get("company", ""))}'
            f'    <span class="ap-k">Type</span>{_iv_app_type_pill(r.get("app_type", ""))}'
            f'    <div class="ap-section">Build</div>'
            f'    <span class="ap-k">Technology</span>{_iv_chip(r.get("build_technology", ""))}'
            f'    <span class="ap-k">Image name</span>{_iv_v(r.get("build_image_name", ""))}'
            f'    <span class="ap-k">Image tag</span>{_iv_v(r.get("build_image_tag", ""))}'
            f'    <div class="ap-section">Deploy</div>'
            f'    <span class="ap-k">Technology</span>{_iv_chip(r.get("deploy_technology", ""))}'
            f'    <span class="ap-k">Platform</span>{_iv_chip(r.get("deploy_platform", ""))}'
            f'    <span class="ap-k">Image name</span>{_iv_v(r.get("deploy_image_name", ""))}'
            f'    <span class="ap-k">Image tag</span>{_iv_v(r.get("deploy_image_tag", ""))}'
            f'    {_prisma_html}'
            f'  </div>'
            f'  <div class="ap-foot">Source: ef-devops-inventory · ef-cicd-deployments · ef-cicd-prismacloud</div>'
            f'</div>'
        )

    # ── Stage version popovers ──────────────────────────────────────────────
    # One popover per (app, stage, version) triple. Each shows:
    #   · live-in-prd banner (tailored to this version)
    #   · prismacloud scan for this version with absolute V/C tiles
    #   · delta vs current prd version (skipped when this IS the prd version)
    #   · delta vs previous-stage version (skipped when no prev stage or same)
    _IV_SEV_KEYS = [
        ("critical", "Critical"), ("high", "High"),
        ("medium",   "Medium"),   ("low",  "Low"),
    ]

    def _iv_sev_tile(level: str, label: str, count: int,
                     delta: int | None, baseline_label: str) -> str:
        _nz = "nonzero" if count > 0 else "zero"
        if delta is None:
            _delta_html = ""
        elif delta > 0:
            _delta_html = f'<div class="sev-delta up">▲ +{delta} vs {baseline_label}</div>'
        elif delta < 0:
            _delta_html = f'<div class="sev-delta down">▼ {delta} vs {baseline_label}</div>'
        else:
            _delta_html = f'<div class="sev-delta eq">= vs {baseline_label}</div>'
        return (
            f'<div class="ap-sev-tile {level} {_nz}">'
            f'  <div class="sev-num">{count}</div>'
            f'  <div class="sev-label">{label}</div>'
            f'  {_delta_html}'
            f'</div>'
        )

    def _iv_sev_strip(prefix: str, scan: dict,
                      baseline: dict | None, baseline_label: str) -> tuple[str, int]:
        tiles: list[str] = []
        _total = 0
        for _lvl, _lbl in _IV_SEV_KEYS:
            _fld = f"{prefix}{_lvl}"
            _n = int(scan.get(_fld, 0) or 0)
            _total += _n
            _delta: int | None = None
            if baseline is not None:
                _delta = _n - int(baseline.get(_fld, 0) or 0)
            tiles.append(_iv_sev_tile(_lvl, _lbl, _n, _delta, baseline_label))
        return "".join(tiles), _total

    for _app, _stages in (_iv_stages_map.items() if _build_popovers_flag else []):
        # Only build popovers for apps that are in the rendered rows.
        if _app not in _iv_apps:
            continue
        _prd_data = _iv_prd_map.get(_app) or {}
        _prd_ver  = _prd_data.get("version") or ""
        _prd_scan = _iv_prisma_map.get((_app, _prd_ver)) if _prd_ver else None

        for _stage, _data in _stages.items():
            _ver = (_data or {}).get("version") or ""
            if not _ver:
                continue
            _vid = _iv_ver_pop_id(_app, _stage, _ver)
            _stage_lbl = _STAGE_LABEL.get(_stage, _stage)
            _when_disp = fmt_dt(_data.get("when"), "%Y-%m-%d %H:%M") or ""
            _status    = _data.get("status", "") or ""
            _is_prd_ver = bool(_prd_ver and _prd_ver == _ver)

            # ── Live banner, tailored to this stage's version ───────────────
            if _prd_data.get("live"):
                if _is_prd_ver:
                    _banner = (
                        f'<div class="ap-live is-live">'
                        f'  <span class="dot"></span>'
                        f'  <span>This version is live in prd · '
                        f'<span class="ap-chip">{_ver}</span></span>'
                        f'</div>'
                    )
                else:
                    _banner = (
                        f'<div class="ap-live is-live">'
                        f'  <span class="dot"></span>'
                        f'  <span>App live in prd · running '
                        f'<span class="ap-chip">{_prd_ver}</span> (not this version)</span>'
                        f'</div>'
                    )
            elif _prd_data:
                _last_st = _prd_data.get("status", "") or "FAILED"
                _banner = (
                    f'<div class="ap-live is-offline">'
                    f'  <span class="dot"></span>'
                    f'  <span>App not live · last prd attempt {_last_st}</span>'
                    f'</div>'
                )
            else:
                _banner = (
                    f'<div class="ap-live is-offline">'
                    f'  <span class="dot"></span>'
                    f'  <span>App has never deployed to prd</span>'
                    f'</div>'
                )

            # ── Stage-detail block (version + date + status) ────────────────
            _stage_block = (
                f'    <div class="ap-section">{_stage_lbl}</div>'
                f'    <span class="ap-k">Version</span>{_iv_chip(_ver)}'
                f'    <span class="ap-k">Status</span>{_iv_v(_status)}'
                f'    <span class="ap-k">When ({DISPLAY_TZ_LABEL})</span>{_iv_v(_when_disp)}'
            )

            # ── Version provenance — always show build date for this version,
            # plus release date & RLM when this version has been released.
            _vmeta = _iv_vermeta_map.get((_app, _ver)) or {}
            _build_when_disp = fmt_dt(_vmeta.get("build_when"), "%Y-%m-%d %H:%M") or ""
            _rel_when_disp   = fmt_dt(_vmeta.get("release_when"), "%Y-%m-%d %H:%M") or ""
            _rlm_id   = _vmeta.get("rlm", "")
            _prov_rows = (
                f'    <div class="ap-section">Version provenance</div>'
                f'    <span class="ap-k">Built ({DISPLAY_TZ_LABEL})</span>{_iv_v(_build_when_disp)}'
            )
            if _rel_when_disp or _rlm_id:
                _prov_rows += (
                    f'    <span class="ap-k">Released ({DISPLAY_TZ_LABEL})</span>'
                    f'{_iv_v(_rel_when_disp)}'
                )
                if _rlm_id:
                    _prov_rows += (
                        f'    <span class="ap-k">RLM</span>{_iv_chip(_rlm_id)}'
                    )
            _stage_block += _prov_rows

            # ── Previous-stage context (for Δ baseline) ─────────────────────
            _prev_stage = _STAGE_PREV.get(_stage)
            _prev_ver: str = ""
            _prev_scan: dict | None = None
            if _prev_stage:
                _prev_data = (_stages.get(_prev_stage) or {})
                _prev_ver = _prev_data.get("version") or ""
                if _prev_ver and _prev_ver != _ver:
                    _prev_scan = _iv_prisma_map.get((_app, _prev_ver))

            # ── Prismacloud for this version ────────────────────────────────
            _this_scan = _iv_prisma_map.get((_app, _ver))
            if _this_scan:
                _v_tiles, _v_total = _iv_sev_strip("V", _this_scan, None, "")
                _c_tiles, _c_total = _iv_sev_strip("C", _this_scan, None, "")
                _scan_when = fmt_dt(_this_scan.get("when"), "%Y-%m-%d %H:%M") or ""
                _scan_stat = _this_scan.get("status", "") or ""
                _prisma_block = (
                    f'    <div class="ap-section">Prismacloud scan</div>'
                    f'    <span class="ap-k">Scan status</span>{_iv_v(_scan_stat)}'
                    f'    <span class="ap-k">Scanned ({DISPLAY_TZ_LABEL})</span>{_iv_v(_scan_when)}'
                    f'    <div class="ap-sev-subhead"><span>Vulnerabilities · this version</span>'
                    f'      <span class="sev-sum">{_v_total} total</span></div>'
                    f'    <div class="ap-sev">{_v_tiles}</div>'
                    f'    <div class="ap-sev-subhead"><span>Compliance · this version</span>'
                    f'      <span class="sev-sum">{_c_total} total</span></div>'
                    f'    <div class="ap-sev">{_c_tiles}</div>'
                )

                # Δ vs current prd
                if _prd_scan is not None and _prd_ver and not _is_prd_ver:
                    _vd, _ = _iv_sev_strip("V", _this_scan, _prd_scan, "prd")
                    _cd, _ = _iv_sev_strip("C", _this_scan, _prd_scan, "prd")
                    _prisma_block += (
                        f'    <div class="ap-compare-head">'
                        f'      <span>Δ vs current prd</span>'
                        f'      <span class="cmp-pill">{_prd_ver}</span>'
                        f'    </div>'
                        f'    <div class="ap-sev-subhead"><span>Vulnerabilities</span></div>'
                        f'    <div class="ap-sev">{_vd}</div>'
                        f'    <div class="ap-sev-subhead"><span>Compliance</span></div>'
                        f'    <div class="ap-sev">{_cd}</div>'
                    )

                # Δ vs previous stage
                if _prev_scan is not None and _prev_ver and _prev_ver != _ver:
                    _prev_lbl = _STAGE_LABEL.get(_prev_stage, _prev_stage).lower()
                    _vd2, _ = _iv_sev_strip("V", _this_scan, _prev_scan, _prev_stage)
                    _cd2, _ = _iv_sev_strip("C", _this_scan, _prev_scan, _prev_stage)
                    _prisma_block += (
                        f'    <div class="ap-compare-head">'
                        f'      <span>Δ vs {_prev_lbl}</span>'
                        f'      <span class="cmp-pill">{_prev_ver}</span>'
                        f'    </div>'
                        f'    <div class="ap-sev-subhead"><span>Vulnerabilities</span></div>'
                        f'    <div class="ap-sev">{_vd2}</div>'
                        f'    <div class="ap-sev-subhead"><span>Compliance</span></div>'
                        f'    <div class="ap-sev">{_cd2}</div>'
                    )
            else:
                _prisma_block = (
                    f'    <div class="ap-section">Prismacloud scan</div>'
                    f'    <div class="ap-sev-empty">No prismacloud scan on record for this version.</div>'
                )

            _iv_popovers.append(
                f'<div id="{_vid}" popover="auto" class="el-app-pop is-version">'
                f'  <div class="ap-head">'
                f'    <div class="ap-icon">▲</div>'
                f'    <div class="ap-title-wrap">'
                f'      <div class="ap-kicker">{_stage_lbl} · {_ver}</div>'
                f'      <div class="ap-title">{_app}</div>'
                f'    </div>'
                f'    <button class="ap-close" popovertarget="{_vid}" popovertargetaction="hide" aria-label="Close">×</button>'
                f'  </div>'
                f'  <div class="ap-body">'
                f'    {_banner}'
                f'    {_stage_block}'
                f'    {_prisma_block}'
                f'  </div>'
                f'  <div class="ap-foot">Sources: ef-cicd-builds · ef-cicd-releases · ef-cicd-deployments · ef-cicd-prismacloud</div>'
                f'</div>'
            )

    # Project popovers
    for _proj in (_iv_pop_projects if _build_popovers_flag else []):
        _pdata = _iv_proj_map.get(_proj)
        if not _pdata:
            continue
        _pid_p = _iv_proj_pop_id(_proj)
        _teams_p = _pdata.get("teams", {}) or {}
        _apps_p  = _pdata.get("apps", []) or []
        _co_p    = _pdata.get("company", "") or ""
        _ordered_p = [k for k in ("dev_team", "qc_team", "uat_team", "prd_team") if k in _teams_p]
        _extras_p  = sorted(k for k in _teams_p.keys() if k not in _ordered_p)
        _team_rows_p: list[str] = []
        for _f in _ordered_p + _extras_p:
            _vals = _teams_p.get(_f) or []
            if not _vals:
                continue
            _chips_p = "".join(f'<span class="ap-chip">{_tv}</span>' for _tv in _vals)
            _team_rows_p.append(
                f'<span class="ap-k">{_iv_team_label(_f)}</span>'
                f'<span class="ap-v" style="display:flex;flex-wrap:wrap;gap:4px">{_chips_p}</span>'
            )
        if not _team_rows_p:
            _team_rows_p.append(
                '<span class="ap-k">Teams</span>'
                '<span class="ap-v empty">none recorded</span>'
            )
        _app_chips_p = []
        for _a in _apps_p:
            _app_chips_p.append(
                f'<button type="button" class="ap-app-chip" '
                f'popovertarget="{_iv_app_pop_id(_a)}" '
                f'title="Open application details">{_a}</button>'
            )
        _apps_block_p = "".join(_app_chips_p)
        _company_block_p = (
            f'    <div class="ap-section">Company</div>'
            f'    <span class="ap-k">Name</span>{_iv_chip(_co_p) if _co_p else _iv_v("")}'
        )
        _iv_popovers.append(
            f'<div id="{_pid_p}" popover="auto" class="el-app-pop is-project">'
            f'  <div class="ap-head">'
            f'    <div class="ap-icon">◇</div>'
            f'    <div class="ap-title-wrap">'
            f'      <div class="ap-kicker">Project</div>'
            f'      <div class="ap-title">{_proj}</div>'
            f'    </div>'
            f'    <button class="ap-close" popovertarget="{_pid_p}" popovertargetaction="hide" aria-label="Close">×</button>'
            f'  </div>'
            f'  <div class="ap-body">'
            f'    {_company_block_p}'
            f'    <div class="ap-section">Teams</div>'
            + "".join(_team_rows_p) +
            f'    <div class="ap-section">Applications <span style="text-transform:none;font-weight:600;color:var(--cc-text-mute);letter-spacing:0;margin-left:4px">· {len(_apps_p)}</span></div>'
            f'    <div class="ap-applist">{_apps_block_p}</div>'
            f'  </div>'
            f'  <div class="ap-foot">Source: ef-devops-inventory · click an app for full details</div>'
            f'</div>'
        )

    # ── Finalize popover cache ──────────────────────────────────────────────
    if _build_popovers_flag:
        _iv_popovers_html = "".join(_iv_popovers)
        # Retain only the most recent scope+bucket to bound session memory.
        _iv_pop_store.clear()
        _iv_pop_store[_iv_pop_cache_key] = _iv_popovers_html
    else:
        _iv_popovers_html = _iv_cached_pop_html

    # ── Final render ────────────────────────────────────────────────────────
    _iv_visible_badge = f"showing {len(_inv_rows)}"
    st.markdown(
        f'<p class="el-tf-caption">'
        f'  <span class="el-tf-caption-count">{_iv_visible_badge}</span>'
        f'  <span class="el-tf-caption-sep">·</span>'
        f'  <span>click any <b>application</b> or <b>project</b> chip to open its detail popover</span>'
        f'</p>'
        + _iv_main
        + _iv_popovers_html,
        unsafe_allow_html=True,
    )

    # ── Nested event log ────────────────────────────────────────────────────
    # Stash the filtered app list so the event-log fragment inherits every
    # filter applied above. An empty list is meaningful ("scope is zero"); we
    # only stash when at least one inventory filter is driving the selection.
    if _show_el:
        _el_scope_apps = sorted({
            r.get("application") or "" for r in _inv_rows if r.get("application")
        })
        st.session_state["_el_inv_scope_apps"] = _el_scope_apps
        st.markdown(
            '<div class="cc-panel-head cc-panel-head--numbered cc-panel-head--live" style="margin-top:22px">'
            '<h2 data-section-num="02">Event log</h2>'
            f'<span class="cc-panel-tag">Live · 60s · scoped to {len(_el_scope_apps)} '
            f'{"app" if len(_el_scope_apps) == 1 else "apps"} from the inventory above</span>'
            '</div>'
            '<div class="cc-panel-sub">Builds · deployments · releases · requests · commits — '
            'newest first · click any row for details · scope mirrors the pipelines table above</div>',
            unsafe_allow_html=True,
        )
        _render_event_log()


# ── Late render into the top-of-page slot ─────────────────────────────────
# The inventory is the primary surface — its fragment renders the stat tiles,
# the pipelines table, and (at the bottom) the event log, which inherits every
# filter selected in the inventory header. Fragment defs live far below, so
# the slot pattern keeps reading order top-down without forward-declaring
# ~2000 lines of helpers.
if _show_inv and _inventory_slot is not None:
    with _inventory_slot.container():
        # Header already rendered inside the sticky filter rail at the top of
        # the page — the rail IS the section header here. A thin sub-caption
        # keeps the context copy without doubling up on the H2.
        st.markdown(
            '<div class="cc-panel-sub" style="margin:-4px 0 6px 0">'
            'One row per registered pipeline · PRD liveness · security posture · '
            'click any chip for project / app / version detail · '
            'event log at the bottom inherits every filter above'
            '</div>',
            unsafe_allow_html=True,
        )
        _render_inventory_view()
elif _show_el:
    # Fallback for roles that somehow have event-log-only visibility (none today,
    # but the mapping allows it). Render the event log standalone with no
    # inventory-driven scope restriction.
    st.session_state.pop("_el_inv_scope_apps", None)
    st.markdown(
        '<div class="cc-panel-head cc-panel-head--numbered cc-panel-head--live">'
        '<h2 data-section-num="02">Event log</h2>'
        f'<span class="cc-panel-tag">Live · auto-refresh 60s · {_effective_role}</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    _render_event_log()


# =============================================================================
# ADMIN INSIGHTS DRAWER — consolidates alerts, lifecycle, pipeline, workflow
# =============================================================================

# Non-admin users only see the event log + inventory — halt here.
if not _is_admin:
    st.stop()

# Single gate for all admin-only analytics. The drawer stays CLOSED by default
# so the default admin page load renders only event log + inventory — the
# analytics sections' ES queries and heavy plotly charts run only when the
# user explicitly opts in by flipping the toggle. Performance critical: the
# stop below skips ~1500 lines of queries + rendering when drawer is closed.
st.markdown(
    '<div style="border-top:1px solid var(--cc-border);margin:18px 0 6px 0"></div>',
    unsafe_allow_html=True,
)
_drawer_open = st.toggle(
    "Admin insights — alerts · lifecycle · pipeline · workflow",
    value=False,
    key="admin_drawer_open_v1",
    help="Heavy analytics are hidden by default to keep the page fast. "
         "Toggle on to run the cross-index queries and render the drill-downs.",
)
if not _drawer_open:
    st.stop()

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


# Landscape treemap + project deep-dive popover removed in the event-log-first
# redesign — the inventory view already renders one row per application with
# project grouping and per-project health chips, so this section was pure
# duplication. The deep-dive flow is covered by the project popover available
# from any project chip in the inventory / event log.


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

# Build application → parent-project mapping from the inventory index.
# Used by lifecycle, workflow, and pipeline sections below.
_app_to_parent: dict[str, str] = {}
try:
    _ap_rows = _fetch_full_inventory(
        json.dumps(scope_filters_inv(), sort_keys=True, default=str)
    )
    for _ap_row in _ap_rows:
        _ap_app = _ap_row.get("application") or ""
        _ap_proj = _ap_row.get("project") or ""
        if _ap_app and _ap_app not in _app_to_parent:
            _app_to_parent[_ap_app] = _ap_proj or "—"
except Exception:
    _app_to_parent = {}

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
        df_tl["time"] = (
            pd.to_datetime(df_tl["time"], utc=True)
            .dt.tz_convert(DISPLAY_TZ)
            .dt.tz_localize(None)
        )
        fig = px.bar(
            df_tl, x="time", y="count", color="status",
            color_discrete_map=STATUS_COLORS,
            title=f"Builds over time ({interval} buckets, {DISPLAY_TZ_LABEL})",
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
        df_tl["time"] = (
            pd.to_datetime(df_tl["time"], utc=True)
            .dt.tz_convert(DISPLAY_TZ)
            .dt.tz_localize(None)
        )
        fig = px.area(
            df_tl, x="time", y="count", color="environment",
            title=f"Deployments over time ({interval} buckets, {DISPLAY_TZ_LABEL})",
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
