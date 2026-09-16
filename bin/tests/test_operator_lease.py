#!/usr/bin/env python3
"""Hermetic tests for bin/operator-lease.py (standing operator lease).

All HTTP is faked via an injectable urlopen-compatible callable; no real
service is touched. Pins the enforce-flip prerequisite contract:

  AC1 ensure issues when no live lease
  AC2 ensure renews when a live lease exists
  AC3 expired leases do not count as live (issue path)
  AC4 409 race on issue converges to renewing the existing lease
  AC5 release revokes the live lease; noop when none
  AC6 status reports live/no-lease correctly
  AC7 degraded outcomes (HTTP errors, transport errors) are non-fatal
  AC8 CLI: ensure exits 0 even on degraded (fleet start must not fail)
"""

import importlib.util
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_module():
    """Load bin/operator-lease.py by path (hyphenated name, not importable)."""
    import importlib.util
    path = os.path.join(_SELF_DIR, "..", "operator-lease.py")
    spec = importlib.util.spec_from_file_location("operator_lease_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ol = _load_module()


def _resp(status, body):
    if status >= 400:
        raise urllib.error.HTTPError(url="x", code=status, msg="e",
                                     hdrs=None, fp=BodyIO(json.dumps(body).encode()))
    return Ctx(json.dumps(body).encode(), status)


class BodyIO:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def close(self):
        pass


class Ctx:
    def __init__(self, data, status):
        self.data, self.status = data, status

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _future_iso(hours=1):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


LEASE = {"id": "lease-1", "role": "operator", "channel": "ui-fleet",
         "status": "ACTIVE", "expires_at": _future_iso(1)}


def _routes(routes):
    """routes: dict (method, url-fragment) -> (status, body)."""
    def do_open(req, timeout=None):
        frag = req.get_method(), req.full_url
        for (method, needle), (status, body) in routes.items():
            if method == req.get_method() and needle in req.full_url:
                return _resp(status, body)
        raise AssertionError(f"unexpected {frag}")
    return do_open


class TestOperatorLease(unittest.TestCase):
    def test_ac1_ensure_issues_when_no_live_lease(self):
        calls = []
        routes = _routes({
            ("GET", "/api/role-leases?role=operator"): (200, {"items": []}),
            ("POST", "/api/role-leases/issue"): (201, dict(LEASE, id="new-lease")),
        })
        do_open = lambda req, timeout=None: calls.append(req.full_url) or routes(req, timeout)
        out = ol.ensure(do_open=do_open)
        self.assertEqual(out["action"], "issued")
        self.assertEqual(out["lease_id"], "new-lease")

    def test_ac2_ensure_renews_when_live(self):
        routes = _routes({
            ("GET", "/api/role-leases?role=operator"): (200, {"items": [LEASE]}),
            ("POST", "/api/role-leases/lease-1/renew"): (200, dict(LEASE, expires_at="2026-09-16T14:00:00Z")),
        })
        out = ol.ensure(do_open=routes)
        self.assertEqual(out["action"], "renewed")
        self.assertEqual(out["lease_id"], "lease-1")

    def test_ac3_expired_lease_not_live(self):
        expired = dict(LEASE, expires_at="2020-01-01T00:00:00Z")
        routes = _routes({
            ("GET", "/api/role-leases?role=operator"): (200, {"items": [expired]}),
            ("POST", "/api/role-leases/issue"): (201, dict(LEASE, id="fresh")),
        })
        out = ol.ensure(do_open=routes)
        self.assertEqual(out["action"], "issued")
        self.assertEqual(out["lease_id"], "fresh")

    def test_ac4_409_race_converges_to_renew(self):
        routes = _routes({
            ("GET", "/api/role-leases?role=operator"): (200, {"items": []}),
            ("POST", "/api/role-leases/issue"): (409, {"existingLeaseId": "winner"}),
            ("POST", "/api/role-leases/winner/renew"): (200, dict(LEASE, id="winner")),
        })
        out = ol.ensure(do_open=routes)
        self.assertEqual(out["action"], "renewed")
        self.assertEqual(out["lease_id"], "winner")
        self.assertIn("race", out.get("note", ""))

    def test_ac5_release_revokes_or_noops(self):
        routes_live = _routes({
            ("GET", "/api/role-leases?role=operator"): (200, {"items": [LEASE]}),
            ("POST", "/api/role-leases/lease-1/revoke"): (200, {"ok": True}),
        })
        self.assertEqual(ol.release(do_open=routes_live)["action"], "released")
        routes_none = _routes({("GET", "/api/role-leases?role=operator"): (200, {"items": []})})
        self.assertEqual(ol.release(do_open=routes_none)["action"], "noop")

    def test_ac6_status_reports_correctly(self):
        live = ol.status(do_open=_routes({("GET", "/api/role-leases?role=operator"): (200, {"items": [LEASE]})}))
        self.assertTrue(live["live"])
        none = ol.status(do_open=_routes({("GET", "/api/role-leases?role=operator"): (200, {"items": []})}))
        self.assertFalse(none["live"])

    def test_ac7_transport_error_is_degraded_not_raised(self):
        def boom(req, timeout=None):
            raise OSError("connection refused")
        out = ol.ensure(do_open=boom)
        self.assertEqual(out["action"], "degraded")
        self.assertIn("connection refused", out["detail"])

    def test_ac7b_http_500_from_list_is_degraded(self):
        routes = _routes({("GET", "/api/role-leases?role=operator"): (500, {"error": "db down"})})
        out = ol.ensure(do_open=routes)
        self.assertEqual(out["action"], "degraded")

    def test_ac8_cli_degraded_exits_zero(self):
        rc = ol.main(["ensure"])
        self.assertEqual(rc, 0)  # no nebula-srv in test env -> degraded path, still 0

    def test_channel_is_ui_fleet_by_default(self):
        seen = {}
        def capture(req, timeout=None):
            seen["url"] = req.full_url
            if req.get_method() == "POST" and "/issue" in req.full_url:
                seen["body"] = json.loads(req.data.decode())
                return _resp(201, dict(LEASE, id="new"))
            return _resp(200, {"items": []})
        ol.ensure(do_open=capture)
        # channel rides in the issue POST body (distinct from agents' interactive)
        self.assertEqual(seen.get("body", {}).get("channel"), "ui-fleet")
        self.assertEqual(seen.get("body", {}).get("role"), "operator")


if __name__ == "__main__":
    unittest.main()
