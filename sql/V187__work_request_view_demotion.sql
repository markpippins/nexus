-- =============================================================================
-- V187 — LEGACY WR VIEW DEMOTION (plan 8261650 Stage 6, Option B ruling)
-- DBA pre-stage · STAGED INERT · NOT APPLIED TO LIVE
-- =============================================================================
-- ⚠️  APPLY GATES — ALL REQUIRED, IN ORDER ⚠️
--   1. Stages 2–4 (writer repoints: conduit db_adapter, cascade
--      admission_subscriber, losm_store) merged and live.
--   2. Stage 5 cutover verified (losm-host serving WR writes; parity probes
--      green).
--   3. Explicit operator go (ruling thread 402d8a0d).
--   4. R9: vanadium replication question resolved at apply time.
-- Applying early demotes surfaces live writers still target — the staged
-- order exists precisely to prevent that.
--
-- PHASES:
--   0 — pre-flight refusals: V186 backfill present; idempotency; unmapped
--       statuses; execution.requests FK resolvability; unexpected dependents;
--       conduit event inventory (notice)
--   1 — repair the V186 entity_key gap (additive column + one-shot backfill
--       from the legacy bases while they still hold the data)
--   2 — legacy identity map: vision.work_request_legacy_id_map
--       (bigint id ↔ wr_id ↔ work_request_uuid, xmin+snapshot evidence)
--   3 — overview views dropped, then legacy triggers/functions/views dropped
--       by explicit name (NO CASCADE), the four vision bases renamed to
--       *_v187_archive, append-only hardening (nebula history deliberately
--       stays a table — it remains execution.requests' FK target)
--   4 — reconstructed read surfaces over the canonical store (legacy shapes),
--       and ONLY THEN the two v_work_request_overview variants rebuilt —
--       their query trees must bind to the rebuilt surfaces, not to the
--       renamed-away originals
--   5 — post-conditions + audit trail
--
-- DEFINING DESIGN FACTS (verified live 2026-09-20, titanium):
--   * vision.work_requests           — TABLE, 6 rows (statuses draft/settled)
--   * vision.work_requests_history   — TABLE, EMPTY (bitemporal substrate)
--   * vision.work_requests_losm      — VIEW over the empty history → reads 0
--     rows on live: the losm read path is DEAD (the store is broken on read
--     as well as write — incident e772b969). The rebuild RESURRECTS it
--     (0→6 rows); this is a deliberate, documented change, not parity.
--   * vision.work_request_edges/_dag — views over empty edges_history
--   * nebula.work_requests           — VIEW over nebula.work_requests_history
--   * entity_key: populated on ALL 7 legacy rows, ABSENT from canonical
--     (V186 migrated 16 of 22 vision columns). Repaired in PHASE 1; rebuilt
--     surfaces additionally coalesce dco_json->>'entity_key'.
--   * envelope columns (business_key, relation_payload, intent_payload,
--     lineage, decomposition, execution_linkage, evidence_obligations,
--     inquiry, shape_version): all NULL on all 7 live rows; archives
--     preserve them byte-exact.
--   * FKs INTO legacy bases: execution.requests.source_wr_id →
--     nebula.work_requests_history(id). The nebula history table is NOT
--     renamed (FK target preserved as-is); only its read surface demotes.
--   * KNOWN SHAPE DRIFT (documented, not silent): post-demotion, the rebuilt
--     edges view id is uuid (canonical PK), not the legacy integer; the dag
--     view's path keeps plain text (fixes the legacy VARCHAR(36) typmod
--     defect D3 — see losm-chain README).
--
-- REFS: ruling 402d8a0d · plan 8261650 · consumer map e3850398 · V186 ·
--       closed PR #369 · R1 b2c3fef1.
-- =============================================================================

BEGIN;

-- ── advisory locks (ordered: canonical → nebula → vision → execution) ───────
SELECT pg_advisory_xact_lock(hashtext('v187.work_request.demotion'));
SELECT pg_advisory_xact_lock(hashtext('v187.nebula.work_requests_history'));
SELECT pg_advisory_xact_lock(hashtext('v187.vision.work_requests'));
SELECT pg_advisory_xact_lock(hashtext('v187.execution.requests'));

-- =============================================================================
-- PHASE 0 — pre-flight refusals (fail the migration BEFORE any mutation)
-- =============================================================================

