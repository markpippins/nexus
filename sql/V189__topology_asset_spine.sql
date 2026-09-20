-- =============================================================================
-- V189 — Topology asset-spine pre-stage (DBA, staged inert)
-- =============================================================================
-- Stage 1: register the infra asset-kind vocabulary as a CONTRACT on
--          semantics.canonical_asset. The table currently carries 26 live
--          kinds with NO CHECK constraint (vocabulary by convention only).
--          This migration installs chk_canonical_asset_kind covering the 26
--          live kinds + 3 infra additions (host, database, broker). The
--          pre-install preflight refuses if live has grown a kind the list
--          does not know (the loud-ELSE discipline: an unknown kind must be
--          named, not silently permitted or silently forbidden).
-- Stage 2: additive asset_id + origin columns on the three declared/observed
--          topology surfaces so each row can bind to its canonical asset.
--          Backfill policy: NULL everywhere — absent-is-honest. No row is
--          invented into the spine; binding happens through the existing
--          registration path (endpoint-register.py), which is upgraded in a
--          follow-up slice to write these columns.
--
-- ⚠️  STAGED INERT — applies only on explicit operator go. Nothing on live
--     mutates by this file existing. Apply-order: any time (additive-only,
--     no writer depends on the new columns until the registration upgrade
--     lands). Companions: bin/endpoint-register.py (post-apply slice).
--
-- Evidence base (live census 2026-09-20, titanium):
--   semantics.canonical_asset: 26 distinct kinds, no CHECK, PK(id uuid),
--     canonical_asset_id text NOT NULL (keys like 'asset:nexus:...'),
--     canonical_key jsonb, source_hash/content_hash text.
--   registry.servers (id bigint, hostname, ip_address, status, ...):
--     declared fleet membership (ansible-derived).
--   terrain.servers (id bigint, hostname, ip_address, status, ...):
--     observed machine state.
--   terrain.service_endpoints (id uuid, host, instance, ip inet, port,
--     scheme, status, unit, last_heartbeat): observed endpoint registrations.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- PREFLIGHT — refuse anything unexpected, name it, write nothing.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    v_unknown text;
    v_missing int;
BEGIN
    -- 0a. every DISTINCT live kind must be in the Stage-1 vocabulary
    SELECT string_agg(DISTINCT k.asset_kind, ', ' ORDER BY k.asset_kind)
      INTO v_unknown
      FROM semantics.canonical_asset k
     WHERE NOT k.asset_kind = ANY (ARRAY[
           -- the 26 live kinds (census 2026-09-20)
           'agent_record:analysis','agent_record:architecture_note',
           'agent_record:assessment','agent_record:decision',
           'agent_record:engineering_log','agent_record:inspection',
           'agent_record:prompt','agent_record:report','agent_record:response',
           'candidate','cli_tool','concept','document','feature','file',
           'implementation_plan','knowledge_entity','mcp_server','plan',
           'requirement','service','session_log','subsystem','system',
           'transcript','work_request',
           -- Stage-1 infra additions
           'host','database','broker']);
    IF v_unknown IS NOT NULL THEN
        RAISE EXCEPTION 'V189-PREFLIGHT-001: unmapped asset_kind(s) on live: [%] — extend the vocabulary list and re-review', v_unknown;
    END IF;

    -- 0b. target tables must exist with the expected identity columns
    SELECT count(*) INTO v_missing
      FROM (VALUES ('registry.servers','id'),
                   ('terrain.servers','id'),
                   ('terrain.service_endpoints','id')) t(tbl, col)
     WHERE to_regclass(t.tbl) IS NULL
        OR NOT EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_schema = split_part(t.tbl,'.',1)
                          AND table_name  = split_part(t.tbl,'.',2)
                          AND column_name = t.col);
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V189-PREFLIGHT-002: % expected topology surface(s) missing or missing identity column', v_missing;
    END IF;

    -- 0c. the new columns must not already exist (this migration is the only
    --     writer; a second presence means someone applied a variant)
    SELECT count(*) INTO v_missing
      FROM (VALUES ('registry.servers','asset_id'),
                   ('terrain.servers','asset_id'),
                   ('terrain.service_endpoints','asset_id')) t(tbl, col)
     WHERE EXISTS (SELECT 1 FROM information_schema.columns
                    WHERE table_schema = split_part(t.tbl,'.',1)
                      AND table_name  = split_part(t.tbl,'.',2)
                      AND column_name = t.col);
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V189-PREFLIGHT-003: % topology surface(s) already carry asset_id — refusing to double-apply', v_missing;
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- STAGE 1 — the kind vocabulary becomes a contract.
-- -----------------------------------------------------------------------------
ALTER TABLE semantics.canonical_asset
    ADD CONSTRAINT chk_canonical_asset_kind
    CHECK (asset_kind = ANY (ARRAY[
           'agent_record:analysis','agent_record:architecture_note',
           'agent_record:assessment','agent_record:decision',
           'agent_record:engineering_log','agent_record:inspection',
           'agent_record:prompt','agent_record:report','agent_record:response',
           'candidate','cli_tool','concept','document','feature','file',
           'implementation_plan','knowledge_entity','mcp_server','plan',
           'requirement','service','session_log','subsystem','system',
           'transcript','work_request',
           'host','database','broker']));

