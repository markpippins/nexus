#!/usr/bin/env python3
"""E2E: V181 — wind.node_requirements DDL + seeding (staged inert → live shape).

wr-conf-036 companion, house throwaway-DB pattern.

World: a minimal skeleton carrying the PRE-V181 surfaces V181 composes —
wind.workflow_nodes (minimal), nebula.capabilities (V172 shape), the V156
audit helper — then the REAL V181 applies and the full demand-side contract
is exercised for real:

  - schema: contract columns, V174 CHECK, bitemporal pair with the V175
    house sentinel, FK to nebula.capabilities(name)
  - gates: missing workflow_nodes → V181-GATE-001; missing capabilities
    → V181-GATE-002 (applied in that order)
  - seeds: the four f0000000-* Requirement Lifecycle nodes carry the
    seeded demands (triage→planner, decide→lead-engineer,
    implement→engineer, review→capability + tester)
  - integrity: V174 CHECK refuses a bogus verdict; demand_required CHECK
    refuses a fully-null demand; FK refuses an unregistered capability;
    open-interval unique indexes refuse a duplicate open demand
  - audit: demand writes leave NEBULA_AUDIT rows via the V156 helper
  - idempotency: re-apply V181 is a no-op (seeds do not duplicate)
  - close-then-insert: closing a demand and opening its successor works
    and leaves the history intact
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V181_PATH = os.path.join(_REPO_ROOT, "sql", "V181__wind_node_requirements.sql")
V156_PATH = os.path.join(_REPO_ROOT, "sql", "V156__nebula_canonical_audit_triggers.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SENTINEL = "9999-12-31 00:00:00+00"

NODES = {
    "triage":    "f0000000-0000-0000-0000-000000000001",
    "decide":    "f0000000-0000-0000-0000-000000000002",
    "implement": "f0000000-0000-0000-0000-000000000003",
    "review":    "f0000000-0000-0000-0000-000000000004",
}

CAP_KEY = "has-active-shrapnel-protocol"


def apply_sql_file(cur, path):
    with open(path, "r", encoding="utf-8") as f:
        cur.execute(f.read())


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_v181_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
        except psycopg2.Error as e:
            return str(e)
        raise AssertionError("expected a database error, got none")


SKELETON_SQL = """
CREATE SCHEMA wind;
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- wind.workflow_nodes (minimal live shape)
CREATE TABLE wind.workflow_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_version_id uuid,
  task_id uuid,
  name text,
  is_entrypoint boolean DEFAULT false,
  is_terminal boolean DEFAULT false,
  created_at timestamptz DEFAULT now()
);
INSERT INTO wind.workflow_nodes (id, name) VALUES
  ('f0000000-0000-0000-0000-000000000001', 'triage'),
  ('f0000000-0000-0000-0000-000000000002', 'decide'),
  ('f0000000-0000-0000-0000-000000000003', 'implement'),
  ('f0000000-0000-0000-0000-000000000004', 'review');

-- nebula.capabilities (V172 shape, minimal)
CREATE TABLE nebula.capabilities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text UNIQUE NOT NULL,
  description text,
  registered_by text,
  created_at timestamptz DEFAULT now()
);
INSERT INTO nebula.capabilities (name) VALUES ('has-active-shrapnel-protocol');

-- V156 audit helper (minimal faithful shape: same signature V177/V181 call)
CREATE TABLE tackle.system_logs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  category text NOT NULL,
  message text NOT NULL,
  details jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE OR REPLACE FUNCTION tackle.fn_nebula_audit_log(
  p_table text, p_op text, p_rows bigint, p_keys text)
RETURNS void LANGUAGE plpgsql AS $fn$
BEGIN
  INSERT INTO tackle.system_logs (category, message, details)
  VALUES ('NEBULA_AUDIT',
          p_table || ' ' || p_op,
          jsonb_build_object('table', p_table, 'op', p_op,
                             'row_count', p_rows, 'keys', p_keys));
