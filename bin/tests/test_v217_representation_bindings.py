#!/usr/bin/env python3
"""Guards for the staged-inert V217 representation-scoped binding migration.

Ratified by DBA 93d3f092 and Architect 9b532211. V217 is NOT applied live;
this guard proves it on a throwaway database only.

What is pinned:

  AC1  staged/idempotent — apply twice, then roll back, and the registry
       returns to its pre-apply shape.
  AC2  deterministic backfill — a binding is scoped only when its
       (schema, table) matches exactly one representation of its concept; a
       concept with two matching representations is NOT arbitrated.
  AC3  executable marking — a representation becomes executable only when a
       binding was scoped to it; nothing is promoted by default.
  AC4  equivalence — one Assessment concept with TWO representations
       (nebula.assessments and resolution.assessment) evaluates identically
       through resolution.compile_root when the representation is selected.
  AC5  fail closed — a representation without a binding raises, and two
       executable representations with no selection raise.
  AC6  no regression — the live binding topology (Answer, ImplementationPlan,
       Proposition, Requirement, WorkRequest, and the ..asset_id relationship
       bridge) still resolves after the migration.
  AC7  allow-list — a new expression.function_call naming an unregistered
       function is refused; applying over an existing unregistered call is
       refused by the preflight.

DB tier: bin/tests-ci-manifest.json -> tiers.db
"""
from __future__ import annotations

import os
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

V217 = REPO_ROOT / "sql" / "V217__representation_scoped_bindings.sql"

ENV = {**os.environ,
       "PGHOST": os.environ.get("PGHOST", "localhost"),
       "PGPORT": os.environ.get("PGPORT", "5432"),
       "PGUSER": os.environ.get("PGUSER", "pguser")}
PSQL = ("psql", "-w", "-q")

