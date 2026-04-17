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

_ALL_MY_TEAMS = "— All my teams —"

with _cb1[1]:
    # Teams come from session state; for non-Admin roles the user is confined
    # to their session teams. Admin may optionally scope to any inventory team.
    if role_pick == "Admin":
        _admin_teams = _session_teams or _load_teams_for_role("Admin")
        team_pick = st.selectbox("Team", [_ALL] + _admin_teams, index=0, key="team_pick",
                                 help="Admin: optionally filter to a specific team")
        team_filter = "" if team_pick == _ALL else team_pick
        _active_teams: list[str] = [team_filter] if team_filter else []
    elif _session_teams:
        if len(_session_teams) > 1:
            team_pick = st.selectbox(
                "Team", [_ALL_MY_TEAMS] + _session_teams, index=0, key="team_pick",
                help="Scope to a single team, or leave on 'all my teams' for a union view",
            )
            if team_pick == _ALL_MY_TEAMS:
                team_filter = ""                        # union of all session teams
                _active_teams = list(_session_teams)
            else:
                team_filter = team_pick
                _active_teams = [team_pick]
        else:
            # Exactly one team → auto-selected, rendered read-only
            team_pick = _session_teams[0]
            st.markdown(
                f'<div style="padding-top:6px;font-size:.68rem;text-transform:uppercase;'
                f'letter-spacing:.10em;color:var(--cc-text-mute);font-weight:600">Team</div>'
                f'<div style="font-size:.90rem;font-weight:600;color:var(--cc-text)">'
                f'{team_pick}</div>',
                unsafe_allow_html=True,
            )
            team_filter = team_pick
            _active_teams = [team_pick]
    else:
        # Non-Admin with no session teams — render informational placeholder.
        team_filter = ""
        _active_teams = []
        st.markdown('<div style="padding-top:6px;font-size:.68rem;text-transform:uppercase;'
                    'letter-spacing:.10em;color:var(--cc-text-mute);font-weight:600">Team</div>'
                    '<div style="font-size:.90rem;color:var(--cc-text-mute)">No team assigned</div>',
                    unsafe_allow_html=True)

# Resolve team → application list for scope filtering
if team_filter:
    # When Admin scopes to a team, query all team fields (any role assignment counts)
    if role_pick == "Admin":
        _admin_team_apps: set[str] = set()
        for _r in ["Developer", "QC", "Operator"]:
            _admin_team_apps.update(_load_team_applications(_r, team_filter))
        _team_apps = sorted(_admin_team_apps)
    else:
        _team_apps = _load_team_applications(role_pick, team_filter)
elif role_pick != "Admin" and _active_teams:
    # Non-admin "all my teams" — union across every session team
    _union: set[str] = set()
    for _t in _active_teams:
        _union.update(_load_team_applications(role_pick, _t))
    _team_apps = sorted(_union)
else:
    _team_apps = []  # no team-based restriction

with _cb1[2]:
    # Company selector is Admin-only. Non-admins are confined to whatever
    # companies their team assignment covers — no company toggle shown.
    if role_pick == "Admin":
        _company_options = [_ALL] + _all_companies
        company_pick = st.selectbox(
            "Company", _company_options, index=0, key="company_pick",
            help=f"{len(_all_companies)} companies in inventory",
        )
        company_filter = "" if company_pick == _ALL else company_pick
    else:
        company_filter = ""
        st.markdown(
            '<div style="padding-top:6px;font-size:.68rem;text-transform:uppercase;'
            'letter-spacing:.10em;color:var(--cc-text-mute);font-weight:600">Company</div>'
            '<div style="font-size:.90rem;color:var(--cc-text-mute)">Scoped by team</div>',
            unsafe_allow_html=True,
        )

