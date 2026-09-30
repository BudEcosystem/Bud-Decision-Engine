#!/usr/bin/env bash
# Starts Bud Decision Studio. Options: --port 8420 (default), --host 0.0.0.0 to allow other machines (set BASAL_API_KEY).
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./install.sh first."; exit 1; }
exec .venv/bin/python -m basal.server "$@"
