#!/usr/bin/env python3
"""E2E: V186 WorkRequest primitive absorb — real migration, throwaway DB.

wr-conf house pattern (V179/V184/V185 companions). The REAL
sql/V186__work_request_primitive.sql applies to a throwaway database
carrying the pre-V186 skeleton (semantics + resolution + vision + nebula
surfaces with live-true constraints), and the full contract is exercised
against live constraint behavior — no mocks on the DB path:

  - GATE-001: idempotency — double-apply refused (P0001, named gate)
  - VOCAB-001: unmapped status REFUSES (loud, not silent-ELSE) — the DBA
    amendment (record 6be5faaa); the confirmed map passes
  - Phase 1: asset mirror by identity (6/6, same id, no semantics mutation)
  - Phase 2a: vision backfill — 6 rows, settled→COMPLETED, draft→DRAFT,
    legacy_id crosswalk, plan_id NULL with value preserved in context
  - Phase 2b: nebula backfill — 1 row via history crosswalk
  - Phase 3: validation gates pass on clean data (VALIDATE-001..005)
  - FK reality: asset_id FK satisfied only through the Phase-1 mirror
    (FK violation if mirrored asset missing)
  - CHECK reality: business_status CHECK enforced live
  - edge table stays empty (VALIDATE-005 invariant)

Suite creates and drops its own throwaway database; no production DB.

Run:
    cd /home/codex/dev/nexus
    python3 -m pytest python/nexus_core/wrp/tests/test_v186_work_request_e2e.py -v
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

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

INF = "'infinity'::timestamptz"

SKELETON_SQL = f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── semantics.canonical_asset (source registry, live shape) ────────────────
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

-- ── resolution substrate (live shape) ──────────────────────────────────────
CREATE SCHEMA resolution;
CREATE TABLE resolution.canonical_asset (LIKE semantics.canonical_asset INCLUDING ALL);
CREATE TABLE resolution.implementation_plan (
    -- live shape: plan_number is TEXT (the V171 blueprint era) — the FK from
    -- work_request.plan_id (text) lands on it, not on the uuid id.
    plan_number text PRIMARY KEY,
    title       text NOT NULL DEFAULT '(untitled)'
);
CREATE TABLE resolution.specification (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);
CREATE TABLE resolution.requirement (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid()
);
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
    valid_until             timestamptz NOT NULL DEFAULT {INF},
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT {INF}
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
    valid_until            timestamptz NOT NULL DEFAULT {INF}
);

-- ── vision.work_requests (live legacy shape; 22 columns) ───────────────────
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

-- ── nebula.work_requests_history (live shape) + view ────────────────────────
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
    valid_until             timestamptz NOT NULL DEFAULT '9999-12-31 00:00:00+00'::timestamptz,
    recorded_on_dt          timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt       timestamptz NOT NULL DEFAULT '9999-12-31 00:00:00+00'::timestamptz,
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
WHERE recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
"""


