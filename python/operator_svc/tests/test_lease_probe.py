#!/usr/bin/env python3
"""Hermetic tests for the synthetic-probe additions (soak evidence, 42a11672).

Covers:
  check_adoption(probe=True)  — journal line gains probe=synthetic on all
                                four outcomes (adopted/unadopted/error/refused);
                                return-shape identical to non-probe calls
  probe_adoption()            — never raises, even when the underlying check
                                raises; classify-able outcome dict back
  /chat probe short-circuit   — probe=true returns 200 {probe, lease_check}
                                WITHOUT touching respond() or the session
                                machinery; 403 in enforce; body validation
                                errors still precede the short-circuit
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
from operator_svc import server as operator_server  # noqa: E402


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


class TestProbeLabeling(_EnvScrub):
    """check_adoption(probe=True): probe=synthetic rides every journal line."""

    def _capture(self, fn, *a, **kw):
        buf = []
        with mock.patch.object(lease_check._log, "warning",
                               side_effect=lambda m, *x: buf.append(m % x if x else m)), \
             mock.patch.object(lease_check._log, "info",
                               side_effect=lambda m, *x: buf.append(m % x if x else m)):
            result = fn(*a, **kw)
        return result, buf

    def test_probe_unadopted_line_has_marker(self):
        r, lines = self._capture(lease_check.check_adoption, "dba",
                                 query=lambda role: None, probe=True)
        self.assertFalse(r["adopted"])
        self.assertTrue(any("probe=synthetic" in l and "outcome=unadopted" in l for l in lines))

    def test_probe_adopted_line_has_marker(self):
        r, lines = self._capture(lease_check.check_adoption, "dba",
                                 query=lambda role: _lease(), probe=True)
        self.assertTrue(r["adopted"])
        self.assertTrue(any("probe=synthetic" in l and "outcome=adopted" in l for l in lines))

    def test_probe_error_line_has_marker(self):
        def boom(role):
            raise RuntimeError("psql down")
        r, lines = self._capture(lease_check.check_adoption, "dba",
                                 query=boom, probe=True)
        self.assertFalse(r["adopted"])
        self.assertIn("error", r)
        self.assertTrue(any("probe=synthetic" in l and "outcome=error" in l for l in lines))

    def test_probe_enforce_refusal_line_has_marker(self):
        os.environ[lease_check.MODE_ENV] = "enforce"
        r, lines = self._capture(lease_check.check_adoption, "dba",
                                 query=lambda role: None, probe=True)
        self.assertFalse(r["allowed"])
        self.assertTrue(any("probe=synthetic" in l and "outcome=refused" in l for l in lines))

    def test_non_probe_lines_are_unchanged(self):
        r, lines = self._capture(lease_check.check_adoption, "dba",
                                 query=lambda role: None)
        self.assertFalse(r["adopted"])
        self.assertTrue(any("outcome=unadopted" in l for l in lines))
        self.assertFalse(any("probe=synthetic" in l for l in lines))


class TestProbeAdoptionHelper(_EnvScrub):
    """probe_adoption: scheduled-runner wrapper, never raises."""

    def test_returns_unadopted(self):
        r = lease_check.probe_adoption("dba", query=lambda role: None)
        self.assertFalse(r["adopted"])
        self.assertTrue(r["allowed"])  # warn default

    def test_error_is_data_not_exception(self):
        def boom(role):
            raise RuntimeError("resolver exploded")
        r = lease_check.probe_adoption("dba", query=boom)
        self.assertFalse(r["adopted"])
        self.assertIn("error", r)
        self.assertTrue(r["allowed"])  # warn fail-open

    def test_enforce_error_is_refusal(self):
        os.environ[lease_check.MODE_ENV] = "enforce"
        r = lease_check.probe_adoption("dba", query=lambda role: None)
        self.assertFalse(r["allowed"])
        self.assertFalse(r["adopted"])


class TestServerProbeShortCircuit(unittest.TestCase):
    """Handler-level: probe=true returns without the LLM/session machinery."""

    def _handler(self, body, mode=None, adopt=None):
        old = None
        if mode is not None:
            old = os.environ.pop(lease_check.MODE_ENV, None)
            os.environ[lease_check.MODE_ENV] = mode
        sent = {}

        def fake_send(status, data):
            sent["status"], sent["data"] = status, data

        h = object.__new__(operator_server.OperatorHandler)
        if adopt is None:
            adopt = {"allowed": True, "mode": "warn", "adopted": False,
                     "reason": "no live lease for role (warn mode)",
                     "lease": None, "error": None}
        with mock.patch.object(operator_server.OperatorHandler, "_read_body",
                               return_value=body), \
             mock.patch.object(operator_server.OperatorHandler, "_send_json",
                               side_effect=fake_send), \
             mock.patch.object(operator_server, "check_adoption",
                               return_value=dict(adopt)) as m_adopt:
            h._handle_chat()
        if old is not None:
            os.environ[lease_check.MODE_ENV] = old
        return sent, m_adopt

    def test_probe_short_circuits_before_respond(self):
        body = {"probe": True, "role": "dba", "message": ""}
        with mock.patch.object(operator_server, "respond") as m_resp, \
             mock.patch.object(operator_server, "_get_or_create_session") as m_sess:
            sent, m_adopt = self._handler(body)
            m_resp.assert_not_called()
            m_sess.assert_not_called()
        self.assertEqual(sent["status"], 200)
        self.assertTrue(sent["data"]["probe"])
        self.assertIn("lease_check", sent["data"])
        self.assertEqual(sent["data"]["lease_check"]["adopted"], False)
        # probe=True must reach the resolver so the journal line is labeled
        self.assertTrue(m_adopt.call_args.kwargs.get("probe"))

    def test_probe_enforce_refusal_returns_403(self):
        body = {"probe": True, "role": "dba", "message": ""}
        refusal = {"allowed": False, "mode": "enforce", "adopted": False,
                   "reason": "no live lease for role (enforce mode)",
                   "lease": None, "error": None}
        sent, _ = self._handler(body, mode="enforce", adopt=refusal)
        self.assertEqual(sent["status"], 403)
        self.assertTrue(sent["data"]["probe"])
        self.assertIn("error", sent["data"])

    def test_probe_true_only_via_boolean(self):
        """probe:"true" (string) is NOT a probe — validation still applies."""
        body = {"probe": "true", "role": "dba", "message": ""}
        with mock.patch.object(operator_server, "respond") as m_resp:
            sent, _ = self._handler(body)
        # falls through to normal path: message required → 400
        self.assertEqual(sent["status"], 400)
        m_resp.assert_not_called()

    def test_non_probe_body_untouched(self):
        body = {"message": "hello", "role": "dba"}
        sess_row = {"id": "s1", "created_at": 0.0, "status": "active", "queue": None}
        operator_server._sessions["s1"] = sess_row
        self.addCleanup(operator_server._sessions.pop, "s1", None)
        with mock.patch.object(operator_server, "respond") as m_resp, \
             mock.patch.object(operator_server, "_get_or_create_session",
                               return_value="s1"), \
             mock.patch.object(operator_server, "_drain_queue"), \
             mock.patch("operator_svc.server.threading.Thread") as m_thread:
            m_thread.return_value.start = lambda: None
            with mock.patch.object(operator_server.OperatorHandler, "_send_json"):
                h = object.__new__(operator_server.OperatorHandler)
                with mock.patch.object(operator_server.OperatorHandler, "_read_body",
                                       return_value=body), \
                     mock.patch.object(operator_server, "check_adoption",
                                       return_value={"allowed": True, "mode": "warn",
                                                     "adopted": True,
                                                     "reason": "live lease for role (warn)",
                                                     "lease": {"id": "abc"}, "error": None}):
                    h._handle_chat()
        m_resp.assert_not_called()  # respond runs in thread; Thread patched


if __name__ == "__main__":
    unittest.main()
