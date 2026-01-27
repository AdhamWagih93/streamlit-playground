#!/usr/bin/env bash
# Best Streamlit Website - Development Startup Script (Linux/macOS)
# This script starts the full development stack using Podman Compose

set -e

# Colors
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

# Parse arguments
# Enable AI profile (Ollama) by default
WITH_AI=true
WITH_TOOLS=false
FULL=false
DETACH=false
BUILD=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --ai)
            WITH_AI=true
            shift
            ;;
        --tools)
            WITH_TOOLS=true
            shift
            ;;
        --full)
            FULL=true
            shift
            ;;
        -d|--detach)
            DETACH=true
            shift
            ;;
        -b|--build)
            BUILD=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--ai] [--tools] [--full] [-d|--detach] [-b|--build]"
            exit 1
            ;;
    esac
done

echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}Best Streamlit Website - Dev Startup${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""

# Work around Docker Desktop BuildKit/Buildx issues on some environments
# by forcing compose to use the classic builder instead of buildx bake.
export DOCKER_BUILDKIT=0
export COMPOSE_DOCKER_CLI_BUILD=0

# Check if Podman is available
if ! command -v podman &>/dev/null; then
    echo -e "${RED}ERROR: podman is not installed or not in PATH.${NC}"
    exit 1
fi

# Navigate to repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# Check if .env exists, if not copy from example
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}Creating .env file from .env.example...${NC}"
    cp .env.example .env
    echo -e "${YELLOW}Please edit .env file with your configuration${NC}"
fi

# Ensure data directory exists
if [ ! -d "data" ]; then
    echo -e "${GREEN}Creating data directory...${NC}"
    mkdir -p data
fi

# Build compose command (Podman Compose)
COMPOSE_CMD="podman compose -f docker-compose.yml -f docker-compose.dev.yml"
PROFILES=()

# Collect profile names and set COMPOSE_PROFILES env var
if [ "$WITH_AI" = true ] || [ "$FULL" = true ]; then
    PROFILES+=("ai")
fi

if [ "$WITH_TOOLS" = true ] || [ "$FULL" = true ]; then
    PROFILES+=("tools")
fi

if [ "$FULL" = true ]; then
    PROFILES+=("full")
fi

if [ ${#PROFILES[@]} -gt 0 ]; then
    IFS=',' read -r -a _TMP <<< "${PROFILES[*]}"
    export COMPOSE_PROFILES="${_TMP[*]}"
else
    unset COMPOSE_PROFILES
fi

# Build command arguments
CMD_ARGS=("up")

if [ "$DETACH" = true ]; then
    CMD_ARGS+=("-d")
fi

if [ "$BUILD" = true ]; then
    CMD_ARGS+=("--build")
fi

echo -e "${GREEN}Starting services...${NC}"
echo -e "${GRAY}Command: COMPOSE_PROFILES=${COMPOSE_PROFILES:-""} $COMPOSE_CMD ${CMD_ARGS[*]}${NC}"
echo ""

# Execute podman compose
export COMPOSE_PROJECT_NAME="bsw"
$COMPOSE_CMD "${CMD_ARGS[@]}"

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=====================================${NC}"
    echo -e "${GREEN}Services started successfully!${NC}"
    echo -e "${GREEN}=====================================${NC}"
    echo ""
    echo -e "${CYAN}Access the application at:${NC}"
    echo -e "  Streamlit UI:     http://localhost:8501"
    echo -e "  Scheduler MCP:    http://localhost:8010"
    echo -e "  Docker MCP:       http://localhost:8001"
    echo -e "  Jenkins MCP:      http://localhost:8002"
    echo -e "  Kubernetes MCP:   http://localhost:8003"

    if [ "$WITH_TOOLS" = true ] || [ "$FULL" = true ]; then
        echo -e "  DB Admin:         http://localhost:8090"
    fi

    if [ "$WITH_AI" = true ] || [ "$FULL" = true ]; then
        echo -e "  Ollama API:       http://localhost:11434"
    fi

    echo ""
    echo -e "${CYAN}Useful commands:${NC}"
    echo -e "${GRAY}  View logs:        ./scripts/dev-logs.sh${NC}"
    echo -e "${GRAY}  Stop services:    ./scripts/dev-stop.sh${NC}"
    echo -e "${GRAY}  Reset data:       ./scripts/dev-reset.sh${NC}"
else
    echo ""
    echo -e "${RED}ERROR: Failed to start services${NC}"
    exit 1
fi
