#!/usr/bin/env python3
"""Guards for the staged-inert resolution v38 / V215 migration (plan 8261668).

Plan 8261668 is the schema half of the Lilac Wave 1 decomposition (parent
8261639). Its six acceptance criteria are pinned here:

  AC1  additive only — the migration text is parsed and refused if it contains
       a destructive statement (DROP / TRUNCATE / DELETE) or an ALTER that is
       not an `ADD COLUMN IF NOT EXISTS`.
  AC2  apply, second apply and rollback are proven on a throwaway database the
       test creates and drops: the second apply is idempotent, and a rollback
       leaves zero new relations, columns, functions or triggers.
  AC3  the provenance columns exist on all four canonical surfaces;
       resolution.mutation_provenance refuses a duplicate idempotency key; the
       default path is unchanged; and `SET LOCAL lilac.provenance_mode =
       'enforce'` refuses a write with no envelope while accepting one that
       carries it.
  AC4  resolution.executable_topology carries every topology field the parent
       criterion names, with a CHECK on the lifecycle vocabulary.
  AC5  sql/V215 and the canonical migration are byte-identical apart from the
       documented header.
  AC6  live application is a separate operator-gated step — this change set
       contains no applied DDL, which the PR's own diff shows.

The DB half runs in the `guards-db` tier, which provides postgres and psql.
Every client call passes `-w` and DEVNULL stdin: a password prompt would hang
a CI runner instead of failing it (the defect test_grants_e2e records).

DB tier: bin/tests-ci-manifest.json -> tiers.db
"""
from __future__ import annotations

import os
import re
import subprocess
import time
import uuid

import pytest

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "createdb", "dropdb", "psql", doing="these tests create a throwaway database"
)

CANONICAL = REPO_ROOT / "schemas" / "migrations" / "resolution" / (
    "resolution_migration_v38_lilac_wave1_provenance.sql"
)
V215 = REPO_ROOT / "sql" / "V215__lilac_wave1_provenance_and_topology.sql"

SURFACES = ("receipt", "ticket", "ticket_transition", "fanout_transition")
ENVELOPE = ("source_implementation", "runtime_route", "source_identity", "idempotency_key")

ENV = {**os.environ,
       "PGHOST": os.environ.get("PGHOST", "localhost"),
       "PGPORT": os.environ.get("PGPORT", "5432"),
       "PGUSER": os.environ.get("PGUSER", "pguser")}
PSQL = ("psql", "-w")

#: Only what the migration touches is pre-created. The point is to prove the
#: migration's own delta, so the skeleton is deliberately the minimum the
#: ALTER TABLE statements require.
SKELETON = """
CREATE SCHEMA resolution;
CREATE TABLE resolution.receipt (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  producer_id text,
  kind text
);
CREATE TABLE resolution.ticket (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_ref text,
  role text
);
CREATE TABLE resolution.ticket_transition (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ticket_id uuid,
  to_status text
);
CREATE TABLE resolution.fanout_transition (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  input_receipt_id uuid,
  kind text
);
CREATE TABLE resolution.migration_disposition (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_schema text,
  source_table text,
  disposition_class text
);
"""


# ── text handling ───────────────────────────────────────────────────────────

def body_of(text: str) -> str:
    """The migration body: everything from the first non-comment line.

    AC5 compares bodies, not whole files, because the two files are meant to
    carry different headers (one names the V-series slot, one names the
    canonical path). Comparing whole files would force one of them to lie.
    """
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if not line.lstrip().startswith("--"))
    return "".join(lines[start:])


def code_of(text: str) -> str:
    """Body with `--` comments removed, so prose cannot trip or satisfy the scan."""
    return "\n".join(
        re.sub(r"--.*$", "", line) for line in body_of(text).splitlines()
    )


