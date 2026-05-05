#!/usr/bin/env bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

git -C "$PROJECT_DIR" config core.hooksPath .githooks
chmod +x "$PROJECT_DIR/.githooks/post-commit"

echo "Git hooks installed from $PROJECT_DIR/.githooks"
