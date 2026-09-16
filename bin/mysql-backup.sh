#!/usr/bin/env bash
# bin/mysql-backup.sh — nightly MySQL (Kodi shared library) backup.
# ================================================================
#
# Per architect decision 13600407 (2026-08-09): systemd user timers are
# the single OS-level substrate; cron stays retired. Run via
# mysql-backup.timer (03:45 daily, Persistent=true).
#
# What this does (per run):
#   1. Full logical dump of EVERY database (kodi, kodi_video, kodi_music,
#      mysql, sys, ...) via mysqldump --all-databases, gzip-compressed.
#   2. Integrity gates: mysqldump exit status + `gzip -t` on the archive.
#   3. sha256 manifest for the batch.
#   4. Retention: dumps older than KEEP_DAYS (14) are pruned.
#   5. Success stamp -> $BACKUP_DIR/last-backup.json. mysql-health-monitor.sh
#      reads this stamp and alerts ("backup stale") if no fresh success
#      appears within STALE_HOURS — that is the SUCCESS side of the alert.
#   6. Failure -> best-effort incident record to nebula tagged to:sysadmin
#      (same channel as pg-backup-to-barium.sh; guarded, never blocks).
#
# Restore (all DBs):
#   gunzip < all__YYYYMMDD_HHMMSS.sql.gz | docker exec -i my-mysql mysql -uroot -prootpass
#
# Usage:
#   mysql-backup.sh                 # normal run
#   mysql-backup.sh --dry-run       # log plan only

set -uo pipefail

# ---------------------------------------------------------------- config ---
CONTAINER="${CONTAINER:-my-mysql}"
MYSQL_USER="${MYSQL_USER:-root}"
MYSQL_PASS="${MYSQL_PASS:-rootpass}"
BACKUP_DIR="${BACKUP_DIR:-/home/codex/backups/mysql}"
LOG_FILE="${LOG_FILE:-${BACKUP_DIR}/mysql-backup.log}"
LOCK_FILE="/tmp/mysql-backup.lock"
KEEP_DAYS="${KEEP_DAYS:-14}"
NEBULA_URL="${NEBULA_URL:-http://localhost:3101/api/agent-records}"

# Drive guard (audit f74eb976): honest absent-drive failures for the backup
# script family. See bin/lib/drive-guard.sh for the semantics.
# shellcheck source=bin/lib/drive-guard.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/drive-guard.sh" \
  || { echo "drive-guard: FATAL: lib load failed" >&2; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

incident() {  # best-effort alert; NEVER lets a notification failure kill us
  local title="${1:-mysql-backup FAILED ($TS)}"
  local detail="${2:-}"
  if [ -z "$detail" ]; then
    # Stamp is itself JSON; strip quotes/newlines so the incident payload
    # stays valid JSON (raw embedding used to break it silently).
    local stamp
    stamp="$(cat "${BACKUP_DIR}/last-backup.json" 2>/dev/null | tr -d '\"\n' || echo none)"
    detail="Nightly MySQL (kodi) backup failed. See $LOG_FILE for detail. Last good stamp: ${stamp}."
  fi
  curl -s --max-time 5 -X POST "$NEBULA_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"recordType\":\"report\",\"role\":\"devops\",\"title\":\"$title\",\"content\":\"$detail\",\"tags\":[\"to:sysadmin\",\"type:incident\",\"status:open\",\"source:mysql-backup\"]}" \
    >/dev/null 2>&1 || true
}

# --------------------------------------------------------------- locking ---
exec 200>"$LOCK_FILE"
flock -n 200 || { log "SKIP: another backup run holds the lock"; exit 0; }

# Drive guard (audit f74eb976): BACKUP_DIR must be usable BEFORE anything
# runs. A dangling symlink to an absent removable drive used to fail later
# with the misleading "mysqldump pipeline failed" (records 0abb6df5 ->
# cd776865). A primary backup destination being unavailable is a FAIL, not
# a skip — silently skipping would hide a coverage gap.
if ! drive_guard_require_dir "$BACKUP_DIR" "backup directory"; then
  log "$DRIVE_GUARD_REASON"
  incident "mysql-backup: backup destination unavailable" \
    "No dump was attempted. $DRIVE_GUARD_REASON"
  exit 1
fi

# ---------------------------------------------------------------- dump -----
OUT="$BACKUP_DIR/all__${TS}.sql.gz"
if [ "$DRY_RUN" = 1 ]; then
  log "[dry] would dump all databases -> $OUT"
  log "[dry] complete"
  exit 0
fi

START=$(date +%s)
if ! docker exec "$CONTAINER" mysqldump -uroot -p"$MYSQL_PASS" \
      --all-databases --single-transaction --routines --triggers --events 2>>"$LOG_FILE" \
  | gzip -n > "$OUT"; then
  log "FAIL: mysqldump pipeline failed"
  rm -f "$OUT"
  incident
  exit 1
fi
# integrity gates: gzip must be valid; dump should not be near-empty
if ! gzip -t "$OUT" 2>>"$LOG_FILE"; then
  log "FAIL: gzip -t rejected $OUT"
  rm -f "$OUT"
  incident
  exit 1
fi
SIZE=$(du -h "$OUT" | cut -f1)
log "dumped -> $OUT ($SIZE, $(( $(date +%s) - START ))s)"

# -------------------------------------------------------------- manifest --
( cd "$BACKUP_DIR" && sha256sum all__"$TS".sql.gz > "manifest__${TS}.sha256" )
log "manifest written"

# ------------------------------------------------------------- retention ---
PRUNED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'all__*.sql.gz' -mtime +"$KEEP_DAYS" -delete -print | wc -l)
log "retention: pruned $PRUNED dump(s) older than ${KEEP_DAYS}d"

# --------------------------------------------------------------- stamp -----
cat > "$BACKUP_DIR/last-backup.json" <<EOF
{
  "last_backup": "$(date -Iseconds)",
  "file": "$(basename "$OUT")",
  "size": "$SIZE",
  "sha256": "$(awk '{print $1}' "$BACKUP_DIR/manifest__${TS}.sha256")"
}
EOF
log "=== backup run complete (ok) ==="
exit 0