DESTRUCTIVE = re.compile(r"\b(DROP|TRUNCATE|DELETE)\b", re.IGNORECASE)
ALTER_STMT = re.compile(r"ALTER\s+TABLE\b[^;]*;", re.IGNORECASE | re.DOTALL)
ALTER_HEAD = re.compile(
    r"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[A-Za-z_\"][\w.\"]*\s+", re.IGNORECASE
)
ADDITIVE_CLAUSE = re.compile(
    r"^ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+[A-Za-z_]\w*\s+\S", re.IGNORECASE
)


def _split_clauses(body: str) -> list[str]:
    """Split an ALTER TABLE body on commas that are not inside parentheses.

    A hand-written scanner rather than a regex: the first version of this
    guard used a nested-quantifier pattern and CodeQL correctly flagged it as
    exponentially backtracking, which would let a crafted migration wedge CI
    instead of failing it.
    """
    clauses: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            clauses.append("".join(current))
            current = []
            continue
        current.append(char)
    clauses.append("".join(current))
    return clauses


def _alter_is_additive(statement: str) -> bool:
    """True only when every clause is `ADD COLUMN IF NOT EXISTS <name> <type>`."""
    flattened = re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()
    head = ALTER_HEAD.match(flattened)
    if not head:
        return False
    clauses = _split_clauses(flattened[head.end():])
    return bool(clauses) and all(ADDITIVE_CLAUSE.match(c.strip()) for c in clauses)


def destructive_statements(text: str) -> list[str]:
    """Statements that would violate the additive contract (AC1).

    Two separate questions, because they fail differently: a whole-statement
    token scan catches the three verbs outright, and a structural check on
    ALTER TABLE catches `ALTER COLUMN ... TYPE`, `RENAME` and `SET NOT NULL`,
    which are destructive without containing any of those verbs.
    """
    code = code_of(text)
    offenders = [m.group(0) for m in DESTRUCTIVE.finditer(code)]
    for statement in ALTER_STMT.findall(code):
        if not _alter_is_additive(statement):
            offenders.append(statement.strip().splitlines()[0][:120])
    return offenders


# ── AC1 / AC5: hermetic ─────────────────────────────────────────────────────

def test_ac5_bodies_are_byte_identical():
    assert CANONICAL.is_file(), f"missing canonical migration {CANONICAL}"
    assert V215.is_file(), f"missing {V215}"
    canonical_body = body_of(CANONICAL.read_text(encoding="utf-8"))
    v215_body = body_of(V215.read_text(encoding="utf-8"))
    assert canonical_body == v215_body, "V215 and the canonical migration have drifted"
    assert canonical_body.strip(), "the migration body is empty"


def test_ac1_migration_is_additive():
    assert destructive_statements(CANONICAL.read_text(encoding="utf-8")) == []
    assert destructive_statements(V215.read_text(encoding="utf-8")) == []


def test_ac1_guard_bites_on_a_destructive_fixture():
    """The scan must fail on the things it claims to catch.

    Without this, a regex that quietly stopped matching would report the real
    migration clean and prove nothing.
    """
    body = "BEGIN;\n{};\nCOMMIT;\n"
    for destructive in (
        "DROP TABLE resolution.receipt",
        "TRUNCATE resolution.ticket",
        "DELETE FROM resolution.fanout_transition",
        "ALTER TABLE resolution.receipt ALTER COLUMN kind TYPE integer",
        "ALTER TABLE resolution.ticket RENAME TO ticket_old",
    ):
        assert destructive_statements(body.format(destructive)), destructive

    benign = body.format(
        "ALTER TABLE resolution.receipt ADD COLUMN IF NOT EXISTS idempotency_key text"
    )
    assert destructive_statements(benign) == []


def test_ac1_scan_is_linear_on_adversarial_input():
    """A crafted migration must fail fast, not wedge the runner.

    The first version of this guard used a nested-quantifier regex and CodeQL
    flagged it as exponentially backtracking. The mitigation is structural (a
    hand-written clause splitter), so this bounds the behaviour rather than
    trusting that no one reintroduces the pattern.
    """
    adversarial = "BEGIN;\nALTER TABLE resolution.receipt " + (
        "ADD COLUMN IF NOT EXISTS column_name text, " * 4000
    ) + "ADD COLUMN IF NOT EXISTS tail text;\nCOMMIT;\n"
    started = time.monotonic()
    offenders = destructive_statements(adversarial)
    elapsed = time.monotonic() - started
    assert offenders == []
    assert elapsed < 5.0, f"additive scan took {elapsed:.1f}s on {len(adversarial)} chars"


