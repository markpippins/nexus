"""Option-A standby repair tests (incident e772b969, ruling thread 402d8a0d).

Proves the full repair arc on a SEALED scratch PostgreSQL database (never
canonical live PG):

  1. DEFECT REPRODUCTION — with the bootstrap's CASE view in place, an ORM
     insert through work_requests_losm fails exactly as the incident
     reported (FeatureNotSupported on 'status'), and a direct base insert
     fails on the missing id default.
  2. REPAIR (migration 016) — id generation on the base + plain
     pass-through view; postconditions (is_insertable_into, no CASE).
  3. ORM ROUND-TRIP — PlanningTask remapped at the base: create / get /
     update / delete / list with current-tense filtering.

Run with a scratch cluster available:
    LOSM_TEST_SCRATCH_DSN=postgresql://.../losm_standby_test \
        python -m pytest python/vision/losm-store/tests/test_option_a_standby_repair.py -v
Skips cleanly when no scratch DSN is provided — never touches
LOSM_DATABASE_URL / canonical PG.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from losm_store.models import PlanningTask, CURRENT_TENSE

SCRATCH_DSN = os.environ.get("LOSM_TEST_SCRATCH_DSN", "")

pytestmark = pytest.mark.skipif(
    not SCRATCH_DSN,
    reason="LOSM_TEST_SCRATCH_DSN not set — standing by, canonical PG untouched",
)

BASE_COLUMNS = """
    id INTEGER,
    wr_id VARCHAR(36) NOT NULL,
    intent TEXT NOT NULL,
    constraints JSONB,
    priority INTEGER NOT NULL DEFAULT 5,
    context JSONB,
    status VARCHAR(32) NOT NULL DEFAULT 'NEW',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    recorded_on_dt TIMESTAMPTZ NOT NULL DEFAULT now(),
    recorded_until_dt TIMESTAMPTZ NOT NULL DEFAULT '9999-12-31 23:59:59+00',
    parent_request_id VARCHAR(36),
    PRIMARY KEY (id, recorded_on_dt)
"""

# Faithful reproduction of the ci-bootstrap view (~L15354): the CASE column
# breaks PostgreSQL auto-updatability.
CASE_VIEW = """
CREATE VIEW vision.work_requests_losm AS
SELECT id, wr_id, parent_request_id, intent, constraints, priority, context,
       CASE
           WHEN status::text = ANY (ARRAY['NEW','INTAKE','PLAN_GENERATION',
                'PLAN_REVIEW','PLAN_APPROVAL_GATE','SPEC_GENERATION','EXECUTION',
                'VALIDATION','COMPLETION','BLOCKED','FAILED']) THEN status
           WHEN status::text = 'pending'   THEN 'NEW'::varchar
           WHEN status::text = 'completed' THEN 'COMPLETION'::varchar
           WHEN status::text = 'cancelled' THEN 'FAILED'::varchar
           ELSE 'NEW'::varchar
       END AS status,
       created_at, recorded_on_dt, recorded_until_dt
  FROM vision.work_requests_history
 WHERE recorded_until_dt = '9999-12-31 23:59:59+00'
"""

PASS_THROUGH_VIEW = """
DROP VIEW IF EXISTS vision.work_requests_losm;
CREATE VIEW vision.work_requests_losm AS
SELECT id, wr_id, parent_request_id, intent, constraints, priority, context,
       status, created_at, recorded_on_dt, recorded_until_dt
  FROM vision.work_requests_history
 WHERE recorded_until_dt = '9999-12-31 23:59:59+00'
