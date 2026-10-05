"""Repo-wide enforcement for the test clock-hygiene guard.

This is the second half of the pair:

  * ``test_test_clock_hygiene.py`` — hermetic unit tests for the detector,
    with embedded fixtures. Always green, never goes stale.
  * ``this file`` — runs the detector across the real suite and FAILS on any
    mixed-clock rot signature.

They are split deliberately. Enforcement must be able to be excluded while the
unit tests still run; excluding a whole guard to buy headroom on one assertion
would take the unit tests down with it, and then nothing would be testing the
detector at all.

CURRENTLY RED, AND THAT IS CORRECT
----------------------------------
At the time of writing this guard, ``bin/tests/test_resolver_soak_report.py`` on
``main`` carries the mixed-clock signature, so this guard fails. That is the
guard working, not the guard being broken -- main genuinely contains the rot
that reddened #715's ``mesh-register probe tests`` on 2026-10-03.

The fix is PR #715 commit ``1deed8769``. It is entered in
``bin/tests-ci-manifest.json`` as a ``kind: "defect"`` exclusion, which is the
sanctioned mechanism for exactly this: red because the repo is wrong, not
because the guard is. ``test_bin_tests_manifest.py`` ratchets defect-kind
exclusions and FAILS if one starts passing, so the entry cannot outlive its
cause -- the moment #715 lands and this guard goes green, the ratchet demands
the exclusion be deleted in the same PR.

No ``unittest.SkipTest``, no ``pytest.skip``: a skip would hide the one case the
guard exists for.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
spec = importlib.util.spec_from_file_location(
    "check_test_clock_hygiene", REPO / "bin" / "check_test_clock_hygiene.py")
chk = importlib.util.module_from_spec(spec)
sys.modules["check_test_clock_hygiene"] = chk
spec.loader.exec_module(chk)


class TestRepoHasNoClockRot(unittest.TestCase):
    def test_no_mixed_clock_rot_in_the_suite(self):
        findings = chk.scan_repo(REPO)
        if findings:
            listed = "\n".join(
                f"  {f.path}:{f.line}  baseline={f.baseline!r}  {f.detail}"
                for f in findings)
            self.fail(
                f"{len(findings)} mixed-clock fixture rot signature(s) in the "
                f"test suite.\n\n{listed}\n\n"
                "A frozen baseline compared against a live clock makes the "
                "measured age drift with the calendar, so a correct test fails "
                "on schedule with no code change. Derive both sides from one "
                "reference clock.\n"
                "See bin/check_test_clock_hygiene.py for why this is not the "
                "same as banning hardcoded dates."
            )


if __name__ == "__main__":
    unittest.main()