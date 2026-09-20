-- =============================================================================
-- V186 (DRAFT SKETCH — engineer-ii for DBA/architect review): WorkRequest
-- primitive landing — absorb legacy WR stores into resolution.work_request,
-- per ruling request thread 402d8a0d + evidence ffe14f38.
--
-- ⚠️  SKETCH — NOT FOR UNREVIEWED APPLICATION. ⚠️
-- Posture mirrors V184 (calendar primitive): staged, gated, idempotent, and
-- inert until the combined e772b969 + canonicalization ruling (thread
-- 402d8a0d) lands Option B and the DBA adopts/renumbers the file.
--
-- THE FORK THIS FILE ANSWERS (Option B):
--   A) repair vision's broken store in place (throwaway if canonicalization
--      follows), or
--   B) absorb: backfill via legacy_id, repoint writers, demote legacy
--      surfaces to views.  ← this file
--
-- VERIFIED LIVE FACTS THIS FILE ENCODES (probed 2026-09-19, titanium):
--   * resolution.work_request exists, EMPTY (0 rows). PK id uuid, FK
--     asset_id → resolution.canonical_asset(id), FK plan_id →
--     resolution.implementation_plan(plan_number), FK source_specification_id
--     → resolution.specification, source_requirement_id → resolution.requirement,
--     CHECK business_status IN (DRAFT,APPROVED,DISPATCHED,COMPLETED,CANCELLED),
--     partial index on legacy_id, inline bitemporal pair (valid_*/recorded_*,
--     infinity sentinels). No _history table needed in the end state.
--   * vision.work_requests: 6 rows, ALL legacy shape (all seven canonical
--     envelope columns NULL → guard trigger currently passes them). statuses:
--     5×'draft', 1×'settled'. Tombstones: 0. asset_id set on all 6, FK'd to
--     semantics.canonical_asset — NONE of the 6 exist yet in resolution.
--     canonical_asset (by canonical_asset_id); no id collisions with
--     resolution.canonical_asset.id.
--   * The two asset registries are SHAPE-IDENTICAL (semantics.canonical_asset
--     and resolution.canonical_asset have identical columns); the bridge is
--     canonical_asset_id (text), not entity_key.
--   * nebula "work_requests" is a VIEW; the base is nebula.work_requests_history
--     (bitemporal, legacy_id column already present). 1 live row:
--     business_status DRAFT, legacy_id 'sysadmin-maintenance-002', NO asset.
--   * conduit.work_request_state (26 rows) is CONDUIT'S OWN execution state —
--     out of scope here (optionally view-demoted in a later tranche).
--   * plan_id caution: vision rows carry plan_ids in context JSONB; ZERO of
--     them exist in resolution.implementation_plan — so plan_id must stay
--     NULL in the backfill (FK would reject), preserved in context instead.
--
-- ── PHASE 1 — asset registry unification (semantics → resolution) ──────────
-- The 6 vision assets are mirrored by identity (same id + same
-- canonical_asset_id). Id-collision precheck verified: 0 conflicts.
-- =============================================================================
BEGIN;

-- Gate: refuse double-apply (mirrors V184-GATE-001)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM resolution.work_request WHERE legacy_id LIKE 'vision.work_requests:%')
       OR EXISTS (SELECT 1 FROM resolution.work_request WHERE legacy_id = 'nebula.work_requests_history:468aa85c-8b75-44ea-885f-2d5ca9dfdcf2') THEN
        RAISE EXCEPTION 'V186-GATE-001: backfill already applied — refusing to double-apply'
            USING ERRCODE = 'P0001';
    END IF;
END $$;

