#!/usr/bin/env python3
"""E2E: V171 blueprint rename/collapse against the REAL migration (throwaway DB).

wr-conf-021 companion, following the house throwaway-DB pattern
(test_persist_e2e_snapshots.py / wr-conf-018): each run creates a throwaway
database (nexus_v171_test_<pid>) on the DEV Postgres, builds the minimal
pre-V171 skeleton (semantics.canonical_asset per V065, legacy
implementation_plans_history per V081 state, tackle.system_logs), applies the
REAL sql/V171__blueprints_rename_collapse.sql, then asserts the migration
invariants live — and drops the database on exit.

  - backfill carryover: every legacy plan row has a blueprint row (same id,
    plan_number verbatim, dual numbering regimes preserved)
  - asset-kind rename: linked current-row assets are kind=blueprint
  - payload fold: legacy flat columns readable through the payload contract
  - compat view: legacy column contract served from the new surface
  - mirror trigger: installed by V171, retired by V176 (transition window
    only — post-V176 the legacy table is frozen and writes must target
    blueprints_history directly)
  - audit trail: NEBULA_AUDIT rows land in tackle.system_logs
  - sanity gates: duplicate plan_numbers refused; re-apply idempotent
"""
import os
import sys
import unittest
import uuid

import psycopg2

_REPO_ROOT = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", ".."))
V171_PATH = os.path.join(_REPO_ROOT, "sql", "V171__blueprints_rename_collapse.sql")
V176_PATH = os.path.join(_REPO_ROOT, "sql", "V176__retire_plans_mirror_trigger.sql")

DSN = os.environ.get("CONDUIT_PG_DSN",
                     "postgresql://pguser:pgpass@localhost:5432/postgres")

SENTINEL = "infinity"  # current-row sentinel (house default)

SKELETON_SQL = """
CREATE SCHEMA semantics;
CREATE SCHEMA nebula;
CREATE SCHEMA tackle;

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
CREATE UNIQUE INDEX idx_canonical_asset_active_canonical_asset_id
    ON semantics.canonical_asset (canonical_asset_id) WHERE expired_at IS NULL;

CREATE TABLE nebula.implementation_plans_history (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_number         text UNIQUE,
    spec_id             uuid,
    requirement_id      uuid,
    title               text NOT NULL,
    goal                text,
    content             text,
    files_affected      text[] NOT NULL DEFAULT '{}',
    acceptance_criteria jsonb DEFAULT '[]'::jsonb,
    dependencies        text[] NOT NULL DEFAULT '{}',
    status              text NOT NULL DEFAULT 'draft'
                        CHECK (status = ANY (ARRAY['draft','pending','approved',
                              'work_requested','completed','archived'])),
    tags                text[] NOT NULL DEFAULT '{}',
    metadata            jsonb DEFAULT '{}'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    valid_from          timestamptz NOT NULL DEFAULT now(),
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    asset_id            uuid REFERENCES semantics.canonical_asset(id)
);

-- legacy read view (V081 shape) so the compat-view contract matches live
CREATE VIEW nebula.implementation_plans AS
SELECT id, plan_number, spec_id, requirement_id, title, goal, content,
       files_affected, acceptance_criteria, dependencies, status, tags,
       metadata, created_at, updated_at, valid_from, valid_until,
       recorded_on_dt, recorded_until_dt, asset_id
FROM nebula.implementation_plans_history
WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
  AND now() >= valid_from AND now() < valid_until;

CREATE TABLE tackle.system_logs (
    id        text PRIMARY KEY,
    timestamp timestamptz NOT NULL DEFAULT now(),
    level     text NOT NULL,
    category  text NOT NULL,
    message   text NOT NULL,
    source    text,
    details   jsonb
);
"""


