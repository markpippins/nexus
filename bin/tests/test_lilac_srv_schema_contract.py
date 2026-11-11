#!/usr/bin/env python3
"""Bind lilac-srv's SQL to the real resolution schema (plan 8261669).

The TypeScript suite drives the repository through an in-memory stand-in, which
proves the CLASSIFICATION logic and nothing about the SQL. This guard closes
that gap: it clones the production resolution shape into a throwaway database,
applies the staged V215 migration on top, and then asks PostgreSQL itself four
questions about the statements the repository actually issues.

  1. Every column named in an INSERT/UPDATE exists (a rename in the schema or a
     typo in the SQL fails here, not in production).
  2. Every statement PARSES and its parameters type-infer against those
     columns — `PREPARE` is the parser's answer, not ours.
  3. The natural unique indexes the idempotency contract depends on exist.
  4. Inserting the same receipt twice yields a row once, and the second attempt
     inserts nothing — the real index, not a test double, classifies the replay.

Why the clone rather than a hand-written skeleton: a skeleton proves the SQL
agrees with the test author. Cloning production proves it agrees with
production, which is the only claim worth making.

DB tier: bin/tests-ci-manifest.json -> tiers.db

topology-inventory: ignore-file — the INSERT below writes fixture data into a
throwaway database, not into a canonical surface.
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from host_tool_guard import skip_unless_host_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_TS = REPO_ROOT / "typescript" / "lilac-srv" / "src" / "repository.ts"
V215 = REPO_ROOT / "sql" / "V215__lilac_wave1_provenance_and_topology.sql"

SURFACES = ("receipt", "ticket", "ticket_transition", "fanout_transition")

pytestmark = skip_unless_host_tools(
    "createdb",
    "dropdb",
    "psql",
    "pg_dump",
    doing="this guard clones the resolution schema into a throwaway database",
)

DSN = (
    os.environ.get("CONDUIT_PG_DSN")
    or os.environ.get("NEXUS_PG_DSN")
    or "postgresql://pguser:pgpass@localhost:5432/nexus"
)

ENV = {
    **os.environ,
    "PGHOST": os.environ.get("PGHOST", "localhost"),
    "PGPORT": os.environ.get("PGPORT", "5432"),
    "PGUSER": os.environ.get("PGUSER", "pguser"),
}
PSQL = ("psql", "-w", "-v", "ON_ERROR_STOP=1")


def psql(db: str, sql: str) -> str:
    result = subprocess.run(
        [*PSQL, "-d", db, "-X", "-A", "-t", "-c", sql],
        capture_output=True,
        text=True,
        env=ENV,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"psql failed: {result.stderr.strip()}\n--- sql ---\n{sql}")
    return result.stdout.strip()


def psql_file(db: str, path: Path) -> None:
    result = subprocess.run(
        [*PSQL, "-d", db, "-f", str(path)],
        capture_output=True,
        text=True,
        env=ENV,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if result.returncode != 0:
        raise AssertionError(f"applying {path.name} failed: {result.stderr.strip()}")


# ── statement extraction ────────────────────────────────────────────────────
def repository_statements() -> list[str]:
    """Every backticked statement in repository.ts that names resolution.*."""
    text = REPOSITORY_TS.read_text(encoding="utf-8")
    statements: list[str] = []
    for body in re.findall(r"`([^`]*)`", text, flags=re.S):
        candidate = body.strip()
        if "resolution." not in candidate:
            continue
        if not re.match(r"^(INSERT|SELECT|UPDATE)\b", candidate, flags=re.I):
            continue
        statements.append(candidate)
    return statements


INSERT_COLUMNS = re.compile(
    r"INSERT\s+INTO\s+resolution\.(\w+)\s*\(([^)]*)\)", flags=re.I | re.S
)
UPDATE_COLUMNS = re.compile(
    r"UPDATE\s+resolution\.(\w+)\s+SET\s+([a-zA-Z_][\w]*)\s*=", flags=re.I | re.S
)


def referenced_columns() -> dict[str, set[str]]:
    """table → column names the repository writes."""
    text = REPOSITORY_TS.read_text(encoding="utf-8")
    found: dict[str, set[str]] = {}
    for table, column_blob in INSERT_COLUMNS.findall(text):
        columns = {
            col.strip()
            for col in column_blob.replace("\n", " ").split(",")
            if col.strip() and not col.strip().startswith("$")
        }
        found.setdefault(table, set()).update(columns)
    for table, column in UPDATE_COLUMNS.findall(text):
        found.setdefault(table, set()).add(column)
    return found


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def schema_db() -> str:
    """A throwaway database holding production resolution + V215."""
    base = DSN.rsplit("/", 1)[-1].split("?")[0]
    name = f"lilac_srv_contract_{uuid.uuid4().hex[:8]}"
    created = subprocess.run(
        ["createdb", "-w", name],
        capture_output=True,
        text=True,
        env=ENV,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    if created.returncode != 0:
        pytest.skip(f"cannot create throwaway database: {created.stderr.strip()}")

    try:
        # Clone every schema, not just resolution: resolution.* carries
        # foreign keys into sibling schemas (peb among them), so a
        # resolution-only dump cannot be replayed — pg_dump emits an ALTER
        # TABLE ... REFERENCES that fails because its target is absent.
        dump = subprocess.run(
            [
                "pg_dump",
                "-d", base,
                "--schema-only",
                "--no-owner",
                "--no-privileges",
            ],
            capture_output=True,
            text=True,
            env=ENV,
            stdin=subprocess.DEVNULL,
            timeout=120,
        )
        if dump.returncode != 0 or "CREATE TABLE" not in dump.stdout:
            pytest.skip(f"cannot clone the resolution schema from {base}: {dump.stderr.strip()[:200]}")

        loaded = subprocess.run(
            [*PSQL, "-d", name],
            input=dump.stdout,
            capture_output=True,
            text=True,
            env=ENV,
            timeout=120,
        )
        if loaded.returncode != 0:
            pytest.skip(f"cannot load the cloned schema: {loaded.stderr.strip()[:200]}")
        psql_file(name, V215)
        yield name
    finally:
        subprocess.run(
            ["dropdb", "-w", "--if-exists", name],
            capture_output=True,
            text=True,
            env=ENV,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )


# ── AC: the SQL agrees with the schema ─────────────────────────────────────
def test_repository_names_at_least_the_four_mutation_statements() -> None:
    statements = repository_statements()
    inserts = [s for s in statements if s.upper().startswith("INSERT")]
    assert len(inserts) >= 4, f"expected an INSERT per surface, found {len(inserts)}"


def test_every_written_column_exists_after_v215(schema_db: str) -> None:
    columns = referenced_columns()
    missing: list[str] = []
    for table, names in columns.items():
        present = {
            row
            for row in psql(
                schema_db,
                "select column_name from information_schema.columns "
                f"where table_schema='resolution' and table_name='{table}'",
            ).splitlines()
            if row
        }
        assert present, f"resolution.{table} does not exist after V215"
        missing.extend(f"{table}.{name}" for name in sorted(names - present))
    assert missing == [], f"repository writes columns the schema does not have: {missing}"


def test_every_statement_prepares_against_the_schema(schema_db: str) -> None:
    """PREPARE is PostgreSQL's own verdict on syntax, columns and arity."""
    failures: list[str] = []
    statements = repository_statements()
    assert statements, "no statements extracted — the guard would be vacuous"
    for index, statement in enumerate(statements):
        name = f"lilac_contract_{index}"
        try:
            psql(schema_db, f"PREPARE {name} AS {statement}")
        except AssertionError as exc:
            failures.append(f"statement {index}: {exc}")
        finally:
            psql(schema_db, f"DEALLOCATE {name}") if False else None
    assert failures == [], "\n\n".join(failures)


