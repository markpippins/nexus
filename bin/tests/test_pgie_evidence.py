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
import shutil
import subprocess
import time
import sys
import tempfile
import unittest

PGIE = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "pgie-evidence.py")

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

def _load_merge_pr():
    """Import bin/merge_pr.py for the gate-binding tests (same importlib
    pattern this repo's bin test files use for script modules)."""
    import importlib.util as _ilu
    _mp = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "merge_pr.py")
    _spec = _ilu.spec_from_file_location("merge_pr_under_test", _mp)
    _mod = _ilu.module_from_spec(_spec)
    import sys as _sys
    _sys.modules["merge_pr_under_test"] = _mod
    _spec.loader.exec_module(_mod)
    return _mod

merge_pr = _load_merge_pr()


# ══════════════════════════════════════════════════════════════════════
#  mint-head (Decision 10 head-binding tool) — output-shape + fail-closed
# ══════════════════════════════════════════════════════════════════════

class MintHead(unittest.TestCase):
    """The minted tag must be `head:<sha7>` of the PR's CURRENT remote head,
    and must fail closed (exit 2, ::error::) when the remote cannot be
    resolved — never print a guessable or copied value."""

    def _run(self, *args, monkey=None):
        env = dict(os.environ)
        if monkey:
            env.update(monkey)
        return subprocess.run(
            [sys.executable, PGIE, "mint-head", *args],
            capture_output=True, text=True, env=env, timeout=30)

    def test_pr_635_head_binds_to_live_remote(self):
        """Live: minted sha must equal the PR's headRefOid prefix. Skipped
        when gh/network is unavailable (CI without token, offline laptop)."""
        gh = shutil.which("gh")
        if not gh:
            self.skipTest("gh not available")
        probe = subprocess.run(
            ["gh", "pr", "view", "635", "--json", "headRefOid", "--jq", ".headRefOid"],
            capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            self.skipTest("gh unauthenticated or offline")
        expected = probe.stdout.strip()[:7]
        r = self._run("--pr", "635", "--repo", "markpippins/nexus")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout.strip(), r"^head:[0-9a-f]{7}$")
        self.assertEqual(r.stdout.strip().split(":")[1], expected)

    def test_bogus_pr_fails_closed(self):
        r = self._run("--pr", "999999999", "--repo", "markpippins/nexus")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("::error::", r.stderr + r.stdout)

    def test_unresolvable_repo_fails_closed(self):
        r = self._run("--pr", "1", "--repo", "definitely-not-a-real-repo-xyz/none")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("::error::", r.stderr + r.stdout)


class GateHeadBinding(unittest.TestCase):
    """merge_pr gate 3: `head:` tag equality vs headRefOid (Decision 10).
    The tag subsumes the timestamp predicate: any push after the attestation
    changes headRefOid and fails the binding regardless of created_ms."""

    HEAD = "a8b1dfc600000000000000000000000000000000"

    def _eval(self, tags, head_sha=HEAD, run_json=None):
        rec = {
            "id": "bind00001", "role": "tester",
            "createdAt": int(time.time() * 1000) - 3600_000,
            "tags": tags + ["type:approval", "status:done", "pr:487"],
            "title": "Tester attestation: PR #487 — CI run 36000000001 success",
            "content": "CI run 36000000001 success at head a8b1dfc6.",
            "recordType": "assessment",
        }
        kwargs = {}
        if run_json is not None:
            kwargs["run_json"] = run_json
        return merge_pr.evaluate_attestation(
            [rec], 487, int(time.time() * 1000) - 7200_000,
            head_sha=head_sha, **kwargs)

    def _ok_run(self, *args):
        return {"c": "success", "s": self.HEAD}

    def test_matching_head_tag_passes(self):
        ok, detail, code = self._eval(["head:a8b1dfc6"], run_json=self._ok_run)
        self.assertTrue(ok, detail)
        self.assertIsNone(code)

    def test_long_form_tag_also_binds(self):
        ok, detail, code = self._eval([f"head:{self.HEAD}"], run_json=self._ok_run)
        self.assertTrue(ok, detail)

    def test_wrong_head_tag_fails_even_when_fresh(self):
        ok, detail, code = self._eval(["head:deadbeef"], run_json=self._ok_run)
        self.assertFalse(ok)
        self.assertEqual(code, "ATT_STALE_HEAD")
        self.assertIn("binding is by SHA", detail)

    def test_binding_defeats_timestamp_freshness(self):
        """The incident case: a re-used/copied row (wrong head tag) must fail
        on the BINDING even though its createdAt postdates the head commit —
        the old timestamp predicate alone passed exactly this shape."""
        ok, detail, code = self._eval(["head:deadbeef"], run_json=self._ok_run)
        self.assertFalse(ok)
        self.assertIn("binding", detail.lower())

    def test_absent_head_tag_falls_through_to_content_rules(self):
        """Legacy rows without a head: tag keep their additive semantics:
        no binding check, content-based CI-evidence rule still applies."""
        ok, detail, code = self._eval([], run_json=self._ok_run)
        self.assertTrue(ok, detail)


if __name__ == "__main__":
    unittest.main()
