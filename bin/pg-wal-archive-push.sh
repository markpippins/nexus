#!/usr/bin/env bash
# pg-wal-archive-push.sh — push the local WAL archive spool to the off-machine
# target (vanadium) and prune both ends.
#
# DESIGN CONTEXT — read docs/runbook-pg-pitr.md before changing the defaults.
#
# `archive_command` writes into a LOCAL spool inside the database container
# (bind mount ./pg_archive → /var/lib/postgresql/data/pg_archive). That keeps
# the archive path free of network I/O, so a vanadium outage cannot pin WAL —
# the exact failure that produced incident e7a15424 (archive_mode='on' with a
# dead archive_command: 156 GB / 10,007 .ready segments, disk to 17 GB).
# This script is the second stage: copies finished segments off-machine.
#
# Invariants this script must never break:
#   1. Never delete a segment that the archiver has not consumed. Any *.ready
#      file in the spool means the archiver is behind or failing — pruning is
#      skipped entirely that run.
#   2. Never delete anything that is not a 24-hex-digit WAL segment name.
#      Timeline history (…​.history), backup labels (…​.backup), .partial and
#      .ready files are all preserved.
#   3. Archived segments are immutable. --ignore-existing means we never
#      re-transfer, never overwrite, and never "repair" a remote file.
#   4. Push before prune, in that order, every run.
#
# Schedule: user-level systemd timer (pg-wal-archive-push.timer), every 15 min.
#
# Usage:
#   pg-wal-archive-push.sh              # push + prune
#   pg-wal-archive-push.sh --dry-run    # show what would transfer/delete
#   pg-wal-archive-push.sh --help
#
# Env (all optional):
#   SPOOL_DIR          local archive spool      (default /home/codex/dev/pgsql/pg_archive)
#   REMOTE_HOST        ssh target              (default vanadium)
#   REMOTE_DIR         remote archive dir      (default /srv/pg_archive/nexus)
#   LOCAL_KEEP_HOURS   keep local segments     (default 72)
#   REMOTE_KEEP_DAYS   keep remote segments    (default 14)
#   MIN_LOCAL_SEGMENTS never prune below this  (default 64)
#   LOG_FILE           log destination         (default /home/codex/dev/pgsql/pg-wal-archive-push.log)
#
# Exit: 0 ok (or nothing to do), 1 transfer/verify failed, 2 spool missing,
#       3 remote unreachable (preflight), 4 remote dir creation failed,
#       75 lock held (another run in progress — not an error).

set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SPOOL_DIR="${SPOOL_DIR:-/home/codex/dev/pgsql/pg_archive}"
REMOTE_HOST="${REMOTE_HOST:-vanadium}"
REMOTE_DIR="${REMOTE_DIR:-/srv/pg_archive/nexus}"
LOCAL_KEEP_HOURS="${LOCAL_KEEP_HOURS:-72}"
REMOTE_KEEP_DAYS="${REMOTE_KEEP_DAYS:-14}"
MIN_LOCAL_SEGMENTS="${MIN_LOCAL_SEGMENTS:-64}"
LOG_FILE="${LOG_FILE:-/home/codex/dev/pgsql/pg-wal-archive-push.log}"
LOCK_FILE="${LOCK_FILE:-/tmp/pg-wal-archive-push.lock}"

DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --help|-h)
            sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) printf 'unknown argument: %s (try --help)\n' "$arg" >&2; exit 64 ;;
    esac
done

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

log() {
    local line
    line="[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"
    printf '%s\n' "$line"
    printf '%s\n' "$line" >>"$LOG_FILE" 2>/dev/null || true
}

die() {
    local code="$1"; shift
    log "FAIL: $*"
    exit "$code"
}

# --------------------------------------------------------------- locking ---
exec 9>"$LOCK_FILE" || die 1 "cannot open lock file $LOCK_FILE"
if ! flock -n 9; then
    log "another pg-wal-archive-push run holds $LOCK_FILE; skipping"
    exit 75
fi

if [ "${REMOTE_TARGET_IS_LOCAL:-0}" = "1" ]; then
    log "=== run start (dry_run=${DRY_RUN}) spool=${SPOOL_DIR} target=${REMOTE_DIR} (local) ==="
else
    log "=== run start (dry_run=${DRY_RUN}) spool=${SPOOL_DIR} target=${REMOTE_HOST}:${REMOTE_DIR} ==="
fi

[ -d "$SPOOL_DIR" ] || die 2 "spool directory $SPOOL_DIR does not exist (has archive_mode been enabled and the bind mount created?)"

