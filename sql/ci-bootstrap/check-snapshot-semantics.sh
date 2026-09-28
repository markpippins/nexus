#!/bin/bash
# check-snapshot-semantics.sh — run shrapnel's real-database behavioural suites
# against a database bootstrapped from sql/ci-bootstrap/nexus-ci-bootstrap.sql.
#
# Usage: check-snapshot-semantics.sh [host] [port] [user] [db] [--keep]
#        defaults: localhost 5432 pguser nexus
# Requires PGPASSWORD or .pgpass. Read-only w.r.t. `db`: a scratch database is
# created and dropped; the database passed in is only read to locate a server.
#
# ─── Why this exists ───────────────────────────────────────────────────────
#
# check-snapshot-parity.sh (PR #631) compares function BODIES. That catches a
# reverted function and nothing else. It is blind to a snapshot that has the
# right bodies but a wrong trigger, a missing constraint, a dropped column, or
# a stale default — and a schema-only artifact is exactly where those hide,
# because the object is still present and still has the right name.
#
# The shrapnel suites in typescript/shrapnel/test/*.sql already assert all of
# that behaviourally, against a real database. But they each CREATE their own
# database from the migrations chain, so they only ever validate the MIGRATIONS.
# They have never run against the SNAPSHOT. Until now the two worlds were
# disjoint: the artifact CI actually deploys was never behaviourally tested.
#
# ─── Why the .sql suites, not the .js harnesses ────────────────────────────
#
# The behavioural content lives in the .sql files; freeze.test.js and
# reconcile.test.js are wrappers that build a scratch database, replay the
# chain, then `psql -f` the .sql and parse its verdicts. The .js suites each
# create their own database and set SHRAPNEL_PG_DSN internally, so they cannot
# be pointed at an existing one. The .sql files run under plain `psql -f`
# against whatever database you name, which is exactly the seam needed here.
#
# ─── Report format, and why psql errors are NOT the signal ─────────────────
#
# These suites use the tap convention: one row per assertion into a temp `tap`
# table, printed as `ok - <label>` / `NOT OK - <label>`, then a summary row
# `total|passed|failed`. Only unaligned/tuples-only output (-At) puts those
# markers at the start of the line; psql's default aligned format indents them.
#
# Critically: the suites DELIBERATELY provoke errors. The negative paths assert
# that an INSERT is REJECTED, and that rejection surfaces as a psql ERROR line.
# So "no ERROR in the output" is not a valid assertion and would fail a
# perfectly healthy suite. The signal is the tap verdict, never stderr.
#
# ─── Suite list, with floors — explicit, not discovered ─────────────────────
#
# format: <filename>:<minimum passing checks>
#
# Explicit because (a) a floor is required — a suite reporting 0 checks would
# otherwise pass vacuously, and (b) only the tap-format suites are runnable
# here. rehearsal_0007_wr_stereotypes.sql is deliberately NOT listed: it uses
# the older convention (section headers + RAISE NOTICE, no tap table, no
# summary row) and cannot be asserted on mechanically. It stays covered by
# service-test-gates.yml. Adding a suite here is a deliberate, reviewed act.
#
# Floors are floors, not targets. Raise them when a suite grows; never lower
# one to let a failure through.
set -euo pipefail
HOST=${1:-localhost}; PORT=${2:-5432}; USER=${3:-pguser}; DB=${4:-nexus}
KEEP=0
for a in "$@"; do [ "$a" = "--keep" ] && KEEP=1; done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SNAPSHOT="$SCRIPT_DIR/nexus-ci-bootstrap.sql"
MIGRATIONS_DIR="$REPO_ROOT/typescript/shrapnel/migrations"
TEST_DIR="$REPO_ROOT/typescript/shrapnel/test"

SUITES=(
  "freeze_subtransaction_0009.sql:14"
  "reconcile_0008_stereotype_reconcile.sql:32"
)

for f in "$SNAPSHOT" "$MIGRATIONS_DIR" "$TEST_DIR"; do
  [ -e "$f" ] || { echo "ERROR: required path missing: $f" >&2; exit 1; }
done
[ -s "$SNAPSHOT" ] || { echo "ERROR: snapshot missing or empty: $SNAPSHOT" >&2; exit 1; }

