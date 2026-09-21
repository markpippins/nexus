#!/usr/bin/env bash
# pg-backup-to-vanadium.sh — off-machine PostgreSQL backup pipeline.
#
# Context: the replication target is VANADIUM since 2026-09-05 (per
# /home/codex/dev/pgsql/pg-backup.env, pg-backup.env REMOTE_HOST). Target
# history: strontium (down for repair, 2026-08) → barium (2026-08-25;
# disk-full forensics, now retired from backup duty entirely) → vanadium.
# Logical dumps are architecture-neutral, so cross-arch is a non-issue.
#
# What this does (per run):
#   1. Full custom-format dump (-Fc) of EVERY non-template database
#      (the legacy backup-nexus.sh covers only `nexus`, plain SQL).
#   2. Cluster globals (roles/tablespaces) via pg_dumpall --globals-only.
#   3. Integrity gate: every archive must pass `pg_restore --list`.
#   4. sha256 manifest for all artifacts.
#   5. rsync artifacts to vanadium:$REMOTE_DIR/
#   6. Verify checksums ON VANADIUM (catches silent transfer corruption).
#   7. GFS retention on vanadium: RETAIN_DAILY dailies + RETAIN_WEEKLY
#      Sundays + RETAIN_MONTHLY month-starts, hard age cap.
#   8. Prune local spool (LOCAL_KEEP_DAYS) so titanium disk doesn't fill.
#
# Failure behavior: nonzero exit (visible to the systemd timer/journal) and
# a best-effort incident record to nebula tagged to:sysadmin (guarded, never
# blocks the backup itself).
#
# Schedule: user-level systemd timer (backup-pg-to-vanadium.timer), 03:30
# daily, Persistent=true catch-up — cron retired per architect decision
# 13600407.
#
# Usage:
#   pg-backup-to-vanadium.sh               # normal run
#   pg-backup-to-vanadium.sh --dry-run     # dump nothing; show plan + retention
#   pg-backup-to-vanadium.sh --verify-last # re-verify newest remote manifest

set -u -o pipefail

# ---------------------------------------------------------------- config ---
CONTAINER="${CONTAINER:-pgvector_db}"
PGUSER="${PGUSER:-pguser}"
REMOTE_HOST="${REMOTE_HOST:-vanadium}"
REMOTE_USER="${REMOTE_USER:-}"            # empty → ssh config default
HOSTNAME_SHORT="$(hostname -s)"           # subdir on vanadium: /pg-backups/titanium
REMOTE_DIR="${REMOTE_DIR:-pg-backups/${HOSTNAME_SHORT}}"
SPOOL_DIR="${SPOOL_DIR:-/home/codex/dev/pgsql/spool}"
LOG_FILE="${LOG_FILE:-/home/codex/dev/pgsql/pg-backup-to-vanadium.log}"
LOCK_FILE="/tmp/pg-backup-to-vanadium.lock"

RETAIN_DAILY="${RETAIN_DAILY:-14}"
RETAIN_WEEKLY="${RETAIN_WEEKLY:-5}"
RETAIN_MONTHLY="${RETAIN_MONTHLY:-3}"
MAX_AGE_DAYS="${MAX_AGE_DAYS:-180}"       # absolute cap, anything older dies
LOCAL_KEEP_DAYS="${LOCAL_KEEP_DAYS:-2}"

NEBULA_URL="${NEBULA_URL:-http://localhost:3101/api/agent-records}"

TS="$(date +%Y%m%d_%H%M%S)"
FAILED=0
DRY_RUN=0
VERIFY_LAST=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1
[ "${1:-}" = "--verify-last" ] && VERIFY_LAST=1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

ssh_remote() {
  if [ -n "$REMOTE_USER" ]; then ssh -o BatchMode=yes "${REMOTE_USER}@${REMOTE_HOST}" "$@"
  else ssh -o BatchMode=yes "$REMOTE_HOST" "$@"; fi
}

incident() {  # best-effort alert; NEVER lets a notification failure kill us
  curl -s --max-time 5 -X POST "$NEBULA_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"recordType\":\"report\",\"role\":\"devops\",\"title\":\"pg-backup-to-vanadium FAILED ($TS)\",\"content\":\"Nightly PG backup to vanadium failed. See $LOG_FILE on titanium for detail.\",\"tags\":[\"to:sysadmin\",\"type:incident\",\"status:open\",\"source:pg-backup\"]}" \
    >/dev/null 2>&1 || true
}

