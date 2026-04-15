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

mkdir -p "$PROJECT_DIR/.deploy"

python3 -m py_compile "$PROJECT_DIR"/app/*.py

RSYNC_RSH=(ssh -i "$UNRAID_SSH_KEY" -p "$UNRAID_PORT" -o StrictHostKeyChecking=no)

ssh -i "$UNRAID_SSH_KEY" -p "$UNRAID_PORT" -o StrictHostKeyChecking=no "$UNRAID_USER@$UNRAID_HOST" \
  "mkdir -p '$UNRAID_APPDATA_DIR/data' '$UNRAID_COMPOSE_PROJECT_DIR'"

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
      - ${UNRAID_APPDATA_DIR}/.env
    volumes:
      - ${UNRAID_APPDATA_DIR}/data:/app/data
EOF

ssh -i "$UNRAID_SSH_KEY" -p "$UNRAID_PORT" -o StrictHostKeyChecking=no "$UNRAID_USER@$UNRAID_HOST" \
  "cat > '$UNRAID_COMPOSE_PROJECT_DIR/$UNRAID_COMPOSE_FILE_NAME' <<'EOF'
$REMOTE_COMPOSE
EOF
printf '%s' '$UNRAID_PROJECT_NAME' > '$UNRAID_COMPOSE_PROJECT_DIR/name'
printf 'true' > '$UNRAID_COMPOSE_PROJECT_DIR/autostart'
rm -f '$UNRAID_COMPOSE_PROJECT_DIR/compose.yaml'
docker compose -f '$UNRAID_COMPOSE_PROJECT_DIR/$UNRAID_COMPOSE_FILE_NAME' up -d --build
docker ps --filter name=qbit-telegram-bot --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
"
