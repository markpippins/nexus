#!/usr/bin/env python3
"""AC11 guards — normalized request/requirement links and the staged V221 cutover.

Plan 8261650 AC11: replace the single `source_requirement_id` shortcut with
`resolution.request_requirement_link(request_id, requirement_id, relation,
provenance)`; no single-source shortcut.

This file pins two things:

  1. The five live consumer files no longer read or write the shortcut; they
     join the normalized link and select current `relation='source'` rows.
  2. The staged V221 migration refuses to drop the shortcut until every
     non-null source row has a matching link, then removes the column and
     rebuilds the dependent views without it. The DB half runs against a
     throwaway PostgreSQL database, never live.

DB tier: bin/tests-ci-manifest.json -> tiers.db

topology-inventory: ignore-file - the INSERTs below are fixture rows written
into a throwaway database, not producers of the canonical surfaces.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from host_tool_guard import skip_unless_host_tools  # noqa: E402

pytestmark = skip_unless_host_tools(
    "createdb", "dropdb", "psql", doing="these tests create a throwaway database"
)

V221 = REPO_ROOT / "sql" / "V221__ac11_drop_source_requirement_shortcut.sql"

CONSUMERS = [
    REPO_ROOT / "moleculer" / "nebula" / "src" / "routes.ts",
    REPO_ROOT / "typescript" / "nebula-srv" / "src" / "routes.ts",
    REPO_ROOT / "adonisjs" / "nexus-control-edge" / "app" / "controllers" / "nebula_harvest_controller.ts",
    REPO_ROOT / "python" / "cascade" / "evaluators" / "kg_impact.py",
    REPO_ROOT / "python" / "tackle" / "planner_mcp_server.py",
]

ENV = {
    **os.environ,
    "PGHOST": os.environ.get("PGHOST", "localhost"),
    "PGPORT": os.environ.get("PGPORT", "5432"),
    "PGUSER": os.environ.get("PGUSER", "pguser"),
}
PSQL = ("psql", "-w")

R1 = "11111111-1111-4111-8111-111111111111"
R2 = "22222222-2222-4222-8222-222222222222"
W1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
W2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
W3 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"


def body_of(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if not line.lstrip().startswith("--"))
    return "".join(lines[start:])


def without_transaction(body: str) -> str:
    return "\n".join(
        line for line in body.splitlines()
        if line.strip().upper() not in ("BEGIN;", "COMMIT;")
    ) + "\n"


def psql(db: str, sql: str, expect_fail: bool = False):
    result = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-Atc", sql],
        env=ENV,
        capture_output=True,
        text=True,
        timeout=90,
        stdin=subprocess.DEVNULL,
    )
    if expect_fail:
        assert result.returncode != 0, f"expected failure, got: {result.stdout}"
        return result.stdout + result.stderr
    assert result.returncode == 0, f"psql failed: {result.stderr}"
    return result.stdout.strip()


def apply_script(db: str, script: str, expect_fail: bool = False):
    result = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
        input=script,
        env=ENV,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if expect_fail:
        assert result.returncode != 0, f"expected failure, got: {result.stdout}"
        return result.stdout + result.stderr
    assert result.returncode == 0, f"apply failed: {result.stderr}"
    return result.stdout


def new_db(prefix: str) -> str:
    db = f"{prefix}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    created = subprocess.run(
        ["createdb", "-w", db], env=ENV, capture_output=True,
        stdin=subprocess.DEVNULL,
    )
    assert created.returncode == 0, f"cannot create throwaway DB {db}: {created.stderr}"
    return db


def drop_db(db: str) -> None:
    subprocess.run(
        ["dropdb", "-w", db], env=ENV, capture_output=True,
        stdin=subprocess.DEVNULL,
    )


SKELETON = f"""
CREATE SCHEMA resolution;
CREATE SCHEMA nebula;
CREATE SCHEMA vision;
CREATE SCHEMA execution;

CREATE TABLE resolution.requirement (
  id uuid PRIMARY KEY,
  system_id uuid,
  subsystem_id uuid,
  feature_id uuid,
  title text NOT NULL,
  status text NOT NULL DEFAULT 'Backlog'
);

CREATE TABLE resolution.work_request (
  id uuid PRIMARY KEY,
  asset_id uuid,
  title text NOT NULL,
  description text,
  source_specification_id uuid,
  source_requirement_id uuid REFERENCES resolution.requirement (id),
  business_status text NOT NULL DEFAULT 'DRAFT',
  intent text,
  context jsonb NOT NULL DEFAULT '{{}}',
  constraints jsonb NOT NULL DEFAULT '{{}}',
  created_by text,
  dco_json text,
  legacy_id text,
  plan_id text,
  step_outputs text NOT NULL DEFAULT '{{}}',
  consumed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  valid_from timestamptz NOT NULL DEFAULT now(),
  valid_until timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
  recorded_on_dt timestamptz NOT NULL DEFAULT now(),
  recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
  request_kind text,
  entity_key text
);