-- 1a. Mirror the referenced assets into the resolution registry.
--     Same id, same canonical_asset_id → downstream uuid references stay
--     identity-stable; the semantics rows are untouched (owner decides later
--     whether semantics.canonical_asset demotes to a view of resolution's).
INSERT INTO resolution.canonical_asset
       (id, canonical_asset_id, asset_kind, canonical_key, source_hash, content_hash,
        validity_start, validity_end, created_at, expired_at)
SELECT s.id, s.canonical_asset_id, s.asset_kind, s.canonical_key, s.source_hash,
       s.content_hash, s.validity_start, s.validity_end, s.created_at, s.expired_at
FROM semantics.canonical_asset s
WHERE s.id IN (SELECT DISTINCT asset_id FROM vision.work_requests WHERE asset_id IS NOT NULL)
  AND NOT EXISTS (SELECT 1 FROM resolution.canonical_asset r WHERE r.id = s.id);

-- =============================================================================
-- ── PHASE 2 — backfill into resolution.work_request via legacy_id ───────────
--    Mapping (vision.work_requests → resolution.work_request):
--      wr_id            → legacy_id = 'vision.work_requests:' || wr_id   (stable crosswalk)
--      work_request_uuid→ id (uuid PK; PK-carried identity, mirrors V184 Q2 posture)
--      title            → title
--      status           → business_status (UPPERCASED + vocab map:
--                          draft→DRAFT, settled→COMPLETED, pending→DRAFT,
--                          completed→COMPLETED, cancelled→CANCELLED;
--                          TODO(dba): confirm 'settled'→COMPLETED is the
--                          intended normalization or add a vocab value)
--      context (jsonb)  → context (as-is; also preserves the original
--                          plan_id / work_request_uuid / intent payloads)
--      dco_json, step_outputs → same-name columns (text)
--      recorded_on_dt / recorded_until_dt → recorded_on_dt / recorded_until_dt
--      created_at       → valid_from (recorded axis start ≈ creation here);
--                          TODO(dba): or preserve per-row created/valid split
--                          if vision kept one (it does not — no created_at col)
--      asset_id         → asset_id (now FK-valid after Phase 1)
--      context->>'plan_id' → plan_id: **NULL** — zero of the referenced plan
--                          numbers exist in resolution.implementation_plan
--                          (FK would reject); the value survives in context.
--      (no source_specification_id / source_requirement_id / intent /
--       constraints / consumed_at equivalents in vision shape → NULL)
-- =============================================================================
-- Amendment (DBA ruling 402d8a0d, record 6be5faaa): the status mapping must
-- NEVER silently default. A silent ELSE attests unknown as a default — the
-- exact epistemic failure V174's satisfaction-states and the auditor-grant
-- dispositions were built to prevent. If an unmapped source status appears,
-- the migration must refuse, not guess. Pre-insert guard (raises loudly) so
-- the mapping CASE below carries no default branch.
DO $$
DECLARE
    unmapped TEXT[];
BEGIN
    SELECT array_agg(DISTINCT lower(v.status))
      INTO unmapped
      FROM vision.work_requests v
     WHERE lower(v.status) NOT IN
           ('draft', 'pending', 'settled', 'completed', 'cancelled');

    IF unmapped IS NOT NULL THEN
        RAISE EXCEPTION 'V186 backfill: unmapped vision.work_requests.status value(s) % — refusing to apply instead of silently defaulting. Add an explicit mapping (or extend the resolution.work_request vocab) before migrating.', unmapped;
    END IF;
END
$$;

INSERT INTO resolution.work_request
       (id, asset_id, title, business_status, intent, context, constraints,
        dco_json, legacy_id, step_outputs, created_at, updated_at,
        valid_from, valid_until, recorded_on_dt, recorded_until_dt)
SELECT
    v.work_request_uuid,
    v.asset_id,
    COALESCE(NULLIF(v.title, ''), '(untitled legacy WR)'),
    (CASE lower(v.status)
        WHEN 'draft'     THEN 'DRAFT'
        WHEN 'pending'   THEN 'DRAFT'
        WHEN 'settled'   THEN 'COMPLETED'
        WHEN 'completed' THEN 'COMPLETED'
        WHEN 'cancelled' THEN 'CANCELLED'
        -- no ELSE: all reachable values are enumerated; the guard above has
        -- already raised on anything this CASE cannot map.
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

-- 2b. nebula row → same table (the shape is a near-ancestor; direct copy).
--     One row, DRAFT, legacy_id already 'sysadmin-maintenance-002' — kept as
--     legacy_id = 'nebula.work_requests_history:<uuid>' for crosswalk clarity.
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
    v_missing_int INTEGER;
BEGIN
    -- 3a. every vision row is represented exactly once
    SELECT count(*) INTO v_missing_int
    FROM vision.work_requests v
    WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request c
                      WHERE c.legacy_id = 'vision.work_requests:' || v.wr_id);
    IF v_missing_int > 0 THEN
        RAISE EXCEPTION 'V186-VALIDATE-001: % vision rows missing after backfill', v_missing_int;
    END IF;

    -- 3b. every nebula row represented
    SELECT count(*) INTO v_missing_int
    FROM nebula.work_requests_history n
    WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request c
                      WHERE c.legacy_id = 'nebula.work_requests_history:' || n.id::text);
    IF v_missing_int > 0 THEN
        RAISE EXCEPTION 'V186-VALIDATE-002: % nebula rows missing after backfill', v_missing_int;
    END IF;

    -- 3c. no legacy_id duplicates
    IF EXISTS (SELECT 1 FROM resolution.work_request
               GROUP BY legacy_id HAVING count(*) > 1) THEN
        RAISE EXCEPTION 'V186-VALIDATE-003: duplicate legacy_id detected';
    END IF;

    -- 3d. bitemporal integrity on the new rows
    IF EXISTS (SELECT 1 FROM resolution.work_request
               WHERE legacy_id LIKE 'vision.work_requests:%'
                 AND (valid_until <= valid_from OR recorded_until_dt <= recorded_on_dt)) THEN
        RAISE EXCEPTION 'V186-VALIDATE-004: bitemporal integrity violation in backfilled rows';
    END IF;
