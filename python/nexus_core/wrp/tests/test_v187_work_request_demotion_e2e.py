#!/usr/bin/env python3
"""E2E: V187 — legacy WR view demotion (real migration, throwaway DB).

wr-conf house pattern (V179/V184/V185/V186 companions). TWO REAL migrations
apply in sequence to a throwaway database: V186 (the absorb) then V187 (the
demotion) — V187's preflights are defined against the post-V186 state. The
skeleton deliberately recreates the PRE-V187 live shape (probed 2026-09-20,
titanium):

  - vision.work_requests as a BASE TABLE (6 rows on live), with the
    landing-guard and asset triggers (real names)
  - vision.work_requests_history EMPTY (the live bitemporal substrate), so
    vision.work_requests_losm reads 0 rows — the live dead read path
  - vision.work_request_edges/_dag views over the empty edges history, with
    INSTEAD OF triggers (real names)
  - vision.work_request_shape_registry with its single-ratified trigger
  - nebula.work_requests_history + nebula.work_requests view
  - execution.requests with the source_wr_id FK → nebula history
  - nebula.v_work_request_overview + scratch.v_work_request_overview with
    the ORIGINAL definitions (join via work_request_uuid)

Contract exercised against live constraint behavior (no mocks on the DB path):

  - demotion: vision bases renamed to *_v187_archive (rows retained 6/0/0/0),
    legacy triggers/functions/views dropped by name
  - resurrection: the rebuilt _losm view returns 6 rows (live reads 0)
  - vocabulary: draft→NEW, settled→COMPLETION; cancelled→CANCELLED (the
    documented correction of the legacy cancelled→FAILED mislabel)
  - legacy id map: 6 rows, bigint ↔ wr_id ↔ uuid, xmin evidence notice
  - entity_key repair: canonical rows carry the legacy entity_key (V186 gap)
  - overview views: rebuilt LAST, exactly 1 row (row set preserved),
    effective_status 'PENDING' for the DRAFT/ unconsumed nebula row
  - archive append-only: UPDATE/DELETE refused by named trigger
  - execution FK: still targets nebula.work_requests_history and enforces
  - gates: IDEM-001 (double apply), PREFLIGHT-001 (V186 missing),
    PREFLIGHT-003 (unresolved execution source_wr_id), PREFLIGHT-005
    (unexpected dependents — the CASCADE audit computed at apply time)
  - audit trail: NEBULA_AUDIT row in tackle.system_logs

Suite creates and drops its own throwaway database; no production DB.

Run:
    cd /home/codex/dev/nexus-worktrees/v187-view-demotion
    python3 -m pytest python/nexus_core/wrp/tests/test_v187_work_request_demotion_e2e.py -v
"""

import json
import os
import sys
import unittest
import uuid

import psycopg2

