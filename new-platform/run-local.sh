#!/usr/bin/env bash
# Local development: demo data, no auth, hot reload on both sides.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d backend/.venv ]; then
  echo "▸ creating backend venv"
  ~/.local/bin/uv venv backend/.venv -q 2>/dev/null || python3 -m venv backend/.venv
  ~/.local/bin/uv pip install -q -p backend/.venv/bin/python -r backend/requirements.txt 2>/dev/null \
    || backend/.venv/bin/pip install -q -r backend/requirements.txt
fi
if [ ! -d frontend/node_modules ]; then
  echo "▸ installing frontend deps"
  (cd frontend && npm install --no-audit --no-fund)
fi

trap 'kill 0' EXIT
echo "▸ backend  → http://127.0.0.1:8000  (demo data, no auth)"
(cd backend && DATA_MODE=demo AUTH_MODE=none .venv/bin/uvicorn app.main:app --reload --port 8000) &
echo "▸ frontend → http://localhost:5173"
(cd frontend && npm run dev) &
wait
