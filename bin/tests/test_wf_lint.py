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

    # -- migration-dup-prefix -------------------------------------------------

    def test_dup_prefix_fails_with_pointer_to_twin(self):
        # the live drift class (thread 6bba5dd3): two files claiming one
        # version in the same migration directory
        self._write(
            "typescript/svc/migrations/055-agent-records-tags-gin.sql",
            "-- gin index\nCREATE INDEX IF NOT EXISTS i ON t USING gin (tags);\n",
        )
        self._write(
            "typescript/svc/migrations/055-allow-supervisor-role.sql",
            "-- supervisor widening (lex-second: the skipped twin)\nSELECT 1;\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        self.assertIn("[migration-dup-prefix]", proc.stderr)
        # the violation anchors on the lex-SECOND file and names its twin
        self.assertIn("055-allow-supervisor-role.sql:1", proc.stderr)
        self.assertIn("also claimed by 055-agent-records-tags-gin.sql", proc.stderr)
        self.assertIn("silently skips", proc.stderr)

    def test_unique_prefixes_and_cross_dir_reuse_pass(self):
        # three files, one dir: no dup; the same prefix in a DIFFERENT
        # service's migrations dir is a separate namespace — never a finding
        for name in ("001-a.sql", "002-b.sql", "003-c.sql"):
            self._write(f"typescript/svc/migrations/{name}", "SELECT 1;\n")
        self._write("typescript/other/migrations/001-x.sql", "SELECT 1;\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_non_numeric_and_four_digit_files_ignored(self):
        # scd-type4-*.sql / seed-*.sql / run-NNN.js are not version-prefixed
        # files; a 4-digit prefix (V-migrations style) is not NNN- either
        self._write("typescript/svc/migrations/scd-type4-temporal.sql", "SELECT 1;\n")
        self._write("typescript/svc/migrations/seed-projections.sql", "SELECT 1;\n")
        self._write("typescript/svc/migrations/0001-wide.sql", "SELECT 1;\n")
        self._write("typescript/svc/migrations/README.md", "docs\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_adonisjs_ordered_runner_dirs_exempt(self):
        # adonisjs database/migrations is an ordered file-based runner — no
        # version ledger, so a shared prefix is not a skip class there
        self._write("adonisjs/app/database/migrations/1681000001_one.sql", "SELECT 1;\n")
        self._write("adonisjs/app/database/migrations/1681000001_two.sql", "SELECT 1;\n")
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_allow_marker_on_header_suppresses_dup(self):
        body_a = "-- a\nSELECT 1;\n"
        body_b = "-- legacy duplicate kept for DR replay # wf-lint-allow: migration-dup-prefix\nSELECT 1;\n"
        self._write("typescript/svc/migrations/010-a.sql", body_a)
        self._write("typescript/svc/migrations/010-b.sql", body_b)
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("1 allow-marker line(s)", proc.stdout)

    def test_triple_dup_reports_both_lex_seconds(self):
        self._write("typescript/svc/migrations/020-a.sql", "SELECT 1;\n")
        self._write("typescript/svc/migrations/020-b.sql", "SELECT 1;\n")
        self._write("typescript/svc/migrations/020-c.sql", "SELECT 1;\n")
        proc = self._run(self.tree)
        self.assertEqual(1, proc.returncode)
        stderr = proc.stderr
        self.assertIn("020-b.sql:1", stderr)
        self.assertIn("020-c.sql:1", stderr)
        self.assertEqual(2, stderr.count("[migration-dup-prefix]"))

    def test_fix_rescan_keeps_dup_rule_state_clean(self):
        # --fix re-scans the same rule instances after writing; cross-file
        # state must reset per scan so a clean re-scan stays clean. Uses the
        # job-hardening rule (the fixable one): missing permissions + timeout
        # are auto-inserted, the post-fix re-scan must exit 0, and the tree's
        # migration dirs (none here) must not re-fire stale dup findings.
        self._write(
            ".github/workflows/wf.yml",
            "on: push\n"
            "jobs:\n"
            "  j:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - run: echo hi\n",
        )
        proc = self._run(self.tree, None, "--fix")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("applied 2 fix", proc.stdout)
        fixed = open(os.path.join(self.tree, ".github", "workflows", "wf.yml")).read()
        self.assertIn("permissions:", fixed)
        self.assertIn("timeout-minutes:", fixed)

    def test_sql_files_use_dash_marker_syntax(self):
        # SQL comment syntax (`--`) carries the same escape hatch (`#` is not
        # a comment in SQL); conduit's historical twins are the live case
        self._write("svc/migrations/030-a.sql", "-- original\nSELECT 1;\n")
        self._write(
            "svc/migrations/030-b-v2.sql",
            "-- wf-lint-allow: migration-dup-prefix — superseded rewrite kept as a historical record\nSELECT 1;\n",
        )
        proc = self._run(self.tree)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("1 allow-marker line(s)", proc.stdout)

    # -- real repo -----------------------------------------------------------------

    def test_real_repo_scan_does_not_crash(self):
        proc = self._run(os.path.join(REPO_ROOT, ".github", "workflows"))
        self.assertIn(proc.returncode, (0, 1))
        self.assertIn("wf-lint:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
