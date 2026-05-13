#!/usr/bin/env bash
# Start the Magento on-prem stack.
#
# Usage:
#   ./start.sh           # start (build only if image missing)
#   ./start.sh --build   # force rebuild of the app image
#   ./start.sh --fresh   # rebuild from scratch (--no-cache)

set -euo pipefail

cd "$(dirname "$0")"

BUILD_ARGS=()
case "${1:-}" in
  --build) BUILD_ARGS=(--build) ;;
  --fresh) docker compose build --no-cache app; BUILD_ARGS=() ;;
  "") ;;
  *) echo "Unknown flag: $1" >&2; exit 2 ;;
esac

echo ">>> Bringing stack up..."
docker compose up -d "${BUILD_ARGS[@]}"

echo ">>> Waiting for services to become healthy (up to 120s)..."
deadline=$((SECONDS + 120))
while (( SECONDS < deadline )); do
  unhealthy=$(docker compose ps --format '{{.Service}} {{.Health}}' \
              | awk '$2 != "" && $2 != "healthy" {print $1}' || true)
  if [[ -z "$unhealthy" ]]; then
    break
  fi
  sleep 3
done

echo
echo ">>> Stack status:"
docker compose ps

cat <<EOF

Access points:
  Magento storefront : http://localhost/
  phpMyAdmin         : http://localhost:8080/
  Elasticsearch      : http://localhost:9200/
  MySQL              : localhost:3306  (magento/magento)
EOF
