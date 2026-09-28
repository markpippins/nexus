#!/usr/bin/env python3
"""Unit tests for bin/pgie-evidence.py — the shared TAP guard + evidence
emitter used by BOTH bin/run-pg-integration-e2e.sh (local) and the CI
pg-integration-e2e job. Hermetic (no DB, no network): synthetic TAP logs
in tmp dirs.

Contract under test (canonical order — fail-first, skip-must-fail,
pass floor; missing summary == suite crashed before completing):
  guard -> exit 0, silent, one TSV row verdict=pass on a conformant log
  guard -> exit 1 + canonical message + TSV row verdict=fail on each
           violation class (failures / skips / floor / missing summary)
  emit  -> pgie-evidence/1 artifact with totals aggregation (an
           "unknown" fail count is NOT summed numerically), ports,
           duration 0 when no start timestamp, and no .tmp residue.

Structural tests bind the two consumers to the helper (Decision 2:
duplication is a forked authority — the guard/emitter must have exactly
one source): the CI pg job must route every suite guard through
`pgie-evidence.py guard` (no inline grep contract) and must upload the
artifact with if: always(); the local runner must delegate likewise.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HELPER = os.path.join(_REPO_ROOT, "bin", "pgie-evidence.py")
RUNNER = os.path.join(_REPO_ROOT, "bin", "run-pg-integration-e2e.sh")
WORKFLOW = os.path.join(_REPO_ROOT, ".github", "workflows", "broker-e2e.yml")

GOOD_TAP = (
    "TAP version 13\n"
    "ok 1 - a\nok 2 - b\n1..2\n"
    "# pass 2\n# fail 0\n# skipped 0\n# ok\n"
)
FAIL_TAP = (
    "TAP version 13\nnot ok 1 - a\n1..1\n"
    "# pass 0\n# fail 1\n"
)
SKIP_TAP = (
    "TAP version 13\nok 1 - a\n1..1\n"
    "# skip 1 no kernel\n# pass 0\n# fail 0\n# skipped 1\n"
)
CRASH_TAP = "TAP version 13\nok 1 - a\n1..1\n# pass 1\n# ok\n"  # no fail summary


def _run_guard(tmp, log_text, floor, label):
    log = os.path.join(tmp, "tap.log")
    tsv = os.path.join(tmp, "results.tsv")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(log_text)
    proc = subprocess.run(
        [sys.executable, HELPER, "guard", log, str(floor), label, tsv],
        capture_output=True, text=True, timeout=30)
    rows = []
    if os.path.exists(tsv):
        rows = [line.rstrip("\n").split("\t")
                for line in open(tsv, encoding="utf-8")]
    return proc, rows


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pgie-test-")

    def test_pass_writes_single_pass_row(self):
        proc, rows = _run_guard(self.tmp, GOOD_TAP, 2, "bridge")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "bridge")
        self.assertEqual(rows[0][1], "2")
        self.assertEqual(rows[0][2], "0")
        self.assertEqual(rows[0][3], "0")
        self.assertEqual(rows[0][5], "pass")

    def test_fail_first_violation(self):
        proc, rows = _run_guard(self.tmp, FAIL_TAP, 12, "smoke")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("test failures", proc.stderr)
        self.assertEqual(rows[0][2], "1")
        self.assertEqual(rows[0][5], "fail")

    def test_skip_must_fail(self):
        proc, rows = _run_guard(self.tmp, SKIP_TAP, 12, "bridge")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("SKIPPED", proc.stderr)
        self.assertEqual(rows[0][5], "fail")

    def test_missing_fail_summary_is_crash(self):
        proc, rows = _run_guard(self.tmp, CRASH_TAP, 1, "crashy")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("crashed before completing", proc.stderr)
        self.assertEqual(rows[0][2], "unknown")
        self.assertEqual(rows[0][5], "fail")

    def test_floor_violation(self):
        proc, rows = _run_guard(self.tmp, GOOD_TAP, 12, "smoke")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("only 2 passed", proc.stderr)
        self.assertEqual(rows[0][5], "fail")

    def test_counts_survive_missing_trailing_newline(self):
        # Regression: summary regexes must not require an EOL after the
        # digits (logs truncated without a trailing newline still count).
        proc, rows = _run_guard(self.tmp, GOOD_TAP.rstrip("\n"), 2, "bridge")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(rows[0][1], "2")


class EmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pgie-test-")

    def _emit(self, tsv_text, verdict="pass", start="0"):
        tsv = os.path.join(self.tmp, "results.tsv")
        out = os.path.join(self.tmp, "ev.json")
        with open(tsv, "w", encoding="utf-8") as fh:
            fh.write(tsv_text)
        proc = subprocess.run(
            [sys.executable, HELPER, "emit", "--verdict", verdict,
             "--exit-code", "0" if verdict == "pass" else "1",
             "--results-tsv", tsv, "--start-ts", start, "--suite", "all",
             "--pg-port", "5432", "--mongo-port", "28017",
             "--kernel-port", "18099", "--legacy-port", "32110",
             "--out", out],
            capture_output=True, text=True, timeout=30)
        return proc, out

    def test_artifact_shape_and_totals(self):
        tsv = (
            "bridge+attempt-lifecycle\t13\t0\t0\t10\tpass\t/tmp/a.log\n"
            "smoke\t0\tunknown\tunknown\t12\tfail\t/tmp/b.log\n"
        )
        proc, out = self._emit(tsv, verdict="fail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        a = json.load(open(out, encoding="utf-8"))
        self.assertEqual(a["schema"], "pgie-evidence/1")
        self.assertEqual(a["verdict"], "fail")
        self.assertEqual(a["exit_code"], 1)
        # "unknown" counts are NOT summed numerically...
        self.assertEqual(a["totals"], {"pass": 13, "fail": 0, "skipped": 0})
        self.assertEqual(a["suites"][1]["fail"], "unknown")
        self.assertEqual(a["stack_ports"]["pg"], 5432)
        self.assertEqual(a["duration_seconds"], 0)  # start-ts 0 -> duration 0

    def test_atomic_no_tmp_residue(self):
        proc, out = self._emit("bridge\t2\t0\t0\t2\tpass\t/tmp/a.log\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(os.path.exists(out))
        self.assertFalse(os.path.exists(out + ".tmp"))


class SingleAuthorityTests(unittest.TestCase):
    """The helper is the ONE source of the guard contract (Decision 2)."""

    def test_ci_pg_job_guards_through_the_helper(self):
        text = open(WORKFLOW, encoding="utf-8").read()
        pg = text.split("  pg-integration-e2e:", 1)[1]
        self.assertIn("pgie-evidence.py guard", pg)
        self.assertNotIn("grep -q '^# fail 0$'", pg,
                         "inline guard copies are a forked authority — use the helper")
        self.assertIn("pgie-evidence.py emit", pg)
        self.assertIn("actions/upload-artifact@v4", pg)

    def test_ci_artifact_upload_runs_on_failure_too(self):
        text = open(WORKFLOW, encoding="utf-8").read()
        pg = text.split("  pg-integration-e2e:", 1)[1]
        upload = pg.split("Upload pgie-evidence/1 artifact", 1)[1]
        head = upload[:200]
        self.assertIn("if: always()", head)

    def test_local_runner_delegates(self):
        text = open(RUNNER, encoding="utf-8").read()
        self.assertIn("pgie-evidence.py guard", text)
        self.assertNotIn("record_result", text)


if __name__ == "__main__":
    unittest.main()
