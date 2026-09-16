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
        self.assertEqual(d["digest_version"], "v0")
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
        self.assertTrue(any("/api/agent-records" in u for u in calls))


if __name__ == "__main__":
    unittest.main()
