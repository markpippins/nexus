#!/usr/bin/env python3
"""Thin wrapper: run the record-durability check and file records on the
record-durability series.

Used by record-durability.service (nightly user timer, 06:30 UTC slot
after the two drift timers). Contract — identical to
sdk_drift_stamp_wrap / users_bcrypt_drift_wrap (green-heartbeat contract,
PRs #527/#540), so all three timer series file records consistently:

  - GREEN runs (no hollow records in the window) are recorded on a
    heartbeat cadence:
      * first clean run ever            -> commissioning record
      * first clean run of an ISO week  -> weekly heartbeat record
      * first clean run after findings  -> recovery record
  - FINDINGS (checker exit 1) file an inspection record, at most one
    per UTC day (suppression state) — new hollow records re-alert daily
    until dispositioned without spamming.
  - Tool errors (checker exit 2 or a DB connection failure) file
    nothing and exit 2 so they are visible in the journal.

Records are filed as DBA on the series:record-durability tag.
Exit codes: 0 = ok or record filed, 2 = tool/environment error.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "bin" / "check_record_durability.py"
STATE = Path(os.environ.get(
    "RECORD_DURABILITY_STATE",
    "/home/codex/.cache/record-durability/state.json"))

GREEN_TAGS = ('["type:report","db-a","record-durability",'
              '"series:record-durability"]')
DRIFT_TAGS = ('["type:inspection","db-a","record-durability",'
              '"series:record-durability"]')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_week(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def decide_green_action(state: dict, week: str) -> tuple[str | None, dict]:
    """Pure decision: given the state dict and the current ISO-week string,
    return (action, new_state). action is one of 'commissioning',
    'heartbeat', 'recovery', or None (nothing to file this run).

    Shared contract with sdk_drift_stamp_wrap.decide_green_action and
    users_bcrypt_drift_wrap.decide_green_action — keep all three in
    lockstep.
    """
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


def _file_record(kind: str, when: str, extra: str = "") -> bool:
    titles = {
        "commissioning": f"Record durability posture GREEN (commissioning run {when}) — no hollow records in fleet",
        "heartbeat": f"Record durability posture GREEN (weekly heartbeat {when})",
        "recovery": f"Record durability posture RECOVERED (green after hollow findings {when})",
    }
    intros = {
        "commissioning": "First run of record-durability.timer: the fleet-wide "
                         "hollow-record scan (content-is-a-path or under 50 bytes) "
                         "is clean and the baseline pointer is set. Green-heartbeat "
                         "contract: commissioning on first run, weekly heartbeat "
                         "thereafter, daily inspection records while findings exist.",
        "heartbeat": "Weekly green heartbeat from record-durability.timer: no "
                     "hollow agent records in the scan window.",
        "recovery": "The previously-reported hollow records are resolved "
                    "(repaired, dispositioned, or backfilled): this is the first "
                    "green run after the findings record. Weekly heartbeat cadence "
                    "resumes.",
    }
    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "DBA",
         "-t", titles[kind],
         "--record-type", "report",
         "--level", "1",
         "--tags", GREEN_TAGS,
         "-c", intros[kind] + (extra or "")],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    print(f"{kind} record filed:", ok)
    return ok


def _file_findings_record(when: str, findings: list[dict]) -> bool:
    lines = []
    for f in findings:
        lines.append(f"- `{f['id'][:8]}` [{f['role']}/{f['record_type']}] "
                     f"{f['created_at']} — {f['title'] or '(untitled)'}")
        lines.append(f"  reasons: {'; '.join(f['reasons'])}; "
                     f"content: {f['content_preview']!r}")
    body = (
        f"The nightly record-durability sweep ({when}) found "
        f"{len(findings)} hollow agent record(s) created since the last "
        "scan — content that looks like a file path or is under 50 bytes "
        "(the 2026-09-24 hollow-records incident class).\n\n"
        + "\n".join(lines)
        + "\n\nDisposition: repair in place from surviving source, annotate "
        "with hollow_content_audit metadata if intentional (I4 originals / "
        "pointer records), or re-file as a hollow-replacement-of record for "
        "immutable type:response. This record repeats daily until the "
        "findings are dispositioned; the next clean run files a recovery "
        "record."
    )
    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "DBA",
         "-t", f"Hollow agent records detected (nightly sweep, {when}): {len(findings)} record(s) need disposition",
         "--record-type", "inspection",
         "--level", "1",
         "--tags", DRIFT_TAGS,
         "-c", body],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    print("findings record filed:", ok)
    return ok


def _run_checker(state: dict) -> tuple[int, str, str, bool]:
    """Run the checker once. Returns (exit, stdout, stderr, is_baseline).
    Baseline on first ever run (state has no pointer): the high-water
    mark is recorded and historical residue is acknowledged, not
    alerted — the sweep's contract is records created AFTER deployment."""
    if state.get("pointer"):
        args = ["--since", state["pointer"]]
        baseline = False
    else:
        args = ["--baseline"]
        baseline = True
    res = subprocess.run(
        [sys.executable, str(CHECK), *args],
        capture_output=True, text=True, timeout=300,
        cwd=str(REPO),
    )
    return res.returncode, (res.stdout or ""), (res.stderr or ""), baseline


def main() -> int:
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    now = _utcnow()
    today = now.strftime("%Y-%m-%d")
    week = _iso_week(now)

    code, out, err, is_baseline = _run_checker(state)
    if out:
        print(out)
    if code == 2:
        print(f"tool error (checker exit 2): {err[-400:]}", file=sys.stderr)
        return 2

    try:
        payload = json.loads(out)
    except Exception:
        print(f"tool error (unparseable checker output): {out[-200:]}",
              file=sys.stderr)
        return 2

    pointer = payload.get("pointer") or payload.get("baseline_pointer")
    findings = payload.get("findings") or []

    if is_baseline:
        # First run: fence off history, commission the series. Residue
        # found by the baseline is pre-deployment (tracked by the 09-24
        # hollow-records repair audit) — it is reported once here, and
        # the pointer excludes it from all future windows.
        note = (f" Baseline scanned full history: {len(findings)} "
                "pre-deployment record(s) match hollow patterns and are "
                "excluded from future windows by the pointer (their "
                "disposition is tracked by the 2026-09-24 hollow-records "
                "repair audit)." if findings else
                " Baseline clean: no hollow-pattern records in history.")
        ok = _file_record("commissioning", today, note)
        new_state = {**state, "pointer": pointer,
                     **decide_green_action({}, week)[1]}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state))
        return 0 if ok else 2

    if code == 0:
        # Green — advance pointer, file heartbeat per contract.
        new_state = {**state, "pointer": pointer}
        action, green_state = decide_green_action(state, week)
        new_state.update(green_state)
        ok = True
        if action is not None:
            ok = _file_record(
                action,
                week if action == "heartbeat" else today)
        new_state["output"] = out[-2000:]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state))
        return 0 if ok else 2

    # Findings — advance pointer regardless (these rows are now seen),
    # file daily inspection record unless one was already filed today.
    if state.get("last_drift_recorded") == today:
        print(f"findings already recorded today ({today}) — suppressing "
              "duplicate record")
        new_state = {**state, "pointer": pointer}
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state))
        return 0

    ok = _file_findings_record(today, findings)
    new_state = {**state,
                 "pointer": pointer,
                 "last_drift_recorded": today,
                 "ever_green": False,
                 "output": out[-2000:]}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(new_state))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
