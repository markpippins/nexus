#!/usr/bin/env python3
"""Guards for V222 — the P4a port of `nebula.requirements` into the canonical table.

Operator ruling P4a: `resolution.requirement` becomes the canonical Requirement
surface by PORTING the 27 legacy rows out of `nebula.requirements` /
`nebula.requirements_history`, instead of retargeting the V220 link substrate at
`nebula`.

These tests pin the promises the migration makes:

  * identity is preserved by value — ids, lifecycle, hierarchy, governance refs,
    business timestamps and the bitemporal window survive the port;
  * the port proves itself — one ledger row per requirement with equal source
    and target digests, and a CHECK that source_id = target_id;
  * the port refuses to be partial or lossy — vocabulary, drift, identity and
    completeness are preflight/postcondition failures, not silent coercion;
  * existing references keep resolving (live blueprint `requirement_ref`s);
  * `nebula.requirements` stays coherent for consumers that have not been
    repointed yet: same 22 columns, read-through, and write-through to the
    canonical table (including derived `source_type` and the retired
    `work_request_dco` column);
  * re-running is a no-op; a rolled-back apply leaves the legacy surface intact.

The DB half runs against a throwaway PostgreSQL database, never live.

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

V222 = REPO_ROOT / "sql" / "V222__port_requirements_to_resolution.sql"
NEBULA_ROUTES = REPO_ROOT / "typescript" / "nebula-srv" / "src" / "routes.ts"
PROMOTE = REPO_ROOT / "python" / "tackle" / "intent_requirement_promote.py"

ENV = {
    **os.environ,
    "PGHOST": os.environ.get("PGHOST", "localhost"),
    "PGPORT": os.environ.get("PGPORT", "5432"),
    "PGUSER": os.environ.get("PGUSER", "pguser"),
}
PSQL = ("psql", "-w")

# Legacy requirement ids. R1/R3 are referenced by live blueprints in the
# fixture, mirroring the 4 real blueprints whose `requirement_ref` must keep
# resolving after the port.
R1 = "804b3f87-fe84-4167-b5a7-15eaa35c2d4c"
R2 = "c805badc-9016-4454-be70-e6868207fe71"
R3 = "5fcf5826-b7ad-48bd-900a-c8ed54b95970"
CAND = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaa1"
ASSET = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbb2"
SYS = "99999999-9999-4999-8999-999999999991"
SUB = "88888888-8888-4888-8888-888888888881"

B1 = "cccccccc-3333-4333-8333-ccccccccccc1"
B2 = "cccccccc-3333-4333-8333-ccccccccccc2"
B3 = "cccccccc-3333-4333-8333-ccccccccccc3"

COMPAT_COLUMNS = [
    "id",
    "system_id",
    "subsystem_id",
    "feature_id",
    "title",
    "description",
    "status",
    "priority",
    "start_date",
    "completion_date",
    "created_at",
    "recorded_on_dt",
    "recorded_until_dt",
    "valid_from",
    "valid_until",
    "parent_id",
    "req_type",
    "acceptance_criteria",
    "candidate_id",
    "conduit_plan_id",
    "work_request_dco",
    "asset_id",
]


# ── helpers ────────────────────────────────────────────────────────────────

def executable_sql() -> str:
    """The migration with comments and the transaction wrapper removed.

    The trailing ROLLBACK section is a comment block that legitimately names
    DELETE/TRUNCATE, so it is cut before the static checks.
    """
    text = V222.read_text(encoding="utf-8")
    head = text.split("-- ROLLBACK (rehearsal only", 1)[0]
    lines = [line for line in head.splitlines() if not line.lstrip().startswith("--")]
    body = "\n".join(lines)
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
    # -At still emits the command tag for DML; drop it so RETURNING callers get
    # a scalar rather than "<uuid>\nINSERT 0 1".
    lines = [
        line for line in result.stdout.strip().splitlines()
        if not re.fullmatch(r"(INSERT|UPDATE|DELETE) 0 \d+", line.strip())
    ]
    return "\n".join(lines).strip()


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
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA resolution;
CREATE SCHEMA nebula;

-- ── canonical target (mirrors live DDL) ───────────────────────────────────
CREATE TABLE resolution.requirement (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id             uuid,
    candidate_id         uuid,
    parent_id            uuid REFERENCES resolution.requirement (id),
    source_type          text NOT NULL,
    system_id            uuid,
    subsystem_id         uuid,
    feature_id           uuid,
    title                text NOT NULL,
    description          text NOT NULL DEFAULT '',
    status               text NOT NULL DEFAULT 'Backlog',
    priority             text NOT NULL DEFAULT 'Medium',
    req_type             text,
    compilation_status   text NOT NULL DEFAULT 'draft',
    sol_ir_expression_id uuid,
    start_date           text,
    completion_date      text,
    acceptance_criteria  jsonb DEFAULT '[]'::jsonb,
    conduit_plan_id      varchar(32),
    created_at           timestamptz NOT NULL DEFAULT now(),
    valid_from           timestamptz NOT NULL DEFAULT now(),
    valid_until          timestamptz NOT NULL DEFAULT 'infinity',
    recorded_on_dt       timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt    timestamptz NOT NULL DEFAULT 'infinity',
    CONSTRAINT requirement_source_type_check CHECK (source_type IN ('candidate', 'manual')),
    CONSTRAINT requirement_check CHECK (source_type = 'candidate' OR candidate_id IS NULL),
    CONSTRAINT requirement_status_check CHECK (status IN
        ('Backlog','ToDo','InProgress','Active','Blocked','Done','Cancelled','Accepted')),
    CONSTRAINT requirement_priority_check CHECK (priority IN ('Low','Medium','High')),
    CONSTRAINT requirement_req_type_check CHECK (req_type IS NULL OR req_type IN
        ('Epic','Story','Task','Bug')),
    CONSTRAINT requirement_compilation_status_check CHECK (compilation_status IN
        ('draft','compiled','rejected'))
);

CREATE TABLE resolution.work_request (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);

-- The candidate store the canonical FK points at. Live it is EMPTY while the
-- application's candidates live in nebula.harvest_candidates, which is what
-- makes requirement_candidate_id_fkey unsatisfiable for real links.
CREATE TABLE resolution.candidate (
    id uuid PRIMARY KEY
);

CREATE TABLE resolution.canonical_asset (
    id uuid PRIMARY KEY
);

CREATE TABLE resolution.expression (
    id uuid PRIMARY KEY
);

ALTER TABLE resolution.requirement
    ADD CONSTRAINT requirement_asset_id_fkey
        FOREIGN KEY (asset_id) REFERENCES resolution.canonical_asset (id),
    ADD CONSTRAINT requirement_candidate_id_fkey
        FOREIGN KEY (candidate_id) REFERENCES resolution.candidate (id),
    ADD CONSTRAINT requirement_sol_ir_expression_id_fkey
        FOREIGN KEY (sol_ir_expression_id) REFERENCES resolution.expression (id);

CREATE TABLE resolution.specification (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agenda_id uuid NOT NULL,
    requirement_id uuid NOT NULL REFERENCES resolution.requirement (id)
);

CREATE TABLE resolution.implementation_plan (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    requirement_id uuid REFERENCES resolution.requirement (id)
);

CREATE TABLE resolution.requirement_segment_set (
    requirement_id uuid NOT NULL REFERENCES resolution.requirement (id),
    segment_set_id uuid NOT NULL
);

CREATE TABLE resolution.request_requirement_link (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL REFERENCES resolution.work_request (id),
    requirement_id uuid NOT NULL REFERENCES resolution.requirement (id) ON DELETE RESTRICT,
    relation text NOT NULL,
    provenance text NOT NULL
);

CREATE TABLE resolution.migration_ledger (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    schema_name text NOT NULL,
    migration_label text NOT NULL,
    description text,
    applied_by text NOT NULL DEFAULT CURRENT_USER,
    applied_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (schema_name, migration_label)
);

-- ── legacy source (mirrors live DDL) ──────────────────────────────────────
CREATE TABLE nebula.requirements_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id uuid NOT NULL,
    subsystem_id uuid,
    feature_id uuid,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'Backlog',
    priority text NOT NULL DEFAULT 'Medium',
    start_date text,
    completion_date text,
    created_at timestamptz NOT NULL DEFAULT now(),
    recorded_on_dt timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    parent_id uuid,
    req_type text,
    acceptance_criteria jsonb DEFAULT '[]'::jsonb,
    candidate_id uuid,
    conduit_plan_id varchar(32),
    work_request_dco jsonb,
    asset_id uuid
);

CREATE VIEW nebula.requirements AS
SELECT id, system_id, subsystem_id, feature_id, title, description, status,
       priority, start_date, completion_date, created_at, recorded_on_dt,
       recorded_until_dt, valid_from, valid_until, parent_id, req_type,
       acceptance_criteria, candidate_id, conduit_plan_id, work_request_dco,
       asset_id
  FROM nebula.requirements_history
 WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
   AND now() >= valid_from AND now() < valid_until;

-- A live dependent view (V221's read projection) must survive the replacement
-- of nebula.requirements, because CREATE OR REPLACE keeps the column contract.
CREATE VIEW nebula.v_work_request_requirement AS
SELECT rrl.request_id,
       rrl.requirement_id,
       rrl.relation,
       rrl.provenance,
       req.system_id,
       req.subsystem_id
  FROM resolution.request_requirement_link rrl
  JOIN nebula.requirements req ON req.id = rrl.requirement_id;

CREATE TABLE nebula.harvest_candidates (
    id uuid PRIMARY KEY
);

INSERT INTO nebula.harvest_candidates (id) VALUES ('{CAND}');
INSERT INTO resolution.canonical_asset (id) VALUES ('{ASSET}');

CREATE TABLE nebula.blueprints_history (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_number text,
    title text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    blueprint_status text NOT NULL DEFAULT 'draft',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_until timestamptz NOT NULL DEFAULT 'infinity',
    recorded_on_dt timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'
);

-- ── fixtures: three requirements covering the representation decisions ────
INSERT INTO nebula.requirements_history
  (id, system_id, subsystem_id, feature_id, title, description, status, priority,
   req_type, start_date, completion_date, created_at, recorded_on_dt,
   valid_from, valid_until, candidate_id, conduit_plan_id, acceptance_criteria, asset_id)
VALUES
  ('{R1}', '{SYS}', '{SUB}', NULL,
   'Verify and close the cascade-bus lifecycle emitter', 'first', 'ToDo', 'High',
   'Story', '2026-09-01', '2026-09-09', '2026-09-09 10:00:00+00',
   '2026-09-09 10:00:00+00', '2026-09-01 00:00:00+00',
   '9999-12-31 23:59:59+00', NULL, '8200001',
   '["emits on ingest","no duplicate on replay"]'::jsonb, '{ASSET}'),
  ('{R2}', '{SYS}', NULL, NULL,
   'Re-point the T24 proposition backfill', 'second', 'Backlog', 'Medium',
   NULL, NULL, NULL, '2026-09-08 12:00:00+00', '2026-09-08 12:00:00+00',
   '2026-09-08 12:00:00+00', '9999-12-31 23:59:59+00', '{CAND}', NULL,
   '[]'::jsonb, NULL),
  ('{R3}', '{SYS}', '{SUB}', NULL,
   'Wire coverage report generation into vanadium-sonar', 'third', 'InProgress', 'Medium',
   'Epic', NULL, NULL, '2026-09-10 08:00:00+00', '2026-09-10 08:00:00+00',
   '2026-09-10 08:00:00+00', '9999-12-31 23:59:59+00', NULL, NULL,
   '[]'::jsonb, NULL);

-- R2 is a child of R1: hierarchy must survive the port.
UPDATE nebula.requirements_history SET parent_id = '{R1}' WHERE id = '{R2}';

-- Two blueprints carry a requirement_ref that must keep resolving, one is bare.
INSERT INTO nebula.blueprints_history (id, plan_number, title, payload)
VALUES
  ('{B1}', '8200001', 'Blueprint on R1', '{{"requirement_ref": "{R1}", "spec_ref": null}}'::jsonb),
  ('{B2}', '8200002', 'Blueprint on R3', '{{"requirement_ref": "{R3}", "spec_ref": null}}'::jsonb),
  ('{B3}', '8200003', 'Blueprint without a requirement', '{{"requirement_ref": null}}'::jsonb);
"""


