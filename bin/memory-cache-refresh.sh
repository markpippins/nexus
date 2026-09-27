#!/usr/bin/env bash
# memory-cache-refresh.sh — repopulate the PG→Redis memory caches.
#
# Why this exists: typescript/role-memory-srv (:3500) and
# typescript/tackle-prompt-sync-srv (:3501) are EVENT-DRIVEN, not polling.
# They sync on a Redis "ready" event and on an explicit POST /refresh. On a
# quiet system nothing re-syncs them, which is how :3500 was found 12.5h stale
# while fully populated (57 procedures, 24 role indices, Redis connected) and
# reporting "degraded".
#
# :3500 computes staleness against STALE_THRESHOLD_MS (1h) and reports
# "degraded" once the cache is older than that. :3501 does NOT compute
# staleness at all, so it silently serves old personas indefinitely — that is
# the more dangerous of the two, and the reason both are refreshed here.
#
# Both endpoints are idempotent full PG→Redis resyncs. URLs are overridable so
# the hermetic test in bin/tests/test_memory_cache_refresh.py can drive this
# script against a stubbed curl without touching the network or the real cache.
#
# Exit: 0 = both refreshed; 1 = at least one failed (details to stderr/journal).

set -uo pipefail

PROCEDURE_URL="${MEMORY_PROCEDURE_URL:-http://localhost:3500/refresh}"
PROMPT_URL="${MEMORY_PROMPT_URL:-http://localhost:3501/refresh}"
CURL_TIMEOUT="${MEMORY_CURL_TIMEOUT:-30}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }
rc=0

refresh() {
  local name="$1" url="$2" out status
  if out="$(curl -sS -f --max-time "$CURL_TIMEOUT" -X POST "$url" 2>&1)"; then
    printf '%s ok   %-18s %s\n' "$(ts)" "$name" "${out:0:200}"
    return 0
  fi
  status=$?
  printf '%s FAIL %-18s %s (curl exit %s)\n' "$(ts)" "$name" "${out:0:200}" "$status" >&2
  return 1
}

# Both are attempted even if the first fails, so one dead service still leaves
# the other freshly synced and the journal shows which one is actually at fault.
refresh "procedure-registry" "$PROCEDURE_URL" || rc=1
refresh "prompt-bridge"       "$PROMPT_URL"       || rc=1

exit "$rc"
