#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
method="${1:-uv}"
case "$method" in
  uv)
    command -v uv >/dev/null || { echo "Install uv first, or run: bash scripts/setup_env.sh pip python3.10" >&2; exit 1; }
    uv sync --locked
    ;;
  pip)
    python_bin="${2:-python3}"
    "$python_bin" -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14), 'Python 3.10-3.13 required (Ubuntu 20.04 system Python 3.8 is unsupported)'"
    "$python_bin" -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    ;;
  *) echo "Usage: bash scripts/setup_env.sh [uv|pip [python3.10]]" >&2; exit 2 ;;
esac
echo "Ready. Run: uv run --no-sync python run.py OR .venv/bin/python run.py"