def apply_migration(db: str, expect_fail: bool = False) -> str:
    script = f"BEGIN;\n{executable_sql()}\nCOMMIT;\n"
    return apply_script(db, script, expect_fail=expect_fail)


@pytest.fixture(scope="module")
def ported():
    db = new_db("e2e_p4a_v222")
    apply_script(db, SKELETON)
    try:
        apply_migration(db)
        yield db
    finally:
        drop_db(db)


# ── static guards ─────────────────────────────────────────────────────────

def test_migration_is_additive_and_keeps_the_legacy_store():
    code = executable_sql()
    assert not re.search(r"\b(TRUNCATE|DROP\s+TABLE)\b", code, re.IGNORECASE)
    assert not re.search(r"DROP\s+COLUMN", code, re.IGNORECASE)

    # The port itself deletes nothing. The single DELETE is the identity-scoped
    # write-through branch for DELETE on the compatibility view (so a legacy
    # caller's delete still means what it meant), not a port-time wipe.
    deletes = re.findall(r"DELETE\s+FROM\s+[A-Za-z_.]+", code, re.IGNORECASE)
    assert deletes == ["DELETE FROM resolution.requirement"], deletes
    assert "WHERE id = OLD.id" in code
    # The legacy bitemporal store is the pre-port audit record.
    assert "nebula.requirements_history" in code
    assert "resolution.requirement_port_ledger" in code
    # Identity, not a fresh key.
    assert re.search(r"ON\s+CONFLICT\s*\(id\)\s+DO\s+NOTHING", code, re.IGNORECASE)