-- 0a. the V186 backfill must already be present: every legacy row has a
--     canonical counterpart via the legacy_id crosswalk
DO $$
DECLARE
    v_missing bigint;
BEGIN
    SELECT count(*) INTO v_missing
    FROM vision.work_requests v
    WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request c
                      WHERE c.legacy_id = 'vision.work_requests:' || v.wr_id);
    v_missing := v_missing + (
        SELECT count(*) FROM nebula.work_requests_history n
        WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request c
                          WHERE c.legacy_id = 'nebula.work_requests_history:' || n.id::text));
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V187-PREFLIGHT-001: % legacy rows lack canonical counterparts — V186 backfill must run first', v_missing;
    END IF;
END $$;

-- 0b. idempotency: a second apply must refuse by NAME, not by a raw
--     mid-Phase-3 error against demoted surfaces
DO $$
BEGIN
    IF to_regclass('vision.work_requests_v187_archive') IS NOT NULL THEN
        RAISE EXCEPTION 'V187-IDEM-001: demotion already applied (vision.work_requests_v187_archive exists)';
    END IF;
END $$;

-- 0c. unmapped legacy statuses must not exist at apply time
DO $$
DECLARE
    v_offenders text;
BEGIN
    SELECT string_agg(DISTINCT status, ', ')
      INTO v_offenders
      FROM vision.work_requests
     WHERE lower(status) NOT IN ('draft', 'pending', 'settled', 'completed', 'cancelled');
    IF v_offenders IS NOT NULL THEN
        RAISE EXCEPTION 'V187-PREFLIGHT-002: unmapped legacy statuses: % — extend the mapping before demotion', v_offenders;
    END IF;
END $$;

-- 0c'. every execution.requests.source_wr_id resolves by construction:
--     0a guarantees every nebula history row has a canonical counterpart and
--     the FK pins source_wr_id to existing nebula rows — no separate gate
--     (a subsumed check is noise, not defense).

-- 0e. every vision work_request_uuid must be uuid-shaped (the legacy id map
--     stores uuid; a non-uuid legacy id must abort with a named gate, not a
--     raw cast error mid-migration)
DO $$
DECLARE
    v_bad text;
BEGIN
    SELECT string_agg(DISTINCT wr_id, ', ') INTO v_bad
    FROM vision.work_requests
    WHERE work_request_uuid IS NULL
       OR work_request_uuid::text !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$';
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'V187-PREFLIGHT-006: non-uuid work_request_uuid on legacy rows: %', v_bad;
    END IF;
END $$;

-- 0f. unexpected-dependents gate — the CASCADE audit, computed at apply time.
--     The only rewrite-dependents allowed on the legacy surfaces are the five
--     this migration itself rebuilds. Anything else (a new consumer added
--     between pre-stage and apply) must abort, name itself, and be added to
--     the allowlist deliberately.
DO $$
DECLARE
    v_unexpected text;
BEGIN
    WITH dependents AS (
        SELECT DISTINCT r.ev_class::regclass::text AS dep,
                        d.refobjid::regclass::text AS src
        FROM pg_depend d
        JOIN pg_rewrite r ON r.oid = d.objid
        WHERE d.refobjid IN ('vision.work_requests'::regclass,
                             'vision.work_requests_history'::regclass,
                             'vision.work_request_edges_history'::regclass,
                             'vision.work_request_shape_registry'::regclass)
          AND r.ev_class <> d.refobjid
          AND (SELECT nspname FROM pg_namespace WHERE oid = (SELECT relnamespace FROM pg_class WHERE oid = r.ev_class)) <> 'pg_catalog'
    )
    SELECT string_agg(DISTINCT dep, ', ') INTO v_unexpected
    FROM dependents
    WHERE dep NOT IN ('vision.work_requests_losm',
                      'vision.work_request_dag',
                      'vision.work_request_edges',
                      'nebula.v_work_request_overview',
                      'scratch.v_work_request_overview');
    IF v_unexpected IS NOT NULL THEN
        RAISE EXCEPTION 'V187-PREFLIGHT-005: unexpected dependents on the legacy WR surfaces: % — extend the allowlist deliberately', v_unexpected;
    END IF;
END $$;

-- 0f. conduit WR event ledger inventory (event tables survive demotion;
--     reader smoke is the postcondition)
DO $$
DECLARE
    v_tables text;
