#!/usr/bin/env python3
"""E2E: the REAL V170 canonical_work_requests pre-stage (throwaway DB).

wr-conf-020 companion: each run creates a throwaway database
(nexus_cwr_test_<pid>), builds the minimal skeleton (tackle.role_leases +
system_logs + the V163 shape registry — because V170's provenance guard and
default-shape resolver reference them), applies the real
sql/V170__canonical_work_requests.sql, then drives the live DDL: INSERT
through the provenance guards, business-key uniqueness across supersession,
WR0001/0002/0003 + WR0010/0011/0012 refusals, the supersede helper's
ordering, audit trail, and the role-scoped view. The live nexus database is
never touched.

Usage:
    CONDUIT_PG_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
      python3 -m pytest python/nexus_core/wrp/tests/test_canonical_wr_e2e.py -v
"""

import os
import sys
import unittest

import psycopg2

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", "..", ".."))

DSN = os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/postgres")
V170_PATH = os.path.join(_REPO_ROOT, "sql", "V170__canonical_work_requests.sql")

SKELETON_SQL = """
CREATE SCHEMA tackle;
CREATE SCHEMA vision;
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
-- the V163 registry + active-shape resolver (V170's column DEFAULT and
-- WR0003 read them; on live, V163 provides both — V170 layers over V163).
-- Table first, then the function: SQL function bodies validate at creation.
CREATE TABLE vision.work_request_shape_registry (
    shape_version      text PRIMARY KEY,
    artifact_path      text NOT NULL,
    artifact_sha256    text NOT NULL,
    ratification_state text NOT NULL DEFAULT 'proposed',
    ratified_at        timestamptz,
    ratified_by        text,
    notes              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    recorded_on_dt     timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt  timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);
CREATE FUNCTION vision.canonical_work_request_shape_active() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT shape_version FROM vision.work_request_shape_registry
    WHERE ratification_state = 'ratified' AND recorded_until_dt = 'infinity'::timestamptz
    LIMIT 1;
$$;
INSERT INTO vision.work_request_shape_registry
    (shape_version, artifact_path, artifact_sha256, ratification_state)
VALUES ('v0.1', 'schemas/work-request/canonical-shape.v0.1.json',
        '88a23ec5f0025af0d20922d7181b4e30dcb35c9e93ae0221d6a72f7258cbe424',
        'ratified');
"""


class _HermeticDB:
    def __init__(self):
        self.dbname = f"nexus_cwr_test_{os.getpid()}"

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
            with open(V170_PATH) as fh:
                cur.execute(fh.read())
        return self

    def __exit__(self, *exc):
        try:
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
            if cur.description:
                return cur.fetchall()
        return None

    def sql_err(self, stmt, params=None):
        try:
            self.sql(stmt, params)
        except psycopg2.Error as e:
            self.conn.rollback()
            return str(e).splitlines()[0]
        return None

    def lease(self, role="dba"):
        row = self.sql(
            "INSERT INTO tackle.role_leases (role, status) VALUES (%s, 'ACTIVE') RETURNING id",
            (role,))
        return row[0][0]


def _wr(role="dba", lease_ref=None, business_key="BK-001", payload=None):
    return {
        "business_key": business_key, "role": role, "model": "freebuff/buffy",
        "channel": "interactive", "lease_ref": lease_ref,
        "payload": payload if payload is not None else
            {"title": "Fix the sortExpr wiring", "goal": "analytics sorts live",
             "execution_linkage": {"artifact_path": "a.ts"}},  # refs-only interior
    }