class ThrowawayDB:
    """Throwaway per-run database with the pre-V171 skeleton applied."""

    def __init__(self):
        self.dbname = f"nexus_v171_test_{os.getpid()}_{uuid.uuid4().hex[:6]}"
        self.conn = None

    def __enter__(self):
        admin = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/postgres")
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{self.dbname}"')
            cur.execute(f'CREATE DATABASE "{self.dbname}"')
        admin.close()
        self.conn = psycopg2.connect(DSN.rsplit("/", 1)[0] + "/" + self.dbname)
        self.conn.autocommit = True
        with self.conn.cursor() as cur:
            cur.execute(SKELETON_SQL)
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

    def sql(self, stmt, params=None):
        with self.conn.cursor() as cur:
            cur.execute(stmt, params)
            rows = cur.fetchall() if cur.description else None
        return rows

    def apply_v171(self):
        with open(V171_PATH) as fh:
            self.sql(fh.read())

    def apply_v176(self):
        """Apply the V176 trigger-retirement migration (idempotent)."""
        with open(V176_PATH) as fh:
            self.sql(fh.read())

    def seed(self, n=24):
        """Seed n legacy plan rows with V081-style asset envelopes.

        Returns list of (row_id, plan_number). Uses the dual numbering
        regimes seen on live: zero-padded 0001..0016 then 8261601+.
        """
        rows = []
        for i in range(n):
            rid = str(uuid.uuid4())
            num = f"{i+1:04d}" if i < 16 else str(8261601 + i)
            rows.append((rid, num))
        with self.conn.cursor() as cur:
            for i, (rid, num) in enumerate(rows):
                cur.execute("""
INSERT INTO nebula.implementation_plans_history
    (id, plan_number, title, goal, content, files_affected,
     acceptance_criteria, dependencies, status, tags, metadata)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)
""", (rid, num, f"Seed plan {i+1}", f"goal {i+1}", f"content {i+1}",
      [f"a/{i}.ts"], f'["ac{i+1}"]', [f"dep{i}"],
      [f"tag{i}"], '{"project": "wrp"}'))
        # V081-style asset envelopes + link (current rows only)
        self.sql(f"""
INSERT INTO semantics.canonical_asset (canonical_asset_id, asset_kind)
SELECT 'asset:nexus:nebula_implementation_plans:' || id::text, 'implementation_plan'
FROM nebula.implementation_plans_history
WHERE recorded_until_dt = '{SENTINEL}'::timestamptz AND asset_id IS NULL
ON CONFLICT (canonical_asset_id) WHERE expired_at IS NULL DO NOTHING;

UPDATE nebula.implementation_plans_history iph
SET asset_id = ca.id
FROM semantics.canonical_asset ca
WHERE ca.canonical_asset_id = 'asset:nexus:nebula_implementation_plans:' || iph.id::text
  AND ca.expired_at IS NULL AND iph.asset_id IS NULL
  AND iph.recorded_until_dt = '{SENTINEL}'::timestamptz;
""")
        return rows


