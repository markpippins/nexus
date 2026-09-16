# shellcheck shell=bash
# bin/lib/drive-guard.sh — absent-drive path guards for the nexus backup
# script family. Sourced, not executed.
# =============================================================================
# Background (DBA audit f74eb976, 2026-09-15): backup scripts depended on
# destinations behind /mnt removable drives — directly (rsync to /mnt/WD14A)
# or indirectly (backup dirs and spools as symlinks into /mnt/SiP1TB). When a
# drive is absent, `mkdir -p` on a symlink-to-absent-mount fails with the
# misleading "File exists", and every redirect fails with raw ENOENT — root
# cause invisible (the mysql-backup incident, records 0abb6df5 -> cd776865).
# Worse, one script (vanadium-ci-backup) could reach a 0-exit "complete (ok)"
# having fetched nothing — a silent no-op marked verified.
#
# This library gives the family two honest semantics:
#
#   drive_guard_require_dir <dir> <label>   FAIL-FAST (rc 90/91/92)
#       For PRIMARY backup destinations. A missing destination is a real
#       failure: silently skipping a primary backup hides coverage gaps.
#       The caller logs DRIVE_GUARD_REASON, raises its incident, exits 1.
#
#   drive_guard_skip_if_absent <dir> <label>  SKIP (rc 99) / proceed (rc 0)
#       For scratch/spool paths on removable drives. Drive absence is an
#       expected environment state, not a failure: caller logs
#       DRIVE_GUARD_REASON and exits 0 WITHOUT claiming a backup happened.
#
#   drive_guard_describe <dir>              one-line state (for watchers)
#       "local" | "ok" | "dangling <target>" | "unmounted <mountroot>"
#
# On failure/skip the functions set DRIVE_GUARD_REASON (single line, honest,
# names the drive and the repair) and return nonzero. Diagnostics also go to
# stderr so systemd journal capture never depends on a writable log dir.
#
# Test seams (default behavior is production-real):
#   DRIVE_GUARD_REMOVABLE_BASES  — bases treated as removable-drive roots
#                                  (default "/mnt /media")
#   DRIVE_GUARD_FAKE_MOUNTS      — space-separated paths treated as
#                                  mounted roots (default empty -> the real
#                                  `mountpoint` command decides)
#
# Verdict vocabulary (drive_guard_describe):
#   dangling <target>        symlink to a path that does not exist
#   unmounted <drive-root>   path under a removable base whose drive root is
#                            not a mountpoint (symlink OR plain path)
#   ok                       under a mounted removable drive
#   local                    ordinary local-filesystem path
# =============================================================================

if [ -n "${_NEXUS_DRIVE_GUARD_LOADED:-}" ]; then
  return 0 2>/dev/null || exit 0
fi
_NEXUS_DRIVE_GUARD_LOADED=1

_drive_guard_diag() {
  printf '%s\n' "drive-guard: $*" >&2
}

# Internal: is <path> a mounted filesystem root? (test-seam aware)
_drive_guard_is_mount() {
  if [ -n "${DRIVE_GUARD_FAKE_MOUNTS:-}" ]; then
    case " ${DRIVE_GUARD_FAKE_MOUNTS} " in
      *" $1 "*) return 0 ;;
    esac
    return 1
  fi
  mountpoint -q "$1" 2>/dev/null
}

