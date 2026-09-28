#!/usr/bin/env bash
# ============================================================================
# catalog_replay_gate.sh — the compiled-catalog replay gate
#
# THE INVARIANT
# A catalog compiled by tsp-eav-emitter in reconcile mode must be IDEMPOTENT:
# applying the generated file to a catalog that already matches it must write
# nothing. This script proves that end-to-end against a real PostgreSQL by
# applying the SAME generated file twice and failing if the second application
# mints even one revision.
#
# WHY THIS IS A SEPARATE GATE (and not just more unit tests)
# The 0008 checks in reconcile_0008_stereotype_reconcile.sql exercise
# shrapnel.stereotype_reconcile directly, with hand-written arguments. This gate
# exercises the ACTUAL COMPILED ARTIFACT — the SQL the emitter produces from a
# TypeSpec program. Those are different failure surfaces: the emitter could
# emit a wrong argument order, a wrong parent subselect, a non-topological
# order, or drop the reconcile verb entirely, and every function-level unit
# test would still pass. #591's own "known limits" entry ("re-running the
# migration creates v2 revisions") lived in exactly that gap, and this is the
# gate that would have caught it at PR time.
#
# THE ASSERTION IS ON THE OUTCOME, NOT THE VERB
# This script counts rows. It does not check which function the emitter chose.
# That matters: the gate asserts the property "re-applying mints nothing", so it
# stays honest if the emitter's verb changes, and it fails loudly today if the
# emitter is still emitting create_revision — which is the correct result, not
# a false positive.
#
# VACUITY GUARD
# A broken compiler that emits an EMPTY catalog would "pass" the replay
# assertion trivially. So the gate also asserts the first application produced
# a non-trivial catalog. A gate that cannot fail is not a gate.
#
# USAGE
#   catalog_replay_gate.sh [--keep] [--strict]
#     --keep     leave the container in place for debugging
#     --strict   fail (instead of skipping) when migration 0008 is absent
#
# Requires: docker, psql, node/npm. Exits non-zero on any failure.
# ============================================================================
set -euo pipefail

KEEP=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    --strict) STRICT=1 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
STRICT="${STRICT:-0}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SHRAPNEL_DIR="$REPO_ROOT/typescript/shrapnel"
EMITTER_DIR="$REPO_ROOT/typescript/tsp-eav-emitter"
CONTAINER="shrapnel-replay-gate"
PORT="${SHRAPNEL_REPLAY_PORT:-55444}"
DB="replay"
DSN="postgresql://pguser:pgpass@localhost:${PORT}/${DB}"

fail() { echo "REPLAY GATE FAILED: $*" >&2; exit 1; }
step() { echo; echo "── $* ──"; }

cleanup() {
  if [ "$KEEP" -eq 0 ]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  else
    echo "--keep: container $CONTAINER left running on port $PORT"
  fi
}
trap cleanup EXIT

for bin in docker psql node npm; do
  command -v "$bin" >/dev/null 2>&1 || fail "required binary not found: $bin"
done

# ── 1. Disposable postgres ────────────────────────────────────────────────
step "starting disposable postgres:17 on :$PORT"
# Pull separately and first. Otherwise a cold runner spends most of the
# readiness budget downloading the image, and the gate fails on a slow pull
# rather than on anything about the catalog.
docker pull -q postgres:17-alpine >/dev/null 2>&1 || fail "could not pull postgres:17-alpine"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" \
  -e POSTGRES_PASSWORD=pgpass -e POSTGRES_USER=pguser -e POSTGRES_DB="$DB" \
  -p "${PORT}:5432" postgres:17-alpine >/dev/null || fail "could not start the postgres container"

# 120s, not 60: first-run initdb on a loaded CI runner is genuinely slow, and a
# gate that flakes on startup is worse than no gate — it trains people to
# re-run red builds.
#
# THE PROBE IS A HOST-SIDE CONNECTION, NOT `pg_isready` INSIDE THE CONTAINER.
# The postgres entrypoint starts a TEMPORARY server to run initdb, then shuts it
# down and starts the real one. `docker exec pg_isready` reports ready against
# that temporary server, so a probe based on it can pass and then have the
# server disappear underneath the first real connection ("server closed the
# connection unexpectedly"). That is a real flake, not a theoretical one — it
# reproduced here. Connecting from the host proves the FINAL server is up and
# accepting connections on the mapped port.
READY=0
for _ in $(seq 1 120); do
  if psql "$DSN" -tAc 'SELECT 1' >/dev/null 2>&1; then READY=1; break; fi
  # A container that exited will never become ready; stop waiting on it.
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || echo false)" != "true" ]; then
    break
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  echo "--- container state ---" >&2
  docker ps -a --filter "name=$CONTAINER" >&2 || true
  echo "--- container logs ---" >&2
  docker logs "$CONTAINER" >&2 2>&1 | tail -40 || true
  fail "postgres did not become reachable on :$PORT (state dumped above)"
