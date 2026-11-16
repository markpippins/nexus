#!/usr/bin/env python3
"""Guards for the staged V220 request/requirement/artifact link schema.

Plan 8261650 AC11/AC12 are the two remaining schema criteria that can be
prepared without the pending live Stage-6 window:

  AC11  replace the single `source_requirement_id` shortcut with a normalized
        `request_requirement_link(request_id, requirement_id, relation,
        provenance)`; no single-source shortcut.
  AC12  replace the polymorphic `(kind, ref)` artefact edge with
        `artifact(id, kind, canonical_ref)` plus
        `request_artifact_link(request_id, artifact_id, role, ...)`; no
        polymorphic edge.

V220 is staged prep. These guards pin the promises the migration makes:

  * additive only - no DROP / TRUNCATE / DELETE in executable SQL;
  * both FK targets are base tables (DBA pre-flight relkind guard);
  * bitemporal link rows, surrogate UUID PKs, one CURRENT link per pair;
  * RESTRICT FKs - audit evidence is not cascade-deleted;
  * required provenance, closed CHECK vocabularies;
  * `source_requirement_id` is retained and marked deprecated, never dropped;
  * the backfill is cardinality-independent, idempotent, and count-gated;
  * rollback leaves the pre-V220 schema intact.

The DB half runs in the `guards-db` tier. Every client call passes `-w` and
DEVNULL stdin: a password prompt would hang CI instead of failing it.

DB tier: bin/tests-ci-manifest.json -> tiers.db

topology-inventory: ignore-file - the INSERTs below are fixture rows written
into a throwaway database, not producers of the canonical surfaces.
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

V220 = REPO_ROOT / "sql" / "V220__work_request_requirement_and_artifact_links.sql"

ENV = {**os.environ,
       "PGHOST": os.environ.get("PGHOST", "localhost"),
       "PGPORT": os.environ.get("PGPORT", "5432"),
       "PGUSER": os.environ.get("PGUSER", "pguser")}
PSQL = ("psql", "-w")

#: Fixed fixture IDs make assertions readable. Three work requests: one live
#: source, one without a source, one retired source. The retired row proves the
#: backfill copies the bitemporal window instead of inventing a current link.
R1 = "11111111-1111-4111-8111-111111111111"
R2 = "22222222-2222-4222-8222-222222222222"
W1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"
W2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2"
W3 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3"

SKELETON = f"""
CREATE SCHEMA resolution;
CREATE TABLE resolution.requirement (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);
CREATE TABLE resolution.work_request (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_requirement_id uuid REFERENCES resolution.requirement (id),
  valid_from timestamptz NOT NULL DEFAULT now(),
  valid_until timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
  recorded_on_dt timestamptz NOT NULL DEFAULT now(),
  recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);
INSERT INTO resolution.requirement (id) VALUES ('{R1}'), ('{R2}');
INSERT INTO resolution.work_request
  (id, source_requirement_id, valid_from, valid_until,
   recorded_on_dt, recorded_until_dt)
VALUES
  ('{W1}', '{R1}', now(), 'infinity', now(), 'infinity'),
  ('{W2}', NULL,    now(), 'infinity', now(), 'infinity'),
  ('{W3}', '{R2}', timestamptz '2019-01-01', timestamptz '2020-01-01',
                    timestamptz '2019-01-01', timestamptz '2020-01-01');
"""


# ── text handling ───────────────────────────────────────────────────────────

def body_of(text: str) -> str:
    """The migration body: everything from the first non-comment line."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if not line.lstrip().startswith("--"))
    return "".join(lines[start:])


def code_of(text: str) -> str:
    """Body with `--` comments removed, so prose cannot trip or satisfy a scan."""
    return "\n".join(
        re.sub(r"--.*$", "", line) for line in body_of(text).splitlines()
    )


DATA_DELETION = re.compile(r"(?im)^\s*DELETE\b|\bDELETE\s+FROM\b")
DROP_TRUNCATE = re.compile(r"\b(DROP|TRUNCATE)\b", re.IGNORECASE)


def destructive_statements(text: str) -> list[str]:
    code = code_of(text)
    return [m.group(0) for m in DROP_TRUNCATE.finditer(code)] + [
        m.group(0) for m in DATA_DELETION.finditer(code)
    ]


# ── hermetic guards ─────────────────────────────────────────────────────────

def test_migration_is_additive():
    assert destructive_statements(V220.read_text(encoding="utf-8")) == []


def test_guard_bites_on_destructive_fixtures():
    for destructive in (
        "DROP TABLE resolution.artifact",
        "TRUNCATE resolution.request_requirement_link",
        "DELETE FROM resolution.request_artifact_link",
    ):
        assert destructive_statements(f"BEGIN;\n{destructive};\nCOMMIT;\n"), destructive


def test_version_and_preflight_guard_are_present():
    text = V220.read_text(encoding="utf-8")
    code = code_of(text)
    assert V220.name.startswith("V220__")
    assert "V220-PREFLIGHT-003" in text
    assert "relkind" in code and "'r'" in code


