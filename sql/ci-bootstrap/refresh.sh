#!/bin/bash
# Regenerate the CI bootstrap from a live (prod or replica) nexus database.
# Usage: refresh.sh [host] [port] [user] [db]   (defaults: localhost 5432 pguser nexus)
# Requires PGPASSWORD or .pgpass for auth.
#
# Dumps COMPLETE schemas (not a hand-picked table list) so that every object
# referenced by conduit's migration chain (e.g. nebula.requirements,
# nebula.plans view chain, vision.check_receipt_integrity) is present. The
# dump is schema-only (no data), so CI cost is negligible.
#
# Also computes the public-function closure: any public-schema function
# referenced by a dumped view or trigger (transitively) is emitted as its
# definition, so trigger functions like public.notify_member_expired() come
# along automatically.
#
# pgvector is REQUIRED (as of 2026-11-03). Vector objects used to be excluded on
# the rationale "nothing in the tested code paths reads them" — that premise was
# false: conduit-mcp/src/steward-recall.ts calls steward.semantic_search(), which
# takes public.vector, and steward.evidence_links joins into it. Excluding the
# vector TABLES also does not compose: functions keep the vector type in their
# signature, and views over an excluded table dangle (knowledge.v_graph_summary
# reads knowledge.graph_entity_embeddings). Partial exclusion produced three
# different apply failures in sequence. So the snapshot now carries every object
# and the CI image must provide the extension: pgvector/pgvector:pg17 (an
# accepted pin — bin/lint_postgres_pins.py recognises it).
#
# Born-clean seed: the ratified aspects vocabulary seed (sql/seeds/) is
# embedded verbatim at the END of the snapshot (after the schema body), so
# fresh databases carry the governed tag vocabulary from day one (PR #491
# data). Refresh refuses to embed a seed file that is missing or lacks its
# fail-closed assertion block.
set -euo pipefail
HOST=${1:-localhost}; PORT=${2:-5432}; USER=${3:-pguser}; DB=${4:-nexus}
OUT="$(dirname "$0")/nexus-ci-bootstrap.sql"
BODY="$(mktemp /tmp/ci-bootstrap-body.XXXXXX)"
trap 'rm -f "$BODY"' EXIT

# The schema set is driven by what RUNTIME SQL REFERENCES, not by what happens to
# sit in the source DB. Provenance for the second group (added 2026-11-03): scan
# runtime code (excluding tests/dist/node_modules) for FROM|JOIN|INTO|UPDATE <schema>.
# and require every referenced schema to be present. Without this, a missing schema
# is invisible until a suite 500s against a fresh DB, and the 500 reads as a code
# defect — that is exactly how throttler was found (Adonis /api/links).
#
# Group 1 (original closure) + Group 2 (code-referenced, were missing):
SCHEMAS=(execution vision nebula conduit semantics terrain peb registry resolution wind cascade tackle aspects assembly duality aegis kernel shrapnel absorb jenkins knowledge mcp operator sonar steward throttler voyager)
# Deliberately NOT included (measured: no runtime SQL references them):
#   gateway (1 table), inventory (0 tables), mesh (0 tables), scratch (94 tables,
#   scratch debris). Adding a schema here is a deliberate act — the vector-table
#   exclusions below are the reason this cannot simply dump "everything".
SCHEMA_ARGS=(); for s in "${SCHEMAS[@]}"; do SCHEMA_ARGS+=(--schema="$s"); done
VALUES_LIST=$(printf "('%s')," "${SCHEMAS[@]}"); VALUES_LIST="${VALUES_LIST%,}"
NOT_IN=$(printf "'%s'," "${SCHEMAS[@]}"); NOT_IN="${NOT_IN%,}"

PSQL="psql -h $HOST -p $PORT -U $USER -d $DB -v ON_ERROR_STOP=1"

