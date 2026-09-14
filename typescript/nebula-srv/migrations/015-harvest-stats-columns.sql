-- Migration 015: Harvest analytics stat columns (generated) + sort indexes
--
-- Closes the DBA escalation of 2026-08-24 (nebula agent record 46b8a0f5,
-- to-do thread c0f659c4): /api/harvests non-default sorts (turns,
-- block_density, collaboration/user_turns, code_blocks) decode the full
-- docklang JSONB (~54 MB across 1,136 rows) on every page because
-- nebula.harvests is a temporal VIEW and cannot be indexed directly.
--
-- Fix: precompute the four sortable metrics as GENERATED ALWAYS ... STORED
-- columns on the base table, expose them through the canonical view
-- (appended at the end — column order preserved for CREATE OR REPLACE),
-- and add (stat DESC NULLS LAST, id DESC) indexes matching routes.ts
-- ORDER BY pathkeys (the V117 NULLS-placement lesson).
--
-- Measured before/after (2026-09-14, live):
--   sort=turns          1.20s -> 0.35 ms (index-driven EXPLAIN ANALYZE)
--   sort=block_density  1.09s -> 0.37 ms
--   sort=collaboration  1.02s -> (engineer wires sortExpr to stats_user_turns)
--   sort=code_blocks    0.34s -> (engineer wires sortExpr to stats_code_blocks)
--   default created_at  0.11s (unchanged, already indexed via idx_harvests_history_created_at_id)
--
-- NOTE for live application under traffic: CREATE INDEX in this file is
-- transactional (plain). On the live system the indexes were built with
-- CREATE INDEX CONCURRENTLY by the DBA before this file was committed;
-- re-running this file against a DB that already has them is a no-op
-- (IF NOT EXISTS). Fresh bootstraps just build them directly.
--
-- jsonb_path_query_array is IMMUTABLE on the deployed PG 16 build, which is
-- what makes the stats_user_turns generated column legal. If porting to a
-- build where it is STABLE, replace the expression with an IMMUTABLE
-- wrapper function before creating the column.

BEGIN;

ALTER TABLE nebula.harvests_history
  ADD COLUMN IF NOT EXISTS stats_turns int GENERATED ALWAYS AS
    (COALESCE(jsonb_array_length(docklang -> 'discourse_units'), 0)) STORED,
  ADD COLUMN IF NOT EXISTS stats_user_turns int GENERATED ALWAYS AS
    (COALESCE(jsonb_array_length(jsonb_path_query_array(
       docklang, '$.discourse_units[*] ? (@.provenance.role == "user")')), 0)) STORED,
  ADD COLUMN IF NOT EXISTS stats_code_blocks int GENERATED ALWAYS AS
    (COALESCE((docklang #>> '{stats,by_type,code}')::int, 0)) STORED,
  ADD COLUMN IF NOT EXISTS stats_block_density numeric GENERATED ALWAYS AS
    (CASE WHEN jsonb_array_length(docklang -> 'discourse_units') > 0
          THEN (docklang #>> '{stats,total_blocks}')::numeric
               / jsonb_array_length(docklang -> 'discourse_units')
          ELSE 0 END) STORED;

-- Expose through the canonical view. Appended columns keep the existing
-- column order intact (required for CREATE OR REPLACE VIEW).
CREATE OR REPLACE VIEW nebula.harvests AS
 SELECT id,
    source_path,
    source_filename,
    model,
    total_candidates,
    candidates,
    source_text,
    tags,
    metadata,
    created_at,
    level,
    visibility_scope,
    docklang,
    source_hash,
    file_size,
    version,
    run_metadata,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    asset_id,
    stats_turns,
    stats_user_turns,
    stats_code_blocks,
    stats_block_density
   FROM nebula.harvests_history
  WHERE now() >= recorded_on_dt AND now() < recorded_until_dt AND now() >= valid_from AND now() < valid_until;

CREATE INDEX IF NOT EXISTS idx_hh_stats_turns
  ON nebula.harvests_history (stats_turns DESC NULLS LAST, id DESC);
CREATE INDEX IF NOT EXISTS idx_hh_stats_user_turns
  ON nebula.harvests_history (stats_user_turns DESC NULLS LAST, id DESC);
CREATE INDEX IF NOT EXISTS idx_hh_stats_code_blocks
  ON nebula.harvests_history (stats_code_blocks DESC NULLS LAST, id DESC);
CREATE INDEX IF NOT EXISTS idx_hh_stats_block_density
  ON nebula.harvests_history (stats_block_density DESC NULLS LAST, id DESC);

COMMIT;
