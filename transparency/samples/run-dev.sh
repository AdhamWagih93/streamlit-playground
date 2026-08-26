#!/usr/bin/env bash
#
# Spin up the Transparency Dashboard in a container.
#
#   ./samples/run-dev.sh              against the report server in samples/powerbi.env
#   ./samples/run-dev.sh --no-upstream    UI only, no report server needed
#   PORT=9090 ./samples/run-dev.sh    on a different port
#
# Builds the jar inside Maven, so no JDK is needed on the host.

set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8080}"
IMAGE="efinance-powerbi:dev"
NAME="transparency-dashboard"
ENV_FILE="samples/powerbi.env"

echo "==> Building $IMAGE (first run downloads dependencies; later runs are cached)"
docker build -t "$IMAGE" .

docker rm -f "$NAME" >/dev/null 2>&1 || true

if [[ "${1:-}" == "--no-upstream" ]]; then
  # No report server: the shell renders and reports the source as unreachable,
  # which is the state you want on screen while working on the UI anyway.
  echo "==> Starting without a report server — the dashboard will show 'Unreachable'"
  docker run -d --name "$NAME" -p "$PORT:8080" \
    -e POWERBI_BASE_URL=http://127.0.0.1:9 \
    -e POWERBI_ALLOWED_HOST=127.0.0.1 \
    -e POWERBI_AUTH_TYPE=NONE \
    -e APP_SECURITY_ENABLED=false \
    "$IMAGE" >/dev/null
else
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE" >&2
    echo "  cp ${ENV_FILE}.sample $ENV_FILE   then fill in the report server details," >&2
    echo "  or run: $0 --no-upstream" >&2
    exit 1
  fi
  echo "==> Starting against the report server in $ENV_FILE"
  # --add-host / --network host may be needed when the report server resolves
  # only on the workstation's network; see the notes in compose.yaml.
  docker run -d --name "$NAME" -p "$PORT:8080" --env-file "$ENV_FILE" "$IMAGE" >/dev/null
fi

echo -n "==> Waiting for startup"
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/actuator/health" >/dev/null 2>&1; then
    echo
    echo "==> Dashboard:  http://127.0.0.1:$PORT/reports/powerbi/transparency"
    echo "    Logs:       docker logs -f $NAME"
    echo "    Stop:       docker rm -f $NAME"
    exit 0
  fi
  echo -n "."
  sleep 2
done

echo
echo "Did not become healthy in time. Recent logs:" >&2
docker logs --tail 40 "$NAME" >&2
exit 1