#: Pre-V217 shape, seeded with the live topology: concepts, their
#: representations, and the seven attribute + six relationship bindings.
SKELETON = """
CREATE SCHEMA resolution;
CREATE SCHEMA nebula;
CREATE SCHEMA conduit;
CREATE SCHEMA vision;

CREATE TABLE resolution.concept (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text UNIQUE NOT NULL,
    description text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expired_at timestamptz
);

CREATE TABLE resolution.concept_attribute (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id uuid NOT NULL REFERENCES resolution.concept(id),
    name text NOT NULL,
    description text,
    value_type text NOT NULL,
    is_state_attribute boolean NOT NULL DEFAULT false,
    UNIQUE (concept_id, name)
);

CREATE TABLE resolution.concept_attribute_binding (
    attribute_id uuid PRIMARY KEY REFERENCES resolution.concept_attribute(id),
    schema_name text NOT NULL,
    table_name text NOT NULL,
    column_name text NOT NULL
);

CREATE TABLE resolution.concept_relationship (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_concept_id uuid NOT NULL REFERENCES resolution.concept(id),
    to_concept_id uuid NOT NULL REFERENCES resolution.concept(id),
    relationship_type text NOT NULL,
    path text,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    expired_at timestamptz
);

CREATE TABLE resolution.concept_relationship_binding (
    concept_relationship_id uuid PRIMARY KEY
        REFERENCES resolution.concept_relationship(id),
    from_schema text,
    from_table text,
    from_column text,
    to_schema text,
    to_table text,
    to_column text,
    notes text
);

CREATE TABLE resolution.representation (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    concept_id uuid NOT NULL REFERENCES resolution.concept(id),
    label text NOT NULL,
    schema_name text,
    table_name text,
    owner text,
    raw_metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    expired_at timestamptz
);

CREATE TABLE resolution.representation_identity (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    representation_id uuid NOT NULL UNIQUE REFERENCES resolution.representation(id),
    identity_strategy_id uuid NOT NULL,
    identity_expression text NOT NULL,
    notes text
);

CREATE TABLE resolution.function_binding (
    function_name text PRIMARY KEY,
    sql_template text NOT NULL,
    arg_count integer NOT NULL,
    return_type text NOT NULL,
    notes text
);
INSERT INTO resolution.function_binding (function_name, sql_template, arg_count, return_type)
VALUES ('lower', 'lower(%s)', 1, 'text'), ('concat', '(%s || %s)', 2, 'text');

CREATE TABLE resolution.expression (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind text NOT NULL,
    operator text,
    literal_value text,
    attribute_id uuid REFERENCES resolution.concept_attribute(id),
    function_name text,
    return_type text NOT NULL,
    concept_relationship_id uuid REFERENCES resolution.concept_relationship(id),
    quantifier text,
    label text
);

CREATE TABLE resolution.expression_operand (
    parent_expression_id uuid NOT NULL REFERENCES resolution.expression(id),
    child_expression_id uuid NOT NULL REFERENCES resolution.expression(id),
    position integer NOT NULL,
    PRIMARY KEY (parent_expression_id, position)
);

-- Target relations the bindings name.
CREATE TABLE resolution.open_question_answer (id uuid PRIMARY KEY, confidence text, role text);
CREATE TABLE resolution.requirement (id uuid PRIMARY KEY, compilation_status text, parent_id uuid);
CREATE TABLE resolution.implementation_plan (id uuid PRIMARY KEY, status text);
CREATE TABLE resolution.work_request (id uuid PRIMARY KEY, business_status text);
CREATE TABLE resolution.proposition (id uuid PRIMARY KEY, disposition_value_id uuid, grounding_status_value_id uuid);
CREATE TABLE resolution.verified_statement (id uuid PRIMARY KEY, answer_id uuid);
CREATE TABLE resolution.open_question (id uuid PRIMARY KEY);
CREATE TABLE resolution.work_request_edge (id uuid PRIMARY KEY, parent_work_request_id uuid, child_work_request_id uuid);
CREATE TABLE resolution.assessment (id uuid PRIMARY KEY, outcome text);
CREATE TABLE nebula.assessments (id uuid PRIMARY KEY, outcome text);
CREATE TABLE conduit.wrp_dag_nodes (id uuid PRIMARY KEY);
CREATE TABLE vision.work_requests (id uuid PRIMARY KEY);

-- ── Live topology seed ─────────────────────────────────────────────────────
INSERT INTO resolution.concept (name) VALUES
  ('Answer'), ('Requirement'), ('ImplementationPlan'), ('WorkRequest'),
  ('Proposition'), ('WorkRequestEdge'), ('ShrapnelFact'), ('PromotionCandidate'),
  ('Assessment'), ('OpenQuestion'), ('VerifiedStatement');

INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'open_question_answer table', 'resolution', 'open_question_answer', 'resolution'
FROM resolution.concept WHERE name = 'Answer';
INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'requirement table', 'resolution', 'requirement', 'resolution'
FROM resolution.concept WHERE name = 'Requirement';
INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'implementation_plan table', 'resolution', 'implementation_plan', 'resolution'
FROM resolution.concept WHERE name = 'ImplementationPlan';
INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'WRP DAG node', 'conduit', 'wrp_dag_nodes', 'conduit'
FROM resolution.concept WHERE name = 'ImplementationPlan';
INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'work_request table', 'resolution', 'work_request', 'resolution'
FROM resolution.concept WHERE name = 'WorkRequest';
INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'vision.work_requests (LOSM satellite)', 'vision', 'work_requests', 'vision'
FROM resolution.concept WHERE name = 'WorkRequest';
INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'work_request_edge table', 'resolution', 'work_request_edge', 'resolution'
FROM resolution.concept WHERE name = 'WorkRequestEdge';
INSERT INTO resolution.representation (concept_id, label, schema_name, table_name, owner)
SELECT id, 'assessment table', 'resolution', 'assessment', 'resolution'
FROM resolution.concept WHERE name = 'Assessment';

INSERT INTO resolution.concept_attribute (concept_id, name, value_type)
SELECT id, 'confidence', 'enum' FROM resolution.concept WHERE name = 'Answer';
INSERT INTO resolution.concept_attribute (concept_id, name, value_type)
SELECT id, 'role', 'text' FROM resolution.concept WHERE name = 'Answer';
INSERT INTO resolution.concept_attribute (concept_id, name, value_type)
SELECT id, 'compilation_status', 'text' FROM resolution.concept WHERE name = 'Requirement';
INSERT INTO resolution.concept_attribute (concept_id, name, value_type)
SELECT id, 'status', 'text' FROM resolution.concept WHERE name = 'ImplementationPlan';
INSERT INTO resolution.concept_attribute (concept_id, name, value_type)
SELECT id, 'business_status', 'text' FROM resolution.concept WHERE name = 'WorkRequest';
INSERT INTO resolution.concept_attribute (concept_id, name, value_type)
SELECT id, 'disposition', 'text' FROM resolution.concept WHERE name = 'Proposition';
INSERT INTO resolution.concept_attribute (concept_id, name, value_type)
SELECT id, 'grounding_status', 'text' FROM resolution.concept WHERE name = 'Proposition';

INSERT INTO resolution.concept_attribute_binding (attribute_id, schema_name, table_name, column_name)
SELECT a.id, 'resolution', 'open_question_answer', 'confidence'
FROM resolution.concept_attribute a JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name = 'Answer' AND a.name = 'confidence';
INSERT INTO resolution.concept_attribute_binding (attribute_id, schema_name, table_name, column_name)
SELECT a.id, 'resolution', 'open_question_answer', 'role'
FROM resolution.concept_attribute a JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name = 'Answer' AND a.name = 'role';
INSERT INTO resolution.concept_attribute_binding (attribute_id, schema_name, table_name, column_name)
SELECT a.id, 'resolution', 'requirement', 'compilation_status'
FROM resolution.concept_attribute a JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name = 'Requirement';
INSERT INTO resolution.concept_attribute_binding (attribute_id, schema_name, table_name, column_name)
SELECT a.id, 'resolution', 'implementation_plan', 'status'
FROM resolution.concept_attribute a JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name = 'ImplementationPlan';
INSERT INTO resolution.concept_attribute_binding (attribute_id, schema_name, table_name, column_name)
SELECT a.id, 'resolution', 'work_request', 'business_status'
FROM resolution.concept_attribute a JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name = 'WorkRequest';
INSERT INTO resolution.concept_attribute_binding (attribute_id, schema_name, table_name, column_name)
SELECT a.id, 'resolution', 'proposition', 'disposition_value_id'
FROM resolution.concept_attribute a JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name = 'Proposition' AND a.name = 'disposition';
INSERT INTO resolution.concept_attribute_binding (attribute_id, schema_name, table_name, column_name)
SELECT a.id, 'resolution', 'proposition', 'grounding_status_value_id'
FROM resolution.concept_attribute a JOIN resolution.concept c ON c.id = a.concept_id
WHERE c.name = 'Proposition' AND a.name = 'grounding_status';

INSERT INTO resolution.concept_relationship (from_concept_id, to_concept_id, relationship_type)
SELECT f.id, t.id, 'answer' FROM resolution.concept f, resolution.concept t
WHERE f.name = 'Answer' AND t.name = 'VerifiedStatement';
INSERT INTO resolution.concept_relationship (from_concept_id, to_concept_id, relationship_type)
SELECT f.id, t.id, 'question' FROM resolution.concept f, resolution.concept t
WHERE f.name = 'OpenQuestion' AND t.name = 'Answer';
INSERT INTO resolution.concept_relationship (from_concept_id, to_concept_id, relationship_type)
SELECT f.id, t.id, 'asset_bridge' FROM resolution.concept f, resolution.concept t
WHERE f.name = 'PromotionCandidate' AND t.name = 'ShrapnelFact';
INSERT INTO resolution.concept_relationship (from_concept_id, to_concept_id, relationship_type)
SELECT f.id, t.id, 'parent' FROM resolution.concept f, resolution.concept t
WHERE f.name = 'Requirement' AND t.name = 'Requirement';
INSERT INTO resolution.concept_relationship (from_concept_id, to_concept_id, relationship_type)
SELECT f.id, t.id, 'parent_edge' FROM resolution.concept f, resolution.concept t
WHERE f.name = 'WorkRequestEdge' AND t.name = 'WorkRequest';
INSERT INTO resolution.concept_relationship (from_concept_id, to_concept_id, relationship_type)
SELECT f.id, t.id, 'child_edge' FROM resolution.concept f, resolution.concept t
WHERE f.name = 'WorkRequest' AND t.name = 'WorkRequestEdge';

INSERT INTO resolution.concept_relationship_binding
  (concept_relationship_id, from_schema, from_table, from_column, to_schema, to_table, to_column)
SELECT cr.id, 'resolution', 'open_question_answer', 'id', 'resolution', 'verified_statement', 'answer_id'
FROM resolution.concept_relationship cr
JOIN resolution.concept f ON f.id = cr.from_concept_id JOIN resolution.concept t ON t.id = cr.to_concept_id
WHERE f.name = 'Answer' AND t.name = 'VerifiedStatement';
INSERT INTO resolution.concept_relationship_binding
  (concept_relationship_id, from_schema, from_table, from_column, to_schema, to_table, to_column)
SELECT cr.id, 'resolution', 'open_question', 'id', 'resolution', 'open_question_answer', 'question_id'
FROM resolution.concept_relationship cr
JOIN resolution.concept f ON f.id = cr.from_concept_id JOIN resolution.concept t ON t.id = cr.to_concept_id
WHERE f.name = 'OpenQuestion' AND t.name = 'Answer';
INSERT INTO resolution.concept_relationship_binding
  (concept_relationship_id, from_schema, from_table, from_column, to_schema, to_table, to_column)
SELECT cr.id, '', '', 'asset_id', '', '', 'asset_id'
FROM resolution.concept_relationship cr
JOIN resolution.concept f ON f.id = cr.from_concept_id JOIN resolution.concept t ON t.id = cr.to_concept_id
WHERE f.name = 'PromotionCandidate' AND t.name = 'ShrapnelFact';
INSERT INTO resolution.concept_relationship_binding
  (concept_relationship_id, from_schema, from_table, from_column, to_schema, to_table, to_column)
SELECT cr.id, 'resolution', 'requirement', 'id', 'resolution', 'requirement', 'parent_id'
FROM resolution.concept_relationship cr
JOIN resolution.concept f ON f.id = cr.from_concept_id JOIN resolution.concept t ON t.id = cr.to_concept_id
WHERE f.name = 'Requirement' AND t.name = 'Requirement';
INSERT INTO resolution.concept_relationship_binding
  (concept_relationship_id, from_schema, from_table, from_column, to_schema, to_table, to_column)
SELECT cr.id, 'resolution', 'work_request_edge', 'parent_work_request_id', 'resolution', 'work_request', 'id'
FROM resolution.concept_relationship cr
JOIN resolution.concept f ON f.id = cr.from_concept_id JOIN resolution.concept t ON t.id = cr.to_concept_id
WHERE f.name = 'WorkRequestEdge' AND t.name = 'WorkRequest';
INSERT INTO resolution.concept_relationship_binding
  (concept_relationship_id, from_schema, from_table, from_column, to_schema, to_table, to_column)
SELECT cr.id, 'resolution', 'work_request', 'id', 'resolution', 'work_request_edge', 'child_work_request_id'
FROM resolution.concept_relationship cr
JOIN resolution.concept f ON f.id = cr.from_concept_id JOIN resolution.concept t ON t.id = cr.to_concept_id
WHERE f.name = 'WorkRequest' AND t.name = 'WorkRequestEdge';
"""


