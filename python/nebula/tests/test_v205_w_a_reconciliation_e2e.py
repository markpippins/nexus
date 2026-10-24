"""E2E: V205 — W-A duplicate-history reconciliation ledger (plan 8261653).

wr-conf house pattern (V198 companion): the REAL migration applies to a
throwaway database built from a faithful minimal skeleton, then the
ratified behaviour is probed end-to-end. V205 depends on nothing but the
``resolution`` schema and ``pgcrypto``, so the skeleton is minimal.

What this asserts:

  Apply        — the three surfaces exist, are EMPTY on apply, the
                 append-only triggers exist, and a second apply is refused
                 by the preflight (no silently divergent second ledger).
  Constraints  — the vocabulary/identity CHECK pins reject violations.
  AppendOnly   — UPDATE and DELETE on the ledger and the fingerprint trail
                 raise ``restrict_violation``; evidence cannot be rewritten.
  SetLifecycle — registration, the member-count status rule
                 (identified → dispositioning → dispositioned), and the
                 explicit-refusal rules (unknown set, bad disposition,
                 missing reason, merge without a distinct primary).
  Generations  — a re-decision appends generation+1 and the effective
                 disposition returns the newest row; the earlier decision
                 remains readable.
  Fingerprints — every disposition writes a fingerprint_audit row, closing
                 the C4 gap where target_refs alone cannot distinguish rows.

Run: ``python3 -m pytest python/nebula/tests/test_v205_w_a_reconciliation_e2e.py``
Requires a PostgreSQL the current user can create databases on (defaults to
the local nexus DSN; override with ``CONDUIT_PG_DSN``).
"""

from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path

import psycopg2

_REPO_ROOT = Path(__file__).resolve().parents[3]
V205_PATH = (
    _REPO_ROOT / "sql" / "V205__w_a_duplicate_history_reconciliation.sql"
)
DSN = os.environ.get(
    "CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/nexus"
)

SKELETON = """
-- Extensions are per-database: the throwaway installs pgcrypto itself
-- (V205's preflight refuses loudly on a host that lacks it).
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA resolution;
"""


class _DB:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.admin_dsn = dsn.rsplit("/", 1)[0] + "/postgres"
        self.dbname = "v205_e2e_" + uuid.uuid4().hex[:12]
        self.conn = None

    def __enter__(self):
        admin = psycopg2.connect(self.admin_dsn)
        admin.autocommit = True
        try:
            with admin.cursor() as cur:
                cur.execute(f'CREATE DATABASE "{self.dbname}"')
        finally:
            admin.close()
        self.conn = psycopg2.connect(self.dsn.rsplit("/", 1)[0] + "/" + self.dbname)
        self.conn.autocommit = True
        return self

    def __exit__(self, *exc):
        try:
            if self.conn:
                self.conn.close()
        finally:
            admin = psycopg2.connect(self.admin_dsn)
            admin.autocommit = True
            try:
                with admin.cursor() as cur:
                    cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            finally:
                admin.close()

    def sql(self, stmt, params=None):
        with self.conn.cursor() as cur:
            cur.execute(stmt, params)
            return cur.fetchall() if cur.description else None

    def scalar(self, stmt, params=None):
        rows = self.sql(stmt, params)
        if not rows:
            return None
        return rows[0][0]

    def apply(self, path: Path):
        sql = path.read_text(encoding="utf-8")
        with self.conn.cursor() as cur:
            cur.execute(sql)


def _register(db, set_id="set-1", member_count=2, kind="duplicate"):
    # identity_value is scoped to the set id so independent fixtures in one
    # database do not collide on reconciliation_set_identity_key.
    db.sql(
        "SELECT resolution.upsert_reconciliation_set(%s, %s, %s, %s, %s, %s, %s)",
        (set_id, "resolution.receipt", kind, "C1", "payload_fingerprint",
         f"identity-{set_id}", member_count),
    )


