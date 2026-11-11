#!/usr/bin/env bash
# bin/mongo-backup.sh — nightly MongoDB backup (verified, local disk).
# =====================================================================
#
# Why this exists: architect handoff c9540d99 §3.1 —
#
#     "3.4M keychain records exist with zero recoverable copy. Any upgrade
#      procedure that starts by stopping this container is currently a
#      one-way operation."
#
# `bin/` had a health monitor and a start script but no dump path, and no
# Mongo backup timer existed. This is that path, scheduled by
# config/systemd/mongo-backup.timer (04:05 daily, Persistent=true).
#
# What this does (per run):
#   1. Destination guard (bin/lib/drive-guard.sh) + free-space preflight.
#   2. `mongodump --gzip --archive` straight to stdout, captured host-side,
#      so nothing has to fit in the container's writable layer.
#   3. Integrity gates — `gzip -t`, a size floor, and `mongorestore --dryRun`
#      reading the archive back through stdin. A dump that has never been
#      read back is not a backup.
#   4. sha256 manifest for the artifact.
#   5. Retention (decision 87a2a794): keep the newest KEEP_ARCHIVES (3)
#      verified archives. Pruning happens ONLY after the new archive passes
#      every gate, and `mongo-pre-upgrade-*` archives are NEVER pruned — they
#      are the rollback net for the 4.4 -> 8.0 migration and stay pinned until
#      the migration's §5 acceptance.
#   6. Success stamp -> $BACKUP_DIR/last-backup.json (watchers read it).
#   7. Failure -> best-effort incident record to nebula (to:sysadmin,
#      source:mongo-backup; guarded, never blocks).
#
# Retention CANNOT make this an off-machine copy: the destination is the same
# NVMe as the data (§4.6 stays unmet, tracked on to-do b098697a).
#
# Restore:
#   docker exec -i atomic-mongodb mongorestore --gzip --archive <archive.gz>
#
# Known limitation: auth users/roles are NOT in the archive. `mongodump
# --dumpDbUsersAndRoles` is rejected by 4.4 when the archive has no --db
# ("must specify a database when running with dumpDbUsersAndRoles"), and a
# whole-estate dump is the point here. Restoring credentials comes from
# .env / the handoff docs, not from this archive.
#
# Usage:
#   mongo-backup.sh                  # normal run
#   mongo-backup.sh --dry-run        # log the plan, touch nothing
#
# Exit codes:
#   0 success (or a dry run) · 1 failure · 3 configuration error

set -uo pipefail

# ---------------------------------------------------------------- config ---
BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXUS_ROOT="$(cd "$BIN_DIR/.." && pwd)"
ENV_FILE="${MONGO_ENV_FILE:-$NEXUS_ROOT/.env}"
CONTAINER="${MONGO_CONTAINER:-atomic-mongodb}"
BACKUP_DIR="${BACKUP_DIR:-/home/codex/backups/mongo}"
LOG_FILE="${LOG_FILE:-$BACKUP_DIR/mongo-backup.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/mongo-backup.lock}"
KEEP_ARCHIVES="${KEEP_ARCHIVES:-3}"
# Below this, abort before dumping: one archive is ~1.9 GiB and the dump
# needs headroom for the temporary file plus the archive being replaced.
MIN_FREE_GIB="${MIN_FREE_GIB:-6}"
MIN_BYTES="${MIN_BYTES:-1048576}"          # 1 MiB: an empty/failed stream is not a backup
PINNED_GLOB="mongo-pre-upgrade-*.gz"       # never pruned (migration rollback net)
NEBULA_URL="${NEBULA_URL:-http://localhost:3101/api/agent-records}"

# shellcheck source=bin/lib/drive-guard.sh
. "$BIN_DIR/lib/drive-guard.sh" \
  || { echo "mongo-backup: FATAL: drive-guard lib load failed" >&2; exit 3; }

TS="$(date -u +%Y%m%dT%H%M%SZ)"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

