#!/usr/bin/env bash
# local-pg-env.sh — point bin/tests at a DISPOSABLE PostgreSQL database.
#
# Why this exists: the DB tier builds a throwaway database per fixture with
# `createdb -w`, and several guards open their own connection and create a
# `test_lifecycle_*` schema. Locally those connections used to land in the
# SHARED, LIVE `nexus` database: fixture databases failed 79 guards with
# "fe_sendauth: no password supplied", three guards aborted collection on an
# unset CONDUIT_PG_DSN (pytest exit 2, nothing run), and the guards that did
# connect left test schemas behind in `nexus` next to 95 live service
# connections. CI never does this: its guards-db job seeds a throwaway
# database from sql/ci-bootstrap and points BOTH DSN conventions at it.
#
# This script does the same thing locally. It creates a dedicated database
# (default: nexus_guards), seeds it from the repo-canonical ci-bootstrap
# snapshot plus the vintage role seed, verifies the result, and exports the
# environment. `--reset` throws the scratch database away and rebuilds it, so
# anything a run leaves behind has exactly one owner and one cleanup path.
#
# Resolution order:
#   1. PGHOST, PGPORT, PGUSER, PGPASSWORD already exported win (defaults
#      below are the documented local convention: pguser/pgpass on
#      127.0.0.1:5432, as in bin/nightshift-flip.sh and docker/vanadium).
#   2. CONDUIT_PG_DSN already exported wins outright: no scratch database is
#      created or seeded, and the DSN is used verbatim (deriving PGDATABASE
#      from its dbname when it carries one). Opt in to a specific server this
#      way; pointing it at `nexus` is your call, not this script's default.
#   3. Otherwise the scratch database is provisioned and used for BOTH
#      CONDUIT_PG_DSN and NEXUS_PG_DSN, and PGDATABASE. PGDATABASE is an
#      OUTPUT here, never an input, so a stray exported PGDATABASE=nexus
#      cannot pull the guards back onto the live database. Rename the scratch
#      database with GUARD_PGDATABASE.
#
# Usage:
#   eval "$(bin/ci/local-pg-env.sh)"                 # export into this shell
#   bin/ci/local-pg-env.sh python3 -m pytest bin/tests   # run one command
#   bin/ci/local-pg-env.sh --check                   # provision if needed + verify
#   bin/ci/local-pg-env.sh --reset                   # drop and re-seed the scratch DB
#
# Exit codes (the guards' own 0/1 are untouched; these are this script's):
#   0  the exported environment is reachable, writable, and seeded
#   3  nothing reachable at the probed address, or authentication refused, or
#      psql is not on PATH at all
#   4  reachable, but the role cannot CREATE DATABASE — every DB fixture
#      would still error, so fail here with the reason instead
#   5  the scratch database could not be created, seeded, or verified

set -euo pipefail

# Print the leading comment block, stopping at the first line of code, so the
# help text cannot drift out of step with the header's length.
usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

die() {
  local code="$1"
  shift
  printf 'local-pg-env: %s\n' "$@" >&2
  exit "$code"
}

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BOOTSTRAP="$REPO_ROOT/sql/ci-bootstrap/nexus-ci-bootstrap.sql"
ROLE_SEED="$REPO_ROOT/sql/ci-bootstrap/tackle_roles_vintage_seed.sql"
MARKER_TABLE="public.bin_tests_seed"

MODE=print
RESET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --print) MODE=print; shift ;;
    --check) MODE=check; shift ;;
    --reset) RESET=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) MODE=exec; break ;;
  esac
done

: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=pguser}"
: "${PGPASSWORD:=pgpass}"
: "${PGCONNECT_TIMEOUT:=3}"
export PGHOST PGPORT PGUSER PGPASSWORD PGCONNECT_TIMEOUT