class TestCanonicalWrE2E(unittest.TestCase):
    def _insert(self, db, w):
        row = db.sql(
            "INSERT INTO vision.canonical_work_requests"
            " (business_key, role, model, channel, lease_ref, payload)"
            " VALUES (%s,%s,%s,%s,%s,%s) RETURNING wr_id::text",
            (w["business_key"], w["role"], w["model"], w["channel"],
             w["lease_ref"], psycopg2.extras.Json(w["payload"])))
        return row[0][0]

    def test_happy_path_lands_with_default_shape_and_audit(self):
        import psycopg2.extras
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            wr_id = self._insert(db, _wr(role="dba", lease_ref=str(lease_id)))
            row = db.sql(
                "SELECT role, shape_version, lease_ref::text, status,"
                " payload->>'title' FROM vision.canonical_work_requests")
            self.assertEqual(row[0][0], "dba")
            self.assertEqual(row[0][1], "v0.1")   # default resolver from registry
            self.assertEqual(row[0][2], str(lease_id))
            self.assertEqual(row[0][3], "open")
            self.assertEqual(row[0][4], "Fix the sortExpr wiring")
            audit = db.sql("SELECT count(*) FROM tackle.system_logs "
                           "WHERE category = 'NEBULA_AUDIT'")
            self.assertGreaterEqual(audit[0][0], 1)  # statement trigger fired

    def test_wr0001_wr0002_lease_provenance(self):
        import psycopg2.extras
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            # WR0001: nonexistent lease
            err = db.sql_err(
                "INSERT INTO vision.canonical_work_requests"
                " (business_key, role, lease_ref) VALUES ('bk','dba',"
                " '00000000-0000-0000-0000-000000000000'::uuid)")
            self.assertIsNotNone(err)
            self.assertIn("WR0001", err)
            # WR0002: role disagreement
            err = db.sql_err(
                "INSERT INTO vision.canonical_work_requests"
                " (business_key, role, lease_ref) VALUES ('bk','engineer',%s)",
                (str(lease_id),))
            self.assertIsNotNone(err)
            self.assertIn("WR0002", err)

    def test_wr0003_unknown_shape_refused(self):
        import psycopg2.extras
        with _HermeticDB() as db:
            err = db.sql_err(
                "INSERT INTO vision.canonical_work_requests"
                " (business_key, role, shape_version) VALUES ('bk','dba','v9.9')")
            self.assertIsNotNone(err)
            self.assertIn("WR0003", err)

    def test_business_key_identifies_the_lineage(self):
        import psycopg2.extras
        with _HermeticDB() as db:
            self._insert(db, _wr(business_key="BK-DUP"))
            err = db.sql_err(
                "INSERT INTO vision.canonical_work_requests"
                " (business_key, role) VALUES ('BK-DUP','dba')")
            self.assertIsNotNone(err)  # second active row with the key refused
            # supersede: the closed incumbent vacates the key FOR ITS SUCCESSOR
            # (lineage semantics — the successor inherits the business key;
            # a partial unique index would be violated by the chain itself
            # if the predicate did not exempt closed rows)
            old = db.sql("SELECT wr_id::text FROM vision.canonical_work_requests"
                         " WHERE business_key='BK-DUP'")[0][0]
            new_id = db.sql("SELECT vision.supersede_canonical_work_request("
                            "'%s', '{\"v\":2}'::jsonb, 'dba')" % old)[0][0]
            rows = db.sql("SELECT wr_id::text, status, superseded_by::text,"
                          " recorded_until_dt = 'infinity' AS open"
                          " FROM vision.canonical_work_requests ORDER BY as_of")
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1], "superseded")
            self.assertEqual(rows[0][2], new_id)   # incumbent points at successor
            self.assertFalse(rows[0][3])           # incumbent closed
            self.assertTrue(rows[1][3])            # successor open
            # exactly ONE active row carries the key at any statement boundary
            n_active = db.sql("SELECT count(*) FROM vision.canonical_work_requests"
                              " WHERE business_key='BK-DUP'"
                              " AND recorded_until_dt = 'infinity'::timestamptz")[0][0]
            self.assertEqual(n_active, 1)
            # a NEW lineage with the same key is refused while the tail is active
            err = db.sql_err(
                "INSERT INTO vision.canonical_work_requests"
                " (business_key, role, payload) VALUES"
                " ('BK-DUP','dba','{\"v\":3}'::jsonb)")
            self.assertIsNotNone(err)

    def test_supersede_unknown_or_closed_refused(self):
        with _HermeticDB() as db:
            err = db.sql_err("SELECT vision.supersede_canonical_work_request("
                             "'00000000-0000-0000-0000-000000000000', '{}'::jsonb, 'dba')")
            self.assertIsNotNone(err)
            self.assertIn("WR0012", err)

    def test_append_only_enforced(self):
        import psycopg2.extras
        with _HermeticDB() as db:
            wr_id = self._insert(db, _wr())
            err = db.sql_err("DELETE FROM vision.canonical_work_requests")
            self.assertIsNotNone(err)
            self.assertIn("WR0010", err)
            # open rows may be updated (status transitions); frozen rows may not
            self._supersede = db.sql(
                "SELECT vision.supersede_canonical_work_request('%s', '{}'::jsonb, 'dba')" % wr_id)[0][0]
            err = db.sql_err(
                "UPDATE vision.canonical_work_requests SET status='open'"
                " WHERE wr_id = '%s'" % wr_id)
            self.assertIsNotNone(err)
            self.assertIn("WR0011", err)

    def test_view_binds_session_role_and_current_rows(self):
        import psycopg2.extras
        with _HermeticDB() as db:
            wr_id = self._insert(db, _wr(role="dba"))
            db.sql("SET vision.session_role = 'engineer'")
            n = db.sql("SELECT count(*) FROM vision.v_canonical_work_requests")[0][0]
            self.assertEqual(n, 0)  # engineer cannot see the dba WR
            db.sql("SET vision.session_role = 'dba'")
            n = db.sql("SELECT count(*) FROM vision.v_canonical_work_requests")[0][0]
            self.assertEqual(n, 1)  # dba sees its own, current rows only
            db.sql("SELECT vision.supersede_canonical_work_request("
                   "'%s', '{\"v\":2}'::jsonb, 'dba')" % wr_id)
            n = db.sql("SELECT count(*) FROM vision.v_canonical_work_requests")[0][0]
            self.assertEqual(n, 1)  # still exactly one CURRENT row (the successor)


if __name__ == "__main__":
    unittest.main()
