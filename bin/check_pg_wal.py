#!/usr/bin/env python3
"""pg_wal / archiver guard for the nexus PostgreSQL cluster.

Why this exists — incident `e7a15424` (2026-10-16). `archive_mode='on'` with
an empty `archive_command` meant the archiver had nothing to run, so every
WAL segment stayed pinned in `pg_wal` forever. It grew to 156 GB / 10,007
`.ready` segments over 30 days and took the filesystem to 17 GB free. At 100%
PostgreSQL PANICs. Two things made it invisible, and both are what this
guard closes (architect ruling `3056d149`):

  1. Nothing watched `pg_wal` size or the `.ready` backlog.
  2. Nothing watched archiver *progress*: `archived_count=0` for 30 days
     with `failed_count=0`, i.e. a durability feature that was switched on
     and silently did nothing. A counter nobody reads is not a guard.

It also classifies the root cause directly rather than waiting for the
symptom: archiving enabled with no `archive_command` AND no
`archive_library` pins WAL **by construction**. That rule fires on the first
run of a misconfigured cluster, not on day 30.

Measurement is done entirely through SQL (`pg_ls_waldir()`,
`pg_ls_archive_statusdir()`, `pg_stat_archiver`, `current_setting()`), never
through the filesystem. `pgdata` is mode 700 owned by the postgres uid, so a
host-side `du` cannot see `pg_wal` at all — the blind spot recorded in
`3056d149`. The one thing SQL cannot report is disk headroom, which is
measured with `statvfs` on the filesystem that actually holds pgdata
(resolved through /proc/self/mounts, not by summing directories).

Exit codes: 0 = healthy, 1 = findings (the wrapper files a record),
2 = tool/environment error (nothing filed; journal-visible).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

DEFAULT_DSN = "postgresql://pguser:pgpass@localhost:5432/nexus"
DSN = os.environ.get("PG_WAL_DSN",
                     os.environ.get("CONDUIT_PG_DSN", DEFAULT_DSN))
PSQL = os.environ.get("PG_WAL_PSQL_BIN", "psql")

# Host path of the cluster's data directory (bind-mount source of the
# container's data_directory). Used ONLY to resolve which filesystem to
# statvfs — never for directory sizing, which cannot be done from the host.
PGDATA_HOST = os.environ.get("PG_WAL_PGDATA_HOST", "/home/codex/dev/pgsql/pgdata")
MOUNTS = os.environ.get("PG_WAL_MOUNTS", "/proc/self/mounts")

GIB = 1024 ** 3


def _env_gib(name: str, default: float) -> int:
    return int(float(os.environ.get(name, default)) * GIB)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# Thresholds. Calibrated against the incident and against steady state:
# max_wal_size is 1024 (16 GB) and a healthy cluster sits near min_wal_size,
# so a settled 80 MB is normal and 20 GB is already two orders off normal.
# The incident's 156 GB / 10,007 .ready / 17 GB free is critical on all
# three dimensions, so this guard would have fired the day the config landed
# (via the construction rule) and again on every growth milestone after.
def thresholds() -> dict:
    return {
        "wal_warn_bytes": _env_gib("PG_WAL_SIZE_WARN_GIB", 20),
        "wal_crit_bytes": _env_gib("PG_WAL_SIZE_CRIT_GIB", 60),
        "ready_warn": _env_int("PG_WAL_READY_WARN", 500),
        "ready_crit": _env_int("PG_WAL_READY_CRIT", 2000),
        "disk_warn_free_bytes": _env_gib("PG_WAL_DISK_FREE_WARN_GIB", 50),
        "disk_crit_free_bytes": _env_gib("PG_WAL_DISK_FREE_CRIT_GIB", 20),
    }


# One round-trip, one row, pipe-separated. Column order is the contract with
# _parse_row(). Every value is coerced so a NULL never shifts the parse.
MEASURE_SQL = """
SELECT
  coalesce((SELECT sum(size) FROM pg_ls_waldir()), 0)::bigint,
  (SELECT count(*) FROM pg_ls_waldir())::bigint,
  (SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready')::bigint,
  (SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.done')::bigint,
  current_setting('archive_mode'),
  current_setting('archive_command'),
  current_setting('archive_library'),
  coalesce((SELECT archived_count FROM pg_stat_archiver), 0)::bigint,
  coalesce((SELECT failed_count FROM pg_stat_archiver), 0)::bigint,
  coalesce((SELECT last_archived_time FROM pg_stat_archiver)::text, ''),
  coalesce((SELECT last_failed_time FROM pg_stat_archiver)::text, ''),
  current_setting('data_directory'),
  current_setting('max_wal_size'),
  coalesce((SELECT setting FROM pg_settings WHERE name = 'server_version'), '')
"""


def parse_row(row: str) -> dict:
    """Pure: pipe-separated psql row -> measurement fields."""
    parts = row.rstrip("\n").split("|")
    if len(parts) != 14:
        raise ValueError(f"expected 14 columns, got {len(parts)}: {row!r}")
    (wal_bytes, seg_count, ready, done, archive_mode, archive_command,
     archive_library, archived_count, failed_count, last_archived_time,
     last_failed_time, data_directory, max_wal_size, server_version) = parts
    return {
        "wal_bytes": int(wal_bytes),
        "seg_count": int(seg_count),
        "ready": int(ready),
        "done": int(done),
        "archive_mode": archive_mode.strip(),
        "archive_command": archive_command,
        "archive_library": archive_library,
        "archived_count": int(archived_count),
        "failed_count": int(failed_count),
        "last_archived_time": last_archived_time or None,
        "last_failed_time": last_failed_time or None,
        "data_directory": data_directory,
        "max_wal_size": max_wal_size,
        "server_version": server_version,
    }


def measure(dsn: str = DSN, psql: str = PSQL) -> dict:
    """Run the measurement query. Raises RuntimeError on tool failure."""
    cmd = [psql, dsn, "-At", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c",
           MEASURE_SQL]
    try:
        ran = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"psql invocation failed: {exc}") from exc
    if ran.returncode != 0:
        raise RuntimeError(
            f"psql exited {ran.returncode}: {(ran.stderr or '').strip()[-300:]}")
    row = (ran.stdout or "").strip().splitlines()
    if not row:
        raise RuntimeError("psql returned no rows")
    try:
        return parse_row(row[0])
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def resolve_mount(path: str, mounts_text: str) -> str | None:
    """Pure: the longest mount point that contains `path`, or None.

    Longest-prefix wins (a bind mount nested under / must beat /). Paths are
    realpath'd on both sides so a symlinked mount target still matches.
    """
    real = os.path.realpath(path)
    best = None
    for line in mounts_text.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        point = fields[1]
        real_point = os.path.realpath(point)
        if real == real_point or real.startswith(real_point.rstrip("/") + "/"):
            if best is None or len(real_point) > len(best):
                best = real_point
    return best


def measure_disk(pgdata_host: str = PGDATA_HOST,
                 mounts_path: str = MOUNTS) -> dict:
    """Filesystem headroom for the volume that holds pgdata.

    Never sums directories: pgdata is unreadable to this process by design.
    Returns measured=False with a reason rather than raising, so a broken
    disk probe is reported as a finding instead of killing the run.
    """
    # Existence first, deliberately. `resolve_mount` matches on path
    # *prefixes*, so a stale or wrong PGDATA_HOST still resolves to '/' and
    # the guard would quietly measure the wrong filesystem and report a
    # healthy number — a guard that cannot see its subject while looking
    # green. stat() succeeds on the directory itself (mode 700 blocks reads
    # *inside* it, not stat), so this is a real check, not a proxy.
    if not os.path.exists(pgdata_host):
        return {"measured": False,
                "reason": f"{pgdata_host} does not exist on this host "
                          "(stale PG_WAL_PGDATA_HOST? then the disk "
                          "dimension is measuring nothing)"}
    try:
        with open(mounts_path, encoding="utf-8", errors="replace") as fh:
            mounts_text = fh.read()
    except OSError as exc:
        return {"measured": False, "reason": f"mounts unreadable: {exc}"}
    mount = resolve_mount(pgdata_host, mounts_text)
    if mount is None:
        return {"measured": False,
                "reason": f"no mount point found containing {pgdata_host}"}
    try:
        st = os.statvfs(mount)
    except OSError as exc:
        return {"measured": False,
                "reason": f"statvfs({mount}) failed: {exc}"}
    return {
        "measured": True,
        "mount": mount,
        "pgdata_host": pgdata_host,
        "free_bytes": st.f_bavail * st.f_frsize,
        "total_bytes": st.f_blocks * st.f_frsize,
    }


def human(n) -> str:
    """Bytes -> GiB/MiB, for record bodies a human reads under pressure."""
    if n is None:
        return "unknown"
    for unit, div in (("GB", GIB), ("MB", 1024 ** 2), ("KB", 1024)):
        if abs(n) >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} B"


def archiving_enabled(m: dict) -> bool:
    return m.get("archive_mode", "").strip().lower() in ("on", "always")


# PostgreSQL renders archive_command as the literal '(disabled)' when
# archive_mode is off (a GUC show-hook), so that sentinel must never be read
# as a real destination. Verified live: the cluster this guard was written
# against reports archive_command='(disabled)' with archive_mode=off.
_NO_TARGET_SENTINELS = ("", "(disabled)")


def has_archive_target(m: dict) -> bool:
    """True when the archiver has something to run at all.

    Empty `archive_command` AND empty `archive_library` is the incident's
    exact configuration: a durability feature switched on with no
    implementation behind it. '(disabled)' is a display sentinel, not a
    command, and counts as no target.
    """
    for field in ("archive_command", "archive_library"):
        value = (m.get(field) or "").strip()
        if value and value.lower() not in _NO_TARGET_SENTINELS:
            return True
    return False


def classify(m: dict, disk: dict, th: dict) -> list[dict]:
    """Pure: measurement -> findings. Ordered most-structural first.

    Each finding is {dimension, tier, message}; tier is "warn" or
    "critical". An empty list means the posture held.
    """
    findings: list[dict] = []

    # 1. Construction rule: enabled with nothing behind it. This is the
    #    root cause of e7a15424 and it fires before any growth.
    if archiving_enabled(m) and not has_archive_target(m):
        findings.append({
            "dimension": "archiving_unimplemented",
            "tier": "critical",
            "message": (
                "archive_mode is '%s' but both archive_command and "
                "archive_library are empty — the archiver has nothing to "
                "run, so every WAL segment is pinned forever by "
                "construction (this is exactly incident e7a15424). Either "
                "give it a real destination or set archive_mode=off."
                % m.get("archive_mode")),
        })

    # 2. Archiver failing loudly (segments not moving despite a target).
    if archiving_enabled(m) and m.get("failed_count", 0) > 0:
        findings.append({
            "dimension": "archiver_failures",
            "tier": "warn",
            "message": (
                "archiver has %d failed attempt(s) (last %s) — WAL is being "
                "retained while archiving fails"
                % (m["failed_count"], m.get("last_failed_time") or "unknown")),
        })

    # 3. Size of pg_wal itself.
    wal = m.get("wal_bytes") or 0
    if wal >= th["wal_crit_bytes"]:
        findings.append({
            "dimension": "wal_size",
            "tier": "critical",
            "message": "pg_wal is %s (critical threshold %s)"
                       % (human(wal), human(th["wal_crit_bytes"])),
        })
    elif wal >= th["wal_warn_bytes"]:
        findings.append({
            "dimension": "wal_size",
            "tier": "warn",
            "message": "pg_wal is %s (warn threshold %s)"
                       % (human(wal), human(th["wal_warn_bytes"])),
        })

    # 4. .ready backlog: segments waiting to be archived. Meaningful while
    #    archiving is on; still reported when off, with the mode named, so
    #    an unexpected backlog is never silently ignored.
    ready = m.get("ready") or 0
    if ready >= th["ready_crit"]:
        findings.append({
            "dimension": "ready_backlog",
            "tier": "critical",
            "message": "%d WAL segments marked .ready (critical threshold %d; "
                       "archive_mode=%s)"
                       % (ready, th["ready_crit"], m.get("archive_mode")),
        })
    elif ready >= th["ready_warn"]:
        findings.append({
            "dimension": "ready_backlog",
            "tier": "warn",
            "message": "%d WAL segments marked .ready (warn threshold %d; "
                       "archive_mode=%s)"
                       % (ready, th["ready_warn"], m.get("archive_mode")),
        })

    # 5. Disk headroom on the filesystem that holds pgdata. 17 GB free is
    #    where the incident stopped being theoretical (96% full).
    if disk.get("measured"):
        free = disk["free_bytes"]
        if free < th["disk_crit_free_bytes"]:
            findings.append({
                "dimension": "disk_headroom",
                "tier": "critical",
                "message": "%s free on %s (critical threshold %s)"
                           % (human(free), disk["mount"],
                              human(th["disk_crit_free_bytes"])),
            })
        elif free < th["disk_warn_free_bytes"]:
            findings.append({
                "dimension": "disk_headroom",
                "tier": "warn",
                "message": "%s free on %s (warn threshold %s)"
                           % (human(free), disk["mount"],
                              human(th["disk_warn_free_bytes"])),
            })
    else:
        # A guard whose disk dimension silently disappeared is the failure
        # mode this whole file exists to prevent — say so out loud.
        findings.append({
            "dimension": "disk_headroom",
            "tier": "warn",
            "message": "disk headroom UNMEASURED — %s" % disk.get(
                "reason", "unknown reason"),
        })

    return findings


def overall_tier(findings: list[dict]) -> str | None:
    """Pure: the worst tier present, or None when there are no findings."""
    tiers = {f["tier"] for f in findings}
    if "critical" in tiers:
        return "critical"
    if "warn" in tiers:
        return "warn"
    return None


def main() -> int:
    th = thresholds()
    try:
        m = measure()
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "tool_error": str(exc)}, indent=2))
        return 2
    disk = measure_disk()
    findings = classify(m, disk, th)
    print(json.dumps({
        "ok": not findings,
        "tier": overall_tier(findings),
        "measurement": m,
        "disk": disk,
        "thresholds": th,
        "findings": findings,
    }, indent=2))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
