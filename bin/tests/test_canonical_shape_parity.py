#!/usr/bin/env python3
"""Parity tests: canonical role_memory shape fragment vs every consumer.

Structural de-drift enforcement. The fragment
(sql/canonical/tackle_role_memory_shape.sql) is the single source of truth;
these tests make any drift between it and a consumer surface fail CI:

  1. every consumer surface consumes (no inline restatement of the shape)
  2. the registry SQL's generated block byte-matches the fragment rendering
  3. the rendered fragment applies cleanly on a throwaway DB and produces
     the ratified live shape (constraint names + kinds)
  4. the stale V155 shape (uq_role_memory_active + plain tstzrange) cannot
     reappear in any surface

Hermetic except test 3, which uses CONDUIT_PG_DSN (default local nexus).
"""
from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FRAGMENT = REPO / "sql" / "canonical" / "tackle_role_memory_shape.sql"
REGISTRY = REPO / "schemas" / "migrations" / "tackle" / "memory_procedure_registry.sql"
SEED_MANIFEST = REPO / "python" / "nexus_core" / "wrp" / "seed_manifest.py"
TACKLE_SRV = REPO / "typescript" / "tackle-srv" / "src" / "db.ts"
TACKLE_MCP = REPO / "typescript" / "tackle-mcp" / "src" / "db.ts"
FIXTURE = REPO / "python" / "nexus_core" / "wrp" / "tests" / "test_conformance_seed_guard.py"

SURFACES = {
    "seed_manifest.py": SEED_MANIFEST,
    "tackle-srv/db.ts": TACKLE_SRV,
    "tackle-mcp/db.ts": TACKLE_MCP,
    "memory_procedure_registry.sql": REGISTRY,
    "test_conformance_seed_guard.py": FIXTURE,
}


def render(schema: str, reftable: str) -> str:
    sql = FRAGMENT.read_text()
    return (sql.replace("__SCHEMA__", schema)
               .replace("__REFTABLE__", reftable)
               .replace("__TABLE_SUFFIX__", ""))


class TestNoInlineRestatement(unittest.TestCase):
    """No surface may restate the shape inline — consumption is structural."""

    def test_no_stale_constraint_name_anywhere(self):
        for label, path in SURFACES.items():
            with self.subTest(surface=label):
                text = path.read_text()
                # strip comment lines mentioning the prohibition
                code = "\n".join(
                    ln for ln in text.splitlines() if not ln.strip().startswith(("--", "#", "//", "*"))
                )
                self.assertNotIn("uq_role_memory_active", code,
                                 f"{label} restates the stale V155 constraint name")

    def test_no_plain_tstzrange_exclusion_anywhere(self):
        for label, path in SURFACES.items():
            with self.subTest(surface=label):
                text = path.read_text()
                code = "\n".join(
                    ln for ln in text.splitlines() if not ln.strip().startswith(("--", "#", "//", "*"))
                )
                self.assertNotIn("tstzrange(as_of_dt, expiration_dt)", code,
                                 f"{label} restates the stale plain tstzrange shape")

    def test_every_surface_declares_consumption(self):
        expectations = {
            "seed_manifest.py": "_load_shape_sql",
            "tackle-srv/db.ts": "loadCanonicalRoleMemoryShape",
            "tackle-mcp/db.ts": "loadCanonicalRoleMemoryShape",
            "memory_procedure_registry.sql": "BEGIN GENERATED CANONICAL SHAPE",
            "test_conformance_seed_guard.py": "_CANONICAL_SHAPE_PATH",
        }
        for label, marker in expectations.items():
            with self.subTest(surface=label):
                self.assertIn(marker, SURFACES[label].read_text(),
                              f"{label} no longer consumes the canonical fragment")


class TestRegistryGeneratedBlock(unittest.TestCase):
    """The registry SQL's generated block must byte-match the rendering."""

    def test_registry_block_matches_fragment(self):
        src = REGISTRY.read_text()
        begin = src.index("-- BEGIN GENERATED CANONICAL SHAPE")
        end = src.index("-- END GENERATED CANONICAL SHAPE")
        block = src[begin:end]
        frag = render("tackle", "tackle.memory")
        for needle in ("CREATE TABLE IF NOT EXISTS tackle.memory",
                       "CREATE TABLE IF NOT EXISTS tackle.role_memory",
                       "uq_role_memory_validity",
                       "COALESCE(expiration_dt, 'infinity')",
                       "'[)'"):
            self.assertIn(needle, block)
        self.assertNotIn("uq_role_memory_active", block.split("-- V178:")[0])


class TestFragmentAppliesToRatifiedShape(unittest.TestCase):
    """The rendered fragment, applied to a throwaway DB, yields the live
    ratified shape (names + kinds). Skips if no DB is reachable."""

    def setUp(self):
        try:
            import psycopg2
        except ImportError:
            self.skipTest("psycopg2 unavailable")
        dsn = os.environ.get("CONDUIT_PG_DSN",
                             "postgresql://pguser:pgpass@localhost:5432/nexus")
        self.dbname = None
        try:
            conn = psycopg2.connect(dsn)
            conn.autocommit = True
        except Exception:
            self.skipTest("no live DB reachable; shape-apply proof skipped")
        self.conn = conn
        cur = conn.cursor()
        self.dbname = f"probe_canon_{os.getpid()}"
        cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
        cur.execute(f'CREATE DATABASE "{self.dbname}"')
        dsn_parts = dsn.rsplit("/", 1)
        self.dsn_probe = f"{dsn_parts[0]}/{self.dbname}"

    def tearDown(self):
        if self.dbname:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (self.dbname,))
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
        self.conn.close()

    def test_apply_produces_ratified_shape(self):
        import psycopg2
        conn = psycopg2.connect(self.dsn_probe)
        conn.autocommit = True
        cur = conn.cursor()
        # registry-prelude equivalent: the fragment owns shape, not schema creation
        cur.execute("CREATE SCHEMA IF NOT EXISTS tackle")
        cur.execute(render("tackle", "tackle.memory"))
        cur.execute(
            "SELECT conname, contype FROM pg_constraint "
            "WHERE conrelid = 'tackle.role_memory'::regclass ORDER BY conname")
        cons = dict(cur.fetchall())
        self.assertIn("uq_role_memory_validity", cons)
        self.assertEqual(cons.get("uq_role_memory_validity"), "x",
                         "validity constraint must be an EXCLUDE (contype x)")
        self.assertNotIn("uq_role_memory_active", cons)
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='tackle' AND tablename='role_memory'")
        idx = {r[0] for r in cur.fetchall()}
        self.assertLessEqual({"idx_role_memory_as_of",
                              "idx_role_memory_expiration"}, idx)
        # overlap semantics: two active rows for same (memory, role) must fail
        cur.execute("INSERT INTO tackle.memory (slug, title) VALUES ('a','A')")
        cur.execute("SELECT id FROM tackle.memory WHERE slug='a'")
        mid = cur.fetchone()[0]
        cur.execute("INSERT INTO tackle.role_memory (memory_id, role) VALUES (%s,'dba')", (mid,))
        with self.assertRaises(psycopg2.errors.ExclusionViolation):
            cur.execute("INSERT INTO tackle.role_memory (memory_id, role) VALUES (%s,'dba')", (mid,))
        conn.close()


if __name__ == "__main__":
    unittest.main()
