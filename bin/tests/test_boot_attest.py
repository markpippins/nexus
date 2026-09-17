#!/usr/bin/env python3
"""Boot-shim tests for the --attest / attest-scan wiring (wr-conf-034).

House pattern (test_boot_conn_record.py): build a real Boot instance, patch
its I/O steps, and pin the attest steps' dispositions directly. The scan
must never fail the boot; the recording path must never fire without
--evidence (G2 client gate); run() order is pinned.
"""

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF_DIR, "..", ".."))
SHIM = os.path.join(_REPO, "bin", "freebuff-boot.py")

_spec = importlib.util.spec_from_file_location("freebuff_boot_under_test", SHIM)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["freebuff_boot_under_test"] = _mod
_spec.loader.exec_module(_mod)


def _boot(**kw):
    defaults = dict(role="tester", model="m", channel="interactive", ttl=60,
                    budget=10, lease_policy="skip", update_pointer=False,
                    limit=1, dry_run=False, strict=False)
    defaults.update(kw)
    return _mod.Boot(**defaults)


def _fake_attest(scan_result=None, record_result=None, scan_exc=None):
    m = types.ModuleType("continuity.attest")
    if scan_exc:
        m.scan = mock.MagicMock(side_effect=scan_exc)
    else:
        m.scan = mock.MagicMock(return_value=scan_result or {"scanned": False, "reason": "x"})
    m.record_attestation = mock.MagicMock(return_value=record_result or {"recorded": False})
    return m


def _with_module(attest_mod):
    """Inject a fake continuity.attest into sys.modules for the duration."""
    saved_pkg = sys.modules.get("continuity")
    saved_mod = sys.modules.get("continuity.attest")
    fake_pkg = types.ModuleType("continuity")
    fake_pkg.__path__ = []
    sys.modules["continuity"] = fake_pkg
    sys.modules["continuity.attest"] = attest_mod
    return saved_pkg, saved_mod


def _restore_module(saved):
    saved_pkg, saved_mod = saved
    if saved_pkg is None:
        sys.modules.pop("continuity", None)
    else:
        sys.modules["continuity"] = saved_pkg
    if saved_mod is None:
        sys.modules.pop("continuity.attest", None)
    else:
        sys.modules["continuity.attest"] = saved_mod


class AttestScanTests(unittest.TestCase):
    def test_scan_ok_actionable_surfaced(self):
        am = _fake_attest(scan_result={"scanned": True, "open": [
            {"attestation_id": "aaaaaaaa-1111-1111-1111-111111111111",
             "disposition": "actionable", "note": "n"}],
            "actionable": 1, "reason": "1 open verification_request(s)"})
        saved = _with_module(am)
        try:
            b = _boot()
            with mock.patch.object(b, "record") as rec:
                b.attest_scan()
        finally:
            _restore_module(saved)
        am.scan.assert_called_once()
        role_arg, = [c.args[1] for c in am.scan.call_args_list] or [None]
        self.assertEqual(role_arg, "tester")
        rec.assert_called_once()
        name, status, detail = rec.call_args.args
        self.assertEqual((name, status), ("attest-scan", "ok"))
        self.assertIn("1 actionable", detail)

    def test_scan_skip_when_unavailable(self):
        am = _fake_attest(scan_result={"scanned": False, "reason": "V179 surface absent"})
        saved = _with_module(am)
        try:
            b = _boot()
            with mock.patch.object(b, "record") as rec:
                b.attest_scan()
        finally:
            _restore_module(saved)
        rec.assert_called_once_with("attest-scan", "skipped", "V179 surface absent")

    def test_scan_error_degrades_never_raises(self):
        am = _fake_attest(scan_exc=RuntimeError("db gone"))
        saved = _with_module(am)
        try:
            b = _boot()
            with mock.patch.object(b, "record") as rec:
                b.attest_scan()
        finally:
            _restore_module(saved)
        rec.assert_called_once()
        name, status, detail = rec.call_args.args
        self.assertEqual((name, status), ("attest-scan", "degraded"))

    def test_dry_run_skips_scan(self):
        am = _fake_attest()
        saved = _with_module(am)
        try:
            b = _boot(dry_run=True)
            with mock.patch.object(b, "record") as rec:
                b.attest_scan()
        finally:
            _restore_module(saved)
        am.scan.assert_not_called()
        rec.assert_called_once_with("attest-scan", "skipped",
                                         "dry-run: read-only scan not run")

    def test_no_attest_scan_flag_suppresses(self):
        am = _fake_attest()
        saved = _with_module(am)
        try:
            b = _boot(want_attest_scan=False)
            b.attest_scan()
        finally:
            _restore_module(saved)
        am.scan.assert_not_called()

    def test_run_order_places_scan_before_clock_in(self):
        b = _boot(want_attest_scan=False, want_conn=False, want_digest=False)
        order = []
        # preflight down -> lease/inbox both skipped by contract, so the
        # pinned order starts at digest; the attest steps sit between conn
        # record and clock-in.
        with mock.patch.object(b, "preflight", lambda: {"nebula-mcp": False}), \
             mock.patch.object(b, "lease", lambda: order.append("lease")), \
             mock.patch.object(b, "inbox", lambda: order.append("inbox")), \
             mock.patch.object(b, "digest_preview", lambda: order.append("digest")), \
             mock.patch.object(b, "connection_record", lambda: order.append("conn")), \
             mock.patch.object(b, "attest_scan", lambda: order.append("attest-scan")), \
             mock.patch.object(b, "attest_record", lambda: order.append("attest-record")), \
             mock.patch.object(b, "clock_in", lambda: order.append("clock-in")), \
             mock.patch.object(b, "forums", lambda: order.append("forums")), \
             mock.patch.object(b, "procedures", lambda: order.append("procs")), \
             mock.patch.object(b, "report", lambda: 0):
            b.run()
        self.assertEqual(order, ["digest", "conn", "attest-scan",
                                 "attest-record", "clock-in", "forums", "procs"])


