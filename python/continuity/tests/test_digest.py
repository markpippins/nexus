#!/usr/bin/env python3
"""Hermetic tests for continuity.digest v0 (read-only assembler).

No sockets: fetchers are injected (or the module-level _get_json is
monkeypatched for the fetcher-filtering tests). Pins the governance
contract: I2 disposition marker, metadata-only records (altitude safety
by construction), model-identity stamp, graceful source degradation,
counts integrity.
"""

import os
import sys
import unittest
from datetime import datetime, timezone

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.abspath(os.path.join(_SELF_DIR, "..", ".."))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from continuity import digest as dg  # noqa: E402


def _rec(rid, title="t", rtype="report", tags=None, created="2026-09-16T10:00:00Z",
         content="SECRET BODY TEXT", level=2):
    return {"id": rid, "title": title, "recordType": rtype, "tags": tags or [],
            "createdAt": created, "content": content, "level": level}


class TestAssembleDigest(unittest.TestCase):
    def test_payload_shape_and_governance_fields(self):
        d = dg.assemble_digest(
            "dba", "freebuff/buffy",
            fetch_inbox=lambda: [{"record_id": "r1", "title": "T"}],
            fetch_threads=lambda: [{"thread_id": "t1", "title": "TT", "shared_surface": True}],
            fetch_records=lambda: [{"record_id": "r2", "title": "M"}],
            level_ceiling="level <= 4",
            now=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(d["digest_version"], "v0.1")
        self.assertEqual(d["disposition"], "context-only")  # I2 marker
        self.assertEqual(d["role"], "dba")
        self.assertEqual(d["assembled_for_model"], "freebuff/buffy")  # R-1..R-3
        self.assertEqual(d["level_provenance"]["level_filter_allowed"], "level <= 4")
        self.assertEqual(d["counts"], {"open_inbox": 1, "open_threads": 1,
                                       "recent_records": 1})
        self.assertEqual(d["sources_degraded"], [])

    def test_degraded_source_never_fails_digest(self):
        def boom():
            raise RuntimeError("rest down")
        d = dg.assemble_digest("dba", "m", fetch_inbox=boom,
                               fetch_threads=lambda: [],
                               fetch_records=lambda: [])
        self.assertEqual(d["counts"]["open_inbox"], 0)
        self.assertTrue(any("inbox" in s for s in d["sources_degraded"]))
        self.assertIn("disposition", d)  # still a valid, marked digest

    def test_all_sources_down_yields_marked_empty_digest(self):
        def boom():
            raise RuntimeError("down")
        d = dg.assemble_digest("dba", "m", boom, boom, boom)
        self.assertEqual(d["counts"], {"open_inbox": 0, "open_threads": 0,
                                       "recent_records": 0})
        self.assertEqual(len(d["sources_degraded"]), 3)


class TestFetcherFiltering(unittest.TestCase):
    """fetch_open_inbox logic via monkeypatched _get_json."""

    def setUp(self):
        self._orig = dg._get_json

    def tearDown(self):
        dg._get_json = self._orig

    def _patch(self, payload):
        dg._get_json = lambda url, timeout=8: payload

    def test_inbox_keeps_unresolved_addressed(self):
        self._patch({"items": [
            _rec("a", tags=["to:dba", "type:task", "status:open"]),
            _rec("b", tags=["to:engineer"]),              # not mine
            _rec("c", tags=["to:dba", "status:resolved"],
                 created="2020-01-01T00:00:00Z"),          # resolved + old
        ]})
        out = dg.fetch_open_inbox("dba")
        self.assertEqual([i["record_id"] for i in out], ["a"])

    def test_inbox_keeps_recent_even_if_resolved(self):
        recent = datetime.now(timezone.utc).isoformat()
        self._patch({"items": [_rec("c", tags=["to:dba", "status:resolved"],
                                    created=recent)]})
        out = dg.fetch_open_inbox("dba")
        self.assertEqual([i["record_id"] for i in out], ["c"])

    def test_recent_records_are_metadata_only(self):
        """The altitude-safety guarantee: source content never reaches the digest."""
        self._patch({"items": [_rec("x", content="L4 DOCTRINE BODY", level=4)]})
        out = dg.fetch_recent_records("dba")
        self.assertEqual(len(out), 1)
        self.assertNotIn("content", out[0])
        self.assertEqual(out[0]["record_id"], "x")
        self.assertEqual(out[0]["level"], 4)  # level rides as metadata only


class TestLevelCeilingFetch(unittest.TestCase):
    def setUp(self):
        self._orig = dg._get_json

    def tearDown(self):
        dg._get_json = self._orig

    def test_ceiling_resolved_case_insensitive(self):
        dg._get_json = lambda url, timeout=8: {"items": [
            {"name": "DBA", "level_filter_allowed": "level <= 4"}]}
        self.assertEqual(dg.fetch_level_ceiling("dba"), "level <= 4")

    def test_ceiling_absent_returns_none(self):
        dg._get_json = lambda url, timeout=8: {"items": []}
        self.assertIsNone(dg.fetch_level_ceiling("dba"))

    def test_ceiling_error_returns_none(self):
        def boom(url, timeout=8):
            raise RuntimeError("down")
        dg._get_json = boom
        self.assertIsNone(dg.fetch_level_ceiling("dba"))


class TestCLI(unittest.TestCase):
    def test_cli_wires_live_fetchers_and_emits_json(self):
        import io
        import json
        calls = []

        def fake_get(url, timeout=8):
            calls.append(url)
            if "/agent-records" in url:
                return {"items": [_rec("z", tags=["to:dba"])]}
            if "/forums/to-do" in url:
                return [{"id": "t9", "title": "A thread"}]
            if "/roles" in url:
                return {"items": [{"name": "dba",
                                   "level_filter_allowed": "level <= 4"}]}
            if "/role-leases" in url:
                return {"items": []}  # no live lease — unbound digest
            raise AssertionError(url)

        orig = dg._get_json
        dg._get_json = fake_get
        try:
            buf = io.StringIO()
            old = sys.stdout
            sys.stdout = buf
            rc = dg.main(["dba", "--model", "test/model"])
            sys.stdout = old
        finally:
            dg._get_json = orig
        self.assertEqual(rc, 0)
        d = json.loads(buf.getvalue())
        self.assertEqual(d["role"], "dba")
        self.assertEqual(d["assembled_for_model"], "test/model")
        self.assertEqual(d["counts"]["open_inbox"], 1)
        self.assertEqual(d["counts"]["open_threads"], 1)
        self.assertEqual(d["level_provenance"]["level_filter_allowed"], "level <= 4")
        self.assertIsNone(d["lease_binding"]["lease_ref"])  # v0.1: explicit unbound
        self.assertTrue(any("/api/role-leases" in u for u in calls))
        self.assertTrue(any("/api/agent-records" in u for u in calls))

class TestLeaseBinding(unittest.TestCase):
    """v0.1 lease_binding: the V167 lease_ref binding (nullable-but-explicit)."""

    def _lease(self, rid="c5510fcd-10dc-4940-8665-9a66e52ab1bc", role="dba",
               model="ui-fleet-standing", expires="2026-09-16T17:17:28Z"):
        return {"lease_ref": rid, "lease_role": role, "lease_model": model,
                "expires_at": expires}

    def test_unbound_digest_is_explicit(self):
        d = dg.assemble_digest("dba", "m", fetch_lease=lambda r: None,
                               now=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc))
        b = d["lease_binding"]
        self.assertIsNone(b["lease_ref"])
        self.assertIsNone(b["role_agreement"])  # no lease: neither agree nor disagree
        self.assertIn("unbound", b["reason"])
        self.assertEqual(b["lease_role"], "dba")

    def test_bound_digest_carries_ref_and_agreement(self):
        d = dg.assemble_digest("dba", "m", fetch_lease=lambda r: self._lease(),
                               now=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc))
        b = d["lease_binding"]
        self.assertEqual(b["lease_ref"], "c5510fcd-10dc-4940-8665-9a66e52ab1bc")
        self.assertEqual(b["lease_model"], "ui-fleet-standing")
        self.assertTrue(b["role_agreement"])
        self.assertNotIn("reason", b)
        self.assertEqual(d["as_of"], b["bound_at"])  # binding stamped at assembly time

    def test_role_mismatch_refuses_to_bind(self):
        """SNAP002 mirror: a lease is a scope artifact, not an authority grant."""
        d = dg.assemble_digest("dba", "m",
                               fetch_lease=lambda r: self._lease(role="engineer"),
                               now=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc))
        b = d["lease_binding"]
        self.assertIsNone(b["lease_ref"])          # refused
        self.assertFalse(b["role_agreement"])
        self.assertIn("refused", b["reason"])
        self.assertEqual(b["lease_role"], "engineer")  # the mismatch is visible

    def test_case_insensitive_role_agreement(self):
        d = dg.assemble_digest("DBA", "m", fetch_lease=lambda r: self._lease(role="dba"))
        self.assertTrue(d["lease_binding"]["role_agreement"])

    def test_lease_source_failure_degrades_not_fails(self):
        def boom(role):
            raise RuntimeError("nebula down")
        d = dg.assemble_digest("dba", "m", fetch_lease=boom)
        b = d["lease_binding"]
        self.assertIsNone(b["lease_ref"])
        self.assertTrue(any(s.startswith("lease:") for s in d["sources_degraded"]))
        self.assertIn("disposition", d)  # still a valid digest

    def test_no_fetcher_states_caller_gap(self):
        d = dg.assemble_digest("dba", "m")
        self.assertIsNone(d["lease_binding"]["lease_ref"])
        self.assertIn("no lease fetcher", d["lease_binding"]["reason"])

    def test_positional_callers_unchanged(self):
        """fetch_lease is keyword-only-usable: old positional arity still works."""
        d = dg.assemble_digest("dba", "m", lambda: [], lambda: [], lambda: [], "level <= 4")
        self.assertIn("lease_binding", d)
        self.assertEqual(d["level_provenance"]["level_filter_allowed"], "level <= 4")


