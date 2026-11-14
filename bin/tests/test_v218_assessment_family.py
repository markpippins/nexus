#!/usr/bin/env python3
"""Guards for the staged-inert V218 assessment-family migration.

Ratified by Architect e3f39f9a + f72817f5 (reconciliation) and DBA f170e343.
V218 is NOT applied live; this guard proves it on a throwaway database only.

What is pinned:

  AC1  apply — V218 applies cleanly to the pre-V218 shape in one transaction.
  AC2  guarded — a second apply is refused by the preflight, not silently
       re-run, and a populated assessments_history is refused.
  AC3  view — nebula.assessments exposes all 19 columns after the migration.
       The pre-V218 view has an explicit column list, so this is the check that
       would catch the migration "succeeding" while hiding the new columns.
  AC4  kind shape — an 'assessment' row requires observation_id; an
       'inspection' row requires subject+evidence; inspection rows are admitted
       without an observation.
  AC5  single writer — the partial unique index permits one row per
       agent_record_id and any number of NULLs (D2: index only, no
       role-gated trigger — which is also asserted absent).
  AC6  agent-record link (D1) — agent_record_id must name a row in the CURRENT
       agent_records_history slice; unknown ids and superseded rows are
       refused, current rows are accepted.

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

V218 = REPO_ROOT / "sql" / "V218__assessment_family_and_decision_link.sql"

ENV = {
    **os.environ,
    "PGPASSWORD": os.environ.get("PGPASSWORD", "pgpass"),
    "PGHOST": os.environ.get("PGHOST", "localhost"),
    "PGUSER": os.environ.get("PGUSER", "pguser"),
}

PSQL = ["psql", "-X", "-w", "-h", ENV["PGHOST"], "-U", ENV["PGUSER"]]


# ── the pre-V218 shape, as it exists live ────────────────────────────────────
# Types, nullability, defaults and column ORDER mirror the live tables: the
# migration's preflight compares the view's column list and refuses on drift,
# so a skeleton that does not match live would prove nothing about live.

SKELETON = """
CREATE SCHEMA nebula;
CREATE SCHEMA peb;

CREATE TABLE nebula.agent_records_history (
    id                uuid NOT NULL,
    record_type       text NOT NULL,
    title             text NOT NULL,
    recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT '9999-12-31 00:00:00+00',
    PRIMARY KEY (id, recorded_on_dt)
);

CREATE TABLE nebula.assessments_history (
    id                   uuid NOT NULL DEFAULT gen_random_uuid(),
    observation_id       uuid NOT NULL,
    outcome              text NOT NULL,
    confidence           numeric(4,3),
    impact_scope         jsonb NOT NULL DEFAULT '{}'::jsonb,
    open_questions       jsonb NOT NULL DEFAULT '[]'::jsonb,
    agenda_id            uuid,
    auto_resolve_plan_id uuid,
    analysis_detail      text,
    created_at           timestamptz NOT NULL DEFAULT now(),
    forum_post_id        uuid,
    valid_from           timestamptz NOT NULL DEFAULT now(),
    valid_until          timestamptz NOT NULL DEFAULT '9999-12-31 00:00:00+00',
    recorded_on_dt       timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt    timestamptz NOT NULL DEFAULT '9999-12-31 00:00:00+00',
    PRIMARY KEY (id),
    CONSTRAINT assessments_outcome_check CHECK (
        outcome = ANY (ARRAY['informational','recommendation','needs_deliberation',
                             'policy_blocked','auto_resolved','rejected'])
    )
);

CREATE VIEW nebula.assessments AS
SELECT id,
       observation_id,
       outcome,
       confidence,
       impact_scope,
       open_questions,
       agenda_id,
       auto_resolve_plan_id,
       analysis_detail,
       created_at,
       forum_post_id,
       valid_from,
       valid_until,
       recorded_on_dt,
       recorded_until_dt
  FROM nebula.assessments_history ah
 WHERE now() >= recorded_on_dt
   AND now() <  recorded_until_dt
   AND now() >= valid_from
   AND now() <  valid_until;

