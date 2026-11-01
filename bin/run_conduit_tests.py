#!/usr/bin/env python3
"""Run every test in python/conduit/tests/ that can run without a database.

Why this exists: at origin/main 00a28c5b, **no workflow ran
python/conduit/tests at all**. The Python conduit suite (36 files) was green or
red only on whatever machine happened to run it by hand. The cost of that gap is
concrete and was found while writing this runner: tests/test_c2_validate_receipt.py
had been failing on main, unnoticed, since the guard started matching a comment
instead of the handler (fixed alongside this file). Under R8 ("the code has
tests and the tests are passing") an ungated suite makes the merge condition
author-asserted.

This mirrors bin/run_bin_tests.py deliberately, including the decisions that
file was forced to learn:

  * **Discover, do not enumerate.** The runner walks tests/test_*.py. A file it
    skips must be named in python/conduit/tests-ci-manifest.json with a reason,
    an owner, and the thing tracking the debt. There is no third state, so a new
    test file can never be silently ungated.
  * **Run through pytest, never as a bare script.** A file that pytest collects
    0 tests from is reported EMPTY (a failure) rather than counted as a pass.
  * **Strip PYTHONPATH **and** CONDUIT_PG_DSN.** A CI runner has neither. Tests
    that only pass because the developer has a database and an exported DSN are
    not evidence of anything, so the runner scrubs both: the gate proves the
    hermetic tier is DB-free instead of trusting that it is. (An ambient
    PYTHONPATH is scrubbed and replaced with the repo-local package roots only
    -- `python/` and `python/vision/losm-ir/src` -- so `tackle` and `nexus_core`
    resolve from the same checkout without re-admitting machine state.)

Exit codes:
  0  every discovered test file ran and passed
  1  at least one test file failed
  2  the manifest is itself invalid (missing/stale exclusion, empty suite)

Same command locally and in CI: python3 bin/run_conduit_tests.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
SUITE = REPO / "python" / "conduit"
TESTS = SUITE / "tests"
MANIFEST = SUITE / "tests-ci-manifest.json"
PER_FILE_TIMEOUT = int(os.environ.get("CONDUIT_TEST_TIMEOUT", "300"))

# A hermetic gate must not inherit a database. These are removed from the child
# environment so a test cannot quietly depend on either.
SCRUBBED_ENV = ("PYTHONPATH", "CONDUIT_PG_DSN", "CONDUIT_PG_SCHEMA")

# Repo-local package roots, provided deliberately after the scrub. An ambient
# PYTHONPATH is developer-machine state and is not evidence; these paths are IN
# the repository, and two guards import them as ordinary packages:
#   tests/test_harness_launcher.py  ->  from tackle.harness_launcher import ...
#   tests/test_wrp_kernel.py        ->  from nexus_core.wrp.kernel import ...
# Without them the gate reports collection errors for code that is on the same
# commit, which says nothing about the commit.
LOCAL_PACKAGE_ROOTS = ("python", "python/vision/losm-ir/src")


def discovered() -> list[pathlib.Path]:
    return sorted(TESTS.glob("test_*.py"))


def load_manifest() -> dict:
    if not MANIFEST.is_file():
        sys.exit(f"manifest missing: {MANIFEST}")
    return json.loads(MANIFEST.read_text())


def env_for_child() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        str(REPO / r) for r in LOCAL_PACKAGE_ROOTS if (REPO / r).is_dir())
    return env


def run_one(path: pathlib.Path) -> tuple[int, str, float]:
    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "-q",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=PER_FILE_TIMEOUT,
            cwd=SUITE, env=env_for_child(),
        )
        return r.returncode, (r.stdout + r.stderr), time.time() - t0
    except subprocess.TimeoutExpired:
        return -9, f"TIMEOUT >{PER_FILE_TIMEOUT}s", time.time() - t0


def collected(path: pathlib.Path) -> int:
    """How many tests pytest actually finds. 0 == it would run nothing."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", str(path), "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=PER_FILE_TIMEOUT,
            cwd=SUITE, env=env_for_child(),
        )
    except subprocess.TimeoutExpired:
        return -1
    for line in reversed(r.stdout.splitlines()):
        parts = line.split()
        if parts and parts[0].isdigit() and ("test" in line or "error" in line):
            return int(parts[0])
    return 0


def passed_count(output: str) -> int:
    m = re.search(r"(\d+) passed", output)
    return int(m.group(1)) if m else 0


def main() -> int:
    files = discovered()
    if not files:
        sys.exit("no test files discovered -- the walk is broken, not the suite")

    manifest = load_manifest()
    excluded = manifest.get("excluded", {})

    # A stale exclusion is a lie: it claims a debt that no longer exists and
    # would keep a now-runnable test file out of CI forever.
    stale = [n for n in excluded if not (TESTS / n).is_file()]
    if stale:
        print("MANIFEST INVALID -- exclusions for files that no longer exist:",
              file=sys.stderr)
        for n in stale:
            print(f"  {n}", file=sys.stderr)
        print("  remove these entries; the suite shrank, the debt did not.",
              file=sys.stderr)
        return 2

    documented = [n for n in excluded
                  if not excluded[n].get("reason") or not excluded[n].get("owner")]
    if documented:
        print("MANIFEST INVALID -- exclusions missing reason/owner:", file=sys.stderr)
        for n in documented:
            print(f"  {n}", file=sys.stderr)
        return 2

    run_set = [f for f in files if f.name not in excluded]
    print(f"python/conduit/tests: {len(files)} discovered, {len(run_set)} running, "
          f"{len(excluded)} excluded\n")

    failures: list[tuple[str, int, str]] = []
    total, ran = 0.0, 0
    for f in run_set:
        n = collected(f)
        rc, out, dt = run_one(f)
        total += dt
        ran += max(passed_count(out), 0)
        if rc == 0 and n <= 0:
            print(f"  EMPTY {f.name:52} {dt:6.1f}s  (collected 0 tests)")
            failures.append((f.name, 5,
                             "pytest collected 0 tests. Named test_*.py but "
                             "exposes nothing runnable, so the gate would count "
                             "it as evidence while checking nothing."))
        elif rc == 0:
            print(f"  PASS  {f.name:52} {dt:6.1f}s  ({n} tests)")
        else:
            print(f"  FAIL  {f.name:52} {dt:6.1f}s  (rc={rc}, {n} tests)")
            failures.append((f.name, rc, out))

    if ran == 0:
        # The whole point of the gate: zero tests run is never a pass.
        print("\nNO TESTS RAN -- a gate that checks nothing is worse than no gate",
              file=sys.stderr)
        return 1

    if excluded:
        print(f"\nexcluded ({len(excluded)}) -- each is a tracked debt item:")
        for name, why in sorted(excluded.items()):
            print(f"  - {name:52} {why.get('reason', '<no reason>')}")
            print(f"      kind={why.get('kind', '?')} owner={why.get('owner', '?')}")
            print(f"      tracked={why.get('tracked', '?')}")
            print(f"      clears_when={why.get('clears_when', '?')}")

    print(f"\nran {len(run_set)} files / {ran} tests in {total:.1f}s; "
          f"{len(failures)} failed")
    if failures:
        for name, rc, out in failures:
            tail = [l for l in out.splitlines() if l.strip()][-6:]
            print(f"\n=== {name} (rc={rc}) ===\n" + "\n".join(tail),
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
