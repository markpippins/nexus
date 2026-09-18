#!/usr/bin/env python3
"""Hermetic tests for bin/resolver-soak-report.py (wind enforce-flip gate).

No services, no database, no journalctl: parsing and analyze() are pure
functions fed line lists; fetch_journal is injected/mocked. Pins the four
mechanical criteria from the enforce-flip gate design (thread 1adce409,
comment 0402e9b2) as the lease-soak pattern applied to the resolver:

  (a) WINDOW      — first observation >= 14d old
  (b) RESOLUTION  — 100% of REAL lines outcome=ok; NO_DATA honest when empty;
                    synthetic NEVER counted toward (b)
  (c) VOCABULARY  — zero verdicts outside the V174 six; unknown=REVIEW,
                    outside-six=FAIL, judged over BOTH streams
  (d) ERRORS      — zero outcome=error lines (both streams)

Also pins the parser contract (marker, key=value, required fields), the
--fail-not-ready gate semantics (exit 0/1/2), and a source cross-check that
the tool's V174_STATES list cannot drift from wind-srv capability-resolver.js.

Run:
  python3 -m pytest bin/tests/test_resolver_soak_report.py -v
"""

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_SELF = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_SELF, "..", ".."))

_spec = importlib.util.spec_from_file_location(
    "resolver_soak_report", os.path.join(REPO_ROOT, "bin", "resolver-soak-report.py"))
RSR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RSR)


def L(ts, node, demand, verdict, outcome, probe=None, mode="warn"):
    probe_tok = f"probe={probe} " if probe else ""
    return (f"{ts} titanium node[1]: resolver-check node={node} demand={demand} "
            f"verdict={verdict} outcome={outcome} mode={mode} {probe_tok}"
            .rstrip())


NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)
T0 = "2026-09-18T09:13:44-0400"   # the actual window-open line
REAL_NOW = datetime.now(timezone.utc)
OLD = (REAL_NOW - timedelta(days=15)).astimezone().isoformat()
RECENT = (REAL_NOW - timedelta(days=1)).astimezone().isoformat()


def parse(lines):
    return [p for p in (RSR.parse_resolver_check_line(l) for l in lines) if p]


class TestParser(unittest.TestCase):
    def test_marker_swap_parses_resolver_lines(self):
        p = parse([f"{T0} titanium node[1]: resolver-check node=review "
                   "demand=capability:has-active-shrapnel-protocol verdict=satisfied "
                   "outcome=ok mode=warn probe=synthetic"])
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["node"], "review")
        self.assertEqual(p[0]["demand"], "capability:has-active-shrapnel-protocol")
        self.assertEqual(p[0]["verdict"], "satisfied")
        self.assertEqual(p[0]["outcome"], "ok")
        self.assertEqual(p[0]["probe"], "synthetic")
        self.assertEqual(p[0]["mode"], "warn")

    def test_non_marker_lines_ignored(self):
        self.assertEqual(parse([
            "Sep 18 05:00:00 titanium node[1]: GET /api/nodes 200",
            "Sep 18 05:00:01 titanium node[1]: lease-check role=dba mode=warn outcome=adopted",
        ]), [])

    def test_missing_required_fields_rejected(self):
        self.assertEqual(parse([
            f"{T0} titanium node[1]: resolver-check node=review outcome=ok",
            f"{T0} titanium node[1]: resolver-check node=review verdict=satisfied",
        ]), [])

    def test_absent_probe_field_means_real(self):
        p = parse([L(RECENT, "review", "capability:k", "satisfied", "ok")])
        self.assertEqual(p[0]["probe"], None)  # real stream in analyze()

    def test_real_line_from_the_actual_opening_journal(self):
        p = parse([f"{T0} titanium node[1]: resolver-check node=decide "
                   "demand=role:lead-engineer verdict=satisfied outcome=ok "
                   "mode=warn probe=synthetic"])
        # fromisoformat preserves the journal's own offset — the wall-clock
        # reading must round-trip exactly as journald emitted it.
        self.assertEqual(p[0]["ts"].isoformat(), "2026-09-18T09:13:44-04:00")

    def test_colonless_offset_parses_on_py310_too(self):
        """CI's 3.10 leg caught this: <3.11 fromisoformat rejects '-0400'
        (colonless), which is exactly what journalctl short-iso emits. The
        parser must normalize — a ts=None on this class of line would make
        the Oct 2 gate read an empty window and report NOT_READY forever."""
        p = parse([f"{T0} titanium node[1]: resolver-check node=n "
                   "demand=capability:k verdict=satisfied outcome=ok"])
        self.assertIsNotNone(p[0]["ts"])
        self.assertEqual(p[0]["ts"].utcoffset(), timedelta(hours=-4))

    def test_garbage_timestamp_stays_none(self):
        p = parse(["not-a-timestamp titanium node[1]: resolver-check "
                   "node=n demand=capability:k verdict=satisfied outcome=ok"])
        self.assertEqual(p[0]["ts"], None)  # still a parseable row, just undated


