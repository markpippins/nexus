#!/usr/bin/env python3
"""Hermetic tests for bin/lease-soak-report.py (enforce-flip readiness report).

No journal, no subprocess: lines are fed directly to the pure analysis core.
Pins the honest-separation doctrine — synthetic (probe=synthetic) never counts
toward real-adoption evidence — and the four mechanical criteria from the
soak review (42a11672).
"""

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "lease_soak_report", os.path.join(_SELF_DIR, "..", "lease-soak-report.py"))
lsr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lsr)

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _line(ts, role, outcome, probe=None, lease_ref=None, error=None, mode="warn"):
    ts_s = ts.isoformat()
    parts = [ts_s, "titanium", "python3[1]:", "lease-check",
             f"role={role}", f"mode={mode}", f"outcome={outcome}"]
    if lease_ref:
        parts.append(f"lease_ref={lease_ref}")
    if probe:
        parts.append(f"probe={probe}")
    if error:
        parts.append(f"error={error}")
    return " ".join(parts)


def _ago(days, hours=0):
    return NOW - timedelta(days=days, hours=hours)


class TestParse(unittest.TestCase):
    def test_real_line(self):
        p = lsr.parse_lease_check_line(_line(_ago(1), "dba", "unadopted"))
        self.assertEqual(p["role"], "dba")
        self.assertEqual(p["outcome"], "unadopted")
        self.assertIsNone(p["probe"])  # real

    def test_synthetic_line(self):
        p = lsr.parse_lease_check_line(_line(_ago(1), "dba", "unadopted", probe="synthetic"))
        self.assertEqual(p["probe"], "synthetic")

    def test_adopted_line_carries_lease_ref(self):
        p = lsr.parse_lease_check_line(
            _line(_ago(1), "operator", "adopted", lease_ref="abc-123"))
        self.assertEqual(p["lease_ref"], "abc-123")

    def test_error_line(self):
        p = lsr.parse_lease_check_line(_line(_ago(1), "dba", "error", error="'psql'"))
        self.assertEqual(p["outcome"], "error")

    def test_non_lease_line_ignored(self):
        self.assertIsNone(lsr.parse_lease_check_line("Sep 16 12:00:00 ti something[1]: other message"))
        self.assertIsNone(lsr.parse_lease_check_line("lease-check garbage"))
        self.assertIsNone(lsr.parse_lease_check_line(""))


class TestCriteria(unittest.TestCase):
    def test_empty_journal(self):
        r = lsr.analyze([], now=NOW)
        self.assertFalse(r["ready"])
        self.assertEqual(r["verdict"], "NOT_READY")
        self.assertEqual(r["criteria"]["b_real_adoption_100pct"]["status"], "NO_DATA")

    def test_young_window_pending(self):
        lines = [_line(_ago(2), "dba", "adopted", lease_ref="l1"),
                 _line(_ago(1), "dba", "adopted", lease_ref="l1")]
        r = lsr.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["a_window_ge_14d"]["status"], "PENDING")
        self.assertFalse(r["ready"])

    def test_mature_window_all_real_adopted_is_ready(self):
        lines = [_line(_ago(20), "dba", "adopted", lease_ref="l1"),
                 _line(_ago(15), "operator", "adopted", lease_ref="l2"),
                 _line(_ago(10), "dba", "adopted", lease_ref="l1")]
        r = lsr.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["a_window_ge_14d"]["status"], "PASS")
        self.assertEqual(r["criteria"]["b_real_adoption_100pct"]["status"], "PASS")
        self.assertEqual(r["criteria"]["c_unadopted_attributed"]["status"], "PASS")
        self.assertEqual(r["criteria"]["d_zero_resolver_errors"]["status"], "PASS")
        self.assertTrue(r["ready"])
        self.assertEqual(r["verdict"], "READY")

    def test_synthetic_never_counts_toward_adoption(self):
        """The core doctrine: synthetic unadopted ≠ real unadopted."""
        lines = [_line(_ago(20), "dba", "adopted", lease_ref="l1"),
                 _line(_ago(10), "dba", "unadopted", probe="synthetic"),
                 _line(_ago(9), "planner", "unadopted", probe="synthetic")]
        r = lsr.analyze(lines, now=NOW)
        self.assertEqual(r["counts"]["synthetic"], 2)
        self.assertEqual(r["counts"]["real_unadopted"], 0)   # excluded
        self.assertEqual(r["criteria"]["b_real_adoption_100pct"]["status"], "PASS")
        self.assertEqual(r["criteria"]["c_unadopted_attributed"]["status"], "PASS")
        self.assertTrue(r["ready"])  # synthetic unadopted does NOT block

    def test_real_unadopted_requires_attribution(self):
        lines = [_line(_ago(20), "dba", "adopted", lease_ref="l1"),
                 _line(_ago(5), "engineer", "unadopted")]
        r = lsr.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["b_real_adoption_100pct"]["status"], "FAIL")
        self.assertEqual(r["criteria"]["c_unadopted_attributed"]["status"], "REVIEW")
        self.assertIn("engineer", r["criteria"]["c_unadopted_attributed"]["unadopted_lines"][0])
        self.assertFalse(r["ready"])

    def test_resolver_errors_block(self):
        lines = [_line(_ago(20), "dba", "adopted", lease_ref="l1"),
                 _line(_ago(3), "dba", "error", error="'boom'")]
        r = lsr.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["d_zero_resolver_errors"]["status"], "FAIL")
        self.assertFalse(r["ready"])

    def test_synthetic_error_also_blocks(self):
        """Errors are gate-health, not adoption — both streams count."""
        lines = [_line(_ago(20), "dba", "adopted", lease_ref="l1"),
                 _line(_ago(3), "dba", "error", error="'x'", probe="synthetic")]
        r = lsr.analyze(lines, now=NOW)
        self.assertEqual(r["criteria"]["d_zero_resolver_errors"]["status"], "FAIL")
        self.assertFalse(r["ready"])

    def test_mixed_streams_counted_separately(self):
        lines = [
            _line(_ago(20), "dba", "adopted", lease_ref="l1"),
            _line(_ago(19), "dba", "unadopted"),                    # real unadopted
            _line(_ago(18), "dba", "unadopted", probe="synthetic"), # synthetic
            _line(_ago(17), "operator", "adopted", lease_ref="l2"),
        ]
        r = lsr.analyze(lines, now=NOW)
        self.assertEqual(r["counts"]["real"], 3)
        self.assertEqual(r["counts"]["synthetic"], 1)
        self.assertEqual(r["counts"]["real_adopted"], 2)
        self.assertEqual(r["counts"]["real_unadopted"], 1)
        self.assertEqual(r["real_by_role"], {"dba": 2, "operator": 1})
        self.assertEqual(r["synthetic_by_role"], {"dba": 1})


class TestRender(unittest.TestCase):
    def test_render_includes_all_criteria_and_unadopted_lines(self):
        lines = [_line(_ago(20), "dba", "adopted", lease_ref="l1"),
                 _line(_ago(5), "engineer", "unadopted")]
        r = lsr.analyze(lines, now=NOW)
        text = lsr.render(r)
        self.assertIn("(a) window", text)
        self.assertIn("(b) real adoption", text)
        self.assertIn("synthetic excluded", text)
        self.assertIn("(d) zero resolver errors", text)
        self.assertIn("VERDICT: NOT_READY", text)
        self.assertIn("unadopted real:", text)


if __name__ == "__main__":
    unittest.main()
