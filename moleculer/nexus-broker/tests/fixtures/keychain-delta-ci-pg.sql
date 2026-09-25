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
-- Seeded rows exercise the resolver's real grouping rules:
--   2 amendment-chained reports     -> 1 logical instance (latest issuance)
--   2 supersession-tagged analyses  -> 1 logical instance (chain tip)
--   1 standalone inspection         -> 1 logical instance
--   => exactly 4 logical instances in the anchor state vector.
--
-- The service never writes to PG (read-only source), so these rows are
-- static input and safe across repeated runs against the same container.

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

INSERT INTO nebula.agent_records
  (id, record_type, role, title, content, tags, created_at, level, visibility_scope)
VALUES
  -- amendment chain: 05aa1111 issued then amended by 05aa1112 (same token)
  ('11111111-1111-4111-8111-111111111111', 'report', 'engineer',
   'Report 05aa1111: initial issuance', 'initial body', '{}',
   now() - interval '30 minutes', 1, 'all'),
  ('22222222-2222-4222-8222-222222222222', 'report', 'engineer',
   'Report 05aa1111: amendment one', 'amended body', '{}',
   now() - interval '20 minutes', 1, 'all'),
  -- explicit supersession chain: bbbb.. supersedes aaaa.. (tag direction)
  ('33333333-3333-4333-8333-333333333333', 'analysis', 'analyst',
   'Analysis: superseded baseline', 'baseline body',
   '{supersedes:}', now() - interval '15 minutes', 2, 'all'),
  ('44444444-4444-4444-8444-444444444444', 'analysis', 'analyst',
   'Analysis: superseding revision', 'revision body',
   '{supersedes:33333333}', now() - interval '10 minutes', 2, 'all'),
  -- standalone single-issuance instance
  ('55555555-5555-4555-8555-555555555555', 'inspection', 'inspector',
   'Inspection: standalone observation', 'inspection body', '{}',
   now() - interval '5 minutes', 3, 'all')
ON CONFLICT (id) DO NOTHING;

CREATE SCHEMA IF NOT EXISTS resolution;
CREATE TABLE IF NOT EXISTS resolution.keychain_event_outbox (
  id bigserial PRIMARY KEY,
  source_namespace text NOT NULL,
  source_event_id text NOT NULL,
  event_kind text NOT NULL,
  outcome text,
  schema_version int DEFAULT 1,
  aggregate_id text,
  causation_id text,
  correlation_id text,
  actor text,
  contract_id text,
  evaluator_id text,
  law_id text,
  effective_at timestamptz,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  read_set jsonb,
  payload jsonb,
  checkpoint_status text NOT NULL DEFAULT 'pending',
  claimed_at timestamptz,
  delivery_attempts int NOT NULL DEFAULT 0,
  last_error text
);
CREATE UNIQUE INDEX IF NOT EXISTS keychain_event_outbox_event_uq
  ON resolution.keychain_event_outbox (source_namespace, source_event_id);

CREATE SCHEMA IF NOT EXISTS semantics;
CREATE TABLE IF NOT EXISTS semantics.canonical_asset (
  canonical_asset_id text PRIMARY KEY,
  asset_kind text NOT NULL,
  expired_at timestamptz
);