GUARD_DB="${GUARD_PGDATABASE:-nexus_guards}"
if ! [[ $GUARD_DB =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
  die 5 "GUARD_PGDATABASE=${GUARD_DB} is not a plain SQL identifier"
fi
if [ "$RESET" = 1 ] && [ -n "${CONDUIT_PG_DSN:-}" ]; then
  die 5 "--reset only manages the scratch database; CONDUIT_PG_DSN is set, so there is nothing here to drop"
fi

target="${PGHOST}:${PGPORT} as ${PGUSER}"
ADMIN_DB=postgres

command -v psql >/dev/null 2>&1 || die 3 \
  "psql is not on PATH, so the DB guards cannot run at all" \
  "  install the PostgreSQL client (bin/tests also needs createdb/dropdb)"

# --- reachability + capability ------------------------------------------------
# Probe before provisioning anything: a refused password must never look like
# a seed failure, and a role that cannot CREATE DATABASE can host neither the
# fixtures nor this scratch database.
if ! capability="$(psql -w -X -At -q -d "$ADMIN_DB" \
    -c 'select rolcreatedb from pg_roles where rolname = current_user' \
    </dev/null 2>&1)"; then
  die 3 "no usable PostgreSQL at ${target}" \
    "  psql said: ${capability}" \
    "  the DB guards create throwaway databases; start the local server," \
    "  or export PGPASSWORD / CONDUIT_PG_DSN for the one you mean"
fi
if [ "${capability//[[:space:]]/}" != "t" ]; then
  die 4 "${target} is reachable but may not CREATE DATABASE" \
    "  every DB guard builds its own scratch database, so they would all" \
    "  error on the first createdb; grant CREATEDB to ${PGUSER} or point" \
    "  PGUSER/PGPASSWORD/CONDUIT_PG_DSN at a role that has it"
fi

db_exists() {
  [ -n "$(psql -w -X -At -q -d "$ADMIN_DB" \
    -c "select 1 from pg_database where datname = '$1'" </dev/null)" ]
}

scratch_is_seeded() {
  [ "$(psql -w -X -At -q -d "$1" \
    -c "select to_regclass('${MARKER_TABLE}') is not null" </dev/null)" = "t" ]
}

stamp_ledger() {
  local db="$1" commit version
  commit="$(git -C "$REPO_ROOT" log -1 --format=%H -- "$BOOTSTRAP" 2>/dev/null || true)"
  if [ -z "$commit" ]; then
    printf 'local-pg-env: could not date the ci-bootstrap snapshot; nebula.schema_version left unstamped\n' >&2
    return 0
  fi
  version="$(git -C "$REPO_ROOT" ls-tree --name-only "$commit" \
      -- typescript/nebula-srv/migrations/ 2>/dev/null \
    | xargs -r -n1 basename | grep -oE '^[0-9]{3}' | sort -n | tail -1)"
  if [ -z "$version" ]; then
    printf 'local-pg-env: could not compute the dump vintage; nebula.schema_version left unstamped\n' >&2
    return 0
  fi
  psql -w -X -q -v ON_ERROR_STOP=1 -d "$db" >/dev/null <<SQL
insert into nebula.schema_version (version, description)
values ($version, 'ci-bootstrap born at dump vintage (bin/ci/local-pg-env.sh)')
on conflict (version) do nothing;
SQL
}

seed_scratch() {
  local db="$1"
  [ -r "$BOOTSTRAP" ] || die 5 "seed file is missing: $BOOTSTRAP"
  [ -r "$ROLE_SEED" ] || die 5 "seed file is missing: $ROLE_SEED"

  psql -w -X -q -v ON_ERROR_STOP=1 -d "$db" -f "$BOOTSTRAP" >/dev/null \
    || die 5 "applying $BOOTSTRAP to $db failed" \
      "  the scratch database is half-built; re-run with --reset"
  psql -w -X -q -v ON_ERROR_STOP=1 -d "$db" -f "$ROLE_SEED" >/dev/null \
    || die 5 "applying $ROLE_SEED to $db failed" \
      "  the scratch database is half-built; re-run with --reset"
  stamp_ledger "$db"
  # Written last: its presence is the only thing that marks a COMPLETE seed.
  psql -w -X -q -v ON_ERROR_STOP=1 -d "$db" >/dev/null <<SQL \
    || die 5 "could not mark $db as seeded (re-run with --reset)"
create table if not exists ${MARKER_TABLE} (
    seeded_at timestamptz not null default now(),
    source    text not null
);
insert into ${MARKER_TABLE} (source)
values ('sql/ci-bootstrap/nexus-ci-bootstrap.sql + tackle_roles_vintage_seed.sql');
SQL
}

ensure_scratch() {
  local db="$1" needs_seed=0

  if [ "$RESET" = 1 ] && db_exists "$db"; then
    psql -w -X -At -q -d "$ADMIN_DB" >/dev/null <<SQL \
      || die 5 "could not disconnect existing sessions from $db"
select pg_terminate_backend(pid) from pg_stat_activity
 where datname = '$db' and pid <> pg_backend_pid();
SQL
    psql -w -X -q -v ON_ERROR_STOP=1 -d "$ADMIN_DB" >/dev/null \
      -c "drop database if exists \"$db\"" \
      || die 5 "could not drop $db"
  fi

  if ! db_exists "$db"; then
    createdb -w "$db" || die 5 "could not create scratch database $db"
    needs_seed=1
  elif ! scratch_is_seeded "$db"; then
    needs_seed=1
  fi

  if [ "$needs_seed" = 1 ]; then
    seed_scratch "$db"
  fi

  if ! scratch_is_seeded "$db"; then
    die 5 "$db exists but is not seeded from the ci-bootstrap snapshot" \
      "  re-run with --reset to rebuild it"
  fi
}

# --- choose the database ------------------------------------------------------
if [ -n "${CONDUIT_PG_DSN:-}" ]; then
  # Explicit opt-in: honour it, manage nothing.
  dsn_db=""
  case "$CONDUIT_PG_DSN" in
    *dbname=*) dsn_db="$(printf '%s' "$CONDUIT_PG_DSN" | sed -n 's/.*dbname=\([^ ]*\).*/\1/p')" ;;
    */*) dsn_db="${CONDUIT_PG_DSN##*/}" ;;
  esac
  PGDATABASE="${dsn_db:-${PGDATABASE:-$GUARD_DB}}"
  export PGDATABASE
  psql -w -X -At -q -c 'select 1' </dev/null >/dev/null 2>&1 \
    || die 3 "CONDUIT_PG_DSN is set but not usable as ${target} dbname=${PGDATABASE}"
else
  PGDATABASE="$GUARD_DB"
  export PGDATABASE
  ensure_scratch "$PGDATABASE"
  CONDUIT_PG_DSN="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}"
fi

# Both connection conventions the guards use point at the same database, as in
# the guards-db CI job: NEXUS_PG_DSN for repo guards, CONDUIT_PG_DSN for the
# conduit guards. One database, so they cannot drift apart.
NEXUS_PG_DSN="$CONDUIT_PG_DSN"
export CONDUIT_PG_DSN NEXUS_PG_DSN

case "$MODE" in
  check)
    exit 0
    ;;
  print)
    printf 'export PGHOST=%q\n' "$PGHOST"
    printf 'export PGPORT=%q\n' "$PGPORT"
    printf 'export PGUSER=%q\n' "$PGUSER"
    printf 'export PGPASSWORD=%q\n' "$PGPASSWORD"
    printf 'export PGDATABASE=%q\n' "$PGDATABASE"
    printf 'export CONDUIT_PG_DSN=%q\n' "$CONDUIT_PG_DSN"
    printf 'export NEXUS_PG_DSN=%q\n' "$NEXUS_PG_DSN"
    ;;
  exec)
    exec "$@"
    ;;
esac
