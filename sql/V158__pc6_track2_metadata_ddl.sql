-- V158: PC6 Track 2 — additive metadata DDL on knowledge graph tables
--
-- Implements the ratified PC6 KG persistence projection contract (draft
-- 7cdf5120; ratification 35482577; re-ratification 9b2ffd4b; collision
-- ruling c0345945), Track 2 as directed by architect (thread dd666a94,
-- comment eaa233d1): identity / provenance / authority_ref / knowledge_level
-- / bitemporal / assertion metadata, additive and nullable (no fabrication),
-- plus the source_migration_id backfill policy.
--
-- Contract fingerprint (PC6 §5 + re-ratification 9b2ffd4b item 2):
--   vocab map (schemas/decision-b-freeze/relation-vocabulary-map.json,
--   commit ca17b623 + exceptions section per c0345945) sha256:
--     0a87a32e8e8688fbd542a1023058cc79e67e4262fc286c84754fcb85a56268b7
--   Contract fingerprint = sha256 over (this DDL + the map sha256 + the
--   section taxonomy + frozen relation list):
--     sha256("PC6-TRACK2|0a87a32e...68b7|sections=facts,entities,concepts,
--            propositions,authority_bindings,state_vectors|frozen=describes,
--            evaluated,instantiates,identified_by,governed_by,evidences|
--            ddl=<this file>")
--   The composite value is recorded in the PR + DBA execution record (R2);
--   the steward re-verifies it at seed time (gate fails closed on mismatch).
--
-- source_migration_id backfill policy (evidence-based):
--   * The steward (python/steward/migrate_graph.py) stamps
--     source_migration_id on every edge it inserts — verified live code
--     path (INSERT_EDGE, ~line 466). Edges inserted through the steward
--     are therefore always stamped.
--   * The live NULLs (13,012 rows) came from slice-pour seeders that
--     bypassed the steward. Exactly one edge generation carries a
--     verifiable provenance marker:
--     properties.projection_generation = 'decision-b-freeze-9a073a10/slice-relationship-pour-001'
--     (118 rows — ratified by the ontologist dd666a94, architect 35482577).
--   * Policy: those 118 rows are stamped with a generation-manifest row
--     in knowledge.graph_migrations (PC6 §6: the ledger doubles as the
--     generation manifest), with entity/edge counts matching observed
--     reality (0 entities for this batch; 118 edges) and migrated_at =
--     MIN(created_at) of the batch (evidence, not now()).
--   * The 12,894 backbone edges carry NO generation marker. Per doctrine
--     (unknown stays unknown) they remain NULL and are re-stamped only if
--     re-seeded through the steward. No invented provenance.
--   * No backfill of authority_ref / knowledge_level / valid_from /
--     valid_to: no per-row evidence exists. Deriving authority_ref from
--     entity_id prefixes would be a claim, not a fact — deferred pending
--     an explicit authority-mapping ruling.
--
-- Idempotent: IF NOT EXISTS / DO blocks throughout; re-run is a no-op.
-- Rollout: additive only; no existing column is altered or dropped;
-- no service restart required. R9 replication question applies (DDL).

BEGIN;

-- Row-neutral snapshot: this migration adds columns and backfills
-- provenance only. Compare against the same snapshot at the end, inside
-- the same transaction — robust against concurrent legitimate writes.
CREATE TEMP TABLE _pc6_pre AS
SELECT (SELECT count(*) FROM knowledge.graph_entities) AS entities,
       (SELECT count(*) FROM knowledge.graph_edges)    AS edges;

DO $$
DECLARE
    fp text;
    map_rows int;
    map_held int;
BEGIN
    -- The vocabulary map lives in the repo (schemas/decision-b-freeze/
    -- relation-vocabulary-map.json) and is NOT readable from SQL by
    -- design. Its sha256 is pinned at authoring time into the migration
    -- (see header + R2); here we only verify the ratified map's live
    -- projections remain consistent with what we're about to encode:
    -- 99 mapped field-name types, 6 held exception rows (ruling c0345945).
    SELECT count(*) INTO map_rows
      FROM knowledge.graph_edges
     WHERE properties ? 'relation_payload'
       AND relation_type IN ('references','contains','defines');
    SELECT count(*) INTO map_held
      FROM knowledge.graph_edges
     WHERE properties->'exception'->>'kind' = 'vocabulary-remap-exception';
    IF map_rows <> 112 THEN
        RAISE EXCEPTION 'precondition failed: expected 112 remapped rows, found %', map_rows;
    END IF;
    IF map_held <> 6 THEN
        RAISE EXCEPTION 'precondition failed: expected 6 held exception rows, found %', map_held;
    END IF;

    RAISE NOTICE 'PC6 Track 2 preconditions OK: remap state 112/6 as ratified (c0345945)';
END $$;

-- ── 1. Schema-level contract note ──────────────────────────────────────
COMMENT ON SCHEMA knowledge IS
'Knowledge graph (PC6 persistence projection contract, fingerprint lineage 9a073a10). Metadata columns per Track 2: authority_ref / knowledge_level / assertion / valid_from / valid_to — nullable, explicit-when-known, never fabricated.';

-- ── 2. graph_entities: metadata columns ────────────────────────────────
ALTER TABLE knowledge.graph_entities
    ADD COLUMN IF NOT EXISTS authority_ref   text,
    ADD COLUMN IF NOT EXISTS knowledge_level text,
    ADD COLUMN IF NOT EXISTS assertion       text NOT NULL DEFAULT 'asserted',
    ADD COLUMN IF NOT EXISTS valid_from      timestamptz,
    ADD COLUMN IF NOT EXISTS valid_to        timestamptz;

-- knowledge_level: AGENTS.md L1-L4 vocabulary (Axis 1), NULL = unknown.
ALTER TABLE knowledge.graph_entities
    DROP CONSTRAINT IF EXISTS ck_graph_entities_knowledge_level;
ALTER TABLE knowledge.graph_entities
    ADD CONSTRAINT ck_graph_entities_knowledge_level
    CHECK (knowledge_level IS NULL OR knowledge_level IN ('L1','L2','L3','L4'));

-- assertion status: drafted vocabulary {asserted, inferred, disputed}.
ALTER TABLE knowledge.graph_entities
    DROP CONSTRAINT IF EXISTS ck_graph_entities_assertion;
ALTER TABLE knowledge.graph_entities
    ADD CONSTRAINT ck_graph_entities_assertion
    CHECK (assertion IN ('asserted','inferred','disputed'));

-- Bitemporal sanity: a bounded validity interval must be forward-running.
ALTER TABLE knowledge.graph_entities
    DROP CONSTRAINT IF EXISTS ck_graph_entities_valid_window;
ALTER TABLE knowledge.graph_entities
    ADD CONSTRAINT ck_graph_entities_valid_window
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to);