def test_old_column_is_kept_and_marked_deprecated():
    text = V220.read_text(encoding="utf-8")
    code = code_of(text)
    assert "source_requirement_id" in code
    assert not re.search(
        r"ALTER\s+TABLE\s+resolution\.work_request\s+DROP\s+COLUMN",
        code, re.IGNORECASE,
    )
    assert "DEPRECATED (V220 staging)" in text


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


def apply_script(db: str, script: str, expect_fail: bool = False):
    result = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-q"],
        input=script, env=ENV, capture_output=True, text=True, timeout=90,
    )
    if expect_fail:
        assert result.returncode != 0, f"expected failure, got: {result.stdout}"
        return result.stdout + result.stderr
    assert result.returncode == 0, f"apply failed: {result.stderr}"
    return result.stdout


def without_transaction(body: str) -> str:
    return "\n".join(
        line for line in body.splitlines()
        if line.strip().upper() not in ("BEGIN;", "COMMIT;")
    ) + "\n"


def catalog(db: str) -> dict[str, int]:
    return {
        "relations": int(psql(db, "select count(*) from pg_class c join pg_namespace n"
                                  " on n.oid=c.relnamespace where n.nspname='resolution'")),
        "columns": int(psql(db, "select count(*) from information_schema.columns"
                                " where table_schema='resolution'")),
        "indexes": int(psql(db, "select count(*) from pg_indexes"
                                 " where schemaname='resolution'")),
    }


def new_db(prefix: str) -> str:
    db = f"{prefix}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    created = subprocess.run(["createdb", "-w", db], env=ENV, capture_output=True,
                             stdin=subprocess.DEVNULL)
    assert created.returncode == 0, f"cannot create throwaway DB {db}: {created.stderr}"
    return db


def drop_db(db: str):
    subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                   stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def e2edb():
    db = new_db("e2e_wr_links_v220")
    seeded = apply_script(db, SKELETON)
    assert seeded is not None
    try:
        yield db
    finally:
        drop_db(db)


@pytest.fixture(scope="module")
def migrated(e2edb):
    body = without_transaction(V220.read_text(encoding="utf-8"))
    apply_script(e2edb, f"BEGIN;\n{body}\nCOMMIT;\n")
    return e2edb


# ── apply / idempotence / rollback ─────────────────────────────────────────

def test_double_apply_is_idempotent_and_rollback_is_clean(e2edb):
    before = catalog(e2edb)
    body = without_transaction(V220.read_text(encoding="utf-8"))
    apply_script(e2edb, f"BEGIN;\n{body}\n{body}\nROLLBACK;\n")
    assert catalog(e2edb) == before


def test_preflight_rejects_a_view_fk_target():
    db = new_db("e2e_wr_links_v220_view")
    try:
        apply_script(db, """
            CREATE SCHEMA resolution;
            CREATE TABLE resolution.requirement_base (id uuid PRIMARY KEY);
            CREATE VIEW resolution.requirement AS
              SELECT id FROM resolution.requirement_base;
            CREATE TABLE resolution.work_request (
              id uuid PRIMARY KEY,
              source_requirement_id uuid
            );
        """)
        body = without_transaction(V220.read_text(encoding="utf-8"))
        message = apply_script(db, f"BEGIN;\n{body}\nCOMMIT;\n", expect_fail=True)
        assert "V220-PREFLIGHT-003" in message
    finally:
        drop_db(db)


def test_committed_apply_creates_the_tables_and_backfills(migrated):
    assert psql(migrated, "select to_regclass('resolution.request_requirement_link')::text") \
        == "resolution.request_requirement_link"
    assert psql(migrated, "select to_regclass('resolution.artifact')::text") \
        == "resolution.artifact"
    assert psql(migrated, "select to_regclass('resolution.request_artifact_link')::text") \
        == "resolution.request_artifact_link"

    assert psql(migrated, "select count(*) from resolution.request_requirement_link") == "2"
    assert psql(migrated, """
        select count(*) from resolution.request_requirement_link
         where relation='source'
           and provenance='v220:legacy-source_requirement_id'
    """) == "2"
    assert psql(migrated, """
        select count(*) from resolution.request_requirement_link
         where request_id='%s' and requirement_id='%s'
           and valid_until='infinity' and recorded_until_dt='infinity'
    """ % (W1, R1)) == "1"
    assert psql(migrated, """
        select count(*) from resolution.request_requirement_link
         where request_id='%s' and requirement_id='%s'
           and valid_until='2020-01-01' and recorded_until_dt='2020-01-01'
    """ % (W3, R2)) == "1"

    assert psql(migrated, """
        select col_description('resolution.work_request'::regclass,
                               attnum)
          from pg_attribute
         where attrelid='resolution.work_request'::regclass
           and attname='source_requirement_id'
    """).startswith("DEPRECATED (V220 staging)")