incident() {  # best-effort; a notification failure must never turn a run into a crash
  local title="${1:-mongo-backup FAILED ($TS)}"
  local detail="${2:-}"
  [ -z "$detail" ] && detail="Nightly MongoDB backup failed. See $LOG_FILE for detail."
  curl -s --max-time 5 -X POST "$NEBULA_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"recordType\":\"report\",\"role\":\"devops\",\"title\":\"$title\",\"content\":\"$detail\",\"tags\":[\"to:sysadmin\",\"type:incident\",\"status:open\",\"source:mongo-backup\"]}" \
    >/dev/null 2>&1 || true
}

# --------------------------------------------------------------- config ----
[ -f "$ENV_FILE" ] || { log "FAIL: $ENV_FILE not found"; exit 3; }
MONGO_URI="$(grep -E '^SPRING_DATA_MONGODB_URI=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
[ -n "$MONGO_URI" ] || { log "FAIL: SPRING_DATA_MONGODB_URI not set in $ENV_FILE"; exit 3; }

# The application URI carries a database path (/nexus). `mongodump --uri` with a
# path dumps THAT DATABASE ONLY — measured on this host: 52 MiB instead of the
# 1.9 GiB a whole-estate dump produces, with `keychains` (3.8 GB, the 3.4M
# records c9540d99 §3.1 is about) silently absent. Strip the path for the dump,
# and use explicitly qualified URIs for the metadata queries below.
DUMP_URI="$(printf '%s' "$MONGO_URI" | sed -E 's#^(mongodb(\+srv)?://[^/]+)/[^?]*#\1/#' || printf '%s' "$MONGO_URI")"
ADMIN_URI="$(printf '%s' "$MONGO_URI" | sed -E 's#^(mongodb(\+srv)?://[^/]+)/[^?]*#\1/admin#' || printf '%s' "$MONGO_URI")"
# System databases are not part of the recovery target (§3.1 is user data).
UNEXPECTED_SYSTEM_DBS="admin config local"

# ------------------------------------------------------------- preflight ---
if [ "$DRY_RUN" = 0 ]; then
  if ! drive_guard_require_dir "$BACKUP_DIR" "MongoDB backup directory"; then
    log "$DRIVE_GUARD_REASON"
    incident "mongo-backup: backup destination unavailable" \
      "No dump was attempted. $DRIVE_GUARD_REASON"
    exit 1
  fi
else
  log "[dry] destination: $BACKUP_DIR"
fi

FREE_GIB="$(df -Pk "$BACKUP_DIR" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1048576}')"
FREE_GIB="${FREE_GIB:-0}"
if [ "$FREE_GIB" -lt "$MIN_FREE_GIB" ]; then
  log "FAIL: ${FREE_GIB} GiB free on $BACKUP_DIR, need ${MIN_FREE_GIB} GiB — refusing to start a $((MIN_FREE_GIB*0)) dump into a nearly full disk"
  [ "$DRY_RUN" = 0 ] && incident "mongo-backup: insufficient disk space" \
    "${FREE_GIB} GiB free on ${BACKUP_DIR}; ${MIN_FREE_GIB} GiB required. No dump attempted."
  exit 1
fi

OUT="$BACKUP_DIR/mongo-${TS}.gz"
TMP="$OUT.part"

if [ "$DRY_RUN" = 1 ]; then
  log "[dry] would dump $CONTAINER -> $OUT"
  log "[dry] gates: gzip -t, size >= ${MIN_BYTES}B, mongorestore --dryRun over stdin"
  log "[dry] retention: keep newest ${KEEP_ARCHIVES} 'mongo-*.gz', never prune '${PINNED_GLOB}'"
  log "[dry] complete (nothing dumped, nothing pruned)"
  exit 0
fi

# ---------------------------------------------------------------- locking ---
exec 200>"$LOCK_FILE"
flock -n 200 || { log "SKIP: another backup run holds $LOCK_FILE"; exit 0; }

