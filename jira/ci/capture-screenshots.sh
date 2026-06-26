#!/usr/bin/env bash
#
# Capture CI screenshots with headless Chromium (Playwright in a container — no
# local browser install needed). Always renders the CI report to a PNG. App
# screenshots can target either:
#
#   --ephemeral   bring up an ISOLATED, seeded Trackly stack (its own project +
#                 RAM-backed DB on port 8099), screenshot every page, tear it
#                 down. Never touches your real database. (Recommended for CI.)
#   --with-app    screenshot an already-running instance at $TRACKLY_URL
#                 (default http://localhost:8080) — read-only, uses real data.
#
# App-mode config via env (defaults shown):
#   TRACKLY_URL=http://localhost:8080  TRACKLY_EMAIL=admin@trackly.local
#   TRACKLY_PASSWORD=admin             TRACKLY_PROJECT=AN
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORT_DIR="$JIRA_DIR/ci-report"
SHOTS="$REPORT_DIR/screenshots"
IMG="mcr.microsoft.com/playwright/python:v1.48.0-jammy"
PW_PKG="playwright==1.48.0"
PIP_CACHE="-v trackly-ci-pw-pip:/root/.cache/pip"

MODE="report"
[ "${1:-}" = "--with-app" ] && MODE="app"
[ "${1:-}" = "--ephemeral" ] && MODE="ephemeral"

if ! docker info >/dev/null 2>&1; then echo "✗ Docker required for screenshots." >&2; exit 2; fi
# Start clean so stale shots don't linger. PNGs are written by a root-running
# container, so clean via a root container to avoid "Permission denied".
docker run --rm -v "$JIRA_DIR":/w busybox rm -rf /w/ci-report/screenshots >/dev/null 2>&1 || true
rm -rf "$SHOTS" 2>/dev/null || true
mkdir -p "$SHOTS"

# --- 1. Always: render the CI report HTML -> PNG (no app needed) ------------
if [ -f "$REPORT_DIR/report.html" ]; then
  docker run --rm $PIP_CACHE -v "$JIRA_DIR":/work -w /work "$IMG" \
    bash -lc "pip install -q $PW_PKG && python ci/screenshots.py report \
      --html /work/ci-report/report.html --out /work/ci-report/screenshots/00-report.png" \
    || echo "report screenshot failed (non-fatal)"
fi

capture_app() {  # $1 = url  $2 = project
  docker run --rm --network host $PIP_CACHE -v "$JIRA_DIR":/work -w /work "$IMG" \
    bash -lc "pip install -q $PW_PKG && python ci/screenshots.py app \
      --url '$1' --email '${TRACKLY_EMAIL:-admin@trackly.local}' \
      --password '${TRACKLY_PASSWORD:-admin}' --project '$2' \
      --out-dir /work/ci-report/screenshots" || echo "app screenshots failed (non-fatal)"
}

# --- 2a. Ephemeral isolated stack ------------------------------------------
if [ "$MODE" = "ephemeral" ]; then
  COMPOSE="docker compose -p trackly-ci -f $SCRIPT_DIR/docker-compose.ci.yml"
  teardown() { echo "▶ tearing down isolated stack"; $COMPOSE down -v >/dev/null 2>&1 || true; }
  trap teardown EXIT

  echo "▶ starting isolated Trackly stack (port 8099, ephemeral DB)…"
  $COMPOSE up -d --build || { echo "stack build/up failed"; exit 1; }

  echo "  waiting for the stack to be ready…"
  ready=0
  for _ in $(seq 1 90); do
    if curl -sf http://localhost:8099/api/health >/dev/null 2>&1; then ready=1; break; fi
    sleep 2
  done
  if [ "$ready" -ne 1 ]; then echo "✗ stack did not become ready"; exit 1; fi

  echo "▶ seeding demo data…"
  python3 "$SCRIPT_DIR/seed_demo.py" --url http://localhost:8099 || echo "seed failed (continuing)"

  echo "▶ capturing every page…"
  capture_app "http://localhost:8099" "DEMO"

# --- 2b. Existing running instance -----------------------------------------
elif [ "$MODE" = "app" ]; then
  capture_app "${TRACKLY_URL:-http://localhost:8080}" "${TRACKLY_PROJECT:-AN}"
fi

echo "Screenshots in: $SHOTS"
ls -1 "$SHOTS" 2>/dev/null || true
