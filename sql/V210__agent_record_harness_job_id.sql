-- =============================================================================
-- V210: let an agent record say which harness run wrote it.
--
-- bin/check_harness_silent_success.py answers one question per harness run:
-- did the role post a record anywhere inside the run's own window? It matched
-- records to runs by role and timestamp only, because nothing on the record
-- identified its run. That is a heuristic, and it fails in the direction that
-- hides defects: a SECOND record from the same role inside the window (an
-- unrelated record, a sibling job, a scheduled sweep) cleared a silent run.
-- The check documented that false negative as deliberate; it was. The fix is
-- cheap, so the heuristic now only needs to cover rows that predate this
-- column.
--
-- The identifier already exists at write time. harness-srv spawns each job as
-- a child process with HARNESS_JOB_ID set to the job uuid
-- (typescript/harness-srv/src/app.ts, executeOpencode), which is the same uuid
-- the run's cascade.events rows use as aggregate_id. Children inherit the
-- environment, so a record posted from a shell inside the run
-- (bin/post-agent-record.py) stamps itself with no cooperation from the model.
-- MCP callers can pass harnessJobId explicitly.
--
-- nebula.agent_records is a bitemporal VIEW over agent_records_history
-- (V121 precedent): the column goes on the history table, the view is
-- re-created. CREATE OR REPLACE is used here rather than V121's DROP+CREATE
-- because the column is APPENDED at the end, which CREATE OR REPLACE allows —
-- so the view's grants and any dependents survive, and the swap cannot fail
-- on a dependent object.
--
-- Nullable on purpose: 20k+ existing rows have no run to point at, and a
-- record written outside a harness (interactive session, cron, human) never
-- will. NULL means "unattributed", which the checker treats differently from
-- "attributed to a different run".
--
-- Idempotent: IF NOT EXISTS guards on column and index; the view re-creation
-- is a pure projection of the history table.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Preflight gates — loud refusals, per house pattern.
-- -----------------------------------------------------------------------------
DO $preflight$
DECLARE
    v_view_has_model boolean;
BEGIN
    -- P1: out-of-order guard against the house migration ledger.
    IF to_regclass('resolution.migration_ledger') IS NOT NULL THEN
        BEGIN
            IF EXISTS (
                SELECT 1 FROM resolution.migration_ledger
                 WHERE migration_label ~ '^V21[1-9]'
                    OR migration_label ~ '^V[3-9][0-9][0-9]'
            ) THEN
                RAISE EXCEPTION 'PREFLIGHT P1 FAIL: migrations newer than V210 present in the ledger'
                    USING HINT = 'Check sql/ head and resolution.migration_ledger; do not apply out-of-order migrations.';
            END IF;
        EXCEPTION WHEN undefined_column THEN
            RAISE NOTICE 'PREFLIGHT P1 skipped: resolution.migration_ledger has no migration_label column';
        END;
    END IF;

    -- P2: the canonical surface must exist (this migration projects it).
    IF to_regclass('nebula.agent_records_history') IS NULL THEN
        RAISE EXCEPTION 'PREFLIGHT P2 FAIL: nebula.agent_records_history is absent'
            USING HINT = 'Agent records live there; apply the base schema first.';
    END IF;
    IF to_regclass('nebula.agent_records') IS NULL THEN
        RAISE EXCEPTION 'PREFLIGHT P3 FAIL: nebula.agent_records view is absent'
            USING HINT = 'The view must exist to be re-created; apply V121-level schema first.';
    END IF;

    -- P4: the view must currently end with `model`, so appending one column
    --     keeps CREATE OR REPLACE legal. If the head has already moved, this
    --     migration is out of order and must be rebased rather than guessed.
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'nebula'
           AND table_name = 'agent_records'
           AND column_name = 'model'
           AND ordinal_position = (
                SELECT max(ordinal_position) FROM information_schema.columns
                 WHERE table_schema = 'nebula' AND table_name = 'agent_records'
           )
    ) INTO v_view_has_model;
    IF NOT v_view_has_model THEN
        RAISE EXCEPTION 'PREFLIGHT P4 FAIL: nebula.agent_records no longer ends with model — column order changed upstream'
            USING HINT = 'This migration appends harness_job_id via CREATE OR REPLACE; rebase it onto the current view definition.';
    END IF;
END
$preflight$;

-- -----------------------------------------------------------------------------
-- 1. Column + partial index on the history table (where the data lives).
-- -----------------------------------------------------------------------------
ALTER TABLE nebula.agent_records_history
  ADD COLUMN IF NOT EXISTS harness_job_id text;

-- Partial: the checker looks up one job at a time, and the overwhelming
-- majority of rows are NULL forever.
CREATE INDEX IF NOT EXISTS idx_agent_records_history_harness_job_id
  ON nebula.agent_records_history (harness_job_id)
  WHERE harness_job_id IS NOT NULL;

COMMENT ON COLUMN nebula.agent_records_history.harness_job_id IS
  'Harness job uuid that wrote this record (HARNESS_JOB_ID at write time). NULL = written outside a harness run. Consumer: bin/check_harness_silent_success.py exact run-to-record matching.';

-- -----------------------------------------------------------------------------
-- 2. Re-project the view. `model` stays last-but-one; harness_job_id is
--    appended so CREATE OR REPLACE is accepted and grants survive.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW nebula.agent_records AS
  SELECT id,
    record_type,
    role,
    title,
    content,
    source_path,
    metadata,
    tags,
    system_id,
    subsystem_id,
    feature_id,
    plan_ref,
    candidate_id,
    requirement_id,
    created_at,
    level,
    visibility_scope,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    model,
    harness_job_id
  FROM nebula.agent_records_history
  WHERE now() >= recorded_on_dt
    AND now() < recorded_until_dt
    AND now() >= valid_from
    AND now() < valid_until;

COMMIT;