# Sanity: every schema must exist in the source DB.
MISSING=$($PSQL -AtAc "SELECT string_agg(s, ',') FROM unnest(ARRAY[${NOT_IN}]) s WHERE NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = s)" 2>/dev/null || true)
if [ -n "$MISSING" ]; then
  echo "ERROR: schema(s) not found in source DB: $MISSING" >&2
  exit 1
fi

# --- Migration-ledger / branch-drift guard (fail-closed) --------------------
# The snapshot this script emits is --schema-only, so a fresh apply leaves
# shrapnel._migration_ledger EMPTY and the branch's migration chain replays
# from 0001. If the source DB is AHEAD of the branch, the chain re-issues the
# OLDER body last and silently reverts whatever the extra migration fixed.
# Demonstrated 2026-09-28: dumping a DB carrying 0008+0009 and replaying main's
# 0001-0006 over it restored 0005's txid_current arithmetic to
# forbid_stereotype_field_mutation — the exact subtransaction-freeze defect
# 0009 fixed. Refuse the dump instead (fail at the source, not in CI).
#
# The guard list is EXPLICIT, not discovered by table-name pattern. Other
# tables match *migration*/*ledger* but do not record migration filenames:
#   resolution.migration_ledger -> migration_label ('v21_batch', 'v32_...')
#   knowledge.graph_migrations  -> source_file ('nexus-knowledge-graph.json')
# Matching those against a migrations/ dir would refuse every refresh, so
# adding one here is a deliberate, reviewed act.
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LEDGER_GUARDS=("shrapnel._migration_ledger:typescript/shrapnel/migrations")