BEGIN
    SELECT coalesce(string_agg(table_name, ', '), 'NONE')
      INTO v_tables
      FROM information_schema.tables
     WHERE table_schema = 'conduit'
       AND (table_name LIKE 'work_request%event%' OR table_name LIKE 'work_request%state%');
    RAISE NOTICE 'V187-PREFLIGHT-004: conduit WR event/state surfaces: %', v_tables;
END $$;

-- =============================================================================
-- PHASE 1 — repair the V186 entity_key gap (additive, one-shot)
-- =============================================================================

ALTER TABLE resolution.work_request ADD COLUMN IF NOT EXISTS entity_key text;

UPDATE resolution.work_request c
SET entity_key = v.entity_key
FROM vision.work_requests v
WHERE c.legacy_id = 'vision.work_requests:' || v.wr_id
  AND c.entity_key IS NULL
  AND v.entity_key IS NOT NULL;

UPDATE resolution.work_request c
SET entity_key = n.entity_key
FROM nebula.work_requests_history n
WHERE c.legacy_id = 'nebula.work_requests_history:' || n.id::text
  AND c.entity_key IS NULL
  AND n.entity_key IS NOT NULL;

-- =============================================================================
-- PHASE 2 — preserve legacy identity: bigint id ↔ wr_id ↔ uuid map
-- =============================================================================

-- 2a. map table (idempotent recreate; apply-time artifact that the rebuilt
--     views continue to depend on)
DROP TABLE IF EXISTS vision.work_request_legacy_id_map;
CREATE TABLE vision.work_request_legacy_id_map (
    id                bigint PRIMARY KEY,
    wr_id             text NOT NULL,
    work_request_uuid uuid,
    mapped_at         timestamptz NOT NULL DEFAULT now()
);

INSERT INTO vision.work_request_legacy_id_map (id, wr_id, work_request_uuid)
SELECT id, wr_id, work_request_uuid::uuid
FROM vision.work_requests v
WHERE NOT EXISTS (
    SELECT 1 FROM vision.work_request_legacy_id_map m
    WHERE m.id = v.id
);

-- 2b. every canonical vision-legacy row must resolve through the map
DO $$
DECLARE
    v_unresolved bigint;
BEGIN
    SELECT count(*) INTO v_unresolved
    FROM resolution.work_request c
    WHERE c.legacy_id LIKE 'vision.work_requests:%'
      AND NOT EXISTS (
          SELECT 1 FROM vision.work_request_legacy_id_map m
          WHERE c.legacy_id = 'vision.work_requests:' || m.wr_id
      );
    IF v_unresolved > 0 THEN
        RAISE EXCEPTION 'V187-MAP-001: % canonical rows do not resolve through the legacy id map', v_unresolved;
    END IF;
END $$;

-- 2c. recorded mapping evidence (xmin + snapshot) for the audit story
DO $$
DECLARE
    v_xmin text;
    v_snap text;
BEGIN
    SELECT min(xmin::text) INTO v_xmin FROM vision.work_requests;
    SELECT txid_current_snapshot()::text INTO v_snap;
    RAISE NOTICE 'V187-MAP-002: legacy map seeded — xmin %, snapshot %', v_xmin, v_snap;
END $$;

-- =============================================================================
-- PHASE 3 — archive-then-demotion (drop order is dependency-correct:
--           overview views first, legacy surfaces next, rename last)
-- =============================================================================

-- 3a. overview views dropped FIRST (rebuilt in 4f against the rebuilt
--     surfaces — rebuilding them now would bind their query trees to the
--     original view/base OIDs, leaving them silently pointing at the
--     renamed-away archive tables after 3c)
DROP VIEW IF EXISTS scratch.v_work_request_overview;
DROP VIEW IF EXISTS nebula.v_work_request_overview;

-- 3b. drop legacy triggers, their functions, and the legacy views by
--     explicit name (NO CASCADE — the inventory is pinned)
DROP TRIGGER IF EXISTS trg_canonical_wr_landing_guard ON vision.work_requests;
DROP TRIGGER IF EXISTS trg_vision_work_requests_asset ON vision.work_requests;
DROP TRIGGER IF EXISTS trg_work_request_edges_insert ON vision.work_request_edges;
DROP TRIGGER IF EXISTS trg_work_request_edges_update ON vision.work_request_edges;
DROP TRIGGER IF EXISTS trg_work_request_edges_delete ON vision.work_request_edges;
DROP TRIGGER IF EXISTS trg_wr_shape_registry_single_ratified ON vision.work_request_shape_registry;

