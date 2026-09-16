#!/usr/bin/env python3
"""E2E: persist_digest against the REAL V167 pre-stage (throwaway DB).

wr-conf-018 companion: each run creates a throwaway database
(nexus_scs_test_<pid>), builds the minimal skeleton, applies the real
sql/V167__session_context_snapshots.sql, then drives the ACTUAL
persist_digest live path (module under test, default exec path stubbed to
psycopg2) — INSERT through the provenance guards, row + audit verification,
restore view (Q3 GUC), and guard refusals. The live nexus database is
never touched.

Usage:
    CONDUIT_PG_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
      python3 -m pytest python/nexus_core/wrp/tests/test_persist_e2e_snapshots.py -v
"""

import json
import os
import sys
import unittest
from unittest import mock

import psycopg2

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if os.path.join(_REPO_ROOT, "python") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "python"))

from continuity import persist  # noqa: E402

DSN = os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/postgres")
V167_PATH = os.path.join(_REPO_ROOT, "sql", "V167__session_context_snapshots.sql")

SKELETON_SQL = """
CREATE SCHEMA tackle;
CREATE SCHEMA nebula;
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
"""

import subprocess


class _HermeticDB:
    """Throwaway per-run database with the V167 pre-stage applied."""

    def __init__(self):
        self.dbname = f"nexus_scs_test_{os.getpid()}"

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
            with open(V167_PATH) as fh:
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

    def lease(self, role="dba", status="ACTIVE"):
        row = self.sql(
            "INSERT INTO tackle.role_leases (role, status) VALUES (%s, %s) RETURNING id",
            (role, status))
        return row[0][0]


def _digest(role="dba", lease_ref=None, role_agreement=True):
    return {
        "digest_version": "v0.1",
        "disposition": "context-only",
        "role": role,
        "assembled_for_model": "freebuff/buffy",
        "as_of": "2026-09-16T17:00:00+00:00",
        "lease_binding": {"lease_ref": lease_ref, "lease_role": role,
                          "lease_model": "freebuff/buffy",
                          "expires_at": "2099-01-01T00:00:00Z",
                          "role_agreement": role_agreement},
        "level_provenance": {"level_filter_allowed": "level <= 4",
                             "applied_at": "source-query"},
        "open_inbox": [{"record_id": "11111111-1111-1111-1111-111111111111",
                        "title": "t"}],
        "open_threads": [{"thread_id": "22222222-2222-2222-2222-222222222222",
                          "title": "tt", "shared_surface": True}],
        "recent_records_metadata": [{"record_id": "33333333-3333-3333-3333-333333333333",
                                     "title": "m"}],
        "sources_degraded": [],
        "counts": {"open_inbox": 1, "open_threads": 1, "recent_records": 1},
    }


class TestPersistE2E(unittest.TestCase):
    def _db_exec(self, db):
        """persist_digest exec_fn backed by the throwaway DB connection."""
        def exec_fn(sql: str) -> str:
            with db.conn.cursor() as cur:
                cur.execute(sql)
                if cur.description:
                    rows = cur.fetchall()
                    return "\n".join(str(r[0]) for r in rows) if rows else ""
            return ""
        return exec_fn

    def test_happy_path_persists_attestable_artifact(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            d = _digest(role="dba", lease_ref=str(lease_id))
            r = persist.persist_digest(d, exec_fn=self._db_exec(db))
            self.assertTrue(r["persisted"], r["reason"])
            self.assertTrue(r["snapshot_id"])
            row = db.sql("SELECT role, model, lease_ref::text, "
                         "read_set_manifest->'sources'->>'inbox_record_ids', "
                         "level_filter_allowed FROM nebula.session_context_snapshots")
            self.assertEqual(row[0][0], "dba")
            self.assertEqual(row[0][2], str(lease_id))
            self.assertIn("11111111", row[0][3])
            self.assertEqual(row[0][4], "level <= 4")
            audit = db.sql("SELECT count(*) FROM tackle.system_logs "
                           "WHERE category = 'NEBULA_AUDIT'")
            self.assertGreaterEqual(audit[0][0], 1)  # V167 statement trigger fired

    def test_unbound_digest_leaves_table_empty(self):
        with _HermeticDB() as db:
            d = _digest(role="dba", lease_ref=None)
            d["lease_binding"]["role_agreement"] = None
            r = persist.persist_digest(d, exec_fn=self._db_exec(db))
            self.assertFalse(r["persisted"])
            n = db.sql("SELECT count(*) FROM nebula.session_context_snapshots")[0][0]
            self.assertEqual(n, 0)  # adoption gate holds at the DB level

    def test_refusal_paths_hold_against_real_guards(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            d = _digest(role="dba", lease_ref=str(lease_id))
            d["lease_binding"]["lease_model"] = "m"
            # Strip the manifest from the write path — SNAP003 must refuse.
            with mock.patch.object(persist, "build_manifest", return_value=None):
                err = db.sql_err(
                    "INSERT INTO nebula.session_context_snapshots "
                    "(role, model, lease_ref, read_set_manifest, source_records, "
                    "digest_payload, as_of) VALUES ('dba','m',%s,NULL,'[]'::jsonb,"
                    "'{}'::jsonb, now())", (str(lease_id),))
            self.assertIsNotNone(err)
            self.assertIn("SNAP003", err)

    def test_restore_view_binds_session_role(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            d = _digest(role="dba", lease_ref=str(lease_id))
            r = persist.persist_digest(d, exec_fn=self._db_exec(db))
            self.assertTrue(r["persisted"])
            db.sql("SET vision.session_role = 'engineer'")
            n = db.sql("SELECT count(*) FROM nebula.v_session_context_restore")[0][0]
            self.assertEqual(n, 0)  # engineer cannot see the dba snapshot
            db.sql("SET vision.session_role = 'dba'")
            n = db.sql("SELECT count(*) FROM nebula.v_session_context_restore")[0][0]
            self.assertEqual(n, 1)  # dba sees its own

    def test_missing_table_is_inert_even_in_e2e(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            d = _digest(role="dba", lease_ref=str(lease_id))
            db.sql("DROP TABLE nebula.session_context_snapshots CASCADE")
            r = persist.persist_digest(d, exec_fn=self._db_exec(db))
            self.assertFalse(r["persisted"])
            self.assertIn("inert gate", r["reason"])


if __name__ == "__main__":
    unittest.main()
