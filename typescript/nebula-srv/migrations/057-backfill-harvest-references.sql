-- ═══════════════════════════════════════════════════════════════════════
--  Migration 057 — Backfill Harvest References from Conversation Blocks
--
--  RENUMBERED from 004-backfill-harvest-references.sql (thread 6bba5dd3):
--  duplicate version prefixes make the startup runner (../src/migrate.ts)
--  skip the lex-second twin on a fresh database. Content unchanged and
--  replay-safe (INSERT ... WHERE NOT EXISTS idempotency).
--
--  Populates nebula.harvest_references_history with inferred references
--  derived from existing conversation blocks. This creates the graph
--  adjacency data needed by the Nebula UI Graph view.
--
--  Reference types created:
--    1. adjacency      — consecutive blocks within the same snapshot
--                        (block_index N → block_index N+1)
--                        confidence: 0.95 (direct sequential relationship)
--    2. same_content   — blocks sharing a non-empty content_hash within
--                        the same conversation
--                        confidence: 0.75 (likely duplicated/related content)
--    3. cross_snapshot — blocks with the same content_hash across different
--                        snapshots of the same conversation
--                        confidence: 0.50 (versioned content)
--    4. parent_child   — blocks linked via parent_block_id (hierarchical)
--                        confidence: 0.90 (explicit parent-child structure)
--
--  All references start as CONFIRMED since they're derived from actual data.
--
--  Safe to re-run: uses INSERT ... WHERE NOT EXISTS with idempotency check.
-- ═══════════════════════════════════════════════════════════════════════

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════
--  1. Adjacency References
--     Connect block N → block N+1 within each snapshot.
--     Skip blocks with empty content hash.
-- ═══════════════════════════════════════════════════════════════════════

WITH adjacent_pairs AS (
    SELECT
        cb1.conversation_id,
        cb1.snapshot_id,
        cb1.id AS source_block_id,
        cb2.id AS target_block_id,
        cb1.block_index AS source_idx,
        cb2.block_index AS target_idx
    FROM nebula.conversation_blocks cb1
    JOIN nebula.conversation_blocks cb2
        ON cb2.snapshot_id = cb1.snapshot_id
        AND cb2.block_index = cb1.block_index + 1
    WHERE cb1.content_hash != 'e3b0c44298fc1c14'  -- skip empty blocks
      AND cb2.content_hash != 'e3b0c44298fc1c14'
),
adjacency_inserts AS (
    INSERT INTO nebula.harvest_references
        (conversation_id, snapshot_id,
         source_block_id, target_block_id,
         edge_type, confidence, state, source,
         reason, evidence_json)
    SELECT
        p.conversation_id,
        p.snapshot_id,
        p.source_block_id,
        p.target_block_id,
        'adjacency',
        0.95,
        'CONFIRMED',
        'BACKFILL',
        format('Block %s → %s (adjacent within snapshot)', p.source_idx, p.target_idx),
        jsonb_build_object(
            'type', 'adjacency',
            'source_index', p.source_idx,
            'target_index', p.target_idx,
            'method', 'block_index_adjacency'
        )
    FROM adjacent_pairs p
    WHERE NOT EXISTS (
        SELECT 1 FROM nebula.harvest_references_history hr
        WHERE hr.source_block_id = p.source_block_id
          AND hr.target_block_id = p.target_block_id
          AND hr.edge_type = 'adjacency'
          AND hr.expiration_dt = '9999-12-31 23:59:59+00'
    )
    RETURNING id
)
SELECT count(*) AS adjacency_references_created FROM adjacency_inserts;


-- ═══════════════════════════════════════════════════════════════════════
--  2. Same-Content References
--     Connect blocks sharing the same non-empty content_hash within
--     the same conversation (including cross-snapshot).
--     Avoid self-references and only create one direction (lower id→higher id).
-- ═══════════════════════════════════════════════════════════════════════

