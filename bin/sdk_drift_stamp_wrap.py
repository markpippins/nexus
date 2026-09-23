#!/usr/bin/env python3
"""Thin wrapper: run the SDK stamp check and file a drift record on failure.

Used by sdk-drift-stamp.service (daily user timer). The stamp check itself
lives in bin/check_sdk_drift.py; this wrapper adds:

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
CHECK = REPO / "bin" / "check_sdk_drift.py"
STATE = Path("/home/codex/.cache/sdk-drift-stamp/state.json")
STAMP_MODE_ARGS = ["--mode", "stamp"]


def main() -> int:
    res = subprocess.run(
        [sys.executable, str(CHECK), *STAMP_MODE_ARGS],
        capture_output=True, text=True, timeout=300,
        cwd=str(REPO),
    )
    out = (res.stdout or "").strip()
    if out:
        print(out)

    if res.returncode == 0:
        return 0

    if res.returncode == 1:
        # Drift found — file the record (this is the timer doing its job).
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            state = {}
        already = state.get("last_drift_recorded") == today
        if not already:
            try:
                from nexus.bin.post_agent_record import build_payload  # not importable; fallback below
            except Exception:
                pass
            record = subprocess.run(
                [sys.executable, str(REPO / "bin" / "post-agent-record.py"),
                 "-r", "engineer",
                 "-t", f"SDK stamp drift detected (daily check, {today}): TypeSpec changed without regeneration",
                 "--record-type", "inspection",
                 "--level", "1",
                 "--tags", '["type:inspection","debris-triage","sdk-drift"]',
                 "-c",
                 "The daily stamp-mode SDK drift check (sdk-drift-stamp.timer) found "
                 "TypeSpec contract changes that have not been regenerated into the "
                 "generated clients. Checker output:\n\n" + (out or "(no output)") +
                 "\n\nRun `make sdk-drift-check` with the tsp toolchain (or "
                 "`bin/check_sdk_drift.py --mode regen`) for the byte-identity diff, "
                 "regenerate + commit, then `make sdk-drift-update-stamp` only after "
                 "verifying. This record repeats daily until the drift is dispositioned."],
                capture_output=True, text=True, timeout=60,
            )
            ok = record.returncode == 0
            print("drift record filed:" , ok)
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps({"last_drift_recorded": today, "output": out[-2000:]}))
        else:
            print(f"drift already recorded today ({today}) — suppressing duplicate record")
        return 0

    print(f"tool error (exit {res.returncode}): {(res.stderr or '')[-400:]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
