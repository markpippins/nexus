#!/usr/bin/env python3
"""Guards for the staged V219 request_kind migration (plan 8261650 AC1).

Plan 8261650 is the WR canonicalization landing (Option B). Its first
acceptance criterion is DBA-owned and must land before any Builder work:

    "add a registry-backed `request_kind` to the canonical WR spine,
     independent of lifecycle status, plus relational support for the
     kind-agnostic backlog eligibility projection; ... DBA posts the
     alteration to Drafts and applies only after admin approval; Builder must
     not start until applied."

The proposal was first posted as a forum-only draft labelled V216. Binding DBA
decision 1a3d32d8-1b10-4097-b2b8-051f28c32683 reviewed the DDL as sound but
blocked apply-as-is on three defects; this file's sibling SQL fixes them, and
the guards below pin each fix so it cannot silently regress:

  D1  version collision — V216/V217/V218 are taken, so this is V219. Pinned by
      the file-name check and by the absence of a V216 label in the SQL.
  D2  hard-coded row count — the postcheck expected "46" while live held 47 and
      keeps moving. Pinned by refusing any bare 46/47 literal in executable
      SQL, and by a live-apply proof that the backfill covers whatever
      cardinality the table actually has.
  D3  no file on disk — pinned by the fact that this test, and CI, read a real
      file rather than a forum post.

Also pinned here, because they are the promises the migration makes:

  * additive only — no DROP / TRUNCATE / `DELETE FROM`, and every ALTER TABLE
    is either `ADD COLUMN IF NOT EXISTS` or a guarded `ADD CONSTRAINT ...
    FOREIGN KEY`. (`ON DELETE CASCADE` on the NEW eligibility table is a
    referential action, not data deletion, and is allowed deliberately.)
  * the kind axis ships nullable with NO default — absent is not a value.
  * the backlog projection carries no `request_kind` predicate: an attestation
    or decision request is exactly as eligible as a build request.
  * `request_kind` and `business_status` are never coupled by a constraint.

The DB half runs in the `guards-db` tier, which provides postgres and psql.
Every client call passes `-w` and DEVNULL stdin: a password prompt would hang a
CI runner instead of failing it.

DB tier: bin/tests-ci-manifest.json -> tiers.db

topology-inventory: ignore-file — the INSERTs below are fixture rows written
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

V219 = REPO_ROOT / "sql" / "V219__work_request_request_kind_and_backlog_eligibility.sql"

ENV = {**os.environ,
       "PGHOST": os.environ.get("PGHOST", "localhost"),
       "PGPORT": os.environ.get("PGPORT", "5432"),
       "PGUSER": os.environ.get("PGUSER", "pguser")}
PSQL = ("psql", "-w")

#: Only what the migration touches is pre-created — the point is to prove the
#: migration's own delta. Five fixture rows (not a round "46") exist to prove
#: the backfill is cardinality-independent: a hard-coded count cannot pass.
SKELETON = """
CREATE SCHEMA resolution;
CREATE TABLE resolution.work_request (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id integer,
  business_status text NOT NULL DEFAULT 'DRAFT',
  consumed_at timestamptz,
  context jsonb
);
INSERT INTO resolution.work_request (plan_id, business_status) VALUES
  (1, 'DRAFT'), (2, 'DRAFT'), (3, 'APPROVED'), (4, 'CANCELLED'), (5, 'COMPLETED');
