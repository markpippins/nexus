#!/usr/bin/env bash
# Canary runner for the WIND twin: boots the standalone broker on :4118,
# waits for health, runs tools/canary-diff.py against the live incumbent
# on :3300, then tears the twin down.
#
# Usage: SERVICE_PORT=4118 bash tools/canary-run.sh
# (moleculer.config.standalone.js lives at the twin root — fleet convention;
#  point MOLECULER_RUNNER at a moleculer-runner binary when npm install
#  hasn't run locally.)
set -euo pipefail

PORT="${SERVICE_PORT:-4118}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="${MOLECULER_RUNNER:-$DIR/node_modules/.bin/moleculer-runner}"

cd "$DIR"

if [ ! -f dist/services/api.service.js ] || [ ! -f dist/services/wind.service.js ]; then
  echo "dist/services/{api,wind}.service.js missing — run: npm run build" >&2
  exit 1
fi

LOG="${CANARY_LOG:-/tmp/moleculer-wind-canary.log}"

echo "[canary] booting wind twin on :$PORT (standalone broker)"
SERVICE_PORT="$PORT" "$RUNNER" --config moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/wind.service.js > "$LOG" 2>&1 &
TWIN_PID=$!

cleanup() {
  echo "[canary] tearing down twin (pid $TWIN_PID)"
  kill "$TWIN_PID" 2>/dev/null || true
  wait "$TWIN_PID" 2>/dev/null || true
}
trap cleanup EXIT

for i in $(seq 1 40); do
  if curl -sf --max-time 2 "http://localhost:$PORT/health" >/dev/null 2>&1; then
    echo "[canary] twin healthy after ${i}s"
    break
  fi
  if ! kill -0 "$TWIN_PID" 2>/dev/null; then
    echo "[canary] twin process died during boot — log:" >&2
    tail -30 "$LOG" >&2
    exit 1
  fi
  sleep 1
done

if ! curl -sf --max-time 2 "http://localhost:$PORT/health" >/dev/null 2>&1; then
  echo "[canary] twin never became healthy on :$PORT — log:" >&2
  tail -30 "$LOG" >&2
  exit 1
fi

echo "[canary] running canary-diff vs incumbent on :${CANARY_BASE:-http://localhost:3300}"
CANARY_BASE="${CANARY_BASE:-http://localhost:3300}" \
TWIN_BASE="http://localhost:$PORT" \
  python3 "$DIR/tools/canary-diff.py"
