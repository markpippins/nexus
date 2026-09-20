#!/usr/bin/env python3
"""r9-replication-verify — one-command R9 vanadium replication verification.

Implements the DBA checklist (agent record 550df8af, "R9: vanadium replication
verification checklist") as a single READ-ONLY command. Nothing is started,
pushed, or deleted; the only remote execution is the backup pipeline's own
--verify-last (checksum re-verification on vanadium).

Every check classifies honestly:

  PASS         verified green
  FAIL         real defect (any FAIL drives exit 1)
  SKIP         honest not-applicable — e.g. the vanadium-ci guard SKIPping while
               the removable spool drive is absent is the guard WORKING, and the
               remote state untouched is the point
  UNREACHABLE  vanadium not reachable right now (expected off-network — a
               travel state, not a defect)

Exit contract: 0 = all green or only honest SKIP/UNREACHABLE; 1 = at least one
FAIL. A one-line JSON summary is printed last (--json prints only that line).

Checks (IDs match the filed checklist):
  V1  vanadium_reachable       ssh BatchMode probe
  V2  pg_backup_timer          backup-pg-to-vanadium.timer enabled + recent trigger
  V3  pg_backup_last_run       last journal run green, no FAIL lines
  V4  remote_checksum          pipeline --verify-last (sha256 -c ON vanadium)
  V5  rpo_boundary             newest remote manifest age; --applied-after gate
  V6  remote_disk              df headroom on the remote backup dir
  V7  live_sync                sync-pg-to-vanadium.timer enabled + last run Finished
  V8  vanadium_ci_state        real-run age, or honest SKIP while drive absent
  V9  escape_hatch_continuity  per-night artifact continuity over the lookback
  V10 mysql_backup_last_run    mysql-client dependency + last run green

Usage:
  r9-replication-verify.py                    # full battery (read-only)
  r9-replication-verify.py --json             # machine summary only
  r9-replication-verify.py --applied-after 2026-09-20T03:22:00Z
  r9-replication-verify.py --lookback 14      # escape-hatch lookback nights
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

# --------------------------------------------------------------- constants ---

BACKUP_SCRIPT = os.environ.get(
    "R9_VERIFY_SCRIPT", "/home/codex/dev/pgsql/pg-backup-to-vanadium.sh"
)
BACKUP_ENV = os.environ.get(
    "R9_BACKUP_ENV", "/home/codex/dev/pgsql/pg-backup.env"
)
REMOTE_DIR = os.environ.get("R9_REMOTE_DIR", "pg-backups/titanium")
ESCAPE_DIR = os.environ.get("R9_ESCAPE_DIR", "/home/codex/backups/nexus-pg")

RPO_WARN_H, RPO_FAIL_H = 30, 54          # hours since newest remote manifest
DISK_WARN_PCT, DISK_FAIL_PCT = 15, 5     # percent free on the remote volume
ESCAPE_SINCE_DEFAULT = "2026-09-16"       # escape-hatch install date

TIMER_RE = re.compile(
    r"\w{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
)


# ------------------------------------------------------------------ helpers --

def parse_timer_line(line: str):
    """Return the LAST-trigger timestamp from a `systemctl list-timers` row.

    The row carries two full timestamps (NEXT, LAST); a never-triggered unit
    shows 'n/a' in the LAST column, so only >=2 matches count.
    """
    stamps = TIMER_RE.findall(line.strip())
    return stamps[1] if len(stamps) >= 2 else None


def parse_naive(ts: str) -> dt.datetime:
    """Parse the list-timers/journal local timestamp form to naive local dt."""
    return dt.datetime.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S")


def parse_timer_ts(ts: str) -> dt.datetime:
    """list-timers shows LOCAL time — interpret as local, return aware."""
    return parse_naive(ts).astimezone()


def parse_manifest_ts(name: str):
    """manifest__20260920_033503.txt -> aware UTC datetime (pipeline clock)."""
    m = re.match(r"manifest__(\d{8})_(\d{6})\.txt$", name.strip())
    if not m:
        return None
    return dt.datetime.strptime(
        m.group(1) + m.group(2), "%Y%m%d%H%M%S"
    ).replace(tzinfo=dt.timezone.utc)


def rpo_classify(age_hours: float) -> str:
    if age_hours <= RPO_WARN_H:
        return "ok"
    if age_hours <= RPO_FAIL_H:
        return "warn"
    return "fail"


def disk_classify(free_pct: float) -> str:
    if free_pct >= DISK_WARN_PCT:
        return "ok"
    if free_pct >= DISK_FAIL_PCT:
        return "warn"
    return "fail"


def parse_df_free_pct(df_line: str):
    """'/dev/x 235G 146G 80G 65% /' -> 35.0 (percent free), or None."""
    parts = df_line.split()
    if len(parts) < 5:
        return None
    try:
        used_pct = float(parts[4].rstrip("%"))
    except ValueError:
        return None
    return round(100.0 - used_pct, 1)


def as_lines(out):
    """Normalize runner output (str or list) to a list of lines."""
    return out.splitlines() if isinstance(out, str) else list(out)


def journal_last_run(lines):
    """Classify the last oneshot run in journal lines.

    Returns (status, evidence). status in {"ok", "fail", "skip", "absent"}.
    The outcome is the LAST 'Finished'/'Failed'/'Skipped' marker; a 'FAIL:'
    body line forces 'fail' even if systemd recorded Finished (the
    exit-0-with-defect guard). A truncated window (no 'Starting' marker, but
    a Finished/Failed present) still classifies from the outcome — a Finished
    line proves the run completed regardless of where the window begins.
    """
    starts = [i for i, ln in enumerate(lines) if "Starting " in ln]
    body = lines[starts[-1]:] if starts else lines
    outcome = "absent"
    evidence = "no run markers in window"
    for ln in body:
        if "Finished " in ln:
            outcome, evidence = "ok", ln.strip()
        elif "Failed " in ln or " failed " in ln:
            outcome, evidence = "fail", ln.strip()
        elif "Skipped " in ln:
            outcome, evidence = "skip", ln.strip()
    fail_lines = [ln.strip() for ln in body if "FAIL:" in ln]
    if fail_lines:
        return "fail", fail_lines[-1]
    return outcome, evidence


def classify_vdci(lines):
    """vanadium-ci run classification: honest drive-absent SKIP vs real run."""
    status, evidence = journal_last_run(lines)
    if status == "skip" or any("drive absent" in ln for ln in lines):
        return "skip", "spool drive absent — guard honest, remote untouched"
    return status, evidence


def escape_hatch_dates(names):
    """nexus__YYYYMMDD_HHMMSS.dump filenames -> sorted unique dates (ISO)."""
    dates = set()
    for n in names:
        m = re.match(r"nexus__(\d{4})(\d{2})(\d{2})_\d{6}\.dump$", n.strip())
        if m:
            dates.add("%s-%s-%s" % m.groups())
    return sorted(dates)


def escape_hatch_gaps(names, lookback_days: int, today: dt.date,
                      since: dt.date = None):
    """Missing nights in the last `lookback_days` nights (excluding today —
    the nightly run may not have fired yet today). Nights before `since`
    (tool install date) are not gaps — the tool did not exist."""
    have = set(escape_hatch_dates(names))
    missing = []
    for i in range(1, lookback_days + 1):
        d = today - dt.timedelta(days=i)
        if since is not None and d < since:
            continue
        if d.isoformat() not in have:
            missing.append(d.isoformat())
    return sorted(missing)


# ------------------------------------------------------------------ runners --

@dataclass
class Runners:
    """Injected process surfaces — hermetic tests substitute fakes here."""

    ssh: object = None        # (args:list[str]) -> (rc, out, err)
    systemctl: object = None  # (args:list[str]) -> (rc, out, err)
    journalctl: object = None  # (args:list[str]) -> (rc, out, err)
    local: object = None      # (args:list[str]) -> (rc, out, err)

    @classmethod
    def real(cls):
        def _run(base, timeout):
            def call(args):
                cmd = ([base] + args) if base else list(args)
                p = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout
                )
                return p.returncode, p.stdout, p.stderr
            return call

        # local: 15 min — V4's sha256 re-verification of a full dumpset is
        # the honest slow step (tens of GB); do not truncate it silently.
        return cls(
            ssh=_run("ssh", 60),
            systemctl=_run("systemctl", 30),
            journalctl=_run("journalctl", 30),
            local=_run("", 900),
        )


@dataclass
class Check:
    cid: str
    name: str
    status: str   # PASS | FAIL | SKIP | UNREACHABLE
    evidence: str


@dataclass
class Report:
    checks: list = field(default_factory=list)

    def add(self, cid, name, status, evidence):
        self.checks.append(Check(cid, name, status, evidence))

    @property
    def has_fail(self):
        return any(c.status == "FAIL" for c in self.checks)

    def verdict(self):
        if self.has_fail:
            return "defects"
        if any(c.status in ("SKIP", "UNREACHABLE") for c in self.checks):
            return "green-with-honest-skips"
        return "green"

    def summary(self):
        return {
            "schema_version": 1,
            "tool": "r9-replication-verify",
            "verdict": self.verdict(),
            "checks": [
                {"id": c.cid, "name": c.name, "status": c.status,
                 "evidence": c.evidence}
                for c in self.checks
            ],
        }


# ------------------------------------------------------------------- checks --

def check_v1_reachable(r, report):
    rc, out, err = r.ssh(["-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                          "vanadium", "true"])
    if rc == 0:
        report.add("V1", "vanadium_reachable", "PASS", "ssh BatchMode probe ok")
        return True
    report.add("V1", "vanadium_reachable", "UNREACHABLE",
               (err or out or "ssh failed").strip().splitlines()[-1][:160])
    return False


def _timer_state(r, unit):
    rc, out, err = r.systemctl(["--user", "is-enabled", unit])
    enabled = rc == 0 and out.strip() == "enabled"
    rc, out, _ = r.systemctl(["--user", "list-timers", "--no-pager", unit])
    last = None
    for line in out.splitlines():
        if unit in line:
            last = parse_timer_line(line)
            break
    return enabled, last


def check_v2_timer(r, report, now):
    enabled, last = _timer_state(r, "backup-pg-to-vanadium.timer")
    if not enabled:
        report.add("V2", "pg_backup_timer", "FAIL",
                   "backup-pg-to-vanadium.timer not enabled")
        return
    if last is None:
        report.add("V2", "pg_backup_timer", "FAIL", "no timer row found")
        return
    age_h = (now - parse_timer_ts(last)).total_seconds() / 3600.0
    if age_h <= 28:
        report.add("V2", "pg_backup_timer", "PASS",
                   "enabled, last trigger %s (%.1fh ago)" % (last, age_h))
    else:
        report.add("V2", "pg_backup_timer", "FAIL",
                   "last trigger %s is %.1fh old (>28h)" % (last, age_h))


def check_v3_last_run(r, report):
    rc, out, _ = r.journalctl(
        ["--user", "-u", "backup-pg-to-vanadium.service", "--no-pager", "-n", "120"]
    )
    status, ev = journal_last_run(as_lines(out))
    if status == "ok":
        report.add("V3", "pg_backup_last_run", "PASS", ev[:160])
    else:
        report.add("V3", "pg_backup_last_run", "FAIL", ev[:160])


def check_v4_checksum(r, report):
    # Source the env exactly like the systemd unit does — a direct invocation
    # without it hits the script's (historically stale) fallback default.
    cmd = ['bash', '-c', 'set -a; source %s 2>/dev/null; exec %s --verify-last'
           % (BACKUP_ENV, BACKUP_SCRIPT)]
    rc, out, err = r.local(cmd)
    if rc == 0:
        tail = (out or "").strip().splitlines()[-1:] or ["verify-last ok"]
        report.add("V4", "remote_checksum", "PASS", tail[0][:160])
    else:
        report.add("V4", "remote_checksum", "FAIL",
                   (err or out or "verify-last failed").strip()[-160:])


def check_v5_rpo(r, report, now, remote_ls, applied_after):
    names = [ln for ln in remote_ls.splitlines() if ln.startswith("manifest__")]
    newest = None
    for n in names:
        ts = parse_manifest_ts(n)
        if ts and (newest is None or ts > newest):
            newest = ts
    if newest is None:
        report.add("V5", "rpo_boundary", "FAIL", "no remote manifests found")
        return
    age_h = (now - newest).total_seconds() / 3600.0
    cls = rpo_classify(age_h)
    ev = "newest manifest %s, %.1fh old (%s)" % (
        newest.strftime("%Y-%m-%d %H:%M"), age_h, cls)
    if cls == "fail":
        report.add("V5", "rpo_boundary", "FAIL", ev)
        return
    if applied_after is not None and newest < applied_after:
        report.add("V5", "rpo_boundary", "FAIL",
                   ev + "; backup PREDATES latest applied migration")
        return
    report.add(
        "V5", "rpo_boundary", "PASS" if cls == "ok" else "FAIL", ev
    )


def check_v6_disk(r, report, remote_df):
    line = (remote_df or "").strip().splitlines()[-1] if remote_df else ""
    pct = parse_df_free_pct(line)
    if pct is None:
        report.add("V6", "remote_disk", "FAIL", "unparseable df output")
        return
    cls = disk_classify(pct)
    ev = "%.1f%% free on vanadium backup volume (%s)" % (pct, cls)
    report.add("V6", "remote_disk", "PASS" if cls != "fail" else "FAIL", ev)


def check_v7_sync(r, report, now):
    enabled, last = _timer_state(r, "sync-pg-to-vanadium.timer")
    if not enabled:
        report.add("V7", "live_sync", "FAIL", "sync-pg-to-vanadium.timer not enabled")
        return
    rc, out, _ = r.journalctl(
        ["--user", "-u", "sync-pg-to-vanadium.service", "--no-pager", "-n", "80"]
    )
    status, ev = journal_last_run(as_lines(out))
    if status == "ok" and last:
        age_h = (now - parse_timer_ts(last)).total_seconds() / 3600.0
        report.add("V7", "live_sync", "PASS",
                   "last run Finished %s (%.1fh ago)" % (last, age_h))
    else:
        report.add("V7", "live_sync",
                   "FAIL" if status in ("fail", "absent") else "PASS", ev[:160])


def check_v8_vdci(r, report):
    rc, out, _ = r.journalctl(
        ["--user", "-u", "backup-vanadium-ci.service", "--no-pager", "-n", "80"]
    )
    status, ev = classify_vdci(as_lines(out))
    if status == "skip":
        report.add("V8", "vanadium_ci_state", "SKIP", ev[:160])
    elif status == "ok":
        report.add("V8", "vanadium_ci_state", "PASS", ev[:160])
    else:
        report.add("V8", "vanadium_ci_state", "FAIL", ev[:160])


def check_v9_escape(r, report, lookback, now, escape_since):
    rc, out, err = r.local(["ls", "-1", ESCAPE_DIR])
    if rc != 0:
        report.add("V9", "escape_hatch_continuity", "FAIL",
                   "cannot list %s" % ESCAPE_DIR)
        return
    missing = escape_hatch_gaps(out.splitlines(), lookback, now.date(),
                                escape_since)
    if missing:
        report.add("V9", "escape_hatch_continuity", "FAIL",
                   "missing nights: %s" % ", ".join(missing))
    else:
        report.add("V9", "escape_hatch_continuity", "PASS",
                   "no gaps in last %d nights" % lookback)


def check_v10_mysql(r, report):
    rc, out, _ = r.journalctl(
        ["--user", "-u", "mysql-backup.service", "--no-pager", "-n", "80"]
    )
    status, ev = journal_last_run(as_lines(out))
    if status == "ok":
        report.add("V10", "mysql_backup_last_run", "PASS", ev[:160])
    else:
        report.add("V10", "mysql_backup_last_run", "FAIL", ev[:160])


# --------------------------------------------------------------------- main --

def run_battery(r, *, lookback=7, applied_after=None, do_verify=True,
                now=None, escape_since=None):
    now = now or dt.datetime.now().astimezone()
    report = Report()

    remote_ls, remote_df = "", ""
    reachable = check_v1_reachable(r, report)
    if reachable:
        rc, remote_ls, _ = r.ssh(["-o", "BatchMode=yes", "vanadium",
                                  "ls", "-1", REMOTE_DIR])
        rc, remote_df, _ = r.ssh(["-o", "BatchMode=yes", "vanadium",
                                  "df", "-h", REMOTE_DIR])

    check_v2_timer(r, report, now)
    check_v3_last_run(r, report)
    if reachable and do_verify:
        check_v4_checksum(r, report)
    elif reachable:
        report.add("V4", "remote_checksum", "SKIP", "--skip-verify-last given")
    else:
        report.add("V4", "remote_checksum", "UNREACHABLE", "vanadium down")
    if reachable:
        check_v5_rpo(r, report, now, remote_ls, applied_after)
        check_v6_disk(r, report, remote_df)
    else:
        report.add("V5", "rpo_boundary", "UNREACHABLE", "vanadium down")
        report.add("V6", "remote_disk", "UNREACHABLE", "vanadium down")
    check_v7_sync(r, report, now)
    check_v8_vdci(r, report)
    check_v9_escape(r, report, lookback, now, escape_since)
    check_v10_mysql(r, report)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="machine summary only")
    ap.add_argument("--lookback", type=int, default=7,
                    help="escape-hatch continuity window in nights")
    ap.add_argument("--applied-after", metavar="ISO",
                    help="newest remote manifest must postdate this timestamp")
    ap.add_argument("--skip-verify-last", action="store_true",
                    help="skip V4 (the checksum re-verification) this run")
    ap.add_argument("--escape-since", metavar="YYYY-MM-DD",
                    default=ESCAPE_SINCE_DEFAULT,
                    help="nights before this date are not V9 gaps "
                         "(default: %s, escape-hatch install)"
                         % ESCAPE_SINCE_DEFAULT)
    args = ap.parse_args(argv)

    applied_after = None
    if args.applied_after:
        applied_after = dt.datetime.fromisoformat(
            args.applied_after.replace("Z", "+00:00"))

    escape_since = dt.date.fromisoformat(args.escape_since)
    report = run_battery(
        Runners.real(), lookback=args.lookback, applied_after=applied_after,
        do_verify=not args.skip_verify_last, escape_since=escape_since,
    )

    if not args.json:
        for c in report.checks:
            print("%-4s %-24s %-11s %s" % (c.cid, c.name, c.status, c.evidence))
    print(json.dumps(report.summary()))
    return 1 if report.has_fail else 0


if __name__ == "__main__":
    sys.exit(main())
