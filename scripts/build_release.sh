#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export UV_PROJECT_ENVIRONMENT=build/release-venv
uv sync --locked --group release
uv run --no-sync python scripts/build_release.py --target "${1:-all}"
