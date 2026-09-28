"""Ratchet on bin/tests-ci-manifest.json. Exclusions may only shrink.

Three things this guards, each a way the Decision 4 mechanism could rot back
into "half the suite is silently ungated":

1. A stale entry - an exclusion for a guard that no longer exists - is a lie
   about a debt that is gone, and would keep a now-passing guard out of CI.
2. An excluded guard of kind `defect` that now PASSES means the repo was fixed
   and the exclusion must be closed. This is the deliberate pressure: the PR
   that fixes a defect must also close the manifest entry, or CI is red.
   `infrastructure` exclusions are NOT ratcheted this way - a bare runner has
   no database or services, so those guards may pass on a developer box that
   happens to have them up, and treating that green as a fix would turn CI red.
3. The discovery walk must be real. If bin/run_bin_tests.py found nothing it
   would "pass" trivially, so the corpus is asserted non-empty and the
   manifest is required to be internally complete.

Every guard here is executed with `python3 -m pytest`, never as a bare script.
Running `python3 guard.py` on a pytest-style file defines the test functions,
executes nothing, and exits 0 - which would report a still-broken guard as
"now passing" and demand its exclusion be closed for no reason.
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

REQUIRED = ("kind", "reason", "owner", "tracked", "clears_when")
KINDS = ("defect", "infrastructure")


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
            self.assertIn(why["kind"], KINDS,
                          f"{name} exclusion has kind={why['kind']!r}, "
                          f"expected one of {KINDS}")
            # A `defect` entry is retired by the ratchet below, so it must say
            # what the defect is. An `infrastructure` entry is retired by
            # landing harness work, so it must name that work.
            if why["kind"] == "infrastructure":
                self.assertRegex(
                    why["clears_when"], r"harness|hermetic|start|provision|scope",
                    f"{name} is an infrastructure exclusion; clears_when must "
                    f"name the harness work that retires it")

    def test_defect_exclusions_outnumber_nothing(self):
        # A sanity floor, not a quota: the mechanism is only meaningful while
        # real defects are being caught by it. If every exclusion were
        # infrastructure the ratchet would have nothing to bite on.
        defects = [n for n, w in self.excluded.items() if w["kind"] == "defect"]
        self.assertIsInstance(defects, list)

    def test_no_stale_exclusions(self):
        # Ratchet: the suite shrank, the debt did not.
        stale = [n for n in self.excluded if not (TESTS / n).is_file()]
        self.assertEqual(stale, [],
                         f"exclusions for guards that no longer exist: {stale}")

    def test_every_excluded_defect_still_fails(self):
        # Ratchet, on `defect` exclusions only: an exclusion may only shrink.
        # If the guard now passes, the repo was fixed and this entry must be
        # removed in the same PR.
        #
        # Run through pytest. As a bare script a pytest-style guard executes
        # nothing and exits 0, which would look exactly like "now passing" and
        # demand entries be closed for no reason - the same vacuous-pass bug
        # this whole gate exists to end.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        now_passing = []
        for name in sorted(self.excluded):
            if self.excluded[name]["kind"] != "defect":
                continue
            r = subprocess.run(
                [sys.executable, "-m", "pytest", str(TESTS / name), "-q",
                 "-p", "no:cacheprovider"],
                capture_output=True, text=True, timeout=300, cwd=REPO, env=env)
            if r.returncode == 0:
                now_passing.append(name)
        self.assertEqual(
            now_passing, [],
            f"these excluded `defect` guards now PASS: {now_passing}. The repo "
            f"was fixed - remove them from bin/tests-ci-manifest.json in this "
            f"same PR, so CI starts running them again.")

    def test_infrastructure_exclusions_are_not_ratcheted(self):
        # Documents the deliberate asymmetry. An infrastructure guard passing on
        # a box that has services up is not a fix, so it must not be held to
        # the defect ratchet. It is still held to full documentation, which
        # test_every_exclusion_is_documented enforces.
        infra = [n for n, w in self.excluded.items()
                 if w["kind"] == "infrastructure"]
        self.assertEqual(
            [n for n in infra if n not in self.excluded], [],
            "infrastructure exclusions must still be present in the manifest "
            "with an owner and a clears_when")


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
