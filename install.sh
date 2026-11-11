#!/usr/bin/env bash
# ==============================================================================
# DinoVoyager-checker One-Line Installer
# ==============================================================================
# Installs, configures, and launches the DinoVoyager-checker Docker container.
#
# Quick interactive run:
#   curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash
#
# Quick non-interactive run with parameters:
#   curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash -s -- \
#     --api-key "dvc_live_..." \
#     --subscription-url "https://app.dinovoyager.net/api/sub/..." \
#     --api-url "https://app.dinovoyager.net" \
#     --name "My-Home-Server" -y
# ==============================================================================

set -e

# Color helpers
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    CYAN=''
    BOLD=''
    NC=''
fi

# Default values
API_KEY="${API_KEY:-}"
SUBSCRIPTION_URL="${SUBSCRIPTION_URL:-}"
API_URL="${API_URL:-https://app.dinovoyager.net}"
CHECKER_NAME="${CHECKER_NAME:-}"
POLLING_INTERVAL="${POLLING_INTERVAL:-300}"
REGION_CODE="${REGION_CODE:-${REGION:-RU-MOW}}"
IMAGE_NAME="${IMAGE_NAME:-as1aw/checker:latest}"
AUTO_CONFIRM=false

# Default directory
if [ -z "${INSTALL_DIR:-}" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        INSTALL_DIR="/opt/dinovoyager-checker"
    else
        INSTALL_DIR="${HOME}/dinovoyager-checker"
    fi
fi

# Print banner
print_banner() {
    echo -e "${CYAN}=================================================================${NC}"
    echo -e "${CYAN}             🦖 DinoVoyager-checker Installer                   ${NC}"
    echo -e "${CYAN}=================================================================${NC}"
}

# Print usage
print_usage() {
    cat <<EOF
Usage: install.sh [OPTIONS]

Options:
  -k, --api-key <KEY>             Checker API Key (starts with dvc_live_)
  -s, --subscription-url <URL>    VLESS/VMess/Trojan subscription URL
  -u, --api-url <URL>             Central API URL (default: https://app.dinovoyager.net)
  -n, --name <NAME>               Checker identifier (default: hostname)
  -r, --region <REGION>           Russian region code (default: RU-MOW)
  -i, --interval <SECONDS>        Polling cadence in seconds (default: 300, min: 15)
  -d, --dir <DIR>                 Target install directory (default: /opt/dinovoyager-checker)
      --image <IMAGE>             Docker image (default: as1aw/checker:latest)
  -y, --yes                       Assume 'yes' to prompts (non-interactive)
  -h, --help                      Show this help message

Environment variables are also supported:
  API_KEY, SUBSCRIPTION_URL, API_URL, CHECKER_NAME, POLLING_INTERVAL, INSTALL_DIR, IMAGE_NAME

Examples:
  # Interactive mode:
  curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash

  # Non-interactive mode:
  curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash -s -- \\
    -k "dvc_live_..." \\
    -s "https://app.dinovoyager.net/api/sub/..." \\
    -u "https://app.dinovoyager.net" \\
    -n "Home-Server" -y
EOF
}

# Parse CLI arguments
while [ $# -gt 0 ]; do
    case "$1" in
        -k|--api-key)
            API_KEY="$2"
            shift 2
            ;;
        -s|--subscription-url)
            SUBSCRIPTION_URL="$2"
            shift 2
            ;;
        -u|--api-url)
            API_URL="$2"
            shift 2
            ;;
        -n|--name)
            CHECKER_NAME="$2"
            shift 2
            ;;
        -r|--region)
            REGION_CODE="$2"
            shift 2
            ;;
        -i|--interval)
            POLLING_INTERVAL="$2"
            shift 2
            ;;
        -d|--dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --image)
            IMAGE_NAME="$2"
            shift 2
            ;;
        -y|--yes)
            AUTO_CONFIRM=true
            shift
            ;;
        -h|--help)
            print_usage
            exit 0
            ;;
        *)
            echo -e "${RED}[ERROR] Unknown option: $1${NC}" >&2
            print_usage
            exit 1
            ;;
    esac
done