-- Limit: only process hashes appearing <= 50 times to avoid quadratic blowup
-- from very common boilerplate content (e.g., "OK" responses, acknowledgements).
WITH frequent_hashes AS (
    SELECT content_hash
    FROM nebula.conversation_blocks
    WHERE content_hash != 'e3b0c44298fc1c14'
      AND content_hash != ''
    GROUP BY content_hash
    HAVING count(*) <= 50
),
content_matches AS (
    SELECT
        cb1.conversation_id,
        cb1.snapshot_id AS source_snapshot_id,
        cb1.id AS source_block_id,
        cb2.snapshot_id AS target_snapshot_id,
        cb2.id AS target_block_id,
        cb1.content_hash,
        CASE WHEN cb1.snapshot_id = cb2.snapshot_id THEN 'same_content' ELSE 'cross_snapshot' END AS edge_type,
        CASE WHEN cb1.snapshot_id = cb2.snapshot_id THEN 0.75 ELSE 0.50 END AS confidence
    FROM nebula.conversation_blocks cb1
    JOIN nebula.conversation_blocks cb2
        ON cb2.content_hash = cb1.content_hash
        AND cb2.conversation_id = cb1.conversation_id
        AND cb2.id > cb1.id  -- one direction only
    JOIN frequent_hashes fh ON fh.content_hash = cb1.content_hash
    WHERE cb1.content_hash != 'e3b0c44298fc1c14'
      AND cb1.content_hash != ''
),
content_inserts AS (
    INSERT INTO nebula.harvest_references
        (conversation_id, snapshot_id,
         source_block_id, target_block_id,
         edge_type, confidence, state, source,
         reason, evidence_json)
    SELECT
        m.conversation_id,
        m.source_snapshot_id,
        m.source_block_id,
        m.target_block_id,
        m.edge_type,
        m.confidence,
        'CONFIRMED',
        'BACKFILL',
        format('Content match: hash=%s (%s)', m.content_hash, m.edge_type),
        jsonb_build_object(
            'type', m.edge_type,
            'content_hash', m.content_hash,
            'method', 'content_hash_match'
        )
    FROM content_matches m
    WHERE NOT EXISTS (
        SELECT 1 FROM nebula.harvest_references_history hr
        WHERE hr.source_block_id = m.source_block_id
          AND hr.target_block_id = m.target_block_id
          AND hr.edge_type = m.edge_type
          AND hr.expiration_dt = '9999-12-31 23:59:59+00'
    )
    RETURNING id
)
SELECT count(*) AS content_match_references_created FROM content_inserts;


-- ═══════════════════════════════════════════════════════════════════════
--  3. Parent-Child References
--     Connect blocks where parent_block_id is set.
-- ═══════════════════════════════════════════════════════════════════════

WITH parent_child AS (
    INSERT INTO nebula.harvest_references
        (conversation_id, snapshot_id,
         source_block_id, target_block_id,
         edge_type, confidence, state, source,
         reason, evidence_json)
    SELECT
        cb.conversation_id,
        cb.snapshot_id,
        cb.parent_block_id AS source_block_id,
        cb.id AS target_block_id,
        'parent_child',
        0.90,
        'CONFIRMED',
        'BACKFILL',
        format('Parent-child: block %s ← block %s', cb.parent_block_id, cb.id),
        jsonb_build_object(
            'type', 'parent_child',
            'child_block_index', cb.block_index,
            'method', 'parent_block_id'
        )
    FROM nebula.conversation_blocks cb
    WHERE cb.parent_block_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM nebula.harvest_references_history hr
        WHERE hr.source_block_id = cb.parent_block_id
          AND hr.target_block_id = cb.id
          AND hr.edge_type = 'parent_child'
          AND hr.expiration_dt = '9999-12-31 23:59:59+00'
    )
    RETURNING id
)
SELECT count(*) AS parent_child_references_created FROM parent_child;


-- ═══════════════════════════════════════════════════════════════════════
--  VERIFICATION
-- ═══════════════════════════════════════════════════════════════════════

DO $$ DECLARE
    v_count INTEGER;
    v_type_counts RECORD;
BEGIN
    SELECT COUNT(*) INTO v_count FROM nebula.harvest_references;
    RAISE NOTICE 'Total active harvest_references after backfill: %', v_count;

    RAISE NOTICE '--- Breakdown by edge_type ---';
    FOR v_type_counts IN
        SELECT edge_type, count(*) AS cnt
        FROM nebula.harvest_references
        GROUP BY edge_type
        ORDER BY cnt DESC
    LOOP
        RAISE NOTICE '  %: %', v_type_counts.edge_type, v_type_counts.cnt;
    END LOOP;

    SELECT COUNT(*) INTO v_count FROM nebula.harvest_references_history;
    RAISE NOTICE 'Total harvest_references_history rows: %', v_count;

    RAISE NOTICE 'Migration 004 complete — harvest references backfilled.';
END $$;

COMMIT;
