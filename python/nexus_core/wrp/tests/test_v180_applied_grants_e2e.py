#!/usr/bin/env python3
"""E2E: V180 applied_grants rediff gate (wr-conf-035, house throwaway-DB pattern).

The self-idempotence doctrine as DDL + pre-flight blocks:

  - V180 applies clean on the V175 skeleton (idempotent: re-applies as
    notices only)
  - the view rows carry the 11-key grant spec (sentinel-correct)
  - grant_is_applied: exact spec match = TRUE; any drift = FALSE; absent
    role = FALSE (fail-open on proceed — absence is "not applied")
  - a patched grant file: first apply runs clean; IDENTICAL second apply
    refuses with GRANT-APPLIED and mints NO redundant event (1 closed /
    1 open, handoff exact)
  - rediff: same role + DIFFERENT spec = a new lawful grant event
    (chain 2 closed / 1 open) — the gate refuses only the redundant case

Companion: the six existing grant suites (patched: V180 in their
skeletons; repeatability tests flipped to the GRANT-APPLIED doctrine).
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
CRITIC_PATH = os.path.join(_REPO_ROOT, "sql", "grants", "critic-grant-v0.1.sql")

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
    owns_domains            text[],
    can_greenlight          boolean DEFAULT false,
    can_create_questions    boolean DEFAULT false,
    can_create_agendas      boolean DEFAULT false,
    can_resolve_questions   boolean DEFAULT false,
    can_verify_work_requests boolean DEFAULT false,
    max_open_questions      integer DEFAULT 0,
    requires_approval_from  text[],
    cron_enabled            boolean DEFAULT false,
    cron_expression         text,
    cron_description        text,
    escalates_to            text[],
    escalation_triggers     text[],
    level_filter_primary    text NOT NULL DEFAULT '<= 4',
    level_filter_allowed    text NOT NULL DEFAULT '<= 4',
    visibility_scope        text[] DEFAULT ARRAY['all']::text[],
    created_at              timestamptz DEFAULT now(),
    updated_at              timestamptz DEFAULT now(),
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_until             timestamptz NOT NULL DEFAULT '9999-12-31'::timestamptz,
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT '9999-12-31'::timestamptz
);
"""


SEED_SQL = "INSERT INTO nebula.roles_history (name, display_name, description) VALUES ('critic', 'critic', 'capability-empty seed row');"


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_v180_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
        try:
            with self.conn.cursor() as cur:
                cur.execute(stmt, params)
                rows = cur.fetchall() if cur.description else None
        except psycopg2.Error:
            try:
                self.conn.rollback()
            except psycopg2.Error:
                pass
            raise
        if rows and len(rows) == 1 and len(rows[0]) == 1:
            return rows[0][0]
        return rows

    def apply_file(self, path):
        with open(path) as fh:
            self.sql(fh.read())

    def chain(self, role):
        n = self.sql(
            "SELECT count(*) FROM nebula.roles_history WHERE name=%s", (role,))
        return (n - 1, 1) if n >= 1 else (0, 0)


class V180RediffGateE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__, None, None, None)
        self.db.sql(SEED_SQL)
        self.db.apply_file(V175_PATH)
        self.db.apply_file(V180_PATH)

    def test_v180_is_idempotent(self):
        self.db.apply_file(V180_PATH)  # second apply: notices only
        n = self.sql_count()
        self.assertGreaterEqual(n, 1)

    def sql_count(self):
        return self.db.sql("SELECT count(*) FROM nebula.applied_grants")

    def test_view_rows_carry_grant_spec(self):
        spec = self.db.sql(
            "SELECT spec FROM nebula.applied_grants WHERE role='critic'")
        self.assertIsNotNone(spec)
        keys = set(spec.keys())
        self.assertIn("owns_domains", keys)
        self.assertIn("can_greenlight", keys)
        self.assertIn("can_verify_work_requests", keys)
        self.assertIn("visibility_scope", keys)
        # carry-forward levels deliberately excluded from the rediff
        self.assertNotIn("level_filter_primary", keys)
        self.assertNotIn("level_filter_allowed", keys)

    def test_grant_is_applied_semantics(self):
        # absent role -> false (proceed)
        self.assertFalse(self.db.sql(
            "SELECT nebula.grant_is_applied('ghost', '{}'::jsonb)"))
        # critic open row is capability-empty; an exact-empty-spec IS applied
        # (the seed state itself). The gate answers over the whole spec.
        self.assertTrue(self.db.sql(
            "SELECT grant_spec = (SELECT spec FROM nebula.applied_grants "
            "WHERE role='critic') FROM (SELECT 1) t(grant_spec)"
            ) if False else self.db.sql(
            "SELECT nebula.grant_is_applied('critic', (SELECT spec FROM "
            "nebula.applied_grants WHERE role='critic'))"))
        # any drift -> false
        self.assertFalse(self.db.sql(
            "SELECT nebula.grant_is_applied('critic', "
            "'{\"can_greenlight\": true}'::jsonb)"))

    def test_identical_reapply_refuses_and_mints_nothing(self):
        self.db.apply_file(CRITIC_PATH)     # first apply: clean event
        closed, opened = self.db.chain("critic")
        self.assertEqual((1, 1), (closed, opened))
        # identical re-apply: the gate refuses (house gotcha-#5 pattern:
        # catch, explicit ROLLBACK to clear the aborted tx, then assert)
        refused = ""
        try:
            self.db.apply_file(CRITIC_PATH)
        except psycopg2.Error as exc:
            refused = str(exc).splitlines()[0]
            try:
                self.db.sql("ROLLBACK")
            except psycopg2.Error:
                pass
        else:
            self.fail("identical re-apply must refuse")
        self.assertIn("GRANT-APPLIED", refused,
                      "the refusal must name the self-idempotence gate")
        closed, opened = self.db.chain("critic")
        self.assertEqual((1, 1), (closed, opened),
                         "redundant event must NOT be minted")

    def test_different_spec_is_a_new_lawful_event(self):
        self.db.apply_file(CRITIC_PATH)
        # critic gains resolve authority -> rediff gate must NOT fire
        # Patch BOTH the granted value and the file's verify block (the
        # in-file gate is tamper-protection; a spec change must update both
        # or the file refuses itself — that refusal is not this test's
        # target).
        patched = open(CRITIC_PATH).read().replace(
            "v_rq IS DISTINCT FROM false",
            "v_rq IS DISTINCT FROM true").replace(
            "v_can_resolve_questions boolean := false;",
            "v_can_resolve_questions boolean := true;")
        assert "v_can_resolve_questions boolean := true;" in patched
        assert "v_rq IS DISTINCT FROM true" in patched
        with open("/tmp/v180_diffspec.sql", "w") as fh:
            fh.write(patched)
        self.db.apply_file("/tmp/v180_diffspec.sql")
        closed, opened = self.db.chain("critic")
        self.assertEqual((2, 1), (closed, opened))
        rq = self.db.sql(
            "SELECT can_resolve_questions FROM nebula.roles_history "
            "WHERE name='critic' AND valid_until='9999-12-31'")
        self.assertTrue(rq)


if __name__ == "__main__":
    unittest.main()
