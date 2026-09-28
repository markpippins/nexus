#!/bin/bash
# check-snapshot-parity.sh — assert that replaying the branch's shrapnel
# migration chain over sql/ci-bootstrap/nexus-ci-bootstrap.sql does not CHANGE
# any function body the snapshot already contains.
#
# Usage: check-snapshot-parity.sh [host] [port] [user] [db] [--self-test] [--keep]
#        defaults: localhost 5432 pguser nexus
#        --self-test   additionally prove the checker can actually FAIL
#        --keep        leave the scratch database behind (debugging)
# Requires PGPASSWORD or .pgpass for auth. Read-only w.r.t. `db`: the source
# database is never touched — a scratch database is created and dropped.
#
# ─── Why this exists ───────────────────────────────────────────────────────
#
# The snapshot is `pg_dump --schema-only`, so a fresh apply leaves
# shrapnel._migration_ledger EMPTY and the branch's migration chain replays
# from the first migration. Several migrations re-issue the same function:
#
#   0005_stereotype_api.sql:437          CREATE OR REPLACE shrapnel.stereotype_create_revision
#   0008_stereotype_reconcile.sql:123   CREATE OR REPLACE shrapnel.stereotype_create_revision
#   0005                                  CREATE OR REPLACE shrapnel.forbid_stereotype_field_mutation
#   0009_..._subtransaction.sql:98       CREATE OR REPLACE shrapnel.forbid_stereotype_field_mutation
#
# Last writer wins. If the snapshot was dumped from a database that is AHEAD of
# the branch, the chain replays the OLDER body last and silently reverts the
# newer migration's fix — with no error, and with every function still present.
#
# Reproduced 2026-09-28: a snapshot carrying 0008+0009, replayed over with
# main's 0001-0006 chain, restored 0005's `txid_current` arithmetic to
# forbid_stereotype_field_mutation and dropped `under_construction` — the exact
# subtransaction-freeze defect 0009 fixes. `stereotype_reconcile` survived the
# whole thing, so an existence check would have stayed green throughout.
#
# refresh.sh (PR #629) stops the mistake at the source. This is the belt to
# that braces: it verifies the committed artifact itself, on every PR.
#
# ─── Why it compares behaviourally, not textually ───────────────────────────
#
# Comparing the snapshot's text against a migration file's text does not work:
# PostgreSQL normalises whitespace into pg_proc.prosrc, and pg_dump re-emits a
# CREATE OR REPLACE wrapper, so the two strings differ for reasons unrelated to
# drift. Instead this fingerprints `md5(prosrc)` per function and compares two
# states of the SAME database — apples to apples, no normalisation issue.
#
# ─── The invariant, and the three outcomes it distinguishes ────────────────
#
#   MODIFIED  a function exists before and after with a different body.
#             => FAIL. This is the clobber signature: the chain reverted it.
#   REMOVED   a function present in the snapshot is gone after the chain.
#             => FAIL. A migration chain should never drop a snapshot function.
#   ADDED     a function exists only after. The snapshot LAGS the branch.
#             => warn, not a failure. Safe: the chain creating a missing object
#                is the normal path when the snapshot is behind.
#
# Only MODIFIED and REMOVED fail. Refusing on ADDED would make the gate red on
# every day the snapshot legitimately trails a merge — which trains people to
# ignore it, and is worse than the drift it would report.
set -euo pipefail
HOST=${1:-localhost}; PORT=${2:-5432}; USER=${3:-pguser}; DB=${4:-nexus}
SELF_TEST=0; KEEP=0
for a in "$@"; do
  case "$a" in
    --self-test) SELF_TEST=1 ;;
    --keep)      KEEP=1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SNAPSHOT="$SCRIPT_DIR/nexus-ci-bootstrap.sql"
MIGRATIONS_DIR="$REPO_ROOT/typescript/shrapnel/migrations"

# Guarded schemas are an explicit list, not name discovery — the same reasoning
# as refresh.sh's LEDGER_GUARDS. Only schemas with a real migrations/ chain and
# a real filename ledger belong here.
GUARDED_SCHEMAS=(shrapnel)

if [ ! -s "$SNAPSHOT" ]; then
  echo "ERROR: snapshot missing or empty: $SNAPSHOT" >&2
  exit 1
fi
if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "ERROR: shrapnel migrations dir missing: $MIGRATIONS_DIR" >&2
  exit 1
fi

