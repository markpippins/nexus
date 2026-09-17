#!/usr/bin/env python3
"""E2E: Wave 1 clone grants — analyst-ii and engineer-ii (wr-conf-029).

House throwaway-DB pattern. The skeleton recreates the pre-V175 world
with the clone SOURCE roles (analyst, engineer) seeded at their pinned
baseline values and the TARGET roles (analyst-ii, engineer-ii) seeded
capability-empty — exactly the live shape at snapshot time. Then:

  - wave1-clone-baseline.sql verifies against the pinned values (and
    REFUSES on drift — tested)
  - analyst-ii-grant-v0.1.sql runs as a real grant event: chain
    closed=1/open=1, handoff exact, values = clone baseline
  - engineer-ii-grant-v0.1.sql same, with verify=TRUE pinned
  - repeatability: a second run is a second grant event
  - refusal: unknown/missing role fails loudly, atomically
"""
import os
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V175_PATH = os.path.join(_REPO_ROOT, "sql", "V175__roles_history_close_then_insert_repair.sql")
BASELINE_PATH = os.path.join(_REPO_ROOT, "sql", "grants", "wave1-clone-baseline.sql")
ANALYST_II_PATH = os.path.join(_REPO_ROOT, "sql", "grants", "analyst-ii-grant-v0.1.sql")
ENGINEER_II_PATH = os.path.join(_REPO_ROOT, "sql", "grants", "engineer-ii-grant-v0.1.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")
SENTINEL = "9999-12-31 00:00:00+00"


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_wave1_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def apply_file(self, path):
        with open(path) as fh:
            self.sql(fh.read())

    def chain(self, role):
        rows = self.sql("""
SELECT count(*) FILTER (WHERE valid_until <> %s::timestamptz),
       count(*) FILTER (WHERE valid_until =  %s::timestamptz)
  FROM nebula.roles_history WHERE name = %s
""", (SENTINEL, SENTINEL, role))
        return rows[0]

    def open_row(self, role):
        rows = self.sql("""
SELECT owns_domains, can_verify_work_requests, can_create_questions,
       can_resolve_questions, can_greenlight, escalates_to,
       escalation_triggers
  FROM nebula.roles_history
 WHERE name = %s AND valid_until = %s::timestamptz
""", (role, SENTINEL))
        return rows[0]

    def handoff_exact(self, role):
        return self.sql("""
SELECT count(*) = 0 FROM nebula.roles_history o
JOIN nebula.roles_history c ON c.name = o.name
  AND c.valid_until <> %s::timestamptz
  AND c.valid_until = (SELECT max(valid_until) FROM nebula.roles_history
                       WHERE name = o.name AND valid_until <> %s::timestamptz)
WHERE o.name = %s AND o.valid_until = %s::timestamptz
  AND o.valid_from IS DISTINCT FROM c.valid_until
""", (SENTINEL, SENTINEL, role, SENTINEL))


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
    max_open_questions      integer,
    requires_approval_from  text[],
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
    CONSTRAINT roles_name_key UNIQUE (name)   -- pre-V175 shape
);

CREATE TABLE tackle.role_leases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role text NOT NULL, channel text, model text,
    status text NOT NULL DEFAULT 'ACTIVE',
    acquired_at timestamptz DEFAULT now(), expires_at timestamptz,
    released_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tackle.system_logs (
    id text PRIMARY KEY, timestamp timestamptz NOT NULL DEFAULT now(),
    level text NOT NULL, category text NOT NULL, message text NOT NULL,
    source text, details jsonb
);

CREATE VIEW nebula.roles AS
SELECT * FROM nebula.roles_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;
"""

SEED_SQL = f"""
-- Clone SOURCES at their pinned baseline values (snapshot 2026-09-17).
INSERT INTO nebula.roles_history
       (name, display_name, description, owns_domains,
       can_create_questions, can_resolve_questions,
       escalates_to, escalation_triggers,
       level_filter_primary, level_filter_allowed, visibility_scope)
VALUES
 ('analyst', 'Analyst',
  'Triages issues, resolves ambiguities, provides detail for unclear requirements.',
  ARRAY['issue_triage','ambiguity_resolution'],
  true, true,
  ARRAY['architect','planner'], ARRAY['requirement_unclear'],
  'level <= 3', 'level <= 3', ARRAY['analyst','all']),
 ('engineer', 'Engineer',
  'Implements features, verifies work requests for buildability.',
  ARRAY['implementation','build_verification'],
  false, false,
  ARRAY['architect'], ARRAY['design_concern'],
  'level <= 1', 'level <= 2', ARRAY['builder','all']);

