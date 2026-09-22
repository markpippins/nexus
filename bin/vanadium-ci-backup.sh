#!/usr/bin/env bash
# vanadium-ci-backup.sh — CI-state backup from vanadium to vanadium (retargeted
# 2026-09-10 from barium, which is offline for disk-full forensics; see record
# e5cc1f06 and AGENTS.md R9).
#
# Extends the strontium-outage backup tier (see pg-backup-to-vanadium.sh) to
# cover the vanadium CI stack, per admin approval 2026-08-22 (devops forum
# thread 36a2f788, answer #5). Runs ON TITANIUM and orchestrates both remotes
# over SSH: vanadium produces artifacts into its own /tmp, titanium pulls,
# checksums, ships to vanadium, verifies there, applies GFS retention.
#
# What is covered:
#   - vd-ci-jenkins      : /var/jenkins_home          (tar.gz via docker exec)
#   - vd-ci-sonarqube    : /opt/sonarqube/data|extensions (tar.gz)
#   - vd-ci-sonar-db     : pg_dump -Fc of the `sonar` database
#
# Schedule: user-level systemd timer backup-vanadium-ci.timer,
# daily 04:15 (after the 03:30 PG pipeline clears). Persistent=true.
#
# Usage: vanadium-ci-backup.sh [--dry-run]

set -u -o pipefail

VD_HOST="${VD_HOST:-vanadium}"
BAR_HOST="${BAR_HOST:-vanadium}"
REMOTE_DIR="${REMOTE_DIR:-pg-backups/vanadium-ci}"
SPOOL_DIR="${SPOOL_DIR:-/home/codex/dev/pgsql/vdci-spool}"
LOG_FILE="${LOG_FILE:-/home/codex/dev/pgsql/vanadium-ci-backup.log}"
LOCK_FILE="/tmp/vanadium-ci-backup.lock"

JENKINS_C="${JENKINS_C:-vd-ci-jenkins}"
SONAR_C="${SONAR_C:-vd-ci-sonarqube}"
SONAR_DB_C="${SONAR_DB_C:-vd-ci-sonar-db}"
SONAR_DB_USER="${SONAR_DB_USER:-sonar}"

RETAIN_DAILY=14; RETAIN_WEEKLY=5; RETAIN_MONTHLY=3; MAX_AGE_DAYS=180
LOCAL_KEEP_DAYS=2
NEBULA_URL="${NEBULA_URL:-http://localhost:3101/api/agent-records}"

