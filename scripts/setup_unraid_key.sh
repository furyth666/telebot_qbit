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

PASSWORD="${1:-}"

if [[ -z "$PASSWORD" ]]; then
  echo "Usage: $0 '<unraid-ssh-password>'"
  exit 1
fi

mkdir -p "$(dirname "$UNRAID_SSH_KEY")"

if [[ ! -f "$UNRAID_SSH_KEY" ]]; then
  ssh-keygen -t ed25519 -f "$UNRAID_SSH_KEY" -N "" -C "qbit-bot-unraid"
fi

PUBKEY_CONTENT="$(cat "${UNRAID_SSH_KEY}.pub")"

expect <<EOF
set timeout 30
spawn ssh -o StrictHostKeyChecking=no -p $UNRAID_PORT $UNRAID_USER@$UNRAID_HOST {mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && grep -qxF "$PUBKEY_CONTENT" ~/.ssh/authorized_keys || echo "$PUBKEY_CONTENT" >> ~/.ssh/authorized_keys}
expect {
  "password:" { send "$PASSWORD\r" }
}
expect eof
EOF

ssh -i "$UNRAID_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=no -p "$UNRAID_PORT" "$UNRAID_USER@$UNRAID_HOST" "echo ssh-key-ok"
echo "SSH key setup complete."
