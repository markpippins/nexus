#!/usr/bin/env bash
# bin/pg-escape-hatch.sh — local nightly nexus PG backup (travel tier).
# ========================================================================
# Purpose (audit f74eb976 zero-coverage finding): the canonical PG backup
# tier ships off-machine (pg-backup-to-vanadium.service; R9). While off the
# home network, vanadium is unreachable and NO nexus PG backup exists
# anywhere. This tier writes a verified dump to the LOCAL disk first —
# 03:10 daily, layered BEFORE the 03:42 vanadium tier — so coverage is
# never zero, on the road or at home.
#
# What this does (per run):
#   1. pg_dump -Fc (custom format, compressed) of the `nexus` database
#      from the live pgvector_db (localhost:5432).
#   0. Version guard (fleet PG audit ecb91216 / ask R4): host pg_dump major
#      must be >= target server major — this is the ONE backup path using
#      bare host clients, and an older client can silently produce an
#      archive it cannot fully represent. Fail loudly, never dump skewed.
#   2. Verification gate: `pg_restore --list` must parse the archive —
#      a dump that cannot be read back is not a backup.
#   3. sha256 manifest for the artifact.
#   4. Retention: dumps older than KEEP_DAYS (7) are pruned.
#   5. Success stamp -> $BACKUP_DIR/last-backup.json.
#   6. Failure -> best-effort incident record to nebula (to:sysadmin,
#      source:pg-escape-hatch; guarded, never blocks).
#
# Drive guard (audit f74eb976 / record cd776865): the destination is
# guarded by bin/lib/drive-guard.sh. A dangling symlink to an absent
# removable drive must produce an HONEST fail-fast (this is a PRIMARY
# backup destination — silently skipping would hide a coverage gap),
# never the misleading "mkdir: File exists" cascade.
#
# Restore:
#   pg_restore -h localhost -p 5432 -U pguser -d nexus --clean --if-exists \
#     <dump-file>
#
# Usage:
#   pg-escape-hatch.sh                 # normal run
#   pg-escape-hatch.sh --dry-run       # log plan only

set -uo pipefail

# ---------------------------------------------------------------- config ---
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-pguser}"
PGPASSWORD="${PGPASSWORD:-pgpass}"
PGDATABASE="${PGDATABASE:-nexus}"
BACKUP_DIR="${BACKUP_DIR:-/home/codex/backups/nexus-pg}"
LOG_FILE="${LOG_FILE:-${BACKUP_DIR}/pg-escape-hatch.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/pg-escape-hatch.lock}"
KEEP_DAYS="${KEEP_DAYS:-7}"
NEBULA_URL="${NEBULA_URL:-http://localhost:3101/api/agent-records}"

# Drive guard (see lib header for semantics).
# shellcheck source=bin/lib/drive-guard.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/drive-guard.sh" \
  || { echo "drive-guard: FATAL: lib load failed" >&2; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

incident() {  # best-effort alert; NEVER lets a notification failure kill us
  local title="${1:-pg-escape-hatch FAILED ($TS)}"
  local detail="${2:-}"
  if [ -z "$detail" ]; then
    local stamp
    stamp="$(cat "${BACKUP_DIR}/last-backup.json" 2>/dev/null | tr -d '"\n' || echo none)"
    detail="Local nexus PG escape-hatch backup failed. See $LOG_FILE for detail. Last good stamp: ${stamp}."
  fi
  curl -s --max-time 5 -X POST "$NEBULA_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"recordType\":\"report\",\"role\":\"devops\",\"title\":\"$title\",\"content\":\"$detail\",\"tags\":[\"to:sysadmin\",\"type:incident\",\"status:open\",\"source:pg-escape-hatch\"]}" \
    >/dev/null 2>&1 || true
}

# --------------------------------------------------------------- locking ---
exec 200>"$LOCK_FILE"
flock -n 200 || { log "SKIP: another backup run holds the lock"; exit 0; }

# ---------------------------------------------------------- drive guard ----
# PRIMARY destination: fail-fast honestly if it is unusable (rc 90/91/92).
if ! drive_guard_require_dir "$BACKUP_DIR" "backup directory"; then
  log "$DRIVE_GUARD_REASON"
  incident "pg-escape-hatch: backup destination unavailable" \
    "No dump was attempted. $DRIVE_GUARD_REASON"
  exit 1
fi

# ------------------------------------------------- client/server version ----
# R4 (fleet PG audit, thread ecb91216): this is the ONE backup path that runs
# BARE HOST clients (pg_dump/pg_restore) instead of server-side docker-exec
# clients (the vanadium tier shells INTO the containers, client==server). A
# pg_dump OLDER than the target server cannot represent newer catalog
# features and may emit an archive the verification gate accepts but a
# future restore mishandles. Same doctrine as the drive guard: honest
# fail-fast beats a silent coverage gap.
#
# Policy: real path — hard fail on major(client) < major(server), or when
# either version cannot be determined. Dry-run — warn only (no dump is
# performed; the operator may be inspecting the plan on a host whose client
# they cannot fix right now).
export PGPASSWORD
CLIENT_MAJOR="$(pg_dump --version 2>/dev/null | sed -n 's/^[^0-9]*\([0-9][0-9]*\).*/\1/p')"
case "$CLIENT_MAJOR" in ''|*[!0-9]*) CLIENT_MAJOR="" ;; esac
SERVER_NUM="$(psql -X -Atq -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
  -c 'SHOW server_version_num' 2>>"$LOG_FILE" | tr -d '[:space:]')"
