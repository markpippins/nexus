#!/usr/bin/env python3
"""Run every guard in bin/tests/ that is not explicitly excluded.

Why this exists: as of 2026-09-28, 38 of the 76 guards in bin/tests/ were
referenced by no workflow and no Makefile target. The Makefile enumerated tests
one per line, so a new guard was ungated by default and silent about it. Under
R8 ("the code has tests and the tests are passing") that made the merge
condition author-asserted for half the suite -- and it hid a real defect:
two migrations both claiming version V194 on origin/main.

The fix is the Tier-1 extraction ruled in 7d6eee39: **discover the guards, do
not enumerate them.** This runner walks bin/tests/test_*.py. Anything it skips
must be named in bin/tests-ci-manifest.json with a reason, an owner, and the
thing tracking the debt. There is no third option -- a guard cannot be silently
ungated, and an exclusion cannot outlive the failure that justified it.

Exit codes:
  0  every discovered guard ran and passed
  1  at least one guard failed
  2  the manifest is itself invalid (stale/undocumented exclusion)
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
TESTS = REPO / "bin" / "tests"
MANIFEST = REPO / "bin" / "tests-ci-manifest.json"
PER_GUARD_TIMEOUT = int(os.environ.get("BIN_TEST_TIMEOUT", "180"))


def discovered() -> list[pathlib.Path]:
    return sorted(TESTS.glob("test_*.py"))


def load_manifest() -> dict:
    if not MANIFEST.is_file():
        sys.exit(f"manifest missing: {MANIFEST}")
    return json.loads(MANIFEST.read_text())


def run_one(guard: pathlib.Path) -> tuple[int, str, float]:
    # A CI runner has no ambient PYTHONPATH. Guards that pass only because
    # something exported one are not passing; scrub it so that is visible.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(guard)], capture_output=True,
                           text=True, timeout=PER_GUARD_TIMEOUT, cwd=REPO, env=env)
        return r.returncode, (r.stdout + r.stderr), time.time() - t0
    except subprocess.TimeoutExpired:
        return -9, f"TIMEOUT >{PER_GUARD_TIMEOUT}s", time.time() - t0


def main() -> int:
    guards = discovered()
    if not guards:
        sys.exit("no guards discovered -- the walk is broken, not the suite")

    manifest = load_manifest()
    excluded = manifest.get("excluded", {})

    # A stale exclusion is a lie: it claims a debt that no longer exists, and it
    # would keep a now-passing guard out of CI forever.
    stale = [n for n in excluded if not (TESTS / n).is_file()]
    if stale:
        print("MANIFEST INVALID — exclusions for guards that no longer exist:",
              file=sys.stderr)
        for n in stale:
            print(f"  {n}", file=sys.stderr)
        print("  remove these entries; the suite shrank, the debt did not.",
              file=sys.stderr)
        return 2

    run_set = [g for g in guards if g.name not in excluded]
    print(f"bin/tests: {len(guards)} discovered, {len(run_set)} running, "
          f"{len(excluded)} excluded\n")

    failures, total = [], 0.0
    for g in run_set:
        rc, out, dt = run_one(g)
        total += dt
        if rc == 0:
            print(f"  PASS  {g.name:52} {dt:6.1f}s")
        else:
            print(f"  FAIL  {g.name:52} {dt:6.1f}s  (rc={rc})")
            failures.append((g.name, rc, out))

    if excluded:
        print(f"\nexcluded ({len(excluded)}) — each is a tracked debt item:")
        for name, why in sorted(excluded.items()):
            print(f"  - {name:52} {why.get('reason', '<no reason>')}")
            print(f"      owner={why.get('owner', '?')} tracked={why.get('tracked', '?')}")

    print(f"\nran {len(run_set)} in {total:.1f}s; {len(failures)} failed")
    if failures:
        for name, rc, out in failures:
            tail = [l for l in out.splitlines() if l.strip()][-6:]
            print(f"\n=== {name} (rc={rc}) ===\n" + "\n".join(tail), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
