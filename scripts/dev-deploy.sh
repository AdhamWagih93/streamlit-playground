#!/usr/bin/env bash
# Best Streamlit Website - Development Deploy Script (Linux/macOS)
# Applies changes with minimal restarts: only services with changed images/config are recreated.
#
# Uses the same compose files as dev-start.sh.

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
GRAY='\033[0;37m'
RED='\033[0;31m'
NC='\033[0m'

WITH_AI=true
WITH_TOOLS=false
FULL=false
BUILD=false
PULL=false

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
    -b|--build)
      BUILD=true
      shift
      ;;
    --pull)
      PULL=true
      shift
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--ai] [--tools] [--full] [-b|--build] [--pull]"
      exit 1
      ;;
  esac
done

echo -e "${CYAN}=====================================${NC}"
echo -e "${CYAN}Best Streamlit Website - Dev Deploy${NC}"
echo -e "${CYAN}=====================================${NC}"
echo ""

# Work around BuildKit/Buildx issues where relevant
export DOCKER_BUILDKIT=0
export COMPOSE_DOCKER_CLI_BUILD=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$REPO_ROOT"

# dev-start.sh uses podman compose; keep consistent here.
COMPOSE_CMD="podman compose -f docker-compose.yml -f docker-compose.dev.yml"
export COMPOSE_PROJECT_NAME="bsw"

PROFILES=()
if [ "$WITH_AI" = true ] || [ "$FULL" = true ]; then PROFILES+=("ai"); fi
if [ "$WITH_TOOLS" = true ] || [ "$FULL" = true ]; then PROFILES+=("tools"); fi
if [ "$FULL" = true ]; then PROFILES+=("full"); fi

if [ ${#PROFILES[@]} -gt 0 ]; then
  IFS=',' read -r -a _TMP <<< "${PROFILES[*]}"
  export COMPOSE_PROFILES="${_TMP[*]}"
else
  unset COMPOSE_PROFILES
fi

# Snapshot container IDs before
SERVICES=($($COMPOSE_CMD config --services))
if [ ${#SERVICES[@]} -eq 0 ]; then
  echo -e "${RED}ERROR: No services found in compose config.${NC}"
  exit 1
fi

BEFORE_FILE="$(mktemp)"
AFTER_FILE="$(mktemp)"
trap 'rm -f "$BEFORE_FILE" "$AFTER_FILE"' EXIT

for s in "${SERVICES[@]}"; do
  id="$($COMPOSE_CMD ps -q "$s" 2>/dev/null | head -n 1 | tr -d '\r')"
  echo "$s=$id" >> "$BEFORE_FILE"
done

ARGS=(up -d --remove-orphans)
if [ "$PULL" = true ]; then ARGS+=(--pull always); fi
if [ "$BUILD" = true ]; then ARGS+=(--build); fi

echo -e "${GREEN}Applying changes (minimal restarts)...${NC}"
echo -e "${GRAY}Command: COMPOSE_PROFILES=${COMPOSE_PROFILES:-""} $COMPOSE_CMD ${ARGS[*]}${NC}"
$COMPOSE_CMD "${ARGS[@]}"

echo "" 

for s in "${SERVICES[@]}"; do
  id="$($COMPOSE_CMD ps -q "$s" 2>/dev/null | head -n 1 | tr -d '\r')"
  echo "$s=$id" >> "$AFTER_FILE"
done

# Report changes
STARTED=()
CHANGED=()
STOPPED=()

while IFS='=' read -r svc before_id; do
  after_id="$(grep -E "^${svc}=" "$AFTER_FILE" | head -n 1 | cut -d'=' -f2-)"
  if [ -z "$before_id" ] && [ -n "$after_id" ]; then
    STARTED+=("$svc")
  elif [ -n "$before_id" ] && [ -z "$after_id" ]; then
    STOPPED+=("$svc")
  elif [ -n "$before_id" ] && [ -n "$after_id" ] && [ "$before_id" != "$after_id" ]; then
    CHANGED+=("$svc")
  fi

done < "$BEFORE_FILE"

echo -e "${GREEN}=====================================${NC}"
echo -e "${GREEN}Deploy complete${NC}"
echo -e "${GREEN}=====================================${NC}"

if [ ${#STARTED[@]} -gt 0 ]; then
  echo -e "${CYAN}Started:${NC}"
  printf '  - %s\n' "${STARTED[@]}"
fi
if [ ${#CHANGED[@]} -gt 0 ]; then
  echo -e "${CYAN}Recreated (changed):${NC}"
  printf '  - %s\n' "${CHANGED[@]}"
fi
if [ ${#STOPPED[@]} -gt 0 ]; then
  echo -e "${CYAN}Stopped:${NC}"
  printf '  - %s\n' "${STOPPED[@]}"
fi
if [ ${#STARTED[@]} -eq 0 ] && [ ${#CHANGED[@]} -eq 0 ] && [ ${#STOPPED[@]} -eq 0 ]; then
  echo -e "${GRAY}No container changes detected.${NC}"
fi
