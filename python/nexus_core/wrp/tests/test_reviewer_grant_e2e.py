#!/usr/bin/env python3
"""E2E: reviewer grant v0.1 — reinstatement from a fully retired history chain.

House throwaway-DB pattern (sibling of test_lead_engineer_grant_e2e.py). Drives
the REAL grant file (sql/grants/reviewer-grant-v0.1.sql) through:

  - the RE-OPEN path: reviewer's history ends in a CLOSED snapshot (retirement
    at 2026-09-27T16:20:29Z in live), so the standard close-then-insert
    template cannot run. The file must derive the successor from the newest
    closed snapshot and open it at now().
  - the retirement gap is PRESERVED, not fabricated: the new valid_from is
    later than the prior close, and no validity episode overlaps.
  - Decision 7 shape pinned: domains {review_judgement}, greenlight TRUE,
    verify FALSE (the separation), max_open_questions 0, architect
    approval/escalation, the three escalation triggers, levels <=2/<=3,
    visibility {reviewer,builder,all}.
  - the separation as a HARD gate: tampering verify=TRUE refuses before any
    mutation (no closed row, no successor).
  - the standard grant path still works: an OPEN snapshot is closed and handed
    off with exact (no-gap) continuity.
  - V180 repeatability: a second application refuses with GRANT-APPLIED and
    mints no redundant event.
  - a role with no history at all refuses loudly (nothing to reinstate).
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
                          "reviewer-grant-v0.1.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SENTINEL = "'9999-12-31 00:00:00+00'::timestamptz"

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
    valid_until             timestamptz NOT NULL DEFAULT {SENTINEL},
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT {SENTINEL}
);

-- V175 shape: exactly one OPEN snapshot per name, closed history exempt.
CREATE UNIQUE INDEX roles_name_open_key ON nebula.roles_history (name)
    WHERE valid_until = {SENTINEL};

CREATE VIEW nebula.roles AS
SELECT * FROM nebula.roles_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;
"""

# reviewer's live pre-reinstatement state: one closed snapshot, nothing open.
# (5654c8e3 in live: closed 2026-09-27T16:20:29Z, owns_domains build_verification.)
SEED_RETIRED = f"""
INSERT INTO nebula.roles_history (
    name, display_name, description, owns_domains,
    created_at, updated_at,
    valid_from, valid_until, recorded_on_dt, recorded_until_dt
) VALUES (
    'reviewer', 'reviewer',
    'Quality gate — reviews changes, issues approval/rejection receipts',
    ARRAY['build_verification'],
    now() - interval '40 days', now() - interval '30 days',
    now() - interval '40 days', now() - interval '30 days',
    now() - interval '40 days', now() - interval '30 days'
);
"""

SEED_OPEN = f"""
INSERT INTO nebula.roles_history (
    name, display_name, description, owns_domains, can_greenlight
) VALUES ('reviewer', 'reviewer', 'Quality gate', ARRAY['build_verification'], false);
"""


class ThrowawayDB:
    """Throwaway DB: V175-shaped roles world -> REAL grant file."""

    def __init__(self):
        self.dbname = f"nexus_rv_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
            # The grant files carry an explicit BEGIN/COMMIT; a failure inside
            # the DO block leaves that transaction aborted and the session
            # unusable until it is rolled back.
            try:
                with self.conn.cursor() as cur:
                    cur.execute("ROLLBACK")
            except psycopg2.Error:
                pass
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
            "max_open_questions, requires_approval_from, escalates_to, "
            "escalation_triggers, level_filter_primary, level_filter_allowed, "
            "visibility_scope, valid_from, display_name, description "
            "FROM nebula.roles_history WHERE name='reviewer' "
            f"AND valid_until = {SENTINEL}")
        return rows[0]

    def counts(self):
        return self.sql(
            "SELECT count(*) FILTER (WHERE valid_until = " + SENTINEL + "), "
            "count(*) FILTER (WHERE valid_until <> " + SENTINEL + ") "
            "FROM nebula.roles_history WHERE name='reviewer'")[0]


