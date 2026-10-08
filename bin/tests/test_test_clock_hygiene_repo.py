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

WHY THE FIX AND THE GUARD LAND TOGETHER
-------------------------------------
The rot this guard catches was present on ``main`` when the guard was written:
``bin/tests/test_resolver_soak_report.py`` froze ``NOW`` at 2026-10-02 while
deriving ``OLD``/``RECENT`` from ``datetime.now()``. ``bin/resolver-soak-report.py``
measures the soak window from the oldest line to ``now``, so the measured age
decayed 1:1 with the calendar -- 15d when authored, under the 14d threshold from
2026-10-03. Correct test, red on schedule, no code change.

The fix is commit ``1deed8769``, delivered by PR #715, and it is **already
merged into ``main``**. It gives that file one reference clock, so both sides
derive from it and the rot is gone.

So the rot is fixed and this guard is what keeps it fixed. Because the fix
already landed on ``main``, this enforcement test was never red there and needs
**no** ``bin/tests-ci-manifest.json`` exclusion. The ``kind: "defect"`` route
exists for red-because-repo-is-wrong; here the detector simply arrived after
the repair. The ``test_bin_tests_manifest.py`` ratchet has no entry to expire.

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