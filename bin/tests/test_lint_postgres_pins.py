"""Tests for bin/lint_postgres_pins.py — the fleet-standard PG pin guard.

Behavioral suite: each test builds a throwaway workflows directory, runs the
lint as a subprocess, and asserts exit codes and output. Fixtures encode the
real-world edges the lint must handle:

  - the R2 drift class (postgres:16 pins) fails with actionable file:line
  - the fleet standard (17, incl. -alpine tags) passes
  - pgvector pins are covered (pgvector/pgvector:pgNN)
  - whole-line comments are ignored (the wr-conf-025.yml:35 case)
  - '# pg-pin-allow:' is the documented escape (counts, doesn't fail)
  - unversioned/latest postgres images warn but do not fail
  - PG_PIN_MIN_MAJOR env can raise the bar
  - the real .github/workflows tree of THIS repo scans without crashing
    (violation count depends on merge state of #470 and is not asserted)

Run:
  python3 -m pytest bin/tests/test_lint_postgres_pins.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LINT = os.path.join(REPO_ROOT, "bin", "lint_postgres_pins.py")


class LintBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pglint-")
        self.wf = os.path.join(self.tmp, "workflows")
        os.makedirs(self.wf)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, body: str) -> str:
        path = os.path.join(self.wf, name)
        with open(path, "w") as fh:
            fh.write(body)
        return path

    def _run(self, target: str | None = None, env_extra: dict | None = None):
        env = {**os.environ, **(env_extra or {})}
        argv = ["python3", LINT] + ([target] if target else [])
        return subprocess.run(argv, capture_output=True, text=True, env=env)

    # -- core policy --------------------------------------------------------

    def test_stale_pin_fails_with_actionable_line(self):
        self._write("stale.yml", "jobs:\n  a:\n    image: postgres:16\n")
        proc = self._run(self.wf)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        # annotation channel (stdout) + plain path:line channel (stderr)
        self.assertIn("file=stale.yml,line=3", proc.stdout)
        self.assertIn("stale.yml:3", proc.stderr)
        self.assertIn("major 16 < fleet standard 17", proc.stderr)

    def test_fleet_standard_pins_pass(self):
        self._write(
            "modern.yml",
            "image: postgres:17\n"
            "other: postgres:17-alpine\n",
        )
        proc = self._run(self.wf)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("0 violation(s)", proc.stdout)

    def test_pgvector_pins_are_scanned(self):
        self._write("vec.yml", "image: pgvector/pgvector:pg15\n")
        proc = self._run(self.wf)
        self.assertEqual(1, proc.returncode)
        self.assertIn("file=vec.yml,line=1", proc.stdout)
        self.assertIn("major 15", proc.stderr)

    def test_comment_line_is_ignored(self):
        # the exact wr-conf-025.yml shape: historical note mentioning :16
        self._write(
            "history.yml",
            "#      postgres:16 — historical: PR #300 failed exactly here\n"
            "image: postgres:17\n",
        )
        proc = self._run(self.wf)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_allow_marker_escapes_and_counts(self):
        self._write(
            "waived.yml",
            "image: postgres:14 # pg-pin-allow: sonar tooling DB, no nexus coupling\n",
        )
        proc = self._run(self.wf)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("1 allow-marker line(s)", proc.stdout)

    # -- warnings (not failures) ---------------------------------------------

    def test_unversioned_postgres_warns_but_passes(self):
        self._write("loose.yml", "image: postgres\n  other: image: postgres:latest\n")
        proc = self._run(self.wf)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        # BOTH loose forms warned: bare `image: postgres` and `postgres:latest`
        self.assertEqual(2, proc.stderr.count("unversioned/latest postgres image"))

    def test_min_major_env_raises_the_bar(self):
        self._write("pg16.yml", "image: postgres:16\n")
        proc = self._run(self.wf, {"PG_PIN_MIN_MAJOR": "18"})
        self.assertEqual(1, proc.returncode)
        self.assertIn("fleet standard 18", proc.stdout)
        proc = self._run(self.wf, {"PG_PIN_MIN_MAJOR": "16"})
        self.assertEqual(0, proc.returncode)

    # -- robustness ------------------------------------------------------------

    def test_missing_target_is_usage_error(self):
        proc = self._run(os.path.join(self.tmp, "does-not-exist"))
        self.assertEqual(2, proc.returncode)

    def test_bad_min_major_is_usage_error(self):
        proc = self._run(self.wf, {"PG_PIN_MIN_MAJOR": "seventeen"})
        self.assertEqual(2, proc.returncode)

    def test_non_workflow_files_ignored(self):
        os.makedirs(os.path.join(self.tmp, "workflows", "sub"), exist_ok=True)
        self._write("notes.txt", "image: postgres:12\n")
        with open(os.path.join(self.wf, "sub", "deep.yml"), "w") as fh:
            fh.write("image: postgres:13\n")
        proc = self._run(self.wf)
        # .txt ignored; nested .yaml still scanned
        self.assertEqual(1, proc.returncode)
        self.assertIn("file=sub/deep.yml,line=1", proc.stdout)
        self.assertNotIn("notes.txt", proc.stdout + proc.stderr)

    def test_real_repo_workflows_scan_does_not_crash(self):
        proc = self._run(os.path.join(REPO_ROOT, ".github", "workflows"))
        self.assertIn(proc.returncode, (0, 1))
        self.assertIn("pg-pin-lint:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