# --------------------------------------------------------------- locking ---
exec 200>"$LOCK_FILE"
flock -n 200 || { log "SKIP: another backup run holds the lock"; exit 0; }

if [ "$VERIFY_LAST" = 1 ]; then
  LATEST_MANIFEST="$(ssh_remote "ls -1 ${REMOTE_DIR}/manifest__*.txt 2>/dev/null | sort | tail -1")"
  if [ -z "$LATEST_MANIFEST" ]; then log "FAIL: no remote manifest found"; exit 1; fi
  log "Verifying latest remote set: $LATEST_MANIFEST"
  if ssh_remote "cd \$(dirname $LATEST_MANIFEST) && sha256sum -c \$(basename $LATEST_MANIFEST)"; then
    log "VERIFY OK"; exit 0
  else
    log "VERIFY FAILED"; incident; exit 1
  fi
fi

# ------------------------------------------------------------ discovery ---
DBS="$(docker exec "$CONTAINER" psql -U "$PGUSER" -d postgres -tA \
  -c "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres' ORDER BY 1;" )"
if [ -z "$DBS" ]; then log "FATAL: could not enumerate databases"; incident; exit 1; fi

log "=== backup run start (dry_run=$DRY_RUN) dbs: $(echo $DBS | tr '\n' ' ')"

mkdir -p "$SPOOL_DIR"

# ----------------------------------------------------------------- dumps ---
for db in $DBS; do
  OUT="$SPOOL_DIR/${db}__${TS}.dump"
  if [ "$DRY_RUN" = 1 ]; then log "[dry] would dump $db -> $OUT"; continue; fi
  START=$(date +%s)
  if ! docker exec "$CONTAINER" pg_dump -U "$PGUSER" -Fc -Z6 "$db" > "$OUT"; then
    log "FAIL: pg_dump $db"; rm -f "$OUT"; FAILED=1; continue
  fi
  # integrity gate: archive must be readable and list its TOC
  if ! docker exec -i "$CONTAINER" pg_restore --list < "$OUT" > /dev/null 2>&1; then
    log "FAIL: pg_restore --list rejected $db archive"; rm -f "$OUT"; FAILED=1; continue
  fi
  log "dumped $db ($(du -h "$OUT" | cut -f1), $(( $(date +%s) - START ))s)"
done

# globals (roles/tablespaces) — tiny but makes restores turnkey
GLOB_OUT="$SPOOL_DIR/globals__${TS}.sql.gz"
if [ "$DRY_RUN" = 1 ]; then
  log "[dry] would dump globals -> $GLOB_OUT"
else
  docker exec "$CONTAINER" pg_dumpall -U "$PGUSER" --globals-only 2>/dev/null \
    | grep -v '^CREATE ROLE pguser;' | gzip > "$GLOB_OUT"
  [ -s "$GLOB_OUT" ] || { log "WARN: globals dump empty (continuing)"; rm -f "$GLOB_OUT"; }
fi

if [ "$FAILED" = 1 ] && [ "$DRY_RUN" = 0 ]; then
  log "ABORT before transfer: dump failures present"; incident; exit 1
fi
[ "$DRY_RUN" = 1 ] && { log "=== dry run complete ==="; exit 0; }

# -------------------------------------------------------------- manifest --
MANIFEST="$SPOOL_DIR/manifest__${TS}.txt"
( cd "$SPOOL_DIR" && sha256sum *__"$TS".dump globals__"$TS".sql.gz 2>/dev/null > "$MANIFEST" )

# --------------------------------------------------------------- transfer -
START=$(date +%s)
if ! ssh_remote "mkdir -p $REMOTE_DIR"; then
  log "FAIL: cannot create remote dir $REMOTE_DIR on $REMOTE_HOST"; incident; exit 1