def test_the_natural_unique_indexes_exist(schema_db: str) -> None:
    expected = {
        "uq_resolution_receipt_idem",
        "uq_resolution_ticket_idem",
        "uq_resolution_ticket_transition_idem",
        "uq_resolution_fanout_idem",
    }
    present = set(
        psql(
            schema_db,
            "select indexname from pg_indexes where schemaname='resolution'",
        ).splitlines()
    )
    assert expected - present == set(), (
        "the idempotency contract relies on indexes that are absent: "
        f"{sorted(expected - present)}"
    )


def test_mutation_provenance_key_is_unique(schema_db: str) -> None:
    rows = psql(
        schema_db,
        "select indexdef from pg_indexes "
        "where schemaname='resolution' and tablename='mutation_provenance'",
    )
    assert "UNIQUE" in rows.upper() and "IDEMPOTENCY_KEY" in rows.upper(), rows


def test_duplicate_receipt_insert_is_a_no_op(schema_db: str) -> None:
    """The real index classifies the replay: the second insert returns no row.

    PREPARE is session-scoped, so all three statements run in ONE psql session.
    Splitting them across invocations was the first version of this test and it
    failed with "prepared statement does not exist" — the kind of failure that
    looks like a schema problem and is actually a harness mistake.
    """
    statement = next(
        s for s in repository_statements()
        if s.upper().startswith("INSERT INTO RESOLUTION.RECEIPT")
    )
    values = (
        "'conduit-mcp', 'implementation', 'conduit', 'src-1', "
        "'fp-1', '{}'::jsonb, '{}'::jsonb, 1, "
        "'typescript/lilac-srv/src/repository.ts', 'POST /v1/receipts', "
        "'agent-record:test', 'idem-test-1'"
    )
    # The cloned schema is empty, so the producer registry starts empty too —
    # and the grant trigger refuses an unregistered producer (a good sign: the
    # enforcement this service relies on is real). Seed the registered producer
    # the canonical writer actually is, rather than inventing a new identity.
    script = (
        "INSERT INTO resolution.producer_registry "
        "(producer_id, name, allowed_kinds, contract_version_min, contract_version_max, "
        " state, registered_by) "
        "VALUES ('conduit-mcp', 'contract guard', ARRAY['implementation'], 1, 1, "
        " 'active', 'bin/tests/test_lilac_srv_schema_contract.py');\n"
        f"PREPARE lilac_receipt AS {statement};\n"
        f"EXECUTE lilac_receipt({values});\n"
        f"EXECUTE lilac_receipt({values});\n"
        "SELECT count(*) FROM resolution.receipt;\n"
    )
    result = subprocess.run(
        [*PSQL, "-d", schema_db, "-X", "-A", "-t"],
        input=script,
        capture_output=True,
        text=True,
        env=ENV,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    # psql reports a command tag per statement; the duplicate's tag is the
    # assertion. "INSERT 0 0" means ON CONFLICT DO NOTHING fired and the real
    # unique index declined the replay.
    assert "INSERT 0 0" in lines, f"the duplicate inserted a row: {lines}"
    assert lines.count("INSERT 0 1") == 2, f"expected the seed plus one insert: {lines}"
    assert lines[-1] == "1", f"the table holds more than one receipt: {lines}"
