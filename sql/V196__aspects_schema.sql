-- V196: Aspects governed-layer schema (Aspect G6)
-- Review record bc724f6b finding 2: the binding port
-- (python/aspects/binding_port.py) queries aspects.governed_tag_vocabulary
-- and aspects.tag_binding, but no in-repo migration created that schema.
-- This DDL matches the port's queries exactly: column names per SELECT/
-- INSERT, the G2 binding lifecycle statuses as a CHECK constraint, and the
-- vocabulary FK on tag_binding. Contract: typespec/v1/aspects/main.tsp,
-- manifest: typespec/v1/aspects/contract-manifest.json,
-- Python conformance: python/aspects/contract.py.

-- ============================================================================
-- 1. SCHEMA
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS aspects;

-- ============================================================================
-- 2. GOVERNED TAG VOCABULARY
-- ============================================================================
CREATE TABLE IF NOT EXISTS aspects.governed_tag_vocabulary (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    normalized_name text NOT NULL,
    member_kind     text NOT NULL,
    definition      text NOT NULL,
    applies_to      text NOT NULL,
    parent_id       uuid REFERENCES aspects.governed_tag_vocabulary(id),
    ratified_by     text,
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    expired_at      timestamptz
);

-- Vocabulary names are unique among active entries.
CREATE UNIQUE INDEX IF NOT EXISTS governed_tag_vocabulary_active_name_idx
    ON aspects.governed_tag_vocabulary (name)
    WHERE expired_at IS NULL;

CREATE INDEX IF NOT EXISTS governed_tag_vocabulary_normalized_idx
    ON aspects.governed_tag_vocabulary (normalized_name)
    WHERE expired_at IS NULL;

-- ============================================================================
-- 3. TAG BINDINGS (G2 lifecycle: proposed -> approved|rejected -> expired)
-- ============================================================================
CREATE TABLE IF NOT EXISTS aspects.tag_binding (
    id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    governed_tag_id           uuid NOT NULL REFERENCES aspects.governed_tag_vocabulary(id),
    source_identity           text NOT NULL,
    source_revision           text NOT NULL,
    namespace                 text NOT NULL,
    tag_key                   text NOT NULL,
    normalized_value          text NOT NULL,
    expression_observation_id text,
    status                    text NOT NULL
                              CHECK (status IN ('proposed', 'approved', 'rejected', 'expired')),
    bound_by                  text,
    created_at                timestamptz NOT NULL DEFAULT now(),
    expired_at                timestamptz,
    -- Expiring stamps expired_at; an active binding must not carry one.
    CHECK (expired_at IS NULL OR status = 'expired')
);

CREATE INDEX IF NOT EXISTS tag_binding_governed_tag_idx
    ON aspects.tag_binding (governed_tag_id);

CREATE INDEX IF NOT EXISTS tag_binding_source_identity_idx
    ON aspects.tag_binding (source_identity);

CREATE INDEX IF NOT EXISTS tag_binding_active_status_idx
    ON aspects.tag_binding (status)
    WHERE expired_at IS NULL;