class TestFetchLiveLease(unittest.TestCase):
    """REST fetcher: liveness predicate pinned to match operator_svc.lease_check
    and nebula-srv /api/role-leases/stale (one definition, three implementations)."""

    def setUp(self):
        self._orig = dg._get_json

    def tearDown(self):
        dg._get_json = self._orig

    def _row(self, rid="l1", role="dba", status="ACTIVE",
             expires="2099-01-01T00:00:00Z", budget=None, consumed=None):
        return {"id": rid, "role": role, "status": status, "model": "m",
                "expires_at": expires, "budget_units": budget,
                "consumed_units": consumed}

    def _patch(self, items):
        dg._get_json = lambda url, timeout=8: {"items": items}

    def test_selects_newest_live(self):
        # REST orders created_at DESC: the FIRST row is the newest lease.
        self._patch([self._row(rid="new"), self._row(rid="old")])
        lease = dg.fetch_live_lease("dba")
        self.assertEqual(lease["lease_ref"], "new")

    def test_expired_not_live(self):
        self._patch([self._row(expires="2020-01-01T00:00:00Z")])
        self.assertIsNone(dg.fetch_live_lease("dba"))

    def test_budget_exhausted_not_live(self):
        self._patch([self._row(budget=10, consumed=10)])
        self.assertIsNone(dg.fetch_live_lease("dba"))

    def test_budget_available_is_live(self):
        self._patch([self._row(budget=10, consumed=9)])
        self.assertIsNotNone(dg.fetch_live_lease("dba"))

    def test_null_expiry_budget_is_live(self):
        self._patch([self._row(expires=None, budget=None)])
        self.assertIsNotNone(dg.fetch_live_lease("dba"))

    def test_empty_items_returns_none(self):
        self._patch([])
        self.assertIsNone(dg.fetch_live_lease("dba"))

    def test_url_carries_role_and_status_filters(self):
        urls = []
        def fake(url, timeout=8):
            urls.append(url)
            return {"items": []}
        dg._get_json = fake
        dg.fetch_live_lease("engineer")
        self.assertIn("role=engineer", urls[0])
        self.assertIn("status=ACTIVE", urls[0])


if __name__ == "__main__":
    unittest.main()
