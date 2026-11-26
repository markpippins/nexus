#!/usr/bin/env bash
# local-pg-env.sh — point bin/tests at a PostgreSQL that can create scratch databases.
#
# Why this exists: the DB tier (bin/tests-ci-manifest.json -> tiers.db) builds a
# throwaway database per fixture with `createdb -w`. Locally that failed 79
# fixtures with
#
#     createdb: error: connection to server at "localhost" (::1), port 5432
#     failed: fe_sendauth: no password supplied
#
# and three guards (test_backfill_propositions, test_drift_intake,
# test_shrapnel_bridge) aborted collection outright on an unset CONDUIT_PG_DSN,
# so `python3 -m pytest bin/tests` exited 2 having run nothing at all. The
# server was up the whole time; only the credentials were missing from the
# environment. This script supplies them and PROVES they work before the guards
# get to spend a run discovering it.
#
# Resolution order (nothing is guessed beyond the documented local convention):
#   1. Values already exported win: PGHOST, PGPORT, PGUSER, PGPASSWORD,
#      PGDATABASE, CONDUIT_PG_DSN. Set PGPASSWORD (or the whole DSN) for any
#      other server; this script then only verifies.
#   2. Otherwise the local convention is used: pguser/pgpass on
#      127.0.0.1:5432/nexus — the same defaults as bin/nightshift-flip.sh,
#      bin/mongodb-health-monitor.sh and docker/vanadium/.env.example.
#
# Usage:
#   eval "$(bin/ci/local-pg-env.sh)"                 # export into this shell
#   bin/ci/local-pg-env.sh python3 -m pytest bin/tests   # run one command with it
#   bin/ci/local-pg-env.sh --check                   # verify only, silent on success
#
# Exit codes (the guards' own 0/1 are untouched; these are this script's):
#   0  reachable, and the role may CREATE DATABASE (the fixtures need it)
#   3  nothing reachable at the probed address, or authentication refused
#   4  reachable, but the role cannot create databases — every DB fixture
#      would still error, so fail here with the reason instead

set -euo pipefail

usage() {
  sed -n '2,40p' "$0" | sed 's/^#\{1\} \{0,1\}//'
}

MODE=print
case "${1:-}" in
  --print) MODE=print; shift ;;
  --check) MODE=check; shift ;;
  -h | --help) usage; exit 0 ;;
  "") MODE=print ;;
  *) MODE=exec ;;
esac

: "${PGHOST:=127.0.0.1}"
: "${PGPORT:=5432}"
: "${PGUSER:=pguser}"
: "${PGDATABASE:=nexus}"
: "${PGPASSWORD:=pgpass}"
CONDUIT_PG_DSN="${CONDUIT_PG_DSN:-postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${PGDATABASE}}"
export PGHOST PGPORT PGUSER PGDATABASE PGPASSWORD CONDUIT_PG_DSN
: "${PGCONNECT_TIMEOUT:=3}"
export PGCONNECT_TIMEOUT

target="${PGHOST}:${PGPORT} as ${PGUSER}"

if command -v psql >/dev/null 2>&1; then
  if ! capability="$(psql -w -X -At -q \
      -c 'select rolcreatedb from pg_roles where rolname = current_user' \
      </dev/null 2>&1)"; then
    {
      echo "local-pg-env: no usable PostgreSQL at ${target}"
      echo "  psql said: ${capability}"
      echo "  the DB guards create throwaway databases; start the local server,"
      echo "  or export PGPASSWORD / CONDUIT_PG_DSN for the one you mean"
    } >&2
    exit 3
  fi
else
  {
    echo "local-pg-env: psql is not on PATH, so the DB guards cannot run at all"
    echo "  install the PostgreSQL client (bin/tests also needs createdb/dropdb)"
  } >&2
  exit 3
fi

if [ "${capability//[[:space:]]/}" != "t" ]; then
  {
    echo "local-pg-env: ${target} is reachable but may not CREATE DATABASE"
    echo "  every DB guard builds its own scratch database, so they would all"
    echo "  error on the first createdb; grant CREATEDB to ${PGUSER} or point"
    echo "  PGUSER/PGPASSWORD/CONDUIT_PG_DSN at a role that has it"
  } >&2
  exit 4
fi

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
    ;;
  exec)
    exec "$@"
    ;;
esac