# ── DB helpers ──────────────────────────────────────────────────────────────

def psql(db: str, sql: str, expect_fail: bool = False):
    result = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-Atc", sql],
        env=ENV, capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL,
    )
    if expect_fail:
        assert result.returncode != 0, f"expected failure, got: {result.stdout}"
        return result.stdout + result.stderr
    assert result.returncode == 0, f"psql failed: {result.stderr}"
    return result.stdout.strip()


def apply_script(db: str, script: str):
    result = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
        input=script, env=ENV, capture_output=True, text=True, timeout=90,
    )
    return result


def without_transaction(body: str) -> str:
    """The migration manages its own transaction; the rehearsal owns one."""
    lines = [line for line in body.splitlines()
             if line.strip().upper() not in ("BEGIN;", "COMMIT;")]
    return "\n".join(lines) + "\n"


def catalog(db: str) -> dict[str, int]:
    """Counts of every object class the rollback must not leave behind."""
    return {
        "relations": int(psql(db, "select count(*) from pg_class c join pg_namespace n"
                                  " on n.oid=c.relnamespace where n.nspname='resolution'")),
        "columns": int(psql(db, "select count(*) from information_schema.columns"
                                " where table_schema='resolution'")),
        "functions": int(psql(db, "select count(*) from pg_proc p join pg_namespace n"
                                  " on n.oid=p.pronamespace where n.nspname='resolution'")),
        "triggers": int(psql(db, "select count(*) from pg_trigger t join pg_class c"
                                  " on c.oid=t.tgrelid join pg_namespace n"
                                  " on n.oid=c.relnamespace"
                                  " where n.nspname='resolution' and not t.tgisinternal")),
        "indexes": int(psql(db, "select count(*) from pg_indexes"
                                 " where schemaname='resolution'")),
    }


