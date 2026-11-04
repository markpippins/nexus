#!/usr/bin/env python3
"""Guard: every schema runtime SQL reads is provisioned by the CI bootstrap.

Why this exists
---------------
The Adonis control-edge suite failed `GET /api/links` against a fresh
ci-bootstrap database with a bare 500. The cause was not the suite and not the
route: `throttler` is simply not one of the schemas `refresh.sh` dumps, so
`throttler.links` does not exist. Nothing anywhere said "this database is
missing a schema" — the symptom was indistinguishable from a code defect, and
the chase went through the module loader and the TS config before anyone
thought to check whether the table was there at all.

Measuring the class rather than the instance found nine: absorb, jenkins,
knowledge, mcp, operator, sonar, steward, throttler, voyager were all read by
runtime SQL and all absent from the snapshot. `steward` was the sharp one —
`typescript/conduit-mcp/src/steward-recall.ts` queries `steward.evidence_links`
and `steward.semantic_search()`, and conduit-mcp is CI-tested code, so those
paths could not be exercised anywhere.

`refresh.sh` already fails when the DUMP BODY references a schema missing from
its list. That check cannot see this case: it inspects what pg_dump emitted, and
a schema nobody dumped contributes nothing to inspect. The reference lives in the
application, so the check has to live there too.

What this asserts
-----------------
1. **Application -> snapshot.** Every `schema.` reference in a SQL statement in
   runtime code resolves to a schema in `refresh.sh`'s `SCHEMAS()` (or to one of
   the two system schemas, `information_schema` / `pg_temp`, which no snapshot
   should ever create).
2. **List -> artifact.** Every schema in `SCHEMAS()` is actually declared in the
   committed snapshot's prelude. This is the half that catches "added to the
   list, forgot to regenerate": both surfaces agree, but the artifact predates
   the edit, so CI applies a snapshot missing the schema the list promises.
3. **Not vacuous.** The scan finds a plausible number of schemas. A guard that
   silently stops matching (a moved directory, a renamed pattern) would pass
   forever while checking nothing — the failure mode this repo keeps paying for.

Deliberately NOT asserted: which schemas are listed. The list is a design
decision; this guard only fails when the code and the list disagree.

Adding a schema therefore takes two edits (SCHEMAS() + the prelude) and one
regeneration. If a reference is not really a schema, add it to NON_APP_SCHEMAS
with the reason — that list is a ratchet, not a mute button.

Note on the universe: an earlier attempt keyed off `CREATE SCHEMA IF NOT EXISTS`
declarations. It would NOT have caught `throttler`, whose schema is created
outside this repo; only its *usage* is visible here. Usage is the signal.

Run:
  python3 -m pytest bin/tests/test_ci_snapshot_schema_coverage.py -v
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
REFRESH = REPO / "sql" / "ci-bootstrap" / "refresh.sh"
SNAPSHOT = REPO / "sql" / "ci-bootstrap" / "nexus-ci-bootstrap.sql"

# Directories holding runtime code. Docs/audit trees are excluded on purpose:
# prose mentions schemas in passing and would make this a spell-checker.
SOURCE_ROOTS = ("typescript", "python", "bin", "adonisjs", "angular")
SOURCE_SUFFIXES = (".ts", ".js", ".mjs", ".py", ".adonisjs", ".java")

# Never runtime code: build output, vendored deps, test files (tests may poke at
# fixtures this guard is not describing).
SKIP_PATH = re.compile(
    r"(node_modules|/dist/|/build/|/\.git/|__pycache__|/\.angular/)"
)
SKIP_FILE = re.compile(r"(\.test\.|_test\.|/tests/)")

# A schema-qualified reference introduces the schema name in one of these
# positions. `update x.` is included because UPDATE targets are tabular.
REFERENCE = re.compile(r"(?i)\b(?:from|join|into|update)\s+([a-z_][a-z0-9_]{2,})\.[a-z_]")
# ... but only when the line is actually a SQL statement. Without this the scan
# also matches prose in comments and plain object access (`this.index.foo`),
# which produced 37 "unknown schemas" that were all noise.
SQL_CONTEXT = re.compile(r"(?i)\b(select|insert\s+into|delete\s+from|update\s+[a-z_]|join)\b")
PYTHON_IMPORT = re.compile(r"^\s*(from\s+[\w.]+\s+import|import\s+[\w.]+)")
COMMENT_LINE = re.compile(r"^\s*(//|\*|#|--|/\*)")

# System schemas: real, schema-shaped, and never part of an application
# snapshot. Anything else that is not an application schema belongs in SCHEMAS()
# — or here, with a reason that survives review.
NON_APP_SCHEMAS = {
    "information_schema": "SQL-standard catalogue; exists in every database",
    "pg_temp": "per-session temporary namespace; exists on demand (e.g. "
               "bin/regenerate_memory_seed.py stages its comparison rows there)",
}

# Floor for the vacuity check. The measured universe at the time of writing is
# 27; the floor is deliberately below it so ordinary churn does not trip it,
# while a broken scan (0 or 2 matches) still fails.
MIN_EXPECTED_SCHEMAS = 20


def load_schemas() -> list[str]:
    """Parse SCHEMAS=(...) out of refresh.sh."""
    text = REFRESH.read_text(encoding="utf-8")
    match = re.search(r"^SCHEMAS=\(([^)]*)\)", text, re.MULTILINE)
    if not match:
        raise AssertionError(
            f"cannot find SCHEMAS=(...) in {REFRESH} — this guard is blind until "
            "the parse is fixed (a rename here would otherwise pass silently)"
        )
    return match.group(1).split()


def scan_references() -> dict[str, list[str]]:
    """Return {schema: ['path:line', ...]} for schema-qualified SQL in runtime code."""
    found: dict[str, list[str]] = {}
    for root in SOURCE_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in SOURCE_SUFFIXES:
                continue
            rel = "/" + str(path.relative_to(REPO)).replace("\\", "/")
            if SKIP_PATH.search(rel) or SKIP_FILE.search(rel):
                continue
            try:
                lines = path.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            is_python = path.suffix == ".py"
            for lineno, line in enumerate(lines, 1):
                if is_python and PYTHON_IMPORT.match(line):
                    continue  # `from mcp.server import ...` is a module, not SQL
                if COMMENT_LINE.match(line):
                    continue
                if not (SQL_CONTEXT.search(line) and REFERENCE.search(line)):
                    continue
                for match in REFERENCE.finditer(line):
                    found.setdefault(match.group(1), []).append(
                        f"{path.relative_to(REPO)}:{lineno}"
                    )
    return found


class CiSnapshotSchemaCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schemas = load_schemas()
        cls.references = scan_references()

    def test_scan_is_not_vacuous(self):
        # Cheap proof that the scan still sees the code it is meant to police.
        self.assertGreaterEqual(
            len(self.references),
            MIN_EXPECTED_SCHEMAS,
            f"scan found only {len(self.references)} schema(s) in runtime SQL; "
            "the reference scan has stopped matching (moved directories, changed "
            "pattern) and would pass while checking nothing",
        )
        self.assertTrue(self.schemas, "SCHEMAS() parsed empty")

    def test_every_referenced_schema_is_in_the_snapshot(self):
        listed = set(self.schemas)
        unresolved = {
            name: where
            for name, where in self.references.items()
            if name not in listed and name not in NON_APP_SCHEMAS
        }
        if not unresolved:
            return
        detail = "\n".join(
            f"  {name}: {', '.join(sorted(set(where))[:3])}"
            for name, where in sorted(unresolved.items())
        )
        self.fail(
            "runtime SQL references schema(s) the CI bootstrap does not create:\n"
            f"{detail}\n\n"
            "A test touching one of these fails with an opaque 500 that looks "
            "like a code defect — that is exactly how `throttler` was found.\n"
            "Fix: add each to SCHEMAS() AND the prelude CREATE SCHEMA list in "
            "sql/ci-bootstrap/refresh.sh, then regenerate the snapshot. If a "
            "reference is not really a schema, add it to NON_APP_SCHEMAS with "
            "the reason."
        )

    def test_snapshot_preamble_declares_every_schema(self):
        # List -> artifact. Guards the "edited the list, forgot to regenerate"
        # gap: both sides are internally consistent, but CI applies the stale dump.
        text = SNAPSHOT.read_text(encoding="utf-8", errors="ignore")
        missing = [
            name for name in self.schemas
            if f"CREATE SCHEMA IF NOT EXISTS {name};" not in text
        ]
        self.assertEqual(
            missing, [],
            "SCHEMAS() lists schema(s) the committed snapshot never declares: "
            f"{missing} — regenerate with sql/ci-bootstrap/refresh.sh",
        )


if __name__ == "__main__":
    unittest.main()
