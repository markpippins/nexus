#!/bin/bash
# Self-contained canary: boot twin, byte-diff vs incumbent, teardown.
# Usage: bash moleculer/prompt-sync/tools/canary-run.sh   (from worktree root)
set -x
cd "$(dirname "$0")/.."
npx tsc
node node_modules/.bin/moleculer-runner --config moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/prompt-sync.service.js </dev/null >/tmp/ps-twin-live.log 2>&1 &
TWIN_PID=$!
echo "twin pid: $TWIN_PID" > /tmp/ps-canary-out.txt
for i in $(seq 1 30); do
  sleep 1
  if curl -s --max-time 2 http://localhost:4501/health > /dev/null 2>&1; then
    echo "twin UP after ${i}s" >> /tmp/ps-canary-out.txt
    break
  fi
done
python3 tools/canary-diff.py >> /tmp/ps-canary-out.txt 2>&1
CANARY=$?
kill $TWIN_PID 2>/dev/null
echo "canary exit: $CANARY" >> /tmp/ps-canary-out.txt
exit $CANARY
