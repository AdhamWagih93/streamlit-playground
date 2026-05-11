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

import base64
import html
import json
import os
import pathlib
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

# Inventory git source — lazy/optional dependencies. We import them up front so
# the availability flags can drive a single admin-visible status banner; the
# loader degrades gracefully when either is missing.
try:
    import yaml as _yaml  # PyYAML
    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _yaml = None  # type: ignore
    _YAML_AVAILABLE = False
try:
    from ansible.parsing.vault import VaultLib as _VaultLib, VaultSecret as _VaultSecret
    _ANSIBLE_VAULT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _VaultLib = _VaultSecret = None  # type: ignore
    _ANSIBLE_VAULT_AVAILABLE = False
try:
    import boto3 as _boto3  # S3-compatible client for the Prisma scan viewer
    from botocore.exceptions import (
        BotoCoreError as _BotoCoreError,
        ClientError as _BotoClientError,
    )
    _BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover
    _boto3 = None  # type: ignore
    _BotoCoreError = _BotoClientError = Exception  # type: ignore
    _BOTO3_AVAILABLE = False
try:
    from utils.vault import VaultClient as _VaultClient  # platform vault SDK
    _VAULT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _VaultClient = None  # type: ignore
    _VAULT_AVAILABLE = False
# Postgres driver — psycopg v3 is preferred; fall back to v2 since both
# are common in this org. Either is fine for our read-only access.
try:
    import psycopg as _psycopg  # type: ignore  # v3
    _PSYCOPG_VARIANT = "v3"
    _POSTGRES_AVAILABLE = True
except ImportError:  # pragma: no cover
    try:
        import psycopg2 as _psycopg  # type: ignore
        _PSYCOPG_VARIANT = "v2"
        _POSTGRES_AVAILABLE = True
    except ImportError:
        _psycopg = None  # type: ignore
        _PSYCOPG_VARIANT = ""
        _POSTGRES_AVAILABLE = False
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
    # Per-app metadata: dev / qc URLs, Remedy product info, recommended
    # build & deploy image versions for outdated-image detection.
    "devops_projects": "ef-devops-projects",
}

CACHE_TTL = 300  # seconds — 5 minutes balances freshness vs cluster load
ES_TIMEOUT = 60  # seconds for individual search calls

# =============================================================================
# INVENTORY GIT SOURCE — primary; ES is fallback
# =============================================================================
# The CI/CD platform's source-of-truth inventory lives in an Azure DevOps git
# repo (one Ansible inventory per app, plus group_vars / host_vars trees with
# optional Ansible Vault encryption). Reading it directly is faster than the
# ES projection AND lets us write back later.
#
# Credentials come from the platform vault at the configured path. The shape
# matches the platform's existing GitHandler convention:
#
#     vc = VaultClient()
#     cfg = vc.read_all_nested_secrets("new_git")
#     hostname = cfg.get("ado", {}).get("hostname", "")
#     git_user = cfg.get("ado", {}).get("username", "")
#     password = cfg.get("ado", {}).get("password", "")
#
# Write-back commits (future) will be authored as the current dashboard
# operator — `st.session_state.username` / `st.session_state.email` are
# applied to the local repo's `user.name` / `user.email` config after each
# clone or fetch, so the service-account credentials only handle transport
# while the commits carry the real author.
GIT_VAULT_PATH = os.environ.get("GIT_VAULT_PATH", "new_git").strip()
# Ansible Vault password (for decrypting any vaulted YAML in the inventories
# repo) is a separate concern — kept env-driven for now since it isn't part
# of the GitHandler's vault entry.
ANSIBLE_VAULT_PASSWORD = os.environ.get("ANSIBLE_VAULT_PASSWORD", "")

INVENTORY_REPO_PATH = "/tmp/inventories"
INVENTORY_BRANCH = "main"
INVENTORY_SYNC_TTL = 60  # seconds between origin fetches
INVENTORY_REPO_URL_TEMPLATE = "http://{host}/DevOps/Platform/_git/inventories"

# =============================================================================
# JENKINS PIPELINE STATUS — smart-loaded panel
# =============================================================================
# Three pipelines drive the platform's build/deploy/promote loop:
#   CICD/Build              — kicks off an app build (project, application, branch)
#   CICD/Request_deploy     — opens a deploy request (project, application, env, version)
#   CICD/Request_promote    — opens a release request (project, application, version)
# We surface their reachability + last-build status + currently-running runs
# so an operator knows at a glance whether the platform is healthy and what's
# already queued. Triggering happens elsewhere (deferred); here we observe.
#
# Credentials come from the platform's vault via ``utils.vault.VaultClient``:
#
#     vc = VaultClient()
#     cfg = vc.read_all_nested_secrets("jenkins")
#     # cfg → {"host": ..., "public_name": ..., "username": ..., "api_token": ...}
#
# The vault path itself is env-driven so it can be retargeted per
# environment without code changes. Env-var creds are kept as a thin
# fallback so dev boxes / CI without vault access still light up.
JENKINS_VAULT_PATH = os.environ.get("JENKINS_VAULT_PATH", "jenkins").strip()
JENKINS_TIMEOUT = 10  # seconds — the panel calls 4 endpoints per refresh
JENKINS_TTL = 30      # seconds — how long status results are cached

# =============================================================================
# PRISMA SCAN VIEWER — S3-backed full-report HTML
# =============================================================================
# Each scanned (project, application, version) has a Prismacloud HTML report
# stored in an S3-compatible bucket (custom host + port — likely MinIO or a
# similar on-prem S3 service rather than AWS). We never list the bucket and
# never bulk-download — fetches are explicitly user-initiated through the
# Scan Viewer tab. Cached in-process for PRISMA_SCAN_TTL so a user flipping
# tabs doesn't re-pay the round trip.
#
# Connection details (host / port / access_key / secret_key) come from vault:
#
#     cfg = vc.read_all_nested_secrets(PRISMA_S3_VAULT_PATH)
#     # cfg → {"host": ..., "port": "443", "access_key": ..., "secret_key": ...}
#
# Bucket name and the object-key template stay env-driven — they aren't
# secrets, and they're typically per-environment configuration the operator
# wants to retarget without touching vault entries.
PRISMA_S3_VAULT_PATH = os.environ.get("PRISMA_S3_VAULT_PATH", "s3/prisma").strip()
PRISMA_S3_BUCKET = os.environ.get("PRISMA_S3_BUCKET", "").strip()
PRISMA_S3_KEY_PATTERN = os.environ.get(
    "PRISMA_S3_KEY_PATTERN",
    "prisma-scans/{project}/{application}/{version}.html",
).strip()
# S3-compatible services (MinIO etc.) don't really care about the region but
# boto3 won't sign requests without one. Default to us-east-1 unless an env
# var explicitly overrides — purely a boto3 plumbing concern.
PRISMA_S3_REGION = os.environ.get("PRISMA_S3_REGION", "us-east-1").strip()
PRISMA_SCAN_TTL = 600  # seconds — scans are immutable per version, longer TTL OK

# =============================================================================
# POSTGRES — authoritative devops_projects table (per-project teams)
# =============================================================================
# A small Postgres table holds the canonical (company, project) → teams
# mapping with consolidated ``ops_team`` (the team owning UAT / PRD /
# PREPROD). Useful for cross-referencing against the inventory, which
# carries per-environment ``uat_team`` / ``prd_team`` / ``preprod_team``
# fields — inventory MAY disagree internally across envs, and even when
# consistent it may disagree with the Postgres record.
#
# The dashboard always talks to the PRD postgres instance (the
# authoritative table). The vault entry lives at a single path with the
# usual host/port/database/username/password keys:
#
#     vc = VaultClient()
#     cfg = vc.read_all_nested_secrets("postgres")
#     # cfg → {host, port, database, username, password}
POSTGRES_VAULT_PATH = os.environ.get("POSTGRES_VAULT_PATH", "postgres").strip()
POSTGRES_TABLE = os.environ.get("POSTGRES_TABLE", "devops_projects").strip()
POSTGRES_CONNECT_TIMEOUT = 10  # seconds
POSTGRES_DATA_TTL = 300        # seconds


JENKINS_PIPELINES: dict[str, dict] = {
    "build": {
        "label":   "Build",
        "path":    "CICD/Build",
        "glyph":   "⚒",
        "params":  ("project", "application", "branch"),
        "summary": "Kicks off CI for an app's branch (developer / release).",
    },
    "deploy_request": {
        "label":   "Deploy request",
        "path":    "CICD/Request_deploy",
        "glyph":   "⇪",
        "params":  ("project", "application", "environment", "version"),
        "summary": "Opens a deploy request to push a version into an environment.",
    },
    "release_request": {
        "label":   "Release request",
        "path":    "CICD/Request_promote",
        "glyph":   "✦",
        "params":  ("project", "application", "version"),
        "summary": "Opens a release request to promote a version.",
    },
}

# Field-alias table — maps the canonical row field names the rest of the
# dashboard reads onto the variable keys you may find in the merged Ansible
# group_vars. The first key that resolves to a non-empty value wins. Order
# in each tuple = priority. Adjust freely once you share real samples.
_INV_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "company":           ("company",),
    "app_type":          ("app_type", "appType", "application_type", "type"),
    "build_technology":  ("build_technology", "buildTechnology", "build_tech"),
    "deploy_technology": ("deploy_technology", "deployTechnology", "deploy_tech"),
    "deploy_platform":   ("deploy_platform", "deployPlatform", "platform"),
    "build_image_name":  ("build_image_name", "build_image.name", "buildImageName"),
    "build_image_tag":   ("build_image_tag", "build_image.tag", "buildImageTag"),
    "deploy_image_name": ("deploy_image_name", "deploy_image.name", "deployImageName"),
    "deploy_image_tag":  ("deploy_image_tag", "deploy_image.tag", "deployImageTag"),
}
# Environments we'll merge group_vars/{env}_{app} for. Same set used by
# the existing dashboard stages.
_INV_ENVIRONMENTS = ("dev", "qc", "uat", "prd")

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
/* Outdated-image chip rendered inline next to the build / deploy
   image-tag rows in the application popover. Amber so it reads as
   advisory, not error. */
.el-app-pop .ap-outdated-chip {
    display: inline-block;
    margin-left: 8px;
    padding: 1px 7px;
    font-family: var(--cc-mono);
    font-size: .60rem;
    font-weight: 700;
    letter-spacing: .04em;
    color: var(--cc-amber);
    background: color-mix(in srgb, var(--cc-amber) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--cc-amber) 35%, transparent);
    border-radius: 3px;
    vertical-align: middle;
    cursor: help;
}
/* "Recommended version" chip — green to signal "go here" */
.el-app-pop .ap-chip.ap-chip--rec {
    background: color-mix(in srgb, var(--cc-green) 12%, transparent);
    color: var(--cc-green);
    border: 1px solid color-mix(in srgb, var(--cc-green) 35%, transparent);
}
/* Stage URL link inside version popovers — monospace, accent-coloured,
   underline only on hover so it stays calm next to the other rows. */
.el-app-pop .ap-url {
    font-family: var(--cc-mono);
    font-size: .76rem;
    color: var(--cc-blue);
    text-decoration: none;
    word-break: break-all;
    transition: color .12s ease;
}
.el-app-pop .ap-url:hover {
    color: var(--cc-accent);
    text-decoration: underline;
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
/* Containerised app (OCP / K8s) is missing Prismacloud scan data for this
   version — an actual gap. Switches the chip to an amber warning palette
   so it stands apart from the regular violet chips, without being as loud
   as a critical-severity treatment. */
.iv-stage-ver.is-no-scan {
    background: rgba(245, 158, 11, .12);
    color: #b45309;
    border-color: rgba(245, 158, 11, .42);
    box-shadow: inset 0 0 0 1px rgba(245, 158, 11, .18);
}
.iv-stage-ver.is-no-scan:hover {
    background: #f59e0b;
    color: #fff;
    border-color: #f59e0b;
}
.iv-stage-noscan {
    font-size: 0.74rem;
    line-height: 1;
    color: #b45309;
    flex-shrink: 0;
}
.iv-stage-ver.is-no-scan:hover .iv-stage-noscan {
    color: #fff;
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
/* Inventory data-source pill — admin-only telemetry. Sits as a quiet
 * teal capsule when the git checkout is healthy; flips amber + glyph
 * when we fall back to the ES projection. The dot pulses subtly on the
 * git path so the eye registers "live source" without distracting from
 * the table. Hidden entirely for non-admins (rendered conditionally). */
.iv-src-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 0 6px 0;
    flex-wrap: wrap;
}
.iv-src {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 3px 11px 3px 9px;
    border-radius: 999px;
    font-family: var(--cc-mono);
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    border: 1px solid transparent;
    transition: filter .15s, transform .15s;
}
.iv-src:hover { filter: brightness(1.04); transform: translateY(-0.5px); }
.iv-src.is-git {
    background: linear-gradient(135deg,
                rgba(13,148,136,.10), rgba(13,148,136,.04));
    border-color: rgba(13,148,136,.32);
    color: #0f766e;
}
.iv-src.is-es {
    background: linear-gradient(135deg,
                rgba(217,119,6,.12), rgba(217,119,6,.05));
    border-color: rgba(217,119,6,.42);
    color: #b45309;
}
.iv-src .iv-src-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: currentColor;
    flex-shrink: 0;
}
.iv-src.is-git .iv-src-dot {
    box-shadow: 0 0 0 0 currentColor;
    animation: ivSrcPulse 2.4s ease-in-out infinite;
}
@keyframes ivSrcPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(13,148,136,.45); }
    50%      { box-shadow: 0 0 0 5px rgba(13,148,136,0); }
}
.iv-src .iv-src-glyph {
    font-size: 0.78rem;
    line-height: 1;
}
.iv-src .iv-src-lbl {
    letter-spacing: 0.07em;
}
.iv-src .iv-src-stat {
    color: var(--cc-text-mute);
    font-weight: 500;
    text-transform: none;
    letter-spacing: 0.02em;
    margin-left: 2px;
}
.iv-src.is-es .iv-src-stat { color: #92400e; opacity: .92; }
.iv-src-warn {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 9px;
    border-radius: 999px;
    background: rgba(220,38,38,.07);
    border: 1px solid rgba(220,38,38,.32);
    color: var(--cc-red);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    cursor: help;
}

/* Expandable detail strip — shown alongside the quiet success pill when
 * git WORKED but emitted parse warnings. Open by default so admins can't
 * miss it on first render. */
.iv-src-detail {
    margin: 4px 0 10px 0;
    padding: 0;
    border: 1px solid rgba(217,119,6,.36);
    background: linear-gradient(90deg,
                rgba(217,119,6,.07), rgba(217,119,6,.02));
    border-radius: 10px;
    font-size: 0.8rem;
    color: #92400e;
}
.iv-src-detail summary {
    cursor: pointer;
    padding: 7px 12px;
    font-weight: 600;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 8px;
}
.iv-src-detail summary::-webkit-details-marker { display: none; }
.iv-src-detail summary::before {
    content: "▸";
    font-size: 0.7rem;
    transition: transform .12s;
    color: var(--cc-amber);
}
.iv-src-detail[open] summary::before { transform: rotate(90deg); }
.iv-src-detail-glyph {
    color: var(--cc-amber);
    font-size: 0.95rem;
}
.iv-src-detail summary em {
    color: var(--cc-text-mute);
    font-style: italic;
    font-weight: 500;
    margin-left: auto;
}
.iv-src-detail-list {
    margin: 0;
    padding: 4px 16px 10px 32px;
    font-family: var(--cc-mono);
    font-size: 0.72rem;
    color: #78350f;
    line-height: 1.55;
}
.iv-src-detail-list li {
    margin-bottom: 2px;
    word-break: break-all;
}
.iv-src-detail-overflow {
    color: var(--cc-text-mute);
    font-style: italic;
}

/* ── ES-FALLBACK ALARM BANNER ────────────────────────────────────────────
 * The user explicitly asked for the fallback case to be UNMISSABLE. This
 * is a full-width red/amber gradient block with a pulsing left stripe,
 * an explicit FALLBACK tag, the failing reason, a remediation hint, and
 * the full warning list. Hidden for non-admins. */
.iv-src-alarm {
    position: relative;
    display: flex;
    align-items: stretch;
    margin: 4px 0 12px 0;
    border: 1px solid rgba(220,38,38,.42);
    border-radius: 12px;
    background: linear-gradient(135deg,
                rgba(220,38,38,.10) 0%,
                rgba(217,119,6,.08) 60%,
                rgba(217,119,6,.04) 100%);
    box-shadow: 0 6px 18px rgba(220,38,38,.08);
    overflow: hidden;
}
.iv-src-alarm-stripe {
    flex: 0 0 5px;
    background: linear-gradient(180deg, #dc2626, #d97706);
    animation: ivSrcAlarmPulse 2.2s ease-in-out infinite;
}
@keyframes ivSrcAlarmPulse {
    0%, 100% { box-shadow: inset 0 0 0 0 rgba(220,38,38,.5); }
    50%      { box-shadow: inset 0 0 12px 0 rgba(220,38,38,.6); }
}
.iv-src-alarm-body {
    flex: 1;
    padding: 11px 14px 13px 14px;
    color: #7f1d1d;
}
.iv-src-alarm-head {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-bottom: 5px;
}
.iv-src-alarm-glyph {
    font-size: 1.1rem;
    color: var(--cc-red);
    animation: ivSrcAlarmGlyph 2.2s ease-in-out infinite;
}
@keyframes ivSrcAlarmGlyph {
    0%, 100% { opacity: .85; transform: scale(1); }
    50%      { opacity: 1;   transform: scale(1.1); }
}
.iv-src-alarm-tag {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    font-weight: 800;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #fff;
    background: var(--cc-red);
    padding: 2px 8px;
    border-radius: 4px;
    flex-shrink: 0;
}
.iv-src-alarm-title {
    font-family: var(--cc-sans);
    font-size: 0.92rem;
    font-weight: 700;
    color: #7f1d1d;
    letter-spacing: -0.005em;
    line-height: 1.3;
}
.iv-src-alarm-reason {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin: 6px 0 4px 0;
    font-size: 0.8rem;
    flex-wrap: wrap;
}
.iv-src-alarm-reason-k {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--cc-text-mute);
    font-weight: 700;
}
.iv-src-alarm-reason code {
    font-family: var(--cc-mono);
    background: rgba(255,255,255,.7);
    color: #7f1d1d;
    padding: 1px 7px;
    border-radius: 4px;
    border: 1px solid rgba(220,38,38,.30);
    font-size: 0.78rem;
    font-weight: 600;
    word-break: break-all;
}
.iv-src-alarm-hint {
    font-size: 0.8rem;
    color: var(--cc-text-dim);
    line-height: 1.5;
    margin-top: 4px;
}
.iv-src-alarm-hint code {
    font-family: var(--cc-mono);
    background: rgba(255,255,255,.85);
    color: var(--cc-red);
    padding: 0 5px;
    border-radius: 4px;
    font-size: 0.74rem;
    font-weight: 600;
    border: 1px solid rgba(220,38,38,.22);
}
.iv-src-alarm-warns {
    margin-top: 10px;
    padding: 8px 10px 10px 10px;
    background: rgba(255,255,255,.55);
    border: 1px dashed rgba(220,38,38,.30);
    border-radius: 8px;
    font-size: 0.74rem;
}
.iv-src-alarm-warns-head {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--cc-text-mute);
    font-weight: 700;
    margin-bottom: 4px;
}
.iv-src-alarm-warns ul {
    margin: 0;
    padding: 0 0 0 20px;
    font-family: var(--cc-mono);
    color: #78350f;
    line-height: 1.55;
}
.iv-src-alarm-warns li { word-break: break-all; margin-bottom: 1px; }

/* ── GIT DIAGNOSTIC PANEL ──────────────────────────────────────────────────
 * Monospace, terminal-style block rendered under the source-alarm banner
 * when the admin clicks 🔍 Diagnose git. Sections: git binary · vault read
 * shape · resolved URL (redacted) · filesystem snapshot · step-by-step
 * trace of the last sync attempt · final outcome. */
.gd-panel {
    margin: 10px 0 12px 0;
    border: 1px solid var(--cc-border-hi);
    border-radius: 12px;
    background: var(--cc-surface);
    overflow: hidden;
}
.gd-panel-head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    background: linear-gradient(90deg,
                rgba(79,70,229,.06), rgba(79,70,229,.01));
    border-bottom: 1px solid var(--cc-border);
}
.gd-panel-glyph { font-size: 1.05rem; color: var(--cc-accent); }
.gd-panel-title {
    font-family: var(--cc-mono);
    font-size: 0.78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--cc-text);
}
.gd-panel-ts {
    margin-left: auto;
    font-family: var(--cc-mono);
    font-size: 0.7rem;
    color: var(--cc-text-mute);
}
.gd-section {
    padding: 8px 14px 12px 14px;
    border-bottom: 1px dashed var(--cc-border);
}
.gd-section:last-child { border-bottom: none; }
.gd-section-title {
    font-family: var(--cc-mono);
    font-size: 0.64rem;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text-mute);
    font-weight: 700;
    margin-bottom: 6px;
}
.gd-section-title code {
    font-family: var(--cc-mono);
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    padding: 0 6px;
    border-radius: 4px;
    font-size: 0.66rem;
    text-transform: none;
    letter-spacing: 0;
}
.gd-kv {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 2px 14px;
    padding: 4px 0;
    font-size: 0.78rem;
}
.gd-kv--ok   { border-left: 3px solid rgba(5,150,105,.45);  padding-left: 8px; }
.gd-kv--warn { border-left: 3px solid rgba(217,119,6,.45);  padding-left: 8px; }
.gd-kv--bad  { border-left: 3px solid rgba(220,38,38,.50);  padding-left: 8px; }
.gd-kv-row {
    display: contents;
}
.gd-kv-k {
    font-family: var(--cc-mono);
    color: var(--cc-text-mute);
    font-size: 0.72rem;
}
.gd-kv-v {
    font-family: var(--cc-mono);
    color: var(--cc-text);
    font-weight: 600;
    word-break: break-all;
}
.gd-url {
    margin: 0;
    padding: 6px 10px;
    background: var(--cc-surface2);
    border: 1px solid var(--cc-border);
    border-radius: 8px;
    font-family: var(--cc-mono);
    font-size: 0.72rem;
    color: var(--cc-text);
    overflow-x: auto;
    word-break: break-all;
}
.gd-auth-note {
    margin-top: 6px;
    font-size: 0.7rem;
    color: var(--cc-text-dim);
    line-height: 1.5;
}
.gd-auth-note code {
    font-family: var(--cc-mono);
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    padding: 0 5px;
    border-radius: 4px;
    font-size: 0.68rem;
}
.gd-trace {
    display: flex;
    flex-direction: column;
    gap: 4px;
}
.gd-step {
    display: grid;
    grid-template-columns: 14px auto auto auto;
    grid-auto-rows: auto;
    column-gap: 8px;
    padding: 6px 10px;
    background: var(--cc-surface2);
    border: 1px solid var(--cc-border);
    border-radius: 8px;
    font-family: var(--cc-mono);
    font-size: 0.74rem;
    align-items: baseline;
}
.gd-step.ok    { border-color: rgba(5,150,105,.32); background: rgba(5,150,105,.04); }
.gd-step.bad   { border-color: rgba(220,38,38,.42); background: rgba(220,38,38,.04); }
.gd-step.warn  { border-color: rgba(217,119,6,.38); background: rgba(217,119,6,.04); }
.gd-step-glyph {
    font-weight: 700;
    font-size: 0.85rem;
    line-height: 1;
}
.gd-step.ok  .gd-step-glyph { color: var(--cc-green); }
.gd-step.bad .gd-step-glyph { color: var(--cc-red); }
.gd-step.warn .gd-step-glyph { color: var(--cc-amber); }
.gd-step-name {
    font-weight: 700;
    color: var(--cc-text);
    word-break: break-all;
}
.gd-step-rc {
    color: var(--cc-text-mute);
    font-size: 0.66rem;
}
.gd-step-dur {
    color: var(--cc-text-mute);
    font-size: 0.66rem;
    text-align: right;
}
.gd-step-head {
    margin-left: 8px;
    font-family: var(--cc-mono);
    font-size: 0.7rem;
    color: var(--cc-text-mute);
}
.gd-step-cmd {
    grid-column: 1 / -1;
    margin-top: 4px;
    padding: 4px 8px;
    background: rgba(0,0,0,.04);
    border-radius: 4px;
    color: var(--cc-text-dim);
    font-size: 0.7rem;
    word-break: break-all;
}
.gd-stderr,
.gd-stdout {
    grid-column: 1 / -1;
    margin: 4px 0 0 0;
    padding: 6px 10px;
    border-radius: 6px;
    font-family: var(--cc-mono);
    font-size: 0.7rem;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-x: auto;
    max-height: 180px;
}
.gd-stderr {
    background: rgba(220,38,38,.06);
    border: 1px solid rgba(220,38,38,.22);
    color: #7f1d1d;
}
.gd-stdout {
    background: rgba(5,150,105,.05);
    border: 1px solid rgba(5,150,105,.18);
    color: #065f46;
}

/* Source-selector radio strip — admin-only inline control above the
 * source pill / banner. Tight + horizontal so it reads as a toggle, not a
 * full form. */
.st-key-cc_inv_src_pref {
    margin: 0 0 6px 0;
}
.iv-src-pref-lbl {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text-mute);
    padding-top: 6px;
}
.st-key-cc_inv_src_pref [role="radiogroup"] {
    gap: 14px !important;
}
.st-key-cc_inv_src_pref [role="radiogroup"] label {
    font-size: 0.78rem;
    color: var(--cc-text-dim);
}

/* ── SYNC CHECK PANEL ──────────────────────────────────────────────────────
 * Idle gate, summary tiles, only-in-X chip lists, and per-app diff cards.
 * ----------------------------------------------------------------------- */
.sync-gate {
    text-align: center;
    padding: 22px 22px 14px 22px;
    margin: 4px 0 12px 0;
    border-radius: 16px;
    background: linear-gradient(180deg,
                rgba(79,70,229,.05) 0%,
                rgba(79,70,229,.01) 100%);
    border: 1px dashed var(--cc-border-hi);
}
.sync-gate-glyph {
    font-size: 2.4rem;
    line-height: 1;
    color: var(--cc-accent);
    opacity: .82;
}
.sync-gate-title {
    font-family: var(--cc-mono);
    font-size: 0.82rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text);
    margin: 10px 0 6px 0;
}
.sync-gate-body {
    font-size: 0.82rem;
    color: var(--cc-text-dim);
    max-width: 560px;
    margin: 0 auto;
    line-height: 1.5;
    text-align: left;
}
.sync-gate-body ul {
    margin: 6px 0 2px 18px;
    padding: 0;
}
.sync-gate-body li { margin-bottom: 1px; }
.sync-gate-body b { color: var(--cc-accent); }

.sync-errs {
    background: rgba(220,38,38,.05);
    border: 1px solid rgba(220,38,38,.30);
    border-radius: 10px;
    padding: 8px 12px;
    margin: 6px 0 10px 0;
    font-size: 0.78rem;
    color: #991b1b;
}
.sync-errs-line { margin: 2px 0; word-break: break-word; }
.sync-errs-k {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 700;
    color: var(--cc-red);
    margin-right: 6px;
}
.sync-errs code {
    font-family: var(--cc-mono);
    background: rgba(255,255,255,.55);
    color: #7f1d1d;
    padding: 1px 6px;
    border-radius: 4px;
    font-weight: 600;
}