# ------------------------------------------------------------- inventory ---
# Segment names are 24 hex digits. Everything else in the spool is metadata
# that must be kept regardless of age.
segment_count=$(find "$SPOOL_DIR" -maxdepth 1 -type f -regextype posix-extended \
    -regex '.*/[0-9A-F]{24}' 2>/dev/null | wc -l)
ready_count=$(find "$SPOOL_DIR" -maxdepth 1 -type f -name '*.ready' 2>/dev/null | wc -l)
partial_count=$(find "$SPOOL_DIR" -maxdepth 1 -type f -name '*.partial' 2>/dev/null | wc -l)
spool_bytes=$(du -sb "$SPOOL_DIR" 2>/dev/null | awk '{print $1}')
log "spool: ${segment_count} segments, ${ready_count} .ready, ${partial_count} .partial, ${spool_bytes:-0} bytes"

if [ "$segment_count" -eq 0 ]; then
    log "nothing archived yet; nothing to push"
    log "=== run end (ok, empty spool) ==="
    exit 0
fi

# ------------------------------------------------------------- preflight ---
# REMOTE_TARGET_IS_LOCAL=1 targets a plain directory (tests, drills) and skips
# the ssh probe and ssh-based operations entirely.
TARGET_IS_LOCAL="${REMOTE_TARGET_IS_LOCAL:-0}"
if [ "$TARGET_IS_LOCAL" != "1" ]; then
    if [ -f "$SCRIPT_DIR/lib/pg-target-preflight.sh" ]; then
        # shellcheck source=/dev/null
        . "$SCRIPT_DIR/lib/pg-target-preflight.sh"
        if ! detail="$(pg_target_preflight)"; then
            die 3 "target unreachable: $(printf '%s' "$detail" | tr '\n' ' ')"
        fi
    else
        log "WARN: preflight helper missing; falling back to a plain ssh probe"
        ssh -o BatchMode=yes -o ConnectTimeout=5 "$REMOTE_HOST" true 2>/dev/null \
            || die 3 "target $REMOTE_HOST unreachable (ssh failed)"
    fi
fi

# --------------------------------------------------------- remote directory ---
if [ "$TARGET_IS_LOCAL" = "1" ]; then
    if [ "$DRY_RUN" -eq 0 ]; then
        mkdir -p "$REMOTE_DIR" || die 4 "cannot create local target dir $REMOTE_DIR"
    fi
else
    if [ "$DRY_RUN" -eq 1 ]; then
        # rsync -n still needs a writable destination path; if the archive dir
        # does not exist yet there is nothing to preview against, so say that
        # plainly instead of reporting a spurious transfer failure.
        if ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" "test -d '$REMOTE_DIR'" 2>/dev/null; then
            log "dry-run: target ${REMOTE_HOST}:${REMOTE_DIR} exists; previewing the transfer"
        else
            log "dry-run: ${REMOTE_HOST}:${REMOTE_DIR} does not exist yet — a real run creates it, then transfers the spool"
            PUSH_PREVIEW_SKIPPED=1
        fi
    else
        ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" \
            "mkdir -p '$REMOTE_DIR' && chmod 750 '$REMOTE_DIR'" 2>/dev/null \
            || die 4 "cannot create ${REMOTE_HOST}:${REMOTE_DIR}"
    fi
fi

# ------------------------------------------------------------------ push ---
# .ready / .partial files are archiver bookkeeping, not archive content: a
# partial is by definition incomplete, and copying either to the DR target
# invites a restore_command to read something it should never see.
RSYNC_FLAGS=(-a --ignore-existing --exclude='*.ready' --exclude='*.partial*' --stats)
if [ "$TARGET_IS_LOCAL" = "1" ]; then DEST="$REMOTE_DIR"; else DEST="${REMOTE_HOST}:${REMOTE_DIR}"; fi

if [ "$DRY_RUN" -eq 1 ] && [ "${PUSH_PREVIEW_SKIPPED:-0}" = "1" ]; then
    log "dry-run: push preview skipped (destination not created yet); ${segment_count} segment(s) would transfer"
    log "dry-run: push complete (nothing transferred)"
elif [ "$DRY_RUN" -eq 1 ]; then
    if ! rsync "${RSYNC_FLAGS[@]}" -n "$SPOOL_DIR"/ "$DEST"/ 2>&1 | tee -a "$LOG_FILE"; then
        die 1 "rsync dry-run failed for $DEST"
    fi
    log "dry-run: push complete (nothing transferred)"
else
    if ! rsync "${RSYNC_FLAGS[@]}" "$SPOOL_DIR"/ "$DEST"/ 2>&1 | tee -a "$LOG_FILE"; then
        die 1 "rsync push failed for $DEST"
    fi
