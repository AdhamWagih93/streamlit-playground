#!/usr/bin/env bash
# Best Streamlit Website - View Logs Script (Linux/macOS)
# This script displays logs from running services

set -e

CYAN='\033[0;36m'
NC='\033[0m'

SERVICE=""
FOLLOW=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--follow)
            FOLLOW=true
            shift
            ;;
        *)
            SERVICE="$1"
            shift
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME="bsw"
COMPOSE_CMD="docker-compose -f docker-compose.yml -f docker-compose.dev.yml"

CMD_ARGS="logs --tail=100"

if [ "$FOLLOW" = true ]; then
    CMD_ARGS="$CMD_ARGS -f"
fi

if [ -n "$SERVICE" ]; then
    CMD_ARGS="$CMD_ARGS $SERVICE"
fi

echo -e "${CYAN}Viewing logs...${NC}"
$COMPOSE_CMD $CMD_ARGS
