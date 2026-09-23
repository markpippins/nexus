"""Tests for bin/wf_lint.py — the shared workflow-lint harness + rules.

Behavioral suite: each test builds a throwaway tree, runs wf_lint as a
subprocess, and asserts exit codes and output channels. Fixtures encode the
real-world findings that motivated the family:

  - postgres-pin: the R2 drift class (#470/#474 history), incl. the legacy
    '# pg-pin-allow' marker and the wr-conf-025 comment-line case
  - dead-base: the #456 docker-gate findings — 4x eclipse-temurin:21-jdk-slim
    (never published) + openjdk:21-jdk-slim (repo discontinued)
  - eol-runtime: node 20 / go 1.22 past EOL; python 3.10 inside the warn
    window; java floor 17. The clock is frozen via WF_LINT_AS_OF so these
    tests never rot.
  - action-ref: floating branch refs and bare uses: fail; @v4 tag pins pass
    (house convention) unless WF_LINT_REQUIRE_SHA=1.

Run:
  python3 -m pytest bin/tests/test_wf_lint.py -v
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LINT = os.path.join(REPO_ROOT, "bin", "wf_lint.py")
FROZEN = {"WF_LINT_AS_OF": "2026-09-01"}


class WfLintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wflint-")
        self.tree = os.path.join(self.tmp, "tree")
        os.makedirs(self.tree)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, body: str) -> str:
        path = os.path.join(self.tree, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(body)
        return path

    def _run(self, target: str | None = None, env_extra: dict | None = None, *flags: str):
        env = {**os.environ, **FROZEN, **(env_extra or {})}
        argv = ["python3", LINT, *flags] + ([target] if target else [])
        return subprocess.run(argv, capture_output=True, text=True, env=env)

    # -- harness ---------------------------------------------------------------

    def test_allow_marker_suppresses_all_rules_and_counts(self):
        self._write(".github/workflows/wf.yml", "image: postgres:14 # wf-lint-allow: sonar tooling DB\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("1 allow-marker line(s)", proc.stdout)

    def test_allow_marker_with_rule_list_is_selective(self):
        self._write(
            ".github/workflows/wf.yml",
            "python-version: \"3.6\" # wf-lint-allow:eol-runtime — pinned for the legacy shim\n"
            "image: postgres:16\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[postgres-pin]", proc.stderr)
        self.assertNotIn("[eol-runtime]", proc.stderr)

    def test_rule_selection_and_unknown_rule(self):
        self._write(".github/workflows/wf.yml", "image: postgres:16\nnode-version: \"20\"\n")
        # dangling --rule is a usage error
        proc = self._run(self.tree, None, "--rule")
        self.assertEqual(2, proc.returncode)
        env = {**os.environ, **FROZEN}
        proc = subprocess.run(
            ["python3", LINT, "--rule", "postgres-pin", self.tree],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(1, proc.returncode)
        self.assertIn("rules: postgres-pin", proc.stdout)
        self.assertNotIn("eol-runtime", proc.stdout)
        proc = subprocess.run(
            ["python3", LINT, "--rule", "no-such-rule", self.tree],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(2, proc.returncode)

    def test_missing_target_is_usage_error(self):
        proc = self._run(os.path.join(self.tmp, "nope"))
        self.assertEqual(2, proc.returncode)

    @unittest.skipIf(os.name != "posix" or os.geteuid() == 0, "needs posix non-root")
    def test_unreadable_file_warns_without_crashing(self):
        locked = self._write(".github/workflows/locked.yml", "image: postgres:16\n")
        os.chmod(locked, 0o000)
        try:
            proc = self._run(self.tree)
        finally:
            os.chmod(locked, 0o644)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)  # warn, not fail
        self.assertIn("unreadable", proc.stderr)

    # -- postgres-pin (ported contract from lint_postgres_pins.py) -------------

    def test_pg_stale_pin_fails_on_both_channels(self):
        self._write(".github/workflows/stale.yml", "jobs:\n  a:\n    image: postgres:16\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("file=.github/workflows/stale.yml,line=3", proc.stdout)
        self.assertIn(".github/workflows/stale.yml:3", proc.stderr)
        self.assertIn("major 16 < fleet standard 17", proc.stderr)

    def test_pg_standard_pins_pass(self):
        self._write(".github/workflows/modern.yml", "image: postgres:17\nother: postgres:17-alpine\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_pg_vector_scanned_and_legacy_allow_honored(self):
        self._write(".github/workflows/vec.yml", "image: pgvector/pgvector:pg15\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self._write(".github/workflows/ok.yml", "image: postgres:16 # pg-pin-allow: legacy suite\n")
        proc = self._run(self.tree)
        self.assertIn("pgvector", proc.stderr)  # vec.yml still fires
        self.assertNotIn("ok.yml", proc.stdout + proc.stderr)

    def test_pg_unversioned_warns_not_fails(self):
        self._write(".github/workflows/loose.yml", "image: postgres\nother: image: postgres:latest\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(2, proc.stderr.count("unversioned/latest postgres image"))

    def test_pg_legacy_env_var_still_works(self):
        self._write(".github/workflows/pg16.yml", "image: postgres:16\n")
        proc = self._run(self.tree, {"PG_PIN_MIN_MAJOR": "18"})
        self.assertEqual(1, proc.returncode)
        self.assertIn("fleet standard 18", proc.stdout)

    def test_pg_comment_lines_ignored(self):
        self._write(
            ".github/workflows/history.yml",
            "#      postgres:16 — historical: PR #300 failed exactly here\nimage: postgres:17\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    # -- dead-base -------------------------------------------------------------

    def test_dead_temurin_slim_fails_valid_temurin_passes(self):
        self._write("svc/Dockerfile", "FROM eclipse-temurin:21-jdk-slim\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("never published -slim tags", proc.stderr)
        # swap the dead pin for a valid one -> clean
        self._write("svc/Dockerfile", "FROM eclipse-temurin:21-jdk\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_dead_openjdk_repo_fails_any_tag(self):
        self._write("a/Dockerfile", "FROM openjdk:21-jdk-slim\n")
        self._write("b/Dockerfile", "FROM openjdk\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("discontinued", proc.stderr)
        self.assertIn("openjdk:<no-tag>", proc.stderr)

    def test_from_flags_and_as_stages_parse(self):
        self._write(
            "c/Dockerfile",
            "FROM --platform=linux/arm64 eclipse-temurin:21-jdk-slim AS builder\n"
            "FROM eclipse-temurin:21-jdk\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("c/Dockerfile:1", proc.stderr)
        self.assertNotIn("c/Dockerfile:2", proc.stderr)

    def test_dockerfile_variants_scanned(self):
        self._write("d/Dockerfile.ci", "FROM openjdk:17\n")
        self._write("e/thing.dockerfile", "FROM openjdk:17\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("d/Dockerfile.ci", proc.stderr)
        self.assertIn("e/thing.dockerfile", proc.stderr)

    def test_non_dockerfile_FROM_not_flagged(self):
        self._write("workflows/x.yml", "runs-on: ubuntu-latest\nFROM nothing in prose: openjdk\n")
        proc = self._run(self.tree)
        self.assertNotIn("[dead-base]", proc.stderr)

    # -- eol-runtime (clock frozen at 2026-09-01) --------------------------------

    def test_eol_node20_fails(self):
        self._write(".github/workflows/n.yml", "node-version: \"20\"\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("node 20 is past EOL (2026-04-30)", proc.stderr)

    def test_eol_go122_fails_and_unassessed_ignored(self):
        self._write(".github/workflows/g.yml", "go-version: \"1.22\"\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self._write(".github/workflows/g2.yml", "go-version: \"1.30\"\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)  # only 1.22 fires
        self.assertNotIn("1.30", proc.stderr)

    def test_eol_python311_passes_and_310_warns(self):
        self._write(
            ".github/workflows/p.yml",
            "python-version: \"3.11\"\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self._write(".github/workflows/p2.yml", "python-version: \"3.10\"\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode)  # warning, not violation (33 days out)
        self.assertIn("WARNING", proc.stderr)
        self.assertIn("schedule the bump", proc.stderr)

    def test_eol_java_floor(self):
        self._write(".github/workflows/j.yml", "java-version: '11'\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("below fleet floor (java 17+)", proc.stderr)
        self._write(".github/workflows/j2.yml", "java-version: '21'\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)  # only java 11 fires
        self.assertNotIn("j2.yml", proc.stdout + proc.stderr)

    def test_eol_comment_lines_ignored(self):
        self._write(".github/workflows/cm.yml", "# node-version: \"16\" — historical note\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    # -- action-ref ---------------------------------------------------------------

    def test_floating_and_bare_refs_fail(self):
        self._write(
            ".github/workflows/a.yml",
            "      - uses: some/action@main\n"
            "      - uses: other/action\n"
            "      - uses: ok/action@v4\n"
            "      - uses: sha/action@11d5960a326750d5838078e36cf38b85af677262\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("floats a branch", proc.stderr)
        self.assertIn("no @ref", proc.stderr)
        self.assertNotIn("ok/action@v4", proc.stderr)
        self.assertNotIn("sha/action@", proc.stderr)

    def test_require_sha_mode(self):
        self._write(".github/workflows/s.yml", "      - uses: actions/checkout@v4\n")
        proc = self._run(self.tree, {"WF_LINT_REQUIRE_SHA": "1"})
        self.assertEqual(1, proc.returncode)
        self.assertIn("not a full 40-hex SHA", proc.stderr)

    # -- real repo -----------------------------------------------------------------

    def test_real_repo_scan_does_not_crash(self):
        proc = self._run(os.path.join(REPO_ROOT, ".github", "workflows"))
        self.assertIn(proc.returncode, (0, 1))
        self.assertIn("wf-lint:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
