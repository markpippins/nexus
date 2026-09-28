#!/usr/bin/env bash
#
# run-pg-integration-e2e.sh — one-command local mirror of the seeded-
# PostgreSQL integration job (job `pg-integration-e2e` in
# .github/workflows/broker-e2e.yml, delivered by PR #607).
#
# Brings up the SAME throwaway chain the CI job runs, on isolated default
# ports so it is safe to run on titanium alongside the live services:
#
#   postgres:17  (docker, port 55442)  seeded from
#                sql/ci-bootstrap/nexus-ci-bootstrap.sql
#   mongo:7      (docker, port 28018)
#   peb-kernel   (fat jar,  port 18098) booted against the seeded DB
#   legacy execution-srv (port 32110)   for the parity suite
#
# then runs the gated suites with the same fail-closed TAP guard as CI
# (fail 0 AND skipped 0 AND a pass floor), so a silent skip cannot masquerade
# as green locally either.
#
# KEEP IN SYNC with the workflow: the workflow is the source of truth for the
# gating contract. If you change steps here, change the job there (or vice
# versa) and say so in the PR. Verified end-to-end 2026-09-28: 35/35, 0
# skipped on a fresh run of the full chain.
#
# Usage:
#   bin/run-pg-integration-e2e.sh                 # full chain, all suites
#   bin/run-pg-integration-e2e.sh bridge          # harness-bridge + attempt-lifecycle only
#   bin/run-pg-integration-e2e.sh smoke           # broker-smoke only
#   bin/run-pg-integration-e2e.sh parity          # legacy-parity only
#   bin/run-pg-integration-e2e.sh --skip-build    # reuse existing jar/dist/node_modules
#   bin/run-pg-integration-e2e.sh --keep          # leave the stack up after the run
#   bin/run-pg-integration-e2e.sh --json          # also emit a pgie-evidence/1 JSON artifact
#   bin/run-pg-integration-e2e.sh --json=PATH     # ...at an explicit path
#
# --json: every RUN (pass or fail) records an attestation-ready evidence
# artifact (schema pgie-evidence/1): per-suite TAP pass/fail/skipped counts
# + floors + verdicts, raw TAP log paths, run timing, git head/branch/dirty,
# and the throwaway-stack ports. Written atomically to
# /tmp/pgie-evidence-<UTC>.json (or PGIE_JSON_PATH / --json=PATH) on success
# AND on infra/test failure (verdict=fail, exit_code=1). Usage/preflight
# errors emit nothing — the artifact covers runs, not misuse. The tester
# role attests against this artifact instead of console output.
#
# Ports are env-overridable: PGIE_PG_PORT, PGIE_MONGO_PORT, PGIE_KERNEL_PORT,
# PGIE_LEGACY_PORT. Credentials match the CI job (pguser/pgpass/nexus) — they
# are throwaway containers, not the live DB.
set -Eeuo pipefail  # -E: ERR trap fires inside functions too — fail-path evidence must cover build/seed aborts

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PG_PORT="${PGIE_PG_PORT:-55442}"
MONGO_PORT="${PGIE_MONGO_PORT:-28018}"
KERNEL_PORT="${PGIE_KERNEL_PORT:-18098}"
LEGACY_PORT="${PGIE_LEGACY_PORT:-32110}"
PG_CONTAINER="pgie-local-pg"
MONGO_CONTAINER="pgie-local-mongo"
KERNEL_JAR="jvm/spring/peb-kernel/peb-bootstrap/target/peb-bootstrap-1.0.0-SNAPSHOT.jar"

SKIP_BUILD=0
KEEP=0
SUITE="all"
JSON_MODE=0
JSON_PATH=""
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=1 ;;
    --keep)       KEEP=1 ;;
    --json)       JSON_MODE=1 ;;
    --json=*)     JSON_MODE=1; JSON_PATH="${arg#--json=}" ;;
    -h|--help)    sed -n '2,43p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    all|bridge|smoke|parity) SUITE="$arg" ;;
    *) echo "unknown argument: $arg (see --help)"; exit 2 ;;
  esac
