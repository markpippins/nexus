#!/usr/bin/env bash
# APPLY-069.sh — DRAFT, DO NOT RUN (see ../README.md; DBA proposal 5ab78e30 §2.3)
#
# WARNING — this wrapper is restored as a DRAFT after the repo-wide read strip.
# It has NOT been verified readable in full after that strip, and it is NOT
# executable from bin/drafts/ until it passes the same gates as everything else
# here (DBA sign-off, ordering check, production-target confirmation). Do not
# run it from here. It is retained so the wrapper logic can be rebuilt from its
# container, not invented from memory.
#
# Commissioning wrapper for migration 069. Migration 068 is DBA-lane work: it is
# applied BY HAND via this wrapper, never by the startup runner (the file must
# not be moved into typescript/nebula-srv/migrations/ until the 068 stamp is in
# the ledger — a numbered file above the ledger gets auto-applied by any boot,
# which is the SEV3 9a70c8c5 hazard this whole workstream exists to close).
#
# Gates (ALL must pass before a single statement runs):
#   1. DBA_CONFIRM_I_UNDERSTAND literal set.
#   2. 069 is actually next: ledger is at 68 AND origin/main carries no
#      numbered file above 068 (checked live below).
#   3. Production PG target explicitly confirmed (default is production here).
#
# Post-apply, on operator confirmation (R9): replicate to vanadium.

set -euo pipefail

REPO="${REPO:-/home/codex/dev/nexus}"
export PGPASSWORD="${PGPASSWORD:-$(grep -m1 '^GATEWAY_PG_PASSWORD=' "$REPO/.env" | cut -d= -f2-)}"
PSQL="psql -X -h localhost -p 5432 -U pguser -d nexus -v ON_ERROR_STOP=1"
SQL="$REPO/bin/drafts/migration-069/069-schema-version-forensic-stamp.sql"

echo "=== APPLY-069 — mode: ${1:-dry-run} ==="

if [ "${1:-}" = "--apply" ]; then
  if [ "${DBA_CONFIRM_I_UNDERSTAND:-}" != "migration-069-dba-lane-hand-apply-only" ]; then
    echo "ABORT: set DBA_CONFIRM_I_UNDERSTAND=\"migration-069-dba-lane-hand-apply-only\" to confirm you have read this header."
    exit 2
  fi
fi

# ── Gate: 068 is actually the next number on the live ledger ──
LEDGER_MAX=$($PSQL -Atc "SELECT COALESCE(MAX(version),0) FROM nebula.schema_version")
NEXT_FILE=$(git -C "$REPO" ls-tree origin/main typescript/nebula-srv/migrations/ --name-only \
  | awk -F/ '{print $NF}' | grep -oE '^[0-9]{3}' | sort -n | tail -1)
echo "live ledger max: $LEDGER_MAX | highest numbered file on origin/main: ${NEXT_FILE:-none}"
if [ "$LEDGER_MAX" != "68" ] || [ "${NEXT_FILE:-0}" != "068" ]; then
  echo "ABORT: expected ledger 68 + highest file 068 before 069. Re-check 3aa390da and re-baseline."
  exit 2
fi
echo "[ok] 069 is next (ordering hazard cleared)"

# ── Gate: production target confirmation ──
TARGET_DB=$($PSQL -Atc "SELECT current_database() || '@' || inet_server_addr()")
echo "resolved target: $TARGET_DB"
if [ "${1:-}" = "--apply" ] && [ "${DBA_TARGET_IS_PRODUCTION:-}" != "yes" ]; then
  echo "ABORT: set DBA_TARGET_IS_PRODUCTION=yes to confirm the target above is intended."
  exit 2
fi

if [ "${1:-}" != "--apply" ]; then
  echo
  echo "=== DRY RUN — SQL that WOULD run (nothing executed): ==="
  cat "$SQL"
  echo
  echo "Dry run complete. To apply: DBA_CONFIRM_I_UNDERSTAND=... DBA_TARGET_IS_PRODUCTION=yes $0 --apply"
  exit 0
fi

echo "=== APPLY: single transaction (the file carries its own BEGIN/COMMIT, 043 style — no -1 wrapper) ==="
$PSQL -f "$SQL"

echo
echo "=== Post-apply verification ==="
$PSQL -P pager=off -c "SELECT * FROM nebula.schema_version_audit ORDER BY id;"
echo "Expect exactly one row: version 69, stamped by $(id -un)'s db role. If empty, the trigger did not fire — investigate BEFORE declaring success."
echo
echo "Follow-ups:"
echo "  1. Move the SQL file into typescript/nebula-srv/migrations/ in a PR (now replay-safe for fresh DBs)."
echo "  2. Land the application_name patch below in the same or a follow-up PR."
echo "  3. R9: ask the operator whether to replicate to vanadium."

exit 0

# ---------------------------------------------------------------------------
# application_name patch — for index.ts (companion code change; NOT executed
# by this wrapper; land it in a PR against origin/main).
#
# Why: the 068 audit captures application_name per stamp. With this patch the
# runner's stamps self-identify (application_name + process ID), and session
# rows in pg_stat_activity become attributable. The per-stamp trigger plus the
# migrate.ts SET LOCAL below deliver capture even without this patch (fallback
# 'unconfigured' beats a silently-inherited value).
#
# --- a/typescript/nebula-srv/src/index.ts
# +++ b/typescript/nebula-srv/src/index.ts
# @@ -10,11 +10,14 @@
#  // defaults preserve the native localhost configuration.
#  const pool = new Pool({
#    host: process.env.PG_HOST || 'localhost',
#    port: parseInt(process.env.PG_PORT || '5432', 10),
#    user: process.env.PG_USER || 'pguser',
#    password: process.env.PG_PASSWORD || process.env.PG_PASS || 'pgpass',
#    database: process.env.PG_DB_NAME || 'nexus',
# -  options: '-c search_path=nebula',
# +  options: `-c search_path=nebula -c application_name=nebula-srv@${{
# +    process.env.NEBULA_GIT_SHA || 'unconfigured'
# +  }}`,
#    max: 10,
#    idleTimeoutMillis: 30000,
#    connectionTimeoutMillis: 5000,
#  });
#
# Companion change for migrate.ts (all stamps carry the identity even if the
# pool-level application_name is overridden upstream):
#
# --- a/typescript/nebula-srv/src/migrate.ts
# +++ b/typescript/nebula-srv/src/migrate.ts
# @@ (inside runMigrations, immediately after `await client.query('SET search_path TO nebula')`)
#    await client.query('SET search_path TO nebula');
# +  await client.query(
# +    `SET LOCAL application_name = 'nebula-migrate@${process.env.NEBULA_GIT_SHA || 'unconfigured'}'`
# +  );
#
# NEBULA_GIT_SHA is injected at deploy time (systemd Environment= or the
# container env); 'unconfigured' is the loud, greppable fallback — an
# attributable-unknown, never a fabricated value.
# ---------------------------------------------------------------------------
