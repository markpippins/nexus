#!/usr/bin/env python3
"""E2E: V185 close-code vocabulary — real migration, throwaway DB (inspector G3).

wr-conf house pattern (V179/V184 companions). The REAL
sql/V185__close_reason_unknown.sql applies to a throwaway database carrying
the pre-V185 (V130-shape) skeleton, and the full contract is exercised
against live constraint behavior — no mocks on the DB path:

  - pre-state: the V130 CHECK accepts the seven original codes and REJECTS
    'unknown' (proving the migration is what unlocks it)
  - apply: the REAL V185 runs cleanly; idempotent re-run is a no-op
  - post-state: all seven original codes still accepted, 'unknown' accepted,
    junk still rejected (23514)
  - existing-data safety: a row closed under the old vocabulary survives
    the constraint rebuild untouched
  - closure semantics: the single-writer close UPDATE pattern still works

Suite creates and drops its own throwaway database; no production DB.

Run:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_v185_close_code_e2e.py -v
"""

import os
import sys
import unittest
import uuid

import psycopg2

_REPO = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V185_PATH = os.path.join(_REPO, "sql", "V185__close_reason_unknown.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SKELETON_SQL = """
CREATE SCHEMA duality;
CREATE TABLE duality.session_watches (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id           text NOT NULL,
    forum_slug          text NOT NULL DEFAULT 'duality-sessions',
    role                text NOT NULL,
    lease_id            uuid,
    max_turns           integer NOT NULL DEFAULT 20,
    turn_count          integer NOT NULL DEFAULT 0,
    idle_timeout_ms     integer NOT NULL DEFAULT 300000,
    last_activity       timestamptz NOT NULL DEFAULT now(),
    status              text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active','paused','closed','expired')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    execution_backend   text,
    -- V130 shape: closed_reason with the SEVEN-value inline CHECK.
    closed_reason       TEXT
                        CHECK (closed_reason IN ('lease_revoked',
                               'lease_exhausted', 'lease_expired', 'turns',
                               'agent', 'idle', 'natural'))
);
CREATE EXTENSION IF NOT EXISTS pgcrypto;
"""


def load_v185_sql():
    with open(V185_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def constraint_def(cur):
    cur.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'duality.session_watches'::regclass "
        "AND conname = 'session_watches_closed_reason_check'")
    row = cur.fetchone()
    return row[0] if row else None


def try_insert_watch(cur, closed_reason):
    """Insert a closed watch row with the given close code.

    Returns True when accepted, False when the CHECK rejected it.
    Uses a savepoint so the failure never aborts the outer transaction.
    """
    cur.execute("SAVEPOINT g3_try")
    try:
        cur.execute(
            """INSERT INTO duality.session_watches
                   (thread_id, role, status, closed_reason)
               VALUES ('t-g3', 'dba', 'closed', %s)""", (closed_reason,))
        cur.execute("RELEASE SAVEPOINT g3_try")
        return True
    except psycopg2.errors.CheckViolation:
        cur.execute("ROLLBACK TO SAVEPOINT g3_try")
        return False


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_v185_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.conn = None

    def __enter__(self):
        admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            cur.execute(f'CREATE DATABASE "{self.dbname}"')
        admin.close()
        self.conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.dbname)
        # autocommit OFF: the CHECK-rejection probes run inside transaction
        # blocks so savepoints can isolate failures (autocommit would make
        # SAVEPOINT illegal). Helpers commit explicitly.
        with self.conn.cursor() as cur:
            cur.execute(SKELETON_SQL)
        self.conn.commit()
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
        return False


class V185CloseCodeE2E(unittest.TestCase):
    """Real V185 against a real throwaway DB — constraint behavior, live."""

    def setUp(self):
        self.db = ThrowawayDB()
        self.db.__enter__()          # stores the live conn on self.db.conn
        self.conn = self.db.conn
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.cur.close()
        self.db.__exit__()
        self.cur = None

    def test_pre_state_rejects_unknown(self):
        """Prove the migration is what unlocks 'unknown' (V130 shape)."""
        # The original seven all pass pre-V185.
        for code in ("lease_revoked", "lease_exhausted", "lease_expired",
                     "turns", "agent", "idle", "natural"):
            self.assertTrue(try_insert_watch(self.cur, code), code)
        self.conn.commit()
        # 'unknown' is rejected pre-V185.
        self.assertFalse(try_insert_watch(self.cur, "unknown"))

    def test_apply_unlocks_unknown_and_preserves_originals(self):
        self.cur.execute(load_v185_sql())
        # All seven original codes still accepted.
        for code in ("lease_revoked", "lease_exhausted", "lease_expired",
                     "turns", "agent", "idle", "natural"):
            self.assertTrue(try_insert_watch(self.cur, code), code)
        # 'unknown' now accepted.
        self.assertTrue(try_insert_watch(self.cur, "unknown"))
        # Junk still rejected.
        self.assertFalse(try_insert_watch(self.cur, "fell_off_a_cliff"))

    def test_existing_data_survives_constraint_rebuild(self):
        """A legacy 'lease_expired' row survives V185 untouched."""
        self.cur.execute(
            """INSERT INTO duality.session_watches
                   (thread_id, role, status, closed_reason)
               VALUES ('t-legacy', 'dba', 'closed', 'lease_expired')""")
        self.cur.execute(load_v185_sql())
        self.cur.execute(
            "SELECT closed_reason FROM duality.session_watches "
            "WHERE thread_id = 't-legacy'")
        self.assertEqual(self.cur.fetchone()[0], "lease_expired")

    def test_removal_of_specific_codes_is_not_silent(self):
        """The rebuild must be strictly additive — old CHECK is dropped."""
        self.cur.execute(load_v185_sql())
        cdef = constraint_def(self.cur)
        self.assertIsNotNone(cdef)
        self.assertIn("unknown", cdef)

    def test_idempotent_reapply(self):
        self.cur.execute(load_v185_sql())
        self.cur.execute(load_v185_sql())  # second run: guarded no-op
        self.assertTrue(try_insert_watch(self.cur, "unknown"))

    def test_closure_single_writer_pattern_unchanged(self):
        """The V130 close path (UPDATE ... WHERE status <> 'closed') still
        lands a valid code post-V185."""
        self.cur.execute(load_v185_sql())
        self.cur.execute(
            """INSERT INTO duality.session_watches
                   (thread_id, role, status) VALUES ('t-open', 'dba', 'active')""")
        self.cur.execute(
            "UPDATE duality.session_watches SET status='closed', "
            "closed_reason='unknown' WHERE thread_id='t-open' "
            "AND status <> 'closed'")
        self.cur.execute(
            "SELECT status, closed_reason FROM duality.session_watches "
            "WHERE thread_id='t-open'")
        row = self.cur.fetchone()
        self.assertEqual(row, ("closed", "unknown"))


if __name__ == "__main__":
    unittest.main()