def test_migration_refuses_partial_and_unrepresentable_ports():
    code = executable_sql()
    for marker in (
        "V222-PREFLIGHT-005",
        "V222-PREFLIGHT-010",
        "V222-PREFLIGHT-011",
        "V222-PREFLIGHT-012",
        "V222-PREFLIGHT-013",
        "V222-PREFLIGHT-014",
        "V222-PREFLIGHT-015",
        "V222-PREFLIGHT-016",
        "V222-PREFLIGHT-020",
        "V222-POST-001",
        "V222-POST-002",
        "V222-POST-003",
        "V222-POST-004",
        "V222-POST-005",
    ):
        assert marker in code, marker


def test_ledger_check_enforces_identity_preservation():
    code = executable_sql()
    assert re.search(
        r"CONSTRAINT\s+requirement_port_ledger_identity_check\s+CHECK\s*\(\s*source_id\s*=\s*target_id\s*\)",
        code,
        re.IGNORECASE,
    )


def test_nebula_srv_targets_the_transition_surface_not_the_unported_table():
    """Ordering guard.

    V222 is operator-gated, so until it is applied `resolution.requirement` is
    empty. A service that reads the base table directly would serve zero
    requirements in that window. The service therefore targets
    `nebula.requirements`, the relation V222 re-points at the canonical table —
    the same statement is correct before and after the apply.
    """
    text = NEBULA_ROUTES.read_text(encoding="utf-8")
    # Comments explain why the base table is not queried yet; check code only.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )
    assert "resolution.requirement" not in code
    assert "nebula.requirements" in code
    # The frozen legacy store is not a live read surface.
    assert "FROM nebula.requirements_history" not in code