class TestCriteria(unittest.TestCase):
    def test_all_pass_when_window_old_resolution_clean(self):
        lines = [
            L(OLD, "review", "capability:k", "satisfied", "ok", probe="synthetic"),
            L(RECENT, "review", "role:tester", "satisfied", "ok"),  # real
        ]
        r = RSR.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["a_window_ge_14d"]["status"], "PASS")
        self.assertEqual(r["criteria"]["b_real_resolution_100pct"]["status"], "PASS")
        self.assertEqual(r["criteria"]["c_vocabulary_v174_cold"]["status"], "PASS")
        self.assertEqual(r["criteria"]["d_zero_errors"]["status"], "PASS")
        self.assertTrue(r["ready"])
        self.assertEqual(r["verdict"], "READY")

    def test_window_pending_when_first_observation_fresh(self):
        # fresh = 1d before REAL now (the report's real clock), so the
        # 14d window is genuinely still pending.
        lines = [L(RECENT, "n", "capability:k", "satisfied", "ok", probe="synthetic")]
        r = RSR.analyze(lines, now=REAL_NOW)
        self.assertEqual(r["criteria"]["a_window_ge_14d"]["status"], "PENDING")
        self.assertFalse(r["ready"])

    def test_no_lines_at_all(self):
        r = RSR.analyze([], now=NOW)
        self.assertEqual(r["window"]["first_observation"], None)
        self.assertEqual(r["criteria"]["b_real_resolution_100pct"]["status"], "NO_DATA")
        self.assertFalse(r["ready"])

    def test_synthetic_never_counts_toward_b(self):
        # ONLY synthetic traffic, 15 days of it — (b) must stay NO_DATA,
        # not PASS. Probes prove the gate; they do not prove the semantics.
        lines = [
            L(OLD, "n", "capability:k", "satisfied", "ok", probe="synthetic"),
            L(RECENT, "n", "role:x", "satisfied", "ok", probe="synthetic"),
        ]
        r = RSR.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["a_window_ge_14d"]["status"], "PASS")
        self.assertEqual(r["criteria"]["b_real_resolution_100pct"]["status"], "NO_DATA")
        self.assertFalse(r["ready"])

    def test_real_error_fails_b_and_d(self):
        lines = [
            L(OLD, "n", "capability:k", "satisfied", "ok", probe="synthetic"),
            L(RECENT, "n", "capability:k", "-", "error"),
        ]
        r = RSR.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["b_real_resolution_100pct"]["status"], "FAIL")
        self.assertEqual(r["criteria"]["d_zero_errors"]["status"], "FAIL")
        self.assertFalse(r["ready"])

    def test_synthetic_error_counts_toward_d_not_b(self):
        lines = [
            L(OLD, "n", "capability:k", "satisfied", "ok", probe="synthetic"),
            L(RECENT, "n", "capability:k", "-", "error", probe="synthetic"),
        ]
        r = RSR.analyze(lines, now=NOW)
        self.assertEqual(r["counts"]["real"], 0)
        self.assertEqual(r["criteria"]["b_real_resolution_100pct"]["status"], "NO_DATA")
        self.assertEqual(r["criteria"]["d_zero_errors"]["status"], "FAIL")

    def test_unknown_verdict_is_review_not_pass(self):
        lines = [
            L(OLD, "n", "capability:absent", "unknown", "ok", probe="synthetic"),
            L(RECENT, "n", "role:x", "satisfied", "ok"),
        ]
        r = RSR.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["c_vocabulary_v174_cold"]["status"], "REVIEW")
        self.assertFalse(r["ready"])
        self.assertEqual(len(r["criteria"]["c_vocabulary_v174_cold"]["unknown_lines"]), 1)

    def test_verdict_outside_v174_is_outright_fail(self):
        lines = [
            L(OLD, "n", "capability:k", "excellent", "ok", probe="synthetic"),
            L(RECENT, "n", "role:x", "satisfied", "ok"),
        ]
        r = RSR.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["c_vocabulary_v174_cold"]["status"], "FAIL")
        self.assertFalse(r["ready"])
        self.assertEqual(r["counts"]["outside_vocabulary"], 1)

    def test_vocabulary_judged_over_both_streams(self):
        # an outside-six verdict on a SYNTHETIC line still fails (c): the
        # classifyVerdict fallback firing anywhere is signal.
        lines = [
            L(OLD, "n", "capability:k", "bogus", "ok", probe="synthetic"),
        ]
        r = RSR.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["c_vocabulary_v174_cold"]["status"], "FAIL")


