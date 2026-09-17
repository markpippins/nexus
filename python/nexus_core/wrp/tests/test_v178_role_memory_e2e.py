#!/usr/bin/env python3
"""E2E: V178 — tackle.role_memory integrity (overlapping-interval exclusion).

wr-conf-025/026/027 companion, house throwaway-DB pattern.

Two paths, one suite:

- RepairPathE2E: a drifted skeleton (no constraint, duplicate and
  overlapping assignment rows seeded) -> REAL V178 repairs, installs the
  btree_gist exclusion constraint, and the post-apply refusals fire.
- BootstrapPathE2E: the REAL ci-bootstrap (born-repaired shape) -> the
  constraint exists WITHOUT V178, and V178 re-applies as a verified no-op.

Pinned:
  - keep-oldest dedupe: identical-interval duplicates collapse to the
    oldest created_at row; overlapping intervals keep the older row
  - non-overlapping history SURVIVES (close-then-reassign is legitimate)
  - exclusion constraint present after repair (contype 'x')
  - refusals: exact-duplicate insert, overlapping open-row insert
  - inverted intervals (expiration_dt <= as_of_dt) refuse via GATE-002
  - idempotent re-apply: no rows lost, no error
  - born-repaired bootstrap: constraint exists pre-V178; V178 is a no-op
"""
import os
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V178_PATH = os.path.join(_REPO_ROOT, "sql", "V178__role_memory_integrity.sql")
BOOTSTRAP_PATH = os.path.join(_REPO_ROOT, "sql", "ci-bootstrap", "nexus-ci-bootstrap.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_role_mem_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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

    def apply_file(self, path, strip_meta=False):
        with open(path) as fh:
            text = fh.read()
        if strip_meta:
            lines = [ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("\\")]
            text = "\n".join(lines)
        self.sql(text)

    def expect_error(self, stmt, params=None):
        try:
            self.sql(stmt, params)
        except psycopg2.Error as exc:
            return str(exc).split("\n")[0]
        raise AssertionError("expected the statement to fail")

    def constraint_type(self, name="uq_role_memory_validity"):
        return self.sql("""
SELECT contype FROM pg_constraint
 WHERE conrelid='tackle.role_memory'::regclass AND conname=%s
""", (name,))

    def add_memory(self, slug="card-a"):
        return self.sql("""
INSERT INTO tackle.memory (slug, title, summary, body_md)
VALUES (%s, 'T', 'S', 'B') RETURNING id
""", (slug,))

    def assign(self, memory_id, role, as_of, exp, created_hint=None):
        self.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt)
VALUES (%s, %s, %s::timestamptz,
        CASE WHEN %s IS NULL THEN NULL ELSE %s::timestamptz END)
""", (memory_id, role, as_of, exp, exp))


SKELETON_SQL = """
CREATE SCHEMA tackle;

CREATE TABLE tackle.roles (
    name       text PRIMARY KEY,
    role       text,
    level      integer,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tackle.memory (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug       text UNIQUE,
    title      text,
    summary    text,
    body_md    text,
    tags       text[],
    triggers   text[],
    mcp_tools  text[],
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tackle.role_memory (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id     uuid NOT NULL REFERENCES tackle.memory(id),
    role          text NOT NULL REFERENCES tackle.roles(name),
    as_of_dt      timestamptz NOT NULL DEFAULT now(),
    expiration_dt timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);
"""


class RepairPathE2E(unittest.TestCase):
    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(self.db.__exit__)
        self.db.sql(SKELETON_SQL)
        self.db.sql("INSERT INTO tackle.roles (name) VALUES ('engineer'), ('engineer-ii')")
        self.mid = self.db.add_memory()
        self.mid2 = self.db.add_memory("card-b")
        # Scenario A — exact duplicates (same interval): (mid, engineer).
        # Older row must survive.
        self.db.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt, created_at)
VALUES (%s, 'engineer', '2026-01-01', NULL, '2026-01-01 10:00'),
       (%s, 'engineer', '2026-01-01', NULL, '2026-01-02 10:00')
""", (self.mid, self.mid))
        # Scenario B — overlapping distinct intervals: (mid, engineer-ii).
        # [Jan3,Jan10) and [Jan7,Jan10) overlap; older (Jan3) must survive.
        self.db.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt, created_at)
VALUES (%s, 'engineer-ii', '2026-01-03', '2026-01-10', '2026-01-03 10:00'),
       (%s, 'engineer-ii', '2026-01-07', '2026-01-10', '2026-01-07 10:00')
