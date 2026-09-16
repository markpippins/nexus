#!/usr/bin/env python3
"""E2E: record_connection against the REAL V169 pre-stage (throwaway DB).

wr-conf-019 companion: each run creates a throwaway database
(nexus_acr_test_<pid>), builds the minimal skeleton, applies the real
sql/V169__agent_connections.sql, then drives the ACTUAL record_connection
live path — INSERT through the provenance guards, census row verification,
"leased but blind" validity, append-only enforcement, audit trail, and the
role-scoped census view. The live nexus database is never touched.

Usage:
    CONDUIT_PG_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
      python3 -m pytest python/nexus_core/wrp/tests/test_census_e2e_connections.py -v
"""

import os
import sys
import unittest

import psycopg2

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_SELF_DIR, "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if os.path.join(_REPO_ROOT, "python") not in sys.path:
    sys.path.insert(0, os.path.join(_REPO_ROOT, "python"))

from continuity import census  # noqa: E402

DSN = os.environ.get("CONDUIT_PG_DSN", "postgresql://pguser:pgpass@localhost:5432/postgres")
V169_PATH = os.path.join(_REPO_ROOT, "sql", "V169__agent_connections.sql")

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


class _HermeticDB:
    """Throwaway per-run database with the V169 pre-stage applied."""

    def __init__(self):
        self.dbname = f"nexus_acr_test_{os.getpid()}"

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
            with open(V169_PATH) as fh:
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


def _census(role="dba", lease_ref=None, tools=None):
    return {
        "session_id": "sess-e2e-1",
        "role": role,
        "model": "freebuff/buffy",
        "channel": "interactive",
        "lease_ref": lease_ref,
        "mcp_tools": tools if tools is not None else
            [{"server": "nebula-mcp", "tool": "nebula_get_inbox"},
             {"server": "tackle-mcp", "tool": "memory_get_procedures"}],
        "procedure_cards": {"available": True, "count": 2, "index": ["a", "b"]},
        "inbox_status": {"available": True, "pending_count": 3, "pointer": "P"},
        "handoff_context": {"digest_available": True, "digest_version": "v0.1",
                            "counts": {"open_inbox": 1, "open_threads": 0,
                                       "recent_records": 0},
                            "degraded_surfaces": 0},
        "keychains": dict(census.KEYCHAINS_ABSENT),
    }


class TestCensusE2E(unittest.TestCase):
    def _db_exec(self, db):
        def exec_fn(sql: str) -> str:
            with db.conn.cursor() as cur:
                cur.execute(sql)
                if cur.description:
                    rows = cur.fetchall()
                    return "\n".join(str(r[0]) for r in rows) if rows else ""
            return ""
        return exec_fn

    def test_happy_path_persists_census_row(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            c = _census(role="dba", lease_ref=str(lease_id))
            r = census.record_connection(c, exec_fn=self._db_exec(db))
            self.assertTrue(r["recorded"], r["reason"])
            self.assertTrue(r["conn_id"])
            row = db.sql(
                "SELECT role, model, session_id, lease_ref::text,"
                " jsonb_array_length(mcp_tools),"
                " procedure_cards->>'count', inbox_status->>'pending_count',"
                " handoff_context->>'digest_version', (keychains->>'available')"
                " FROM nebula.agent_connections")
            self.assertEqual(row[0][0], "dba")
            self.assertEqual(row[0][2], "sess-e2e-1")
            self.assertEqual(row[0][3], str(lease_id))
            self.assertEqual(row[0][4], 2)
            self.assertEqual(row[0][5], "2")
            self.assertEqual(row[0][6], "3")
            self.assertEqual(row[0][7], "v0.1")
            self.assertEqual(row[0][8], "false")  # keychains: explicitly absent
            audit = db.sql("SELECT count(*) FROM tackle.system_logs "
                           "WHERE category = 'NEBULA_AUDIT'")
            self.assertGreaterEqual(audit[0][0], 1)  # V169 statement trigger fired

    def test_leased_but_blind_is_a_valid_record(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            c = _census(role="dba", lease_ref=str(lease_id), tools=[])
            c["procedure_cards"] = {"available": False, "reason": "tackle-mcp down"}
            c["inbox_status"] = {"available": False, "reason": "nebula-mcp down"}
            c["handoff_context"] = {"digest_available": False,
                                    "reason": "flag off"}
            r = census.record_connection(c, exec_fn=self._db_exec(db))
            self.assertTrue(r["recorded"], r["reason"])
            # the census view is role-scoped: bind the session role before reading
            db.sql("SET vision.session_role = 'dba'")
            verdict = db.sql(
                "SELECT has_affordances, is_leased FROM nebula.v_agent_connections")
            self.assertEqual(verdict[0][0], False)  # blind boot, honestly labeled
            self.assertEqual(verdict[0][1], True)

    def test_lease_provenance_guards(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            # CON0001: nonexistent lease
            err = db.sql_err(
                "INSERT INTO nebula.agent_connections (session_id, role, model,"
                " channel, lease_ref) VALUES ('s','dba','m','interactive',"
                " '00000000-0000-0000-0000-000000000000'::uuid)")
            self.assertIsNotNone(err)
            self.assertIn("CON0001", err)
            # CON0002: role disagreement
            err = db.sql_err(
                "INSERT INTO nebula.agent_connections (session_id, role, model,"
                " channel, lease_ref) VALUES ('s','engineer','m','interactive',%s)",
                (str(lease_id),))
            self.assertIsNotNone(err)
            self.assertIn("CON0002", err)

    def test_append_only_enforced(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            r = census.record_connection(_census(role="dba", lease_ref=str(lease_id)),
                                         exec_fn=self._db_exec(db))
            self.assertTrue(r["recorded"])
            err = db.sql_err("DELETE FROM nebula.agent_connections")
            self.assertIsNotNone(err)
            self.assertIn("CON010", err)
            err = db.sql_err("UPDATE nebula.agent_connections SET role = 'x'")
            self.assertIsNotNone(err)

    def test_census_view_binds_session_role(self):
        with _HermeticDB() as db:
            lease_id = db.lease(role="dba")
            census.record_connection(_census(role="dba", lease_ref=str(lease_id)),
                                     exec_fn=self._db_exec(db))
            db.sql("SET vision.session_role = 'engineer'")
            n = db.sql("SELECT count(*) FROM nebula.v_agent_connections")[0][0]
            self.assertEqual(n, 0)  # engineer cannot see the dba census
            db.sql("SET vision.session_role = 'dba'")
            n = db.sql("SELECT count(*) FROM nebula.v_agent_connections")[0][0]
            self.assertEqual(n, 1)  # dba sees its own

    def test_missing_table_is_inert_even_in_e2e(self):
        with _HermeticDB() as db:
            db.sql("DROP TABLE nebula.agent_connections CASCADE")
            r = census.record_connection(_census(role="dba"),
                                         exec_fn=self._db_exec(db))
            self.assertFalse(r["recorded"])
            self.assertIn("inert gate", r["reason"])


if __name__ == "__main__":
    unittest.main()
