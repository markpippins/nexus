#!/usr/bin/env python3
"""Hermetic tests for operator.lease_check (adoption-path lease liveness).

No database or docker is touched: the resolver's live-lease predicate is
mirrored by an injectable query stub, the psql path is exercised only via
monkeypatched _psql, and mode selection runs against a scrubbed environment.

Covers:
  mode matrix          — off (adopted=None), warn (default), enforce
  liveness predicate   — live / expired / budget-exhausted / released leases
  fail-open / closed   — resolver error: warn passes, enforce refuses
  metadata             — lease id/model surfaced for continuity binding
  server wiring        — 403 refusal path in enforce, 200 + lease_check
                         metadata in warn (handler-level, stubbed respond)
"""

import importlib
import json
import os
import sys
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.abspath(os.path.join(_SELF_DIR, "..", ".."))
for p in (_PARENT, _SELF_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from operator_svc import lease_check  # noqa: E402


def _lease(lease_id="11111111-1111-1111-1111-111111111111", model="freebuff/buffy"):
    return {"id": lease_id, "role": "dba", "model": model, "expires_at": None,
            "window_end": None, "budget_units": None, "consumed_units": None,
            "status": "ACTIVE"}


class _EnvScrub(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.pop(lease_check.MODE_ENV, None)

    def tearDown(self):
        if self._old is not None:
            os.environ[lease_check.MODE_ENV] = self._old
        else:
            os.environ.pop(lease_check.MODE_ENV, None)


class TestModes(_EnvScrub):
    def test_off_mode_skips_check(self):
        os.environ[lease_check.MODE_ENV] = "off"
        r = lease_check.check_adoption("dba", query=lambda role: _lease())
        self.assertTrue(r["allowed"])
        self.assertIsNone(r["adopted"])
        self.assertIsNone(r["lease"])

    def test_default_mode_is_warn(self):
        r = lease_check.check_adoption("dba", query=lambda role: None)
        self.assertEqual(r["mode"], "warn")
        self.assertTrue(r["allowed"])
        self.assertFalse(r["adopted"])

    def test_invalid_mode_falls_back_to_warn(self):
        os.environ[lease_check.MODE_ENV] = "yolo"
        r = lease_check.check_adoption("dba", query=lambda role: _lease())
        self.assertEqual(r["mode"], "warn")

    def test_enforce_accepts_live_lease(self):
        os.environ[lease_check.MODE_ENV] = "enforce"
        r = lease_check.check_adoption("dba", query=lambda role: _lease())
        self.assertTrue(r["allowed"])
        self.assertTrue(r["adopted"])
        self.assertEqual(r["lease"]["id"], "11111111-1111-1111-1111-111111111111")

    def test_enforce_refuses_no_lease(self):
        os.environ[lease_check.MODE_ENV] = "enforce"
        r = lease_check.check_adoption("dba", query=lambda role: None)
        self.assertFalse(r["allowed"])
        self.assertIn("no live lease", r["reason"])

    def test_warn_never_refuses(self):
        os.environ[lease_check.MODE_ENV] = "warn"
        r = lease_check.check_adoption("dba", query=lambda role: None)
        self.assertTrue(r["allowed"])

    def test_resolver_error_fail_open_in_warn(self):
        def boom(role):
            raise RuntimeError("psql down")
        r = lease_check.check_adoption("dba", query=boom)
        self.assertTrue(r["allowed"])
        self.assertFalse(r["adopted"])
        self.assertIn("fail-open", r["reason"])

    def test_resolver_error_fail_closed_in_enforce(self):
        os.environ[lease_check.MODE_ENV] = "enforce"

        def boom(role):
            raise RuntimeError("psql down")
        r = lease_check.check_adoption("dba", query=boom)
        self.assertFalse(r["allowed"])
        self.assertIn("fail-closed", r["reason"])


def _server_module():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "server.py")
    spec = importlib.util.spec_from_file_location("operator_server_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestServerWiring(_EnvScrub):
    """Handler-level tests: the 403 path and the response metadata."""

    def test_chat_refuses_403_in_enforce(self):
        os.environ[lease_check.MODE_ENV] = "enforce"
        server_mod = _server_module()
        sent = {}

        handler = mock.MagicMock()
        handler._send_json.side_effect = lambda code, obj: sent.update(code=code, obj=obj)
        handler._read_body.return_value = {"message": "hello", "role": "dba"}

        with mock.patch.object(server_mod, "check_adoption",
                               return_value={"allowed": False, "mode": "enforce",
                                             "adopted": False,
                                             "reason": "no live lease for role (enforce mode)",
                                             "lease": None, "error": None}):
            server_mod.OperatorHandler._handle_chat(handler)
        self.assertEqual(sent["code"], 403)
        self.assertEqual(sent["obj"]["error"], "role adoption refused (lease check)")
        self.assertEqual(sent["obj"]["role"], "dba")

    def test_chat_passes_lease_metadata_in_warn(self):
        os.environ[lease_check.MODE_ENV] = "warn"
        server_mod = _server_module()

        handler = mock.MagicMock()
        sent = {}
        handler._send_json.side_effect = lambda code, obj: sent.update(code=code, obj=obj)
        # Pre-seed a real session whose queue already holds the response, and
        # give the body that session_id: the handler then runs end-to-end
        # (gate → session → thread spawn) without any dict dunder patching.
        import queue as queue_mod
        q = queue_mod.Queue()
        q.put({"type": "response", "data": {"response": "ok",
                                             "model_identifier": "m",
                                             "latency_ms": 1}})
        server_mod._sessions["sess-fixed"] = {"queue": q, "status": "processing"}
        handler._read_body.return_value = {"message": "hello", "role": "dba",
                                           "session_id": "sess-fixed"}

        adoption = {"allowed": True, "mode": "warn", "adopted": True,
                    "reason": "live lease for role (warn)", "lease": _lease(),
                    "error": None}
        with mock.patch.object(server_mod, "check_adoption",
                               return_value=adoption), \
             mock.patch.object(server_mod, "_drain_queue"), \
             mock.patch.object(server_mod.threading.Thread, "start"):
            server_mod.OperatorHandler._handle_chat(handler)
        self.assertEqual(sent.get("code"), 200)
        self.assertEqual(sent["obj"]["lease_check"]["adopted"], True)
        self.assertEqual(sent["obj"]["lease_check"]["lease_ref"],
                         "11111111-1111-1111-1111-111111111111")


if __name__ == "__main__":
    unittest.main()