UPDATE resolution.work_request SET consumed_at = now() WHERE plan_id = 3;
"""


# ── text handling ───────────────────────────────────────────────────────────

def body_of(text: str) -> str:
    """The migration body: everything from the first non-comment line."""
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if not line.lstrip().startswith("--"))
    return "".join(lines[start:])


def code_of(text: str) -> str:
    """Body with `--` comments removed, so prose cannot trip or satisfy a scan.

    This matters for V219 specifically: the file documents its own rollback and
    its postchecks in comments, and those comments legitimately contain DROP,
    UPDATE and the numbers a naive scan would flag.
    """
    return "\n".join(
        re.sub(r"--.*$", "", line) for line in body_of(text).splitlines()
    )


#: `DELETE FROM` / a line-leading DELETE is data deletion. Bare `ON DELETE
#: CASCADE` is a referential action on a table this migration creates, and is
#: allowed — refusing it would forbid every correctly-modelled child table.
DATA_DELETION = re.compile(r"(?im)^\s*DELETE\b|\bDELETE\s+FROM\b")
DROP_TRUNCATE = re.compile(r"\b(DROP|TRUNCATE)\b", re.IGNORECASE)

ALTER_STMT = re.compile(r"ALTER\s+TABLE\b[^;]*;", re.IGNORECASE | re.DOTALL)
ALTER_HEAD = re.compile(
    r"^ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[A-Za-z_\"][\w.\"]*\s+", re.IGNORECASE
)
ADD_COLUMN = re.compile(
    r"^ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+[A-Za-z_]\w*\s+\S", re.IGNORECASE
)
ADD_FK = re.compile(
    r"^ADD\s+CONSTRAINT\s+[A-Za-z_]\w*\s+FOREIGN\s+KEY\s*\([^)]*\)\s*"
    r"REFERENCES\s+[A-Za-z_\"][\w.\"]*\s*\([^)]*\)",
    re.IGNORECASE,
)


def destructive_statements(text: str) -> list[str]:
    """Statements that would break the additive contract."""
    code = code_of(text)
    offenders = [m.group(0) for m in DROP_TRUNCATE.finditer(code)]
    offenders += [m.group(0) for m in DATA_DELETION.finditer(code)]
    for statement in ALTER_STMT.findall(code):
        flattened = re.sub(r"\s+", " ", statement).strip().rstrip(";").strip()
        head = ALTER_HEAD.match(flattened)
        clause = flattened[head.end():] if head else ""
        if not (ADD_COLUMN.match(clause) or ADD_FK.match(clause)):
            offenders.append(flattened[:120])
    return offenders


def view_body(text: str) -> str:
    """The CREATE OR REPLACE VIEW statement, comments removed."""
    code = code_of(text)
    match = re.search(
        r"CREATE\s+OR\s+REPLACE\s+VIEW\s+resolution\.work_request_backlog\s+AS(.*?);",
        code,
        re.IGNORECASE | re.DOTALL,
    )
    assert match, "the backlog projection is missing from the migration"
    return match.group(1)


# ── hermetic guards ─────────────────────────────────────────────────────────

def test_migration_is_additive():
    assert destructive_statements(V219.read_text(encoding="utf-8")) == []


def test_guard_bites_on_a_destructive_fixture():
    """The scan must fail on the things it claims to catch."""
    body = "BEGIN;\n{};\nCOMMIT;\n"
    for destructive in (
        "DROP TABLE resolution.request_kind_registry",
        "TRUNCATE resolution.request_role_eligibility",
        "DELETE FROM resolution.work_request",
        "ALTER TABLE resolution.work_request ALTER COLUMN request_kind TYPE integer",
        "ALTER TABLE resolution.work_request RENAME TO work_request_old",
        "ALTER TABLE resolution.work_request ALTER COLUMN request_kind SET NOT NULL",
    ):
        assert destructive_statements(body.format(destructive)), destructive

    benign = body.format(
        "ALTER TABLE resolution.work_request "
        "ADD COLUMN IF NOT EXISTS request_kind text"
    )
    assert destructive_statements(benign) == []

    cascade = body.format(
        "CREATE TABLE resolution.request_role_eligibility (\n"
        "  request_id uuid NOT NULL REFERENCES resolution.work_request (id) ON DELETE CASCADE\n"
        ")"
    )
    assert destructive_statements(cascade) == [], "ON DELETE CASCADE must remain permitted"


def test_version_is_v219_not_the_collided_v216():
    """D1: V216 is applied history; this artifact must not claim that slot."""
    assert V219.is_file(), f"missing {V219}"
    code = code_of(V219.read_text(encoding="utf-8"))
    assert "V216" not in code, "the migration still carries the collided V216 version"
    assert "V219" in V219.name


def test_no_hard_coded_cardinality():
    """D2: the postcheck must not assert a row count that time can falsify."""
    code = code_of(V219.read_text(encoding="utf-8"))
    assert not re.search(r"\b4[0-9]\b", code), (
        "executable SQL hard-codes a row count; compare counts to each other instead"
    )
    assert "attested = total" in V219.read_text(encoding="utf-8")


def test_kind_axis_ships_nullable_without_a_default():
    code = code_of(V219.read_text(encoding="utf-8"))
    assert re.search(
        r"ADD COLUMN IF NOT EXISTS request_kind text;", code, re.IGNORECASE
    ), "the column must be added as bare nullable text"
    assert not re.search(r"request_kind\s+text\s+DEFAULT", code, re.IGNORECASE), (
        "a default would make a forgotten write indistinguishable from an attested one"
    )


def test_backlog_projection_has_no_request_kind_predicate():
    body = view_body(V219.read_text(encoding="utf-8"))
    assert "request_kind" not in body, (
        "the backlog projection became kind-specific; it must stay kind-agnostic"
    )
    assert "business_status" in body and "consumed_at" in body


def test_kind_and_lifecycle_are_never_coupled_by_a_constraint():
    code = code_of(V219.read_text(encoding="utf-8"))
    for statement in ALTER_STMT.findall(code):
        flattened = re.sub(r"\s+", " ", statement)
        if "request_kind" in flattened and "business_status" in flattened:
            pytest.fail(f"constraint couples the two axes: {flattened[:160]}")


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
        input=script, env=ENV, capture_output=True, text=True, timeout=90,
    )


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
        "indexes": int(psql(db, "select count(*) from pg_indexes"
                                 " where schemaname='resolution'")),
    }


@pytest.fixture(scope="module")
def e2edb():
    db = f"e2e_req_kind_v219_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    assert subprocess.run(["createdb", "-w", db], env=ENV, capture_output=True,
                          stdin=subprocess.DEVNULL).returncode == 0, \
        "cannot create throwaway DB"
    seeded = apply_script(db, SKELETON)
    assert seeded.returncode == 0, f"skeleton failed: {seeded.stderr}"
    try:
        yield db
    finally:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def migrated(e2edb):
    """A database with the migration committed once."""
    body = without_transaction(V219.read_text(encoding="utf-8"))
    result = apply_script(e2edb, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert result.returncode == 0, f"apply failed: {result.stderr}"
    return e2edb


# ── apply / idempotence / rollback ─────────────────────────────────────────

def test_double_apply_is_idempotent_and_rollback_is_clean(e2edb):
    before = catalog(e2edb)
    body = without_transaction(V219.read_text(encoding="utf-8"))

    rolled = apply_script(e2edb, f"BEGIN;\n{body}\n{body}\nROLLBACK;\n")
    assert rolled.returncode == 0, f"double apply failed: {rolled.stderr}"

    after = catalog(e2edb)
    assert after == before, f"rollback left objects behind: {before} -> {after}"


def test_committed_apply_creates_the_objects_and_seeds_the_registry(migrated):
    assert psql(migrated, "select to_regclass('resolution.request_kind_registry')::text") \
        == "resolution.request_kind_registry"
    assert psql(migrated, "select to_regclass('resolution.request_role_eligibility')::text") \
        == "resolution.request_role_eligibility"
    assert psql(migrated, "select to_regclass('resolution.work_request_backlog')::text") \
        == "resolution.work_request_backlog"
    assert psql(migrated, """
        select count(*) from resolution.request_kind_registry
         where request_kind in ('build','attestation','decision')
    """) == "3"


def test_backfill_is_cardinality_independent(migrated):
    """D2: whatever the table holds is attested — five rows, not a magic 46."""
    total, null_kind, attested, unbacked = psql(migrated, """
        select
          (select count(*) from resolution.work_request),
          (select count(*) from resolution.work_request where request_kind is null),
          (select count(*) from resolution.work_request
            where context ? 'legacy_request_kind_basis'),
          (select count(*) from resolution.work_request wr
            where not exists (select 1 from resolution.request_kind_registry r
                               where r.request_kind = wr.request_kind))
    """).split("|")
    assert total == "5"
    assert null_kind == "0", "some rows were left without a kind"
    assert attested == total, "not every pre-existing row carries its attested basis"
    assert unbacked == "0"
    assert psql(migrated, """
        select count(*) from resolution.work_request
         where context->>'legacy_request_kind_basis' <> 'v219:legacy-execution-store'
    """) == "0", "the basis key still names the superseded V216 slot"


def test_second_committed_apply_is_a_no_op(migrated):
    body = without_transaction(V219.read_text(encoding="utf-8"))
    again = apply_script(migrated, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert again.returncode == 0, f"re-apply failed: {again.stderr}"
    assert psql(migrated, "select count(*) from resolution.request_kind_registry") == "3"
    assert psql(migrated, "select count(*) from resolution.work_request") == "5"


def test_registry_fk_refuses_an_unknown_kind(migrated):
    message = psql(migrated, """
        insert into resolution.work_request (plan_id, business_status, request_kind)
        values (99, 'DRAFT', 'not-a-kind')
    """, expect_fail=True)
    assert "fk_work_request_request_kind" in message or "foreign key" in message


def test_backlog_is_empty_until_eligibility_exists_then_kind_agnostic(migrated):
    assert psql(migrated, "select count(*) from resolution.work_request_backlog") == "0"

    ids = psql(migrated, "select id from resolution.work_request order by plan_id limit 2").split()
    assert len(ids) == 2
    # one row per kind, to prove the projection has no kind predicate
    psql(migrated, f"update resolution.work_request set request_kind='decision'"
                   f" where id='{ids[0]}'")
    psql(migrated, f"update resolution.work_request set request_kind='build'"
                   f" where id='{ids[1]}'")
    psql(migrated, f"""
        insert into resolution.request_role_eligibility (request_id, role)
        values ('{ids[0]}', 'engineer'), ('{ids[1]}', 'engineer')
    """)
    kinds = psql(migrated, """
        select request_kind from resolution.work_request_backlog order by request_kind
    """).split()
    assert kinds == ["build", "decision"], (
        f"kind-agnostic backlog should surface both kinds, got {kinds}"
    )
    assert psql(migrated, """
        select eligible_roles::text from resolution.work_request_backlog limit 1
    """) == "{engineer}"