PSQL="psql -h $HOST -p $PORT -U $USER -d postgres -v ON_ERROR_STOP=1 -q"
SCRATCH="cib_semantics_$$"
WORK="$(mktemp -d /tmp/cib-semantics.XXXXXX)"
cleanup() {
  [ "$KEEP" -eq 1 ] || $PSQL -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

echo "semantics check: bootstrapping $SCRATCH from the snapshot, then replaying the chain..."
$PSQL -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true
$PSQL -c "CREATE DATABASE $SCRATCH" >/dev/null
psql -h "$HOST" -p "$PORT" -U "$USER" -d "$SCRATCH" -v ON_ERROR_STOP=1 -q -o /dev/null -f "$SNAPSHOT"

# Replaying the chain is deliberate, not incidental. CI's boot smoke applies the
# snapshot alone, but the parity gate (PR #631) establishes that the chain
# changes no function body in a healthy artifact — so "snapshot + chain" is
# semantically the snapshot, AND it is the exact state in which a clobber would
# do damage. Testing this state exercises the clobber path, not just the calm one.
for f in $(ls -1 "$MIGRATIONS_DIR"/*.sql | sort); do
  PGOPTIONS='--client-min-messages=warning' \
    psql -h "$HOST" -p "$PORT" -U "$USER" -d "$SCRATCH" \
      -v ON_ERROR_STOP=1 -q -o /dev/null --single-transaction -f "$f" \
      2> >(grep -vE 'already a transaction|no transaction in progress' >&2) || {
        echo "ERROR: replaying $(basename "$f") failed" >&2; exit 1; }
done

RAN=0; FAILED_SUITES=""
for spec in "${SUITES[@]}"; do
  FILE="${spec%%:*}"; FLOOR="${spec##*:}"
  PATH_="$TEST_DIR/$FILE"
  if [ ! -f "$PATH_" ]; then
    # Not a failure: the suite lives on a PR that may not have merged yet, and
    # a branch legitimately predates it. Loud, but not red.
    echo "::warning::suite not present on this branch: $FILE (floor $FLOOR checks when it does land)"
    continue
  fi
  RAN=$((RAN+1))
  OUT="$WORK/$FILE.out"
  # ON_ERROR_STOP=0 and stderr discarded on purpose: these suites provoke errors
  # as negative-path assertions. The tap verdict is the only signal.
  psql -At -h "$HOST" -p "$PORT" -U "$USER" -d "$SCRATCH" \
    -v ON_ERROR_STOP=0 -q -f "$PATH_" > "$OUT" 2>/dev/null || true

  PASSES=$(grep -cE '^ok - ' "$OUT" || true)
  FAILS=$(grep -cE '^NOT OK - ' "$OUT" || true)
  SUMMARY=$(grep -E '^[0-9]+\|[0-9]+\|[0-9]+$' "$OUT" | tail -1 || true)
  TOTAL=""; PASSED=""; SFAILED=""
  if [ -n "$SUMMARY" ]; then
    IFS='|' read -r TOTAL PASSED SFAILED <<< "$SUMMARY"
  fi

  REASON=""
  [ -n "$SUMMARY" ] || REASON="no 'total|passed|failed' summary row — the suite did not run to completion, or it is not tap-format"
  if [ -z "$REASON" ]; then
    [ "$SFAILED" = "0" ] || REASON="$SFAILED check(s) failed"
    [ "$PASSED" = "$TOTAL" ]  || REASON="passed ($PASSED) != total ($TOTAL)"
    [ "$TOTAL" = "$((PASSES+FAILS))" ] || REASON="summary counts $TOTAL verdict lines but $((PASSES+FAILS)) were printed — the report is truncated"
    [ "$PASSES" -ge "$FLOOR" ] || REASON="only $PASSES passing check(s), floor is $FLOOR — assertions may be silently vanishing (a NULL verdict hits tap.ok NOT NULL and deletes the row instead of reporting a failure)"
  fi

  if [ -n "$REASON" ]; then
    FAILED_SUITES="$FAILED_SUITES $FILE"
    {
      echo "::error::$FILE FAILED against the snapshot: $REASON"
      echo "  failing checks:"
      grep -E '^NOT OK - ' "$OUT" | sed 's/^/    /' || echo "    (none printed — see the counts above)"
      echo "  full output: $OUT"
    } >&2
  else
    echo "  $FILE: $PASSES passed, 0 failed (floor $FLOOR)"
  fi
done

if [ "$RAN" -eq 0 ]; then
  echo "::warning::NO SUITES RAN. Both listed suites are absent from this branch, so the snapshot was NOT behaviourally tested."
  echo "::warning::This is expected while #623 (reconcile) and #628 (freeze) are unmerged — it becomes real once they land."
  echo "semantics check: 0 suites executed (nothing to assert)"
  exit 0
fi

if [ -n "$FAILED_SUITES" ]; then
  echo "::error::snapshot semantics regressed in:$FAILED_SUITES" >&2
  exit 1
fi

echo "semantics check: $RAN suite(s) green against the snapshot"