def test_direct_producer_writes_the_transition_surface():
    text = PROMOTE.read_text(encoding="utf-8")
    assert "INSERT INTO nebula.requirements\n" in text
    assert "INSERT INTO nebula.requirements_history" not in text
    assert "INSERT INTO resolution.requirement" not in text


# Runtime roots that hold live services, jobs and scripts. Migrations, the CI
# bootstrap, SQL archives, docs and tests are excluded: they are history, not a
# live read path.
RUNTIME_ROOTS = ("typescript", "python", "adonisjs", "moleculer", "bin", "scripts")
SKIP_PARTS = (
    "node_modules", ".git", "dist", "build", "migrations", "__tests__",
    "/tests/", ".test.", ".spec.", ".md",
)
# A live consumer must name the transition surface; naming the frozen store is
# how a reader silently starts serving stale rows (or a writer silently stops
# being visible to the application).
FROZEN_REF = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM|TRUNCATE)\s+(?:nebula\.)?requirements_history\b",
    re.IGNORECASE,
)


def test_no_runtime_consumer_reads_or_writes_the_frozen_store():
    repo = Path(__file__).resolve().parent.parent.parent
    offenders = []
    for root in RUNTIME_ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".ts", ".js", ".py", ".sh"}:
                continue
            posix = "/" + path.relative_to(repo).as_posix()
            if any(part in posix for part in SKIP_PARTS):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if FROZEN_REF.search(text):
                offenders.append(posix)
    assert offenders == [], (
        "runtime code still reads or writes nebula.requirements_history, which "
        "V222 freezes; target nebula.requirements instead: " + ", ".join(offenders)
    )


# ── the port ──────────────────────────────────────────────────────────────