# ── helpers ─────────────────────────────────────────────────────────────────

def psql(db: str, sql: str, expect_fail: bool = False):
    r = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-Atc", sql],
        env=ENV, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    if expect_fail:
        assert r.returncode != 0, f"expected failure, got: {r.stdout}"
        return r.stdout + r.stderr
    assert r.returncode == 0, f"psql failed: {r.stderr}"
    return r.stdout.strip()


def apply_script(db: str, script: str):
    return subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
        input=script, env=ENV, capture_output=True, text=True, timeout=120,
    )


def body_without_transaction(text: str) -> str:
    lines = [ln for ln in text.splitlines()
             if ln.strip().upper() not in ("BEGIN;", "COMMIT;")]
    return "\n".join(lines) + "\n"


def counts(db: str) -> dict[str, int]:
    tables = ("concept", "concept_attribute", "concept_attribute_binding",
              "concept_relationship", "concept_relationship_binding",
              "representation", "expression")
    return {t: int(psql(db, f"select count(*) from resolution.{t}")) for t in tables}


@pytest.fixture(scope="module")
def e2edb():
    db = f"e2e_v217_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    created = subprocess.run(["createdb", "-w", db], env=ENV,
                             capture_output=True, stdin=subprocess.DEVNULL)
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
    body = body_without_transaction(V217.read_text(encoding="utf-8"))
    result = apply_script(e2edb, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert result.returncode == 0, f"V217 apply failed: {result.stderr}"
    return e2edb


# ── AC1: staged, idempotent, rollback-clean ─────────────────────────────────

def test_ac1_double_apply_and_rollback(e2edb):
    before = counts(e2edb)
    shape = psql(e2edb, """
        select count(*) from information_schema.columns
         where table_schema='resolution' and table_name='representation'
           and column_name in ('evaluation_role','read_adapter')
    """)
    body = body_without_transaction(V217.read_text(encoding="utf-8"))
    rolled = apply_script(e2edb, f"BEGIN;\n{body}\n{body}\nROLLBACK;\n")
    assert rolled.returncode == 0, f"double apply failed: {rolled.stderr}"
    assert counts(e2edb) == before
    assert psql(e2edb, """
        select count(*) from information_schema.columns
         where table_schema='resolution' and table_name='representation'
           and column_name in ('evaluation_role','read_adapter')
    """) == shape


def test_ac1_committed_apply_has_the_selector(migrated):
    assert psql(migrated, """
        select count(*) from information_schema.columns
         where table_schema='resolution' and table_name='representation'
           and column_name in ('evaluation_role','read_adapter')
    """) == "2"
    assert psql(migrated, """
        select count(*) from information_schema.columns
         where table_schema='resolution' and table_name='concept_attribute_binding'
           and column_name in ('id','representation_id')
    """) == "2"


# ── AC2/AC3: deterministic backfill and executable marking ──────────────────

def test_ac2_unique_match_is_scoped(migrated):
    assert psql(migrated, """
        select count(*) from resolution.concept_attribute_binding b
          join resolution.concept_attribute a on a.id=b.attribute_id
          join resolution.concept c on c.id=a.concept_id
         where c.name='Answer' and b.representation_id is not null
    """) == "2"
    for concept in ("Requirement", "ImplementationPlan", "WorkRequest"):
        assert psql(migrated, f"""
            select count(*) from resolution.concept_attribute_binding b
              join resolution.concept_attribute a on a.id=b.attribute_id
              join resolution.concept c on c.id=a.concept_id
             where c.name='{concept}' and b.representation_id is not null
        """) == "1", f"{concept} should scope to its one matching representation"


def test_ac2_zero_representation_concept_keeps_null_leg(migrated):
    assert psql(migrated, """
        select count(*) from resolution.concept_attribute_binding b
          join resolution.concept_attribute a on a.id=b.attribute_id
          join resolution.concept c on c.id=a.concept_id
         where c.name='Proposition' and b.representation_id is null
    """) == "2"


def test_ac3_executable_only_where_scoped(migrated):
    # Scoped representations became executable; the unscoped satellites did not.
    for schema, table in (("resolution", "open_question_answer"),
                          ("resolution", "requirement"),
                          ("resolution", "implementation_plan"),
                          ("resolution", "work_request"),
                          ("resolution", "work_request_edge")):
        assert psql(migrated, f"""
            select evaluation_role from resolution.representation
             where schema_name='{schema}' and table_name='{table}'
        """) == "executable", f"{schema}.{table} should be executable"
    for schema, table in (("conduit", "wrp_dag_nodes"), ("vision", "work_requests")):
        assert psql(migrated, f"""
            select evaluation_role from resolution.representation
             where schema_name='{schema}' and table_name='{table}'
        """) == "descriptive", f"{schema}.{table} must not be promoted"


# ── AC4: two representations evaluate identically ───────────────────────────

def test_ac4_two_representations_evaluate_identically(migrated):
    concept = psql(migrated, "select id from resolution.concept where name='Assessment'")
    attr = psql(migrated, f"""
        insert into resolution.concept_attribute (concept_id, name, value_type)
        values ('{concept}', 'outcome', 'text') returning id
    """)
    rep_a = psql(migrated, f"""
        insert into resolution.representation (concept_id, label, schema_name, table_name, evaluation_role)
        values ('{concept}', 'operational assessment', 'nebula', 'assessments', 'executable')
        returning id
    """)
    rep_b = psql(migrated, "select id from resolution.representation "
                           f"where concept_id='{concept}' and table_name='assessment'")
    psql(migrated, "update resolution.representation set evaluation_role='executable' "
                   f"where id='{rep_b}'")
    psql(migrated, f"""
        insert into resolution.concept_attribute_binding
          (attribute_id, representation_id, schema_name, table_name, column_name)
        values ('{attr}', '{rep_a}', 'nebula', 'assessments', 'outcome'),
               ('{attr}', '{rep_b}', 'resolution', 'assessment', 'outcome')
    """)
    psql(migrated, "insert into resolution.assessment (id, outcome) "
                   "values ('11111111-1111-4111-8111-111111111111','pass')")
    psql(migrated, "insert into nebula.assessments (id, outcome) "
                   "values ('11111111-1111-4111-8111-111111111111','pass')")
    expr = psql(migrated, f"""
        insert into resolution.expression (kind, attribute_id, return_type)
        values ('attribute_ref', '{attr}', 'text') returning id
    """)

    def value_for(rep: str) -> str:
        # The selector is session-scoped, so SET and the query must share one
        # psql invocation; a separate call would lose it.
        compiled = psql(
            migrated,
            f"set resolution.representation_id = '{rep}'; "
            "select resolution.compile_root("
            f"'{expr}', '''11111111-1111-4111-8111-111111111111''')",
        ).splitlines()[-1]
        return psql(
            migrated,
            f"set resolution.representation_id = '{rep}'; select {compiled}",
        ).splitlines()[-1]

    assert value_for(rep_a) == "pass"
    assert value_for(rep_b) == "pass"


# ── AC5: fail closed ────────────────────────────────────────────────────────

def test_ac5_unbound_representation_fails_closed(migrated):
    concept = psql(migrated, "select id from resolution.concept where name='Assessment'")
    attr = psql(migrated, "select id from resolution.concept_attribute "
                          f"where concept_id='{concept}' and name='outcome'")
    unbound = psql(migrated, f"""
        insert into resolution.representation (concept_id, label, schema_name, table_name, evaluation_role)
        values ('{concept}', 'unbound assessment', 'resolution', 'assessment', 'executable')
        returning id
    """)
    expr = psql(migrated, f"select id from resolution.expression where attribute_id='{attr}'")
    err = psql(
        migrated,
        f"set resolution.representation_id = '{unbound}'; "
        "select resolution.compile_root("
        f"'{expr}', '''11111111-1111-4111-8111-111111111111''')",
        expect_fail=True,
    )
    assert "no concept_attribute_binding" in err


def test_ac5_ambiguous_defaults_fail_closed(migrated):
    concept = psql(migrated, """
        insert into resolution.concept (name) values ('Ambiguous') returning id
    """)
    attr = psql(migrated, f"""
        insert into resolution.concept_attribute (concept_id, name, value_type)
        values ('{concept}', 'state', 'text') returning id
    """)
    for label in ("a", "b"):
        rep = psql(migrated, f"""
            insert into resolution.representation (concept_id, label, schema_name, table_name, evaluation_role)
            values ('{concept}', '{label}', 'resolution', 'assessment', 'executable')
            returning id
        """)
        psql(migrated, f"""
            insert into resolution.concept_attribute_binding
              (attribute_id, representation_id, schema_name, table_name, column_name)
            values ('{attr}', '{rep}', 'resolution', 'assessment', 'outcome')
        """)
    err = psql(migrated, "select resolution.resolve_binding_representation("
                         f"'{concept}')", expect_fail=True)
    assert "ambiguous representation" in err


# ── AC6: no regression on the live topology ─────────────────────────────────

def test_ac6_live_bindings_still_resolve(migrated):
    for concept, attribute in (("Answer", "confidence"), ("Answer", "role"),
                               ("Requirement", "compilation_status"),
                               ("ImplementationPlan", "status"),
                               ("WorkRequest", "business_status"),
                               ("Proposition", "disposition"),
                               ("Proposition", "grounding_status")):
        row = psql(migrated, f"""
            select (resolution.attribute_binding_for(a.id)).table_name
              from resolution.concept_attribute a
              join resolution.concept c on c.id=a.concept_id
             where c.name='{concept}' and a.name='{attribute}'
        """)
        assert row, f"{concept}.{attribute} lost its binding"

    # The asset-bridge relationship keeps the legacy NULL leg.
    assert psql(migrated, """
        select (resolution.relationship_binding_for(cr.id)).from_column
          from resolution.concept_relationship cr
          join resolution.concept f on f.id=cr.from_concept_id
          join resolution.concept t on t.id=cr.to_concept_id
         where f.name='PromotionCandidate' and t.name='ShrapnelFact'
    """) == "asset_id"


# ── AC7: allow-list enforcement ─────────────────────────────────────────────

def test_ac7_new_unregistered_function_is_refused(migrated):
    err = psql(migrated, """
        insert into resolution.expression (kind, function_name, return_type)
        values ('function_call', 'not_registered', 'text')
    """, expect_fail=True)
    assert "expression_function_name_fk" in err or "violates foreign key" in err


def test_ac7_preflight_refuses_over_legacy_call():
    """A pre-migration DB carrying the legacy unlisted call cannot apply V217.

    This needs its own database: after V217 is applied, the new FK refuses to
    create the legacy row, which is the other half of AC7.
    """
    db = f"e2e_v217_pre_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    created = subprocess.run(["createdb", "-w", db], env=ENV,
                             capture_output=True, stdin=subprocess.DEVNULL)
    assert created.returncode == 0, f"cannot create throwaway DB: {created.stderr}"
    try:
        assert apply_script(db, SKELETON).returncode == 0
        psql(db, """
            insert into resolution.expression (kind, function_name, return_type)
            values ('function_call', 'escalates_to', 'boolean')
        """)
        body = body_without_transaction(V217.read_text(encoding="utf-8"))
        attempted = apply_script(db, f"BEGIN;\n{body}\nCOMMIT;\n")
        assert attempted.returncode != 0
        assert "V217 refused" in (attempted.stderr + attempted.stdout)
    finally:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)
