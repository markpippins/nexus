-- Migration 056: Complete level + visibility_scope DDL on harvests_history and agent_records_history
-- Adds defaults, NOT NULL, CHECK constraints; backfills existing NULLs; updates INSTEAD OF trigger functions.
-- Depends on: schema-v2.sql, migrations/scd-type4-bitemporal-upgrade.sql
--
-- Renumbered from 003-level-visibility-constraints.sql (thread 6bba5dd3):
-- duplicate version prefixes make the startup runner (src/migrate.ts) skip
-- the lex-second twin on a fresh database.
--
-- NOT a verbatim copy, per DBA ruling 22c217bb-9b4f-42e9-9c91-3e752bd6141d:
-- sections 1-3 (the level/visibility contract) are unchanged and replay-safe
-- (backfills + DO-block guards are idempotent); the four INSTEAD OF trigger
-- function bodies the original carried have been REMOVED, because replaying
-- stale copies at v56 downgraded live trigger bodies. See section 4.

SET search_path TO nebula;

-- ── 1. Backfill existing NULLs ──────────────────────────────────

UPDATE nebula.harvests_history
   SET level = 1
 WHERE level IS NULL;

UPDATE nebula.harvests_history
   SET visibility_scope = 'all'
 WHERE visibility_scope IS NULL;

UPDATE nebula.agent_records_history
   SET level = 1
 WHERE level IS NULL;

UPDATE nebula.agent_records_history
   SET visibility_scope = 'all'
 WHERE visibility_scope IS NULL;

-- ── 2. Set defaults + NOT NULL ──────────────────────────────────

ALTER TABLE nebula.harvests_history
    ALTER COLUMN level SET DEFAULT 1,
    ALTER COLUMN level SET NOT NULL,
    ALTER COLUMN visibility_scope SET DEFAULT 'all',
    ALTER COLUMN visibility_scope SET NOT NULL;

ALTER TABLE nebula.agent_records_history
    ALTER COLUMN level SET DEFAULT 1,
    ALTER COLUMN level SET NOT NULL,
    ALTER COLUMN visibility_scope SET DEFAULT 'all',
    ALTER COLUMN visibility_scope SET NOT NULL;

-- ── 3. Add CHECK constraints (idempotent via DO block) ───────────

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_harvests_level'
          AND conrelid = 'nebula.harvests_history'::regclass
    ) THEN
        ALTER TABLE nebula.harvests_history
            ADD CONSTRAINT chk_harvests_level CHECK (level BETWEEN 1 AND 4);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'chk_agent_records_level'
          AND conrelid = 'nebula.agent_records_history'::regclass
    ) THEN
        ALTER TABLE nebula.agent_records_history
            ADD CONSTRAINT chk_agent_records_level CHECK (level BETWEEN 1 AND 4);
    END IF;
END $$;

-- ── 4. INSTEAD OF trigger functions: DELIBERATELY NOT REDEFINED HERE ──
--
-- DBA ruling, thread 6bba5dd3 (record 22c217bb-9b4f-42e9-9c91-3e752bd6141d),
-- verified defect 3: this file previously carried verbatim copies of four
-- trigger-function bodies:
--
--     nebula.harvests_insert_trigger()
--     nebula.harvests_update_trigger()
--     nebula.agent_records_insert_trigger()
--     nebula.agent_records_update_trigger()
--
-- Replaying those copies at v56 DOWNGRADED live bodies. 023-add-harvest-file-size.sql
-- is the current owner of the harvests pair and adds the file_size column that the
-- copies here predate; because this file sorts after 023, replaying it stripped
-- file_size from two production triggers (verified: pg_get_functiondef contained
-- file_size before the replay and did not after). 013/022/023 progressively fixed
-- these bodies over time; a renumbered file must not carry stale copies of them.
--
-- Ownership:
--   * harvests pair      -> 023-add-harvest-file-size.sql (defines the bodies AND
--                           creates trg_harvests_insert / trg_harvests_update).
--   * agent_records pair -> scd-type4-bitemporal-upgrade.sql / -temporal.sql /
--                           -fix-insert-returning.sql, which the D2 bootstrap
--                           applies. The NNN-*.sql startup runner (src/migrate.ts)
--                           creates no INSTEAD OF trigger on the agent_records
--                           view, so under that runner alone these functions are
--                           never called and are not needed here.
--
-- Consequence of removal, stated so the next reader does not have to re-derive it:
-- on a fresh database the functions are created earlier in the sequence (023 for
-- harvests, the D2 bootstrap for agent_records) or are never invoked; on an
-- existing database the live bodies are left untouched, which is the point.
-- Sections 1-3 above (backfill, defaults/NOT NULL, CHECK constraints) are the
-- level/visibility contract this file exists to deliver and are unchanged.
