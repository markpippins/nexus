-- V218 — Assessment family DDL and decision identity link (staged inert).
--
-- Phases 4 and 5 of docs/nebula-sol-absorption.md. Ratified by
--   Architect record e3f39f9a-1a5e-4168-8481-74a80235e41b (RATIFIED_WITH_AMENDMENTS)
--   DBA       record f170e343-2464-4b1a-9811-ae6bc6628def (APPROVED_WITH_AMENDMENTS)
--   Architect record f72817f5-3ff9-4634-8625-a97476678d90 (reconciliation ruling)
--
-- NOT APPLIED BY THIS CHANGE. Live application is operator-gated, exactly as
-- V216 and V217: this file is the staged artifact and bin/tests proves it on a
-- throwaway database only.
--
-- What changes:
--   1. nebula.assessments_history gains assessment_kind ('assessment' |
--      'inspection'), subject, evidence and agent_record_id.
--   2. observation_id is relaxed to nullable, because an inspection is
--      subject -> finding -> confidence -> evidence and carries no observation.
--      A per-kind CHECK keeps the shape honest: an 'assessment' row still
--      requires observation_id; an 'inspection' row requires subject+evidence.
--   3. A partial unique index enforces one assessment row per authoring agent
--      record (the "single writer" property, see D2 below).
--   4. A BEFORE INSERT OR UPDATE trigger enforces that agent_record_id names a
--      row in the CURRENT slice of nebula.agent_records_history (D1 below).
--   5. nebula.assessments is re-created so the new columns are actually
--      visible. The view has an explicit column list, so without this the
--      columns would exist on the table and be invisible through the view.
--   6. peb.decisions gains a nullable agent_record_id. No link table, no table
--      move, no backfill: all 16 rows stay NULL (ADR-015 keeps decision
--      authority in PEB).
--
-- ── Two reconciliation defects, resolved here ───────────────────────────────
--
-- D1 (adopted, authored). The reconciliation's resolution table adopted DBA's
--    BEFORE INSERT OR UPDATE trigger asserting the referenced agent_record_id
--    EXISTS in the current agent_records_history slice, but the trigger was
--    absent from its SQL block. It is authored below. An FK is impossible:
--    agent_records_history is keyed (id, recorded_on_dt) and id alone is not
--    unique (16,895 rows / 16,860 distinct ids at authoring time), and
--    nebula.agent_records is a view. The trigger is the only available
--    enforcement, so the column does not ship silently unenforced.
--
-- D2 (dropped, with reason). The reconciliation's SQL also carried
--    trg_assessments_single_writer, which raises unless current_user =
--    'nebula_writer'. No ruling creates that role, grants it, or revokes
--    anyone, and every service connects as pguser — applying it as written
--    would make nebula.assessments_history unwritable. The reconciliation's
--    own item 4 designates the partial unique index as the required
--    enforcement for single-writer, and the plan's constraints forbid writer
--    changes. So the trigger is dropped and the index stands alone. This is
--    the mechanism the rulings actually support; it is recorded in the
--    change-log.
--
-- ── One forced deviation from the reconciliation SQL ────────────────────────
--
-- The reconciliation placed assessment_kind/subject/evidence/agent_record_id
-- in the MIDDLE of the view's column list (after open_questions). PostgreSQL
-- forbids that: CREATE OR REPLACE VIEW may only ADD columns at the END of the
-- existing list, otherwise it raises "cannot change name of view column".
-- The four new columns are therefore appended after recorded_until_dt. The
-- view exposes all 19 columns either way; only the ordinal position differs.

BEGIN;

-- ── 0. Preflight ────────────────────────────────────────────────────────────
-- Fail closed rather than half-apply. Each condition is a state in which
-- applying V218 would either double-apply or act on a shape it was not
-- ratified against.

DO $$
DECLARE
    v_cols     text;
    v_rows     bigint;
    v_viewcols text;