fi

# --------------------------------------------------------------- verify ----
# Cheap, decisive check: the newest local segment must exist remotely with the
# same size. Catches truncation and a silently wrong destination.
newest_segment=$(find "$SPOOL_DIR" -maxdepth 1 -type f -regextype posix-extended \
    -regex '.*/[0-9A-F]{24}' -printf '%T@ %f\n' 2>/dev/null | sort -n | tail -1 | awk '{print $2}')

if [ -n "${newest_segment:-}" ] && [ "$DRY_RUN" -eq 0 ]; then
    local_size=$(stat -c %s "$SPOOL_DIR/$newest_segment" 2>/dev/null || echo 0)
    if [ "$TARGET_IS_LOCAL" = "1" ]; then
        remote_size=$(stat -c %s "$REMOTE_DIR/$newest_segment" 2>/dev/null || echo -1)
    else
        remote_size=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" \
            "stat -c %s '$REMOTE_DIR/$newest_segment'" 2>/dev/null || echo -1)
    fi
    if [ "$remote_size" != "$local_size" ]; then
        die 1 "verify failed: $newest_segment local=${local_size}B remote=${remote_size}B"
    fi
    log "verify ok: $newest_segment (${local_size}B) present on target"
fi

# ---------------------------------------------------------- prune: local ---
# Skip pruning entirely if the archiver has not consumed everything: a .ready
# file means a segment is still owed to the archive, and deleting around it is
# how a spool turns into a data-loss story.
if [ "$ready_count" -gt 0 ]; then
    log "WARN: ${ready_count} .ready file(s) in spool — archiver is behind or failing; skipping local prune"
else
    keep_after=$((segment_count - MIN_LOCAL_SEGMENTS))
    if [ "$keep_after" -le 0 ]; then
        log "prune: only ${segment_count} local segments (floor ${MIN_LOCAL_SEGMENTS}); nothing to prune"
    else
        pruned=0
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            if [ "$DRY_RUN" -eq 1 ]; then
                log "dry-run: would prune local $(basename "$f")"
            else
                rm -f -- "$f" && pruned=$((pruned + 1))
            fi
        done < <(find "$SPOOL_DIR" -maxdepth 1 -type f -regextype posix-extended \
                    -regex '.*/[0-9A-F]{24}' -mmin +$((LOCAL_KEEP_HOURS * 60)) 2>/dev/null \
                 | sort | head -n "$keep_after")
        log "prune: local removed=${pruned} (older than ${LOCAL_KEEP_HOURS}h, floor ${MIN_LOCAL_SEGMENTS})"
    fi
fi

# --------------------------------------------------------- prune: remote ---
# Same 24-hex filter, same reason. Age is measured from mtime, which rsync -a
# preserves, so the effective age is the segment's own age, not transfer time.
remote_find="find '$REMOTE_DIR' -maxdepth 1 -type f -regextype posix-extended -regex '.*/[0-9A-F]{24}' -mtime +${REMOTE_KEEP_DAYS}"
if [ "$TARGET_IS_LOCAL" = "1" ]; then
    remote_pruned=$(find "$REMOTE_DIR" -maxdepth 1 -type f -regextype posix-extended \
        -regex '.*/[0-9A-F]{24}' -mtime +"$REMOTE_KEEP_DAYS" 2>/dev/null | wc -l)
    if [ "$DRY_RUN" -eq 0 ]; then
        find "$REMOTE_DIR" -maxdepth 1 -type f -regextype posix-extended \
            -regex '.*/[0-9A-F]{24}' -mtime +"$REMOTE_KEEP_DAYS" -delete 2>/dev/null || true
        log "prune: remote removed=${remote_pruned} (older than ${REMOTE_KEEP_DAYS}d)"
    else
        log "dry-run: would prune ${remote_pruned} remote segment(s) older than ${REMOTE_KEEP_DAYS}d"
    fi
else
    if [ "$DRY_RUN" -eq 1 ]; then
        remote_pruned=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" \
            "$remote_find -print | wc -l" 2>/dev/null || echo unknown)
        log "dry-run: would prune ${remote_pruned} remote segment(s) older than ${REMOTE_KEEP_DAYS}d"
    else
        remote_pruned=$(ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" \
            "n=\$($remote_find -print | wc -l); $remote_find -delete; echo \$n" 2>/dev/null || echo unknown)
        log "prune: remote removed=${remote_pruned} (older than ${REMOTE_KEEP_DAYS}d)"
    fi
fi

log "=== run end (ok) ==="
exit 0
