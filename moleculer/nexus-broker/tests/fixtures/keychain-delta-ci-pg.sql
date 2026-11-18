-- keychain-delta CI shim: minimal-but-real schema for the keychain-snapshot
-- service's PG reads in a fresh CI PostgreSQL (broker-e2e workflow).
--
-- Truth table (no fiction):
--   REAL   : nebula.agent_records (the service's source of truth for the
--            state vector; rows here drive resolveKeychainEntries), plus the
--            outbox table the poller claims rows from (no fake deliveries —
--            an empty outbox is simply idle).
--   FAKE   : semantics.canonical_asset (DBA backfill surface; empty here —
--            the service already degrades to null asset_ids when absent).
--
-- NO SEEDED ROWS. This file used to ship five `nebula.agent_records` rows
-- (an amendment chain, a supersession chain and a standalone) that the delta
-- suite silently depended on: the anchor state vector resolved to exactly four
-- logical instances because the seed said so, not because the test built them.
-- That is fixture-as-scaffolding, and it is the reason this file could drift
-- from the schema it mirrors without anyone noticing.
--
-- The harness now runs against a per-run database built from the canonical
-- bootstrap (tests/helpers/isolated-pg.js), and every test that needs rows
-- creates them itself and removes them again. This file keeps only the schema,
-- so it stays a faithful projection of `resolution.keychain_event_outbox` and
-- `tests/verify-outbox-fixture.js` keeps comparing like with like.

CREATE SCHEMA IF NOT EXISTS nebula;
CREATE TABLE IF NOT EXISTS nebula.agent_records (
  id uuid PRIMARY KEY,
  record_type text NOT NULL,
  role text NOT NULL,
  title text NOT NULL,
  content text,
  source_path text,
  metadata jsonb,
  tags text[] DEFAULT '{}',
  system_id text,
  subsystem_id text,
  feature_id text,
  plan_ref text,
  candidate_id text,
  requirement_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  level int,
  visibility_scope text,
  model text
);


CREATE SCHEMA IF NOT EXISTS resolution;
-- Column-for-column copy of production's table (23 columns; same types,
-- nullability and defaults; same primary key, unique key, partial pending
-- index and CHECKs).
--
-- Fidelity here is not cosmetic. This file is the ONLY outbox schema a CI
-- database has, and the harness creates its test queue with
-- `CREATE TABLE ... (LIKE resolution.keychain_event_outbox INCLUDING ALL)` —
-- so every omission in this fixture becomes a missing column in the table the
-- broker actually polls. That is exactly what happened: this fixture omitted
-- `created_at` and `delivered_at` and typed `id` as bigserial, the clone
-- inherited all three defects, `/keychain-snapshot/status` failed with
-- `column "created_at" does not exist`, and the delta suite reported only
-- "broker did not become healthy" because the broker's stderr was discarded.
-- Keep this in step with `resolution.keychain_event_outbox`.
CREATE TABLE IF NOT EXISTS resolution.keychain_event_outbox (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_namespace text NOT NULL,
  source_event_id text NOT NULL,
  event_kind text NOT NULL,
  outcome text NOT NULL
    CHECK (outcome = ANY (ARRAY['committed','refused','rejected','unknown','stale',
                                 'drift','quarantined','superseded','rolled_back','failed'])),
  schema_version integer NOT NULL DEFAULT 1,
  aggregate_id text,
  causation_id text,
  correlation_id text,
  actor text,
  contract_id text,
  evaluator_id text,
  law_id text,
  effective_at timestamptz,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  read_set jsonb NOT NULL DEFAULT '{}'::jsonb,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  checkpoint_status text NOT NULL DEFAULT 'pending'
    CHECK (checkpoint_status = ANY (ARRAY['pending','delivering','delivered',
                                          'not_applicable','failed'])),
  delivery_attempts integer NOT NULL DEFAULT 0,
  delivered_at timestamptz,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  claimed_at timestamptz,
  -- Declared as a table CONSTRAINT, as the canonical schema does: a standalone
  -- unique index would be equivalent for ON CONFLICT but not for the shape a
  -- `LIKE ... INCLUDING ALL` clone reproduces, and
  -- tests/verify-outbox-fixture.js compares constraint semantics.
  CONSTRAINT keychain_event_outbox_source_event_uq UNIQUE (source_namespace, source_event_id)
);
CREATE INDEX IF NOT EXISTS idx_keychain_event_outbox_pending
  ON resolution.keychain_event_outbox (checkpoint_status, recorded_at)
  WHERE checkpoint_status = ANY (ARRAY['pending','failed']);

CREATE SCHEMA IF NOT EXISTS semantics;
CREATE TABLE IF NOT EXISTS semantics.canonical_asset (
  canonical_asset_id text PRIMARY KEY,
  asset_kind text NOT NULL,
  expired_at timestamptz
);
