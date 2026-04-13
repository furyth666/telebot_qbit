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
: "${UNRAID_REMOTE_DIR:?UNRAID_REMOTE_DIR is required}"
: "${UNRAID_SSH_KEY:?UNRAID_SSH_KEY is required}"

mkdir -p "$PROJECT_DIR/.deploy"

python3 -m py_compile "$PROJECT_DIR"/app/*.py

RSYNC_RSH=(ssh -i "$UNRAID_SSH_KEY" -p "$UNRAID_PORT" -o StrictHostKeyChecking=no)

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
  "$PROJECT_DIR"/ "$UNRAID_USER@$UNRAID_HOST:$UNRAID_REMOTE_DIR/"

ssh -i "$UNRAID_SSH_KEY" -p "$UNRAID_PORT" -o StrictHostKeyChecking=no "$UNRAID_USER@$UNRAID_HOST" \
  "cd '$UNRAID_REMOTE_DIR' && DOCKER_BUILDKIT=0 docker compose up -d --build && docker ps --filter name=qbit-telegram-bot --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'"
