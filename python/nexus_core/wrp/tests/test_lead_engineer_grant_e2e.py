#!/usr/bin/env python3
"""E2E: lead-engineer grant v0.1 — Wave-3 greenlight authority (throwaway DB).

wr-conf-025/026/029/030 companion, house throwaway-DB pattern. Drives the
REAL grant file (sql/grants/lead-engineer-grant-v0.1.sql) through:

  - the ratified shape: greenlight=TRUE, verify=FALSE (the separation),
    domains={implementation_supervision}, escalate/approve {architect}
  - the separation as a HARD gate: the file refuses to open a successor
    with can_verify=TRUE alongside can_greenlight=TRUE
  - chain shape: closed=1 open=1, handoff exact
  - repeatability: a second application is a second lawful grant event
  - no-open-snapshot refusal (idempotency-of-refusal)
  - greenlight holder set: planner + lead-engineer, exactly two
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V180_PATH = os.path.join(_REPO_ROOT, "sql", "V180__applied_grants_preflight.sql")
GRANT_PATH = os.path.join(_REPO_ROOT, "sql", "grants",
                          "lead-engineer-grant-v0.1.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SKELETON_SQL = """
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;

CREATE TABLE nebula.roles_history (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                    text NOT NULL,
    display_name            text,
    description             text,
    owns_domains            text[] DEFAULT '{}',
    can_greenlight          boolean DEFAULT false,
    can_create_questions    boolean DEFAULT false,
    can_create_agendas      boolean DEFAULT false,
    can_resolve_questions   boolean DEFAULT false,
    can_verify_work_requests boolean DEFAULT false,
    max_open_questions      integer DEFAULT 0,
    requires_approval_from  text[] DEFAULT '{}',
    cron_enabled            boolean DEFAULT false,
    cron_expression         text,
    cron_description        text,
    escalates_to            text[] DEFAULT '{}',
    escalation_triggers     text[] DEFAULT '{}',
    level_filter_primary    text,
    level_filter_allowed    text,
    visibility_scope        text[] DEFAULT '{}',
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_until             timestamptz NOT NULL DEFAULT '9999-12-31'::timestamptz,
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT '9999-12-31'::timestamptz
);

CREATE VIEW nebula.roles AS
SELECT * FROM nebula.roles_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;

INSERT INTO nebula.roles_history (name, display_name, description)
VALUES ('lead-engineer', 'Lead Engineer', 'implementation supervision (capability-empty pending Wave-3 grant)');
"""


class ThrowawayDB:
    """Throwaway DB: skeleton (V175-shaped roles world) -> REAL grant file."""

    def __init__(self):
        self.dbname = f"nexus_le_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def apply_grant(self):
        with open(GRANT_PATH) as fh:
            self.sql(fh.read())

    def apply_file(self, path):
        with open(path) as fh:
            self.sql(fh.read())

    def open_row(self):
        rows = self.sql(
            "SELECT owns_domains, can_greenlight, can_verify_work_requests, "
            "requires_approval_from, escalates_to, escalation_triggers, "
            "level_filter_primary, valid_from "
            "FROM nebula.roles_history WHERE name='lead-engineer' "
            "AND valid_until >= '9999-12-31'")
        return rows[0] if isinstance(rows, list) else rows


class LeadEngineerGrantE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_file(V180_PATH)  # rediff gate (GRANT-APPLIED)

    def test_grant_event_shape(self):
        self.db.apply_grant()
        domains, greenlight, verify, approval, escalates, triggers, level, _ = \
            self.db.open_row()
        self.assertEqual(["implementation_supervision"], domains)
        self.assertTrue(greenlight)
        self.assertFalse(verify)          # the separation
        self.assertEqual(["architect"], approval)
        self.assertEqual(["architect"], escalates)
        self.assertIn("greenlight_without_verification_attestation", triggers)
        self.assertEqual("<=3", level)
        closed = self.db.sql(
            "SELECT count(*) FROM nebula.roles_history "
            "WHERE name='lead-engineer' AND valid_until < '9999-12-31'")
        self.assertEqual(1, closed)       # chain: 1 closed + 1 open

    def test_separation_is_a_hard_gate(self):
        """The file must REFUSE to grant greenlight without the separation:
        tamper the value block (verify=TRUE) -> the verify gate refuses, the
        transaction rolls back, and the role keeps its pre-grant shape."""
        with open(GRANT_PATH) as fh:
            body = fh.read()
        tampered = body.replace(
            "v_can_verify_wrs        boolean := FALSE;",
            "v_can_verify_wrs        boolean := TRUE;")
        self.assertNotEqual(body, tampered, "tamper anchor not found")
        err = self.db.expect_error(tampered)
        self.assertIn("GRANT verify: can_verify_work_requests must be FALSE", err)
        # rollback proof from a fresh connection: the tampered grant must have
        # left NO granted successor behind — still exactly the seeded open row
        fresh = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.db.dbname)
        fresh.autocommit = True
        with fresh.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE can_greenlight) "
                "FROM nebula.roles_history WHERE name='lead-engineer'")
            total, granted = cur.fetchone()
            self.assertEqual((1, 0), (total, granted))
        fresh.close()

    def test_reapply_same_spec_refuses_GRANT_APPLIED(self):
        """V180 rediff gate: identical re-apply = loud no-op (self-idempotence)."""
        self.db.apply_grant()
        first_from = self.db.open_row()[7]
        refused = ""
        try:
            self.db.apply_grant()
        except psycopg2.Error as exc:
            refused = str(exc).splitlines()[0]
            try:
                self.db.sql("ROLLBACK")
            except psycopg2.Error:
                pass
        else:
            self.fail("identical re-apply must refuse with GRANT-APPLIED")
        self.assertIn("GRANT-APPLIED", refused)
        closed = self.db.sql(
            "SELECT count(*) FROM nebula.roles_history "
            "WHERE name='lead-engineer' AND valid_until < '9999-12-31'")
        self.assertEqual(1, closed, "no redundant event minted")
        self.assertEqual(self.db.open_row()[7], first_from)

    def test_no_open_snapshot_refused(self):
        self.db.apply_grant()
        # close the open row by hand -> nothing left to grant onto
        self.db.sql(
            "UPDATE nebula.roles_history SET valid_until = now(), "
            "recorded_until_dt = now() WHERE name='lead-engineer'")
        err = self.db.expect_error(
            open(GRANT_PATH).read())
        self.assertIn("GRANT: no OPEN snapshot found", err)

    def test_greenlight_holder_set_after_grant(self):
        self.db.apply_grant()
        self.db.sql(
            "INSERT INTO nebula.roles_history (name, can_greenlight) "
            "VALUES ('planner', true)")
        holders = self.db.sql(
            "SELECT array_agg(name ORDER BY name) FROM nebula.roles "
            "WHERE can_greenlight")
        self.assertEqual(["lead-engineer", "planner"], holders)


if __name__ == "__main__":
    unittest.main()