# Helper function to read from TTY when script is piped from curl
prompt_input() {
    local prompt_var="$1"
    local prompt_label="$2"
    local default_val="$3"

    local current_val="${!prompt_var:-}"
    if [ -n "$current_val" ]; then
        return 0
    fi

    # Find accessible TTY device
    local tty_input=""
    if [ -t 0 ]; then
        tty_input="/dev/stdin"
    elif [ -e /dev/tty ] && [ -r /dev/tty ]; then
        tty_input="/dev/tty"
    else
        return 1
    fi

    if [ -n "$default_val" ]; then
        echo -ne "${BOLD}${prompt_label}${NC} [${GREEN}${default_val}${NC}]: " >&2
    else
        echo -ne "${BOLD}${prompt_label}${NC}: " >&2
    fi

    local user_val=""
    if ! read -r user_val < "$tty_input" 2>/dev/null; then
        return 1
    fi

    if [ -z "$user_val" ]; then
        user_val="$default_val"
    fi

    eval "$prompt_var=\"\$user_val\""
    return 0
}

# Prompt confirmation (y/N)
prompt_confirm() {
    local prompt_label="$1"
    local default_choice="$2" # y or n

    if [ "$AUTO_CONFIRM" = true ]; then
        return 0
    fi

    local tty_input=""
    if [ -t 0 ]; then
        tty_input="/dev/stdin"
    elif [ -e /dev/tty ] && [ -r /dev/tty ]; then
        tty_input="/dev/tty"
    else
        [ "$default_choice" = "y" ] && return 0 || return 1
    fi

    local hint="[y/N]"
    [ "$default_choice" = "y" ] && hint="[Y/n]"

    echo -ne "${BOLD}${prompt_label}${NC} ${hint}: " >&2
    local answer=""
    if ! read -r answer < "$tty_input" 2>/dev/null; then
        [ "$default_choice" = "y" ] && return 0 || return 1
    fi
    answer="$(echo "$answer" | tr '[:upper:]' '[:lower:]')"

    if [ -z "$answer" ]; then
        [ "$default_choice" = "y" ] && return 0 || return 1
    fi

    case "$answer" in
        y|yes|д|да) return 0 ;;
        *) return 1 ;;
    esac
}

print_banner

# Step 1: Collect parameters interactively if missing and user is interactive
can_prompt() {
    [ "$AUTO_CONFIRM" != "true" ] && { [ -t 0 ] || { [ -e /dev/tty ] && [ -r /dev/tty ] && [ -t 1 ]; }; }
}

if { [ -z "$API_KEY" ] || [ -z "$SUBSCRIPTION_URL" ]; } && can_prompt; then
    echo -e "${YELLOW}>> Configuration parameters required. Please enter details:${NC}\n"

    # Prompt API_URL
    prompt_input "API_URL" "DinoVoyager Central API URL" "$API_URL" || true

    # Prompt API_KEY
    while [ -z "$API_KEY" ]; do
        if ! prompt_input "API_KEY" "Checker API Key (starts with dvc_live_)" ""; then
            break
        fi
        if [ -z "$API_KEY" ]; then
            echo -e "${RED}[ERROR] API Key is required!${NC}" >&2
        fi
    done

    # Prompt SUBSCRIPTION_URL
    while [ -z "$SUBSCRIPTION_URL" ]; do
        if ! prompt_input "SUBSCRIPTION_URL" "Proxy Subscription URL (https://...)" ""; then
            break
        fi
        if [ -z "$SUBSCRIPTION_URL" ]; then
            echo -e "${RED}[ERROR] Subscription URL is required!${NC}" >&2
        fi
    done

    # Prompt CHECKER_NAME
    HOSTNAME_DEFAULT="$(hostname 2>/dev/null || echo "checker-node")"
    prompt_input "CHECKER_NAME" "Checker Node Name" "$HOSTNAME_DEFAULT" || true

    # Prompt REGION_CODE
    prompt_input "REGION_CODE" "Assigned Region Code (e.g. RU-MOW, RU-SPE)" "$REGION_CODE" || true

    # Prompt POLLING_INTERVAL
    prompt_input "POLLING_INTERVAL" "Polling cadence in seconds (min 15)" "$POLLING_INTERVAL" || true
fi

# Normalize API_URL (strip trailing slash)
API_URL="${API_URL%/}"