PSQL="psql -h $HOST -p $PORT -U $USER -d postgres -v ON_ERROR_STOP=1 -q"
SCHEMA_LITERAL="$(printf "'%s'," "${GUARDED_SCHEMAS[@]}")"; SCHEMA_LITERAL="${SCHEMA_LITERAL%,}"

# md5(prosrc) per function, keyed by name + identity args so overloads stay
# distinct. Compared only against another fingerprint of the same shape, so
# PostgreSQL's whitespace normalisation in prosrc cancels out.
#
# The database name is a REQUIRED argument, not a default. An earlier revision
# of this script fingerprinted $PSQL's own `postgres` database instead of the
# scratch one and passed on 2 unrelated functions — a vacuous green that looked
# exactly like a real pass. Never let the target database be implicit here.
fingerprint() {
  local db="$1"
  # The key must be ONE line. pg_get_function_identity_arguments() happily
  # returns embedded newlines (stereotype_create_revision's seven arguments come
  # back on seven lines), which would make each fingerprint record span several
  # lines — breaking `cut -f1`, inflating the row count, and letting comm() and
  # awk compare mismatched halves of a key. Collapse every whitespace run to a
  # single space before concatenating.
  psql -h "$HOST" -p "$PORT" -U "$USER" -d "$db" -v ON_ERROR_STOP=1 -qAt -c "
    SELECT p.proname
           || '(' || regexp_replace(pg_get_function_identity_arguments(p.oid), '\s+', ' ', 'g') || ')'
           || E'\t' || md5(p.prosrc)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname IN ($SCHEMA_LITERAL)
    ORDER BY 1"
}

apply_snapshot() {
  $PSQL -c "DROP DATABASE IF EXISTS $1" >/dev/null 2>&1 || true
  $PSQL -c "CREATE DATABASE $1" >/dev/null
  # stdout to /dev/null: pg_dump emits `SELECT set_config(...)` calls that
  # would otherwise print as bare result tables. Errors still reach stderr and
  # still trip ON_ERROR_STOP.
  psql -h "$HOST" -p "$PORT" -U "$USER" -d "$1" -v ON_ERROR_STOP=1 -q -o /dev/null -f "$SNAPSHOT"
}

# Sorted, one statement stream per file — mirrors what migrate.js replays onto a
# database whose ledger is empty, which is exactly the fresh-apply case.
#
# --single-transaction is REQUIRED, not cosmetic: 0003_candidate_state_model.sql
# contains a literal `SAVEPOINT`, and PostgreSQL rejects it outside a
# transaction block. migrate.js wraps each file in BEGIN/COMMIT for the same
# reason, so this is the faithful reproduction — omitting it fails the run
# three migrations in with "SAVEPOINT can only be used in transaction blocks".
#
# --client-min-messages=warning drops the "relation already exists, skipping"
# NOTICEs that 0001's IF NOT EXISTS guards legitimately emit when the chain
# replays over a snapshot that already has the tables. They are expected here
# and are not the subject of any assertion.
apply_chain() {
  local db="$1"; shift
  local f
  for f in "$@"; do
    PGOPTIONS='--client-min-messages=warning' \
      psql -h "$HOST" -p "$PORT" -U "$USER" -d "$db" \
        -v ON_ERROR_STOP=1 -q -o /dev/null --single-transaction \
        -f "$MIGRATIONS_DIR/$f"
  done
}

