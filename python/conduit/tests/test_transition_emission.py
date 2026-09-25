"""Transition-emission tests: every Python-adapter ticket state change must
emit a kernel.transition_event row (ADR-016).

Background (2026-09-24): the gen-2 8261654 builder ticket was cancelled via
the Python adapter's cancel_ticket and produced ZERO transition events — the
durable trail lived only on the ticket row, so event-driven consumers (the
CD-2 re-alert cursor, PR #549) could never see the cancellation. This suite
pins emission coverage for the whole Python cancel/claim/release family,
with event shapes matched to the TS counterparts (conduit-mcp db.ts):
  cancel / supersede / abandon / orphan-close → transition.rejected
  claim / close / release / session-release   → transition.committed

House pattern (test_c6_ticket_dispositions): canary-prefixed rows on the
shared-live vision.tickets + nebula.plan_status surfaces (both trigger-free;
zz-plan canaries are invisible to nebula.implementation_plans, which reads
blueprints_history), teardown-guaranteed cleanup — INCLUDING the emitted
kernel events, since they flow onto the live transition-event stream.

Run:
  CONDUIT_PG_DSN='host=localhost port=5432 user=pguser password=pgpass dbname=nexus' \
      python3 -m pytest conduit/tests/test_transition_emission.py -v
"""
import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from db_adapter import DBAdapter  # noqa: E402

_DSN = os.environ.get("CONDUIT_PG_DSN", "")
if not _DSN:
    raise RuntimeError("CONDUIT_PG_DSN must be set to run tests (PG is mandatory)")

TICKET_PREFIX = "zz-trx-test-"
PLAN_PREFIX = "zz-trx-plan-"