# Final parameter validation
if [ -z "$API_KEY" ] || [ -z "$SUBSCRIPTION_URL" ] || [ -z "$API_URL" ]; then
    echo -e "\n${RED}[ERROR] Required parameters are missing!${NC}" >&2
    echo -e "You must provide --api-key, --subscription-url, and --api-url." >&2
    echo -e "\nExample non-interactive usage:" >&2
    echo -e "  curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash -s -- \\" >&2
    echo -e "    -k \"dvc_live_your_key_here\" \\" >&2
    echo -e "    -s \"https://app.dinovoyager.net/api/sub/...\" \\" >&2
    echo -e "    -u \"https://app.dinovoyager.net\" \\" >&2
    echo -e "    -y" >&2
    exit 1
fi

if [ -z "$CHECKER_NAME" ]; then
    CHECKER_NAME="$(hostname 2>/dev/null || echo "checker-node")"
fi

if [ "$POLLING_INTERVAL" -lt 15 ] 2>/dev/null; then
    echo -e "${YELLOW}>> Warning: Polling interval was set to ${POLLING_INTERVAL}s. Adjusting to minimum 15s.${NC}"
    POLLING_INTERVAL=15
fi

# Step 2: Check & Install Docker
echo -e "\n${YELLOW}>> Checking Docker installation...${NC}"
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${YELLOW}>> Docker is not installed on this system.${NC}"
    if [ "$(uname -s)" = "Linux" ]; then
        if prompt_confirm "Would you like to automatically install Docker via official script?" "y"; then
            echo -e "${CYAN}>> Installing Docker...${NC}"
            if [ "$(id -u)" -eq 0 ]; then
                curl -fsSL https://get.docker.com | sh
            elif command -v sudo >/dev/null 2>&1; then
                curl -fsSL https://get.docker.com | sudo sh
            else
                echo -e "${RED}[ERROR] Root or sudo access required to install Docker.${NC}" >&2
                exit 1
            fi
        else
            echo -e "${RED}[ERROR] Docker is required. Please install Docker manually and retry:${NC}" >&2
            echo -e "        https://docs.docker.com/engine/install/" >&2
            exit 1
        fi
    else
        echo -e "${RED}[ERROR] Docker is required. Please install Docker for your OS:${NC}" >&2
        echo -e "        https://docs.docker.com/engine/install/" >&2
        exit 1
    fi
fi

# Ensure Docker daemon is accessible
if ! docker info >/dev/null 2>&1; then
    echo -e "${YELLOW}>> Docker daemon is not active. Attempting to start...${NC}"
    if [ "$(id -u)" -eq 0 ]; then
        systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true
    elif command -v sudo >/dev/null 2>&1; then
        sudo systemctl start docker 2>/dev/null || sudo service docker start 2>/dev/null || true
    fi

    if ! docker info >/dev/null 2>&1; then
        echo -e "${RED}[ERROR] Unable to connect to Docker daemon.${NC}" >&2
        echo -e "Please ensure Docker is running and your user has permissions to run docker commands (e.g. usermod -aG docker \$USER)." >&2
        exit 1
    fi
fi
echo -e "${GREEN}>> Docker is installed and running.${NC}"

# Step 3: Setup Installation Directory
echo -e "\n${YELLOW}>> Setting up directory: ${INSTALL_DIR}...${NC}"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Step 4: Write Configuration (.env)
echo -e "${YELLOW}>> Writing configuration (.env)...${NC}"
cat > "$INSTALL_DIR/.env" <<EOF
# DinoVoyager-checker Configuration
SUBSCRIPTION_URL=${SUBSCRIPTION_URL}
POLLING_INTERVAL=${POLLING_INTERVAL}
API_URL=${API_URL}
API_KEY=${API_KEY}
CHECKER_NAME=${CHECKER_NAME}
REGION_CODE=${REGION_CODE}
LOG_LEVEL=INFO
PROXY_TIMEOUT=10
EOF

# Restrict permissions on .env
chmod 600 "$INSTALL_DIR/.env" 2>/dev/null || true

# Step 5: Write docker-compose.yml
cat > "$INSTALL_DIR/docker-compose.yml" <<EOF
version: "3.8"

services:
  dinovoyager-checker:
    image: ${IMAGE_NAME}
    container_name: dinovoyager-checker
    restart: unless-stopped
    env_file:
      - .env
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
EOF

