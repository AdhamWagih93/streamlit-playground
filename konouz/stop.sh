#!/usr/bin/env bash
# Stop the Magento on-prem stack.
#
# Usage:
#   ./stop.sh           # stop & remove containers, KEEP data volumes
#   ./stop.sh --pause   # just pause containers (faster restart, keeps state)
#   ./stop.sh --wipe    # remove containers AND volumes (DESTROYS db/es/code data)

set -euo pipefail

cd "$(dirname "$0")"

case "${1:-}" in
  --pause)
    echo ">>> Stopping containers (state preserved)..."
    docker compose stop
    ;;
  --wipe)
    read -r -p "This will DELETE mysql-data, es-data, magento-code volumes. Type 'wipe' to confirm: " ans
    if [[ "$ans" != "wipe" ]]; then
      echo "Aborted."; exit 1
    fi
    echo ">>> Removing containers AND volumes..."
    docker compose down -v
    ;;
  "")
    echo ">>> Removing containers (volumes kept)..."
    docker compose down
    ;;
  *)
    echo "Unknown flag: $1" >&2; exit 2
    ;;
esac

echo
echo ">>> Remaining state:"
docker compose ps
