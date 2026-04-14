#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_ENV_FILE="${DEPLOY_ENV_FILE:-$PROJECT_DIR/.deploy/unraid.env}"
DOCKERHUB_ENV_FILE="${DOCKERHUB_ENV_FILE:-$PROJECT_DIR/.deploy/dockerhub.env}"

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

if [[ -f "$DOCKERHUB_ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$DOCKERHUB_ENV_FILE"
fi

UNRAID_APPDATA_DIR="${UNRAID_APPDATA_DIR:-${UNRAID_REMOTE_DIR:-/mnt/user/appdata/qbit-telegram-bot}}"
UNRAID_COMPOSE_PROJECT_DIR="${UNRAID_COMPOSE_PROJECT_DIR:-/boot/config/plugins/compose.manager/projects/qbit-telegram-bot}"
UNRAID_COMPOSE_FILE_NAME="${UNRAID_COMPOSE_FILE_NAME:-compose.yaml}"

if [[ -n "${UNRAID_DOCKER_IMAGE_REPO:-}" ]]; then
  IMAGE_REPO="$UNRAID_DOCKER_IMAGE_REPO"
elif [[ -n "${DOCKERHUB_USERNAME:-}" ]]; then
  IMAGE_REPO="${DOCKERHUB_USERNAME}/${DOCKERHUB_IMAGE_NAME:-qbit-telegram-bot}"
else
  echo "Missing image repository. Set UNRAID_DOCKER_IMAGE_REPO or configure .deploy/dockerhub.env."
  exit 1
fi

IMAGE_TAG="${UNRAID_DOCKER_IMAGE_TAG:-$(git -C "$PROJECT_DIR" rev-parse --short HEAD)}"

mkdir -p "$PROJECT_DIR/.deploy"

python3 -m py_compile "$PROJECT_DIR"/app/*.py

RSYNC_RSH=(ssh -i "$UNRAID_SSH_KEY" -p "$UNRAID_PORT" -o StrictHostKeyChecking=no)

ssh -i "$UNRAID_SSH_KEY" -p "$UNRAID_PORT" -o StrictHostKeyChecking=no "$UNRAID_USER@$UNRAID_HOST" \
  "mkdir -p '$UNRAID_APPDATA_DIR/data' '$UNRAID_COMPOSE_PROJECT_DIR'"

rsync -az \
  --exclude ".git/" \
  --exclude ".deploy/" \
  -e "${RSYNC_RSH[*]}" \
  "$PROJECT_DIR/README.md" \
  "$PROJECT_DIR/.env.example" \
  "$UNRAID_USER@$UNRAID_HOST:$UNRAID_APPDATA_DIR/"

read -r -d '' REMOTE_COMPOSE <<EOF || true
services:
  qbit-telegram-bot:
    image: ${IMAGE_REPO}:${IMAGE_TAG}
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
docker compose -f '$UNRAID_COMPOSE_PROJECT_DIR/$UNRAID_COMPOSE_FILE_NAME' pull
docker compose -f '$UNRAID_COMPOSE_PROJECT_DIR/$UNRAID_COMPOSE_FILE_NAME' up -d
docker ps --filter name=qbit-telegram-bot --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
"