def test_port_preserves_ids_lifecycle_hierarchy_and_refs(ported):
    assert psql(ported, "select count(*) from resolution.requirement") == "3"
    assert psql(ported, f"""
        select status || '|' || priority || '|' || coalesce(req_type, '-') || '|' ||
               coalesce(conduit_plan_id, '-') || '|' || coalesce(subsystem_id::text, '-')
          from resolution.requirement where id = '{R1}'
    """) == f"ToDo|High|Story|8200001|{SUB}"
    # Hierarchy survives.
    assert psql(ported, f"select parent_id::text from resolution.requirement where id = '{R2}'") == R1
    # Governance + business metadata survive.
    assert psql(ported, f"""
        select coalesce(candidate_id::text,'-') || '|' || coalesce(asset_id::text,'-') || '|' ||
               acceptance_criteria::text
          from resolution.requirement where id = '{R2}'
    """) == f"{CAND}|-|[]"
    assert psql(ported, f"select asset_id::text from resolution.requirement where id = '{R1}'") == ASSET


def test_port_preserves_timestamps_and_bitemporal_window(ported):
    row = psql(ported, f"""
        select created_at at time zone 'UTC',
               valid_from at time zone 'UTC',
               valid_until at time zone 'UTC',
               recorded_on_dt at time zone 'UTC',
               recorded_until_dt
          from resolution.requirement where id = '{R1}'
    """)
    created, valid_from, valid_until, recorded_on, recorded_until = row.split("|")
    assert created.startswith("2026-09-09 10:00:00")
    assert valid_from.startswith("2026-09-01 00:00:00")
    assert recorded_on.startswith("2026-09-09 10:00:00")
    # Canonical stores open-ended segments with its own marker, on both axes.
    assert valid_until == "infinity"
    assert recorded_until == "infinity"


def test_projection_presents_the_legacy_open_segment_sentinels(ported):
    """Consumers compare these literals, so the projection must emit them.

    All 27 live rows carry '9999-12-31 23:59:59+00' on both open axes. A view
    that leaked the canonical 'infinity' would silently change every such
    comparison from true to false.
    """
    row = psql(ported, f"""
        select valid_until at time zone 'UTC', recorded_until_dt at time zone 'UTC'
          from nebula.requirements where id = '{R1}'
    """)
    assert row == "9999-12-31 23:59:59|9999-12-31 23:59:59"


def test_projection_keeps_the_legacy_current_segment_visibility(ported):
    """The pre-port view hid closed recordings and expired business windows."""
    psql(ported, f"""
        insert into resolution.requirement
            (id, source_type, system_id, title, status, recorded_until_dt)
        values ('77777777-7777-4777-8777-777777777777', 'manual', '{SYS}',
                'closed recording', 'Backlog', '2026-01-01 00:00:00+00')
    """)
    assert psql(ported, """
        select count(*) from resolution.requirement where title = 'closed recording'
    """) == "1"
    assert psql(ported, """
        select count(*) from nebula.requirements where title = 'closed recording'
    """) == "0"


def test_source_type_is_derived_without_losing_candidate_links(ported):
    assert psql(ported, f"select source_type from resolution.requirement where id = '{R1}'") == "manual"
    assert psql(ported, f"select source_type from resolution.requirement where id = '{R2}'") == "candidate"
    # The target CHECK would reject candidate_id with a non-candidate source_type.
    assert psql(ported, f"""
        select count(*) from resolution.requirement
         where candidate_id is not null and source_type <> 'candidate'
    """) == "0"


def test_candidate_link_is_preserved_while_the_unsatisfiable_fk_is_resolved(ported):
    # Identity: the legacy candidate id is still on the canonical row.
    assert psql(ported, f"select candidate_id::text from resolution.requirement where id = '{R2}'") == CAND

    # `resolution.candidate` is empty while every link names a harvest candidate,
    # so requirement_candidate_id_fkey can never be satisfied. It is resolved,
    # not worked around with a NULLed column or fabricated candidate rows.
    assert psql(ported, """
        select count(*) from pg_constraint
         where conrelid = 'resolution.requirement'::regclass
           and conname = 'requirement_candidate_id_fkey'
    """) == "0"
    assert psql(ported, """
        select count(*) from pg_constraint
         where conrelid = 'resolution.requirement'::regclass
           and conname in ('requirement_asset_id_fkey', 'requirement_parent_id_fkey')
    """) == "2"

    comment = psql(ported, """
        select col_description('resolution.requirement'::regclass,
               (select attnum from pg_attribute
                 where attrelid = 'resolution.requirement'::regclass
                   and attname = 'candidate_id'))
    """)
    assert "cross-domain" in comment


