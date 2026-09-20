#!/usr/bin/env python3
"""Boot-shim tests for the --conn-record step (V169 affordance census).

Mirrors test_boot_digest.py conventions: the shim is loaded by file path,
fake continuity modules are injected into sys.modules, and every step
status is pinned. The census must never fail the boot.
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import types
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))

_spec = importlib.util.spec_from_file_location(
    "freebuff_boot", os.path.join(_SELF_DIR, "..", "freebuff-boot.py"))
freebuff_boot = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freebuff_boot)

Boot = freebuff_boot.Boot


def _install_fake_census(census=None, record=None, import_error=None):
    """Inject a fake continuity.census module; returns a cleanup callable.

    The captured dict records collect_census kwargs and record_connection
    args for wiring assertions.
    """
    captured = {"collect_kwargs": None, "record_arg": None}
    pkg = types.ModuleType("continuity")
    pkg.__path__ = []  # mark as package (preserves any real submodules)
    mod = types.ModuleType("continuity.census")

    if import_error is not None:
        def _raise(*a, **k):
            raise import_error
        mod.collect_census = _raise
        mod.record_connection = _raise
    else:
        def _collect(*args, **kwargs):
            captured["collect_kwargs"] = {**dict(zip(
                ("role", "model", "channel"), args)), **kwargs}
            return (census or {"session_id": None, "role": "dba", "model": "m",
                               "channel": "interactive", "lease_ref": None,
                               "mcp_tools": [], "procedure_cards": {},
                               "inbox_status": {}, "handoff_context": {},
                               "keychains": {"available": False}})
        mod.collect_census = _collect

        def _record(row):
            captured["record_arg"] = row
            return (record or {"recorded": True, "reason": "ok",
                               "conn_id": "12345678-1234-1234-1234-123456789012"})
        mod.record_connection = _record

    sys.modules["continuity"] = pkg
    sys.modules["continuity.census"] = mod
    mod._captured = captured

    def cleanup():
        sys.modules.pop("continuity.census", None)
        sys.modules.pop("continuity", None)
    return cleanup


def _boot(**kw):
    defaults = dict(role="dba", model="freebuff/buffy", channel="interactive",
                    ttl=14400, budget=100, lease_policy="auto",
                    update_pointer=False, limit=5, dry_run=False, strict=False)
    defaults.update(kw)
    return Boot(**defaults)


class TestFlagPlumbing(unittest.TestCase):
    def test_conn_flag_reaches_boot(self):
        with mock.patch.object(freebuff_boot, "Boot") as B:
            B.return_value.run.return_value = 0
            freebuff_boot.main(["--role", "dba", "--conn-record"])
            self.assertIs(B.call_args.kwargs.get("want_conn"), True)

    def test_default_is_off(self):
        with mock.patch.object(freebuff_boot, "Boot") as B:
            B.return_value.run.return_value = 0
            freebuff_boot.main(["--role", "dba"])
            self.assertIs(B.call_args.kwargs.get("want_conn"), False)


class TestConnRecordStep(unittest.TestCase):
    def test_no_flag_no_step(self):
        b = _boot()
        b.connection_record()
        self.assertEqual([s["step"] for s in b.steps if s["step"] == "conn-record"], [])

    def test_ok_step_records_with_conn_id(self):
        cleanup = _install_fake_census()
        try:
            b = _boot(want_conn=True)
            b.lease_id = "aaaaaaaa-1111-2222-3333-444444444444"
            b.digest_summary = {"counts": {"open_inbox": 1}}
            b.connection_record()
            step = next(s for s in b.steps if s["step"] == "conn-record")
            self.assertEqual(step["status"], "ok")
            self.assertIn("12345678", step["detail"])
            # wiring: census received the captured lease + digest
            kw = sys.modules["continuity.census"]._captured["collect_kwargs"]
            self.assertEqual(kw["lease_ref"], "aaaaaaaa-1111-2222-3333-444444444444")
            self.assertEqual(kw["digest_result"], b.digest_summary)
            self.assertEqual(kw["role"], "dba")
            self.assertEqual(kw["session_id"], os.environ.get("FREEBUFF_SESSION_ID"))
        finally:
            cleanup()

    def test_inert_result_is_skip_not_failure(self):
        cleanup = _install_fake_census(
            record={"recorded": False,
                    "reason": "V169 pre-stage not applied — inert gate",
                    "conn_id": None})
        try:
            b = _boot(want_conn=True)
            b.connection_record()
            step = next(s for s in b.steps if s["step"] == "conn-record")
            self.assertEqual(step["status"], "skipped")
            self.assertIn("inert gate", step["detail"])
        finally:
            cleanup()

    def test_import_absence_is_skip(self):
        # Block the real continuity package the way test_boot_digest does:
        # sys.modules entries set to None make "import continuity.census" raise.
        saved = {k: sys.modules.get(k) for k in ("continuity", "continuity.census")}
        sys.modules["continuity"] = None
        sys.modules["continuity.census"] = None
        try:
            b = _boot(want_conn=True)
            b.connection_record()
            step = next(s for s in b.steps if s["step"] == "conn-record")
            self.assertEqual(step["status"], "skipped")
            self.assertIn("not importable", step["detail"])
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v

    def test_census_exception_is_degraded_boot_continues(self):
        cleanup = _install_fake_census(import_error=RuntimeError("probe exploded"))
        try:
            b = _boot(want_conn=True)
            b.connection_record()
            step = next(s for s in b.steps if s["step"] == "conn-record")
            self.assertEqual(step["status"], "degraded")
            self.assertIn("probe exploded", step["detail"])
        finally:
            cleanup()

    def test_dry_run_skips(self):
        cleanup = _install_fake_census()
        try:
            b = _boot(want_conn=True, dry_run=True)
            b.connection_record()
            step = next(s for s in b.steps if s["step"] == "conn-record")
            self.assertEqual(step["status"], "skipped")
            self.assertIn("dry-run", step["detail"])
            self.assertIsNone(
                sys.modules["continuity.census"]._captured["collect_kwargs"])
        finally:
            cleanup()


class TestRunOrder(unittest.TestCase):
    def test_run_sequence_places_conn_record_after_digest_before_clockin(self):
        order = []
        b = _boot(want_conn=True)
        with mock.patch.object(b, "preflight", lambda: {"nebula-mcp": True}), \
             mock.patch.object(b, "lease", lambda: order.append("lease")), \
             mock.patch.object(b, "inbox", lambda: order.append("inbox")), \
             mock.patch.object(b, "digest_preview",
                               lambda: order.append("digest")), \
             mock.patch.object(b, "connection_record",
                               lambda: order.append("conn-record")), \
             mock.patch.object(b, "calendar_step",
                               lambda: order.append("calendar")), \
             mock.patch.object(b, "consolidate_step",
                               lambda: order.append("consolidate")), \
             mock.patch.object(b, "clock_in", lambda: order.append("clock-in")), \
             mock.patch.object(b, "forums", lambda: order.append("forums")), \
             mock.patch.object(b, "procedures", lambda: order.append("procedures")), \
             mock.patch.object(b, "report", lambda: 0):
            code = b.run()
        self.assertEqual(code, 0)
        self.assertEqual(order, ["lease", "inbox", "digest", "conn-record",
                                 "clock-in", "calendar", "consolidate",
                                 "forums", "procedures"])


if __name__ == "__main__":
    unittest.main()
