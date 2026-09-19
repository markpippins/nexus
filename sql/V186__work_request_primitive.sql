-- =============================================================================
-- V186: WorkRequest primitive landing — absorb legacy WR stores into
-- resolution.work_request (DBA; ruling thread 402d8a0d, records e772b969 /
-- ffe14f38, DBA confirmation 6be5faaa).
--
-- ⚠️  STAGED INERT — NOT APPLIED TO LIVE. ⚠️
-- Posture mirrors V184 (calendar primitive): staged, gated, idempotent.
-- Apply happens ONLY on explicit operator go, after the architect rules the
-- Option A/B fork (engineer-ii recommends B; DBA concurs with evidence).
-- R9 note: schema change — vanadium replication question attaches at apply
-- time (vanadium unreachable from titanium while traveling).
--
-- THE FORK THIS FILE ANSWERS (Option B):
--   A) repair vision's broken store in place (throwaway if canonicalization
--      follows), or
--   B) absorb: backfill via legacy_id, repoint writers, demote legacy
--      surfaces to views (Phase 5, separate migration).  ← this file
--
-- WHY B (evidence, probed live 2026-09-19/20, titanium):
--   * vision.work_requests_losm is doubly broken: non-updatable CASE-view
--     (writes 500, e772b969) AND its normalization doesn't know the real
--     live vocabulary ('settled'/'draft' fall to ELSE → 'NEW' mislabels on
--     read). Repair-in-place fixes two defects in a store slated for
--     demolition, contrary to doctrine a4232e3d (vision is cache, not
--     authority).
--   * 16 WR objects across 6 schemas, 13 empty. Real data: vision 6 rows,
--     nebula 1 row, conduit work_request_state 26 rows (conduit's own
--     execution state — out of this tranche).
--   * resolution.work_request exists EMPTY with the canonical shape:
--     asset_id FK, specification lineage, dco_json, legacy_id (anticipates
--     absorption), inline bitemporality (no separate _history needed).
--
-- DBA AMENDMENT vs engineer-ii's DRAFT SKETCH (sql/sketches/
-- V186__work_request_primitive_DRAFT.sql): the backfill vocabulary CASE has
-- NO silent ELSE. An unmapped status REFUSES (V186-VOCAB-001 pre-insert
-- gate + explicit CASE without default). Silent-ELSE attests unknown as a
-- default — the exact epistemic failure the auditor grant (79feb142) and
-- V174 satisfaction-states codify against. If a future status appears, the
-- migration refuses; it does not guess.
--
-- DBA-CONFIRMED VOCABULARY (record 6be5faaa): canonical CHECK is
-- (DRAFT, APPROVED, DISPATCHED, COMPLETED, CANCELLED). Map:
--   draft→DRAFT, pending→DRAFT, settled→COMPLETED (the one settled row is a
--   terminal-successful test execution; 'settled' appears nowhere else in
--   the system), completed→COMPLETED, cancelled→CANCELLED.
--
-- VERIFIED LIVE FACTS ENCODED (re-verified 2026-09-20):
--   * vision.work_requests: 6 rows (5×'draft', 1×'settled'), all legacy
--     shape (seven canonical envelope columns NULL → guard triggers pass).
--     asset_id set on all 6, FK'd to semantics.canonical_asset; 0 id
--     collisions with resolution.canonical_asset; registries shape-identical
--     (10/10 columns, 0 type/nullability diffs). Tombstones: 0.
--   * vision.work_requests has NO parent_request_id column — no parent data
--     exists to migrate; resolution.work_request_edge stays empty (edge
--     rows arrive with future WRs, not backfill).
--   * nebula.work_requests is a VIEW; base nebula.work_requests_history has
--     1 live row (DRAFT, legacy_id 'sysadmin-maintenance-002', no asset).
--   * plan_id: vision rows carry plan_ids in context JSONB; ZERO exist in
--     resolution.implementation_plan → plan_id backfills NULL, values
--     preserved in context.
-- =============================================================================

BEGIN;

-- ── Gate: refuse double-apply (mirrors V184-GATE-001) ───────────────────────
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM resolution.work_request
               WHERE legacy_id LIKE 'vision.work_requests:%')
       OR EXISTS (SELECT 1 FROM resolution.work_request
                  WHERE legacy_id LIKE 'nebula.work_requests_history:%') THEN
        RAISE EXCEPTION 'V186-GATE-001: backfill already applied — refusing to double-apply'
            USING ERRCODE = 'P0001';
    END IF;
END $$;

-- ── Gate: vocabulary pre-insert check — loud refusal on unmapped status ─────
-- Every DISTINCT source status must be in the confirmed map BEFORE any row
-- is written. An unknown status aborts the transaction; it is never silently
-- normalized to a default. (DBA amendment — record 6be5faaa.)
DO $$
DECLARE
    v_unknown TEXT;