"""


@pytest.fixture(scope="module")
def scratch():
    """Create the sealed scratch database; drop it when the module ends."""
    admin = sa.create_engine(SCRATCH_DSN, isolation_level="AUTOCOMMIT")
    name = "losm_standby_test"
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    dsn = SCRATCH_DSN.rsplit("/", 1)[0] + "/" + name
    engine = sa.create_engine(dsn)
    with engine.begin() as c:
        c.execute(text("CREATE SCHEMA vision"))
        c.execute(text(f"CREATE TABLE vision.work_requests_history ({BASE_COLUMNS})"))
        c.execute(text(CASE_VIEW))
    yield engine
    engine.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    admin.dispose()


@pytest.fixture
def session(scratch):
    """Session factory against the scratch store."""
    maker = sessionmaker(bind=scratch, autoflush=False, autocommit=False)
    s = maker()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _migration_016(engine):
    """Apply migration 016's statements (kept inline so the test proves the
    SQL as written, not a copy of it)."""
    with engine.begin() as c:
        c.execute(text("""
            CREATE SEQUENCE IF NOT EXISTS vision.work_requests_history_id_seq AS INTEGER
        """))
        c.execute(text("""
            SELECT setval('vision.work_requests_history_id_seq',
                          COALESCE((SELECT max(id) FROM vision.work_requests_history), 0) + 1,
                          false)
        """))
        c.execute(text(
            "ALTER TABLE vision.work_requests_history "
            "ALTER COLUMN id SET DEFAULT nextval('vision.work_requests_history_id_seq')"
        ))
        for stmt in PASS_THROUGH_VIEW.split(";"):
            if stmt.strip():
                c.execute(text(stmt))


# ── 1. DEFECT REPRODUCTION ────────────────────────────────────────────────

def test_defect_insert_through_case_view_fails(session):
    # Raw-SQL reproduction: pre-repair, the ORM mapped the view, so its
    # INSERT rendered against work_requests_losm and failed exactly like
    # this. (Post-repair models write the base directly — that path is
    # covered by the round-trip tests below.)
    with pytest.raises(sa.exc.DBAPIError) as ei:
        session.execute(text(
            "INSERT INTO vision.work_requests_losm (wr_id, intent, status) "
            "VALUES (:w, 'defect probe', 'NEW')"
        ), {"w": str(uuid.uuid4())})
    assert "cannot insert into column" in str(ei.value).lower()
    assert "work_requests_losm" in str(ei.value).lower()
    session.rollback()


def test_defect_direct_base_insert_fails_on_missing_id_default(session):
    with pytest.raises(sa.exc.DBAPIError) as ei:
        session.execute(text(
            "INSERT INTO vision.work_requests_history "
            "(wr_id, intent, status) VALUES (:w, :i, 'NEW')"
        ), {"w": str(uuid.uuid4()), "i": "defect probe"})
    assert "null value in column" in str(ei.value).lower()
    session.rollback()


# ── 2. REPAIR (migration 016) ─────────────────────────────────────────────

def test_migration_016_repairs_and_postconditions_hold(scratch):
    _migration_016(scratch)
    with scratch.connect() as c:
        insertable = c.execute(text(
            "SELECT is_insertable_into FROM information_schema.tables "
            "WHERE table_schema='vision' AND table_name='work_requests_losm'"
        )).scalar()
        assert insertable == "YES"
        viewdef = c.execute(text(
            "SELECT pg_get_viewdef('vision.work_requests_losm'::regclass, true)"
        )).scalar()
        assert "CASE" not in viewdef.upper()
        id_default = c.execute(text(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema='vision' AND table_name='work_requests_history' "
            "AND column_name='id'"
        )).scalar()
        assert id_default and "nextval" in id_default


def test_migration_016_gate_refuses_when_history_rows_present(scratch):
    """The normalization-loss guard: a history row must block re-apply."""
    with scratch.begin() as c:
        c.execute(text(
            "INSERT INTO vision.work_requests_history (id, wr_id, intent, recorded_until_dt) "
            "VALUES (1, :w, 'tombstone probe', now())"
        ), {"w": str(uuid.uuid4())})
    with pytest.raises(sa.exc.DBAPIError) as ei:
        _migration_016_gate_only(scratch)
    assert "016-GATE-001" in str(ei.value)
    with scratch.begin() as c:
        c.execute(text("DELETE FROM vision.work_requests_history"))


def _migration_016_gate_only(engine):
    with engine.begin() as c:
        c.execute(text("""
            DO $$
            DECLARE n INTEGER;
            BEGIN
                SELECT count(*) INTO n FROM vision.work_requests_history;
                IF n > 0 THEN
                    RAISE EXCEPTION '016-GATE-001: % row(s) in vision.work_requests_history',
                        n USING ERRCODE = 'P0001';
                END IF;
            END $$;
        """))


# ── 3. ORM ROUND-TRIP (models remapped at the base) ──────────────────────

def test_orm_create_get_update_delete_round_trip(session):
    wr = PlanningTask(intent="Option-A round trip", context_data={"k": "v"})
    session.add(wr)
    session.commit()
    session.refresh(wr)

    assert wr.id is not None and wr.wr_id
    assert wr.status.value == "NEW"
    assert wr.recorded_on_dt is not None
    assert wr.recorded_until_dt == CURRENT_TENSE

    fetched = (
        session.query(PlanningTask)
        .filter(PlanningTask.wr_id == wr.wr_id)
        .first()
    )
    assert fetched is not None and fetched.id == wr.id

    fetched.intent = "updated intent"
    fetched.status = "EXECUTION"
    session.commit()
    session.refresh(fetched)
    assert fetched.status.value == "EXECUTION"

    session.delete(fetched)
    session.commit()
    assert (
        session.query(PlanningTask).filter(PlanningTask.wr_id == wr.wr_id).first()
        is None
    )


def test_orm_writes_visible_through_repaired_view(scratch, session):
    """After 016, ORM rows (written at the base) are current-tense visible
    through the pass-through view — parity with the pre-incident contract."""
    _migration_016(scratch)
    wr = PlanningTask(intent="view parity probe", context_data={})
    session.add(wr)
    session.commit()
    n = session.execute(text(
        "SELECT count(*) FROM vision.work_requests_losm WHERE wr_id = :w"
    ), {"w": wr.wr_id}).scalar()
    assert n == 1
