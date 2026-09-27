-- =============================================================================
-- CANONICAL ROLE-MEMORY SHAPE (single source of truth) — V178-ratified
-- =============================================================================
-- The ONLY checked-in place where the tackle.memory / tackle.role_memory
-- reconstruction shape exists. Every reconstruction surface (seed_manifest.py,
-- tackle-mcp/db.ts, tackle-srv/db.ts, schemas/migrations/tackle/
-- memory_procedure_registry.sql, and the conformance fixture) MUST consume this
-- file rather than restating the shape inline.
--
-- Ratified shape: V178 (merge #305) — exclusion constraint named
-- uq_role_memory_validity with COALESCE(expiration_dt,'infinity') and explicit
-- '[)' bounds, subsuming V155's partial unique (uq_role_memory_active).
--
-- Template substitution contract (all consumers):
--   __SCHEMA__   -> target schema (tackle in production; pg_temp/scratch in tests)
--   __REFTABLE__ -> the memory table reference inside the FK, qualified
--                   ({__SCHEMA__}.memory by default; pg_temp.memory for the
--                   temp-table fixture)
--   __TABLE_SUFFIX__ -> empty in production/tests; '_shape' for the CI
--                   shadow-seed world (tables named memory_shape etc.)
--
-- Consumers MUST NOT restate any column, constraint, or index defined here.
-- bin/tests/test_canonical_shape_parity.py makes drift structurally fail.
--
-- Idempotent: safe to apply repeatedly (IF NOT EXISTS throughout).
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS __SCHEMA__.memory (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    body_md     TEXT NOT NULL DEFAULT '',
    tags        TEXT[] NOT NULL DEFAULT '{}',
    triggers    TEXT[] NOT NULL DEFAULT '{}',
    mcp_tools   TEXT[] NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS __SCHEMA__.role_memory (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id     UUID NOT NULL REFERENCES __REFTABLE__(id) ON DELETE CASCADE,
    role          TEXT NOT NULL,
    as_of_dt      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expiration_dt TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- V178: no overlapping validity intervals per (memory, role); open-open
    -- range with infinity coalescing subsumes and replaces V155's partial
    -- unique (uq_role_memory_active) — that name must NOT reappear.
    CONSTRAINT uq_role_memory_validity
        EXCLUDE USING gist (
            memory_id WITH =,
            role WITH =,
            tstzrange(as_of_dt, COALESCE(expiration_dt, 'infinity'), '[)') WITH &&
        )
);

CREATE INDEX IF NOT EXISTS idx_role_memory_as_of
    ON __SCHEMA__.role_memory (role, as_of_dt DESC);

CREATE INDEX IF NOT EXISTS idx_role_memory_expiration
    ON __SCHEMA__.role_memory (role, expiration_dt DESC NULLS FIRST);