.sync-summary {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 8px;
    margin: 10px 0 12px 0;
    padding: 12px;
    border-radius: 14px;
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    transition: border-color .15s;
}
.sync-summary.is-clean { border-color: rgba(13,148,136,.36); }
.sync-summary.is-drift { border-color: rgba(217,119,6,.42); }
.sync-tile {
    text-align: center;
    padding: 8px 6px;
    background: var(--cc-surface2);
    border-radius: 10px;
    border: 1px solid var(--cc-border);
}
.sync-tile.is-only-git { border-color: rgba(13,148,136,.28); background: rgba(13,148,136,.04); }
.sync-tile.is-only-es  { border-color: rgba(37,99,235,.28);  background: rgba(37,99,235,.04); }
.sync-tile.is-field    { border-color: rgba(217,119,6,.34);  background: rgba(217,119,6,.04); }
.sync-tile-lbl {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--cc-text-mute);
    font-weight: 700;
}
.sync-tile-val {
    font-family: var(--cc-mono);
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--cc-text);
    line-height: 1.2;
    margin-top: 2px;
    font-variant-numeric: tabular-nums;
}
.sync-tile.is-only-git .sync-tile-val { color: #0f766e; }
.sync-tile.is-only-es  .sync-tile-val { color: #1d4ed8; }
.sync-tile.is-field    .sync-tile-val { color: #b45309; }

.sync-clean {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 12px 16px;
    border-radius: 12px;
    background: var(--cc-green-bg);
    border: 1px solid rgba(5,150,105,.30);
    color: #047857;
    font-size: 0.86rem;
    line-height: 1.45;
}
.sync-clean-glyph {
    font-size: 1.3rem;
    color: var(--cc-green);
    flex-shrink: 0;
}

.sync-section {
    margin-top: 10px;
    border-radius: 12px;
    border: 1px solid var(--cc-border);
    background: var(--cc-surface);
    overflow: hidden;
}
.sync-section.is-only-git { border-color: rgba(13,148,136,.30); }
.sync-section.is-only-es  { border-color: rgba(37,99,235,.30); }
.sync-section.is-field    { border-color: rgba(217,119,6,.32); }
.sync-section-head {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--cc-border);
    background: var(--cc-surface2);
}
.sync-section.is-only-git .sync-section-head { background: rgba(13,148,136,.04); }
.sync-section.is-only-es  .sync-section-head { background: rgba(37,99,235,.04); }
.sync-section.is-field    .sync-section-head { background: rgba(217,119,6,.04); }
.sync-section-glyph {
    width: 24px; height: 24px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 0.95rem;
    border-radius: 6px;
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
}
.sync-section.is-only-git .sync-section-glyph { color: #0f766e; }
.sync-section.is-only-es  .sync-section-glyph { color: #1d4ed8; }
.sync-section.is-field    .sync-section-glyph { color: #b45309; }
.sync-section-title {
    font-family: var(--cc-sans);
    font-weight: 700;
    font-size: 0.88rem;
    color: var(--cc-text);
}
.sync-section-count {
    margin-left: auto;
    font-family: var(--cc-mono);
    font-size: 0.7rem;
    color: var(--cc-text-mute);
    background: var(--cc-surface);
    padding: 2px 8px;
    border-radius: 999px;
    border: 1px solid var(--cc-border);
}

.sync-only-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    padding: 10px 12px;
}
.sync-only-chip {
    display: inline-flex;
    padding: 3px 9px;
    border-radius: 6px;
    font-family: var(--cc-mono);
    font-size: 0.72rem;
    font-weight: 600;
    border: 1px solid;
}
.sync-only-chip.is-only-git {
    color: #0f766e;
    background: rgba(13,148,136,.06);
    border-color: rgba(13,148,136,.30);
}
.sync-only-chip.is-only-es {
    color: #1d4ed8;
    background: rgba(37,99,235,.06);
    border-color: rgba(37,99,235,.30);
}
.sync-only-more {
    display: inline-flex;
    padding: 3px 9px;
    border-radius: 6px;
    font-family: var(--cc-mono);
    font-size: 0.7rem;
    color: var(--cc-text-mute);
    font-style: italic;
    background: var(--cc-surface2);
    border: 1px dashed var(--cc-border);
}

/* Per-app diff cards — collapsed by default */
.sync-diff-card {
    margin: 6px 0;
    border: 1px solid rgba(217,119,6,.28);
    border-radius: 10px;
    background: var(--cc-surface);
    overflow: hidden;
}
.sync-diff-card summary {
    cursor: pointer;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 12px;
    background: rgba(217,119,6,.04);
}
.sync-diff-card summary::-webkit-details-marker { display: none; }
.sync-diff-card summary::before {
    content: "▸";
    color: var(--cc-amber);
    font-size: 0.78rem;
    transition: transform .12s;
}
.sync-diff-card[open] summary::before { transform: rotate(90deg); }
.sync-diff-app {
    font-family: var(--cc-mono);
    font-size: 0.82rem;
    font-weight: 700;
    color: var(--cc-text);
}
.sync-diff-proj {
    font-family: var(--cc-mono);
    font-size: 0.7rem;
    color: var(--cc-text-mute);
    margin-left: 4px;
}
.sync-diff-count {
    margin-left: auto;
    font-family: var(--cc-mono);
    font-size: 0.66rem;
    color: var(--cc-amber);
    background: rgba(217,119,6,.10);
    padding: 1px 8px;
    border-radius: 4px;
    border: 1px solid rgba(217,119,6,.30);
}
.sync-diff-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.78rem;
    background: var(--cc-surface);
}
.sync-diff-table th {
    text-align: left;
    padding: 6px 12px;
    background: var(--cc-surface2);
    color: var(--cc-text-mute);
    font-family: var(--cc-mono);
    font-size: 0.64rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 700;
    border-bottom: 1px solid var(--cc-border);
}
.sync-diff-table th.is-git { color: #0f766e; }
.sync-diff-table th.is-es  { color: #1d4ed8; }
.sync-diff-table td {
    padding: 6px 12px;
    vertical-align: top;
    border-bottom: 1px solid var(--cc-border);
}
.sync-diff-table tr:last-child td { border-bottom: none; }
.sync-diff-k {
    font-family: var(--cc-mono);
    color: var(--cc-text-dim);
    font-weight: 600;
    white-space: nowrap;
}
.sync-diff-side.is-git { background: rgba(13,148,136,.03); }
.sync-diff-side.is-es  { background: rgba(37,99,235,.03); }
.sync-cell-val {
    font-family: var(--cc-mono);
    color: var(--cc-text);
    font-weight: 600;
    word-break: break-word;
}
.sync-cell-val.is-git { color: #0f766e; }
.sync-cell-val.is-es  { color: #1d4ed8; }
.sync-cell-chip {
    display: inline-block;
    margin: 1px 3px 1px 0;
    padding: 1px 7px;
    border-radius: 4px;
    font-family: var(--cc-mono);
    font-size: 0.72rem;
    font-weight: 600;
}
.sync-cell-chip.is-git { color: #0f766e; background: rgba(13,148,136,.08); border: 1px solid rgba(13,148,136,.24); }
.sync-cell-chip.is-es  { color: #1d4ed8; background: rgba(37,99,235,.08);  border: 1px solid rgba(37,99,235,.24); }
.sync-cell-empty {
    color: var(--cc-text-mute);
    font-style: italic;
    font-family: var(--cc-mono);
}
.sync-cell-val.is-warn,
.sync-cell-chip.is-warn {
    color: #92400e;
    background: rgba(217,119,6,.10);
    border: 1px solid rgba(217,119,6,.30);
}

/* Divider between the two sync-check sub-sections */
.sync-section-divider {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 18px 0 8px 0;
    padding: 4px 0;
    font-family: var(--cc-mono);
    font-size: 0.74rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text);
    border-bottom: 1px dashed var(--cc-border);
}
.sync-section-divider:first-child { margin-top: 4px; }
.sync-section-divider-glyph {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 24px; height: 24px;
    border-radius: 6px;
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    font-size: 0.85rem;
}

/* Postgres-specific accents */
.sync-tile.pg-inconsistent {
    border-color: rgba(220,38,38,.36);
    background: rgba(220,38,38,.04);
}
.sync-tile.pg-inconsistent .sync-tile-val { color: var(--cc-red); }

.pg-apps-n {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    color: var(--cc-text-mute);
    margin-left: 4px;
    font-weight: 500;
}

.sync-diff-card.pg-card-inconsistent {
    border-color: rgba(220,38,38,.40);
}
.sync-diff-card.pg-card-inconsistent summary {
    background: rgba(220,38,38,.05);
}
.pg-ops-inconsistent {
    padding: 8px 14px;
    background: rgba(220,38,38,.06);
    border-bottom: 1px dashed rgba(220,38,38,.32);
    color: var(--cc-red);
    font-family: var(--cc-mono);
    font-size: 0.74rem;
    font-weight: 700;
    cursor: help;
}
.pg-ops-breakdown {
    margin-top: 4px;
    border-top: 1px dashed var(--cc-border);
}
.pg-ops-breakdown th { background: rgba(217,119,6,.04); }

/* ── INTEGRATIONS HEALTH STRIP ─────────────────────────────────────────────
 * Compact admin-only chip row that summarises every external integration's
 * state. Collapsed (default) shows: label · count summary · chip row · ▾
 * Expanded shows a small detail card per integration with the full tip.
 * Stays quiet on healthy days; outer hue + a soft pulsing dot signal when
 * something needs attention. */
.ih-strip {
    margin: 0 0 10px 0;
    border: 1px solid var(--cc-border);
    border-radius: 12px;
    background: var(--cc-surface);
    overflow: hidden;
    transition: border-color .15s, box-shadow .15s;
}
.ih-strip.is-outer-ok       { border-color: rgba(13,148,136,.28); }
.ih-strip.is-outer-warn     { border-color: rgba(217,119,6,.38); }
.ih-strip.is-outer-down     { border-color: rgba(220,38,38,.46); box-shadow: 0 4px 14px rgba(220,38,38,.06); }
.ih-strip.is-outer-mixed    { border-color: var(--cc-border-hi); }
.ih-strip:hover {
    border-color: var(--cc-border-hi);
}

.ih-strip-head {
    cursor: pointer;
    list-style: none;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 7px 12px;
    flex-wrap: wrap;
}
.ih-strip-head::-webkit-details-marker { display: none; }
.ih-strip-lbl {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    font-weight: 700;
    color: var(--cc-text-mute);
    flex-shrink: 0;
}
.ih-strip-counts {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
    margin-right: 4px;
}
.ih-sum {
    font-family: var(--cc-mono);
    font-size: 0.6rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    padding: 1px 6px;
    border-radius: 4px;
    border: 1px solid;
}
.ih-sum.is-ok    { color: #047857; background: rgba(5,150,105,.08);  border-color: rgba(5,150,105,.30); }
.ih-sum.is-warn  { color: #92400e; background: rgba(217,119,6,.10);  border-color: rgba(217,119,6,.34); }
.ih-sum.is-down  { color: #b91c1c; background: rgba(220,38,38,.08);  border-color: rgba(220,38,38,.34); }
.ih-sum.is-skip  { color: var(--cc-text-mute); background: rgba(136,144,164,.10); border-color: rgba(136,144,164,.30); }

.ih-strip-chips {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    flex-wrap: wrap;
    flex: 1 1 auto;
    min-width: 0;
}
.ih-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 8px 2px 6px;
    border-radius: 999px;
    background: var(--cc-surface2);
    border: 1px solid var(--cc-border);
    font-size: 0.7rem;
    cursor: help;
    transition: filter .12s, transform .12s, border-color .12s;
}
.ih-chip:hover { filter: brightness(1.05); transform: translateY(-0.5px); }
.ih-chip-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
}
.ih-chip.is-ok    .ih-chip-dot { background: var(--cc-green); box-shadow: 0 0 0 0 var(--cc-green);
                                  animation: ihDotPulseOk 2.6s ease-in-out infinite; }
.ih-chip.is-warn  .ih-chip-dot { background: var(--cc-amber); }
.ih-chip.is-down  .ih-chip-dot { background: var(--cc-red);   animation: ihDotPulseDown 1.4s ease-in-out infinite; }
.ih-chip.is-skip  .ih-chip-dot { background: var(--cc-text-mute); opacity: .5; }
@keyframes ihDotPulseOk {
    0%, 100% { box-shadow: 0 0 0 0 rgba(5,150,105,.4); }
    50%      { box-shadow: 0 0 0 4px rgba(5,150,105,0); }
}
@keyframes ihDotPulseDown {
    0%, 100% { box-shadow: 0 0 0 0 rgba(220,38,38,.55); }
    50%      { box-shadow: 0 0 0 5px rgba(220,38,38,0); }
}
.ih-chip-glyph { font-size: 0.78rem; line-height: 1; opacity: .85; }
.ih-chip-lbl {
    font-weight: 600;
    color: var(--cc-text);
    letter-spacing: 0.005em;
}
.ih-chip-detail {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    color: var(--cc-text-mute);
    letter-spacing: 0.03em;
}
.ih-chip.is-ok    { border-color: rgba(13,148,136,.30); }
.ih-chip.is-warn  { border-color: rgba(217,119,6,.34); background: rgba(217,119,6,.04); }
.ih-chip.is-down  { border-color: rgba(220,38,38,.36); background: rgba(220,38,38,.04); }
.ih-chip.is-skip  { border-color: var(--cc-border); opacity: .80; }

.ih-strip-toggle {
    font-size: 0.7rem;
    color: var(--cc-text-mute);
    transition: transform .12s;
    flex-shrink: 0;
    margin-left: auto;
}
.ih-strip[open] .ih-strip-toggle { transform: rotate(180deg); }

/* Expanded cards — one per integration */
.ih-strip-cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 8px;
    padding: 8px 12px 12px 12px;
    border-top: 1px dashed var(--cc-border);
    background: var(--cc-surface2);
}
.ih-card {
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 9px;
    padding: 8px 10px;
}
.ih-card-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
}
.ih-card-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
}
.ih-card.is-ok    { border-color: rgba(13,148,136,.30); }
.ih-card.is-ok    .ih-card-dot { background: var(--cc-green); }
.ih-card.is-warn  { border-color: rgba(217,119,6,.34); background: rgba(217,119,6,.02); }
.ih-card.is-warn  .ih-card-dot { background: var(--cc-amber); }
.ih-card.is-down  { border-color: rgba(220,38,38,.40); background: rgba(220,38,38,.03); }
.ih-card.is-down  .ih-card-dot { background: var(--cc-red); }
.ih-card.is-skip  { border-color: var(--cc-border); opacity: .92; }
.ih-card.is-skip  .ih-card-dot { background: var(--cc-text-mute); opacity: .55; }
.ih-card-glyph { font-size: 0.85rem; line-height: 1; }
.ih-card-lbl {
    font-weight: 700;
    font-size: 0.78rem;
    color: var(--cc-text);
    flex: 1;
}
.ih-card-state {
    font-family: var(--cc-mono);
    font-size: 0.58rem;
    letter-spacing: 0.10em;
    font-weight: 700;
    padding: 1px 5px;
    border-radius: 3px;
    background: rgba(136,144,164,.15);
    color: var(--cc-text-mute);
}
.ih-card.is-ok   .ih-card-state { background: rgba(5,150,105,.14);  color: #047857; }
.ih-card.is-warn .ih-card-state { background: rgba(217,119,6,.16);  color: #92400e; }
.ih-card.is-down .ih-card-state { background: rgba(220,38,38,.14);  color: #b91c1c; }
.ih-card-detail {
    font-family: var(--cc-mono);
    font-size: 0.7rem;
    color: var(--cc-text);
    font-weight: 600;
    margin-bottom: 3px;
    word-break: break-word;
}
.ih-card-tip {
    font-size: 0.72rem;
    color: var(--cc-text-dim);
    line-height: 1.45;
    word-break: break-word;
}

/* ── JENKINS PANEL ─────────────────────────────────────────────────────────
   Gate (idle), connection header, pipeline cards, status pills, params.
   ----------------------------------------------------------------------- */

/* Idle gate — shown until the operator opts to load. Soft glassy card so
 * the "click to load" CTA reads as deliberate rather than a placeholder. */
.jk-gate {
    text-align: center;
    padding: 26px 22px 14px 22px;
    margin: 0 0 12px 0;
    border-radius: 18px;
    background: linear-gradient(180deg,
                rgba(79,70,229,.04) 0%,
                rgba(79,70,229,.01) 100%);
    border: 1px dashed var(--cc-border-hi);
}
.jk-gate-glyph {
    font-size: 2.4rem;
    line-height: 1;
    color: var(--cc-accent);
    opacity: .8;
}
.jk-gate-title {
    font-family: var(--cc-mono);
    font-size: 0.8rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--cc-text);
    margin: 10px 0 4px 0;
}
.jk-gate-body {
    font-size: 0.82rem;
    color: var(--cc-text-dim);
    max-width: 520px;
    margin: 0 auto;
    line-height: 1.45;
}
.jk-empty {
    text-align: center;
    padding: 22px 18px;
    border: 1px dashed var(--cc-border);
    border-radius: 14px;
    color: var(--cc-text-mute);
    background: var(--cc-surface2);
}
.jk-empty-glyph { font-size: 2rem; opacity: .55; }
.jk-empty-title {
    font-family: var(--cc-mono);
    font-size: 0.78rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text);
    margin: 8px 0 4px 0;
}
.jk-empty-body { font-size: 0.78rem; line-height: 1.5; }
.jk-empty-err {
    margin-top: 10px;
    padding: 8px 12px;
    background: rgba(220,38,38,.06);
    border: 1px solid rgba(220,38,38,.30);
    border-radius: 8px;
    text-align: left;
    font-size: 0.74rem;
    line-height: 1.5;
    color: var(--cc-red);
    overflow-wrap: anywhere;
}
.jk-empty-err-k {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 700;
    color: var(--cc-red);
    margin-right: 6px;
}
.jk-empty-err code {
    background: transparent;
    color: var(--cc-red);
    padding: 0;
    font-weight: 600;
    word-break: break-all;
}
.psv-empty-err {
    margin-top: 10px;
    padding: 8px 12px;
    background: rgba(220,38,38,.06);
    border: 1px solid rgba(220,38,38,.30);
    border-radius: 8px;
    text-align: left;
    font-size: 0.74rem;
    line-height: 1.5;
    color: var(--cc-red);
    overflow-wrap: anywhere;
}
.psv-empty-err-k {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 700;
    color: var(--cc-red);
    margin-right: 6px;
}
.psv-empty-err code {
    background: transparent;
    color: var(--cc-red);
    padding: 0;
    font-weight: 600;
    word-break: break-all;
}
.jk-empty code {
    font-family: var(--cc-mono);
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 0.74rem;
}

/* Connection header — full-width banner so reachability state is the
 * loudest signal in the panel. Pulses softly on the healthy path. */
.jk-hdr {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    border-radius: 12px;
    margin: 6px 0 12px 0;
    font-family: var(--cc-mono);
    font-size: 0.78rem;
    border: 1px solid var(--cc-border);
}
.jk-hdr.is-ok {
    background: linear-gradient(90deg,
                rgba(5,150,105,.06), rgba(5,150,105,.02));
    border-color: rgba(5,150,105,.30);
    color: #047857;
}
.jk-hdr.is-down {
    background: linear-gradient(90deg,
                rgba(220,38,38,.06), rgba(220,38,38,.02));
    border-color: rgba(220,38,38,.32);
    color: #b91c1c;
}
.jk-hdr-glyph { font-size: 1rem; line-height: 1; }
.jk-hdr.is-ok .jk-hdr-glyph {
    animation: jkHdrPulse 2.6s ease-in-out infinite;
}
@keyframes jkHdrPulse {
    0%, 100% { text-shadow: 0 0 0 rgba(5,150,105,.55); }
    50%      { text-shadow: 0 0 8px rgba(5,150,105,.7); }
}
.jk-hdr-host {
    font-weight: 700;
    letter-spacing: 0.02em;
}
.jk-hdr-stat {
    color: var(--cc-text-mute);
    font-size: 0.72rem;
    margin-left: auto;
    font-weight: 500;
}

/* Jenkins version pill — sits inside the connection header. Three states:
 *   .is-current  → quiet teal, "✓ v2.450 · LATEST"
 *   .is-outdated → amber, animated arrow, "⬆ v2.440 → 2.450 · UPDATE"
 *   .is-unknown  → muted, "? v2.440 · CHECK·N/A"
 * The UPDATE tag pulses softly so the eye registers a maintenance signal
 * without it screaming for attention every refresh. */
.jk-ver {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 9px 3px 7px;
    border-radius: 999px;
    font-family: var(--cc-mono);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    border: 1px solid;
    cursor: help;
    margin: 0 4px;
}
.jk-ver.is-current {
    background: rgba(13,148,136,.08);
    border-color: rgba(13,148,136,.32);
    color: #0f766e;
}
.jk-ver.is-outdated {
    background: linear-gradient(135deg,
                rgba(217,119,6,.14), rgba(217,119,6,.04));
    border-color: rgba(217,119,6,.45);
    color: #b45309;
}
.jk-ver.is-unknown {
    background: rgba(136,144,164,.10);
    border-color: rgba(136,144,164,.30);
    color: var(--cc-text-mute);
}
.jk-ver-glyph {
    font-size: 0.85rem;
    line-height: 1;
}
.jk-ver.is-outdated .jk-ver-glyph {
    animation: jkVerArrow 2.2s ease-in-out infinite;
}
@keyframes jkVerArrow {
    0%, 100% { transform: translateY(0); opacity: .85; }
    50%      { transform: translateY(-1.5px); opacity: 1; }
}
.jk-ver-arrow {
    color: var(--cc-text-mute);
    opacity: .7;
    margin: 0 1px;
}
.jk-ver-target {
    color: #b45309;
    font-weight: 700;
}
.jk-ver-tag {
    margin-left: 4px;
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 0.56rem;
    letter-spacing: 0.10em;
    font-weight: 700;
}
.jk-ver.is-current .jk-ver-tag {
    background: rgba(13,148,136,.14);
    color: #0f766e;
}
.jk-ver.is-outdated .jk-ver-tag {
    background: rgba(217,119,6,.20);
    color: #92400e;
    animation: jkVerTagPulse 2.6s ease-in-out infinite;
}
@keyframes jkVerTagPulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(217,119,6,.35); }
    50%      { box-shadow: 0 0 0 4px rgba(217,119,6,0); }
}
.jk-ver.is-unknown .jk-ver-tag {
    background: rgba(136,144,164,.18);
    color: var(--cc-text-mute);
}

/* ── PRISMA SCAN VIEWER ────────────────────────────────────────────────────
 * Empty / hint states + the framed iframe header. The iframe itself is
 * Streamlit-rendered (components.v1.html) and lives sandboxed below.
 * ----------------------------------------------------------------------- */
.psv-empty {
    text-align: center;
    padding: 26px 22px 18px 22px;
    border: 1px dashed var(--cc-border-hi);
    border-radius: 16px;
    color: var(--cc-text-mute);
    background: linear-gradient(180deg,
                rgba(13,148,136,.04) 0%,
                rgba(13,148,136,.01) 100%);
}
.psv-empty-glyph {
    font-size: 2.4rem;
    line-height: 1;
    color: var(--cc-teal);
    opacity: .85;
}
.psv-empty-title {
    font-family: var(--cc-mono);
    font-size: 0.8rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text);
    margin: 10px 0 4px 0;
}
.psv-empty-body {
    font-size: 0.82rem;
    color: var(--cc-text-dim);
    max-width: 540px;
    margin: 0 auto;
    line-height: 1.45;
}
.psv-empty code {
    font-family: var(--cc-mono);
    background: var(--cc-teal-lt);
    color: var(--cc-teal);
    padding: 1px 5px;
    border-radius: 4px;
    font-size: 0.74rem;
}
.psv-hint {
    margin-top: 12px;
    padding: 12px 16px;
    border-radius: 10px;
    background: var(--cc-accent-bg);
    border: 1px dashed rgba(79,70,229,.30);
    color: var(--cc-text-dim);
    font-size: 0.82rem;
    line-height: 1.5;
}
.psv-hint b { color: var(--cc-accent); }

/* Frame header — sits ABOVE the iframe and gives the report a polished
 * surround instead of dumping raw HTML into the page. */
.psv-frame-head {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 14px;
    margin: 12px 0 0 0;
    border: 1px solid var(--cc-border);
    border-bottom: none;
    border-radius: 12px 12px 0 0;
    background: linear-gradient(135deg,
                rgba(37,99,235,.06), rgba(37,99,235,.01));
}
.psv-frame-icon {
    width: 36px; height: 36px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 1.25rem;
    background: var(--cc-blue-lt);
    color: var(--cc-blue);
    border-radius: 9px;
    flex-shrink: 0;
}
.psv-frame-title-wrap { flex: 1; min-width: 0; }
.psv-frame-kicker {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text-mute);
    font-weight: 600;
}
.psv-frame-title {
    font-family: var(--cc-sans);
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--cc-text);
    letter-spacing: -0.01em;
}
.psv-frame-ver {
    font-family: var(--cc-mono);
    color: var(--cc-blue);
    font-weight: 600;
    margin-left: 6px;
    font-size: 0.95rem;
}
.psv-frame-meta {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-left: auto;
}
.psv-meta-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 9px;
    border-radius: 6px;
    background: rgba(255,255,255,.7);
    border: 1px solid var(--cc-border);
    font-size: 0.7rem;
    cursor: default;
}
.psv-meta-k {
    font-family: var(--cc-mono);
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--cc-text-mute);
    font-weight: 600;
}
.psv-meta-v {
    font-family: var(--cc-mono);
    color: var(--cc-text);
    font-weight: 600;
}
.psv-open {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 11px;
    border-radius: 999px;
    background: var(--cc-blue);
    color: #fff !important;
    font-family: var(--cc-mono);
    font-size: 0.68rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    text-decoration: none !important;
    border: 1px solid var(--cc-blue);
    transition: filter .12s, transform .12s;
}
.psv-open:hover {
    filter: brightness(1.08);
    transform: translateY(-0.5px);
}

/* The iframe Streamlit injects — give it a matching bottom-radius and the
 * sibling border so the framed effect spans the whole component. */
[data-testid="stIFrame"] iframe {
    border: 1px solid var(--cc-border);
    border-top: none;
    border-radius: 0 0 12px 12px;
    box-shadow: 0 4px 14px rgba(10,14,30,.05);
    background: var(--cc-surface);
}

/* Card grid — 3 pipelines side-by-side on wide viewports, stacks on narrow. */
.jk-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 12px;
    margin: 4px 0 8px 0;
}
.jk-card {
    background: var(--cc-surface);
    border: 1px solid var(--cc-border);
    border-radius: 14px;
    padding: 14px 14px 12px 14px;
    box-shadow: 0 2px 6px rgba(10,14,30,.04);
    display: flex;
    flex-direction: column;
    gap: 9px;
    transition: border-color .15s, box-shadow .15s;
}
.jk-card:hover {
    border-color: var(--cc-border-hi);
    box-shadow: 0 6px 18px rgba(10,14,30,.07);
}
.jk-card-head {
    display: flex;
    align-items: center;
    gap: 10px;
}
.jk-card-glyph {
    width: 34px; height: 34px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    font-size: 1.2rem;
    background: var(--cc-accent-lt);
    color: var(--cc-accent);
    border-radius: 9px;
    flex-shrink: 0;
}
.jk-card-title-wrap { flex: 1; min-width: 0; }
.jk-card-kicker {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--cc-text-mute);
    font-weight: 600;
}
.jk-card-title {
    font-family: var(--cc-sans);
    font-size: 1rem;
    font-weight: 700;
    color: var(--cc-text);
    letter-spacing: -0.01em;
}
.jk-card-summary {
    font-size: 0.74rem;
    color: var(--cc-text-dim);
    line-height: 1.45;
    margin: 0 0 2px 0;
}

/* "Ready to trigger" indicator on each card */
.jk-ready {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 3px 9px;
    border-radius: 999px;
    border: 1px solid;
    cursor: default;
    flex-shrink: 0;
}
.jk-ready.is-ready {
    background: var(--cc-green-bg);
    border-color: rgba(5,150,105,.36);
    color: var(--cc-green);
}
.jk-ready.is-blocked {
    background: var(--cc-red-bg);
    border-color: rgba(220,38,38,.34);
    color: var(--cc-red);
    cursor: help;
}

/* Status pills (also reused for inline running state) */
.jk-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 2px 9px;
    border-radius: 999px;
    font-family: var(--cc-mono);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    border: 1px solid transparent;
}
.jk-pill.is-ok {
    background: var(--cc-green-bg);
    border-color: rgba(5,150,105,.32);
    color: var(--cc-green);
}
.jk-pill.is-fail {
    background: var(--cc-red-bg);
    border-color: rgba(220,38,38,.32);
    color: var(--cc-red);
}
.jk-pill.is-warn {
    background: var(--cc-amber-bg);
    border-color: rgba(217,119,6,.32);
    color: var(--cc-amber);
}
.jk-pill.is-mute {
    background: rgba(136,144,164,.10);
    border-color: rgba(136,144,164,.30);
    color: var(--cc-text-mute);
}
.jk-pill.is-running {
    background: var(--cc-blue-bg);
    border-color: rgba(37,99,235,.34);
    color: var(--cc-blue);
}
.jk-pill-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: currentColor;
    animation: jkRunDot 1.1s ease-in-out infinite;
}
@keyframes jkRunDot {
    0%, 100% { opacity: .3; transform: scale(.8); }
    50%      { opacity: 1;  transform: scale(1.1); }
}

/* Last-build summary row */
.jk-last {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-top: 1px dashed var(--cc-border);
    border-bottom: 1px dashed var(--cc-border);
}
.jk-last-lbl {
    font-family: var(--cc-mono);
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-text-mute);
    font-weight: 600;
}
.jk-last-meta {
    font-family: var(--cc-mono);
    font-size: 0.68rem;
    color: var(--cc-text-dim);
    margin-left: auto;
    font-weight: 500;
}