class TestFetchJournal(unittest.TestCase):
    def test_journalctl_timeout_is_tool_error_not_traceback(self):
        """A 60s journalctl timeout (huge journal, wide --since) must yield a
        clean (2, []) — the post-merge live run on the shared checkout hit
        exactly this and crashed with a raw TimeoutExpired traceback."""
        import subprocess as sp
        import unittest.mock as mock
        with mock.patch.object(
                RSR.subprocess, "run",
                side_effect=sp.TimeoutExpired(cmd=["journalctl"], timeout=60)):
            rc, lines = RSR.fetch_journal("wind-srv.service", "21 days ago")
        self.assertEqual(rc, 2)
        self.assertEqual(lines, [])

    def test_journalctl_missing_binary_is_tool_error(self):
        import unittest.mock as mock
        with mock.patch.object(
                RSR.subprocess, "run", side_effect=FileNotFoundError("journalctl")):
            rc, lines = RSR.fetch_journal("wind-srv.service", "21 days ago")
        self.assertEqual(rc, 2)
        self.assertEqual(lines, [])


class TestGateMode(unittest.TestCase):
    def _run_gate(self, lines):
        import unittest.mock as mock
        with mock.patch.object(RSR, "fetch_journal", return_value=(0, lines)), \
             mock.patch("sys.argv", ["resolver-soak-report.py", "--fail-not-ready"]):
            return RSR.main()

    def test_exit_1_when_not_ready(self):
        self.assertEqual(self._run_gate([]), 1)

    def test_exit_0_when_ready(self):
        lines = [
            L(OLD, "n", "capability:k", "satisfied", "ok", probe="synthetic"),
            L(RECENT, "n", "role:x", "satisfied", "ok"),
        ]
        self.assertEqual(self._run_gate(lines), 0)


class TestV174NoDrift(unittest.TestCase):
    def test_states_match_wind_srv_source(self):
        """The tool's V174 list must equal the resolver's SATISFACTION_STATES —
        parsed from the JS source so the two cannot drift silently."""
        src_path = os.path.join(
            REPO_ROOT, "typescript", "wind-srv", "src", "capability-resolver.js")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("SATISFACTION_STATES", src)
        # crude but effective: the array literal between the export and the
        # closing bracket.
        seg = src.split("SATISFACTION_STATES", 1)[1].split("[", 1)[1].split("]", 1)[0]
        js_states = [s.strip().strip("'\"") for s in seg.split(",") if s.strip()]
        self.assertEqual(RSR.V174_STATES, js_states)
        self.assertEqual(len(RSR.V174_STATES), 6)


if __name__ == "__main__":
    unittest.main()