case "$SERVER_NUM" in ''|*[!0-9]*) SERVER_NUM=0 ;; esac
SERVER_MAJOR=$(( SERVER_NUM / 10000 ))

if [ -z "$CLIENT_MAJOR" ] || [ "$SERVER_MAJOR" -eq 0 ]; then
  REASON="cannot determine versions (client='${CLIENT_MAJOR:-?}', server_num='${SERVER_NUM}')"
  if [ "$DRY_RUN" = 1 ]; then
    log "[dry] WARNING: version guard $REASON — not enforced in dry-run"
  else
    log "FAIL: pg client/server version guard: $REASON"
    incident "pg-escape-hatch: version guard cannot verify client" \
      "pg_dump client/server versions could not be determined ($REASON). No dump attempted. Install/verify postgresql-client (>= server major) on this host. See $LOG_FILE."
    exit 1
  fi
elif [ "$CLIENT_MAJOR" -lt "$SERVER_MAJOR" ]; then
  if [ "$DRY_RUN" = 1 ]; then
    log "[dry] WARNING: pg client/server major skew: host pg_dump ${CLIENT_MAJOR}.x < server ${SERVER_MAJOR}.x — real runs would fail (restore-incompatible dump risk)"
  else
    log "FAIL: pg client/server major skew: host pg_dump ${CLIENT_MAJOR}.x < server ${SERVER_MAJOR}.x ($PGDATABASE@$PGHOST:$PGPORT)"
    log "      remediation: install postgresql-client-${SERVER_MAJOR} (or newer) on this host"
    incident "pg-escape-hatch: pg client older than server" \
      "host pg_dump ${CLIENT_MAJOR}.x < server ${SERVER_MAJOR}.x — backup NOT attempted (restore-incompatible dump risk). Remediation: install postgresql-client-${SERVER_MAJOR}. See $LOG_FILE."
    exit 1
  fi
else
  log "pg client ${CLIENT_MAJOR}.x >= server ${SERVER_MAJOR}.x — version guard ok"
fi

# ---------------------------------------------------------------- dump -----
OUT="$BACKUP_DIR/nexus__${TS}.dump"
if [ "$DRY_RUN" = 1 ]; then
  log "[dry] would pg_dump -Fc $PGDATABASE@$PGHOST:$PGPORT -> $OUT"
  log "[dry] would verify via pg_restore --list, write manifest + stamp"
  log "[dry] complete"
  exit 0
fi

START=$(date +%s)
if ! pg_dump -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" \
      --format=custom --file "$OUT" 2>>"$LOG_FILE"; then
  log "FAIL: pg_dump pipeline failed"
  rm -f "$OUT"
  incident
  exit 1
fi

# ----------------------------------------------------- verification gate ---
# A dump that cannot be read back is not a backup: pg_restore --list must
# parse the archive cleanly.
if ! pg_restore --list "$OUT" >/dev/null 2>>"$LOG_FILE"; then
  log "FAIL: pg_restore --list rejected $OUT — archive unreadable"
  rm -f "$OUT"
  incident "pg-escape-hatch: verification FAILED" \
    "pg_restore --list rejected $OUT after dump; artifact removed. See $LOG_FILE."
  exit 1
fi

SIZE=$(du -h "$OUT" | cut -f1)
log "dumped + verified -> $OUT ($SIZE, $(( $(date +%s) - START ))s)"

# -------------------------------------------------------------- manifest ---
( cd "$BACKUP_DIR" && sha256sum "$(basename "$OUT")" > "manifest__${TS}.sha256" )
log "manifest written"

# ------------------------------------------------------------- retention ---
PRUNED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'nexus__*.dump' -mtime +"$KEEP_DAYS" -delete -print | wc -l)
log "retention: pruned $PRUNED dump(s) older than ${KEEP_DAYS}d"

# --------------------------------------------------------------- stamp -----
cat > "$BACKUP_DIR/last-backup.json" <<EOF
{
  "last_backup": "$(date -Iseconds)",
  "file": "$(basename "$OUT")",
  "size": "$SIZE",
  "sha256": "$(awk '{print $1}' "$BACKUP_DIR/manifest__${TS}.sha256")",
  "database": "$PGDATABASE",
  "tier": "local-escape-hatch"
}
EOF
log "=== escape-hatch backup run complete (ok) ==="
exit 0
