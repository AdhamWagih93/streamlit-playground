#!/usr/bin/env bash
# Best Streamlit Website - Reset Script (Linux/macOS)
# This script resets the development environment

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

KEEP_DATA=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-data)
            KEEP_DATA=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--keep-data]"
            exit 1
            ;;
    esac
done

echo -e "${RED}=====================================${NC}"
echo -e "${RED}Reset Development Environment${NC}"
echo -e "${RED}=====================================${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

if [ "$KEEP_DATA" = false ]; then
    echo -e "${YELLOW}WARNING: This will delete all data including databases!${NC}"
    read -p "Are you sure? (yes/no): " confirm

    if [ "$confirm" != "yes" ]; then
        echo -e "${GREEN}Reset cancelled${NC}"
        exit 0
    fi
fi

echo -e "${YELLOW}Stopping containers...${NC}"
./scripts/dev-stop.sh --remove

echo -e "${YELLOW}Removing volumes...${NC}"
docker volume rm bsw-ollama-data -f 2>/dev/null || true

if [ "$KEEP_DATA" = false ]; then
    echo -e "${YELLOW}Removing data directory...${NC}"
    rm -rf data
    mkdir -p data
fi

echo ""
echo -e "${GREEN}Environment reset complete!${NC}"
echo -e "${CYAN}Run ./scripts/dev-start.sh to start fresh${NC}"