# Drive guard (audit f74eb976): the spool is scratch space on a removable
# drive (vdci-spool -> /mnt/SiP1TB/...). See bin/lib/drive-guard.sh.
# shellcheck source=bin/lib/drive-guard.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/drive-guard.sh" \
  || { echo "drive-guard: FATAL: lib load failed" >&2; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
DRY_RUN=0; [ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
incident() {
  local title="${1:-vanadium-ci backup FAILED ($TS)}"
  local detail="${2:-Vanadium CI backup to vanadium failed. See $LOG_FILE on titanium.}"
  curl -s --max-time 5 -X POST "$NEBULA_URL" -H 'Content-Type: application/json' \
    -d "{\"recordType\":\"report\",\"role\":\"devops\",\"title\":\"$title\",\"content\":\"$detail\",\"tags\":[\"to:sysadmin\",\"type:incident\",\"status:open\",\"source:vdci-backup\"]}" \
    >/dev/null 2>&1 || true
}

exec 200>"$LOCK_FILE"
flock -n 200 || { log "SKIP: lock held"; exit 0; }

# Drive guard (audit f74eb976, finding #4 — silent no-op): the spool lives
# on a removable drive. When the drive is absent the OLD code failed here
# with the misleading "mkdir: File exists" and, on some paths, could reach
# "complete (ok)" having fetched nothing. Drive absence is an environment
# state, not a backup failure: SKIP without touching remote state.
if ! drive_guard_skip_if_absent "$SPOOL_DIR" "spool directory"; then
  log "=== vanadium-ci backup skipped (drive absent) ==="
  log "$DRIVE_GUARD_REASON"
  exit 0
fi
mkdir -p "$SPOOL_DIR"
log "=== vanadium-ci backup start (dry_run=$DRY_RUN) ==="

ARTS=()
mk() {  # mk <remote-cmd-producing-tar-or-dump-on-stdout> <artifact-name>
  local name="$2"
  if [ "$DRY_RUN" = 1 ]; then log "[dry] would fetch $name"; return 0; fi
  if ssh -o BatchMode=yes "$VD_HOST" "$1" > "$SPOOL_DIR/$name"; then
    ARTS+=("$name"); log "fetched $name ($(du -h "$SPOOL_DIR/$name" | cut -f1))"
  else
    log "FAIL fetching $name"; return 1
  fi
}

FAIL=0
# jenkins_home: exclude rebuildables (workspace/builds/caches per ruling V4,
# vd-ci-backup.sh) and tolerate files changing under a live build — tar exit 1
# on "file changed as we read it" was aborting the whole run (2026-09-10).
mk "docker exec $JENKINS_C tar czf - --warning=no-file-changed \
--exclude=workspace --exclude=builds --exclude=cache --exclude=caches \
--exclude=.m2 --exclude=war --exclude=copy_reference_file.log \
-C /var/jenkins_home ." \
   "jenkins_home__${TS}.tgz"                                   || FAIL=1
mk "docker exec $SONAR_C tar czf - -C /opt/sonarqube/data ." \
   "sonar-data__${TS}.tgz"                                     || FAIL=1
mk "docker exec $SONAR_C tar czf - -C /opt/sonarqube/extensions ." \
   "sonar-ext__${TS}.tgz"                                      || FAIL=1
mk "docker exec $SONAR_DB_C pg_dump -U $SONAR_DB_USER -Fc sonar" \
   "sonar-db__${TS}.dump"                                      || FAIL=1

if [ "$FAIL" = 1 ]; then log "ABORT: fetch failures present"; incident "vanadium-ci backup ABORT (fetch failures)" "One or more artifact fetches failed; nothing was shipped. See $LOG_FILE on titanium."; exit 1; fi
[ "$DRY_RUN" = 1 ] && { log "=== dry run complete ==="; exit 0; }

( cd "$SPOOL_DIR" && sha256sum *__"${TS}".* > "manifest__${TS}.txt" )

ssh -o BatchMode=yes "$BAR_HOST" "mkdir -p $REMOTE_DIR" || { log "FAIL mkdir remote"; incident; exit 1; }
rsync -a --partial "$SPOOL_DIR"/*__"${TS}".* "${BAR_HOST}:${REMOTE_DIR}/" || { log "FAIL rsync"; incident; exit 1; }

ssh -o BatchMode=yes "$BAR_HOST" "cd $REMOTE_DIR && sha256sum -c manifest__${TS}.txt --quiet" \
  || { log "FAIL remote verification"; incident; exit 1; }
log "remote checksum verification OK"

# GFS retention on vanadium (same policy as the PG tier)
# QUOTED heredoc: nothing expands locally; parameters travel as argv:
# $1 REMOTE_DIR, $2 RETAIN_DAILY, $3 RETAIN_WEEKLY, $4 RETAIN_MONTHLY,
# $5 MAX_AGE_DAYS, $6+ glob patterns. Date math decodes YYYYMMDD via
# PG-safe `date -d "<y>-01-01 +N months +M days"`; fail-closed on garbage.
# (The previous unquoted heredoc mangled quoting and its nested
# $(( $(date ...) )) greedy-closed, so remote pruning aborted on the
# first file every run — a silent no-op.)
ssh -o BatchMode=yes "$BAR_HOST" bash -s -- "$REMOTE_DIR" "$RETAIN_DAILY" \
  "$RETAIN_WEEKLY" "$RETAIN_MONTHLY" "$MAX_AGE_DAYS" \
  'jenkins_home__*.tgz' 'sonar-data__*.tgz' 'sonar-ext__*.tgz' 'sonar-db__*.dump' 'manifest__*.txt' <<'REMOTE_EOF'
set -u
REMOTE_DIR="$1"; RETAIN_DAILY="$2"; RETAIN_WEEKLY="$3"; RETAIN_MONTHLY="$4"; MAX_AGE_DAYS="$5"
shift 5
cd "$REMOTE_DIR" || { echo "retention: cannot cd $REMOTE_DIR" >&2; exit 3; }
deleted=0

pgsql2epoch() {  # YYYYMMDD -> UTC epoch seconds; nonzero on undecodable stamp
  case "$1" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) ;;
    *) return 1 ;;
  esac
  date -u -d "${1:0:4}-01-01 +$((10#${1:4:2}-1)) months +$((10#${1:6:2}-1)) days" +%s
}

prune() {
  local glob="$1"
  local daily="$RETAIN_DAILY" weeks_left="$RETAIN_WEEKLY" months_left="$RETAIN_MONTHLY" n=0 f stamp d age dow m
  local -A wk mo
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    stamp="${f#*__}"; stamp="${stamp%.*}"; d="${stamp:0:8}"
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
    dow=$(date -u -d "${d:0:4}-${d:4:2}-${d:6:2}" +%u); m="${d:0:6}"
    if [ "${d:6:2}" = "01" ]; then
      [ -z "${mo[$m]:-}" ] && [ "$RETAIN_MONTHLY" -gt 0 ] && { mo[$m]=1; months_left=$((months_left-1)); continue; }
    elif [ "$dow" = "7" ]; then
      [ -z "${wk[$m]:-}" ] && [ "$RETAIN_WEEKLY" -gt 0 ] && { wk[$m]=1; weeks_left=$((weeks_left-1)); continue; }
    fi
    rm -f -- "$f"; deleted=$((deleted+1))
  done < <(ls -1 $glob 2>/dev/null | sort -r)
}
for g in "$@"; do prune "$g"; done
echo "retention removed $deleted"
REMOTE_EOF

find "$SPOOL_DIR" -type f -mtime +"$LOCAL_KEEP_DAYS" -delete
log "=== vanadium-ci backup complete (ok) ==="
exit 0