""", (self.mid, self.mid))
        # Scenario C — legitimate history that must SURVIVE: (mid2, engineer).
        # [Jan12,Jan15) then [Jan15,open) — touching, not overlapping.
        self.db.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt, created_at)
VALUES (%s, 'engineer', '2026-01-12', '2026-01-15', '2026-01-12 10:00'),
       (%s, 'engineer', '2026-01-15', NULL,        '2026-01-15 10:00')
""", (self.mid2, self.mid2))

    def test_repair_keeps_oldest_and_installs_constraint(self):
        self.db.apply_file(V178_PATH)
        self.assertEqual("x", self.db.constraint_type())
        # Scenario A: exact dup pair collapses to the older row.
        self.assertEqual(1, self.db.sql("""
SELECT count(*) FROM tackle.role_memory WHERE memory_id=%s AND role='engineer'
""", (self.mid,)))
        self.assertEqual(1, self.db.sql("""
SELECT count(*) FROM tackle.role_memory
 WHERE memory_id=%s AND role='engineer' AND created_at='2026-01-01 10:00'::timestamptz
""", (self.mid,)))
        # Scenario B: overlap pair keeps the older (Jan3), drops Jan7.
        self.assertEqual(1, self.db.sql("""
SELECT count(*) FROM tackle.role_memory WHERE memory_id=%s AND role='engineer-ii'
""", (self.mid,)))
        self.assertEqual(1, self.db.sql("""
SELECT count(*) FROM tackle.role_memory
 WHERE memory_id=%s AND role='engineer-ii' AND as_of_dt='2026-01-03'::timestamptz
""", (self.mid,)))
        self.assertEqual(0, self.db.sql("""
SELECT count(*) FROM tackle.role_memory
 WHERE memory_id=%s AND role='engineer-ii' AND as_of_dt='2026-01-07'::timestamptz
""", (self.mid,)))
        # Scenario C: non-overlapping chain survived untouched.
        self.assertEqual(2, self.db.sql("""
SELECT count(*) FROM tackle.role_memory WHERE memory_id=%s AND role='engineer'
""", (self.mid2,)))

    def test_refusal_exact_duplicate(self):
        self.db.apply_file(V178_PATH)
        try:
            self.db.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt)
VALUES (%s, 'engineer', '2026-01-01', NULL)
""", (self.mid,))
        except psycopg2.Error as exc:
            self.assertIn("uq_role_memory_validity", str(exc))
        else:
            self.fail("exact duplicate of the open row must be refused")

    def test_refusal_overlapping_new_row(self):
        self.db.apply_file(V178_PATH)
        try:
            self.db.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt)
VALUES (%s, 'engineer', '2026-01-14', '2026-01-20')
""", (self.mid,))
        except psycopg2.Error as exc:
            self.assertIn("uq_role_memory_validity", str(exc))
        else:
            self.fail("overlapping interval must be refused")

    def test_idempotent_reapply_no_data_loss(self):
        self.db.apply_file(V178_PATH)
        before = self.db.sql("SELECT count(*) FROM tackle.role_memory")
        self.db.apply_file(V178_PATH)
        self.assertEqual(before, self.db.sql("SELECT count(*) FROM tackle.role_memory"))
        self.assertEqual("x", self.db.constraint_type())

    def test_gate_inverted_interval(self):
        db2 = ThrowawayDB().__enter__()
        self.addCleanup(db2.__exit__)
        db2.sql(SKELETON_SQL)
        db2.sql("INSERT INTO tackle.roles (name) VALUES ('engineer')")
        mid = db2.add_memory()
        db2.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt, expiration_dt)
VALUES (%s, 'engineer', '2026-01-10', '2026-01-05')
""", (mid,))
        try:
            db2.apply_file(V178_PATH)
        except psycopg2.Error as exc:
            self.assertIn("V178-GATE-002", str(exc))
        else:
            self.fail("inverted interval must refuse via GATE-002")
        # The gate aborted inside the file's explicit BEGIN — clear the
        # stranded transaction (sql-pitfalls gotcha #5) before probing.
        try:
            db2.sql("ROLLBACK")
        except psycopg2.Error:
            pass
        self.assertFalse(db2.constraint_type(), "nothing installed on gate refusal")


class BootstrapPathE2E(unittest.TestCase):
    """The ci-bootstrap carries the born-repaired shape; V178 is a no-op."""

    def test_born_repaired_shape_and_noop_v178(self):
        db = ThrowawayDB().__enter__()
        self.addCleanup(db.__exit__)
        db.apply_file(BOOTSTRAP_PATH, strip_meta=True)
        self.assertEqual(
            "x", db.constraint_type(),
            "bootstrap must carry the exclusion constraint WITHOUT V178")
        # V178 on top: converges silently (constraint already present).
        db.apply_file(V178_PATH)
        self.assertEqual("x", db.constraint_type())
        # Real pair insert works, exact duplicate refused. (The real
        # bootstrap carries NOT NULLs on memory's text columns and no
        # slug-unique — satisfy the NOT NULLs explicitly.)
        db.sql("""
INSERT INTO tackle.memory (slug, title, summary, body_md)
VALUES ('born', 't', 's', 'b')
""")
        mid = db.sql("SELECT id FROM tackle.memory WHERE slug='born'")
        db.sql("INSERT INTO tackle.roles (name) VALUES ('engineer') ON CONFLICT DO NOTHING")
        db.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt) VALUES (%s, 'engineer', now())
""", (mid,))
        try:
            db.sql("""
INSERT INTO tackle.role_memory (memory_id, role, as_of_dt) VALUES (%s, 'engineer', now())
""", (mid,))
        except psycopg2.Error as exc:
            self.assertIn("uq_role_memory_validity", str(exc))
        else:
            self.fail("bootstrap-born constraint must refuse duplicates")


if __name__ == "__main__":
    unittest.main()
