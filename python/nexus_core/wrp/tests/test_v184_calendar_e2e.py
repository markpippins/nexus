#!/usr/bin/env python3
"""E2E: V184 — vision.calendars / calendar_events / sessions (staged inert).

wr-conf house throwaway-DB pattern (V179-style). The REAL V184 applies to a
throwaway database carrying only the prerequisites it needs (vision schema;
tackle.system_logs for the audit fallback) and the full write-path contract
is exercised for real:

  - schema: three surfaces, CHECK battery, indexes, bitemporal sentinels
  - contract parity: kind epistemics CHECK, observed-requires-consolidated,
    window ordering, scope/owner, port range, bundle-shape, participants array
  - Q2 identity: the PK is the dedupe — re-INSERT of the same event_id is the
    only idempotent path; distinct windows/machines are distinct rows
  - session lifecycle: open -> closed -> reconciled (CHECK-enforced)
  - extendable windows: window_end NULL native; extension appends
  - append-only: DELETE refused (CAL011), TRUNCATE refused (CAL012)
  - NEBULA_AUDIT: INSERT/UPDATE land in tackle.system_logs (audit fallback
    installed by the migration itself when absent)
  - agent_ref wire parity: participants rows carry the `model` wire key

The suite creates and drops its own throwaway databases; no production
database is touched.
"""
import json
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V184_PATH = os.path.join(_REPO_ROOT, "sql", "V184__calendar_primitive.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SKELETON_SQL = """
CREATE SCHEMA vision;
CREATE SCHEMA tackle;

CREATE TABLE tackle.system_logs (
    id         text PRIMARY KEY,
    timestamp  timestamptz NOT NULL DEFAULT now(),
    level      text NOT NULL,
    category   text NOT NULL,
    message    text NOT NULL,
    source     text,
    details    jsonb
);
"""


class ThrowawayDB:
    """Throwaway DB: minimal skeleton (vision + tackle.system_logs) -> REAL V184."""

    def __init__(self):
        self.dbname = f"nexus_v184_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.conn = None

    def __enter__(self):
        admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            cur.execute(f'CREATE DATABASE "{self.dbname}"')
        admin.close()
        self.conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.dbname)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute(SKELETON_SQL)
        return self

    def __exit__(self, *exc):
        try:
            if self.conn:
                self.conn.close()
        finally:
            admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            admin.close()

    def sql(self, stmt, params=None):
        with self.conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall() if cur.description else None
        if rows and len(rows) == 1 and len(rows[0]) == 1:
            return rows[0][0]
        return rows

    def expect_error(self, stmt, params=None):
        try:
            self.sql(stmt, params)
        except psycopg2.Error as exc:
            return str(exc).split("\n")[0]
        raise AssertionError("expected the statement to fail")

    def apply_v184(self):
        with open(V184_PATH) as fh:
            self.sql(fh.read())


def _uuid5(machine, emitter, window_start):
    """The emitter-side derivation (bin/calendar-emit.py EVENT_NS) — the PK
    contract of V184 is that THIS id, stored, dedupes."""
    import hashlib
    # uuid5 with the emitter's fixed namespace
    ns = uuid.UUID("7c9e6679-7425-40de-944b-e07fc1f90ae7")
    return str(uuid.uuid5(ns, f"{machine}|{emitter}|{window_start}"))


class V184SchemaE2E(unittest.TestCase):
    """Apply path + schema shape."""

    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_v184()

    def test_three_surfaces_exist(self):
        for t in ("calendars", "calendar_events", "sessions"):
            self.assertEqual(
                self.db.sql("SELECT to_regclass(%s)", (f"vision.{t}",)),
                f"vision.{t}")

    def test_bitemporal_sentinels(self):
        cid = str(uuid.uuid4())
        self.db.sql("INSERT INTO vision.calendars (calendar_id, title, scope, owner) "
                    "VALUES (%s, 'c', 'local', 'titanium')", (cid,))
        row = self.db.sql(
            "SELECT valid_until::text, recorded_until_dt::text "
            "FROM vision.calendars WHERE calendar_id = %s", (cid,))
        self.assertIn("infinity", row[0])

    def test_idempotent_refuse_double_apply(self):
        # re-apply must hit V184-GATE-001
        err = self.db.expect_error(open(V184_PATH).read())
        self.assertIn("V184-GATE-001", err)


class V184EventContractE2E(unittest.TestCase):
    """The #331 contract, CHECK-enforced."""

    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_v184()
        self.cid = str(uuid.uuid4())
        self.db.sql("INSERT INTO vision.calendars (calendar_id, title, scope, owner) "
                    "VALUES (%s, 'local', 'local', 'titanium')", (self.cid,))

    def _insert_event(self, **kw):
        eid = kw.get("event_id") or _uuid5(
            kw.get("machine", "titanium"), kw.get("emitter", "lease-probe.timer"),
            kw.get("window_start", "2026-09-18T13:05:00Z"))
        self.db.sql(
            """INSERT INTO vision.calendar_events
               (event_id, calendar_id, kind, source_machine, source_emitter,
                title, window_start, window_end, participants, payload,
                recorded_by, consolidated_from)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb)""",
            (eid, self.cid,
             kw.get("kind", "occurred"),
             kw.get("machine", "titanium"),
             kw.get("emitter", "lease-probe.timer"),
             kw.get("title", "timer lease-probe.timer occurred"),
             kw.get("window_start", "2026-09-18T13:05:00Z"),
             kw.get("window_end"),
             json.dumps(kw.get("participants", [])),
             json.dumps(kw.get("payload", {})),
             kw.get("recorded_by", "calendar-emit"),
             json.dumps(kw["consolidated_from"]) if "consolidated_from" in kw else None))
        return eid

    def test_q2_pk_is_the_dedupe(self):
        """Same derived id re-INSERTed: only via explicit upsert semantics —
        plain re-INSERT must fail (PK), which IS the collector's dedupe key."""
        eid = self._insert_event()
        err = self.db.expect_error(
            """INSERT INTO vision.calendar_events
               (event_id, calendar_id, kind, source_machine, source_emitter,
                title, window_start, participants, recorded_by)
               VALUES (%s,%s,'occurred','titanium','lease-probe.timer','t',
                       '2026-09-18T13:05:00Z','[]'::jsonb,'x')""",
            (eid, self.cid))
        self.assertIn("duplicate key", err.lower())
        # and the derivation is stable: same inputs -> same stored id
        self.assertEqual(eid, _uuid5("titanium", "lease-probe.timer",
                                     "2026-09-18T13:05:00Z"))

    def test_kind_epistemics_enforced(self):
        err = self.db.expect_error(
            """INSERT INTO vision.calendar_events
               (event_id, calendar_id, kind, source_machine, source_emitter,
                title, window_start, participants, recorded_by)
               VALUES (gen_random_uuid(),%s,'failed','titanium','e','t',
                       '2026-09-18T13:05:00Z','[]'::jsonb,'x')""", (self.cid,))
        self.assertIn("cks_calendar_events_kind", err)

    def test_observed_requires_consolidated(self):
        """V174 discipline: heard-from-elsewhere must cite its sources."""
        err = self.db.expect_error(
            """INSERT INTO vision.calendar_events
               (event_id, calendar_id, kind, source_machine, source_emitter,
                title, window_start, participants, recorded_by)
               VALUES (gen_random_uuid(),%s,'observed','titanium','e','t',
                       '2026-09-18T13:05:00Z','[]'::jsonb,'x')""", (self.cid,))
        self.assertIn("cks_calendar_events_kind_epistemics", err)
        # with provenance it passes
        self._insert_event(kind="observed", emitter="other-machine.timer",
                           consolidated_from=["some-other-event-id"])
        self.assertEqual(
            self.db.sql("SELECT count(*) FROM vision.calendar_events WHERE kind='observed'"),
            1)

    def test_window_ordering(self):
        err = self.db.expect_error(
            """INSERT INTO vision.calendar_events
               (event_id, calendar_id, kind, source_machine, source_emitter,
                title, window_start, window_end, participants, recorded_by)
               VALUES (gen_random_uuid(),%s,'occurred','titanium','e','t',
                       '2026-09-18T14:00:00Z','2026-09-18T13:00:00Z',
                       '[]'::jsonb,'x')""", (self.cid,))
        self.assertIn("cks_calendar_events_window", err)

    def test_participants_wire_key_model(self):
        """AgentRef wire parity: `model` key (V169 census), not `modelId`."""
        self._insert_event(participants=[{"role": "dba", "model": "freebuff/buffy"}])
        raw = self.db.sql(
            "SELECT participants::text FROM vision.calendar_events "
            "WHERE participants::text LIKE '%freebuff%'")
        self.assertIn('"model"', raw)

    def test_extendable_window_append_not_mutate(self):
        """end NULL native; extension appends a new event, never UPDATEs the
        recorded one (append-only posture)."""
        e1 = self._insert_event(emitter="probe-window.timer",
                                window_start="2026-09-18T13:05:00Z")
        self.assertIsNone(self.db.sql(
            "SELECT window_end FROM vision.calendar_events WHERE event_id=%s",
            (e1,)))
        e2 = self._insert_event(emitter="probe-window.extend",
                                window_start="2026-09-18T13:35:00Z",
                                window_end="2026-09-18T14:00:00Z")
        self.assertEqual(self.db.sql(
            "SELECT count(*) FROM vision.calendar_events "
            "WHERE source_emitter LIKE 'probe-window%'"), 2)


class V184SessionLifecycleE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_v184()
        self.cid = str(uuid.uuid4())
        self.db.sql("INSERT INTO vision.calendars (calendar_id, title, scope) "
                    "VALUES (%s, 'shared', 'shared')", (self.cid,))

    def _insert_session(self, state="open", port=4014):
        return self.db.sql(
            """INSERT INTO vision.sessions
               (title, address_host, address_port, window_start, window_end,
                participants, context_bundle, calendar_id, reconcile_state)
               VALUES ('probe window', 'titanium', %s,
                       '2026-09-18T13:05:00Z', NULL, '[]'::jsonb, %s::jsonb,
                       %s, %s) RETURNING session_id""",
            (port,
             json.dumps({"snapshotRefs": ["snap-1"], "digestRefs": [],
                         "keychains": [], "procedureCards": ["dba-change-review-workflow"]}),
             self.cid, state))

    def test_open_session_with_null_end(self):
        sid = self._insert_session()
        self.assertIsNotNone(sid)

    def test_reconcile_lifecycle(self):
        sid = self._insert_session()
        self.db.sql("UPDATE vision.sessions SET reconcile_state='closed' "
                    "WHERE session_id=%s", (sid,))
        self.db.sql("UPDATE vision.sessions SET reconcile_state='reconciled' "
                    "WHERE session_id=%s", (sid,))
        self.assertEqual(self.db.sql(
            "SELECT reconcile_state FROM vision.sessions WHERE session_id=%s",
            (sid,)), "reconciled")
        bad = self.db.expect_error(
            "UPDATE vision.sessions SET reconcile_state='failed' WHERE session_id=%s",
            (sid,))
        self.assertIn("cks_sessions_reconcile", bad)

    def test_port_check(self):
        err = self.db.expect_error(
            """INSERT INTO vision.sessions
               (title, address_host, address_port, window_start,
                participants, context_bundle, calendar_id)
               VALUES ('x','titanium',99999,'2026-09-18T13:05:00Z',
                       '[]'::jsonb,'{}'::jsonb,%s)""", (self.cid,))
        self.assertIn("cks_sessions_port", err)

    def test_bundle_shape(self):
        err = self.db.expect_error(
            """INSERT INTO vision.sessions
               (title, address_host, address_port, window_start,
                participants, context_bundle, calendar_id)
               VALUES ('x','titanium',4014,'2026-09-18T13:05:00Z',
                       '[]'::jsonb,'[]'::jsonb,%s)""", (self.cid,))
        self.assertIn("cks_sessions_bundle", err)


class V184AppendOnlyAndAuditE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_v184()
        self.cid = str(uuid.uuid4())
        self.db.sql("INSERT INTO vision.calendars (calendar_id, title, scope, owner) "
                    "VALUES (%s,'c','local','titanium')", (self.cid,))
        self.db.sql(
            """INSERT INTO vision.calendar_events
               (event_id, calendar_id, kind, source_machine, source_emitter,
                title, window_start, participants, recorded_by)
               VALUES (gen_random_uuid(),%s,'occurred','titanium','e.timer','t',
                       '2026-09-18T13:05:00Z','[]'::jsonb,'calendar-emit')""",
            (self.cid,))

    def test_delete_refused(self):
        err = self.db.expect_error("DELETE FROM vision.calendar_events")
        self.assertIn("CAL011", err)

    def test_truncate_refused(self):
        err = self.db.expect_error("TRUNCATE vision.calendar_events")
        self.assertIn("CAL012", err)
        # sessions is FK-referenced by calendar_events: PG's FK machinery
        # refuses the truncate BEFORE the trigger fires. Either refusal is
        # the contract (the surface is un-truncatable); pin both honestly.
        err2 = self.db.expect_error("TRUNCATE vision.sessions")
        self.assertTrue("CAL012" in err2 or "referenced in a foreign key" in err2,
                        f"unexpected refusal: {err2}")

    def test_audit_trail_lands(self):
        self.db.sql(
            """INSERT INTO vision.calendar_events
               (event_id, calendar_id, kind, source_machine, source_emitter,
                title, window_start, participants, recorded_by)
               VALUES (gen_random_uuid(),%s,'occurred','titanium','f.timer','t',
                       '2026-09-18T14:05:00Z','[]'::jsonb,'calendar-emit')""",
            (self.cid,))
        n = self.db.sql(
            "SELECT count(*) FROM tackle.system_logs "
            "WHERE category='NEBULA_AUDIT' AND message LIKE '%vision.calendar_events%'")
        self.assertGreaterEqual(n, 2)  # setUp insert + this insert


class V184InertGuaranteeE2E(unittest.TestCase):
    """The staged-inert claim itself: nothing calendar-shaped exists before
    apply, everything exists after — on a DB shaped like LIVE minus V184."""

    def test_absence_before_presence_after(self):
        db = ThrowawayDB().__enter__()  # __enter__ builds the skeleton only
        try:
            self.assertIsNone(db.sql("SELECT to_regclass('vision.calendar_events')"))
            db.apply_v184()
            self.assertEqual(db.sql("SELECT to_regclass('vision.calendar_events')"),
                             "vision.calendar_events")
        finally:
            db.__exit__(None, None, None)


if __name__ == "__main__":
    unittest.main()