END $$;

COMMIT;

-- =============================================================================
-- ── PHASE 4 — writer repoint (application-side; paired with PR #366 + this) ──
--    NOT DDL — enumerated here because the migration is not "done" without it.
--    Verified writer/reader map (from the 2026-09-19 blast-radius + today's
--    probes; PR #366 is the compat router that already serves the absorbed
--    surface from losm-host):
--      W1 vision-srv  WR write path  → RETIRED on cutover (service retires)
--      W2 losm-host   losm_store.create/update WR → REPOINT at
--         resolution.work_request (new repo functions; string wr_id stays the
--         API address, now = legacy_id suffix instead of vision PK)
--      W3 conduit db_adapter (CORRECTED by consumer map bd78662a/2026-09-19:
--         nebula-srv is a READER, not a writer) → add_work_request
--         (:1399 INSERT INTO nebula.work_requests_history) and
--         update_work_request_status (:1433/:1438 UPDATE via the
--         nebula.work_requests VIEW by legacy_id) → REPOINT at
--         resolution.work_request
--      W4 cascade admission_subscriber (:247 INSERT INTO
--         nebula.work_requests_history) → REPOINT at resolution.work_request
--      R1 vision reads (work_requests / _losm / _edges / _dag views) → all
--         become pass-through views over resolution (Phase 5 keeps names alive)
--      R2 conduit db_adapter → reads base vision.work_requests today; UNAFFECTED
--         by Phase 5 as long as the view keeps the base's column names (it does,
--         SELECT *); conduit repoint is explicitly OUT OF THIS TRANCHE.
--      R3 nebula-srv (READER ONLY — ~10 routes via the nebula.work_requests
--         view + a vision.work_requests join at routes.ts:4041) → reads keep
--         working through the Phase-5 pass-through views; optional repoint later.
--      R4 address-tts (python + MCP) → reads conduit.work_request_state/_events
--         (conduit-local event sourcing — out of this tranche).
--      R5 tackle (planner_mcp, agent_scheduler_runner) → reads execution.requests
--         READY counts — indirect WR consumer via the execution ledger; the
--         execution.requests.source_wr_id FK (nebula history, 1/410 rows filled)
--         must keep resolvable lineage through the legacy_id crosswalk.
--      NOTE scratch.work_requests reads NEBULA history (not its own
--         work_requests_history — that base is orphaned, 1 row, zero code
--         consumers; archive candidate for the DBA).
-- =============================================================================

