#!/usr/bin/env python3
"""Hermetic tests for bin/lease-probe.py (daily synthetic lease-check probe).

No sockets, no docker, no database: HTTP is injectable, role discovery is
monkeypatched. Pins the soak-evidence semantics — the runner always exits 0
on completed runs (probe failures are data), degrades to [operator] when
role discovery fails, and honors LEASE_PROBE_ROLES.

Covers:
  resolve_roles     — env wins / live discovery / fallback on failure
  probe_role        — outcome row from a 200 /chat probe response,
                      error-shaped row from transport failure and 403
  run/main          — all-probe-fail still exits 0; --print emits rows
"""

import importlib.util
import json
import os
import sys
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
for p in (_SELF_DIR, os.path.abspath(os.path.join(_SELF_DIR, ".."))):
    if p not in sys.path:
        sys.path.insert(0, p)

_spec = importlib.util.spec_from_file_location(
    "lease_probe", os.path.join(_SELF_DIR, "..", "lease-probe.py"))
lease_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lease_probe)


class TestResolveRoles(unittest.TestCase):
    def test_env_wins(self):
        with mock.patch.dict(os.environ, {"LEASE_PROBE_ROLES": "dba, planner"}):
            self.assertEqual(lease_probe.resolve_roles(), ["dba", "planner"])

    def test_env_single(self):
        with mock.patch.dict(os.environ, {"LEASE_PROBE_ROLES": "engineer"}):
            self.assertEqual(lease_probe.resolve_roles(), ["engineer"])

    def test_fallback_on_discovery_failure(self):
        with mock.patch.dict(os.environ, {"LEASE_PROBE_ROLES": ""}, clear=False):
            os.environ.pop("LEASE_PROBE_ROLES", None)
            with mock.patch.object(lease_probe, "_discover_roles",
                                   side_effect=RuntimeError("docker down")):
                self.assertEqual(lease_probe.resolve_roles(), ["operator"])

    def test_discovery_includes_operator(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEASE_PROBE_ROLES", None)
            with mock.patch.object(lease_probe, "_discover_roles",
                                   return_value=["dba", "operator", "planner"]):
                self.assertEqual(lease_probe.resolve_roles(), ["dba", "operator", "planner"])


class TestProbeRole(unittest.TestCase):
    def test_outcome_from_200(self):
        body = {"probe": True, "role": "dba",
                "lease_check": {"mode": "warn", "adopted": False,
                                "lease_ref": None,
                                "reason": "no live lease for role (warn mode)"}}
        with mock.patch.object(lease_probe, "_http_post",
                               return_value=(200, body)):
            row = lease_probe.probe_role("dba")
        self.assertEqual(row["http_status"], 200)
        self.assertEqual(row["adopted"], False)
        self.assertEqual(row["mode"], "warn")
        self.assertIsNone(row["lease_ref"])

    def test_transport_failure_is_error_row(self):
        with mock.patch.object(lease_probe, "_http_post",
                               return_value=(0, {"error": "conn refused"})):
            row = lease_probe.probe_role("dba")
        self.assertEqual(row["http_status"], 0)
        self.assertIn("conn refused", row["reason"] or "")

    def test_403_refusal_is_recorded(self):
        body = {"probe": True, "role": "dba",
                "error": "role adoption refused (lease check)",
                "lease_check": {"mode": "enforce", "adopted": False,
                                "lease_ref": None,
                                "reason": "no live lease for role (enforce mode)"}}
        with mock.patch.object(lease_probe, "_http_post",
                               return_value=(403, body)):
            row = lease_probe.probe_role("dba")
        self.assertEqual(row["http_status"], 403)
        self.assertEqual(row["mode"], "enforce")
        self.assertFalse(row["adopted"])

    def test_probe_never_raises(self):
        with mock.patch.object(lease_probe, "_http_post",
                               side_effect=Exception("should not leak")):
            row = lease_probe.probe_role("dba")
        self.assertEqual(row["http_status"], 0)
        self.assertIn("should not leak", row["reason"] or "")


class TestMain(unittest.TestCase):
    def test_all_probes_fail_still_exit_zero(self):
        rows = [{"role": "dba", "http_status": 0, "probe": True,
                 "mode": None, "adopted": False, "lease_ref": None,
                 "reason": "conn refused"}]
        with mock.patch.object(lease_probe, "resolve_roles",
                               return_value=["dba"]), \
             mock.patch.object(lease_probe, "run", return_value=rows), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LEASE_PROBE_ROLES", None)
            rc = lease_probe.main()
        self.assertEqual(rc, 0)

    def test_print_emits_rows(self):
        rows = [{"role": "dba", "http_status": 200, "probe": True,
                 "mode": "warn", "adopted": False, "lease_ref": None,
                 "reason": "no live lease for role (warn mode)"}]
        buf = []
        with mock.patch.object(lease_probe, "resolve_roles",
                               return_value=["dba"]), \
             mock.patch.object(lease_probe, "run", return_value=rows), \
             mock.patch.object(sys, "argv", ["lease-probe.py", "--print"]):
            with mock.patch.object(sys, "stdout") as m_out:
                m_out.write = lambda s: buf.append(s)
                m_out.flush = lambda: None
                rc = lease_probe.main()
        self.assertEqual(rc, 0)
        joined = "".join(buf)
        self.assertIn('"role": "dba"', joined)

    def test_no_roles_is_clean_zero(self):
        with mock.patch.object(lease_probe, "resolve_roles", return_value=[]):
            rc = lease_probe.main()
        self.assertEqual(rc, 0)

    def test_help(self):
        with mock.patch.object(sys, "argv", ["lease-probe.py", "--help"]):
            rc = lease_probe.main()
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