def test_candidate_preflight_refuses_when_the_canonical_store_is_populated():
    db = new_db("e2e_p4a_v222_cand")
    try:
        apply_script(db, SKELETON)
        # A populated canonical candidate store means a missing link is a real
        # identity break, not a cross-domain reference.
        psql(db, "insert into resolution.candidate (id) values (gen_random_uuid())")
        message = apply_migration(db, expect_fail=True)
        assert "V222-PREFLIGHT-016" in message
        assert psql(db, "select count(*) from resolution.requirement") == "0"
        assert psql(db, """
            select count(*) from pg_constraint
             where conrelid = 'resolution.requirement'::regclass
               and conname = 'requirement_candidate_id_fkey'
        """) == "1"
    finally:
        drop_db(db)


def test_ledger_proves_identity_row_by_row(ported):
    assert psql(ported, f"select count(*) from resolution.requirement_port_ledger") == "3"
    assert psql(ported, f"""
        select count(*) from resolution.requirement_port_ledger
         where source_id <> target_id or source_digest <> target_digest
    """) == "0"
    assert psql(ported, f"""
        select count(*) from resolution.requirement_port_ledger
         where source_relation = 'nebula.requirements' and provenance = 'v222:port-p4a'
    """) == "3"
    # Every live blueprint reference still resolves against the canonical table.
    assert psql(ported, """
        select count(*) from nebula.blueprints_history b
          join resolution.requirement r
            on r.id = (b.payload ->> 'requirement_ref')::uuid
    """) == "2"


def test_dependent_read_view_survives_and_now_reads_canonical_rows(ported):
    # The projection was replaced underneath V221's view; it must still compile
    # and must now resolve through the canonical table.
    assert psql(ported, "select count(*) from nebula.v_work_request_requirement") == "0"

    request_id = psql(ported, "insert into resolution.work_request default values returning id")
    psql(ported, f"""
        insert into resolution.request_requirement_link
          (request_id, requirement_id, relation, provenance)
        values ('{request_id}', '{R1}', 'source', 'test:v222')
    """)
    assert psql(ported, f"""
        select system_id::text from nebula.v_work_request_requirement
         where request_id = '{request_id}'
    """) == SYS


def test_ledger_row_is_written_for_the_migration_itself(ported):
    assert psql(ported, """
        select count(*) from resolution.migration_ledger
         where migration_label = 'V222__port_requirements_to_resolution'
    """) == "1"


# ── compatibility projection ──────────────────────────────────────────────

def test_compatibility_view_keeps_the_legacy_column_contract(ported):
    cols = psql(ported, """
        select string_agg(column_name, ',' order by ordinal_position)
          from information_schema.columns
         where table_schema = 'nebula' and table_name = 'requirements'
    """)
    assert cols == ",".join(COMPAT_COLUMNS)
    assert psql(ported, "select count(*) from nebula.requirements") == "3"
    assert psql(ported, f"select work_request_dco from nebula.requirements where id = '{R1}'") == ""


def test_writes_through_the_view_land_on_the_canonical_table(ported):
    new_id = psql(ported, f"""
        insert into nebula.requirements (system_id, title)
        values ('{SYS}', 'compat insert')
        returning id
    """)
    assert uuid.UUID(new_id)
    assert psql(ported, f"""
        select status || '|' || priority || '|' || source_type || '|' || description
          from resolution.requirement where id = '{new_id}'
    """) == "Backlog|Medium|manual|"
    assert psql(ported, f"select count(*) from nebula.requirements where id = '{new_id}'") == "1"

    psql(ported, f"update nebula.requirements set status = 'Done' where id = '{new_id}'")
    assert psql(ported, f"select status from resolution.requirement where id = '{new_id}'") == "Done"

    # A candidate-derived insert through the view gets the candidate source_type.
    cand_req = psql(ported, f"""
        insert into nebula.requirements (system_id, title, candidate_id)
        values ('{SYS}', 'compat candidate insert', '{CAND}')
        returning id
    """)
    assert psql(ported, f"select source_type from resolution.requirement where id = '{cand_req}'") == "candidate"

    psql(ported, f"delete from nebula.requirements where id = '{new_id}'")
    assert psql(ported, f"select count(*) from resolution.requirement where id = '{new_id}'") == "0"


