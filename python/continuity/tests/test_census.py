#!/usr/bin/env python3
"""Hermetic tests for continuity.census (agent connection records, V169).

No services, no database: probes are injected as fakes; the recording path
uses an injected exec_fn. Pins the refs-only stance, absence-as-data, the
inert gate, and never-raises.
"""

import json
import os
import sys
import unittest

_SELF = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.abspath(os.path.join(_SELF, "..", "..", ".."))
for p in (os.path.join(_REPO, "python"),
          os.path.join(_REPO, "python", "nebula-mcp-client")):
    if p not in sys.path:
        sys.path.insert(0, p)

from continuity import census  # noqa: E402


class _FakeClient:
    """MCP client double with a list_tools() surface."""

    def __init__(self, tools, exc=None):
        self._tools = tools
        self._exc = exc

    def list_tools(self):
        if self._exc is not None:
            raise self._exc
        return self._tools


class TestProbeMcpTools(unittest.TestCase):
    def test_collects_refs_per_server(self):
        made = {}

        def factory(base):
            made[base] = True
            return _FakeClient([{"name": "tool_a"}, {"name": "tool_b"}])

        tools = census.probe_mcp_tools(factory)
        self.assertEqual(len(tools), 4)  # 2 servers x 2 tools
        servers = {t["server"] for t in tools}
        self.assertEqual(servers, {"nebula-mcp", "tackle-mcp"})
        self.assertTrue(all(set(t) == {"server", "tool"} for t in tools))

    def test_unreachable_server_contributes_nothing(self):
        def factory(base):
            if ":3400" in base:  # tackle-mcp's port
                raise RuntimeError("connection refused")
            return _FakeClient([{"name": "tool_a"}])

        tools = census.probe_mcp_tools(factory)
        self.assertEqual([t["server"] for t in tools], ["nebula-mcp"])

    def test_malformed_tool_entries_skipped(self):
        tools = census.probe_mcp_tools(
            lambda base: _FakeClient([{"noname": "x"}, None, 7]))
        self.assertEqual(tools, [])


class TestProbes(unittest.TestCase):
    def test_procedure_cards_available(self):
        r = census.probe_procedure_cards(
            "dba", lambda role: {"count": 2, "procedures": [{"slug": "a"}, {"slug": "b"}]})
        self.assertEqual(r, {"available": True, "count": 2, "index": ["a", "b"]})

    def test_procedure_cards_absent_on_error(self):
        def boom(role):
            raise RuntimeError("down")
        r = census.probe_procedure_cards("dba", boom)
        self.assertFalse(r["available"])
        self.assertIn("RuntimeError", r["reason"])

    def test_inbox_available(self):
        r = census.probe_inbox("dba", lambda role: {"items": [1, 2], "pointer": "P"})
        self.assertEqual(r, {"available": True, "pending_count": 2, "pointer": "P"})

    def test_inbox_absent_on_error(self):
        r = census.probe_inbox("dba", lambda role: (_ for _ in ()).throw(OSError("x")))
        self.assertFalse(r["available"])
        self.assertIn("OSError", r["reason"])


class TestHandoff(unittest.TestCase):
    def test_from_digest_summary(self):
        r = census.build_handoff({"counts": {"open_inbox": 3, "open_threads": 2,
                                             "recent_records": 5},
                                  "digest_version": "v0.1",
                                  "sources_degraded": []})
        self.assertTrue(r["digest_available"])
        self.assertEqual(r["digest_version"], "v0.1")
        self.assertEqual(r["counts"], {"open_inbox": 3, "open_threads": 2,
                                       "recent_records": 5})
        self.assertEqual(r["degraded_surfaces"], 0)

    def test_none_is_explicit_absence(self):
        r = census.build_handoff(None)
        self.assertFalse(r["digest_available"])
        self.assertIn("reason", r)

    def test_digest_exception_result(self):
        r = census.build_handoff({"counts": {}})
        self.assertTrue(r["digest_available"])


class TestKeychains(unittest.TestCase):
    def test_keychains_always_explicitly_absent(self):
        c = census.collect_census(
            "dba", "m", "interactive",
            client_factory=lambda base: _FakeClient([]),
            fetch_index=lambda role: {"count": 0, "procedures": []},
            fetch_inbox=lambda role: {"items": []},
            digest_result=None)
        self.assertEqual(c["keychains"], census.KEYCHAINS_ABSENT)
        self.assertFalse(c["keychains"]["available"])
        self.assertIn("reason", c["keychains"])