CREATE TABLE resolution.request_requirement_link (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id uuid NOT NULL REFERENCES resolution.work_request (id) ON DELETE RESTRICT,
  requirement_id uuid NOT NULL REFERENCES resolution.requirement (id) ON DELETE RESTRICT,
  relation text NOT NULL CHECK (relation IN ('source', 'derives_from', 'basis_of', 'refines', 'validates')),
  provenance text NOT NULL,
  valid_from timestamptz NOT NULL DEFAULT now(),
  valid_until timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
  recorded_on_dt timestamptz NOT NULL DEFAULT now(),
  recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);

CREATE TABLE resolution.request_role_eligibility (
  request_id uuid NOT NULL REFERENCES resolution.work_request (id),
  role text NOT NULL
);

CREATE TABLE execution.requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_wr_id uuid,
  status text,
  business_key text
);

CREATE TABLE vision.work_requests (
  work_request_uuid text,
  status text
);

CREATE SCHEMA scratch;
CREATE VIEW scratch.v_work_request_overview AS
SELECT 'placeholder'::text AS id;

CREATE VIEW nebula.requirements AS
SELECT id, system_id, subsystem_id, feature_id, title, status
FROM resolution.requirement;

INSERT INTO resolution.requirement (id, system_id, subsystem_id, feature_id, title)
VALUES
  ('{R1}', '99999999-9999-4999-8999-999999999991', '88888888-8888-4888-8888-888888888881', NULL, 'Requirement one'),
  ('{R2}', '99999999-9999-4999-8999-999999999992', NULL, NULL, 'Requirement two');

INSERT INTO resolution.work_request
  (id, title, legacy_id, source_requirement_id, business_status, consumed_at)
VALUES
  ('{W1}', 'Linked request one', 'nebula.work_requests_history:{W1}', '{R1}', 'DRAFT', NULL),
  ('{W2}', 'Linked request two', 'nebula.work_requests_history:{W2}', '{R2}', 'DRAFT', NULL),
  ('{W3}', 'Unlinked request', 'nebula.work_requests_history:{W3}', NULL, 'DRAFT', NULL);

INSERT INTO resolution.request_requirement_link
  (request_id, requirement_id, relation, provenance)
VALUES
  ('{W1}', '{R1}', 'source', 'test:ac11'),
  ('{W2}', '{R2}', 'source', 'test:ac11');

INSERT INTO resolution.request_role_eligibility (request_id, role)
VALUES ('{W1}', 'builder');

INSERT INTO execution.requests (source_wr_id, status, business_key)
VALUES ('{W1}', 'READY', 'bk-1');

