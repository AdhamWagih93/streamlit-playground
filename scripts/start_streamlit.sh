#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$SCRIPT_DIR/.."
cd "$REPO_ROOT"

# Kill existing python processes optionally via env
if [ "$KILL_PYTHON" = "1" ]; then
  pkill -f "streamlit" || true
fi

if [ ! -d .venv ]; then
  echo ".venv not found. Creating..."
  python3 -m venv .venv
  source .venv/bin/activate
  pip install --upgrade pip
  pip install -r best-streamlit-website/requirements.txt
else
  source .venv/bin/activate
fi

python -m streamlit run best-streamlit-website/app.py --server.headless true