DROP FUNCTION IF EXISTS vision.trg_canonical_wr_landing_guard();
DROP FUNCTION IF EXISTS vision.vision_work_request_asset_trigger();
DROP FUNCTION IF EXISTS vision.work_request_edges_insert_trigger();
DROP FUNCTION IF EXISTS vision.work_request_edges_update_trigger();
DROP FUNCTION IF EXISTS vision.work_request_edges_delete_trigger();
DROP FUNCTION IF EXISTS vision.trg_work_request_shape_registry_single_ratified();

DROP VIEW IF EXISTS vision.work_requests_losm;
DROP VIEW IF EXISTS vision.work_request_dag;
DROP VIEW IF EXISTS vision.work_request_edges;
DROP VIEW IF EXISTS nebula.work_requests;

-- 3c. rename the vision bases to archive (data retained byte-exact). The
--     nebula history table is deliberately NOT renamed — execution.requests
--     keeps its FK target; write-freezing comes from the W3 repoint.
ALTER TABLE vision.work_requests RENAME TO work_requests_v187_archive;
ALTER TABLE vision.work_requests_history RENAME TO work_requests_history_v187_archive;
ALTER TABLE vision.work_request_edges_history RENAME TO work_request_edges_history_v187_archive;
ALTER TABLE vision.work_request_shape_registry RENAME TO work_request_shape_registry_v187_archive;

-- 3d. append-only hardening on the archives (INSERT allowed; mutation refused)
CREATE OR REPLACE FUNCTION vision.v187_archive_append_only() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION 'V187: % on % refused — archive tables are append-only (insert ok)',
        TG_OP, TG_TABLE_NAME;
END $fn$;

CREATE TRIGGER archive_no_update BEFORE UPDATE OR DELETE ON vision.work_requests_v187_archive
    FOR EACH ROW EXECUTE FUNCTION vision.v187_archive_append_only();
CREATE TRIGGER archive_no_update BEFORE UPDATE OR DELETE ON vision.work_requests_history_v187_archive
    FOR EACH ROW EXECUTE FUNCTION vision.v187_archive_append_only();
CREATE TRIGGER archive_no_update BEFORE UPDATE OR DELETE ON vision.work_request_edges_history_v187_archive
    FOR EACH ROW EXECUTE FUNCTION vision.v187_archive_append_only();
CREATE TRIGGER archive_no_update BEFORE UPDATE OR DELETE ON vision.work_request_shape_registry_v187_archive
    FOR EACH ROW EXECUTE FUNCTION vision.v187_archive_append_only();

-- =============================================================================
-- PHASE 4 — reconstructed read surfaces over the canonical store
-- =============================================================================

-- 4a. vision.work_requests — the legacy 22-column read shape
CREATE VIEW vision.work_requests AS
SELECT v.id,
    v.wr_id,
    v.dco_json,
    v.context,
    v.status,
    v.step_outputs,
    v.recorded_on_dt,
    v.recorded_until_dt,
    v.work_request_uuid,
    v.title,
    v.nexus_work_request_id,
    v.asset_id,
    v.entity_key,
    v.business_key,
    v.relation_payload,
    v.intent_payload,
    v.lineage,
    v.decomposition,
    v.execution_linkage,
    v.evidence_obligations,
    v.inquiry,
    v.shape_version
FROM (
    SELECT m.id,
        m.wr_id,
        c.dco_json,
        c.context,
        lower(c.business_status) AS status,
        c.step_outputs,
        c.recorded_on_dt,
        c.recorded_until_dt,
        m.work_request_uuid::text AS work_request_uuid,
        c.title,
        m.work_request_uuid::text AS nexus_work_request_id,
        c.asset_id,
        coalesce(c.entity_key, (c.dco_json::jsonb)->>'entity_key') AS entity_key,
        (c.dco_json::jsonb)->>'business_key' AS business_key,
        (c.dco_json::jsonb)->'relation_payload' AS relation_payload,
        (c.dco_json::jsonb)->'intent_payload' AS intent_payload,
        (c.dco_json::jsonb)->'lineage' AS lineage,
        (c.dco_json::jsonb)->'decomposition' AS decomposition,
        (c.dco_json::jsonb)->'execution_linkage' AS execution_linkage,
        (c.dco_json::jsonb)->'evidence_obligations' AS evidence_obligations,
        (c.dco_json::jsonb)->'inquiry' AS inquiry,
        (c.dco_json::jsonb)->>'shape_version' AS shape_version
    FROM vision.work_request_legacy_id_map m
    JOIN resolution.work_request c
      ON c.legacy_id = 'vision.work_requests:' || m.wr_id
) v;

