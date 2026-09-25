#!/usr/bin/env python3
"""Thin wrapper: run the transition-event attestation check and file records
on the conduit-attestation series.

Used by transition-attestation.service (nightly user timer, 06:50 UTC —
fifth slot in the drift/heartbeat cluster: SDK stamp 06:10, users bcrypt
06:20, record durability 06:30, pg-logging 06:40, attestation 06:50).
Contract — identical to sdk_drift_stamp_wrap / users_bcrypt_drift_wrap /
record_durability_wrap / pg_logging_check_wrap (green-heartbeat contract,
PRs #527/#540 parity), so all five timer series file records consistently:

  - GREEN runs (zero unattested terminal tickets since the cutoff) are
    recorded on a heartbeat cadence: commissioning / weekly heartbeat /
    recovery.
  - DRIFT (checker exit 1 — unattested terminal tickets exist) files an
    inspection record, at most one per UTC day; repeats daily while red.
  - Tool errors (checker exit 2) file nothing and exit 2 — visible in the
    journal, never misread as green or drift (the ae18bba7 lesson).

All records carry the series:conduit-attestation tag for time-series
queries. Exit codes: 0 = ok or record filed, 2 = tool/environment error.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "bin" / "check_transition_attestation.py"
STATE = Path(os.environ.get(
    "TRANSITION_ATTESTATION_STATE",
    "/home/codex/.cache/transition-attestation/state.json"))

GREEN_TAGS = ('["type:report","conduit-attestation","adr-016","db-a",'
              '"series:conduit-attestation"]')
DRIFT_TAGS = ('["type:inspection","conduit-attestation","adr-016","db-a",'
              '"series:conduit-attestation"]')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_week(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def decide_green_action(state: dict, week: str) -> tuple[str | None, dict]:
    """Pure decision: given the state dict and the current ISO-week string,
    return (action, new_state). action is one of 'commissioning',
    'heartbeat', 'recovery', or None (nothing to file this run)."""
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


def _file_record(kind: str, when: str, out: str, extra: str = "") -> bool:
    titles = {
        "commissioning": f"Transition-event attestation GREEN (commissioning run {when}) — every recent terminal ticket attested",
        "heartbeat": f"Transition-event attestation GREEN (weekly heartbeat {when})",
        "recovery": f"Transition-event attestation RECOVERED (green after drift {when})",
    }
    intros = {
        "commissioning": "First run of transition-attestation.timer after "
                         "deployment: the ADR-016 terminal-ticket/transition-event "
                         "join is clean since the adoption cutoff. Green-heartbeat "
                         "contract: commissioning record on first run, weekly "
                         "heartbeat thereafter, daily inspection records while red.",
        "heartbeat": "Weekly green heartbeat from transition-attestation.timer: "
                     "no unattested terminal tickets since the cutoff.",
        "recovery": "The previously-reported transition-attestation drift is "
                    "resolved: this is the first green run after the drift "
                    "record. Weekly heartbeat cadence resumes.",
    }
    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "DBA",
         "-t", titles[kind],
         "--record-type", "report",
         "--level", "1",
         "--tags", GREEN_TAGS,
         "-c", intros[kind] + (extra or "")
         + "\n\nChecker output:\n\n" + (out or "(no output)")],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    print(f"{kind} record filed:", ok)
    return ok


def main() -> int:
    res = subprocess.run(
        [sys.executable, str(CHECK)],
        capture_output=True, text=True, timeout=120,
        cwd=str(REPO),
        env={**os.environ,
             "CONDUIT_PG_DSN": os.environ.get(
                 "CONDUIT_PG_DSN",
                 "postgresql://pguser:pgpass@localhost:5432/nexus")},
    )
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if out:
        print(out)
    if res.returncode == 2:
        # Tool error — surface it but file nothing.
        print(err, file=sys.stderr)
        return 2

    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    now = _utcnow()
    today = now.strftime("%Y-%m-%d")
    week = _iso_week(now)

    if res.returncode == 0:
        action, new_state = decide_green_action(state, week)
        if action is None:
            return 0
        ok = _file_record(action, week if action == "heartbeat" else today, out)
        new_state["output"] = out[-2000:]
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(new_state))
        return 0 if ok else 2

    # Drift found — file the record (this is the timer doing its job).
    if state.get("last_drift_recorded") == today:
        print(f"drift already recorded today ({today}) — suppressing duplicate record")
        return 0

    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "DBA",
         "-t", f"Transition-event attestation DRIFT (daily check, {today}): terminal tickets mutated without transition events",
         "--record-type", "inspection",
         "--level", "1",
         "--tags", DRIFT_TAGS,
         "-c",
         "The daily transition-event attestation check "
         "(transition-attestation.timer) found terminal tickets closed since "
         "the adoption cutoff (2026-09-22) with NO kernel.transition_event "
         "rows: state was mutated without an audit trail (ADR-016 violation "
         "class; ruling 1867c88d requires durable transitions per "
         "generation). A silent write path, an out-of-band mutation, or a "
         "projection gap. Checker output:\n\n"
         + (out or "(no output)")
         + "\n\nThis record repeats daily until the drift is dispositioned. "
           "The next green run files a recovery record."],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    print("drift record filed:", ok)
    new_state = {**state,
                 "last_drift_recorded": today,
                 "ever_green": False,
                 "output": out[-2000:]}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(new_state))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
