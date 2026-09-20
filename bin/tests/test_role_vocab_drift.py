#!/usr/bin/env python3
"""Hermetic tests for bin/role-vocab-drift.py (three-way parity comparator).

No psql, no filesystem: the three loaders are injected via monkeypatching
the module's load_* functions. Pins the contract:

  - green chain (all three equal) -> exit 0, pairs all True
  - any single-surface drift      -> exit 1, the drifted pair named
  - loader error (missing live)   -> exit 1 with the error surfaced
  - vocabulary normalization: '' excluded; order/case of lists irrelevant
"""
import contextlib
import importlib.util
import io
import os
import sys
import unittest

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "role-vocab-drift.py")

_spec = importlib.util.spec_from_file_location("rvd", _TOOL)
rvd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = rvd  # dataclass/py readability; exec below
_spec.loader.exec_module(rvd)

V24 = ["architect", "planner", "builder", "reviewer", "critic", "analyst",
       "inspector", "engineer", "engineer-ii", "devops", "topologist",
       "auditor", "dba", "epistemologist", "operator", "sysadmin", "DBA",
       "tester", "analyst-ii", "design-synthesist", "layout-mechanic",
       "ontologist", "lead-engineer", "sound-technician"]


def _region(roles):
    """A plausible constraint region for the _vocab extractor."""
    return "CHECK (((role = ''::text) OR (role = ANY (ARRAY[%s]))))" % (
        ", ".join("'%s'::text" % r for r in roles))


def _inject(live=None, pin=None, boot=None):
    def set_or_fail(name, value):
        if value is None:
            return lambda: (_ for _ in ()).throw(RuntimeError("%s unavailable" % name))
        return lambda: value

    orig = (rvd.load_live, rvd.load_pin, rvd.load_boot)
    rvd.load_live = set_or_fail("LIVE", live and _region(live))
    rvd.load_pin = set_or_fail("PIN", pin and _region(pin))
    rvd.load_boot = set_or_fail("BOOT", boot and _region(boot))
    return orig


def _restore(orig):
    rvd.load_live, rvd.load_pin, rvd.load_boot = orig


def _run_main(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = rvd.main(argv)
    return rc, buf.getvalue()


class Comparator(unittest.TestCase):
    def tearDown(self):
        pass

    def test_green_chain_exit0(self):
        orig = _inject(live=V24, pin=V24, boot=V24)
        try:
            rc, out = _run_main([])
        finally:
            _restore(orig)
        self.assertEqual(rc, 0)
        self.assertIn("LIVE==PIN  OK", out)
        self.assertIn("PIN==BOOT  OK", out)
        self.assertIn("Chain green", out)

    def test_bootstrap_drift_exit1(self):
        stale = [r for r in V24
                 if r not in ("ontologist", "lead-engineer", "sound-technician")]
        orig = _inject(live=V24, pin=V24, boot=stale)
        try:
            rc, out = _run_main([])
        finally:
            _restore(orig)
        self.assertEqual(rc, 1)
        self.assertIn("PIN==BOOT  DRIFT", out)
        self.assertIn("LIVE==PIN  OK", out)

    def test_live_drift_exit1(self):
        stale = [r for r in V24 if r != "ontologist"]
        orig = _inject(live=stale, pin=V24, boot=V24)
        try:
            rc, out = _run_main([])
        finally:
            _restore(orig)
        self.assertEqual(rc, 1)
        self.assertIn("LIVE==PIN  DRIFT", out)
        self.assertIn("PIN==BOOT  OK", out)

    def test_missing_live_is_drift_not_crash(self):
        orig = _inject(live=None, pin=V24, boot=V24)
        try:
            rc, out = _run_main([])
        finally:
            _restore(orig)
        self.assertEqual(rc, 1)
        self.assertIn("ERROR", out)
        self.assertIn("LIVE", out)

    def test_vocab_normalization(self):
        """'' structural literal and ordering never affect the verdict."""
        orig = _inject(live=sorted(V24, reverse=True), pin=V24, boot=V24)
        try:
            rc, _ = _run_main([])
        finally:
            _restore(orig)
        self.assertEqual(rc, 0)


class VocabExtractor(unittest.TestCase):
    def test_empty_escape_excluded(self):
        self.assertEqual(rvd._vocab(_region(["a", "b"])), ["a", "b"])

    def test_duplicates_collapse(self):
        self.assertEqual(rvd._vocab(_region(["a", "a", "b"])), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
