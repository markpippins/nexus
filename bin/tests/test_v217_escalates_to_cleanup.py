"""Throwaway-DB guards for the staged v39 escalates_to cleanup.

The cleanup must never run against the live nexus database in this guard.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "createdb", "dropdb", "psql", doing="these tests create throwaway databases"
)

MIGRATION = ROOT / "schemas/migrations/resolution/resolution_migration_v39_escalates_to_cleanup.sql"
SEED = ROOT / "schemas/migrations/resolution/resolution_oq_seed.sql"
DATA = ROOT / "schemas/migrations/resolution/resolution_data_consolidated.sql"
TARGET = "77777777-7777-7777-7777-777777777701"
ENV = {
    **os.environ,
    "PGHOST": os.environ.get("PGHOST", "localhost"),
    "PGPORT": os.environ.get("PGPORT", "5432"),
    "PGUSER": os.environ.get("PGUSER", "pguser"),
}
PSQL = ("psql", "-w", "-q")
SKELETON = """
CREATE SCHEMA resolution;
CREATE SCHEMA aegis;
CREATE TABLE resolution.expression (
  id uuid PRIMARY KEY,
  kind text NOT NULL,
  function_name text,
  return_type text NOT NULL,
  label text
);
CREATE TABLE resolution.expression_operand (
  parent_expression_id uuid NOT NULL REFERENCES resolution.expression(id),
  child_expression_id uuid NOT NULL REFERENCES resolution.expression(id),
  position integer NOT NULL,
  PRIMARY KEY (parent_expression_id, position)
);
CREATE TABLE resolution.function_binding (
  function_name text PRIMARY KEY,
  sql_template text NOT NULL,
  arg_count integer NOT NULL,
  return_type text NOT NULL
);
CREATE TABLE resolution.verified_statement (
  id uuid PRIMARY KEY,
  expression_id uuid NOT NULL REFERENCES resolution.expression(id)
);
CREATE TABLE resolution.rule (
  id uuid PRIMARY KEY,
  expression_id uuid REFERENCES resolution.expression(id)
);
CREATE TABLE resolution.requirement (
  id uuid PRIMARY KEY,
  sol_ir_expression_id uuid REFERENCES resolution.expression(id)
);
CREATE TABLE aegis.invariant (
  id uuid PRIMARY KEY,
  expression_id uuid REFERENCES resolution.expression(id) ON DELETE SET NULL
);
INSERT INTO resolution.expression (id, kind, function_name, return_type, label)
VALUES ('77777777-7777-7777-7777-777777777701', 'function_call', 'escalates_to', 'boolean', 'placeholder'),
       ('77777777-7777-7777-7777-777777777702', 'literal', NULL, 'text', 'operand');
INSERT INTO resolution.expression_operand (parent_expression_id, child_expression_id, position)
VALUES ('77777777-7777-7777-7777-777777777701',
        '77777777-7777-7777-7777-777777777702', 1);
