#!/usr/bin/env python3
"""E2E: V177 — nebula.roles_history joins the V156 NEBULA_AUDIT family.

wr-conf-025/026 companion, house throwaway-DB pattern. The skeleton
recreates the pre-V175 world plus minimal canonical shapes for the two
V156-covered surfaces (agent_records_history, harvests_history), so the
REAL V156 and the REAL V177 apply unmodified; then live writes and a REAL
grant file are driven through the actual triggers.

Pinned:
  - grant events leave an attributable trail: close-then-insert on
    roles_history produces one UPDATE + one INSERT NEBULA_AUDIT row
  - every op audited with the right keys/counts (roles are the keys)
  - statement semantics: multi-row statements make ONE audit row
  - details envelope shape: table/op/row_count/keys/txid/client_addr
  - gates: no V156 helper -> V177-GATE-002 refused atomically; missing
    target table -> V177-GATE-001 refused
  - idempotency: re-apply is a no-op (no spurious audit rows)
  - integration: sql/grants/tester-grant-v0.1.sql runs end-to-end and
    its grant event is visible in the audit trail
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V156_PATH = os.path.join(_REPO_ROOT, "sql", "V156__nebula_canonical_audit_triggers.sql")
V177_PATH = os.path.join(_REPO_ROOT, "sql", "V177__roles_history_audit_triggers.sql")
V175_PATH = os.path.join(_REPO_ROOT, "sql", "V175__roles_history_close_then_insert_repair.sql")
GRANT_PATH = os.path.join(_REPO_ROOT, "sql", "grants", "tester-grant-v0.1.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SENTINEL = "9999-12-31 00:00:00+00"


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_roles_audit_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def audit_rows(self, like=None):
        rows = self.sql("""
SELECT timestamp, category, source, details
  FROM tackle.system_logs
 WHERE category = 'NEBULA_AUDIT'
 ORDER BY timestamp, details->>'op'
""")
        if like:
            rows = [r for r in rows if (r[3] or {}).get("table") == like]
        return rows

    def trigger_count(self, table="nebula.roles_history"):
        return self.sql("""
SELECT count(*) FROM pg_trigger
 WHERE tgrelid = %s::regclass AND tgname LIKE 'trg_rh_audit%%' AND NOT tgisinternal
""", (table,))


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

-- Minimal canonical shapes so the REAL V156 applies unmodified (its
-- triggers need the two surfaces it already covers).
CREATE TABLE nebula.agent_records_history (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role        text,
    record_type text,
    title       text,
    content     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE nebula.harvests_history (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_filename text,
    created_at      timestamptz NOT NULL DEFAULT now()
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


class RolesAuditE2E(unittest.TestCase):
    """Full world: skeleton -> REAL V156 -> REAL V177 -> live writes."""

    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        self.db.seed_role()
        self.db.apply_file(V156_PATH)
        self.db.apply_file(V177_PATH)
        # Grant events need the repaired shape (close-then-insert is
        # impossible under the pre-V175 full unique — that trap is
        # wr-conf-025's charter, not this suite's).
        self.db.apply_file(V175_PATH)

    def test_triggers_installed_with_notice(self):
        self.assertEqual(3, self.db.trigger_count())
        notices = "\n".join(self.db.conn.notices or [])
        self.assertIn("joined the NEBULA_AUDIT family", notices)

    def test_grant_event_two_audit_rows_same_txid(self):
        """The deliverable: a close-then-insert grant event is witnessed.

        The grant template wraps the whole event in one explicit
        transaction, so both audit rows must carry the same txid.
        """
        before = len(self.db.audit_rows(like="roles_history"))
        self.db.conn.autocommit = False   # one real transaction, like the grant file
        try:
            with self.db.conn.cursor() as cur:
                cur.execute("""
UPDATE nebula.roles_history
   SET valid_until = now()
 WHERE name = 'tester' AND valid_until = %s::timestamptz
""", (SENTINEL,))
                cur.execute("""
INSERT INTO nebula.roles_history
       (name, display_name, description, owns_domains, can_verify_work_requests)
