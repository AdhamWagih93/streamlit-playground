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

import html
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
    "prismacloud": "ef-cicd-prismacloud",   # container image scan
    "invicti":     "ef-cicd-invicti",       # DAST web scan
    "zap":         "ef-cicd-zap",           # DAST OWASP-ZAP scan
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
    max-height: min(86vh, 880px);
    overflow: auto;
    border-radius: 14px;
    box-shadow:
        0 1px 2px rgba(26, 29, 46, .05),
        0 20px 50px -10px rgba(26, 29, 46, .25),
        0 0 0 1px rgba(79, 70, 229, .08);
    color: var(--cc-text);
    font-family: var(--cc-sans);
    /* subtle fade-in */
    animation: el-pop-in .18s ease-out;
}
/* Version + application popovers both carry the 3-up security scan grid,
   so they need to be wider than the project-detail popover. Falls back to
   viewport width on narrow screens. The is-project variant inherits the
   default 420px (no scan grid, mostly text rows). */
.el-app-pop.is-version,
.el-app-pop.is-app {
    width: min(820px, 96vw);
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
    flex-wrap: nowrap;          /* single-line strip — no vertical bloat */
    align-items: center;
    gap: 5px;
    margin: 0 0 6px 0;
    padding: 4px 0 6px 0;
    overflow-x: auto;
    overflow-y: hidden;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: thin;
    scrollbar-color: color-mix(in srgb, var(--cc-border-hi) 80%, transparent) transparent;
    /* Soft fade on the right edge hints at horizontal scroll without
       reserving extra space. */
    mask-image: linear-gradient(90deg, black 0%, black 92%, transparent 100%);
    -webkit-mask-image: linear-gradient(90deg, black 0%, black 92%, transparent 100%);
}
.iv-proj-ribbon::-webkit-scrollbar { height: 4px; }
.iv-proj-ribbon::-webkit-scrollbar-thumb {
    background: color-mix(in srgb, var(--cc-border-hi) 80%, transparent);
    border-radius: 2px;
}
.iv-proj-ribbon .iv-pr-lbl {
    font-size: 0.58rem;
    color: var(--cc-text-mute);
    font-weight: 700;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    margin-right: 4px;
    white-space: nowrap;
    flex: 0 0 auto;
}
.iv-proj-ribbon .iv-pr-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.66rem;
    font-weight: 600;
    background: var(--cc-surface2);
    color: var(--cc-text-dim);
    border: 1px solid var(--cc-border);
    cursor: pointer;
    transition: transform .12s ease, box-shadow .12s ease;
    white-space: nowrap;
    flex: 0 0 auto;
    line-height: 1.3;
}
.iv-proj-ribbon .iv-pr-chip:hover {
    transform: translateY(-1px);
    box-shadow: 0 2px 6px rgba(15, 23, 42, 0.08);
}
.iv-proj-ribbon .iv-pr-chip .iv-pr-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    display: inline-block;
    flex: none;
}
.iv-proj-ribbon .iv-pr-chip .iv-pr-n {
    font-size: 0.58rem;
    font-weight: 500;
    opacity: 0.65;
    margin-left: 1px;
    font-variant-numeric: tabular-nums;
}
.iv-proj-ribbon .iv-pr-more {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    font-size: 0.58rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--cc-text-mute);
    background: transparent;
    border: 1px dashed var(--cc-border-hi);
    border-radius: 12px;
    flex: 0 0 auto;
    white-space: nowrap;
    line-height: 1.3;
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
/* Jira tile — type chip strip below the priority distribution bar */
.iv-jira-types {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 4px;
    margin-top: 8px;
    padding-top: 7px;
    border-top: 1px dashed
        color-mix(in srgb, var(--cc-border) 80%, transparent);
}
.iv-jira-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 8px 2px 6px;
    font-family: var(--cc-body);
    font-size: .68rem;
    line-height: 1.4;
    color: var(--cc-text);
    background: color-mix(in srgb, var(--cc-blue) 6%, transparent);
    border: 1px solid color-mix(in srgb, var(--cc-blue) 18%, var(--cc-border));
    border-radius: 999px;
    letter-spacing: .005em;
}
.iv-jira-chip-g {
    color: var(--cc-blue);
    font-size: .76rem;
    line-height: 1;
    width: 12px;
    text-align: center;
}
.iv-jira-chip b {
    font-family: var(--cc-data);
    font-weight: 700;
    color: var(--cc-ink);
    margin-left: 2px;
    font-variant-numeric: tabular-nums;
    background: color-mix(in srgb, var(--cc-blue) 14%, transparent);
    padding: 0 5px;
    border-radius: 4px;
    font-size: .62rem;
    letter-spacing: .02em;
}
/* Jira tile accent — slightly cooler edge accent than the build tile */
.iv-pulse-tile--jira::before {
    background: linear-gradient(180deg, #2684ff 0%, #7048e8 100%) !important;
}

/* Twin stat block — builds + deploys side by side inside the build tile */
.iv-pulse-twin {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
    margin: 6px 0 8px 0;
    padding: 8px 0 0 0;
    border-top: 1px dashed
        color-mix(in srgb, var(--cc-border) 80%, transparent);
}
.iv-pulse-twin-stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
}
.iv-pulse-twin-stat + .iv-pulse-twin-stat {
    padding-left: 10px;
    border-left: 1px dashed
        color-mix(in srgb, var(--cc-border) 80%, transparent);
}
.iv-pulse-twin-rate {
    font-family: var(--cc-display);
    font-variation-settings: "opsz" 144;
    font-size: 1.65rem;
    font-weight: 600;
    line-height: 1.0;
    color: var(--cc-ink);
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums lining-nums;
}
.iv-pulse-twin-rate .iv-pulse-unit {
    font-family: var(--cc-body);
    font-size: .68rem;
    font-weight: 500;
    color: var(--cc-text-mute);
    margin-left: 2px;
    letter-spacing: 0;
}
.iv-pulse-twin-lbl {
    font-family: var(--cc-data);
    font-size: .54rem;
    letter-spacing: .14em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--cc-text-mute);
    margin-top: 1px;
}
.iv-pulse-twin-meta {
    font-family: var(--cc-body);
    font-size: .64rem;
    color: var(--cc-text-dim);
    line-height: 1.3;
    font-variant-numeric: tabular-nums;
}
.iv-pulse-twin-meta b {
    color: var(--cc-ink);
    font-weight: 700;
}
.iv-pulse-twin-meta--quiet {
    color: var(--cc-text-mute);
    font-style: italic;
    font-size: .60rem;
}
.iv-pulse-ok   { color: var(--cc-green); font-weight: 600; }
.iv-pulse-fail { color: var(--cc-red);   font-weight: 600; }

.iv-jira-scope {
    display: inline-block;
    margin-left: 2px;
    padding: 1px 7px;
    border-radius: 999px;
    font-family: var(--cc-data);
    font-size: .56rem;
    letter-spacing: .08em;
    text-transform: uppercase;
    font-weight: 700;
    color: #2684ff;
    background: color-mix(in srgb, #2684ff 12%, transparent);
    border: 1px solid color-mix(in srgb, #2684ff 28%, transparent);
}

/* Security tile — per-scanner attribution chip strip below the V* bar */
.iv-sec-srcs {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 4px;
    margin-top: 8px;
    padding-top: 7px;
    border-top: 1px dashed
        color-mix(in srgb, var(--cc-border) 80%, transparent);
}
.iv-sec-src {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 9px 2px 7px;
    font-family: var(--cc-body);
    font-size: .68rem;
    line-height: 1.4;
    color: var(--cc-text);
    background: color-mix(in srgb, var(--iv-sec-src-c, var(--cc-accent)) 6%, transparent);
    border: 1px solid color-mix(in srgb, var(--iv-sec-src-c, var(--cc-accent)) 22%, var(--cc-border));
    border-radius: 999px;
    letter-spacing: .005em;
}
.iv-sec-src-g {
    color: var(--iv-sec-src-c, var(--cc-accent));
    font-size: .82rem;
    line-height: 1;
    width: 14px;
    text-align: center;
}
.iv-sec-src-n {
    font-family: var(--cc-data);
    font-size: .58rem;
    letter-spacing: .12em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--iv-sec-src-c, var(--cc-accent));
}
.iv-sec-src b {
    font-family: var(--cc-data);
    font-weight: 700;
    color: var(--cc-ink);
    margin-left: 1px;
    font-variant-numeric: tabular-nums;
    background: color-mix(in srgb, var(--iv-sec-src-c, var(--cc-accent)) 14%, transparent);
    padding: 0 6px;
    border-radius: 4px;
    font-size: .64rem;
}
.iv-sec-src-apps {
    font-size: .60rem;
    color: var(--cc-text-mute);
    letter-spacing: .02em;
}

/* Multi-source scan section inside the version popover */
.ap-scan-src {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 12px 0 6px 0;
    padding: 6px 10px;
    border-left: 3px solid var(--ap-scan-src-c, var(--cc-accent));
    background: color-mix(in srgb, var(--ap-scan-src-c, var(--cc-accent)) 5%, transparent);
    border-radius: 0 6px 6px 0;
}
.ap-scan-src-glyph {
    font-size: 1.05rem;
    color: var(--ap-scan-src-c, var(--cc-accent));
    line-height: 1;
}
.ap-scan-src-name {
    font-family: var(--cc-data);
    font-size: .66rem;
    letter-spacing: .14em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--ap-scan-src-c, var(--cc-accent));
}
.ap-scan-src-status {
    margin-left: auto;
    font-family: var(--cc-data);
    font-size: .60rem;
    letter-spacing: .08em;
    text-transform: uppercase;
    font-weight: 600;
    color: var(--cc-text-mute);
    background: color-mix(in srgb, var(--ap-scan-src-c, var(--cc-accent)) 12%, transparent);
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--ap-scan-src-c, var(--cc-accent)) 28%, transparent);
}
.ap-scan-src-when {
    font-family: var(--cc-data);
    font-size: .62rem;
    color: var(--cc-text-mute);
    font-variant-numeric: tabular-nums;
}
.ap-scan-empty-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    margin: 6px 0;
    color: var(--cc-text-mute);
    font-size: .74rem;
    background: color-mix(in srgb, var(--cc-text-mute) 4%, transparent);
    border: 1px dashed color-mix(in srgb, var(--cc-border) 80%, transparent);
    border-radius: 6px;
}

/* ── Compact 3-up security scan grid (version popover) ─────────────────── */
.el-app-pop .ap-section.ap-section--scan {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
}
.ap-section-note {
    font-family: var(--cc-data);
    font-size: .58rem;
    letter-spacing: .08em;
    text-transform: uppercase;
    font-weight: 600;
    color: var(--cc-text-mute);
    text-align: right;
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.ap-section-note .cmp-pill {
    font-family: var(--cc-data);
    font-size: .58rem;
    color: var(--cc-ink);
    background: color-mix(in srgb, var(--cc-accent) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--cc-accent) 28%, transparent);
    padding: 1px 7px;
    border-radius: 999px;
}
.ap-section-note--live {
    color: var(--cc-green);
}

.ap-scan-grid {
    grid-column: 1 / -1;
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 10px;
    margin-top: 4px;
}
@media (max-width: 720px) {
    .ap-scan-grid { grid-template-columns: 1fr; }
}

.ap-scan-card {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 10px 12px 10px 13px;
    background: color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 4%, var(--cc-surface));
    border: 1px solid color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 18%, var(--cc-border));
    border-left: 3px solid var(--ap-scan-card-c, var(--cc-accent));
    border-radius: 0 8px 8px 0;
    min-width: 0;
}
.ap-scan-card--empty {
    background: color-mix(in srgb, var(--cc-text-mute) 4%, transparent);
    border-style: dashed;
    border-color: color-mix(in srgb, var(--cc-border) 80%, transparent);
    border-left-style: dashed;
    color: var(--cc-text-mute);
}
.ap-scan-card-head {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    line-height: 1.2;
}
.ap-scan-card-glyph {
    color: var(--ap-scan-card-c, var(--cc-accent));
    font-size: 1.0rem;
    line-height: 1;
}
.ap-scan-card-name {
    font-family: var(--cc-data);
    font-size: .60rem;
    letter-spacing: .14em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--ap-scan-card-c, var(--cc-accent));
    flex: 1;
    min-width: 0;
}
.ap-scan-card-status {
    font-family: var(--cc-data);
    font-size: .54rem;
    letter-spacing: .08em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--cc-text);
    background: color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 26%, transparent);
    padding: 1px 7px;
    border-radius: 999px;
    line-height: 1.4;
}
.ap-scan-card-when {
    font-family: var(--cc-data);
    font-size: .58rem;
    font-variant-numeric: tabular-nums;
    color: var(--cc-text-mute);
    line-height: 1.2;
}
.ap-scan-card-empty {
    font-size: .70rem;
    color: var(--cc-text-mute);
    text-align: center;
    padding: 14px 0 10px 0;
    letter-spacing: .04em;
}

/* Compact DAST meta strip — environment + extra counts inline */
.ap-scan-card-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 6px;
    margin: 2px 0 0 0;
    font-family: var(--cc-data);
    font-size: .56rem;
    letter-spacing: .06em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
}
.ap-scan-card-env {
    color: var(--ap-scan-card-c, var(--cc-accent));
    font-weight: 700;
    background: color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 14%, transparent);
    padding: 1px 6px;
    border-radius: 4px;
}
.ap-scan-card-aux {
    color: var(--cc-text-mute);
}
.ap-scan-card-aux b {
    color: var(--cc-ink);
    font-weight: 700;
    margin-left: 2px;
    font-variant-numeric: tabular-nums;
}
.ap-scan-card-url {
    font-family: var(--cc-mono);
    font-size: .58rem;
    color: var(--cc-text-mute);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: 1px;
}

/* Section subhead inside a card (Vulnerabilities / Compliance) */
.ap-scan-card-section {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: 6px;
    padding-bottom: 3px;
    border-bottom: 1px dashed
        color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 22%, transparent);
    font-family: var(--cc-data);
    font-size: .56rem;
    letter-spacing: .12em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--cc-text-mute);
}
.ap-scan-card-section--c { margin-top: 8px; }
.ap-scan-card-total {
    margin-left: auto;
    font-family: var(--cc-data);
    font-size: .58rem;
    color: var(--cc-ink);
    font-variant-numeric: tabular-nums;
    background: color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 14%, transparent);
    padding: 0 7px;
    border-radius: 999px;
    border: 1px solid color-mix(in srgb, var(--ap-scan-card-c, var(--cc-accent)) 26%, transparent);
}
.ap-scan-card-delta-chip {
    margin-left: 6px;
    font-family: var(--cc-data);
    font-size: .54rem;
    letter-spacing: .10em;
    color: var(--cc-text-mute);
    background: color-mix(in srgb, var(--cc-text-mute) 6%, transparent);
    border: 1px solid color-mix(in srgb, var(--cc-border) 90%, transparent);
    padding: 0 6px;
    border-radius: 999px;
}

.ap-scan-card-rows {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-top: 3px;
}
.ap-scan-row {
    display: grid;
    grid-template-columns: 6px 1fr auto auto;
    gap: 7px;
    align-items: center;
    padding: 3px 7px 3px 6px;
    border-radius: 4px;
    background: color-mix(in srgb, var(--cc-text-mute) 3%, transparent);
}
.ap-scan-row.zero {
    opacity: .55;
}
.ap-scan-row.critical { background: color-mix(in srgb, var(--cc-red) 9%, transparent); }
.ap-scan-row.high     { background: color-mix(in srgb, var(--cc-amber) 9%, transparent); }
.ap-scan-row.medium   { background: color-mix(in srgb, var(--cc-blue) 8%, transparent); }
.ap-scan-row.low      { background: color-mix(in srgb, var(--cc-text-mute) 4%, transparent); }
.ap-scan-row.critical.zero,
.ap-scan-row.high.zero,
.ap-scan-row.medium.zero,
.ap-scan-row.low.zero { background: color-mix(in srgb, var(--cc-text-mute) 2%, transparent); }
.ap-scan-row-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: currentColor;
    opacity: .7;
}
.ap-scan-row.critical .ap-scan-row-dot { background: var(--cc-red); }
.ap-scan-row.high     .ap-scan-row-dot { background: var(--cc-amber); }
.ap-scan-row.medium   .ap-scan-row-dot { background: var(--cc-blue); }
.ap-scan-row.low      .ap-scan-row-dot { background: var(--cc-text-mute); }
.ap-scan-row-name {
    font-family: var(--cc-body);
    font-size: .68rem;
    color: var(--cc-text-dim);
    letter-spacing: .005em;
}
.ap-scan-row.nonzero .ap-scan-row-name { color: var(--cc-text); font-weight: 600; }
.ap-scan-row-num {
    font-family: var(--cc-display);
    font-size: .92rem;
    font-weight: 700;
    color: var(--cc-ink);
    font-variant-numeric: tabular-nums lining-nums;
    line-height: 1;
}
.ap-scan-row-delta {
    font-family: var(--cc-data);
    font-size: .58rem;
    letter-spacing: .03em;
    font-variant-numeric: tabular-nums;
    padding: 1px 5px;
    border-radius: 4px;
    line-height: 1.4;
    min-width: 38px;
    text-align: right;
}
.ap-scan-row-delta.up   { color: var(--cc-red);   background: color-mix(in srgb, var(--cc-red)   12%, transparent); }
.ap-scan-row-delta.down { color: var(--cc-green); background: color-mix(in srgb, var(--cc-green) 12%, transparent); }
.ap-scan-row-delta.eq   { color: var(--cc-text-mute); background: color-mix(in srgb, var(--cc-text-mute) 6%, transparent); }

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
   RAIL — minimal scope line shown next to the role badge.
   The rail used to host search + a settings popover; both moved into the
   Filter Console below. This line is purely informational so the rail
   doesn't feel like a dead bar.
   ========================================================================== */
