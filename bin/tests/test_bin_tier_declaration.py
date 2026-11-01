#!/usr/bin/env python3
"""Guard: CI's `--tier` invocations, the manifest's tiers, and the runner agree.

Why this exists
---------------
df6eff7db added the `guards-db` tier: `bin/run_bin_tests.py --tier`, plus a
`tiers.db` membership list in `bin/tests-ci-manifest.json`. Only the WORKFLOW
half survived onto main. The runner lost its `argparse` block (so `--tier db`
was an unparsed leftover argv entry that `main()` simply never read), and the
manifest lost `tiers` entirely.

The failure mode that produced is the exact class this repo keeps paying for:
`guards-db` seeded a throwaway PostgreSQL and a role-row vintage seed, then ran
`--tier db`, which silently ran the whole DEFAULT set instead. The two guards
the seeding existed to unlock stayed in `excluded` and never executed, and CI
reported a green job whose entire setup was decorative.

Nothing caught it because no check tied the three surfaces together. Each was
locally consistent:
  * the workflow names `--tier db` -- syntax is fine,
  * the runner ignores argv -- exit 0,
  * the manifest has no tiers -- nothing declares one is missing.

So this guard asserts the JOIN, in the three directions that can drift:

1. **Workflow -> manifest.** Every `--tier <name>` in `.github/workflows/*.yml`,
   the Makefile, or `bin/**/*.sh` must be declared as a LIST in the manifest.
   A named-but-undeclared tier is a job that runs the wrong suite.
2. **Manifest -> filesystem.** Every tier member must be a real guard file and
   must NOT also sit in `excluded` (tiered means it runs; excluded means it
   does not -- both at once is a contradiction, and `excluded` would win).
3. **Manifest -> runner.** The runner must actually accept `--tier`, and must
   fail LOUD (exit 2) on an unknown tier rather than ignoring it. That second
   half is what makes direction 1 enforceable at runtime: without it, a typo in
   a workflow file degrades to "runs the wrong suite", exactly the bug here.

Cheap on purpose: it reads files and runs the runner's `--help` / bad-tier path
only. No guards are executed, so it stays a fast gate.
"""
import json
import pathlib
import re
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO / "bin" / "tests-ci-manifest.json"
RUNNER = REPO / "bin" / "run_bin_tests.py"
TESTS = REPO / "bin" / "tests"

# `--tier db`, `--tier=db`. Deliberately not matching shell variable expansions:
# a workflow that computes the tier dynamically cannot be checked statically,
# and pretending otherwise would give a false green.
TIER_CALL = re.compile(r"--tier[ =]\s*([A-Za-z0-9_.-]+)")


def _tier_callers():
    """(path, tier-name) for every static `--tier` use outside the runner/tests."""
    callers = []
    globs = [
        REPO.glob(".github/workflows/*.yml"),
        REPO.glob(".github/workflows/*.yaml"),
        [REPO / "Makefile"],
        REPO.glob("bin/*.sh"),
    ]
    for group in globs:
        for path in group:
            if not path.is_file():
                continue
            for m in TIER_CALL.finditer(path.read_text(errors="ignore")):
                callers.append((path.relative_to(REPO), m.group(1)))
    return callers


class TestTierDeclaration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.tiers = cls.manifest.get("tiers", {})
        cls.excluded = cls.manifest.get("excluded", {})

    def test_every_ci_tier_is_declared(self):
        callers = _tier_callers()
        # If the corpus walk breaks, this guard would pass vacuously while CI
        # kept invoking tiers. The workflow that uses a tier is the subject, so
        # its absence is a failure, not a skip.
        self.assertTrue(
            callers,
            "no `--tier` invocation found in .github/workflows, the Makefile, or "
            "bin/*.sh. Either the DB-tier job was removed (then delete this guard) "
            "or the scan is broken -- both need a human, neither is a pass.")

        undeclared = sorted(
            {f"{path}: --tier {name}" for path, name in callers
             if name not in self.tiers})
        self.assertEqual(
            undeclared, [],
            "these workflows/Makefile targets run a tier the manifest does not "
            "declare as a list in `tiers`. The job will not fail -- the runner "
            "would have run the DEFAULT suite and reported green:\n  "
            + "\n  ".join(undeclared))

    def test_declared_tiers_are_non_empty_lists(self):
        for name, members in self.tiers.items():
            if name.startswith("_"):
                # `_why` is prose. It is a STRING, and iterating a string yields
                # its characters -- df6eff7db's own bug registered 42
                # one-character tier members that way.
                self.assertIsInstance(members, str, f"{name} is a prose key")
                continue
            self.assertIsInstance(members, list, f"tier {name!r} must be a list")
            self.assertTrue(
                members,
                f"tier {name!r} is empty. An empty tier is a silently vacuous CI "
                f"job: it starts a database, runs nothing, and passes.")

    def test_tier_members_exist_and_are_not_also_excluded(self):
        for name, members in self.tiers.items():
            if name.startswith("_"):
                continue
            for member in members:
                self.assertTrue(
                    (TESTS / member).is_file(),
                    f"tier {name!r} lists {member}, which is not a guard in "
                    f"bin/tests/ -- a stale membership keeps a tier non-empty "
                    f"while running nothing.")
                self.assertNotIn(
                    member,
                    self.excluded,
                    f"{member} is both tiered ({name!r}) and excluded. Tiered "
                    f"means it runs in the job that provides its dependency; "
                    f"excluded means it runs nowhere. `excluded` wins, so this "
                    f"combination silently disables the guard.")

    def test_default_tier_excludes_tiered_guards(self):
        # The runner's default selection must skip tiered guards, otherwise a DB
        # guard also runs in the no-DB job and fails closed for the wrong reason.
        # Asserted on the source, because running the 100+ guard suite to prove
        # a selection rule would make this gate as slow as the gate it checks.
        src = RUNNER.read_text()
        self.assertIn("--tier", src,
                      "bin/run_bin_tests.py no longer accepts --tier; every "
                      "workflow invocation of it would silently run the default "
                      "suite again.")
        self.assertIn("tiered", src,
                      "the runner no longer computes a `tiered` set, so the "
                      "default run does not exclude tiered guards.")

    def test_runner_fails_loud_on_unknown_tier(self):
        # The runtime half of direction 1: a workflow typo must not degrade to
        # "ran the default suite and passed".
        r = subprocess.run(
            [sys.executable, str(RUNNER), "--tier", "__no_such_tier__"],
            capture_output=True, text=True, timeout=120, cwd=REPO)
        self.assertEqual(
            r.returncode, 2,
            f"unknown --tier must exit 2 (manifest-invalid), got rc={r.returncode}. "
            f"stdout={r.stdout[-400:]} stderr={r.stderr[-400:]}")
        self.assertIn("unknown tier", r.stderr.lower(),
                      f"unknown tier must say so, not fail obscurely: {r.stderr[-400:]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