-- 4b. vision.work_requests_losm — legacy read shape, corrected vocabulary
--     (documented changes vs the legacy view: cancelled→CANCELLED, not
--     →FAILED; no silent ELSE — unmapped statuses surface as NULL)
CREATE VIEW vision.work_requests_losm AS
SELECT h.id::integer AS id,
    h.wr_id,
    h.parent_request_id,
    h.intent,
    h.constraints,
    h.priority,
    h.context,
    CASE
        WHEN h.status = 'new' THEN 'NEW'
        WHEN h.status = 'intake' THEN 'INTAKE'
        WHEN h.status = 'plan_generation' THEN 'PLAN_GENERATION'
        WHEN h.status = 'plan_review' THEN 'PLAN_REVIEW'
        WHEN h.status = 'plan_approval_gate' THEN 'PLAN_APPROVAL_GATE'
        WHEN h.status = 'spec_generation' THEN 'SPEC_GENERATION'
        WHEN h.status = 'execution' THEN 'EXECUTION'
        WHEN h.status = 'validation' THEN 'VALIDATION'
        WHEN h.status = 'completion' THEN 'COMPLETION'
        WHEN h.status = 'blocked' THEN 'BLOCKED'
        WHEN h.status = 'failed' THEN 'FAILED'
        WHEN h.status = 'pending' THEN 'NEW'
        WHEN h.status = 'draft' THEN 'NEW'
        WHEN h.status = 'completed' THEN 'COMPLETION'
        WHEN h.status = 'settled' THEN 'COMPLETION'
        WHEN h.status = 'cancelled' THEN 'CANCELLED'
        -- No ELSE. Unmapped statuses surface as NULL — unmapped is honest.
    END AS status,
    h.created_at,
    h.recorded_on_dt,
    h.recorded_until_dt
FROM (
    SELECT m.id,
        m.wr_id,
        NULL::varchar AS parent_request_id,
        c.intent,
        c.constraints,
        NULL::integer AS priority,
        c.context,
        lower(c.business_status) AS status,
        c.created_at,
        c.recorded_on_dt,
        c.recorded_until_dt
    FROM vision.work_request_legacy_id_map m
    JOIN resolution.work_request c
      ON c.legacy_id = 'vision.work_requests:' || m.wr_id
    WHERE c.valid_until = 'infinity'::timestamptz
) h;

-- 4c. vision.work_request_edges — legacy string-addressed read shape via the
--     uuid↔wr_id map (empty until canonical edges data exists; note the
--     documented id type drift integer→uuid)
CREATE VIEW vision.work_request_edges AS
SELECT e.id,
    e.id::text AS edge_id,
    map_parent.wr_id AS parent_wr_id,
    map_child.wr_id AS child_wr_id,
    e.edge_type,
    e.metadata,
    e.created_at,
    e.valid_from AS recorded_on_dt,
    e.valid_until AS recorded_until_dt
FROM resolution.work_request_edge e
LEFT JOIN vision.work_request_legacy_id_map map_parent
       ON map_parent.work_request_uuid = e.parent_work_request_id
LEFT JOIN vision.work_request_legacy_id_map map_child
       ON map_child.work_request_uuid = e.child_work_request_id;