# Step 6: Write local update.sh helper
cat > "$INSTALL_DIR/update.sh" << 'UPDATE_EOF'
#!/bin/sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================================"
echo "       🦖 Updating DinoVoyager-checker Container     "
echo "======================================================"

if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
else
    COMPOSE=""
fi

if [ -n "$COMPOSE" ] && [ -f "docker-compose.yml" ]; then
    echo ">> Pulling latest image..."
    $COMPOSE pull
    echo ">> Restarting service..."
    $COMPOSE up -d --remove-orphans
else
    echo ">> Pulling latest image..."
    docker pull ${IMAGE_NAME}
    echo ">> Re-running container..."
    docker rm -f dinovoyager-checker 2>/dev/null || true
    docker run -d --name dinovoyager-checker --restart unless-stopped --env-file .env ${IMAGE_NAME}
fi

echo ">> Update complete! Recent logs:"
sleep 3
docker logs --tail 15 dinovoyager-checker || true
UPDATE_EOF

chmod +x "$INSTALL_DIR/update.sh" 2>/dev/null || true

# Step 7: Pull Docker Image
echo -e "\n${YELLOW}>> Pulling Docker image: ${IMAGE_NAME}...${NC}"
docker pull "$IMAGE_NAME"

# Step 8: Start Container
echo -e "${YELLOW}>> Starting container...${NC}"
docker rm -f dinovoyager-checker >/dev/null 2>&1 || true

# Use compose if available, otherwise docker run
if docker compose version >/dev/null 2>&1; then
    docker compose -f "$INSTALL_DIR/docker-compose.yml" up -d --remove-orphans
elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "$INSTALL_DIR/docker-compose.yml" up -d --remove-orphans
else
    docker run -d \
      --name dinovoyager-checker \
      --restart unless-stopped \
      --env-file "$INSTALL_DIR/.env" \
      "$IMAGE_NAME"
fi

# Step 9: Verify Container Status
echo -e "${YELLOW}>> Verifying container health...${NC}"
sleep 3

IS_RUNNING="$(docker inspect -f '{{.State.Running}}' dinovoyager-checker 2>/dev/null || echo "false")"

# Mask API Key for display
MASKED_KEY="${API_KEY:0:12}****${API_KEY: -4}"

echo -e "\n${CYAN}=================================================================${NC}"
if [ "$IS_RUNNING" = "true" ]; then
    echo -e "${GREEN}    ✅ DinoVoyager-checker successfully installed and started!   ${NC}"
else
    echo -e "${YELLOW}    ⚠️  Container launched, but status is not 'running'.         ${NC}"
fi
echo -e "${CYAN}=================================================================${NC}"

echo -e "\n${BOLD}Deployment Summary:${NC}"
echo -e "  • Install Directory : ${CYAN}${INSTALL_DIR}${NC}"
echo -e "  • Checker Name      : ${GREEN}${CHECKER_NAME}${NC}"
echo -e "  • Region Code       : ${CYAN}${REGION_CODE}${NC}"
echo -e "  • API Endpoint      : ${CYAN}${API_URL}${NC}"
echo -e "  • API Key           : ${MASKED_KEY}"
echo -e "  • Polling Interval  : ${POLLING_INTERVAL} seconds"
echo -e "  • Container Status  : $([ "$IS_RUNNING" = "true" ] && echo -e "${GREEN}RUNNING${NC}" || echo -e "${RED}STOPPED${NC}")"

echo -e "\n${BOLD}Useful Commands:${NC}"
echo -e "  • View live logs    : ${CYAN}docker logs -f dinovoyager-checker${NC}"
echo -e "  • Update to latest  : ${CYAN}cd ${INSTALL_DIR} && ./update.sh${NC}"
echo -e "  • Stop checker      : ${CYAN}docker stop dinovoyager-checker${NC}"
echo -e "  • Start checker     : ${CYAN}docker start dinovoyager-checker${NC}"
echo -e "  • Edit config       : ${CYAN}nano ${INSTALL_DIR}/.env${NC} && ${CYAN}cd ${INSTALL_DIR} && ./update.sh${NC}"

echo -e "\n${BOLD}Recent Container Logs:${NC}"
docker logs --tail 8 dinovoyager-checker 2>&1 || true
echo -e "\n${GREEN}All done! Check your Admin Panel to see the checker reporting telemetry.${NC}"
