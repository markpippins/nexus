#!/usr/bin/env python3
"""Thin wrapper: run the users-plaintext check and file a drift record.

Used by users-bcrypt-drift.service (daily user timer). Same contract as
sdk_drift_stamp_wrap.py:

  - record-only-on-drift semantics (checker exit 1 = drift found, which is
    the timer doing its job — filed as an inspection record, exit 0 so the
    systemd unit stays green; anything else re-exits as a tool error)
  - suppression window: one drift record per unique day (state file), so a
    multi-day unaddressed drift still re-alerts daily without spamming

Exit codes: 0 = ok or drift-recorded, 2 = tool/environment error.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHECK = REPO / "bin" / "check_users_plaintext.py"
STATE = Path("/home/codex/.cache/users-bcrypt-drift/state.json")


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
        # Tool error — surface it but do not file a drift record.
        print(err, file=sys.stderr)
        return 2

    if res.returncode == 0:
        return 0

    # Drift found — file the record (this is the timer doing its job).
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    if state.get("last_drift_recorded") == today:
        print(f"drift already recorded today ({today}) — suppressing duplicate record")
        return 0

    record = subprocess.run(
        [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
         "-r", "DBA",
         "-t", f"Users bcrypt drift detected (daily check, {today}): plaintext or missing enforcement on a users surface",
         "--record-type", "inspection",
         "--level", "1",
         "--tags", '["type:inspection","bcrypt-enforcement","users-drift","db-a"]',
         "-c",
         "The daily users-plaintext check (users-bcrypt-drift.timer) found "
         "drift in the V191/V201 bcrypt enforcement path: plaintext rows on an "
         "enforced surface, a missing users_password_bcrypt_check CHECK, or a "
         "missing trg_bcrypt_write_guard trigger. Checker output:\n\n"
         + (out or "(no output)")
         + "\n\nRemediation: re-apply sql/V201__bcrypt_write_guard.sql "
         "(idempotent — backfill no-op unless plaintext exists, CHECKs and "
         "triggers re-created), then disposition why the guard vanished "
         "(out-of-band restore / manual drop are the known classes). This "
         "record repeats daily until the drift is dispositioned."],
        capture_output=True, text=True, timeout=60,
    )
    ok = record.returncode == 0
    print("drift record filed:", ok)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"last_drift_recorded": today, "output": out[-2000:]}))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
