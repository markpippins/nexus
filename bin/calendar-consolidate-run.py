#!/usr/bin/env python3
"""calendar-consolidate-run — dormant-wiring wrapper for the fold-back.

Called by config/systemd/calendar-consolidate.service (timer 13:35Z) and by
the boot shim's consolidation step. Its one job: normalize the observe tool's
exit codes into unit-safe semantics so the timer is SILENTLY QUIESCENT while
V184 is absent and self-activating the moment it applies.

Exit normalization (observe -> this wrapper):
  0  consolidated      -> 0  (journal: consolidated N inserted / M skipped)
  3  inert (V184 off)  -> 0  (journal: [inert] marker — a skip, NOT a failure)
  1  unreachable store -> 1  (genuine error; the unit SHOULD show failed)
  4  strict-invalid    -> 1  (data problem; operator-visible, not silent)
  other                -> propagate (unexpected; investigate)

The inert case is the whole design: while the gate is closed the timer logs
one quiet line per tick and exits 0; when V184 applies, the next tick (or
next boot, via the shim step) folds the accumulated calendar with no
operator ceremony. The gate IS the activation.

Everything else (parsing, validation, dedupe, provenance) is
calendar-consolidate.py's contract — this wrapper adds nothing but the
exit-code mapping and journal legibility.
"""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OBSERVE = os.path.join(SCRIPT_DIR, "calendar-consolidate.py")

INERT_EXIT = 3


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = [sys.executable, OBSERVE, "observe"]
    if "--source" not in argv:
        cmd += ["--source", os.environ.get(
            "CALENDAR_STATE_DIR",
            os.path.expanduser("~/.local/state/nexus-calendar")) + "/calendar.jsonl"]
    cmd += [a for a in argv if a != "--quiet"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        print("[failed] consolidation timed out after 120s", file=sys.stderr)
        return 1
    tail = (r.stdout.strip().splitlines() or [""])[-1][:200]
    err = (r.stderr.strip().splitlines() or [""])[-1][:200]

    if r.returncode == 0:
        print(f"[consolidated] {tail}")
        return 0
    if r.returncode == INERT_EXIT:
        # The designed quiescent path: marker + exit 0. Never noise.
        print(f"[inert] {tail}")
        return 0
    if r.returncode == 4:
        print(f"[failed] strict-invalid lines: {tail} {err}", file=sys.stderr)
        return 1
    print(f"[failed] observe exit {r.returncode}: {tail} {err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