fi

# Assert the SERVER is PG17. This is the real version constraint, and it is
# asserted here rather than in CI because the image is what decides it.
#
# I previously wrote a CI step asserting the psql CLIENT was 17+, on the belief
# that the chain needs transaction_timeout. Both halves of that were wrong:
# transaction_timeout is not referenced anywhere in the shrapnel migrations (the
# ci-bootstrap schema is what needs it, a different chain), and a client version
# cannot gate a server GUC anyway. It also failed in practice, because
# ubuntu-latest ships psql 16 preinstalled so an "install if absent" step never
# fired. Asserting the thing that is actually load-bearing is simpler AND works.
SERVER_NUM="$(psql "$DSN" -tAc 'SHOW server_version_num' 2>/dev/null | tr -d '[:space:]')"
if [ -z "$SERVER_NUM" ]; then
  fail "could not read the server version from :$PORT"
fi
if [ "${SERVER_NUM%%00}" -lt 17 ] 2>/dev/null || [ "$SERVER_NUM" -lt 170000 ]; then
  fail "server is PostgreSQL ${SERVER_NUM} (pre-17) — this gate requires a PG17+ server"
fi
echo "  server: PostgreSQL ${SERVER_NUM}"

# ── 2. Schema + migrations ───────────────────────────────────────────────
step "applying shrapnel migrations"
psql "$DSN" -q -v ON_ERROR_STOP=1 \
  -c 'CREATE SCHEMA IF NOT EXISTS shrapnel AUTHORIZATION pguser' >/dev/null
for f in "$SHRAPNEL_DIR"/migrations/0*.sql; do
  { echo "BEGIN;"; cat "$f"; echo "COMMIT;"; } \
    | psql "$DSN" -q -v ON_ERROR_STOP=1 -f - >/dev/null \
    || fail "migration failed: $(basename "$f")"
  echo "  applied $(basename "$f")"
done

# Migration 0008 must be present, or the gate is testing nothing.
#
# WHY THIS SKIPS INSTEAD OF FAILING
# This gate is delivered alongside, but ahead of, migration 0008 (PR #623), so
# that it is already watching the moment 0008 lands. On a main that does not yet
# have 0008 there is nothing to assert, and a hard failure would be a red check
# no one can fix in this PR. So it SKIPS — loudly, with the reason, never
# silently. Set STRICT=1 to turn the skip into a failure (useful locally, and
# what CI should use once 0008 is on main).
#
# This is a real gate that is currently dormant, not a gate that has passed.
# The skip banner says so on every run so nobody reads a green tick as
# enforcement that is not happening.
if ! psql "$DSN" -tAc "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                        WHERE n.nspname='shrapnel' AND p.proname='stereotype_reconcile'" \
     | grep -q 1; then
  if [ "${STRICT:-0}" = "1" ]; then
    fail "shrapnel.stereotype_reconcile is absent — migration 0008 is required for this gate"
  fi
  echo
  echo "╔══════════════════════════════════════════════════════════════════╗"
  echo "║  REPLAY GATE SKIPPED — NOT ENFORCED                               ║"
  echo "╠══════════════════════════════════════════════════════════════════╣"
  echo "║  shrapnel.stereotype_reconcile is absent (migration 0008 missing).  ║"
  echo "║  There is no reconcile verb to replay, so nothing was asserted.   ║"
  echo "║  This gate starts enforcing automatically once 0008 lands.       ║"
  echo "║  A green result here is NOT evidence of replay-idempotence.       ║"
  echo "║  Re-run with STRICT=1 to turn this into a failure.               ║"
  echo "╚══════════════════════════════════════════════════════════════════╝"
  echo
  exit 0
fi