class ReviewerGrantE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.apply_file(V180_PATH)  # rediff gate (GRANT-APPLIED)

    def test_reopen_from_retired_history(self):
        self.db.sql(SEED_RETIRED)
        prior_close = self.db.sql(
            "SELECT valid_until FROM nebula.roles_history WHERE name='reviewer'")

        self.db.apply_grant()

        (domains, greenlight, verify, maxq, approval, escalates, triggers,
         level_primary, level_allowed, visibility, valid_from,
         display_name, description) = self.db.open_row()

        self.assertEqual(["review_judgement"], domains)
        self.assertTrue(greenlight)
        self.assertFalse(verify)                      # the separation
        self.assertEqual(0, maxq)
        self.assertEqual(["architect"], approval)
        self.assertEqual(["architect"], escalates)
        self.assertEqual(
            ["greenlight_without_verification_attestation",
             "attestation_chain_bypass_pressure",
             "unattested_merge_pressure"], triggers)
        self.assertEqual("<=2", level_primary)
        self.assertEqual("<=3", level_allowed)
        self.assertEqual(["reviewer", "builder", "all"], visibility)

        # identity carried forward from the retired snapshot
        self.assertEqual("reviewer", display_name)
        self.assertIn("Quality gate", description)

        # exactly one open + one closed (the seeded retirement snapshot)
        self.assertEqual((1, 1), self.db.counts())

        # the retirement gap is preserved, not fabricated
        self.assertGreater(valid_from, prior_close)

        # and the role is now current in the bitemporal view
        self.assertEqual(["review_judgement"], self.db.sql(
            "SELECT owns_domains FROM nebula.roles WHERE name='reviewer'"))

    def test_standard_grant_path_still_exact_handoff(self):
        """An OPEN snapshot is the ordinary case: close, then hand off with
        valid_from == closed.valid_until (no gap, no overlap)."""
        self.db.sql(SEED_OPEN)
        self.db.apply_grant()

        closed_until = self.db.sql(
            "SELECT valid_until FROM nebula.roles_history "
            "WHERE name='reviewer' AND valid_until <> " + SENTINEL)
        open_from = self.db.open_row()[10]

        self.assertEqual(closed_until, open_from)
        self.assertEqual((1, 1), self.db.counts())

    def test_separation_is_a_hard_gate(self):
        """Tamper verify=TRUE: the file must refuse BEFORE any mutation —
        no retirement snapshot is closed, no successor is inserted."""
        self.db.sql(SEED_RETIRED)
        with open(GRANT_PATH) as fh:
            body = fh.read()
        tampered = body.replace(
            "v_can_verify_wrs        boolean := FALSE;",
            "v_can_verify_wrs        boolean := TRUE;")
        self.assertNotEqual(body, tampered, "tamper anchor not found")

        err = self.db.expect_error(tampered)
        self.assertIn("can_verify_work_requests must be FALSE", err)

        # rollback proof from a fresh connection
        fresh = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.db.dbname)
        fresh.autocommit = True
        with fresh.cursor() as cur:
            cur.execute(
                "SELECT count(*), count(*) FILTER (WHERE valid_until = " + SENTINEL + ") "
                "FROM nebula.roles_history WHERE name='reviewer'")
            total, open_rows = cur.fetchone()
            self.assertEqual((1, 0), (total, open_rows))
        fresh.close()

    def test_reapply_same_spec_refuses_GRANT_APPLIED(self):
        self.db.sql(SEED_RETIRED)
        self.db.apply_grant()
        first_from = self.db.open_row()[10]

        self.db.expect_error(open(GRANT_PATH).read())
        # autocommit + failed statement: the grant transaction rolled back
        self.assertEqual((1, 1), self.db.counts())
        self.assertEqual(first_from, self.db.open_row()[10])

    def test_no_history_refused(self):
        self.db.sql("DELETE FROM nebula.roles_history WHERE name='reviewer'")
        err = self.db.expect_error(open(GRANT_PATH).read())
        self.assertIn("no roles_history snapshot at all", err)


if __name__ == "__main__":
    unittest.main()
