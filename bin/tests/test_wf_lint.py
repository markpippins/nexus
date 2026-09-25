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
  - job-hardening: structural (first scan_file rule) — jobs without
    timeout-minutes fail (GitHub default 360 min), workflows without a
    permissions block fail; caller jobs exempt from timeout; nested
    timeout-minutes does not count; allow marker works on the anchor line.
  - fix mode (--fix/--dry-run): rules with a fix_file pass auto-apply
    remediations (job-hardening first). Edits apply descending by line so
    positions never shift; allow markers suppress fixes exactly as they
    suppress findings; post-fix re-scan proves the tree; a second run is a
    no-op; dry-run touches nothing and exits 1 while fixes are pending.

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

    # -- job-hardening (structural) ----------------------------------------------

    def test_job_without_timeout_fails_at_job_header(self):
        self._write(
            ".github/workflows/t.yml",
            "on: push\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("[job-hardening]", proc.stderr)
        self.assertIn("no timeout-minutes", proc.stderr)
        self.assertIn("file=.github/workflows/t.yml,line=5", proc.stdout)  # job header
        self.assertIn("default is 360", proc.stderr)

    def test_job_with_timeout_and_top_permissions_passes(self):
        self._write(
            ".github/workflows/ok.yml",
            "on: push\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    timeout-minutes: 10\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("[job-hardening]", proc.stderr)

    def test_missing_permissions_block_fails_anchored_on_trigger(self):
        self._write(
            ".github/workflows/np.yml",
            "name: No Perms\n"
            "on: push\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    timeout-minutes: 5\n"
            "    steps: []\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("declares no permissions block", proc.stderr)
        self.assertIn("file=.github/workflows/np.yml,line=2", proc.stdout)  # the on: line

    def test_job_level_permissions_satisfies_rule(self):
        self._write(
            ".github/workflows/jp.yml",
            "on: push\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    timeout-minutes: 5\n"
            "    permissions:\n"
            "      contents: read\n"
            "    steps: []\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_caller_job_exempt_from_timeout_check(self):
        self._write(
            ".github/workflows/caller.yml",
            "on: push\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  call:\n"
            "    uses: ./.github/workflows/reusable.yml\n"
            "    with:\n"
            "      env: prod\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("[job-hardening]", proc.stderr)

    def test_nested_timeout_does_not_count(self):
        # timeout-minutes nested under strategy: is NOT a job-level timeout
        self._write(
            ".github/workflows/nested.yml",
            "on: push\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    strategy:\n"
            "      matrix:\n"
            "        timeout-minutes: 5\n"
            "    steps: []\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("no timeout-minutes", proc.stderr)

    def test_allow_marker_on_anchor_lines(self):
        # marker on the job header suppresses that job's timeout finding;
        # marker on the on: line suppresses the permissions finding
        self._write(
            ".github/workflows/allow.yml",
            "on: push # wf-lint-allow: job-hardening — token restricted at repo level\n"
            "jobs:\n"
            "  a: # wf-lint-allow: job-hardening — upstream reusable gate owns the bound\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("2 allow-marker line(s)", proc.stdout)

    def test_non_workflow_yaml_not_scanned_by_job_hardening(self):
        self._write(
            "tools/config.yml",
            "jobs:\n"
            "  a:\n"
            "    runs-on: nowhere\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_file_without_jobs_section_is_ignored(self):
        self._write(".github/workflows/empty.yml", "name: placeholder\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_fix_dry_run_leaves_file_untouched_and_exits_pending(self):
        body = (
            "on: push\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n"
        )
        path = self._write(".github/workflows/dry.yml", body)
        proc = self._run(self.tree, None, "--fix", "--dry-run")
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)  # fixes pending
        self.assertIn("would apply 2 fix(es), 0 fix(es) suppressed", proc.stdout)
        with open(path) as fh:
            self.assertEqual(body, fh.read())  # dry run wrote nothing

    def test_fix_writes_missing_blocks_and_rescans_clean(self):
        self._write(
            ".github/workflows/fixme.yml",
            "on: push\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n"
            "  b:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n",
        )
        proc = self._run(self.tree, None, "--fix")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)  # re-scan proves the tree
        self.assertIn("applied 3 fix(es), 0 fix(es) suppressed", proc.stdout)
        with open(os.path.join(self.tree, ".github/workflows/fixme.yml")) as fh:
            fixed = fh.read()
        self.assertEqual(2, fixed.count("timeout-minutes: 10"))
        self.assertIn("permissions:\n  contents: read\n\njobs:", fixed)
        self.assertIn("  a:\n    timeout-minutes: 10\n    runs-on: ubuntu-latest", fixed)
        self.assertIn("  b:\n    timeout-minutes: 10\n    runs-on: ubuntu-latest", fixed)

    def test_fix_idempotent_second_run_is_noop(self):
        self._write(
            ".github/workflows/idem.yml",
            "on: push\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n",
        )
        first = self._run(self.tree, None, "--fix")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertIn("applied 2 fix(es)", first.stdout)
        second = self._run(self.tree, None, "--fix")
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertIn("applied 0 fix(es)", second.stdout)

    def test_fix_suppressed_by_allow_marker(self):
        self._write(
            ".github/workflows/allow.yml",
            "on: push # wf-lint-allow: job-hardening — token restricted at repo level\n"
            "jobs:\n"
            "  a: # wf-lint-allow: job-hardening — upstream reusable gate owns the bound\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n",
        )
        proc = self._run(self.tree, None, "--fix")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("applied 0 fix(es), 2 fix(es) suppressed", proc.stdout)

    def test_fix_leaves_caller_jobs_alone(self):
        body = (
            "on: push\n"
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  call:\n"
            "    uses: ./.github/workflows/reusable.yml\n"
            "    with:\n"
            "      env: prod\n"
        )
        path = self._write(".github/workflows/caller.yml", body)
        proc = self._run(self.tree, None, "--fix")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("applied 0 fix(es)", proc.stdout)  # caller exempt; no permission gap
        with open(path) as fh:
            self.assertEqual(body, fh.read())

    def test_fix_count_matches_scan_findings(self):
        # invariant: every fixable violation the scan reports gets exactly one fix
        self._write(
            ".github/workflows/multi.yml",
            "on: push\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n"
            "  b:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n"
            "  c:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps: []\n",
        )
        scan = self._run(self.tree)
        self.assertEqual(1, scan.returncode)
        self.assertIn("4 violation(s)", scan.stdout)  # 3 timeouts + 1 permissions
        proc = self._run(self.tree, None, "--fix")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("applied 4 fix(es)", proc.stdout)

    def test_dry_run_without_fix_is_usage_error(self):
        proc = self._run(self.tree, None, "--dry-run")
        self.assertEqual(2, proc.returncode)
        self.assertIn("--dry-run only makes sense with --fix", proc.stderr)

    # -- npm-ci (lockfile-aware install policy) -----------------------------------

    # The rule is opt-in like #518's adoption: silent wherever no committed
    # package-lock.json applies, loud where one does. Fixtures mirror the two
    # real shapes on main: typescript/<svc>/Dockerfile (service-dir context,
    # lock in the same dir) and the typescript/-root context (lock one level
    # up the ancestor chain).

    def test_workflow_cd_into_lockdir_fails_and_other_dir_passes(self):
        self._write("typescript/nebula-srv/package-lock.json", "{}\n")
        self._write("typescript/other-svc/package.json", '{}\n')
        self._write(
            ".github/workflows/w.yml",
            "jobs:\n"
            "  a:\n"
            "    steps:\n"
            "      - run: |\n"
            "          cd typescript/nebula-srv\n"
            "          npm install --no-audit\n"
            "      - run: |\n"
            "          cd typescript/other-svc\n"
            "          npm install --no-audit\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("typescript/nebula-srv/package-lock.json", proc.stderr)
        self.assertIn("use `npm ci`", proc.stderr)
        self.assertNotIn("other-svc", proc.stdout + proc.stderr)  # no lock: silent

    def test_workflow_step_working_directory_resolved(self):
        self._write("typescript/assembly-srv/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/w.yml",
            "jobs:\n"
            "  a:\n"
            "    steps:\n"
            "      - name: x\n"
            "        working-directory: typescript/assembly-srv\n"
            "        run: npm install --no-audit --no-fund\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("typescript/assembly-srv/package-lock.json", proc.stderr)

    def test_workflow_defaults_run_wd_resolved_and_step_resets(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/w.yml",
            "jobs:\n"
            "  a:\n"
            "    defaults:\n"
            "      run:\n"
            "        working-directory: svc\n"
            "    steps:\n"
            "      - run: npm install\n"
            "      - working-directory: other\n"
            "        run: npm install\n",
        )
        self._write("other/package.json", "{}\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("svc/package-lock.json", proc.stderr)
        self.assertNotIn("other/package-lock.json", proc.stderr)  # reset works

    def test_workflow_inline_run_cd_prefixed_resolved(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/w.yml",
            "jobs:\n"
            "  a:\n"
            "    steps:\n"
            "      - run: cd svc && npm install\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("svc/package-lock.json", proc.stderr)

    def test_workflow_npm_ci_and_exemptions_pass(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/w.yml",
            "jobs:\n"
            "  a:\n"
            "    steps:\n"
            "      - run: cd svc && npm ci --no-audit\n"
            "      # npm install --global typescript   <- comment line, ignored\n"
            "      - run: npm install --global typescript\n"
            "      - run: npm install --package-lock-only --dry-run\n"
            "      - run: cd svc && npm install # wf-lint-allow: npm-ci — vendored dir, reviewed\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("1 allow-marker line(s)", proc.stdout)

    def test_dockerfile_service_dir_lock_copied_hint(self):
        self._write("typescript/nebula-srv/package-lock.json", "{}\n")
        self._write(
            "typescript/nebula-srv/Dockerfile",
            "FROM node:20-bookworm\n"
            "COPY package.json ./\n"
            "COPY package-lock.json ./\n"
            "RUN npm install --omit=dev\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("typescript/nebula-srv/Dockerfile:4", proc.stderr)
        self.assertIn("lock already COPYed", proc.stderr)

    def test_dockerfile_ancestor_context_lock_hint(self):
        # typescript/-root build context: the lock lives one level up from
        # the Dockerfile's own dir — the ancestor walk must find it
        self._write("typescript/assembly-srv/package-lock.json", "{}\n")
        self._write(
            "typescript/assembly-srv/Dockerfile",
            "FROM node:20-bookworm-slim\n"
            "COPY assembly-srv/package.json ./\n"
            "RUN npm install --omit=dev --no-package-lock\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("COPY package-lock.json", proc.stderr)  # not-yet-copied hint

    def test_dockerfile_cd_chain_and_no_lock_pass(self):
        self._write("typescript/nebula-srv/package-lock.json", "{}\n")
        self._write("typescript/plain/Dockerfile", "FROM node:20\nRUN npm install\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)  # opt-in: no lock → silent
        # ...and nebula-srv's lock (with no Dockerfile there) fires nothing either
        # cd chain: last cd wins — the lock the chain resolves to must exist;
        # realistic shape (assembly-srv's actual heartbeat-client dance)
        self._write("typescript/assembly-srv/package-lock.json", "{}\n")
        self._write(
            "typescript/assembly-srv/Dockerfile",
            "FROM node:20-bookworm-slim\n"
            "RUN cd ../heartbeat-client && cd ../assembly-srv && npm install\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("typescript/assembly-srv/Dockerfile:2", proc.stderr)

    def test_dockerfile_multiline_run_and_copy_from_exempt(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            "svc/Dockerfile",
            "FROM node:20 AS build\n"
            "COPY package.json ./\n"
            "COPY package-lock.json ./\n"
            "RUN npm install \\\n"
            "  --omit=dev\n"
            "FROM node:20-slim\n"
            "COPY --from=build /app/node_modules ./node_modules\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("svc/Dockerfile:4", proc.stderr)  # RUN header anchors

    def test_npm_ci_rule_alone_selectable(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write("svc/Dockerfile", "FROM openjdk:17\nRUN npm install\n")
        proc = self._run(self.tree, None, "--rule", "npm-ci")
        self.assertEqual(1, proc.returncode)
        self.assertIn("rules: npm-ci", proc.stdout)
        self.assertNotIn("[dead-base]", proc.stderr)  # other rules not selected

    def test_node_modules_never_scanned(self):
        # vendored packages ship their own workflows/Dockerfiles; after any
        # local npm ci they would flood every rule. The harness prunes them.
        self._write(
            "node_modules/pkg/.github/workflows/vendored.yml",
            "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps: []\n",
        )
        self._write(
            ".github/workflows/real.yml",
            "on: push\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps: []\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)  # real.yml's missing blocks fire
        self.assertNotIn("node_modules", proc.stdout + proc.stderr)

    # -- maven-cache ----------------------------------------------------------------

    MAVEN_OK = (
        "on: push\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Set up JDK\n"
        "        uses: actions/setup-java@v3\n"
        "        with:\n"
        "          java-version: '21'\n"
        "          distribution: temurin\n"
        "          cache: maven\n"
        "      - run: mvn -B test\n"
    )

    def test_uncached_setup_java_with_mvn_fails(self):
        # The drift class: the 2026-09-25 main 429 (run 36088456372) — setup-java
        # without cache: maven + a runner mvn invocation, concurrent jobs cold-pull.
        body = self.MAVEN_OK.replace("          cache: maven\n", "")
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[maven-cache]", proc.stderr)
        self.assertIn("cache: maven", proc.stderr)

    def test_cached_maven_passes(self):
        self._write(".github/workflows/ci.yml", self.MAVEN_OK)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    def test_quoted_and_commented_cache_values_pass(self):
        for variant in ("          cache: 'maven'  # fleet standard\n",
                        '          cache: "maven"\n'):
            body = self.MAVEN_OK.replace("          cache: maven\n", variant)
            self._write(".github/workflows/ci.yml", body)
            proc = self._run(self.tree, None, "--rule", "maven-cache")
            self.assertEqual(0, proc.returncode, variant)

    def test_no_setup_java_no_finding(self):
        # Maven via a preinstalled JDK (no setup-java step) is not the rule's
        # subject — nothing to attach cache: maven to.
        body = self.MAVEN_OK.replace(
            "      - name: Set up JDK\n"
            "        uses: actions/setup-java@v3\n"
            "        with:\n"
            "          java-version: '21'\n"
            "          distribution: temurin\n"
            "          cache: maven\n",
            "",
        )
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    def test_no_mvn_no_finding(self):
        # setup-java without any runner mvn (e.g. java-only tooling) is not a
        # cold-pull risk — the rule requires both halves.
        body = self.MAVEN_OK.replace("      - run: mvn -B test\n", "      - run: java -jar app.jar\n")
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    def test_mvnw_counts(self):
        # Same cold-pull class, different wrapper spelling.
        body = self.MAVEN_OK.replace(
            "      - run: mvn -B test\n",
            "      - run: ./mvnw -B test\n",
        )
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    def test_docker_internal_maven_not_in_scope(self):
        # docker-gate shape: mvn executes inside the container; a host cache
        # would not reach it, and docker build/run lines are excluded.
        body = self.MAVEN_OK.replace(
            "      - run: mvn -B test\n",
            "      - run: docker build -f jvm/Dockerfile .\n",
        )
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    def test_wrong_ecosystem_cache_still_fails(self):
        # cache: gradle does not cover ~/.m2 — the rule demands maven.
        body = self.MAVEN_OK.replace("          cache: maven\n", "          cache: gradle\n")
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[maven-cache]", proc.stderr)

    def test_other_steps_cache_does_not_leak(self):
        # A DIFFERENT setup-java-family step cached elsewhere must not satisfy
        # the uncached one; each step is judged within its own window.
        body = (
            "on: push\n"
            "jobs:\n"
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - name: Cached gradle step\n"
            "        uses: gradle/actions/setup-gradle@v3\n"
            "        with:\n"
            "          cache: maven\n"
            "      - name: Uncached java step\n"
            "        uses: actions/setup-java@v3\n"
            "      - run: mvn -B test\n"
        )
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(1, proc.returncode)

    def test_multi_job_one_offender_fails_with_anchor(self):
        # Two mvn jobs in ONE document; only `bad` is uncached. Finding
        # anchors at that job's setup-java line so the allow marker works
        # there. (Concatenating two YAML documents is invalid — the line-based
        # job locator reads the first jobs: block only.)
        body = self.MAVEN_OK.replace("  build:\n", "  ok:\n") + self.MAVEN_OK.replace(
            "          cache: maven\n", ""
        ).replace("  build:\n", "  bad:\n").replace("on: push\n", "").replace("jobs:\n", "", 1)
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(1, proc.returncode)
        self.assertIn("job `bad`", proc.stderr)
        self.assertNotIn("job `ok`", proc.stderr)

    def test_allow_marker_on_uses_line_suppresses(self):
        body = self.MAVEN_OK.replace("          cache: maven\n", "").replace(
            "        uses: actions/setup-java@v3\n",
            "        uses: actions/setup-java@v3  # wf-lint-allow: deliberate cold-pull\n",
        )
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)
        self.assertIn("allow-marker", proc.stdout)

    def test_reusable_workflow_caller_job_exempt(self):
        body = self.MAVEN_OK.replace(
            "  build:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - name: Set up JDK\n"
            "        uses: actions/setup-java@v3\n"
            "        with:\n"
            "          java-version: '21'\n"
            "          distribution: temurin\n"
            "          cache: maven\n"
            "      - run: mvn -B test\n",
            "  build:\n"
            "    uses: ./.github/workflows/reusable.yml\n",
        )
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    def test_no_trigger_scratch_file_ignored(self):
        body = self.MAVEN_OK.replace("          cache: maven\n", "").replace("on: push\n", "")
        self._write(".github/workflows/draft.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    def test_maven_cache_rule_alone_selectable(self):
        body = self.MAVEN_OK.replace("          cache: maven\n", "")
        self._write(".github/workflows/ci.yml", body)
        proc = self._run(self.tree, None, "--rule", "maven-cache")
        self.assertEqual(1, proc.returncode)
        self.assertIn("rules: maven-cache", proc.stdout)

    def test_real_repo_maven_workflows_green(self):
        # Born-green invariant: the #569-fixed fleet must pass the new rule —
        # this test rots the moment someone drops a cache: maven input again.
        proc = self._run(os.path.join(REPO_ROOT, ".github", "workflows"), None, "--rule", "maven-cache")
        self.assertEqual(0, proc.returncode)

    # -- node-cache / pip-cache ------------------------------------------------------

    NODE_WF = (
        "on: push\n"
        "jobs:\n"
        "  build:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-node@v4\n"
        "        with:\n"
        "          node-version: '22'\n"
        "      - name: Install\n"
        "        working-directory: svc\n"
        "        run: npm ci\n"
    )

    PIP_WF = (
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        "          python-version: '3.11'\n"
        "      - name: Install\n"
        "        run: python3 -m pip install -r requirements-dev.txt\n"
    )

    def test_node_uncached_lockbearing_fails_and_names_lock(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(".github/workflows/ci.yml", self.NODE_WF)
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[node-cache]", proc.stderr)
        self.assertIn("svc/package-lock.json", proc.stderr)

    def test_node_root_lock_fails(self):
        self._write("package-lock.json", "{}\n")
        self._write(
            ".github/workflows/ci.yml",
            self.NODE_WF.replace("        working-directory: svc\n", ""),
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[node-cache]", proc.stderr)

    def test_node_cached_passes(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/ci.yml",
            self.NODE_WF.replace(
                "          node-version: '22'\n",
                "          node-version: '22'\n"
                "          cache: npm\n"
                "          cache-dependency-path: svc/package-lock.json\n",
            ),
        )
        proc = self._run(self.tree, None, "--rule", "node-cache")
        self.assertEqual(0, proc.returncode)

    def test_node_lockless_dir_silent(self):
        # GitHub's cache: npm hard-fails without a lock — the rule must not
        # demand an unremediable fix (npm-ci's opt-in philosophy).
        self._write(".github/workflows/ci.yml", self.NODE_WF)
        proc = self._run(self.tree, None, "--rule", "node-cache")
        self.assertEqual(0, proc.returncode)

    def test_node_no_npm_silent(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/ci.yml",
            self.NODE_WF.replace("        run: npm ci\n", "        run: node app.js\n"),
        )
        proc = self._run(self.tree, None, "--rule", "node-cache")
        self.assertEqual(0, proc.returncode)

    def test_node_wrong_ecosystem_cache_fails(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/ci.yml",
            self.NODE_WF.replace("          node-version: '22'\n", "          node-version: '22'\n          cache: pnpm\n"),
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)

    def test_node_cd_into_lockdir_resolved(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/ci.yml",
            self.NODE_WF.replace(
                "      - name: Install\n        working-directory: svc\n        run: npm ci\n",
                "      - name: Install\n        run: cd svc && npm ci\n",
            ),
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[node-cache]", proc.stderr)

    def test_pip_uncached_requirements_install_fails(self):
        self._write("requirements-dev.txt", "pytest\n")
        self._write(".github/workflows/ci.yml", self.PIP_WF)
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[pip-cache]", proc.stderr)
        self.assertIn("requirements-dev.txt", proc.stderr)

    def test_pip_cached_quoted_passes(self):
        # mesh-pytest's live shape: cache: 'pip' with quotes.
        self._write("requirements-dev.txt", "pytest\n")
        self._write(
            ".github/workflows/ci.yml",
            self.PIP_WF.replace(
                "          python-version: '3.11'\n",
                "          python-version: '3.11'\n          cache: 'pip'\n          cache-dependency-path: requirements-dev.txt\n",
            ),
        )
        proc = self._run(self.tree, None, "--rule", "pip-cache")
        self.assertEqual(0, proc.returncode)

    def test_pip_missing_requirements_file_silent(self):
        # -r naming a file the repo doesn't ship: cache-dependency-path would
        # be fiction — GitHub's cache: pip would also fail. Silent.
        self._write(".github/workflows/ci.yml", self.PIP_WF)
        proc = self._run(self.tree, None, "--rule", "pip-cache")
        self.assertEqual(0, proc.returncode)

    def test_pip_adhoc_install_silent(self):
        # The ~40 wr-conf jobs install pytest/psycopg2 ad hoc — marginal
        # wheel-cache win, arbitrary key file. Deliberately out of scope.
        self._write("requirements-dev.txt", "pytest\n")
        self._write(
            ".github/workflows/ci.yml",
            self.PIP_WF.replace(
                "        run: python3 -m pip install -r requirements-dev.txt\n",
                "        run: python3 -m pip install --quiet pytest psycopg2-binary\n",
            ),
        )
        proc = self._run(self.tree, None, "--rule", "pip-cache")
        self.assertEqual(0, proc.returncode)

    def test_pip_requirements_via_working_directory(self):
        self._write("svc/requirements.txt", "pytest\n")
        self._write(
            ".github/workflows/ci.yml",
            self.PIP_WF.replace(
                "requirements-dev.txt", "requirements.txt"
            ).replace(
                "      - name: Install\n",
                "      - name: Install\n        working-directory: svc\n",
            ),
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[pip-cache]", proc.stderr)

    def test_node_pip_allow_marker_on_uses_line(self):
        self._write("svc/package-lock.json", "{}\n")
        self._write(
            ".github/workflows/ci.yml",
            self.NODE_WF.replace(
                "      - uses: actions/setup-node@v4\n",
                "      - uses: actions/setup-node@v4  # wf-lint-allow: node-cache — runner pre-warms the store\n",
            ),
        )
        proc = self._run(self.tree, None, "--rule", "node-cache")
        self.assertEqual(0, proc.returncode)
        self.assertIn("allow-marker", proc.stdout)

    def test_node_pip_rules_alone_selectable(self):
        # --rule takes one name per flag (a comma blob reads as one unknown
        # rule and exits 2).
        self._write(".github/workflows/ci.yml", self.NODE_WF)
        proc = self._run(self.tree, None, "--rule", "node-cache", "--rule", "pip-cache")
        self.assertEqual(0, proc.returncode)
        self.assertIn("rules: node-cache,pip-cache", proc.stdout)

    def test_real_repo_node_pip_workflows_green(self):
        # Born-green invariant on the remediated fleet (broker-e2e +
        # vanadium-sonar carry cache: npm; mesh-pytest/vanadium-sonar pip
        # already cached). Rots the moment a lock-bearing npm job or a -r
        # pip job drops its cache input again.
        proc = self._run(
            os.path.join(REPO_ROOT, ".github", "workflows"),
            None, "--rule", "node-cache", "--rule", "pip-cache",
        )
        self.assertEqual(0, proc.returncode)

    # -- real repo -----------------------------------------------------------------

    def test_real_repo_scan_does_not_crash(self):
        proc = self._run(os.path.join(REPO_ROOT, ".github", "workflows"))
        self.assertIn(proc.returncode, (0, 1))
        self.assertIn("wf-lint:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