def test_second_committed_apply_is_a_no_op(migrated):
    body = without_transaction(V220.read_text(encoding="utf-8"))
    apply_script(migrated, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert psql(migrated, "select count(*) from resolution.request_requirement_link") == "2"
    assert psql(migrated, "select count(*) from resolution.work_request") == "3"


# ── shape / constraints ────────────────────────────────────────────────────

def test_shape_is_bitemporal_with_surrogate_pks_and_restrict_fks(migrated):
    for table in ("request_requirement_link", "artifact", "request_artifact_link"):
        cols = psql(migrated, f"""
            select string_agg(column_name || ':' || data_type, ',' order by ordinal_position)
              from information_schema.columns
             where table_schema='resolution' and table_name='{table}'
        """)
        for required in ("id:uuid", "valid_from:timestamp with time zone",
                         "valid_until:timestamp with time zone",
                         "recorded_on_dt:timestamp with time zone",
                         "recorded_until_dt:timestamp with time zone"):
            assert required in cols, f"{table} missing {required}: {cols}"

    assert psql(migrated, """
        select confdeltype from pg_constraint
         where conrelid='resolution.request_requirement_link'::regclass
           and contype='f' order by conname
    """).split() == ["r", "r"]
    assert psql(migrated, """
        select confdeltype from pg_constraint
         where conrelid='resolution.request_artifact_link'::regclass
           and contype='f' order by conname
    """).split() == ["r", "r"]

    assert psql(migrated, """
        select indexname from pg_indexes
         where schemaname='resolution'
           and indexname in ('uq_request_requirement_link_current',
                             'uq_artifact_current',
                             'uq_request_artifact_link_current')
         order by indexname
    """).splitlines() == [
        "uq_artifact_current",
        "uq_request_artifact_link_current",
        "uq_request_requirement_link_current",
    ]


def test_partial_unique_current_and_historical_duplicate(migrated):
    psql(migrated, f"""
        insert into resolution.request_requirement_link
          (request_id, requirement_id, relation, provenance)
        values ('%s', '%s', 'validates', 'test:current-duplicate')
    """ % (W1, R1), expect_fail=True)

    psql(migrated, f"""
        insert into resolution.request_requirement_link
          (request_id, requirement_id, relation, provenance,
           valid_from, valid_until, recorded_on_dt, recorded_until_dt)
        values ('%s', '%s', 'validates', 'test:historical',
                now() - interval '2 days', now() - interval '1 day',
                now() - interval '2 days', now() - interval '1 day')
    """ % (W1, R1))


def test_restrict_blocks_hard_delete_but_soft_retire_works(migrated):
    message = psql(migrated, f"delete from resolution.work_request where id='{W1}'",
                   expect_fail=True)
    assert "foreign key" in message.lower()

    apply_script(migrated, f"""
        BEGIN;
        update resolution.work_request
           set valid_until = now() - interval '1 second',
               recorded_until_dt = now() - interval '1 second'
         where id='{W1}';
        select count(*) from resolution.work_request where id='{W1}';
        ROLLBACK;
    """)


def test_check_vocabularies_and_required_provenance(migrated):
    message = psql(migrated, f"""
        insert into resolution.request_requirement_link
          (request_id, requirement_id, relation, provenance)
        values ('{W2}', '{R2}', 'not-a-relation', 'test')
    """, expect_fail=True)
    assert "check constraint" in message.lower()

    artifact_id = psql(migrated, """
        insert into resolution.artifact (kind, canonical_ref)
        values ('reference', 'test/role-check-artifact') returning id
    """).splitlines()[0]
    message = psql(migrated, f"""
        insert into resolution.request_artifact_link
          (request_id, artifact_id, role, provenance)
        values ('{W2}', '{artifact_id}', 'not-a-role', 'test')
    """, expect_fail=True)
    assert "check constraint" in message.lower()

    message = psql(migrated, f"""
        insert into resolution.request_requirement_link
          (request_id, requirement_id, relation)
        values ('{W2}', '{R2}', 'validates')
    """, expect_fail=True)
    assert "not-null" in message.lower() or "null value" in message.lower()


def test_artifact_identity_and_link_role(migrated):
    artifact_id = psql(migrated, """
        insert into resolution.artifact (kind, canonical_ref)
        values ('document', 'docs/plan-8261650.md') returning id
    """).splitlines()[0]
    psql(migrated, """
        insert into resolution.artifact (kind, canonical_ref)
        values ('document', 'docs/plan-8261650.md')
    """, expect_fail=True)
    psql(migrated, """
        insert into resolution.artifact (kind, canonical_ref)
        values ('reference', 'docs/plan-8261650.md')
    """)

    psql(migrated, f"""
        insert into resolution.request_artifact_link
          (request_id, artifact_id, role, provenance)
        values ('{W2}', '{artifact_id}', 'input', 'test:artifact-link')
    """)
    psql(migrated, f"""
        insert into resolution.request_artifact_link
          (request_id, artifact_id, role, provenance)
        values ('{W2}', '{artifact_id}', 'output', 'test:duplicate')
    """, expect_fail=True)

    message = psql(migrated, f"delete from resolution.artifact where id='{artifact_id}'",
                   expect_fail=True)
    assert "foreign key" in message.lower()