with _cb1[3]:
    # Project options are filtered to the role's team assignment via inventory.
    # Admin default: only projects where the admin's own team matches dev_team
    # (so admin views the same slice a developer on their team would see).
    # The "view everything" toggle bypasses that.
    admin_view_all = bool(st.session_state.get("admin_view_all", False))
    if role_pick == "Admin":
        if admin_view_all:
            _proj_scoped = _all_projects
            _proj_help = f"{len(_all_projects)} projects in inventory · view-everything ON"
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
                _proj_help = f"{len(_all_projects)} projects (no teams on admin session)"
    elif _active_teams:
        _proj_scoped = _load_projects_for_role_teams(role_pick, tuple(_active_teams))
        _proj_help = (
            f"{len(_proj_scoped)} project(s) where {role_pick.lower()} team ∈ "
            f"{', '.join(_active_teams)}"
        )
    else:
        _proj_scoped = []
        _proj_help = "No projects visible — no team assigned"
    _proj_options = [_ALL] + _proj_scoped
    project_pick = st.selectbox(
        "Project", _proj_options, index=0, key="project_pick", help=_proj_help,
    )
    project_filter = "" if project_pick == _ALL else project_pick

# For non-admin roles with no specific project picked, restrict queries to the
# role's visible projects. For Admin, apply the same scoping unless the
# "view everything" toggle is on.
_scoped_projects: list[str] = []
if not project_filter:
    if role_pick != "Admin":
        _scoped_projects = _proj_scoped
    elif not admin_view_all:
        _scoped_projects = _proj_scoped

with _cb1[4]:
    # Admin-only: lift the default dev_team scoping and see every project.
    if role_pick == "Admin":
        st.toggle(
            "View all", value=admin_view_all,
            help="Admin: bypass the default dev_team scoping and see every project & stage",
            key="admin_view_all",
        )
    else:
        auto_refresh = st.toggle("Auto", value=False, help="Auto-refresh every 60s", key="auto_refresh")

with _cb1[5]:
    if role_pick == "Admin":
        auto_refresh = st.toggle("Auto", value=False, help="Auto-refresh every 60s", key="auto_refresh")
    else:
        exclude_svc = st.toggle("Excl. svc", value=True,
                                help="Exclude service account 'azure_sql' from all commit displays",
                                key="exclude_svc")