# Sets MODIFIED / REMOVED / ADDED to function-key lists. Returns 0 iff there is
# no MODIFIED and no REMOVED. Operates on FILES, not on in-line strings: awk
# treats a multi-line argument as a filename, which fails obscurely.
compare() {
  local before="$1" after="$2"   # paths
  MODIFIED=$(awk -F'\t' 'NR==FNR { b[$1]=$2; next }
                           $1 in b { if (b[$1] != $2) print $1 }' "$before" "$after" | sort)
  REMOVED=$(comm -23 <(cut -f1 "$before" | sort) <(cut -f1 "$after" | sort))
  ADDED=$(comm -13 <(cut -f1 "$before" | sort) <(cut -f1 "$after" | sort))
  [ -z "$MODIFIED" ] && [ -z "$REMOVED" ]
}

SCRATCH="cib_parity_$$"
WORK="$(mktemp -d /tmp/cib-parity.XXXXXX)"
BEFORE_F="$WORK/before.tsv"; AFTER_F="$WORK/after.tsv"; AFTER2_F="$WORK/after2.tsv"
cleanup() {
  [ "$KEEP" -eq 1 ] || $PSQL -c "DROP DATABASE IF EXISTS $SCRATCH" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# Floor, not a target. shrapnel has ~20 functions on main; a fingerprint far
# below that means the guard is pointed at the wrong database or the schema
# failed to materialise, and every comparison downstream would be vacuous.
FINGERPRINT_FLOOR=10

CHAIN_FILES=()
while IFS= read -r f; do CHAIN_FILES+=("$(basename "$f")"); done < <(ls -1 "$MIGRATIONS_DIR"/*.sql | sort)

echo "parity check: ${#CHAIN_FILES[@]} migration file(s); guarded schema(s): ${GUARDED_SCHEMAS[*]}"

apply_snapshot "$SCRATCH"
fingerprint "$SCRATCH" > "$BEFORE_F"
BEFORE_N=$(grep -c . "$BEFORE_F" || true)
if [ "$BEFORE_N" -lt "$FINGERPRINT_FLOOR" ]; then
  echo "ERROR: only $BEFORE_N function(s) fingerprinted in ${SCRATCH} (floor $FINGERPRINT_FLOOR) — the comparison would be vacuous." >&2
  echo "       This is what a wrong-database or failed-bootstrap looks like, not a pass." >&2
  exit 1
fi

apply_chain "$SCRATCH" "${CHAIN_FILES[@]}"
fingerprint "$SCRATCH" > "$AFTER_F"

if ! compare "$BEFORE_F" "$AFTER_F"; then
  {
    echo "::error::CLOBBERED SNAPSHOT — replaying the branch's migration chain over sql/ci-bootstrap/nexus-ci-bootstrap.sql CHANGED existing function bodies."
    if [ -n "$MODIFIED" ]; then
      echo ""
      echo "MODIFIED by the chain (it re-issued an older body — the snapshot is ahead of, or stale relative to, the branch):"
      printf '%s\n' "$MODIFIED" | sed 's/^/  /'
    fi
    if [ -n "$REMOVED" ]; then
      echo ""
      echo "REMOVED by the chain (a snapshot function disappeared — never expected):"
      printf '%s\n' "$REMOVED" | sed 's/^/  /'
    fi
    echo ""
    echo "A function can be reverted in place: stereotype_reconcile survived this exact clobber on 2026-09-28, so an existence check would not have caught it."
    echo "Fix: re-generate the snapshot from a database that is not ahead of the branch (sql/ci-bootstrap/refresh.sh), then re-run."
  } >&2
  exit 1
fi

echo "parity check: $BEFORE_N function(s) fingerprinted (floor $FINGERPRINT_FLOOR); 0 modified, 0 removed by the chain."
if [ -n "$ADDED" ]; then
  echo "::warning::snapshot LAGS the branch — the chain created $(printf '%s\n' "$ADDED" | grep -c .) function(s) absent from the snapshot. Safe, but the artifact is behind:"
  printf '%s\n' "$ADDED" | sed 's/^/  /'
fi

# ─── Non-vacuity: prove the checker can actually fail ───────────────────────
# A gate that cannot go red is worse than no gate. Replay the EARLIEST
# migration that defines a function some LATER migration also defines — the
# exact shape of the 2026-09-28 clobber — and require the comparison to catch
# it. This walks the corpus rather than hard-coding function names, because the
# set of re-issued functions changes as migrations land.
#
# Note it must be a genuinely re-issued function. Replaying a migration whose
# definitions are still the LATEST in the chain is a true no-op, and demanding
# that it be "detected" would be demanding a false positive. On main that pair
# is set_updated_at (0001 then 0006); stereotype_create_revision and
# forbid_stereotype_field_mutation do not qualify until 0008/0009 land.
if [ "$SELF_TEST" -eq 1 ]; then
  echo ""
  echo "self-test: confirming the checker can detect a clobber..."
  DEFMAP="$WORK/defmap.tsv"
  : > "$DEFMAP"
  for f in "${CHAIN_FILES[@]}"; do
    # `grep` returns 1 when a migration defines no functions, and this script
    # runs under `set -o pipefail` — so an unguarded `grep | sed | while`
    # pipeline aborts the whole script on the first such file, silently, with
    # no diagnostic. Capture first, then iterate.
    fns=$(grep -oE 'CREATE( OR REPLACE)? FUNCTION shrapnel\.[a-zA-Z0-9_]+' \
            "$MIGRATIONS_DIR/$f" || true)
    while IFS= read -r fn; do
      if [ -n "$fn" ]; then
        printf '%s %s\n' "$fn" "$f" >> "$DEFMAP"
      fi
    done <<< "$(printf '%s\n' "$fns" | sed -E 's/.*FUNCTION //')"
  done
  # Candidate regressors: files defining a function some later file also
  # defines. "Redefined" is NOT the same as "redefined DIFFERENTLY" — on main,
  # 0006 re-issues set_updated_at with a byte-identical body, so replaying
  # 0001 is a true no-op and must NOT be flagged. Determining which candidates
  # actually change something is done empirically below, on a clone, rather
  # than guessed from the text.
  CANDIDATES=$(awk '{ if (!($1 in first)) first[$1]=$2; last[$1]=$2 }
                    END { for (k in first) if (first[k] != last[k]) print first[k] }' \
                    "$DEFMAP" | sort -u | tr '\n' ' ')
  if [ -n "$CANDIDATES" ]; then
    echo "  chain has re-issued functions (candidates: $CANDIDATES)"
  else
    echo "  chain currently has no re-issued functions; detection is proven synthetically below"
  fi

  # Prove the detection path by mutating a REAL function body in a clone of the
  # post-chain state. This is deliberately not "replay an older migration":
  #   * replaying 0001 is a genuine no-op (0006 re-issues set_updated_at with a
  #     byte-identical body), so demanding it be flagged would demand a false
  #     positive; and
  #   * replaying 0004 does not merely no-op, it ERRORS — 0005's freeze trigger
  #     rejects 0004's own re-INSERT ("stereotype_field rows for revision 1 are
  #     frozen"), so whole-file replay is the wrong instrument for testing body
  #     comparison.
  # Injecting a comment just inside the first dollar-quote delimiter changes
  # prosrc without changing behaviour, which is exactly the shape of change a
  # clobber produces.
  CAND_DB="$SCRATCH.selftest"
  $PSQL -c "DROP DATABASE IF EXISTS \"$CAND_DB\"" >/dev/null 2>&1 || true
  $PSQL -c "CREATE DATABASE \"$CAND_DB\" TEMPLATE $SCRATCH" >/dev/null
  psql -h "$HOST" -p "$PORT" -U "$USER" -d "$CAND_DB" -v ON_ERROR_STOP=1 -q -o /dev/null <<SQL
DO \$cib\$
DECLARE
  v_name text;
  v_def  text;
  v_new  text;
BEGIN
  SELECT p.proname INTO v_name
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname IN ($SCHEMA_LITERAL) AND p.prokind = 'f'
     AND p.prosrc IS NOT NULL AND p.prosrc <> ''
   ORDER BY p.proname LIMIT 1;
  IF v_name IS NULL THEN
    RAISE EXCEPTION 'no function available to mutate in the guarded schemas';
  END IF;
  SELECT pg_get_functiondef(p.oid) INTO v_def
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
   WHERE n.nspname IN ($SCHEMA_LITERAL) AND p.proname = v_name AND p.prokind = 'f'
   LIMIT 1;
  -- Put a comment just inside the body's first dollar-quote delimiter.
  v_new := regexp_replace(v_def, '(\\\$[a-zA-Z_]*\\\$)', '\1 -- cib-parity-selftest', '');
  IF v_new = v_def THEN
    RAISE EXCEPTION 'could not inject a marker into %', v_name;
  END IF;
  EXECUTE v_new;
  RAISE NOTICE 'mutated %', v_name;
END
\$cib\$;
SQL
  fingerprint "$CAND_DB" > "$WORK/cand.tsv"
  $PSQL -c "DROP DATABASE IF EXISTS \"$CAND_DB\"" >/dev/null 2>&1 || true

  if compare "$AFTER_F" "$WORK/cand.tsv"; then
    echo "ERROR: SELF-TEST FAILED — mutating a real function body produced no detectable change." >&2
    echo "       This check cannot detect a clobber and must not be trusted. Do not" >&2
    echo "       let this gate go green by default." >&2
    exit 1
  fi
  echo "self-test: OK — detected $(printf '%s\n' "$MODIFIED" | grep -c . || true) modified, $(printf '%s\n' "$REMOVED" | grep -c . || true) removed. The check can fail."
fi

echo "snapshot parity: PASS"