.cc-rail-scope-line {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px 8px 16px;
    margin: 0 0 0 4px;
    border: 1px dashed
        color-mix(in srgb, var(--cc-border-hi) 90%, var(--cc-accent) 10%);
    border-radius: 999px;
    background:
        linear-gradient(90deg,
            color-mix(in srgb, var(--cc-accent) 4%, transparent) 0%,
            transparent 60%);
    font-family: var(--cc-body);
    font-size: 0.74rem;
    color: var(--cc-text-mute);
    letter-spacing: 0.01em;
    line-height: 1.3;
    transition: border-color .22s ease, background .22s ease;
}
.cc-rail-scope-line:hover {
    border-color: color-mix(in srgb, var(--cc-accent) 32%, var(--cc-border-hi));
    background:
        linear-gradient(90deg,
            color-mix(in srgb, var(--cc-accent) 8%, transparent) 0%,
            transparent 70%);
}
.cc-rail-scope-line .cc-rail-scope-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--cc-accent);
    box-shadow:
        0 0 0 2px color-mix(in srgb, var(--cc-accent) 22%, transparent),
        0 0 10px 1px color-mix(in srgb, var(--cc-accent) 50%, transparent);
    flex: 0 0 auto;
    animation: cc-rail-scope-pulse 3.4s ease-in-out infinite;
}
@keyframes cc-rail-scope-pulse {
    0%, 100% {
        box-shadow:
            0 0 0 2px color-mix(in srgb, var(--cc-accent) 22%, transparent),
            0 0 10px 1px color-mix(in srgb, var(--cc-accent) 50%, transparent);
    }
    50% {
        box-shadow:
            0 0 0 4px color-mix(in srgb, var(--cc-accent) 14%, transparent),
            0 0 16px 2px color-mix(in srgb, var(--cc-accent) 60%, transparent);
    }
}
.cc-rail-scope-line .cc-rail-scope-text { flex: 1; }
.cc-rail-scope-line .cc-rail-scope-text b {
    color: var(--cc-ink);
    font-weight: 700;
    letter-spacing: 0.005em;
}

/* ==========================================================================
   FILTER CONSOLE — the single popover that owns every filter, view toggle,
   sort and system action. Visible trigger sits in cc_filter_secondary col 1;
   the popover content is a tabbed panel (Scope / View & System) with
   sectioned widget groups. Tagline at the top sets tone.
   ========================================================================== */

/* The Filter Console trigger — accent-gradient pill with a subtle internal
   beacon so it reads as the dashboard's primary action. Scoped to col 1 of
   cc_filter_secondary so other popovers stay neutral. */
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopover"] button,
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopoverButton"] button {
    position: relative;
    overflow: hidden;
    font-family: var(--cc-body) !important;
    font-size: 0.78rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.04em !important;
    text-transform: uppercase;
    padding: 9px 14px !important;
    border-radius: 10px !important;
    background:
        linear-gradient(135deg,
            color-mix(in srgb, var(--cc-accent) 92%, #fff) 0%,
            var(--cc-accent) 60%,
            color-mix(in srgb, var(--cc-accent) 80%, var(--cc-blue)) 100%) !important;
    color: #fff !important;
    border: 1px solid
        color-mix(in srgb, var(--cc-accent) 75%, #000) !important;
    box-shadow:
        0 1px 0 rgba(255,255,255,.30) inset,
        0 0 0 1px color-mix(in srgb, var(--cc-accent) 25%, transparent),
        0 8px 18px -8px color-mix(in srgb, var(--cc-accent) 65%, transparent) !important;
    transition: transform .16s ease, box-shadow .16s ease, filter .16s ease !important;
}
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopover"] button::before,
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopoverButton"] button::before {
    content: '';
    position: absolute;
    top: 0; bottom: 0;
    left: -100%;
    width: 60%;
    background: linear-gradient(90deg,
        transparent 0%,
        rgba(255,255,255,.18) 50%,
        transparent 100%);
    animation: iv-fc-sheen 5.2s ease-in-out infinite;
    pointer-events: none;
}
@keyframes iv-fc-sheen {
    0%   { left: -100%; }
    55%  { left: 130%; }
    100% { left: 130%; }
}
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopover"] button:hover,
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopoverButton"] button:hover {
    transform: translateY(-1px);
    filter: brightness(1.07);
    box-shadow:
        0 1px 0 rgba(255,255,255,.32) inset,
        0 0 0 1px color-mix(in srgb, var(--cc-accent) 35%, transparent),
        0 14px 26px -10px color-mix(in srgb, var(--cc-accent) 75%, transparent) !important;
}
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopover"] button[aria-expanded="true"],
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(2)
    [data-testid="stPopoverButton"] button[aria-expanded="true"] {
    box-shadow:
        0 1px 0 rgba(255,255,255,.40) inset,
        0 0 0 2px color-mix(in srgb, var(--cc-accent) 45%, transparent),
        0 16px 30px -12px color-mix(in srgb, var(--cc-accent) 75%, transparent) !important;
}

/* Filter Console content — the floating panel.
   Streamlit emits popover content into a portal at body level, so the
   selectors below have to be wide; we scope by markers we control inside. */
.iv-fc-tagline {
    display: flex;
    align-items: center;
    gap: 9px;
    margin: 4px 2px 14px 2px;
    padding: 6px 12px 6px 10px;
    font-family: var(--cc-data);
    font-size: 0.66rem;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--cc-text-mute);
    background: linear-gradient(90deg,
        color-mix(in srgb, var(--cc-accent) 5%, transparent),
        transparent);
    border-left: 2px solid var(--cc-accent);
    border-radius: 2px;
}
.iv-fc-tagline-glyph {
    color: var(--cc-accent);
    font-size: 0.85rem;
    line-height: 1;
    text-shadow: 0 0 12px color-mix(in srgb, var(--cc-accent) 60%, transparent);
}

.iv-fc-section {
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 14px 0 8px 0;
    padding: 0 0 6px 0;
    border-bottom: 1px solid
        color-mix(in srgb, var(--cc-border) 65%, transparent);
}
.iv-fc-section:first-child {
    margin-top: 4px;
}
.iv-fc-section-glyph {
    font-size: 0.95rem;
    line-height: 1;
    color: var(--cc-text-mute);
    width: 18px;
    text-align: center;
}
.iv-fc-section-label {
    font-family: var(--cc-data);
    font-size: 0.66rem;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--cc-ink);
}

.iv-fc-hint {
    font-family: var(--cc-data);
    font-size: 0.62rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    font-variant-numeric: tabular-nums;
    color: var(--cc-text-mute);
    padding: 0 0 6px 2px;
    font-weight: 600;
}

/* Locked-scope row (e.g. session-bound company / single team) — read-only
   pill that signals "this is fixed for your session". */
.iv-fc-locked {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 10px 7px 9px;
    margin: 12px 0 10px 0;
    background: color-mix(in srgb, var(--cc-text-mute) 4%, transparent);
    border: 1px dashed
        color-mix(in srgb, var(--cc-border-hi) 80%, transparent);
    border-radius: 8px;
    font-family: var(--cc-body);
    font-size: 0.78rem;
    color: var(--cc-text);
}
.iv-fc-locked-glyph {
    font-size: 0.95rem;
    color: var(--cc-text-mute);
}
.iv-fc-locked-label {
    font-family: var(--cc-data);
    font-size: 0.62rem;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 700;
}
.iv-fc-locked-val {
    color: var(--cc-ink);
    font-weight: 600;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.iv-fc-locked-tag {
    font-family: var(--cc-data);
    font-size: 0.58rem;
    letter-spacing: 0.10em;
    text-transform: uppercase;
    color: var(--cc-accent);
    background: color-mix(in srgb, var(--cc-accent) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--cc-accent) 30%, transparent);
    padding: 2px 7px;
    border-radius: 999px;
    font-weight: 700;
}