-- -----------------------------------------------------------------------------
-- STAGE 2 — additive asset_id + origin columns on the three topology surfaces.
-- -----------------------------------------------------------------------------
-- registry.servers: DECLARED membership (ansible-derived spine)
ALTER TABLE registry.servers
    ADD COLUMN asset_id   uuid,
    ADD COLUMN origin_source text NOT NULL DEFAULT 'registry.servers',
    ADD COLUMN origin_ref    text;

COMMENT ON COLUMN registry.servers.asset_id   IS 'FK → semantics.canonical_asset(id), kind=host. NULL = not yet bound (absent-is-honest).';
COMMENT ON COLUMN registry.servers.origin_source IS 'Declared-layer provenance: which surface attests this row.';
COMMENT ON COLUMN registry.servers.origin_ref    IS 'Declared-layer locator: ansible inventory host key or equivalent.';

ALTER TABLE registry.servers
    ADD CONSTRAINT fk_registry_servers_asset
    FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);

-- terrain.servers: OBSERVED machine state
ALTER TABLE terrain.servers
    ADD COLUMN asset_id   uuid,
    ADD COLUMN origin_source text NOT NULL DEFAULT 'terrain.servers',
    ADD COLUMN origin_ref    text;

COMMENT ON COLUMN terrain.servers.asset_id   IS 'FK → semantics.canonical_asset(id), kind=host. NULL = not yet bound.';
COMMENT ON COLUMN terrain.servers.origin_source IS 'Observed-layer provenance.';
COMMENT ON COLUMN terrain.servers.origin_ref    IS 'Observed-layer locator (probe source).';

ALTER TABLE terrain.servers
    ADD CONSTRAINT fk_terrain_servers_asset
    FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);

-- terrain.service_endpoints: OBSERVED service registrations
ALTER TABLE terrain.service_endpoints
    ADD COLUMN asset_id   uuid,
    ADD COLUMN origin_source text NOT NULL DEFAULT 'terrain.service_endpoints',
    ADD COLUMN origin_ref    text;

COMMENT ON COLUMN terrain.service_endpoints.asset_id IS 'FK → semantics.canonical_asset(id), kind=service (or mcp_server). NULL = not yet bound.';
COMMENT ON COLUMN terrain.service_endpoints.origin_source IS 'Observed-layer provenance.';
COMMENT ON COLUMN terrain.service_endpoints.origin_ref    IS 'Observed-layer locator (register invocation evidence).';

ALTER TABLE terrain.service_endpoints
    ADD CONSTRAINT fk_terrain_service_endpoints_asset
    FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);

-- Binding uniqueness: one asset binds to at most one row per surface.
-- (uq on asset_id where not null)
CREATE UNIQUE INDEX IF NOT EXISTS uq_registry_servers_asset_id
    ON registry.servers (asset_id) WHERE asset_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_terrain_servers_asset_id
    ON terrain.servers (asset_id) WHERE asset_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_terrain_service_endpoints_asset_id
    ON terrain.service_endpoints (asset_id) WHERE asset_id IS NOT NULL;

COMMIT;

-- =============================================================================
-- POST-APPLY VERIFICATION (manual, run after apply):
--   1. kinds constraint present:
--        SELECT conname FROM pg_constraint
--         WHERE conrelid='semantics.canonical_asset'::regclass AND conname='chk_canonical_asset_kind';
--   2. three surfaces carry asset_id (information_schema.columns)
--      and all backfilled NULL (SELECT count(*) ... WHERE asset_id IS NOT NULL → 0).
--   3. insertion of a bogus kind now REFUSES (CHK-001 fires).
-- =============================================================================
-- FOLLOW-UP SLICE (not this migration): upgrade bin/endpoint-register.py
-- register/heartbeat to resolve-or-mint the canonical asset and write
-- asset_id + origin_ref; then a declared-vs-observed reconciliation view
-- joins registry.servers → terrain.servers → terrain.service_endpoints
-- through the spine.
-- =============================================================================