def test_legacy_dco_column_is_carried_and_round_trips(ported):
    """`work_request_dco` has a live writer, so it must not be dropped.

    bin/compiler_processInProgress.py writes the compiled DCO into this column and
    gates recompilation on it being non-null. Losing it would make every
    requirement look uncompiled and hide the DCO from the application.
    """
    psql(ported, f"""
        insert into nebula.requirements (system_id, title, work_request_dco)
        values ('{SYS}', 'dco write', '{{"x":1}}'::jsonb)
    """)
    assert psql(ported, """
        select work_request_dco::text from resolution.requirement where title = 'dco write'
    """) == '{"x": 1}'
    assert psql(ported, """
        select work_request_dco::text from nebula.requirements where title = 'dco write'
    """) == '{"x": 1}'
    # And an update through the projection reaches the canonical row too.
    psql(ported, """
        update nebula.requirements set work_request_dco = '{"y":2}'::jsonb
         where title = 'dco write'
    """)
    assert psql(ported, """
        select work_request_dco::text from resolution.requirement where title = 'dco write'
    """) == '{"y": 2}'


def test_canonical_gains_the_legacy_dco_column_idempotently(ported):
    assert psql(ported, """
        select count(*) from information_schema.columns
         where table_schema = 'resolution' and table_name = 'requirement'
           and column_name = 'work_request_dco' and data_type = 'jsonb'
    """) == "1"
    # Re-applying must not fail on the ADD COLUMN IF NOT EXISTS.
    apply_migration(ported)
    assert psql(ported, """
        select count(*) from resolution.requirement where title = 'dco write'
    """) == "1"


# ── idempotency and refusals ──────────────────────────────────────────────

def test_second_apply_is_a_no_op():
    db = new_db("e2e_p4a_v222_idem")
    try:
        apply_script(db, SKELETON)
        apply_migration(db)
        before = psql(db, "select count(*) || '|' || (select count(*) from resolution.requirement_port_ledger) from resolution.requirement")
        apply_migration(db)
        after = psql(db, "select count(*) || '|' || (select count(*) from resolution.requirement_port_ledger) from resolution.requirement")
        assert before == after == "3|3"
        assert psql(db, "select count(*) from resolution.migration_ledger") == "1"
    finally:
        drop_db(db)


def test_reapplying_after_the_application_has_written_is_still_a_no_op():
    """The ledger is port-time proof, so live edits must not look like drift.

    A re-apply that re-litigated the ledger would either fail on a legitimate
    status move or rewrite the proof it is supposed to keep. The operator needs
    re-apply to be safe after go-live (and after restoring a pre-V222 dump).
    """
    db = new_db("e2e_p4a_v222_idem_edits")
    try:
        apply_script(db, SKELETON)
        apply_migration(db)
        ported_at = psql(db, "select min(ported_at)::text from resolution.requirement_port_ledger")

        psql(db, "update nebula.requirements set status = 'Done' where status = 'Backlog'")
        psql(db, """
            update nebula.requirements set work_request_dco = '{"a":1}'::jsonb
             where id = (select id from nebula.requirements order by created_at limit 1)
        """)
        done_before = psql(db, "select count(*) from nebula.requirements where status = 'Done'")
        assert done_before != "0"

        apply_migration(db)  # must not raise
        assert psql(db, """
            select count(*) || '|' || (select count(*) from resolution.requirement_port_ledger)
              from resolution.requirement
        """) == "3|3"
        # The proof keeps its port-time timestamp and the edits survive untouched.
        assert psql(db, "select min(ported_at)::text from resolution.requirement_port_ledger") == ported_at
        assert psql(db, "select count(*) from nebula.requirements where status = 'Done'") == done_before
        assert psql(db, "select count(*) from resolution.requirement where work_request_dco is not null") == "1"
    finally:
        drop_db(db)


ROLLBACK_SQL = """
BEGIN;
CREATE TEMP TABLE _v222_rb ON COMMIT DROP AS
SELECT source_id FROM resolution.requirement_port_ledger
 WHERE provenance = 'v222:port-p4a';
CREATE OR REPLACE VIEW nebula.requirements AS
SELECT id, system_id, subsystem_id, feature_id, title, description, status,
       priority, start_date, completion_date, created_at, recorded_on_dt,
       recorded_until_dt, valid_from, valid_until, parent_id, req_type,
       acceptance_criteria, candidate_id, conduit_plan_id, work_request_dco,
       asset_id
  FROM nebula.requirements_history
 WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
   AND now() >= valid_from AND now() < valid_until;
DROP TRIGGER IF EXISTS trg_requirements_compat_write ON nebula.requirements;
DROP TABLE resolution.requirement_port_ledger;
DELETE FROM resolution.requirement
 WHERE id IN (SELECT source_id FROM _v222_rb);
ALTER TABLE resolution.requirement DROP COLUMN IF EXISTS work_request_dco;
DELETE FROM resolution.migration_ledger
 WHERE migration_label = 'V222__port_requirements_to_resolution';
COMMIT;
"""