with _cb1[6]:
    if role_pick == "Admin":
        exclude_svc = st.toggle("Excl. svc", value=True,
                                help="Exclude service account 'azure_sql'",
                                key="exclude_svc")
    else:
        if st.button("↻", help="Clear cache & reload", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

# Admin lacks a dedicated reload button slot — surface it inline below the row.
if role_pick == "Admin":
    _rel_cols = st.columns([0.1, 0.9])
    with _rel_cols[0]:
        if st.button("↻", help="Clear cache & reload", use_container_width=True, key="admin_reload"):
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
    # Builds are split by branch into Build-develop / Build-release — roles opt
    # into each subtype independently.
    "Admin":     ["Build-develop", "Build-release", "Deployments", "Releases", "Requests", "Commits"],
    # Developer: commits, all builds, and dev-env deployments.
    "Developer": ["Commits", "Build-develop", "Build-release", "Deployments"],
    # QC: qc-env deployments, releases, and the related approval queue.
    "QC":        ["Deployments", "Releases", "Requests"],
    # Operator: uat- and prd-env deployments, releases, and their request queue.
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
    # Developer stages per new RBAC: no requests — only commits/builds/dev deploys.
    "Developer": [],
    "QC":        ["qc", "request_deploy_qc", "request_promote"],
    # Operator now also sees release-promotion requests alongside uat/prd deploy approvals.
    "Operator":  ["uat", "prd", "request_deploy_uat", "request_deploy_prd", "request_promote"],
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

# ── Effective role (admin view-as support) + admin-only flag ────────────────
# Determined here so downstream section-gating can short-circuit before the
# HUD/KPIs render. Non-admin roles (incl. Admin viewing AS another role) only
# see the event log; the rest of the page is admin-exclusive.
_effective_role = role_pick
if role_pick == "Admin":
    _admin_view = st.session_state.get("admin_role_view", "Admin")
    if _admin_view in ("Admin", "Developer", "QC", "Operator"):
        _effective_role = _admin_view
_is_admin = (_effective_role == "Admin")

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

_start_local = start_dt.astimezone(DISPLAY_TZ)
_end_local   = end_dt.astimezone(DISPLAY_TZ)
_now_local   = now_utc.astimezone(DISPLAY_TZ)
_window_label = (
    "All-time" if preset == "All-time"
    else f"{_start_local:%Y-%m-%d %H:%M} → {_end_local:%Y-%m-%d %H:%M} {DISPLAY_TZ_LABEL}"
)
st.caption(
    f"{_window_label}  ·  bucket {interval}  ·  vs prior equal window  ·  "
    f"{_now_local:%H:%M} {DISPLAY_TZ_LABEL}"
    + ("  ·  ⊘ azure_sql excluded" if exclude_svc else "")
)


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

# ── Render HUD ── (admin only) ────────────────────────────────────────────────
if _is_admin:
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
if _is_admin:
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

if _tick_evts and _is_admin:
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
# Admin sees every section. All other roles (Developer / QC / Operator) are
# restricted to the event log, where their configured RBAC rules govern what
# rows they can see. This keeps operational context focused for non-admins.
_ROLE_PRIORITY_SECTIONS: dict[str, list[str]] = {
    "Admin":     ["eventlog", "inventory", "alerts", "landscape", "lifecycle", "pipeline", "workflow"],
    "Developer": ["eventlog", "inventory"],
    "QC":        ["eventlog", "inventory"],
    "Operator":  ["eventlog", "inventory"],
}
# _effective_role and _is_admin are computed earlier (right after admin_role_view
# widget) so that the HUD/KPI blocks can be gated before they render.
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
    ("inventory", "Inventory", "#sec-inventory"),
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

    def _when_cell(ev: dict) -> str:
        """Render the When column as absolute timestamp + relative age.

        Two stacked lines: top = absolute (DISPLAY_TZ), bottom = "5h ago" /
        "3d ago" style tag so the reader sees recency at a glance without
        doing date-math in their head.
        """
        _abs = ev.get("When") or ""
        _rel = _relative_age(ev.get("_ts"))
        if not _abs and not _rel:
            return '<span style="color:var(--cc-text-mute);font-size:0.72rem">—</span>'
        _rel_html = (
            f'<div style="color:var(--cc-text-mute);font-size:0.68rem;'
            f'letter-spacing:.03em;margin-top:1px">{_rel}</div>'
            if _rel else ""
        )
        return (
            f'<div style="color:var(--cc-text-dim);font-size:0.78rem;'
            f'font-family:var(--cc-mono);line-height:1.15">{_abs}</div>'
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
        # release date + RLM + RLM status.
        _vmeta = _ver_meta_map.get((_app, _ver)) or {}
        _build_when_disp = fmt_dt(_vmeta.get("build_when"), "%Y-%m-%d %H:%M") or ""
        _rel_when_disp   = fmt_dt(_vmeta.get("release_when"), "%Y-%m-%d %H:%M") or ""
        _rlm_id   = _vmeta.get("rlm", "")
        _rlm_stat = _vmeta.get("rlm_status", "")
        _prov_block = (
            f'    <div class="ap-section">Version provenance</div>'
            f'    <span class="ap-k">Built ({DISPLAY_TZ_LABEL})</span>{_v(_build_when_disp)}'
        )
        if _rel_when_disp or _rlm_id or _rlm_stat:
            _prov_block += (
                f'    <span class="ap-k">Released ({DISPLAY_TZ_LABEL})</span>{_v(_rel_when_disp)}'
            )
            if _rlm_id:
                _prov_block += f'    <span class="ap-k">RLM</span>{_chip(_rlm_id)}'
            if _rlm_stat:
                _prov_block += f'    <span class="ap-k">RLM status</span>{_v(_rlm_stat)}'

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
            f'<div style="overflow-y:auto;max-height:{max_h};border:1px solid var(--cc-border);border-radius:10px">'
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


# ── Shared controls for the side-by-side event log + inventory panel ───────
# Both fragments read these out of session_state so users only set project /
# search / per-project once. A single search query feeds both views — each
# view narrows with its own haystack so "jane prd" finds Jane's prd deploys in
# events AND any inventory row that has jane in a team + prd in a platform/tag.
_SHARED_LAYOUT_CHOICES: dict[str, tuple[int, int]] = {
    "Events 75 / Inv 25": (3, 1),
    "Events 66 / Inv 33": (2, 1),
    "Balanced 50 / 50":   (1, 1),
    "Events 33 / Inv 66": (1, 2),
    "Events 25 / Inv 75": (1, 3),
}


def _render_shared_inv_el_controls(*, show_layout: bool) -> None:
    """Render shared Project · Search · Per-project (+ Layout) row.

    Writes session_state under the widgets' own keys (``shared_project_v1``,
    ``shared_search_v1``, ``shared_per_project_v1``, ``shared_layout_v1``).
    """
    _opts = [_ALL] + (_proj_scoped or [])
    _proj_default = 0
    if project_filter and project_filter in _opts:
        _proj_default = _opts.index(project_filter)

    # Layout chooser only renders when both views are visible; otherwise the
    # four-column grid stays compact: project · search · per-project · badge.
    _cols_spec = [1.3, 1.9, 1.0, 1.2] if not show_layout else [1.2, 1.7, 0.95, 1.1, 1.2]
    _r = st.columns(_cols_spec)
    with _r[0]:
        _proj = st.selectbox(
            "Project", _opts, index=_proj_default, key="shared_project_v1",
            help="Shared between event log and inventory — 'All' falls back to the global scope",
        )
    with _r[1]:
        st.text_input(
            "Search", key="shared_search_v1",
            placeholder="app · project · version · tech · person · detail…",
            help="Shared across both views · case-insensitive · "
                 "space-separated terms are AND · matches every string field "
                 "(each view narrows with its own haystack)",
        )
    with _r[2]:
        st.toggle(
            "Per-project tables", value=False, key="shared_per_project_v1",
            help="Group rows into a separate table per project in both views",
        )
    _badge_col = _r[-1]
    if show_layout:
        with _r[3]:
            st.select_slider(
                "Layout",
                options=list(_SHARED_LAYOUT_CHOICES.keys()),
                value="Balanced 50 / 50",
                key="shared_layout_v1",
                help="Adjust the width split — slide right to give inventory more room",
            )
    with _badge_col:
        st.markdown(
            f'<div style="font-size:.65rem;color:var(--cc-text-mute);letter-spacing:.06em;'
            f'text-transform:uppercase;font-weight:600;margin-top:26px;white-space:nowrap">'
            f'↻ {datetime.now(DISPLAY_TZ).strftime("%H:%M:%S")} {DISPLAY_TZ_LABEL}</div>',
            unsafe_allow_html=True,
        )


def _shared_project_filter() -> str:
    """Resolve the shared project selector to a filter string ("" = all)."""
    _v = st.session_state.get("shared_project_v1", _ALL)
    return "" if _v == _ALL else _v


def _shared_search_query() -> str:
    """Resolve the shared search box to a lowercased, stripped query."""
    return (st.session_state.get("shared_search_v1", "") or "").strip().lower()


def _shared_per_project() -> bool:
    """Resolve the shared per-project-tables toggle."""
    return bool(st.session_state.get("shared_per_project_v1", False))


def _shared_layout_ratio() -> tuple[int, int]:
    """Resolve the shared layout ratio for the side-by-side split."""
    _lbl = st.session_state.get("shared_layout_v1", "Balanced 50 / 50")
    return _SHARED_LAYOUT_CHOICES.get(_lbl, (1, 1))


_show_el = _show("eventlog")
_show_inv = _show("inventory")

if _show_el or _show_inv:
    st.markdown('<a class="anchor" id="sec-eventlog"></a>', unsafe_allow_html=True)
    st.markdown('<a class="anchor" id="sec-inventory"></a>', unsafe_allow_html=True)

    _el_hint = {
        "Admin":     "builds (dev/rel) · deployments · releases · requests · commits — full visibility, toggle scope via ‘view all’",
        "Developer": "commits · dev/rel builds · dev deployments — scoped to projects where your team owns dev",
        "QC":        "QC deployments + requests · releases + requests — scoped to projects where your team owns QC",
        "Operator":  "UAT/PRD deployments + requests · releases + requests — scoped to projects where your team owns UAT/PRD",
    }.get(_effective_role, "all event types")

    _combined_title = (
        "Event log &amp; Application inventory" if (_show_el and _show_inv)
        else ("Event log" if _show_el else "Application inventory")
    )
    _combined_hint = (
        f"{_el_hint} &mdash; paired with the live application inventory · "
        f"shared project, search &amp; per-project toggle"
    ) if (_show_el and _show_inv) else (
        f"{_el_hint} &mdash; newest first · auto-refreshes every minute"
        if _show_el else
        "One row per registered application · PRD liveness · security posture · click any chip for details"
    )
    _combined_badge = (
        f'{ROLE_ICONS[_effective_role]} Live · EL auto 60s · Inv auto 5m · {_effective_role}'
        if (_show_el and _show_inv) else
        f'{ROLE_ICONS[_effective_role]} Live · auto 60s · {_effective_role}'
        if _show_el else
        f'{ROLE_ICONS[_effective_role]} auto 5m · {_effective_role}'
    )
    st.markdown(
        f'<div class="section">'
        f'<div class="title-wrap"><h2>{_combined_title}</h2>'
        f'<span class="badge">{_combined_badge}</span></div>'
        f'<span class="hint">{_combined_hint}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    _render_shared_inv_el_controls(show_layout=(_show_el and _show_inv))

    if _show_el and _show_inv:
        _ratio = _shared_layout_ratio()
        _cols_panel = st.columns(list(_ratio), gap="large")
        with _cols_panel[0]:
            with st.expander("Event log (expand / collapse)", expanded=True):
                _render_event_log()
        with _cols_panel[1]:
            with st.expander("Application inventory (expand / collapse)", expanded=True):
                _render_inventory_view()
    elif _show_el:
        with st.expander("Event log (expand / collapse)", expanded=True):
            _render_event_log()
    else:
        with st.expander("Application inventory (expand / collapse)", expanded=True):
            _render_inventory_view()


# =============================================================================
# APPLICATION INVENTORY — one row per app, RBAC-scoped, below event log
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


@st.fragment(run_every="300s")
def _render_inventory_view() -> None:
    """Application inventory table — one row per registered application."""

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

    # Shared controls live above the combined panel — inventory only renders
    # its view-specific Sort selector next to the live-refresh badge.
    iv_project_filter = _shared_project_filter()
    iv_search = _shared_search_query()
    iv_per_project = _shared_per_project()

    _iv_r1 = st.columns([1.8, 1.0])
    with _iv_r1[0]:
        iv_sort = st.selectbox(
            "Sort by", _IV_SORT_OPTIONS, index=0, key="iv_sort_v1",
            help="Reorder applications — activity uses latest stage date · "
                 "vulnerabilities are weighted (critical ≫ high ≫ medium ≫ low) "
                 "on the version live in PRD",
        )
    with _iv_r1[1]:
        st.markdown(
            f'<div style="font-size:.65rem;color:var(--cc-text-mute);letter-spacing:.06em;'
            f'text-transform:uppercase;font-weight:600;margin-top:26px;white-space:nowrap">'
            f'↻ {datetime.now(DISPLAY_TZ).strftime("%H:%M:%S")} {DISPLAY_TZ_LABEL} · auto 5m</div>',
            unsafe_allow_html=True,
        )

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

    # Apply text search filter client-side — matches against every string-ish
    # inventory field so users can narrow by tech, platform, image, team, etc.
    # Space-separated terms are AND so "golang prd_team:jane" narrows
    # progressively. Each term is a plain lowercase substring match.
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

    # Prismacloud covers: prd-live version (baseline) + every version that
    # appears in any stage across every app (so each stage's popover can show
    # its own vulnerability tiles and compute Δ vs prd).
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
    # Per-version build date / release date / RLM — used inside the stage
    # version popover so each code version carries its own provenance.
    _iv_vermeta_map = _fetch_version_meta(tuple(sorted(_iv_prisma_keys))) if _iv_prisma_keys else {}

    # ── Group by technology / platform for pill filter ──────────────────────
    _iv_techs: dict[str, int] = {}
    _iv_deploy_techs: dict[str, int] = {}
    _iv_platforms: dict[str, int] = {}
    _iv_projects: dict[str, int] = {}
    for r in _inv_rows:
        _bt = r.get("build_technology") or ""
        if _bt:
            _iv_techs[_bt] = _iv_techs.get(_bt, 0) + 1
        _dt = r.get("deploy_technology") or ""
        if _dt:
            _iv_deploy_techs[_dt] = _iv_deploy_techs.get(_dt, 0) + 1
        _dp = r.get("deploy_platform") or ""
        if _dp:
            _iv_platforms[_dp] = _iv_platforms.get(_dp, 0) + 1
        _p = r.get("project") or "(none)"
        _iv_projects[_p] = _iv_projects.get(_p, 0) + 1

    _iv_total = len(_inv_rows)
    _iv_live = sum(1 for r in _inv_rows if (_iv_prd_map.get(r["application"]) or {}).get("live"))
    _iv_layout = "per-project" if iv_per_project else "consolidated"

    # ── Stats card ──────────────────────────────────────────────────────────
    _iv_live_pct = f"{_iv_live / _iv_total * 100:.0f}%" if _iv_total else "—"
    st.markdown(
        f'<div class="el-typefilter-head">'
        f'  <div class="el-tf-left">'
        f'    <div class="el-tf-total">{_iv_total}</div>'
        f'    <div class="el-tf-total-label">application{"s" if _iv_total != 1 else ""}</div>'
        f'  </div>'
        f'  <div class="el-tf-mid">'
        f'    <div class="el-tf-kicker">Application inventory</div>'
        f'    <div class="el-tf-hint">'
        f'      {_iv_live} live in PRD ({_iv_live_pct}) · '
        f'      {len(_iv_projects)} project{"s" if len(_iv_projects) != 1 else ""} · '
        f'      {len(_iv_techs)} build tech{"s" if len(_iv_techs) != 1 else ""} · '
        f'      {len(_iv_platforms)} deploy platform{"s" if len(_iv_platforms) != 1 else ""}'
        f'    </div>'
        f'  </div>'
        f'  <div class="el-tf-right">'
        f'    <span class="el-tf-badge layout">{_iv_layout}</span>'
        f'    <span class="el-tf-badge sort">{_IV_SORT_BADGES.get(iv_sort, "A → Z")}</span>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Tech / platform filter pills ────────────────────────────────────────
    # One pill-row per dimension (build tech, deploy tech, deploy platform).
    # Each row is self-labelled with a glyph + caption so users can tell them
    # apart at a glance without a bulky Streamlit field label.
    def _iv_pill_filter(
        *,
        field: str,
        counts: dict[str, int],
        caption: str,
        glyph: str,
        widget_key: str,
    ) -> None:
        """Render a pill filter row for ``field`` and narrow _inv_rows in place."""
        nonlocal _inv_rows
        if not counts:
            return
        _opts: list[str] = []
        _pill_to_val: dict[str, str] = {}
        for _val, _cnt in sorted(counts.items(), key=lambda x: -x[1]):
            _opt = f"{glyph} {_val} · {_cnt}"
            _opts.append(_opt)
            _pill_to_val[_opt] = _val
        st.markdown(
            f'<div class="iv-pill-caption">{caption}</div>',
            unsafe_allow_html=True,
        )
        _sel = st.pills(
            caption,
            options=_opts,
            selection_mode="multi",
            default=None,
            key=widget_key,
            label_visibility="collapsed",
        )
        _active = {_pill_to_val[o] for o in (_sel or [])}
        if _active:
            _inv_rows = [r for r in _inv_rows if (r.get(field) or "") in _active]

    _iv_pill_filter(
        field="build_technology", counts=_iv_techs,
        caption="Build technology", glyph="⚙",
        widget_key="iv_tech_pills_v1",
    )
    _iv_pill_filter(
        field="deploy_technology", counts=_iv_deploy_techs,
        caption="Deploy technology", glyph="⛭",
        widget_key="iv_deploy_tech_pills_v1",
    )
    _iv_pill_filter(
        field="deploy_platform", counts=_iv_platforms,
        caption="Deploy platform", glyph="☁",
        widget_key="iv_deploy_platform_pills_v1",
    )

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
        if _when:
            _rel_span = (
                f'<span class="iv-stage-rel"> · {_rel}</span>' if _rel else ""
            )
            _date_html = f'<div class="iv-stage-when">{_when}{_rel_span}</div>'
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
            f'<div style="overflow-y:auto;max-height:{max_h};border:1px solid var(--cc-border);border-radius:10px">'
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

        # Team rows
        _teams = r.get("teams") or {}
        _ordered_t = [k for k in ("dev_team", "qc_team", "uat_team", "prd_team") if k in _teams]
        _extras_t  = sorted(k for k in _teams.keys() if k not in _ordered_t)
        _team_html = ""
        for _f in _ordered_t + _extras_t:
            _vals = _teams.get(_f) or []
            if not _vals:
                continue
            _chips_t = "".join(f'<span class="ap-chip">{_tv}</span>' for _tv in _vals)
            _team_html += (
                f'<span class="ap-k">{_iv_team_label(_f)}</span>'
                f'<span class="ap-v" style="display:flex;flex-wrap:wrap;gap:4px">{_chips_t}</span>'
            )
        if not _team_html:
            _team_html = '<span class="ap-k">Teams</span><span class="ap-v empty">none recorded</span>'

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
            f'    <div class="ap-section">Teams</div>'
            f'    {_team_html}'
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
            _rlm_stat = _vmeta.get("rlm_status", "")
            _prov_rows = (
                f'    <div class="ap-section">Version provenance</div>'
                f'    <span class="ap-k">Built ({DISPLAY_TZ_LABEL})</span>{_iv_v(_build_when_disp)}'
            )
            if _rel_when_disp or _rlm_id or _rlm_stat:
                _prov_rows += (
                    f'    <span class="ap-k">Released ({DISPLAY_TZ_LABEL})</span>'
                    f'{_iv_v(_rel_when_disp)}'
                )
                if _rlm_id:
                    _prov_rows += (
                        f'    <span class="ap-k">RLM</span>{_iv_chip(_rlm_id)}'
                    )
                if _rlm_stat:
                    _prov_rows += (
                        f'    <span class="ap-k">RLM status</span>{_iv_v(_rlm_stat)}'
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


# Inventory rendering is handled alongside the event log above in the combined
# side-by-side panel — no standalone section here.


# =============================================================================
# ALERTS — compact chips, vivid colors
# =============================================================================

# _lc_classified is populated later in the lifecycle section; pre-init so
# alert popovers that reference it don't raise NameError on first render.

# Non-admin users see only the event log + inventory — halt before admin-only sections.
if not _is_admin:
    st.stop()

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