INSERT INTO vision.work_requests (work_request_uuid, status)
VALUES ('{W1}', 'queued');
"""


@pytest.fixture(scope="module")
def migrated():
    db = new_db("e2e_ac11_v221")
    apply_script(db, SKELETON)
    try:
        yield db
    finally:
        drop_db(db)


# ── source / static guards ─────────────────────────────────────────────────

def test_no_consumer_reads_or_writes_the_shortcut():
    for path in CONSUMERS:
        text = path.read_text(encoding="utf-8")
        assert "source_requirement_id" not in text, path
        assert "resolution.request_requirement_link" in text, path


def test_migration_is_the_staged_drop_and_keeps_the_link_path():
    text = V221.read_text(encoding="utf-8")
    code = without_transaction(body_of(text))
    assert "V221-PREFLIGHT-003" in text
    assert "V221-POST-001" in text
    assert re.search(
        r"ALTER\s+TABLE\s+resolution\.work_request\s+DROP\s+COLUMN\s+source_requirement_id",
        code,
        re.IGNORECASE,
    )
    assert "resolution.request_requirement_link" in code
    assert not re.search(r"\b(DELETE|TRUNCATE|DROP\s+TABLE)\b", code, re.IGNORECASE)


# ── throwaway database ─────────────────────────────────────────────────────

def test_v221_drops_the_shortcut_and_rebuilds_views(migrated):
    body = without_transaction(V221.read_text(encoding="utf-8"))
    apply_script(migrated, f"BEGIN;\n{body}\nCOMMIT;\n")

    assert psql(migrated, """
        select count(*) from information_schema.columns
         where table_schema='resolution' and table_name='work_request'
           and column_name='source_requirement_id'
    """) == "0"
    assert psql(migrated, """
        select count(*) from information_schema.columns
         where table_schema in ('resolution','nebula')
           and table_name in ('work_request_backlog','work_requests')
           and column_name='source_requirement_id'
    """) == "0"
    assert psql(migrated, "select count(*) from nebula.work_requests") == "3"
    assert psql(migrated, "select count(*) from scratch.v_work_request_overview") == "3"


def test_link_scoping_returns_the_requirement_hierarchy(migrated):
    rows = psql(migrated, f"""
        select distinct wr.id
          from nebula.work_requests wr
          join resolution.request_requirement_link rrl
            on rrl.request_id = wr.id
           and rrl.relation = 'source'
           and rrl.valid_until = 'infinity'::timestamptz
           and rrl.recorded_until_dt = 'infinity'::timestamptz
          join nebula.requirements req on req.id = rrl.requirement_id
         where req.system_id = '99999999-9999-4999-8999-999999999991'
         order by wr.id
    """)
    assert rows == W1

    # exact shape used by the HTTP consumer: DISTINCT ON data + DISTINCT count
    assert psql(migrated, f"""
        select count(*) from (
          select distinct on (wr.id) wr.*
            from nebula.work_requests wr
            join resolution.request_requirement_link rrl
              on rrl.request_id = wr.id
             and rrl.relation = 'source'
             and rrl.valid_until = 'infinity'::timestamptz
             and rrl.recorded_until_dt = 'infinity'::timestamptz
            join nebula.requirements req on req.id = rrl.requirement_id
           where req.system_id = '99999999-9999-4999-8999-999999999991'
           order by wr.id, wr.created_at desc
           limit 2 offset 0
        ) scoped
    """) == "1"
    assert psql(migrated, """
        select count(distinct wr.id)::int
          from nebula.work_requests wr
          join resolution.request_requirement_link rrl
            on rrl.request_id = wr.id
           and rrl.relation = 'source'
           and rrl.valid_until = 'infinity'::timestamptz
           and rrl.recorded_until_dt = 'infinity'::timestamptz
          join nebula.requirements req on req.id = rrl.requirement_id
         where req.system_id = '99999999-9999-4999-8999-999999999991'
    """) == "1"


def test_link_insert_and_read_are_the_normalized_path(migrated):
    new_req = "33333333-3333-4333-8333-333333333333"
    psql(migrated, f"""
        insert into resolution.requirement (id, system_id, title)
        values ('{new_req}', '99999999-9999-4999-8999-999999999993', 'Requirement three')
    """)
    psql(migrated, f"""
        insert into resolution.request_requirement_link
          (request_id, requirement_id, relation, provenance)
        values ('{W3}', '{new_req}', 'source', 'test:ac11-write')
    """)
    assert psql(migrated, f"""
        select count(*) from resolution.request_requirement_link
         where request_id='{W3}' and requirement_id='{new_req}'
           and relation='source' and valid_until='infinity'
           and recorded_until_dt='infinity'
    """) == "1"


def test_preflight_refuses_unbackfilled_source_rows():
    db = new_db("e2e_ac11_v221_preflight")
    try:
        apply_script(db, SKELETON)
        psql(db, f"delete from resolution.request_requirement_link where request_id='{W2}'")
        body = without_transaction(V221.read_text(encoding="utf-8"))
        message = apply_script(db, f"BEGIN;\n{body}\nCOMMIT;\n", expect_fail=True)
        assert "V221-PREFLIGHT-003" in message
        assert psql(db, """
            select count(*) from information_schema.columns
             where table_schema='resolution' and table_name='work_request'
               and column_name='source_requirement_id'
        """) == "1"
    finally:
        drop_db(db)


def test_second_apply_is_idempotent(migrated):
    body = without_transaction(V221.read_text(encoding="utf-8"))
    apply_script(migrated, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert psql(migrated, "select count(*) from nebula.work_requests") == "3"
    assert psql(migrated, """
        select count(*) from information_schema.columns
         where table_schema='resolution' and table_name='work_request'
           and column_name='source_requirement_id'
    """) == "0"


def test_transactional_rollback_leaves_the_shortcut_intact():
    db = new_db("e2e_ac11_v221_rollback")
    try:
        apply_script(db, SKELETON)
        body = without_transaction(V221.read_text(encoding="utf-8"))
        apply_script(db, f"BEGIN;\n{body}\nROLLBACK;\n")
        assert psql(db, """
            select count(*) from information_schema.columns
             where table_schema='resolution' and table_name='work_request'
               and column_name='source_requirement_id'
        """) == "1"
        assert psql(db, "select count(*) from resolution.request_requirement_link") == "2"
    finally:
        drop_db(db)