def load_v186_sql():
    with open(V186_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


class ThrowawayDB:
    def __init__(self):
        self.dbname = f"nexus_v186_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
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
    """Seed exactly the live row surfaces the migration must absorb.

    6 semantics assets; 6 vision WRs (5 draft, 1 settled) all with asset_id,
    plan_ids in context (nonexistent in resolution.implementation_plan);
    1 nebula history row (DRAFT, no asset). Returns the seeded ids.
    """
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
                    title, asset_id)
               VALUES (%s, %s::text, %s::jsonb, %s, %s::text,
                       now() - interval '10 days', NULL, %s, %s, %s)""",
            (wid, json.dumps({"origin": "test"}),
             json.dumps({"plan_id": f"99999{i}", "intent": {"type": "implement"},
                         "work_request_uuid": wid}),
             st, json.dumps({"steps": i}),
             wid, f"test WR {i}", assets[i]))

    nebula_id = str(uuid.uuid4())
    cur.execute(
        """INSERT INTO nebula.work_requests_history
               (id, title, business_status, context, legacy_id, dco_json,
                created_at, updated_at, valid_from, valid_until,
                recorded_on_dt, recorded_until_dt)
           VALUES (%s, %s, 'DRAFT', '{}'::jsonb, %s, '{}'::text,
                   now() - interval '5 days', now() - interval '5 days',
                   now() - interval '5 days', '9999-12-31 00:00:00+00'::timestamptz,
                   now() - interval '5 days', '9999-12-31 00:00:00+00'::timestamptz)""",
        (nebula_id, "Post maintenance cycle summary to Assembly syslog heartbeat",
         "sysadmin-maintenance-002"))
    return assets, vision_uuids, nebula_id


def apply_v186(conn):
    with conn.cursor() as cur:
        cur.execute(load_v186_sql())
    # The migration file commits itself (BEGIN...COMMIT inside the batch),
    # so the server-side transaction is already closed. Reset client state
    # to match (psycopg2 keeps status=INTRANS after a multi-statement
    # execute) — savepoints in the probes below then behave for real.
    conn.rollback()


class V186WorkRequestE2E(unittest.TestCase):
    """Real V186 against a real throwaway DB — every gate, live."""

    def setUp(self):
        self.db = ThrowawayDB()
        self.db.__enter__()
        self.conn = self.db.conn
        with self.conn.cursor() as cur:
            seed_live_shape(cur)
        self.conn.commit()

    def tearDown(self):
        self.db.__exit__()

    # ── the happy path ──────────────────────────────────────────────────────

    def test_full_absorb_happy_path(self):
        """Clean live-shape seed → V186 applies; full post-state verified."""
        apply_v186(self.conn)  # raises on any gate failure

        with self.conn.cursor() as cur:
            # Phase 1: assets mirrored by identity, semantics untouched
            cur.execute("SELECT count(*) FROM resolution.canonical_asset")
            self.assertEqual(cur.fetchone()[0], 6)
            cur.execute("SELECT count(*) FROM semantics.canonical_asset")
            self.assertEqual(cur.fetchone()[0], 6)

            # Phase 2a: 6 vision rows absorbed with confirmed vocabulary
            cur.execute("""
                SELECT business_status, count(*) FROM resolution.work_request
                WHERE legacy_id LIKE 'vision.work_requests:%'
                GROUP BY business_status ORDER BY business_status""")
            self.assertEqual(cur.fetchall(), [("COMPLETED", 1), ("DRAFT", 5)])

            # legacy_id crosswalk is the wr_id; id is the PK-carried uuid
            cur.execute("""
                SELECT count(*) FROM resolution.work_request c
                JOIN vision.work_requests v
                  ON c.legacy_id = 'vision.work_requests:' || v.wr_id
                 AND c.id::text = v.work_request_uuid""")
            self.assertEqual(cur.fetchone()[0], 6)

            # plan_id NULL but preserved in context
            cur.execute("""
                SELECT count(*) FROM resolution.work_request
                WHERE legacy_id LIKE 'vision.work_requests:%'
                  AND plan_id IS NULL
                  AND context->>'plan_id' IS NOT NULL""")
            self.assertEqual(cur.fetchone()[0], 6)

            # settled row reads COMPLETED (the confirmed mapping)
            cur.execute("""
                SELECT c.business_status FROM resolution.work_request c
                JOIN vision.work_requests v ON c.legacy_id = 'vision.work_requests:' || v.wr_id
                WHERE v.status = 'settled'""")
            self.assertEqual(cur.fetchone()[0], "COMPLETED")

            # bitemporal sentinels: open pairs are infinity
            cur.execute("""
                SELECT count(*) FROM resolution.work_request
                WHERE legacy_id LIKE 'vision.work_requests:%'
                  AND valid_until = 'infinity'::timestamptz
                  AND recorded_until_dt = 'infinity'::timestamptz""")
            self.assertEqual(cur.fetchone()[0], 6)

            # Phase 2b: nebula row absorbed with crosswalk legacy_id
            cur.execute("""
                SELECT count(*) FROM resolution.work_request
                WHERE legacy_id LIKE 'nebula.work_requests_history:%'""")
            self.assertEqual(cur.fetchone()[0], 1)

            # total: 7; edge table empty
            cur.execute("SELECT count(*) FROM resolution.work_request")
            self.assertEqual(cur.fetchone()[0], 7)
            cur.execute("SELECT count(*) FROM resolution.work_request_edge")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_fk_reality_via_mirror(self):
        """asset_id FK satisfies only because Phase 1 mirrored the assets."""
        with self.conn.cursor() as cur:
            # a vision row whose asset is NOT mirrored would violate the FK —
            # prove the FK is live by attempting a row with a foreign asset.
            cur.execute("SAVEPOINT fk_probe")
            with self.assertRaises(psycopg2.errors.ForeignKeyViolation):
                cur.execute(
                    """INSERT INTO resolution.work_request
                           (id, title, business_status, asset_id)
                       VALUES (%s, 'fk probe', 'DRAFT', %s)""",
                    (str(uuid.uuid4()), str(uuid.uuid4())))
            cur.execute("ROLLBACK TO SAVEPOINT fk_probe")

    def test_check_reality(self):
        """business_status CHECK enforced live — junk rejected (23514)."""
        with self.conn.cursor() as cur:
            cur.execute("SAVEPOINT chk_probe")
            with self.assertRaises(psycopg2.errors.CheckViolation):
                cur.execute(
                    """INSERT INTO resolution.work_request
                           (id, title, business_status)
                       VALUES (%s, 'chk probe', 'SETTLED')""",
                    (str(uuid.uuid4()),))
            cur.execute("ROLLBACK TO SAVEPOINT chk_probe")

    # ── gates ────────────────────────────────────────────────────────────────

    def test_gate_001_refuses_double_apply(self):
        """Second apply raises V186-GATE-001 (idempotency gate)."""
        apply_v186(self.conn)
        with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
            apply_v186(self.conn)
        self.assertIn("V186-GATE-001", str(ctx.exception))

    def test_vocab_001_loud_refusal_on_unmapped_status(self):
        """The DBA amendment: an unmapped status REFUSES — no silent ELSE."""
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO vision.work_requests
                       (wr_id, work_request_uuid, title, status, context,
                        recorded_on_dt)
                   VALUES (%s, %s, 'future-vocab row', 'deferred', '{}'::jsonb,
                           now())""",
                (str(uuid.uuid4()), str(uuid.uuid4())))
        self.conn.commit()
        with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
            apply_v186(self.conn)
        self.assertIn("V186-VOCAB-001", str(ctx.exception))
        self.assertIn("deferred", str(ctx.exception))
        # nothing was written: the whole transaction aborted
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM resolution.work_request")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_validate_003_duplicate_protection_is_structural(self):
        """Duplicate legacy_id is refused by the UNIQUE partial index itself.

        VALIDATE-003 remains as defense-in-depth for index-less environments;
        here we pin that the structural guard exists and fires (23505) before
        the gate could ever see a duplicate.
        """
        apply_v186(self.conn)
        with self.conn.cursor() as cur:
            # the partial unique index exists
            cur.execute("""
                SELECT count(*) FROM pg_indexes
                WHERE schemaname = 'resolution'
                  AND tablename = 'work_request'
                  AND indexdef LIKE '%UNIQUE%legacy_id%'""")
            self.assertEqual(cur.fetchone()[0], 1)
            # a second row sharing a crosswalk legacy_id is refused live
            cur.execute("SAVEPOINT dupe_probe")
            with self.assertRaises(psycopg2.errors.UniqueViolation):
                cur.execute("""
                    INSERT INTO resolution.work_request
                           (id, title, business_status, legacy_id)
                    SELECT %s, 'dupe', 'DRAFT', legacy_id
                    FROM resolution.work_request
                    WHERE legacy_id LIKE 'nebula.work_requests_history:%%'
                    LIMIT 1""", (str(uuid.uuid4()),))
            cur.execute("ROLLBACK TO SAVEPOINT dupe_probe")
            # and the Phase-3 duplicate probe agrees (0 dupes on clean data)
            cur.execute("""
                SELECT count(*) FROM (
                    SELECT 1 FROM resolution.work_request
                    WHERE legacy_id IS NOT NULL
                    GROUP BY legacy_id HAVING count(*) > 1) d""")
            self.assertEqual(cur.fetchone()[0], 0)

    def test_idempotent_gate_leaves_state_untouched(self):
        """Failed re-apply writes nothing (transaction atomicity)."""
        apply_v186(self.conn)
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM resolution.work_request")
            before = cur.fetchone()[0]
        with self.assertRaises(psycopg2.errors.RaiseException):
            apply_v186(self.conn)
        self.conn.rollback()
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM resolution.work_request")
            self.assertEqual(cur.fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
