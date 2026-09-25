#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export SERVICE_PORT="${SERVICE_PORT:-4110}"
export PORT="${PORT:-3110}"

npm run build
node_modules/.bin/moleculer-runner --config moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/execution.service.js > /tmp/moleculer-execution-canary.log 2>&1 &
twin_pid=$!
trap 'kill "$twin_pid" 2>/dev/null || true; wait "$twin_pid" 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  if curl -fsS "http://localhost:${SERVICE_PORT}/health" >/dev/null; then
    python3 tools/canary-diff.py
    exit $?
  fi
  if ! kill -0 "$twin_pid" 2>/dev/null; then
    cat /tmp/moleculer-execution-canary.log
    exit 1
  fi
  sleep 0.5
done

echo "twin did not become healthy on :${SERVICE_PORT}" >&2
cat /tmp/moleculer-execution-canary.log >&2
exit 1
