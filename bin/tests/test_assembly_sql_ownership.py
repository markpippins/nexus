#!/usr/bin/env python3
"""Hermetic guard for assembly SQL-migration ownership.

assembly-mcp used to own the assembly schema: it had a `db.ts` migration
runner and its own copy of `assembly-migration.sql`. SQL ownership moved to
assembly-srv (the runner's own comment still reads "migrated from
assembly-mcp db.ts"), and `assembly-client.ts` states that assembly-mcp now
has ZERO direct pg dependencies.

`src/db.ts` was deleted but `assembly-migration.sql` was not. The surviving
copy was dead and had drifted ~110 lines from the canonical one, missing the
`builder`, `tester`, and `supervisor` seeds added later to assembly-srv's
copy. It was a trap: anyone reading it would conclude assembly-mcp still
applies the schema, and re-introduce the old naive `split(';')` runner.

These tests pin the ownership boundary so the copy cannot come back.

No database or network access.
"""
from __future__ import annotations

import json
import os
import re
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

MCP_DIR = os.path.join(ROOT, "typescript", "assembly-mcp")
SRV_DIR = os.path.join(ROOT, "typescript", "assembly-srv")

MCP_MIGRATION_SQL = os.path.join(MCP_DIR, "assembly-migration.sql")
SRV_MIGRATION_SQL = os.path.join(SRV_DIR, "assembly-migration.sql")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class AssemblySqlOwnership(unittest.TestCase):
    def test_assembly_srv_owns_the_migration_sql(self):
        """The canonical migration must live with the service that applies it."""
        self.assertTrue(
            os.path.isfile(SRV_MIGRATION_SQL),
            "typescript/assembly-srv/assembly-migration.sql is the canonical "
            "migration and is applied by src/db.js on startup; it must exist.",
        )

    def test_assembly_mcp_does_not_ship_a_dead_migration_copy(self):
        """assembly-mcp owns no SQL, so it must carry no migration SQL."""
        self.assertFalse(
            os.path.exists(MCP_MIGRATION_SQL),
            "typescript/assembly-mcp/assembly-migration.sql is dead: "
            "assembly-mcp has no pg dependency and no migration runner since "
            "SQL ownership moved to assembly-srv. A copy here is a stale "
            "trap — delete it rather than letting it drift further.",
        )

    def test_assembly_mcp_has_no_runtime_pg_dependency(self):
        with open(os.path.join(MCP_DIR, "package.json"), encoding="utf-8") as fh:
            pkg = json.load(fh)
        deps = dict(pkg.get("dependencies") or {})
        self.assertNotIn(
            "pg",
            deps,
            "assembly-mcp must not depend on pg at runtime — it talks to "
            "assembly-srv over HTTP (see src/assembly-client.ts). Re-adding "
            "the dependency re-opens the question of who owns migrations.",
        )

    def test_assembly_mcp_source_has_no_sql_migration_runner(self):
        """No source file may read or naively chunk a .sql migration."""
        offenders = []
        for dirpath, _dirnames, filenames in os.walk(os.path.join(MCP_DIR, "src")):
            for name in filenames:
                if not name.endswith((".ts", ".js")) or name.endswith(".test.ts"):
                    continue
                path = os.path.join(dirpath, name)
                text = read(path)
                rel = os.path.relpath(path, ROOT)
                if re.search(r"\.sql", text):
                    offenders.append(f"{rel}: references a .sql file")
                if re.search(r"split\(\s*['\"];['\"]\s*\)", text):
                    offenders.append(f"{rel}: naive split(';') statement chunking")
        self.assertEqual(
            offenders,
            [],
            "assembly-mcp must not execute or chunk SQL. Found: " + "; ".join(offenders),
        )

    def test_assembly_client_documents_zero_pg_ownership(self):
        """Keep the boundary stated where a future maintainer will read it."""
        text = read(os.path.join(MCP_DIR, "src", "assembly-client.ts"))
        self.assertIn("assembly-srv", text)
        # The phrasing is wrapped across a JSDoc line, so assert the two
        # halves in order rather than matching a single line.
        zero_at = text.find("ZERO")
        self.assertNotEqual(zero_at, -1, "boundary statement lost its 'ZERO' claim")
        after = text[zero_at:]
        self.assertIn(
            "direct pg dependencies",
            after,
            "boundary statement must still assert zero direct pg dependencies",
        )


if __name__ == "__main__":
    unittest.main()
