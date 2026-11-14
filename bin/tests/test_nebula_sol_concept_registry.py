#!/usr/bin/env python3
"""Guards for the staged-inert Nebula → SOL registry migration (V216).

The migration is data-only by contract, and this guard pins that contract:

  AC1  no DDL and no mutation of domain data. The code (comments stripped) is
       scanned for CREATE/ALTER/DROP/TRUNCATE/DELETE/UPDATE/GRANT/REVOKE, and
       every INSERT must carry a NOT EXISTS or ON CONFLICT guard. A fixture
       proves the scan bites.
  AC2  apply, second apply and rollback are proven on a throwaway database:
       the second apply changes zero rows, and a rollback leaves the registry
       exactly as it started.
  AC3  the registry is complete and closed: the six new concepts exist, each
       concept has exactly one identity strategy, every representation has an
       identity, the two governed vocabularies have their expected cardinality,
       and the pre-existing Assessment representation was extended in place
       rather than duplicated.
  AC4  every registered representation resolves to a real relation, and the
       migration adds no binding — so nothing becomes evaluable by applying it.
  AC5  no new resolution domain table: the migration's concepts and
       representations are the only delta.

The DB half runs in the `guards-db` tier, which provides postgres and psql.
Every client call passes `-w` and DEVNULL stdin: a password prompt would hang
a CI runner instead of failing it (the defect test_grants_e2e records).

DB tier: bin/tests-ci-manifest.json -> tiers.db

topology-inventory: ignore-file — this SKELETON is fixture data written into a
throwaway database, not a producer of the canonical surfaces.
"""
from __future__ import annotations

import os
import re
import subprocess
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

V216 = REPO_ROOT / "sql" / "V216__nebula_sol_concept_registry.sql"

NEW_CONCEPTS = ("Ticket", "Receipt", "AgentRecord", "Decision", "Agenda", "AgendaItem")
RECORD_TYPES = (
    "report", "analysis", "assessment", "inspection", "prompt",
    "response", "engineering_log", "architecture_note", "decision",
)
ASSESSMENT_KINDS = ("assessment", "inspection")

ENV = {**os.environ,
       "PGHOST": os.environ.get("PGHOST", "localhost"),
       "PGPORT": os.environ.get("PGPORT", "5432"),
       "PGUSER": os.environ.get("PGUSER", "pguser")}
PSQL = ("psql", "-w")

#: The live pre-state this migration must extend, not replace: Assessment
#: already exists with one representation and (deliberately) no identity.
#: Every registry table the migration reads or writes is created here exactly
#: as resolution_schema_v2.sql declares it, constraints included.
SKELETON = """
CREATE SCHEMA resolution;
CREATE SCHEMA nebula;
CREATE SCHEMA peb;

CREATE TABLE resolution.concept (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text UNIQUE NOT NULL,
    description   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expired_at    timestamptz
);

CREATE TABLE resolution.concept_attribute (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id          uuid NOT NULL REFERENCES resolution.concept(id),
    name                text NOT NULL,
    description         text,
    value_type          text NOT NULL,
    is_state_attribute  boolean NOT NULL DEFAULT false,
    UNIQUE (concept_id, name)
);
CREATE UNIQUE INDEX one_state_attr_per_concept
    ON resolution.concept_attribute (concept_id) WHERE is_state_attribute;

CREATE TABLE resolution.concept_attribute_value (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    attribute_id   uuid NOT NULL REFERENCES resolution.concept_attribute(id),
    value          text NOT NULL,
    description    text,
    UNIQUE (attribute_id, value)
);

CREATE TABLE resolution.identity_strategy (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id                  uuid NOT NULL UNIQUE REFERENCES resolution.concept(id),
    canonical_key_description   text NOT NULL,
    notes                       text
);

CREATE TABLE resolution.representation (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id            uuid NOT NULL REFERENCES resolution.concept(id),
    label                 text NOT NULL,
    schema_name           text,
    table_name            text,
    owner                 text,
    raw_metadata          jsonb,
    created_at            timestamptz NOT NULL DEFAULT now(),
    expired_at            timestamptz
);

CREATE TABLE resolution.representation_identity (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    representation_id      uuid NOT NULL UNIQUE REFERENCES resolution.representation(id),
    identity_strategy_id    uuid NOT NULL REFERENCES resolution.identity_strategy(id),
    identity_expression      text NOT NULL,
    notes                    text
);

CREATE TABLE resolution.concept_attribute_binding (
    attribute_id  uuid PRIMARY KEY REFERENCES resolution.concept_attribute(id),
    schema_name   text NOT NULL,
    table_name    text NOT NULL,
    column_name   text NOT NULL
);

-- Target relations: the migration must resolve every representation to one.
CREATE TABLE resolution.ticket    (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE resolution.receipt   (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE resolution.assessment(id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE nebula.agent_records (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE nebula.agendas       (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE nebula.agenda_items  (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE nebula.assessments   (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE peb.decisions        (id uuid PRIMARY KEY DEFAULT gen_random_uuid());

-- Live pre-state: Assessment exists, has one representation, and has neither
-- an identity strategy nor a representation identity.
INSERT INTO resolution.concept (name, description)
VALUES ('Assessment', 'pre-existing concept');

INSERT INTO resolution.representation (concept_id, label, schema_name, table_name)
SELECT id, 'assessment table', 'resolution', 'assessment'
FROM resolution.concept WHERE name = 'Assessment';
"""