VALUES ('tester', 'Tester', 'granted', '{test-verification}', true)
""")
            self.db.conn.commit()
        except Exception:
            self.db.conn.rollback()
            raise
        finally:
            self.db.conn.autocommit = True
        rows = [r for r in self.db.audit_rows(like="roles_history")][before:]
        self.assertEqual(2, len(rows))
        ops = [(r[3]["op"], r[3]["keys"], r[3]["row_count"]) for r in rows]
        self.assertIn(("UPDATE", "tester", 1), ops)
        self.assertIn(("INSERT", "tester", 1), ops)
        txids = {r[3]["txid"] for r in rows}
        self.assertEqual(1, len(txids), "both halves of the grant event share one txid")

    def test_autocommit_statements_get_distinct_txids(self):
        """Autocommit writes are separate transactions — separate txids.

        Pins the honest semantics: the pairing property comes from the
        grant template's explicit transaction, not from the triggers.
        """
        before = len(self.db.audit_rows(like="roles_history"))
        self.db.sql("UPDATE nebula.roles_history SET description='a' WHERE name='tester'")
        self.db.sql("UPDATE nebula.roles_history SET description='b' WHERE name='tester'")
        rows = [r for r in self.db.audit_rows(like="roles_history")][before:]
        self.assertEqual(2, len(rows))
        self.assertEqual(2, len({r[3]["txid"] for r in rows}))

    def test_all_ops_audited_with_role_keys(self):
        self.db.sql("INSERT INTO nebula.roles_history (name) VALUES ('auditrow'), ('auditrow2')")
        self.db.sql("UPDATE nebula.roles_history SET description='x' WHERE name='auditrow'")
        self.db.sql("DELETE FROM nebula.roles_history WHERE name='auditrow'")
        ops = [(r[3]["op"], r[3]["keys"], r[3]["row_count"])
               for r in self.db.audit_rows(like="roles_history")]
        self.assertIn(("INSERT", "auditrow, auditrow2", 2), ops)
        self.assertIn(("UPDATE", "auditrow", 1), ops)
        self.assertIn(("DELETE", "auditrow", 1), ops)

    def test_statement_semantics_one_row_per_statement(self):
        self.db.sql("INSERT INTO nebula.roles_history (name) SELECT 'm' || g FROM generate_series(1,5) g")
        ins = [r for r in self.db.audit_rows(like="roles_history") if r[3]["op"] == "INSERT"]
        self.assertEqual(1, len(ins), "one statement = one audit row")
        self.assertEqual(5, ins[0][3]["row_count"])

    def test_details_envelope_shape(self):
        self.db.sql("INSERT INTO nebula.roles_history (name) VALUES ('env')")
        d = self.db.audit_rows(like="roles_history")[0][3]
        for key in ("table", "op", "row_count", "keys", "txid", "client_addr", "application_name"):
            self.assertIn(key, d)
        self.assertEqual("roles_history", d["table"])

    def test_idempotent_reapply_no_spurious_rows(self):
        before = len(self.db.audit_rows(like="roles_history"))
        self.db.apply_file(V177_PATH)
        self.assertEqual(3, self.db.trigger_count())
        self.assertEqual(before, len(self.db.audit_rows(like="roles_history")))

    def test_grant_file_end_to_end_with_audit(self):
        """Integration: the REAL tester grant file runs, and its event is audited."""
        self.db.apply_file(V175_PATH)   # grant requires the repaired shape
        self.db.apply_file(GRANT_PATH)
        chain = self.db.sql("""
SELECT count(*) FILTER (WHERE valid_until <> %s::timestamptz),
       count(*) FILTER (WHERE valid_until =  %s::timestamptz)
  FROM nebula.roles_history WHERE name='tester'
""", (SENTINEL, SENTINEL))
        self.assertEqual((1, 1), (chain[0][0], chain[0][1]))
        grant_rows = [(r[3]["op"], r[3]["keys"])
                      for r in self.db.audit_rows(like="roles_history")
                      if r[3]["keys"] == "tester"]
        self.assertIn(("UPDATE", "tester"), grant_rows)
        self.assertIn(("INSERT", "tester"), grant_rows)


class V177GateE2E(unittest.TestCase):
    """The gates: V177 refuses to run on worlds it does not belong to."""

    def expect_error_fn(self, fn):
        """Run fn(), return the first line of the psycopg2 error it raises."""
        try:
            fn()
        except psycopg2.Error as exc:
            return str(exc).split("\n")[0]
        raise AssertionError("expected the callable to fail")

    def test_gate_no_v156_helper(self):
        """No V156 helper -> V177-GATE-002, and NOTHING is created (rollback)."""
        db = ThrowawayDB().__enter__()
        self.addCleanup(db.__exit__)
        db.seed_role()
        err = self.expect_error_fn(lambda: db.apply_file(V177_PATH))
        self.assertIn("V177-GATE-002", err)
        # Prove the rollback server-side through a fresh connection.
        fresh = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + db.dbname)
        fresh.autocommit = True
        with fresh.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid='nebula.roles_history'::regclass AND NOT tgisinternal")
            self.assertEqual(0, cur.fetchone()[0])
        fresh.close()

    def test_gate_no_target_table(self):
        """No roles_history table -> V177-GATE-001, nothing created."""
        db = ThrowawayDB().__enter__()
        self.addCleanup(db.__exit__)
        db.apply_file(V156_PATH)
        # CASCADE: the nebula.roles view dependency drops with the table.
        db.sql("DROP TABLE nebula.roles_history CASCADE")
        err = self.expect_error_fn(lambda: db.apply_file(V177_PATH))
        self.assertIn("V177-GATE-001", err)


if __name__ == "__main__":
    unittest.main()
