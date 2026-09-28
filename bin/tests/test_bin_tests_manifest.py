"""Ratchet on bin/tests-ci-manifest.json. Exclusions may only shrink.

Three things this guards, each a way the Decision 4 mechanism could rot back
into "half the suite is silently ungated":

1. A stale entry - an exclusion for a guard that no longer exists - is a lie
   about a debt that is gone, and would keep a now-passing guard out of CI.
2. An excluded guard that now PASSES means its cause is fixed and the
   exclusion must be closed. This is the deliberate pressure: the PR that fixes
   a defect must also close the manifest entry, or CI is red.
3. The discovery walk must be real. If bin/run_bin_tests.py found nothing it
   would "pass" trivially, so the corpus is asserted non-empty and the
   manifest is required to be internally complete.
"""
import json
import os
import pathlib
import subprocess
import sys
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
TESTS = REPO / "bin" / "tests"
MANIFEST = REPO / "bin" / "tests-ci-manifest.json"
RUNNER = REPO / "bin" / "run_bin_tests.py"

REQUIRED = ("reason", "owner", "tracked", "clears_when")


class TestManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.excluded = cls.manifest["excluded"]

    def test_manifest_exists_and_declares_policy(self):
        self.assertIn("_policy", self.manifest)
        self.assertTrue(self.manifest["_policy"].strip())

    def test_every_exclusion_is_documented(self):
        for name, why in self.excluded.items():
            for field in REQUIRED:
                self.assertIn(field, why, f"{name} exclusion missing '{field}'")
                self.assertTrue(str(why[field]).strip(),
                                f"{name} exclusion has an empty '{field}'")

    def test_no_stale_exclusions(self):
        # Ratchet: the suite shrank, the debt did not.
        stale = [n for n in self.excluded if not (TESTS / n).is_file()]
        self.assertEqual(stale, [],
                         f"exclusions for guards that no longer exist: {stale}")

    def test_every_excluded_guard_still_fails(self):
        # Ratchet: an exclusion may only shrink. If the guard now passes, its
        # cause is fixed and this entry must be removed in the same PR.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        now_passing = []
        for name in sorted(self.excluded):
            r = subprocess.run([sys.executable, str(TESTS / name)],
                               capture_output=True, text=True, timeout=180,
                               cwd=REPO, env=env)
            if r.returncode == 0:
                now_passing.append(name)
        self.assertEqual(
            now_passing, [],
            f"these excluded guards now PASS: {now_passing}. Their cause is "
            f"fixed - remove them from bin/tests-ci-manifest.json in this same "
            f"PR, so CI starts running them again.")

    def test_discovery_walk_is_non_trivial(self):
        guards = sorted(TESTS.glob("test_*.py"))
        self.assertGreaterEqual(len(guards), 70,
                                "corpus walk is broken or the suite shrank sharply")
        # The runner must exist and be the discovery mechanism, not a stub.
        self.assertTrue(RUNNER.is_file())
        src = RUNNER.read_text()
        self.assertIn('glob("test_*.py")', src,
                      "runner must discover guards by glob, not an enumeration")


if __name__ == "__main__":
    unittest.main(verbosity=2)
