#!/usr/bin/env bash
# Best Streamlit Website - Development Stop Script (Linux/macOS)
# This script stops all running services

set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

REMOVE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --remove)
            REMOVE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--remove]"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}Stopping Best Streamlit Website${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME="bsw"
COMPOSE_CMD="docker-compose -f docker-compose.yml -f docker-compose.dev.yml"

if [ "$REMOVE" = true ]; then
    echo -e "${YELLOW}Stopping and removing containers...${NC}"
    $COMPOSE_CMD down --remove-orphans
else
    echo -e "${YELLOW}Stopping containers...${NC}"
    $COMPOSE_CMD stop
fi

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}Services stopped successfully!${NC}"
else
    echo ""
    echo -e "${RED}ERROR: Failed to stop services${NC}"
    exit 1
fi
