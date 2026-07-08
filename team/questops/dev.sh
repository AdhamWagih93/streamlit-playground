#!/usr/bin/env bash
# QuestOps local dev instance — runs entirely in containers via podman-compose
# (falls back to `podman compose` / `docker compose`). No host python deps.
#
#   ./dev.sh start      build + up -d, wait until healthy
#   ./dev.sh stop       down (keeps DB volume)
#   ./dev.sh restart    stop + start
#   ./dev.sh status     containers + health
#   ./dev.sh logs       follow app logs
#   ./dev.sh reset      down + wipe volumes (fresh demo data)
#
# Env overrides: PORT (default 8080), OLLAMA_URL, OLLAMA_MODEL — or use ./.env.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PORT="${PORT:-8080}"
URL="http://localhost:$PORT"
cd "$HERE"

if command -v podman-compose >/dev/null 2>&1; then
  COMPOSE=(podman-compose)
elif command -v podman >/dev/null 2>&1 && podman compose version >/dev/null 2>&1; then
  COMPOSE=(podman compose)
elif command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
else
  echo "no compose tool found — install podman-compose (pip install podman-compose)"
  exit 1
fi

# rootless podman without a systemd user session (WSL, CI) can't use the
# systemd cgroup manager — point podman at a minimal cgroupfs config
if [[ "${COMPOSE[0]}" == podman* ]] \
   && [[ ! -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/systemd/private" ]]; then
  mkdir -p "$HERE/.dev"
  cat > "$HERE/.dev/containers.conf" <<'EOF'
[engine]
cgroup_manager = "cgroupfs"
events_logger = "file"
EOF
  export CONTAINERS_CONF="$HERE/.dev/containers.conf"
fi

# podman < 4 (CNI era) can't parse the '--network=net:alias=svc' syntax
# podman-compose emits — containers land on the default network without DNS
# and the app can't resolve 'db'. No reliable workaround; use podman >= 4.
if [[ "${COMPOSE[0]}" == "podman-compose" ]]; then
  PODMAN_MAJOR="$(podman --version | grep -oE '[0-9]+' | head -1)"
  if (( PODMAN_MAJOR < 4 )); then
    echo "WARNING: podman ${PODMAN_MAJOR}.x detected — inter-container DNS needs podman >= 4;"
    echo "         the app container will likely fail to reach the db on this host."
  fi
fi

start() {
  if curl -sf "$URL/api/health" >/dev/null 2>&1; then
    echo "already running → $URL"
    exit 0
  fi
  # podman 3.x leaks its rootlessport forwarder when a container dies during
  # startup — the zombie keeps the port bound. Kill it (only that process kind).
  local stale
  stale="$(ss -tlnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1 || true)"
  if [[ -n "${stale:-}" ]] && grep -qa rootlessport "/proc/$stale/cmdline" 2>/dev/null; then
    echo "freeing port $PORT from a stale podman rootlessport (pid $stale)"
    kill "$stale" 2>/dev/null || true
    sleep 1
  fi
  echo "starting with ${COMPOSE[*]} (first run builds the image — takes a few minutes)…"
  "${COMPOSE[@]}" up -d --build

  for _ in $(seq 1 120); do
    if curl -sf "$URL/api/health" >/dev/null 2>&1; then
      echo "QuestOps up → $URL"
      echo "demo login: alice/demo (lead+approver), bob, carol, dave"
      exit 0
    fi
    sleep 1
  done
  echo "timed out waiting for $URL/api/health — recent app logs:"
  "${COMPOSE[@]}" logs --tail 30 app || true
  exit 1
}

case "${1:-}" in
  start)   start;;
  stop)    "${COMPOSE[@]}" down; echo "stopped (DB volume kept — './dev.sh reset' wipes it)";;
  restart) "${COMPOSE[@]}" down; start;;
  reset)   "${COMPOSE[@]}" down --volumes; echo "stopped + volumes wiped";;
  logs)    exec "${COMPOSE[@]}" logs -f app;;
  status)
    "${COMPOSE[@]}" ps
    if curl -sf "$URL/api/health" 2>/dev/null; then echo; else echo "health: not responding on $URL"; fi
    ;;
  *)       grep '^#   ' "$0" | sed 's/^#   //'; exit 1;;
esac
