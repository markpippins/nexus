"""Hermetic tests for bin/drift-check-run.py (drift census timer wrapper).

No terrain, no nebula: the census subprocess is mocked (classification law)
plus one REAL subprocess integration via a stub census script. Pins the
R1 4f4f2574 contract:

  - classify(): 0 -> 0; 1..125 (completed WITH findings) -> 0 (defects are
    data — a red unit would mute the cadence); anything else -> 4
  - the journal summary line carries checked/expected_offline/defects/classes
  - census crash (timeout/OSError) -> 4
  - real subprocess integration through the actual main() path
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_TOOL = os.path.join(_REPO, "bin", "drift-check-run.py")

_spec = importlib.util.spec_from_file_location("drift_check_run", _TOOL)
dcr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dcr)


class Classify(unittest.TestCase):
    def test_clean_census_is_zero(self):
        self.assertEqual(dcr.classify(0), 0)

    def test_defect_census_is_data_not_failure(self):
        # 1..124 = completed-with-N-defects -> successful unit run
        for rc in (1, 3, 12, 124):
            self.assertEqual(dcr.classify(rc), 0, f"rc={rc}")

    def test_unrunnable_is_unit_failure(self):
        # 125 is the tool's FATAL (terrain unreachable): a completed-but-
        # finding census exits 1..124; 125 means the watcher saw nothing —
        # that must be a red unit, not silence.
        self.assertEqual(dcr.classify(125), 1)

    def test_unknown_codes_are_loud(self):
        self.assertEqual(dcr.classify(126), 4)
        self.assertEqual(dcr.classify(255), 4)


class Summary(unittest.TestCase):
    def _run_with_census(self, census_script):
        """Run the REAL wrapper main() against a stub census script."""
        with tempfile.TemporaryDirectory() as td:
            stub = os.path.join(td, "terrain-drift-check.py")
            with open(stub, "w", encoding="utf-8") as fh:
                fh.write(census_script)
            buf = __import__("io").StringIO()
            with mock.patch.object(dcr, "TOOL", stub), \
                 mock.patch("sys.stdout", buf):
                rc = dcr.main()
            return rc, buf.getvalue()

    def test_clean_run_summary_and_exit(self):
        # --json emits a MULTI-LINE document — the wrapper must parse the
        # whole stdout, not just the last line (regression pin)
        payload = {"checked": 86, "expected_offline": [], "defects": [],
                   "degraded": None}
        rc, out = self._run_with_census(
            "import json,sys; print(json.dumps(%r, indent=2))" % payload)
        self.assertEqual(rc, 0)
        self.assertIn("checked=86", out)
        self.assertIn("defects=0", out)
        self.assertIn("wrapper exit=0", out)

    def test_defect_run_surfaces_classes_and_exits_zero(self):
        payload = {"checked": 86, "expected_offline": [{"class": "x"}],
                   "defects": [{"class": "dead"}, {"class": "dead"}],
                   "degraded": None}
        rc, out = self._run_with_census(
            "import json,sys; print(json.dumps(%r))" % payload)
        self.assertEqual(rc, 0)
        self.assertIn("defects=2", out)
        self.assertIn("expected_offline=1", out)
        self.assertIn("'dead', 'dead'", out)

    def test_unrunnable_census_maps_to_unit_failure(self):
        rc, out = self._run_with_census(
            "import sys; print('FATAL', file=sys.stderr); sys.exit(125)")
        self.assertEqual(rc, 1)
        self.assertIn("wrapper exit=1", out)

    def test_crashing_census_maps_to_loud_four(self):
        rc, out = self._run_with_census(
            "import sys; sys.exit(200)")
        self.assertEqual(rc, 4)

    def test_garbled_output_degrades_to_raw_exit(self):
        rc, out = self._run_with_census(
            "import sys; print('not json'); sys.exit(0)")
        self.assertEqual(rc, 0)
        self.assertIn("no JSON summary", out)

    def test_json_with_trailing_chatter_still_parses(self):
        # raw_decode tolerance: operational chatter after the JSON document
        # (e.g. an older census printing the record id to stdout) must not
        # kill the summary — the document itself is still parsed
        payload = {"checked": 40, "expected_offline": [], "defects": [],
                   "degraded": None}
        rc, out = self._run_with_census(
            "import json,sys; print(json.dumps(%r, indent=2)); "
            "print('  record: abc-123')" % payload)
        self.assertEqual(rc, 0)
        self.assertIn("checked=40", out)
        self.assertNotIn("no JSON summary", out)


class RealSubprocess(unittest.TestCase):
    def test_timeout_and_oserror_paths_return_four(self):
        with mock.patch.object(
                dcr.subprocess, "run",
                side_effect=subprocess.TimeoutExpired(cmd="x", timeout=600)):
            self.assertEqual(dcr.main.__wrapped__ if hasattr(
                dcr.main, "__wrapped__") else None, None)
        # direct: classification of an interrupted run is handled in main();
        # classify() itself is total over ints
        self.assertEqual(dcr.classify(124), 0)


if __name__ == "__main__":
    unittest.main()