-- 4d. vision.work_request_dag — legacy 11-column read shape (recursive CTE
--     reconstructed over canonical; plain-text path fixes typmod defect D3)
CREATE VIEW vision.work_request_dag AS
WITH RECURSIVE dag_tree AS (
    SELECT m.wr_id AS node_wr_id,
        m.wr_id AS root_wr_id,
        m.wr_id::text AS path,
        0 AS depth,
        false AS is_cycle,
        c.intent,
        lower(c.business_status) AS status,
        NULL::integer AS priority,
        NULL::varchar AS parent_wr_id,
        NULL::varchar AS edge_type,
        c.created_at
    FROM vision.work_request_legacy_id_map m
    JOIN resolution.work_request c ON c.legacy_id = 'vision.work_requests:' || m.wr_id
    WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request_edge e
                      WHERE e.child_work_request_id = m.work_request_uuid)
  UNION ALL
    SELECT child_map.wr_id,
        dt.root_wr_id,
        dt.path || '->' || child_map.wr_id,
        dt.depth + 1,
        (dt.path || '->' || child_map.wr_id) LIKE '%' || child_map.wr_id || '%',
        child.intent,
        lower(child.business_status),
        NULL::integer,
        parent_map.wr_id,
        e.edge_type,
        child.created_at
    FROM dag_tree dt
    JOIN resolution.work_request_edge e
      ON e.parent_work_request_id = (SELECT m2.work_request_uuid
                                     FROM vision.work_request_legacy_id_map m2
                                     WHERE m2.wr_id = dt.node_wr_id)
    JOIN vision.work_request_legacy_id_map child_map
      ON child_map.work_request_uuid = e.child_work_request_id
    JOIN resolution.work_request child
      ON child.legacy_id = 'vision.work_requests:' || child_map.wr_id
    JOIN vision.work_request_legacy_id_map parent_map
      ON parent_map.work_request_uuid = e.parent_work_request_id
    WHERE dt.depth < 32
)
SELECT node_wr_id, root_wr_id, path, depth, is_cycle, intent, status, priority,
       parent_wr_id, edge_type, created_at
FROM dag_tree;

-- 4e. nebula.work_requests — the legacy 23-column read shape over canonical,
--     row set PRESERVED: the legacy view showed only nebula-origin rows
--     (it read the nebula history store), so the rebuild restricts to the
--     nebula legacy prefix. Whether vision-origin rows should surface here
--     post-repoint is W3's writer-convention decision, not a migration side
--     effect.
CREATE VIEW nebula.work_requests AS
SELECT c.id,
    c.title,
    c.description,
    c.source_specification_id,
    c.source_requirement_id,
    c.business_status,
    c.intent,
    c.context,
    c.constraints,
    c.created_by,
    c.created_at,
    c.updated_at,
    c.dco_json,
    c.legacy_id,
    c.plan_id,
    c.step_outputs,
    c.consumed_at,
    c.valid_from,
    c.valid_until,
    c.recorded_on_dt,
    c.recorded_until_dt,
    c.asset_id,
    coalesce(c.entity_key, (c.dco_json::jsonb)->>'entity_key') AS entity_key
FROM resolution.work_request c
WHERE c.legacy_id LIKE 'nebula.work_requests_history:%';

