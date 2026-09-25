-- Migration 056: Complete level + visibility_scope DDL on harvests_history and agent_records_history
-- Adds defaults, NOT NULL, CHECK constraints; backfills existing NULLs; updates INSTEAD OF trigger functions.
-- Depends on: schema-v2.sql, migrations/scd-type4-bitemporal-upgrade.sql
--
-- Renumbered from 003-level-visibility-constraints.sql (thread 6bba5dd3):
-- duplicate version prefixes make the startup runner (src/migrate.ts) skip
-- the lex-second twin on a fresh database. Content unchanged and replay-safe
-- (backfills + DO-block guards are idempotent).

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

-- ── 4. Update INSTEAD OF INSERT trigger: harvests ───────────────

CREATE OR REPLACE FUNCTION nebula.harvests_insert_trigger()
RETURNS TRIGGER AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.harvests_history
        (id, source_path, source_filename, model, total_candidates,
         candidates, source_text, tags, metadata, created_at,
         level, visibility_scope,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.source_path, NEW.source_filename, NEW.model,
         NEW.total_candidates, NEW.candidates, NEW.source_text,
         NEW.tags, NEW.metadata, COALESCE(NEW.created_at, NOW()),
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.level := COALESCE(NEW.level, 1);
    NEW.visibility_scope := COALESCE(NEW.visibility_scope, 'all');
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── 5. Update INSTEAD OF UPDATE trigger: harvests ───────────────

CREATE OR REPLACE FUNCTION nebula.harvests_update_trigger()
RETURNS TRIGGER AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.harvests_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.harvests_history
        (id, source_path, source_filename, model, total_candidates,
         candidates, source_text, tags, metadata, created_at,
         level, visibility_scope,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.source_path, NEW.source_filename, NEW.model,
         NEW.total_candidates, NEW.candidates, NEW.source_text,
         NEW.tags, NEW.metadata, OLD.created_at,
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, source_path, source_filename, model, total_candidates,
              candidates, source_text, tags, metadata, created_at,
              level, visibility_scope,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$ LANGUAGE plpgsql;

-- ── 6. Update INSTEAD OF INSERT trigger: agent_records ──────────

CREATE OR REPLACE FUNCTION nebula.agent_records_insert_trigger()
RETURNS TRIGGER AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.agent_records_history
        (id, record_type, role, title, content, source_path,
         metadata, tags, system_id, subsystem_id, feature_id,
         plan_ref, created_at,
         level, visibility_scope,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.record_type, NEW.role, NEW.title, NEW.content,
         NEW.source_path, NEW.metadata, NEW.tags, NEW.system_id,
         NEW.subsystem_id, NEW.feature_id, NEW.plan_ref,
         COALESCE(NEW.created_at, NOW()),
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.level := COALESCE(NEW.level, 1);
    NEW.visibility_scope := COALESCE(NEW.visibility_scope, 'all');
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── 7. Update INSTEAD OF UPDATE trigger: agent_records ──────────

CREATE OR REPLACE FUNCTION nebula.agent_records_update_trigger()
RETURNS TRIGGER AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.agent_records_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.agent_records_history
        (id, record_type, role, title, content, source_path,
         metadata, tags, system_id, subsystem_id, feature_id,
         plan_ref, created_at,
         level, visibility_scope,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.record_type, NEW.role, NEW.title, NEW.content,
         NEW.source_path, NEW.metadata, NEW.tags, NEW.system_id,
         NEW.subsystem_id, NEW.feature_id, NEW.plan_ref,
         OLD.created_at,
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, record_type, role, title, content, source_path,
              metadata, tags, system_id, subsystem_id, feature_id,
              plan_ref, created_at,
              level, visibility_scope,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$ LANGUAGE plpgsql;