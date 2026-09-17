#!/usr/bin/env python3
"""E2E: tester capability grant v0.1 — the corrected grant template's second
use, and its first with a TRUE capability boolean (can_verify_work_requests).

wr-conf-025 companion, house throwaway-DB pattern (mirrors
test_v175_roles_history_e2e.py): the skeleton recreates the pre-V175 world,
V175 repairs it, then the grant file executes as a real grant event.

Pinned:
  - grant runs end-to-end: closed=1 open=1, handoff exact
  - granted fields: owns_domains={test-verification}, can_verify_work_requests=TRUE
  - deliberately-not-granted fields stay false/0 (greenlight, questions, agendas)
  - escalation posture: escalates_to={architect} + the four refusal triggers
  - repeatability: a second run is another grant event (chain grows, handoff exact)
  - refusals: unknown role fails loudly; pre-V175 shape aborts atomically
    (close rolls back with the failed successor INSERT)
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V175_PATH = os.path.join(_REPO_ROOT, "sql", "V175__roles_history_close_then_insert_repair.sql")
V180_PATH = os.path.join(_REPO_ROOT, "sql", "V180__applied_grants_preflight.sql")
GRANT_PATH = os.path.join(_REPO_ROOT, "sql", "grants", "tester-grant-v0.1.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SENTINEL = "9999-12-31 00:00:00+00"


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_tester_grant_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def apply_file(self, path):
        with open(path) as fh:
            self.sql(fh.read())

    def seed_role(self, name="tester"):
        self.sql("""
INSERT INTO nebula.roles_history (name, display_name, description)
VALUES (%s, 'Tester', 'Ratified role vocabulary (roles thread 435c7a3e)')
""", (name,))


SKELETON_SQL = f"""
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;

CREATE TABLE nebula.roles_history (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text NOT NULL,
    display_name            text,
    description             text,
    owns_domains            text[] DEFAULT '{{}}',
    can_greenlight          boolean DEFAULT false,
    can_create_questions    boolean DEFAULT false,
    can_create_agendas      boolean DEFAULT false,
    can_resolve_questions   boolean DEFAULT false,
    can_verify_work_requests boolean DEFAULT false,
    max_open_questions      integer DEFAULT 0,
    requires_approval_from  text[] DEFAULT '{{}}',
    cron_enabled            boolean DEFAULT false,
    cron_expression         text,
    cron_description        text,
    escalates_to            text[] DEFAULT '{{}}',
    escalation_triggers     text[] DEFAULT '{{}}',
    level_filter_primary    text,
    level_filter_allowed    text,
    visibility_scope        text[] DEFAULT '{{}}',
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_until             timestamptz NOT NULL DEFAULT '{SENTINEL}'::timestamptz,
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT '{SENTINEL}'::timestamptz,
    CONSTRAINT roles_name_key UNIQUE (name)   -- the pre-V175 shape
);