def _record(db, set_id="set-1", member_ref="r1", fingerprint_=None,
            disposition="preserve", reason="primary of the set",
            primary_ref=None):
    # Fingerprint-audit rows are unique per (surface, source_ref, fingerprint,
    # generation); derive a per-set fingerprint so independent fixtures in the
    # shared class database cannot collide and silently ON CONFLICT DO NOTHING.
    if fingerprint_ is None:
        fingerprint_ = f"fp-{set_id}-{member_ref}"
    return db.scalar(
        "SELECT resolution.record_reconciliation_disposition(%s, %s, %s, %s, %s, %s)",
        (set_id, member_ref, fingerprint_, disposition, reason, primary_ref),
    )


class V205Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not V205_PATH.exists():
            raise unittest.SkipTest(f"V205 migration not found at {V205_PATH}")
        cls.db = _DB(DSN).__enter__()
        cls.db.sql(SKELETON)
        cls.db.apply(V205_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.db.__exit__(None, None, None)


class Apply(V205Base):
    def test_surfaces_exist(self):
        for table in (
            "reconciliation_set",
            "reconciliation_disposition",
            "fingerprint_audit",
        ):
            self.assertIsNotNone(
                self.db.scalar("SELECT to_regclass(%s)", (f"resolution.{table}",))
            )

    def test_ledger_and_audit_start_empty(self):
        self.assertEqual(
            self.db.scalar("SELECT count(*) FROM resolution.reconciliation_disposition"),
            0,
        )
        self.assertEqual(
            self.db.scalar("SELECT count(*) FROM resolution.fingerprint_audit"), 0
        )

    def test_append_only_triggers_exist(self):
        names = {
            r[0]
            for r in self.db.sql(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
            )
        }
        self.assertIn("trg_reconciliation_disposition_append_only", names)
        self.assertIn("trg_fingerprint_audit_append_only", names)

    def test_double_apply_is_refused(self):
        with self.assertRaises(psycopg2.errors.RaiseException):
            self.db.apply(V205_PATH)


class Constraints(V205Base):
    def test_unknown_set_kind_is_refused(self):
        with self.assertRaises(psycopg2.errors.RaiseException):
            _register(self.db, set_id="bad-kind", kind="rather-not")

    def test_negative_member_count_is_refused(self):
        with self.assertRaises(psycopg2.errors.RaiseException):
            _register(self.db, set_id="bad-count", member_count=-1)

    def test_generation_zero_is_refused(self):
        _register(self.db, set_id="gen-zero")
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.db.sql(
                "INSERT INTO resolution.reconciliation_disposition "
                "(set_id, member_ref, member_fingerprint, disposition, reason, generation) "
                "VALUES ('gen-zero', 'r1', 'fp', 'preserve', 'why', 0)"
            )

    def test_set_kind_check_pin(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            self.db.sql(
                "INSERT INTO resolution.reconciliation_set "
                "(set_id, surface, set_kind, condition_ref, identity_kind, identity_value) "
                "VALUES ('raw-bad', 's', 'bogus', 'C1', 'k', 'v')"
            )


class AppendOnly(V205Base):
    def setUp(self):
        self.set_id = "ao-" + uuid.uuid4().hex[:8]
        _register(self.db, set_id=self.set_id, member_count=1)
        _record(self.db, set_id=self.set_id, member_ref="r1")

    def test_update_is_refused(self):
        with self.assertRaises(psycopg2.errors.RestrictViolation):
            self.db.sql(
                "UPDATE resolution.reconciliation_disposition SET reason = 'rewritten' "
                "WHERE set_id = %s",
                (self.set_id,),
            )

    def test_delete_is_refused(self):
        with self.assertRaises(psycopg2.errors.RestrictViolation):
            self.db.sql(
                "DELETE FROM resolution.reconciliation_disposition WHERE set_id = %s",
                (self.set_id,),
            )

    def test_fingerprint_audit_update_is_refused(self):
        with self.assertRaises(psycopg2.errors.RestrictViolation):
            self.db.sql(
                "UPDATE resolution.fingerprint_audit SET fingerprint = 'rewritten' "
                "WHERE set_id = %s",
                (self.set_id,),
            )

    def test_fingerprint_audit_delete_is_refused(self):
        with self.assertRaises(psycopg2.errors.RestrictViolation):
            self.db.sql(
                "DELETE FROM resolution.fingerprint_audit WHERE set_id = %s",
                (self.set_id,),
            )


class SetLifecycle(V205Base):
    def test_unknown_set_is_refused(self):
        with self.assertRaises(psycopg2.errors.RaiseException):
            _record(self.db, set_id="never-registered")

    def test_bad_disposition_is_refused(self):
        _register(self.db, set_id="bad-disp", member_count=1)
        with self.assertRaises(psycopg2.errors.RaiseException):
            _record(self.db, set_id="bad-disp", disposition="delete")

    def test_missing_reason_is_refused(self):
        _register(self.db, set_id="no-reason", member_count=1)
        with self.assertRaises(psycopg2.errors.RaiseException):
            _record(self.db, set_id="no-reason", reason="   ")

    def test_merge_requires_a_distinct_primary(self):
        _register(self.db, set_id="merge-bad", member_count=2)
        with self.assertRaises(psycopg2.errors.RaiseException):
            _record(self.db, set_id="merge-bad", member_ref="r2", disposition="merge")
        with self.assertRaises(psycopg2.errors.RaiseException):
            _record(
                self.db, set_id="merge-bad", member_ref="r2",
                disposition="merge", primary_ref="r2",
            )

    def test_member_count_drives_the_status_transition(self):
        _register(self.db, set_id="lifecycle", member_count=2)
        self.assertEqual(
            self.db.scalar(
                "SELECT status FROM resolution.reconciliation_set WHERE set_id = 'lifecycle'"
            ),
            "identified",
        )
        _record(self.db, set_id="lifecycle", member_ref="r1")
        self.assertEqual(
            self.db.scalar(
                "SELECT status FROM resolution.reconciliation_set WHERE set_id = 'lifecycle'"
            ),
            "dispositioning",
        )
        _record(self.db, set_id="lifecycle", member_ref="r2", fingerprint_="fp-2")
        row = self.db.sql(
            "SELECT status, verified_at IS NOT NULL "
            "FROM resolution.reconciliation_set WHERE set_id = 'lifecycle'"
        )[0]
        self.assertEqual(row[0], "dispositioned")
        self.assertTrue(row[1])


class Generations(V205Base):
    def test_redecision_appends_generation_two(self):
        _register(self.db, set_id="redecision", member_count=1)
        _record(self.db, set_id="redecision", member_ref="r1",
                disposition="preserve", reason="first pass")
        _record(self.db, set_id="redecision", member_ref="r1",
                disposition="archive", reason="later evidence: retire")

        gens = self.db.sql(
            "SELECT generation, disposition, reason "
            "FROM resolution.reconciliation_disposition "
            "WHERE set_id = 'redecision' ORDER BY generation"
        )
        self.assertEqual([g[0] for g in gens], [1, 2])
        self.assertEqual(gens[0][1], "preserve")  # the earlier decision survives

        effective = self.db.sql(
            "SELECT disposition, generation "
            "FROM resolution.reconciliation_effective_disposition('redecision', 'r1')"
        )
        self.assertEqual(effective[0], ("archive", 2))


class Fingerprints(V205Base):
    def test_every_disposition_writes_a_fingerprint_audit_row(self):
        _register(self.db, set_id="fp-audit", member_count=2)
        _record(self.db, set_id="fp-audit", member_ref="r1", fingerprint_="fp-1")
        _record(self.db, set_id="fp-audit", member_ref="r2", fingerprint_="fp-2")
        rows = self.db.sql(
            "SELECT source_ref, fingerprint FROM resolution.fingerprint_audit "
            "WHERE set_id = 'fp-audit' ORDER BY source_ref"
        )
        self.assertEqual(rows, [("r1", "fp-1"), ("r2", "fp-2")])

    def test_audit_row_is_linked_to_the_set(self):
        _register(self.db, set_id="fp-link", member_count=1)
        _record(self.db, set_id="fp-link", member_ref="r1")
        self.assertEqual(
            self.db.scalar(
                "SELECT set_id FROM resolution.fingerprint_audit WHERE source_ref = 'r1'"
            ),
            "fp-link",
        )


if __name__ == "__main__":
    unittest.main()
