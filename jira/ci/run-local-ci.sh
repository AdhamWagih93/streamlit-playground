#!/usr/bin/env bash
#
# Trackly local CI — runs entirely on your machine (no GitHub involved).
# Runs backend tests, the frontend build, and a docker-compose validation,
# then writes a consolidated report (machine JSON + visual Markdown + HTML) and
# prints it. Exit code is non-zero if any check fails, so the pre-push hook can
# block a bad push.
#
# Usage:  jira/ci/run-local-ci.sh [--skip-frontend] [--skip-docker] [--quiet]
#
# Requirements: Docker (everything runs in throwaway containers, so no local
# Python/Node toolchain is needed). A small pip/npm cache volume is reused
# between runs to keep repeat runs fast.
set -uo pipefail

# --- locate paths ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JIRA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPORT_DIR="$JIRA_DIR/ci-report"
ART_DIR="$REPORT_DIR/artifacts"

# Load local CI secrets (e.g. DISCORD_WEBHOOK_URL) if present — gitignored,
# never committed. See jira/ci/secrets.env.
# shellcheck disable=SC1091
[ -f "$SCRIPT_DIR/secrets.env" ] && . "$SCRIPT_DIR/secrets.env"

SKIP_FRONTEND=0
SKIP_DOCKER=0
QUIET=0
SCREENSHOTS=${CI_SCREENSHOTS:-0}
APP_SHOTS=${CI_APP_SCREENSHOTS:-0}
for arg in "$@"; do
  case "$arg" in
    --skip-frontend)   SKIP_FRONTEND=1 ;;
    --skip-docker)     SKIP_DOCKER=1 ;;
    --quiet)           QUIET=1 ;;
    --screenshots)     SCREENSHOTS=1 ;;
    --app-screenshots) SCREENSHOTS=1; APP_SHOTS=1 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
info() { [ "$QUIET" -eq 1 ] || printf '  %s\n' "$1"; }

if ! docker info >/dev/null 2>&1; then
  echo "✗ Docker is not available — local CI needs Docker." >&2
  exit 2
fi

# Some report files are written by containers running as root, so a plain `rm`
# by the host user can hit "Permission denied". Clean via a throwaway root
# container so every run starts from a truly clean slate.
clean_paths() {
  docker run --rm -v "$JIRA_DIR":/w busybox rm -rf "$@" >/dev/null 2>&1 || true
}
clean_paths /w/ci-report /w/backend/reports /w/frontend/reports
rm -rf "$REPORT_DIR" 2>/dev/null || true
mkdir -p "$ART_DIR/backend" "$ART_DIR/frontend" "$ART_DIR/docker"

UID_TAG="$$"
NET="trackly-localci-$UID_TAG"
PG="trackly-localci-pg-$UID_TAG"

