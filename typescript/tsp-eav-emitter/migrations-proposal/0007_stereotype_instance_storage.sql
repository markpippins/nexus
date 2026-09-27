-- ============================================================================
-- shrapnel migration 0007 (PROPOSAL, pending DBA): instance-storage registry
-- ----------------------------------------------------------------------------
-- Companion to the tsp-eav-emitter @instanceStorage decorator (PR #591).
-- This table is deliberately OUTSIDE the stereotype type contract:
--   * no stereotype_field rows -> no contract_fingerprint v2 involvement;
--   * no freeze-trigger coverage -> storage class may be re-classified by
--     UPDATE without forcing a new stereotype revision;
--   * one row per stereotype (unique stereotype_name), latest registration
--     wins; revision_ref records which revision the disposition was made
--     against for audit purposes.
-- If the roundtable instead rules that storage class belongs INSIDE the
-- contract, this table is unnecessary — that disposition would live in the
-- field set and evolve through normal revisions. Do not apply until ruled.
-- ============================================================================

BEGIN;

CREATE SEQUENCE IF NOT EXISTS shrapnel.stereotype_instance_storage_seq;

CREATE TABLE IF NOT EXISTS shrapnel.stereotype_instance_storage (
    id              bigint PRIMARY KEY DEFAULT nextval('shrapnel.stereotype_instance_storage_seq'::regclass),
    stereotype_name text    NOT NULL,
    storage_class   text    NOT NULL CHECK (storage_class IN ('shrapnel', 'mongodb', 'jsonb_table')),
    revision_ref    bigint  REFERENCES shrapnel.stereotype_revision(id),
    registered_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_instance_storage_stereotype UNIQUE (stereotype_name)
);

COMMENT ON TABLE  shrapnel.stereotype_instance_storage IS 'Where instances of a shrapnel-typed shape live (shrapnel | mongodb | jsonb_table). Metadata outside the type contract; proposed by PR #591 decorator plumbing, pending DBA ruling.';
COMMENT ON COLUMN shrapnel.stereotype_instance_storage.storage_class IS 'shrapnel = object_instance/value_* tables; mongodb = dedicated collection; jsonb_table = JSONB column in a PG table named for the type.';

COMMIT;