fi
if ! rsync -a --partial "$SPOOL_DIR"/*__"${TS}".* "${REMOTE_HOST}:${REMOTE_DIR}/"; then
  log "FAIL: rsync to $REMOTE_HOST"; incident; exit 1
fi
log "rsync complete ($(( $(date +%s) - START ))s)"

# ------------------------------------------------------ remote validation -
if ! ssh_remote "cd $REMOTE_DIR && sha256sum -c manifest__${TS}.txt --quiet"; then
  log "FAIL: remote checksum verification"; incident; exit 1
fi
log "remote checksum verification OK"

# ------------------------------------------------------------- retention --
# GFS: newest RETAIN_DAILY always kept; beyond that keep Sundays (weekly,
# RETAIN_WEEKLY) and month-firsts (monthly, RETAIN_MONTHLY); hard age cap.
# Shipped via a QUOTED heredoc: nothing expands on the local side; the few
# values the block needs travel as argv: $1 REMOTE_DIR, $2 RETAIN_DAILY,
# $3 RETAIN_WEEKLY, $4 RETAIN_MONTHLY, $5 MAX_AGE_DAYS, $6+ database names.
# Date math decodes YYYYMMDD via `date -d "<y>-01-01 +N months +M days"`
# (PG-safe, no substring arithmetic on date strings) and is fail-closed:
# an undecodable stamp is skipped, never deleted blindly. The previous
# unquoted heredoc mangled quoting AND put $( ) inside $(( )) — the greedy
# close made every remote prune abort on its first file, so remote GFS
# retention was a silent no-op (nothing was ever deleted remotely).
ssh_remote bash -s -- "$REMOTE_DIR" "$RETAIN_DAILY" "$RETAIN_WEEKLY" \
  "$RETAIN_MONTHLY" "$MAX_AGE_DAYS" $DBS <<'REMOTE_EOF'
set -u
REMOTE_DIR="$1"; RETAIN_DAILY="$2"; RETAIN_WEEKLY="$3"; RETAIN_MONTHLY="$4"; MAX_AGE_DAYS="$5"
shift 5
DBS="$*"
cd "$REMOTE_DIR" || { echo "retention: cannot cd $REMOTE_DIR" >&2; exit 3; }
deleted=0

pgsql2epoch() {  # YYYYMMDD -> UTC epoch seconds; nonzero on undecodable stamp
  case "$1" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) return 1 ;;
  esac
  date -u -d "${1:0:4}-01-01 +$((10#${1:4:2}-1)) months +$((10#${1:6:2}-1)) days" +%s
}

prune_gfs() {
  local glob="$1"
  local daily="$RETAIN_DAILY" weeks_left="$RETAIN_WEEKLY" months_left="$RETAIN_MONTHLY" n=0 f stamp d age dow mo
  local -A wk_seen mo_seen
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    stamp="${f#*__}"; stamp="${stamp%.*}"
    d="${stamp:0:8}"
    # two-step: fallible substitution OUTSIDE arithmetic, so a bad stamp is
    # an ordinary command failure we can skip on (never a parse abort).
    # Count only decodable files: an undecodable name sorts ahead of dated
    # files and must not consume a keep slot.
    now=$(date -u +%s)
    epoch=$(pgsql2epoch "$d") || { echo "retention: skip undecodable $f" >&2; continue; }
    n=$((n+1))
    age=$(( (now - epoch) / 86400 ))
    if [ "$age" -gt "$MAX_AGE_DAYS" ]; then rm -f -- "$f"; deleted=$((deleted+1)); continue; fi
    [ "$n" -le "$daily" ] && continue
    dow=$(date -u -d "${d:0:4}-${d:4:2}-${d:6:2}" +%u)   # 7 = Sunday
    mo="${d:0:6}"
    if [ "${d:6:2}" = "01" ] && [ -z "${mo_seen[$mo]:-}" ]; then
      mo_seen[$mo]=1
      if [ "$months_left" -gt 0 ]; then months_left=$((months_left-1)); continue; fi
    elif [ "$dow" = "7" ] && [ -z "${wk_seen[$mo]:-}" ]; then
      wk_seen[$mo]=1
      if [ "$weeks_left" -gt 0 ]; then weeks_left=$((weeks_left-1)); continue; fi
    fi
    rm -f -- "$f"; deleted=$((deleted+1))
  done < <(ls -1 $glob 2>/dev/null | sort -r)
}
for db in $DBS; do prune_gfs "${db}__*.dump"; done
prune_gfs "manifest__*.txt"
prune_gfs "globals__*.sql.gz"
df -h / | tail -1
echo "retention: removed $deleted file(s)"
REMOTE_EOF
log "retention applied"

# --------------------------------------------------------- local pruning --
find "$SPOOL_DIR" -type f -mtime +"$LOCAL_KEEP_DAYS" -delete
log "local spool pruned (> ${LOCAL_KEEP_DAYS}d)"
log "=== backup run complete (ok) ==="
exit 0
