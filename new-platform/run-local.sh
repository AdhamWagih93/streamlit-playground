#!/usr/bin/env bash
# Local development: demo data, no auth, hot reload on both sides.
# Starts a local Postgres 17 container (meridian-pg) for the encrypted
# integration-settings store; without Docker the app still runs, holding
# settings in memory (the UI shows a warning).
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

DB_URL=""
if command -v docker >/dev/null 2>&1; then
  if ! docker ps --format '{{.Names}}' | grep -qx meridian-pg; then
    echo "▸ starting local Postgres 17 (container meridian-pg, port 5433)"
    docker start meridian-pg >/dev/null 2>&1 || docker run -d --name meridian-pg \
      --restart unless-stopped -p 5433:5432 \
      -e POSTGRES_USER=meridian -e POSTGRES_PASSWORD=meridian-dev -e POSTGRES_DB=meridian \
      -v meridian-pgdata:/var/lib/postgresql/data postgres:17 >/dev/null
  fi
  DB_URL="postgresql://meridian:meridian-dev@127.0.0.1:5433/meridian"
else
  echo "▸ docker not found — settings will be held in memory only"
fi

trap 'kill 0' EXIT
echo "▸ backend  → http://127.0.0.1:8000  (demo data, no auth)"
(cd backend && DATA_MODE=demo AUTH_MODE=none DATABASE_URL="$DB_URL" \
  .venv/bin/uvicorn app.main:app --reload --port 8000) &
echo "▸ frontend → http://localhost:5173"
(cd frontend && npm run dev) &
wait