UPDATE nebula.roles_history SET can_verify_work_requests = true
 WHERE name = 'engineer';

-- Clone TARGETS as the 12-role batch created them: open, capability-empty.
INSERT INTO nebula.roles_history (name, display_name, description)
VALUES ('analyst-ii', 'Analyst II', 'Ratified role vocabulary'),
       ('engineer-ii', 'Engineer II', 'Ratified role vocabulary');
"""


class Wave1GrantsE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        self.db.sql(SEED_SQL)
        self.db.apply_file(V175_PATH)   # grant mechanics require the repaired shape

    def test_baseline_gate_passes_on_pinned_world(self):
        self.db.apply_file(BASELINE_PATH)
        notices = "\n".join(self.db.conn.notices or [])
        self.assertIn("match the pinned clone-source values", notices)

    def test_baseline_gate_refuses_drift(self):
        self.db.sql("UPDATE nebula.roles_history SET can_resolve_questions = false WHERE name='analyst'")
        try:
            self.db.apply_file(BASELINE_PATH)
        except psycopg2.Error as exc:
            self.assertIn("W1-BASELINE-GATE-002", str(exc))
        else:
            self.fail("baseline must refuse on clone-source drift")

    def test_analyst_ii_grant_event(self):
        self.db.apply_file(ANALYST_II_PATH)
        closed, opened = self.db.chain("analyst-ii")
        self.assertEqual((1, 1), (closed, opened))
        self.assertTrue(self.db.handoff_exact("analyst-ii"))
        dom, ver, cq, rq, gl, esc, trig = self.db.open_row("analyst-ii")
        self.assertEqual(sorted(["issue_triage", "ambiguity_resolution"]), sorted(dom))
        self.assertFalse(ver)
        self.assertTrue(cq)
        self.assertTrue(rq)
        self.assertFalse(gl)
        self.assertEqual(["architect", "planner"], sorted(esc))
        self.assertEqual(["requirement_unclear"], list(trig))

    def test_engineer_ii_grant_event(self):
        self.db.apply_file(ENGINEER_II_PATH)
        closed, opened = self.db.chain("engineer-ii")
        self.assertEqual((1, 1), (closed, opened))
        self.assertTrue(self.db.handoff_exact("engineer-ii"))
        dom, ver, cq, rq, gl, esc, trig = self.db.open_row("engineer-ii")
        self.assertEqual(["build_verification", "implementation"], sorted(dom))
        self.assertTrue(ver, "verify=TRUE mirrors the engineer baseline")
        self.assertFalse(cq)
        self.assertFalse(rq)
        self.assertFalse(gl)
        self.assertEqual(["architect"], list(esc))
        self.assertEqual(["design_concern"], list(trig))

    def test_repeatability_is_second_grant_event(self):
        self.db.apply_file(ANALYST_II_PATH)
        self.db.apply_file(ANALYST_II_PATH)
        closed, opened = self.db.chain("analyst-ii")
        self.assertEqual((2, 1), (closed, opened))
        self.assertTrue(self.db.handoff_exact("analyst-ii"))

    def test_missing_role_refuses_atomically(self):
        self.db.sql("DELETE FROM nebula.roles_history WHERE name='analyst-ii'")
        try:
            self.db.apply_file(ANALYST_II_PATH)
        except psycopg2.Error:
            pass
        else:
            self.fail("grant against a missing role must fail loudly")
        # Explicit BEGIN in the file strands the aborted tx on this
        # autocommit connection (sql-pitfalls gotcha #5) — clear it.
        try:
            self.db.sql("ROLLBACK")
        except psycopg2.Error:
            pass
        # Nothing half-written: analyst-ii stays deleted (its grant
        # refused and rolled back); engineer-ii is untouched (still the
        # seeded open row).
        self.assertEqual((0, 0), tuple(self.db.chain("analyst-ii")))
        self.assertEqual((0, 1), tuple(self.db.chain("engineer-ii")))


if __name__ == "__main__":
    unittest.main()