for guard in "${LEDGER_GUARDS[@]}"; do
  LEDGER_TABLE="${guard%%:*}"; LEDGER_DIR_REL="${guard#*:}"
  LEDGER_DIR="$REPO_ROOT/$LEDGER_DIR_REL"

  LEDGER_EXISTS=$($PSQL -AtAc "SELECT to_regclass('$LEDGER_TABLE') IS NOT NULL" 2>/dev/null || true)
  if [ "$LEDGER_EXISTS" != "t" ]; then
    # No ledger in the source DB => nothing to compare, no drift possible
    # through this mechanism. Not an error: a fresh/unmigrated source is valid.
    echo "ledger guard: $LEDGER_TABLE absent from source DB — nothing to verify."
    continue
  fi

  if [ ! -d "$LEDGER_DIR" ]; then
    echo "ERROR: $LEDGER_TABLE exists in the source DB but its migrations dir is missing from the branch:" >&2
    echo "         $LEDGER_DIR" >&2
    echo "       Unverifiable drift — refusing to dump. Add the dir, or remove the entry from LEDGER_GUARDS if it is not a filename ledger." >&2
    exit 1
  fi

  # Read the ledger; a query failure is a failure (fail-closed), not a pass.
  if ! LEDGER_FILES=$($PSQL -AtAc "SELECT filename FROM $LEDGER_TABLE ORDER BY 1" 2>&1); then
    echo "ERROR: could not read $LEDGER_TABLE from the source DB — refusing to dump." >&2
    echo "$LEDGER_FILES" >&2
    exit 1
  fi

  AHEAD=""; BEHIND=""
  while IFS= read -r applied; do
    [ -n "$applied" ] || continue
    [ -f "$LEDGER_DIR/$applied" ] || AHEAD="$AHEAD $applied"
  done <<< "$LEDGER_FILES"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    grep -qxF "$f" <<< "$LEDGER_FILES" || BEHIND="$BEHIND $f"
  done < <(cd "$LEDGER_DIR" && ls -1 ./*.sql 2>/dev/null | sed 's|^\./||')

  if [ -n "$AHEAD" ]; then
    echo "ERROR: source DB is AHEAD of the branch — $LEDGER_TABLE records migration(s) absent from $LEDGER_DIR_REL:" >&2
    for f in $AHEAD; do echo "         $f" >&2; done
    echo >&2
    echo "       Dumping now would bake objects the branch's migration chain cannot" >&2
    echo "       reproduce: a fresh apply leaves the ledger empty, so the chain" >&2
    echo "       replays from the first migration and re-issues the OLDER body" >&2
    echo "       last, silently reverting the fix inside CI. Merge the migration" >&2
    echo "       first, or refresh from a DB that is not ahead of the branch." >&2
    exit 1
  fi

  if [ -n "$BEHIND" ]; then
    # Safe direction: the snapshot will simply lack these, and the branch's
    # chain will create them on first apply. But it means the artifact is
    # older than the branch — surface it rather than let it pass quietly.
    echo "WARNING: source DB is BEHIND the branch — $LEDGER_DIR_REL has migration(s) not yet applied to the source:" >&2
    for f in $BEHIND; do echo "         $f" >&2; done
    echo "         The snapshot will omit these; the migration chain will create them on apply. Artifact will lag main." >&2
  fi

  echo "ledger guard: $LEDGER_TABLE ok ($(printf '%s\n' "$LEDGER_FILES" | grep -c .) applied, 0 ahead of branch)."
done

# Public functions referenced by dumped views/triggers, transitively through
# function-to-function calls (matched by name-in-body, which may over-include
# harmlessly: creating extra plpgsql/sql functions has no side effects).
PUBFUNCS=$($PSQL -AtAc "
WITH RECURSIVE dumped AS (SELECT unnest(ARRAY[${NOT_IN}]) AS s),
closure(oid) AS (
  SELECT DISTINCT p.oid
  FROM pg_proc p
  JOIN pg_namespace pn ON pn.oid = p.pronamespace
  WHERE pn.nspname = 'public' AND p.prokind = 'f' AND (
    EXISTS (SELECT 1 FROM pg_views v JOIN dumped d ON d.s = v.schemaname
            WHERE v.definition LIKE '%' || p.proname || '(%')
    OR EXISTS (SELECT 1 FROM pg_trigger t
               JOIN pg_class c ON c.oid = t.tgrelid
               JOIN pg_namespace cn ON cn.oid = c.relnamespace
               JOIN dumped d ON d.s = cn.nspname
               WHERE NOT t.tgisinternal
                 AND pg_get_triggerdef(t.oid) LIKE '%' || p.proname || '(%')
  )
  UNION
  SELECT p2.oid
  FROM closure cl
  JOIN pg_proc p1 ON p1.oid = cl.oid
  JOIN pg_proc p2 ON p2.proname IN (
    SELECT m[1] FROM regexp_matches(coalesce(p1.prosrc,''), '([a-z_]+)\(', 'g') m
  )
  JOIN pg_namespace pn2 ON pn2.oid = p2.pronamespace
  WHERE pn2.nspname = 'public' AND p2.prokind = 'f' AND p2.oid <> p1.oid
)
SELECT pg_get_functiondef(oid) || ';'
FROM closure
ORDER BY 1" 2>"${PUBFUNCS_ERR:-/dev/null}")
if [ -n "${PUBFUNCS_ERR:-}" ] && [ -s "$PUBFUNCS_ERR" ]; then echo "closure query error:" >&2; cat "$PUBFUNCS_ERR" >&2; fi

# Full schema-only dump of the closed schema set. NO object exclusions: a
# partially-included schema does not compose (see the pgvector note at the top).
PGPASSWORD="${PGPASSWORD:-}" pg_dump -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" \
  --schema-only --no-owner --no-privileges "${SCHEMA_ARGS[@]}" \
  > "$BODY"

# pg_dump 17.x emits \restrict/\unrestrict psql meta-commands with a RANDOM
# per-run token (psql meta-command-injection guard). They make the artifact
# non-deterministic run-to-run; strip them — this dump is generated from our
# own trusted DB, not an untrusted file. (Folded from PR #495.)
sed -i '/^\\restrict /d; /^\\unrestrict /d' "$BODY"

# Fail-closed extension precondition: the dump now carries vector objects, so the
# artifact is only applicable on a server that has the extension available. Catch
# that here rather than as an opaque CREATE TABLE failure during apply.
if grep -q 'public\.vector' "$BODY" && ! $PSQL -AtAc "SELECT 1 FROM pg_extension WHERE extname = 'vector'" | grep -qx 1; then
  echo "ERROR: dump body uses public.vector but the source server has no 'vector' extension." >&2
  echo "The snapshot is complete (no object exclusions), so CI MUST run pgvector/pgvector:pg17." >&2
  exit 1
fi

# Closure verification (folded from PR #495): every real source schema
# referenced anywhere in the dump body must be in SCHEMAS() (public is always
# present in a fresh DB). A missed inclusion breaks the restore — proven
# empirically 2026-09-22 when aegis, shrapnel, assembly, kernel and duality
# each broke a clean-room apply at a different object. Fail the REFRESH at
# the source (fix = one list edit) instead of failing CI (fix = forensics).
REAL_SCHEMAS=$($PSQL -AtAc "SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg\\_%' AND nspname <> 'information_schema'" 2>/dev/null || true)
INC=" ${SCHEMAS[*]} public "
MISSING_REFS=""
for s in $REAL_SCHEMAS; do
  case "$INC" in *" $s "*) continue ;; esac
  if grep -qE "(^|[^a-zA-Z0-9_])$s\\." "$BODY"; then
    MISSING_REFS="$MISSING_REFS $s"
  fi
done
if [ -n "$MISSING_REFS" ]; then
  echo "ERROR: dump body references schema(s) missing from SCHEMAS():$MISSING_REFS" >&2
  echo "Add each to SCHEMAS() AND the prelude CREATE SCHEMA list, then re-run." >&2
  exit 1
fi

{
  cat <<'PRELUDE'
-- nexus CI bootstrap — complete global schemas for DB-backed tests
-- Extracted from a live nexus DB via pg_dump --schema-only; regenerate with
-- refresh.sh when conduit adapter global reads/writes change.
CREATE SCHEMA IF NOT EXISTS execution;
CREATE SCHEMA IF NOT EXISTS vision;
CREATE SCHEMA IF NOT EXISTS nebula;
CREATE SCHEMA IF NOT EXISTS conduit;
CREATE SCHEMA IF NOT EXISTS semantics;
CREATE SCHEMA IF NOT EXISTS terrain;
CREATE SCHEMA IF NOT EXISTS peb;
CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS resolution;
CREATE SCHEMA IF NOT EXISTS wind;
CREATE SCHEMA IF NOT EXISTS cascade;
CREATE SCHEMA IF NOT EXISTS tackle;
CREATE SCHEMA IF NOT EXISTS aspects;
CREATE SCHEMA IF NOT EXISTS assembly;
CREATE SCHEMA IF NOT EXISTS duality;
CREATE SCHEMA IF NOT EXISTS aegis;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
-- Required: the snapshot carries vector columns and vector-typed functions.
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS kernel;
CREATE SCHEMA IF NOT EXISTS shrapnel;
CREATE SCHEMA IF NOT EXISTS absorb;
CREATE SCHEMA IF NOT EXISTS jenkins;
CREATE SCHEMA IF NOT EXISTS knowledge;
CREATE SCHEMA IF NOT EXISTS mcp;
CREATE SCHEMA IF NOT EXISTS operator;
CREATE SCHEMA IF NOT EXISTS sonar;
CREATE SCHEMA IF NOT EXISTS steward;
CREATE SCHEMA IF NOT EXISTS throttler;
CREATE SCHEMA IF NOT EXISTS voyager;
PRELUDE
  # Public functions referenced by dumped views/triggers (closure above) —
  # must exist before any view/trigger definition that calls them.
  printf '%s\n' "$PUBFUNCS"
  # pg_dump --schema=X emits plain CREATE SCHEMA X; which collides with the
  # prelude's IF NOT EXISTS guards on re-apply — strip those lines only.
  grep -v '^CREATE SCHEMA [a-z]' "$BODY"
} > "$OUT"

# --- Ratified seed embedding (born-clean requirement) -----------------------
# Source of truth is sql/seeds/aspects_vocabulary_seed.sql, never live rows:
# the snapshot must stay reproducible, and the seed's own fail-closed census
# assertion (11 = 5 axes + 6 values, scoped to its provenance marker) polices
# every apply. Appended LAST — the INSERTs require the dumped aspects tables.
SEED_FILE="$(dirname "$0")/../seeds/aspects_vocabulary_seed.sql"
if [ ! -s "$SEED_FILE" ]; then
  echo "ERROR: aspects seed file missing or empty: $SEED_FILE" >&2
  exit 1
fi
if ! grep -q 'seed_assert' "$SEED_FILE"; then
  echo "ERROR: aspects seed file carries no fail-closed assertion block: $SEED_FILE" >&2
  exit 1
fi
SEED_SHA=$(sha256sum "$SEED_FILE" | cut -d' ' -f1)
{
  echo ""
  echo "-- ---------------------------------------------------------------------------"
  echo "-- Ratified seed: aspects.governed_tag_vocabulary — embedded verbatim from"
  echo "-- sql/seeds/aspects_vocabulary_seed.sql (sha256 ${SEED_SHA}). Its own"
  echo "-- fail-closed assertions police the census on every apply."
  echo "-- ---------------------------------------------------------------------------"
  cat "$SEED_FILE"
} >> "$OUT"

# --- Fresh-apply check (born-clean proof, fail-closed) ----------------------
# Every refresh must prove the snapshot it just wrote actually applies to an
# EMPTY database and yields the seeded census. This is the guard that catches
# missing extensions (citext incident 2026-09-23), unprovisioned schemas, and
# seed drift — cheap because refresh is already an operator action.
if [ "${SKIP_APPLY_CHECK:-0}" != "1" ]; then
  CHECKDB="nexus_cib_check_$$"
  if ! createdb -h "$HOST" -p "$PORT" -U "$USER" "$CHECKDB" 2>/dev/null; then
    echo "ERROR: cannot create scratch DB '$CHECKDB' on $HOST:$PORT/$DB's cluster for the fresh-apply check (read-only source?). Re-run with SKIP_APPLY_CHECK=1 to skip." >&2
    exit 1
  fi
  if ! PGPASSWORD="${PGPASSWORD:-}" psql -h "$HOST" -p "$PORT" -U "$USER" -d "$CHECKDB" -v ON_ERROR_STOP=1 -q -f "$OUT"; then
    dropdb -h "$HOST" -p "$PORT" -U "$USER" "$CHECKDB" 2>/dev/null || true
    echo "ERROR: fresh-apply check FAILED — snapshot does not apply to an empty database. Not overwriting a proven-good snapshot blindly; investigate the error above." >&2
    exit 1
  fi
  CENSUS=$(PGPASSWORD="${PGPASSWORD:-}" psql -h "$HOST" -p "$PORT" -U "$USER" -d "$CHECKDB" -Atc \
    "SELECT count(*) FROM aspects.governed_tag_vocabulary")
  dropdb -h "$HOST" -p "$PORT" -U "$USER" "$CHECKDB"
  if [ "$CENSUS" != "11" ]; then
    echo "ERROR: fresh-apply census wrong: governed_tag_vocabulary=$CENSUS (expected 11)." >&2
    exit 1
  fi
  echo "fresh-apply check: OK (empty-DB apply clean, seed census 11)"
fi

echo "wrote $OUT ($(wc -l < "$OUT") lines, seed sha256 ${SEED_SHA})"