_REPO = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V186_PATH = os.path.join(_REPO, "sql", "V186__work_request_primitive.sql")
V187_PATH = os.path.join(_REPO, "sql", "V187__work_request_view_demotion.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

INF_TSTZ = "'9999-12-31 00:00:00+00'::timestamptz"


def _sql(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# ── the PRE-V187 live shape ─────────────────────────────────────────────────
SKELETON_SQL = f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── semantics + resolution substrate (same as V186 skeleton) ───────────────
CREATE SCHEMA semantics;
CREATE TABLE semantics.canonical_asset (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_asset_id text NOT NULL,
    asset_kind         text NOT NULL,
    canonical_key      jsonb,
    source_hash        text,
    content_hash       text,
    validity_start     timestamptz,
    validity_end       timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    expired_at         timestamptz
);
CREATE SCHEMA resolution;
CREATE TABLE resolution.canonical_asset (LIKE semantics.canonical_asset INCLUDING ALL);
CREATE TABLE resolution.implementation_plan (
    plan_number text PRIMARY KEY,
    title       text NOT NULL DEFAULT '(untitled)'
);
CREATE TABLE resolution.specification (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE resolution.requirement   (id uuid PRIMARY KEY DEFAULT gen_random_uuid());
CREATE TABLE resolution.work_request (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id                uuid REFERENCES resolution.canonical_asset(id),
    title                   text NOT NULL,
    description             text,
    source_specification_id uuid REFERENCES resolution.specification(id),
    source_requirement_id   uuid REFERENCES resolution.requirement(id),
    business_status         text NOT NULL DEFAULT 'DRAFT'
                            CHECK (business_status IN
                                   ('DRAFT','APPROVED','DISPATCHED','COMPLETED','CANCELLED')),
    intent                  text,
    context                 jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    constraints             jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    created_by              text,
    dco_json                text,
    legacy_id               text,
    plan_id                 text REFERENCES resolution.implementation_plan(plan_number),
    step_outputs            text NOT NULL DEFAULT '{{}}'::text,
    consumed_at             timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_until             timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);
CREATE UNIQUE INDEX work_request_legacy_id_key
    ON resolution.work_request (legacy_id) WHERE legacy_id IS NOT NULL;
CREATE TABLE resolution.work_request_edge (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_work_request_id uuid NOT NULL REFERENCES resolution.work_request(id),
    child_work_request_id  uuid NOT NULL REFERENCES resolution.work_request(id),
    edge_type              text NOT NULL,
    metadata               jsonb DEFAULT '{{}}'::jsonb,
    created_at             timestamptz NOT NULL DEFAULT now(),
    valid_from             timestamptz NOT NULL DEFAULT now(),
    valid_until            timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);

-- ── vision: base table + triggers (live names) ─────────────────────────────
CREATE SCHEMA vision;
CREATE TABLE vision.work_requests (
    id                   bigserial,
    wr_id                text,
    dco_json             text NOT NULL DEFAULT '{{}}'::text,
    context              jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    status               text NOT NULL DEFAULT 'pending'::text,
    step_outputs         text NOT NULL DEFAULT '{{}}'::text,
    recorded_on_dt       timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt    timestamptz,
    work_request_uuid    text NOT NULL,
    title                text NOT NULL DEFAULT ''::text,
    nexus_work_request_id uuid,
    asset_id             uuid REFERENCES semantics.canonical_asset(id),
    entity_key           text,
    business_key         text,
    relation_payload     jsonb,
    intent_payload       jsonb,
    lineage              jsonb,
    decomposition        jsonb,
    execution_linkage    jsonb,
    evidence_obligations jsonb,
    inquiry              jsonb,
    shape_version        text
);

CREATE FUNCTION vision.trg_canonical_wr_landing_guard() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF TG_OP = 'DELETE' THEN RETURN NULL; END IF;
    RETURN NEW;
END $fn$;
CREATE TRIGGER trg_canonical_wr_landing_guard
    BEFORE INSERT OR UPDATE ON vision.work_requests
    FOR EACH ROW EXECUTE FUNCTION vision.trg_canonical_wr_landing_guard();

CREATE FUNCTION vision.vision_work_request_asset_trigger() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RETURN NEW;
END $fn$;
CREATE TRIGGER trg_vision_work_requests_asset
    AFTER INSERT OR UPDATE ON vision.work_requests
    FOR EACH ROW EXECUTE FUNCTION vision.vision_work_request_asset_trigger();

-- the EMPTY bitemporal substrate (live: 0 rows)
CREATE TABLE vision.work_requests_history (
    id                integer NOT NULL,
    wr_id             varchar NOT NULL,
    intent            text,
    constraints       jsonb,
    priority          integer,
    context           jsonb,
    status            varchar NOT NULL DEFAULT 'NEW',
    created_at        timestamptz NOT NULL DEFAULT now(),
    recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT {INF_TSTZ},
    parent_request_id varchar(36),
    PRIMARY KEY (id, recorded_on_dt)
);

CREATE VIEW vision.work_requests_losm AS
SELECT h.id,
    h.wr_id,
    h.parent_request_id,
    h.intent,
    h.constraints,
    h.priority,
    h.context,
    CASE
        WHEN h.status::text = ANY (ARRAY['NEW','INTAKE','PLAN_GENERATION',
            'PLAN_REVIEW','PLAN_APPROVAL_GATE','SPEC_GENERATION','EXECUTION',
            'VALIDATION','COMPLETION','BLOCKED','FAILED']::varchar[]) THEN h.status
        WHEN h.status::text = 'pending' THEN 'NEW'::varchar
        WHEN h.status::text = 'completed' THEN 'COMPLETION'::varchar
        WHEN h.status::text = 'cancelled' THEN 'FAILED'::varchar
        ELSE 'NEW'::varchar
    END AS status,
    h.created_at,
    h.recorded_on_dt,
    h.recorded_until_dt
FROM vision.work_requests_history h
WHERE h.recorded_until_dt = {INF_TSTZ};

-- edges: history (empty) + view + INSTEAD OF triggers (live names)
CREATE TABLE vision.work_request_edges_history (
    id                serial,
    edge_id           varchar NOT NULL,
    parent_wr_id      varchar NOT NULL,
    child_wr_id       varchar NOT NULL,
    edge_type         varchar NOT NULL DEFAULT 'child_of',
    metadata          jsonb DEFAULT '{{}}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT {INF_TSTZ},
    PRIMARY KEY (id, recorded_on_dt)
);
CREATE VIEW vision.work_request_edges AS
SELECT id, edge_id, parent_wr_id, child_wr_id, edge_type, metadata,
       created_at, recorded_on_dt, recorded_until_dt
FROM vision.work_request_edges_history
WHERE recorded_until_dt = {INF_TSTZ};

CREATE FUNCTION vision.work_request_edges_insert_trigger() RETURNS trigger
LANGUAGE plpgsql AS $fn$ BEGIN RETURN NEW; END $fn$;
CREATE FUNCTION vision.work_request_edges_update_trigger() RETURNS trigger
LANGUAGE plpgsql AS $fn$ BEGIN RETURN NEW; END $fn$;
CREATE FUNCTION vision.work_request_edges_delete_trigger() RETURNS trigger
LANGUAGE plpgsql AS $fn$ BEGIN RETURN NULL; END $fn$;
CREATE TRIGGER trg_work_request_edges_insert INSTEAD OF INSERT
    ON vision.work_request_edges
    FOR EACH ROW EXECUTE FUNCTION vision.work_request_edges_insert_trigger();
CREATE TRIGGER trg_work_request_edges_update INSTEAD OF UPDATE
    ON vision.work_request_edges
    FOR EACH ROW EXECUTE FUNCTION vision.work_request_edges_update_trigger();
CREATE TRIGGER trg_work_request_edges_delete INSTEAD OF DELETE
    ON vision.work_request_edges
    FOR EACH ROW EXECUTE FUNCTION vision.work_request_edges_delete_trigger();

-- dag view over the empty history (non-recursive stub, same 11 columns)
CREATE VIEW vision.work_request_dag AS
SELECT wr.wr_id AS node_wr_id,
    wr.wr_id AS root_wr_id,
    wr.wr_id::text AS path,
    0 AS depth,
    false AS is_cycle,
    wr.intent,
    wr.status,
    wr.priority,
    wr.parent_request_id AS parent_wr_id,
    NULL::varchar AS edge_type,
    wr.created_at
FROM vision.work_requests_history wr
WHERE wr.parent_request_id IS NULL AND wr.recorded_until_dt = {INF_TSTZ};

-- shape registry (empty on live) + its trigger
CREATE TABLE vision.work_request_shape_registry (
    shape_version      text NOT NULL,
    artifact_path      text,
    artifact_sha256    text,
    ratification_state text NOT NULL DEFAULT 'proposed',
    ratified_at        timestamptz,
    ratified_by        text,
    notes              jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    recorded_on_dt     timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt  timestamptz NOT NULL DEFAULT {INF_TSTZ},
    PRIMARY KEY (shape_version, recorded_on_dt)
);
CREATE FUNCTION vision.trg_work_request_shape_registry_single_ratified() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    IF NEW.ratification_state = 'ratified' THEN
        PERFORM 1 FROM vision.work_request_shape_registry r
        WHERE r.ratification_state = 'ratified' AND r.shape_version <> NEW.shape_version
              AND r.recorded_until_dt = {INF_TSTZ};
        IF FOUND THEN
            RAISE EXCEPTION 'single-ratified constraint: a ratified shape already exists';
        END IF;
    END IF;
    RETURN NEW;
END $fn$;
CREATE TRIGGER trg_wr_shape_registry_single_ratified
    BEFORE INSERT OR UPDATE ON vision.work_request_shape_registry
    FOR EACH ROW EXECUTE FUNCTION vision.trg_work_request_shape_registry_single_ratified();

-- ── nebula history + view ───────────────────────────────────────────────────
CREATE SCHEMA nebula;
CREATE TABLE nebula.work_requests_history (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title                   text NOT NULL,
    description             text,
    source_specification_id uuid,
    source_requirement_id   uuid,
    business_status         text NOT NULL DEFAULT 'DRAFT'::text,
    intent                  text,
    context                 jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    constraints             jsonb NOT NULL DEFAULT '{{}}'::jsonb,
    created_by              text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now(),
    dco_json                text,
    legacy_id               text,
    plan_id                 text,
    step_outputs            text NOT NULL DEFAULT '{{}}'::text,
    consumed_at             timestamptz,
    valid_from              timestamptz NOT NULL DEFAULT now(),
    valid_until             timestamptz NOT NULL DEFAULT {INF_TSTZ},
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT {INF_TSTZ},
    asset_id                uuid,
    entity_key              text
);
CREATE VIEW nebula.work_requests AS
SELECT id, title, description, source_specification_id, source_requirement_id,
       business_status, intent, context, constraints, created_by,
       created_at, updated_at, dco_json, legacy_id, plan_id, step_outputs,
       consumed_at, valid_from, valid_until, recorded_on_dt, recorded_until_dt,
       asset_id, entity_key
FROM nebula.work_requests_history
WHERE recorded_until_dt = {INF_TSTZ};

-- ── execution.requests with the legacy FK ───────────────────────────────────
CREATE SCHEMA execution;
CREATE TABLE execution.requests (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_key text,
    title        text,
    status       text NOT NULL DEFAULT 'READY',
    source_wr_id uuid REFERENCES nebula.work_requests_history(id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ── the ORIGINAL overview views (verbatim semantics, live 2026-09-20) ──────
CREATE SCHEMA scratch;
CREATE VIEW nebula.v_work_request_overview AS
SELECT wr.id,
    wr.legacy_id,
    wr.title,
    wr.plan_id,
    wr.business_status,
    wr.consumed_at,
    wr.created_at AS business_created_at,
    er.status AS execution_status,
    er.id AS execution_request_id,
    er.business_key,
    vr.status AS runtime_status,
    vr.work_request_uuid AS vision_id,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'CANCELLED'
        WHEN vr.status = 'rejected' THEN 'REJECTED'
        WHEN vr.status = 'failed' THEN 'FAILED'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'COMPLETE'
        WHEN wr.business_status = 'COMPLETED' THEN 'AWAITING_REVIEW'
        WHEN vr.status = 'settled' THEN 'SETTLED_AWAITING_BUSINESS'
        WHEN vr.status = 'claimed' THEN 'RUNNING'
        WHEN vr.status = 'validated' THEN 'VALIDATED'
        WHEN vr.status = 'queued' THEN 'QUEUED'
        WHEN vr.status = 'deferred' THEN 'DEFERRED'
        WHEN vr.status = 'noop' THEN 'NOOP'
        WHEN er.status = 'READY' THEN 'READY_FOR_EXECUTION'
        WHEN er.status = 'ADMITTED' THEN 'ADMITTED'
        WHEN er.status = 'VALIDATED' THEN 'EXECUTION_VALIDATED'
        WHEN er.status = 'COMPLETED' THEN 'EXECUTION_COMPLETE'
        WHEN er.status = 'FAILED' THEN 'EXECUTION_FAILED'
        WHEN wr.business_status = 'DISPATCHED' THEN 'DISPATCHED'
        WHEN wr.business_status = 'APPROVED' THEN 'APPROVED'
        WHEN wr.business_status = 'DRAFT' AND wr.consumed_at IS NULL THEN 'PENDING'
        WHEN wr.business_status = 'DRAFT' THEN 'DRAFT'
        ELSE 'UNKNOWN'
    END AS effective_status,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'Business intent cancelled'
        WHEN vr.status = 'rejected' THEN 'Execution rejected'
        WHEN vr.status = 'failed' THEN 'Execution failed'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'Complete - business objective satisfied'
        WHEN wr.business_status = 'COMPLETED' THEN 'Awaiting business review'
        WHEN vr.status = 'settled' THEN 'Settled - awaiting business confirmation'
        WHEN vr.status = 'claimed' THEN 'Worker actively executing'
        WHEN vr.status = 'validated' THEN 'Validated - awaiting execution'
        WHEN vr.status = 'queued' THEN 'Queued for execution'
        WHEN er.status = 'READY' THEN 'Ready for execution'
        WHEN wr.business_status = 'DISPATCHED' THEN 'Dispatched to execution layer'
        WHEN wr.business_status = 'APPROVED' THEN 'Approved - awaiting compilation'
        WHEN wr.consumed_at IS NULL THEN 'Pending - not yet consumed'
        ELSE 'Draft'
    END AS effective_status_description
FROM nebula.work_requests_history wr
LEFT JOIN execution.requests er ON er.source_wr_id = wr.id
LEFT JOIN vision.work_requests vr ON vr.work_request_uuid = wr.id::text;

CREATE VIEW scratch.v_work_request_overview AS
SELECT wr.id,
    wr.legacy_id,
    wr.title,
    wr.plan_id,
    wr.business_status,
    wr.consumed_at,
    wr.created_at AS business_created_at,
    er.status AS execution_status,
    er.id AS execution_request_id,
    er.business_key,
    vr.status AS runtime_status,
    vr.work_request_uuid AS vision_id,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'CANCELLED'
        WHEN vr.status = 'rejected' THEN 'REJECTED'
        WHEN vr.status = 'failed' THEN 'FAILED'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'COMPLETE'
        WHEN wr.business_status = 'COMPLETED' THEN 'AWAITING_REVIEW'
        WHEN vr.status = 'settled' THEN 'SETTLED_AWAITING_BUSINESS'
        WHEN vr.status = 'claimed' THEN 'RUNNING'
        WHEN vr.status = 'validated' THEN 'VALIDATED'
        WHEN vr.status = 'queued' THEN 'QUEUED'
        WHEN vr.status = 'deferred' THEN 'DEFERRED'
        WHEN vr.status = 'noop' THEN 'NOOP'
        WHEN er.status = 'READY' THEN 'READY_FOR_EXECUTION'
        WHEN er.status = 'ADMITTED' THEN 'ADMITTED'
        WHEN er.status = 'VALIDATED' THEN 'EXECUTION_VALIDATED'
        WHEN er.status = 'COMPLETED' THEN 'EXECUTION_COMPLETE'
        WHEN er.status = 'FAILED' THEN 'EXECUTION_FAILED'
        WHEN wr.business_status = 'DISPATCHED' THEN 'DISPATCHED'
        WHEN wr.business_status = 'APPROVED' THEN 'APPROVED'
        WHEN wr.business_status = 'DRAFT' AND wr.consumed_at IS NULL THEN 'PENDING'
        WHEN wr.business_status = 'DRAFT' THEN 'DRAFT'
        ELSE 'Draft'
    END AS effective_status,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'Business intent cancelled'
        WHEN vr.status = 'rejected' THEN 'Execution rejected'
        WHEN vr.status = 'failed' THEN 'Execution failed'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'Complete - business objective satisfied'
        WHEN wr.business_status = 'COMPLETED' THEN 'Awaiting business review'
        WHEN vr.status = 'settled' THEN 'Settled - awaiting business confirmation'
        WHEN vr.status = 'claimed' THEN 'Worker actively executing'
        WHEN vr.status = 'validated' THEN 'Validated - awaiting execution'
        WHEN vr.status = 'queued' THEN 'Queued for execution'
        WHEN er.status = 'READY' THEN 'Ready for execution'
        WHEN wr.business_status = 'DISPATCHED' THEN 'Dispatched to execution layer'
        WHEN wr.business_status = 'APPROVED' THEN 'Approved - awaiting compilation'
        WHEN wr.consumed_at IS NULL THEN 'Pending - not yet consumed'
        ELSE 'Draft'
    END AS effective_status_description
FROM nebula.work_requests_history wr
LEFT JOIN execution.requests er ON er.source_wr_id = wr.id
LEFT JOIN vision.work_requests vr ON vr.work_request_uuid = wr.id::text;

-- ── tackle.system_logs (live shape) ────────────────────────────────────────
CREATE SCHEMA tackle;
CREATE TABLE tackle.system_logs (
    id        text PRIMARY KEY,
    timestamp timestamptz NOT NULL DEFAULT now(),
    level     text,
    category  text,
    message   text,
    source    text,
    details   jsonb
);
"""


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_v187_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.conn = None

    def __enter__(self):
        admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            cur.execute(f'CREATE DATABASE "{self.dbname}"')
        admin.close()
        self.conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.dbname)
        self.conn.autocommit = False
        with self.conn.cursor() as cur:
            cur.execute(SKELETON_SQL)
        self.conn.commit()
        return self

    def __exit__(self, *exc):
        try:
            if self.conn:
                self.conn.close()
        finally:
            admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            admin.close()
        return False


def seed_live_shape(cur):
    """Seed the live row surfaces: 6 assets, 6 vision WRs (entity_key set),
    1 nebula history row (entity_key set), 1 execution request."""
    assets = []
    for i in range(6):
        aid = str(uuid.uuid4())
        cur.execute(
            """INSERT INTO semantics.canonical_asset
                   (id, canonical_asset_id, asset_kind, canonical_key,
                    validity_start, validity_end, created_at)
               VALUES (%s, %s, %s, %s::jsonb, now() - interval '30 days',
                       NULL, now())""",
            (aid, f"sem-asset-{i}", "work_request", json.dumps({"key": f"k{i}"})))
        assets.append(aid)

    vision_uuids = []
    statuses = ["draft"] * 5 + ["settled"]
    for i, st in enumerate(statuses):
        wid = str(uuid.uuid4())
        vision_uuids.append(wid)
        cur.execute(
            """INSERT INTO vision.work_requests
                   (wr_id, dco_json, context, status, step_outputs,
                    recorded_on_dt, recorded_until_dt, work_request_uuid,
                    title, asset_id, entity_key)
               VALUES (%s, %s::text, %s::jsonb, %s, %s::text,
                       now() - interval '10 days', NULL, %s, %s, %s, %s)""",
            (wid, json.dumps({"origin": "test"}),
             json.dumps({"plan_id": f"99999{i}", "intent": {"type": "implement"},
                         "work_request_uuid": wid}),
             st, json.dumps({"steps": i}),
             wid, f"test WR {i}", assets[i], f"ek-vision-{i}"))

    nebula_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO nebula.work_requests_history
               (id, title, business_status, context, legacy_id, dco_json,
                created_at, updated_at, valid_from, valid_until,
                recorded_on_dt, recorded_until_dt, entity_key)
           VALUES (%s, %s, 'DRAFT', '{}'::jsonb, %s, '{}'::text,
                   now() - interval '5 days', now() - interval '5 days',
                   now() - interval '5 days', 'infinity'::timestamptz,
                   now() - interval '5 days', 'infinity'::timestamptz, %s)""",
        (nebula_id, "Post maintenance cycle summary to Assembly syslog heartbeat",
         "sysadmin-maintenance-002", "ek-nebula-1"))

    cur.execute(
        """INSERT INTO execution.requests (business_key, title, status, source_wr_id)
           VALUES ('bk-e2e', 'e2e execution request', 'READY', %s)""",
        (nebula_id,))
    return assets, vision_uuids, nebula_id


def _apply(conn, path):
    with conn.cursor() as cur:
        cur.execute(_sql(path))
    # Each migration commits itself (BEGIN...COMMIT inside the batch); reset
    # psycopg2's client-side tracker to match the server's idle state.
    conn.rollback()


def apply_v186(conn):
    _apply(conn, V186_PATH)


def apply_v187(conn):
    _apply(conn, V187_PATH)


def relkind(cur, regclass):
    cur.execute(
        "SELECT relkind::text FROM pg_class WHERE oid = %s::regclass",
        (regclass,))
    return cur.fetchone()[0]


class V187WorkRequestDemotionE2E(unittest.TestCase):
    """Real V186 → real V187 against a real throwaway DB."""

    def setUp(self):
        self.db = ThrowawayDB()
        self.db.__enter__()
        self.conn = self.db.conn
        with self.conn.cursor() as cur:
            self.assets, self.vision_uuids, self.nebula_id = seed_live_shape(cur)
        self.conn.commit()
        apply_v186(self.conn)

    def tearDown(self):
        self.db.__exit__()

    # ── the happy path ──────────────────────────────────────────────────────

    def test_full_demotion_happy_path(self):
        """Clean post-V186 state → V187 applies; full post-state verified."""
        apply_v187(self.conn)  # raises on any gate failure

        with self.conn.cursor() as cur:
            # archives exist as TABLES with the retained rows (6/0/0/0)
            self.assertEqual(relkind(cur, "vision.work_requests_v187_archive"), "r")
            cur.execute("SELECT count(*) FROM vision.work_requests_v187_archive")
            self.assertEqual(cur.fetchone()[0], 6)
            cur.execute("SELECT count(*) FROM vision.work_requests_history_v187_archive")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM vision.work_request_edges_history_v187_archive")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT count(*) FROM vision.work_request_shape_registry_v187_archive")
            self.assertEqual(cur.fetchone()[0], 0)

            # the legacy identity map: 6 rows, bigint ↔ wr_id ↔ uuid
            cur.execute("SELECT count(*) FROM vision.work_request_legacy_id_map")
            self.assertEqual(cur.fetchone()[0], 6)
            cur.execute("""
                SELECT count(*) FROM vision.work_request_legacy_id_map m
                JOIN vision.work_requests_v187_archive a ON a.id = m.id
                 AND a.wr_id = m.wr_id AND m.work_request_uuid::text = a.work_request_uuid""")
            self.assertEqual(cur.fetchone()[0], 6)

            # vision.work_requests is now a VIEW returning 6 rows
            self.assertEqual(relkind(cur, "vision.work_requests"), "v")
            cur.execute("SELECT count(*) FROM vision.work_requests")
            self.assertEqual(cur.fetchone()[0], 6)
            # the rebuilt view normalizes to canonical vocabulary
            # (legacy 'settled' surfaces as 'completed') — documented change
            cur.execute("""
                SELECT count(*) FROM vision.work_requests
                WHERE status NOT IN ('draft', 'completed')""")
            self.assertEqual(cur.fetchone()[0], 0)

            # RESURRECTION: _losm reads 6 rows (live read path was dead: 0)
            cur.execute("SELECT count(*) FROM vision.work_requests_losm")
            self.assertEqual(cur.fetchone()[0], 6)
            # corrected vocabulary: draft→NEW, settled→COMPLETION
            cur.execute("""
                SELECT status, count(*) FROM vision.work_requests_losm
                GROUP BY status ORDER BY status""")
            self.assertEqual(cur.fetchall(), [("COMPLETION", 1), ("NEW", 5)])

            # nebula.work_requests rebuilt as a view over canonical —
            # row set PRESERVED is NOT the test here: the legacy view read
            # only the nebula store (1 row), and the rebuild pins the same
            # restriction (nebula legacy prefix). Assert exactly that.
            self.assertEqual(relkind(cur, "nebula.work_requests"), "v")
            cur.execute("SELECT count(*) FROM nebula.work_requests")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("""
                SELECT count(*) FROM resolution.work_request
                WHERE legacy_id LIKE 'nebula.work_requests_history:%'
                  AND NOT EXISTS (SELECT 1 FROM nebula.work_requests n
                                  WHERE n.id = resolution.work_request.id)
                  AND legacy_id LIKE 'nebula.work_requests_history:%'""")
            self.assertEqual(cur.fetchone()[0], 0)  # every nebula-legacy row surfaces

            # legacy triggers/functions are gone
            cur.execute("""
                SELECT count(*) FROM pg_trigger t
                JOIN pg_class c ON c.oid = t.tgrelid
                WHERE c.oid = 'vision.work_requests_v187_archive'::regclass
                  AND t.tgname IN ('trg_canonical_wr_landing_guard',
                                   'trg_vision_work_requests_asset')""")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("""
                SELECT to_regprocedure('vision.trg_canonical_wr_landing_guard()') IS NULL,
                       to_regprocedure('vision.vision_work_request_asset_trigger()') IS NULL,
                       to_regprocedure('vision.v187_archive_append_only()') IS NOT NULL""")
            dropped_gone, asset_gone, append_only_lives = cur.fetchone()
            self.assertTrue(dropped_gone and asset_gone and append_only_lives)

            # audit trail row landed
            cur.execute("""
                SELECT count(*) FROM tackle.system_logs
                WHERE category = 'NEBULA_AUDIT'
                  AND details->>'migration' = 'V187'""")
            self.assertEqual(cur.fetchone()[0], 1)

    def test_entity_key_repair(self):
        """The V186 gap is repaired: canonical rows carry the legacy keys."""
        apply_v187(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) FROM resolution.work_request
                WHERE legacy_id LIKE 'vision.work_requests:%'
                  AND entity_key LIKE 'ek-vision-%'""")
            self.assertEqual(cur.fetchone()[0], 6)
            cur.execute("""
                SELECT count(*) FROM resolution.work_request
                WHERE legacy_id = 'nebula.work_requests_history:' || %s
                  AND entity_key = 'ek-nebula-1'""",
                (self.nebula_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_overview_views_rebuilt_row_set_preserved(self):
        """Both overviews resolve post-demotion with the ORIGINAL row set
        (exactly the 1 nebula-legacy row) and correct semantics."""
        apply_v187(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM nebula.v_work_request_overview")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("SELECT count(*) FROM scratch.v_work_request_overview")
            self.assertEqual(cur.fetchone()[0], 1)
            cur.execute("""
                SELECT effective_status FROM nebula.v_work_request_overview
                WHERE legacy_id = 'nebula.work_requests_history:' || %s""",
                (self.nebula_id,))
            # legacy-faithful: the original CASE puts er.status='READY' before
            # the DRAFT branch, so READY_FOR_EXECUTION wins — pinned verbatim
            self.assertEqual(cur.fetchone()[0], "READY_FOR_EXECUTION")
            # the execution join resolves through the canonical legacy_id
            cur.execute("""
                SELECT execution_status FROM nebula.v_work_request_overview
                WHERE legacy_id = 'nebula.work_requests_history:' || %s""",
                (self.nebula_id,))
            self.assertEqual(cur.fetchone()[0], "READY")

    def test_dag_and_edges_views_shape_faithful(self):
        """Rebuilt dag/edges views exist, queryable, legacy columns. With no
        edges, every WR is the root of its own trivial tree — the dag view
        returns 6 depth-0 rows (the legacy 0-row read was the dead-path
        artifact, same as _losm)."""
        apply_v187(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM vision.work_request_dag")
            self.assertEqual(cur.fetchone()[0], 6)
            cur.execute("""
                SELECT count(*) FROM vision.work_request_dag
                WHERE depth = 0 AND NOT is_cycle
                  AND node_wr_id = root_wr_id AND parent_wr_id IS NULL""")
            self.assertEqual(cur.fetchone()[0], 6)
            cur.execute("SELECT count(*) FROM vision.work_request_edges")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='vision' AND table_name='work_request_dag'
                ORDER BY ordinal_position""")
            self.assertEqual(
                [r[0] for r in cur.fetchall()],
                ["node_wr_id", "root_wr_id", "path", "depth", "is_cycle",
                 "intent", "status", "priority", "parent_wr_id", "edge_type",
                 "created_at"])

    def test_archive_append_only(self):
        """Archive tables refuse UPDATE and DELETE by named trigger."""
        apply_v187(self.conn)
        with self.conn.cursor() as cur:
            for op in ("UPDATE", "DELETE"):
                with self.assertRaises(psycopg2.errors.RaiseException) as cm:
                    if op == "UPDATE":
                        cur.execute(
                            "UPDATE vision.work_requests_v187_archive SET title = 'x'")
                    else:
                        cur.execute(
                            "DELETE FROM vision.work_requests_v187_archive")
                self.assertIn("append-only", str(cm.exception))
                self.conn.rollback()

    def test_execution_fk_still_enforces(self):
        """execution.requests keeps its FK to the (unrenamed) nebula history
        and still refuses orphan source_wr_id values."""
        apply_v187(self.conn)
        with self.conn.cursor() as cur:
            with self.assertRaises(psycopg2.errors.ForeignKeyViolation):
                cur.execute(
                    """INSERT INTO execution.requests
                           (business_key, title, status, source_wr_id)
                       VALUES ('bk-orphan', 'orphan', 'READY', %s)""",
                    (str(uuid.uuid4()),))
            self.conn.rollback()

    # ── the gates ───────────────────────────────────────────────────────────

    def test_gate_idem_001_double_apply(self):
        """Second V187 apply refuses by NAME (archive already exists)."""
        apply_v187(self.conn)
        with self.assertRaises(psycopg2.errors.RaiseException) as cm:
            apply_v187(self.conn)
        self.assertIn("V187-IDEM-001", str(cm.exception))

    def test_gate_preflight_001_requires_v186(self):
        """V187 without the V186 backfill refuses: legacy rows lack
        canonical counterparts."""
        db2 = ThrowawayDB()
        with db2:
            with db2.conn.cursor() as cur:
                seed_live_shape(cur)
            db2.conn.commit()
            with self.assertRaises(psycopg2.errors.RaiseException) as cm:
                apply_v187(db2.conn)
            self.assertIn("V187-PREFLIGHT-001", str(cm.exception))

    def test_gate_preflight_003_subsumed_by_001(self):
        """0c is subsumed by 0a + the FK: an execution row pointing at a
        nebula row with no canonical counterpart fires PREFLIGHT-001 (the
        unresolved-row count includes it) — pinned so nobody re-adds the
        dead gate."""
        with self.conn.cursor() as cur:
            orphan_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO nebula.work_requests_history
                       (id, title, business_status, legacy_id)
                   VALUES (%s, 'post-V186 orphan', 'DRAFT', 'post-v186-orphan')""",
                (orphan_id,))
            cur.execute(
                """INSERT INTO execution.requests
                       (business_key, title, status, source_wr_id)
                   VALUES ('bk-orphan2', 'orphan2', 'READY', %s)""",
                (orphan_id,))
        self.conn.commit()
        with self.assertRaises(psycopg2.errors.RaiseException) as cm:
            apply_v187(self.conn)
        self.assertIn("V187-PREFLIGHT-001", str(cm.exception))

    def test_gate_preflight_005_unexpected_dependents(self):
        """The CASCADE audit: a consumer view added between pre-stage and
        apply aborts the migration and names itself."""
        with self.conn.cursor() as cur:
            cur.execute(
                "CREATE VIEW vision.sneaky_consumer AS "
                "SELECT count(*) AS n FROM vision.work_requests")
        self.conn.commit()
        with self.assertRaises(psycopg2.errors.RaiseException) as cm:
            apply_v187(self.conn)
        self.assertIn("V187-PREFLIGHT-005", str(cm.exception))
        self.assertIn("vision.sneaky_consumer", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
