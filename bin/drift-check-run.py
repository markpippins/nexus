#!/usr/bin/env python3
"""drift-check-run — timer wrapper for terrain-drift-check (R1 4f4f2574).

One job: normalize the census tool's exit codes into unit-safe semantics so
the daily timer is a reliable surface. Mirrors the lease-probe wrapper
convention (calendar-consolidate-run.py lineage).

Exit normalization (terrain-drift-check -> this wrapper):
  0    census completed, no defects      -> 0
  1..124  census completed WITH defects  -> 0 (defects are DATA: they are
          printed to the journal from --json and filed as an agent record
          tagged to:sysadmin by the tool itself; a red unit would just mute
          the cadence — the opposite of surfacing drift)
  125  census UNRUNNABLE (terrain unreachable FATAL) -> 1 (real unit
          failure — silence here would be a blind watcher)
  other / crash                        -> 4 (unexpected — loud, journal-visible)

Env passthrough: TERRAIN_URL, NEBULA_URL, REGISTRY_DSN, TERRAIN_PROBE_TIMEOUT.
"""

import json
import subprocess
import sys
import time

TOOL = sys.argv[0].rsplit("/", 1)[0] + "/terrain-drift-check.py" \
    if "/" in sys.argv[0] else "terrain-drift-check.py"


def classify(rc):
    """Census exit code -> wrapper exit code (see module docstring)."""
    if rc == 0:
        return 0
    if 1 <= rc <= 124:
        return 0          # completed with findings — data, not unit failure
    if rc == 125:
        return 1          # FATAL: unrunnable census = blind watcher
    return 4              # anything else is unexpected


def main():
    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, TOOL, "--record", "--json"],
            capture_output=True, text=True, timeout=600)
    except Exception as e:  # noqa: BLE001
        print(f"drift-check-run: census crashed: {e.__class__.__name__}: {e}",
              file=sys.stderr)
        return 4

    dur = time.time() - t0

    # journal-friendly summary line from the JSON payload
    summary = None
    try:
        payload = json.loads(r.stdout.strip().splitlines()[-1])
        defects = payload.get("defects", [])
        summary = (
            f"checked={payload.get('checked')} "
            f"expected_offline={len(payload.get('expected_offline', []))} "
            f"defects={len(defects)} "
            f"classes={[d.get('class') for d in defects]} "
            f"degraded={payload.get('degraded')}")
    except Exception:  # noqa: BLE001 — summary is best-effort
        summary = f"raw_exit={r.returncode} (no JSON summary)"

    print(f"drift-check-run: {summary} ({dur:.1f}s)")
    if r.stderr.strip():
        print(r.stderr.strip(), file=sys.stderr)

    rc = classify(r.returncode)
    print(f"drift-check-run: census exit={r.returncode} -> wrapper exit={rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
