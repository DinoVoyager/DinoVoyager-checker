#!/bin/sh
# ==============================================================================
# DinoVoyager-checker Auto-Update Script (GitHub -> Local / Docker)
# ==============================================================================
# This script pulls the latest code from GitHub, updates local permissions,
# rebuilds or pulls the Docker image, and gracefully restarts the checker container
# while preserving all existing environment variables and configuration.
#
# Usage:
#   chmod +x update.sh
#   ./update.sh
# ==============================================================================

set -e

# Color helpers (disabled if not terminal)
if [ -t 1 ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    RED='\033[0;31m'
    NC='\033[0m'
else
    GREEN=''
    YELLOW=''
    CYAN=''
    RED=''
    NC=''
fi

echo "${CYAN}======================================================${NC}"
echo "${CYAN}        🦖 DinoVoyager-checker Update Tool           ${NC}"
echo "${CYAN}======================================================${NC}"

# Navigate to script directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 1. Check Git Repository & Pull Latest Commits
if [ -d ".git" ]; then
    if command -v git >/dev/null 2>&1; then
        echo -e "${YELLOW}>> Checking for updates on GitHub...${NC}"
        
        # Determine current branch
        CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")"
        git fetch origin "$CURRENT_BRANCH" --quiet 2>/dev/null || true

        LOCAL_HASH="$(git rev-parse HEAD 2>/dev/null || echo "")"
        REMOTE_HASH="$(git rev-parse "origin/$CURRENT_BRANCH" 2>/dev/null || echo "")"

        if [ -n "$LOCAL_HASH" ] && [ -n "$REMOTE_HASH" ] && [ "$LOCAL_HASH" != "$REMOTE_HASH" ]; then
            echo -e "${GREEN}>> New commits found on GitHub. Updating repository...${NC}"
            git pull origin "$CURRENT_BRANCH"
            echo -e "${GREEN}>> Git repository updated to ${REMOTE_HASH:0:8}${NC}"
        else
            echo -e "${GREEN}>> Local repository is already up to date (${LOCAL_HASH:0:8}).${NC}"
        fi
    else
        echo -e "${YELLOW}>> Warning: 'git' command not found, skipping git pull.${NC}"
    fi
else
    echo -e "${YELLOW}>> Not a git repository (standalone directory). Skipping git fetch.${NC}"
fi

# 2. Fix Executable Permissions
echo -e "${YELLOW}>> Ensuring executable permissions...${NC}"
chmod +x bin/xray-checker 2>/dev/null || true
chmod +x entrypoint.sh 2>/dev/null || true
chmod +x update.sh 2>/dev/null || true

# 3. Docker Service & Container Restart
if command -v docker >/dev/null 2>&1; then
    CONTAINER_NAME="dinovoyager-checker"
    IS_COMPOSE="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER_NAME" 2>/dev/null || true)"

    # Check if existing container was managed by docker-compose
    if [ -n "$IS_COMPOSE" ] && { [ -f "docker-compose.yml" ] || [ -f "docker-compose.yaml" ] || [ -f "compose.yaml" ]; }; then
        if docker compose version >/dev/null 2>&1; then
            COMPOSE_CMD="docker compose"
        elif command -v docker-compose >/dev/null 2>&1; then
            COMPOSE_CMD="docker-compose"
        else
            COMPOSE_CMD=""
        fi

        if [ -n "$COMPOSE_CMD" ]; then
            echo -e "${YELLOW}>> Rebuilding and restarting via Docker Compose...${NC}"
            $COMPOSE_CMD build --pull || true
            $COMPOSE_CMD up -d --remove-orphans
            echo -e "${GREEN}>> Compose services updated and running.${NC}"
        fi
    elif docker ps -a --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}\$"; then
        echo -e "${YELLOW}>> Detected running container '${CONTAINER_NAME}'. Preserving settings...${NC}"
        
        # Extract environment variables from current container
        ENV_ARGS=""
        for env_var in SUBSCRIPTION_URL POLLING_INTERVAL API_URL API_KEY CHECKER_NAME LOG_LEVEL PROXY_TIMEOUT; do
            VAL="$(docker inspect --format "{{range .Config.Env}}{{println .}}{{end}}" "$CONTAINER_NAME" 2>/dev/null | grep "^${env_var}=" | head -n 1 | cut -d= -f2- || true)"
            if [ -n "$VAL" ]; then
                ENV_ARGS="${ENV_ARGS} -e ${env_var}=\"${VAL}\""
            fi
        done

        RESTART_POLICY="$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$CONTAINER_NAME" 2>/dev/null || echo "unless-stopped")"
        [ -z "$RESTART_POLICY" ] && RESTART_POLICY="unless-stopped"

        echo -e "${YELLOW}>> Building updated image 'as1aw/checker:latest'...${NC}"
        docker build -t as1aw/checker:latest .

        echo -e "${YELLOW}>> Re-creating container '${CONTAINER_NAME}'...${NC}"
        docker stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
        docker rm "$CONTAINER_NAME" >/dev/null 2>&1 || true

        # Run new container
        eval "docker run -d --name \"$CONTAINER_NAME\" --restart \"$RESTART_POLICY\" $ENV_ARGS as1aw/checker:latest"
        echo -e "${GREEN}>> Container '${CONTAINER_NAME}' successfully updated and started.${NC}"
    else
        echo -e "${YELLOW}>> Building Docker image 'as1aw/checker:latest'...${NC}"
        docker build -t as1aw/checker:latest .
        echo -e "${GREEN}>> Image built successfully.${NC}"
        echo -e "${CYAN}>> You can start the container using:${NC}"
        echo -e "   docker run -d --name $CONTAINER_NAME --restart unless-stopped -e SUBSCRIPTION_URL=\"...\" -e API_URL=\"...\" -e API_KEY=\"...\" as1aw/checker:latest"
    fi

    # 4. Quick Health Check
    if docker ps --format '{{.Names}}' | grep -Eq "^${CONTAINER_NAME}\$"; then
        echo -e "${YELLOW}>> Waiting 5 seconds for health check...${NC}"
        sleep 5
        echo -e "${GREEN}>> Recent container logs:${NC}"
        docker logs --tail 8 "$CONTAINER_NAME" || true
    fi
else
    echo -e "${YELLOW}>> Docker not found on host. Local files updated successfully.${NC}"
fi

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}    ✅ DinoVoyager-checker update completed!          ${NC}"
echo -e "${GREEN}======================================================${NC}"