done
if [ "$JSON_MODE" = "1" ] && [ -z "$JSON_PATH" ]; then
  JSON_PATH="${PGIE_JSON_PATH:-/tmp/pgie-evidence-$(date -u +%Y%m%dT%H%M%SZ).json}"
fi

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

# ── Evidence artifact (pgie-evidence/1) ────────────────────────────────
RESULTS_TSV="$(mktemp /tmp/pgie-results.XXXXXX)"
RUN_START_TS="$(date -u +%s)"
RUN_STARTED=0
if [ "$JSON_MODE" = "1" ]; then
  mkdir -p "$(dirname "$JSON_PATH")"
fi

# Emit the evidence artifact atomically. Called on success (verdict=pass)
# and from die() (verdict=fail) once the run has started.
json_emit() { # $1 verdict, $2 exit_code
  [ "$JSON_MODE" = "1" ] || return 0
  python3 - "$1" "$2" "$JSON_PATH" "$RESULTS_TSV" "$RUN_START_TS" "$SUITE" \
      "$PG_PORT" "$MONGO_PORT" "$KERNEL_PORT" "$LEGACY_PORT" <<'PYEOF'
import datetime, json, os, subprocess, sys
verdict, exit_code = sys.argv[1], int(sys.argv[2])
path, tsv, start_ts, suite = sys.argv[3], sys.argv[4], sys.argv[5], sys.argv[6]
ports = {k: int(v) for k, v in zip(("pg", "mongo", "kernel", "legacy"), sys.argv[7:11])}

def git(*args):
    try:
        return subprocess.run(("git",) + args, capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:
        return ""

end_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
suites = []
totals = {"pass": 0, "fail": 0, "skipped": 0}
for line in open(tsv, encoding="utf-8"):
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 7:
        continue
    label, passed, failed, skipped, floor, s_verdict, log = parts
    suites.append({"suite": label, "pass": int(passed), "fail": int(failed),
                   "skipped": int(skipped), "pass_floor": int(floor),
                   "verdict": s_verdict, "tap_log": log})
    totals["pass"] += int(passed)
    if failed.isdigit():
        totals["fail"] += int(failed)
    if skipped.isdigit():
        totals["skipped"] += int(skipped)
artifact = {
    "schema": "pgie-evidence/1",
    "generated_at": datetime.datetime.now(datetime.timezone.utc)
                    .replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "suite_selector": suite,
    "verdict": verdict,
    "exit_code": exit_code,
    "duration_seconds": max(end_ts - int(start_ts), 0),
    "git": {"head": git("rev-parse", "HEAD"),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": git("status", "--porcelain") != ""},
    "stack_ports": ports,
    "suites": suites,
    "totals": totals,
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(artifact, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
print(f"evidence artifact: {path}")
PYEOF
}

die()  {
  printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2
  if [ "$RUN_STARTED" = "1" ]; then
    json_emit fail 1 || true
  fi
  exit 1
}

# Unguarded post-start command failures (seed psql, builds, ...) abort via
# set -e — emit the fail artifact on the way out so the run is still recorded.
on_err() {
  rc=$?
  if [ "$RUN_STARTED" = "1" ]; then
    json_emit fail "$rc" || true
  fi
}
trap on_err ERR

# ── Preflight ────────────────────────────────────────────────────────────
for dep in docker psql node mvn java curl python3 grep; do
  command -v "$dep" >/dev/null 2>&1 || die "missing dependency: $dep (install it, or run this on titanium)"
done
JAVA_HOME_OK=""
if [ -z "${JAVA_HOME:-}" ] && [ -d /usr/lib/jvm/java-21-openjdk-amd64 ]; then
  export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
  JAVA_HOME_OK=" (JAVA_HOME defaulted to $JAVA_HOME)"
fi
say "suite: $SUITE | pg:$PG_PORT mongo:$MONGO_PORT kernel:$KERNEL_PORT legacy:$LEGACY_PORT${JAVA_HOME_OK}"
RUN_STARTED=1
if [ "$JSON_MODE" = "1" ]; then say "evidence artifact: $JSON_PATH (written on pass AND fail)"; fi

PIDS=()
cleanup() {
  rm -f "${RESULTS_TSV:-}" 2>/dev/null || true
  if [ "$KEEP" = "1" ]; then
    say "--keep: stack left up. Tear down with:"
    say "  docker rm -f $PG_CONTAINER $MONGO_CONTAINER; pkill -f 'peb-bootstrap-1.0.0-SNAPSHOT.jar.*--server.port=$KERNEL_PORT'"
    [ -n "${LEGACY_PID:-}" ] && say "  kill $LEGACY_PID  # legacy execution-srv on :$LEGACY_PORT"
    return
  fi
  say "tearing down throwaway stack"
  for pid in "${PIDS[@]:-}"; do kill "$pid" 2>/dev/null || true; done
  docker rm -f "$PG_CONTAINER" "$MONGO_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# ── Throwaway containers ─────────────────────────────────────────────────
say "starting throwaway containers ($PG_CONTAINER :$PG_PORT, $MONGO_CONTAINER :$MONGO_PORT)"
docker rm -f "$PG_CONTAINER" "$MONGO_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$PG_CONTAINER" -p "$PG_PORT:5432" \
  -e POSTGRES_DB=nexus -e POSTGRES_USER=pguser -e POSTGRES_PASSWORD=pgpass \
  postgres:17 >/dev/null \
  || die "could not start $PG_CONTAINER (host port $PG_PORT taken? see: docker ps)"
docker run -d --name "$MONGO_CONTAINER" -p "$MONGO_PORT:27017" mongo:7 >/dev/null \
  || die "could not start $MONGO_CONTAINER (host port $MONGO_PORT taken? see: docker ps)"

export PGPASSWORD=pgpass
for i in $(seq 1 60); do
  psql -h localhost -p "$PG_PORT" -U pguser -d nexus -tAc 'SELECT 1' >/dev/null 2>&1 && break
  [ "$i" = 60 ] && die "postgres never accepted an authenticated connection"
  sleep 2
done
say "postgres accepting authenticated connections"
for i in $(seq 1 30); do
  docker exec "$MONGO_CONTAINER" mongosh --quiet --eval 'db.adminCommand({ping: 1}).ok' 2>/dev/null | grep -q 1 && break
  [ "$i" = 30 ] && die "mongo never became ready"
  sleep 2
done
say "mongo ready"

# ── Seed: repo-canonical CI bootstrap (same single file as CI) ────────────
say "seeding throwaway DB from sql/ci-bootstrap/nexus-ci-bootstrap.sql"
psql -v ON_ERROR_STOP=1 -h localhost -p "$PG_PORT" -U pguser -d nexus -q \
  -f sql/ci-bootstrap/nexus-ci-bootstrap.sql
psql -v ON_ERROR_STOP=1 -h localhost -p "$PG_PORT" -U pguser -d nexus -tAc "
  SELECT to_regclass('execution.attempts'),
         to_regclass('resolution.execution_claim'),
         to_regclass('resolution.execution_claim_evidence'),
         to_regclass('resolution.execution_evidence'),
         to_regclass('resolution.execution_admission_receipt'),
         to_regclass('resolution.keychain_event_outbox'),
         to_regclass('nebula.agent_records'),
         to_regclass('peb.transactions');
" | tee /tmp/pgie-seed-objects.txt | grep -q 'execution.admission_receipt' \
  || die "seed missing gated-suite objects"
V4=$(psql -h localhost -p "$PG_PORT" -U pguser -d nexus -tAc \
  "SELECT count(*) FROM information_schema.columns \
   WHERE table_schema='peb' AND table_name='transactions' \
     AND column_name IN ('kernel_event_id','kernel_event_type');")
[ "$V4" = "2" ] || die "peb.transactions missing kernel V4 columns — kernel ddl-auto=validate would fail"
say "seed OK: all gated-suite objects present (V4 kernel columns: 2/2)"

# ── Builds ────────────────────────────────────────────────────────────────
if [ "$SKIP_BUILD" != "1" ]; then
  say "building broker (npm ci + tsc)"
  (cd moleculer/nexus-broker && npm ci --silent && npm run build --silent)
  say "packaging peb-kernel fat jar (reactor -pl peb-bootstrap -am; module-pom direct builds resolve stale ~/.m2 jars)"
  mvn -q -B -f jvm/pom.xml -pl spring/peb-kernel/peb-bootstrap -am -DskipTests package
else
  say "--skip-build: reusing existing build artifacts"
fi
[ -f "$KERNEL_JAR" ] || die "kernel jar missing: $KERNEL_JAR (run without --skip-build)"
[ -f moleculer/nexus-broker/dist/services/harness.worker.js ] || die "broker dist missing (run without --skip-build)"

# psycopg2 for the git claim producer (PEP 668-safe fallback chain)
if ! python3 -c 'import psycopg2' 2>/dev/null; then
  say "installing psycopg2-binary"
  python3 -m pip install --quiet psycopg2-binary 2>/dev/null \
    || python3 -m pip install --quiet --break-system-packages psycopg2-binary \
    || die "could not install psycopg2-binary (install it into your python3 and re-run)"
fi

# ── peb-kernel boot (same config as CI: Flyway off, ddl-auto=validate) ────
say "booting peb-kernel on :$KERNEL_PORT against the throwaway DB"
java -jar "$KERNEL_JAR" \
  --server.port="$KERNEL_PORT" \
  --spring.datasource.url="jdbc:postgresql://localhost:$PG_PORT/nexus?currentSchema=peb" \
  --spring.datasource.username=pguser \
  --spring.datasource.password=pgpass \
  --spring.jpa.hibernate.ddl-auto=validate \
  > /tmp/pgie-kernel.log 2>&1 &
KERNEL_PID=$!
PIDS+=("$KERNEL_PID")
for i in $(seq 1 90); do
  curl -sf --max-time 2 "http://localhost:$KERNEL_PORT/actuator/health" 2>/dev/null | grep -q '"UP"' && break
  [ "$i" = 90 ] && { tail -40 /tmp/pgie-kernel.log; die "peb-kernel never became healthy (log: /tmp/pgie-kernel.log)"; }
  sleep 2
done
say "peb-kernel UP (pid $KERNEL_PID)"

# ── Fail-closed TAP guard (identical contract to the CI job) ──────────────
record_result() { # $1 label, $2 pass, $3 fail, $4 skip, $5 floor, $6 verdict, $7 log
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" "$6" "$7" >> "$RESULTS_TSV"
}

tap_guard() { # $1 log, $2 floor, $3 label
  # Same contract as CI, checked in the same order: fail-first,
  # skip-must-fail, then the pass floor. Counts feed the evidence artifact;
  # "unknown" means the TAP summary line never appeared (crash mid-suite).
  PASSC=$(grep -E '^# pass [0-9]+$' "$1" | grep -oE '[0-9]+' | tail -1)
  FAILC=$(grep -E '^# fail [0-9]+$' "$1" | grep -oE '[0-9]+' | tail -1)
  SKIPC=$(grep -E '^# (skip|skipped) [0-9]+$' "$1" | grep -oE '[0-9]+' | tail -1)
  if ! grep -q '^# fail 0$' "$1"; then
    record_result "$3" "${PASSC:-0}" "${FAILC:-unknown}" "${SKIPC:-0}" "$2" fail "$1"
    die "$3: test failures (log: $1)"
  fi
  if ! grep -Eq '^# (skip|skipped) 0$' "$1"; then
    record_result "$3" "${PASSC:-0}" "${FAILC:-0}" "${SKIPC:-unknown}" "$2" fail "$1"
    die "$3: tests SKIPPED — integration env degraded; a skip must fail here (log: $1)"
  fi
  if [ "${PASSC:-0}" -lt "$2" ]; then
    record_result "$3" "${PASSC:-0}" 0 "${SKIPC:-0}" "$2" fail "$1"
    die "$3: only ${PASSC:-0} passed (expected >= $2)"
  fi
  record_result "$3" "${PASSC:-0}" 0 "${SKIPC:-0}" "$2" pass "$1"
  printf '\033[1;32m  ✓ %s: %s passed, 0 failed, 0 skipped\033[0m\n' "$3" "$PASSC"
}
SUITE_ENV=(PG_HOST=localhost PG_PORT="$PG_PORT" PG_USER=pguser PG_PASSWORD=pgpass PG_DB_NAME=nexus \
           MONGO_URL="mongodb://localhost:$MONGO_PORT/nexus")

run_bridge() {
  say "E2E: harness bridge producer cycle + attempt lifecycle"
  ( cd moleculer/nexus-broker && env PEB_BASE_URL="http://localhost:$KERNEL_PORT" "${SUITE_ENV[@]}" \
      node --test --test-reporter=tap \
      tests/harness-bridge.e2e.test.js tests/attempt-lifecycle.test.js ) \
    > /tmp/pgie-tap-bridge.log 2>&1 || true
  tap_guard /tmp/pgie-tap-bridge.log 10 "bridge + attempt-lifecycle"
}

run_smoke() {
  say "E2E: broker boot smoke + keychain checkpoints/rewind/SOL outbox"
  ( cd moleculer/nexus-broker && env "${SUITE_ENV[@]}" \
      node --test --test-reporter=tap tests/broker-smoke.test.js ) \
    > /tmp/pgie-tap-smoke.log 2>&1 || true
  tap_guard /tmp/pgie-tap-smoke.log 12 "broker-smoke"
}

run_parity() {
  say "building heartbeat-client (file: dependency of execution-srv)"
  if [ "$SKIP_BUILD" != "1" ]; then
    ( cd typescript/heartbeat-client && npm install --no-package-lock --silent && npm run build --silent )
  fi
  [ -f typescript/heartbeat-client/dist/index.js ] || die "heartbeat-client dist missing (run without --skip-build)"
  say "booting legacy execution-srv on :$LEGACY_PORT"
  ( cd typescript/execution-srv
    if [ "$SKIP_BUILD" != "1" ]; then npm ci --silent && npm run build --silent; fi
    [ -f dist/index.js ] || die "execution-srv dist missing (run without --skip-build)"
    env PGHOST=localhost PGPORT="$PG_PORT" PGUSER=pguser PGPASSWORD=pgpass PGDATABASE=nexus \
      PORT="$LEGACY_PORT" node dist/index.js > /tmp/pgie-execution-srv.log 2>&1 &
    echo $! > /tmp/pgie-legacy.pid )
  LEGACY_PID="$(cat /tmp/pgie-legacy.pid)"
  PIDS+=("$LEGACY_PID")
  for i in $(seq 1 30); do
    curl -sf --max-time 2 "http://localhost:$LEGACY_PORT/health" >/dev/null 2>&1 && break
    [ "$i" = 30 ] && { tail -30 /tmp/pgie-execution-srv.log; die "execution-srv never became healthy"; }
    sleep 1
  done
  say "E2E: legacy execution-srv parity"
  ( cd moleculer/nexus-broker && env LEGACY_EXECUTION_URL="http://localhost:$LEGACY_PORT" "${SUITE_ENV[@]}" \
      node --test --test-reporter=tap tests/legacy-parity.test.js ) \
    > /tmp/pgie-tap-parity.log 2>&1 || true
  tap_guard /tmp/pgie-tap-parity.log 8 "legacy-parity"
}

case "$SUITE" in
  bridge) run_bridge ;;
  smoke)  run_smoke ;;
  parity) run_parity ;;
  all)    run_bridge; run_smoke; run_parity ;;
esac

json_emit pass 0
say "DONE — suite '$SUITE' green on the throwaway stack (logs: /tmp/pgie-tap-*.log)"
