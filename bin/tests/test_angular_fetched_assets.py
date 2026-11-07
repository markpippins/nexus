"""Gate the fetched-asset build guard's own suite in `make bintests`.

The guard lives at angular/tools/check-fetched-assets.mjs and its tests are
Node's built-in runner (no dependencies). run_bin_tests.py only discovers
bin/tests/test_*.py, so without this bridge the guard's tests would be ungated
-- the exact "silently not run" failure mode that runner exists to prevent.

Why it matters for the guard, not just for tidiness: the check fails builds on
a *missing* file, so a false positive here breaks every Angular build. Its
matching rules (comments, interpolated URLs, stylesheet-relative URLs,
angular.json outputPath shapes) are the whole product, and a wrong rule is
only visible if the tests run.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
TOOLS = REPO / "angular" / "tools"
SUITE = TOOLS / "check-fetched-assets.test.mjs"
GUARD = TOOLS / "check-fetched-assets.mjs"


class FetchedAssetGuardTests(unittest.TestCase):
    def test_guard_and_suite_are_committed(self):
        self.assertTrue(GUARD.is_file(), f"missing guard: {GUARD}")
        self.assertTrue(SUITE.is_file(), f"missing guard suite: {SUITE}")

    def test_node_suite_passes(self):
        node = shutil.which("node")
        if node is None:
            self.fail("node is required to run the fetched-asset guard suite")
        proc = subprocess.run(
            [node, "--test", str(SUITE)],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, output)
        passes = self._count(output, "pass")
        self.assertGreater(passes, 0, f"suite reported no passing tests:\n{output}")

    def test_angular_cli_builds_run_the_guard(self):
        """The guard is only a build-time check if the build invokes it."""
        angular = REPO / "angular"
        cli_projects = sorted(path.parent for path in angular.glob("*/angular.json"))
        self.assertGreater(len(cli_projects), 0, "no Angular CLI projects found")
        for project in cli_projects:
            package = json.loads((project / "package.json").read_text())
            build = package.get("scripts", {}).get("build", "")
            self.assertIn(
                "check-fetched-assets.mjs",
                build,
                f"{project.relative_to(REPO)} build does not run the fetched-asset guard",
            )

    @staticmethod
    def _count(output: str, label: str) -> int:
        for line in output.splitlines():
            parts = line.replace("\u2139", "").split()
            if len(parts) == 2 and parts[0] == label and parts[1].isdigit():
                return int(parts[1])
        return 0


if __name__ == "__main__":
    unittest.main()
