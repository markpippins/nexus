#!/usr/bin/env python3
"""Hermetic tests for continuity.blackboard (boot-shim slice 2).

No database, no network: the Redis client runs against a scripted fake
socket; render/format are pure; render_role_digest's environmental paths are
exercised by injecting failures. Pins the contract from R1 e93dfc7a:

  - RESP client: GET/SET EX wire format, nil handling, -ERR raising, and the
    buffered reader surviving a whole reply arriving in one segment
  - RedisCache degrade: any socket error flips available=False permanently
    (no retry storms during boot)
  - render_digest: bucket vocabulary (policy ac2d1382), counts block,
    checkpoint freshness classification, inbox seen_count
  - format_digest: stable line order, never raises on empty
  - render_role_digest: inert gate (V192 absent -> inert-skip), PG-down ->
    degraded, cache hit/miss, advance path gated and non-fatal
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

_SELF = os.path.dirname(os.path.abspath(__file__))
_PY = os.path.abspath(os.path.join(_SELF, "..", ".."))
if _PY not in sys.path:
    sys.path.insert(0, _PY)

from continuity import blackboard as bb  # noqa: E402


# ── fake socket for the RESP client ──────────────────────────────────────────

class FakeRedisSock:
    """Scripted server: records commands, replies from a queue."""

    def __init__(self, replies):
        self.sent = io.BytesIO()
        self.replies = list(replies)

    def sendall(self, data):
        self.sent.write(data)

    def recv(self, n):
        if not self.replies:
            return b""
        return self.replies.pop(0)

    def settimeout(self, t):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RespClientTest(unittest.TestCase):
    def test_get_hit_wire_format(self):
        s = FakeRedisSock([b"$5\r\nhello\r\n"])
        val = bb._redis_cmd(s, b"GET", b"nexus:blackboard:dba")
        self.assertEqual(val, b"hello")
        wire = s.sent.getvalue()
        self.assertIn(b"*2\r\n$3\r\nGET\r\n$20\r\nnexus:blackboard:dba\r\n", wire)

    def test_get_nil(self):
        s = FakeRedisSock([b"$-1\r\n"])
        self.assertIsNone(bb._redis_cmd(s, b"GET", b"k"))

    def test_setex_wire_format(self):
        s = FakeRedisSock([b"+OK\r\n"])
        val = bb._redis_cmd(s, b"SET", b"k", b"v", b"EX", b"300")
        self.assertEqual(val, b"OK")
        self.assertIn(
            b"$3\r\nSET\r\n$1\r\nk\r\n$1\r\nv\r\n$2\r\nEX\r\n$3\r\n300\r\n",
            s.sent.getvalue())

    def test_err_raises(self):
        s = FakeRedisSock([b"-ERR bad\r\n"])
        with self.assertRaises(RuntimeError):
            bb._redis_cmd(s, b"GET", b"k")

    def test_bulk_payload_arriving_with_line(self):
        # The buffered reader must survive the whole reply landing in one
        # segment (real Redis does this on fast loopback) — a raw-recv line
        # reader drops the payload bytes past the CRLF.
        s = FakeRedisSock([b"$3\r\nabc\r\n"])
        self.assertEqual(bb._redis_cmd(s, b"GET", b"k"), b"abc")


class RedisCacheTest(unittest.TestCase):
    def test_get_miss_returns_none_and_degrades(self):
        c = bb.RedisCache()
        with mock.patch.object(bb.socket, "create_connection",
                               side_effect=OSError("no redis")):
            self.assertIsNone(c.get("k"))
        self.assertFalse(c.available, "env failure must flip available=False")

    def test_degraded_cache_stays_down(self):
        c = bb.RedisCache()
        c.available = False
        with mock.patch.object(bb.socket, "create_connection") as m:
            self.assertIsNone(c.get("k"))
            c.setex("k", 10, "v")
            m.assert_not_called()

    def test_get_hit_roundtrip(self):
        c = bb.RedisCache()
        s = FakeRedisSock([b"$2\r\nhi\r\n"])
        with mock.patch.object(bb.socket, "create_connection", return_value=s):
            self.assertEqual(c.get("k"), b"hi")


# ── render_digest / format_digest ────────────────────────────────────────────

def row(kind, bucket, title, rating=None, created="2026-09-20 12:00:00+00",
        reason="r"):
    return {"item_kind": kind, "bucket": bucket, "title": title,
            "status_rating": rating, "created": created, "reason": reason}


class RenderTest(unittest.TestCase):
    def test_full_bucket_vocabulary(self):
        rows = [
            row("todo", "action-needed", "[engineer] fresh item", 0),
            row("todo", "awaiting-pickup", "[engineer] 3d old", 0),
            row("todo", "stale", "[engineer] 20d unacked", 0),
            row("todo", "in-flight", "[engineer] picked up", 2),
            row("todo", "done", "[engineer] finished", 4),
            row("todo", "unrouted", "[barbie] unmapped token", 0),
            row("inbox", "action-needed", "W3 disposition request"),
            row("inbox", "seen", "older record 1"),
            row("inbox", "seen", "older record 2"),
            row("checkpoint", "checkpoint", "dba / inbox checkpoint",
                reason="never reviewed"),
            row("checkpoint", "checkpoint", "dba / todo checkpoint",
                reason="reviewed 2026-09-20 10:00Z"),
        ]
        d = bb.render_digest(rows, "dba")
        self.assertEqual(d["digest_version"], "bb-v0.1")
        self.assertEqual(d["role"], "dba")
        c = d["counts"]
        self.assertEqual(c["todo_action_needed"], 1)
        self.assertEqual(c["todo_awaiting_pickup"], 1)
        self.assertEqual(c["todo_stale"], 1)
        self.assertEqual(c["todo_in_flight"], 1)
        self.assertEqual(c["todo_unrouted"], 1)
        self.assertEqual(c["inbox_action_needed"], 1)
        self.assertEqual(c["inbox_seen"], 2)
        self.assertEqual(c["checkpoints_never_reviewed"], 1)
        self.assertIn("dba / inbox checkpoint", d["checkpoints"])

    def test_empty_rows(self):
        d = bb.render_digest([], "critic")
        self.assertEqual(all(v == [] for v in d["todo"].values()), True)
        self.assertEqual(d["counts"]["inbox_seen"], 0)

    def test_format_stable_and_safe(self):
        d = bb.render_digest([], "analyst")
        text = bb.format_digest(d)
        self.assertIn("blackboard [analyst]", text)
        d2 = bb.render_digest(
            [row("todo", "action-needed", "[analyst] top item", 0)], "analyst")
        text2 = bb.format_digest(d2)
        self.assertIn("[analyst] top item", text2)
        self.assertLess(text2.index("blackboard"), text2.index("[analyst] top item"))


# ── render_role_digest environmental paths ───────────────────────────────────

class EntryPointTest(unittest.TestCase):
    def test_pg_down_degraded(self):
        def factory(dsn):
            raise OSError("refused")
        r = bb.render_role_digest("dba", "postgresql://x", cache=None,
                                  conn_factory=factory)
        self.assertEqual(r["status"], "degraded")
        self.assertIn("connect", r["reason"].lower())
        self.assertEqual(r["cache"], "off")

    def test_inert_skip_when_view_absent(self):
        conn = mock.MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
        r = bb.render_role_digest("dba", "postgresql://x", cache=None,
                                  conn_factory=lambda dsn: conn)
        self.assertEqual(r["status"], "inert-skip")
        self.assertIn("V192", r["reason"])

    def test_ok_render_with_cache_miss_and_store(self):
        conn = mock.MagicMock()
        cu = conn.cursor.return_value.__enter__.return_value
        cu.fetchone.side_effect = [(True,)]  # view present
        cu.description = [("item_kind",), ("bucket",), ("title",),
                          ("status_rating",), ("created",), ("reason",)]
        cu.fetchall.return_value = [
            ("todo", "action-needed", "[dba] item", 0,
             "2026-09-20 12:00:00+00", "inside 72h ack SLA")]
        cache = mock.MagicMock()
        cache.get.return_value = None
        r = bb.render_role_digest("dba", "postgresql://x", cache=cache,
                                  cache_ttl=42, conn_factory=lambda dsn: conn)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["cache"], "miss")
        self.assertEqual(r["digest"]["counts"]["todo_action_needed"], 1)
        cache.setex.assert_called_once()
        args = cache.setex.call_args[0]
        self.assertEqual(args[1], 42)
        self.assertIn("todo_action_needed", args[2])

    def test_cache_hit_skips_pg_query(self):
        conn = mock.MagicMock()
        cu = conn.cursor.return_value.__enter__.return_value
        cu.fetchone.side_effect = [(True,)]
        cached = json.dumps(bb.render_digest(
            [row("todo", "stale", "[dba] cached stale", 0)], "dba"), default=str)
        cache = mock.MagicMock()
        cache.get.return_value = cached.encode()
        r = bb.render_role_digest("dba", "postgresql://x", cache=cache,
                                  conn_factory=lambda dsn: conn)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["cache"], "hit")
        self.assertEqual(r["digest"]["counts"]["todo_stale"], 1)
        for call in cu.execute.call_args_list:
            self.assertNotIn("FROM nebula.v_coordination_blackboard",
                             call[0][0])

    def test_advance_gated_and_boundary_invalidates(self):
        conn = mock.MagicMock()
        cu = conn.cursor.return_value.__enter__.return_value
        cu.fetchone.side_effect = [(True,)]
        cu.description = [("item_kind",), ("bucket",), ("title",),
                          ("status_rating",), ("created",), ("reason",)]
        cu.fetchall.return_value = []
        cu.rowcount = 1
        r = bb.render_role_digest("dba", "postgresql://x", cache=None,
                                  advance=True, model="freebuff/buffy",
                                  conn_factory=lambda dsn: conn)
        self.assertEqual(r["digest"]["checkpoints_advanced"], 2)  # inbox+todo
        sqls = " ".join(c[0][0] for c in cu.execute.call_args_list)
        self.assertIn("ON CONFLICT", sqls)
        self.assertIn("last_reviewed_at = now()", sqls)


class AdvanceRoleCheckpointsTest(unittest.TestCase):
    """Session-protocol v2 standalone advance (R17.1)."""

    def _conn(self):
        conn = mock.MagicMock()
        cu = conn.cursor.return_value.__enter__.return_value
        cu.rowcount = 1
        return conn, cu

    def test_ok_advances_inbox_and_todo(self):
        conn, cu = self._conn()
        r = bb.advance_role_checkpoints(
            "dba", "postgresql://x", model="freebuff/buffy",
            conn_factory=lambda dsn: conn)
        self.assertEqual(r, {"status": "ok", "advanced": 2})
        sqls = " ".join(c[0][0] for c in cu.execute.call_args_list)
        self.assertIn("coordination_checkpoints", sqls)
        self.assertIn("ON CONFLICT", sqls)

    def test_psycopg2_absent_degrades_not_raises(self):
        # No injected factory + psycopg2 import fails -> honest degraded dict.
        with mock.patch.dict(sys.modules, {"psycopg2": None}):
            r = bb.advance_role_checkpoints("dba", "postgresql://x")
        self.assertEqual(r["status"], "degraded")
        self.assertIn("psycopg2", r["reason"])

    def test_pg_error_degraded_never_raises(self):
        def _boom(dsn):
            raise RuntimeError("pg gone")
        r = bb.advance_role_checkpoints("dba", "postgresql://x",
                                        conn_factory=_boom)
        self.assertEqual(r["status"], "degraded")
        self.assertIn("RuntimeError", r["reason"])


class RoleCanonicalizationTest(unittest.TestCase):
    """Incident 2026-09-22: --role DBA failed the coordination_checkpoints
    role FK (nebula.roles is lowercase) and the advance degraded silently.
    All coordination surfaces must canonicalize role to lowercase."""

    def test_canonical_role(self):
        self.assertEqual(bb.canonical_role("DBA"), "dba")
        self.assertEqual(bb.canonical_role(" Lead-Engineer "), "lead-engineer")
        self.assertEqual(bb.canonical_role(""), "")
        self.assertEqual(bb.canonical_role(None), "")

    def test_advance_upsert_receives_lowercase_role(self):
        conn = mock.MagicMock()
        cu = conn.cursor.return_value.__enter__.return_value
        cu.rowcount = 1
        bb.advance_checkpoints(conn, "DBA", model="freebuff/codebuff")
        for call in cu.execute.call_args_list:
            self.assertEqual(call[0][1][0], "dba")  # params[0] is the role

    def test_standalone_advance_canonicalizes(self):
        conn = mock.MagicMock()
        cu = conn.cursor.return_value.__enter__.return_value
        cu.rowcount = 1
        r = bb.advance_role_checkpoints(
            "DBA", "postgresql://x", model="freebuff/codebuff",
            conn_factory=lambda dsn: conn)
        self.assertEqual(r, {"status": "ok", "advanced": 2})
        for call in cu.execute.call_args_list:
            self.assertEqual(call[0][1][0], "dba")

    def test_fetch_rows_and_cache_key_canonicalized(self):
        conn = mock.MagicMock()
        cu = conn.cursor.return_value.__enter__.return_value
        cu.fetchone.return_value = (True,)   # view_present
        cu.description = [("item_kind",), ("bucket",), ("title",),
                          ("status_rating",), ("created",), ("reason",)]
        cu.fetchall.return_value = []
        cache = mock.MagicMock()
        cache.get.return_value = None
        r = bb.render_role_digest("DBA", "postgresql://x", cache=cache,
                                  conn_factory=lambda dsn: conn)
        self.assertEqual(r["status"], "ok")
        # the view read must use the canonical lowercase role
        fetch_calls = [c for c in cu.execute.call_args_list
                       if "v_coordination_blackboard" in c[0][0]]
        self.assertEqual(len(fetch_calls), 1)
        self.assertEqual(fetch_calls[0][0][1], ("dba",))
        # the cache key must not fragment across case variants
        setex_keys = [c[0][0] for c in cache.setex.call_args_list]
        self.assertIn(bb.CACHE_PREFIX + "dba", setex_keys)
        self.assertNotIn(bb.CACHE_PREFIX + "DBA", setex_keys)
        self.assertEqual(r["digest"]["role"], "dba")


if __name__ == "__main__":
    unittest.main(verbosity=2)
