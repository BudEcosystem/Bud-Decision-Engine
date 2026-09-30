#!/usr/bin/env bash
# One-step setup from a source checkout (the desktop app does the same thing with buttons). It detects this computer's
# processors, asks where models should run, then installs the matching PyTorch build and every model library into
# ./.venv. Safe to run again, including to move models to a different processor.
#   ./install.sh                  ask where to run
#   ./install.sh --device cpu     no questions (cuda, mps, xpu, rocm or cpu)
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v uv >/dev/null; then
  echo "Installing uv, the Python package manager the studio uses..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
exec uv run --no-project --python 3.12 installer/engine.py setup "$@"
