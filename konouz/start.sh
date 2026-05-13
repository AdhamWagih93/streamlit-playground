#!/usr/bin/env bash
# Start the Magento on-prem stack (podman-compose).
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
  --fresh) podman-compose build --no-cache app; BUILD_ARGS=() ;;
  "") ;;
  *) echo "Unknown flag: $1" >&2; exit 2 ;;
esac

echo ">>> Bringing stack up..."
podman-compose up -d "${BUILD_ARGS[@]}"

echo ">>> Waiting for services to become healthy (up to 120s)..."
HEALTH_CONTAINERS=(magento-mysql magento-elasticsearch)
deadline=$((SECONDS + 120))
while (( SECONDS < deadline )); do
  all_ok=true
  for c in "${HEALTH_CONTAINERS[@]}"; do
    status=$(podman inspect --format '{{.State.Health.Status}}' "$c" 2>/dev/null || echo "missing")
    if [[ "$status" != "healthy" ]]; then
      all_ok=false
      break
    fi
  done
  $all_ok && break
  sleep 3
done

echo
echo ">>> Stack status:"
podman-compose ps

cat <<EOF

Access points:
  Magento storefront : http://localhost/
  phpMyAdmin         : http://localhost:8080/
  Elasticsearch      : http://localhost:9200/
  MySQL              : localhost:3306  (magento/magento)
EOF