class AttestRecordTests(unittest.TestCase):
    def test_record_success_reported_ok(self):
        am = _fake_attest(record_result={"recorded": True,
                                         "attestation_id": "bbbbbbbb-1",
                                         "txid": "17900001",
                                         "reason": "attestation recorded"})
        saved = _with_module(am)
        try:
            b = _boot()
            b.attest_cmd = ("c", ["run: x"], "sess-1")
            with mock.patch.object(b, "record") as rec:
                b.attest_record()
        finally:
            _restore_module(saved)
        am.record_attestation.assert_called_once()
        args = am.record_attestation.call_args
        self.assertEqual(args.args[1], "tester")
        self.assertEqual(args.args[2], "c")
        self.assertEqual(args.kwargs.get("session_id"), "sess-1")
        name, status, detail = rec.call_args.args
        self.assertEqual((name, status), ("attest-record", "ok"))

    def test_record_refusal_reported_failed(self):
        am = _fake_attest(record_result={"recorded": False,
                                         "reason": "G3 client-side: refused"})
        saved = _with_module(am)
        try:
            b = _boot()
            b.attest_cmd = ("c", ["run: x"], None)
            with mock.patch.object(b, "record") as rec:
                b.attest_record()
        finally:
            _restore_module(saved)
        name, status, detail = rec.call_args.args
        self.assertEqual((name, status), ("attest-record", "failed"))

    def test_no_cmd_is_silent(self):
        am = _fake_attest()
        saved = _with_module(am)
        try:
            b = _boot()
            b.attest_record()
        finally:
            _restore_module(saved)
        am.record_attestation.assert_not_called()


class ArgParsingTests(unittest.TestCase):
    def test_attest_without_evidence_refuses(self):
        with mock.patch.object(sys, "argv", ["freebuff-boot.py", "--role", "tester",
                                             "--attest", "c"]), \
             mock.patch.object(_mod.Boot, "run", lambda self: 0):
            code = _mod.main(["--role", "tester", "--attest", "c"])
        self.assertEqual(code, 2)

    def test_attest_with_evidence_wires_cmd(self):
        captured = {}
        real_boot = _mod.Boot

        class SpyBoot(real_boot):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                captured["boot"] = self

            def run(self):
                return 0

        with mock.patch.object(_mod, "Boot", SpyBoot):
            code = _mod.main(["--role", "tester", "--attest", "c",
                              "--evidence", "r1, r2", "--attest-session", "s9"])
        self.assertEqual(code, 0)
        self.assertEqual(captured["boot"].attest_cmd, ("c", ["r1", "r2"], "s9"))


if __name__ == "__main__":
    unittest.main()