def test_documented_rollback_restores_the_pre_port_surface():
    """The operator rollback in V222's footer must actually run.

    The legacy history table is never modified, so reversing the port has to
    leave the pre-port view in place with the original rows, and nothing of the
    port behind. Exercised here rather than asserted in a comment.
    """
    db = new_db("e2e_p4a_v222_rollback")
    try:
        apply_script(db, SKELETON)
        apply_migration(db)
        statuses_before = psql(db, "select string_agg(status, ',' order by status) from nebula.requirements_history")

        psql(db, ROLLBACK_SQL)

        assert psql(db, "select count(*) from nebula.requirements") == "3"
        assert psql(db, "select count(*) from resolution.requirement") == "0"
        assert psql(db, """
            select count(*) from information_schema.tables
             where table_schema = 'resolution' and table_name = 'requirement_port_ledger'
        """) == "0"
        assert psql(db, """
            select count(*) from information_schema.columns
             where table_schema = 'resolution' and table_name = 'requirement'
               and column_name = 'work_request_dco'
        """) == "0"
        assert psql(db, "select count(*) from resolution.migration_ledger") == "0"
        # History was the pre-port audit record and is byte-identical after the
        # round trip; the view serves it again.
        assert psql(db, "select string_agg(status, ',' order by status) from nebula.requirements_history") == statuses_before
        assert psql(db, "select count(*) from pg_trigger where tgname = 'trg_requirements_compat_write'") == "0"
    finally:
        drop_db(db)


def test_preflight_refuses_drift_on_an_existing_target_row():
    db = new_db("e2e_p4a_v222_drift")
    try:
        apply_script(db, SKELETON)
        psql(db, f"""
            insert into resolution.requirement (id, source_type, system_id, title, status)
            values ('{R1}', 'manual', '{SYS}', 'someone already wrote here', 'ToDo')
        """)
        message = apply_migration(db, expect_fail=True)
        assert "V222-PREFLIGHT-020" in message
        assert psql(db, f"select title from resolution.requirement where id = '{R1}'") == "someone already wrote here"
    finally:
        drop_db(db)


def test_preflight_refuses_an_unrepresentable_status():
    db = new_db("e2e_p4a_v222_vocab")
    try:
        apply_script(db, SKELETON)
        psql(db, f"update nebula.requirements_history set status = 'Triage' where id = '{R3}'")
        message = apply_migration(db, expect_fail=True)
        assert "V222-PREFLIGHT-010" in message
        assert psql(db, "select count(*) from resolution.requirement") == "0"
    finally:
        drop_db(db)


def test_preflight_refuses_a_row_without_identity():
    db = new_db("e2e_p4a_v222_identity")
    try:
        apply_script(db, SKELETON)
        # The live legacy column is NOT NULL, so relax it to exercise the
        # migration's own identity guard rather than the table's constraint.
        psql(db, "alter table nebula.requirements_history alter column title drop not null")
        psql(db, f"update nebula.requirements_history set title = NULL where id = '{R3}'")
        message = apply_migration(db, expect_fail=True)
        assert "V222-PREFLIGHT-014" in message
        assert psql(db, "select count(*) from resolution.requirement") == "0"
    finally:
        drop_db(db)


def test_transactional_rollback_leaves_the_legacy_surface_intact():
    db = new_db("e2e_p4a_v222_rollback")
    try:
        apply_script(db, SKELETON)
        apply_script(db, f"BEGIN;\n{executable_sql()}\nROLLBACK;\n")
        assert psql(db, "select count(*) from resolution.requirement") == "0"
        assert psql(db, "select count(*) from nebula.requirements") == "3"
        assert psql(db, """
            select count(*) from pg_trigger
             where tgrelid = 'nebula.requirements'::regclass and not tgisinternal
        """) == "0"
    finally:
        drop_db(db)
