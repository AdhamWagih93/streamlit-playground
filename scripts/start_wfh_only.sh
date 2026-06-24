#!/usr/bin/env bash
# Start ONLY the WFH Schedule page (standalone), not the whole multipage app.
#
# Usage:
#   ./scripts/start_wfh_only.sh [PORT]      # default port 8502
#
# Env:
#   WFH_PORT=8600   alternative way to set the port (arg wins)
#   KILL_PYTHON=1   stop any running streamlit first
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$SCRIPT_DIR/.."
APP_DIR="$REPO_ROOT/best-streamlit-website"
PAGE="pages/3_WFH_Schedule.py"
PORT="${1:-${WFH_PORT:-8502}}"

# The page reads ./data relative to the working directory, so run from the app dir.
cd "$APP_DIR"

if [ "$KILL_PYTHON" = "1" ]; then
  pkill -f "streamlit" || true
fi

if [ ! -f "$PAGE" ]; then
  echo "error: $PAGE not found under $APP_DIR" >&2
  exit 1
fi

# Use the repo's virtualenv (matching scripts/start_streamlit.sh); create on first run.
if [ ! -d "$REPO_ROOT/.venv" ]; then
  echo ".venv not found. Creating..."
  python3 -m venv "$REPO_ROOT/.venv"
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
  pip install --upgrade pip
  pip install -r "$APP_DIR/requirements.txt"
else
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.venv/bin/activate"
fi

echo "Starting WFH Schedule page on http://localhost:${PORT}  (Ctrl-C to stop)…"
exec python -m streamlit run "$PAGE" \
  --server.port "$PORT" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