-- =============================================================================
-- ── PHASE 5 — demote legacy surfaces to views (AFTER writer cutover) ────────
--    Runs as a SEPARATE, separately-reviewed migration (V187 sketch below).
--    Never in the same transaction as the backfill: views must only flip once
--    no writer still targets the base tables.
-- =============================================================================

-- =============================================================================
-- PHASE 5 SKETCH (V187__work_request_demote_views_DRAFT.sql) — not executed here
-- =============================================================================
-- BEGIN;
-- -- 5a. vision.work_requests: base table → pass-through view over resolution.
-- --     Column names preserved 1:1 (conduit reads base names; SELECT * view is
-- --     wire-compatible for reads). Requires the hatch: writes go through
-- --     resolution only.
-- DROP TABLE vision.work_requests CASCADE;   -- ← CASCADE AUDIT REQUIRED: drops
--     -- trg_canonical_wr_landing_guard, trg_vision_work_requests_asset,
--     -- uq/uq/idx indexes, and any dependent views (work_requests_losm,
--     -- work_request_edges, work_request_dag, scratch mirrors) — all recreated
--     -- below. DBA to confirm no OTHER dependents at apply time.
--
-- CREATE VIEW vision.work_requests AS
-- SELECT
--     (split_part(c.legacy_id, ':', 3))::bigint          AS id,            -- surrogate
--     split_part(c.legacy_id, ':', 3)                    AS wr_id,         -- text address
--     c.dco_json, c.context, lower(c.business_status)    AS status,
--     c.step_outputs,
--     c.recorded_on_dt, c.recorded_until_dt,
--     c.id::text                                         AS work_request_uuid,
--     c.title,
--     NULL::uuid                                         AS nexus_work_request_id,
--     c.asset_id,
--     NULL::text                                         AS entity_key,
--     NULL::text                                         AS business_key,
--     NULL::jsonb                                        AS relation_payload,
--     NULL::jsonb                                        AS intent_payload,
--     NULL::jsonb                                        AS lineage,
--     NULL::jsonb                                        AS decomposition,
--     NULL::jsonb                                        AS execution_linkage,
--     NULL::jsonb                                        AS evidence_obligations,
--     NULL::jsonb                                        AS inquiry,
--     NULL::text                                         AS shape_version
-- FROM resolution.work_request c
-- WHERE c.legacy_id LIKE 'vision.work_requests:%';
--
-- -- 5b. work_requests_losm: SAME body as above (it was a filtered/normalized
-- --     view before; as a pass-through it finally makes the LOSM surface
-- --     writable through resolution — the incident e772b969 failure mode dies
-- --     with the base table). Recreate work_request_edges/_dag as views over
-- --     resolution.work_request_edge, same column-preservation rule.
-- -- 5c. nebula: work_requests_history base → archive (RENAME TO
-- --     work_requests_history_pre_v187) and recreate nebula.work_requests view
-- --     over resolution (its existing view definition already projects the
-- --     canonical shape — it becomes a WHERE-clause shim).
-- -- 5d. Postconditions: every legacy name still answerable; counts match
-- --     (6 vision + 1 nebula); conduit smoke read 200.
-- -- 5e. Rollback = inverse: rename views, restore archived bases from the
-- --     pre-apply snapshot (Phase 0 gate requires pg_dump of the three objects).
-- COMMIT;