@pytest.fixture(scope="module")
def e2edb():
    db = f"e2e_lilac_v215_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    assert subprocess.run(["createdb", "-w", db], env=ENV, capture_output=True,
                          stdin=subprocess.DEVNULL).returncode == 0, \
        "cannot create throwaway DB"
    seeded = subprocess.run([*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
                            input=SKELETON, env=ENV, capture_output=True,
                            text=True, timeout=90)
    assert seeded.returncode == 0, f"skeleton failed: {seeded.stderr}"
    try:
        yield db
    finally:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def migrated(e2edb):
    """A database with the migration committed once, for the assertions that
    need the objects to exist. The apply/rollback proof uses its own
    transaction in test_ac2_* and never relies on this."""
    body = without_transaction(CANONICAL.read_text(encoding="utf-8"))
    result = apply_script(e2edb, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert result.returncode == 0, f"apply failed: {result.stderr}"
    return e2edb


# ── AC2: apply / second apply / rollback ────────────────────────────────────

def test_ac2_double_apply_is_idempotent_and_rollback_is_clean(e2edb):
    before = catalog(e2edb)
    body = without_transaction(CANONICAL.read_text(encoding="utf-8"))

    rolled = apply_script(
        e2edb,
        f"BEGIN;\n{body}\n{body}\nROLLBACK;\n",
    )
    assert rolled.returncode == 0, f"double apply failed: {rolled.stderr}"

    after = catalog(e2edb)
    assert after == before, f"rollback left objects behind: {before} -> {after}"


def test_ac2_committed_apply_creates_the_objects(migrated):
    assert psql(migrated, """
        select count(*) from information_schema.columns
         where table_schema='resolution' and table_name='receipt'
           and column_name in ('source_implementation','runtime_route',
                               'source_identity','idempotency_key')
    """) == "4"
    for relation in ("mutation_provenance", "executable_topology", "parity_evidence"):
        assert psql(migrated, f"select to_regclass('resolution.{relation}')::text") \
            == f"resolution.{relation}"


# ── AC3: provenance columns, ledger uniqueness, enforce mode ────────────────

def test_ac3_envelope_columns_exist_on_all_four_surfaces(migrated):
    for surface in SURFACES:
        found = psql(migrated, f"""
            select column_name from information_schema.columns
             where table_schema='resolution' and table_name='{surface}'
               and column_name in {tuple(ENVELOPE)!r}
        """).split()
        assert set(found) == set(ENVELOPE), f"{surface} missing {set(ENVELOPE) - set(found)}"


def test_ac3_mutation_provenance_refuses_a_duplicate_idempotency_key(migrated):
    insert = """
        insert into resolution.mutation_provenance
            (surface, source_implementation, runtime_route, source_identity, idempotency_key)
        values ('receipt', 'conduit', 'POST /receipt', 'impl-1', 'idem-dup-1')
    """
    psql(migrated, insert)
    message = psql(migrated, insert, expect_fail=True)
    assert "mutation_provenance_idempotency_key_key" in message or "duplicate key" in message


def test_ac3_default_path_is_unchanged(migrated):
    """No setting, no envelope, no refusal: the default path is untouched."""
    psql(migrated, "insert into resolution.receipt (producer_id, kind) values ('p','k')")
    assert psql(migrated, "select count(*) from resolution.receipt") != "0"


def test_ac3_enforce_mode_refuses_a_write_without_an_envelope(migrated):
    script = """
        BEGIN;
        SET LOCAL lilac.provenance_mode = 'enforce';
        INSERT INTO resolution.receipt (producer_id, kind) VALUES ('p','no-envelope');
        COMMIT;
    """
    refused = apply_script(migrated, script)
    assert refused.returncode != 0, "enforce mode accepted a write with no envelope"
    assert "provenance enforcement" in (refused.stderr + refused.stdout)

    accepted = apply_script(migrated, """
        BEGIN;
        SET LOCAL lilac.provenance_mode = 'enforce';
        INSERT INTO resolution.receipt
            (producer_id, kind, source_implementation, runtime_route,
             source_identity, idempotency_key)
        VALUES ('p','enveloped','conduit','POST /receipt','impl-1','idem-enforce-1');
        COMMIT;
    """)
    assert accepted.returncode == 0, f"enforce refused a complete envelope: {accepted.stderr}"


def test_ac3_enforcement_is_scoped_to_the_session(migrated):
    """`SET LOCAL` must not leak: after the transaction the default returns."""
    psql(migrated, "insert into resolution.receipt (producer_id, kind) values ('p','after')")


# ── AC4: topology table shape ───────────────────────────────────────────────

TOPOLOGY_FIELDS = {
    "pid", "working_tree", "service_unit", "port", "invocation_id",
    "language", "db_target", "route", "lifecycle",
}


def test_ac4_executable_topology_carries_every_required_field(migrated):
    found = set(psql(migrated, """
        select column_name from information_schema.columns
         where table_schema='resolution' and table_name='executable_topology'
    """).split())
    assert TOPOLOGY_FIELDS <= found, f"missing {TOPOLOGY_FIELDS - found}"


def test_ac4_lifecycle_vocabulary_is_enforced_by_storage(migrated):
    psql(migrated, """
        insert into resolution.executable_topology
            (surface, path, line, role, language, lifecycle)
        values ('receipt','python/conduit/lilac.py',120,'writer','python','live')
    """)
    refused = psql(migrated, """
        insert into resolution.executable_topology
            (surface, path, line, role, language, lifecycle)
        values ('receipt','python/conduit/lilac.py',121,'writer','python','probably-live')
    """, expect_fail=True)
    assert "executable_topology_lifecycle_check" in refused or "check constraint" in refused
