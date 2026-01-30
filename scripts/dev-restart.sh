#!/usr/bin/env bash
# Best Streamlit Website - Development Restart Script (Linux/macOS)
# Stops the dev stack, then starts it again.

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Pass-through args to dev-start.sh, plus an optional --remove for dev-stop.sh
REMOVE=false
START_ARGS=()

while [[ $# -gt 0 ]]; do
  case $1 in
    --remove)
      REMOVE=true
      shift
      ;;
    *)
      START_ARGS+=("$1")
      shift
      ;;
  esac
done

echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}Restarting Best Streamlit Website${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""

echo -e "${YELLOW}Stopping...${NC}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

if [ "$REMOVE" = true ]; then
  ./scripts/dev-stop.sh --remove
else
  ./scripts/dev-stop.sh
fi

echo ""
echo -e "${GREEN}Starting...${NC}"
./scripts/dev-start.sh "${START_ARGS[@]}"
