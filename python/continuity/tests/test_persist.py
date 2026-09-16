#!/usr/bin/env python3
"""Hermetic tests for continuity.persist (step-4 pre-stage).

No database, no sockets, no subprocess: the SQL executor is injected.
Pins the pre-stage contract: adoption-gated persistence (bound digests
only), inert-by-table-detection, manifest built from digest sources
(refs-only), never-raise semantics.
"""

import os
import sys
import unittest
from unittest import mock

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.abspath(os.path.join(_SELF_DIR, "..", ".."))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from continuity import persist  # noqa: E402


def _digest(role="dba", lease_ref="c5510fcd-10dc-4940-8665-9a66e52ab1bc",
            role_agreement=True, version="v0.1"):
    return {
        "digest_version": version,
        "disposition": "context-only",
        "role": role,
        "assembled_for_model": "freebuff/buffy",
        "as_of": "2026-09-16T17:00:00+00:00",
        "lease_binding": {"lease_ref": lease_ref, "lease_role": role,
                          "lease_model": "ui-fleet-standing",
                          "expires_at": "2026-09-16T18:00:00Z",
                          "role_agreement": role_agreement},
        "level_provenance": {"level_filter_allowed": "level <= 4",
                             "applied_at": "source-query"},
        "open_inbox": [{"record_id": "r1", "title": "t"}],
        "open_threads": [{"thread_id": "th1", "title": "tt", "shared_surface": True}],
        "recent_records_metadata": [{"record_id": "r2", "title": "m"}],
        "sources_degraded": [],
        "counts": {"open_inbox": 1, "open_threads": 1, "recent_records": 1},
    }


def _exec(table_present=True, insert_returns="11111111-1111-1111-1111-111111111111"):
    """Injectable executor: records SQL, answers the probe + INSERT."""
    calls = []

    def exec_fn(sql: str) -> str:
        calls.append(sql)
        if "to_regclass" in sql:
            return "nebula.session_context_snapshots" if table_present else ""
        if "INSERT INTO" in sql:
            return insert_returns
        return ""

    exec_fn.calls = calls
    return exec_fn


class TestAdoptionGate(unittest.TestCase):
    def test_unbound_digest_skipped_with_reason(self):
        d = _digest(lease_ref=None)
        d["lease_binding"]["role_agreement"] = None
        e = _exec(table_present=True)
        r = persist.persist_digest(d, exec_fn=e)
        self.assertFalse(r["persisted"])
        self.assertIn("adoption-gated", r["reason"])
        self.assertNotIn("INSERT INTO", "".join(e.calls))  # no write attempted

    def test_refused_binding_skipped(self):
        d = _digest(role_agreement=False)
        d["lease_binding"]["lease_ref"] = None
        e = _exec(table_present=True)
        r = persist.persist_digest(d, exec_fn=e)
        self.assertFalse(r["persisted"])
        self.assertIn("refused", r["reason"])

    def test_bound_digest_persists(self):
        e = _exec(table_present=True)
        r = persist.persist_digest(_digest(), exec_fn=e)
        self.assertTrue(r["persisted"])
        self.assertEqual(r["snapshot_id"], "11111111-1111-1111-1111-111111111111")
        insert_sql = "".join(e.calls)
        self.assertIn("INSERT INTO nebula.session_context_snapshots", insert_sql)
        self.assertIn("read_set_manifest", insert_sql)  # manifest always present
        self.assertIn("c5510fcd", insert_sql)


class TestInertGate(unittest.TestCase):
    def test_table_absent_is_inert_with_roundtable_reason(self):
        e = _exec(table_present=False)
        r = persist.persist_digest(_digest(), exec_fn=e)
        self.assertFalse(r["persisted"])
        self.assertIn("Q1/Q2", r["reason"])
        self.assertNotIn("INSERT INTO", "".join(e.calls))  # no write attempted


class TestManifest(unittest.TestCase):
    def test_manifest_built_from_digest_sources_refs_only(self):
        m = persist.build_manifest(_digest())
        self.assertEqual(m["lease"]["lease_ref"], "c5510fcd-10dc-4940-8665-9a66e52ab1bc")
        self.assertEqual(m["sources"]["inbox_record_ids"], ["r1"])
        self.assertEqual(m["sources"]["thread_ids"], ["th1"])
        self.assertEqual(m["sources"]["record_ids"], ["r2"])
        self.assertEqual(m["disposition"], "context-only")
        # refs-only: no content fields anywhere
        blob = str(m)
        self.assertNotIn("SECRET", blob)

    def test_liveness_stamped_into_manifest(self):
        m = persist.build_manifest(_digest())
        self.assertEqual(m["lease"]["expires_at"], "2026-09-16T18:00:00Z")

    def test_source_records_pair_ids_with_as_of(self):
        sr = persist.build_source_records(_digest())
        self.assertEqual({x["record_id"] for x in sr}, {"r1", "r2"})
        self.assertTrue(all(x["as_of"] == "2026-09-16T17:00:00+00:00" for x in sr))


class TestNeverRaises(unittest.TestCase):
    def test_exec_error_is_data(self):
        def boom(sql):
            raise RuntimeError("psql gone")
        r = persist.persist_digest(_digest(), exec_fn=boom)
        self.assertFalse(r["persisted"])
        self.assertIn("persistence error", r["reason"])

    def test_role_missing_is_data(self):
        d = _digest()
        d["role"] = ""
        r = persist.persist_digest(d, exec_fn=_exec())
        self.assertFalse(r["persisted"])
        self.assertIn("role", r["reason"])


class TestV0DigestCompat(unittest.TestCase):
    """Merge-order agnostic: v0 digests (no lease_binding) skip cleanly."""

    def test_v0_digest_without_binding_skips_explicitly(self):
        d = _digest(version="v0")
        d.pop("lease_binding")
        e = _exec()
        r = persist.persist_digest(d, exec_fn=e)
        self.assertFalse(r["persisted"])
        self.assertIn("adoption-gated", r["reason"])
        self.assertNotIn("INSERT INTO", "".join(e.calls))


if __name__ == "__main__":
    unittest.main()
