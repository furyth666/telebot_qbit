#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERHUB_ENV_FILE="${DOCKERHUB_ENV_FILE:-$PROJECT_DIR/.deploy/dockerhub.env}"

if [[ ! -f "$DOCKERHUB_ENV_FILE" ]]; then
  echo "Skipping Docker Hub publish: missing $DOCKERHUB_ENV_FILE"
  exit 0
fi

# shellcheck disable=SC1090
source "$DOCKERHUB_ENV_FILE"

: "${DOCKERHUB_USERNAME:?DOCKERHUB_USERNAME is required}"
: "${DOCKERHUB_TOKEN:?DOCKERHUB_TOKEN is required}"

IMAGE_NAME="${DOCKERHUB_IMAGE_NAME:-qbit-telegram-bot}"
IMAGE_REPO="${DOCKERHUB_USERNAME}/${IMAGE_NAME}"
IMAGE_TAG="${DOCKERHUB_IMAGE_TAG:-$(git -C "$PROJECT_DIR" rev-parse --short HEAD)}"
LATEST_TAG="${DOCKERHUB_LATEST_TAG:-latest}"

if [[ -n "${DOCKERHUB_HTTP_PROXY:-}" ]]; then
  export http_proxy="$DOCKERHUB_HTTP_PROXY"
fi

if [[ -n "${DOCKERHUB_HTTPS_PROXY:-}" ]]; then
  export https_proxy="$DOCKERHUB_HTTPS_PROXY"
fi

if [[ -n "${DOCKERHUB_ALL_PROXY:-}" ]]; then
  export all_proxy="$DOCKERHUB_ALL_PROXY"
fi

printf '%s' "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USERNAME" --password-stdin

docker build \
  -t "${IMAGE_REPO}:${LATEST_TAG}" \
  -t "${IMAGE_REPO}:${IMAGE_TAG}" \
  "$PROJECT_DIR"

docker push "${IMAGE_REPO}:${LATEST_TAG}"
docker push "${IMAGE_REPO}:${IMAGE_TAG}"

echo "Published ${IMAGE_REPO}:${LATEST_TAG}"
echo "Published ${IMAGE_REPO}:${IMAGE_TAG}"