COMMENT ON COLUMN knowledge.graph_entities.authority_ref IS
'PC6 Track 2: which system owns the truth for this entity (e.g. semantics.canonical_asset, resolution.*, aegis.*). NULL until an explicit authority mapping exists — no prefix-derived backfill.';
COMMENT ON COLUMN knowledge.graph_entities.knowledge_level IS
'PC6 Track 2: abstraction altitude L1-L4 per AGENTS.md knowledge stratification. NULL = unknown (never guessed).';
COMMENT ON COLUMN knowledge.graph_entities.assertion IS
'PC6 Track 2: assertion status {asserted, inferred, disputed}; defaults to asserted. Aligned with graph_edges.resolution semantics.';
COMMENT ON COLUMN knowledge.graph_entities.valid_from IS
'PC6 Track 2 bitemporal: valid-time lower bound (when true in the world). NULL until real valid-time data exists — never fabricated.';
COMMENT ON COLUMN knowledge.graph_entities.valid_to IS
'PC6 Track 2 bitemporal: valid-time upper bound. NULL = unbounded/unknown.';

-- ── 3. graph_edges: metadata columns ───────────────────────────────────
ALTER TABLE knowledge.graph_edges
    ADD COLUMN IF NOT EXISTS assertion  text NOT NULL DEFAULT 'asserted',
    ADD COLUMN IF NOT EXISTS valid_from timestamptz,
    ADD COLUMN IF NOT EXISTS valid_to   timestamptz;

ALTER TABLE knowledge.graph_edges
    DROP CONSTRAINT IF EXISTS ck_graph_edges_assertion;
ALTER TABLE knowledge.graph_edges
    ADD CONSTRAINT ck_graph_edges_assertion
    CHECK (assertion IN ('asserted','inferred','disputed'));

ALTER TABLE knowledge.graph_edges
    DROP CONSTRAINT IF EXISTS ck_graph_edges_valid_window;
ALTER TABLE knowledge.graph_edges
    ADD CONSTRAINT ck_graph_edges_valid_window
    CHECK (valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to);

COMMENT ON COLUMN knowledge.graph_edges.assertion IS
'PC6 Track 2: assertion status {asserted, inferred, disputed}; defaults to asserted. Coexists with resolution {resolved, unresolved}.';
COMMENT ON COLUMN knowledge.graph_edges.valid_from IS
'PC6 Track 2 bitemporal: valid-time lower bound. NULL until real valid-time data exists.';
COMMENT ON COLUMN knowledge.graph_edges.valid_to IS
'PC6 Track 2 bitemporal: valid-time upper bound. NULL = unbounded/unknown.';

-- ── 4. source_migration_id backfill (generation manifest + stamp) ─────
-- One generation has verifiable provenance: the relationship pour batch
-- (properties.projection_generation = decision-b-freeze-9a073a10/
-- slice-relationship-pour-001, 118 rows — ratified). Record it in the
-- ledger as the generation manifest (PC6 §6), then stamp its edges.
DO $$
DECLARE
    gen_manifest uuid;
    gen_literal  text := 'decision-b-freeze-9a073a10/slice-relationship-pour-001';
    batch_min    timestamptz;
    batch_edges  int;
    stamped      int;