CREATE TABLE tackle.role_leases (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role        text NOT NULL,
    channel     text,
    model       text,
    status      text NOT NULL DEFAULT 'ACTIVE',
    acquired_at timestamptz DEFAULT now(),
    expires_at  timestamptz,
    released_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tackle.system_logs (
    id        text PRIMARY KEY,
    timestamp timestamptz NOT NULL DEFAULT now(),
    level     text NOT NULL,
    category  text NOT NULL,
    message   text NOT NULL,
    source    text,
    details   jsonb
);

CREATE VIEW nebula.roles AS
SELECT * FROM nebula.roles_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;
"""


class TesterGrantE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        self.db.seed_role()
        self.db.apply_file(V175_PATH)  # grant requires the V175 shape
        self.db.apply_file(V180_PATH)  # rediff gate (GRANT-APPLIED)

    def _open_row(self):
        rows = self.db.sql("""
SELECT owns_domains, can_verify_work_requests, can_greenlight,
       can_create_questions, can_create_agendas, can_resolve_questions,
       max_open_questions, requires_approval_from, escalates_to,
       escalation_triggers, valid_from
FROM nebula.roles_history
WHERE name='tester' AND valid_until = %s::timestamptz
""", (SENTINEL,))
        self.assertEqual(len(rows), 1, "exactly one open snapshot")
        return rows[0]

    def test_grant_runs_end_to_end(self):
        self.db.apply_file(GRANT_PATH)
        chain = self.db.sql("""
SELECT count(*) FILTER (WHERE valid_until = %s::timestamptz) AS open,
       count(*) FILTER (WHERE valid_until <> %s::timestamptz) AS closed
FROM nebula.roles_history WHERE name='tester'
""", (SENTINEL, SENTINEL))
        self.assertEqual(tuple(chain[0]), (1, 1))
        # handoff exact: open.valid_from == closed.valid_until
        handoff = self.db.sql("""
SELECT o.valid_from = c.valid_until FROM nebula.roles_history o
JOIN nebula.roles_history c
  ON c.name='tester' AND c.valid_until <> %s::timestamptz
WHERE o.name='tester' AND o.valid_until = %s::timestamptz
""", (SENTINEL, SENTINEL))
        self.assertTrue(handoff)

    def test_granted_fields(self):
        self.db.apply_file(GRANT_PATH)
        (domains, verify, *_rest) = self._open_row()
        self.assertEqual(list(domains), ["test-verification"])
        self.assertTrue(verify)  # the point of the grant

    def test_deliberately_not_granted(self):
        self.db.apply_file(GRANT_PATH)
        (_d, _v, greenlight, create_q, create_agenda, resolve_q,
         max_open, _req, _esc, _trig, _vf) = self._open_row()
        self.assertFalse(greenlight)
        self.assertFalse(create_q)
        self.assertFalse(create_agenda)
        self.assertFalse(resolve_q)
        self.assertEqual(max_open, 0)

    def test_escalation_posture(self):
        self.db.apply_file(GRANT_PATH)
        (_d, _v, _g, _cq, _ca, _rq, _m, requires, escalates, triggers,
         _vf) = self._open_row()
        self.assertEqual(list(requires), ["architect"])
        self.assertEqual(list(escalates), ["architect"])
        self.assertEqual(
            list(triggers),
            ["greenlight_pressure", "wr_verification_without_evidence",
             "untested_merge_pressure", "flaky_or_skipped_tests"])

    def test_grant_visible_through_view(self):
        self.db.apply_file(GRANT_PATH)
        rows = self.db.sql("""
SELECT owns_domains, can_verify_work_requests FROM nebula.roles
WHERE name='tester'
""")
        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0][0]), ["test-verification"])
        self.assertTrue(rows[0][1])

    def test_reapply_same_spec_refuses_GRANT_APPLIED(self):
        """V180 rediff gate: identical re-apply = loud no-op (self-idempotence)."""
        self.db.apply_file(GRANT_PATH)
        refused = ""
        try:
            self.db.apply_file(GRANT_PATH)
        except psycopg2.Error as exc:
            refused = str(exc).splitlines()[0]
            try:
                self.db.sql("ROLLBACK")
            except psycopg2.Error:
                pass
        else:
            self.fail("identical re-apply must refuse with GRANT-APPLIED")
        self.assertIn("GRANT-APPLIED", refused)
        chain = self.db.sql("""
SELECT count(*) FILTER (WHERE valid_until = %s::timestamptz) AS open,
       count(*) FILTER (WHERE valid_until <> %s::timestamptz) AS closed
FROM nebula.roles_history WHERE name='tester'
""", (SENTINEL, SENTINEL))
        self.assertEqual(tuple(chain[0]), (1, 1))
        # handoff stays exact across events
        handoff = self.db.sql("""
SELECT o.valid_from = c.valid_until FROM nebula.roles_history o
JOIN nebula.roles_history c
  ON c.name='tester' AND c.valid_until <> %s::timestamptz
WHERE o.name='tester' AND o.valid_until = %s::timestamptz
  AND c.valid_until = (SELECT max(valid_until) FROM nebula.roles_history
                       WHERE name='tester' AND valid_until <> %s::timestamptz)
""", (SENTINEL, SENTINEL, SENTINEL))
        self.assertTrue(handoff)
        # granted values carry into the newest open snapshot
        (_d, verify, *_r) = self._open_row()
        self.assertTrue(verify)

    def test_grant_refuses_unknown_role(self):
        db2 = ThrowawayDB().__enter__()
        self.addCleanup(db2.__exit__)
        db2.apply_file(V175_PATH)  # right shape, but no tester row
        db2.apply_file(V180_PATH)  # pre-flight is universal
        with open(GRANT_PATH) as fh:
            err = db2.expect_error(fh.read())
        self.assertIn("no OPEN snapshot", err)

    def test_grant_refuses_pre_v175_shape_atomically(self):
        db2 = ThrowawayDB().__enter__()
        self.addCleanup(db2.__exit__)
        db2.seed_role()  # NO V175 — full UNIQUE(name) still in place
        db2.apply_file(V180_PATH)  # pre-flight is universal; tested refusal is the SHAPE trap
        with open(GRANT_PATH) as fh:
            err = db2.expect_error(fh.read())
        self.assertIn("roles_name_key", err)
        # the whole grant transaction rolled back SERVER-SIDE: a fresh
        # connection (the granting connection is left in aborted state)
        # sees the seed row untouched and still open
        fresh = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + db2.dbname)
        fresh.autocommit = True
        try:
            with fresh.cursor() as cur:
                cur.execute("""
SELECT count(*), count(*) FILTER (WHERE valid_until = %s::timestamptz)
FROM nebula.roles_history WHERE name='tester'
""", (SENTINEL,))
                remaining = cur.fetchone()
        finally:
            fresh.close()
        self.assertEqual(tuple(remaining), (1, 1))


if __name__ == "__main__":
    unittest.main()