# ------------------------------------------------------------------ dump ---
log "dumping $CONTAINER -> $OUT"
START=$(date +%s)
#
# mongodump writes the archive to stdout when --archive has no path, so the
# bytes land on the host and nothing has to fit in the container layer.
# The URI is passed to the container as argv (same parity as the mysql tier);
# it is never echoed by this script.
# The namespace lines go to a per-run file: they are the coverage evidence.
NSLOG="$OUT.nslog"
if ! docker exec "$CONTAINER" mongodump --uri "$DUMP_URI" --gzip --archive \
      >"$TMP" 2>"$NSLOG"; then
  log "FAIL: mongodump pipeline failed"
  cat "$NSLOG" >>"$LOG_FILE" 2>/dev/null || true
  rm -f "$TMP" "$NSLOG"
  incident
  exit 1
fi
cat "$NSLOG" >>"$LOG_FILE" 2>/dev/null || true
SIZE_BYTES="$(stat -c %s "$TMP" 2>/dev/null || echo 0)"
if [ "$SIZE_BYTES" -lt "$MIN_BYTES" ]; then
  log "FAIL: archive is ${SIZE_BYTES}B (< ${MIN_BYTES}B floor) — treating as empty"
  rm -f "$TMP"
  incident "mongo-backup: archive below size floor" \
    "mongodump exited 0 but produced ${SIZE_BYTES} bytes. No usable backup."
  exit 1
fi
log "dumped $(numfmt --to=iec "$SIZE_BYTES" 2>/dev/null || echo "${SIZE_BYTES}B") in $(( $(date +%s) - START ))s"

# ----------------------------------------------------------- coverage gate --
# "The file is big enough" is not "the data is in it". Compare the namespaces
# mongodump reports against the databases that actually exist.
# The server shell differs by image: 4.4/5.0 ship `mongo`, 6.0+ ship `mongosh`.
# This job must keep working across the 4.4 -> 8.0 migration, and assuming one
# of them silently disables the gate on the other (measured: on mongo:8.0,
# `exec: "mongo": executable file not found in $PATH` -> "coverage gate
# skipped" -> a partial archive could be promoted as a backup). Probe instead.
_mongo_eval() {
  local sh out
  for sh in mongosh mongo; do
    if out="$(docker exec "$CONTAINER" "$sh" --quiet "$ADMIN_URI" --eval "$1" 2>/dev/null)"; then
      printf '%s' "$out" | tr -d '\r' | tail -1
      return 0
    fi
  done
  return 1
}
EXPECTED_DBS="$(_mongo_eval \
  'print(db.adminCommand({listDatabases:1}).databases.map(d=>d.name).filter(n=>["admin","config","local"].indexOf(n)<0).sort().join(" "))' \
  || true)"
# The namespace log format changed at 6.0: 4.4 prints
#   writing keychains.entries to archive on stdout
# while 8.0 quotes the identifiers:
#   writing `nexus.docklang` to `archive on stdout`
# Parsing only the older form made the coverage gate see NOTHING on 8.0 and
# reject a perfectly good archive ("missing databases: keychains legacy-audit
# nexus (dump covered: nothing)"). Strip backticks before matching so one
# parser covers both.
DUMPED_DBS="$(sed 's/`//g' "$NSLOG" 2>/dev/null \
  | grep -oE 'writing [A-Za-z0-9_.-]+ to archive' \
  | awk '{print $2}' | cut -d. -f1 | sort -u | tr '\n' ' ' | sed -E 's/ +$//' || true)"
if [ -n "$EXPECTED_DBS" ]; then
  MISSING=""
  for db in $EXPECTED_DBS; do
    case " $DUMPED_DBS " in
      *" $db "*) ;;
      *) MISSING="$MISSING $db" ;;
    esac
  done
  if [ -n "$MISSING" ]; then
    log "FAIL: archive is missing databases:$MISSING (dump covered: ${DUMPED_DBS:-nothing})"
    rm -f "$TMP" "$NSLOG"
    incident "mongo-backup: archive does not cover every database" \
      "mongodump produced ${SIZE_BYTES} bytes but skipped:$MISSING. Covered: ${DUMPED_DBS:-none}. Archive discarded."
    exit 1
  fi
  log "coverage: all ${EXPECTED_DBS} databases present in archive"