BEGIN
    SELECT count(*), min(created_at) INTO batch_edges, batch_min
      FROM knowledge.graph_edges
     WHERE properties->>'projection_generation' = gen_literal;

    IF batch_edges <> 118 THEN
        RAISE EXCEPTION 'backfill precondition failed: pour generation rows = % (expected 118)', batch_edges;
    END IF;

    SELECT id INTO gen_manifest
      FROM knowledge.graph_migrations
     WHERE source_file = 'decision-b-freeze/slice-relationship-pour-001';

    IF gen_manifest IS NULL THEN
        INSERT INTO knowledge.graph_migrations
            (source_file, file_checksum, entity_count, edge_count,
             cross_ref_count, version, migrated_at)
        VALUES
            ('decision-b-freeze/slice-relationship-pour-001',
             md5(gen_literal),            -- batch fingerprint (slice id hash; NOT a file checksum)
             0,                           -- entities produced by this batch (none; verified live)
             batch_edges,
             0,
             'decision-b-freeze-9a073a10',
             batch_min)                   -- evidence-based, not now()
        RETURNING id INTO gen_manifest;
    END IF;

    UPDATE knowledge.graph_edges
       SET source_migration_id = gen_manifest
     WHERE properties->>'projection_generation' = gen_literal
       AND source_migration_id IS NULL;
    GET DIAGNOSTICS stamped = ROW_COUNT;

    RAISE NOTICE 'backfill: generation manifest %; stamped % rows', gen_manifest, stamped;
END $$;

-- ── 5. Postconditions (migration fails closed if violated) ────────────
DO $$
DECLARE
    n int;
BEGIN
    -- Entities: all columns present.
    SELECT count(*) INTO n FROM information_schema.columns
     WHERE table_schema='knowledge' AND table_name='graph_entities'
       AND column_name IN ('authority_ref','knowledge_level','assertion','valid_from','valid_to');
    IF n <> 5 THEN RAISE EXCEPTION 'entity metadata columns missing (%/5)', n; END IF;

    -- Edges: metadata columns present.
    SELECT count(*) INTO n FROM information_schema.columns
     WHERE table_schema='knowledge' AND table_name='graph_edges'
       AND column_name IN ('assertion','valid_from','valid_to');
    IF n <> 3 THEN RAISE EXCEPTION 'edge metadata columns missing (%/3)', n; END IF;

    -- No fabrication: backfill policy fields still all-NULL.
    SELECT count(*) INTO n FROM knowledge.graph_entities
     WHERE authority_ref IS NOT NULL OR knowledge_level IS NOT NULL
        OR valid_from IS NOT NULL OR valid_to IS NOT NULL;
    IF n <> 0 THEN RAISE EXCEPTION 'fabrication check failed: % entity rows carry unearned metadata', n; END IF;

    SELECT count(*) INTO n FROM knowledge.graph_edges
     WHERE valid_from IS NOT NULL OR valid_to IS NOT NULL;
    IF n <> 0 THEN RAISE EXCEPTION 'fabrication check failed: % edge rows carry unearned bitemporal data', n; END IF;

    -- Defaults hold on the full existing population.
    SELECT count(*) INTO n FROM knowledge.graph_entities WHERE assertion <> 'asserted';
    IF n <> 0 THEN RAISE EXCEPTION 'assertion default violated on % entity rows', n; END IF;
    SELECT count(*) INTO n FROM knowledge.graph_edges WHERE assertion <> 'asserted';
    IF n <> 0 THEN RAISE EXCEPTION 'assertion default violated on % edge rows', n; END IF;

    -- Backfill: exactly the 118 pour rows stamped; manifest row present.
    SELECT count(*) INTO n FROM knowledge.graph_edges
     WHERE properties->>'projection_generation' = 'decision-b-freeze-9a073a10/slice-relationship-pour-001'
       AND source_migration_id IS NOT NULL;
    IF n <> 118 THEN RAISE EXCEPTION 'backfill coverage %/118', n; END IF;

    SELECT count(*) INTO n FROM knowledge.graph_migrations
     WHERE source_file = 'decision-b-freeze/slice-relationship-pour-001';
    IF n <> 1 THEN RAISE EXCEPTION 'generation manifest rows = % (expected 1)', n; END IF;

    -- Row-neutrality: this migration must not add or remove any rows
    -- (compared to the in-transaction snapshot taken at BEGIN).
    IF (SELECT entities FROM _pc6_pre) <> (SELECT count(*) FROM knowledge.graph_entities) THEN
        RAISE EXCEPTION 'entity rows changed by this migration: % -> %',
            (SELECT entities FROM _pc6_pre), (SELECT count(*) FROM knowledge.graph_entities);
    END IF;
    IF (SELECT edges FROM _pc6_pre) <> (SELECT count(*) FROM knowledge.graph_edges) THEN
        RAISE EXCEPTION 'edge rows changed by this migration: % -> %',
            (SELECT edges FROM _pc6_pre), (SELECT count(*) FROM knowledge.graph_edges);
    END IF;

    RAISE NOTICE 'V158 postconditions: all PASS';
END $$;

COMMIT;