INSERT INTO resolution.function_binding VALUES ('lower', 'lower(%s)', 1, 'text');
INSERT INTO resolution.expression (id, kind, function_name, return_type, label)
VALUES ('77777777-7777-7777-7777-777777777704', 'function_call', 'lower', 'text', 'registered function');
"""


def run(db: str, sql: str, *, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-Atc", sql],
        env=ENV, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=90,
    )
    if succeeds:
        assert result.returncode == 0, result.stderr
    else:
        assert result.returncode != 0, result.stdout
    return result


def new_db():
    name = f"e2e_v39_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    result = subprocess.run(["createdb", "-w", name], env=ENV, capture_output=True,
                           text=True, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    return name


def drop_db(name: str) -> None:
    subprocess.run(["dropdb", "-w", name], env=ENV, capture_output=True,
                   stdin=subprocess.DEVNULL)


def test_cleanup_archives_deletes_is_idempotent_and_restorable():
    db = new_db()
    try:
        run(db, SKELETON)
        run(db, MIGRATION.read_text())
        assert run(db, f"select count(*) from resolution.expression where id='{TARGET}'").stdout.strip() == "0"
        assert run(db, f"select count(*) from resolution.expression_operand where parent_expression_id='{TARGET}'").stdout.strip() == "0"
        assert run(db, f"select count(*) from resolution.expression_cleanup_tombstone where expression_id='{TARGET}'").stdout.strip() == "1"
        assert run(db, f"select expression_row->>'function_name' from resolution.expression_cleanup_tombstone where expression_id='{TARGET}'").stdout.strip() == "escalates_to"
        assert run(db, f"select jsonb_array_length(operand_rows) from resolution.expression_cleanup_tombstone where expression_id='{TARGET}'").stdout.strip() == "1"
        assert run(db, f"select expression_row->>'label' from resolution.expression_cleanup_tombstone where expression_id='{TARGET}'").stdout.strip() == "placeholder"
        assert run(db, """
          select count(*) from resolution.expression e
           where e.kind='function_call' and e.function_name is not null
             and not exists (select 1 from resolution.function_binding f
                              where f.function_name=e.function_name)
        """).stdout.strip() == "0"
        # Repeat proves idempotence; unrelated registered expression survives.
        run(db, MIGRATION.read_text())
        assert run(db, "select count(*) from resolution.expression where id='77777777-7777-7777-7777-777777777704'").stdout.strip() == "1"
        # If an old seed resurrects the target with altered contents, the
        # original rollback image is not silently overwritten.
        run(db, f"INSERT INTO resolution.expression VALUES ('{TARGET}', 'function_call', 'escalates_to', 'boolean', 'resurrected')")
        result = run(db, MIGRATION.read_text(), succeeds=False)
        assert "differs from its existing cleanup tombstone" in result.stderr
        run(db, f"DELETE FROM resolution.expression WHERE id='{TARGET}'")
        # Exercise the documented tombstone rollback path.
        run(db, f"""
          INSERT INTO resolution.expression
          SELECT (jsonb_populate_record(NULL::resolution.expression, expression_row)).*
            FROM resolution.expression_cleanup_tombstone WHERE expression_id='{TARGET}';
          INSERT INTO resolution.expression_operand
          SELECT * FROM jsonb_populate_recordset(NULL::resolution.expression_operand,
             (SELECT operand_rows FROM resolution.expression_cleanup_tombstone WHERE expression_id='{TARGET}'));
        """)
        assert run(db, f"select count(*) from resolution.expression where id='{TARGET}'").stdout.strip() == "1"
        assert run(db, f"select count(*) from resolution.expression_operand where parent_expression_id='{TARGET}'").stdout.strip() == "1"
    finally:
        drop_db(db)


def test_cleanup_refuses_if_a_non_owned_foreign_key_reference_appears():
    db = new_db()
    try:
        run(db, SKELETON)
        run(db, "INSERT INTO resolution.rule VALUES ('77777777-7777-7777-7777-777777777705', '" + TARGET + "')")
        result = run(db, MIGRATION.read_text(), succeeds=False)
        assert "v39 refused" in result.stderr
        assert run(db, f"select count(*) from resolution.expression where id='{TARGET}'").stdout.strip() == "1"
        # The DDL and any tombstone writes are transactional too: when the
        # guard raises, the table creation rolls back along with the cleanup.
        exists = run(db, """select count(*) from information_schema.tables
                            where table_schema='resolution'
                              and table_name='expression_cleanup_tombstone'""").stdout.strip()
        assert exists == "0"
    finally:
        drop_db(db)


def test_seed_and_recovery_dump_do_not_resurrect_placeholder():
    assert TARGET not in SEED.read_text()
    assert "ba20d3d8-7428-4369-aae0-707001409c57" not in SEED.read_text()
    assert TARGET not in DATA.read_text()
    assert "ba20d3d8-7428-4369-aae0-707001409c57" not in DATA.read_text()
    # Keep deliberation evidence and the V217 artifact untouched.
    assert "99999999-9999-9999-9999-999999999901" in SEED.read_text()
    seed_text = SEED.read_text()
    assert "escalates_to" in seed_text
    assert "88888888-8888-8888-8888-888888888801" in seed_text
    assert "Should WorkRequest escalation target a Role or a specific Person?" in seed_text
