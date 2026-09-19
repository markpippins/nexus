"""test_conformance_attribution_c1.py — inspector C1 conformance.

Proves the attribution contract for Assembly projections from the
interactive turn subscriber (discussions post 5d45f921, C1):

  A1  each acting role's comment is authored by that role's Assembly user
      (case-insensitive alias match — the DBA user is 'DBA')
  A2  the payload's postedById and role always agree (role/author divergence
      was the defect: everything posted as engineer)
  A3  'system' and unknown roles fall back to ASSEMBLY_FALLBACK_POSTED_BY_ID
      with loud provenance — absent identity is visible data, never silent
      misattribution
  A4  resolver failure (DB error) falls back without raising; posting
      continues
  A5  the resolver never touches the network — only the payload POST goes
      to Assembly (urllib.request.urlopen)

Hermetic: no real database, no real Assembly. The pg_conn is a fake whose
cursor returns the configured row; urlopen is stubbed and asserts the
payload it receives. Run:

    cd /home/codex/dev/nexus/python/cascade
    python3 -m pytest test_conformance_attribution_c1.py -v
"""

import contextlib
import io
import json
import sys
import unittest
from unittest import mock

_PARENT = sys.path[0]  # python/cascade when run per house convention

import interactive_turn_subscriber as its  # noqa: E402

ENGINEER = "af069ff6-760c-44cb-a0d4-11517164169b"
ARCHITECT = "a71f75ba-1f53-46b2-9708-17269d1210b0"
DBA = "1ea49b6d-1f57-456d-941a-626b6b344a79"


class FakeCursor:
    def __init__(self, row):
        self._row = row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    """Minimal pg_conn fake: one cursor whose row is configurable."""

    def __init__(self, row=None, error=None):
        self._row = row
        self._error = error
        self.last_cursor = None

    def cursor(self):
        cur = FakeCursor(self._row)
        if self._error is not None:
            def _boom(*a, **k):
                raise self._error
            cur.execute = _boom
        self.last_cursor = cur
        return cur

    def commit(self):
        pass

    def rollback(self):
        pass


class ResolvePostedByIdTests(unittest.TestCase):
    def test_role_resolves_to_its_own_user(self):
        posted, prov = its._resolve_posted_by_id(FakeConn((ARCHITECT,)), "architect")
        self.assertEqual(posted, ARCHITECT)
        self.assertEqual(prov, "role")

    def test_case_insensitive_alias_match(self):
        # The Assembly user is 'DBA'; the role string arrives lowercase.
        posted, prov = its._resolve_posted_by_id(FakeConn((DBA,)), "dba")
        self.assertEqual(posted, DBA)
        self.assertEqual(prov, "role")

    def test_system_role_falls_back_without_lookup(self):
        conn = FakeConn((ENGINEER,))
        posted, prov = its._resolve_posted_by_id(conn, "system")
        self.assertEqual(posted, its.ASSEMBLY_FALLBACK_POSTED_BY_ID)
        self.assertEqual(prov, "fallback")
        # 'system' has no user by design — no cursor is ever created.
        self.assertIsNone(conn.last_cursor)

    def test_unknown_role_falls_back_loudly(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            posted, prov = its._resolve_posted_by_id(FakeConn(None), "poltergeist")
        self.assertEqual(prov, "fallback")
        self.assertIn("fallback", buf.getvalue())
        self.assertIn("poltergeist", buf.getvalue())

    def test_db_error_falls_back_without_raising(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            posted, prov = its._resolve_posted_by_id(
                FakeConn(error=RuntimeError("db gone")), "architect")
        self.assertEqual(posted, its.ASSEMBLY_FALLBACK_POSTED_BY_ID)
        self.assertEqual(prov, "fallback")
        self.assertIn("lookup failed", buf.getvalue())


class PostAssemblyCommentAttributionTests(unittest.TestCase):
    """End-to-end through _post_assembly_comment with urlopen stubbed."""

    def _run(self, role, row, expected_posted):
        captured = {}
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"id": "comment-123"}).encode()
        response.__exit__.return_value = False

        with mock.patch.object(its.urllib.request, "urlopen",
                               return_value=response) as urlopen:
            comment_id = its._post_assembly_comment(
                FakeConn(row), "thread-abc", "statement of record", role)
        self.assertEqual(comment_id, "comment-123")

        req = urlopen.call_args[0][0]
        payload = json.loads(req.data.decode())
        captured.update(payload)
        self.assertEqual(payload["postedById"], expected_posted)
        self.assertEqual(payload["role"], role)
        return captured

    def test_architect_response_authored_by_architect(self):
        captured = self._run("architect", (ARCHITECT,), ARCHITECT)
        self.assertEqual(captured["postedById"], ARCHITECT)
        self.assertNotEqual(captured["postedById"], ENGINEER)

    def test_role_and_author_agree_for_every_mapped_role(self):
        for role, uid in (("architect", ARCHITECT), ("dba", DBA),
                          ("engineer", ENGINEER)):
            captured = self._run(role, (uid,), uid)
            self.assertEqual(captured["postedById"], uid)

    def test_system_comment_uses_fallback_with_explicit_provenance(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self._run("system", None, its.ASSEMBLY_FALLBACK_POSTED_BY_ID)
        self.assertIn("fallback", buf.getvalue())

    def test_thinking_role_gets_real_author_when_mapped(self):
        # The reasoning-trace post passes the acting role; a mapped role
        # must own it, not the fallback.
        self._run("architect", (ARCHITECT,), ARCHITECT)

    def test_postedbyid_never_hardcoded_engineer_for_other_roles(self):
        # Regression pin for the exact C1 defect: any resolved role must
        # not carry the engineer UUID unless it IS the engineer.
        captured = self._run("reviewer", ("bc5b0646-c2ee-4bf3-a40b-7b80085856bd",),
                             "bc5b0646-c2ee-4bf3-a40b-7b80085856bd")
        self.assertNotEqual(captured["postedById"], ENGINEER)

    def test_event_recorded_with_comment_payload_before_projection(self):
        # The event-first ordering is existing contract; pin that the
        # attribution fix did not disturb it (event payload carries role).
        events = []

        class EventConn(FakeConn):
            def cursor(self):
                cur = FakeCursor(self._row)

                real_execute = cur.execute

                def execute(sql, params=None):
                    if "duality.session_events" in str(sql):
                        events.append((sql, params))
                    return real_execute(sql, params)

                cur.execute = execute
                self.last_cursor = cur
                return cur

        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {"id": "c1"}).encode()
        response.__exit__.return_value = False
        with mock.patch.object(its.urllib.request, "urlopen",
                               return_value=response):
            its._post_assembly_comment(
                EventConn((ARCHITECT,)), "t1", "body", "architect")
        self.assertEqual(len(events), 2)  # insert + link-back update
        self.assertIn("duality.session_events", events[0][0])


class HermeticityTests(unittest.TestCase):
    def test_resolver_does_no_network_io(self):
        with mock.patch.object(its.urllib.request, "urlopen") as no_net:
            its._resolve_posted_by_id(FakeConn((ARCHITECT,)), "architect")
        no_net.assert_not_called()


if __name__ == "__main__":
    unittest.main()