END $fn$;
"""


class TestV181E2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        with self.db.conn.cursor() as cur:
            apply_sql_file(cur, V181_PATH)

    def tearDown(self):
        self.db.__exit__()

    # ── schema ────────────────────────────────────────────────────────
    def test_schema_shape(self):
        cols = dict(self.db.sql(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema='wind' AND table_name='node_requirements'"))
        self.assertIn("capability_key", cols)
        self.assertIn("role_credential", cols)
        self.assertIn("last_verdict", cols)
        self.assertIn("recorded_on_dt", cols)
        self.assertIn("recorded_until_dt", cols)
        open_rows = self.db.sql(
            "SELECT count(*) FROM wind.node_requirements"
            f" WHERE valid_until = '{SENTINEL}'::timestamptz")
        self.assertEqual(open_rows, 5)

    def test_seeds_landed_with_expected_demands(self):
        rows = self.db.sql(
            "SELECT n.name, r.capability_key, r.role_credential"
            " FROM wind.node_requirements r"
            " JOIN wind.workflow_nodes n ON n.id = r.node_id"
            " ORDER BY n.name, r.capability_key NULLS FIRST")
        by_node = {}
        for name, cap, cred in rows:
            by_node.setdefault(name, []).append((cap, cred))
        self.assertEqual(by_node["triage"], [(None, "planner")])
        self.assertEqual(by_node["decide"], [(None, "lead-engineer")])
        self.assertEqual(by_node["implement"], [(None, "engineer")])
        self.assertEqual(len(by_node["review"]), 2)
        self.assertIn((CAP_KEY, None), by_node["review"])
        self.assertIn((None, "tester"), by_node["review"])

    # ── integrity ─────────────────────────────────────────────────────
    def test_v174_check_refuses_bogus_verdict(self):
        err = self.db.expect_error(
            "INSERT INTO wind.node_requirements (node_id, role_credential, last_verdict)"
            " VALUES ('f0000000-0000-0000-0000-000000000001', 'ghost', 'ok-ish')")
        self.assertIn("node_requirements_last_verdict_check", err)

    def test_demand_required_check(self):
        err = self.db.expect_error(
            "INSERT INTO wind.node_requirements (node_id) VALUES"
            " ('f0000000-0000-0000-0000-000000000001')")
        self.assertIn("node_requirements_demand_required", err)

    def test_fk_refuses_unregistered_capability(self):
        err = self.db.expect_error(
            "INSERT INTO wind.node_requirements (node_id, capability_key) VALUES"
            " ('f0000000-0000-0000-0000-000000000001', 'no-such-capability')")
        self.assertIn("node_requirements_capability_fk", err)

    def test_open_interval_unique_refuses_duplicate_demand(self):
        err = self.db.expect_error(
            "INSERT INTO wind.node_requirements (node_id, role_credential) VALUES"
            " ('f0000000-0000-0000-0000-000000000001', 'planner')")
        self.assertIn("node_requirements_open_node_role_uq", err)

    # ── audit ─────────────────────────────────────────────────────────
    def test_writes_leave_nebula_audit_rows(self):
        before = self.db.sql(
            "SELECT count(*) FROM tackle.system_logs WHERE category='NEBULA_AUDIT'")
        self.db.sql(
            "INSERT INTO wind.node_requirements (node_id, role_credential) VALUES"
            " ('f0000000-0000-0000-0000-000000000003', 'engineer-ii')")
        after = self.db.sql(
            "SELECT count(*) FROM tackle.system_logs WHERE category='NEBULA_AUDIT'")
        self.assertEqual(after - before, 1)
        last = self.db.sql(
            "SELECT message FROM tackle.system_logs WHERE category='NEBULA_AUDIT'"
            " ORDER BY id DESC LIMIT 1")
        self.assertIn("INSERT", last)

    # ── idempotency ───────────────────────────────────────────────────
    def test_reapply_is_noop(self):
        with self.db.conn.cursor() as cur:
            apply_sql_file(cur, V181_PATH)
        self.assertEqual(self.db.sql(
            "SELECT count(*) FROM wind.node_requirements"), 5)

    # ── close-then-insert ─────────────────────────────────────────────
    def test_close_then_insert_replacement(self):
        req_id = self.db.sql(
            "SELECT id FROM wind.node_requirements WHERE role_credential='planner'"
            f" AND valid_until='{SENTINEL}'::timestamptz")
        self.db.sql(
            "UPDATE wind.node_requirements SET valid_until = now()"
            " WHERE id = %s", (req_id,))
        self.db.sql(
            "INSERT INTO wind.node_requirements (node_id, role_credential) VALUES"
            " ('f0000000-0000-0000-0000-000000000001', 'architect')")
        history = self.db.sql(
            "SELECT count(*) FROM wind.node_requirements"
            " WHERE role_credential='planner' AND valid_until <>"
            f" '{SENTINEL}'::timestamptz")
        self.assertEqual(history, 1)
        self.assertEqual(self.db.sql(
            "SELECT role_credential FROM wind.node_requirements"
            " WHERE node_id='f0000000-0000-0000-0000-000000000001'"
            f" AND valid_until='{SENTINEL}'::timestamptz"), "architect")


class TestV181Gates(unittest.TestCase):
    def test_gate_001_missing_nodes(self):
        with ThrowawayDB() as db:
            with db.conn.cursor() as cur:
                cur.execute("DROP TABLE wind.workflow_nodes CASCADE")
            with self.assertRaises(psycopg2.Error) as cm:
                with db.conn.cursor() as cur:
                    apply_sql_file(cur, V181_PATH)
            self.assertIn("V181-GATE-001", str(cm.exception))

    def test_gate_003_missing_capabilities(self):
        with ThrowawayDB() as db:
            with db.conn.cursor() as cur:
                cur.execute("DROP TABLE nebula.capabilities CASCADE")
            with self.assertRaises(psycopg2.Error) as cm:
                with db.conn.cursor() as cur:
                    apply_sql_file(cur, V181_PATH)
            self.assertIn("V181-GATE-002", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
