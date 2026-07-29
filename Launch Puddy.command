#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -d ".venv" ]]; then
  source ".venv/bin/activate"
fi

export PUDDY_BACKEND=desktop
exec python3 puddy.py
