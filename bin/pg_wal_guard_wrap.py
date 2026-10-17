#!/usr/bin/env python3
"""Wrapper: run the pg_wal guard and file its records on the pg-wal series.

Used by pg-wal-guard.timer (every 15 minutes — the incident grew ~3 GB per
32 h, so a 15-minute cadence catches a threshold crossing long before it
becomes a disk emergency, at negligible cost).

Contract (green-heartbeat, same shape as pg_logging_check_wrap /
record_durability_wrap / sdk_drift_stamp_wrap — PRs #527/#540/#542, keep in
lockstep):

  - GREEN runs are recorded on a heartbeat cadence: commissioning on the
    first run ever, weekly heartbeat on the first clean run of an ISO week,
    recovery on the first clean run after findings.
  - FINDINGS file an incident record, at most one per UTC day EXCEPT when
    the tier escalates to `critical`, which files immediately — a steady
    known problem should not drown the log, but a new emergency must not
    wait up to 24 hours behind a suppression window.
  - Tool errors (checker exit 2: psql unreachable) file nothing and exit 2
    — journal-visible, never a silent green.

Beyond the checker's per-run findings, this wrapper owns the one check that
needs history: whether the archiver is actually *progressing*. A flat
`archived_count` across runs with a standing `.ready` backlog is the
"counter nobody reads" failure from incident e7a15424, and it is invisible
to any single measurement.

Records are filed as DBA and tagged to:dba + to:devops (both lanes were in
the incident). Exit codes: 0 = ok or record filed, 2 = tool/environment error.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "bin" / "check_pg_wal.py"
STATE = Path(os.environ.get("PG_WAL_GUARD_STATE",
                            "/home/codex/.cache/pg-wal-guard/state.json"))

# A standing backlog below this is normal churn (a segment or two mid-switch)
# and proves nothing about progress; only a real backlog makes a flat
# counter meaningful.
STALL_READY_FLOOR = int(os.environ.get("PG_WAL_STALL_READY_FLOOR", "10"))

GREEN_TAGS = '["type:report","scope:pg_wal","series:pg-wal-guard"]'
FINDING_TAGS = ('["type:incident","scope:pg_wal","status:open","to:dba",'
                '"to:devops","series:pg-wal-guard"]')
RECOVERY_TAGS = ('["type:report","scope:pg_wal","status:resolved","to:dba",'
                 '"to:devops","series:pg-wal-guard"]')

# The role these records are filed as. Lowercase canonical form: the role
# vocabulary is case-sensitive end to end (role_memory, roles.json, the
# assembly alias after V203/V204), and post-agent-record.py validates against
# it, so 'DBA' is rejected outright. That exact case error silently disabled
# four sibling guard wrappers on 2026-10-05 — see test_guard_wrapper_roles.py.
RECORD_ROLE = "dba"

# Calibrated constants for re-usable context in record bodies.
CRIT_READY_CONTEXT = 2000
INCIDENT_REF = "e7a15424"
RULING_REF = "3056d149"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_week(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def decide_green_action(state: dict, week: str) -> tuple[str | None, dict]:
    """Pure decision — shared contract with pg_logging_check_wrap,
    record_durability_wrap, sdk_drift_stamp_wrap. Keep in lockstep."""
    new_state = dict(state)
    if not state.get("ever_green"):
        action = "recovery" if state.get("last_drift_recorded") else "commissioning"
        new_state.pop("last_drift_recorded", None)
        new_state["ever_green"] = True
        new_state["last_green_recorded"] = week
        return action, new_state
    if state.get("last_green_recorded") != week:
        return "heartbeat", {**new_state, "last_green_recorded": week}
    return None, new_state


def decide_archiver_stall(prev: dict | None, m: dict,
                          floor: int = STALL_READY_FLOOR) -> dict | None:
    """Pure: did the archiver fail to make progress since the last run?

    Returns a finding dict or None. Deliberately silent when archiving is
    off (nothing should be archiving, so a flat counter is correct) and when
    the same misconfiguration is already reported as a construction finding,
    so one root cause never yields two records.
    """
    mode = (m.get("archive_mode") or "").strip().lower()
    if mode not in ("on", "always"):
        return None
    if not ((m.get("archive_command") or "").strip()
            or (m.get("archive_library") or "").strip()):
        return None  # covered by the construction rule in the checker
    if (m.get("ready") or 0) < floor:
        return None  # no backlog to archive: nothing to conclude
    if not prev or prev.get("archived_count") is None:
        return None  # first sighting; only a later run can prove a flat line
    if m["archived_count"] > prev["archived_count"]:
        return None  # advancing
    return {
        "dimension": "archiver_stall",
        "tier": "critical",
        "message": (
            "archiving is enabled with a target, but the archiver made no "
            "progress: archived_count is still %d after a previous run saw "
            "%d, with %d segment(s) .ready (last_archived_time=%s). WAL is "
            "being retained while archiving silently does nothing — the "
            "failure mode of incident %s."
            % (m["archived_count"], prev["archived_count"], m.get("ready", 0),
               m.get("last_archived_time") or "never", INCIDENT_REF)),
    }


def escalated(prev_state: dict, tier: str | None) -> bool:
    """Pure: is this tier worse than what was last alerted? (critical only)"""
    if tier != "critical":
        return False
    return prev_state.get("last_alerted_tier") != "critical"


def _file_record(title: str, record_type: str, tags: str, body: str) -> bool:
    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", RECORD_ROLE, "-t", title, "--record-type", record_type,
         "--level", "1", "--tags", tags, "-c", body],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    if not ok:
        print(f"record filing failed rc={record.returncode}: "
              f"{(record.stderr or '')[-300:]}", file=sys.stderr)
    return ok


def _fmt_bytes(n) -> str:
    if n is None:
        return "unknown"
    gb = 1024 ** 3
    if abs(n) >= gb:
        return f"{n / gb:.1f} GB"
    return f"{n / (1024 ** 2):.0f} MB"


def _evidence(payload: dict, when: str) -> str:
    """The measured numbers, so the alert is actionable without re-running."""
    m = payload.get("measurement", {})
    disk = payload.get("disk", {})
    th = payload.get("thresholds", {})
    lines = [
        f"Measured {when} on the nexus PostgreSQL cluster "
        f"(server {m.get('server_version', '?')}).",
        "",
        f"- **pg_wal**: {_fmt_bytes(m.get('wal_bytes'))} across "
        f"{m.get('seg_count')} segment(s)",
        f"- **.ready backlog**: {m.get('ready')} ready / {m.get('done')} done",
        f"- **archiving**: archive_mode={m.get('archive_mode')}, "
        f"archive_command={m.get('archive_command')!r}, "
        f"archive_library={m.get('archive_library')!r}",
        f"- **archiver**: archived_count={m.get('archived_count')}, "
        f"failed_count={m.get('failed_count')}, "
        f"last_archived_time={m.get('last_archived_time') or 'never'}",
    ]
    if disk.get("measured"):
        pct = (100.0 * (1 - disk["free_bytes"] / disk["total_bytes"])
               if disk.get("total_bytes") else 0.0)
        lines.append(f"- **disk**: {_fmt_bytes(disk['free_bytes'])} free on "
                     f"{disk['mount']} ({pct:.0f}% used; pgdata host path "
                     f"{disk.get('pgdata_host')})")
    else:
        lines.append(f"- **disk**: UNMEASURED — {disk.get('reason')}")
    lines += [
        "",
        "Thresholds in force: pg_wal warn "
        f"{_fmt_bytes(th.get('wal_warn_bytes'))} / critical "
        f"{_fmt_bytes(th.get('wal_crit_bytes'))}; .ready backlog warn "
        f"{th.get('ready_warn')} / critical {th.get('ready_crit')}; free disk "
        f"warn {_fmt_bytes(th.get('disk_warn_free_bytes'))} / critical "
        f"{_fmt_bytes(th.get('disk_crit_free_bytes'))}.",
    ]
    return "\n".join(lines)


def _findings_body(payload: dict, when: str, tier: str) -> str:
    rows = "\n".join(
        f"| {f['dimension']} | {f['tier']} | {f['message']} |"
        for f in payload.get("findings", []))
    return (
        "The pg_wal / archiver guard found the cluster's WAL retention "
        f"posture broken. **Overall tier: {tier.upper()}**\n\n"
        "| dimension | tier | detail |\n|---|---|---|\n" + rows + "\n\n"
        + _evidence(payload, when) + "\n\n"
        "**Why this is filed as an incident rather than a note:** PostgreSQL "
        f"with archiving enabled pins WAL until it is archived. Incident "
        f"`{INCIDENT_REF}` ran for 30 days this way and reached 156 GB / "
        "10,007 `.ready` segments with 17 GB free on a 444 GB filesystem; at "
        "100% PostgreSQL PANICs and the canonical database goes down. This "
        f"guard implements the observability ruling `{RULING_REF}`: pg_wal "
        "size, `.ready` backlog, disk headroom, and archiver progress.\n\n"
        "Suggested first moves: read the archiving line above — an enabled "
        "mode with an empty command is a configuration defect, not a data "
        "problem. Check the pgbackrest/WAL-G destination before assuming "
        "archiving works. **Do not** simply flip `archive_mode=on` to test: "
        "with no destination that is precisely how "
        f"`{INCIDENT_REF}` started.\n\n"
        "This record repeats at most once per UTC day while the findings "
        "persist (immediately again on escalation to critical). The next "
        "clean run files a recovery record.\n"
    )


def _green_body(action: str, payload: dict, when: str) -> str:
    intro = {
        "commissioning": (
            "First run of pg-wal-guard.timer. The cluster's WAL retention "
            "posture is measured and healthy: pg_wal size, `.ready` backlog, "
            "disk headroom on the filesystem holding pgdata, and archiver "
            "progress are all within thresholds. This guard exists because a "
            f"DB-level config defect went 30 days unnoticed in "
            f"`{INCIDENT_REF}` (ruling `{RULING_REF}`); this record "
            "commissions that guard rather than reporting a state change."),
        "heartbeat": (
            "Weekly green heartbeat from pg-wal-guard.timer: the WAL "
            "retention posture was measured and is healthy."),
        "recovery": (
            "The previously-reported pg_wal guard findings are resolved: "
            "first clean run after the incident record. Weekly heartbeat "
            "cadence resumes."),
    }[action]
    return (intro + "\n\n" + _evidence(payload, when) + "\n\n"
            + f"Verdict: no finding on any dimension ({when}).\n")


def main() -> int:
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}

    res = subprocess.run(
        [sys.executable, str(CHECK)],
        capture_output=True, text=True, timeout=120, cwd=str(REPO),
    )
    out = (res.stdout or "").strip()
    if out:
        print(out)
    if res.returncode not in (0, 1):
        print(f"tool error (checker exit {res.returncode}): "
              f"{(res.stderr or out)[-400:]}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(out)
    except Exception:
        print(f"tool error (unparseable checker output): {out[-200:]}",
              file=sys.stderr)
        return 2

    now = _utcnow()
    today = now.strftime("%Y-%m-%d")
    week = _iso_week(now)
    when = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    m = payload.get("measurement", {})

    # The cross-run check the checker cannot do on its own.
    findings = list(payload.get("findings", []))
    stall = decide_archiver_stall(state.get("archiver"), m)
    if stall:
        findings.append(stall)
        payload["findings"] = findings
        payload["ok"] = False
    tier = None
    if findings:
        tier = ("critical" if any(f["tier"] == "critical" for f in findings)
                else "warn")

    # Always carry the archiver snapshot forward, so the next run can judge
    # progress even if this one was clean.
    snapshot = {"archived_count": m.get("archived_count"),
                "ready": m.get("ready"), "at": when}

    if not findings:
        action, new_state = decide_green_action(state, week)
        ok = True
        if action is not None:
            ok = _file_record(
                f"pg_wal posture GREEN ({action} run {when})" if action !=
                "heartbeat" else f"pg_wal posture GREEN (weekly heartbeat {week})",
                "report", GREEN_TAGS, _green_body(action, payload, when))
        if not ok:
            # Do NOT consume the heartbeat. If the record was refused, the
            # green it was supposed to witness does not exist yet; advancing
            # `ever_green`/`last_green_recorded` here would silence the guard
            # for the rest of the week and hide the filing failure behind a
            # clean-looking heartbeat — the exact way four sibling wrappers
            # went dark on 2026-10-05. Exit 2 so systemd reports it.
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps({**state, "archiver": snapshot,
                                         "output": out[-2000:]}))
            return 2
        new_state = {**state, **new_state, "archiver": snapshot,
                     "last_alerted_tier": None, "output": out[-2000:]}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state))
        return 0

    if state.get("last_drift_recorded") == today and not escalated(state, tier):
        print(f"findings already recorded today ({today}, tier {tier}) — "
              "suppressing duplicate record")
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({**state, "archiver": snapshot}))
        return 0

    ok = _file_record(
        f"pg_wal guard findings — {tier.upper()} ({when})",
        "inspection", FINDING_TAGS, _findings_body(payload, when, tier))
    if not ok:
        # Same rule as the green path: a refused record must not be recorded
        # as delivered, or the daily suppression window that follows would
        # swallow the retry and the incident would never reach anyone.
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({**state, "archiver": snapshot,
                                     "output": out[-2000:]}))
        return 2
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({
        **state,
        "last_drift_recorded": today,
        "last_alerted_tier": tier,
        "ever_green": False,
        "archiver": snapshot,
        "output": out[-2000:],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