/* Search recap line (top of Scope tab) */
.iv-fc-search-recap {
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 8px 12px;
    margin: 4px 0 14px 0;
    background:
        linear-gradient(90deg,
            color-mix(in srgb, var(--cc-blue) 6%, transparent),
            transparent);
    border: 1px solid color-mix(in srgb, var(--cc-blue) 22%, var(--cc-border));
    border-radius: 8px;
    font-family: var(--cc-body);
    font-size: 0.78rem;
}
.iv-fc-search-recap--empty {
    background:
        linear-gradient(90deg,
            color-mix(in srgb, var(--cc-text-mute) 4%, transparent),
            transparent);
    border-color:
        color-mix(in srgb, var(--cc-border) 80%, transparent);
}
.iv-fc-search-glyph {
    font-size: 0.95rem;
    color: var(--cc-blue);
    line-height: 1;
}
.iv-fc-search-recap--empty .iv-fc-search-glyph {
    color: var(--cc-text-mute);
}
.iv-fc-search-label {
    font-family: var(--cc-data);
    font-size: 0.60rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--cc-text-mute);
    font-weight: 700;
}
.iv-fc-search-q {
    font-family: var(--cc-data);
    font-size: 0.78rem;
    color: var(--cc-ink);
    background: color-mix(in srgb, var(--cc-blue) 10%, transparent);
    padding: 2px 8px;
    border-radius: 6px;
    border: 1px solid color-mix(in srgb, var(--cc-blue) 25%, transparent);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.iv-fc-search-q-empty {
    font-family: var(--cc-data);
    font-size: 0.74rem;
    color: var(--cc-text-mute);
    font-style: italic;
}

/* Tabs inside the Filter Console — flatter, more deliberate than default */
[data-baseweb="popover"] [data-baseweb="tab-list"] {
    gap: 0 !important;
    border-bottom: 1px solid
        color-mix(in srgb, var(--cc-border) 70%, transparent) !important;
    margin-bottom: 12px !important;
}
[data-baseweb="popover"] [data-baseweb="tab"] {
    font-family: var(--cc-data) !important;
    font-size: 0.66rem !important;
    letter-spacing: 0.14em !important;
    text-transform: uppercase !important;
    font-weight: 700 !important;
    padding: 10px 14px !important;
    color: var(--cc-text-mute) !important;
    transition: color .18s ease, border-color .18s ease;
}
[data-baseweb="popover"] [data-baseweb="tab"][aria-selected="true"] {
    color: var(--cc-accent) !important;
    border-bottom-color: var(--cc-accent) !important;
}

/* Search input in cc_filter_secondary col 0 — pill the user can type into */
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(1)
    [data-testid="stTextInput"] input {
    font-family: var(--cc-body) !important;
    font-size: 0.84rem !important;
    padding: 9px 14px !important;
    border-radius: 10px !important;
    border: 1px solid var(--cc-border-hi) !important;
    background: rgba(255,255,255,.92) !important;
    transition: border-color .18s ease, box-shadow .18s ease, background .18s ease !important;
}
.st-key-cc_filter_secondary [data-testid="stHorizontalBlock"]
    > div[data-testid="column"]:nth-child(1)
    [data-testid="stTextInput"] input:focus {
    border-color: var(--cc-accent) !important;
    background: #fff !important;
    box-shadow:
        0 0 0 3px color-mix(in srgb, var(--cc-accent) 18%, transparent) !important;
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
.iv-tile {
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
.iv-tile::before {
    content: '';
    position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
    background: var(--iv-stat-accent, var(--cc-accent));
    box-shadow: 0 0 14px 0
        color-mix(in srgb, var(--iv-stat-accent, var(--cc-accent)) 45%, transparent);
    opacity: .92;
    transition: box-shadow .28s ease, width .28s ease;
}
.iv-tile::after {
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
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(1) .iv-tile { animation-delay: .00s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(2) .iv-tile { animation-delay: .06s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(3) .iv-tile { animation-delay: .12s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(4) .iv-tile { animation-delay: .18s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(5) .iv-tile { animation-delay: .24s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(6) .iv-tile { animation-delay: .30s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(7) .iv-tile { animation-delay: .36s; }
.st-key-cc_iv_tiles_row [data-testid="stHorizontalBlock"] > div[data-testid="column"]:nth-child(8) .iv-tile { animation-delay: .42s; }

/* Hover / expanded state propagates from the wrapper to the card */
[class*="st-key-cc_tile_"]:hover .iv-tile,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile {
    transform: translateY(-3px);
    border-color: var(--iv-stat-accent);
    box-shadow:
        0 18px 34px -20px color-mix(in srgb, var(--iv-stat-accent) 45%, transparent),
        0 0 0 1px color-mix(in srgb, var(--iv-stat-accent) 20%, transparent);
}
[class*="st-key-cc_tile_"]:hover .iv-tile::before,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile::before {
    width: 4px;
    box-shadow: 0 0 22px 0
        color-mix(in srgb, var(--iv-stat-accent) 70%, transparent);
}
[class*="st-key-cc_tile_"]:hover .iv-tile::after,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile::after {
    transform: translate(-14px, 14px) scale(1.12);
}
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile {
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
    transform: translateX(1px);
}

/* The stat number is the primary click target — give it a quiet signal:
   a soft accent halo on hover and a slow ambient glow pulse so the numeral
   reads as an affordance without screaming for attention. */
.iv-tile .iv-tile-number {
    animation:
        iv-stat-in .6s cubic-bezier(.2,.7,.2,1) both,
        iv-tile-num-glow 5.6s ease-in-out 1.2s infinite;
    will-change: text-shadow, transform;
}
@keyframes iv-tile-num-glow {
    0%, 100% { text-shadow: none; }
    50%      { text-shadow:
        0 0 12px color-mix(in srgb, var(--iv-stat-accent) 22%, transparent),
        0 0 2px  color-mix(in srgb, var(--iv-stat-accent) 16%, transparent); }
}
[class*="st-key-cc_tile_"]:hover .iv-tile .iv-tile-number,
[class*="st-key-cc_tile_"]:has([aria-expanded="true"]) .iv-tile .iv-tile-number {
    transform: translateX(1px);
    text-shadow:
        0 0 18px color-mix(in srgb, var(--iv-stat-accent) 50%, transparent),
        0 0 2px  color-mix(in srgb, var(--iv-stat-accent) 32%, transparent);
    animation: iv-tile-num-glow 1.4s ease-in-out infinite;
}

/* Single-value variant: collapse the big numeral into the actual selected
   string. Drops the display font down in weight, lets long values truncate
   with ellipsis, and tints with the tile accent so it reads as an active
   identity rather than a stat. */
.iv-tile .iv-tile-number.iv-tile-number--value {
    font-family: var(--cc-body) !important;
    font-size: 1.10rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.005em !important;
    line-height: 1.25 !important;
    color: var(--iv-stat-accent, var(--cc-accent)) !important;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 6px 0 8px 0;
}
.iv-tile .iv-tile-number.iv-tile-number--value::after {
    width: 32px;
    opacity: .85;
}
[class*="st-key-cc_tile_"]:hover .iv-tile .iv-tile-number.iv-tile-number--value {
    color: var(--iv-stat-accent, var(--cc-accent)) !important;
    filter: brightness(1.12);
}

/* ======================================================================
   PAGER — compact Prev / page N of M / Next bar used by both the inventory
   table and the event log when their row count exceeds the page size.
   ====================================================================== */
.st-key-cc_iv_pager_top,
.st-key-cc_el_pager_top {
    margin: 10px 0 10px 0;
    padding: 6px 10px;
    border: 1px solid var(--cc-border);
    border-radius: 12px;
    background: linear-gradient(180deg,
        color-mix(in srgb, var(--cc-surface) 92%, transparent) 0%,
        color-mix(in srgb, var(--cc-surface2) 80%, transparent) 100%);
    box-shadow:
        inset 0 1px 0 color-mix(in srgb, #ffffff 6%, transparent),
        0 6px 18px -14px color-mix(in srgb, #000 70%, transparent);
    backdrop-filter: blur(6px) saturate(1.1);
    -webkit-backdrop-filter: blur(6px) saturate(1.1);
}
.st-key-cc_iv_pager_top [data-testid="stButton"] button,
.st-key-cc_el_pager_top [data-testid="stButton"] button {
    min-height: 34px !important;
    padding: 4px 10px !important;
    font-family: var(--cc-body) !important;
    font-size: 0.76rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em !important;
    color: var(--cc-text) !important;
    background: color-mix(in srgb, var(--cc-ink) 3%, transparent) !important;
    border: 1px solid var(--cc-border) !important;
    border-radius: 8px !important;
    box-shadow:
        inset 0 1px 0 color-mix(in srgb, #ffffff 6%, transparent) !important;
    transition:
        background .18s ease,
        border-color .18s ease,
        color .18s ease,
        transform .12s ease !important;
}
.st-key-cc_iv_pager_top [data-testid="stButton"] button:hover:not([disabled]),
.st-key-cc_el_pager_top [data-testid="stButton"] button:hover:not([disabled]) {
    background: color-mix(in srgb, var(--cc-accent) 12%, transparent) !important;
    border-color: color-mix(in srgb, var(--cc-accent) 45%, var(--cc-border)) !important;
    color: var(--cc-ink) !important;
    transform: translateY(-1px);
}
.st-key-cc_iv_pager_top [data-testid="stButton"] button:active:not([disabled]),
.st-key-cc_el_pager_top [data-testid="stButton"] button:active:not([disabled]) {
    transform: translateY(0);
}
.st-key-cc_iv_pager_top [data-testid="stButton"] button[disabled],
.st-key-cc_el_pager_top [data-testid="stButton"] button[disabled] {
    opacity: .38 !important;
    cursor: not-allowed !important;
}
.cc-pager-caption {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    flex-wrap: wrap;
    font-family: var(--cc-body);
    font-size: 0.78rem;
    color: var(--cc-text-mute);
    line-height: 1.2;
    padding: 2px 6px;
}
.cc-pager-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    font-family: var(--cc-display);
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    color: var(--cc-ink);
    background: linear-gradient(180deg,
        color-mix(in srgb, var(--cc-accent) 12%, transparent),
        color-mix(in srgb, var(--cc-teal) 10%, transparent));
    border: 1px solid color-mix(in srgb, var(--cc-accent) 35%, var(--cc-border));
    border-radius: 999px;
    font-variant-numeric: tabular-nums;
}
.cc-pager-pill b {
    font-weight: 700;
    color: var(--cc-accent);
    margin-right: 1px;
}
.cc-pager-sep {
    opacity: .45;
    font-weight: 700;
}
.cc-pager-range {
    font-family: var(--cc-body);
    font-variant-numeric: tabular-nums;
    color: var(--cc-text);
    letter-spacing: 0.01em;
}
.cc-pager-range b {
    color: var(--cc-ink);
    font-weight: 700;
}

/* ======================================================================
   SURFACE TABS — Inventory / Event log
   Scoped to .st-key-cc_surface_tabs so default Streamlit tabs elsewhere
   render unchanged. The design intent here is an editorial "chapter
   select" — an etched tablist with a molten underline that slides
   between tabs, wide uppercase labels set in the display face, and a
   subtle living gradient that activates on the selected chapter.
   ====================================================================== */
.st-key-cc_surface_tabs {
    margin-top: 6px;
}
.st-key-cc_surface_tabs [data-testid="stTabs"] {
    position: relative;
    isolation: isolate;
}
/* Tablist container — glass bar with etched edges */
.st-key-cc_surface_tabs [data-baseweb="tab-list"] {
    position: relative;
    display: flex;
    gap: 0;
    padding: 6px;
    margin: 2px 0 18px 0;
    background: linear-gradient(180deg,
        color-mix(in srgb, var(--cc-surface) 92%, transparent) 0%,
        color-mix(in srgb, var(--cc-surface2) 80%, transparent) 100%);
    border: 1px solid var(--cc-border);
    border-radius: 14px;
    box-shadow:
        inset 0 1px 0 color-mix(in srgb, #ffffff 7%, transparent),
        0 10px 30px -18px color-mix(in srgb, #000 80%, transparent),
        0 1px 0 color-mix(in srgb, #000 18%, transparent);
    backdrop-filter: blur(10px) saturate(1.2);
    -webkit-backdrop-filter: blur(10px) saturate(1.2);
    overflow: hidden;
}
/* Soft living aura behind the tablist */
.st-key-cc_surface_tabs [data-baseweb="tab-list"]::before {
    content: "";
    position: absolute;
    inset: -1px;
    pointer-events: none;
    background:
        radial-gradient(70% 160% at 0% 50%,
            color-mix(in srgb, var(--cc-accent) 10%, transparent) 0%,
            transparent 60%),
        radial-gradient(70% 160% at 100% 50%,
            color-mix(in srgb, var(--cc-teal) 10%, transparent) 0%,
            transparent 60%);
    z-index: 0;
    opacity: .7;
}
/* Individual tab buttons — equal width, centered, uppercase display */
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab"] {
    position: relative;
    flex: 1 1 0;
    min-height: 52px;
    padding: 10px 22px !important;
    margin: 0 !important;
    background: transparent !important;
    border: none !important;
    border-radius: 10px !important;
    color: var(--cc-text-mute) !important;
    font-family: var(--cc-display) !important;
    font-size: 0.80rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.22em !important;
    text-transform: uppercase;
    cursor: pointer;
    z-index: 1;
    transition:
        color .3s cubic-bezier(.2,.8,.2,1),
        background .3s cubic-bezier(.2,.8,.2,1),
        transform .3s cubic-bezier(.2,.8,.2,1);
}
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab"]:hover {
    color: var(--cc-text) !important;
    background: color-mix(in srgb, var(--cc-ink) 4%, transparent) !important;
}
/* Selected tab — warm inked surface with serif emphasis */
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab"][aria-selected="true"] {
    color: var(--cc-ink) !important;
    background: linear-gradient(180deg,
        color-mix(in srgb, var(--cc-ink) 3%, var(--cc-paper, var(--cc-surface))) 0%,
        color-mix(in srgb, var(--cc-accent) 6%, var(--cc-paper, var(--cc-surface))) 100%) !important;
    box-shadow:
        inset 0 1px 0 color-mix(in srgb, #ffffff 55%, transparent),
        0 1px 0 color-mix(in srgb, #000 20%, transparent),
        0 6px 18px -10px color-mix(in srgb, var(--cc-accent) 60%, transparent);
}
/* Molten underline — anchored beneath the active label */
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab"][aria-selected="true"]::after {
    content: "";
    position: absolute;
    left: 22%;
    right: 22%;
    bottom: 6px;
    height: 2px;
    border-radius: 2px;
    background: linear-gradient(90deg,
        transparent,
        var(--cc-accent),
        var(--cc-teal),
        transparent);
    opacity: .85;
    animation: cc-surface-underline .5s cubic-bezier(.2,.8,.2,1);
}
@keyframes cc-surface-underline {
    0%   { transform: scaleX(0); opacity: 0; }
    60%  { transform: scaleX(1.05); opacity: 1; }
    100% { transform: scaleX(1); opacity: .85; }
}
/* Hide the default baseweb indicator bar — replaced by our own */
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab-highlight"],
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab-border"] {
    display: none !important;
}
/* Tab panels — give them a subtle frame that feels continuous with the list */
.st-key-cc_surface_tabs [data-baseweb="tab-panel"] {
    padding: 4px 0 0 0 !important;
    animation: cc-surface-panel-in .45s cubic-bezier(.2,.8,.2,1);
}
@keyframes cc-surface-panel-in {
    0%   { opacity: 0; transform: translateY(4px); }
    100% { opacity: 1; transform: translateY(0); }
}
/* Prevent focus ring from re-adding baseweb's blue outline */
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab"]:focus {
    outline: none !important;
    box-shadow: none !important;
}
.st-key-cc_surface_tabs [data-baseweb="tab-list"] [data-baseweb="tab"][aria-selected="true"]:focus {
    outline: none !important;
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
    .iv-tile {
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
   ROLE DETECTION EXPLAINER — popover next to the identity badge.
   Everything lives inside the popover, so styles only need to paint text
   blocks, a KV list, and a compact resolution-trace table.
   ========================================================================== */
.cc-role-why {
    font-family: var(--cc-body);
    margin: -4px 0 8px 0;
    padding: 8px 10px 10px 10px;
    border-radius: 10px;
    border: 1px solid color-mix(in srgb, var(--cc-border) 70%, transparent);
    background:
        linear-gradient(180deg,
            color-mix(in srgb, var(--cc-accent) 3%, #fff) 0%,
            #fff 100%);
}
.cc-role-why-head {
    font-size: .64rem; font-weight: 700;
    letter-spacing: .12em; text-transform: uppercase;
    color: var(--cc-text-mute);
    margin-bottom: 4px;
}
.cc-role-why-pick {
    display: flex; align-items: baseline; gap: 8px;
    margin-bottom: 4px;
}
.cc-role-why-icon { font-size: 1.15rem; line-height: 1; }
.cc-role-why-name {
    font-family: var(--cc-display);
    font-weight: 700; font-size: 1.05rem;
    letter-spacing: -.005em;
}
.cc-role-why-reason {
    font-size: .80rem;
    color: var(--cc-text);
    line-height: 1.4;
}
.cc-role-why-sub {
    font-size: .62rem; font-weight: 700;
    letter-spacing: .12em; text-transform: uppercase;
    color: var(--cc-text-mute);
    margin: 10px 0 4px 0;
    border-top: 1px solid color-mix(in srgb, var(--cc-border) 50%, transparent);
    padding-top: 8px;
}
.cc-role-why-kv {
    font-size: .78rem;
    color: var(--cc-ink);
    margin-bottom: 3px;
    line-height: 1.4;
    word-break: break-word;
}
.cc-role-why-kv code {
    font-family: var(--cc-data);
    font-size: .72rem;
    padding: 1px 5px;
    border-radius: 4px;
    background: color-mix(in srgb, var(--cc-accent) 8%, #fff);
    border: 1px solid color-mix(in srgb, var(--cc-accent) 18%, transparent);
    color: var(--cc-accent);
}
.cc-role-why-rules {
    list-style: none;
    padding: 0; margin: 0 0 6px 0;
}
.cc-role-why-rules li {
    font-size: .78rem;
    margin-bottom: 2px;
    color: var(--cc-text);
}
.cc-role-why-rules code {
    font-family: var(--cc-data);
    font-size: .72rem;
    padding: 1px 5px;
    border-radius: 4px;
    background: var(--cc-surface2);
    border: 1px solid var(--cc-border);
    color: var(--cc-ink);
}
.cc-role-why-rules b { color: var(--cc-accent); }
.cc-role-why-note {
    font-size: .72rem;
    color: var(--cc-text-mute);
    line-height: 1.45;
    font-style: italic;
    margin-top: 2px;
}
.cc-role-why-note code {
    font-family: var(--cc-data);
    font-size: .68rem;
    font-style: normal;
    padding: 0 3px;
    background: color-mix(in srgb, var(--cc-border) 40%, transparent);
    border-radius: 3px;
}
.cc-role-why-trace {
    width: 100%;
    font-family: var(--cc-data);
    font-size: .72rem;
    border-collapse: collapse;
    margin-top: 4px;
}
.cc-role-why-trace thead th {
    text-align: left;
    font-weight: 700;
    text-transform: uppercase;
    font-size: .60rem;
    letter-spacing: .1em;
    color: var(--cc-text-mute);
    padding: 4px 6px;
    border-bottom: 1px solid var(--cc-border);
}
.cc-role-why-trace tbody td {
    padding: 4px 6px;
    border-bottom: 1px dashed color-mix(in srgb, var(--cc-border) 60%, transparent);
    color: var(--cc-ink);
    vertical-align: top;
}
.cc-role-why-trace tbody tr:last-child td { border-bottom: none; }
.cc-role-why-trace code {
    font-family: var(--cc-data);
    font-size: .70rem;
    padding: 1px 4px;
    border-radius: 3px;
    background: var(--cc-surface2);
    color: var(--cc-ink);
}
.cc-role-why-trace b { color: var(--cc-accent); }
.cc-role-why-skip {
    color: var(--cc-text-mute);
    font-style: italic;
    font-family: var(--cc-body);
    font-size: .72rem;
}

/* The ⓘ popover button sits in the rail's identity column. Keep it compact
   and visually subordinate to the role badge itself. */
.st-key-cc_filter_rail .stColumn:first-child [data-testid="stPopover"] button,
.st-key-cc_filter_rail .stColumn:first-child [data-testid="stPopoverButton"] button {
    min-height: 30px !important;
    padding: 4px 6px !important;
    font-size: 0.82rem !important;
    font-family: var(--cc-body) !important;
    background: transparent !important;
    border: 1px solid color-mix(in srgb, var(--cc-border) 60%, transparent) !important;
    color: var(--cc-text-mute) !important;
    box-shadow: none !important;
}
.st-key-cc_filter_rail .stColumn:first-child [data-testid="stPopover"] button:hover,
.st-key-cc_filter_rail .stColumn:first-child [data-testid="stPopoverButton"] button:hover {
    background: color-mix(in srgb, var(--cc-accent) 6%, #fff) !important;
    border-color: var(--cc-accent) !important;
    color: var(--cc-accent) !important;
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

/* Strip clipping/transform ONLY on the inner block wrappers between the rail
   and the scroll root. The scroll root itself (stAppViewContainer / stMain /
   section.main) MUST keep its overflow — otherwise the page stops scrolling.
   These inner wrappers are the ones that break sticky; the outer scroller
   provides the scroll axis and the sticky viewport. */
[data-testid="stMainBlockContainer"],
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

ROLES = ["Admin", "CLevel", "Developer", "QC", "Operations"]
# CLevel = executive-level role with the same VIEW as Admin but a distinct
# display identity. Treated as admin-equivalent for every gate via
# `_is_admin`, but the rail badge / role-detection trace label it "CLevel"
# so executives don't read as administrators in screenshots.
ROLE_ICONS = {
    "Admin": "🛡", "CLevel": "♛",
    "Developer": "⌨", "QC": "🔬", "Operations": "🚀",
}
ROLE_COLORS = {
    "Admin": "#4f46e5", "CLevel": "#b45309",  # warm amber for the exec view
    "Developer": "#2563eb", "QC": "#7c3aed", "Operations": "#059669",
}
# Map role → inventory team field(s) used to filter projects. Each role is
# scoped *strictly* to its own ownership field on the inventory document —
# Developer sees only projects where dev_team ∈ their teams; QC only where
# qc_team matches; Operations only where ops_team matches. Admin and CLevel
# bypass this entirely (full fleet visibility).
ROLE_TEAM_FIELDS: dict[str, list[str]] = {
    "Admin":     [],
    "CLevel":    [],
    "Developer": ["dev_team.keyword"],
    "QC":        ["qc_team.keyword"],
    "Operations":  ["ops_team.keyword"],
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
    # One field per role today; keep the OR structure in case a role is ever
    # scoped against multiple ownership fields again.
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
    # Pull every candidate date field so downstream parsing can fall back when
    # the index uses a non-canonical casing (StartDate, @timestamp, etc.).
    _deploy_date_fields = _DATE_CANDIDATES.get("deploy", ["startdate"])
    _hit_source = ["application", "codeversion", "status", *_deploy_date_fields]
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
                                    "_source": _hit_source,
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
                                            "_source": _hit_source,
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
        _last_hit = _latest_hits[0] if _latest_hits else {}
        _succ_hit = _succ_hits[0]   if _succ_hits   else {}
        _last_s  = (_last_hit.get("_source") if _last_hit else {}) or {}
        _succ_s  = (_succ_hit.get("_source") if _succ_hit else {}) or {}
        _live_version = _succ_s.get("codeversion", "") or ""
        # Use _hit_date so we pull the sort value (epoch-ms) or any candidate
        # date field rather than failing silently on a single hard-coded name.
        _succ_when = _hit_date(_succ_hit, "deploy") if _succ_hit else ""
        _last_when = _hit_date(_last_hit, "deploy") if _last_hit else ""
        out[_app] = {
            "live":           bool(_succ_s),
            "version":        _live_version,
            "when":           _succ_when or "",
            "status":         _last_s.get("status", "") or "",
            # Extra context so popovers can show "last attempt failed" etc.
            "last_version":   _last_s.get("codeversion", "") or "",
            "last_when":      _last_when or "",
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
                                                "imageName", "imageTag",
                                                "enddate", "startdate", "environment",
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
                "environment": _s.get("environment", "") or "",
                # Prisma index has both enddate + startdate as date fields.
                # enddate is the scan completion timestamp; fall back to
                # startdate only if a document somehow lacks enddate.
                "when":      _s.get("enddate") or _s.get("startdate") or "",
            }
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_invicti(app_versions: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], dict]:
    """Latest Invicti DAST scan per ``(app, version)`` pair.

    Returns ``{(app, version): {Vcritical, Vhigh, Vmedium, Vlow, BestPractice,
    Informational, status, environment, url, when}}``. Pairs with no scan are
    omitted — callers treat that as "never DAST-scanned by Invicti".
    """
    if not app_versions:
        return {}
    apps = sorted({_a for _a, _ in app_versions if _a})
    if not apps:
        return {}
    try:
        resp = es_search(
            IDX["invicti"],
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
                                                "BestPractice", "Informational",
                                                "environment", "url",
                                                "enddate", "startdate",
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
                "Vcritical":     int(_s.get("Vcritical") or 0),
                "Vhigh":         int(_s.get("Vhigh")     or 0),
                "Vmedium":       int(_s.get("Vmedium")   or 0),
                "Vlow":          int(_s.get("Vlow")      or 0),
                "BestPractice":  int(_s.get("BestPractice") or 0),
                "Informational": int(_s.get("Informational") or 0),
                "status":        _s.get("status", "")      or "",
                "environment":   _s.get("environment", "") or "",
                "url":           _s.get("url", "")         or "",
                "when":          _s.get("enddate") or _s.get("startdate") or "",
            }
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_zap(app_versions: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], dict]:
    """Latest OWASP-ZAP DAST scan per ``(app, version)`` pair.

    ZAP doesn't surface a critical bucket — only ``Vhigh`` / ``Vmedium`` /
    ``Vlow`` plus ``Informational`` and ``FalsePositives`` (both keyword in
    the index, but cast to int defensively for counting).
    """
    if not app_versions:
        return {}
    apps = sorted({_a for _a, _ in app_versions if _a})
    if not apps:
        return {}
    try:
        resp = es_search(
            IDX["zap"],
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
                                                "Vhigh", "Vmedium", "Vlow",
                                                "FalsePositives", "Informational",
                                                "environment", "url",
                                                "enddate", "startdate",
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

    def _coerce_int(v) -> int:
        try:
            return int(v) if v not in (None, "") else 0
        except (TypeError, ValueError):
            return 0

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
                # ZAP has no critical bucket — we still expose the field as 0 so
                # downstream code can sum across scanners with a uniform shape.
                "Vcritical":      0,
                "Vhigh":          int(_s.get("Vhigh")   or 0),
                "Vmedium":        int(_s.get("Vmedium") or 0),
                "Vlow":           int(_s.get("Vlow")    or 0),
                "Informational":  _coerce_int(_s.get("Informational")),
                "FalsePositives": _coerce_int(_s.get("FalsePositives")),
                "status":         _s.get("status", "")      or "",
                "environment":    _s.get("environment", "") or "",
                "url":            _s.get("url", "")         or "",
                "when":           _s.get("enddate") or _s.get("startdate") or "",
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
    """For each application, fetch the latest *successful* record at each stage.

    A "stage" is one of: build (ef-cicd-builds), release (ef-cicd-releases),
    or a deployment in a given environment (dev/qc/uat/prd on
    ef-cicd-deployments).

    Build and deployment queries filter on ``status`` ∈ ``SUCCESS_STATUSES`` so
    the inventory's "latest" columns reflect what actually shipped — a failed
    deploy on top of an older successful one should not mask the last known
    good version. Releases are not status-filtered (they lack a consistent
    success flag).

    Returns::

        {app: {stage: {"version": str, "when": iso-str, "status": str}}}

    Stages with no successful record are simply absent from the inner dict.
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

    # ---- builds (startdate) — SUCCESS only so the inventory's Build column
    # reflects the last known-good build rather than the last attempted one.
    try:
        resp = es_search(
            IDX["builds"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": apps_list}},
                    {"terms": {"status": SUCCESS_STATUSES}},
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

    # ---- deployments split by environment (startdate) — SUCCESS only so the
    # inventory's env columns (Dev / QC / UAT / PRD) reflect what is actually
    # running in each environment, not the last attempt.
    try:
        resp = es_search(
            IDX["deployments"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": apps_list}},
                    {"terms": {"environment": ["dev", "qc", "uat", "prd"]}},
                    {"terms": {"status": SUCCESS_STATUSES}},
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

    Developer → ``dev_team``; QC → ``qc_team``; Operations → ``ops_team``.
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
# Canonical role source in this repo is ``st.session_state.user_roles`` — a
# dict keyed by role name (agent.py / agentUI.py both gate admin on
# ``"admin" in user_roles``). ``session_state.roles`` is not used by the auth
# layer here, so we don't read it.
_session_teams: list[str] = st.session_state.get("teams") or []
_session_user_roles = st.session_state.get("user_roles") or {}
_user_role_keys: list[str] = (
    list(_session_user_roles.keys())
    if isinstance(_session_user_roles, dict)
    else list(_session_user_roles)
    if isinstance(_session_user_roles, (list, tuple, set))
    else []
)

# Strict role mapping — only the canonical strings below are honoured. No
# loose aliases (devops / dev / ops / quality) so a typo or adjacent role
# can't silently elevate.
_ROLE_STRICT: dict[str, str] = {
    "admin":           "Admin",
    "clevel":          "CLevel",
    "c-level":         "CLevel",
    "executive":       "CLevel",
    "developer":       "Developer",
    "quality-control": "QC",
    "operator":        "Operations",
    "operations":      "Operations",
}
_detected_roles: list[str] = []
_role_trace: list[tuple[str, str]] = []  # (raw, resolved)
for _sr in _user_role_keys:
    if not isinstance(_sr, str):
        continue
    _norm = _sr.strip().lower()
    _canon = _ROLE_STRICT.get(_norm)
    if _canon is not None:
        _detected_roles.append(_canon)
        _role_trace.append((_sr, _canon))
    else:
        _role_trace.append((_sr, "—"))
# Deduplicate while preserving order
_detected_roles = list(dict.fromkeys(_detected_roles))


# ── Resolve role early so the filter rail can style itself by role color ────
# Priority: Admin > CLevel > anything else. Admin and CLevel both grant
# full-fleet visibility, but Admin wins the tie-break so an admin who's
# ALSO listed as clevel surfaces as Admin (matches the more privileged
# label).
if "Admin" in _detected_roles:
    role_pick = "Admin"
    _role_pick_reason = (
        "'admin' present in session_state.user_roles — highest privilege wins"
    )
elif "CLevel" in _detected_roles:
    role_pick = "CLevel"
    _role_pick_reason = (
        "'clevel' present in session_state.user_roles — executive view "
        "(admin-equivalent visibility, distinct identity)"
    )
elif _detected_roles:
    role_pick = _detected_roles[0]
    _role_pick_reason = (
        f"no 'admin' in session_state.user_roles; first recognised role "
        f"'{role_pick}' used"
    )
else:
    # No recognised role in user_roles — surface it explicitly rather than
    # silently granting Admin. The rail still renders; downstream gates
    # (hygiene, requests, env scope) already key off role_pick.
    role_pick = "Developer"
    _role_pick_reason = (
        "no recognised role in session_state.user_roles — defaulted to "
        "Developer (least privileged)"
    )

# Time-window presets — resolved before the rail so selectbox order is stable.
_TW_LABELS = list(PRESETS.keys())
_preset_default_idx = _TW_LABELS.index("7d")

# ── Role-scoped visibility flags — relied on by scope filters + sections ───
# CLevel mirrors Admin in every flag below — same view, different label.
_ROLE_SHOWS_JIRA: dict[str, bool] = {
    "Admin": True, "CLevel": True,
    "Developer": True, "QC": True, "Operations": False,
}
_ROLE_SHOWS_BUILDS: dict[str, bool] = {
    "Admin": True, "CLevel": True,
    "Developer": True, "QC": False, "Operations": False,
}
_ROLE_EVENT_TYPES: dict[str, list[str]] = {
    "Admin":     ["Build-develop", "Build-release", "Deployments", "Releases", "Requests", "Commits"],
    "CLevel":    ["Build-develop", "Build-release", "Deployments", "Releases", "Requests", "Commits"],
    "Developer": ["Commits", "Build-develop", "Build-release", "Deployments"],
    "QC":        ["Deployments", "Releases", "Requests"],
    "Operations":  ["Deployments", "Releases", "Requests"],
}
_ROLE_ENVS: dict[str, list[str]] = {
    "Admin":     ["prd", "uat", "qc", "dev"],
    "CLevel":    ["prd", "uat", "qc", "dev"],
    "Developer": ["dev"],
    "QC":        ["qc"],
    "Operations":  ["uat", "prd"],
}
_ROLE_APPROVAL_STAGES: dict[str, list[str]] = {
    "Admin":     [],
    "CLevel":    [],
    "Developer": [],
    "QC":        ["qc", "request_deploy_qc", "request_promote"],
    "Operations":  ["uat", "prd", "request_deploy_uat", "request_deploy_prd", "request_promote"],
}
_effective_role = role_pick
# `_is_admin` gates EVERY admin-equivalent view (full-fleet visibility,
# admin-only Filter Console toggles, glossary expander, role-detection
# popover, …). CLevel rides the same rails — its only distinction is the
# display label on the rail badge.
_is_admin = (_effective_role in ("Admin", "CLevel"))

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
    if _is_admin:
        # Admin / CLevel: union the team's apps across every team-field —
        # they have full visibility regardless of which department owns
        # the application.
        _admin_team_apps: set[str] = set()
        for _r in ["Developer", "QC", "Operations"]:
            _admin_team_apps.update(_load_team_applications(_r, team_filter))
        _team_apps = sorted(_admin_team_apps)
    else:
        _team_apps = _load_team_applications(role_pick, team_filter)
elif (not _is_admin) and _active_teams:
    _union: set[str] = set()
    for _t in _active_teams:
        _union.update(_load_team_applications(role_pick, _t))
    _team_apps = sorted(_union)
else:
    _team_apps = []

# Resolve project scope before the rail so the project dropdown respects
# admin_view_all + team assignment without re-querying per widget.
# Admin / CLevel see every project by default on first load — they can opt
# out via the toggle in the Filter Console. Non-admins never see the
# toggle and stay in team-scoped mode.
if _is_admin and "admin_view_all" not in st.session_state:
    st.session_state["admin_view_all"] = True
admin_view_all = bool(st.session_state.get("admin_view_all", False)) if _is_admin else False

# Time window / global toggles — defaults seeded here so the rail can read
# them via session_state. The actual widgets live inside the inventory's
# unified Filter Console popover (see `cc_filter_secondary` below).
st.session_state.setdefault("time_preset", _TW_LABELS[_preset_default_idx])
st.session_state.setdefault("auto_refresh", False)
st.session_state.setdefault("exclude_svc", True)
st.session_state.setdefault("exclude_test_runs", True)
if _is_admin:
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
            _proj_help = f"{len(_all_projects)} projects (no team)"
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
    # Minimal rail: role identity only. Every filter (search, time window,
    # auto-refresh, admin toggles, sort, per-project view, dimensional
    # multiselects, clear cache) was consolidated into a single "Filter
    # Console" popover that sits in the inventory's secondary bar below.
    # The rail intentionally carries zero filterable widgets — this keeps
    # identity and scope visually distinct.
    _rail = st.columns(
        [1.6, 6.4],
        vertical_alignment="bottom",
    )

    # ── Col 0: compact identity badge (role + team) + "how was this role
    # picked?" explainer popover. The badge is visible at a glance; the
    # popover surfaces the raw session state, the mapping rules, and the
    # tie-break so "why am I detected as X" never needs a code dive.
    with _rail[0]:
        # Non-admins don't need the role-resolution explainer — they'd never
        # change the underlying auth wiring anyway. Render the badge alone in
        # a single column for them; admins keep the ⓘ popover beside it.
        if _is_admin:
            _ident_cols = st.columns([4.2, 1], gap="small", vertical_alignment="center")
            _badge_col = _ident_cols[0]
            _why_col = _ident_cols[1]
        else:
            _badge_col = st.container()
            _why_col = None
        with _badge_col:
            st.markdown(
                f'<div class="cc-rail-id">'
                f'<div class="cc-rail-id-role" '
                f'style="color:{_role_clr};border-color:{_role_clr}55;'
                f'background:{_role_clr}0F">{_role_icon} {role_pick}</div>'
                f'<div class="cc-rail-id-team" title="{_team_display}">{_team_display}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        if _why_col is not None:
          with _why_col:
            with st.popover("ⓘ", help="How was this role picked?",
                            use_container_width=True):
                st.markdown(
                    '<div class="cc-role-why">'
                    '<div class="cc-role-why-head">Role detection</div>'
                    f'<div class="cc-role-why-pick">'
                    f'<span class="cc-role-why-icon" '
                    f'style="color:{_role_clr}">{_role_icon}</span>'
                    f'<span class="cc-role-why-name" '
                    f'style="color:{_role_clr}">{role_pick}</span>'
                    '</div>'
                    f'<div class="cc-role-why-reason">{_role_pick_reason}</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

                # Raw session-state values — so the user can see exactly what
                # the parent auth layer fed us.
                st.markdown(
                    '<div class="cc-role-why-sub">Session state</div>',
                    unsafe_allow_html=True,
                )
                _user_roles_repr = ", ".join(
                    f"'{k}'" for k in _user_role_keys
                ) or "— empty —"
                _teams_repr = ", ".join(
                    f"'{t}'" for t in _session_teams
                ) or "— empty —"
                st.markdown(
                    f'<div class="cc-role-why-kv">'
                    f'<code>st.session_state.user_roles</code> keys: '
                    f'{_user_roles_repr}</div>'
                    f'<div class="cc-role-why-kv">'
                    f'<code>st.session_state.teams</code>: {_teams_repr}</div>',
                    unsafe_allow_html=True,
                )

                # Mapping rules — strict list so the user knows which literal
                # strings are honoured.
                st.markdown(
                    '<div class="cc-role-why-sub">Mapping rules (strict)</div>'
                    '<ul class="cc-role-why-rules">'
                    '<li><code>admin</code> → <b>Admin</b></li>'
                    '<li><code>clevel</code> / <code>c-level</code> / '
                    '<code>executive</code> → <b>CLevel</b></li>'
                    '<li><code>developer</code> → <b>Developer</b></li>'
                    '<li><code>quality-control</code> → <b>QC</b></li>'
                    '<li><code>operator</code> / <code>operations</code> → <b>Operations</b></li>'
                    '</ul>'
                    '<div class="cc-role-why-note">'
                    'Only <code>st.session_state.user_roles</code> is read — '
                    'that\'s the canonical role source across this repo. '
                    'Comparison is case-insensitive on the stripped key. '
                    'Anything not in this list is ignored (no loose aliases). '
                    'Tie-break: <code>admin</code> wins, then '
                    '<code>clevel</code>, then the first recognised role.'
                    '</div>',
                    unsafe_allow_html=True,
                )

                # Team-scope mapping — explains which inventory ownership
                # field gates the visible project set for each role.
                st.markdown(
                    '<div class="cc-role-why-sub">Project scope (team field)</div>'
                    '<ul class="cc-role-why-rules">'
                    '<li><b>Developer</b> → <code>dev_team</code> ∈ your teams</li>'
                    '<li><b>QC</b> → <code>qc_team</code> ∈ your teams</li>'
                    '<li><b>Operations</b> → <code>ops_team</code> ∈ your teams</li>'
                    '<li><b>Admin</b> / <b>CLevel</b> → bypass team scoping (full fleet)</li>'
                    '</ul>'
                    '<div class="cc-role-why-note">'
                    'Non-admin roles only see inventory projects where the '
                    "role's ownership field on the inventory document "
                    'matches a team you belong to. No cross-ownership leakage.'
                    '</div>',
                    unsafe_allow_html=True,
                )

                # Trace — shows every token seen and how it was resolved. Most
                # useful when a role you expected isn't being picked up.
                if _role_trace:
                    _skip_html = (
                        "<span class=\"cc-role-why-skip\">ignored</span>"
                    )
                    _rows: list[str] = []
                    for _raw, _out in _role_trace:
                        _cell = (
                            f"<b>{_out}</b>" if _out != "—" else _skip_html
                        )
                        _rows.append(
                            f"<tr><td><code>{_raw}</code></td>"
                            f"<td>{_cell}</td></tr>"
                        )
                    _rows_html = "".join(_rows)
                    st.markdown(
                        '<div class="cc-role-why-sub">Resolution trace</div>'
                        '<table class="cc-role-why-trace">'
                        '<thead><tr><th>user_roles key</th>'
                        '<th>Resolved</th></tr></thead>'
                        f'<tbody>{_rows_html}</tbody></table>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        '<div class="cc-role-why-note">'
                        '<code>st.session_state.user_roles</code> carried no '
                        'keys. Check that your auth layer populates it as a '
                        'dict keyed by role name (e.g. '
                        '<code>{"admin": {...}}</code>).'
                        '</div>',
                        unsafe_allow_html=True,
                    )

    # ── Col 1: scope-summary line (no widgets — everything filterable lives
    # in the unified Filter Console below). The line is purely informational:
    # it nods at the active window + role scope so the rail still feels
    # contextual without re-introducing widget state.
    with _rail[1]:
        st.markdown(
            '<div class="cc-rail-scope-line">'
            '<span class="cc-rail-scope-dot"></span>'
            '<span class="cc-rail-scope-text">'
            'Filters live in the <b>Filter Console</b> below — '
            'every scope (search, time, dimensions, sort) is consolidated there.'
            '</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    # All filter widgets — search, time window, auto-refresh, admin toggles,
    # sort, per-project view, dimensional multiselects, clear cache — were
    # consolidated into the Filter Console popover (rendered later via
    # `cc_filter_secondary` in `_render_inventory_view`). The rail simply
    # reads their current values from session_state. Defaults are seeded
    # upstream via `st.session_state.setdefault(...)`.
    preset       = st.session_state["time_preset"]
    auto_refresh = bool(st.session_state["auto_refresh"])
    exclude_svc  = bool(st.session_state["exclude_svc"]) if _is_admin else True

    # Global company/project pickers were removed from the rail — the
    # Filter Console below owns scope. Defaults stay empty so every rail-level
    # ES query is unscoped at this layer; the inventory's filters apply their
    # own restrictions, and the event log inherits the inventory-filtered set.
    company_filter = ""
    project_filter = ""

    # Resolve the selected window → start/end timestamps. `preset` is read
    # from the Filter Console (seeded to "7d" on first paint).
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

    interval    = pick_interval(end_dt - start_dt)

    _start_local = start_dt.astimezone(DISPLAY_TZ)
    _end_local   = end_dt.astimezone(DISPLAY_TZ)
    _now_local   = datetime.now(timezone.utc).astimezone(DISPLAY_TZ)
    _window_label = (
        "All-time" if preset == "All-time"
        else f"{_start_local:%Y-%m-%d %H:%M} → {_end_local:%Y-%m-%d %H:%M} {DISPLAY_TZ_LABEL}"
    )

# For non-admin roles with no specific project picked, restrict queries to
# the role's visible projects. Admin / CLevel scope the same way unless
# their view-all toggle is on.
_scoped_projects: list[str] = []
if not project_filter:
    if not _is_admin:
        _scoped_projects = _proj_scoped
    elif not admin_view_all:
        _scoped_projects = _proj_scoped


# ── Pipelines inventory panel anchor + slot ───────────────────────────────
# The event log renders inside the inventory fragment so it inherits every
# filter the user selects on the inventory. Both views are visible for all
# roles — role-specific scoping happens inside the event log via
# _ROLE_EVENT_TYPES / _ROLE_ENVS. Kept as module-level flags because several
# rendering blocks below branch on them when composing their layouts.
_show_el  = True
_show_inv = True
st.markdown('<a class="anchor" id="sec-inventory"></a>', unsafe_allow_html=True)
st.markdown('<a class="anchor" id="sec-eventlog"></a>', unsafe_allow_html=True)
# Two top-level slots so the filter bar (controls) lives as a sibling of the
# inventory body — both pinned at page-scope. Putting controls at the same
# DOM depth as the rail lets `position: sticky` on the filter bar reference
# the page's main scroll context (the natural one), instead of the
# inventory slot's containing block which would only let the bar stick
# WHILE the inventory tab is in view.
_iv_top_controls_slot = st.empty()
_inventory_slot = st.empty()


# Match-nothing sentinel — used to refuse a fall-back unscoped query when a
# non-admin user has assigned teams but no role-team coverage in the index
# (e.g. Operations user whose teams don't appear in any document's ops_team
# field). Without this, an empty `_team_apps` + empty `_scoped_projects`
# would silently drop both filters and the user would see ALL apps.
_MATCH_NONE_FILTER = {"bool": {"must_not": [{"match_all": {}}]}}


def _role_team_scope_empty() -> bool:
    """True when a non-admin role has session teams but neither projects nor
    apps came back from the role's team field. Triggering this means the
    query MUST be forced to an empty result rather than running unscoped."""
    if _is_admin or not _active_teams:
        return False
    return (not _team_apps) and (not _scoped_projects)


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
    # Team-based application restriction — skipped for admins in view-all
    # mode so the toggle truly means "every project, every app".
    if _team_apps and not (_is_admin and admin_view_all):
        fs.append({"terms": {"application": _team_apps}})
    # Refuse to run unscoped when the role's team field has zero coverage.
    if _role_team_scope_empty():
        fs.append(_MATCH_NONE_FILTER)
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
    # Team-based application restriction — skipped for admins in view-all
    # mode so the toggle truly means "every project, every app".
    if _team_apps and not (_is_admin and admin_view_all):
        fs.append({"terms": {"application.keyword": _team_apps}})
    # Refuse to run unscoped when the role's team field has zero coverage.
    if _role_team_scope_empty():
        fs.append(_MATCH_NONE_FILTER)
    # Always exclude noise/test projects
    fs.append({"bool": {"must_not": [{"terms": {"project.keyword": EXCLUDED_PROJECTS}}]}})
    return fs


def commit_scope_filters() -> list[dict]:
    """scope_filters() + optional service-account exclusion for commit queries."""
    fs = list(scope_filters())
    if exclude_svc:
        fs.append({"bool": {"must_not": [{"term": {"authorname": SVC_ACCOUNT}}]}})
    return fs


def _testflag_filter() -> list[dict]:
    """When the "Production runs only" toggle is on (default), restrict
    build / deployment queries to documents flagged ``testflag = "Normal"``.
    The toggle lives in the Filter Console (View & System tab); the value
    is read from session_state so this helper is callable anywhere.

    Builds and deployments are the only indices today that carry a
    ``testflag`` field, so the helper is invoked exclusively from
    ``build_scope_filters`` / ``deploy_scope_filters``.
    """
    if bool(st.session_state.get("exclude_test_runs", True)):
        return [{"term": {"testflag": "Normal"}}]
    return []


def build_scope_filters() -> list[dict]:
    """scope_filters() + release-branch only (production pipeline builds)."""
    return scope_filters() + [{"term": {"branch": "release"}}] + _testflag_filter()


def deploy_scope_filters() -> list[dict]:
    """scope_filters() + exclude pre-release/test versions (codeversion 0.*)."""
    return (
        scope_filters()
        + [{"bool": {"must_not": [{"prefix": {"codeversion": "0."}}]}}]
        + _testflag_filter()
    )


def idx_scope(index: str) -> list[dict]:
    """Return the appropriate scope filters for the given index."""
    if index == IDX["builds"]:
        return build_scope_filters()
    if index == IDX["deployments"]:
        return deploy_scope_filters()
    if index == IDX["commits"]:
        return commit_scope_filters()
    return scope_filters()


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
# Page sizes for the two big row tables. Paginating keeps rendered DOM small
# even when the filtered set is large — inventory popovers and event rows
# dominate paint cost, so capping visible rows is the single biggest lever.
_EL_PAGE_SIZE = 75
_IV_PAGE_SIZE = 50


def _render_pager(
    *, total: int, page_size: int, page_key: str,
    unit_label: str, container_key: str,
) -> tuple[int, int, int]:
    """Render a Prev / N of M / Next pager and return (page, start, end).

    Only renders when ``total > page_size``. When not needed, returns a
    no-op window ``(1, 0, total)`` so callers can always slice with the
    returned range. Session state is the single source of truth for the
    current page — buttons mutate it then rely on the fragment-auto-rerun
    that follows a widget interaction."""
    if total <= page_size:
        return 1, 0, total
    _max_page = max(1, (total + page_size - 1) // page_size)
    try:
        _page = int(st.session_state.get(page_key, 1) or 1)
    except (TypeError, ValueError):
        _page = 1
    _page = max(1, min(_page, _max_page))
    # Persist the clamped value so a narrowed filter doesn't leave the user
    # on an out-of-range page.
    st.session_state[page_key] = _page
    _start = (_page - 1) * page_size
    _end = min(_start + page_size, total)

    with st.container(key=container_key):
        _pc = st.columns([1.0, 1.0, 4.6, 1.0, 1.0], vertical_alignment="center")
        with _pc[0]:
            if st.button("◀  Prev", key=f"{page_key}_prev",
                         use_container_width=True,
                         disabled=_page <= 1,
                         help="Previous page"):
                st.session_state[page_key] = _page - 1
                st.rerun()
        with _pc[1]:
            if st.button("⇤  First", key=f"{page_key}_first",
                         use_container_width=True,
                         disabled=_page <= 1,
                         help="Jump to first page"):
                st.session_state[page_key] = 1
                st.rerun()
        with _pc[2]:
            st.markdown(
                f'<div class="cc-pager-caption">'
                f'<span class="cc-pager-pill">Page <b>{_page}</b> / {_max_page}</span>'
                f'<span class="cc-pager-sep">·</span>'
                f'<span class="cc-pager-range">{_start + 1:,}–{_end:,} '
                f'of <b>{total:,}</b> {unit_label}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _pc[3]:
            if st.button("Last  ⇥", key=f"{page_key}_last",
                         use_container_width=True,
                         disabled=_page >= _max_page,
                         help="Jump to last page"):
                st.session_state[page_key] = _max_page
                st.rerun()
        with _pc[4]:
            if st.button("Next  ▶", key=f"{page_key}_next",
                         use_container_width=True,
                         disabled=_page >= _max_page,
                         help="Next page"):
                st.session_state[page_key] = _page + 1
                st.rerun()

    return _page, _start, _end


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


def _render_event_log() -> None:
    """Inline event log — role-scoped.

    Not a fragment: every filter widget that drives this view (search,
    project pick, per-project layout, the Filter Console multiselects /
    pills) lives OUTSIDE this function. A `@st.fragment` decorator
    (especially with ``run_every``) sets up an independent refresh loop
    whose state can decouple from the parent rerun, leaving the event
    log staring at a stale `_el_inv_scope_apps` after a filter change.
    Running it as a plain function guarantees a fresh re-render on every
    parent rerun. Periodic refresh remains available via the
    Filter Console's "Auto-refresh (60s)" toggle.
    """
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
    # If commits are role-allowed, surface that they're opt-in by default so
    # the empty-pill state isn't surprising.
    _commit_optin_hint = _role_allows_type("Commits")
    _hint_html = (
        'Click any pill to include it · select multiple to combine · '
        '<b>commits hidden by default — click ⎇ to surface</b>'
        if _commit_optin_hint
        else 'Click any pill to include it · select multiple to combine · '
             'none selected = show all'
    )
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
        f'    <div class="el-tf-hint">{_hint_html}</div>'
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

    # Filter semantics:
    #   • No pills selected → show every visible-by-default type, but HIDE
    #     commits (they're high-volume noise — opt-in via the ⎇ pill).
    #   • Any pill selected → show ONLY those types (so clicking ⎇ Commits
    #     surfaces commits, optionally combined with other selections).
    if _active_types:
        events = [ev for ev in events if ev["type"] in _active_types]
    else:
        events = [ev for ev in events if ev["type"] != "commit"]

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

    # ── Pagination: keep the DOM small even when hundreds of events match ──
    # Popovers + row HTML are built only for the visible slice, so paint cost
    # scales with page size, not the full filtered set.
    _events_filtered_total = len(events)
    _el_page, _el_start, _el_end = _render_pager(
        total=_events_filtered_total,
        page_size=_EL_PAGE_SIZE,
        page_key="_el_page_v1",
        unit_label="events",
        container_key="cc_el_pager_top",
    )
    if _events_filtered_total > _EL_PAGE_SIZE:
        events = events[_el_start:_el_end]

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
    _prisma_keys_t = tuple(sorted(_prisma_keys))
    _prisma_map  = _fetch_prismacloud(_prisma_keys_t) if _prisma_keys else {}
    _invicti_map = _fetch_invicti(_prisma_keys_t)     if _prisma_keys else {}
    _zap_map     = _fetch_zap(_prisma_keys_t)         if _prisma_keys else {}
    # Per-version build/release provenance for the event-log version popovers.
    _ver_meta_map = _fetch_version_meta(_prisma_keys_t) if _prisma_keys else {}

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

        # ── Compact 3-up security scan grid (Prismacloud + Invicti + ZAP) ──
        # Same shape as the inventory version popover so users build one
        # mental model. Each scanner is a vertical card with horizontal
        # severity rows showing inline Δ vs the live PRD version.
        _SCAN_SOURCES_EL = (
            ("prisma",  "Prismacloud", "⛟", "var(--cc-blue)",  _prisma_map,  True),
            ("invicti", "Invicti",     "⊛", "var(--cc-teal)",  _invicti_map, False),
            ("zap",     "ZAP",         "⌖", "var(--cc-amber)", _zap_map,     False),
        )

        def _el_scan_sev_rows(prefix: str, scan: dict,
                              baseline: dict | None) -> tuple[str, int]:
            _rows: list[str] = []
            _total = 0
            for _lvl, _lbl in _SEV_KEYS:
                _fld = f"{prefix}{_lvl}"
                _n = int(scan.get(_fld, 0) or 0)
                _total += _n
                _delta_html = ""
                if baseline is not None:
                    _d = _n - int(baseline.get(_fld, 0) or 0)
                    if _d > 0:
                        _delta_html = (
                            f'<span class="ap-scan-row-delta up" '
                            f'title="up vs prd">▲ +{_d}</span>'
                        )
                    elif _d < 0:
                        _delta_html = (
                            f'<span class="ap-scan-row-delta down" '
                            f'title="down vs prd">▼ {_d}</span>'
                        )
                    else:
                        _delta_html = (
                            '<span class="ap-scan-row-delta eq" '
                            'title="unchanged vs prd">=</span>'
                        )
                _rows.append(
                    f'<div class="ap-scan-row {_lvl}'
                    f'{" zero" if _n == 0 else " nonzero"}">'
                    f'  <span class="ap-scan-row-dot"></span>'
                    f'  <span class="ap-scan-row-name">{_lbl}</span>'
                    f'  <span class="ap-scan-row-num">{_n}</span>'
                    f'  {_delta_html}'
                    f'</div>'
                )
            return "".join(_rows), _total

        def _el_scan_card(name: str, glyph: str, color: str,
                          this_scan: dict | None,
                          prd_baseline: dict | None,
                          has_compliance: bool,
                          meta_html: str = "") -> str:
            if not this_scan:
                return (
                    f'<div class="ap-scan-card ap-scan-card--empty" '
                    f'style="--ap-scan-card-c:{color}">'
                    f'  <div class="ap-scan-card-head">'
                    f'    <span class="ap-scan-card-glyph">{glyph}</span>'
                    f'    <span class="ap-scan-card-name">{name}</span>'
                    f'  </div>'
                    f'  <div class="ap-scan-card-empty">No scan on record</div>'
                    f'</div>'
                )
            _stat  = this_scan.get("status", "") or ""
            _when  = fmt_dt(this_scan.get("when"), "%Y-%m-%d %H:%M") or ""
            _v_rows, _v_total = _el_scan_sev_rows("V", this_scan, prd_baseline)
            _delta_chip = (
                '<span class="ap-scan-card-delta-chip">Δ vs prd</span>'
                if prd_baseline is not None else ''
            )
            _card = (
                f'<div class="ap-scan-card" '
                f'style="--ap-scan-card-c:{color}">'
                f'  <div class="ap-scan-card-head">'
                f'    <span class="ap-scan-card-glyph">{glyph}</span>'
                f'    <span class="ap-scan-card-name">{name}</span>'
                + (f'<span class="ap-scan-card-status" '
                   f'title="{html.escape(_stat)}">'
                   f'{html.escape(_stat[:8])}</span>'
                   if _stat else '')
                + '  </div>'
                + (f'<div class="ap-scan-card-when">{_when}</div>'
                   if _when else '')
                + meta_html
                + '<div class="ap-scan-card-section">'
                + f'  <span>Vulnerabilities</span>'
                + f'  <span class="ap-scan-card-total">{_v_total}</span>'
                + _delta_chip
                + '</div>'
                + f'<div class="ap-scan-card-rows">{_v_rows}</div>'
            )
            if has_compliance:
                _c_rows, _c_total = _el_scan_sev_rows("C", this_scan, prd_baseline)
                _card += (
                    '<div class="ap-scan-card-section ap-scan-card-section--c">'
                    + f'  <span>Compliance</span>'
                    + f'  <span class="ap-scan-card-total">{_c_total}</span>'
                    + '</div>'
                    + f'<div class="ap-scan-card-rows">{_c_rows}</div>'
                )
            _card += '</div>'
            return _card

        def _el_dast_meta(src_key: str, scan: dict) -> str:
            _env  = (scan.get("environment") or "").strip()
            _url  = (scan.get("url") or "").strip()
            _info = int(scan.get("Informational") or 0)
            _bits: list[str] = []
            if _env:
                _bits.append(
                    f'<span class="ap-scan-card-env">'
                    f'{html.escape(_env.upper())}</span>'
                )
            if src_key == "invicti":
                _bp = int(scan.get("BestPractice") or 0)
                _bits.append(
                    f'<span class="ap-scan-card-aux" title="Best practice">'
                    f'BP <b>{_bp}</b></span>'
                )
            else:
                _fp = int(scan.get("FalsePositives") or 0)
                _bits.append(
                    f'<span class="ap-scan-card-aux" title="False positives">'
                    f'FP <b>{_fp}</b></span>'
                )
            _bits.append(
                f'<span class="ap-scan-card-aux" title="Informational">'
                f'INFO <b>{_info}</b></span>'
            )
            _meta = (
                '<div class="ap-scan-card-meta">' + "".join(_bits) + '</div>'
            )
            if _url:
                _short = _url
                if len(_short) > 38:
                    _short = _short[:35] + "…"
                _meta += (
                    f'<div class="ap-scan-card-url" '
                    f'title="{html.escape(_url)}">'
                    f'↗ {html.escape(_short)}</div>'
                )
            return _meta

        _scan_cards_el: list[str] = []
        for _src_key, _src_lbl, _src_glyph, _src_color, _src_map, _has_c in _SCAN_SOURCES_EL:
            _this = _src_map.get((_app, _ver))
            _baseline_src = (
                _src_map.get((_app, _prd_ver))
                if (_prd_ver and not _is_this_prd)
                else None
            )
            _meta = (
                _el_dast_meta(_src_key, _this)
                if _this and _src_key in ("invicti", "zap") else ""
            )
            _scan_cards_el.append(
                _el_scan_card(_src_lbl, _src_glyph, _src_color,
                              _this, _baseline_src, _has_c, _meta)
            )

        _section_note_el = (
            f'<span class="ap-section-note">Δ vs live · '
            f'<span class="cmp-pill">{_prd_ver}</span></span>'
            if (_prd_ver and not _is_this_prd)
            else (
                '<span class="ap-section-note ap-section-note--live">'
                '◉ this version is live</span>'
                if _is_this_prd else ''
            )
        )
        _prisma_block = (
            f'    <div class="ap-section ap-section--scan">'
            f'      <span>Security scans</span>{_section_note_el}'
            f'    </div>'
            f'    <div class="ap-scan-grid">' + "".join(_scan_cards_el) + '</div>'
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
            f'  <div class="ap-foot">Sources: ef-cicd-builds · ef-cicd-releases · ef-cicd-deployments · ef-cicd-prismacloud · ef-cicd-invicti · ef-cicd-zap</div>'
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
    _paging = _events_filtered_total > _EL_PAGE_SIZE
    if _paging:
        _visible_badge = (
            f"rows {_el_start + 1:,}–{_el_end:,} of {_events_filtered_total:,} "
            f"(of {_total_events_unfiltered:,} total)"
            if _active_types else
            f"rows {_el_start + 1:,}–{_el_end:,} of {_events_filtered_total:,}"
        )
    else:
        _visible_badge = (
            f"showing {_events_filtered_total:,} of {_total_events_unfiltered:,}"
            if _active_types else
            f"showing all {_events_filtered_total:,}"
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
def _fetch_inv_pulse(apps_json: str, days: int = 14,
                     exclude_test: bool = True) -> dict:
    """Daily build + PRD-deploy activity for the given application scope.

    ``exclude_test`` is included in the cache key so the toggle's two
    states have separate cached results. When True (default), only docs
    flagged ``testflag = "Normal"`` are counted; when False, every test
    run is included too. Builds and deployments are the two indices that
    carry ``testflag``; both are filtered uniformly.

    Returns ``{"build": [{"success", "failure", "other"}, ...],
    "deploy_prd": [counts]}`` with one entry per calendar day (oldest first).
    """
    _apps: list[str] = json.loads(apps_json)
    _empty = {
        "build":          [0] * days,
        "build_success":  [0] * days,
        "build_failure":  [0] * days,
        "deploy_prd":     [0] * days,
        "deploy_success": [0] * days,
        "deploy_failure": [0] * days,
    }
    if not _apps:
        return _empty
    _now = datetime.now(timezone.utc)
    _start = _now - timedelta(days=days)
    _testflag_clause = (
        [{"term": {"testflag": "Normal"}}] if exclude_test else []
    )
    # Builds — daily bucket with status breakdown
    try:
        _br = es_search(
            IDX["builds"],
            {
                "query": {"bool": {"filter": [
                    {"terms": {"application": _apps}},
                    range_filter("startdate", _start, _now),
                ] + _testflag_clause}},
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
                ] + _testflag_clause}},
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
                    }
                },
            },
            size=0,
        )
    except Exception:
        _dr = {}
    _dep_succ: list[int] = []
    _dep_fail: list[int] = []
    _dep_other: list[int] = []
    for _b in _dr.get("aggregations", {}).get("tl", {}).get("buckets", []):
        _ds = _df = _do = 0
        for _s in _b.get("s", {}).get("buckets", []):
            _k = _s.get("key") or ""
            _n = int(_s.get("doc_count") or 0)
            if _k in SUCCESS_STATUSES:
                _ds += _n
            elif _k in FAILED_STATUSES:
                _df += _n
            else:
                _do += _n
        _dep_succ.append(_ds)
        _dep_fail.append(_df)
        _dep_other.append(_do)
    # Pad to exactly ``days`` slots (histograms may return ±1 bucket depending
    # on bounds alignment).
    def _pad(xs: list[int]) -> list[int]:
        if len(xs) >= days:
            return xs[-days:]
        return [0] * (days - len(xs)) + xs
    return {
        "build_success":  _pad(_build_succ),
        "build_failure":  _pad(_build_fail),
        "build":          _pad([s + f + o for s, f, o in zip(_build_succ, _build_fail, _build_other)]),
        "deploy_success": _pad(_dep_succ),
        "deploy_failure": _pad(_dep_fail),
        "deploy_prd":     _pad([s + f + o for s, f, o in zip(_dep_succ, _dep_fail, _dep_other)]),
    }


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_jira_open(projects_json: str) -> dict:
    """Aggregate open Jira issues, scoped to a set of inventory projects.

    The ef-bs-jira-issues index has no ``application`` field — its schema
    is keyword-typed ``project`` / ``projectkey`` (Jira project keys),
    plus a ``components`` keyword and an unrelated ``remedyappname`` text
    field. We use ``project`` to scope to whatever subset of Jira projects
    overlap with the in-scope inventory projects, and fall back to a
    fleet-wide view when the two namespaces don't intersect.

    Open = ``status`` not in ``CLOSED_JIRA``. Returns
    ``{"total": int, "priority": {label: count}, "type": {label: count},
    "scope": "projects" | "fleet" | ""}``.
    """
    _projects: list[str] = json.loads(projects_json)
    _empty = {"total": 0, "priority": {}, "type": {}, "scope": ""}

    _aggs = {
        "by_priority": {"terms": {
            "field": "priority", "size": 20, "missing": "—",
        }},
        "by_type": {"terms": {
            "field": "issuetype", "size": 20, "missing": "—",
        }},
    }
    _must_not_closed = [{"terms": {"status": CLOSED_JIRA}}]

    def _extract(resp: dict | None) -> tuple[int, dict[str, int], dict[str, int]]:
        if not resp:
            return 0, {}, {}
        _hits = resp.get("hits") or {}
        _t = _hits.get("total")
        _total = (
            int(_t.get("value", 0)) if isinstance(_t, dict)
            else int(_t or 0)
        )
        _agg = resp.get("aggregations") or {}
        _by_p = {
            str(_b.get("key", "")): int(_b.get("doc_count") or 0)
            for _b in (_agg.get("by_priority") or {}).get("buckets", [])
        }
        _by_t = {
            str(_b.get("key", "")): int(_b.get("doc_count") or 0)
            for _b in (_agg.get("by_type") or {}).get("buckets", [])
        }
        return _total, _by_p, _by_t

    # ── Pass 1 — scope by project (preferred) ────────────────────────────
    if _projects:
        try:
            resp = es_search(
                IDX["jira"],
                {
                    "query": {"bool": {
                        "filter":   [{"terms": {"project": _projects}}],
                        "must_not": _must_not_closed,
                    }},
                    "aggs": _aggs,
                    "track_total_hits": True,
                },
                size=0,
            )
        except Exception:
            resp = None
        _total, _by_p, _by_t = _extract(resp)
        if _total > 0 or _by_p or _by_t:
            return {
                "total": _total,
                "priority": _by_p,
                "type": _by_t,
                "scope": "projects",
            }

    # ── Pass 2 — fleet-wide fallback ─────────────────────────────────────
    # Jira project keys (e.g. "PLAT") rarely match CI/CD inventory project
    # names (e.g. "platform-frontend") character-for-character, so the
    # project filter often returns zero. Surface the open-issue count for
    # the entire Jira instance instead of a misleading "0 / clean".
    try:
        resp = es_search(
            IDX["jira"],
            {
                "query": {"bool": {"must_not": _must_not_closed}},
                "aggs": _aggs,
                "track_total_hits": True,
            },
            size=0,
        )
    except Exception:
        return _empty
    _total, _by_p, _by_t = _extract(resp)
    if _total > 0 or _by_p or _by_t:
        return {
            "total": _total,
            "priority": _by_p,
            "type": _by_t,
            "scope": "fleet",
        }
    return _empty


def _svg_stacked_spark(success: list[int], failure: list[int]) -> str:
    """Daily stacked bars — success (green) on bottom, failure (red) on top."""
    if not success and not failure:
        return '<div class="iv-pulse-empty">no builds in 30d</div>'
    _W, _H = 240.0, 38.0
    _n = max(len(success), len(failure))
    if _n == 0:
        return '<div class="iv-pulse-empty">no builds in 30d</div>'
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
        return '<div class="iv-pulse-empty">no deploys in 30d</div>'
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


def _render_inventory_view(controls_slot, body_slot) -> None:
    """Pipelines inventory table — one row per registered pipeline.

    Output is split across two caller-supplied slots so the filter bar
    + stat tiles can live ABOVE the Inventory/Event-log tab group (both
    views inherit the same filters) while the project ribbon, pager,
    and pipeline table render inside the inventory tab itself.

    Not wrapped in @st.fragment — fragments forbid writing widgets into
    containers declared outside the fragment body, and the controls slot
    is a top-of-page st.empty() placeholder. Data fetches are
    @st.cache_data cached, so re-running on every widget change is cheap.
    """
    _ctrl_container = controls_slot.container()
    _body_container = body_slot.container()

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
    _iv_prisma_keys_t = tuple(sorted(_iv_prisma_keys))
    _iv_prisma_map  = _fetch_prismacloud(_iv_prisma_keys_t) if _iv_prisma_keys else {}
    _iv_invicti_map = _fetch_invicti(_iv_prisma_keys_t)     if _iv_prisma_keys else {}
    _iv_zap_map     = _fetch_zap(_iv_prisma_keys_t)         if _iv_prisma_keys else {}
    _iv_vermeta_map = _fetch_version_meta(_iv_prisma_keys_t) if _iv_prisma_keys else {}

    # ── Team extraction helper (inventory rows may carry multiple *_team fields) ─
    # For admins we surface every *_team field so the Teams tile reflects the
    # full ownership graph. For scoped roles we restrict the "teams" of a row
    # to just the values in that role's own team field (dev_team for
    # Developer, qc_team for QC, ops_team for Operations) — otherwise a
    # co-assigned team on a shared project would leak into the Team tile and
    # let the user pick teams they don't actually belong to.
    _iv_row_team_fields: list[str] = []
    if not _is_admin:
        _iv_row_team_fields = [
            _f.replace(".keyword", "")
            for _f in ROLE_TEAM_FIELDS.get(_effective_role, [])
        ]

    def _iv_row_teams(_r: dict) -> set[str]:
        """Team values on a row — role-scoped for non-admin users."""
        _out: set[str] = set()
        _teams_blob = _r.get("teams") or {}
        if _iv_row_team_fields:
            _iter = [
                (_f, _teams_blob.get(_f))
                for _f in _iv_row_team_fields
                if _f in _teams_blob
            ]
        else:
            _iter = list(_teams_blob.items())
        for _f, _tv in _iter:
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
    elif not _is_admin and len(_iv_session_teams) > 1:
        # Clamp any previously-persisted team selection to session_teams so
        # a leaked co-team value from a shared project can't widen the view.
        _legal_teams = set(_iv_session_teams)
        _prev_team_sel = list(st.session_state.get(_iv_filter_keys["team"]) or [])
        _clean_team_sel = [t for t in _prev_team_sel if t in _legal_teams]
        if _clean_team_sel != _prev_team_sel:
            st.session_state[_iv_filter_keys["team"]] = _clean_team_sel
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
    # Non-admins should never see team options beyond their own session
    # teams — even if a shared project surfaces co-assigned teams in its
    # inventory document. Strip the options dict down to the legal set.
    if not _is_admin and _iv_session_teams:
        _legal = set(_iv_session_teams)
        _iv_teams_opts = {t: c for t, c in _iv_teams_opts.items() if t in _legal}
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

    # ── Filter Console — every filter, view toggle, sort, and system action
    # is consolidated into a single popover here. The visible row carries:
    #   [ search input ] [ ⚙ Filter Console popover ] [ active chips ] [ Clear ]
    # …and the popover hosts two tabs:
    #   🎯 SCOPE — search recap + every dimension multiselect
    #   ⚙ VIEW & SYSTEM — time window, auto-refresh, admin toggles,
    #       sort, per-project view, clear cache
    # Stat tiles below are display-only — their popovers were retired so
    # widgets exist exactly once (no duplicate-key collisions).
    #
    # Everything from here through the Fleet-pulse strip is emitted into the
    # caller-provided controls_slot so it renders ABOVE the Inventory/Event-log
    # tab group — both views share the same filter state.
    _ctrl_container.__enter__()
    with st.container(key="cc_filter_secondary"):
        _iv_fb = st.columns([4.4, 1.7, 3.1, 0.8], vertical_alignment="center")

    # Dimension widget renderers — used inside the Filter Console popover.
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
            f'<div class="iv-fc-hint">{len(opts)} available · '
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
            f'<div class="iv-fc-hint">{len(opts)} available · '
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
            f'<div class="iv-fc-hint">{len(opts)} combinations available · '
            f'{len(_cur_keys)} selected</div>',
            unsafe_allow_html=True,
        )
        st.multiselect(
            "Pipeline combinations", options=_options, key=ss_key,
            label_visibility="collapsed",
            placeholder="Select build × deploy × platform combinations",
        )

    # ── Col 0: persistent search ─────────────────────────────────────────
    with _iv_fb[0]:
        st.text_input(
            "Search",
            key="shared_search_v1",
            placeholder="🔎  app · project · version · tech · person · detail…  (space-separated terms are AND)",
            help="Shared across event log and inventory · case-insensitive · "
                 "space-separated terms are AND",
            label_visibility="collapsed",
        )

    # ── Col 1: ⚙ Filter Console mega popover ─────────────────────────────
    with _iv_fb[1]:
        _console_badge = (
            f" · ✱{_iv_active_total}" if _iv_active_total else ""
        )
        with st.popover(
            f"⚙  Filter Console{_console_badge}",
            use_container_width=True,
            help="Every filter, view toggle, sort, and system action — "
                 "all consolidated here",
        ):
            st.markdown(
                '<div class="iv-fc-tagline">'
                '<span class="iv-fc-tagline-glyph">◆</span>'
                'One console — search, time, scope, sort, system.'
                '</div>',
                unsafe_allow_html=True,
            )
            _scope_tab, _view_tab = st.tabs([
                "🎯  SCOPE",
                "⚙  VIEW & SYSTEM",
            ])

            with _scope_tab:
                # Search recap so users always see what's active without
                # closing the popover. The actual input lives in col 0.
                _search_now = (st.session_state.get("shared_search_v1", "") or "").strip()
                if _search_now:
                    _search_recap = (
                        f'<div class="iv-fc-search-recap">'
                        f'<span class="iv-fc-search-glyph">🔎</span>'
                        f'<span class="iv-fc-search-label">Search</span>'
                        f'<code class="iv-fc-search-q">{html.escape(_search_now)}</code>'
                        f'</div>'
                    )
                else:
                    _search_recap = (
                        '<div class="iv-fc-search-recap iv-fc-search-recap--empty">'
                        '<span class="iv-fc-search-glyph">🔎</span>'
                        '<span class="iv-fc-search-label">Search</span>'
                        '<span class="iv-fc-search-q-empty">— none —</span>'
                        '</div>'
                    )
                st.markdown(_search_recap, unsafe_allow_html=True)

                _scope_l, _scope_r = st.columns(2, gap="medium")
                with _scope_l:
                    _admin_company_visible = (
                        _is_admin and (_iv_companies_opts or _sel_company)
                    )
                    if _admin_company_visible:
                        st.markdown(
                            '<div class="iv-fc-section">'
                            '<span class="iv-fc-section-glyph" '
                            'style="color:var(--cc-accent)">🏢</span>'
                            '<span class="iv-fc-section-label">Companies</span>'
                            '</div>', unsafe_allow_html=True)
                        _render_tile_ms("company", _iv_companies_opts,
                                        "Select companies")
                    elif not _is_admin and _iv_session_company:
                        st.markdown(
                            f'<div class="iv-fc-locked">'
                            f'<span class="iv-fc-locked-glyph">🏢</span>'
                            f'<span class="iv-fc-locked-label">Company</span>'
                            f'<span class="iv-fc-locked-val">{html.escape(_iv_session_company)}</span>'
                            f'<span class="iv-fc-locked-tag">scoped</span>'
                            f'</div>', unsafe_allow_html=True)

                    _team_admin_visible = (
                        _is_admin and (_iv_teams_opts or _sel_team)
                    )
                    _team_user_visible = (
                        (not _is_admin) and len(_iv_session_teams) > 1
                    )
                    if _team_admin_visible:
                        st.markdown(
                            '<div class="iv-fc-section">'
                            '<span class="iv-fc-section-glyph" '
                            'style="color:var(--cc-teal)">👥</span>'
                            '<span class="iv-fc-section-label">Teams</span>'
                            '</div>', unsafe_allow_html=True)
                        _render_tile_ms("team", _iv_teams_opts, "Select teams")
                    elif _team_user_visible:
                        st.markdown(
                            '<div class="iv-fc-section">'
                            '<span class="iv-fc-section-glyph" '
                            'style="color:var(--cc-teal)">👥</span>'
                            '<span class="iv-fc-section-label">Teams</span>'
                            '</div>', unsafe_allow_html=True)
                        _sess_opts = {
                            t: _iv_teams_opts.get(t, 0)
                            for t in _iv_session_teams
                        }
                        _render_tile_ms("team", _sess_opts,
                                        "Narrow your session teams")
                    elif (not _is_admin) and len(_iv_session_teams) == 1:
                        st.markdown(
                            f'<div class="iv-fc-locked">'
                            f'<span class="iv-fc-locked-glyph">👥</span>'
                            f'<span class="iv-fc-locked-label">Team</span>'
                            f'<span class="iv-fc-locked-val">{html.escape(_iv_session_teams[0])}</span>'
                            f'<span class="iv-fc-locked-tag">scoped</span>'
                            f'</div>', unsafe_allow_html=True)

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph" '
                        'style="color:var(--cc-blue)">📁</span>'
                        '<span class="iv-fc-section-label">Projects</span>'
                        '</div>', unsafe_allow_html=True)
                    _render_tile_ms("project", _iv_projects_opts,
                                    "Select projects")

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph" '
                        'style="color:var(--cc-green)">▣</span>'
                        '<span class="iv-fc-section-label">Applications</span>'
                        '</div>', unsafe_allow_html=True)
                    _render_tile_ms("app", _iv_apps_opts,
                                    "Select applications")

                with _scope_r:
                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph" '
                        'style="color:var(--cc-amber)">⚙</span>'
                        '<span class="iv-fc-section-label">Build stacks</span>'
                        '</div>', unsafe_allow_html=True)
                    _render_tile_pills("build", _iv_build_opts, "⚙")

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph" '
                        'style="color:var(--cc-teal)">⛭</span>'
                        '<span class="iv-fc-section-label">Deploy stacks</span>'
                        '</div>', unsafe_allow_html=True)
                    _render_tile_pills("deploy", _iv_deploy_opts, "⛭")

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph" '
                        'style="color:var(--cc-blue)">☁</span>'
                        '<span class="iv-fc-section-label">Deploy platforms</span>'
                        '</div>', unsafe_allow_html=True)
                    _render_tile_pills("platform", _iv_platform_opts, "☁")

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph" '
                        'style="color:var(--cc-red)">⇋</span>'
                        '<span class="iv-fc-section-label">Pipeline combos</span>'
                        '</div>', unsafe_allow_html=True)
                    _render_tile_combos(_iv_combo_opts)

            with _view_tab:
                _view_l, _view_r = st.columns(2, gap="medium")
                with _view_l:
                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph">🕐</span>'
                        '<span class="iv-fc-section-label">Time window</span>'
                        '</div>', unsafe_allow_html=True)
                    st.selectbox(
                        "Window", _TW_LABELS,
                        key="time_preset",
                        label_visibility="collapsed",
                        help="Query time window for admin analytics · the "
                             "event log carries its own scope",
                    )

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph">↕</span>'
                        '<span class="iv-fc-section-label">Sort order</span>'
                        '</div>', unsafe_allow_html=True)
                    st.selectbox(
                        "Sort by", _IV_SORT_OPTIONS, index=0,
                        key="iv_sort_v1",
                        label_visibility="collapsed",
                        help="Activity uses latest stage date · "
                             "vulnerabilities are weighted "
                             "(critical ≫ high ≫ medium ≫ low) on the PRD version",
                    )

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph">▦</span>'
                        '<span class="iv-fc-section-label">Layout</span>'
                        '</div>', unsafe_allow_html=True)
                    st.toggle(
                        "Per-project view", key="shared_per_project_v1",
                        help="Group rows into a separate table per project",
                    )

                with _view_r:
                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph">⚡</span>'
                        '<span class="iv-fc-section-label">Live</span>'
                        '</div>', unsafe_allow_html=True)
                    st.toggle(
                        "Auto-refresh (60s)", key="auto_refresh",
                        help="Rerun the page every 60 seconds",
                    )

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph">▣</span>'
                        '<span class="iv-fc-section-label">Pipeline data</span>'
                        '</div>', unsafe_allow_html=True)
                    st.toggle(
                        "Production runs only", key="exclude_test_runs",
                        help="Only count builds + deployments where "
                             "testflag = \"Normal\". Turn off to include "
                             "test-flagged runs (testflag = \"Test\").",
                    )

                    if _is_admin:
                        # Admin + CLevel both see the privileged toggles —
                        # they share full-fleet visibility. The label uses
                        # the role's icon/colour so executives don't see a
                        # confusing "Admin" header.
                        _adm_glyph = ROLE_ICONS.get(role_pick, "🛡")
                        _adm_color = ROLE_COLORS.get(role_pick, "var(--cc-accent)")
                        st.markdown(
                            f'<div class="iv-fc-section">'
                            f'<span class="iv-fc-section-glyph" '
                            f'style="color:{_adm_color}">{_adm_glyph}</span>'
                            f'<span class="iv-fc-section-label">{role_pick}</span>'
                            f'</div>', unsafe_allow_html=True)
                        st.toggle(
                            "View all projects", key="admin_view_all",
                            help="Bypass the default team scoping — see every project",
                        )
                        st.toggle(
                            "Exclude service accounts", key="exclude_svc",
                            help="Hide 'azure_sql' service-account commits",
                        )

                    st.markdown(
                        '<div class="iv-fc-section">'
                        '<span class="iv-fc-section-glyph">↻</span>'
                        '<span class="iv-fc-section-label">System</span>'
                        '</div>', unsafe_allow_html=True)
                    if st.button(
                        "↻ Clear cache & reload",
                        key="settings_reload",
                        use_container_width=True,
                        help="Drop cached query results and rerun from scratch",
                    ):
                        st.cache_data.clear()
                        st.rerun()

    # ── Col 2: active-filter chips summary ────────────────────────────────
    with _iv_fb[2]:
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
                '<div class="iv-filter-hint">No filters applied — open '
                '<b>Filter Console</b> to narrow the scope.</div>',
                unsafe_allow_html=True,
            )

    # ── Col 3: Clear button ──────────────────────────────────────────────
    with _iv_fb[3]:
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

    # ── Stat tiles — display-only metrics that mirror the current scope.
    # Filter widgets live exclusively in the Filter Console popover above
    # (so each session_state key backs exactly one widget — no duplicate-key
    # collisions). Tiles still glow + animate to draw the eye, and the
    # ✱<n> badge surfaces how many filters are active per dimension.
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
        # Tiles that collapse to the single selected value when exactly one
        # entry is picked. Combo stays numeric (its label is too long).
        _SINGLE_VAL_DIMS = {"company", "team", "project", "app",
                            "build", "deploy", "platform"}
        for _idx, (_dk, _glyph, _tlabel, _tnum, _tsub_md) in enumerate(_tile_specs):
            with _tile_cols[_idx]:
                _selected = _iv_active_sel.get(_dk, [])
                _nsel = len(_selected)
                _accent = _TILE_COLORS[_dk]
                _badge_html = (
                    f'<span class="iv-tile-badge">✱ {_nsel}</span>'
                    if _nsel else ''
                )
                if _nsel == 1 and _dk in _SINGLE_VAL_DIMS:
                    _solo = _selected[0]
                    if _dk in {"build", "deploy", "platform"}:
                        _solo = _pill_to_val(_solo)
                    _solo_esc = html.escape(str(_solo))
                    _number_html = (
                        f'<div class="iv-tile-number iv-tile-number--value" '
                        f'title="{_solo_esc}">{_solo_esc}</div>'
                    )
                else:
                    _number_html = f'<div class="iv-tile-number">{_tnum}</div>'
                _tile_html = (
                    f'<div class="iv-tile" '
                    f'style="--iv-stat-accent:{_accent}">'
                    f'<div class="iv-tile-head">'
                    f'<span class="iv-tile-glyph">{_glyph}</span>'
                    f'<span class="iv-tile-label">{_tlabel}</span>'
                    f'{_badge_html}'
                    f'</div>'
                    f'{_number_html}'
                    f'<div class="iv-tile-sub">{_tsub_md}</div>'
                    f'</div>'
                )
                with st.container(key=f"cc_tile_{_dk}"):
                    st.markdown(_tile_html, unsafe_allow_html=True)

    # ── Fleet pulse strip — four subtle visualizations of scope state ──────
    # 30d build + PRD-deploy success rates (twin stat block) + Jira open
    # issues + PRD freshness + security posture distribution bars. Every
    # tile reflects the current filtered scope.
    if _post_apps:
        _pulse = _fetch_inv_pulse(
            json.dumps(sorted(_post_apps)), days=30,
            exclude_test=bool(st.session_state.get("exclude_test_runs", True)),
        )
        _bs = _pulse.get("build_success", [])
        _bf = _pulse.get("build_failure", [])
        _ds = _pulse.get("deploy_success", [])
        _df = _pulse.get("deploy_failure", [])
        _bs_sum = sum(_bs)
        _bf_sum = sum(_bf)
        _ds_sum = sum(_ds)
        _df_sum = sum(_df)
        _b_total = _bs_sum + _bf_sum
        _d_total = _ds_sum + _df_sum
        # Per-stream rates (build / deploy)
        if _b_total:
            _rate_pct = _bs_sum / _b_total * 100
            _rate = f"{_rate_pct:.0f}"
        else:
            _rate_pct = 0.0
            _rate = "—"
        if _d_total:
            _drate_pct = _ds_sum / _d_total * 100
            _drate = f"{_drate_pct:.0f}"
        else:
            _drate_pct = 0.0
            _drate = "—"
        # Combined rate for the tile-level severity tag — degraded if EITHER
        # stream is unhealthy (we don't want a stellar build rate to hide a
        # deploy failure spike).
        _combined_pcts = [
            _r for _r, _t in ((_rate_pct, _b_total), (_drate_pct, _d_total)) if _t
        ]
        if _combined_pcts:
            _worst_pct = min(_combined_pcts)
            _rate_tag = (
                "ok" if _worst_pct >= 90
                else "warn" if _worst_pct >= 75
                else "crit"
            )
            _rate_tag_lbl = (
                "healthy" if _worst_pct >= 90
                else "watch" if _worst_pct >= 75
                else "degraded"
            )
        else:
            _rate_tag = ""
            _rate_tag_lbl = "quiet"

        # Jira open-issue rollup — only for roles that actually see Jira.
        # Scoped by project (Jira's `project` keyword) intersected with the
        # inventory projects currently in view; falls back to fleet-wide
        # when the two namespaces don't overlap.
        _jira_show = _ROLE_SHOWS_JIRA.get(_effective_role, False)
        if _jira_show:
            _jira = _fetch_jira_open(json.dumps(sorted(_post_projects)))
        else:
            _jira = {"total": 0, "priority": {}, "type": {}, "scope": ""}
        _jira_total: int = int(_jira.get("total") or 0)
        _jira_pri: dict[str, int] = dict(_jira.get("priority") or {})
        _jira_type: dict[str, int] = dict(_jira.get("type") or {})
        _jira_scope: str = str(_jira.get("scope") or "")
        # An empty scope means even the fleet-wide pass returned zero
        # buckets — the index is unreachable / empty / mapping mismatch.
        _jira_unmapped = (
            _jira_show
            and _jira_total == 0
            and not _jira_pri
            and not _jira_type
            and not _jira_scope
        )
        # Severity tag — surface highest/critical first, then high.
        _pri_lower = {k.lower(): v for k, v in _jira_pri.items()}
        _highest = (
            _pri_lower.get("highest", 0)
            + _pri_lower.get("blocker", 0)
            + _pri_lower.get("critical", 0)
        )
        _high = _pri_lower.get("high", 0)
        if _jira_unmapped:
            _jira_tag, _jira_tag_lbl = "warn", "field mismatch"
        elif _jira_total == 0 and _jira_show:
            _jira_tag, _jira_tag_lbl = "ok", "clean"
        elif _highest > 0:
            _jira_tag, _jira_tag_lbl = "crit", f"{_highest} blocker"
        elif _high > 0:
            _jira_tag, _jira_tag_lbl = "warn", f"{_high} high"
        elif _jira_show:
            _jira_tag, _jira_tag_lbl = "ok", f"{_jira_total} open"
        else:
            _jira_tag, _jira_tag_lbl = "", "n/a"

        # Priority distribution bar — ordered Highest → Lowest, missing last.
        # Map known priority labels to colors; everything else falls back to mute.
        _PRI_ORDER = [
            ("Highest",  "var(--cc-red)"),
            ("Blocker",  "var(--cc-red)"),
            ("Critical", "var(--cc-red)"),
            ("High",     "var(--cc-amber)"),
            ("Medium",   "var(--cc-blue)"),
            ("Low",      "var(--cc-teal)"),
            ("Lowest",   "var(--cc-text-mute)"),
        ]
        _pri_remaining = dict(_jira_pri)
        _pri_segments: list[tuple[int, str, str]] = []
        for _lbl, _color in _PRI_ORDER:
            _v = _pri_remaining.pop(_lbl, 0)
            if _v:
                _pri_segments.append((_v, _color, _lbl))
        # Anything else (e.g. "—" missing bucket, custom priorities) → mute
        for _lbl, _v in _pri_remaining.items():
            if _v:
                _pri_segments.append((_v, "var(--cc-text-mute)",
                                      _lbl if _lbl != "—" else "(no priority)"))
        if _jira_unmapped:
            _jira_bar_empty_msg = "Jira index empty or unreachable"
        elif _jira_show:
            _jira_bar_empty_msg = "no open issues"
        else:
            _jira_bar_empty_msg = "Jira hidden for role"
        _jira_bar = (
            _svg_dist_bar(_pri_segments) if _pri_segments
            else f'<div class="iv-pulse-empty">{_jira_bar_empty_msg}</div>'
        )

        # Type chip strip — top six types, biggest first; "—" rendered as
        # "(no type)". Each chip shows a glyph hint based on the label.
        _TYPE_GLYPH = {
            "bug":         "🐛",
            "story":       "✦",
            "task":        "▣",
            "epic":        "❖",
            "improvement": "↑",
            "incident":    "!",
            "support":     "⌥",
            "subtask":     "↳",
            "sub-task":    "↳",
        }
        _jira_type_html = ""
        if _jira_type:
            _ranked_types = sorted(
                _jira_type.items(), key=lambda kv: (-kv[1], kv[0].lower())
            )[:6]
            _chip_parts: list[str] = []
            for _tlbl, _tcnt in _ranked_types:
                _key = _tlbl.lower().strip()
                _glyph = _TYPE_GLYPH.get(_key, "·")
                _disp = _tlbl if _tlbl != "—" else "(no type)"
                _chip_parts.append(
                    f'<span class="iv-jira-chip">'
                    f'<span class="iv-jira-chip-g">{_glyph}</span>'
                    f'{html.escape(_disp)}'
                    f'<b>{_tcnt}</b>'
                    f'</span>'
                )
            _jira_type_html = (
                '<div class="iv-jira-types">' + "".join(_chip_parts) + '</div>'
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

        # Security posture — sum critical/high/medium/low across the PRD
        # version of every in-scope app, combining findings from THREE
        # scanners: Prismacloud (container), Invicti (DAST web), ZAP (DAST
        # OWASP). ZAP has no critical bucket — its high+medium+low add into
        # the totals normally and only the critical column reflects prisma +
        # invicti.
        _vc = 0; _vh = 0; _vm = 0; _vl = 0
        # Per-source aggregates so the tile can attribute findings to a
        # specific scanner. Each is an independent (V*, app-count) tuple.
        _src_totals = {
            "prisma":  {"vc": 0, "vh": 0, "vm": 0, "vl": 0, "apps": 0},
            "invicti": {"vc": 0, "vh": 0, "vm": 0, "vl": 0, "apps": 0},
            "zap":     {"vc": 0, "vh": 0, "vm": 0, "vl": 0, "apps": 0},
        }
        _apps_scanned: set[str] = set()  # any scanner has data
        _apps_with_ver = 0
        for _ap in _post_apps:
            _prd = _iv_prd_map.get(_ap) or {}
            _pv = (_prd.get("version") or "")
            if not _pv:
                continue
            _apps_with_ver += 1
            _did_scan = False
            for _src, _src_map in (
                ("prisma",  _iv_prisma_map),
                ("invicti", _iv_invicti_map),
                ("zap",     _iv_zap_map),
            ):
                _sc = _src_map.get((_ap, _pv))
                if not _sc:
                    continue
                _did_scan = True
                _src_totals[_src]["apps"] += 1
                _src_totals[_src]["vc"] += int(_sc.get("Vcritical") or 0)
                _src_totals[_src]["vh"] += int(_sc.get("Vhigh")     or 0)
                _src_totals[_src]["vm"] += int(_sc.get("Vmedium")   or 0)
                _src_totals[_src]["vl"] += int(_sc.get("Vlow")      or 0)
            if _did_scan:
                _apps_scanned.add(_ap)
                _vc += _src_totals["prisma"]["vc"] + _src_totals["invicti"]["vc"] + _src_totals["zap"]["vc"]
                # The sum-on-each-app-loop double-counts; reset using current
                # source totals after the loop instead. Continue here.
        # Recompute final totals from per-source aggregates (avoids the
        # progressive overcount inside the per-app loop above).
        _vc = sum(_src_totals[_s]["vc"] for _s in _src_totals)
        _vh = sum(_src_totals[_s]["vh"] for _s in _src_totals)
        _vm = sum(_src_totals[_s]["vm"] for _s in _src_totals)
        _vl = sum(_src_totals[_s]["vl"] for _s in _src_totals)
        _v_crit_high = _vc + _vh
        _apps_scanned_n = len(_apps_scanned)
        _sec_tag = (
            "crit" if _vc > 0
            else "warn" if _vh > 0
            else "ok" if _apps_scanned_n
            else ""
        )
        _sec_tag_lbl = (
            f"{_vc} crit" if _vc > 0
            else f"{_vh} high" if _vh > 0
            else "clean" if _apps_scanned_n
            else "unscanned"
        )
        _sec_bar = _svg_dist_bar([
            (_vc, "var(--cc-red)",       "critical"),
            (_vh, "var(--cc-amber)",     "high"),
            (_vm, "var(--cc-blue)",      "medium"),
            (_vl, "var(--cc-text-mute)", "low"),
        ])
        # Per-scanner attribution chip strip. Surfaces which tools actually
        # produced these findings so a "30 high" total isn't ambiguous.
        _SRC_META = {
            "prisma":  ("⛟",  "Prismacloud", "var(--cc-blue)"),
            "invicti": ("⊛",  "Invicti",     "var(--cc-teal)"),
            "zap":     ("⌖",  "ZAP",         "var(--cc-amber)"),
        }
        _sec_src_chips: list[str] = []
        for _src in ("prisma", "invicti", "zap"):
            _t = _src_totals[_src]
            _findings = _t["vc"] + _t["vh"] + _t["vm"] + _t["vl"]
            if _t["apps"] == 0:
                continue
            _glyph, _name, _color = _SRC_META[_src]
            _sec_src_chips.append(
                f'<span class="iv-sec-src" style="--iv-sec-src-c:{_color}">'
                f'<span class="iv-sec-src-g">{_glyph}</span>'
                f'<span class="iv-sec-src-n">{_name}</span>'
                f'<b>{_findings}</b>'
                f'<span class="iv-sec-src-apps">on {_t["apps"]} app{"s" if _t["apps"] != 1 else ""}</span>'
                f'</span>'
            )
        _sec_src_html = (
            '<div class="iv-sec-srcs">' + "".join(_sec_src_chips) + '</div>'
            if _sec_src_chips else ''
        )

        _spark_build = _svg_stacked_spark(_bs, _bf)

        # Tile 2 — Jira open issues. For roles that don't see Jira (Operations
        # today) we still render the tile so the strip layout stays balanced
        # but it announces "Jira hidden for role".
        _jira_scope_lbl = (
            "in scope projects" if _jira_scope == "projects"
            else "fleet-wide" if _jira_scope == "fleet"
            else ""
        )
        if not _jira_show:
            _jira_value_html = '<div class="iv-pulse-value">—</div>'
            _jira_sub_html = '<div class="iv-pulse-sub">role has no Jira visibility</div>'
        elif _jira_unmapped:
            _jira_value_html = '<div class="iv-pulse-value">?</div>'
            _jira_sub_html = (
                '<div class="iv-pulse-sub">'
                'Jira index empty or unreachable'
                '</div>'
            )
        elif _jira_total:
            _scope_pill = (
                f' · <span class="iv-jira-scope">{_jira_scope_lbl}</span>'
                if _jira_scope_lbl else ''
            )
            _jira_value_html = f'<div class="iv-pulse-value">{_jira_total}</div>'
            _jira_sub_html = (
                '<div class="iv-pulse-sub">priority breakdown · '
                f'<b>{len(_jira_type)}</b> issue type'
                f'{"s" if len(_jira_type) != 1 else ""}'
                f'{_scope_pill}'
                '</div>'
            )
        else:
            _jira_value_html = '<div class="iv-pulse-value">0</div>'
            _jira_sub_html = '<div class="iv-pulse-sub">no open issues</div>'

        # Build twin-stat block (Builds % | Deploys %) — replaces the old
        # single big number so build success and PRD-deploy success appear
        # side by side. The stacked spark below tracks the build stream
        # (deploys are quieter so they live in the inline meta lines).
        _twin_html = (
            '<div class="iv-pulse-twin">'
            # Builds
            '<div class="iv-pulse-twin-stat">'
            f'  <div class="iv-pulse-twin-rate">{_rate}'
            + ('<span class="iv-pulse-unit">%</span>' if _b_total else '')
            + '</div>'
            + '<div class="iv-pulse-twin-lbl">Builds</div>'
            + (
                f'<div class="iv-pulse-twin-meta">'
                f'<b>{_b_total}</b> · '
                f'<span class="iv-pulse-ok">{_bs_sum} ok</span> · '
                f'<span class="iv-pulse-fail">{_bf_sum} ✗</span>'
                f'</div>' if _b_total else
                '<div class="iv-pulse-twin-meta iv-pulse-twin-meta--quiet">no builds in 30d</div>'
            )
            + '</div>'
            # Deploys (PRD only)
            + '<div class="iv-pulse-twin-stat">'
            f'  <div class="iv-pulse-twin-rate">{_drate}'
            + ('<span class="iv-pulse-unit">%</span>' if _d_total else '')
            + '</div>'
            + '<div class="iv-pulse-twin-lbl">Deploys · PRD</div>'
            + (
                f'<div class="iv-pulse-twin-meta">'
                f'<b>{_d_total}</b> · '
                f'<span class="iv-pulse-ok">{_ds_sum} ok</span> · '
                f'<span class="iv-pulse-fail">{_df_sum} ✗</span>'
                f'</div>' if _d_total else
                '<div class="iv-pulse-twin-meta iv-pulse-twin-meta--quiet">no PRD deploys in 30d</div>'
            )
            + '</div>'
            + '</div>'
        )

        _pulse_html = (
            '<div class="iv-pulse-strip">'
            # Tile 1: Pipeline health (builds + deploys, 30d)
            '<div class="iv-pulse-tile" style="--iv-pulse-accent:'
            'linear-gradient(90deg,var(--cc-green),var(--cc-teal))">'
            '<div class="iv-pulse-label">'
            '<span>Pipeline health · 30d</span>'
            + (f'<span class="iv-pulse-tag {_rate_tag}">{_rate_tag_lbl}</span>'
               if _rate_tag else '')
            + '</div>'
            + _twin_html
            + _spark_build
            + '<div class="iv-pulse-axis"><span>30d ago</span><span>today</span></div>'
            + '</div>'
            # Tile 2: Jira open issues
            + '<div class="iv-pulse-tile iv-pulse-tile--jira" style="--iv-pulse-accent:'
              'linear-gradient(90deg,#2684ff,#7048e8)">'
            '<div class="iv-pulse-label">'
            '<span>Jira · open issues</span>'
            + (f'<span class="iv-pulse-tag {_jira_tag}">{_jira_tag_lbl}</span>'
               if _jira_tag_lbl else '')
            + '</div>'
            + _jira_value_html
            + _jira_sub_html
            + _jira_bar
            + _jira_type_html
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
            # Tile 4: Security posture (Prismacloud + Invicti + ZAP)
            + '<div class="iv-pulse-tile iv-pulse-tile--sec" style="--iv-pulse-accent:'
              'linear-gradient(90deg,var(--cc-red),var(--cc-amber))">'
            '<div class="iv-pulse-label">'
            '<span>Security posture</span>'
            + (f'<span class="iv-pulse-tag {_sec_tag}">{_sec_tag_lbl}</span>'
               if _sec_tag else '')
            + '</div>'
            + f'<div class="iv-pulse-value">{_v_crit_high}</div>'
            + f'<div class="iv-pulse-sub">crit + high · <b>{_apps_scanned_n}</b>/'
              f'<b>{_apps_with_ver}</b> PRD versions scanned (any source)</div>'
            + _sec_bar
            + _sec_src_html
            + '</div>'
            + '</div>'
        )
        st.markdown(_pulse_html, unsafe_allow_html=True)

    # End of controls_slot: filter bar, clickable stat tiles, and fleet pulse
    # are now emitted. Everything below this point lives inside the inventory
    # tab (body_slot), so the event-log tab renders independently alongside.
    _ctrl_container.__exit__(None, None, None)

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
        with _body_container:
            inline_note("No applications match the current filters.", "info")
        # Publish an empty scope so the sibling Event log tab shows
        # "no events" in the same scope — consistent with the filter-
        # inheritance contract.
        if _show_el:
            st.session_state["_el_inv_scope_apps"] = []
            st.session_state["_iv_total_v1"] = 0
        return

    # ── Pagination ─────────────────────────────────────────────────────────
    # Keep the un-sliced filtered set for anything that summarizes the whole
    # result (project ribbon, event-log scope publication, app_type map).
    # Only the table row HTML consumes the page slice, which is where the
    # render-time cost is concentrated.
    _inv_rows_filtered = _inv_rows
    _inv_total = len(_inv_rows_filtered)
    # Everything below this point renders inside the inventory tab (body_slot).
    _body_container.__enter__()
    _iv_page, _iv_start, _iv_end = _render_pager(
        total=_inv_total,
        page_size=_IV_PAGE_SIZE,
        page_key="_iv_page_v1",
        unit_label="pipelines",
        container_key="cc_iv_pager_top",
    )
    if _inv_total > _IV_PAGE_SIZE:
        _inv_rows = _inv_rows_filtered[_iv_start:_iv_end]

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
    # Use the full filtered set so version popovers (built from _inv_rows_all)
    # and paginated stage cells resolve their kind consistently.
    _iv_app_type_map = {
        r["application"]: (r.get("app_type") or "").strip().lower()
        for r in _inv_rows_filtered
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

    # ── Project-health ribbon — subtle landscape replacement ────────────────
    # One chip per project in the filtered inventory, colored by the worst
    # security tier across its apps. Clicking a chip opens the existing
    # project popover (teams + applications). This is the compact successor
    # to the old landscape treemap.
    # Walk the full filtered set (not the page slice) so the ribbon reflects
    # every project in scope, not just the ones visible on the current page.
    _pr_TIER_RANK = {"crit": 5, "high": 4, "med": 3, "low": 2, "clean": 1, "na": 0}
    _pr_by_proj: dict[str, dict] = {}
    for _r in _inv_rows_filtered:
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
        # Cap visible chips so a fleet of 80+ projects doesn't push the
        # table off-screen. Overflow surfaces as a "+N more" pill that
        # the horizontal scroller can still reach.
        _PR_VISIBLE_CAP = 24
        _pr_visible = _pr_sorted[:_PR_VISIBLE_CAP]
        _pr_overflow = len(_pr_sorted) - len(_pr_visible)
        _pr_chips: list[str] = []
        for _proj, _b in _pr_visible:
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
        if _pr_overflow > 0:
            _pr_chips.append(
                f'<span class="iv-pr-more" title="{_pr_overflow} more project'
                f'{"s" if _pr_overflow != 1 else ""} not shown — '
                f'narrow filters to surface them">+{_pr_overflow} more</span>'
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

        # ── Multi-source security scan grid (Prismacloud + Invicti + ZAP) ──
        # Mirrors the version popover's compact 3-up layout. Since the app
        # popover is anchored to the live PRD version, no Δ-vs-baseline is
        # needed — each scanner is a display-only card.
        _APP_SCAN_SOURCES = (
            ("prisma",  "Prismacloud", "⛟", "var(--cc-blue)",  _iv_prisma_map,  True),
            ("invicti", "Invicti",     "⊛", "var(--cc-teal)",  _iv_invicti_map, False),
            ("zap",     "ZAP",         "⌖", "var(--cc-amber)", _iv_zap_map,     False),
        )
        _APP_SEV_KEYS = [("critical", "Critical"), ("high", "High"),
                         ("medium", "Medium"), ("low", "Low")]

        def _app_scan_rows(prefix: str, scan: dict) -> tuple[str, int]:
            _rows: list[str] = []
            _total = 0
            for _lvl, _lbl in _APP_SEV_KEYS:
                _n = int(scan.get(f"{prefix}{_lvl}", 0) or 0)
                _total += _n
                _rows.append(
                    f'<div class="ap-scan-row {_lvl}'
                    f'{" zero" if _n == 0 else " nonzero"}">'
                    f'  <span class="ap-scan-row-dot"></span>'
                    f'  <span class="ap-scan-row-name">{_lbl}</span>'
                    f'  <span class="ap-scan-row-num">{_n}</span>'
                    f'</div>'
                )
            return "".join(_rows), _total

        def _app_dast_meta(src_key: str, scan: dict) -> str:
            _env  = (scan.get("environment") or "").strip()
            _url  = (scan.get("url") or "").strip()
            _info = int(scan.get("Informational") or 0)
            _bits: list[str] = []
            if _env:
                _bits.append(
                    f'<span class="ap-scan-card-env">'
                    f'{html.escape(_env.upper())}</span>'
                )
            if src_key == "invicti":
                _bp = int(scan.get("BestPractice") or 0)
                _bits.append(
                    f'<span class="ap-scan-card-aux" title="Best practice">'
                    f'BP <b>{_bp}</b></span>'
                )
            else:
                _fp = int(scan.get("FalsePositives") or 0)
                _bits.append(
                    f'<span class="ap-scan-card-aux" title="False positives">'
                    f'FP <b>{_fp}</b></span>'
                )
            _bits.append(
                f'<span class="ap-scan-card-aux" title="Informational">'
                f'INFO <b>{_info}</b></span>'
            )
            _meta = '<div class="ap-scan-card-meta">' + "".join(_bits) + '</div>'
            if _url:
                _short = _url if len(_url) <= 38 else _url[:35] + "…"
                _meta += (
                    f'<div class="ap-scan-card-url" '
                    f'title="{html.escape(_url)}">'
                    f'↗ {html.escape(_short)}</div>'
                )
            return _meta

        def _app_scan_card(name: str, glyph: str, color: str,
                           scan: dict | None, has_compliance: bool,
                           meta_html: str = "") -> str:
            if not scan:
                return (
                    f'<div class="ap-scan-card ap-scan-card--empty" '
                    f'style="--ap-scan-card-c:{color}">'
                    f'  <div class="ap-scan-card-head">'
                    f'    <span class="ap-scan-card-glyph">{glyph}</span>'
                    f'    <span class="ap-scan-card-name">{name}</span>'
                    f'  </div>'
                    f'  <div class="ap-scan-card-empty">No scan on record</div>'
                    f'</div>'
                )
            _stat = scan.get("status", "") or ""
            _when = fmt_dt(scan.get("when"), "%Y-%m-%d %H:%M") or ""
            _v_rows, _v_total = _app_scan_rows("V", scan)
            _card = (
                f'<div class="ap-scan-card" '
                f'style="--ap-scan-card-c:{color}">'
                f'  <div class="ap-scan-card-head">'
                f'    <span class="ap-scan-card-glyph">{glyph}</span>'
                f'    <span class="ap-scan-card-name">{name}</span>'
                + (f'<span class="ap-scan-card-status" '
                   f'title="{html.escape(_stat)}">'
                   f'{html.escape(_stat[:8])}</span>' if _stat else '')
                + '  </div>'
                + (f'<div class="ap-scan-card-when">{_when}</div>'
                   if _when else '')
                + meta_html
                + '<div class="ap-scan-card-section">'
                + f'  <span>Vulnerabilities</span>'
                + f'  <span class="ap-scan-card-total">{_v_total}</span>'
                + '</div>'
                + f'<div class="ap-scan-card-rows">{_v_rows}</div>'
            )
            if has_compliance:
                _c_rows, _c_total = _app_scan_rows("C", scan)
                _card += (
                    '<div class="ap-scan-card-section ap-scan-card-section--c">'
                    + f'  <span>Compliance</span>'
                    + f'  <span class="ap-scan-card-total">{_c_total}</span>'
                    + '</div>'
                    + f'<div class="ap-scan-card-rows">{_c_rows}</div>'
                )
            _card += '</div>'
            return _card

        _app_scan_cards: list[str] = []
        for _src_key, _src_lbl, _src_glyph, _src_color, _src_map, _has_c in _APP_SCAN_SOURCES:
            _scan_app = _src_map.get((_app, _prd_ver)) if _prd_ver else None
            _meta_app = (
                _app_dast_meta(_src_key, _scan_app)
                if _scan_app and _src_key in ("invicti", "zap") else ""
            )
            _app_scan_cards.append(
                _app_scan_card(_src_lbl, _src_glyph, _src_color,
                               _scan_app, _has_c, _meta_app)
            )

        if _prd_ver:
            _scan_section_note = (
                f'<span class="ap-section-note ap-section-note--live">'
                f'◉ live · <span class="cmp-pill">{_prd_ver}</span></span>'
            )
        else:
            _scan_section_note = (
                '<span class="ap-section-note">no live PRD version</span>'
            )
        _prisma_html = (
            f'    <div class="ap-section ap-section--scan">'
            f'      <span>Security scans</span>{_scan_section_note}'
            f'    </div>'
            f'    <div class="ap-scan-grid">'
            + "".join(_app_scan_cards) + '</div>'
        )

        # Team rows intentionally omitted — ownership is surfaced by the
        # project popover, which the project chip in the Identity section
        # links into. Duplicating it here just clutters the app view.

        _iv_popovers.append(
            f'<div id="{_pid}" popover="auto" class="el-app-pop is-app">'
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
            f'  <div class="ap-foot">Sources: ef-devops-inventory · ef-cicd-deployments · ef-cicd-prismacloud · ef-cicd-invicti · ef-cicd-zap</div>'
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

            # ── Per-scanner card builder (compact 3-column grid layout) ──────
            # Goal: surface every scanner's findings side-by-side with an
            # inline Δ vs the live (PRD) version. The previous full-width
            # vertical stack made the popover scroll past the viewport, so
            # each scanner now collapses into a fixed-width card with
            # horizontal severity rows.
            _SCAN_SOURCES = (
                # (key,        label,         glyph, color,            map,             has_compliance)
                ("prisma",  "Prismacloud", "⛟", "var(--cc-blue)",  _iv_prisma_map,  True),
                ("invicti", "Invicti",     "⊛", "var(--cc-teal)",  _iv_invicti_map, False),
                ("zap",     "ZAP",         "⌖", "var(--cc-amber)", _iv_zap_map,     False),
            )

            def _scan_sev_rows(prefix: str, scan: dict, baseline: dict | None) -> tuple[str, int]:
                """Render four horizontal severity rows (Crit/High/Med/Low) for
                the V or C field family. Each row carries its count and an
                inline Δ vs the live PRD baseline (when provided)."""
                _rows: list[str] = []
                _total = 0
                for _lvl, _lbl in _IV_SEV_KEYS:
                    _fld = f"{prefix}{_lvl}"
                    _n = int(scan.get(_fld, 0) or 0)
                    _total += _n
                    _delta_html = ""
                    if baseline is not None:
                        _d = _n - int(baseline.get(_fld, 0) or 0)
                        if _d > 0:
                            _delta_html = (
                                f'<span class="ap-scan-row-delta up" '
                                f'title="up vs prd">▲ +{_d}</span>'
                            )
                        elif _d < 0:
                            _delta_html = (
                                f'<span class="ap-scan-row-delta down" '
                                f'title="down vs prd">▼ {_d}</span>'
                            )
                        else:
                            _delta_html = (
                                '<span class="ap-scan-row-delta eq" '
                                'title="unchanged vs prd">=</span>'
                            )
                    _rows.append(
                        f'<div class="ap-scan-row {_lvl}'
                        f'{" zero" if _n == 0 else " nonzero"}">'
                        f'  <span class="ap-scan-row-dot"></span>'
                        f'  <span class="ap-scan-row-name">{_lbl}</span>'
                        f'  <span class="ap-scan-row-num">{_n}</span>'
                        f'  {_delta_html}'
                        f'</div>'
                    )
                return "".join(_rows), _total

            def _iv_scan_card(name: str, glyph: str, color: str,
                              this_scan: dict | None,
                              prd_baseline: dict | None,
                              has_compliance: bool,
                              meta_html: str = "") -> str:
                if not this_scan:
                    return (
                        f'<div class="ap-scan-card ap-scan-card--empty" '
                        f'style="--ap-scan-card-c:{color}">'
                        f'  <div class="ap-scan-card-head">'
                        f'    <span class="ap-scan-card-glyph">{glyph}</span>'
                        f'    <span class="ap-scan-card-name">{name}</span>'
                        f'  </div>'
                        f'  <div class="ap-scan-card-empty">No scan on record</div>'
                        f'</div>'
                    )
                _stat  = this_scan.get("status", "") or ""
                _when  = fmt_dt(this_scan.get("when"), "%Y-%m-%d %H:%M") or ""
                _v_rows, _v_total = _scan_sev_rows("V", this_scan, prd_baseline)
                _delta_chip = (
                    '<span class="ap-scan-card-delta-chip">Δ vs prd</span>'
                    if prd_baseline is not None else ''
                )
                _card = (
                    f'<div class="ap-scan-card" '
                    f'style="--ap-scan-card-c:{color}">'
                    f'  <div class="ap-scan-card-head">'
                    f'    <span class="ap-scan-card-glyph">{glyph}</span>'
                    f'    <span class="ap-scan-card-name">{name}</span>'
                    + (f'<span class="ap-scan-card-status" '
                       f'title="{html.escape(_stat)}">'
                       f'{html.escape(_stat[:8])}</span>'
                       if _stat else '')
                    + '  </div>'
                    + (f'<div class="ap-scan-card-when">{_when}</div>'
                       if _when else '')
                    + meta_html
                    + '<div class="ap-scan-card-section">'
                    + f'  <span>Vulnerabilities</span>'
                    + f'  <span class="ap-scan-card-total">{_v_total}</span>'
                    + _delta_chip
                    + '</div>'
                    + f'<div class="ap-scan-card-rows">{_v_rows}</div>'
                )
                if has_compliance:
                    _c_rows, _c_total = _scan_sev_rows("C", this_scan, prd_baseline)
                    _card += (
                        '<div class="ap-scan-card-section ap-scan-card-section--c">'
                        + f'  <span>Compliance</span>'
                        + f'  <span class="ap-scan-card-total">{_c_total}</span>'
                        + '</div>'
                        + f'<div class="ap-scan-card-rows">{_c_rows}</div>'
                    )
                _card += '</div>'
                return _card

            def _iv_dast_meta(src_key: str, scan: dict) -> str:
                """Compact one-line meta strip for DAST scanners (env + counts)
                plus an optional URL link below."""
                _env  = (scan.get("environment") or "").strip()
                _url  = (scan.get("url") or "").strip()
                _info = int(scan.get("Informational") or 0)
                _bits: list[str] = []
                if _env:
                    _bits.append(
                        f'<span class="ap-scan-card-env">'
                        f'{html.escape(_env.upper())}</span>'
                    )
                if src_key == "invicti":
                    _bp = int(scan.get("BestPractice") or 0)
                    _bits.append(
                        f'<span class="ap-scan-card-aux" title="Best practice">'
                        f'BP <b>{_bp}</b></span>'
                    )
                else:  # zap
                    _fp = int(scan.get("FalsePositives") or 0)
                    _bits.append(
                        f'<span class="ap-scan-card-aux" title="False positives">'
                        f'FP <b>{_fp}</b></span>'
                    )
                _bits.append(
                    f'<span class="ap-scan-card-aux" title="Informational">'
                    f'INFO <b>{_info}</b></span>'
                )
                _meta = (
                    '<div class="ap-scan-card-meta">' + "".join(_bits) + '</div>'
                )
                if _url:
                    _short = _url
                    if len(_short) > 38:
                        _short = _short[:35] + "…"
                    _meta += (
                        f'<div class="ap-scan-card-url" '
                        f'title="{html.escape(_url)}">'
                        f'↗ {html.escape(_short)}</div>'
                    )
                return _meta

            _scan_cards: list[str] = []
            for _src_key, _src_lbl, _src_glyph, _src_color, _src_map, _has_c in _SCAN_SOURCES:
                _this = _src_map.get((_app, _ver))
                _prd_b = (
                    _src_map.get((_app, _prd_ver))
                    if (_prd_ver and not _is_prd_ver)
                    else None
                )
                _meta = (
                    _iv_dast_meta(_src_key, _this)
                    if _this and _src_key in ("invicti", "zap") else ""
                )
                _scan_cards.append(
                    _iv_scan_card(_src_lbl, _src_glyph, _src_color,
                                  _this, _prd_b, _has_c, _meta)
                )

            # Header note clarifies what the inline Δ refers to so users
            # don't have to inspect each row's tooltip.
            _section_note = (
                f'<span class="ap-section-note">Δ vs live · '
                f'<span class="cmp-pill">{_prd_ver}</span></span>'
                if (_prd_ver and not _is_prd_ver)
                else (
                    '<span class="ap-section-note ap-section-note--live">'
                    '◉ this version is live</span>'
                    if _is_prd_ver else ''
                )
            )
            _prisma_block = (
                f'    <div class="ap-section ap-section--scan">'
                f'      <span>Security scans</span>{_section_note}'
                f'    </div>'
                f'    <div class="ap-scan-grid">' + "".join(_scan_cards) + '</div>'
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
                f'  <div class="ap-foot">Sources: ef-cicd-builds · ef-cicd-releases · ef-cicd-deployments · ef-cicd-prismacloud · ef-cicd-invicti · ef-cicd-zap</div>'
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
    _iv_visible_badge = (
        f"rows {_iv_start + 1:,}–{_iv_end:,} of {_inv_total:,}"
        if _inv_total > _IV_PAGE_SIZE
        else f"showing {_inv_total:,}"
    )
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
    # End of body_slot; the tab panel closes here.
    _body_container.__exit__(None, None, None)

    # ── Publish scope for the event-log tab ─────────────────────────────────
    # The event log lives in a sibling tab (rendered by the late-render block
    # below) and inherits every inventory filter via this session-state key.
    # Use the full filtered row set (not the page slice) so the event log
    # reflects every pipeline the filters match, regardless of which
    # pipeline inventory page is currently open.
    if _show_el:
        _el_scope_apps = sorted({
            r.get("application") or "" for r in _inv_rows_filtered
            if r.get("application")
        })
        st.session_state["_el_inv_scope_apps"] = _el_scope_apps
    # Publish pipeline count so the tab header can show a live badge.
    st.session_state["_iv_total_v1"] = _inv_total


# ── Late render into the top-of-page slot ─────────────────────────────────
# Both data surfaces live inside a custom-styled tab group. The inventory
# tab is rendered first (it publishes the app-scope set via
# st.session_state["_el_inv_scope_apps"]), and the event-log tab consumes
# that scope — Streamlit renders both tab contents on every run, so the
# scope is always current when the event log fragment executes, regardless
# of which tab is visible.
if _show_inv and _inventory_slot is not None:
    # Slot A is now the page-level _iv_top_controls_slot (sibling of the
    # inventory slot). Reusing the same name keeps the renderer call below
    # unchanged — the actual st.empty() target is just located higher in
    # the DOM so position:sticky has the page scroll as its containing
    # block.
    _iv_controls_slot = _iv_top_controls_slot
    with _inventory_slot.container():

        # Live tab badges reflect the last fragment run. On the first run of
        # a session the counters may be zero; they stabilize on the next
        # refresh once the fragment has published them to session_state.
        _iv_badge_n = int(st.session_state.get("_iv_total_v1", 0) or 0)
        _el_badge_n = len(st.session_state.get("_el_inv_scope_apps") or [])
        _iv_badge_txt = (
            f"  ·  {_iv_badge_n:,}" if _iv_badge_n else ""
        )
        _el_badge_txt = (
            f"  ·  {_el_badge_n:,} apps" if _el_badge_n else ""
        )
        with st.container(key="cc_surface_tabs"):
            _tab_inv, _tab_log = st.tabs([
                f"❖  PIPELINES INVENTORY{_iv_badge_txt}",
                f"⧗  EVENT LOG{_el_badge_txt}",
            ])
            with _tab_inv:
                st.markdown(
                    '<div class="cc-panel-sub" style="margin:0 0 6px 0">'
                    'One row per registered pipeline · PRD liveness · security '
                    'posture · click any chip for project / app / version detail'
                    '</div>',
                    unsafe_allow_html=True,
                )
                # Slot B: ribbon + pager + the pipeline table itself.
                _iv_body_slot = st.empty()
            with _tab_log:
                st.markdown(
                    '<div class="cc-panel-sub" style="margin:0 0 6px 0">'
                    'Builds · deployments · releases · requests · commits — '
                    'newest first · click any row for details · scope mirrors '
                    'every filter applied in the Inventory tab'
                    '</div>',
                    unsafe_allow_html=True,
                )
                # Slot C: event log body — drawn retroactively AFTER the
                # inventory fragment publishes `_el_inv_scope_apps`, so the
                # event log always reflects the current filter state.
                _el_slot = st.empty()

        # Run the inventory fragment first — it emits into slots A + B and
        # publishes the scope keys the event log needs.
        _render_inventory_view(_iv_controls_slot, _iv_body_slot)

        # Now the event log reads a fresh scope and fills slot C.
        with _el_slot.container():
            _render_event_log()
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
# GLOSSARY — admin-only. Non-admin roles never need to know which ES indices
# back the dashboard, so the field guide stays hidden from them.
# =============================================================================

if _is_admin:
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

**ef-cicd-prismacloud** — container-image scan results. Per `(application,
codeversion)` pair: `Vcritical` / `Vhigh` / `Vmedium` / `Vlow` (vulnerabilities)
plus `Ccritical` / `Chigh` / `Cmedium` / `Clow` (compliance), `imageName`,
`imageTag`, `enddate`.

**ef-cicd-invicti** — DAST web-app scan (Invicti). Per `(application,
codeversion)`: `Vcritical` / `Vhigh` / `Vmedium` / `Vlow`, plus `BestPractice`
and `Informational` counts, `environment`, `url`, `enddate`.

**ef-cicd-zap** — DAST web-app scan (OWASP ZAP). Per `(application,
codeversion)`: `Vhigh` / `Vmedium` / `Vlow` (no critical bucket) plus
`Informational` and `FalsePositives`, `environment`, `url`, `enddate`.

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
