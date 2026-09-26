#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export SERVICE_PORT="${SERVICE_PORT:-4420}"
export HARNESS_PORT="${HARNESS_PORT:-3420}"

npm run build
node_modules/.bin/moleculer-runner --config moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/harness.service.js > /tmp/moleculer-harness-canary.log 2>&1 &
twin_pid=$!
trap 'kill "$twin_pid" 2>/dev/null || true; wait "$twin_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  if curl -fsS "http://localhost:${SERVICE_PORT}/health" >/dev/null; then
    python3 tools/canary-diff.py
    exit $?
  fi
  if ! kill -0 "$twin_pid" 2>/dev/null; then
    cat /tmp/moleculer-harness-canary.log
    exit 1
  fi
  sleep 0.5
done

echo "twin did not become healthy on :${SERVICE_PORT}" >&2
cat /tmp/moleculer-harness-canary.log >&2
exit 1