-- 4f. overview views rebuilt LAST — their query trees bind to the REBUILT
--     surfaces above. Semantics carried verbatim from the live definitions
--     (pg_get_viewdef, 2026-09-20); the FROM surfaces swap to canonical+map.
--     Row set stays restricted to nebula-legacy rows (legacy_id LIKE
--     'nebula.work_requests_history:%') to preserve the original FROM-clause
--     semantics exactly; expanding it to vision rows is a roundtable
--     decision, not a migration side effect.
CREATE OR REPLACE VIEW nebula.v_work_request_overview AS
SELECT wr.id,
    wr.legacy_id,
    wr.title,
    wr.plan_id,
    wr.business_status,
    wr.consumed_at,
    wr.created_at AS business_created_at,
    er.status AS execution_status,
    er.id AS execution_request_id,
    er.business_key,
    vr.status AS runtime_status,
    vr.work_request_uuid AS vision_id,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'CANCELLED'
        WHEN vr.status = 'rejected' THEN 'REJECTED'
        WHEN vr.status = 'failed' THEN 'FAILED'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'COMPLETE'
        WHEN wr.business_status = 'COMPLETED' THEN 'AWAITING_REVIEW'
        WHEN vr.status = 'settled' THEN 'SETTLED_AWAITING_BUSINESS'
        WHEN vr.status = 'claimed' THEN 'RUNNING'
        WHEN vr.status = 'validated' THEN 'VALIDATED'
        WHEN vr.status = 'queued' THEN 'QUEUED'
        WHEN vr.status = 'deferred' THEN 'DEFERRED'
        WHEN vr.status = 'noop' THEN 'NOOP'
        WHEN er.status = 'READY' THEN 'READY_FOR_EXECUTION'
        WHEN er.status = 'ADMITTED' THEN 'ADMITTED'
        WHEN er.status = 'VALIDATED' THEN 'EXECUTION_VALIDATED'
        WHEN er.status = 'COMPLETED' THEN 'EXECUTION_COMPLETE'
        WHEN er.status = 'FAILED' THEN 'EXECUTION_FAILED'
        WHEN wr.business_status = 'DISPATCHED' THEN 'DISPATCHED'
        WHEN wr.business_status = 'APPROVED' THEN 'APPROVED'
        WHEN wr.business_status = 'DRAFT' AND wr.consumed_at IS NULL THEN 'PENDING'
        WHEN wr.business_status = 'DRAFT' THEN 'DRAFT'
        ELSE 'UNKNOWN'
    END AS effective_status,
    CASE
        WHEN wr.business_status = 'CANCELLED' THEN 'Business intent cancelled'
        WHEN vr.status = 'rejected' THEN 'Execution rejected'
        WHEN vr.status = 'failed' THEN 'Execution failed'
        WHEN wr.business_status = 'COMPLETED' AND vr.status = 'settled' THEN 'Complete - business objective satisfied'
        WHEN wr.business_status = 'COMPLETED' THEN 'Awaiting business review'
        WHEN vr.status = 'settled' THEN 'Settled - awaiting business confirmation'
        WHEN vr.status = 'claimed' THEN 'Worker actively executing'
        WHEN vr.status = 'validated' THEN 'Validated - awaiting execution'
        WHEN vr.status = 'queued' THEN 'Queued for execution'
        WHEN er.status = 'READY' THEN 'Ready for execution'
        WHEN wr.business_status = 'DISPATCHED' THEN 'Dispatched to execution layer'
        WHEN wr.business_status = 'APPROVED' THEN 'Approved - awaiting compilation'
        WHEN wr.consumed_at IS NULL THEN 'Pending - not yet consumed'
        ELSE 'Draft'
    END AS effective_status_description
FROM nebula.work_requests wr
LEFT JOIN execution.requests er
       ON er.source_wr_id::text = substring(wr.legacy_id from 30)   -- 'nebula.work_requests_history:' is 29 chars
LEFT JOIN vision.work_requests vr
       ON vr.work_request_uuid = substring(wr.legacy_id from 30)
WHERE wr.legacy_id LIKE 'nebula.work_requests_history:%';

CREATE OR REPLACE VIEW scratch.v_work_request_overview AS
SELECT * FROM nebula.v_work_request_overview;

-- =============================================================================
-- PHASE 5 — post-conditions + audit trail
-- =============================================================================

DO $$
DECLARE
    v_missing bigint;
    v_archive bigint;
    v_mapped  bigint;
BEGIN
    -- 5a. read parity: every live canonical vision-legacy row must surface
    --     through the rebuilt losm view
    SELECT count(*) INTO v_missing
    FROM resolution.work_request c
    WHERE c.legacy_id LIKE 'vision.work_requests:%'
      AND c.valid_until = 'infinity'::timestamptz
      AND NOT EXISTS (SELECT 1 FROM vision.work_requests_losm l
                      WHERE l.wr_id = substring(c.legacy_id from 22));
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'V187-POST-001: % canonical rows not surfaced by the rebuilt losm view', v_missing;
    END IF;

    -- 5b. archives retained: archived vision rows = mapped rows (invariant,
    --     not a hardcoded count)
    SELECT count(*) INTO v_archive FROM vision.work_requests_v187_archive;
    SELECT count(*) INTO v_mapped  FROM vision.work_request_legacy_id_map;
    IF v_archive <> v_mapped THEN
        RAISE EXCEPTION 'V187-POST-002: archive rows (%) != mapped rows (%)', v_archive, v_mapped;
    END IF;

    -- 5c. overview views resolve against the rebuilt surfaces
    PERFORM count(*) FROM nebula.v_work_request_overview;
    PERFORM count(*) FROM scratch.v_work_request_overview;
END $$;

INSERT INTO tackle.system_logs (id, timestamp, category, level, message, details)
VALUES (gen_random_uuid()::text,
        now(),
        'NEBULA_AUDIT',
        'INFO',
        'V187 legacy WR demotion applied: bases archived, views rebuilt over resolution.work_request',
        '{"migration":"V187","plan":"8261650","stage":6}'::jsonb);

COMMIT;
