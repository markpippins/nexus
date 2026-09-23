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
# Vector-backed embedding tables are excluded: they pull in a pgvector
# dependency, and nothing in the tested code paths reads them.
set -euo pipefail
HOST=${1:-localhost}; PORT=${2:-5432}; USER=${3:-pguser}; DB=${4:-nexus}
OUT="$(dirname "$0")/nexus-ci-bootstrap.sql"
BODY="$(mktemp /tmp/ci-bootstrap-body.XXXXXX)"
trap 'rm -f "$BODY"' EXIT

SCHEMAS=(aegis shrapnel execution vision nebula conduit semantics terrain peb registry resolution wind cascade tackle assembly kernel duality)
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

# Full schema-only dump of the closed schema set, minus vector tables.
PGPASSWORD="${PGPASSWORD:-}" pg_dump -h "$HOST" -p "$PORT" -U "$USER" -d "$DB" \
  --schema-only --no-owner --no-privileges "${SCHEMA_ARGS[@]}" \
  --exclude-table=nebula.agent_record_embeddings \
  --exclude-table=nebula.harvest_candidate_embeddings \
  --exclude-table=nebula.harvest_candidate_embeddings_history \
  --exclude-table=semantics.source_observation_embeddings > "$BODY"

# pg_dump 17.x emits \restrict/\unrestrict psql meta-commands with a RANDOM
# per-run token (psql meta-command-injection guard). They make the artifact
# non-deterministic run-to-run; strip them — this dump is generated from our
# own trusted DB, not an untrusted file.
sed -i '/^\\restrict /d; /^\\unrestrict /d' "$BODY"

# Closure verification: every real source schema referenced anywhere in the
# dump body must be in SCHEMAS() (public is always present in a fresh DB).
# A missed inclusion breaks the restore — proven empirically 2026-09-22 when
# aegis, shrapnel, assembly, kernel and duality each broke a clean-room apply
# at a different object. Fail the REFRESH at the source (fix = one list edit)
# instead of failing CI (fix = forensic archaeology).
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
CREATE SCHEMA IF NOT EXISTS aegis;
CREATE SCHEMA IF NOT EXISTS shrapnel;
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
CREATE SCHEMA IF NOT EXISTS assembly;
CREATE SCHEMA IF NOT EXISTS kernel;
CREATE SCHEMA IF NOT EXISTS duality;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
PRELUDE
  # Public functions referenced by dumped views/triggers (closure above) —
  # must exist before any view/trigger definition that calls them.
  printf '%s\n' "$PUBFUNCS"
  # pg_dump --schema=X emits plain CREATE SCHEMA X; which collides with the
  # prelude's IF NOT EXISTS guards on re-apply — strip those lines only.
  grep -v '^CREATE SCHEMA [a-z]' "$BODY"
} > "$OUT"

echo "wrote $OUT ($(wc -l < "$OUT") lines)"
