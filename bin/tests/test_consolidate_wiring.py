#!/usr/bin/env python3
"""Hermetic tests for the dormant consolidation wiring.

Pins the contract from R1 ff20ce30 (thread a330914e):

Wrapper (bin/calendar-consolidate-run.py) exit normalization:
  observe 0 -> 0 ([consolidated]) · 3 -> 0 ([inert] — quiescent, NOT failure)
  1/4 -> 1 ([failed]) · wrapper injects --source default · timeout -> 1

Boot shim consolidate_step:
  default-on (plain Boot runs it) · --no-consolidate (want_consolidate=False)
  removes the step · inert detail -> status "skipped" (quiet, not degraded)
  ok detail -> "ok" · nonzero wrapper -> "degraded" · never raises ·
  dry-run -> [skip] · run() order pins calendar -> consolidate -> forums

No database, no network: the wrapper's observe subprocess is mocked at the
subprocess boundary; the boot shim's subprocess likewise.
"""
import contextlib
import importlib.util
import io
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", ".."))
RUNNER = os.path.join(_REPO, "bin", "calendar-consolidate-run.py")
BOOT = os.path.join(_REPO, "bin", "freebuff-boot.py")

_rspec = importlib.util.spec_from_file_location("ccrun", RUNNER)
ccrun = importlib.util.module_from_spec(_rspec)
_rspec.loader.exec_module(ccrun)

_bspec = importlib.util.spec_from_file_location(
    f"freebuff_boot_consolidate_{os.getpid()}", BOOT)
bootmod = importlib.util.module_from_spec(_bspec)
sys.modules[_bspec.name] = bootmod
_bspec.loader.exec_module(bootmod)


def run_wrapper(observed_exit, stdout="", stderr=""):
    """Run the wrapper with the observe subprocess mocked to a fixed result."""
    with mock.patch.object(ccrun.subprocess, "run") as m:
        m.return_value = mock.Mock(returncode=observed_exit,
                                   stdout=stdout, stderr=stderr)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = ccrun.main([])
        cmd = m.call_args[0][0]
    return rc, buf.getvalue(), cmd


class WrapperExitNormalization(unittest.TestCase):
    def test_ok_is_ok(self):
        rc, out, _ = run_wrapper(0, stdout='{"status": "consolidated"}')
        self.assertEqual(rc, 0)
        self.assertIn("[consolidated]", out)

    def test_inert_maps_to_zero_with_marker(self):
        """THE contract: gate closed -> exit 0 + [inert] marker (no unit noise)."""
        rc, out, _ = run_wrapper(3, stdout="INERT: vision.calendar_events ABSENT")
        self.assertEqual(rc, 0, "inert refusal must NOT fail the unit")
        self.assertIn("[inert]", out)
        self.assertNotIn("[failed]", out)

    def test_unreachable_maps_to_one(self):
        rc, _, _ = run_wrapper(1, stderr="store unreachable")
        self.assertEqual(rc, 1)

    def test_strict_invalid_maps_to_one(self):
        rc, _, _ = run_wrapper(4, stdout="invalid lines")
        self.assertEqual(rc, 1)

    def test_default_source_injected(self):
        _, _, cmd = run_wrapper(0, stdout="x")
        self.assertIn("--source", cmd)
        self.assertTrue(str(cmd[cmd.index("--source") + 1]).endswith("calendar.jsonl"))

    def test_explicit_source_respected(self):
        with mock.patch.object(ccrun.subprocess, "run") as m:
            m.return_value = mock.Mock(returncode=0, stdout="x", stderr="")
            ccrun.main(["--source", "/tmp/cal.jsonl"])
        cmd = m.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--source") + 1], "/tmp/cal.jsonl")


class BootConsolidateStep(unittest.TestCase):
    def _boot(self, **kw):
        defaults = dict(role="dba", model="freebuff/buffy", channel="interactive",
                        ttl=3600, budget=100, lease_policy="skip", update_pointer=False,
                        limit=5, dry_run=False, strict=False)
        defaults.update(kw)
        return bootmod.Boot(**defaults)

    def _run_step(self, b, returncode=0, stdout="[inert] x"):
        with mock.patch.object(bootmod, "subprocess") as msub:
            msub.run.return_value = mock.Mock(returncode=returncode,
                                              stdout=stdout, stderr="")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                b.consolidate_step()
        return [s for s in b.steps if s["step"] == "consolidate"]

    def test_default_on(self):
        b = self._boot()
        steps = self._run_step(b)
        self.assertEqual(len(steps), 1, "default Boot must run consolidate_step")
        self.assertEqual(steps[0]["status"], "skipped")  # inert -> skipped
        self.assertIn("[inert]", steps[0]["detail"])

    def test_no_consolidate_removes_step(self):
        b = self._boot(want_consolidate=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            b.consolidate_step()
        self.assertEqual([s for s in b.steps if s["step"] == "consolidate"], [])

    def test_consolidated_detail_is_ok(self):
        b = self._boot()
        steps = self._run_step(b, returncode=0,
                               stdout='{"status": "consolidated", "inserted": 400}')
        self.assertEqual(steps[0]["status"], "ok")

    def test_wrapper_failure_is_degraded_not_raise(self):
        b = self._boot()
        steps = self._run_step(b, returncode=1, stdout="store unreachable")
        self.assertEqual(steps[0]["status"], "degraded")

    def test_subprocess_exception_degraded_never_raises(self):
        b = self._boot()
        with mock.patch.object(bootmod, "subprocess") as msub:
            msub.run.side_effect = RuntimeError("boom")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                b.consolidate_step()
        self.assertEqual([s for s in b.steps if s["step"] == "consolidate"][0]["status"],
                         "degraded")

    def test_dry_run_skips(self):
        b = self._boot(dry_run=True)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            b.consolidate_step()
        step = [s for s in b.steps if s["step"] == "consolidate"][0]
        self.assertEqual(step["status"], "skipped")
        self.assertIn("zero-mutation", step["detail"])

    def test_run_order_calendar_then_consolidate_then_forums(self):
        b = self._boot(want_conn=False, want_digest=False,
                       want_attest_scan=False)
        order = []
        with mock.patch.object(b, "preflight", lambda: {"nebula-mcp": False}), \
             mock.patch.object(b, "lease", lambda: order.append("lease")), \
             mock.patch.object(b, "inbox", lambda: order.append("inbox")), \
             mock.patch.object(b, "clock_in", lambda: order.append("clock-in")), \
             mock.patch.object(b, "calendar_step", lambda: order.append("calendar")), \
             mock.patch.object(b, "consolidate_step",
                               lambda: order.append("consolidate")), \
             mock.patch.object(b, "forums", lambda: order.append("forums")), \
             mock.patch.object(b, "procedures", lambda: order.append("procs")), \
             mock.patch.object(b, "report", lambda: 0):
            b.run()
        # preflight down -> lease/inbox are degraded records, not calls;
        # the pinned order starts at clock-in
        self.assertEqual(order, ["clock-in", "calendar", "consolidate",
                                 "forums", "procs"])

    def test_argparse_flags_exist(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                bootmod.main(["--help"])
            except SystemExit:
                pass
        self.assertIn("--no-consolidate", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