cleanup() {
  docker rm -f "$PG" >/dev/null 2>&1 || true
  docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

bold "▶ Trackly local CI"

# --- 1. Backend: pytest against a throwaway Postgres -----------------------
bold "▶ backend · tests"
docker network create "$NET" >/dev/null 2>&1 || true
docker run -d --rm --name "$PG" --network "$NET" \
  -e POSTGRES_DB=trackly -e POSTGRES_USER=trackly -e POSTGRES_PASSWORD=trackly \
  postgres:16-alpine >/dev/null
info "waiting for postgres…"
for _ in $(seq 1 30); do
  if docker exec "$PG" pg_isready -U trackly >/dev/null 2>&1; then break; fi
  sleep 1
done

clean_paths /w/backend/reports; mkdir -p "$JIRA_DIR/backend/reports"
docker run --rm --network "$NET" \
  -v "$JIRA_DIR/backend":/app -w /app \
  -v trackly-ci-pip:/root/.cache/pip \
  -e POSTGRES_HOST="$PG" -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=trackly -e POSTGRES_USER=trackly -e POSTGRES_PASSWORD=trackly \
  -e SECRET_KEY=local-ci-secret-0123456789abcdef0123456789 \
  -e APP_ENV=test -e DEBUG=false -e PYTHONDONTWRITEBYTECODE=1 \
  python:3.12-slim bash -lc '
    set -e
    pip install -q -r requirements-dev.txt
    python -m compileall -q app
    mkdir -p reports
    pytest -q \
      --json-report --json-report-file=reports/pytest.json \
      --html=reports/pytest.html --self-contained-html \
      --cov=app --cov-report=json:reports/coverage.json
  ' 2>&1 | { [ "$QUIET" -eq 1 ] && grep -E "passed|failed|error" || cat; }

python3 "$SCRIPT_DIR/report.py" backend \
  --pytest "$JIRA_DIR/backend/reports/pytest.json" \
  --coverage "$JIRA_DIR/backend/reports/coverage.json" \
  --out-dir "$JIRA_DIR/backend/reports" >/dev/null 2>&1 || true
cp "$JIRA_DIR/backend/reports/result.json" "$ART_DIR/backend/result.json" 2>/dev/null || \
  echo '{"job":"backend","title":"Backend · tests","status":"failed","headline":"runner error","metrics":{},"tables":[]}' > "$ART_DIR/backend/result.json"
cp "$JIRA_DIR/backend/reports/pytest.html" "$REPORT_DIR/pytest.html" 2>/dev/null || true

# --- 2. Frontend: typecheck + build ----------------------------------------
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  bold "▶ frontend · build"
  clean_paths /w/frontend/reports; mkdir -p "$JIRA_DIR/frontend/reports"
  docker run --rm \
    -v "$JIRA_DIR/frontend":/app -w /app \
    -v trackly-ci-npm:/root/.npm \
    node:20-alpine sh -lc '
      set -e
      npm ci --no-audit --no-fund 2>/dev/null || npm install --no-audit --no-fund
      npm run build
    ' 2>&1 | { [ "$QUIET" -eq 1 ] && grep -E "built in|error|Error" || cat; }
  python3 "$SCRIPT_DIR/report.py" frontend \
    --dist "$JIRA_DIR/frontend/dist" --out-dir "$JIRA_DIR/frontend/reports" >/dev/null 2>&1 || true
  cp "$JIRA_DIR/frontend/reports/result.json" "$ART_DIR/frontend/result.json" 2>/dev/null || \
    echo '{"job":"frontend","title":"Frontend · build","status":"failed","headline":"runner error","metrics":{},"tables":[]}' > "$ART_DIR/frontend/result.json"
fi

# --- 3. Docker: compose validation -----------------------------------------
if [ "$SKIP_DOCKER" -eq 0 ]; then
  bold "▶ docker · compose"
  if (cd "$JIRA_DIR" && docker compose config >/dev/null 2>&1); then
    python3 "$SCRIPT_DIR/report.py" docker --status passed \
      --note "compose configuration valid" --out-dir "$ART_DIR/docker" >/dev/null 2>&1 || true
  else
    python3 "$SCRIPT_DIR/report.py" docker --status failed \
      --note "docker compose config failed" --out-dir "$ART_DIR/docker" >/dev/null 2>&1 || true
  fi
fi

# --- 4. Consolidate + present ----------------------------------------------
python3 "$SCRIPT_DIR/report.py" consolidate --in "$ART_DIR" --out-dir "$REPORT_DIR" >/dev/null 2>&1
RESULT=$?

echo
if command -v cat >/dev/null; then cat "$REPORT_DIR/report.md"; fi
echo
bold "Reports written to: $REPORT_DIR"
info "• report.md   (visual)   • report.json (machine)   • pytest.html (open in a browser)"

# Optional desktop notification.
if command -v notify-send >/dev/null 2>&1; then
  STATUS=$(python3 -c "import json;print(json.load(open('$REPORT_DIR/report.json'))['overall_status'])" 2>/dev/null || echo unknown)
  notify-send "Trackly local CI: $STATUS" "See $REPORT_DIR/report.md" 2>/dev/null || true
fi

# Optional screenshots (headless Chromium via Playwright container). Opt-in so
# normal pushes stay fast: --screenshots (report PNG) / --app-screenshots (+app).
if [ "$SCREENSHOTS" -eq 1 ]; then
  bold "▶ screenshots"
  if [ "$APP_SHOTS" -eq 1 ]; then
    # Isolated, seeded stack — never touches the real database.
    bash "$SCRIPT_DIR/capture-screenshots.sh" --ephemeral || true
  else
    bash "$SCRIPT_DIR/capture-screenshots.sh" || true
  fi
fi

# Optional Discord notification — posts an embed (+ report.md and any
# screenshots/PNGs, else pytest.html) when DISCORD_WEBHOOK_URL is set. Never
# fails the run.
if [ -n "${DISCORD_WEBHOOK_URL:-}" ]; then
  python3 "$SCRIPT_DIR/report.py" discord --report-dir "$REPORT_DIR" --attach || true
fi

exit $RESULT
