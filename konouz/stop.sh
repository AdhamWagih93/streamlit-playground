#!/usr/bin/env bash
# Stop the Magento on-prem stack (podman-compose).
#
# Usage:
#   ./stop.sh           # stop & remove containers, KEEP data volumes
#   ./stop.sh --pause   # just stop containers (faster restart, keeps state)
#   ./stop.sh --wipe    # remove containers AND volumes (DESTROYS db/es/code data)
#   ./stop.sh --force   # nuclear: force-remove the whole pod + orphan nets

set -euo pipefail

cd "$(dirname "$0")"

PROJECT="$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"

force_cleanup() {
  echo ">>> Force cleanup: removing pods matching '${PROJECT}'..."
  podman pod ls --format '{{.Name}}' \
    | grep -i "${PROJECT}" \
    | xargs -r -I{} podman pod rm -f {} || true

  echo ">>> Removing project networks..."
  podman network ls --format '{{.Name}}' \
    | grep -i "${PROJECT}" \
    | xargs -r -I{} podman network rm {} 2>/dev/null || true
}

case "${1:-}" in
  --pause)
    echo ">>> Stopping containers (state preserved)..."
    podman-compose stop || true
    ;;
  --wipe)
    read -r -p "This will DELETE mysql-data, es-data, magento-code volumes. Type 'wipe' to confirm: " ans
    if [[ "$ans" != "wipe" ]]; then
      echo "Aborted."; exit 1
    fi
    echo ">>> Removing containers AND volumes..."
    podman-compose down -v --remove-orphans || force_cleanup
    ;;
  --force)
    force_cleanup
    ;;
  "")
    echo ">>> Removing containers (volumes kept)..."
    podman-compose down --remove-orphans || {
      echo ">>> podman-compose down failed; falling back to force cleanup."
      force_cleanup
    }
    ;;
  *)
    echo "Unknown flag: $1" >&2; exit 2
    ;;
esac

echo
echo ">>> Remaining state:"
podman ps -a --filter "name=magento-" || true
podman pod ls
