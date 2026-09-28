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

Guards are executed with `python3 -m pytest`, never as bare scripts, and a
guard pytest collects 0 tests from is reported EMPTY (a failure). A guard that
runs zero tests must never be counted as a pass -- see Decision 4.
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
        # Run through pytest, NOT as a bare script. A pytest-style guard has no
        # __main__ block, so `python3 guard.py` defines the test functions,
        # executes nothing, and exits 0 -- a vacuous pass. That is how 25 of 79
        # guards were reported PASS while running zero tests, and it is the
        # failure mode Decision 4 names: a check that does not check the thing
        # is worse than no check, because it is counted as evidence.
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(guard), "-q",
             "-p", "no:cacheprovider", "-x"],
            capture_output=True, text=True, timeout=PER_GUARD_TIMEOUT,
            cwd=REPO, env=env,
        )
        return r.returncode, (r.stdout + r.stderr), time.time() - t0
    except subprocess.TimeoutExpired:
        return -9, f"TIMEOUT >{PER_GUARD_TIMEOUT}s", time.time() - t0


def collected(guard: pathlib.Path) -> int:
    """How many tests pytest actually finds in a guard. 0 == it would run nothing."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(guard), "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=PER_GUARD_TIMEOUT,
            cwd=REPO, env=env,
        )
    except subprocess.TimeoutExpired:
        return -1
    for line in reversed(r.stdout.splitlines()):
        parts = line.split()
        if parts and parts[0].isdigit() and ("test" in line or "error" in line):
            return int(parts[0])
    return 0


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

    failures, total, ran = [], 0.0, 0
    for g in run_set:
        n = collected(g)
        rc, out, dt = run_one(g)
        total += dt
        ran += max(n, 0)
        if rc == 0 and n <= 0:
            # pytest exit 5 means "collected nothing". Without this branch a
            # guard that pytest cannot see would be reported PASS forever.
            print(f"  EMPTY {g.name:52} {dt:6.1f}s  (collected 0 tests)")
            failures.append((g.name, 5,
                             "pytest collected 0 tests from this file. It is "
                             "named test_*.py but exposes nothing runnable, so "
                             "the gate would be counting it as evidence while "
                             "checking nothing. Either it is a real guard that "
                             "needs a __main__/import fix, or it does not "
                             "belong in bin/tests/."))
        elif rc == 0:
            print(f"  PASS  {g.name:52} {dt:6.1f}s  ({n} tests)")
        else:
            print(f"  FAIL  {g.name:52} {dt:6.1f}s  (rc={rc}, {n} tests)")
            failures.append((g.name, rc, out))

    if excluded:
        print(f"\nexcluded ({len(excluded)}) — each is a tracked debt item:")
        for name, why in sorted(excluded.items()):
            print(f"  - {name:52} {why.get('reason', '<no reason>')}")
            print(f"      owner={why.get('owner', '?')} tracked={why.get('tracked', '?')}")

    print(f"\nran {len(run_set)} guards / {ran} tests in {total:.1f}s; "
          f"{len(failures)} failed")
    if failures:
        for name, rc, out in failures:
            tail = [l for l in out.splitlines() if l.strip()][-6:]
            print(f"\n=== {name} (rc={rc}) ===\n" + "\n".join(tail), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
