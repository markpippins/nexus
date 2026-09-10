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

TS="$(date +%Y%m%d_%H%M%S)"
DRY_RUN=0; [ "${1:-}" = "--dry-run" ] && DRY_RUN=1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }
incident() {
  curl -s --max-time 5 -X POST "$NEBULA_URL" -H 'Content-Type: application/json' \
    -d "{\"recordType\":\"report\",\"role\":\"devops\",\"title\":\"vanadium-ci backup FAILED ($TS)\",\"content\":\"Vanadium CI backup to vanadium failed. See $LOG_FILE on titanium.\",\"tags\":[\"to:sysadmin\",\"type:incident\",\"status:open\",\"source:vdci-backup\"]}" \
    >/dev/null 2>&1 || true
}

exec 200>"$LOCK_FILE"
flock -n 200 || { log "SKIP: lock held"; exit 0; }

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

if [ "$FAIL" = 1 ]; then log "ABORT: fetch failures present"; incident; exit 1; fi
[ "$DRY_RUN" = 1 ] && { log "=== dry run complete ==="; exit 0; }

( cd "$SPOOL_DIR" && sha256sum *__"${TS}".* > "manifest__${TS}.txt" )

ssh -o BatchMode=yes "$BAR_HOST" "mkdir -p $REMOTE_DIR" || { log "FAIL mkdir remote"; incident; exit 1; }
rsync -a --partial "$SPOOL_DIR"/*__"${TS}".* "${BAR_HOST}:${REMOTE_DIR}/" || { log "FAIL rsync"; incident; exit 1; }

ssh -o BatchMode=yes "$BAR_HOST" "cd $REMOTE_DIR && sha256sum -c manifest__${TS}.txt --quiet" \
  || { log "FAIL remote verification"; incident; exit 1; }
log "remote checksum verification OK"

# GFS retention on vanadium (same policy as the PG tier)
ssh -o BatchMode=yes "$BAR_HOST" bash -s <<REMOTE_EOF
set -u
cd "$REMOTE_DIR" || exit 0
deleted=0
prune() {
  local n=0 weeks_left=$RETAIN_WEEKLY months_left=$RETAIN_MONTHLY
  local -A wk mo
  for f in \$(ls -1 \$1 2>/dev/null | sort -r); do
    n=\$((n+1))
    local stamp="\${f#*__}"; stamp="\${stamp%.*}"; local d="\${stamp:0:8}"
    local age=$(( (\$(date +%s) - \$(date -d "\${d:0:4}-\${d:4:2}-\${d:6:2}" +%s)) / 86400 ))
    [ "\$age" -gt $MAX_AGE_DAYS ] && { rm -f "\$f"; deleted=\$((deleted+1)); continue; }
    [ "\$n" -le $RETAIN_DAILY ] && continue
    local dow=\$(date -d "\${d:0:4}-\${d:4:2}-\${d:6:2}" +%u); local m=\${d:0:6}
    if [ "\${d:6:2}" = "01" ]; then
      [ -z "\${mo[\$m]:-}" ] && [ $RETAIN_MONTHLY -gt 0 ] && { mo[\$m]=1; months_left=\$((months_left-1)); continue; }
    elif [ "\$dow" = "7" ]; then
      [ -z "\${wk[\$m]:-}" ] && [ $RETAIN_WEEKLY -gt 0 ] && { wk[\$m]=1; weeks_left=\$((weeks_left-1)); continue; }
    fi
    rm -f "\$f"; deleted=\$((deleted+1))
  done
}
for g in 'jenkins_home__*.tgz' 'sonar-data__*.tgz' 'sonar-ext__*.tgz' 'sonar-db__*.dump' 'manifest__*.txt'; do prune "\$g"; done
echo "retention removed \$deleted"
REMOTE_EOF

find "$SPOOL_DIR" -type f -mtime +"$LOCAL_KEEP_DAYS" -delete
log "=== vanadium-ci backup complete (ok) ==="
exit 0