/* In-flight runs */
.jk-running-block {
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.jk-running-block.is-quiet {
    text-align: center;
    color: var(--cc-text-mute);
    font-size: 0.74rem;
    padding: 6px 0 4px 0;
}
.jk-running-quiet { font-style: italic; }
.jk-running-head {
    display: flex;
    align-items: center;
    margin-top: 2px;
}
.jk-running-lbl {
    font-family: var(--cc-mono);
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.10em;
    color: var(--cc-blue);
    font-weight: 700;
}
.jk-running-row {
    display: flex;
    flex-direction: column;
    gap: 5px;
    padding: 7px 9px 9px 9px;
    background: var(--cc-blue-bg);
    border: 1px solid rgba(37,99,235,.18);
    border-radius: 10px;
}
.jk-running-meta {
    font-family: var(--cc-mono);
    font-size: 0.68rem;
    color: var(--cc-text-dim);
    font-weight: 500;
}
.jk-params {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-top: 2px;
}
.jk-param-empty {
    font-size: 0.68rem;
    color: var(--cc-text-mute);
    font-style: italic;
}
.jk-param-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 1px 7px 2px 7px;
    background: rgba(255,255,255,.7);
    border: 1px solid rgba(37,99,235,.20);
    border-radius: 6px;
    font-size: 0.68rem;
}
.jk-param-chip.is-other {
    background: rgba(136,144,164,.08);
    border-color: rgba(136,144,164,.22);
}
.jk-param-k {
    font-family: var(--cc-mono);
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--cc-text-mute);
    font-weight: 600;
}
.jk-param-v {
    font-family: var(--cc-mono);
    color: var(--cc-text);
    font-weight: 700;
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

/* Inline outdated-image pills appended to the inventory's application
   cell. Subtle amber tag (⬆ B / ⬆ D) so an admin scanning the table
   spots upgrade candidates without the row screaming. The popover shows
   the exact current → recommended path. */
.iv-outdated-row {
    display: inline-flex;
    gap: 3px;
    margin-left: 6px;
    vertical-align: middle;
}
.iv-outdated-pill {
    display: inline-flex;
    align-items: center;
    font-family: var(--cc-mono);
    font-size: 0.55rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--cc-amber);
    background: color-mix(in srgb, var(--cc-amber) 11%, transparent);
    border: 1px solid color-mix(in srgb, var(--cc-amber) 35%, transparent);
    border-radius: 3px;
    padding: 1px 4px;
    line-height: 1.25;
    cursor: help;
    transition: background .15s ease, color .15s ease;
}
.iv-outdated-pill:hover {
    color: #fff;
    background: var(--cc-amber);
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

/* ----- Whisper-thin identity rail. Just enough surface to anchor the role
   pill — no panel chrome, no heavy gradients, no glow. The rail scrolls
   away with the page; the Filter Console (position: fixed below) keeps
   active state visible. */
.st-key-cc_filter_rail {
    background: transparent !important;
    border: 0 !important;
    border-bottom: 1px solid rgba(15,13,38,.06) !important;
    border-radius: 0 !important;
    padding: 6px 4px 6px 4px !important;
    box-shadow: none !important;
    overflow: visible;
    margin-bottom: 4px !important;
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

/* ----- Rail identity — compact, whisper-style. Pill carries role + team
   inline, kerned tight, no shadows, semi-translucent so it doesn't
   compete with the data below. ----- */
.cc-rail-id--whisper {
    display: inline-flex !important;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    line-height: 1.1;
    padding: 0;
    margin: 0;
}
/* Multi-role badge: tighter pill spacing so several roles fit inline */
.cc-rail-id--multi { gap: 4px; }
.cc-rail-id--multi .cc-rail-id-role { padding: 2px 7px !important; }
.cc-rail-id--whisper .cc-rail-id-role {
    display: inline-flex !important;
    align-items: center;
    gap: 5px;
    font-family: var(--cc-body) !important;
    font-weight: 600 !important;
    font-size: 0.66rem !important;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 3px 9px !important;
    border-radius: 999px !important;
    border: 1px solid currentColor;
    border-color: rgba(0,0,0,.10) !important;
    box-shadow: none !important;
    line-height: 1.2;
}
.cc-rail-id--whisper .cc-rail-id-icon {
    font-size: 0.78rem;
    line-height: 1;
    opacity: .85;
}
.cc-rail-id--whisper .cc-rail-id-team {
    font-family: var(--cc-body) !important;
    font-size: 0.62rem !important;
    color: var(--cc-text-mute);
    letter-spacing: 0.02em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 18ch;
    opacity: .80;
}

/* The ⓘ explainer popover trigger should also be whisper-light — small
   ghost button so it's not the visual centre of the rail. */
.st-key-cc_filter_rail [data-testid="stPopover"] button,
.st-key-cc_filter_rail [data-testid="stPopoverButton"] button {
    min-height: 22px !important;
    padding: 1px 6px !important;
    font-size: 0.72rem !important;
    background: transparent !important;
    border: 1px solid rgba(0,0,0,.08) !important;
    color: var(--cc-text-mute) !important;
    box-shadow: none !important;
    border-radius: 999px !important;
}

.cc-rail-spacer { display: block; height: 1px; }

/* Legacy selector kept for any markup we haven't migrated yet. */
.cc-rail-id-role:not(.cc-rail-id--whisper *) {
    font-family: var(--cc-display) !important;
    font-variation-settings: "opsz" 60;
    font-weight: 600 !important;
    letter-spacing: -0.005em !important;
    font-size: 0.82rem !important;
    padding: 4px 12px !important;
}
.cc-rail-id-team:not(.cc-rail-id--whisper *) {
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
/* Per-stage rows inside the security-posture tile (admin / CLevel only).
   Three compact rows stacked: DEV / QC / PRD, each with its own
   crit·high·med·low quick-read and a mini distribution bar. */
.iv-sec-stages {
    display: flex;
    flex-direction: column;
    gap: 7px;
    margin-top: 6px;
    padding-top: 8px;
    border-top: 1px dashed
        color-mix(in srgb, var(--cc-border) 80%, transparent);
}
.iv-sec-stage-row { display: flex; flex-direction: column; gap: 3px; }
.iv-sec-stage-row-head {
    display: flex;
    align-items: center;
    gap: 8px;
    line-height: 1.2;
}
.iv-sec-stage-name {
    font-family: var(--cc-data);
    font-size: .58rem;
    letter-spacing: .14em;
    text-transform: uppercase;
    font-weight: 700;
    color: var(--cc-ink);
    background: color-mix(in srgb, var(--cc-text-mute) 8%, transparent);
    padding: 1px 7px;
    border-radius: 4px;
    line-height: 1.4;
    flex: 0 0 auto;
}
.iv-sec-stage-count {
    font-family: var(--cc-data);
    font-size: .60rem;
    color: var(--cc-text-dim);
    font-variant-numeric: tabular-nums;
    flex: 1;
}
.iv-sec-stage-count b {
    color: var(--cc-red);
    font-weight: 700;
    margin-right: 1px;
}
.iv-sec-stage-apps {
    font-family: var(--cc-data);
    font-size: .56rem;
    letter-spacing: .04em;
    color: var(--cc-text-mute);
    flex: 0 0 auto;
}
.iv-sec-stages .iv-pulse-bar { margin-top: 0; height: 7px; }
.iv-sec-stages .iv-pulse-legend { display: none; }

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

/* The rail is intentionally NOT sticky any more — it scrolls away with
   the page as part of the "subtler header" cleanup. Filter Console
   visibility is owned by the position-fixed cc_filter_secondary below.
   Leaving overflow overrides in place because other layout pieces still
   benefit (popover positioning, etc.). */
[data-testid="stMainBlockContainer"],
.main .block-container,
[data-testid="stVerticalBlock"],
[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stElementContainer"] {
    overflow: visible !important;
    transform: none !important;
    filter: none !important;
}

/* Filter Console — pinned to the viewport with `position: fixed`. We
   tried `position: sticky` twice and it kept losing its anchor when
   nested in Streamlit's flex wrappers. Fixed positioning is unambiguous:
   the bar floats at the top, every other layout block flows under it
   normally, and we add a global padding-top below to compensate for the
   removed flow space.

   Vertical anchor: ``--header-height`` is set on :root by Streamlit and
   reflects the real height of the top header (which grows when the page
   is hosted inside a multipage app with ``st.navigation(position="top")``).
   We anchor the bar 8px below it so the host's nav stays visible.
   Fallback value (88px) is the typical multipage nav-row height.

   Horizontal anchor: by default the bar spans the whole content column
   (left/right both set, with max-width centring it within wide
   viewports). When the host's sidebar is EXPANDED, we slide its left
   edge past the sidebar so the bar fits inside the content area
   instead of being clipped underneath. The :has() selector reads
   ``[data-testid="stSidebar"][aria-expanded="true"]`` — the attribute
   Streamlit sets on the sidebar container in recent versions. When the
   sidebar is collapsed (or the host has none), the default rule wins
   and the bar extends across the full page width. */
.st-key-cc_filter_secondary {
    position: fixed !important;
    top: calc(var(--header-height, 88px) + 8px) !important;
    left: 16px !important;
    right: 16px !important;
    width: auto !important;
    max-width: 1240px !important;
    margin: 0 auto !important;
    transform: none !important;
    z-index: 1100 !important;
}
body:has([data-testid="stSidebar"][aria-expanded="true"])
    .st-key-cc_filter_secondary,
[data-testid="stApp"]:has([data-testid="stSidebar"][aria-expanded="true"])
    .st-key-cc_filter_secondary {
    left: calc(var(--sidebar-width, 244px) + 16px) !important;
}
/* Compensate for the removed flow space — the main block container
   already starts BELOW the header (Streamlit handles that), so this
   padding only needs to clear the FIXED bar's height plus a small
   buffer. Independent of --header-height. */
[data-testid="stMainBlockContainer"] {
    padding-top: 84px !important;
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
# scoped *strictly* to its own ownership field(s) on the inventory document:
#   Developer  → projects where dev_team ∈ user's teams
#   QC         → projects where qc_team ∈ user's teams
#   Operations → projects where uat_team OR prd_team ∈ user's teams
#                (Operations runs both UAT and PRD, so they need
#                visibility into both ownership lanes)
#   Admin/CLevel → bypass entirely (full fleet visibility)
ROLE_TEAM_FIELDS: dict[str, list[str]] = {
    "Admin":     [],
    "CLevel":    [],
    "Developer": ["dev_team.keyword"],
    "QC":        ["qc_team.keyword"],
    "Operations":  ["uat_team.keyword", "prd_team.keyword"],
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


# ── Loose-version dict for security/scan result maps ────────────────────────
# Different indices store the same logical version slightly differently:
# `ef-cicd-builds` may write "1.2.3", `ef-cicd-prismacloud` may write "1.2.3 "
# (trailing space) or "V1.2.3" (different case). Exact tuple equality on
# (app, codeversion) then drops perfectly real scans on the floor — the user
# saw apps with prisma data in the index that didn't appear in the dashboard.
#
# This subclass auto-inserts a stripped+lowercased variant key alongside the
# literal one, and `.get()` / `__contains__` lookups also fall back to the
# normalised form when the literal misses. Net effect: consumers don't have
# to know which side of the version-string drift they're on.
_LOOSE_VER_SENTINEL = object()


class _LooseVerDict(dict):
    """Dict keyed by ``(app, version)`` tuples that tolerates whitespace
    and case drift in either component on both write and read.

    NOTE: instances of this subclass cannot be returned from a function
    decorated with ``@st.cache_data`` — Streamlit's cache rejects custom
    classes defined in ``__main__`` because pickle can't reliably re-import
    them. The pattern in this file is therefore: cached fetchers return
    plain ``dict`` objects (which DO pickle), and a thin uncached wrapper
    around each fetcher hands the result to ``_LooseVerDict(...)`` so
    consumers still see a tolerant view.
    """

    @staticmethod
    def _norm_key(key):
        if not isinstance(key, tuple) or len(key) != 2:
            return key
        a, v = key
        return (
            a.strip().lower() if isinstance(a, str) else a,
            v.strip().lower() if isinstance(v, str) else v,
        )

    def __init__(self, *args, **kwargs):
        super().__init__()
        # Re-route bulk init through our overridden __setitem__ so the
        # normalised-variant keys actually get inserted. ``dict.__init__``
        # bypasses Python-level __setitem__, which would otherwise leave us
        # with literal-only keys when wrapping a plain dict.
        if len(args) == 1:
            src = args[0]
            if hasattr(src, "items"):
                for _k, _v in src.items():
                    self[_k] = _v
            else:
                for _k, _v in src:
                    self[_k] = _v
        elif args:
            raise TypeError(
                f"_LooseVerDict expected at most 1 positional argument, "
                f"got {len(args)}"
            )
        for _k, _v in kwargs.items():
            self[_k] = _v

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        nk = self._norm_key(key)
        if nk != key:
            super().setdefault(nk, value)

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            nk = self._norm_key(key)
            if nk != key:
                return super().__getitem__(nk)
            raise

    def get(self, key, default=None):
        v = super().get(key, _LOOSE_VER_SENTINEL)
        if v is not _LOOSE_VER_SENTINEL:
            return v
        nk = self._norm_key(key)
        if nk != key:
            return super().get(nk, default)
        return default

    def __contains__(self, key):
        if super().__contains__(key):
            return True
        nk = self._norm_key(key)
        return nk != key and super().__contains__(nk)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_prismacloud_raw(app_versions: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], dict]:
    """Cached, plain-``dict`` body of :func:`_fetch_prismacloud`.

    Returns ``{(app, version): {Vcritical, Vhigh, Vmedium, Vlow, Ccritical,
    Chigh, Cmedium, Clow, status, when, imageName, imageTag}}``. Pairs with no
    matching scan are omitted — the caller treats that as "no prisma data".

    Returns a *plain* ``dict`` so Streamlit's ``cache_data`` (which pickles
    return values) doesn't choke on a custom subclass. The public
    :func:`_fetch_prismacloud` wraps this in :class:`_LooseVerDict` for
    whitespace/case tolerance on consumer lookups.
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
    # Compare against a NORMALISED wanted-set (whitespace/case-tolerant) so
    # a version drift between ef-cicd-builds and ef-cicd-prismacloud doesn't
    # silently drop the scan. The result map is itself a _LooseVerDict, so
    # consumers' literal-key lookups also fall through to the normalised
    # variant when needed.
    wanted_norm = {
        _LooseVerDict._norm_key((a, v)) for a, v in app_versions if a and v
    }
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
            if wanted_norm and _LooseVerDict._norm_key(key) not in wanted_norm:
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


def _fetch_prismacloud(app_versions: tuple[tuple[str, str], ...]) -> _LooseVerDict:
    """Whitespace/case-tolerant wrapper around the cached raw fetcher."""
    return _LooseVerDict(_fetch_prismacloud_raw(app_versions))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_invicti_raw(app_versions: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], dict]:
    """Cached, plain-``dict`` body of :func:`_fetch_invicti`.

    Returns ``{(app, version): {Vcritical, Vhigh, Vmedium, Vlow, BestPractice,
    Informational, status, environment, url, when}}``. Pairs with no scan are
    omitted — callers treat that as "never DAST-scanned by Invicti".

    Returns a plain ``dict`` for ``st.cache_data`` compatibility; the public
    :func:`_fetch_invicti` wraps it in :class:`_LooseVerDict`.
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
    wanted_norm = {
        _LooseVerDict._norm_key((a, v)) for a, v in app_versions if a and v
    }
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
            if wanted_norm and _LooseVerDict._norm_key(key) not in wanted_norm:
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


def _fetch_invicti(app_versions: tuple[tuple[str, str], ...]) -> _LooseVerDict:
    """Whitespace/case-tolerant wrapper around the cached raw fetcher."""
    return _LooseVerDict(_fetch_invicti_raw(app_versions))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _fetch_zap_raw(app_versions: tuple[tuple[str, str], ...]) -> dict[tuple[str, str], dict]:
    """Cached, plain-``dict`` body of :func:`_fetch_zap`.

    ZAP doesn't surface a critical bucket — only ``Vhigh`` / ``Vmedium`` /
    ``Vlow`` plus ``Informational`` and ``FalsePositives`` (both keyword in
    the index, but cast to int defensively for counting).

    Returns a plain ``dict`` for ``st.cache_data`` compatibility; the public
    :func:`_fetch_zap` wraps it in :class:`_LooseVerDict`.
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

    wanted_norm = {
        _LooseVerDict._norm_key((a, v)) for a, v in app_versions if a and v
    }
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
            if wanted_norm and _LooseVerDict._norm_key(key) not in wanted_norm:
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


def _fetch_zap(app_versions: tuple[tuple[str, str], ...]) -> _LooseVerDict:
    """Whitespace/case-tolerant wrapper around the cached raw fetcher."""
    return _LooseVerDict(_fetch_zap_raw(app_versions))


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
def _fetch_version_meta_raw(app_versions: tuple[tuple[str, str], ...]
                            ) -> dict[tuple[str, str], dict]:
    """Cached, plain-``dict`` body of :func:`_fetch_version_meta`.

    Returns ``{(app, ver): {"build_when": str, "release_when": str,
    "rlm": str, "rlm_status": str}}`` — missing lookups are simply absent
    (callers treat that as "no record").

    Returns a plain ``dict`` for ``st.cache_data`` compatibility; the public
    :func:`_fetch_version_meta` wraps it in :class:`_LooseVerDict`.
    """
    if not app_versions:
        return {}
    apps = sorted({_a for _a, _ in app_versions if _a})
    if not apps:
        return {}
    wanted_norm = {
        _LooseVerDict._norm_key((a, v)) for a, v in app_versions if a and v
    }
    out: dict[tuple[str, str], dict] = {}

    def _set(key: tuple[str, str], field: str, val: str) -> None:
        # Membership test on normalised form so a whitespace/case drift in
        # `codeversion` between ef-cicd-builds and ef-cicd-releases doesn't
        # discard legitimate version metadata.
        if not val or _LooseVerDict._norm_key(key) not in wanted_norm:
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


def _fetch_version_meta(app_versions: tuple[tuple[str, str], ...]) -> _LooseVerDict:
    """Whitespace/case-tolerant wrapper around the cached raw fetcher."""
    return _LooseVerDict(_fetch_version_meta_raw(app_versions))


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
def _fetch_devops_projects(apps_json: str) -> dict[str, dict]:
    """Per-application metadata from ``ef-devops-projects``.

    Index fields are all ``text`` (no ``.keyword`` subfields), so we
    can't use a ``terms`` query for application scoping. Instead emit
    ``match_phrase`` clauses in a ``should`` (OR) bool — works for the
    typical 50-300 in-scope apps without fanning out.

    Returns ``{App: {raw _source}}``. Callers pluck QC URLs
    (``qcRouteUrl`` / ``qcServiceUrl``) — the dev URLs are NOT carried by
    this index and are derived at render time by swapping ``qc`` → ``dev``
    in the QC URL string. Also exposes Remedy product fields
    (``RemedyProductName``,
    ``RemedyProductTier1`` … ``Tier3``), and recommended-vs-current
    image versions (``BuildCurrentVer`` / ``BuildRecommendationVer``,
    ``DeployCurrentVer`` / ``DeployRecommendationVer``) from the
    returned dict.
    """
    _apps: list[str] = json.loads(apps_json)
    if not _apps:
        return {}
    try:
        resp = es_search(
            IDX["devops_projects"],
            {
                "query": {"bool": {
                    "should": [{"match_phrase": {"App": _a}} for _a in _apps],
                    "minimum_should_match": 1,
                }},
                "_source": [
                    "App", "AppType", "Project", "Company",
                    "BuildImageName", "BuildImageTag", "BuildTechnology",
                    "BuildCurrentVer", "BuildRecommendationVer", "BuildOutdatedVer",
                    "DeployImageName", "DeployImageTag", "DeployTechnology", "DeployPlatform",
                    "DeployCurrentVer", "DeployRecommendationVer", "DeployOutdatedVer",
                    "DeployThroughInternet",
                    "ArchiveImageName", "ArchiveImageTag",
                    "DockerfileName", "NFSUsage",
                    "qcRouteUrl", "qcServiceUrl",
                    "qcRouteConsumers", "qcServiceConsumers",
                    "RemedyProductName",
                    "RemedyProductTier1", "RemedyProductTier2", "RemedyProductTier3",
                    "DevTeam", "QcTeam", "PrdTeam",
                    "JiraProjectKey",
                ],
            },
            # Buffer for any duplicate / stale rows the index may carry.
            size=max(len(_apps) * 2, 100),
        )
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for _h in resp.get("hits", {}).get("hits", []):
        _s = _h.get("_source", {}) or {}
        _app = (_s.get("App") or "").strip()
        if not _app:
            continue
        # Last write wins — if the index has multiple records for an app,
        # later docs in the result overwrite earlier ones. The index is
        # supposed to be one-row-per-app, so this is normally a no-op.
        out[_app] = _s
    return out


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_projects_for_role_teams(role: str, teams: tuple[str, ...]) -> list[str]:
    """Return inventory projects where the role's team field(s) match any of ``teams``.

    Developer → ``dev_team``; QC → ``qc_team``; Operations → ``uat_team``
    OR ``prd_team`` (both ownership lanes — Operations runs UAT + PRD).
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
    "Developer": True, "QC": True, "Operations": True,
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

# ── Multi-role aggregation ──────────────────────────────────────────────────
# A single user can carry several non-admin role tokens at once
# (Developer + QC + Operations on the same team, for example, when the
# same group is registered as dev_team / qc_team / uat_team / prd_team
# across projects). The display label `role_pick` keeps the priority
# pick (Admin > CLevel > first detected), but every SCOPING decision
# below — visible team fields, allowed event types / envs / approval
# stages, security-posture stages — must consider the UNION of every
# detected role's row in the corresponding table. These `_user_*`
# aggregates live next to `_is_admin` so downstream consumers stay
# single-lookup but read the unioned set instead of a single role.
_NON_ADMIN_DETECTED: list[str] = [
    _r for _r in _detected_roles if _r not in ("Admin", "CLevel")
]


def _union_role_list(table: dict[str, list], default_for_admin: list) -> list:
    """Order-preserving union of every detected role's row in ``table``.
    Admin / CLevel users get the Admin row directly (it already grants
    full access). Non-admin users union every detected role's entries.
    Falls back to ``default_for_admin`` only when the union is empty."""
    if _is_admin:
        return list(table.get("Admin") or default_for_admin)
    seen: list = []
    for _r in _NON_ADMIN_DETECTED or [_effective_role]:
        for _v in (table.get(_r) or []):
            if _v not in seen:
                seen.append(_v)
    return seen if seen else list(default_for_admin)


_user_team_fields = (
    [] if _is_admin
    else _union_role_list(ROLE_TEAM_FIELDS, [])
)
_user_event_types     = _union_role_list(_ROLE_EVENT_TYPES,     _ROLE_EVENT_TYPES["Admin"])
_user_envs            = _union_role_list(_ROLE_ENVS,            _ROLE_ENVS["Admin"])
_user_approval_stages = _union_role_list(_ROLE_APPROVAL_STAGES, [])
_user_shows_jira = (
    _is_admin or any(_ROLE_SHOWS_JIRA.get(_r, False)   for _r in _NON_ADMIN_DETECTED)
)
_user_shows_builds = (
    _is_admin or any(_ROLE_SHOWS_BUILDS.get(_r, False) for _r in _NON_ADMIN_DETECTED)
)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_apps_for_user_teams(team: str, fields_json: str) -> list[str]:
    """Apps where ANY of the user's role-team fields contains ``team``.
    ``fields_json`` is a JSON-encoded list because Streamlit's cache key
    requires a hashable arg. Falls back to an empty list when the user
    has no role-team fields (admin / unknown role)."""
    fields: list[str] = json.loads(fields_json)
    if not fields or not team:
        return []
    should = [{"term": {f: team}} for f in fields]
    query = {"bool": {"should": should, "minimum_should_match": 1}}
    try:
        return sorted(composite_terms(IDX["inventory"], "application.keyword", query).keys())
    except Exception:
        return []


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_projects_for_user_teams(teams_json: str, fields_json: str) -> list[str]:
    """Projects where ANY of the user's role-team fields contains
    ANY team in ``teams_json``. Same JSON-encoded args pattern as
    ``_load_apps_for_user_teams``."""
    fields: list[str] = json.loads(fields_json)
    teams:  list[str] = json.loads(teams_json)
    if not fields or not teams:
        return []
    should = [{"terms": {f: teams}} for f in fields]
    query = {"bool": {"should": should, "minimum_should_match": 1}}
    try:
        return sorted(composite_terms(IDX["inventory"], "project.keyword", query).keys())
    except Exception:
        return []


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

_user_fields_json = json.dumps(_user_team_fields)
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
        # Non-admin: union across every detected role's team field
        # (e.g. Developer+QC user → dev_team OR qc_team match).
        _team_apps = _load_apps_for_user_teams(team_filter, _user_fields_json)
elif (not _is_admin) and _active_teams:
    _union: set[str] = set()
    for _t in _active_teams:
        _union.update(_load_apps_for_user_teams(_t, _user_fields_json))
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
    _proj_scoped = _load_projects_for_user_teams(
        json.dumps(sorted(_active_teams)),
        _user_fields_json,
    )
    _scope_field_lbls = ", ".join(
        _f.replace(".keyword", "") for _f in _user_team_fields
    ) or "team"
    _proj_help = (
        f"{len(_proj_scoped)} project(s) where {_scope_field_lbls} ∈ "
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
    # Whisper-thin identity rail. The role badge + (admin-only) ⓘ
    # explainer popover sit on the left as a compact pill; the right
    # column is empty by design — the visible Filter Console button
    # (in cc_filter_secondary, position-fixed below the rail) makes
    # the previous "filters live in the console below" hint redundant.
    _rail = st.columns(
        [2.0, 6.0],
        vertical_alignment="center",
    )

    # ── Col 0: compact identity pill + (admin) ⓘ explainer ──────────
    # The whole pill is laid out as one row to keep the rail one-line tall.
    with _rail[0]:
        if _is_admin:
            _ident_cols = st.columns([5, 1], gap="small", vertical_alignment="center")
            _badge_col = _ident_cols[0]
            _why_col = _ident_cols[1]
        else:
            _badge_col = st.container()
            _why_col = None
        with _badge_col:
            # Multi-role users get one pill per detected role (priority
            # pick first), each tinted with its own role colour. Single-
            # role users see exactly one pill — same shape as before.
            _badge_roles = _detected_roles or [role_pick]
            _badge_pills: list[str] = []
            for _br in _badge_roles:
                _br_clr  = ROLE_COLORS.get(_br, _role_clr)
                _br_icon = ROLE_ICONS.get(_br, "")
                _badge_pills.append(
                    f'<span class="cc-rail-id-role" '
                    f'style="color:{_br_clr};border-color:{_br_clr}40;'
                    f'background:{_br_clr}0A">'
                    f'<span class="cc-rail-id-icon">{_br_icon}</span>'
                    f'{_br}</span>'
                )
            _multi_class = (
                ' cc-rail-id--multi' if len(_badge_roles) > 1 else ''
            )
            st.markdown(
                f'<div class="cc-rail-id cc-rail-id--whisper{_multi_class}">'
                + "".join(_badge_pills)
                + f'<span class="cc-rail-id-team" title="{_team_display}">'
                  f'{_team_display}</span>'
                + '</div>',
                unsafe_allow_html=True,
            )
        if _why_col is not None:
          with _why_col:
            with st.popover("ⓘ", help="How was this role picked?",
                            use_container_width=True):
                # Header: priority pick + ALL detected roles when there's
                # more than one (so multi-role users can verify the union
                # of their permissions at a glance).
                _why_pick_chips = "".join(
                    f'<span class="cc-role-why-icon" '
                    f'style="color:{ROLE_COLORS.get(_r, _role_clr)}">'
                    f'{ROLE_ICONS.get(_r, "")}</span>'
                    f'<span class="cc-role-why-name" '
                    f'style="color:{ROLE_COLORS.get(_r, _role_clr)}">'
                    f'{_r}</span>'
                    for _r in _detected_roles
                ) or (
                    f'<span class="cc-role-why-icon" '
                    f'style="color:{_role_clr}">{_role_icon}</span>'
                    f'<span class="cc-role-why-name" '
                    f'style="color:{_role_clr}">{role_pick}</span>'
                )
                _why_extra_note = (
                    '<div class="cc-role-why-reason"><b>'
                    f'{len(_detected_roles)} roles detected</b> — scope is '
                    'the UNION of every role\'s permissions; the pill above '
                    'lists each one in priority order.</div>'
                    if len(_detected_roles) > 1 else ''
                )
                st.markdown(
                    '<div class="cc-role-why">'
                    '<div class="cc-role-why-head">Role detection</div>'
                    + f'<div class="cc-role-why-pick">{_why_pick_chips}</div>'
                    + f'<div class="cc-role-why-reason">{_role_pick_reason}</div>'
                    + _why_extra_note
                    + '</div>',
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
                    '<li><b>Operations</b> → <code>uat_team</code> ∨ '
                    '<code>prd_team</code> ∈ your teams</li>'
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

    # ── Col 1: intentionally empty. The rail used to carry a "Filters live
    # in the Filter Console below" hint here, but the Filter Console button
    # is pinned to the viewport (position: fixed) so the hint is redundant
    # and was just adding noise.
    with _rail[1]:
        st.markdown(
            '<div class="cc-rail-spacer" aria-hidden="true"></div>',
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
# These helpers read from the multi-role unioned aggregates
# (_user_event_types / _user_envs / _user_approval_stages) so users with
# multiple non-admin role tokens see the union of every role's allowed
# values, not just the priority-picked role's row.


def _role_allows_type(t: str) -> bool:
    return t in _user_event_types


def _role_allows_env(env: str) -> bool:
    return env in _user_envs


def _role_stage_filter() -> dict | None:
    """Return an ES filter that restricts approval stages to the user's
    scope (union across every detected role), or None for Admin / CLevel
    where there is no restriction."""
    if not _user_approval_stages:
        return None
    shoulds: list[dict] = []
    for s in _user_approval_stages:
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
    rerun_scope: str | None = None,
) -> tuple[int, int, int]:
    """Render a Prev / N of M / Next pager and return (page, start, end).

    Only renders when ``total > page_size``. When not needed, returns a
    no-op window ``(1, 0, total)`` so callers can always slice with the
    returned range. Session state is the single source of truth for the
    current page — buttons mutate it then rely on the fragment-auto-rerun
    that follows a widget interaction.

    ``rerun_scope`` toggles the rerun granularity. Pass ``"fragment"``
    when the pager is rendered inside a ``@st.fragment`` so a page click
    only re-runs that fragment instead of the whole app — keeps the
    inventory tile + table from redrawing when the user paginates the
    event log. Default ``None`` triggers a full app rerun (correct for
    the inventory pager which lives outside any fragment).
    """
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

    def _rerun() -> None:
        if rerun_scope == "fragment":
            st.rerun(scope="fragment")
        else:
            st.rerun()

    with st.container(key=container_key):
        _pc = st.columns([1.0, 1.0, 4.6, 1.0, 1.0], vertical_alignment="center")
        with _pc[0]:
            if st.button("◀  Prev", key=f"{page_key}_prev",
                         use_container_width=True,
                         disabled=_page <= 1,
                         help="Previous page"):
                st.session_state[page_key] = _page - 1
                _rerun()
        with _pc[1]:
            if st.button("⇤  First", key=f"{page_key}_first",
                         use_container_width=True,
                         disabled=_page <= 1,
                         help="Jump to first page"):
                st.session_state[page_key] = 1
                _rerun()
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
                _rerun()
        with _pc[4]:
            if st.button("Next  ▶", key=f"{page_key}_next",
                         use_container_width=True,
                         disabled=_page >= _max_page,
                         help="Next page"):
                st.session_state[page_key] = _page + 1
                _rerun()

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


# =============================================================================
# JENKINS PANEL — smart-loaded, auto-refreshing
# =============================================================================
# UX contract:
#   1. On first render the panel is *idle* — no API calls fire. A single
#      "▶ Load Jenkins panel" button is shown. This honours the user's
#      "smart loading" requirement: opening the page never costs a Jenkins
#      round-trip unless the operator actually wants to look.
#   2. Clicking the load button flips a session_state flag that sticks for
#      the rest of the session, so navigating away and back doesn't make
#      the user re-arm it.
#   3. Once active the panel runs as a fragment with run_every="30s" so
#      Jenkins state refreshes itself without a full page rerun. A manual
#      "↻ Refresh now" button busts the cache for users who want it
#      immediately.

_JK_LOAD_FLAG = "_jenkins_panel_loaded_v1"


def _jk_status_pill(result: str, is_running: bool) -> str:
    """Map a Jenkins build result/color into a small status pill."""
    if is_running:
        return ('<span class="jk-pill is-running">'
                '<span class="jk-pill-dot"></span>RUNNING</span>')
    if result == "SUCCESS":
        return '<span class="jk-pill is-ok">SUCCESS</span>'
    if result in ("FAILURE", "FAILED"):
        return '<span class="jk-pill is-fail">FAILURE</span>'
    if result == "ABORTED":
        return '<span class="jk-pill is-mute">ABORTED</span>'
    if result == "UNSTABLE":
        return '<span class="jk-pill is-warn">UNSTABLE</span>'
    if result:
        return f'<span class="jk-pill is-mute">{html.escape(result)}</span>'
    return '<span class="jk-pill is-mute">NO RUNS</span>'


def _jk_param_chips(params: dict) -> str:
    """Render the (project / application / branch / env / version) parameter
    set as compact chips. Empty/missing keys are silently skipped."""
    if not params:
        return '<span class="jk-param-empty">no parameters</span>'
    chips: list[str] = []
    for k in ("project", "application", "branch", "environment", "version"):
        v = params.get(k) or params.get(k.upper()) or params.get(k.capitalize())
        if v:
            chips.append(
                f'<span class="jk-param-chip">'
                f'<span class="jk-param-k">{k}</span>'
                f'<span class="jk-param-v">{html.escape(str(v))}</span>'
                f'</span>'
            )
    # Surface any unknown params verbatim so we don't hide useful metadata.
    for k, v in params.items():
        if k.lower() in ("project", "application", "branch", "environment", "version"):
            continue
        if not v:
            continue
        chips.append(
            f'<span class="jk-param-chip is-other">'
            f'<span class="jk-param-k">{html.escape(str(k))}</span>'
            f'<span class="jk-param-v">{html.escape(str(v))}</span>'
            f'</span>'
        )
    return "".join(chips) if chips else '<span class="jk-param-empty">no parameters</span>'


def _jk_relative(when_ms: int) -> str:
    """Convert a Jenkins millisecond timestamp into a short relative phrase."""
    if not when_ms:
        return ""
    try:
        dt = datetime.fromtimestamp(when_ms / 1000, tz=timezone.utc)
    except (ValueError, OSError):
        return ""
    return _relative_age(dt) or ""


def _jk_duration(ms: int) -> str:
    """Render a duration in ms as a compact human label."""
    if not ms or ms < 0:
        return ""
    s = ms // 1000
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m {s % 60}s"
    h = m // 60
    return f"{h}h {m % 60}m"


# =============================================================================
# INTEGRATIONS HEALTH STRIP — admin-only, "clearly but subtly"
# =============================================================================
# A single compact chip row that reports the state of every external
# integration the dashboard depends on:
#   • Elasticsearch  — the legacy projection + the fallback path for inventory
#   • Git inventories — the authoritative Ansible repo (when configured)
#   • Vault          — credential source for Jenkins + S3
#   • Jenkins        — pipeline status panel
#   • S3 (Prisma)    — scan-report viewer
#   • Optional deps  — PyYAML / ansible.parsing.vault availability
#
# Each integration has one of four states:
#   ok    → working, green dot
#   warn  → degraded but usable, amber dot
#   down  → broken, red dot
#   skip  → not configured / not loaded yet, muted dot
#
# The row collapses behind a <details>: the always-visible header carries
# the small count summary (e.g. "5 ok · 1 skip"); expanding it reveals a
# per-integration detail card with the message and a hover tip. Designed
# to stay quiet on a healthy day and visibly surface a failing integration
# without dominating the page.

def _integrations_health() -> list[dict]:
    """Probe every integration the dashboard depends on. Each call is
    cheap (no network round-trips beyond what was already cached) so the
    strip can re-render on every page rerun without measurable cost.

    Returns a list of dicts each carrying ``key``, ``label``, ``glyph``,
    ``state`` (one of ok/warn/down/skip), ``detail`` (one-line),
    ``tip`` (longer hover text)."""
    out: list[dict] = []

    # 1. Elasticsearch — peek at the inventory-choices loader which is
    # already cached. If it returned data, ES is reachable & populated.
    try:
        comps, projs = _load_inventory_choices()
        if comps or projs:
            out.append({
                "key": "es", "label": "Elasticsearch", "glyph": "Σ",
                "state": "ok",
                "detail": f"{len(comps)} companies · {len(projs)} projects",
                "tip": "Inventory index reachable; aggregates flowing.",
            })
        else:
            out.append({
                "key": "es", "label": "Elasticsearch", "glyph": "Σ",
                "state": "warn",
                "detail": "0 aggregates returned",
                "tip": "ES is reachable but the inventory index is empty.",
            })
    except Exception as e:
        out.append({
            "key": "es", "label": "Elasticsearch", "glyph": "Σ",
            "state": "down",
            "detail": type(e).__name__,
            "tip": f"ES query failed: {e}",
        })

    # 2. Git inventories — host comes from vault path GIT_VAULT_PATH.
    _git_host = _git_creds().get("hostname", "")
    if not _git_host:
        v_err = _vault_last_error(GIT_VAULT_PATH)
        out.append({
            "key": "git", "label": "Git inventories", "glyph": "⎇",
            "state": "down" if v_err else "skip",
            "detail": "host unresolved",
            "tip": (
                (f"Vault error at {GIT_VAULT_PATH!r}: {v_err}" if v_err else
                 f"Add an ADO entry to vault path {GIT_VAULT_PATH!r} "
                 "(nested keys: ado.hostname / ado.username / ado.password) "
                 "to read inventory from the authoritative Ansible repo. "
                 "While unset the page reads from the ES projection.")
            ),
        })
    else:
        ok, head, msg = _ensure_inventory_repo(_git_host)
        if ok:
            out.append({
                "key": "git", "label": "Git inventories", "glyph": "⎇",
                "state": "ok",
                "detail": (head[:8] if head else "OK"),
                "tip": (
                    f"clone path: {INVENTORY_REPO_PATH} · "
                    f"branch: {INVENTORY_BRANCH} · {msg}"
                ),
            })
        else:
            out.append({
                "key": "git", "label": "Git inventories", "glyph": "⎇",
                "state": "down",
                "detail": "sync failed",
                "tip": msg or "Git sync failed — page is on ES fallback.",
            })

    # 3. Vault — single status across all known paths.
    if not _VAULT_AVAILABLE:
        out.append({
            "key": "vault", "label": "Vault", "glyph": "🔐",
            "state": "skip",
            "detail": "utils.vault missing",
            "tip": (
                "VaultClient not importable. Jenkins/S3 fall through to "
                "env-var creds when present."
            ),
        })
    else:
        store = st.session_state.get(_VAULT_ERR_KEY) or {}
        errors = [(p, e) for p, e in store.items() if e]
        if errors:
            tip = " · ".join(f"{p}: {e}" for p, e in errors)
            out.append({
                "key": "vault", "label": "Vault", "glyph": "🔐",
                "state": "down",
                "detail": f"{len(errors)} path err",
                "tip": tip[:280],
            })
        else:
            out.append({
                "key": "vault", "label": "Vault", "glyph": "🔐",
                "state": "ok",
                "detail": "resolving cleanly",
                "tip": "All recent vault reads succeeded.",
            })

    # 4. Jenkins — three states: skip (host unset), skip (panel idle),
    # ok / warn / down (panel loaded; mirror its cached status).
    creds = _jenkins_creds()
    j_host = creds.get("host")
    if not j_host:
        out.append({
            "key": "jenkins", "label": "Jenkins", "glyph": "⚙",
            "state": "skip",
            "detail": "host unresolved",
            "tip": (
                f"No Jenkins host from vault path {JENKINS_VAULT_PATH!r} "
                "or JENKINS_HOSTNAME env var."
            ),
        })
    elif not st.session_state.get(_JK_LOAD_FLAG):
        out.append({
            "key": "jenkins", "label": "Jenkins", "glyph": "⚙",
            "state": "skip",
            "detail": "panel idle",
            "tip": (
                f"Configured for {creds.get('public_name') or j_host}. "
                f"Open the JENKINS tab and click ▶ Load to probe."
            ),
        })
    else:
        try:
            status = _fetch_jenkins_status_raw()
            if status.get("ok"):
                out.append({
                    "key": "jenkins", "label": "Jenkins", "glyph": "⚙",
                    "state": "ok",
                    "detail": status.get("status_msg", "connected"),
                    "tip": (
                        f"{status.get('public_name') or j_host} · "
                        f"running v{status.get('version', {}).get('running', '?')}"
                    ),
                })
            else:
                out.append({
                    "key": "jenkins", "label": "Jenkins", "glyph": "⚙",
                    "state": "down",
                    "detail": "unreachable",
                    "tip": status.get("status_msg") or j_host,
                })
        except Exception as e:
            out.append({
                "key": "jenkins", "label": "Jenkins", "glyph": "⚙",
                "state": "warn",
                "detail": type(e).__name__,
                "tip": str(e),
            })

    # 5. S3 (Prisma scans).
    if not _BOTO3_AVAILABLE:
        out.append({
            "key": "s3", "label": "S3 (prisma)", "glyph": "⛟",
            "state": "skip",
            "detail": "boto3 missing",
            "tip": "Install boto3 to enable the scan viewer.",
        })
    elif not PRISMA_S3_BUCKET:
        out.append({
            "key": "s3", "label": "S3 (prisma)", "glyph": "⛟",
            "state": "skip",
            "detail": "bucket unset",
            "tip": "Set PRISMA_S3_BUCKET to enable.",
        })
    else:
        s3_creds = _prisma_s3_creds()
        if not s3_creds:
            err = _vault_last_error(PRISMA_S3_VAULT_PATH)
            out.append({
                "key": "s3", "label": "S3 (prisma)", "glyph": "⛟",
                "state": "down",
                "detail": "creds unresolved",
                "tip": (
                    err
                    or f"No vault entry at {PRISMA_S3_VAULT_PATH!r}."
                ),
            })
        else:
            ep = _prisma_s3_endpoint(s3_creds["host"], s3_creds["port"])
            out.append({
                "key": "s3", "label": "S3 (prisma)", "glyph": "⛟",
                "state": "ok",
                "detail": "ready",
                "tip": f"endpoint: {ep} · bucket: {PRISMA_S3_BUCKET}",
            })

    # 6. Postgres devops_projects — only meaningful when configured.
    if not _POSTGRES_AVAILABLE:
        out.append({
            "key": "postgres", "label": "Postgres", "glyph": "🗂",
            "state": "skip",
            "detail": "driver missing",
            "tip": "pip install psycopg[binary] or psycopg2 to enable.",
        })
    else:
        pg_creds = _postgres_creds()
        if not pg_creds.get("host"):
            v_err = _vault_last_error(POSTGRES_VAULT_PATH)
            out.append({
                "key": "postgres", "label": "Postgres", "glyph": "🗂",
                "state": "down" if v_err else "skip",
                "detail": "creds unresolved",
                "tip": (
                    v_err or f"No vault entry at {POSTGRES_VAULT_PATH!r}."
                ),
            })
        else:
            # Don't actually connect on every render — too expensive. Trust
            # that creds resolution succeeded and surface "ready" until the
            # admin runs the comparison panel (which is where real errors
            # show up).
            out.append({
                "key": "postgres", "label": "Postgres", "glyph": "🗂",
                "state": "ok",
                "detail": "creds resolved",
                "tip": (
                    f"{pg_creds['username']}@{pg_creds['host']}:"
                    f"{pg_creds['port']}/{pg_creds['database']} · "
                    f"table {POSTGRES_TABLE}"
                ),
            })

    # 7. Inventory sync check — surfaces the last-known diff count so
    # admins see drift across sources without opening the dedicated tab.
    sync_sum = st.session_state.get("_sync_summary_v1") or {}
    if not sync_sum:
        out.append({
            "key": "sync", "label": "Sync check", "glyph": "🔀",
            "state": "skip",
            "detail": "not run",
            "tip": (
                "Open the SYNC CHECK tab and click ▶ Run to diff the git "
                "inventory against the Elasticsearch projection for the "
                "current scope."
            ),
        })
    else:
        total = int(sync_sum.get("total") or 0)
        errs  = sync_sum.get("errors") or {}
        if errs.get("git") or errs.get("es"):
            out.append({
                "key": "sync", "label": "Sync check", "glyph": "🔀",
                "state": "down",
                "detail": "fetch errors",
                "tip": " · ".join(
                    f"{k}: {v}" for k, v in errs.items() if v
                ) or "see SYNC CHECK tab",
            })
        elif total == 0:
            out.append({
                "key": "sync", "label": "Sync check", "glyph": "🔀",
                "state": "ok",
                "detail": "clean",
                "tip": "Last comparison: git and ES agree completely.",
            })
        else:
            out.append({
                "key": "sync", "label": "Sync check", "glyph": "🔀",
                "state": "warn",
                "detail": f"{total} drift",
                "tip": (
                    f"{total} discrepancy/ies recorded in the last run — "
                    f"open the SYNC CHECK tab for the breakdown."
                ),
            })

    # 8. Optional deps — soft signal so admins know which features
    # would light up if a missing package were installed.
    missing_deps: list[str] = []
    if not _YAML_AVAILABLE:
        missing_deps.append("PyYAML")
    if not _ANSIBLE_VAULT_AVAILABLE:
        missing_deps.append("ansible.parsing.vault")
    if missing_deps:
        out.append({
            "key": "deps", "label": "Optional deps", "glyph": "📦",
            "state": "warn",
            "detail": f"{len(missing_deps)} missing",
            "tip": "Missing: " + ", ".join(missing_deps),
        })
    else:
        out.append({
            "key": "deps", "label": "Optional deps", "glyph": "📦",
            "state": "ok",
            "detail": "all present",
            "tip": "PyYAML + ansible.parsing.vault both importable.",
        })

    return out


def _render_integrations_strip() -> None:
    """Admin-only chip row + collapsible detail block. See section header."""
    health = _integrations_health()
    counts = {s: sum(1 for h in health if h["state"] == s)
              for s in ("ok", "warn", "skip", "down")}

    # Worst state drives the strip's outer hue — green when everything's
    # ok, amber on warn/skip-only, red as soon as anything's down.
    if counts["down"]:
        outer = "down"
    elif counts["warn"]:
        outer = "warn"
    elif counts["ok"] and not counts["skip"]:
        outer = "ok"
    else:
        outer = "mixed"

    sum_bits: list[str] = []
    for state, label in (("down", "down"), ("warn", "warn"),
                         ("ok", "ok"), ("skip", "skip")):
        if counts[state]:
            sum_bits.append(
                f'<span class="ih-sum is-{state}">{counts[state]} {label}</span>'
            )

    # Always-visible chip row inside the <summary>. Each chip carries the
    # tip as title= so the operator can hover for full detail without
    # expanding the section.
    chip_html: list[str] = []
    for h in health:
        chip_html.append(
            f'<span class="ih-chip is-{h["state"]}" '
            f'title="{html.escape(h["tip"], quote=True)}">'
            f'  <span class="ih-chip-dot"></span>'
            f'  <span class="ih-chip-glyph">{html.escape(h["glyph"])}</span>'
            f'  <span class="ih-chip-lbl">{html.escape(h["label"])}</span>'
            f'  <span class="ih-chip-detail">{html.escape(h["detail"])}</span>'
            f'</span>'
        )

    # Expanded detail — one card per integration with the full tip.
    card_html: list[str] = []
    for h in health:
        card_html.append(
            f'<div class="ih-card is-{h["state"]}">'
            f'  <div class="ih-card-head">'
            f'    <span class="ih-card-dot"></span>'
            f'    <span class="ih-card-glyph">{html.escape(h["glyph"])}</span>'
            f'    <span class="ih-card-lbl">{html.escape(h["label"])}</span>'
            f'    <span class="ih-card-state">{h["state"].upper()}</span>'
            f'  </div>'
            f'  <div class="ih-card-detail">{html.escape(h["detail"])}</div>'
            f'  <div class="ih-card-tip">{html.escape(h["tip"])}</div>'
            f'</div>'
        )

    st.markdown(
        f'<details class="ih-strip is-outer-{outer}">'
        f'  <summary class="ih-strip-head">'
        f'    <span class="ih-strip-lbl">Integrations</span>'
        f'    <span class="ih-strip-counts">{"".join(sum_bits)}</span>'
        f'    <span class="ih-strip-chips">{"".join(chip_html)}</span>'
        f'    <span class="ih-strip-toggle">▾</span>'
        f'  </summary>'
        f'  <div class="ih-strip-cards">{"".join(card_html)}</div>'
        f'</details>',
        unsafe_allow_html=True,
    )


# =============================================================================
# SYNC CHECK PANEL — admin-only, smart-loaded
# =============================================================================
# When both git and Elasticsearch are reachable, the platform exposes the
# inventory twice. Drift between the two is normal until the projection
# refreshes, and pathological when an app exists in one but not the other,
# or a row's metadata disagrees. This panel lets the admin run the diff on
# demand. Each run does two full fetches + a comparison — too heavy for an
# auto-refresh, so we gate it behind an explicit ▶ Run button. The result
# is stashed in session_state for the rest of the session, with manual
# "↻ Re-run" / "✕ Clear" controls.

_SYNC_LOADED_KEY = "_sync_check_loaded_v1"  # holds the last diff dict


def _sync_count_total_diffs(diff: dict) -> int:
    """Number of distinct discrepancies for the integrations strip / badge."""
    return (
        len(diff.get("only_in_git") or [])
        + len(diff.get("only_in_es") or [])
        + len(diff.get("field_diffs") or [])
    )


def _render_sync_value(val: Any, side: str) -> str:
    """Render a value cell for the diff list. List / sequence values
    collapse to chips; missing values become a muted "—"."""
    if isinstance(val, (list, tuple)):
        if not val:
            return f'<span class="sync-cell-empty {side}">—</span>'
        return "".join(
            f'<span class="sync-cell-chip {side}">{html.escape(str(v))}</span>'
            for v in val
        )
    if val in (None, ""):
        return f'<span class="sync-cell-empty {side}">—</span>'
    return f'<span class="sync-cell-val {side}">{html.escape(str(val))}</span>'


def _render_sync_check_panel(scope_json: str) -> None:
    """Admin-only sync-check panel. See section header for the UX contract."""
    # ── Idle gate ──────────────────────────────────────────────────────────
    if not st.session_state.get(_SYNC_LOADED_KEY):
        st.markdown(
            '<div class="sync-gate">'
            '  <div class="sync-gate-glyph">🔀</div>'
            '  <div class="sync-gate-title">Run inventory sync check</div>'
            '  <div class="sync-gate-body">'
            '    Compares the git inventory against the Elasticsearch '
            '    projection for the <b>current scope</b> and surfaces:'
            '    <ul>'
            '      <li>applications present only in git</li>'
            '      <li>applications present only in Elasticsearch</li>'
            '      <li>applications in both that disagree on company / '
            '          tech / image / teams</li>'
            '    </ul>'
            '    Two full inventory fetches per run — gated behind the '
            '    button so the rest of the page never pays this cost.'
            '  </div>'
            '</div>',
            unsafe_allow_html=True,
        )
        _gc1, _gc2, _gc3 = st.columns([1, 2, 1])
        with _gc2:
            if st.button("▶  Run sync check",
                         key="_sync_run_btn",
                         type="primary",
                         use_container_width=True):
                with st.spinner("Comparing git vs Elasticsearch..."):
                    diff = _inventory_compare(scope_json)
                st.session_state[_SYNC_LOADED_KEY] = diff
                # Publish a tiny summary so the Integrations strip can
                # show "sync clean" / "N diffs" without re-running.
                st.session_state["_sync_summary_v1"] = {
                    "total":   _sync_count_total_diffs(diff),
                    "checked_at": diff.get("checked_at", ""),
                    "errors":  diff.get("errors", {}),
                }
                st.rerun()
        return

    diff = st.session_state.get(_SYNC_LOADED_KEY) or {}

    # ── Control row ────────────────────────────────────────────────────────
    _cc1, _cc2, _cc3 = st.columns([1, 1, 6])
    with _cc1:
        if st.button("↻ Re-run", key="_sync_rerun_btn",
                     use_container_width=True):
            with st.spinner("Re-comparing..."):
                diff = _inventory_compare(scope_json)
            st.session_state[_SYNC_LOADED_KEY] = diff
            st.session_state["_sync_summary_v1"] = {
                "total": _sync_count_total_diffs(diff),
                "checked_at": diff.get("checked_at", ""),
                "errors": diff.get("errors", {}),
            }
            st.rerun()
    with _cc2:
        if st.button("✕ Clear", key="_sync_clear_btn",
                     use_container_width=True):
            st.session_state.pop(_SYNC_LOADED_KEY, None)
            st.session_state.pop("_sync_summary_v1", None)
            st.rerun()
    with _cc3:
        _ts = (diff.get("checked_at") or "").replace("T", " ")[:19]
        st.caption(
            f"comparison run at {_ts} UTC · result kept in session until "
            f"cleared or re-run · scope mirrors every active filter"
        )

    # ── Error-state shortcuts ──────────────────────────────────────────────
    errors = diff.get("errors") or {}
    err_git, err_es = errors.get("git") or "", errors.get("es") or ""
    if err_git or err_es:
        st.markdown(
            f'<div class="sync-errs">'
            + (f'<div class="sync-errs-line"><span class="sync-errs-k">Git:</span>'
               f'<code>{html.escape(err_git)}</code></div>' if err_git else "")
            + (f'<div class="sync-errs-line"><span class="sync-errs-k">ES:</span>'
               f'<code>{html.escape(err_es)}</code></div>' if err_es else "")
            + '</div>',
            unsafe_allow_html=True,
        )
        if err_git and err_es:
            # Both sides failed — nothing useful to compare.
            inline_note(
                "Both sources failed to load. The Integrations strip at the "
                "top has the per-source detail; sync check can't proceed.",
                "warning",
            )
            return

    # ── Summary tiles ──────────────────────────────────────────────────────
    git_total  = diff.get("git_total", 0)
    es_total   = diff.get("es_total", 0)
    common     = diff.get("common", 0)
    only_git   = diff.get("only_in_git", []) or []
    only_es    = diff.get("only_in_es", []) or []
    field_diffs = diff.get("field_diffs", []) or []
    total_drift = len(only_git) + len(only_es) + len(field_diffs)
    drift_state = "clean" if total_drift == 0 else "drift"

    st.markdown(
        f'<div class="sync-summary is-{drift_state}">'
        f'  <div class="sync-tile">'
        f'    <div class="sync-tile-lbl">Git</div>'
        f'    <div class="sync-tile-val">{git_total:,}</div>'
        f'  </div>'
        f'  <div class="sync-tile">'
        f'    <div class="sync-tile-lbl">Elasticsearch</div>'
        f'    <div class="sync-tile-val">{es_total:,}</div>'
        f'  </div>'
        f'  <div class="sync-tile">'
        f'    <div class="sync-tile-lbl">In both</div>'
        f'    <div class="sync-tile-val">{common:,}</div>'
        f'  </div>'
        f'  <div class="sync-tile is-only is-only-git">'
        f'    <div class="sync-tile-lbl">Only in git</div>'
        f'    <div class="sync-tile-val">{len(only_git):,}</div>'
        f'  </div>'
        f'  <div class="sync-tile is-only is-only-es">'
        f'    <div class="sync-tile-lbl">Only in ES</div>'
        f'    <div class="sync-tile-val">{len(only_es):,}</div>'
        f'  </div>'
        f'  <div class="sync-tile is-field">'
        f'    <div class="sync-tile-lbl">Field diffs</div>'
        f'    <div class="sync-tile-val">{len(field_diffs):,}</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if total_drift == 0:
        st.markdown(
            '<div class="sync-clean">'
            '  <span class="sync-clean-glyph">✓</span>'
            '  <span>The two sources agree completely for the current '
            '  scope. Every app exists in both and every compared field '
            '  matches.</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Only-in-X lists ────────────────────────────────────────────────────
    def _render_only_list(title: str, glyph: str, cls: str, apps: list[str]) -> str:
        if not apps:
            return ""
        chips = "".join(
            f'<span class="sync-only-chip {cls}">{html.escape(a)}</span>'
            for a in apps[:200]
        )
        overflow = (
            f'<span class="sync-only-more">+{len(apps) - 200} more</span>'
            if len(apps) > 200 else ""
        )
        return (
            f'<div class="sync-section {cls}">'
            f'  <div class="sync-section-head">'
            f'    <span class="sync-section-glyph">{glyph}</span>'
            f'    <span class="sync-section-title">{title}</span>'
            f'    <span class="sync-section-count">{len(apps):,}</span>'
            f'  </div>'
            f'  <div class="sync-only-chips">{chips}{overflow}</div>'
            f'</div>'
        )

    only_html = _render_only_list(
        "Only in git", "⎇", "is-only-git", only_git,
    ) + _render_only_list(
        "Only in Elasticsearch", "Σ", "is-only-es", only_es,
    )
    if only_html:
        st.markdown(only_html, unsafe_allow_html=True)

    # ── Field-diff list — collapsible per app ──────────────────────────────
    if field_diffs:
        st.markdown(
            f'<div class="sync-section is-field">'
            f'  <div class="sync-section-head">'
            f'    <span class="sync-section-glyph">≠</span>'
            f'    <span class="sync-section-title">Field discrepancies</span>'
            f'    <span class="sync-section-count">{len(field_diffs):,}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for d in field_diffs[:200]:
            _app = html.escape(d["application"])
            _proj = html.escape(d["project"] or "—")
            _diff_rows: list[str] = []
            for f, val in d["differences"].items():
                if f == "__teams__":
                    for tk, tv in (val or {}).items():
                        _diff_rows.append(
                            f'<tr>'
                            f'  <td class="sync-diff-k">{html.escape(tk)}</td>'
                            f'  <td class="sync-diff-side is-git">{_render_sync_value(tv["git"], "is-git")}</td>'
                            f'  <td class="sync-diff-side is-es">{_render_sync_value(tv["es"], "is-es")}</td>'
                            f'</tr>'
                        )
                else:
                    _diff_rows.append(
                        f'<tr>'
                        f'  <td class="sync-diff-k">{html.escape(f)}</td>'
                        f'  <td class="sync-diff-side is-git">{_render_sync_value(val["git"], "is-git")}</td>'
                        f'  <td class="sync-diff-side is-es">{_render_sync_value(val["es"], "is-es")}</td>'
                        f'</tr>'
                    )
            st.markdown(
                f'<details class="sync-diff-card">'
                f'  <summary>'
                f'    <span class="sync-diff-app">{_app}</span>'
                f'    <span class="sync-diff-proj">{_proj}</span>'
                f'    <span class="sync-diff-count">{len(d["differences"])} field'
                f'{"s" if len(d["differences"]) != 1 else ""} differ</span>'
                f'  </summary>'
                f'  <table class="sync-diff-table">'
                f'    <thead><tr>'
                f'      <th>Field</th>'
                f'      <th class="is-git">Git</th>'
                f'      <th class="is-es">Elasticsearch</th>'
                f'    </tr></thead>'
                f'    <tbody>{"".join(_diff_rows)}</tbody>'
                f'  </table>'
                f'</details>',
                unsafe_allow_html=True,
            )
        if len(field_diffs) > 200:
            inline_note(
                f"Showing first 200 of {len(field_diffs)} field-discrepant "
                f"apps — narrow filters to inspect the rest.",
                "info",
            )


# =============================================================================
# INVENTORY ↔ POSTGRES PANEL — admin-only, smart-loaded
# =============================================================================
# Second comparison panel inside the SYNC CHECK tab. Diffs (company, project)
# coverage and per-project teams between the live inventory (whichever source
# is active) and the Postgres ``devops_projects`` table. Surfaces the
# inventory's internal ops-team inconsistency separately when uat / prd /
# preprod teams disagree within a single project.

_PG_CHECK_LOADED_KEY = "_pg_check_loaded_v1"


def _render_pg_team_value(val: Any, side: str) -> str:
    """Reuse the sync-check value cell renderer for Postgres comparison."""
    return _render_sync_value(val, side)


def _render_postgres_compare_panel(scope_json: str) -> None:
    """Admin-only inventory ↔ Postgres comparison. Same smart-load pattern
    as the git-vs-ES panel — gated behind ▶ Run."""

    if not _POSTGRES_AVAILABLE:
        st.markdown(
            '<div class="sync-gate">'
            '  <div class="sync-gate-glyph">🗂</div>'
            '  <div class="sync-gate-title">Postgres driver not installed</div>'
            '  <div class="sync-gate-body">Install psycopg v3 '
            '  (<code>pip install psycopg[binary]</code>) or psycopg2 '
            '  on the streamlit host to enable this comparison.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    if not st.session_state.get(_PG_CHECK_LOADED_KEY):
        st.markdown(
            '<div class="sync-gate">'
            '  <div class="sync-gate-glyph">🗂</div>'
            '  <div class="sync-gate-title">'
            '    Run inventory ↔ Postgres check'
            '  </div>'
            '  <div class="sync-gate-body">'
            '    Compares the live inventory (current scope, current source) '
            '    against the Postgres <code>devops_projects</code> table at '
            '    the <b>(company, project)</b> granularity. Surfaces:'
            '    <ul>'
            '      <li>projects present only in inventory or only in Postgres</li>'
            '      <li>per-project team mismatches on <code>dev_team</code>, '
            '          <code>qc_team</code>, <code>ops_team</code></li>'
            '      <li><b>Ops inconsistency</b> within the inventory — when '
            '          <code>uat_team</code>, <code>prd_team</code>, '
            '          <code>preprod_team</code> disagree, that\'s flagged '
            '          regardless of what Postgres says.</li>'
            '    </ul>'
            '    One Postgres SELECT + one inventory aggregation per run. '
            '    Gated so the rest of the page never pays this cost.'
            '  </div>'
            '</div>',
            unsafe_allow_html=True,
        )
        _gc1, _gc2, _gc3 = st.columns([1, 2, 1])
        with _gc2:
            if st.button("▶  Run Postgres check",
                         key="_pg_check_run_btn",
                         type="primary",
                         use_container_width=True):
                with st.spinner("Comparing inventory vs Postgres..."):
                    diff = _inventory_vs_postgres_compare(scope_json)
                st.session_state[_PG_CHECK_LOADED_KEY] = diff
                st.session_state["_pg_check_summary_v1"] = {
                    "total": (
                        len(diff.get("only_in_inv") or [])
                        + len(diff.get("only_in_pg") or [])
                        + len(diff.get("diffs") or [])
                    ),
                    "checked_at": diff.get("checked_at", ""),
                    "errors": diff.get("errors", {}),
                    "ops_inconsistent": sum(
                        1 for d in (diff.get("diffs") or [])
                        if d.get("ops_inconsistent")
                    ),
                }
                st.rerun()
        return

    diff = st.session_state.get(_PG_CHECK_LOADED_KEY) or {}

    # ── Control row ────────────────────────────────────────────────────────
    _cc1, _cc2, _cc3 = st.columns([1, 1, 6])
    with _cc1:
        if st.button("↻ Re-run", key="_pg_check_rerun_btn",
                     use_container_width=True):
            with st.spinner("Re-comparing..."):
                diff = _inventory_vs_postgres_compare(scope_json)
            st.session_state[_PG_CHECK_LOADED_KEY] = diff
            st.session_state["_pg_check_summary_v1"] = {
                "total": (
                    len(diff.get("only_in_inv") or [])
                    + len(diff.get("only_in_pg") or [])
                    + len(diff.get("diffs") or [])
                ),
                "checked_at": diff.get("checked_at", ""),
                "errors": diff.get("errors", {}),
                "ops_inconsistent": sum(
                    1 for d in (diff.get("diffs") or [])
                    if d.get("ops_inconsistent")
                ),
            }
            st.rerun()
    with _cc2:
        if st.button("✕ Clear", key="_pg_check_clear_btn",
                     use_container_width=True):
            st.session_state.pop(_PG_CHECK_LOADED_KEY, None)
            st.session_state.pop("_pg_check_summary_v1", None)
            st.rerun()
    with _cc3:
        _ts = (diff.get("checked_at") or "").replace("T", " ")[:19]
        _src = diff.get("inv_source") or "?"
        st.caption(
            f"comparison run at {_ts} UTC · inventory source: {_src} · "
            f"Postgres table: {POSTGRES_TABLE}"
        )

    errors = diff.get("errors") or {}
    err_inv, err_pg = errors.get("inventory") or "", errors.get("postgres") or ""
    if err_inv or err_pg:
        st.markdown(
            f'<div class="sync-errs">'
            + (f'<div class="sync-errs-line"><span class="sync-errs-k">Inventory:</span>'
               f'<code>{html.escape(err_inv)}</code></div>' if err_inv else "")
            + (f'<div class="sync-errs-line"><span class="sync-errs-k">Postgres:</span>'
               f'<code>{html.escape(err_pg)}</code></div>' if err_pg else "")
            + '</div>',
            unsafe_allow_html=True,
        )
        if err_inv and err_pg:
            inline_note(
                "Both sources failed to load — see the Integrations strip "
                "for per-source detail.",
                "warning",
            )
            return

    inv_total = diff.get("inv_total", 0)
    pg_total = diff.get("pg_total", 0)
    common = diff.get("common", 0)
    only_inv = diff.get("only_in_inv", []) or []
    only_pg = diff.get("only_in_pg", []) or []
    diffs = diff.get("diffs", []) or []
    n_inconsistent = sum(1 for d in diffs if d.get("ops_inconsistent"))
    total_drift = len(only_inv) + len(only_pg) + len(diffs)
    drift_state = "clean" if total_drift == 0 else "drift"

    st.markdown(
        f'<div class="sync-summary is-{drift_state}">'
        f'  <div class="sync-tile">'
        f'    <div class="sync-tile-lbl">Inventory</div>'
        f'    <div class="sync-tile-val">{inv_total:,}</div>'
        f'  </div>'
        f'  <div class="sync-tile">'
        f'    <div class="sync-tile-lbl">Postgres</div>'
        f'    <div class="sync-tile-val">{pg_total:,}</div>'
        f'  </div>'
        f'  <div class="sync-tile">'
        f'    <div class="sync-tile-lbl">In both</div>'
        f'    <div class="sync-tile-val">{common:,}</div>'
        f'  </div>'
        f'  <div class="sync-tile is-only is-only-git">'
        f'    <div class="sync-tile-lbl">Only inv.</div>'
        f'    <div class="sync-tile-val">{len(only_inv):,}</div>'
        f'  </div>'
        f'  <div class="sync-tile is-only is-only-es">'
        f'    <div class="sync-tile-lbl">Only pg</div>'
        f'    <div class="sync-tile-val">{len(only_pg):,}</div>'
        f'  </div>'
        f'  <div class="sync-tile is-field">'
        f'    <div class="sync-tile-lbl">Team diffs</div>'
        f'    <div class="sync-tile-val">{len(diffs):,}</div>'
        f'  </div>'
        f'  <div class="sync-tile pg-inconsistent">'
        f'    <div class="sync-tile-lbl">Ops inconsistent</div>'
        f'    <div class="sync-tile-val">{n_inconsistent:,}</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if total_drift == 0:
        st.markdown(
            '<div class="sync-clean">'
            '  <span class="sync-clean-glyph">✓</span>'
            '  <span>Inventory and Postgres agree on every project in '
            '  scope. <code>dev_team</code>, <code>qc_team</code>, and the '
            '  consolidated <code>ops_team</code> all match — and the '
            '  inventory itself is internally consistent on '
            '  uat / prd / preprod team ownership.</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Only-in-X lists ────────────────────────────────────────────────────
    if only_inv:
        chips = "".join(
            f'<span class="sync-only-chip is-only-git" '
            f'title="{html.escape(o["company"] or "—")}">'
            f'{html.escape(o["project"])}'
            f'<span class="pg-apps-n"> · {o["apps_n"]} app{"s" if o["apps_n"] != 1 else ""}</span>'
            f'</span>'
            for o in only_inv[:200]
        )
        overflow = (
            f'<span class="sync-only-more">+{len(only_inv) - 200} more</span>'
            if len(only_inv) > 200 else ""
        )
        st.markdown(
            f'<div class="sync-section is-only-git">'
            f'  <div class="sync-section-head">'
            f'    <span class="sync-section-glyph">⎇</span>'
            f'    <span class="sync-section-title">Only in inventory</span>'
            f'    <span class="sync-section-count">{len(only_inv):,}</span>'
            f'  </div>'
            f'  <div class="sync-only-chips">{chips}{overflow}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if only_pg:
        chips = "".join(
            f'<span class="sync-only-chip is-only-es" '
            f'title="dev: {html.escape(o["dev_team"] or "—")} · '
            f'qc: {html.escape(o["qc_team"] or "—")} · '
            f'ops: {html.escape(o["ops_team"] or "—")}">'
            f'{html.escape(o["project"])}'
            f'<span class="pg-apps-n"> · {html.escape(o["company"] or "—")}</span>'
            f'</span>'
            for o in only_pg[:200]
        )
        overflow = (
            f'<span class="sync-only-more">+{len(only_pg) - 200} more</span>'
            if len(only_pg) > 200 else ""
        )
        st.markdown(
            f'<div class="sync-section is-only-es">'
            f'  <div class="sync-section-head">'
            f'    <span class="sync-section-glyph">🗂</span>'
            f'    <span class="sync-section-title">Only in Postgres</span>'
            f'    <span class="sync-section-count">{len(only_pg):,}</span>'
            f'  </div>'
            f'  <div class="sync-only-chips">{chips}{overflow}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── Field-diff cards ───────────────────────────────────────────────────
    if diffs:
        st.markdown(
            f'<div class="sync-section is-field">'
            f'  <div class="sync-section-head">'
            f'    <span class="sync-section-glyph">≠</span>'
            f'    <span class="sync-section-title">Team discrepancies</span>'
            f'    <span class="sync-section-count">{len(diffs):,}</span>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for d in diffs[:200]:
            _co = html.escape(d["company"] or "—")
            _pj = html.escape(d["project"])
            _ops_warn = ""
            _ops_breakdown_html = ""
            if d.get("ops_inconsistent"):
                _ob = d.get("ops_breakdown") or {}
                _breakdown_rows = "".join(
                    f'<tr>'
                    f'  <td class="sync-diff-k">{env}_team</td>'
                    f'  <td class="sync-diff-side is-warn" colspan="2">'
                    f'    {_render_sync_value(_ob.get(env + "_team", []), "is-warn")}'
                    f'  </td>'
                    f'</tr>'
                    for env in ("uat", "prd", "preprod")
                )
                _ops_warn = (
                    '<div class="pg-ops-inconsistent" '
                    'title="Inventory rows in this project disagree on '
                    'who owns uat / prd / preprod — Postgres folds these '
                    'into one ops_team, so the inventory should too.">'
                    '⚠ ops teams differ across uat / prd / preprod'
                    '</div>'
                )
                _ops_breakdown_html = (
                    f'<table class="sync-diff-table pg-ops-breakdown">'
                    f'  <thead><tr>'
                    f'    <th>Env field</th>'
                    f'    <th colspan="2">Inventory team(s)</th>'
                    f'  </tr></thead>'
                    f'  <tbody>{_breakdown_rows}</tbody>'
                    f'</table>'
                )
            _field_rows: list[str] = []
            for fname, val in (d.get("fields") or {}).items():
                _field_rows.append(
                    f'<tr>'
                    f'  <td class="sync-diff-k">{html.escape(fname)}</td>'
                    f'  <td class="sync-diff-side is-git">'
                    f'    {_render_sync_value(val["inventory"], "is-git")}'
                    f'  </td>'
                    f'  <td class="sync-diff-side is-es">'
                    f'    {_render_sync_value(val["postgres"], "is-es")}'
                    f'  </td>'
                    f'</tr>'
                )
            _field_table = (
                f'<table class="sync-diff-table">'
                f'  <thead><tr>'
                f'    <th>Field</th>'
                f'    <th class="is-git">Inventory (union)</th>'
                f'    <th class="is-es">Postgres</th>'
                f'  </tr></thead>'
                f'  <tbody>{"".join(_field_rows)}</tbody>'
                f'</table>'
                if _field_rows else ""
            )
            _diff_count = (
                len(d.get("fields") or {})
                + (1 if d.get("ops_inconsistent") else 0)
            )
            st.markdown(
                f'<details class="sync-diff-card{" pg-card-inconsistent" if d.get("ops_inconsistent") else ""}">'
                f'  <summary>'
                f'    <span class="sync-diff-app">{_pj}</span>'
                f'    <span class="sync-diff-proj">{_co}</span>'
                f'    <span class="sync-diff-count">{_diff_count} issue'
                f'{"s" if _diff_count != 1 else ""}</span>'
                f'  </summary>'
                f'  {_ops_warn}'
                f'  {_field_table}'
                f'  {_ops_breakdown_html}'
                f'</details>',
                unsafe_allow_html=True,
            )
        if len(diffs) > 200:
            inline_note(
                f"Showing first 200 of {len(diffs)} team-discrepant "
                f"projects — narrow filters to inspect the rest.",
                "info",
            )


@st.fragment(run_every="30s")
def _render_jenkins_panel_active() -> None:
    """Active half of the Jenkins panel — fires the API call + draws the
    pipeline cards. Lives in its own fragment so the 30s refresh cadence
    doesn't redraw the rest of the page.

    NOTE: the ``run_every`` here is safe because every widget the fragment
    owns (the manual refresh button, the unload toggle) lives inside this
    function. The "fragment + run_every gotcha" only applies when filter
    state is mutated outside the fragment — not the case here.
    """
    # Manual controls row (refresh + collapse back to idle).
    _ctrl1, _ctrl2, _ctrl3 = st.columns([1, 1, 6])
    with _ctrl1:
        if st.button("↻ Refresh now", key="_jk_refresh_now",
                     use_container_width=True):
            _fetch_jenkins_status_raw.clear()
            st.rerun(scope="fragment")
    with _ctrl2:
        if st.button("⏸ Pause panel", key="_jk_pause",
                     use_container_width=True,
                     help="Stops the 30s auto-refresh until reopened"):
            st.session_state[_JK_LOAD_FLAG] = False
            st.rerun()
    with _ctrl3:
        st.caption(
            f"auto-refresh every {JENKINS_TTL}s · cached server-side · "
            f"all values via Jenkins REST API"
        )

    status = _fetch_jenkins_status_raw()

    # ── Header row: connection state + queue ───────────────────────────────
    if status["ok"]:
        _hdr_cls = "jk-hdr is-ok"
        _hdr_glyph = "●"
    else:
        _hdr_cls = "jk-hdr is-down"
        _hdr_glyph = "○"

    # Version pill — admin-only by feature requirement, but ALSO inherently
    # gated here because the entire Jenkins tab is admin-only. Three states:
    #   current  → quiet teal "v2.450"
    #   outdated → amber "v2.440 → 2.450 available"
    #   unknown  → muted "v2.440 · update check unavailable" (or just v? when
    #              the running version itself wasn't reported)
    _ver = status.get("version") or {}
    _running, _latest, _cmp = (
        (_ver.get("running") or "").strip(),
        (_ver.get("latest") or "").strip(),
        _ver.get("compare") or "unknown",
    )
    _ver_check_err = (_ver.get("check_error") or "").strip()
    if _running and _cmp == "outdated":
        _ver_pill = (
            f'<span class="jk-ver is-outdated" '
            f'title="Latest available: {html.escape(_latest)}">'
            f'<span class="jk-ver-glyph">⬆</span>'
            f'v{html.escape(_running)}'
            f'<span class="jk-ver-arrow">→</span>'
            f'<span class="jk-ver-target">{html.escape(_latest)}</span>'
            f'<span class="jk-ver-tag">UPDATE</span>'
            f'</span>'
        )
    elif _running and _cmp == "current":
        _ver_pill = (
            f'<span class="jk-ver is-current" '
            f'title="Up to date with the latest LTS / weekly core advertised '
            f'by the configured update site">'
            f'<span class="jk-ver-glyph">✓</span>'
            f'v{html.escape(_running)}'
            f'<span class="jk-ver-tag">LATEST</span>'
            f'</span>'
        )
    elif _running:
        _tip = _ver_check_err or "update site not reachable from Jenkins"
        _ver_pill = (
            f'<span class="jk-ver is-unknown" '
            f'title="Update check unavailable: {html.escape(_tip)}">'
            f'<span class="jk-ver-glyph">?</span>'
            f'v{html.escape(_running)}'
            f'<span class="jk-ver-tag">CHECK·N/A</span>'
            f'</span>'
        )
    else:
        # No X-Jenkins header — extremely unusual; degrade silently.
        _ver_pill = ""

    # Prefer the friendly public name from vault when present; fall back to
    # the host URL. Tooltip carries the actual host so the operator can
    # confirm which instance the panel is talking to.
    _label = status.get("public_name") or status.get("url") or "—"
    _label_tip = status.get("url") or _label
    st.markdown(
        f'<div class="{_hdr_cls}">'
        f'  <span class="jk-hdr-glyph">{_hdr_glyph}</span>'
        f'  <span class="jk-hdr-host" title="{html.escape(_label_tip, quote=True)}">'
        f'    {html.escape(_label)}'
        f'  </span>'
        f'  {_ver_pill}'
        f'  <span class="jk-hdr-stat">{html.escape(status.get("status_msg") or "")}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if not status["ok"]:
        inline_note(
            "Jenkins is not reachable. The panel will retry on the next "
            "refresh; check the hostname / token / network path.",
            "warning",
        )
        return

    # ── Pipeline cards ─────────────────────────────────────────────────────
    pipelines_state = status.get("pipelines") or {}
    cards: list[str] = []
    for key, cfg in JENKINS_PIPELINES.items():
        s = pipelines_state.get(key) or {}
        running = s.get("running") or []
        last = s.get("last_build")
        ready = (
            s.get("exists") and s.get("buildable") and not s.get("error")
        )
        ready_chip = (
            '<span class="jk-ready is-ready">✓ ready to trigger</span>'
            if ready else
            f'<span class="jk-ready is-blocked" title="'
            f'{html.escape(s.get("error") or "not buildable")}">'
            f'✕ not ready</span>'
        )

        # Last-build summary
        if last:
            _r = last.get("result") or ""
            _last_html = (
                f'<div class="jk-last">'
                f'  <span class="jk-last-lbl">last build</span>'
                f'  {_jk_status_pill(_r, False)}'
                f'  <span class="jk-last-meta">'
                f'    #{html.escape(str(last.get("number") or "—"))}'
                f'    · {_jk_relative(last.get("timestamp") or 0)}'
                f'    · {_jk_duration(last.get("duration") or 0)}'
                f'  </span>'
                f'</div>'
            )
        else:
            _last_html = (
                '<div class="jk-last">'
                '  <span class="jk-last-lbl">last build</span>'
                '  <span class="jk-pill is-mute">NO RUNS</span>'
                '</div>'
            )

        # Running builds
        if running:
            _rows: list[str] = []
            for r in running:
                _started = _jk_relative(r.get("started") or 0)
                _est = r.get("estimated") or 0
                _dur = r.get("duration") or 0
                _eta = ""
                if _est > 0:
                    _eta = f" · ETA {_jk_duration(max(_est - _dur, 0))}"
                _rows.append(
                    f'<div class="jk-running-row">'
                    f'  <span class="jk-pill is-running">'
                    f'    <span class="jk-pill-dot"></span>'
                    f'    #{html.escape(str(r.get("number") or "?"))}'
                    f'  </span>'
                    f'  <span class="jk-running-meta">'
                    f'    started {_started}{_eta}'
                    f'  </span>'
                    f'  <div class="jk-params">{_jk_param_chips(r.get("params") or {})}</div>'
                    f'</div>'
                )
            _running_html = (
                f'<div class="jk-running-block">'
                f'  <div class="jk-running-head">'
                f'    <span class="jk-running-lbl">'
                f'      ⏵ {len(running)} in flight'
                f'    </span>'
                f'  </div>'
                + "".join(_rows)
                + '</div>'
            )
        else:
            _running_html = (
                '<div class="jk-running-block is-quiet">'
                '  <span class="jk-running-quiet">no runs in flight</span>'
                '</div>'
            )

        cards.append(
            f'<div class="jk-card">'
            f'  <div class="jk-card-head">'
            f'    <span class="jk-card-glyph">{cfg["glyph"]}</span>'
            f'    <div class="jk-card-title-wrap">'
            f'      <div class="jk-card-kicker">{html.escape(cfg["path"])}</div>'
            f'      <div class="jk-card-title">{cfg["label"]}</div>'
            f'    </div>'
            f'    {ready_chip}'
            f'  </div>'
            f'  <div class="jk-card-summary">{html.escape(cfg["summary"])}</div>'
            f'  {_last_html}'
            f'  {_running_html}'
            f'</div>'
        )

    st.markdown(
        '<div class="jk-grid">' + "".join(cards) + '</div>',
        unsafe_allow_html=True,
    )


# =============================================================================
# PRISMA SCAN VIEWER PANEL — admin-only, lazy fetch
# =============================================================================
# UX contract:
#   - The viewer never auto-loads. The user picks (project →) application →
#     version, clicks "▶ Load full scan", THEN we fetch the S3 object.
#   - The picker pulls choices from the inventory data already in memory
#     (rows + ``_iv_prisma_map``), so no extra API traffic just to populate
#     the selectors.
#   - The fetched HTML is rendered inside a sandboxed iframe via
#     ``st.components.v1.html`` — Prisma reports embed scripts/styles, and
#     the iframe isolation keeps them from polluting the parent page.
#   - We persist the loaded scan in session state keyed on (app, version),
#     so flipping tabs / scrolling doesn't redownload it.

_PSV_LOADED_KEY = "_psv_loaded_v1"  # holds {"app": …, "ver": …, "html": …, "size": …, "key": …, "bucket": …, "region": …}


def _psv_inventory_options() -> tuple[list[str], dict, dict]:
    """Resolve the (project, app, version) options from already-loaded
    inventory + scan data so the viewer picker doesn't make extra API calls.

    Returns ``(apps_sorted, app_to_project, app_to_versions)`` where:
      ``app_to_project[app]``  → project string (best-effort) used to render
                                 the {project} placeholder in the key pattern.
      ``app_to_versions[app]`` → list of version strings known to have a
                                 prismacloud scan (sourced from session-state
                                 telemetry the inventory tab publishes), with
                                 a fallback to "any version we've heard of"
                                 if the scan map isn't present yet.
    """
    apps_meta: dict[str, str] = {}
    versions_by_app: dict[str, set[str]] = {}
    # The inventory fragment publishes the scoped app list every render; that
    # set is the right floor for the picker.
    for r in (st.session_state.get("_psv_app_rows") or []):
        a = r.get("application") or ""
        if not a:
            continue
        apps_meta.setdefault(a, r.get("project") or "")
    for (a, v) in (st.session_state.get("_psv_prisma_keys") or []):
        if not a or not v:
            continue
        apps_meta.setdefault(a, "")
        versions_by_app.setdefault(a, set()).add(v)
    return (
        sorted(apps_meta.keys(), key=str.lower),
        apps_meta,
        {a: sorted(vs, key=str.lower) for a, vs in versions_by_app.items()},
    )


def _render_prisma_scan_viewer() -> None:
    """Admin-only Prisma scan viewer. See module-level UX contract above."""
    # Empty-state when configuration is missing — keep it actionable so
    # the operator knows exactly which env var / vault path to set.
    if not _BOTO3_AVAILABLE:
        st.markdown(
            '<div class="psv-empty">'
            '  <div class="psv-empty-glyph">⚠</div>'
            '  <div class="psv-empty-title">boto3 not installed</div>'
            '  <div class="psv-empty-body">Install with '
            '  <code>pip install boto3</code> on the streamlit host '
            '  to enable the Prisma scan viewer.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return
    if not PRISMA_S3_BUCKET:
        st.markdown(
            '<div class="psv-empty">'
            '  <div class="psv-empty-glyph">🔬</div>'
            '  <div class="psv-empty-title">Prisma scan viewer not configured</div>'
            '  <div class="psv-empty-body">Set <code>PRISMA_S3_BUCKET</code> '
            '  (and optionally <code>PRISMA_S3_KEY_PATTERN</code> / '
            '  <code>PRISMA_S3_REGION</code>) in the environment, plus a '
            '  vault entry at <code>' + html.escape(PRISMA_S3_VAULT_PATH) +
            '  </code> with <code>host</code>, <code>port</code>, '
            '  <code>access_key</code>, <code>secret_key</code>. While '
            '  unconfigured the panel costs nothing.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return
    s3_creds = _prisma_s3_creds()
    if not s3_creds:
        _v_err = _vault_last_error(PRISMA_S3_VAULT_PATH)
        _err_block = (
            '<div class="psv-empty-err">'
            '  <span class="psv-empty-err-k">Vault error:</span>'
            f'  <code>{html.escape(_v_err)}</code>'
            '</div>'
            if _v_err else ""
        )
        st.markdown(
            '<div class="psv-empty">'
            '  <div class="psv-empty-glyph">🔐</div>'
            '  <div class="psv-empty-title">S3 credentials not resolved</div>'
            '  <div class="psv-empty-body">No vault entry found at '
            '  <code>' + html.escape(PRISMA_S3_VAULT_PATH) + '</code>. '
            '  Expected keys: <code>host</code>, <code>port</code>, '
            '  <code>access_key</code>, <code>secret_key</code> '
            '  (or <code>secret_id</code>).</div>'
            + _err_block +
            '</div>',
            unsafe_allow_html=True,
        )
        return
    s3_endpoint = _prisma_s3_endpoint(s3_creds["host"], s3_creds["port"])

    apps, app_to_project, app_to_versions = _psv_inventory_options()
    if not apps:
        st.markdown(
            '<div class="psv-empty">'
            '  <div class="psv-empty-glyph">⏳</div>'
            '  <div class="psv-empty-title">Inventory not loaded yet</div>'
            '  <div class="psv-empty-body">Open the Pipelines Inventory tab '
            '  once so the viewer can populate its picker — the version list '
            '  is sourced from the same data the inventory uses, no extra '
            '  API calls.</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # ── Picker row: app → version → load ──────────────────────────────────
    _c1, _c2, _c3 = st.columns([3, 2, 1])
    with _c1:
        app = st.selectbox(
            "Application",
            options=apps,
            key="_psv_app_v1",
            help="Pulled from the currently-loaded inventory scope.",
        )
    _versions = app_to_versions.get(app or "", [])
    with _c2:
        if _versions:
            version = st.selectbox(
                "Version",
                options=_versions,
                key="_psv_ver_v1",
                help="Versions that have a known Prismacloud scan record.",
            )
        else:
            version = st.text_input(
                "Version",
                key="_psv_ver_v1_free",
                placeholder="e.g. 1.4.2",
                help=(
                    "No scanned versions surfaced for this app yet — type "
                    "one manually; the viewer will try to fetch the matching "
                    "S3 object."
                ),
            ).strip()
    with _c3:
        st.markdown('<div style="height: 1.65rem"></div>', unsafe_allow_html=True)
        load = st.button(
            "▶  Load",
            key="_psv_load_btn",
            type="primary",
            use_container_width=True,
            disabled=not (app and version),
        )

    project = app_to_project.get(app or "", "")
    s3_key = _prisma_scan_s3_key(project, app, version) if (app and version) else ""

    # Optional cache-bust + reset row
    if st.session_state.get(_PSV_LOADED_KEY):
        _r1, _r2, _r3 = st.columns([1, 1, 6])
        with _r1:
            if st.button("↻ Re-fetch", key="_psv_refetch_btn",
                         use_container_width=True,
                         help="Bypasses the local cache for this scan"):
                _fetch_prisma_scan_html.clear()
                load = True  # fall through into the fetch block below
        with _r2:
            if st.button("✕ Clear", key="_psv_clear_btn",
                         use_container_width=True,
                         help="Drops the loaded scan from view"):
                st.session_state.pop(_PSV_LOADED_KEY, None)
                st.rerun()

    # ── Fetch on demand ────────────────────────────────────────────────────
    if load and app and version:
        html_doc, size, err = _fetch_prisma_scan_html(
            PRISMA_S3_BUCKET, s3_key, s3_endpoint, PRISMA_S3_REGION,
            s3_creds["access_key"], s3_creds["secret_key"],
        )
        if err:
            inline_note(
                f"Couldn't load the scan for {app} @ {version}: {err}. "
                f"S3 key tried: `{s3_key}`",
                "warning",
            )
        else:
            st.session_state[_PSV_LOADED_KEY] = {
                "app":      app,
                "ver":      version,
                "project":  project,
                "html":     html_doc,
                "size":     size,
                "key":      s3_key,
                "bucket":   PRISMA_S3_BUCKET,
                "endpoint": s3_endpoint,
            }
            st.rerun()

    # ── Render loaded scan ─────────────────────────────────────────────────
    loaded = st.session_state.get(_PSV_LOADED_KEY)
    if not loaded:
        st.markdown(
            '<div class="psv-hint">'
            '  Pick an application and version, then click '
            '  <b>▶ Load</b> to fetch the full Prismacloud HTML report from '
            '  S3. The viewer never touches the bucket until you click — '
            '  even tabbing here costs zero requests.'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # Header chrome — beautiful frame around the iframe.
    _kib = max(1, (loaded.get("size") or 0) // 1024)
    _open_url = _prisma_scan_console_url(
        loaded.get("endpoint", ""), loaded["bucket"], loaded["key"],
    )
    # Build the optional "open in console" link OUTSIDE the f-string —
    # f-string expressions can't contain backslash escapes, and the
    # attribute quoting needs them.
    _open_link_html = ""
    if _open_url:
        _open_link_html = (
            '<a class="psv-open" href="'
            + html.escape(_open_url, quote=True)
            + '" target="_blank" rel="noopener noreferrer">'
            '↗ Open in console</a>'
        )
    _key_safe = html.escape(loaded["key"])
    _key_tail = html.escape(loaded["key"][-48:])
    _app_safe = html.escape(loaded["app"])
    _ver_safe = html.escape(loaded["ver"])
    st.markdown(
        f'<div class="psv-frame-head">'
        f'  <div class="psv-frame-icon">⛟</div>'
        f'  <div class="psv-frame-title-wrap">'
        f'    <div class="psv-frame-kicker">Prismacloud full scan</div>'
        f'    <div class="psv-frame-title">'
        f'      {_app_safe}'
        f'      <span class="psv-frame-ver">@ {_ver_safe}</span>'
        f'    </div>'
        f'  </div>'
        f'  <div class="psv-frame-meta">'
        f'    <span class="psv-meta-chip">'
        f'      <span class="psv-meta-k">size</span>'
        f'      <span class="psv-meta-v">{_kib} KiB</span>'
        f'    </span>'
        f'    <span class="psv-meta-chip" title="{_key_safe}">'
        f'      <span class="psv-meta-k">key</span>'
        f'      <span class="psv-meta-v">{_key_tail}</span>'
        f'    </span>'
        f'    {_open_link_html}'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Sandboxed iframe with the report HTML. Streamlit's components.v1.html
    # generates a sandboxed iframe with srcdoc — perfect for embedded HTML
    # that may include its own <script> tags.
    try:
        import streamlit.components.v1 as _components
        _components.html(loaded["html"], height=900, scrolling=True)
    except Exception as e:
        inline_note(
            f"Failed to render the scan iframe: {type(e).__name__}: {e}",
            "warning",
        )


def _render_jenkins_panel() -> None:
    """Outer half of the Jenkins panel — handles the load gate. Idle until
    the operator clicks the load button; once flipped, hands off to the
    fragment-decorated active half so the 30s auto-refresh doesn't drag
    the rest of the page along with it."""
    if not _jenkins_creds().get("host"):
        _v_err = _vault_last_error(JENKINS_VAULT_PATH)
        _err_block = (
            '<div class="jk-empty-err">'
            '  <span class="jk-empty-err-k">Vault error:</span>'
            f'  <code>{html.escape(_v_err)}</code>'
            '</div>'
            if _v_err else ""
        )
        st.markdown(
            '<div class="jk-empty">'
            '  <div class="jk-empty-glyph">⚙</div>'
            '  <div class="jk-empty-title">Jenkins not configured</div>'
            '  <div class="jk-empty-body">No Jenkins host resolved from '
            '  vault path <code>' + html.escape(JENKINS_VAULT_PATH) + '</code> '
            '  (expected keys: <code>host</code>, <code>username</code>, '
            '  <code>api_token</code>; optional <code>public_name</code>). '
            '  Falls back to <code>JENKINS_HOSTNAME</code> / '
            '  <code>JENKINS_USER</code> / <code>JENKINS_TOKEN</code> env '
            '  vars when vault isn\'t reachable. Until configured the panel '
            '  costs nothing.</div>'
            + _err_block +
            '</div>',
            unsafe_allow_html=True,
        )
        return

    if not st.session_state.get(_JK_LOAD_FLAG):
        # Idle state — purely decorative until the user opts in.
        _g1, _g2, _g3 = st.columns([1, 2, 1])
        with _g2:
            st.markdown(
                '<div class="jk-gate">'
                '  <div class="jk-gate-glyph">⏵</div>'
                '  <div class="jk-gate-title">Jenkins panel paused</div>'
                '  <div class="jk-gate-body">'
                '    Click below to fetch the live status of the build, '
                '    deploy-request, and release-request pipelines. '
                '    While paused the panel makes zero API calls.'
                '  </div>'
                '</div>',
                unsafe_allow_html=True,
            )
            if st.button("▶  Load Jenkins panel",
                         key="_jk_load_btn",
                         use_container_width=True,
                         type="primary"):
                st.session_state[_JK_LOAD_FLAG] = True
                st.rerun()
        return

    _render_jenkins_panel_active()


@st.fragment
def _render_event_log() -> None:
    """Inline event log — role-scoped, fragmented for internal-widget perf.

    `@st.fragment` (without ``run_every``) means:
      • The event log's OWN widgets (env, time window, type pills,
        per-project toggle, pager buttons) only re-run THIS fragment —
        the inventory tab and stat tiles are not redrawn for those
        interactions.
      • A parent rerun (Filter Console change, search edit, anything
        outside the fragment) still re-executes the fragment as part of
        the normal top-down script run, so filter-driven changes still
        propagate correctly.

    The previous implementation was a plain function; we removed the
    decorator earlier because `@st.fragment(run_every="60s")` set up an
    independent refresh schedule that decoupled from parent reruns.
    Without ``run_every`` the decoupling concern goes away — only the
    interaction-isolation benefit remains.
    """
    # Role-allowed environments for the Env selector — UNION across every
    # detected role (Developer + Operations user → ["dev", "uat", "prd"]).
    _allowed_envs = list(_user_envs)
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
        # Event log lives inside a @st.fragment — paginate without
        # re-rendering the inventory tab.
        rerun_scope="fragment",
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
    # Parallelise the four (app, version) lookups — same pattern as the
    # inventory render. ES round-trips are I/O-bound and @st.cache_data
    # is thread-safe, so collapsing to a single wave shaves several
    # hundred ms off cold loads of the event log.
    if _prisma_keys:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="el-scans") as _ex:
            _f_pri = _ex.submit(_fetch_prismacloud,  _prisma_keys_t)
            _f_inv = _ex.submit(_fetch_invicti,      _prisma_keys_t)
            _f_zap = _ex.submit(_fetch_zap,          _prisma_keys_t)
            # Per-version build/release provenance for the event-log version popovers.
            _f_vmd = _ex.submit(_fetch_version_meta, _prisma_keys_t)
            _prisma_map   = _f_pri.result()
            _invicti_map  = _f_inv.result()
            _zap_map      = _f_zap.result()
            _ver_meta_map = _f_vmd.result()
    else:
        _prisma_map = _invicti_map = _zap_map = _ver_meta_map = {}

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
# INVENTORY GIT LOADER — primary source; ES is the fallback
# =============================================================================
# The dashboard prefers the on-disk git checkout because:
#   1. it's the authoritative source operators edit (ES is a projection),
#   2. local FS reads are an order of magnitude faster than ES round-trips,
#   3. it lets us write variables back through `git commit + push` later.
# We still call into ES on any failure path — vault unreachable, missing
# ADO entry, clone error, or a missing dependency all degrade to the
# previous behaviour rather than blanking the page.
# -----------------------------------------------------------------------------

def _git_creds() -> dict:
    """Resolve the ADO credentials from vault.

    Mirrors the platform's GitHandler convention exactly: a nested
    ``ado`` dict under the vault path holds ``hostname`` / ``username``
    / ``password``. All three may be empty when vault is unreachable —
    callers gate on the hostname before doing any git work.

    Returns ``{"hostname": str, "username": str, "password": str}``.
    """
    cfg = _vault_secrets(GIT_VAULT_PATH) or {}
    ado = (cfg.get("ado") or {}) if isinstance(cfg.get("ado"), dict) else {}
    return {
        "hostname": (ado.get("hostname") or "").strip(),
        "username": (ado.get("username") or "").strip(),
        "password": (ado.get("password") or "").strip(),
    }


def _inv_repo_url() -> str:
    """Build the clean clone URL — no credentials embedded.

    The URL stays plaintext. Authentication is provided via the
    ``store --file ~/.git-credentials`` credential helper that
    :func:`_configure_git_credentials` installs globally. That mirrors
    the platform's existing ``GitHandler.configure_credentials()``
    pattern — git auto-resolves auth via the helper at request time
    using the proper challenge / response handshake the ADO Server
    expects, instead of relying on URL embedding or preemptive
    ``Authorization`` headers (both of which fail in this environment)."""
    creds = _git_creds()
    host = creds["hostname"]
    if not host:
        return ""
    return INVENTORY_REPO_URL_TEMPLATE.format(host=host)


_GIT_CREDENTIALS_PATH = os.path.expanduser("~/.git-credentials")


def _configure_git_credentials() -> tuple[bool, str]:
    """Provision git so any HTTP basic-auth request to the ADO host uses
    the vault-resolved credentials.

    Mirrors the platform's working ``GitHandler.configure_credentials()``
    pattern exactly:

      1. Write ``~/.git-credentials`` with a ``http://user:pw@host`` line
         (all three components URL-encoded so special characters survive).
      2. Globally point git at that file via
         ``credential.helper "store --file <path>"``.
      3. Set ``user.name`` / ``user.email`` from ``st.session_state.username``
         / ``st.session_state.email`` (the current dashboard operator) so
         future write-back commits are authored as them.
      4. Add the defensive ``url.https://.insteadof git://`` rewrite and
         disable ``http.sslVerify`` (the ADO Server cert chain isn't always
         in the container trust store).

    Idempotent — every call overwrites the credentials file and re-applies
    the global config. Returns ``(ok, error_msg)``. Streamed through
    ``_run_git(..., inject_auth=False)`` so the config writes never carry
    a (no-longer-emitted) ``-c http.extraHeader``."""
    creds = _git_creds()
    host = creds.get("hostname", "")
    user = creds.get("username", "")
    pw = creds.get("password", "")
    if not host or not user or not pw:
        missing = [
            n for n, v in (("hostname", host), ("username", user), ("password", pw))
            if not v
        ]
        return False, (
            f"incomplete git config: missing {', '.join(missing)} "
            f"(vault path {GIT_VAULT_PATH!r}, sub-key `ado`)"
        )
    try:
        safe_host = urllib.parse.quote(host, safe="")
        safe_user = urllib.parse.quote(user, safe="")
        safe_pw = urllib.parse.quote(pw, safe="")
        # Write the credentials file atomically (truncate-then-write).
        with open(_GIT_CREDENTIALS_PATH, "w") as f:
            f.write(f"http://{safe_user}:{safe_pw}@{safe_host}\n")
        # Tighten perms so a co-tenant can't read the secret.
        try:
            os.chmod(_GIT_CREDENTIALS_PATH, 0o600)
        except Exception:
            pass

        # The helper string is passed as a SINGLE argument to credential.helper.
        # Git invokes `sh -c "<value> <action>"`, so quoting / spaces inside
        # the value are handled by the helper-spec parser.
        helper_value = f"store --file {_GIT_CREDENTIALS_PATH}"
        _commands = (
            ("config", "--global", "credential.helper", helper_value),
            ("config", "--global", "url.https://.insteadof", "git://"),
            ("config", "--global", "http.sslVerify", "false"),
        )
        for _args in _commands:
            r = _run_git(*_args, inject_auth=False)
            if r.returncode != 0:
                return False, f"git {_args[0]} {_args[2]} failed: {r.stderr.strip()}"

        # Author identity from session state — best-effort, optional.
        sess_user = (st.session_state.get("username") or "").strip()
        sess_email = (st.session_state.get("email") or "").strip()
        if sess_user:
            _run_git("config", "--global", "user.name", sess_user,
                     inject_auth=False)
        if sess_email:
            _run_git("config", "--global", "user.email", sess_email,
                     inject_auth=False)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _git_diag_redact(s: str) -> str:
    """Scrub every form of the git password out of a string.

    The standard ``_run_git`` already redacts stdout/stderr, but the
    diagnostic path also captures the raw command-line + the resolved
    URL, neither of which goes through that sanitisation. Apply this
    helper anywhere admin-visible debug data is rendered.

    Redacts:
      • the raw password
      • the URL-encoded password
      • the base64-encoded ``user:pw`` form carried by ``http.extraHeader``
    """
    if not s:
        return s
    creds = _git_creds()
    pw = creds.get("password") or ""
    user = creds.get("username") or ""
    if not pw:
        return s
    out = s
    out = out.replace(pw, "***")
    pw_enc = urllib.parse.quote(pw, safe="")
    if pw_enc and pw_enc != pw:
        out = out.replace(pw_enc, "***")
    try:
        b64 = base64.b64encode(f"{user}:{pw}".encode("utf-8")).decode("ascii")
        if b64 and b64 != pw and b64 not in ("***",):
            out = out.replace(b64, "***")
    except Exception:
        pass
    return out


def _git_diag_capture_step(
    trace: list, step: str, cmd: list[str],
    proc: subprocess.CompletedProcess, started_at: float,
) -> None:
    """Append a single git invocation's redacted summary to *trace*.

    Streams are truncated at 2 KB so a chatty git error doesn't blow up
    session_state size; the diagnostic UI shows the most-recent slice,
    which is where every real error lives. The command list itself is
    redacted in case the URL (and its embedded password) was passed as
    a positional argument."""
    import time
    _max = 2048
    _stdout = (proc.stdout or "")
    _stderr = (proc.stderr or "")
    if len(_stdout) > _max:
        _stdout = _stdout[-_max:]
    if len(_stderr) > _max:
        _stderr = _stderr[-_max:]
    trace.append({
        "step":         step,
        "cmd_redacted": [_git_diag_redact(c) for c in cmd],
        "returncode":   int(proc.returncode if proc.returncode is not None else -1),
        "stdout_short": _git_diag_redact(_stdout).rstrip(),
        "stderr_short": _git_diag_redact(_stderr).rstrip(),
        "duration_ms":  int(max(0.0, (time.monotonic() - started_at) * 1000)),
    })


def _run_git(
    *args: str,
    cwd: str | None = None,
    trace_into: list | None = None,
    inject_auth: bool = True,  # kept for API stability; no-op under helper auth
) -> subprocess.CompletedProcess:
    """Run a git subcommand quietly and capture both streams.

    Authentication is sourced from the credential helper installed by
    :func:`_configure_git_credentials` — git auto-resolves auth via the
    helper at request time using the challenge/response handshake the
    ADO Server expects. No ``-c`` args are injected here (an earlier
    attempt used ``http.extraHeader``, which Azure DevOps Server
    rejected; the helper-based form mirrors the platform's known-working
    pattern).

    The ``inject_auth`` keyword is preserved for API stability but is
    currently a no-op — every git command gets its auth from the
    installed credential helper regardless. Local-only callers can still
    pass ``inject_auth=False`` to document intent.

    Captured stdout/stderr are scrubbed of the password (raw + URL-encoded
    form) before returning so admin-visible error banners never leak it.
    When *trace_into* is non-None, a redacted command + streams + duration
    record is appended for the diagnostic panel."""
    import time

    cmd = ["git", *args]
    _started = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )
    # Scrub the password from anything git might surface — typically not
    # present in stderr (the helper handles auth opaquely) but a defensive
    # pass keeps the diagnostic panel safe.
    creds = _git_creds()
    pw = creds.get("password") or ""
    if pw:
        pw_enc = urllib.parse.quote(pw, safe="")
        for stream_attr in ("stdout", "stderr"):
            s = getattr(proc, stream_attr, None) or ""
            if pw in s:
                s = s.replace(pw, "***")
            if pw_enc and pw_enc != pw and pw_enc in s:
                s = s.replace(pw_enc, "***")
            setattr(proc, stream_attr, s)
    if trace_into is not None:
        _git_diag_capture_step(
            trace_into, step=args[0] if args else "git",
            cmd=cmd, proc=proc, started_at=_started,
        )
    return proc


def _git_set_author(repo_path: str, username: str, email: str) -> None:
    """Apply the current dashboard operator's identity to the local repo's
    git config so future write-back commits are authored as them, not the
    service-account credentials used for transport. Silent no-op when
    username/email are empty or the repo isn't checked out yet. Cheap (two
    local config writes), safe to call on every render."""
    if not repo_path or not os.path.isdir(os.path.join(repo_path, ".git")):
        return
    if username:
        _run_git("config", "user.name", username, cwd=repo_path,
                 inject_auth=False)
    if email:
        _run_git("config", "user.email", email, cwd=repo_path,
                 inject_auth=False)


@st.cache_resource(ttl=INVENTORY_SYNC_TTL, show_spinner=False)
def _ensure_inventory_repo(host_marker: str, trace_into: list | None = None) -> tuple[bool, str, str]:
    """Idempotent clone-or-pull of the inventories repo onto INVENTORY_BRANCH.

    Returns ``(ok, head_sha, status_msg)``. ``host_marker`` is the
    vault-resolved hostname; it's included in the cache key so a hostname
    change (e.g. vault rotation pointing to a new ADO server) invalidates
    the cached clone state and triggers a fresh sync.

    Cached as a *resource* (not data) because it has filesystem side-effects
    and we want exactly one sync per TTL window per Streamlit process.

    *trace_into* — optional list that ``_run_git`` will append a redacted
    per-step record to. Used by the admin diagnostic panel to render a
    step-by-step trace. Streamlit's ``cache_resource`` keys ONLY on the
    positional ``host_marker`` arg (mutable default-less kwargs are not
    part of the key), so a diagnostic call that supplies ``trace_into=[]``
    still benefits from / pollutes the same cache entry; callers must
    invoke ``.clear()`` first when they want a fresh execution.
    """
    if not host_marker:
        return False, "", "Git host not resolved (vault unreachable?)"
    # Provision the credential helper before any auth-requiring git
    # operation. Idempotent — every call rewrites the credentials file +
    # re-applies global config, so a vault rotation is picked up on the
    # next ``Force re-sync`` without restart.
    cred_ok, cred_err = _configure_git_credentials()
    if not cred_ok:
        return False, "", f"git auth setup failed: {cred_err}"
    url = _inv_repo_url()
    if not url:
        return False, "", "Git URL could not be built (host missing)"
    repo_path = INVENTORY_REPO_PATH
    git_dir = os.path.join(repo_path, ".git")
    try:
        if os.path.isdir(git_dir):
            # Existing checkout — fetch + hard-reset to the remote head so a
            # local edit (e.g. a future write-back retry that aborted halfway)
            # never wedges the dashboard on a stale tip.
            r = _run_git("remote", "set-url", "origin", url, cwd=repo_path,
                         trace_into=trace_into)
            if r.returncode != 0:
                return False, "", f"git remote set-url failed: {r.stderr.strip()}"
            r = _run_git("fetch", "--depth", "1", "origin", INVENTORY_BRANCH,
                         cwd=repo_path, trace_into=trace_into)
            if r.returncode != 0:
                return False, "", f"git fetch failed: {r.stderr.strip()}"
            r = _run_git("checkout", INVENTORY_BRANCH, cwd=repo_path,
                         trace_into=trace_into)
            if r.returncode != 0:
                # Branch may not exist locally yet on a freshly-shallow-cloned
                # repo; create it from FETCH_HEAD.
                r = _run_git("checkout", "-B", INVENTORY_BRANCH, "FETCH_HEAD",
                             cwd=repo_path, trace_into=trace_into)
                if r.returncode != 0:
                    return False, "", f"git checkout failed: {r.stderr.strip()}"
            r = _run_git("reset", "--hard", f"origin/{INVENTORY_BRANCH}",
                         cwd=repo_path, trace_into=trace_into)
            if r.returncode != 0:
                return False, "", f"git reset failed: {r.stderr.strip()}"
        else:
            # Fresh clone. Wipe any partial directory so we never inherit
            # half-applied state from a previous failed clone.
            if os.path.exists(repo_path):
                shutil.rmtree(repo_path, ignore_errors=True)
            os.makedirs(os.path.dirname(repo_path) or "/", exist_ok=True)
            r = _run_git(
                "clone", "--depth", "1", "--branch", INVENTORY_BRANCH,
                url, repo_path,
                trace_into=trace_into,
            )
            if r.returncode != 0:
                return False, "", f"git clone failed: {r.stderr.strip()}"
        # Resolve HEAD for cache-busting downstream.
        r = _run_git("rev-parse", "HEAD", cwd=repo_path,
                     trace_into=trace_into, inject_auth=False)
        if r.returncode != 0:
            return False, "", f"git rev-parse failed: {r.stderr.strip()}"
        head = (r.stdout or "").strip()
        return True, head, f"OK · {head[:8]}"
    except subprocess.TimeoutExpired:
        return False, "", "git operation timed out (120s)"
    except FileNotFoundError:
        return False, "", "git executable not found on PATH"
    except Exception as e:  # pragma: no cover — last-resort safety net
        return False, "", f"unexpected: {type(e).__name__}: {e}"


# =============================================================================
# GIT DIAGNOSTIC PROBE — admin-only on-demand
# =============================================================================
# Distinguishes "vault unreachable" from "credentials malformed" from "host
# unreachable" from "auth failed" from "stale checkout" without the admin
# having to read tea leaves out of a single short status phrase. Never
# auto-fires — the result is only populated when the admin clicks one of
# the diagnostic buttons inside the source-failure alarm banner.

def _git_diag_probe() -> dict:
    """Run every cheap probe relevant to diagnosing a git-source failure.

    Returns a structured dict; see ``_render_git_diag_panel`` for the
    consumed shape. Safe to call even when vault is unreachable / git is
    not installed — every probe is wrapped in its own try block so a
    single failure surfaces as a populated error field rather than
    aborting the whole report. The password is NEVER captured."""

    out: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_version": {"available": False, "version": "", "error": ""},
        "vault": {
            "ok": False, "keys_top": [], "has_ado": False,
            "ado_keys": [], "host_present": False,
            "user_present": False, "pw_present": False, "pw_len": 0,
            "last_error": "",
        },
        "resolved_url_redacted": "",
        "fs": {
            "repo_path": INVENTORY_REPO_PATH,
            "exists": False, "git_dir_exists": False,
            "head_sha": "",
        },
        "trace": [],
        "final": {"ok": False, "head": "", "status_msg": ""},
    }

    # 1. git --version — runs without cwd, captures stdout directly.
    try:
        gv = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, timeout=5,
        )
        out["git_version"]["available"] = (gv.returncode == 0)
        out["git_version"]["version"] = (gv.stdout or gv.stderr or "").strip()
    except FileNotFoundError:
        out["git_version"]["error"] = "git binary not on PATH"
    except subprocess.TimeoutExpired:
        out["git_version"]["error"] = "git --version timed out"
    except Exception as e:
        out["git_version"]["error"] = f"{type(e).__name__}: {e}"

    # 2. Vault read shape — never includes the password value.
    try:
        cfg = _vault_secrets(GIT_VAULT_PATH) or {}
        out["vault"]["ok"] = bool(cfg)
        out["vault"]["keys_top"] = sorted(cfg.keys())
        ado = cfg.get("ado") if isinstance(cfg.get("ado"), dict) else {}
        out["vault"]["has_ado"] = bool(ado)
        out["vault"]["ado_keys"] = sorted(ado.keys()) if ado else []
        out["vault"]["host_present"] = bool((ado.get("hostname") or "").strip())
        out["vault"]["user_present"] = bool((ado.get("username") or "").strip())
        _pw = (ado.get("password") or "")
        out["vault"]["pw_present"] = bool(_pw.strip())
        out["vault"]["pw_len"] = len(_pw)
    except Exception as e:
        out["vault"]["last_error"] = f"{type(e).__name__}: {e}"
    # Surface any stashed vault error for this path even if THIS call worked
    # (stale state from a prior render is still useful for diagnosis).
    out["vault"]["last_error"] = (
        out["vault"]["last_error"]
        or _vault_last_error(GIT_VAULT_PATH)
        or ""
    )

    # 3. Resolved URL — redacted.
    try:
        out["resolved_url_redacted"] = _git_diag_redact(_inv_repo_url())
    except Exception as e:
        out["resolved_url_redacted"] = f"<URL build failed: {type(e).__name__}: {e}>"

    # 4. Filesystem snapshot.
    try:
        rp = INVENTORY_REPO_PATH
        out["fs"]["exists"] = os.path.isdir(rp)
        out["fs"]["git_dir_exists"] = os.path.isdir(os.path.join(rp, ".git"))
        if out["fs"]["git_dir_exists"]:
            try:
                r = _run_git("rev-parse", "HEAD", cwd=rp, inject_auth=False)
                if r.returncode == 0:
                    out["fs"]["head_sha"] = (r.stdout or "").strip()
            except Exception:
                pass
    except Exception as e:
        out["fs"]["error"] = f"{type(e).__name__}: {e}"

    # 5. Step trace — clear the cached resource so we get a FRESH execution,
    # then call _ensure_inventory_repo with trace_into to capture every step.
    trace: list[dict] = []
    try:
        _ensure_inventory_repo.clear()
    except Exception:
        pass
    try:
        host = _git_creds().get("hostname", "")
        ok, head, status_msg = _ensure_inventory_repo(host, trace_into=trace)
        out["final"] = {"ok": ok, "head": head, "status_msg": status_msg}
    except Exception as e:
        out["final"]["status_msg"] = f"{type(e).__name__}: {e}"
    out["trace"] = trace

    return out


def _render_git_diag_panel(result: dict) -> None:
    """Render the diagnostic dict as an admin-readable monospace block.

    The result is stashed in ``st.session_state["_git_diag_result_v1"]`` —
    callers populate it via ``_git_diag_probe()`` and clear it via the
    "✕ Clear" button inside this panel."""
    if not result:
        return

    # ── Step trace
    trace = result.get("trace") or []
    if trace:
        step_rows: list[str] = []
        for s in trace:
            rc = s.get("returncode", -1)
            cls = "gd-step ok" if rc == 0 else "gd-step bad"
            glyph = "✓" if rc == 0 else "✗"
            _cmd = " ".join(s.get("cmd_redacted") or [])
            _stderr = s.get("stderr_short") or ""
            _stdout = s.get("stdout_short") or ""
            _streams = ""
            if _stderr:
                _streams += (
                    f'<pre class="gd-stderr">{html.escape(_stderr)}</pre>'
                )
            if _stdout and rc == 0:
                _streams += (
                    f'<pre class="gd-stdout">{html.escape(_stdout)}</pre>'
                )
            step_rows.append(
                f'<div class="{cls}">'
                f'  <span class="gd-step-glyph">{glyph}</span>'
                f'  <span class="gd-step-name">{html.escape(s.get("step", "?"))}</span>'
                f'  <span class="gd-step-rc">rc={rc}</span>'
                f'  <span class="gd-step-dur">{s.get("duration_ms", 0)}ms</span>'
                f'  <div class="gd-step-cmd">{html.escape(_cmd)}</div>'
                f'  {_streams}'
                f'</div>'
            )
        trace_html = "".join(step_rows)
    else:
        trace_html = (
            '<div class="gd-step bad">'
            '  <span class="gd-step-glyph">✗</span>'
            '  <span class="gd-step-name">'
            '    no git commands ran — failure was earlier in the pipeline'
            '  </span>'
            '</div>'
        )

    # ── Vault summary
    v = result.get("vault") or {}
    _vault_state_cls = (
        "ok" if v.get("ok") and v.get("has_ado") and v.get("host_present")
        else ("bad" if v.get("last_error") or not v.get("ok") else "warn")
    )
    _keys_top = ", ".join(v.get("keys_top") or []) or "—"
    _ado_keys = ", ".join(v.get("ado_keys") or []) or "—"
    _vault_err = v.get("last_error") or ""
    _vault_kv = [
        ("path", html.escape(GIT_VAULT_PATH)),
        ("top-level keys", html.escape(_keys_top)),
        ("has `ado` sub-dict", "yes" if v.get("has_ado") else "no"),
        ("`ado` keys", html.escape(_ado_keys)),
        ("hostname present", "yes" if v.get("host_present") else "no"),
        ("username present", "yes" if v.get("user_present") else "no"),
        ("password present",
         f"yes (len={v.get('pw_len',0)})" if v.get("pw_present") else "no"),
    ]
    if _vault_err:
        _vault_kv.append(("last error", html.escape(_vault_err)))
    _vault_kv_html = "".join(
        f'<div class="gd-kv-row">'
        f'  <span class="gd-kv-k">{k}</span>'
        f'  <span class="gd-kv-v">{val}</span>'
        f'</div>'
        for k, val in _vault_kv
    )

    # ── Filesystem snapshot
    fs = result.get("fs") or {}
    _fs_kv = [
        ("repo path", html.escape(fs.get("repo_path") or "")),
        ("dir exists", "yes" if fs.get("exists") else "no"),
        ("`.git/` present", "yes" if fs.get("git_dir_exists") else "no"),
        ("HEAD sha",
         html.escape(fs.get("head_sha") or "—")[:40]),
    ]
    _fs_kv_html = "".join(
        f'<div class="gd-kv-row">'
        f'  <span class="gd-kv-k">{k}</span>'
        f'  <span class="gd-kv-v">{val}</span>'
        f'</div>'
        for k, val in _fs_kv
    )

    # ── Git binary
    gv = result.get("git_version") or {}
    _gv_state = (
        "ok" if gv.get("available") else "bad"
    )
    _gv_line = html.escape(
        gv.get("version") or gv.get("error") or "no information"
    )

    # ── URL
    _url = result.get("resolved_url_redacted") or "(not resolved)"

    # ── Final outcome
    final = result.get("final") or {}
    _final_cls = "ok" if final.get("ok") else "bad"
    _final_msg = html.escape(final.get("status_msg") or "—")
    _final_head = html.escape(final.get("head") or "")
    # f-strings can't embed backslash escapes inside expressions — build the
    # optional head-sha span out-of-band.
    _final_head_html = (
        f'<span class="gd-step-head">{_final_head[:8]}</span>'
        if _final_head else ""
    )
    _final_glyph = "✓" if final.get("ok") else "✗"
    _gv_glyph = "✓" if gv.get("available") else "✗"

    _ts = (result.get("timestamp") or "").replace("T", " ")[:19]

    st.markdown(
        f'<div class="gd-panel">'
        f'  <div class="gd-panel-head">'
        f'    <span class="gd-panel-glyph">🔍</span>'
        f'    <span class="gd-panel-title">Git diagnostic</span>'
        f'    <span class="gd-panel-ts">{_ts} UTC</span>'
        f'  </div>'
        f'  <div class="gd-section">'
        f'    <div class="gd-section-title">git binary</div>'
        f'    <div class="gd-step {_gv_state}">'
        f'      <span class="gd-step-glyph">{_gv_glyph}</span>'
        f'      <span class="gd-step-name">{_gv_line}</span>'
        f'    </div>'
        f'  </div>'
        f'  <div class="gd-section">'
        f'    <div class="gd-section-title">vault read · '
        f'<code>{html.escape(GIT_VAULT_PATH)}</code></div>'
        f'    <div class="gd-kv gd-kv--{_vault_state_cls}">{_vault_kv_html}</div>'
        f'  </div>'
        f'  <div class="gd-section">'
        f'    <div class="gd-section-title">resolved URL (redacted)</div>'
        f'    <pre class="gd-url">{html.escape(_url)}</pre>'
        f'    <div class="gd-auth-note">'
        f'      auth resolved at request-time via git\'s <code>store --file</code> '
        f'credential helper · file at <code>{html.escape(_GIT_CREDENTIALS_PATH)}</code> '
        f'(exists: {("yes" if os.path.exists(_GIT_CREDENTIALS_PATH) else "no")}) '
        f'· file contains a single line <code>http://user:***@host</code> '
        f'for the configured ADO hostname · SSL verification disabled '
        f'(internal CA path) · matches the platform GitHandler pattern'
        f'    </div>'
        f'  </div>'
        f'  <div class="gd-section">'
        f'    <div class="gd-section-title">filesystem · '
        f'<code>{html.escape(INVENTORY_REPO_PATH)}</code></div>'
        f'    <div class="gd-kv">{_fs_kv_html}</div>'
        f'  </div>'
        f'  <div class="gd-section">'
        f'    <div class="gd-section-title">git steps · last attempt</div>'
        f'    <div class="gd-trace">{trace_html}</div>'
        f'  </div>'
        f'  <div class="gd-section">'
        f'    <div class="gd-section-title">final outcome</div>'
        f'    <div class="gd-step {_final_cls}">'
        f'      <span class="gd-step-glyph">{_final_glyph}</span>'
        f'      <span class="gd-step-name">{_final_msg}</span>'
        f'      {_final_head_html}'
        f'    </div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )


_GIT_DIAG_RESULT_KEY = "_git_diag_result_v1"


def _render_git_diag_controls() -> None:
    """Render the diagnostic button row + (when populated) the result panel.

    Sits directly under the source alarm banner in the inventory tab.
    Three buttons:
      • 🔍 Diagnose git — runs ``_git_diag_probe()`` and stashes the result.
      • ↻ Force re-sync — clears the cached ``_ensure_inventory_repo`` and
        re-renders so :func:`_inventory_load` runs fresh on the next call.
      • ✕ Clear         — drops the stashed diagnostic result.

    The expensive probe runs only on the button click — the panel itself
    just renders the cached dict on subsequent reruns."""
    _b1, _b2, _b3, _spacer = st.columns([1, 1, 1, 4])
    with _b1:
        if st.button("🔍 Diagnose git",
                     key="cc_git_diag_btn",
                     use_container_width=True,
                     help=(
                         "Runs every diagnostic probe (git binary, vault "
                         "shape, resolved URL, filesystem state, per-step "
                         "git trace). Password is never captured. Safe to "
                         "click repeatedly."
                     )):
            with st.spinner("Running git diagnostic probe..."):
                st.session_state[_GIT_DIAG_RESULT_KEY] = _git_diag_probe()
            st.rerun()
    with _b2:
        if st.button("↻ Force re-sync",
                     key="cc_git_diag_resync_btn",
                     use_container_width=True,
                     help=(
                         "Drops the 60-second cached repo state and "
                         "immediately re-runs the inventory load with the "
                         "active source preference. Use after fixing vault "
                         "config so you don't have to wait for the TTL."
                     )):
            try:
                _ensure_inventory_repo.clear()
            except Exception:
                pass
            st.rerun()
    with _b3:
        if st.session_state.get(_GIT_DIAG_RESULT_KEY):
            if st.button("✕ Clear",
                         key="cc_git_diag_clear_btn",
                         use_container_width=True,
                         help="Drops the displayed diagnostic result."):
                st.session_state.pop(_GIT_DIAG_RESULT_KEY, None)
                st.rerun()
    result = st.session_state.get(_GIT_DIAG_RESULT_KEY)
    if result:
        _render_git_diag_panel(result)


def _decrypt_vault(blob: bytes) -> bytes:
    """Decrypt an Ansible-vault-encrypted file. Returns the plaintext bytes,
    or raises if the dependency / password is missing or the decrypt fails.

    The dashboard catches the exception at the YAML-read layer and surfaces
    the failing path in the admin banner — we deliberately don't blanket-
    suppress so a wrong vault password is visible rather than silent."""
    if not _ANSIBLE_VAULT_AVAILABLE:
        raise RuntimeError("ansible.parsing.vault not installed")
    if not ANSIBLE_VAULT_PASSWORD:
        raise RuntimeError("ANSIBLE_VAULT_PASSWORD not set")
    vault = _VaultLib([("default", _VaultSecret(ANSIBLE_VAULT_PASSWORD.encode()))])
    return vault.decrypt(blob)


def _read_yaml_file(path: pathlib.Path,
                    warnings: list[str]) -> dict:
    """Load a YAML file, transparently decrypting Ansible vault payloads.

    Returns ``{}`` on any error; appends a one-line diagnostic to *warnings*
    so the admin banner can surface the offending paths."""
    if not _YAML_AVAILABLE:
        warnings.append("PyYAML not installed — git inventory disabled")
        return {}
    try:
        raw = path.read_bytes()
    except Exception as e:
        warnings.append(f"read {path}: {type(e).__name__}")
        return {}
    # Vault detection — official header is `$ANSIBLE_VAULT;<version>;<cipher>`
    if raw.lstrip().startswith(b"$ANSIBLE_VAULT;"):
        try:
            raw = _decrypt_vault(raw)
        except Exception as e:
            warnings.append(f"vault {path.name}: {type(e).__name__}: {e}")
            return {}
    try:
        loaded = _yaml.safe_load(raw)
    except Exception as e:
        warnings.append(f"yaml {path}: {type(e).__name__}")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_yaml_dir(directory: pathlib.Path,
                   warnings: list[str]) -> dict:
    """Merge every ``*.yml`` / ``*.yaml`` file in *directory* into one dict.
    Later files overwrite earlier ones (alphabetical order — matches Ansible
    precedence within a single group_vars subdirectory)."""
    if not directory.is_dir():
        return {}
    merged: dict = {}
    for f in sorted(directory.iterdir()):
        if f.is_file() and f.suffix.lower() in (".yml", ".yaml"):
            data = _read_yaml_file(f, warnings)
            if isinstance(data, dict):
                merged.update(data)
    return merged


def _resolve_field(merged: dict, field: str) -> str:
    """Given the canonical row field name, scan the alias list and return the
    first non-empty string value found. Tolerates dotted aliases by walking
    nested dicts (so ``build_image.name`` reaches ``{'build_image': {'name': X}}``)."""
    aliases = _INV_FIELD_ALIASES.get(field, (field,))
    for alias in aliases:
        if "." in alias:
            cur: Any = merged
            for part in alias.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                else:
                    cur = None
                    break
            if cur not in (None, "", []):
                return str(cur)
        else:
            v = merged.get(alias)
            if v not in (None, "", []):
                return str(v)
    return ""


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def _load_inventory_from_git(head_sha: str) -> tuple[list[dict], list[str]]:
    """Walk the cloned inventories repo and produce inventory rows in the
    same shape as :func:`_fetch_full_inventory`.

    Cached on the git HEAD SHA — the cache invalidates only when the repo
    actually moves, so consecutive Streamlit reruns within the same git
    revision pay zero parse cost.

    Returns ``(rows, warnings)``. Warnings surface missing dependencies,
    decrypt failures, and parse errors for the admin banner.
    """
    warnings: list[str] = []
    rows: list[dict] = []
    base = pathlib.Path(INVENTORY_REPO_PATH)
    if not base.is_dir() or not head_sha:
        return rows, warnings
    if not _YAML_AVAILABLE:
        warnings.append("PyYAML missing — `pip install pyyaml`")
        return rows, warnings

    # group_vars/all is shared baseline across every app/project.
    group_vars_root = base / "group_vars"
    all_vars = _load_yaml_dir(group_vars_root / "all", warnings)

    # Iterate projects (top-level directories that are not group_vars / host_vars / .git)
    skip_dirs = {"group_vars", "host_vars", ".git", ".github", ".gitlab"}
    for project_dir in sorted(p for p in base.iterdir()
                              if p.is_dir() and p.name not in skip_dirs and not p.name.startswith(".")):
        project = project_dir.name
        # Each {application}.yml at the project root defines an app's inventory.
        for inv_file in sorted(project_dir.glob("*.yml")):
            app = inv_file.stem
            # Per-app group_vars live at the REPO root, not under the project
            # directory (Ansible convention). Same for env-scoped vars.
            app_vars = _load_yaml_dir(group_vars_root / app, warnings)
            env_vars: dict[str, dict] = {}
            for env in _INV_ENVIRONMENTS:
                env_vars[env] = _load_yaml_dir(group_vars_root / f"{env}_{app}", warnings)

            # Merge precedence: all → app → env-specific (env-specific wins).
            # We expose the app-level merge for the row's primary fields and
            # the env-specific merges for image/team fields where it matters.
            app_merged: dict = {**all_vars, **app_vars}

            # Teams: collect *_team keys from every level. Env-specific values
            # override app-level for that env's team key, matching how
            # ef-devops-inventory currently surfaces them.
            teams: dict[str, list[str]] = {}
            for src in (all_vars, app_vars):
                for k, v in (src or {}).items():
                    if k.endswith("_team") and v not in (None, "", []):
                        teams[k] = sorted([str(x) for x in v]) if isinstance(v, list) else [str(v)]
            for env, ev in env_vars.items():
                for k, v in (ev or {}).items():
                    if k.endswith("_team") and v not in (None, "", []):
                        teams[k] = sorted([str(x) for x in v]) if isinstance(v, list) else [str(v)]
                # Also accept a bare `team` key under env_app and map it onto {env}_team.
                _bare = (ev or {}).get("team")
                if _bare and not teams.get(f"{env}_team"):
                    teams[f"{env}_team"] = [str(_bare)] if not isinstance(_bare, list) else sorted(str(x) for x in _bare)

            # Image fields can be env-specific. Pick the best signal: prd
            # first (production is the canonical version), then dev → qc →
            # uat. Falls back to the app-level value if no env carries it.
            def _pick_env_field(field: str) -> str:
                for env in ("prd", "uat", "qc", "dev"):
                    v = _resolve_field(env_vars.get(env, {}) or {}, field)
                    if v:
                        return v
                return _resolve_field(app_merged, field)

            row = {
                "application":       app,
                "project":           project,
                "company":           _resolve_field(app_merged, "company"),
                "app_type":          _resolve_field(app_merged, "app_type").strip(),
                "build_technology":  _resolve_field(app_merged, "build_technology"),
                "deploy_technology": _resolve_field(app_merged, "deploy_technology"),
                "deploy_platform":   _resolve_field(app_merged, "deploy_platform"),
                "build_image_name":  _pick_env_field("build_image_name"),
                "build_image_tag":   _pick_env_field("build_image_tag"),
                "deploy_image_name": _pick_env_field("deploy_image_name"),
                "deploy_image_tag":  _pick_env_field("deploy_image_tag"),
                "teams":             teams,
            }
            rows.append(row)

    rows.sort(key=lambda r: (r["project"].lower(), r["application"].lower()))
    return rows, warnings


def _row_matches_es_filters(row: dict, filters: list) -> bool:
    """Apply the small subset of ES filter clauses produced by the dashboard's
    own ``scope_filters_inv`` (term/terms on company/project/application,
    must_not on excluded projects, the match-none sentinel) to a single row.

    Anything outside that vocabulary is ignored — falling open is safer than
    silently dropping data when the filter shape evolves; the ES path retains
    full fidelity as the fallback."""

    def _accept(clause: dict) -> bool:
        if not isinstance(clause, dict):
            return True
        # Match-none sentinel
        if (
            "bool" in clause
            and clause["bool"].get("must_not") == [{"match_all": {}}]
            and not clause["bool"].get("filter")
            and not clause["bool"].get("should")
        ):
            return False
        if "term" in clause:
            for k, v in clause["term"].items():
                if not _row_field_match(row, k, [v]):
                    return False
            return True
        if "terms" in clause:
            for k, vs in clause["terms"].items():
                if not _row_field_match(row, k, list(vs)):
                    return False
            return True
        if "bool" in clause:
            b = clause["bool"]
            for sub in (b.get("filter") or []):
                if not _accept(sub):
                    return False
            for sub in (b.get("must_not") or []):
                if _accept(sub):
                    return False
            shoulds = b.get("should") or []
            if shoulds:
                msm = b.get("minimum_should_match", 1)
                hits = sum(1 for sub in shoulds if _accept(sub))
                if hits < msm:
                    return False
            return True
        return True

    return all(_accept(c) for c in filters)


def _row_field_match(row: dict, key: str, values: list[Any]) -> bool:
    """Read a row field by ES key (strips ``.keyword``) and check membership."""
    bare = key[:-8] if key.endswith(".keyword") else key
    val = row.get(bare)
    # *_team fields can be a list (we store under row["teams"][bare]) — but
    # the standard scope filters target application / project / company, so
    # the simple scalar path covers the common case. Fall through to the
    # teams dict for *_team scoping.
    if val is None and bare.endswith("_team"):
        teams = row.get("teams") or {}
        val = teams.get(bare) or []
    if isinstance(val, (list, tuple, set)):
        return any(str(v) in {str(x) for x in values} for v in val)
    return str(val or "") in {str(x) for x in values}


# Admin-controlled source preference. Default ``"auto"`` keeps the legacy
# behaviour (git → ES on failure). ``"git"`` forces git and surfaces the
# error loudly instead of silently falling back; ``"es"`` bypasses git
# entirely. Stored in session_state so a sticky toggle persists across
# reruns without polluting URL state.
_INV_SRC_PREF_KEY = "_inv_source_pref_v1"


def _inv_source_pref() -> str:
    """Resolved source preference. ``"auto"`` unless the admin selected
    otherwise. Non-admins never get to set this — non-admin sessions
    silently coerce to ``"auto"`` so role escalation isn't a path."""
    pref = st.session_state.get(_INV_SRC_PREF_KEY)
    return pref if pref in ("auto", "git", "es") else "auto"


def _load_inventory_from_git_scoped(scope_json: str) -> tuple[list[dict], str, str, list[str]]:
    """Run the git inventory pipeline end-to-end (clone + parse + scope
    filter + author sync). Returns ``(rows, status_msg, head_sha, warnings)``
    where an empty ``head_sha`` indicates a hard failure (vault / clone /
    yaml). Centralised so :func:`_inventory_load` and
    :func:`_inventory_compare` share the same code path."""
    warnings: list[str] = []
    creds = _git_creds()
    host = creds["hostname"]
    if not host:
        v_err = _vault_last_error(GIT_VAULT_PATH)
        warnings.append(
            f"Git host not resolved from vault `{GIT_VAULT_PATH}`"
            + (f": {v_err}" if v_err else "")
        )
        return [], "host unresolved", "", warnings
    ok, head, status_msg = _ensure_inventory_repo(host)
    if not ok:
        return [], status_msg or "git unavailable", "", warnings
    # Sync git author identity on every successful sync.
    _git_set_author(
        INVENTORY_REPO_PATH,
        (st.session_state.get("username") or "").strip(),
        (st.session_state.get("email") or "").strip(),
    )
    if not _YAML_AVAILABLE:
        warnings.append("PyYAML not installed")
        return [], "PyYAML missing", head, warnings
    rows, parse_warnings = _load_inventory_from_git(head)
    warnings.extend(parse_warnings)
    if not rows:
        warnings.append("git checkout parsed 0 apps — check field aliases")
        return [], "0 apps parsed", head, warnings
    # Apply the same scope filters locally so role/company/project scoping
    # behaves identically across both paths.
    try:
        sf = json.loads(scope_json) if scope_json else []
        rows = [r for r in rows if _row_matches_es_filters(r, sf)]
    except Exception as e:
        warnings.append(f"scope filter: {type(e).__name__}")
    return rows, status_msg, head, warnings


def _inventory_load(scope_json: str) -> tuple[list[dict], str, str, list[str]]:
    """Resolve the inventory rows for the current scope.

    Honors the admin source-preference toggle:
      * ``"auto"`` — try git first; fall back to ES on any failure
        (the legacy behaviour).
      * ``"git"``  — force git; on failure, return zero rows with an
        emphatic status message rather than silently degrading.
      * ``"es"``   — bypass git entirely.

    Returns ``(rows, source, status, warnings)`` where ``source`` is one of
    ``"git"`` / ``"es"`` / ``"git-forced-failed"`` and ``status`` is a
    short human-readable phrase suitable for the source pill / banner.
    """
    pref = _inv_source_pref()

    # ``es`` — admin asked to ignore git outright. No-op clone, no diff.
    if pref == "es":
        rows = _fetch_full_inventory(scope_json)
        return rows, "es", "ES (admin chose)", []

    # ``git`` or ``auto`` — attempt the git path first.
    rows, status_msg, head, warnings = _load_inventory_from_git_scoped(scope_json)
    if rows:
        return rows, "git", status_msg, warnings

    # Git produced nothing. Branch on preference for what to do next.
    if pref == "git":
        # Forced git mode — DON'T silently fall back to ES. Return zero
        # rows so the inventory table empties and the alarm banner names
        # exactly what failed.
        return [], "git-forced-failed", status_msg or "git unavailable", warnings

    # Auto mode — fall through to the legacy ES projection.
    rows = _fetch_full_inventory(scope_json)
    return rows, "es", "ES fallback", warnings


# =============================================================================
# INVENTORY SYNC CHECK — git vs ES discrepancy detection
# =============================================================================
# When both sources are reachable, an admin should be able to spot rows that
# are in one but not the other, and rows that exist in both but disagree on
# specific fields. This is a deliberate manual operation (smart-loaded
# behind a button) — two full inventory fetches + a diff isn't something we
# want firing on every rerun. The result is stashed in session_state for
# the rest of the session so flipping tabs doesn't lose it.

# Fields compared 1:1 between the git row and the ES row.
_INV_COMPARE_FIELDS: tuple[str, ...] = (
    "project", "company", "app_type",
    "build_technology", "deploy_technology", "deploy_platform",
    "build_image_name", "build_image_tag",
    "deploy_image_name", "deploy_image_tag",
)


def _inv_norm(v: Any) -> str:
    """Normalise a row field for diff comparison — strips whitespace and
    lowercases. We don't want to flag '1.4.2 ' vs '1.4.2' as a real
    discrepancy, but we DO want to flag 'Active' vs 'active' since case
    might matter elsewhere; this normaliser only lowercases for the
    comparison, the surfaced VALUES preserve the original casing."""
    if isinstance(v, str):
        return v.strip().lower()
    if v is None:
        return ""
    return str(v).strip().lower()


def _inv_teams_norm(t: Any) -> dict[str, tuple[str, ...]]:
    """Render a row's teams dict into a comparable shape:
    ``{team_field: tuple(sorted unique values, case-folded)}``."""
    out: dict[str, tuple[str, ...]] = {}
    if not isinstance(t, dict):
        return out
    for k, vs in t.items():
        if isinstance(vs, (list, tuple, set)):
            uniq = sorted({str(v).strip().lower() for v in vs if v})
        elif vs:
            uniq = [str(vs).strip().lower()]
        else:
            uniq = []
        if uniq:
            out[k] = tuple(uniq)
    return out


def _inventory_compare(scope_json: str) -> dict:
    """Fetch from BOTH git and ES then compute a per-app discrepancy report.

    Pure computation on top of cached fetchers — both sides hit
    ``@st.cache_data``-memoised results, so calling this is cheap once the
    individual loaders are warm. Returns ``{"errors": {...}, "git_total":
    int, "es_total": int, "common": int, "only_in_git": [...],
    "only_in_es": [...], "field_diffs": [...]}``."""
    errors: dict[str, str] = {"git": "", "es": ""}

    # ── Git side ───────────────────────────────────────────────────────────
    git_rows, git_status, _head, git_warnings = _load_inventory_from_git_scoped(scope_json)
    if not git_rows:
        errors["git"] = git_status or "no git rows"

    # ── ES side ────────────────────────────────────────────────────────────
    try:
        es_rows = _fetch_full_inventory(scope_json)
    except Exception as e:
        es_rows = []
        errors["es"] = f"{type(e).__name__}: {e}"
    if not es_rows and not errors["es"]:
        errors["es"] = "no es rows"

    git_by_app = {r["application"]: r for r in git_rows if r.get("application")}
    es_by_app  = {r["application"]: r for r in es_rows  if r.get("application")}

    only_in_git = sorted(set(git_by_app) - set(es_by_app), key=str.lower)
    only_in_es  = sorted(set(es_by_app) - set(git_by_app),  key=str.lower)
    common      = sorted(set(git_by_app) & set(es_by_app),  key=str.lower)

    field_diffs: list[dict] = []
    for app in common:
        g, e = git_by_app[app], es_by_app[app]
        diffs: dict[str, dict] = {}
        for f in _INV_COMPARE_FIELDS:
            gv, ev = g.get(f), e.get(f)
            if _inv_norm(gv) != _inv_norm(ev):
                diffs[f] = {"git": (gv or ""), "es": (ev or "")}
        gt = _inv_teams_norm(g.get("teams"))
        et = _inv_teams_norm(e.get("teams"))
        team_keys = sorted(set(gt) | set(et))
        team_diffs: dict[str, dict] = {}
        for tk in team_keys:
            if gt.get(tk, ()) != et.get(tk, ()):
                team_diffs[tk] = {
                    "git": sorted([
                        v for v in (g.get("teams") or {}).get(tk, [])
                    ] if isinstance((g.get("teams") or {}).get(tk), list)
                    else [(g.get("teams") or {}).get(tk, "")] if (g.get("teams") or {}).get(tk) else []),
                    "es": sorted([
                        v for v in (e.get("teams") or {}).get(tk, [])
                    ] if isinstance((e.get("teams") or {}).get(tk), list)
                    else [(e.get("teams") or {}).get(tk, "")] if (e.get("teams") or {}).get(tk) else []),
                }
        if team_diffs:
            diffs["__teams__"] = team_diffs
        if diffs:
            field_diffs.append({
                "application": app,
                "project": g.get("project") or e.get("project") or "",
                "differences": diffs,
            })

    return {
        "errors":       errors,
        "git_total":    len(git_by_app),
        "es_total":     len(es_by_app),
        "common":       len(common),
        "only_in_git":  only_in_git,
        "only_in_es":   only_in_es,
        "field_diffs":  field_diffs,
        "warnings":     git_warnings,
        "checked_at":   datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# INVENTORY ↔ POSTGRES COMPARISON
# =============================================================================
# The Postgres ``devops_projects`` table holds the canonical (company,
# project) → teams mapping. The inventory's per-environment team fields
# (uat_team / prd_team / preprod_team) are MEANT to collapse to a single
# "ops" team — when they don't, both internal inconsistency and Postgres
# disagreement are surfaced separately. The comparison is run on demand
# from the SYNC CHECK tab.

# Inventory team fields that fold together into the canonical "ops team"
# value compared against Postgres. uat/prd/preprod are typically the same
# squad; differences within this set trigger the "ops_inconsistency" flag.
_INV_OPS_FIELDS: tuple[str, ...] = ("uat_team", "prd_team", "preprod_team")


def _row_teams_for_field(row: dict, field: str) -> list[str]:
    """Pull a team field's values from an inventory row. Tolerates both
    list-valued (multi-team) and scalar-valued shapes."""
    blob = (row.get("teams") or {}).get(field)
    if isinstance(blob, (list, tuple, set)):
        return sorted({str(v).strip() for v in blob if v})
    if blob:
        return [str(blob).strip()]
    return []


def _aggregate_inv_by_project(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Group inventory rows under ``(company, project)`` and union the
    *_team values across the project's apps. Returns a dict keyed on the
    tuple, with team sets per field plus the derived ``ops_*`` view:

        {
          (company, project): {
            "apps":         [app, ...],
            "dev_team":     [t, ...],
            "qc_team":      [t, ...],
            "uat_team":     [t, ...],
            "prd_team":     [t, ...],
            "preprod_team": [t, ...],
            # derived
            "ops_union":           [t, ...],   # all distinct values across uat/prd/preprod
            "ops_inconsistent":    bool,        # True when env-specific values differ
          }
        }
    """
    by_proj: dict[tuple[str, str], dict] = {}
    for r in rows:
        co = (r.get("company") or "").strip()
        pj = (r.get("project") or "").strip()
        if not pj:
            continue
        key = (co, pj)
        bucket = by_proj.setdefault(key, {
            "apps": [],
            "dev_team": set(),
            "qc_team": set(),
            "uat_team": set(),
            "prd_team": set(),
            "preprod_team": set(),
        })
        app = r.get("application") or ""
        if app and app not in bucket["apps"]:
            bucket["apps"].append(app)
        for f in ("dev_team", "qc_team") + _INV_OPS_FIELDS:
            for t in _row_teams_for_field(r, f):
                bucket[f].add(t)
    # Finalise per-bucket — sort sets, derive ops union/inconsistency.
    out: dict[tuple[str, str], dict] = {}
    for k, b in by_proj.items():
        per_env: dict[str, list[str]] = {
            f: sorted(b[f]) for f in _INV_OPS_FIELDS
        }
        # Non-empty env sets only — an env with no team isn't part of the
        # consistency check (you can't disagree with "absent").
        non_empty = [tuple(v) for v in per_env.values() if v]
        ops_inconsistent = len({tuple(v) for v in non_empty}) > 1
        ops_union = sorted({t for vs in per_env.values() for t in vs})
        out[k] = {
            "apps":            list(b["apps"]),
            "dev_team":        sorted(b["dev_team"]),
            "qc_team":         sorted(b["qc_team"]),
            "uat_team":        per_env["uat_team"],
            "prd_team":        per_env["prd_team"],
            "preprod_team":    per_env["preprod_team"],
            "ops_union":       ops_union,
            "ops_inconsistent": ops_inconsistent,
        }
    return out


def _team_set_norm(values: list[str] | str | None) -> tuple[str, ...]:
    """Normalise a team value to a comparable tuple of stripped+lowercase
    distinct strings. ``""`` / ``None`` / ``[]`` all map to ``()``."""
    if isinstance(values, (list, tuple, set)):
        return tuple(sorted({str(v).strip().lower() for v in values if v}))
    if values:
        return (str(values).strip().lower(),)
    return ()


def _inventory_vs_postgres_compare(scope_json: str) -> dict:
    """Compare the inventory (in scope) against the Postgres
    ``devops_projects`` table at the (company, project) granularity.

    Returns a structured diff:

        {
          "errors":      {"inventory": str, "postgres": str},
          "inv_total":   int,    # distinct (company, project) in inventory
          "pg_total":    int,    # distinct (company, project) in postgres
          "common":      int,
          "only_in_inv": [{"company", "project", "apps_n"}, ...],
          "only_in_pg":  [{"company", "project", "dev_team", "qc_team", "ops_team"}, ...],
          "diffs":       [{
              "company", "project",
              "apps":     [app, ...],
              "ops_inconsistent": bool,
              "ops_breakdown":    {"uat_team": [...], "prd_team": [...], "preprod_team": [...]},
              "fields": {
                  "dev_team": {"inventory": [...], "postgres": "..."},
                  "qc_team":  {...},
                  "ops_team": {"inventory": [...], "postgres": "..."},
              },
          }, ...],
          "checked_at":  iso-string,
        }
    """
    errors = {"inventory": "", "postgres": ""}

    # ── Inventory side — re-use whichever source is currently active so
    # the comparison matches what the operator is actually viewing.
    inv_rows, inv_source, inv_status, _inv_warnings = _inventory_load(scope_json)
    if not inv_rows:
        errors["inventory"] = inv_status or f"no inventory rows ({inv_source})"

    # ── Postgres side
    pg_rows, pg_err = _fetch_devops_projects_from_postgres()
    if pg_err:
        errors["postgres"] = pg_err

    inv_by_proj = _aggregate_inv_by_project(inv_rows)
    pg_by_proj: dict[tuple[str, str], dict] = {}
    for r in pg_rows:
        key = (r["company"], r["project"])
        # Postgres has one row per (company, project); duplicate rows would
        # be a data quality issue but we tolerate by last-wins.
        pg_by_proj[key] = r

    inv_keys = set(inv_by_proj.keys())
    pg_keys = set(pg_by_proj.keys())

    only_in_inv = sorted(inv_keys - pg_keys, key=lambda k: (k[0].lower(), k[1].lower()))
    only_in_pg  = sorted(pg_keys - inv_keys, key=lambda k: (k[0].lower(), k[1].lower()))
    common      = sorted(inv_keys & pg_keys, key=lambda k: (k[0].lower(), k[1].lower()))

    only_in_inv_out = [
        {
            "company": co, "project": pj,
            "apps_n":  len(inv_by_proj[(co, pj)]["apps"]),
        }
        for (co, pj) in only_in_inv
    ]
    only_in_pg_out = [
        {
            "company":  co, "project": pj,
            "dev_team": pg_by_proj[(co, pj)]["dev_team"],
            "qc_team":  pg_by_proj[(co, pj)]["qc_team"],
            "ops_team": pg_by_proj[(co, pj)]["ops_team"],
        }
        for (co, pj) in only_in_pg
    ]

    diffs: list[dict] = []
    for key in common:
        co, pj = key
        inv = inv_by_proj[key]
        pg  = pg_by_proj[key]
        per_field: dict[str, dict] = {}
        # dev / qc — straight comparison (inventory union vs postgres scalar)
        if _team_set_norm(inv["dev_team"]) != _team_set_norm(pg["dev_team"]):
            per_field["dev_team"] = {
                "inventory": inv["dev_team"],
                "postgres":  pg["dev_team"],
            }
        if _team_set_norm(inv["qc_team"]) != _team_set_norm(pg["qc_team"]):
            per_field["qc_team"] = {
                "inventory": inv["qc_team"],
                "postgres":  pg["qc_team"],
            }
        # ops — derived ops_union vs postgres ops_team
        if _team_set_norm(inv["ops_union"]) != _team_set_norm(pg["ops_team"]):
            per_field["ops_team"] = {
                "inventory": inv["ops_union"],
                "postgres":  pg["ops_team"],
            }
        if per_field or inv["ops_inconsistent"]:
            diffs.append({
                "company": co, "project": pj,
                "apps":    inv["apps"],
                "ops_inconsistent": inv["ops_inconsistent"],
                "ops_breakdown": {
                    "uat_team":     inv["uat_team"],
                    "prd_team":     inv["prd_team"],
                    "preprod_team": inv["preprod_team"],
                },
                "fields": per_field,
            })

    return {
        "errors":      errors,
        "inv_total":   len(inv_keys),
        "pg_total":    len(pg_keys),
        "common":      len(common),
        "only_in_inv": only_in_inv_out,
        "only_in_pg":  only_in_pg_out,
        "diffs":       diffs,
        "inv_source":  inv_source,
        "checked_at":  datetime.now(timezone.utc).isoformat(),
    }


# =============================================================================
# VAULT — shared credential resolver for Jenkins + S3
# =============================================================================
# `utils.vault.VaultClient` exposes `read_all_nested_secrets(path)`. We MUST
# instantiate a fresh `VaultClient()` for each read — the constructor is
# what re-initialises the auth token, so caching a single client as a
# Streamlit resource causes its token to go stale and every subsequent read
# fails with "permission denied / invalid token" once the original token's
# lease expires.
#
# The plaintext SECRETS are cached briefly (TTL=300s) so each rerun doesn't
# hammer vault — short enough that a rotation surfaces within minutes, long
# enough to absorb the typical click-to-click rerun storm.

_VAULT_ERR_KEY = "_vault_last_error_v1"  # session-state stash for admin UI


def _vault_remember_error(path: str, exc: Exception) -> None:
    """Record the most recent vault failure so admin-only empty-states can
    surface it. Keeps the dict bounded — one entry per path."""
    try:
        store = st.session_state.setdefault(_VAULT_ERR_KEY, {})
        store[path] = f"{type(exc).__name__}: {exc}"
    except Exception:
        pass


def _vault_last_error(path: str) -> str:
    """Return the most recent error string for *path*, or empty if the
    last read succeeded / the path was never read."""
    return (st.session_state.get(_VAULT_ERR_KEY) or {}).get(path, "")


@st.cache_data(ttl=300, show_spinner=False)
def _vault_secrets_raw(path: str) -> dict:
    """Cached vault read. RAISES on any failure — that's deliberate so
    Streamlit doesn't memoise an empty result for 5 minutes when the
    token is temporarily invalid. The public wrapper below catches and
    stashes the error for admin surfacing.

    Instantiates a fresh VaultClient inside the call (matches the
    platform's documented pattern — the constructor re-initialises the
    auth token, so a cached client would go stale and produce exactly
    the "invalid token" symptom)."""
    if not _VAULT_AVAILABLE or not path:
        return {}
    vc = _VaultClient()  # init vault token (per-call, matches platform docs)
    cfg = vc.read_all_nested_secrets(path) or {}
    return dict(cfg) if isinstance(cfg, dict) else {}


def _vault_secrets(path: str) -> dict:
    """Public resolver. Returns ``{}`` on any error, stashes the failure
    for the admin-only empty-state, and lets a successful retry on the
    next rerun clear the stash. Failures are NOT cached because the
    underlying raw function re-raises — only successes hit Streamlit's
    memoisation."""
    if not _VAULT_AVAILABLE or not path:
        return {}
    try:
        result = _vault_secrets_raw(path)
        # Clear any prior recorded error for this path on success.
        store = st.session_state.get(_VAULT_ERR_KEY)
        if isinstance(store, dict):
            store.pop(path, None)
        return result
    except Exception as e:
        _vault_remember_error(path, e)
        return {}


def _jenkins_creds() -> dict:
    """Resolved Jenkins credentials. Vault is the primary source; env vars
    (JENKINS_HOSTNAME / JENKINS_USER / JENKINS_TOKEN) are honored as a
    fallback so dev boxes without vault access still light up.

    Returns ``{"host": str, "public_name": str, "username": str,
    "token": str}`` — all strings, all may be empty on a misconfig.
    """
    cfg = _vault_secrets(JENKINS_VAULT_PATH)
    host = (cfg.get("host") or os.environ.get("JENKINS_HOSTNAME", "")).strip()
    public = (cfg.get("public_name") or host).strip() or host
    user = (cfg.get("username") or os.environ.get("JENKINS_USER", "")).strip()
    # The vault key is documented as `api_token`; env-var fallback uses the
    # legacy `JENKINS_TOKEN` name for consistency with existing deployments.
    token = (cfg.get("api_token") or os.environ.get("JENKINS_TOKEN", "")).strip()
    return {"host": host, "public_name": public, "username": user, "token": token}


@st.cache_data(ttl=300, show_spinner=False)
def _vault_secrets_nested_raw(path: str, sub: str) -> dict:
    """Cached two-arg vault read. Mirrors the platform's pattern:
    ``vc.read_all_nested_secrets(path, sub)``. Same caching contract as
    :func:`_vault_secrets_raw` — raises on failure so a transient token
    issue doesn't get memoised as ``{}``."""
    if not _VAULT_AVAILABLE or not path:
        return {}
    vc = _VaultClient()
    cfg = (
        vc.read_all_nested_secrets(path, sub) if sub
        else vc.read_all_nested_secrets(path)
    ) or {}
    return dict(cfg) if isinstance(cfg, dict) else {}


def _vault_secrets_nested(path: str, sub: str) -> dict:
    """Public wrapper. Catches and stashes errors keyed on ``path/sub``."""
    if not _VAULT_AVAILABLE or not path:
        return {}
    key = f"{path}/{sub}" if sub else path
    try:
        result = _vault_secrets_nested_raw(path, sub)
        store = st.session_state.get(_VAULT_ERR_KEY)
        if isinstance(store, dict):
            store.pop(key, None)
        return result
    except Exception as e:
        _vault_remember_error(key, e)
        return {}


def _postgres_creds() -> dict:
    """Resolve Postgres credentials from the PRD vault path.

    Always reads the single ``POSTGRES_VAULT_PATH`` entry — the
    dashboard never connects to a dev instance. Returns ``{host, port,
    database, username, password}``; an empty ``host`` means "not
    configured"."""
    cfg = _vault_secrets(POSTGRES_VAULT_PATH)
    if not cfg:
        return {}
    return {
        "host":     (cfg.get("host") or "").strip(),
        "port":     str(cfg.get("port") or "5432").strip(),
        "database": (cfg.get("database") or "").strip(),
        "username": (cfg.get("username") or "").strip(),
        "password": (cfg.get("password") or "").strip(),
    }


@st.cache_data(ttl=POSTGRES_DATA_TTL, show_spinner=False)
def _fetch_devops_projects_from_postgres() -> tuple[list[dict], str]:
    """Read the ``devops_projects`` table. Returns ``(rows, error)``.

    Connection details come fresh from vault on every cache miss; the
    rows themselves cache for ``POSTGRES_DATA_TTL`` since the table
    changes slowly (project assignments don't shift minute-to-minute).
    Errors are NOT cached — they surface and the next render retries.

    Each returned row is a plain dict with the five columns specified:
    ``company``, ``project``, ``dev_team``, ``qc_team``, ``ops_team``.
    All values are coerced to stripped strings; missing values become
    empty strings so downstream comparison code can treat them uniformly.
    """
    if not _POSTGRES_AVAILABLE:
        return [], "psycopg / psycopg2 not installed"
    creds = _postgres_creds()
    if not creds or not creds.get("host"):
        return [], "postgres creds not resolved (check vault)"
    conn = None
    try:
        try:
            _port = int(creds["port"])
        except (ValueError, TypeError):
            _port = 5432
        conn = _psycopg.connect(
            host=creds["host"],
            port=_port,
            dbname=creds["database"],
            user=creds["username"],
            password=creds["password"],
            connect_timeout=POSTGRES_CONNECT_TIMEOUT,
        )
        # We never write — explicit read-only intent for the connection
        # so a stray UPDATE wouldn't slip through if the SQL ever drifts.
        try:
            conn.set_session(readonly=True, autocommit=True)  # psycopg2 API
        except Exception:
            try:
                conn.read_only = True  # psycopg v3 API
            except Exception:
                pass
        # Quote the table identifier defensively. We deliberately do NOT
        # f-string it into the SQL itself — the env-var-driven name lives
        # in code-trusted config, but the explicit allow-list keeps SQLi
        # one more layer away.
        safe_table = POSTGRES_TABLE.strip()
        if not all(c.isalnum() or c in "_." for c in safe_table):
            return [], f"refusing unsafe table identifier: {safe_table!r}"
        cur = conn.cursor()
        cur.execute(
            f"SELECT company, project, dev_team, qc_team, ops_team "
            f"FROM {safe_table}"
        )
        rows_raw = cur.fetchall()
        cur.close()
        out: list[dict] = []
        for r in rows_raw:
            out.append({
                "company":   (str(r[0]) if r[0] is not None else "").strip(),
                "project":   (str(r[1]) if r[1] is not None else "").strip(),
                "dev_team":  (str(r[2]) if r[2] is not None else "").strip(),
                "qc_team":   (str(r[3]) if r[3] is not None else "").strip(),
                "ops_team":  (str(r[4]) if r[4] is not None else "").strip(),
            })
        return out, ""
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _prisma_s3_creds() -> dict:
    """Resolved S3 connection details for the scan-viewer bucket. Empty
    dict means "not configured" — viewer renders an actionable empty-state
    instead of trying to fetch."""
    cfg = _vault_secrets(PRISMA_S3_VAULT_PATH)
    if not cfg:
        return {}
    host = (cfg.get("host") or "").strip()
    if not host:
        return {}
    return {
        "host":       host,
        "port":       str(cfg.get("port") or "443").strip(),
        "access_key": (cfg.get("access_key") or "").strip(),
        # The platform mixes naming conventions — `secret_key` is canonical
        # but some entries write `secret_id`. Accept either.
        "secret_key": (cfg.get("secret_key") or cfg.get("secret_id") or "").strip(),
    }


def _prisma_s3_endpoint(host: str, port: str) -> str:
    """Build an S3-compatible endpoint URL from host + port. Defaults to
    HTTPS; only port 80 produces an HTTP URL. The host string may already
    include a scheme — we trust that and pass it through unchanged."""
    if not host:
        return ""
    if host.startswith(("http://", "https://")):
        return host.rstrip("/")
    p = (port or "443").strip()
    if p == "80":
        return f"http://{host}"
    if p in ("", "443"):
        return f"https://{host}"
    return f"https://{host}:{p}"


# =============================================================================
# JENKINS API — read-only status surface
# =============================================================================
# Tiny urllib-based client (no extra dependency). All endpoints used:
#   /api/json                                — root probe + queue size
#   /job/{folder}/job/{name}/api/json        — pipeline metadata
#   /job/{folder}/job/{name}/lastBuild/api/json
#   /job/{folder}/job/{name}/lastCompletedBuild/api/json
# We deliberately DON'T call wfapi or sse endpoints — the cost/value of
# in-flight stage detail is not worth a second round-trip from the panel.

def _jenkins_path_segments(path: str) -> str:
    """Convert a folder-style path like ``CICD/Build`` into Jenkins'
    nested ``/job/CICD/job/Build`` form. Each segment is URL-encoded
    so spaces/specials in folder names don't break the URL."""
    parts = [urllib.parse.quote(p, safe="") for p in path.split("/") if p]
    return "/" + "/".join("job/" + p for p in parts)


def _jenkins_request_full(url: str) -> tuple[Any, dict]:
    """Run a Jenkins GET with optional Basic auth. Returns ``(body, headers)``
    where headers is a plain ``dict`` (case-insensitive lookups handled by
    callers via ``.lower()`` keys). Raises ``RuntimeError`` on any HTTP /
    network / decode failure with a distinguishable phrase so the panel
    can label them precisely."""
    creds = _jenkins_creds()
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")
    if creds["username"] and creds["token"]:
        token = base64.b64encode(
            f"{creds['username']}:{creds['token']}".encode()
        ).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=JENKINS_TIMEOUT) as resp:
            body = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
        if not body:
            return {}, headers
        return json.loads(body.decode("utf-8", errors="replace")), headers
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"unreachable: {e.reason}") from e
    except json.JSONDecodeError:
        raise RuntimeError("non-JSON response from Jenkins")


def _jenkins_request(url: str) -> Any:
    """Body-only variant kept for the per-pipeline calls that don't need
    headers — keeps those callsites tidy."""
    body, _ = _jenkins_request_full(url)
    return body


def _jk_version_tuple(ver: str) -> tuple:
    """Parse a Jenkins version string (e.g. ``2.440.1``, ``2.440``) into a
    tuple suitable for comparison. Non-numeric segments fall through as
    strings so lexicographic comparison still does something reasonable for
    pre-release suffixes; an empty string sorts to a ``(-1,)`` sentinel so
    "unknown" is never treated as up-to-date."""
    if not ver:
        return (-1,)
    out: list[Any] = []
    for part in ver.strip().split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(part)
    return tuple(out)


def _jk_compare_versions(running: str, latest: str) -> str:
    """Return ``"current"``, ``"outdated"``, or ``"unknown"``.

    "unknown" covers the case where either side is missing — we never want
    to falsely claim "up to date" when we couldn't actually check."""
    if not running or not latest:
        return "unknown"
    if running.strip() == latest.strip():
        return "current"
    return "outdated" if _jk_version_tuple(running) < _jk_version_tuple(latest) else "current"


def _jenkins_root_url() -> str:
    """Normalise the resolved host into a fully-qualified URL. The vault
    entry MAY include a scheme; if not, we assume HTTPS."""
    h = (_jenkins_creds().get("host") or "").strip()
    if not h:
        return ""
    if not h.startswith(("http://", "https://")):
        h = "https://" + h
    return h.rstrip("/")


def _jenkins_extract_params(actions: list) -> dict:
    """Pull build parameters out of a Jenkins build's ``actions`` list.
    Matches both ``ParametersAction`` and the newer flat-form. Returns
    a plain ``{name: value}`` dict; missing actions yield ``{}``."""
    out: dict[str, str] = {}
    for act in actions or []:
        if not isinstance(act, dict):
            continue
        params = act.get("parameters")
        if not params:
            continue
        for p in params:
            if not isinstance(p, dict):
                continue
            name = p.get("name") or ""
            value = p.get("value")
            if name and value not in (None, ""):
                out[name] = str(value)
    return out


def _jenkins_extract_running_builds(job_data: dict) -> list[dict]:
    """``builds`` from /job/.../api/json contains the most-recent N builds;
    we filter to those still in progress and return their parameter sets."""
    out: list[dict] = []
    for b in job_data.get("builds") or []:
        if not isinstance(b, dict):
            continue
        if b.get("building"):
            out.append({
                "number":   b.get("number"),
                "url":      b.get("url"),
                "duration": b.get("duration") or 0,
                "estimated": b.get("estimatedDuration") or 0,
                "started":  b.get("timestamp") or 0,
                "params":   _jenkins_extract_params(b.get("actions") or []),
            })
    return out


@st.cache_data(ttl=JENKINS_TTL, show_spinner=False)
def _fetch_jenkins_status_raw() -> dict:
    """One-shot status fetch for the Jenkins panel. Returns a plain dict
    (so ``@st.cache_data`` can pickle it safely):

        {
          "ok":            bool,            # root probe succeeded
          "status_msg":    str,             # human-readable health line
          "url":           str,             # configured hostname
          "queue_size":    int,
          "fetched_at":    iso-string,
          "pipelines": {
              key: {
                  "exists":         bool,
                  "buildable":      bool,
                  "last_build":     {...} | None,
                  "running":        [{...}, ...],
                  "color":          "blue"|"red"|"disabled"|"notbuilt"|...,
                  "error":          str,        # set when this pipeline failed
              },
              ...
          },
        }
    """
    creds = _jenkins_creds()
    out: dict[str, Any] = {
        "ok": False,
        "status_msg": "",
        "url": creds.get("host") or "",
        "public_name": creds.get("public_name") or creds.get("host") or "",
        "queue_size": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "pipelines": {},
        # Version telemetry — admin-only in the UI even when the wider
        # panel eventually opens up to other roles.
        "version": {
            "running":     "",   # X-Jenkins header from the root probe
            "latest":      "",   # latest core advertised by /updateCenter
            "compare":     "unknown",   # current / outdated / unknown
            "check_error": "",   # populated if the update-center probe failed
        },
    }
    root = _jenkins_root_url()
    if not root:
        out["status_msg"] = (
            "Jenkins host not resolved — check vault path "
            f"`{JENKINS_VAULT_PATH}` or env JENKINS_HOSTNAME"
        )
        return out
    # Root probe — ALSO yields the queue depth + the running version
    # (Jenkins always sets X-Jenkins on /api/json regardless of auth state).
    try:
        root_data, root_headers = _jenkins_request_full(
            f"{root}/api/json?tree=mode,nodeName"
        )
        out["version"]["running"] = (
            (root_headers.get("x-jenkins") or "").strip()
        )
        # Queue depth: light secondary call; if it fails, we still report ok.
        try:
            q = _jenkins_request(f"{root}/queue/api/json?tree=items[id]")
            out["queue_size"] = len(q.get("items") or [])
        except Exception:
            pass
        out["ok"] = True
        out["status_msg"] = (
            f"connected · {root_data.get('mode','?')} · queue {out['queue_size']}"
        )
    except RuntimeError as e:
        out["status_msg"] = str(e)
        return out

    # Latest-core probe via the configured update site. This may fail on
    # air-gapped instances or fresh installs that haven't refreshed their
    # update sites yet — we record the failure reason but keep the rest of
    # the panel rendering normally.
    try:
        uc = _jenkins_request(
            f"{root}/updateCenter/site/default/api/json"
            f"?tree=data[core[name,version,buildDate]]"
        )
        core = (((uc or {}).get("data") or {}).get("core") or {})
        out["version"]["latest"] = (core.get("version") or "").strip()
    except RuntimeError as e:
        out["version"]["check_error"] = str(e)
    except Exception as e:
        out["version"]["check_error"] = f"{type(e).__name__}: {e}"
    out["version"]["compare"] = _jk_compare_versions(
        out["version"]["running"], out["version"]["latest"]
    )

    # Per-pipeline metadata + last build + in-flight builds. Fetched in
    # parallel because every call is an independent HTTP round-trip.
    def _pipeline_status(key: str, cfg: dict) -> tuple[str, dict]:
        seg = _jenkins_path_segments(cfg["path"])
        pdata: dict[str, Any] = {
            "exists": False, "buildable": False, "color": "",
            "last_build": None, "running": [], "error": "",
        }
        try:
            job = _jenkins_request(
                f"{root}{seg}/api/json"
                f"?tree=buildable,color,inQueue,builds[number,building,timestamp,duration,estimatedDuration,url,actions[parameters[name,value]]]"
            )
            pdata["exists"] = True
            pdata["buildable"] = bool(job.get("buildable"))
            pdata["color"] = job.get("color") or ""
            pdata["running"] = _jenkins_extract_running_builds(job)
        except RuntimeError as e:
            pdata["error"] = str(e)
            return key, pdata
        try:
            last = _jenkins_request(
                f"{root}{seg}/lastCompletedBuild/api/json"
                f"?tree=number,result,timestamp,duration,url,displayName,actions[parameters[name,value]]"
            )
            pdata["last_build"] = {
                "number":      last.get("number"),
                "result":      last.get("result") or "",
                "timestamp":   last.get("timestamp") or 0,
                "duration":    last.get("duration") or 0,
                "url":         last.get("url") or "",
                "display":     last.get("displayName") or "",
                "params":      _jenkins_extract_params(last.get("actions") or []),
            }
        except RuntimeError:
            # No completed build yet — leave last_build as None, not an error.
            pass
        return key, pdata

    with ThreadPoolExecutor(max_workers=len(JENKINS_PIPELINES)) as ex:
        futures = [
            ex.submit(_pipeline_status, k, c)
            for k, c in JENKINS_PIPELINES.items()
        ]
        for fut in futures:
            key, pdata = fut.result()
            out["pipelines"][key] = pdata
    return out


# =============================================================================
# PRISMA SCAN VIEWER — S3 fetch helpers
# =============================================================================

def _prisma_scan_s3_key(project: str, application: str, version: str) -> str:
    """Render the configured key template into a concrete S3 object key.

    Empty placeholders are tolerated (some bucket layouts only key on app +
    version) — the resulting key may have ``//`` collapses that S3 will
    reject, which the caller surfaces as a useful error rather than a 404."""
    return PRISMA_S3_KEY_PATTERN.format(
        project=project or "",
        application=application or "",
        version=version or "",
    )


def _prisma_scan_console_url(endpoint: str, bucket: str, key: str) -> str:
    """Build a "view in browser" URL for the loaded scan. The S3-compatible
    service is custom (likely MinIO), so we point at the direct GET URL on
    the endpoint rather than the AWS console — the operator opening that
    link still needs valid creds against the service, but the URL itself
    is a usable permalink for sharing across their network."""
    if not endpoint or not bucket or not key:
        return ""
    safe_key = urllib.parse.quote(key, safe="/")
    return f"{endpoint.rstrip('/')}/{urllib.parse.quote(bucket, safe='')}/{safe_key}"


@st.cache_data(ttl=PRISMA_SCAN_TTL, show_spinner=False, max_entries=20)
def _fetch_prisma_scan_html(
    bucket: str, key: str, endpoint: str, region: str,
    access_key: str, secret_key: str,
) -> tuple[str, int, str]:
    """Fetch the HTML report for one ``(bucket, key)`` pair against the
    S3-compatible ``endpoint`` (host:port pulled from vault).

    Returns ``(html, content_length, error)``. On success ``error`` is empty;
    on failure ``html`` is empty and ``error`` carries a short label suitable
    for the viewer's error banner.

    Cache settings:
      - ``ttl``         : reports are immutable per (app, version), so a
                          long TTL is safe; 10 minutes lets us absorb retries.
      - ``max_entries`` : 20 — bounds memory if a user pages through many
                          scans in a single session. The least-recently-used
                          entry is evicted automatically by Streamlit.

    All credential fields are part of the cache key so a vault rotation
    invalidates the cached scans on the next read.
    """
    if not _BOTO3_AVAILABLE:
        return "", 0, "boto3 not installed (pip install boto3)"
    if not bucket or not key:
        return "", 0, "S3 bucket / key not configured"
    if not endpoint:
        return "", 0, "S3 endpoint not resolved (check vault path)"
    try:
        # ``endpoint_url`` lets boto3 talk to S3-compatible services (MinIO,
        # Ceph, etc.). We don't pass a session_token — the vault here
        # exposes only access_key + secret_key, which is the access pattern
        # for static service credentials.
        s3 = _boto3.client(
            "s3",
            region_name=region or "us-east-1",
            endpoint_url=endpoint,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
        )
        obj = s3.get_object(Bucket=bucket, Key=key)
        body_bytes = obj["Body"].read()
        size = int(obj.get("ContentLength") or len(body_bytes))
        # decode with replace so a single bad byte in the report doesn't
        # blow up rendering — viewers tolerate substituted glyphs better
        # than a stack trace.
        return body_bytes.decode("utf-8", errors="replace"), size, ""
    except _BotoClientError as e:
        err = e.response.get("Error", {}) if hasattr(e, "response") else {}
        code = err.get("Code") or "ClientError"
        return "", 0, f"S3 {code}: {err.get('Message') or '(no detail)'}"
    except _BotoCoreError as e:
        return "", 0, f"S3 connection: {type(e).__name__}"
    except Exception as e:
        return "", 0, f"unexpected: {type(e).__name__}: {e}"


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
    """Aggregate open Jira issues, scoped strictly to a set of inventory
    projects.

    Two filter columns are matched in parallel via ``bool.should``:
      - ``project``    — Jira project name (e.g. "Engineering")
      - ``projectkey`` — Jira project key  (e.g. "ENG")
    Either match counts, so an inventory project name that lines up with
    EITHER side surfaces its issues correctly.

    No silent fleet fallback: if the project filter returns zero issues,
    the tile shows zero. The previous version fell back to the entire
    Jira fleet when the project pass came up empty, which broke the
    superset invariant — selecting a SINGLE project that happened to
    match could return MORE issues than the "all projects" view (which
    returned the whole fleet because no inventory name matched). The
    fleet-wide query is now ONLY emitted when the caller passes an
    empty projects list (typically: admin with no team / project
    scope active).

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

    # ── Project-scoped pass — the canonical path for any non-empty
    # projects list. Returns whatever the filter actually matches,
    # including zero. Mathematical invariant restored: counts for a
    # subset of projects are always ≤ counts for the superset.
    if _projects:
        try:
            resp = es_search(
                IDX["jira"],
                {
                    "query": {"bool": {
                        "filter": [{"bool": {
                            "should": [
                                {"terms": {"project":    _projects}},
                                {"terms": {"projectkey": _projects}},
                            ],
                            "minimum_should_match": 1,
                        }}],
                        "must_not": _must_not_closed,
                    }},
                    "aggs": _aggs,
                    "track_total_hits": True,
                },
                size=0,
            )
        except Exception:
            return _empty
        _total, _by_p, _by_t = _extract(resp)
        return {
            "total": _total,
            "priority": _by_p,
            "type": _by_t,
            "scope": "projects",
        }

    # ── Fleet pass — only when no projects were passed at all.
    # Typically: admin with no team / project scope active.
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
    # Published so the Sync Check panel can run its dual fetch against the
    # SAME scope the inventory table is showing — otherwise the panel's
    # diff would silently mix scopes and produce false positives.
    st.session_state["_iv_scope_key_v1"] = _iv_scope_key
    # Full scope rows — git checkout first (faster + authoritative), ES is
    # the safety net if the clone or parse fails. The source & status are
    # stashed for the admin-only source pill rendered just below the
    # inventory tab header.
    _inv_rows_all, _iv_source, _iv_source_status, _iv_source_warnings = _inventory_load(_iv_scope_key)
    st.session_state["_iv_source_v1"] = _iv_source
    st.session_state["_iv_source_status_v1"] = _iv_source_status
    st.session_state["_iv_source_warnings_v1"] = list(_iv_source_warnings)
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
    # Independent fetches run in PARALLEL via a small ThreadPoolExecutor —
    # @st.cache_data is thread-safe, and ES round-trips are I/O-bound, so
    # the 3-up + 4-up batches collapse from sequential into single-wave.
    _iv_apps = tuple(sorted({r["application"] for r in _inv_rows_all}))
    if _iv_apps:
        _iv_apps_json = json.dumps(sorted(_iv_apps))
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="iv-stage2") as _ex:
            _f_prd     = _ex.submit(_fetch_prd_status,     _iv_apps)
            _f_stages  = _ex.submit(_fetch_latest_stages,  _iv_apps)
            _f_devproj = _ex.submit(_fetch_devops_projects, _iv_apps_json)
            _iv_prd_map     = _f_prd.result()
            _iv_stages_map  = _f_stages.result()
            _iv_devproj_map = _f_devproj.result()
    else:
        _iv_prd_map = _iv_stages_map = _iv_devproj_map = {}

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
    # Publish for the Prisma Scan Viewer's picker — the viewer pulls
    # (app, version) options from these without firing any extra ES /
    # S3 calls. Restricted to (project, app) tuples actually present in
    # this scope so the picker stays scope-aware.
    st.session_state["_psv_app_rows"] = [
        {"application": r.get("application", ""), "project": r.get("project", "")}
        for r in _inv_rows_all
        if r.get("application")
    ]
    st.session_state["_psv_prisma_keys"] = list(_iv_prisma_keys)
    if _iv_prisma_keys:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="iv-stage3") as _ex:
            _f_pri = _ex.submit(_fetch_prismacloud,  _iv_prisma_keys_t)
            _f_inv = _ex.submit(_fetch_invicti,      _iv_prisma_keys_t)
            _f_zap = _ex.submit(_fetch_zap,          _iv_prisma_keys_t)
            _f_vmd = _ex.submit(_fetch_version_meta, _iv_prisma_keys_t)
            _iv_prisma_map  = _f_pri.result()
            _iv_invicti_map = _f_inv.result()
            _iv_zap_map     = _f_zap.result()
            _iv_vermeta_map = _f_vmd.result()
    else:
        _iv_prisma_map = _iv_invicti_map = _iv_zap_map = _iv_vermeta_map = {}

    def _iv_image_outdated(current: str, recommended: str) -> bool:
        """True when the recommended image version is set and differs from
        the current — both stripped + case-insensitive. An empty recommended
        means "no advisory" → not flagged."""
        _cur = (current or "").strip()
        _rec = (recommended or "").strip()
        if not _rec:
            return False
        return _cur.lower() != _rec.lower()

    def _iv_outdated_flags(app: str) -> tuple[bool, bool, dict]:
        """Return ``(build_outdated, deploy_outdated, raw_dict)`` for an
        app, where raw_dict carries the four versions for tooltips."""
        _dp = _iv_devproj_map.get(app) or {}
        _bc = (_dp.get("BuildCurrentVer") or "").strip()
        _br = (_dp.get("BuildRecommendationVer") or "").strip()
        _dc = (_dp.get("DeployCurrentVer") or "").strip()
        _dr = (_dp.get("DeployRecommendationVer") or "").strip()
        return (
            _iv_image_outdated(_bc, _br),
            _iv_image_outdated(_dc, _dr),
            {
                "build_current": _bc, "build_recommended": _br,
                "deploy_current": _dc, "deploy_recommended": _dr,
            },
        )

    # OCP / K8s apps are containerised and so MUST have a Prismacloud image
    # scan for every shipped version. Other platforms (VM, serverless, etc.)
    # aren't covered by Prismacloud, so missing scans there are expected and
    # not flagged. Match is case-insensitive and tolerant of "Kubernetes",
    # "OpenShift", and the short "K8s" / "OCP" forms.
    _IV_CONTAINER_PLATFORMS = {"ocp", "openshift", "k8s", "kubernetes"}

    def _iv_container_platform(app: str) -> str:
        """Return a normalised container-platform label (``"OCP"`` / ``"K8s"``)
        for an app, or ``""`` when the app doesn't run on a Prismacloud-covered
        platform. Used to decide whether a missing image scan is a gap or
        simply out-of-scope."""
        _p = ((_iv_devproj_map.get(app) or {}).get("DeployPlatform") or "").strip().lower()
        if not _p:
            return ""
        if _p in ("ocp", "openshift"):
            return "OCP"
        if _p in ("k8s", "kubernetes"):
            return "K8s"
        return ""

    # ── Team extraction helper (inventory rows may carry multiple *_team fields) ─
    # For admins we surface every *_team field so the Teams tile reflects the
    # full ownership graph. For scoped roles we restrict the "teams" of a row
    # to just the values in that role's own team field (dev_team for
    # Developer, qc_team for QC, ops_team for Operations) — otherwise a
    # co-assigned team on a shared project would leak into the Team tile and
    # let the user pick teams they don't actually belong to.
    # Role-scoped row team fields. Walks the UNION across every detected
    # role (multi-role users see every team field they own, no leakage).
    _iv_row_team_fields: list[str] = []
    if not _is_admin:
        _iv_row_team_fields = [
            _f.replace(".keyword", "") for _f in _user_team_fields
        ]

    # `_iv_row_teams` runs in HOT loops — once per row × 8 leave-one-out
    # aggregations + the post-filter aggregate + the table renderer's haystack
    # walk. For a 2k-row fleet that's ~20k+ calls per rerun, each doing dict
    # lookups + set construction. Memoize per-row using id() (rows are dict
    # objects with stable identity for the duration of a single render) so the
    # work happens exactly once per unique row regardless of how many code
    # paths consult it.
    _iv_row_teams_cache: dict[int, frozenset[str]] = {}

    def _iv_row_teams(_r: dict) -> frozenset[str]:
        """Team values on a row — role-scoped for non-admin users."""
        _rid = id(_r)
        _hit = _iv_row_teams_cache.get(_rid)
        if _hit is not None:
            return _hit
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
        _frozen = frozenset(_out)
        _iv_row_teams_cache[_rid] = _frozen
        return _frozen

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
        _jira_show = _user_shows_jira
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

        # ── Per-stage security posture ─────────────────────────────────
        # The role determines which stage(s) to show:
        #   Developer   → DEV   (own pipeline)
        #   QC          → QC    (own pipeline)
        #   Operations  → PRD   (own pipeline)
        #   Admin/CLevel→ DEV + QC + PRD side by side
        # For each stage we sum V* (critical/high/medium/low) across all
        # three scanners (Prismacloud + Invicti + ZAP) for the version that
        # actually shipped to that stage on each in-scope app.
        # Stage list per role:
        #   Developer  → dev (own pipeline only)
        #   QC         → qc
        #   Operations → uat + prd (matches _ROLE_ENVS["Operations"])
        #   Admin/CLevel → dev + qc + uat + prd (full ladder)
        _SEC_STAGE_LABEL = {"dev": "Dev", "qc": "QC", "uat": "UAT", "prd": "PRD"}
        _ROLE_SEC_STAGES = {
            "Developer":  ["dev"],
            "QC":         ["qc"],
            "Operations": ["uat", "prd"],
        }
        # Stage list per user — admins see the full ladder; non-admin users
        # union every detected role's stages so a Developer+QC user reads
        # both "dev" and "qc" rows. Order is enforced by _SEC_STAGE_LABEL
        # iteration (dev → qc → uat → prd) for visual stability.
        if _is_admin:
            _sec_stages: list[str] = ["dev", "qc", "uat", "prd"]
        else:
            _seen_sec: set[str] = set()
            for _r in (_NON_ADMIN_DETECTED or [_effective_role]):
                for _s in _ROLE_SEC_STAGES.get(_r) or []:
                    _seen_sec.add(_s)
            _sec_stages = [
                _s for _s in ("dev", "qc", "uat", "prd")
                if _s in _seen_sec
            ] or ["prd"]

        def _sec_aggregate(stage: str) -> dict:
            """Sum V* across all 3 scanners for the given stage's version
            of every in-scope app. Returns ``{vc, vh, vm, vl, src_totals,
            apps_scanned, apps_with_ver}``."""
            _src = {
                "prisma":  {"vc": 0, "vh": 0, "vm": 0, "vl": 0, "apps": 0},
                "invicti": {"vc": 0, "vh": 0, "vm": 0, "vl": 0, "apps": 0},
                "zap":     {"vc": 0, "vh": 0, "vm": 0, "vl": 0, "apps": 0},
            }
            _scanned: set[str] = set()
            _with_ver = 0
            for _ap in _post_apps:
                _stages = _iv_stages_map.get(_ap) or {}
                _sd = _stages.get(stage) or {}
                _ver = (_sd or {}).get("version") or ""
                if not _ver:
                    continue
                _with_ver += 1
                for _name, _smap in (
                    ("prisma",  _iv_prisma_map),
                    ("invicti", _iv_invicti_map),
                    ("zap",     _iv_zap_map),
                ):
                    _sc = _smap.get((_ap, _ver))
                    if not _sc:
                        continue
                    _scanned.add(_ap)
                    _src[_name]["apps"] += 1
                    _src[_name]["vc"]   += int(_sc.get("Vcritical") or 0)
                    _src[_name]["vh"]   += int(_sc.get("Vhigh")     or 0)
                    _src[_name]["vm"]   += int(_sc.get("Vmedium")   or 0)
                    _src[_name]["vl"]   += int(_sc.get("Vlow")      or 0)
            return {
                "vc": sum(_src[s]["vc"] for s in _src),
                "vh": sum(_src[s]["vh"] for s in _src),
                "vm": sum(_src[s]["vm"] for s in _src),
                "vl": sum(_src[s]["vl"] for s in _src),
                "src_totals":   _src,
                "apps_scanned": len(_scanned),
                "apps_with_ver": _with_ver,
            }

        _sec_per_stage = {st: _sec_aggregate(st) for st in _sec_stages}

        _SRC_META = {
            "prisma":  ("⛟",  "Prismacloud", "var(--cc-blue)"),
            "invicti": ("⊛",  "Invicti",     "var(--cc-teal)"),
            "zap":     ("⌖",  "ZAP",         "var(--cc-amber)"),
        }

        # Tile-level severity tag: pick the WORST stage's worst severity
        # so a clean prd doesn't visually mask a critical-flooded dev.
        _worst_vc = max((_sec_per_stage[s]["vc"] for s in _sec_stages), default=0)
        _worst_vh = max((_sec_per_stage[s]["vh"] for s in _sec_stages), default=0)
        _any_scanned = any(_sec_per_stage[s]["apps_scanned"] for s in _sec_stages)
        _sec_tag = (
            "crit" if _worst_vc > 0
            else "warn" if _worst_vh > 0
            else "ok" if _any_scanned
            else ""
        )
        _sec_tag_lbl = (
            f"{_worst_vc} crit" if _worst_vc > 0
            else f"{_worst_vh} high" if _worst_vh > 0
            else "clean" if _any_scanned
            else "unscanned"
        )

        # Hero number: total crit+high across all rendered stages.
        _v_crit_high = sum(
            _sec_per_stage[s]["vc"] + _sec_per_stage[s]["vh"]
            for s in _sec_stages
        )

        def _stage_bar(stage: str) -> str:
            _ag = _sec_per_stage[stage]
            return _svg_dist_bar([
                (_ag["vc"], "var(--cc-red)",       "critical"),
                (_ag["vh"], "var(--cc-amber)",     "high"),
                (_ag["vm"], "var(--cc-blue)",      "medium"),
                (_ag["vl"], "var(--cc-text-mute)", "low"),
            ])

        def _stage_src_chips(stage: str) -> str:
            _ag = _sec_per_stage[stage]
            _chips: list[str] = []
            for _src in ("prisma", "invicti", "zap"):
                _t = _ag["src_totals"][_src]
                _findings = _t["vc"] + _t["vh"] + _t["vm"] + _t["vl"]
                if _t["apps"] == 0:
                    continue
                _glyph, _name, _color = _SRC_META[_src]
                _chips.append(
                    f'<span class="iv-sec-src" style="--iv-sec-src-c:{_color}">'
                    f'<span class="iv-sec-src-g">{_glyph}</span>'
                    f'<span class="iv-sec-src-n">{_name}</span>'
                    f'<b>{_findings}</b>'
                    f'<span class="iv-sec-src-apps">on {_t["apps"]} '
                    f'app{"s" if _t["apps"] != 1 else ""}</span>'
                    f'</span>'
                )
            return (
                '<div class="iv-sec-srcs">' + "".join(_chips) + '</div>'
                if _chips else ''
            )

        if len(_sec_stages) == 1:
            # Single-stage view — keep the old tile shape (one bar + one
            # scanner-attribution chip strip below). Title gains the stage
            # name so "PRD" / "QC" / "DEV" is unambiguous.
            _sec_only = _sec_stages[0]
            _sec_bar = _stage_bar(_sec_only)
            _sec_src_html = _stage_src_chips(_sec_only)
            _sec_stage_label = _SEC_STAGE_LABEL[_sec_only]
            _sec_apps_scanned_n = _sec_per_stage[_sec_only]["apps_scanned"]
            _sec_apps_with_ver  = _sec_per_stage[_sec_only]["apps_with_ver"]
            _sec_multi_html = ""
        else:
            # Admin / CLevel — three mini-rows stacked, one per stage.
            # The hero number sums all three stages; each row has its own
            # label, count, and per-stage scanner-attribution.
            _multi_rows: list[str] = []
            for _st_key in _sec_stages:
                _ag = _sec_per_stage[_st_key]
                _stage_total = _ag["vc"] + _ag["vh"] + _ag["vm"] + _ag["vl"]
                _multi_rows.append(
                    f'<div class="iv-sec-stage-row">'
                    f'  <div class="iv-sec-stage-row-head">'
                    f'    <span class="iv-sec-stage-name">'
                    f'{_SEC_STAGE_LABEL[_st_key]}</span>'
                    f'    <span class="iv-sec-stage-count">'
                    f'<b>{_ag["vc"]}</b>·{_ag["vh"]}·{_ag["vm"]}·{_ag["vl"]}'
                    f'</span>'
                    f'    <span class="iv-sec-stage-apps">'
                    f'{_ag["apps_scanned"]}/{_ag["apps_with_ver"]} apps'
                    f'</span>'
                    f'  </div>'
                    f'  {_stage_bar(_st_key)}'
                    f'</div>'
                )
            _sec_bar = ""              # the per-stage rows include their own bars
            _sec_src_html = ""         # scanner attribution moves below the rows
            _sec_stage_label = "all stages"
            _sec_apps_scanned_n = sum(
                _sec_per_stage[s]["apps_scanned"] for s in _sec_stages
            )
            _sec_apps_with_ver = sum(
                _sec_per_stage[s]["apps_with_ver"] for s in _sec_stages
            )
            _sec_multi_html = (
                '<div class="iv-sec-stages">' + "".join(_multi_rows) + '</div>'
            )

        # Compatibility shim — keep `_apps_scanned_n` / `_apps_with_ver`
        # names for the existing tile sub-line template.
        _apps_scanned_n = _sec_apps_scanned_n
        _apps_with_ver  = _sec_apps_with_ver

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
            # Tile 4: Security posture (Prismacloud + Invicti + ZAP) —
            # per-stage for non-admin roles, stacked dev/qc/prd for admin.
            + '<div class="iv-pulse-tile iv-pulse-tile--sec" style="--iv-pulse-accent:'
              'linear-gradient(90deg,var(--cc-red),var(--cc-amber))">'
            '<div class="iv-pulse-label">'
            + f'<span>Security posture · {_sec_stage_label}</span>'
            + (f'<span class="iv-pulse-tag {_sec_tag}">{_sec_tag_lbl}</span>'
               if _sec_tag else '')
            + '</div>'
            + f'<div class="iv-pulse-value">{_v_crit_high}</div>'
            + f'<div class="iv-pulse-sub">crit + high · <b>{_apps_scanned_n}</b>/'
              f'<b>{_apps_with_ver}</b> '
            + ('stage-version' if len(_sec_stages) == 1 else 'cross-stage')
            + ' scan'
            + ('' if _apps_with_ver == 1 else 's')
            + '</div>'
            + _sec_multi_html
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
        # Flag a missing Prismacloud image scan for containerised apps
        # (OCP / K8s). Other platforms aren't covered by Prismacloud, so a
        # missing scan there is expected — only flag where the absence is a
        # real gap. The flag is per-version: if THIS version has no scan in
        # `_iv_prisma_map`, mark this chip individually.
        _platform = _iv_container_platform(app)
        _no_scan_warn = ""
        _btn_classes = "iv-stage-ver"
        if _platform and not _iv_prisma_map.get((app, _ver)):
            _btn_classes += " is-no-scan"
            _ns_tip = (
                f"No Prismacloud scan on record for {app}@{_ver} — "
                f"app runs on {_platform} so an image scan is expected."
            )
            _no_scan_warn = (
                f'<span class="iv-stage-noscan" title="{html.escape(_ns_tip)}">⚠</span>'
            )
        _btn_title = (
            "Click for version details" if not _no_scan_warn
            else f"Click for version details · No Prismacloud scan ({_platform})"
        )
        _btn = (
            f'<button type="button" class="{_btn_classes}" '
            f'popovertarget="{_iv_ver_pop_id(app, stage, _ver)}" '
            f'title="{_btn_title}">{_dot}{_no_scan_warn}{_ver}</button>'
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

    def _iv_outdated_pills(app: str) -> str:
        """Tiny inline pills appended to the app cell when the app's build
        or deploy image trails the recommended version. Hovering the pill
        surfaces "current → recommended" so users see the upgrade path
        without opening the popover."""
        _b_old, _d_old, _ver = _iv_outdated_flags(app)
        if not (_b_old or _d_old):
            return ""
        _pills: list[str] = []
        if _b_old:
            _tip = (
                f"Build image — current "
                f"{_ver['build_current'] or '—'} → recommended "
                f"{_ver['build_recommended']}"
            )
            _pills.append(
                f'<span class="iv-outdated-pill" title="{html.escape(_tip)}">'
                f'⬆ B</span>'
            )
        if _d_old:
            _tip = (
                f"Deploy image — current "
                f"{_ver['deploy_current'] or '—'} → recommended "
                f"{_ver['deploy_recommended']}"
            )
            _pills.append(
                f'<span class="iv-outdated-pill" title="{html.escape(_tip)}">'
                f'⬆ D</span>'
            )
        return (
            '<span class="iv-outdated-row">' + "".join(_pills) + '</span>'
        )

    def _iv_app_cell(app: str) -> str:
        return (
            f'<div class="iv-app-cell">'
            f'<button type="button" class="el-app-trigger" '
            f'popovertarget="{_iv_app_pop_id(app)}" '
            f'title="Click for full inventory details">{app}</button>'
            f'{_iv_outdated_pills(app)}'
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
    # Popover HTML is bulky (each version popover carries 3 security cards +
    # provenance + URLs ≈ 4-8 KB; a 2k-app scope can produce 50+ MB). Even with
    # the server-side cache, Streamlit re-emits the entire markdown payload on
    # every rerun, so a giant blob freezes the browser on every filter click.
    # We therefore restrict popover construction to the elements actually
    # REACHABLE from the visible page slice:
    #   · table rows on the current page  →  app + project + version popovers
    #   · project ribbon (top 24 chips)   →  project popovers + their app lists
    # Anything outside these sets isn't clickable on this render anyway.
    _visible_page_apps: set[str] = {
        r["application"] for r in _inv_rows if r.get("application")
    }
    _visible_page_projects: set[str] = {
        r["project"] for r in _inv_rows if r.get("project")
    }
    _ribbon_projects: set[str] = (
        {p for p, _ in _pr_visible} if _pr_by_proj else set()
    )
    # Project popovers list every app that belongs to the project, so we need
    # an app popover for each of those even if the row isn't on this page.
    _visible_projects_reach: set[str] = _visible_page_projects | _ribbon_projects
    _visible_apps_reach: set[str] = set(_visible_page_apps)
    for _pj in _visible_projects_reach:
        _pdata_v = _iv_proj_map.get(_pj) or {}
        for _a_v in (_pdata_v.get("apps") or []):
            _visible_apps_reach.add(_a_v)

    _iv_popovers: list[str] = []
    _IV_POP_SS = "_iv_pop_html_cache_v1"
    _iv_pop_store: dict = st.session_state.setdefault(_IV_POP_SS, {})
    _iv_pop_bucket = int(datetime.now(timezone.utc).timestamp() // CACHE_TTL)
    # Cache key now also tracks the visible reach. Page changes (pager,
    # search, pill filters that narrow which rows render) all alter this
    # signature, so the popover blob is rebuilt only when the reach actually
    # changes — and stays small instead of growing with the full scope.
    _iv_pop_visible_sig = json.dumps(
        [sorted(_visible_apps_reach), sorted(_visible_projects_reach)],
    )
    _iv_pop_cache_key = (_iv_scope_key, _iv_pop_bucket, _iv_pop_visible_sig)
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

    # App popovers — only for apps reachable from the current page (visible
    # rows + apps inside visible project popovers). Anything outside that set
    # has no clickable trigger on this render, so emitting it would just
    # bloat the markdown payload without unlocking new UX.
    for r in (_inv_rows_all if _build_popovers_flag else []):
        _app = r["application"]
        if _app not in _visible_apps_reach:
            continue
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

        # Outdated-image hints next to the build / deploy image-tag rows.
        # Pulls current + recommended versions from ef-devops-projects;
        # surfaces a small amber tag when they diverge plus a separate
        # "Recommended" row so the upgrade target is obvious.
        _ap_b_old, _ap_d_old, _ap_ver_info = _iv_outdated_flags(_app)
        _build_tag_chip = (
            f'<span class="ap-outdated-chip" '
            f'title="Recommended: {html.escape(_ap_ver_info["build_recommended"])}">'
            f'⬆ outdated</span>'
            if _ap_b_old else ""
        )
        _deploy_tag_chip = (
            f'<span class="ap-outdated-chip" '
            f'title="Recommended: {html.escape(_ap_ver_info["deploy_recommended"])}">'
            f'⬆ outdated</span>'
            if _ap_d_old else ""
        )
        _build_recommend_row = (
            f'    <span class="ap-k">Recommended</span>'
            f'<span class="ap-v"><span class="ap-chip ap-chip--rec">'
            f'{html.escape(_ap_ver_info["build_recommended"])}</span></span>'
            if _ap_b_old else ""
        )
        _deploy_recommend_row = (
            f'    <span class="ap-k">Recommended</span>'
            f'<span class="ap-v"><span class="ap-chip ap-chip--rec">'
            f'{html.escape(_ap_ver_info["deploy_recommended"])}</span></span>'
            if _ap_d_old else ""
        )

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
            f'    <span class="ap-k">Image tag</span>'
            f'<span class="ap-v">'
            f'{html.escape(r.get("build_image_tag", "") or "—")}'
            f'{_build_tag_chip}'
            f'</span>'
            f'{_build_recommend_row}'
            f'    <div class="ap-section">Deploy</div>'
            f'    <span class="ap-k">Technology</span>{_iv_chip(r.get("deploy_technology", ""))}'
            f'    <span class="ap-k">Platform</span>{_iv_chip(r.get("deploy_platform", ""))}'
            f'    <span class="ap-k">Image name</span>{_iv_v(r.get("deploy_image_name", ""))}'
            f'    <span class="ap-k">Image tag</span>'
            f'<span class="ap-v">'
            f'{html.escape(r.get("deploy_image_tag", "") or "—")}'
            f'{_deploy_tag_chip}'
            f'</span>'
            f'{_deploy_recommend_row}'
            f'    {_prisma_html}'
            f'  </div>'
            f'  <div class="ap-foot">Sources: ef-devops-inventory · ef-devops-projects · ef-cicd-deployments · ef-cicd-prismacloud · ef-cicd-invicti · ef-cicd-zap</div>'
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
        # Version popovers are triggered ONLY by version chips in the
        # paginated rows — apps reached purely via project popovers don't
        # expose stage chips, so skip them. (`_iv_apps` is the full scope set;
        # `_visible_page_apps` is the page slice — version popovers belong to
        # the latter only.)
        if _app not in _visible_page_apps:
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

            # ── Stage-detail block (version + date + status + URLs) ─────────
            _stage_block = (
                f'    <div class="ap-section">{_stage_lbl}</div>'
                f'    <span class="ap-k">Version</span>{_iv_chip(_ver)}'
                f'    <span class="ap-k">Status</span>{_iv_v(_status)}'
                f'    <span class="ap-k">When ({DISPLAY_TZ_LABEL})</span>{_iv_v(_when_disp)}'
            )

            # Application URLs — only meaningful for `dev` / `qc` stages.
            # ef-devops-projects carries `qcRouteUrl` / `qcServiceUrl` only;
            # the dev variants are NOT populated in the index, so derive the
            # dev URL from the qc value by swapping the literal "qc" → "dev"
            # everywhere it appears (matches the host-naming convention).
            # UAT / PRD don't expose URLs in this index, so we skip them.
            if _stage in ("dev", "qc"):
                _dp_proj = _iv_devproj_map.get(_app) or {}
                _qc_route_url   = (_dp_proj.get("qcRouteUrl")   or "").strip()
                _qc_service_url = (_dp_proj.get("qcServiceUrl") or "").strip()
                if _stage == "qc":
                    _route_url   = _qc_route_url
                    _service_url = _qc_service_url
                else:
                    _route_url   = _qc_route_url.replace("qc", "dev")
                    _service_url = _qc_service_url.replace("qc", "dev")

                def _iv_url_row(label: str, url: str) -> str:
                    if not url:
                        return ""
                    _href = html.escape(url, quote=True)
                    _short = url if len(url) <= 56 else url[:53] + "…"
                    return (
                        f'    <span class="ap-k">{label}</span>'
                        f'<span class="ap-v">'
                        f'<a class="ap-url" href="{_href}" target="_blank" '
                        f'rel="noopener noreferrer" title="{_href}">'
                        f'↗ {html.escape(_short)}</a>'
                        f'</span>'
                    )
                _stage_block += _iv_url_row("Route URL", _route_url)
                _stage_block += _iv_url_row("Service URL", _service_url)

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

    # Project popovers — only for projects reachable on this render: rows on
    # the current page + the up-to-24 chips in the project ribbon.
    for _proj in (_iv_pop_projects if _build_popovers_flag else []):
        if _proj not in _visible_projects_reach:
            continue
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

        # Remedy product info — aggregated across the project's apps. The
        # ef-devops-projects index carries one row per app; for shared
        # Remedy tiers we collapse identical values to a single chip so
        # multi-product projects surface every distinct tier.
        def _remedy_collect(field: str) -> list[str]:
            _vals: list[str] = []
            for _a in _apps_p:
                _v = ((_iv_devproj_map.get(_a) or {}).get(field) or "").strip()
                if _v and _v not in _vals:
                    _vals.append(_v)
            return _vals

        _rem_name  = _remedy_collect("RemedyProductName")
        _rem_tier1 = _remedy_collect("RemedyProductTier1")
        _rem_tier2 = _remedy_collect("RemedyProductTier2")
        _rem_tier3 = _remedy_collect("RemedyProductTier3")

        def _remedy_row(label: str, vals: list[str]) -> str:
            if not vals:
                return ""
            _chips = "".join(
                f'<span class="ap-chip">{html.escape(_v)}</span>'
                for _v in vals
            )
            return (
                f'<span class="ap-k">{label}</span>'
                f'<span class="ap-v" style="display:flex;flex-wrap:wrap;gap:4px">'
                f'{_chips}</span>'
            )

        _remedy_block_p = ""
        if any([_rem_name, _rem_tier1, _rem_tier2, _rem_tier3]):
            _remedy_rows = (
                _remedy_row("Product", _rem_name)
                + _remedy_row("Tier 1", _rem_tier1)
                + _remedy_row("Tier 2", _rem_tier2)
                + _remedy_row("Tier 3", _rem_tier3)
            )
            _remedy_block_p = (
                '    <div class="ap-section">Remedy</div>'
                + _remedy_rows
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
            + "".join(_team_rows_p)
            + _remedy_block_p +
            f'    <div class="ap-section">Applications <span style="text-transform:none;font-weight:600;color:var(--cc-text-mute);letter-spacing:0;margin-left:4px">· {len(_apps_p)}</span></div>'
            f'    <div class="ap-applist">{_apps_block_p}</div>'
            f'  </div>'
            f'  <div class="ap-foot">Sources: ef-devops-inventory · ef-devops-projects · click an app for full details</div>'
            f'</div>'
        )

    # ── Finalize popover cache ──────────────────────────────────────────────
    if _build_popovers_flag:
        _iv_popovers_html = "".join(_iv_popovers)
        # Drop entries from older TTL buckets first (stale data), then trim
        # to a bounded LRU so flipping between recent pages / filter sets
        # is instant. Each entry is now a page-sized blob (≈1-3 MB worst
        # case) instead of the full-scope multi-MB blob.
        for _stale_key in [
            _k for _k in _iv_pop_store
            if isinstance(_k, tuple) and len(_k) >= 2 and _k[1] != _iv_pop_bucket
        ]:
            _iv_pop_store.pop(_stale_key, None)
        _IV_POP_LRU = 6
        while len(_iv_pop_store) >= _IV_POP_LRU:
            try:
                _iv_pop_store.pop(next(iter(_iv_pop_store)))
            except StopIteration:
                break
        _iv_pop_store[_iv_pop_cache_key] = _iv_popovers_html
    else:
        _iv_popovers_html = _iv_cached_pop_html
        # Touch: move this key to the end so LRU eviction favours stale ones.
        _iv_pop_store.pop(_iv_pop_cache_key, None)
        _iv_pop_store[_iv_pop_cache_key] = _iv_popovers_html

    # ── Source telemetry — admin-only, calibrated to severity ──────────────
    # Non-admins never need to know the dashboard talks to git OR ES; this
    # whole block stays hidden for them. For admins we render at three
    # severity levels so signal/noise stays right:
    #
    #   1. Git source, no warnings              → quiet teal pill (existing).
    #   2. Git source WITH parse warnings       → quiet pill + amber detail
    #      strip listing each warning so misconfigured field aliases /
    #      vault passwords surface immediately rather than producing thin
    #      rows that look correct.
    #   3. ES fallback                          → loud full-width banner +
    #      the failing reason + every warning + remediation hints. This is
    #      the case the user explicitly asked to be unmissable: the page
    #      is operating off the projection, not the authoritative source,
    #      and admins need to know AT A GLANCE.
    if _is_admin:
        _src = _iv_source
        _stat = _iv_source_status or ""
        _warns = list(_iv_source_warnings)

        # ── Source-selector radio — sticky in session_state, drives
        # _inventory_load on the NEXT rerun. The current resolution
        # (git/es/git-forced-failed) drives the pill / banner below.
        st.session_state.setdefault(_INV_SRC_PREF_KEY, "auto")
        _SRC_LABELS = {
            "auto": "Auto (git, fall back to ES)",
            "git":  "Git only (no fallback)",
            "es":   "Elasticsearch only",
        }
        with st.container(key="cc_inv_src_pref"):
            _c1, _c2 = st.columns([1, 6])
            with _c1:
                st.markdown(
                    '<div class="iv-src-pref-lbl">Inventory source</div>',
                    unsafe_allow_html=True,
                )
            with _c2:
                st.radio(
                    "Inventory source",
                    options=("auto", "git", "es"),
                    format_func=lambda v: _SRC_LABELS.get(v, v),
                    horizontal=True,
                    key=_INV_SRC_PREF_KEY,
                    label_visibility="collapsed",
                    help=(
                        "Auto prefers git, falls back to Elasticsearch on any "
                        "failure. Git-only refuses to fall back so a failure "
                        "is visible. Elasticsearch-only bypasses git entirely."
                    ),
                )

        if _src == "git-forced-failed":
            # Admin explicitly forced git but git failed. Don't fall back
            # silently — show a loud banner with the failure reason. The
            # inventory table will be empty until git recovers OR the admin
            # switches back to auto.
            _reason = _stat or "git source returned no rows"
            _warn_items_f = "".join(
                f'<li>{html.escape(w)}</li>' for w in _warns[:20]
            )
            _warn_overflow_f = (
                f'<li class="iv-src-detail-overflow">'
                f'… +{len(_warns) - 20} more (truncated)</li>'
                if len(_warns) > 20 else ""
            )
            _warn_block_f = (
                f'<div class="iv-src-alarm-warns">'
                f'  <div class="iv-src-alarm-warns-head">'
                f'    Loader emitted {len(_warns)} warning'
                f'{"s" if len(_warns) != 1 else ""}:'
                f'  </div>'
                f'  <ul>{_warn_items_f}{_warn_overflow_f}</ul>'
                f'</div>'
                if _warns else ""
            )
            st.markdown(
                f'<div class="iv-src-alarm" role="alert">'
                f'  <div class="iv-src-alarm-stripe"></div>'
                f'  <div class="iv-src-alarm-body">'
                f'    <div class="iv-src-alarm-head">'
                f'      <span class="iv-src-alarm-glyph">⚠</span>'
                f'      <span class="iv-src-alarm-tag">GIT FORCED · FAILED</span>'
                f'      <span class="iv-src-alarm-title">'
                f'        Inventory is empty — git source unavailable and '
                f'        "Git only" is selected (no fallback).'
                f'      </span>'
                f'    </div>'
                f'    <div class="iv-src-alarm-reason">'
                f'      <span class="iv-src-alarm-reason-k">Reason:</span>'
                f'      <code>{html.escape(_reason)}</code>'
                f'    </div>'
                f'    <div class="iv-src-alarm-hint">'
                f'      Switch the source selector above to <b>Auto</b> to '
                f'      fall back to Elasticsearch, or fix the git path. '
                f'      The Integrations strip shows what failed.'
                f'    </div>'
                f'    {_warn_block_f}'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # ── Diagnostic affordance ──────────────────────────────────────
            _render_git_diag_controls()
        elif _src == "git":
            # Quiet success pill stays.
            st.markdown(
                f'<div class="iv-src-row">'
                f'  <span class="iv-src is-git">'
                f'    <span class="iv-src-dot"></span>'
                f'    <span class="iv-src-glyph">✦</span>'
                f'    <span class="iv-src-lbl">Git source</span>'
                f'    <span class="iv-src-stat">{html.escape(_stat)}</span>'
                f'  </span>'
                + (
                    f'<span class="iv-src-warn" title="{html.escape(_warns[0])}">'
                    f'⚠ {len(_warns)} parse warning'
                    f'{"s" if len(_warns) != 1 else ""}</span>'
                    if _warns else ""
                )
                + '</div>',
                unsafe_allow_html=True,
            )
            # When git works but emitted warnings, surface every one so
            # admins can fix the underlying YAML / vault issue.
            if _warns:
                _detail_items = "".join(
                    f'<li>{html.escape(w)}</li>' for w in _warns[:20]
                )
                _overflow = (
                    f'<li class="iv-src-detail-overflow">'
                    f'… +{len(_warns) - 20} more (truncated)</li>'
                    if len(_warns) > 20 else ""
                )
                st.markdown(
                    f'<details class="iv-src-detail" open>'
                    f'  <summary>'
                    f'    <span class="iv-src-detail-glyph">⚠</span>'
                    f'    Git checkout parsed with {len(_warns)} warning'
                    f'{"s" if len(_warns) != 1 else ""} — '
                    f'    <em>some rows may be missing fields</em>'
                    f'  </summary>'
                    f'  <ul class="iv-src-detail-list">'
                    f'    {_detail_items}{_overflow}'
                    f'  </ul>'
                    f'</details>',
                    unsafe_allow_html=True,
                )
        else:
            # ES fallback — the loud case. Compute a remediation hint based
            # on the failure category so admins know exactly what to fix.
            _reason = _stat or "unknown reason"
            _r_lower = _reason.lower()
            if "host not resolved" in _r_lower or "vault" in _r_lower:
                _hint = (
                    "The ADO hostname couldn't be read from vault path "
                    f"<code>{html.escape(GIT_VAULT_PATH)}</code>. Verify "
                    "the entry exists with the nested keys "
                    "<code>ado.hostname</code>, <code>ado.username</code>, "
                    "<code>ado.password</code>, and that the dashboard's "
                    "vault token is still valid (see the Integrations "
                    "strip for the exact vault error)."
                )
            elif "credentials" in _r_lower or "missing credentials" in _r_lower:
                _hint = (
                    "Vault returned a partial entry — hostname is present "
                    "but <code>ado.username</code> / <code>ado.password</code> "
                    "are missing. Re-issue the credential entry under "
                    f"<code>{html.escape(GIT_VAULT_PATH)}</code>."
                )
            elif "git executable" in _r_lower:
                _hint = (
                    "Install <code>git</code> on the streamlit host — "
                    "the repo clone helper shells out to the standard binary."
                )
            elif "pyyaml" in _r_lower:
                _hint = (
                    "Install PyYAML on the streamlit host "
                    "(<code>pip install pyyaml</code>) — the loader needs it "
                    "to parse Ansible inventory files."
                )
            elif "git clone failed" in _r_lower or "git fetch failed" in _r_lower:
                _hint = (
                    "Network / auth path to the inventories repo is broken. "
                    "Check that the ADO host from vault is reachable from this "
                    "pod, the vault-stored credentials are still valid, and "
                    "the <code>main</code> branch exists."
                )
            elif "0 apps" in _r_lower:
                _hint = (
                    "The git repo cloned but produced no rows — most likely "
                    "the field-alias table doesn't match your YAML keys. "
                    "Adjust <code>_INV_FIELD_ALIASES</code> in code, or "
                    "share a sample <code>.yml</code> so the mapping can be "
                    "tuned."
                )
            elif "timed out" in _r_lower:
                _hint = (
                    "Repo fetch exceeded its 120-second budget. The repo may "
                    "have grown too large for a shallow clone, or the network "
                    "path is slow."
                )
            else:
                _hint = (
                    "Page is operating off the Elasticsearch projection "
                    "rather than the authoritative git source. Inventory "
                    "writes won't be possible until git is restored."
                )

            _warn_items = "".join(
                f'<li>{html.escape(w)}</li>' for w in _warns[:20]
            )
            _warn_overflow = (
                f'<li class="iv-src-detail-overflow">'
                f'… +{len(_warns) - 20} more (truncated)</li>'
                if len(_warns) > 20 else ""
            )
            _warn_block = (
                f'<div class="iv-src-alarm-warns">'
                f'  <div class="iv-src-alarm-warns-head">'
                f'    Loader emitted {len(_warns)} additional warning'
                f'{"s" if len(_warns) != 1 else ""}:'
                f'  </div>'
                f'  <ul>{_warn_items}{_warn_overflow}</ul>'
                f'</div>'
                if _warns else ""
            )

            st.markdown(
                f'<div class="iv-src-alarm" role="alert">'
                f'  <div class="iv-src-alarm-stripe"></div>'
                f'  <div class="iv-src-alarm-body">'
                f'    <div class="iv-src-alarm-head">'
                f'      <span class="iv-src-alarm-glyph">⚠</span>'
                f'      <span class="iv-src-alarm-tag">FALLBACK</span>'
                f'      <span class="iv-src-alarm-title">'
                f'        Inventory loaded from Elasticsearch — git source unavailable'
                f'      </span>'
                f'    </div>'
                f'    <div class="iv-src-alarm-reason">'
                f'      <span class="iv-src-alarm-reason-k">Reason:</span>'
                f'      <code>{html.escape(_reason)}</code>'
                f'    </div>'
                f'    <div class="iv-src-alarm-hint">{_hint}</div>'
                f'    {_warn_block}'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            # ── Diagnostic affordance ──────────────────────────────────────
            _render_git_diag_controls()

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

        # ── Integrations health strip — admin-only ─────────────────────────
        # Compact chip row showing the state of every external integration
        # the dashboard talks to (ES, git inventories, vault, Jenkins, S3,
        # optional deps). Subtle by default — a one-line strip with colored
        # dots; click the strip to expand a per-integration detail card so
        # admins can dig in when something's off without the row screaming.
        if _is_admin:
            _render_integrations_strip()

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
        # Jenkins panel — admin-only for now. Other roles eventually get a
        # role-scoped variant (developer triggers Build, operations triggers
        # Deploy / Release request) but the read-only status surface is
        # intentionally gated to admins until that role-scoping lands.
        _jk_show = _is_admin
        _jk_configured = bool(_jenkins_creds().get("host"))
        _jk_badge_txt = "  ·  ⏵" if (_jk_show and _jk_configured) else ""
        # Scan Viewer — admin-only too. Doesn't add a badge until configured.
        _psv_show = _is_admin
        _psv_configured = bool(PRISMA_S3_BUCKET) and _BOTO3_AVAILABLE
        _psv_badge_txt = "  ·  S3" if (_psv_show and _psv_configured) else ""
        # Sync check — admin-only. Badge surfaces the last-known drift
        # across BOTH internal panels (git↔ES + inventory↔Postgres).
        _sync_show = _is_admin
        _sync_summary = st.session_state.get("_sync_summary_v1") or {}
        _pg_summary = st.session_state.get("_pg_check_summary_v1") or {}
        _sync_badge_txt = ""
        if _sync_show and (_sync_summary or _pg_summary):
            _t_total = (
                int(_sync_summary.get("total") or 0)
                + int(_pg_summary.get("total") or 0)
            )
            _sync_badge_txt = (
                "  ·  ✓ clean" if _t_total == 0 else f"  ·  ⚠ {_t_total} drift"
            )
        _tab_labels = [
            f"❖  PIPELINES INVENTORY{_iv_badge_txt}",
            f"⧗  EVENT LOG{_el_badge_txt}",
        ]
        if _jk_show:
            _tab_labels.append(f"⚙  JENKINS{_jk_badge_txt}")
        if _psv_show:
            _tab_labels.append(f"🔬  SCAN VIEWER{_psv_badge_txt}")
        if _sync_show:
            _tab_labels.append(f"🔀  SYNC CHECK{_sync_badge_txt}")
        with st.container(key="cc_surface_tabs"):
            _tabs = st.tabs(_tab_labels)
            _tab_inv, _tab_log = _tabs[0], _tabs[1]
            _next_idx = 2
            _tab_jenkins = _tabs[_next_idx] if _jk_show else None
            if _jk_show:
                _next_idx += 1
            _tab_psv = _tabs[_next_idx] if _psv_show else None
            if _psv_show:
                _next_idx += 1
            _tab_sync = _tabs[_next_idx] if _sync_show else None
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
            if _tab_jenkins is not None:
                with _tab_jenkins:
                    st.markdown(
                        '<div class="cc-panel-sub" style="margin:0 0 6px 0">'
                        'Jenkins pipelines · build / deploy request / release '
                        'request · live status of in-flight runs · click ▶ to '
                        'load — refreshes every 30s once active'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    _jk_slot = st.empty()
            else:
                _jk_slot = None
            if _tab_psv is not None:
                with _tab_psv:
                    st.markdown(
                        '<div class="cc-panel-sub" style="margin:0 0 6px 0">'
                        'Prisma scan viewer · pick an app + version · '
                        'click ▶ Load to fetch the full HTML report from S3 — '
                        'never auto-loads, never lists the bucket'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    _psv_slot = st.empty()
            else:
                _psv_slot = None
            if _tab_sync is not None:
                with _tab_sync:
                    st.markdown(
                        '<div class="cc-panel-sub" style="margin:0 0 6px 0">'
                        'Compare the git inventory against the Elasticsearch '
                        'projection · click ▶ Run to run a full diff for the '
                        'current scope · results stay until cleared or re-run'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    _sync_slot = st.empty()
            else:
                _sync_slot = None

        # Run the inventory fragment first — it emits into slots A + B and
        # publishes the scope keys the event log needs.
        _render_inventory_view(_iv_controls_slot, _iv_body_slot)

        # Now the event log reads a fresh scope and fills slot C.
        with _el_slot.container():
            _render_event_log()

        # Jenkins panel — smart-loaded: nothing fetches until the user
        # clicks "Load". After first load the panel runs as a fragment
        # with run_every="30s" so it keeps itself fresh independently.
        if _jk_slot is not None:
            with _jk_slot.container():
                _render_jenkins_panel()

        # Prisma scan viewer — picker pulls options from the inventory
        # fragment's published session state, so this runs LAST so the
        # picker is already populated.
        if _psv_slot is not None:
            with _psv_slot.container():
                _render_prisma_scan_viewer()

        # Sync check — smart-loaded; uses the scope key the inventory
        # fragment publishes so its diff matches the visible scope.
        # Houses two independent panels: git↔ES first, then inventory↔Postgres.
        if _sync_slot is not None:
            with _sync_slot.container():
                _scope_key_for_check = st.session_state.get("_iv_scope_key_v1", "")
                st.markdown(
                    '<div class="sync-section-divider">'
                    '  <span class="sync-section-divider-glyph">⎇</span>'
                    '  Git ↔ Elasticsearch'
                    '</div>',
                    unsafe_allow_html=True,
                )
                _render_sync_check_panel(_scope_key_for_check)
                st.markdown(
                    '<div class="sync-section-divider">'
                    '  <span class="sync-section-divider-glyph">🗂</span>'
                    '  Inventory ↔ Postgres devops_projects'
                    '</div>',
                    unsafe_allow_html=True,
                )
                _render_postgres_compare_panel(_scope_key_for_check)
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
