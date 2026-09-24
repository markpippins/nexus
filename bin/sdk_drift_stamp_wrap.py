#!/usr/bin/env python3
"""Thin wrapper: run the SDK stamp check and file records on the
sdk-drift-stamp series.

Used by sdk-drift-stamp.service (daily user timer). The stamp check itself
lives in bin/check_sdk_drift.py (CI runs the authoritative regen-diff via
sdk-drift-guard.yml). Contract — identical to users-bcrypt-drift-wrap.py
(green-heartbeat contract, PR #527), so both timer series file records
consistently:

  - GREEN runs are recorded on a heartbeat cadence, so the SDK stamp
    posture is a time series rather than silence:
      * first clean run ever            -> commissioning record
      * first clean run of an ISO week  -> weekly heartbeat record
      * first clean run after drift     -> recovery record
  - DRIFT (checker exit 1) files an inspection record, at most one per
    UTC day (suppression state file) — a multi-day unaddressed drift
    still re-alerts daily without spamming.
  - Tool errors (checker exit other than 0/1) file nothing and exit 2
    so they are visible in the journal.

All records carry the series:sdk-drift-stamp tag for time-series queries.
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
CHECK = REPO / "bin" / "check_sdk_drift.py"
STATE = Path(os.environ.get(
    "SDK_DRIFT_STAMP_STATE",
    "/home/codex/.cache/sdk-drift-stamp/state.json"))
STAMP_MODE_ARGS = ["--mode", "stamp"]

GREEN_TAGS = ('["type:report","sdk-drift","db-a",'
              '"series:sdk-drift-stamp"]')
DRIFT_TAGS = ('["type:inspection","sdk-drift","db-a",'
              '"series:sdk-drift-stamp"]')


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_week(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def decide_green_action(state: dict, week: str) -> tuple[str | None, dict]:
    """Pure decision: given the state dict and the current ISO-week string,
    return (action, new_state). action is one of 'commissioning',
    'heartbeat', 'recovery', or None (nothing to file this run).

    Shared contract with users_bcrypt_drift_wrap.decide_green_action —
    keep the two in lockstep.
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
        "commissioning": f"SDK stamp posture GREEN (commissioning run {when}) — generated clients match committed TypeSpec",
        "heartbeat": f"SDK stamp posture GREEN (weekly heartbeat {when})",
        "recovery": f"SDK stamp posture RECOVERED (green after drift {when})",
    }
    intros = {
        "commissioning": "First run of sdk-drift-stamp.timer after deployment: "
                         "stamp-mode SDK drift check verified clean (generated "
                         "client trees byte-identical to their committed stamps). "
                         "Green-heartbeat contract: commissioning record on first "
                         "run, weekly heartbeat thereafter, daily inspection "
                         "records while red.",
        "heartbeat": "Weekly green heartbeat from sdk-drift-stamp.timer: "
                     "generated clients still match their committed TypeSpec "
                     "stamps.",
        "recovery": "The previously-reported SDK stamp drift is resolved: this "
                    "is the first green run after the drift record. Weekly "
                    "heartbeat cadence resumes.",
    }
    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "engineer",
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
        [sys.executable, str(CHECK), *STAMP_MODE_ARGS],
        capture_output=True, text=True, timeout=300,
        cwd=str(REPO),
    )
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if out:
        print(out)
    if res.returncode not in (0, 1):
        # Tool error — surface it but file nothing.
        print(f"tool error (exit {res.returncode}): {err[-400:]}", file=sys.stderr)
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
         "-r", "engineer",
         "-t", f"SDK stamp drift detected (daily check, {today}): TypeSpec changed without regeneration",
         "--record-type", "inspection",
         "--level", "1",
         "--tags", DRIFT_TAGS,
         "-c",
         "The daily stamp-mode SDK drift check (sdk-drift-stamp.timer) found "
         "TypeSpec contract changes that have not been regenerated into the "
         "generated clients. Checker output:\n\n" + (out or "(no output)")
         + "\n\nRun `make sdk-drift-check` with the tsp toolchain (or "
           "`bin/check_sdk_drift.py --mode regen`) for the byte-identity diff, "
           "regenerate + commit, then `make sdk-drift-update-stamp` only after "
           "verifying. This record repeats daily until the drift is "
           "dispositioned. The next green run files a recovery record."],
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