else
  log "WARNING: could not list databases (server shell unavailable?); coverage gate skipped"
fi
rm -f "$NSLOG"

# --------------------------------------------------------------- integrity --
if ! gzip -t "$TMP" 2>>"$LOG_FILE"; then
  log "FAIL: gzip -t rejected $TMP"
  rm -f "$TMP"
  incident
  exit 1
fi
log "gzip -t ok"

# Read the archive back through the server's own tools. This is the gate that
# distinguishes "a file exists" from "a backup exists".
if ! docker exec -i "$CONTAINER" mongorestore --gzip --archive --dryRun \
      <"$TMP" >>"$LOG_FILE" 2>&1; then
  log "FAIL: mongorestore --dryRun rejected the archive"
  rm -f "$TMP"
  incident "mongo-backup: archive failed dryRun restore" \
    "mongodump produced ${SIZE_BYTES} bytes but mongorestore --dryRun rejected it. Archive discarded."
  exit 1
fi
if grep -qE '[1-9][0-9]* document\(s\) failed' "$LOG_FILE" 2>/dev/null; then
  log "FAIL: dryRun reported document failures"
  rm -f "$TMP"
  incident "mongo-backup: dryRun reported document failures" \
    "mongorestore --dryRun completed but reported failed documents. Archive discarded."
  exit 1
fi
log "mongorestore --dryRun ok (0 documents failed)"

mv -f "$TMP" "$OUT"
log "verified archive: $OUT"

# --------------------------------------------------------------- manifest ---
( cd "$BACKUP_DIR" && sha256sum "$(basename "$OUT")" > "manifest__${TS}.sha256" )
SHA="$(awk '{print $1}' "$BACKUP_DIR/manifest__${TS}.sha256")"
log "manifest: sha256 ${SHA:0:16}…"

# -------------------------------------------------------------- retention ---
# Prune only now that the replacement is verified. Pinned migration archives
# are excluded by name and are never candidates.
PRUNED=0
mapfile -t ARCHIVES < <(find "$BACKUP_DIR" -maxdepth 1 -name 'mongo-*.gz' ! -name "$PINNED_GLOB" -printf '%T@ %p\n' \
                          | sort -rn | awk '{print $2}')
if [ "${#ARCHIVES[@]}" -gt "$KEEP_ARCHIVES" ]; then
  for old in "${ARCHIVES[@]:$KEEP_ARCHIVES}"; do
    case "$(basename "$old")" in
      $PINNED_GLOB) continue ;;                 # belt and braces
    esac
    if rm -f "$old" "$BACKUP_DIR/manifest__$(basename "${old%.gz}" | sed 's/^mongo-//').sha256"; then
      log "pruned $(basename "$old")"; PRUNED=$((PRUNED+1))
    fi
  done
fi
PINNED_COUNT="$(find "$BACKUP_DIR" -maxdepth 1 -name "$PINNED_GLOB" | wc -l)"
log "retention: kept ${KEEP_ARCHIVES}, pruned ${PRUNED}, pinned migration archives ${PINNED_COUNT} untouched"

# ------------------------------------------------------------------ stamp ---
IMG_VERSION="$(docker inspect -f '{{.Config.Image}}' "$CONTAINER" 2>/dev/null || echo unknown)"
cat > "$BACKUP_DIR/last-backup.json" <<EOF
{
  "last_backup": "$(date -Iseconds)",
  "file": "$(basename "$OUT")",
  "bytes": $SIZE_BYTES,
  "sha256": "$SHA",
  "container": "$CONTAINER",
  "image": "$IMG_VERSION",
  "retention_keep": $KEEP_ARCHIVES,
  "pinned_untouched": $PINNED_COUNT,
  "off_machine": false
}
EOF
log "stamp written: $BACKUP_DIR/last-backup.json"
log "=== backup run complete (ok) ==="
exit 0