BEGIN
    -- (1) Already applied, or partially applied.
    SELECT string_agg(t.c, ', ' ORDER BY t.c) INTO v_cols
      FROM (VALUES ('assessment_kind'), ('subject'), ('evidence'), ('agent_record_id')) AS t(c)
     WHERE EXISTS (
         SELECT 1 FROM information_schema.columns
          WHERE table_schema = 'nebula'
            AND table_name   = 'assessments_history'
            AND column_name  = t.c
     );
    IF v_cols IS NOT NULL THEN
        RAISE EXCEPTION
            'V218 refused: nebula.assessments_history already has column(s) [%]. V218 is already applied or partially applied; refusing to re-apply.',
            v_cols;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_schema = 'peb'
           AND table_name   = 'decisions'
           AND column_name  = 'agent_record_id'
    ) THEN
        RAISE EXCEPTION
            'V218 refused: peb.decisions already has agent_record_id. V218 is already applied or partially applied; refusing to re-apply.';
    END IF;

    -- (2) Populated table. Phase 4 was ratified against a 0-row table; a
    -- populated one needs a backfill decision first.
    SELECT count(*) INTO v_rows FROM nebula.assessments_history;
    IF v_rows > 0 THEN
        RAISE EXCEPTION
            'V218 refused: nebula.assessments_history holds % row(s). Phase 4 was ratified for the empty table; a populated table needs an explicit backfill decision before this DDL.',
            v_rows;
    END IF;

    -- (3) View drift. Do not CREATE OR REPLACE over a view whose shape is not
    -- the one this migration was written against.
    SELECT string_agg(column_name, ',' ORDER BY ordinal_position) INTO v_viewcols
      FROM information_schema.columns
     WHERE table_schema = 'nebula'
       AND table_name   = 'assessments';
    IF v_viewcols IS DISTINCT FROM
       'id,observation_id,outcome,confidence,impact_scope,open_questions,agenda_id,auto_resolve_plan_id,analysis_detail,created_at,forum_post_id,valid_from,valid_until,recorded_on_dt,recorded_until_dt'
    THEN
        RAISE EXCEPTION
            'V218 refused: nebula.assessments has drifted from the expected 15-column shape (found: %). Reconcile the view before applying.',
            coalesce(v_viewcols, '<view missing>');
    END IF;

    -- (4) The link target must exist, or D1's trigger would be meaningless.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
         WHERE table_schema = 'nebula' AND table_name = 'agent_records_history'
    ) THEN
        RAISE EXCEPTION
            'V218 refused: nebula.agent_records_history is missing; the agent_record_id link target must exist.';
    END IF;
END $$;

-- ── 1. Assessment family columns ────────────────────────────────────────────

ALTER TABLE nebula.assessments_history
    ADD COLUMN assessment_kind text NOT NULL DEFAULT 'assessment',
    ADD COLUMN subject         text,
    ADD COLUMN evidence        jsonb,
    ADD COLUMN agent_record_id uuid;

-- Inspections carry no observation; assessments still require one (see the
-- per-kind CHECK below, which is what actually enforces that).
ALTER TABLE nebula.assessments_history
    ALTER COLUMN observation_id DROP NOT NULL;

ALTER TABLE nebula.assessments_history
    ADD CONSTRAINT assessments_assessment_kind_check
        CHECK (assessment_kind IN ('assessment', 'inspection')),
    ADD CONSTRAINT assessments_kind_shape_check
        CHECK (
            (assessment_kind = 'assessment' AND observation_id IS NOT NULL)
            OR (assessment_kind = 'inspection' AND subject IS NOT NULL AND evidence IS NOT NULL)
        );

-- ── 2. Single writer (D2: index only, no role-gated trigger) ────────────────
-- At most one assessment row per authoring agent record. This is the
-- enforcement the reconciliation designated; the nebula_writer trigger is
-- deliberately not created (see D2 in the header).

CREATE UNIQUE INDEX assessments_single_writer_idx
    ON nebula.assessments_history (agent_record_id)
    WHERE agent_record_id IS NOT NULL;

-- ── 3. agent_record_id integrity (D1) ───────────────────────────────────────
-- No FK is possible (composite bitemporal PK; agent_records is a view), so the
-- link is checked against the current slice on write.

CREATE OR REPLACE FUNCTION nebula.trg_assessments_agent_record_link()
RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.agent_record_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- Unchanged on UPDATE: nothing new to verify.
    IF TG_OP = 'UPDATE' AND NEW.agent_record_id IS NOT DISTINCT FROM OLD.agent_record_id THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM nebula.agent_records_history a
         WHERE a.id = NEW.agent_record_id
           AND now() >= a.recorded_on_dt
           AND now() <  a.recorded_until_dt
    ) THEN
        RAISE EXCEPTION
            'agent_record_id % does not exist in the current nebula.agent_records_history slice',
            NEW.agent_record_id;
    END IF;

    RETURN NEW;
END $$;

CREATE TRIGGER trg_assessments_agent_record_link
    BEFORE INSERT OR UPDATE ON nebula.assessments_history
    FOR EACH ROW EXECUTE FUNCTION nebula.trg_assessments_agent_record_link();

-- ── 4. View: expose the new columns ─────────────────────────────────────────
-- Explicit column list, existing 15 in their original order, new columns
-- appended (PostgreSQL only permits additions at the end of a replaced view).

CREATE OR REPLACE VIEW nebula.assessments AS
SELECT id,
       observation_id,
       outcome,
       confidence,
       impact_scope,
       open_questions,
       agenda_id,
       auto_resolve_plan_id,
       analysis_detail,
       created_at,
       forum_post_id,
       valid_from,
       valid_until,
       recorded_on_dt,
       recorded_until_dt,
       assessment_kind,
       subject,
       evidence,
       agent_record_id
  FROM nebula.assessments_history ah
 WHERE now() >= recorded_on_dt
   AND now() <  recorded_until_dt
   AND now() >= valid_from
   AND now() <  valid_until;

-- ── 5. Phase 5: decision identity link ──────────────────────────────────────
-- Nullable, ships empty. The 14 title-matched ADR pairs are NOT written: title
-- equality is not identity proof (design note §8.1), and no backfill is
-- authorised. ADR-015 keeps decision authority in PEB, so the link lives here
-- rather than in a nebula link table.

ALTER TABLE peb.decisions
    ADD COLUMN agent_record_id uuid;

COMMIT;