# The proposal migration. Applied to the THROWAWAY container ONLY.
#
# sample/main.tsp uses @ShrapnelCatalog.instanceStorage, so the compiled catalog
# contains an INSERT into shrapnel.stereotype_instance_storage. That table is
# defined in migrations-proposal/0007, which is marked "Do not apply until
# ruled" and is still pending the B1-B4 amendments. Without it the compiled
# catalog cannot be applied AT ALL, against any schema.
#
# It is applied here because this gate only ever touches a disposable container
# that is destroyed on exit — "do not apply" means do not apply it to the real
# catalog. The PROPOSAL FILE is applied rather than a hand-written shim, so the
# two cannot drift. The instance-storage registry is metadata outside the type
# contract and does not participate in the revision/fingerprint invariant this
# gate asserts, so it is out of scope here.
PROPOSAL="$EMITTER_DIR/migrations-proposal/0007_stereotype_instance_storage.sql"
[ -f "$PROPOSAL" ] || fail "expected the instance-storage proposal at $PROPOSAL"
echo "  applying migrations-proposal/0007 (throwaway container only; 'do not apply until ruled' refers to the real catalog)"
psql "$DSN" -q -v ON_ERROR_STOP=1 -f "$PROPOSAL" >/dev/null \
  || fail "the instance-storage proposal failed to apply"

# ── 3. Compile the TypeSpec sample in reconcile mode ──────────────────────
step "compiling the TypeSpec sample (reconcile mode)"
cd "$EMITTER_DIR"
npm ci --silent >/dev/null 2>&1 || npm install --silent >/dev/null 2>&1 \
  || fail "emitter npm install failed"
npm run build --silent >/dev/null 2>&1 || fail "emitter build failed"

COMPILED="$EMITTER_DIR/shrapnel-catalog-reconcile.sql"
rm -f "$COMPILED"
npx tsp compile sample/main.tsp --config sample/tspconfig-reconcile.yaml >/dev/null 2>&1 \
  || fail "tsp compile failed"
[ -s "$COMPILED" ] || fail "emitter produced no output at $COMPILED"

EMITTED_VERB="$(grep -c 'stereotype_reconcile(' "$COMPILED" || true)"
echo "  compiled catalog: $EMITTED_VERB stereotype_reconcile call(s)"
if [ "$EMITTED_VERB" -eq 0 ]; then
  fail "emitter emitted no stereotype_reconcile calls — the reconcile option is not being honoured"
fi

# ── 4. First application ─────────────────────────────────────────────────
step "applying the compiled catalog (first time)"
psql "$DSN" -q -v ON_ERROR_STOP=1 -f "$COMPILED" >/tmp/replay-run1.log 2>&1 \
  || { cat /tmp/replay-run1.log; fail "first application failed"; }

read -r R1 R1F <<<"$(psql "$DSN" -tAF' ' -c "
  SELECT (SELECT count(*) FROM shrapnel.stereotype_revision),
         (SELECT count(*) FROM shrapnel.field)")"
echo "  after run 1: revisions=$R1 fields=$R1F"

# VACUITY GUARD — without this an empty compile would pass trivially.
[ "$R1" -gt 0 ] || fail "first application produced ZERO revisions — the gate would be vacuous"

# ── 5. Second application — the assertion ────────────────────────────────
step "re-applying the SAME file (this is the assertion)"
psql "$DSN" -q -v ON_ERROR_STOP=1 -f "$COMPILED" >/tmp/replay-run2.log 2>&1 \
  || { cat /tmp/replay-run2.log; fail "second application FAILED (expected a clean no-op)"; }

read -r R2 R2F <<<"$(psql "$DSN" -tAF' ' -c "
  SELECT (SELECT count(*) FROM shrapnel.stereotype_revision),
         (SELECT count(*) FROM shrapnel.field)")"
echo "  after run 2: revisions=$R2 fields=$R2F"

RECONCILED="$(grep -c 'reconciled' /tmp/replay-run2.log || true)"
CREATED="$(grep -c 'created' /tmp/replay-run2.log || true)"
echo "  run 2 echo: $RECONCILED reconciled / $CREATED created"

if [ "$R2" -ne "$R1" ]; then
  fail "REPLAY IS NOT IDEMPOTENT: the second application minted revisions ($R1 -> $R2)"
fi
if [ "$R2F" -ne "$R1F" ]; then
  fail "REPLAY IS NOT IDEMPOTENT: the second application created field rows ($R1F -> $R2F)"
fi
if [ "$RECONCILED" -eq 0 ]; then
  fail "no stereotype reported 'reconciled' on the second run — the created/reconciled echo is not working"
fi

echo
echo "REPLAY GATE PASSED"
echo "  compiled catalog is idempotent: $R1 revision(s), unchanged across two applications"
echo "  no new field rows; $RECONCILED stereotype(s) reported reconciled"
