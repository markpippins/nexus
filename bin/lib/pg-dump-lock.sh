# shellcheck shell=bash
# bin/lib/pg-dump-lock.sh — cluster-wide serialization for the nightly PG
# dump family. Sourced, not executed.
# =============================================================================
# Incident (2026-10-02): the 03:16 `backup-pg-to-vanadium` run died mid-dump
# when the host ran out of memory (1.9 GiB free, 29 GiB swap used, 3.5 GB DB,
# two concurrent dumps). The nightly PG tiers have OVERLAPPING RandomizedDelay-
# Sec windows and each script held only its OWN lock file, so different tiers
# never excluded one another:
#
#   backup-nexus.timer          02:00 ±5min   pgsql/backup-nexus.sh   pg_dump
#   sync-pg-to-vanadium.timer   02:45 ±15min  pgsql/sync-to-vanadium.sh  pg_dump
#   pg-escape-hatch.timer       03:10 ±15min  bin/pg-escape-hatch.sh  pg_dump
#   backup-pg-to-vanadium.timer 03:30 ±15min  bin/pg-backup-to-vanadium.sh  pg_dump
#
# All four dump the SAME host/container. Two overlapping runs tipped the host
# over and the OOM killer reaped the dump clients.
#
# This library gives the family ONE shared lock so overlapping tiers QUEUE
# (serialize) instead of stampeding. The caller sources this file and calls:
#
#   pg_dump_lock_acquire [timeout_s]   # 0 = held, 1 = timed out
#       Blocks until the cluster lock is free, up to `timeout_s` (default
#       PG_DUMP_LOCK_WAIT, 3600s). On success the lock is held on a dedicated
#       fd for the life of the process. On timeout the caller MUST NOT dump —
#       log an honest SKIP and exit 0 (a missed night beats a stampede).
#
#   pg_dump_lock_release               # explicit early release (optional;
#                                      # process exit releases it anyway)
#
# Environment (all optional):
#   PG_DUMP_CLUSTER_LOCK   lock file path      (default /tmp/pg-dump-cluster.lock)
#   PG_DUMP_LOCK_WAIT      default wait, secs  (default 3600)
#   PG_DUMP_LOCK_LIB       path, for the pgsql/ scripts outside the repo
#
# Test seams: point PG_DUMP_CLUSTER_LOCK at a scratch path and set
# PG_DUMP_LOCK_WAIT to pin behavior; nothing else here touches the system.

# fd 201 — distinct from the per-script fd 200 that several callers already
# hold, so the cluster lock and the per-script lock never alias.
PG_DUMP_LOCK_FD=201

pg_dump_lock_path() {
  printf '%s' "${PG_DUMP_CLUSTER_LOCK:-/tmp/pg-dump-cluster.lock}"
}

pg_dump_lock_acquire() {
  local wait="${1:-${PG_DUMP_LOCK_WAIT:-3600}}"
  local path
  path="$(pg_dump_lock_path)"

  # Open the lock fd for the life of the process (never truncate the file).
  eval "exec ${PG_DUMP_LOCK_FD}>>\"\$path\""

  # Fast path: uncontended.
  if flock -n "$PG_DUMP_LOCK_FD" 2>/dev/null; then
    return 0
  fi

  # Contended: serialize by waiting, do not stampede.
  printf '%s pg-dump-lock: another PG backup tier holds %s — waiting up to %ss\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" "$path" "$wait" >&2
  flock -w "$wait" "$PG_DUMP_LOCK_FD" 2>/dev/null
}

pg_dump_lock_release() {
  flock -u "$PG_DUMP_LOCK_FD" 2>/dev/null || true
}