BEGIN
    SELECT string_agg(DISTINCT v.status, ', ')
      INTO v_unknown
      FROM vision.work_requests v
     WHERE lower(v.status) NOT IN
           ('draft', 'pending', 'settled', 'completed', 'cancelled');
    IF v_unknown IS NOT NULL THEN
        RAISE EXCEPTION 'V186-VOCAB-001: unmapped vision.work_requests status value(s): % — refusing to guess; extend the confirmed map and re-run',
            v_unknown
            USING ERRCODE = 'P0001';
    END IF;
END $$;

-- =============================================================================
-- ── PHASE 1 — asset registry unification (semantics → resolution) ──────────
-- The 6 referenced assets mirror by identity (same id, same
-- canonical_asset_id) so downstream uuid references stay identity-stable.
-- semantics rows untouched (their demotion is a later, separately-ruled
-- tranche). NOT EXISTS makes this a no-op on re-run.
-- =============================================================================
INSERT INTO resolution.canonical_asset
       (id, canonical_asset_id, asset_kind, canonical_key, source_hash, content_hash,
        validity_start, validity_end, created_at, expired_at)
SELECT s.id, s.canonical_asset_id, s.asset_kind, s.canonical_key, s.source_hash,
       s.content_hash, s.validity_start, s.validity_end, s.created_at, s.expired_at
FROM semantics.canonical_asset s
WHERE s.id IN (SELECT DISTINCT asset_id FROM vision.work_requests WHERE asset_id IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM resolution.canonical_asset r WHERE r.id = s.id);

-- =============================================================================
-- ── PHASE 2a — vision backfill via legacy_id ────────────────────────────────
--    Mapping (vision.work_requests → resolution.work_request):
--      wr_id             → legacy_id = 'vision.work_requests:' || wr_id
--      work_request_uuid → id (uuid PK; PK-carried identity)
--      title             → title
--      status            → business_status (confirmed map; loud refusal
--                          upstream on anything unmapped — V186-VOCAB-001)
--      context (jsonb)   → context (as-is; preserves plan_id /
--                          work_request_uuid / intent payloads)
--      dco_json, step_outputs → same-name columns (text)
--      recorded_on_dt    → created_at / updated_at / valid_from /
--                          recorded_on_dt; recorded_until_dt carries over;
--                          open pairs get 'infinity' sentinels
--      asset_id          → asset_id (FK-valid after Phase 1)
--      (plan_id, description, source_specification_id, source_requirement_id,
--       intent, constraints, consumed_at, created_by: no vision equivalent
--       → NULL / defaults)
-- =============================================================================
INSERT INTO resolution.work_request
       (id, asset_id, title, business_status, intent, context, constraints,
        dco_json, legacy_id, step_outputs, created_at, updated_at,
        valid_from, valid_until, recorded_on_dt, recorded_until_dt)
SELECT
    v.work_request_uuid::uuid,
    v.asset_id,
    COALESCE(NULLIF(v.title, ''), '(untitled legacy WR)'),
    (CASE lower(v.status)
        WHEN 'draft'     THEN 'DRAFT'
        WHEN 'pending'   THEN 'DRAFT'
        WHEN 'settled'   THEN 'COMPLETED'
        WHEN 'completed' THEN 'COMPLETED'
        WHEN 'cancelled' THEN 'CANCELLED'
        -- No ELSE: the V186-VOCAB-001 gate already refused unmapped values,
        -- and NULL here would violate the NOT NULL CHECK-bearing column.
        -- A status added later must extend the map, not fall through.
     END),
    v.context->'intent'->>'type',
    v.context,
    '{}'::jsonb,
    v.dco_json,
    'vision.work_requests:' || v.wr_id,
    v.step_outputs,
    v.recorded_on_dt,
    v.recorded_on_dt,
    v.recorded_on_dt,
    'infinity'::timestamptz,
    v.recorded_on_dt,
    'infinity'::timestamptz
FROM vision.work_requests v;

