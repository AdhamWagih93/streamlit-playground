#!/usr/bin/env bash
# QuestOps local dev instance — uvicorn + SQLite in demo mode, no Docker needed.
#
#   ./dev.sh start [--live]   start (demo mode by default; --live reads ./.env)
#   ./dev.sh stop             stop
#   ./dev.sh restart          stop + start
#   ./dev.sh status           pid + health
#   ./dev.sh logs             tail the server log
#
# Env overrides: PORT (default 8080), OLLAMA_URL, OLLAMA_MODEL.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8080}"
RUN_DIR="$HERE/.dev"
PID_FILE="$RUN_DIR/uvicorn.pid"
LOG_FILE="$RUN_DIR/server.log"
URL="http://localhost:$PORT"

mkdir -p "$RUN_DIR"

pid() { [[ -f "$PID_FILE" ]] && cat "$PID_FILE" 2>/dev/null || true; }

running() {
  local p; p="$(pid)"
  [[ -n "$p" ]] && kill -0 "$p" 2>/dev/null
}

check_deps() {
  python3 -c "import fastapi, uvicorn, sqlalchemy, jwt, pydantic_settings, requests" 2>/dev/null || {
    echo "missing python deps — run: pip install -r $HERE/backend/requirements.txt"
    exit 1
  }
}

start() {
  if running; then
    echo "already running (pid $(pid)) → $URL"
    exit 0
  fi
  check_deps

  local mode="demo"
  local -a env_vars=(
    "DEMO_MODE=true"
    "DATABASE_URL=sqlite:///$RUN_DIR/questops.db"
    "OLLAMA_URL=${OLLAMA_URL:-http://localhost:11434}"
    "OLLAMA_MODEL=${OLLAMA_MODEL:-llama3.1}"
  )
  if [[ "${1:-}" == "--live" ]]; then
    [[ -f "$HERE/.env" ]] || { echo "--live needs $HERE/.env (copy .env.example)"; exit 1; }
    mode="live (.env)"
    env_vars=("DATABASE_URL=sqlite:///$RUN_DIR/questops.db")  # .env supplies the rest
    cd "$HERE"  # so pydantic-settings picks up ./.env
  fi

  PYTHONPATH="$HERE/backend" env "${env_vars[@]}" \
    nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT" --reload \
    >"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"

  for _ in $(seq 1 30); do
    if curl -sf "$URL/api/health" >/dev/null 2>&1; then
      echo "QuestOps up ($mode) → $URL"
      [[ "$mode" == demo ]] && echo "login: alice/demo (lead+approver), bob, carol, dave"
      exit 0
    fi
    running || { echo "server died on startup — last log lines:"; tail -15 "$LOG_FILE"; exit 1; }
    sleep 0.5
  done
  echo "timed out waiting for $URL/api/health — see $LOG_FILE"
  exit 1
}

stop() {
  if ! running; then
    echo "not running"
    rm -f "$PID_FILE"
    return 0
  fi
  local p; p="$(pid)"
  # --reload spawns a child worker; kill the whole process group if possible
  kill "$p" 2>/dev/null || true
  pkill -P "$p" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$p" 2>/dev/null || break
    sleep 0.3
  done
  kill -9 "$p" 2>/dev/null || true
  rm -f "$PID_FILE"
  echo "stopped"
}

status() {
  if running; then
    echo "running (pid $(pid)) → $URL"
    curl -sf "$URL/api/health" || echo "  (process alive but /api/health not responding)"
    echo
  else
    echo "not running"
  fi
}

case "${1:-}" in
  start)   shift; start "$@";;
  stop)    stop;;
  restart) stop; start "${2:-}";;
  status)  status;;
  logs)    exec tail -f "$LOG_FILE";;
  *)       grep '^#   ' "$0" | sed 's/^#   //'; exit 1;;
esac