# Internal: if <path> points into a removable-drive base, echo the drive root
# (e.g. /mnt/SiP1TB for /mnt/SiP1TB/home/backups); else return 1.
_drive_guard_expected_mount() {
  local p="$1" base rest first
  local bases="${DRIVE_GUARD_REMOVABLE_BASES:-/mnt /media}"
  for base in $bases; do
    case "$p" in
      "$base"/*)
        rest="${p#"$base"/}"
        first="${rest%%/*}"
        [ -n "$first" ] || continue
        printf '%s/%s\n' "$base" "$first"
        return 0
        ;;
    esac
  done
  return 1
}

# drive_guard_describe <dir> -> one-line state on stdout
# Verdicts: dangling | unmounted | ok | local. The removable-base check
# applies to PLAIN paths too (e.g. /mnt/WD14A/bak/pgdata): a plain dir under
# an absent drive root would otherwise let `mkdir -p` quietly write the
# backup onto the root filesystem — a silent mis-destination.
drive_guard_describe() {
  local dir="$1"

  # 1. Dangling symlink — highest priority (the mysql-backup pattern).
  if [ -L "$dir" ] && [ ! -e "$dir" ]; then
    printf 'dangling %s\n' "$(readlink "$dir")"
    return 0
  fi

  # 2. Resolve to a canonical target (symlinks chased, '..' normalized).
  local tgt mp
  if [ -L "$dir" ]; then
    tgt="$(readlink -f "$dir" 2>/dev/null || readlink "$dir")"
  else
    tgt="$(cd "$(dirname "$dir")" 2>/dev/null && printf '%s/%s' "$(pwd -P)" "$(basename "$dir")")"
  fi
  [ -n "$tgt" ] || tgt="$dir"

  # 3. Under a removable-drive base? Then the drive root must be mounted.
  if mp="$(_drive_guard_expected_mount "$tgt")"; then
    if _drive_guard_is_mount "$mp"; then
      printf 'ok\n'
    else
      printf 'unmounted %s\n' "$mp"
    fi
    return 0
  fi

  # 4. Ordinary local path.
  printf 'local\n'
}

# drive_guard_require_dir <dir> <label> -> 0 usable | 90/91/92 + REASON
drive_guard_require_dir() {
  local dir="$1" label="${2:-backup directory}" desc
  desc="$(drive_guard_describe "$dir")"
  case "$desc" in
    dangling*)
      DRIVE_GUARD_REASON="FAIL: $label '$dir' is a dangling symlink (-> ${desc#dangling }) — backup destination drive is absent. Remount the drive, or repoint the destination at a local path. Nothing was attempted."
      _drive_guard_diag "$DRIVE_GUARD_REASON"
      return 90
      ;;
    unmounted*)
      DRIVE_GUARD_REASON="FAIL: $label '$dir' resolves into ${desc#unmounted } which is NOT a mounted drive — destination unavailable. Remount the drive, or repoint the destination at a local path. Nothing was attempted."
      _drive_guard_diag "$DRIVE_GUARD_REASON"
      return 90
      ;;
  esac
  if ! mkdir -p "$dir" 2>/dev/null; then
    DRIVE_GUARD_REASON="FAIL: $label '$dir' cannot be created (mkdir -p failed) — check permissions and parent path."
    _drive_guard_diag "$DRIVE_GUARD_REASON"
    return 91
  fi
  if [ ! -w "$dir" ]; then
    DRIVE_GUARD_REASON="FAIL: $label '$dir' is not writable by this user — check ownership/permissions."
    _drive_guard_diag "$DRIVE_GUARD_REASON"
    return 92
  fi
  return 0
}

# drive_guard_skip_if_absent <dir> <label> -> 0 proceed | 99 + REASON (skip)
drive_guard_skip_if_absent() {
  local dir="$1" label="${2:-spool directory}" desc
  desc="$(drive_guard_describe "$dir")"
  case "$desc" in
    dangling*)
      DRIVE_GUARD_REASON="SKIP: $label '$dir' is a dangling symlink (-> ${desc#dangling }) — removable drive absent. Nothing fetched; remote state untouched. This is an environment condition, not a backup failure."
      _drive_guard_diag "$DRIVE_GUARD_REASON"
      return 99
      ;;
    unmounted*)
      DRIVE_GUARD_REASON="SKIP: $label '$dir' resolves into ${desc#unmounted } which is NOT a mounted drive — removable drive absent. Nothing fetched; remote state untouched. This is an environment condition, not a backup failure."
      _drive_guard_diag "$DRIVE_GUARD_REASON"
      return 99
      ;;
  esac
  return 0
}