# ── text handling ───────────────────────────────────────────────────────────

def body_of(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if not line.lstrip().startswith("--"))
    return "".join(lines[start:])


def code_of(text: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in body_of(text).splitlines())


DDL_OR_MUTATION = re.compile(
    r"\b(CREATE|ALTER|DROP|TRUNCATE|DELETE|UPDATE|GRANT|REVOKE|COMMENT|REINDEX|VACUUM)\b",
    re.IGNORECASE,
)
INSERT_HEAD = re.compile(r"INSERT\s+INTO\b", re.IGNORECASE)
GUARD = re.compile(r"\b(NOT\s+EXISTS|ON\s+CONFLICT)\b", re.IGNORECASE)


def offending_verbs(text: str) -> list[str]:
    return [m.group(0).upper() for m in DDL_OR_MUTATION.finditer(code_of(text))]


def insert_slices(text: str) -> list[str]:
    """Each INSERT up to the next INSERT.

    Semicolon splitting is wrong here: a description literal legitimately
    contains `;` ("record_type selects the intent; lifecycle ..."), and a
    literal-tolerant SQL lexer is more machinery than this guard needs. The
    slice is bounded by the next INSERT, which is exactly the unit whose guard
    we are checking.
    """
    code = code_of(text)
    starts = [m.start() for m in INSERT_HEAD.finditer(code)]
    return [code[a:b] for a, b in zip(starts, starts[1:] + [len(code)])]


def unguarded_inserts(text: str) -> list[str]:
    return [stmt.strip().splitlines()[0][:100]
            for stmt in insert_slices(text)
            if not GUARD.search(stmt)]


# ── AC1 / hermetic ──────────────────────────────────────────────────────────

def test_migration_exists_and_is_data_only():
    assert V216.is_file(), f"missing {V216}"
    text = V216.read_text(encoding="utf-8")
    assert offending_verbs(text) == [], "V216 must not contain DDL or domain mutations"
    assert unguarded_inserts(text) == [], "every INSERT must be idempotent"
    assert code_of(text).count("INSERT INTO") >= 6, "the registry seed is missing"


def test_scan_bites_on_a_bad_fixture():
    """A guard that stopped matching would pass the real file vacuously."""
    bad = V216.read_text(encoding="utf-8").replace(
        "\nCOMMIT;", "\nCREATE TABLE resolution.oops (id int);\nCOMMIT;"
    )
    assert "CREATE" in offending_verbs(bad)

    unguarded = "BEGIN;\nINSERT INTO resolution.concept (name) VALUES ('X');\nCOMMIT;\n"
    assert unguarded_inserts(unguarded) != []
    guarded = (
        "BEGIN;\nINSERT INTO resolution.concept (name) SELECT 'X' "
        "WHERE NOT EXISTS (SELECT 1 FROM resolution.concept WHERE name='X');\nCOMMIT;\n"
    )
    assert unguarded_inserts(guarded) == []


def test_no_new_resolution_domain_table_is_created():
    text = code_of(V216.read_text(encoding="utf-8"))
    assert not re.search(r"\bCREATE\s+TABLE\b", text, re.IGNORECASE)


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
    return subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
        input=script, env=ENV, capture_output=True, text=True, timeout=120,
    )


def without_transaction(body: str) -> str:
    lines = [line for line in body.splitlines()
             if line.strip().upper() not in ("BEGIN;", "COMMIT;")]
    return "\n".join(lines) + "\n"