class TestCollectCensus(unittest.TestCase):
    def test_full_row_shape(self):
        c = census.collect_census(
            "engineer", "freebuff/buffy", "interactive",
            session_id="sess-123", lease_ref="lease-abc",
            digest_result={"counts": {"open_inbox": 1}, "digest_version": "v0.1",
                           "sources_degraded": []},
            client_factory=lambda base: _FakeClient([{"name": "t1"}]),
            fetch_index=lambda role: {"count": 1, "procedures": [{"slug": "s"}]},
            fetch_inbox=lambda role: {"items": [1], "pointer": "P"})
        self.assertEqual(c["role"], "engineer")
        self.assertEqual(c["model"], "freebuff/buffy")
        self.assertEqual(c["session_id"], "sess-123")
        self.assertEqual(c["lease_ref"], "lease-abc")
        self.assertEqual(len(c["mcp_tools"]), 2)
        self.assertTrue(c["procedure_cards"]["available"])
        self.assertTrue(c["inbox_status"]["available"])
        self.assertTrue(c["handoff_context"]["digest_available"])

    def test_never_raises_on_total_probe_failure(self):
        def factory(base):
            raise RuntimeError("no networks")
        def boom(*a, **k):
            raise RuntimeError("nope")
        c = census.collect_census(
            "dba", "m", "interactive",
            client_factory=factory, fetch_index=boom, fetch_inbox=boom,
            digest_result=None)
        self.assertEqual(c["mcp_tools"], [])
        self.assertFalse(c["procedure_cards"]["available"])
        self.assertFalse(c["inbox_status"]["available"])
        self.assertFalse(c["handoff_context"]["digest_available"])


class _Exec:
    """Scripted exec_fn: sequence of (predicate, response-or-exception)."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, sql: str) -> str:
        self.calls.append(sql)
        for i, (pred, resp) in enumerate(list(self.steps)):
            if pred(sql):
                self.steps.pop(i)
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError("unexpected SQL: " + sql[:120])


def _regclass_hit(out):
    return lambda sql: "to_regclass" in sql, out


class TestRecordConnection(unittest.TestCase):
    def test_inert_when_table_absent(self):
        ex = _Exec([_regclass_hit("")])
        r = census.record_connection({"role": "dba"}, exec_fn=ex)
        self.assertFalse(r["recorded"])
        self.assertIn("inert gate", r["reason"])
        self.assertEqual(len(ex.calls), 1)  # no INSERT attempted

    def test_42p01_classified_as_inert(self):
        ex = _Exec([_regclass_hit("nebula.agent_connections"),
                    (lambda sql: "INSERT" in sql,
                     RuntimeError("ERROR: relation \"nebula.agent_connections\" "
                                  "does not exist (42P01)"))])
        r = census.record_connection({"role": "dba"}, exec_fn=ex)
        self.assertFalse(r["recorded"])
        self.assertIn("inert gate", r["reason"])

    def test_happy_path_inserts_refs_only_row(self):
        ex = _Exec([
            _regclass_hit("nebula.agent_connections"),
            (lambda sql: "INSERT" in sql, "99999999-9999-9999-9999-999999999999"),
        ])
        c = census.collect_census(
            "dba", "freebuff/buffy", "interactive", session_id=None,
            lease_ref="aaaaaaaa-1111-2222-3333-444444444444",
            digest_result={"counts": {"open_inbox": 2}, "digest_version": "v0.1",
                           "sources_degraded": []},
            client_factory=lambda base: _FakeClient([{"name": "t"}]),
            fetch_index=lambda role: {"count": 1, "procedures": [{"slug": "s"}]},
            fetch_inbox=lambda role: {"items": [1]})
        r = census.record_connection(c, exec_fn=ex)
        self.assertTrue(r["recorded"])
        self.assertTrue(r["conn_id"].startswith("99999999"))
        insert = ex.calls[-1]
        # refs-only: tool names present as JSON, no payload fields beyond names
        self.assertIn('"tool": "t"', insert)
        self.assertIn("aaaaaaaa-1111", insert)
        # lease_ref rendered as uuid literal, not NULL
        self.assertIn("::uuid", insert)

    def test_unleased_uses_null_lease(self):
        ex = _Exec([
            _regclass_hit("nebula.agent_connections"),
            (lambda sql: "INSERT" in sql, "11111111-2222-3333-4444-555555555555"),
        ])
        c = census.collect_census(
            "dba", "m", "interactive", client_factory=lambda base: _FakeClient([]),
            fetch_index=lambda role: {"count": 0, "procedures": []},
            fetch_inbox=lambda role: {"items": []})
        r = census.record_connection(c, exec_fn=ex)
        self.assertTrue(r["recorded"])
        # NULL lease: no uuid literal cast in the VALUES tuple
        insert = ex.calls[-1]
        self.assertNotIn("::uuid", insert)
        self.assertIn("NULL", insert)

    def test_generic_error_never_raises(self):
        ex = _Exec([_regclass_hit("nebula.agent_connections"),
                    (lambda sql: "INSERT" in sql, RuntimeError("disk on fire"))])
        r = census.record_connection({"role": "dba"}, exec_fn=ex)
        self.assertFalse(r["recorded"])
        self.assertIn("recording error", r["reason"])

    def test_no_role_is_refused(self):
        r = census.record_connection({"role": ""}, exec_fn=_Exec([]))
        self.assertFalse(r["recorded"])
        self.assertIn("no role", r["reason"])


if __name__ == "__main__":
    unittest.main()
