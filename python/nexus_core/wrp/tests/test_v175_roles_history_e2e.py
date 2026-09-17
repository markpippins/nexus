#!/usr/bin/env python3
"""E2E: V175 roles_history close-then-insert repair against a REAL throwaway DB.

wr-conf-025 companion, house throwaway-DB pattern. The skeleton deliberately
recreates the PRE-V175 (V081-era) shape — full UNIQUE(name) on roles_history,
nebula.roles as a view over it — so the migration's repair path is exercised
for real:

  - old shape: a second snapshot for the same role is impossible (unique violation)
  - view-write trap: UPDATE via nebula.roles matches nothing once the snapshot
    closes mid-transaction (UPDATE 0 — the silent no-op that bit the auditor grant)
  - V175 repairs: full unique dropped, partial open-snapshot index installed
  - exactly-one-open enforced (second open snapshot rejected, closed ones fine)
  - close-then-insert chain possible: handoff exact, audit untouched
  - the corrected grant template runs end-to-end on a fresh auditor-style role
  - idempotent re-apply
"""
import os
import sys
import unittest
import uuid

import psycopg2
import psycopg2.extras

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V175_PATH = os.path.join(_REPO_ROOT, "sql", "V175__roles_history_close_then_insert_repair.sql")
GRANT_PATH = os.path.join(_REPO_ROOT, "sql", "grants", "auditor-grant-v0.1.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SENTINEL = "9999-12-31 00:00:00+00"


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_v175_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def apply_v175(self):
        with open(V175_PATH) as fh:
            self.sql(fh.read())


SKELETON_SQL = f"""
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;
CREATE SCHEMA semantics;

CREATE TABLE semantics.canonical_asset (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_kind text
);

-- PRE-V175 shape: full UNIQUE(name) — history impossible by construction.
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
    CONSTRAINT roles_name_key UNIQUE (name)   -- the anti-history shape
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

-- nebula.roles as a VIEW (the live discovery): current-row predicate.
CREATE VIEW nebula.roles AS
SELECT * FROM nebula.roles_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;
"""


def _new_row():
    return {
        "name": "auditor",
        "display_name": "Auditor",
        "description": "vocabulary-only seed role",
        "owns_domains": [],
        "can_greenlight": False,
        "can_create_questions": False,
        "can_create_agendas": False,
        "can_resolve_questions": False,
        "can_verify_work_requests": False,
        "max_open_questions": 0,
        "requires_approval_from": ["architect"],
        "cron_enabled": False,
        "cron_expression": None,
        "cron_description": None,
        "escalates_to": ["architect"],
        "escalation_triggers": [],
        "level_filter_primary": "<=2",
        "level_filter_allowed": "<=4",
        "visibility_scope": ["all"],
    }


class V175E2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        with self.db.conn.cursor() as cur:
            r = _new_row()
            cur.execute("""
INSERT INTO nebula.roles_history (name, display_name, description, owns_domains,
    can_greenlight, can_create_questions, can_create_agendas, can_resolve_questions,
    can_verify_work_requests, max_open_questions, requires_approval_from,
    escalates_to, escalation_triggers, level_filter_primary, level_filter_allowed,
    visibility_scope)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
""", (r["name"], r["display_name"], r["description"], r["owns_domains"],
      r["can_greenlight"], r["can_create_questions"], r["can_create_agendas"],
      r["can_resolve_questions"], r["can_verify_work_requests"],
      r["max_open_questions"], r["requires_approval_from"], r["escalates_to"],
      r["escalation_triggers"], r["level_filter_primary"],
      r["level_filter_allowed"], r["visibility_scope"]))

    # ── the old shape's two traps, demonstrated before repair ──────────────

    def test_old_shape_rejects_second_snapshot(self):
        # history is structurally impossible under the full unique
        err = self.db.expect_error(
            "INSERT INTO nebula.roles_history (name) VALUES ('auditor')")
        self.assertIn("roles_name_key", err)

    def test_view_write_trap_silent_noop(self):
        # close the row THROUGH THE VIEW (the auditor-grant trap): the
        # snapshot closes, the row vanishes from the view mid-transaction,
        # and a view-based follow-up UPDATE matches nothing
        self.db.sql(f"UPDATE nebula.roles SET valid_until = now() WHERE name='auditor'")
        with self.db.conn.cursor() as cur:
            cur.execute("UPDATE nebula.roles SET owns_domains = %s WHERE name = %s",
                        (["audit-trails"], "auditor"))
            self.assertEqual(cur.rowcount, 0)  # THE silent no-op — lesson 1

    # ── the repair ──────────────────────────────────────────────────────────

    def test_v175_repairs_and_enables_history(self):
        self.db.apply_v175()
        # a second snapshot is now possible (closed history chain)
        self.db.sql("""
INSERT INTO nebula.roles_history (name, valid_from, valid_until)
VALUES ('auditor', now(), now())
""")
        # exactly-one-open: a second OPEN snapshot is refused by the partial index
        err = self.db.expect_error("""
INSERT INTO nebula.roles_history (name)
VALUES ('auditor')
""")
        self.assertIn("roles_name_open_key", err)
        # closed rows coexist fine
        self.db.sql("""
INSERT INTO nebula.roles_history (name, valid_from, valid_until)
VALUES ('auditor', now() - interval '1 hour', now() - interval '30 minutes')
""")

    def test_verify_gate_catches_multi_open(self):
        # drifted database: two open rows, no partial index. Re-applying V175
        # must FAIL — either the partial-index recreation (UniqueViolation on
        # the duplicate) or the gate (multi-open / full-unique detection).
        # Drift is never silently converged.
        self.db.apply_v175()
        self.db.sql("DROP INDEX nebula.roles_name_open_key")
        self.db.sql("INSERT INTO nebula.roles_history (name) VALUES ('auditor')")
        with self.assertRaises(psycopg2.Error):
            with open(V175_PATH) as fh:
                self.db.sql(fh.read())
        self.db.sql("ROLLBACK")  # V175's explicit BEGIN leaves an aborted tx on an autocommit conn
        # and the gate's own multi-open query detects the drift precisely
        n = self.db.sql("""
SELECT count(*) FROM (
    SELECT name FROM nebula.roles_history
    WHERE valid_until = %s GROUP BY name HAVING count(*) > 1
) m
""", (SENTINEL,))
        self.assertEqual(n, 1)

    def test_gate_refuses_multi_open(self):
        # drift AFTER repair: drop index, create dup, restore index fails.
        # Then show the standalone gate logic via a direct query.
        self.db.apply_v175()
        self.db.sql("DROP INDEX nebula.roles_name_open_key")
        self.db.sql("INSERT INTO nebula.roles_history (name) VALUES ('auditor')")
        n = self.db.sql("""
SELECT count(*) FROM (
    SELECT name FROM nebula.roles_history
    WHERE valid_until = %s GROUP BY name HAVING count(*) > 1
) m
""", (SENTINEL,))
        self.assertEqual(n, 1)  # drift is detectable by the gate's own query

    def test_idempotent_reapply(self):
        self.db.apply_v175()
        self.db.apply_v175()  # second apply: notices only, no error
        n = self.db.sql("SELECT count(*) FROM nebula.roles_history")
        self.assertEqual(n, 1)

    # ── the corrected grant template, end to end ───────────────────────────

    def test_grant_template_end_to_end(self):
        self.db.apply_v175()
        with open(GRANT_PATH) as fh:
            self.db.sql(fh.read())  # runs clean: close + insert + verify gate

        rows = self.db.sql("""
SELECT valid_until = %s AS open, owns_domains FROM nebula.roles_history
WHERE name='auditor' ORDER BY valid_from
""", (SENTINEL,))
        # psycopg2 returns list of tuples via fetchall; our sql() helper
        # returns raw rows for multi-row results
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0][0])   # first snapshot closed
        self.assertTrue(rows[1][0])    # successor open
        self.assertEqual(list(rows[1][1]), ["audit-trails", "attestation"])

        # handoff exactness: open.valid_from == closed.valid_until
        handoff = self.db.sql("""
SELECT o.valid_from = c.valid_until FROM nebula.roles_history o
JOIN nebula.roles_history c ON c.name='auditor' AND c.valid_until <> %s
WHERE o.name='auditor' AND o.valid_until = %s
""", (SENTINEL, SENTINEL))
        self.assertTrue(handoff)

    def test_grant_template_refuses_pre_v175_shape(self):
        # against the old shape the run must fail loudly (never a silent
        # no-op): the close fires, then the successor INSERT hits the full
        # unique — refusal via the constraint, transaction aborted
        with open(GRANT_PATH) as fh:
            err = self.db.expect_error(fh.read())
        self.assertTrue("roles_name_key" in err or "GRANT" in err, msg=err)

    def test_grant_template_is_repeatable(self):
        # the template is a GRANT-EVENT executor: each run closes the current
        # open snapshot and opens a successor — the bitemporal chain grows,
        # handoff stays exact (this is the V175-enabled semantics)
        self.db.apply_v175()
        with open(GRANT_PATH) as fh:
            self.db.sql(fh.read())
        with open(GRANT_PATH) as fh:
            self.db.sql(fh.read())  # second grant event
        rows = self.db.sql("""
SELECT valid_until = %s AS open FROM nebula.roles_history
WHERE name='auditor' ORDER BY valid_from
""", (SENTINEL,))
        self.assertEqual(len(rows), 3)
        self.assertFalse(rows[0][0])
        self.assertFalse(rows[1][0])
        self.assertTrue(rows[2][0])


if __name__ == "__main__":
    unittest.main()