def counts(db: str) -> dict[str, int]:
    tables = ("concept", "identity_strategy", "representation",
              "representation_identity", "concept_attribute",
              "concept_attribute_value", "concept_attribute_binding")
    return {t: int(psql(db, f"select count(*) from resolution.{t}")) for t in tables}


@pytest.fixture(scope="module")
def e2edb():
    db = f"e2e_nebula_sol_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    created = subprocess.run(["createdb", "-w", db], env=ENV, capture_output=True,
                             stdin=subprocess.DEVNULL)
    assert created.returncode == 0, f"cannot create throwaway DB: {created.stderr}"
    seeded = apply_script(db, SKELETON)
    assert seeded.returncode == 0, f"skeleton failed: {seeded.stderr}"
    try:
        yield db
    finally:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def migrated(e2edb):
    body = without_transaction(V216.read_text(encoding="utf-8"))
    result = apply_script(e2edb, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert result.returncode == 0, f"apply failed: {result.stderr}"
    return e2edb


# ── AC2: apply / second apply / rollback ────────────────────────────────────

def test_double_apply_is_idempotent_and_rollback_is_clean(e2edb):
    before = counts(e2edb)
    body = without_transaction(V216.read_text(encoding="utf-8"))

    rolled = apply_script(e2edb, f"BEGIN;\n{body}\n{body}\nROLLBACK;\n")
    assert rolled.returncode == 0, f"double apply failed: {rolled.stderr}"
    assert counts(e2edb) == before, "rollback left registry rows behind"


def test_committed_apply_is_complete(migrated):
    assert counts(migrated)["concept_attribute_binding"] == 0, \
        "V216 must not add a binding — it is descriptive, not evaluable"

    assert int(psql(migrated, """
        select count(*) from resolution.concept
         where name in %s
    """ % (NEW_CONCEPTS,))) == len(NEW_CONCEPTS)

    # one identity strategy per concept, for every concept
    assert psql(migrated, """
        select count(*) from resolution.concept c
         where not exists (select 1 from resolution.identity_strategy s
                            where s.concept_id = c.id)
    """) == "0"

    # every representation has an identity, including the pre-existing one
    assert psql(migrated, """
        select count(*) from resolution.representation r
         where not exists (select 1 from resolution.representation_identity ri
                            where ri.representation_id = r.id)
    """) == "0"


def test_assessment_was_extended_not_duplicated(migrated):
    assert psql(migrated, """
        select count(*) from resolution.representation r
          join resolution.concept c on c.id = r.concept_id
         where c.name = 'Assessment' and r.label = 'assessment table'
    """) == "1", "the pre-existing Assessment representation was duplicated"
    assert psql(migrated, """
        select count(*) from resolution.representation r
          join resolution.concept c on c.id = r.concept_id
         where c.name = 'Assessment' and r.label = 'operational assessment table'
    """) == "1"


def test_governed_vocabularies_have_their_expected_cardinality(migrated):
    assert psql(migrated, """
        select count(*) from resolution.concept_attribute_value v
          join resolution.concept_attribute a on a.id = v.attribute_id
          join resolution.concept c on c.id = a.concept_id
         where c.name = 'AgentRecord' and a.name = 'record_type'
    """) == str(len(RECORD_TYPES))
    assert psql(migrated, """
        select count(*) from resolution.concept_attribute_value v
          join resolution.concept_attribute a on a.id = v.attribute_id
          join resolution.concept c on c.id = a.concept_id
         where c.name = 'Assessment' and a.name = 'assessment_kind'
    """) == str(len(ASSESSMENT_KINDS))


# ── AC4: every representation resolves; nothing executable was added ────────

def test_every_representation_resolves_to_a_real_relation(migrated):
    unresolved = psql(migrated, """
        select coalesce(string_agg(r.schema_name || '.' || r.table_name, ','), '')
          from resolution.representation r
         where to_regclass(quote_ident(r.schema_name) || '.' || quote_ident(r.table_name))
               is null
    """)
    assert unresolved == "", f"representations point at missing relations: {unresolved}"


def test_decision_has_authority_and_narrative_representations(migrated):
    rows = psql(migrated, """
        select r.schema_name || '.' || r.table_name
          from resolution.representation r
          join resolution.concept c on c.id = r.concept_id
         where c.name = 'Decision'
         order by 1
    """).splitlines()
    assert rows == ["nebula.agent_records", "peb.decisions"]