class V171E2E(unittest.TestCase):
    db = None

    def setUp(self):
        self.db = ThrowawayDB().__enter__()
        self.addCleanup(lambda: self.db.__exit__())

    # ── invariants ───────────────────────────────────────────────────────
    def test_01_backfill_carryover_and_kind_rename(self):
        rows = self.db.seed(24)
        self.db.apply_v171()
        # every row carried over: same id, plan_number verbatim
        count = self.db.sql("""
SELECT count(*) FROM nebula.blueprints_history b
JOIN nebula.implementation_plans_history i ON i.id = b.id
  AND i.plan_number IS NOT DISTINCT FROM b.plan_number;
""")[0][0]
        self.assertEqual(count, 24)
        # asset kinds renamed on the linked envelopes — SAME asset row
        count = self.db.sql("""
SELECT count(*) FROM semantics.canonical_asset ca
JOIN nebula.blueprints_history b ON b.asset_id = ca.id
WHERE ca.asset_kind = 'blueprint';
""")[0][0]
        self.assertEqual(count, 24)
        # and no stray implementation_plan-kind rows remain linked to blueprints
        count = self.db.sql("""
SELECT count(*) FROM semantics.canonical_asset ca
JOIN nebula.blueprints_history b ON b.asset_id = ca.id
WHERE ca.asset_kind = 'implementation_plan';
""")[0][0]
        self.assertEqual(count, 0)

    def test_02_payload_fold_and_compat_view_contract(self):
        self.db.seed(18)
        self.db.apply_v171()
        # payload contract: flat columns readable through the fold
        count = self.db.sql("""
SELECT count(*) FROM nebula.blueprints_history
WHERE payload->>'goal' IS NOT NULL
  AND payload->'acceptance_criteria' IS NOT NULL
  AND payload->>'files_affected' IS NOT NULL
  AND payload->>'project' = 'wrp';
""")[0][0]
        self.assertEqual(count, 18)
        # compat view serves the legacy contract from the new surface
        count = self.db.sql("""
SELECT count(*) FROM nebula.implementation_plans
WHERE goal IS NOT NULL AND status IN ('pending','draft')
  AND acceptance_criteria IS NOT NULL
  AND metadata->>'project' = 'wrp';
""")[0][0]
        self.assertEqual(count, 18)
        # dual numbering regimes preserved verbatim (18 seeded: 0001..0016
        # zero-padded, 8261601+ unsigned)
        count = self.db.sql("""
SELECT count(*) FROM nebula.v_blueprints
WHERE plan_number IN ('0001','0016','8261617');
""")[0][0]
        self.assertEqual(count, 3)

    def test_03_mirror_trigger_writes_through(self):
        # Transition-window semantics: V171 installs the write-through shim,
        # and a legacy INSERT (Conduit-era writer shape) mirrors to
        # blueprints_history while the shim is live.
        self.db.seed(1)
        self.db.apply_v171()
        self.db.sql("""
INSERT INTO nebula.implementation_plans_history
    (plan_number, title, goal, content, status)
VALUES ('9999', 'post-V171 legacy write', 'mirrored goal', 'mirrored content', 'pending');
""")
        count = self.db.sql("""
SELECT count(*) FROM nebula.blueprints_history
WHERE plan_number = '9999' AND payload->>'goal' = 'mirrored goal';
""")[0][0]
        self.assertEqual(count, 1)

    def test_04_v176_retires_mirror_and_freezes_legacy(self):
        # Post-V176 consumer-cutover semantics: the shim is gone (new legacy
        # INSERTs stay put and propagate nowhere) while the compat view keeps
        # serving the canonical surface unchanged.
        self.db.seed(1)
        self.db.apply_v171()
        self.db.apply_v176()

        self.db.sql("""
INSERT INTO nebula.implementation_plans_history
    (plan_number, title, goal, content, status)
VALUES ('9998', 'post-V176 frozen write', 'frozen goal', 'frozen content', 'pending');
""")
        # the frozen write stays ONLY in the legacy table
        count = self.db.sql("""
SELECT count(*) FROM nebula.blueprints_history
WHERE plan_number = '9998';
""")[0][0]
        self.assertEqual(count, 0)
        count = self.db.sql("""
SELECT count(*) FROM nebula.implementation_plans_history
WHERE plan_number = '9998';
""")[0][0]
        self.assertEqual(count, 1)
        # and the audit trigger is the only remaining one on the legacy table
        triggers = self.db.sql("""
SELECT count(*) FROM pg_trigger
WHERE tgrelid = 'nebula.implementation_plans_history'::regclass
  AND NOT tgisinternal;
""")[0][0]
        self.assertEqual(triggers, 0)

    def test_05_audit_trail_lands(self):
        self.db.seed(1)
        self.db.apply_v171()
        self.db.sql("""
INSERT INTO nebula.implementation_plans_history
    (plan_number, title, goal, status)
VALUES ('9998', 'audit probe', 'goal', 'draft');
""")
        count = self.db.sql("""
SELECT count(*) FROM tackle.system_logs
WHERE category = 'NEBULA_AUDIT'
  AND details->>'table' = 'nebula.blueprints_history'
  AND details->>'op' = 'INSERT'
  AND details->>'keys' = '9998';
""")[0][0]
        self.assertEqual(count, 1)

    def test_06_duplicate_plan_numbers_refused(self):
        self.db.seed(2)
        # The legacy UNIQUE constraint already blocks duplicates at the
        # storage layer, so the migration's own gate is defensive against
        # drift on databases where the constraint was lost. Simulate that:
        # drop the constraint, inject a duplicate, expect the gate to fire.
        self.db.sql("ALTER TABLE nebula.implementation_plans_history DROP CONSTRAINT implementation_plans_history_plan_number_key;")
        self.db.sql("""
UPDATE nebula.implementation_plans_history
SET plan_number = '0001'
WHERE plan_number = '0002';
""")
        with self.assertRaises(psycopg2.errors.RaiseException) as ctx:
            self.db.apply_v171()
        self.assertIn("duplicate plan_number", str(ctx.exception))

    def test_07_idempotent_reapply(self):
        self.db.seed(1)
        self.db.apply_v171()
        # re-apply: guarded, no duplicates, no errors
        self.db.apply_v171()
        count = self.db.sql("SELECT count(*) FROM nebula.blueprints_history;")[0][0]
        self.assertEqual(count, 1)

    def test_08_legacy_view_dropped_and_replaced(self):
        self.db.seed(1)
        self.db.apply_v171()
        # the old standalone view is gone: implementation_plans now resolves
        # to the V171 compat view (served from blueprints, exposes metadata)
        kind = self.db.sql("""
SELECT obj_description('nebula.implementation_plans'::regclass)::text;
""")[0][0]
        self.assertIsNotNone(kind)
        self.assertIn("compat view", kind)


if __name__ == "__main__":
    unittest.main(verbosity=2)