-- =============================================================================
-- ── PHASE 2b — nebula backfill (near-ancestor shape; direct copy) ───────────
--    One live row: DRAFT, legacy_id 'sysadmin-maintenance-002', no asset.
--    Crosswalk legacy_id = 'nebula.work_requests_history:<uuid>'.
--    WHERE NOT EXISTS makes re-run a no-op (belt to GATE-001's braces).
-- =============================================================================
INSERT INTO resolution.work_request
       (id, asset_id, title, business_status, intent, context, constraints,
        dco_json, legacy_id, step_outputs, created_at, updated_at,
        valid_from, valid_until, recorded_on_dt, recorded_until_dt)
SELECT
    n.id,
    n.asset_id,                       -- NULL on the live row — FK-safe
    COALESCE(NULLIF(n.title, ''), '(untitled legacy WR)'),
    COALESCE(n.business_status, 'DRAFT'),
    n.intent,
    COALESCE(n.context, '{}'::jsonb),
    COALESCE(n.constraints, '{}'::jsonb),
    n.dco_json,
    'nebula.work_requests_history:' || n.id::text,
    COALESCE(n.step_outputs, '{}'::text),
    n.created_at,
    n.updated_at,
    n.valid_from,
    n.valid_until,
    n.recorded_on_dt,
    n.recorded_until_dt
FROM nebula.work_requests_history n
WHERE NOT EXISTS (
    SELECT 1 FROM resolution.work_request c
    WHERE c.legacy_id = 'nebula.work_requests_history:' || n.id::text
);

-- =============================================================================
-- ── PHASE 3 — validation gates (abort the transaction on any miss) ──────────
-- =============================================================================
DO $$
DECLARE
    v_missing INTEGER;
    v_dupes   INTEGER;
    v_bitemp  INTEGER;
BEGIN
    -- 3a. every vision row is represented exactly once
    SELECT count(*) INTO v_missing
    FROM vision.work_requests v
    WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request c
                      WHERE c.legacy_id = 'vision.work_requests:' || v.wr_id);
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V186-VALIDATE-001: % vision rows missing after backfill', v_missing;
    END IF;

    -- 3b. every nebula row represented
    SELECT count(*) INTO v_missing
    FROM nebula.work_requests_history n
    WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request c
                      WHERE c.legacy_id = 'nebula.work_requests_history:' || n.id::text);
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V186-VALIDATE-002: % nebula rows missing after backfill', v_missing;
    END IF;

    -- 3c. no legacy_id duplicates
    SELECT count(*) INTO v_dupes FROM (
        SELECT 1 FROM resolution.work_request
        WHERE legacy_id IS NOT NULL
        GROUP BY legacy_id HAVING count(*) > 1) d;
    IF v_dupes > 0 THEN
        RAISE EXCEPTION 'V186-VALIDATE-003: duplicate legacy_id detected';
    END IF;

    -- 3d. bitemporal integrity on the backfilled rows
    SELECT count(*) INTO v_bitemp
    FROM resolution.work_request
    WHERE (legacy_id LIKE 'vision.work_requests:%'
           OR legacy_id LIKE 'nebula.work_requests_history:%')
      AND (valid_until <= valid_from OR recorded_until_dt <= recorded_on_dt);
    IF v_bitemp > 0 THEN
        RAISE EXCEPTION 'V186-VALIDATE-004: bitemporal integrity violation in backfilled rows';
    END IF;

    -- 3e. edge table stayed empty (no parent data exists to migrate —
    --     vision base has no parent_request_id column; verified 2026-09-20)
    IF EXISTS (SELECT 1 FROM resolution.work_request_edge) THEN
        RAISE EXCEPTION 'V186-VALIDATE-005: work_request_edge non-empty after backfill — investigate before committing';
    END IF;
END $$;

COMMIT;

-- =============================================================================
-- ── PHASE 4 — writer repoint (application-side; NOT DDL, NOT this file) ─────
--    Enumerated because the migration is not "done" without it. Verified
--    writer/reader map (engineer-ii blast-radius + consumer map bd78662a):
--      W1 vision-srv  WR write path  → RETIRED on cutover (service retires)
--      W2 losm-host   losm_store create/update WR → REPOINT at
--         resolution.work_request (string wr_id stays the API address —
--         now the legacy_id suffix)
--      W3 nebula-srv  add_work_request / update_work_request_status
--         (INSERT/UPDATE via nebula.work_requests view) → REPOINT at
--         resolution.work_request (nebula-srv is a READER of vision; the
--         writer correction is per consumer map bd78662a)
--      W4 cascade admission_subscriber (INSERT INTO
--         nebula.work_requests_history) → REPOINT at resolution.work_request
--      R1 vision read views (work_requests/_losm/_edges/_dag) → pass-through
--         views over resolution in Phase 5 (names stay alive)
--      R2 conduit db_adapter → reads base vision.work_requests; UNAFFECTED
--         while Phase 5 keeps column names (it does). Repoint explicitly
--         OUT OF THIS TRANCHE.
--      R3 nebula-srv reads (~10 routes via the view + routes.ts:4041 join)
--         → keep working through Phase-5 views; optional repoint later.
--      R4 address-tts → conduit-local event sourcing; out of this tranche.
--      R5 tackle planner_mcp/agent_scheduler_runner → execution.requests
--         READY counts; source_wr_id FK lineage must stay resolvable through
--         the legacy_id crosswalk.
--      scratch.work_requests reads NEBULA history (its own _history base is
--         orphaned, 1 row, zero code consumers — archive candidate).
-- =============================================================================

-- =============================================================================
-- ── PHASE 5 — demote legacy surfaces to views (SEPARATE migration, V187) ────
--    Never in this transaction: views flip only after no writer targets the
--    base tables. Full sketch: sql/sketches/V186__work_request_primitive_DRAFT.sql
--    Phase-5 section (DROP TABLE ... CASCADE audit required at apply time;
--    pre-apply snapshot gate; postcondition counts 6+1; conduit smoke 200).
-- =============================================================================
