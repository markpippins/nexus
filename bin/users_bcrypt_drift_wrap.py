#!/usr/bin/env python3
"""Thin wrapper: run the users-plaintext check and file records on the
users-bcrypt-drift series.

Used by users-bcrypt-drift.service (daily user timer). Contract:

  - GREEN runs are recorded on a heartbeat cadence, so the enforcement
    posture is a time series rather than silence:
      * first clean run ever            -> commissioning record
      * first clean run of an ISO week  -> weekly heartbeat record
      * first clean run after drift     -> recovery record
  - DRIFT (checker exit 1) files an inspection record, at most one per
    UTC day (suppression state file) — a multi-day unaddressed drift
    still re-alerts daily without spamming.
  - Tool errors (checker exit 2) file nothing and exit 2 so they are
    visible in the journal.

All records carry the series:users-bcrypt-drift tag for time-series
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
CHECK = REPO / "bin" / "check_users_plaintext.py"
STATE = Path(os.environ.get(
    "USERS_BCRYPT_DRIFT_STATE",
    "/home/codex/.cache/users-bcrypt-drift/state.json"))

GREEN_TAGS = ('["type:report","bcrypt-enforcement","users-drift","db-a",'
              '"series:users-bcrypt-drift"]')
DRIFT_TAGS = ('["type:inspection","bcrypt-enforcement","users-drift","db-a",'
              '"series:users-bcrypt-drift"]')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_week(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def decide_green_action(state: dict, week: str) -> tuple[str | None, dict]:
    """Pure decision: given the state dict and the current ISO-week string,
    return (action, new_state). action is one of 'commissioning',
    'heartbeat', 'recovery', or None (nothing to file this run).
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


def _file_record(kind: str, when: str, out: str, extra: str = "") -> bool:
    titles = {
        "commissioning": f"Users bcrypt enforcement GREEN (commissioning run {when}) — 0 plaintext, guards present",
        "heartbeat": f"Users bcrypt enforcement GREEN (weekly heartbeat {when})",
        "recovery": f"Users bcrypt enforcement RECOVERED (green after drift {when})",
    }
    intros = {
        "commissioning": "First run of users-bcrypt-drift.timer after deployment: "
                         "the V191/V202 bcrypt enforcement path verified clean. "
                         "Green-heartbeat contract: commissioning record on first run, "
                         "weekly heartbeat thereafter, daily inspection records while red.",
        "heartbeat": "Weekly green heartbeat from users-bcrypt-drift.timer: "
                     "enforced surfaces clean, guards present.",
        "recovery": "The previously-reported bcrypt enforcement drift is resolved: "
                    "this is the first green run after the drift record. "
                    "Weekly heartbeat cadence resumes.",
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
         "-t", f"Users bcrypt drift detected (daily check, {today}): plaintext or missing enforcement on a users surface",
         "--record-type", "inspection",
         "--level", "1",
         "--tags", DRIFT_TAGS,
         "-c",
         "The daily users-plaintext check (users-bcrypt-drift.timer) found "
         "drift in the V191/V202 bcrypt enforcement path: plaintext rows on an "
         "enforced surface, a missing users_password_bcrypt_check CHECK, or a "
         "missing trg_bcrypt_write_guard trigger. Checker output:\n\n"
         + (out or "(no output)")
         + "\n\nRemediation: re-apply sql/V202__bcrypt_write_guard.sql "
         "(idempotent — backfill no-op unless plaintext exists, CHECKs and "
         "triggers re-created), then disposition why the guard vanished "
         "(out-of-band restore / manual drop are the known classes). This "
         "record repeats daily until the drift is dispositioned. The next "
         "green run files a recovery record."],
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
