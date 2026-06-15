#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-$PROJECT_DIR/.deploy/unraid.env}"

if [[ ! -f "$DEPLOY_ENV_FILE" ]]; then
  echo "Missing deploy config: $DEPLOY_ENV_FILE"
  echo "Copy $PROJECT_DIR/.deploy.example.env to .deploy/unraid.env and fill it in."
  exit 1
fi

# shellcheck disable=SC1090
source "$DEPLOY_ENV_FILE"

: "${UNRAID_HOST:?UNRAID_HOST is required}"
: "${UNRAID_PORT:?UNRAID_PORT is required}"
: "${UNRAID_USER:?UNRAID_USER is required}"
: "${UNRAID_SSH_KEY:?UNRAID_SSH_KEY is required}"

UNRAID_APPDATA_DIR="${UNRAID_APPDATA_DIR:-${UNRAID_REMOTE_DIR:-/mnt/user/appdata/qbit-telegram-bot}}"
UNRAID_COMPOSE_PROJECT_DIR="${UNRAID_COMPOSE_PROJECT_DIR:-/boot/config/plugins/compose.manager/projects/qbit-telegram-bot}"
UNRAID_COMPOSE_FILE_NAME="${UNRAID_COMPOSE_FILE_NAME:-docker-compose.yml}"
UNRAID_PROJECT_NAME="${UNRAID_PROJECT_NAME:-qbit-telegram-bot}"
UNRAID_COMPOSE_ENV_FILE="${UNRAID_COMPOSE_ENV_FILE:-$UNRAID_COMPOSE_PROJECT_DIR/.env}"
UNRAID_HTTP_PROXY="${UNRAID_HTTP_PROXY:-}"
UNRAID_HTTPS_PROXY="${UNRAID_HTTPS_PROXY:-}"
UNRAID_NO_PROXY="${UNRAID_NO_PROXY:-localhost,127.0.0.1}"
UNRAID_HOST_NETWORK_ACK="${UNRAID_HOST_NETWORK_ACK:-}"

if [[ "$UNRAID_HOST_NETWORK_ACK" != "I_UNDERSTAND_HOST_NETWORK_IS_INTENTIONAL" ]]; then
  cat >&2 <<'EOF'
unRAID deployment uses Docker host networking intentionally.

Set this in .deploy/unraid.env after reviewing the deployment notes:

  UNRAID_HOST_NETWORK_ACK=I_UNDERSTAND_HOST_NETWORK_IS_INTENTIONAL

Host networking is kept for unRAID compatibility with local qBittorrent,
Cloudflare Tunnel, and host-side proxy access. Do not use this script for
untrusted multi-tenant hosts without changing the compose network model.
EOF
  exit 1
fi

mkdir -p "$PROJECT_DIR/.deploy"

python3 -m py_compile "$PROJECT_DIR"/app/*.py

SSH_OPTS=(
  -F /dev/null
  -i "$UNRAID_SSH_KEY"
  -p "$UNRAID_PORT"
  -o StrictHostKeyChecking=no
  -o IdentitiesOnly=yes
  -o PreferredAuthentications=publickey
)
RSYNC_RSH=(ssh "${SSH_OPTS[@]}")

ssh "${SSH_OPTS[@]}" "$UNRAID_USER@$UNRAID_HOST" \
  "mkdir -p '$UNRAID_APPDATA_DIR/data' '$UNRAID_COMPOSE_PROJECT_DIR'
if [ -f '$UNRAID_APPDATA_DIR/.env' ]; then
  cp '$UNRAID_APPDATA_DIR/.env' '$UNRAID_COMPOSE_ENV_FILE'
  mv '$UNRAID_APPDATA_DIR/.env' '$UNRAID_APPDATA_DIR/.env.moved_to_compose'
fi"

rsync -az --delete \
  --filter "protect .env" \
  --filter "protect data/" \
  --exclude ".git/" \
  --exclude ".deploy/" \
  --exclude ".env" \
  --exclude "data/" \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  -e "${RSYNC_RSH[*]}" \
  "$PROJECT_DIR"/ "$UNRAID_USER@$UNRAID_HOST:$UNRAID_APPDATA_DIR/"

read -r -d '' REMOTE_COMPOSE <<EOF || true
name: ${UNRAID_PROJECT_NAME}

services:
  qbit-telegram-bot:
    build: ${UNRAID_APPDATA_DIR}
    container_name: qbit-telegram-bot
    restart: unless-stopped
    network_mode: host
    env_file:
      - ${UNRAID_COMPOSE_ENV_FILE}
    environment:
      - HTTP_PROXY=${UNRAID_HTTP_PROXY}
      - HTTPS_PROXY=${UNRAID_HTTPS_PROXY}
      - NO_PROXY=${UNRAID_NO_PROXY}
    volumes:
      - ${UNRAID_APPDATA_DIR}/data:/app/data
    healthcheck:
      test: ["CMD-SHELL", "test \"\$TELEGRAM_MODE\" != \"webhook\" || python -c \"import os, socket; port=int(os.environ.get('WEBHOOK_LISTEN_PORT', '8099')); s=socket.create_connection(('127.0.0.1', port), 3); s.close()\""]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
EOF

ssh "${SSH_OPTS[@]}" "$UNRAID_USER@$UNRAID_HOST" \
  "cat > '$UNRAID_COMPOSE_PROJECT_DIR/$UNRAID_COMPOSE_FILE_NAME' <<'EOF'
$REMOTE_COMPOSE
EOF
printf '%s' '$UNRAID_PROJECT_NAME' > '$UNRAID_COMPOSE_PROJECT_DIR/name'
printf 'true' > '$UNRAID_COMPOSE_PROJECT_DIR/autostart'
rm -f '$UNRAID_COMPOSE_PROJECT_DIR/compose.yaml'
docker rm -f qbit-telegram-bot >/dev/null 2>&1 || true
docker compose -f '$UNRAID_COMPOSE_PROJECT_DIR/$UNRAID_COMPOSE_FILE_NAME' up -d --build
docker ps --filter name=qbit-telegram-bot --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
"
