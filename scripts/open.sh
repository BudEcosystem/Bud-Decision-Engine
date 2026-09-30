#!/usr/bin/env bash
# Start Bud Decision Studio in the background if it isn't running, then open it in the browser.
cd "$(dirname "$0")/.."
PORT="${BASAL_PORT:-8420}"
if ! curl -s -o /dev/null "http://127.0.0.1:$PORT/api/state"; then
  mkdir -p data/logs
  nohup ./run.sh --port "$PORT" > data/logs/server.log 2>&1 &
  for _ in $(seq 1 60); do curl -s -o /dev/null "http://127.0.0.1:$PORT/api/state" && break; sleep 0.5; done
fi
xdg-open "http://localhost:$PORT" >/dev/null 2>&1 || echo "Open http://localhost:$PORT in your browser."