CREATE TABLE peb.decisions (
    id                 uuid NOT NULL PRIMARY KEY,
    transaction_id     uuid NOT NULL,
    adr_number         varchar(32),
    title              varchar(256) NOT NULL,
    status             varchar(32) NOT NULL,
    summary            jsonb,
    affected_keys      text[],
    entropy_class      varchar(32),
    before_hash        varchar(64),
    after_hash         varchar(64),
    author_id          varchar(128) NOT NULL,
    parent_decision_id uuid,
    rollback_of        uuid,
    created_at         timestamptz NOT NULL
);
"""


# ── helpers ─────────────────────────────────────────────────────────────────

def psql(db: str, sql: str, expect_fail: bool = False):
    r = subprocess.run(
        [*PSQL, "-d", db, "-v", "ON_ERROR_STOP=1", "-Atc", sql],
        env=ENV, capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
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


def apply_v218(db: str):
    body = body_without_transaction(V218.read_text(encoding="utf-8"))
    return apply_script(db, f"BEGIN;\n{body}\nCOMMIT;\n")


def view_columns(db: str) -> list[str]:
    out = psql(db, """
        select column_name from information_schema.columns
         where table_schema='nebula' and table_name='assessments'
         order by ordinal_position
    """)
    return [c for c in out.splitlines() if c.strip()]


def make_db(prefix: str) -> str:
    db = f"{prefix}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
    created = subprocess.run(["createdb", "-w", db], env=ENV,
                             capture_output=True, stdin=subprocess.DEVNULL)
    assert created.returncode == 0, f"cannot create throwaway DB: {created.stderr}"
    seeded = apply_script(db, SKELETON)
    assert seeded.returncode == 0, f"skeleton failed: {seeded.stderr}"
    return db


@pytest.fixture(scope="module")
def e2edb():
    """Fresh pre-V218 database, used only by AC1 (which applies the migration)."""
    db = make_db("e2e_v218")
    try:
        yield db
    finally:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)


@pytest.fixture(scope="module")
def migrated():
    """A separate database with V218 already applied, for the behaviour guards.

    Deliberately not built on `e2edb`: that fixture's database is migrated by
    AC1, and a second apply there would be refused by the preflight.
    """
    db = make_db("e2e_v218m")
    result = apply_v218(db)
    if result.returncode != 0:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)
        raise AssertionError(f"V218 apply failed: {result.stderr}")
    try:
        yield db
    finally:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)


# ── AC1: applies cleanly ────────────────────────────────────────────────────

def test_ac1_applies_from_pre_v218_shape(e2edb):
    cols = psql(e2edb, """
        select count(*) from information_schema.columns
         where table_schema='nebula' and table_name='assessments_history'
    """)
    assert int(cols) == 15, "skeleton must start at the pre-V218 15-column shape"

    result = apply_v218(e2edb)
    assert result.returncode == 0, f"V218 apply failed: {result.stderr}"

    after = psql(e2edb, """
        select count(*) from information_schema.columns
         where table_schema='nebula' and table_name='assessments_history'
    """)
    assert int(after) == 19


# ── AC2: guarded, not silently re-applied ───────────────────────────────────

def test_ac2_second_apply_is_refused(migrated):
    body = body_without_transaction(V218.read_text(encoding="utf-8"))
    result = apply_script(migrated, f"BEGIN;\n{body}\nCOMMIT;\n")
    assert result.returncode != 0, "second apply must be refused by the preflight"
    assert "already applied" in (result.stdout + result.stderr).lower()


def test_ac2_populated_table_is_refused():
    db = make_db("e2e_v218_pop")
    try:
        psql(db, """
            insert into nebula.assessments_history (observation_id, outcome)
            values (gen_random_uuid(), 'informational')
        """)
        body = body_without_transaction(V218.read_text(encoding="utf-8"))
        result = apply_script(db, f"BEGIN;\n{body}\nCOMMIT;\n")
        assert result.returncode != 0, "populated table must be refused"
        assert "row(s)" in (result.stdout + result.stderr)
    finally:
        subprocess.run(["dropdb", "-w", db], env=ENV, capture_output=True,
                       stdin=subprocess.DEVNULL)


# ── AC3: the view actually exposes the new columns ──────────────────────────

def test_ac3_view_exposes_all_columns(migrated):
    cols = view_columns(migrated)
    assert len(cols) == 19, cols
    for name in ("assessment_kind", "subject", "evidence", "agent_record_id"):
        assert name in cols, f"{name} not exposed by nebula.assessments"
    # Existing order preserved (CREATE OR REPLACE may only append).
    assert cols[:15] == [
        "id", "observation_id", "outcome", "confidence", "impact_scope",
        "open_questions", "agenda_id", "auto_resolve_plan_id",
        "analysis_detail", "created_at", "forum_post_id", "valid_from",
        "valid_until", "recorded_on_dt", "recorded_until_dt",
    ]


def test_ac3_view_returns_rows_with_new_values(migrated):
    obs = str(uuid.uuid4())
    psql(migrated, f"""
        insert into nebula.assessments_history
            (observation_id, outcome, assessment_kind)
        values ('{obs}', 'informational', 'assessment')
    """)
    row = psql(migrated, f"""
        select assessment_kind || '|' || outcome
          from nebula.assessments where observation_id = '{obs}'
    """)
    assert row == "assessment|informational"


# ── AC4: per-kind shape ─────────────────────────────────────────────────────

def test_ac4_assessment_requires_observation(migrated):
    err = psql(migrated, """
        insert into nebula.assessments_history
            (observation_id, outcome, assessment_kind)
        values (NULL, 'informational', 'assessment')
    """, expect_fail=True)
    assert "assessments_kind_shape_check" in err or "observation_id" in err


def test_ac4_inspection_requires_subject_and_evidence(migrated):
    err = psql(migrated, """
        insert into nebula.assessments_history
            (observation_id, outcome, assessment_kind, subject)
        values (NULL, 'informational', 'inspection', 'only a subject')
    """, expect_fail=True)
    assert "assessments_kind_shape_check" in err or "evidence" in err


def test_ac4_inspection_admitted_without_observation(migrated):
    psql(migrated, """
        insert into nebula.assessments_history
            (observation_id, outcome, assessment_kind, subject, evidence)
        values (NULL, 'informational', 'inspection',
                'bin/start-mongodb-docker.sh targets old volume', '{"quote":"x"}'::jsonb)
    """)
    n = psql(migrated, """
        select count(*) from nebula.assessments
         where assessment_kind = 'inspection'
    """)
    assert int(n) >= 1


# ── AC5: single writer, index only ──────────────────────────────────────────

def test_ac5_unique_index_enforces_one_row_per_agent_record(migrated):
    ar = str(uuid.uuid4())
    psql(migrated, f"""
        insert into nebula.agent_records_history (id, record_type, title)
        values ('{ar}', 'assessment', 'first')
    """)
    psql(migrated, f"""
        insert into nebula.assessments_history
            (observation_id, outcome, agent_record_id)
        values (gen_random_uuid(), 'informational', '{ar}')
    """)
    err = psql(migrated, f"""
        insert into nebula.assessments_history
            (observation_id, outcome, agent_record_id)
        values (gen_random_uuid(), 'informational', '{ar}')
    """, expect_fail=True)
    assert "assessments_single_writer_idx" in err or "duplicate key" in err


def test_ac5_null_agent_record_ids_are_unconstrained(migrated):
    for _ in range(2):
        psql(migrated, """
            insert into nebula.assessments_history
                (observation_id, outcome, agent_record_id)
            values (gen_random_uuid(), 'informational', NULL)
        """)
    n = psql(migrated, """
        select count(*) from nebula.assessments_history
         where agent_record_id is null
    """)
    assert int(n) >= 2


def test_ac5_no_writer_role_trigger(migrated):
    """D2: the nebula_writer gate is deliberately absent (it would block pguser)."""
    n = psql(migrated, """
        select count(*) from pg_trigger t
         join pg_class c on c.oid = t.tgrelid
         join pg_namespace n2 on n2.oid = c.relnamespace
        where n2.nspname = 'nebula'
          and c.relname = 'assessments_history'
          and t.tgname = 'trg_assessments_single_writer'
    """)
    assert int(n) == 0


# ── AC6: agent-record link integrity (D1) ───────────────────────────────────

def test_ac6_unknown_agent_record_is_refused(migrated):
    err = psql(migrated, f"""
        insert into nebula.assessments_history
            (observation_id, outcome, agent_record_id)
        values (gen_random_uuid(), 'informational', '{uuid.uuid4()}')
    """, expect_fail=True)
    assert "does not exist in the current" in err


def test_ac6_current_slice_agent_record_is_accepted(migrated):
    ar = str(uuid.uuid4())
    psql(migrated, f"""
        insert into nebula.agent_records_history (id, record_type, title)
        values ('{ar}', 'assessment', 'current')
    """)
    psql(migrated, f"""
        insert into nebula.assessments_history
            (observation_id, outcome, agent_record_id)
        values (gen_random_uuid(), 'informational', '{ar}')
    """)
    n = psql(migrated, f"""
        select count(*) from nebula.assessments_history
         where agent_record_id = '{ar}'
    """)
    assert int(n) == 1


def test_ac6_superseded_agent_record_is_refused(migrated):
    ar = str(uuid.uuid4())
    psql(migrated, f"""
        insert into nebula.agent_records_history
            (id, record_type, title, recorded_on_dt, recorded_until_dt)
        values ('{ar}', 'assessment', 'superseded',
                now() - interval '2 hours', now() - interval '1 hour')
    """)
    err = psql(migrated, f"""
        insert into nebula.assessments_history
            (observation_id, outcome, agent_record_id)
        values (gen_random_uuid(), 'informational', '{ar}')
    """, expect_fail=True)
    assert "does not exist in the current" in err
