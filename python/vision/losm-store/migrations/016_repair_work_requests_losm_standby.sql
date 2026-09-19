-- =====================================================================
-- 016 (DRAFT — Option-A STANDBY, ruling-gated): repair the LOSM WR
-- write path (incident e772b969).
--
-- ⚠️  STANDBY — APPLY ONLY IF THE COMBINED RULING LANDS OPTION A. ⚠️
-- Ruling thread 402d8a0d decides: Option A (repair vision's store in
-- place → THIS migration applies) vs Option B (absorb into
-- resolution.work_request → DISCARD this file; implementation plan
-- 8261650 supersedes it). Do not apply before the ruling record cites
-- Option A.
--
-- TWO DEFECTS REPAIRED
--   D1. sql/ci-bootstrap/nexus-ci-bootstrap.sql (~L15354) re-created
--       vision.work_requests_losm as a NON-updatable CASE view over
--       work_requests_history: the CASE on 'status' is not a
--       base-relation column, so ORM INSERT/UPDATE through the view
--       fails with FeatureNotSupported ("cannot insert into column
--       'status' of view").
--   D2. vision.work_requests_history.id has NO generation default —
--       a defect in losm-store migrations/001 itself (bare
--       `id INTEGER NOT NULL`, composite PK (id, recorded_on_dt)),
--       present in live after the bootstrap restore. Even a
--       direct-base insert fails with NULL id.
--
-- REPAIR
--   R1. Gated sequence + id default on the base (idempotent; D2).
--   R2. Replace the CASE view with a plain pass-through of
--       current-tense rows — restores PostgreSQL auto-updatability
--       (D1). NOTE: DROP + CREATE, not CREATE OR REPLACE — OR REPLACE
--       cannot change a view column's type, and the CASE projects
--       status as varchar while the base column is varchar(32)
--       (caught live by the standby test suite). The view is a leaf
--       (no dependent views/tables — verified via pg_rewrite), and
--       DDL here is transactional, so the drop/recreate is atomic.
--       The bootstrap view's legacy status normalization
--       (pending→NEW, completed→COMPLETION, cancelled→FAILED) is
--       dropped ON PURPOSE: live work_requests_history is EMPTY
--       (verified 2026-09-19), so it currently normalizes nothing,
--       and the ORM writes the canonical WorkStatus vocabulary
--       directly. GATE-001 below refuses to apply if history rows
--       have appeared, so the normalization loss can never be silent.
--
-- Idempotent (IF NOT EXISTS / OR REPLACE / guarded setval). No data
-- is written by this migration.
-- =====================================================================
BEGIN;

-- ── Gate: refuse if history rows exist (normalization-loss guard) ──
DO $$
DECLARE n INTEGER;
BEGIN
    SELECT count(*) INTO n FROM vision.work_requests_history;
    IF n > 0 THEN
        RAISE EXCEPTION '016-GATE-001: % row(s) in vision.work_requests_history — the pre-bootstrap status normalization could lose data; reconcile first', n
            USING ERRCODE = 'P0001';
    END IF;
END $$;

-- ── R1. id generation on the base (D2) ──────────────────────────────
CREATE SEQUENCE IF NOT EXISTS vision.work_requests_history_id_seq AS INTEGER;

SELECT setval('vision.work_requests_history_id_seq',
              COALESCE((SELECT max(id) FROM vision.work_requests_history), 0) + 1,
              false);

ALTER TABLE vision.work_requests_history
    ALTER COLUMN id SET DEFAULT nextval('vision.work_requests_history_id_seq');

-- ── R2. pass-through view (D1) — current-tense rows, base columns ──
-- DROP + CREATE (see header note): OR REPLACE would fail with
-- InvalidTableDefinition (varchar → varchar(32) column type change).
DROP VIEW IF EXISTS vision.work_requests_losm;
CREATE VIEW vision.work_requests_losm AS
SELECT id,
       wr_id,
       parent_request_id,
       intent,
       constraints,
       priority,
       context,
       status,
       created_at,
       recorded_on_dt,
       recorded_until_dt
  FROM vision.work_requests_history
 WHERE recorded_until_dt = '9999-12-31 23:59:59+00'::timestamptz;

-- ── Postconditions ──────────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'vision'
          AND table_name  = 'work_requests_history'
          AND column_name = 'id'
          AND column_default IS NOT NULL
    ) THEN
        RAISE EXCEPTION '016-POSTCONDITION-001: id generation default missing after repair';
    END IF;

    IF (
        SELECT is_insertable_into
          FROM information_schema.tables
         WHERE table_schema = 'vision'
           AND table_name  = 'work_requests_losm'
    ) <> 'YES' THEN
        RAISE EXCEPTION '016-POSTCONDITION-002: work_requests_losm is not insertable — auto-updatability not restored';
    END IF;

    IF position('CASE' in upper(pg_get_viewdef('vision.work_requests_losm'::regclass, true))) > 0 THEN
        RAISE EXCEPTION '016-POSTCONDITION-003: view still contains a CASE projection';
    END IF;
END $$;

COMMIT;