class TransitionEmissionTest(unittest.TestCase):
    """Canary tickets on live vision.tickets; events asserted in
    kernel.transition_event by aggregate_id, then deleted at teardown.

    The adapter runs against a throwaway shadow schema (unique per test) so
    unqualified `plan_status` resolves to a shadow TABLE (nebula.plan_status
    is a VIEW over blueprints_history — not insertable), while unqualified
    `tickets` still resolves to the shared-live vision.tickets exactly as in
    production. The shadow schema stubs sessions/circuit_breaker with the
    columns _init_db verifies."""

    def setUp(self):
        import psycopg2
        self.raw = psycopg2.connect(_DSN)
        self.shadow = "zz_trx_shadow_" + uuid.uuid4().hex[:8]
        cur = self.raw.cursor()
        cur.execute(f'CREATE SCHEMA {self.shadow}')
        cur.execute(
            f"CREATE TABLE {self.shadow}.sessions ("
            "id TEXT PRIMARY KEY, cost_usd DOUBLE PRECISION DEFAULT 0)")
        cur.execute(
            f"CREATE TABLE {self.shadow}.circuit_breaker ("
            "paused BOOLEAN NOT NULL DEFAULT false)")
        cur.execute(
            f"CREATE TABLE {self.shadow}.plan_status ("
            "id TEXT PRIMARY KEY, derived_status TEXT NOT NULL)")
        self.raw.commit()
        self.adapter = DBAdapter(schema=self.shadow)
        self.ticket_ids = []
        self.plan_ids = []
        self.addCleanup(self._teardown)

    def _teardown(self):
        try:
            cur = self.raw.cursor()
            for tid in self.ticket_ids:
                cur.execute(
                    "DELETE FROM kernel.transition_event "
                    "WHERE aggregate_type = 'ticket' AND aggregate_id = %s",
                    (tid,))
            for tid in self.ticket_ids:
                cur.execute("DELETE FROM vision.tickets WHERE id = %s", (tid,))
            self.raw.commit()
        except Exception:
            self.raw.rollback()
        try:
            cur = self.raw.cursor()
            cur.execute(f'DROP SCHEMA IF EXISTS {self.shadow} CASCADE')
            self.raw.commit()
        except Exception:
            self.raw.rollback()
        finally:
            self.raw.close()

    def _ticket(self, status="open", role="builder", session_id=None):
        tid = TICKET_PREFIX + uuid.uuid4().hex[:12]
        cur = self.raw.cursor()
        cur.execute(
            """INSERT INTO vision.tickets (id, plan_id, role, status, created_at, session_id)
               VALUES (%s, %s, %s, %s, '2020-01-01T00:00:00Z', %s)""",
            (tid, PLAN_PREFIX + uuid.uuid4().hex[:8], role, status, session_id))
        self.raw.commit()
        self.ticket_ids.append(tid)
        return tid

    def _claim(self, ticket_id, session_id="zz-trx-session"):
        cur = self.raw.cursor()
        cur.execute(
            "UPDATE vision.tickets SET status='claimed', session_id=%s WHERE id=%s",
            (session_id, ticket_id))
        self.raw.commit()

    def _plan_status(self, derived_status, roles=("builder",)):
        pid = PLAN_PREFIX + uuid.uuid4().hex[:8]
        cur = self.raw.cursor()
        cur.execute(
            f"INSERT INTO {self.shadow}.plan_status (id, derived_status) VALUES (%s, %s)",
            (pid, derived_status))
        self.raw.commit()
        self.plan_ids.append(pid)
        return pid

    def _events(self, ticket_id):
        cur = self.raw.cursor()
        cur.execute(
            "SELECT event_type::text, payload FROM kernel.transition_event "
            "WHERE aggregate_type = 'ticket' AND aggregate_id = %s ORDER BY id",
            (ticket_id,))
        return cur.fetchall()

    # ── cancel family: transition.rejected ─────────────────────────

    def test_cancel_ticket_emits_rejected_event(self):
        tid = self._ticket(status="open")
        n = self.adapter.cancel_ticket(tid, reason="test-cancel")
        self.assertEqual(n, 1)
        events = self._events(tid)
        self.assertEqual(len(events), 1, f"expected exactly 1 event, got {events}")
        etype, payload = events[0]
        self.assertEqual(etype, "transition.rejected")
        self.assertEqual(payload["from_status"], "open")
        self.assertEqual(payload["to_status"], "cancelled")
        self.assertEqual(payload["reason"], "test-cancel")

    def test_cancel_ticket_no_event_on_ineligible(self):
        tid = self._ticket(status="expired")
        n = self.adapter.cancel_ticket(tid, reason="test-cancel-miss")
        self.assertEqual(n, 0)
        self.assertEqual(self._events(tid), [], "no event for a no-op cancel")

    def test_supersede_ticket_emits_rejected_event(self):
        tid = self._ticket(status="open")
        result = self.adapter.supersede_ticket(tid, reason="test-supersede")
        self.assertTrue(result["superseded"])
        events = self._events(tid)
        self.assertEqual(len(events), 1)
        etype, payload = events[0]
        self.assertEqual(etype, "transition.rejected")
        self.assertEqual(payload["from_status"], "open")
        self.assertEqual(payload["to_status"], "superseded")

    def test_abandon_ticket_emits_rejected_event(self):
        tid = self._ticket(status="claimed")
        ok = self.adapter.abandon_ticket(
            "nonexistent-plan", "builder", "no-such-session")
        self.assertFalse(ok)  # sanity: wrong identity closes nothing
        # now the real path: claim then abandon by identity
        cur = self.raw.cursor()
        cur.execute("SELECT plan_id, role FROM vision.tickets WHERE id=%s", (tid,))
        plan_id, role = cur.fetchone()
        self.raw.commit()
        ok = self.adapter.abandon_ticket(plan_id, role, "zz-trx-session-aban")
        self.assertFalse(ok, "abandon requires a claimed ticket; ours is open")
        # claim it properly, then abandon
        self._claim(tid, session_id="zz-trx-session-aban")
        ok = self.adapter.abandon_ticket(plan_id, role, "zz-trx-session-aban")
        self.assertTrue(ok)
        events = self._events(tid)
        self.assertEqual(len(events), 1)
        etype, payload = events[0]
        self.assertEqual(etype, "transition.rejected")
        self.assertEqual(payload["from_status"], "claimed")
        self.assertEqual(payload["to_status"], "abandoned")

    # ── claim / close / release family: transition.committed ───────

    def test_claim_ticket_emits_committed_event(self):
        tid = self._ticket(status="open")
        cur = self.raw.cursor()
        cur.execute("SELECT plan_id, role FROM vision.tickets WHERE id=%s", (tid,))
        plan_id, role = cur.fetchone()
        self.raw.commit()
        got = self.adapter.claim_ticket(plan_id, role, "zz-trx-session-claim")
        self.assertEqual(got, tid)
        events = self._events(tid)
        self.assertEqual(len(events), 1)
        etype, payload = events[0]
        self.assertEqual(etype, "transition.committed")
        self.assertEqual(payload["from_status"], "open")
        self.assertEqual(payload["to_status"], "claimed")
        self.assertEqual(payload["reason"], "manual_claim")
        self.assertEqual(payload["session_id"], "zz-trx-session-claim")

    def test_close_ticket_emits_committed_event(self):
        tid = self._ticket(status="claimed", session_id="zz-trx-session-close")
        cur = self.raw.cursor()
        cur.execute("SELECT plan_id, role FROM vision.tickets WHERE id=%s", (tid,))
        plan_id, role = cur.fetchone()
        self.raw.commit()
        ok = self.adapter.close_ticket(plan_id, role, "zz-trx-session-close",
                                       terminal_status="completed")
        self.assertTrue(ok)
        events = self._events(tid)
        self.assertEqual(len(events), 1)
        etype, payload = events[0]
        self.assertEqual(etype, "transition.committed")
        self.assertEqual(payload["from_status"], "claimed")
        self.assertEqual(payload["to_status"], "completed")

    def test_release_ticket_emits_committed_event(self):
        tid = self._ticket(status="claimed", session_id="zz-trx-session-rel")
        cur = self.raw.cursor()
        cur.execute("SELECT plan_id, role FROM vision.tickets WHERE id=%s", (tid,))
        plan_id, role = cur.fetchone()
        self.raw.commit()
        ok = self.adapter.release_ticket(plan_id, role, "zz-trx-session-rel")
        self.assertTrue(ok)
        events = self._events(tid)
        self.assertEqual(len(events), 1)
        etype, payload = events[0]
        self.assertEqual(etype, "transition.committed")
        self.assertEqual(payload["from_status"], "claimed")
        self.assertEqual(payload["to_status"], "open")
        self.assertEqual(payload["reason"], "manual_release")

    def test_release_session_tickets_emits_per_ticket(self):
        t1 = self._ticket(status="claimed", session_id="zz-trx-session-multi")
        t2 = self._ticket(status="claimed", session_id="zz-trx-session-multi")
        n = self.adapter.release_session_tickets("zz-trx-session-multi")
        self.assertEqual(n, 2)
        for tid in (t1, t2):
            events = self._events(tid)
            self.assertEqual(len(events), 1, f"ticket {tid}: {events}")
            etype, payload = events[0]
            self.assertEqual(etype, "transition.committed")
            self.assertEqual(payload["to_status"], "open")
            self.assertEqual(payload["reason"], "session_released")

    # ── orphan closure: transition.rejected per closed ticket ──────

    def test_close_orphaned_tickets_emits_per_orphan(self):
        pid = self._plan_status("CANCELLED")  # no eligible roles
        tid = self._ticket(status="open")
        cur = self.raw.cursor()
        cur.execute("UPDATE vision.tickets SET plan_id=%s WHERE id=%s", (pid, tid))
        self.raw.commit()
        n = self.adapter.close_orphaned_tickets(pid)
        self.assertEqual(n, 1)
        events = self._events(tid)
        self.assertEqual(len(events), 1)
        etype, payload = events[0]
        self.assertEqual(etype, "transition.rejected")
        self.assertEqual(payload["from_status"], "open")
        self.assertEqual(payload["to_status"], "cancelled")
        self.assertIn("orphaned", payload["reason"])
        self.assertEqual(payload["plan_id"], pid)

    def test_close_orphaned_tickets_role_filter(self):
        # REVIEW_REJECT: only builder is eligible (critic's map has no
        # REVIEW_REJECT) — so the critic ticket orphans, builder survives.
        pid = self._plan_status("REVIEW_REJECT")
        critic_t = self._ticket(status="open", role="critic")
        builder_t = self._ticket(status="open", role="builder")
        cur = self.raw.cursor()
        for t in (critic_t, builder_t):
            cur.execute("UPDATE vision.tickets SET plan_id=%s WHERE id=%s", (pid, t))
        self.raw.commit()
        n = self.adapter.close_orphaned_tickets(pid)
        self.assertEqual(n, 1, "only the non-eligible role's ticket closes")
        events = self._events(critic_t)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][0], "transition.rejected")
        self.assertEqual(events[0][1]["to_status"], "cancelled")
        self.assertEqual(self._events(builder_t), [],
                         "eligible builder ticket must survive untouched")


if __name__ == "__main__":
    unittest.main()
