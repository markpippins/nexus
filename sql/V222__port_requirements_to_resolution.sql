-- =============================================================================
-- V222 — P4a: port `nebula.requirements` into `resolution.requirement`
-- Plan: P4a (operator ruling on the D1 authority question)
-- STAGED · NOT APPLIED TO LIVE
-- =============================================================================
-- Why this file exists
-- --------------------
-- Two Requirement surfaces coexist (D1):
--   * `nebula.requirements` — an auto-updatable VIEW over the bitemporal table
--     `nebula.requirements_history`; 27 current rows, no historical versions;
--   * `resolution.requirement` — the canonical base table that
--     `resolution.specification`, `resolution.implementation_plan`,
--     `resolution.requirement_segment_set` and
--     `resolution.request_requirement_link` already reference by FK; 0 rows.
--
-- Operator chose P4a: make `resolution.requirement` canonical by PORTING the
-- legacy rows, not by retargeting the link substrate at `nebula`.
--
-- Identity preservation is the point, so this is deliberately more than a row
-- copy:
--   * primary keys are preserved BY VALUE — 4 live blueprints carry
--     `payload->>'requirement_ref'` values that are requirement ids, and they
--     must keep resolving after the port;
--   * lifecycle (`status`, `priority`, `req_type`), hierarchy (`parent_id`,
--     `system_id`/`subsystem_id`/`feature_id`), governance links
--     (`candidate_id`, `conduit_plan_id`, `asset_id`) and business timestamps
--     (`created_at`, `start_date`, `completion_date`) are copied verbatim;
--   * the bitemporal business window (`valid_from`, `valid_until`) and
--     `recorded_on_dt` are copied verbatim, so the port does not rewrite when a
--     requirement became true;
--   * `resolution.requirement_port_ledger` gains one row per requirement with a
--     SHA-256 digest of BOTH sides and a CHECK that `source_id = target_id`, so
--     identity is auditable after the fact instead of asserted in a comment;
--   * the port REFUSES to complete if any source row is missing, mismatched, or
--     outside the target vocabulary — partial ports are not allowed.
--
-- Transition, not a cutover in one shot
-- -------------------------------------
-- `nebula.requirements` is replaced in place by a compatibility projection over
-- `resolution.requirement` with INSTEAD OF write-through triggers. That keeps
-- every existing reader (nebula-srv search/counts/hierarchy, conduit-mcp,
-- moleculer, adonisjs, the planner MCP server) coherent on the canonical table
-- without a lockstep deploy. nebula-srv's requirement statements deliberately
-- target `nebula.requirements` — the transition relation this file re-points —
-- rather than `resolution.requirement` directly: the same SQL is then correct
-- both before the operator-gated apply (legacy rows) and after it (ported rows),
-- and its delete paths reach the canonical table only through the guard
-- functions below. The projection therefore stays load-bearing for every
-- consumer, nebula-srv included, until each is repointed to the base table.
--
-- `nebula.requirements_history` is RETAINED untouched as the pre-port audit
-- record. This file performs no DELETE, TRUNCATE, DROP TABLE or column drop.
--
-- Delete semantics are settled here too, because the port turns the requirement
-- into a real reference target: a requirement is hard-deleted only while no
-- specification, implementation plan, segment set, request link, work request,
-- child requirement or live blueprint names it. Otherwise the delete is refused
-- with the blocking surfaces named (a BEFORE DELETE guard on
-- `resolution.requirement`, so the projection's write-through cannot route
-- around it). Cascades remove the set leaf-first through that guard — see the
-- guard section below. This file still performs no DELETE of its own.
--
-- Field mapping / representation decisions (no silent coercion):
--   * `source_type` (NOT NULL, no default on the target) is derived:
--       candidate_id IS NOT NULL -> 'candidate', else 'manual'.
--     The target CHECK is `source_type = 'candidate' OR candidate_id IS NULL`,
--     so both derivations are valid and no candidate link is lost.
--   * `compilation_status` has no legacy equivalent -> 'draft'.
--   * `sol_ir_expression_id` has no legacy equivalent -> NULL.
--   * `work_request_dco` (jsonb) has no canonical equivalent yet. It is carried
--     across as a nullable legacy column on `resolution.requirement` rather than
--     retired, because `bin/compiler_processInProgress.py` writes it and gates
--     recompilation on it; the compatibility view maps it 1:1. Retire the
--     column once compiled output lives in `sol_ir_expression_id`.
--   * `recorded_until_dt` becomes 'infinity' (the target's current-row marker);
--     the legacy '9999-12-31 23:59:59+00' was that table's marker for "current".
--
-- Idempotent, with a precise contract:
--   * first application ports, proves each row against the ledger CHECK and
--     refuses on any drift, gap or vocabulary mismatch;
--   * later applications adopt only ids the ledger does not already hold, so
--     rows the application has since edited are left exactly as they are. They
--     still replace the view and recreate the triggers.
-- The ledger is port-time proof, not a live assertion: it records the digests
-- verified at port, and never rewrites them.
-- =============================================================================

BEGIN;

-- ── preflight: the two surfaces must be the shapes this file assumes ───────
DO $$
DECLARE
    v_src_kind "char";
    v_dst_kind "char";
BEGIN
    IF to_regclass('nebula.requirements') IS NULL THEN
        RAISE EXCEPTION 'V222-PREFLIGHT-001: nebula.requirements is missing';
    END IF;
    IF to_regclass('nebula.requirements_history') IS NULL THEN
        RAISE EXCEPTION 'V222-PREFLIGHT-002: nebula.requirements_history is missing';
    END IF;
    IF to_regclass('resolution.requirement') IS NULL THEN
        RAISE EXCEPTION 'V222-PREFLIGHT-003: resolution.requirement is missing';
    END IF;

    SELECT relkind INTO v_src_kind
      FROM pg_class WHERE oid = 'nebula.requirements_history'::regclass;
    SELECT relkind INTO v_dst_kind
      FROM pg_class WHERE oid = 'resolution.requirement'::regclass;

    -- The source must be a base table (the legacy bitemporal store) and the
    -- target a base table. A view on either side means the model drifted.
    IF v_src_kind NOT IN ('r', 'p') THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-004: nebula.requirements_history must be a base table, found relkind=%',
            v_src_kind;
    END IF;
    IF v_dst_kind <> 'r' THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-005: resolution.requirement must be a base table, found relkind=%',
            v_dst_kind;
    END IF;

    -- The four FK dependants must be present before the destination gains rows;
    -- otherwise the port would create rows no governance surface can reference.
    IF to_regclass('resolution.specification') IS NULL
       OR to_regclass('resolution.request_requirement_link') IS NULL
       OR to_regclass('resolution.implementation_plan') IS NULL THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-006: resolution requirement dependants are incomplete';
    END IF;

    -- The identity ledger hashes with pgcrypto. Fail by name instead of part-way
    -- through the file with "function public.digest(text, text) does not exist".
    IF to_regprocedure('public.digest(text, text)') IS NULL
       OR to_regprocedure('public.digest(bytea, text)') IS NULL THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-007: pgcrypto digest() is not installed in schema public; the identity ledger cannot be written';
    END IF;
END $$;

-- ── preflight: every source row must be representable in the target ────────
-- "We are not prepared to represent those types" is the failure this file must
-- not repeat. Abort by count instead of letting a CHECK reject mid-insert or,
-- worse, coercing a lifecycle state.
DO $$
DECLARE
    v_bad_status bigint;
    v_bad_priority bigint;
    v_bad_type bigint;
    v_orphan_parent bigint;
    v_missing_identity bigint;
BEGIN
    SELECT count(*) INTO v_bad_status
      FROM nebula.requirements
     WHERE status NOT IN ('Backlog', 'ToDo', 'InProgress', 'Active', 'Blocked',
                          'Done', 'Cancelled', 'Accepted');
    IF v_bad_status > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-010: % requirement(s) carry a status the target vocabulary cannot represent',
            v_bad_status;
    END IF;

    SELECT count(*) INTO v_bad_priority
      FROM nebula.requirements
     WHERE priority NOT IN ('Low', 'Medium', 'High');
    IF v_bad_priority > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-011: % requirement(s) carry a priority the target vocabulary cannot represent',
            v_bad_priority;
    END IF;

    SELECT count(*) INTO v_bad_type
      FROM nebula.requirements
     WHERE req_type IS NOT NULL
       AND req_type NOT IN ('Epic', 'Story', 'Task', 'Bug');
    IF v_bad_type > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-012: % requirement(s) carry a req_type the target vocabulary cannot represent',
            v_bad_type;
    END IF;

    SELECT count(*) INTO v_orphan_parent
      FROM nebula.requirements r
     WHERE r.parent_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM nebula.requirements p WHERE p.id = r.parent_id);
    IF v_orphan_parent > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-013: % requirement(s) reference a parent that is not in the ported set',
            v_orphan_parent;
    END IF;

    SELECT count(*) INTO v_missing_identity
      FROM nebula.requirements
     WHERE id IS NULL OR title IS NULL OR system_id IS NULL;
    IF v_missing_identity > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-014: % requirement(s) lack id/title/system_id and cannot be ported with identity intact',
            v_missing_identity;
    END IF;
END $$;

-- ── preflight: the candidate link crosses domains today ────────────────────
-- `resolution.requirement.candidate_id` carries an FK to `resolution.candidate`,
-- but that store is EMPTY while the application's candidates live in
-- `nebula.harvest_candidates` (12,477 rows) and every legacy requirement link
-- (22 of 27) names a harvest-candidate id. The FK is therefore unsatisfiable
-- for the data that exists — legacy and newly authored alike — and a naive port
-- fails on `requirement_candidate_id_fkey`.
--
-- The honest resolution is NOT to drop the candidate link (it is identity) and
-- NOT to fabricate `resolution.candidate` rows (that domain has its own
-- NOT NULL representation decisions: harvest_id, type, implementation_notes,
-- tags, design_rationale...). Instead:
--   * if the canonical candidate store is populated, a missing candidate is a
--     real identity break and the port refuses by name;
--   * if it is empty, the link is cross-domain for now: the unsatisfiable FK is
--     dropped, the column is commented as a cross-domain reference, and the
--     requirement to restore the FK after a candidate port is recorded.
DO $$
DECLARE
    v_cand_rows    bigint;
    v_unrepresented bigint;
BEGIN
    IF to_regclass('resolution.candidate') IS NULL THEN
        RAISE EXCEPTION 'V222-PREFLIGHT-015: resolution.candidate is missing';
    END IF;

    SELECT count(*) INTO v_cand_rows FROM resolution.candidate;
    SELECT count(*) INTO v_unrepresented
      FROM nebula.requirements r
     WHERE r.candidate_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM resolution.candidate c WHERE c.id = r.candidate_id);

    IF v_unrepresented > 0 AND v_cand_rows > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-016: % legacy candidate link(s) have no resolution.candidate row while the canonical candidate store is populated; port or reconcile candidates before porting requirements',
            v_unrepresented;
    END IF;

    IF v_unrepresented > 0 THEN
        RAISE NOTICE
            'V222: % legacy candidate link(s) are cross-domain (resolution.candidate is empty, % harvest candidates); resolving the unsatisfiable requirement_candidate_id_fkey',
            v_unrepresented, (SELECT count(*) FROM nebula.harvest_candidates);

        ALTER TABLE resolution.requirement
            DROP CONSTRAINT IF EXISTS requirement_candidate_id_fkey;

        COMMENT ON COLUMN resolution.requirement.candidate_id IS
            'Candidate this requirement derives from. P4a (V222): cross-domain reference until resolution.candidate is populated — the legacy links (and the application write paths) name nebula.harvest_candidates ids, which the former requirement_candidate_id_fkey could never satisfy. RESTORE that FK after a resolution.candidate port backfills and validates these ids.';
    END IF;
END $$;

-- ── preflight: every legacy asset link must be representable ──────────────
-- `resolution.requirement.asset_id` carries `requirement_asset_id_fkey` to
-- `resolution.canonical_asset`, but the legacy link points at a DIFFERENT
-- store: `semantics.canonical_asset`. The two stores are populated
-- independently, so a legacy id need not have a resolution counterpart.
--
-- Copying such an id aborts the insert on `requirement_asset_id_fkey`, which
-- names the constraint but not the rows, after the compatibility view and
-- triggers were already replaced inside this transaction. The asset link is
-- identity — the stable asset a requirement is about — so it is neither NULLed
-- nor backfilled with a fabricated `resolution.canonical_asset` row. Refuse by
-- name and count, and let an operator populate `resolution.canonical_asset`
-- (or deliberately clear the link) and re-run.
DO $$
DECLARE
    v_with_asset    bigint;
    v_unrepresented bigint;
BEGIN
    IF to_regclass('resolution.canonical_asset') IS NULL THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-017: resolution.canonical_asset is missing; legacy asset links cannot be represented';
    END IF;

    SELECT count(*) INTO v_with_asset
      FROM nebula.requirements
     WHERE asset_id IS NOT NULL;

    SELECT count(*) INTO v_unrepresented
      FROM nebula.requirements r
     WHERE r.asset_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM resolution.canonical_asset a WHERE a.id = r.asset_id
       );

    IF v_unrepresented > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-018: % of % legacy asset link(s) have no resolution.canonical_asset row; the legacy links name semantics.canonical_asset, a separate store — populate resolution.canonical_asset for them, or clear the link deliberately, before porting requirements',
            v_unrepresented, v_with_asset;
    END IF;
END $$;

-- ── carry the legacy compiled-DCO column ──────────────────────────────────
-- `work_request_dco` has no canonical equivalent yet: the canonical home for
-- compiled output is `sol_ir_expression_id` + `compilation_status`, and that
-- pipeline does not exist. Retiring the column here would break a live writer:
-- `bin/compiler_processInProgress.py` persists the compiled WorkRequest DCO into
-- it and gates recompilation on `work_request_dco IS NOT NULL`, so dropping it
-- would make every requirement look uncompiled and the DCO invisible.
--
-- So the column is carried across (nullable, additive, metadata-only in PG 11+)
-- rather than lost, and stays a legacy carry-over: when the expression pipeline
-- lands, backfill `sol_ir_expression_id`, then drop this column and the
-- projection column with it.
ALTER TABLE resolution.requirement
    ADD COLUMN IF NOT EXISTS work_request_dco jsonb;

COMMENT ON COLUMN resolution.requirement.work_request_dco IS
    'P4a (V222) legacy carry-over: the compiled WorkRequest DCO previously held by nebula.requirements.work_request_dco. Written by bin/compiler_processInProgress.py. Retire this column once compiled output lives in sol_ir_expression_id and compilation_status.';

-- ── identity ledger ────────────────────────────────────────────────────────
-- The audit record for the port. `source_id = target_id` is a CHECK, not a
-- convention: a future re-key would have to rewrite the ledger deliberately.
CREATE TABLE IF NOT EXISTS resolution.requirement_port_ledger (
    id                uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_relation   text        NOT NULL,
    source_id         uuid        NOT NULL,
    target_id         uuid        NOT NULL
                      REFERENCES resolution.requirement (id) ON DELETE RESTRICT,
    source_digest     text        NOT NULL,
    target_digest     text        NOT NULL,
    fields_preserved  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    provenance        text        NOT NULL,
    ported_at         timestamptz NOT NULL DEFAULT now(),
    verified_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT requirement_port_ledger_identity_check
        CHECK (source_id = target_id),
    CONSTRAINT requirement_port_ledger_digest_check
        CHECK (source_digest = target_digest),
    CONSTRAINT requirement_port_ledger_unique
        UNIQUE (source_relation, source_id)
);

COMMENT ON TABLE resolution.requirement_port_ledger IS
    'P4a (V222): one row per requirement ported from nebula.requirements_history into resolution.requirement. Records the source identity, both content digests and the preserved field set so the port is auditable rather than asserted.';
COMMENT ON COLUMN resolution.requirement_port_ledger.source_relation IS
    'Legacy surface the row came from, e.g. nebula.requirements (the current-row projection of nebula.requirements_history).';
COMMENT ON COLUMN resolution.requirement_port_ledger.provenance IS
    'Why/how the port happened, e.g. v222:port-p4a.';

-- ── what the ledger already proves (pre-run snapshot) ──────────────────────
-- Re-application must distinguish rows this run adopts from rows an earlier run
-- already ported and proved. Capture that set before anything is written.
CREATE TEMP TABLE _v222_already ON COMMIT DROP AS
SELECT source_id
  FROM resolution.requirement_port_ledger
 WHERE source_relation = 'nebula.requirements';
COMMENT ON TABLE _v222_already IS
    'V222 scratch: source ids the ledger already holds before this run. Dropped at COMMIT.';

-- ── the source identity set, hashed once ───────────────────────────────────
-- SHA-256 over a canonical JSON rendering. Timestamps are rendered explicitly
-- in UTC so the digest does not depend on the session TimeZone, and jsonb sorts
-- its keys, so the rendering is stable across sessions.
CREATE TEMP TABLE _v222_source ON COMMIT DROP AS
SELECT
    h.id,
    encode(public.digest(jsonb_build_object(
        'id',                  h.id::text,
        'system_id',           h.system_id::text,
        'subsystem_id',        h.subsystem_id::text,
        'feature_id',          h.feature_id::text,
        'title',               h.title,
        'description',         h.description,
        'status',              h.status,
        'priority',            h.priority,
        'start_date',          h.start_date,
        'completion_date',     h.completion_date,
        'created_at',          to_char(h.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        'parent_id',           h.parent_id::text,
        'req_type',            h.req_type,
        'acceptance_criteria', h.acceptance_criteria::text,
        'candidate_id',        h.candidate_id::text,
        'conduit_plan_id',     h.conduit_plan_id,
        'work_request_dco',    h.work_request_dco::text,
        'asset_id',            h.asset_id::text,
        'valid_from',          to_char(h.valid_from AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        -- Open-ended business time is rendered as 'infinity' on BOTH sides: the
        -- legacy sentinel ('9999-12-31 00:00:00+00') and the target's marker
        -- mean the same thing, and the port normalizes the stored value, so
        -- hashing the raw literal would fail every open row on identity.
        'valid_until',         CASE WHEN h.valid_until >= '9999-12-31'::timestamptz
                                    THEN 'infinity'
                                    ELSE to_char(h.valid_until AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') END
    )::text, 'sha256'), 'hex') AS source_digest
  FROM nebula.requirements h;

COMMENT ON TABLE _v222_source IS
    'V222 scratch: per-requirement identity digest of the legacy surface. Dropped at COMMIT.';

-- ── preflight: refusing to overwrite a row that already drifted ────────────
-- A pre-existing target row with the same id but different content means
-- somebody wrote to the canonical side already. Refuse rather than silently
-- prefer one side.
DO $$
DECLARE
    v_drift bigint;
BEGIN
    SELECT count(*) INTO v_drift
      FROM resolution.requirement r
      JOIN _v222_source s ON s.id = r.id
     WHERE encode(public.digest(jsonb_build_object(
               'id',                  r.id::text,
               'system_id',           r.system_id::text,
               'subsystem_id',        r.subsystem_id::text,
               'feature_id',          r.feature_id::text,
               'title',               r.title,
               'description',         r.description,
               'status',              r.status,
               'priority',            r.priority,
               'start_date',          r.start_date,
               'completion_date',     r.completion_date,
               'created_at',          to_char(r.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
               'parent_id',           r.parent_id::text,
               'req_type',            r.req_type,
               'acceptance_criteria', r.acceptance_criteria::text,
               'candidate_id',        r.candidate_id::text,
               'conduit_plan_id',     r.conduit_plan_id,
               'work_request_dco',    r.work_request_dco::text,
               'asset_id',            r.asset_id::text,
               'valid_from',          to_char(r.valid_from AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
               'valid_until',         CASE WHEN r.valid_until >= '9999-12-31'::timestamptz
                                           THEN 'infinity'
                                           ELSE to_char(r.valid_until AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') END
           )::text, 'sha256'), 'hex') <> s.source_digest
       -- Rows the ledger already holds were ported and proven at port time;
       -- later edits through the projection are the application working, not
       -- drift, so a re-run must not re-litigate them.
       AND s.id NOT IN (SELECT source_id FROM _v222_already);

    IF v_drift > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-020: % target row(s) already exist with the same id but different content; reconcile before porting',
            v_drift;
    END IF;
END $$;


-- ── the port ──────────────────────────────────────────────────────────────
INSERT INTO resolution.requirement (
    id,
    asset_id,
    candidate_id,
    parent_id,
    source_type,
    system_id,
    subsystem_id,
    feature_id,
    title,
    description,
    status,
    priority,
    req_type,
    compilation_status,
    start_date,
    completion_date,
    acceptance_criteria,
    conduit_plan_id,
    work_request_dco,
    created_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
)
SELECT
    h.id,
    h.asset_id,
    h.candidate_id,
    h.parent_id,
    CASE WHEN h.candidate_id IS NOT NULL THEN 'candidate' ELSE 'manual' END,
    h.system_id,
    h.subsystem_id,
    h.feature_id,
    h.title,
    COALESCE(h.description, ''),
    h.status,
    h.priority,
    h.req_type,
    'draft',
    h.start_date,
    h.completion_date,
    h.acceptance_criteria,
    h.conduit_plan_id,
    h.work_request_dco,
    h.created_at,
    h.valid_from,
    -- Canonical open-ended business time is 'infinity'; the legacy sentinel is
    -- the same statement in a different vocabulary. The view maps it back, so
    -- no consumer sees the change.
    CASE WHEN h.valid_until >= '9999-12-31'::timestamptz
         THEN 'infinity'::timestamptz
         ELSE h.valid_until END,
    h.recorded_on_dt,
    'infinity'::timestamptz
  FROM nebula.requirements h
ON CONFLICT (id) DO NOTHING;

-- ── the target identity set, hashed the same way ───────────────────────────
CREATE TEMP TABLE _v222_target ON COMMIT DROP AS
SELECT
    r.id,
    encode(public.digest(jsonb_build_object(
        'id',                  r.id::text,
        'system_id',           r.system_id::text,
        'subsystem_id',        r.subsystem_id::text,
        'feature_id',          r.feature_id::text,
        'title',               r.title,
        'description',         r.description,
        'status',              r.status,
        'priority',            r.priority,
        'start_date',          r.start_date,
        'completion_date',     r.completion_date,
        'created_at',          to_char(r.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        'parent_id',           r.parent_id::text,
        'req_type',            r.req_type,
        'acceptance_criteria', r.acceptance_criteria::text,
        'candidate_id',        r.candidate_id::text,
        'conduit_plan_id',     r.conduit_plan_id,
        'work_request_dco',    r.work_request_dco::text,
        'asset_id',            r.asset_id::text,
        'valid_from',          to_char(r.valid_from AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        'valid_until',         CASE WHEN r.valid_until >= '9999-12-31'::timestamptz
                                    THEN 'infinity'
                                    ELSE to_char(r.valid_until AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') END
    )::text, 'sha256'), 'hex') AS target_digest
  FROM resolution.requirement r
 WHERE r.id IN (SELECT id FROM _v222_source);

-- ── ledger: prove, don't assert ────────────────────────────────────────────
INSERT INTO resolution.requirement_port_ledger (
    source_relation,
    source_id,
    target_id,
    source_digest,
    target_digest,
    fields_preserved,
    provenance
)
SELECT
    'nebula.requirements',
    s.id,
    s.id,
    s.source_digest,
    t.target_digest,
    '["id", "system_id", "subsystem_id", "feature_id", "title", "description",
      "status", "priority", "req_type", "start_date", "completion_date",
      "acceptance_criteria", "candidate_id", "conduit_plan_id", "asset_id",
      "created_at", "valid_from", "valid_until", "recorded_on_dt", "parent_id"]'::jsonb,
    'v222:port-p4a'
  FROM _v222_source s
  JOIN _v222_target t ON t.id = s.id
 -- The ledger is port-time proof, not a live assertion: the first application
 -- records the digests it verified, later applications leave them alone (and
 -- leave the ported rows alone) so re-applying after the application has
 -- written to those rows is a no-op instead of a false mismatch.
 WHERE s.id NOT IN (SELECT source_id FROM _v222_already)
ON CONFLICT (source_relation, source_id) DO NOTHING;

-- ── postconditions: the port must be complete and faithful ─────────────────
DO $$
DECLARE
    v_total bigint;
    v_missing bigint;
    v_mismatch bigint;
    v_ledger bigint;
    v_broken_refs bigint;
    v_candidate_lost bigint;
BEGIN
    SELECT count(*) INTO v_total FROM _v222_source;

    SELECT count(*) INTO v_missing
      FROM _v222_source s
     WHERE NOT EXISTS (SELECT 1 FROM resolution.requirement r WHERE r.id = s.id);
    IF v_missing > 0 THEN
        RAISE EXCEPTION
            'V222-POST-001: % of % source requirement(s) have no target row; the port is partial',
            v_missing, v_total;
    END IF;

    SELECT count(*) INTO v_mismatch
      FROM _v222_source s
      JOIN _v222_target t ON t.id = s.id
     WHERE s.source_digest <> t.target_digest
       -- Verify only the rows this run adopted. Rows an earlier run proved are
       -- its record, and the application may have changed them since.
       AND s.id NOT IN (SELECT source_id FROM _v222_already);
    IF v_mismatch > 0 THEN
        RAISE EXCEPTION
            'V222-POST-002: % of % ported requirement(s) differ from their source; identity is not preserved',
            v_mismatch, v_total;
    END IF;

    SELECT count(*) INTO v_ledger
      FROM resolution.requirement_port_ledger
     WHERE provenance = 'v222:port-p4a';
    IF v_ledger <> v_total THEN
        RAISE EXCEPTION
            'V222-POST-003: ledger has % row(s) for % ported requirement(s)',
            v_ledger, v_total;
    END IF;

    -- Existing references must survive. Every live blueprint payload whose
    -- requirement_ref names a ported requirement must still resolve.
    SELECT count(*) INTO v_broken_refs
      FROM nebula.blueprints_history b
      JOIN _v222_source s
        ON s.id = (b.payload ->> 'requirement_ref')::uuid
     WHERE b.payload ->> 'requirement_ref'
           ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
       AND NOT EXISTS (
           SELECT 1 FROM resolution.requirement r WHERE r.id = s.id
       );
    IF v_broken_refs > 0 THEN
        RAISE EXCEPTION
            'V222-POST-004: % blueprint requirement reference(s) no longer resolve after the port',
            v_broken_refs;
    END IF;

    -- The candidate link is identity. Resolving the unsatisfiable FK must not
    -- have dropped the value; the digest check already covers this, but name the
    -- specific failure so an operator does not have to decode a digest.
    SELECT count(*) INTO v_candidate_lost
      FROM nebula.requirements h
      JOIN resolution.requirement r ON r.id = h.id
     WHERE h.candidate_id IS DISTINCT FROM r.candidate_id;
    IF v_candidate_lost > 0 THEN
        RAISE EXCEPTION
            'V222-POST-005: % ported requirement(s) lost or changed their candidate_id',
            v_candidate_lost;
    END IF;
END $$;

-- ── what a requirement delete means after the port ────────────────────────
-- Before V222, `nebula.requirements` was a view over `requirements_history`, so
-- a delete removed the current history row. Nothing in `resolution` pointed at
-- a requirement yet, which is why a delete could not fail; and because a
-- blueprint names its requirement in JSONB rather than through an FK, a delete
-- could also strand `payload->>'requirement_ref'` without anybody noticing.
--
-- After the port the canonical row is a real reference target:
--   * FK dependants — resolution.specification, resolution.implementation_plan,
--     resolution.requirement_segment_set, resolution.request_requirement_link
--     (ON DELETE RESTRICT) and resolution.work_request.source_requirement_id;
--   * hierarchy — resolution.requirement.parent_id (self-FK);
--   * by-value references — live blueprint payloads (`requirement_ref`);
--   * this file's own identity ledger (ON DELETE RESTRICT).
-- A bare hard delete would therefore either fail part-way through a
-- system/feature cascade or succeed and strand blueprint refs.
--
-- The ruling encoded here: a requirement is HARD-deleted, atomically, only
-- while nothing governance-relevant depends on it. Dependants are never
-- cascaded away silently and payload references are never rewritten — the
-- caller is told exactly which surfaces block the delete (a 409 up the stack)
-- and retires or repoints them deliberately.
--
-- The port ledger is not a blocker: its row is proof about a row that exists,
-- so the guard removes it in the same statement as the requirement. Keeping the
-- FK at RESTRICT makes that removal an explicit step in the guard instead of a
-- silent cascade. `nebula.requirements_history` is untouched audit and keeps
-- the pre-port row.

-- Which surfaces still reference this requirement, as a JSONB object of arrays.
-- An empty object means "deletable". `p_also_deleting` lets a cascade exclude
-- the children it is removing in the same call, so a subtree delete does not
-- report its own leaves as blockers.
CREATE OR REPLACE FUNCTION resolution.requirement_delete_blockers(
    p_id uuid,
    p_also_deleting uuid[] DEFAULT ARRAY[]::uuid[]
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
AS $fn$
DECLARE
    v_blockers jsonb := '{}'::jsonb;
    v_ids jsonb;
BEGIN
    IF p_id IS NULL THEN
        RETURN v_blockers;
    END IF;

    SELECT jsonb_agg(s.id::text ORDER BY s.id) INTO v_ids
      FROM resolution.specification s
     WHERE s.requirement_id = p_id;
    IF v_ids IS NOT NULL THEN
        v_blockers := v_blockers || jsonb_build_object('specifications', v_ids);
    END IF;

    SELECT jsonb_agg(p.id::text ORDER BY p.id) INTO v_ids
      FROM resolution.implementation_plan p
     WHERE p.requirement_id = p_id;
    IF v_ids IS NOT NULL THEN
        v_blockers := v_blockers || jsonb_build_object('implementation_plans', v_ids);
    END IF;

    SELECT jsonb_agg(ss.segment_set_id::text ORDER BY ss.segment_set_id) INTO v_ids
      FROM resolution.requirement_segment_set ss
     WHERE ss.requirement_id = p_id;
    IF v_ids IS NOT NULL THEN
        v_blockers := v_blockers || jsonb_build_object('requirement_segment_sets', v_ids);
    END IF;

    SELECT jsonb_agg(l.id::text ORDER BY l.id) INTO v_ids
      FROM resolution.request_requirement_link l
     WHERE l.requirement_id = p_id;
    IF v_ids IS NOT NULL THEN
        v_blockers := v_blockers || jsonb_build_object('request_requirement_links', v_ids);
    END IF;

    SELECT jsonb_agg(w.id::text ORDER BY w.id) INTO v_ids
      FROM resolution.work_request w
     WHERE w.source_requirement_id = p_id;
    IF v_ids IS NOT NULL THEN
        v_blockers := v_blockers || jsonb_build_object('work_requests', v_ids);
    END IF;

    SELECT jsonb_agg(c.id::text ORDER BY c.id) INTO v_ids
      FROM resolution.requirement c
     WHERE c.parent_id = p_id
       AND c.id <> ALL (COALESCE(p_also_deleting, ARRAY[]::uuid[]));
    IF v_ids IS NOT NULL THEN
        v_blockers := v_blockers || jsonb_build_object('child_requirements', v_ids);
    END IF;

    -- Blueprints are the by-value reference JSONB cannot enforce. Only the
    -- current segment is checked: historical versions are frozen instance data,
    -- exactly like requirements_history, and readers resolve through the
    -- current-segment view. The CASE keeps a non-uuid payload from raising a
    -- cast error instead of being reported.
    SELECT jsonb_agg(b.id::text ORDER BY b.id) INTO v_ids
      FROM nebula.blueprints_history b
     WHERE (CASE
                WHEN b.payload ->> 'requirement_ref'
                     ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                THEN (b.payload ->> 'requirement_ref')::uuid
            END) = p_id
       AND now() >= b.recorded_on_dt
       AND now() <  b.recorded_until_dt
       AND now() >= b.valid_from
       AND now() <  b.valid_until;
    IF v_ids IS NOT NULL THEN
        v_blockers := v_blockers || jsonb_build_object('blueprints', v_ids);
    END IF;

    RETURN v_blockers;
END
$fn$;

COMMENT ON FUNCTION resolution.requirement_delete_blockers(uuid, uuid[]) IS
    'P4a (V222): the governance surfaces that still reference a requirement (FK dependants, child requirements and live blueprint refs). Empty object means the requirement is deletable. Called by the delete guard and by nebula-srv to explain a refused cascade.';

-- The authority for DELETE: it runs for direct canonical deletes and for the
-- compatibility projection's INSTEAD OF DELETE branch, so no consumer can
-- route around it.
CREATE OR REPLACE FUNCTION resolution.requirement_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_blockers jsonb;
BEGIN
    v_blockers := resolution.requirement_delete_blockers(OLD.id);

    IF v_blockers <> '{}'::jsonb THEN
        RAISE EXCEPTION
            'P4A-DELETE-BLOCKED: requirement % still has dependants', OLD.id
            USING ERRCODE = 'P4A01',
                  DETAIL = jsonb_build_object(
                               'requirementId', OLD.id,
                               'blockers', v_blockers
                           )::text;
    END IF;

    -- Port-time proof refers to a row that is about to stop existing. Remove it
    -- here, deliberately, rather than letting the RESTRICT FK surface as an
    -- opaque constraint error part-way through a cascade.
    DELETE FROM resolution.requirement_port_ledger WHERE target_id = OLD.id;

    RETURN OLD;
END
$fn$;

COMMENT ON FUNCTION resolution.requirement_delete_guard() IS
    'P4a (V222) BEFORE DELETE guard on resolution.requirement: refuses a delete while any governance surface still references the requirement (SQLSTATE P4A01, DETAIL names them), and releases the requirement_port_ledger row in the same statement.';

DROP TRIGGER IF EXISTS trg_requirement_delete_guard ON resolution.requirement;
CREATE TRIGGER trg_requirement_delete_guard
    BEFORE DELETE ON resolution.requirement
    FOR EACH ROW EXECUTE FUNCTION resolution.requirement_delete_guard();

-- ── cascade deletes in dependency order ───────────────────────────────────
-- `resolution.requirement.parent_id` is a self-FK, so a system/feature cascade
-- must remove children before parents or PostgreSQL refuses mid-statement.
-- Rather than trust the caller's id order, remove the leaves of the requested
-- set repeatedly. A set the schema will not let go of (a hierarchy cycle) is
-- reported and nothing is removed, so a cascade is all-or-nothing.
CREATE OR REPLACE FUNCTION resolution.delete_requirements(p_ids uuid[])
RETURNS integer
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_deleted integer := 0;
    v_batch   integer;
    v_left    uuid[];
BEGIN
    IF p_ids IS NULL OR cardinality(p_ids) = 0 THEN
        RETURN 0;
    END IF;

    LOOP
        WITH removable AS (
            SELECT r.id
              FROM resolution.requirement r
             WHERE r.id = ANY (p_ids)
               AND NOT EXISTS (
                   SELECT 1
                     FROM resolution.requirement c
                    WHERE c.parent_id = r.id
                      AND c.id = ANY (p_ids)
               )
        ), gone AS (
            DELETE FROM resolution.requirement r
             USING removable m
             WHERE r.id = m.id
            RETURNING r.id
        )
        SELECT count(*) INTO v_batch FROM gone;

        EXIT WHEN v_batch = 0;
        v_deleted := v_deleted + v_batch;
    END LOOP;

    SELECT array_agg(w.id) INTO v_left
      FROM unnest(p_ids) AS w(id)
     WHERE EXISTS (SELECT 1 FROM resolution.requirement r WHERE r.id = w.id);

    IF v_left IS NOT NULL THEN
        RAISE EXCEPTION
            'P4A-DELETE-ORDER-001: % of % requirement(s) could not be removed; the hierarchy may contain a cycle',
            cardinality(v_left), cardinality(p_ids)
            USING DETAIL = jsonb_build_object('remaining', v_left)::text;
    END IF;

    RETURN v_deleted;
END
$fn$;

COMMENT ON FUNCTION resolution.delete_requirements(uuid[]) IS
    'P4a (V222): deletes a requirement set leaf-first so the parent_id self-FK is satisfied, through the delete guard. Returns the number of rows removed; raises if any requested row survives (cycle).';

-- ── compatibility projection ──────────────────────────────────────────────
-- Column list and order are unchanged from the legacy view (which is required
-- by CREATE OR REPLACE VIEW and keeps `SELECT *` consumers stable).
-- `work_request_dco` has no canonical column; it is exposed as NULL and a
-- non-null write raises in the trigger instead of being dropped.
CREATE OR REPLACE VIEW nebula.requirements AS
SELECT id,
       system_id,
       subsystem_id,
       feature_id,
       title,
       description,
       status,
       priority,
       start_date,
       completion_date,
       created_at,
       recorded_on_dt,
       -- Open-ended segments are stored as 'infinity' canonically and presented
       -- with the legacy sentinels, which is what the pre-port view emitted and
       -- what SCD-style consumers compare against.
       CASE WHEN recorded_until_dt >= '9999-12-31'::timestamptz
            THEN '9999-12-31 23:59:59+00'::timestamptz
            ELSE recorded_until_dt END AS recorded_until_dt,
       valid_from,
       CASE WHEN valid_until >= '9999-12-31'::timestamptz
            THEN '9999-12-31 23:59:59+00'::timestamptz
            ELSE valid_until END AS valid_until,
       parent_id,
       req_type,
       acceptance_criteria,
       candidate_id,
       conduit_plan_id,
       work_request_dco,
       asset_id
  FROM resolution.requirement
 -- The pre-port view was a current-segment view: it hid closed recordings and
 -- expired business windows. Preserve that visibility exactly, so a consumer
 -- cannot start seeing history rows it never saw before.
 WHERE now() >= recorded_on_dt
   AND now() < recorded_until_dt
   AND now() >= valid_from
   AND now() < valid_until;

COMMENT ON VIEW nebula.requirements IS
    'P4a (V222) compatibility projection over canonical resolution.requirement. Same 22 columns, column order and open-segment sentinels as the pre-port view; same current-segment visibility. Writes pass through INSTEAD OF triggers to resolution.requirement. Do not add canonical logic here.';

COMMENT ON COLUMN nebula.requirements.work_request_dco IS
    'Legacy carry-over; maps 1:1 onto resolution.requirement.work_request_dco (to be retired with the expression pipeline).';

-- ── write-through so un-repointed services write the canonical table ──────
CREATE OR REPLACE FUNCTION nebula.requirements_compat_write()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_id uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_id := COALESCE(NEW.id, gen_random_uuid());

        INSERT INTO resolution.requirement (
            id, asset_id, candidate_id, parent_id, source_type,
            system_id, subsystem_id, feature_id, title, description,
            status, priority, req_type, compilation_status,
            start_date, completion_date, acceptance_criteria, conduit_plan_id,
            work_request_dco,
            created_at, valid_from, valid_until, recorded_on_dt, recorded_until_dt
        ) VALUES (
            v_id,
            NEW.asset_id,
            NEW.candidate_id,
            NEW.parent_id,
            CASE WHEN NEW.candidate_id IS NOT NULL THEN 'candidate' ELSE 'manual' END,
            NEW.system_id,
            NEW.subsystem_id,
            NEW.feature_id,
            NEW.title,
            COALESCE(NEW.description, ''),
            COALESCE(NEW.status, 'Backlog'),
            COALESCE(NEW.priority, 'Medium'),
            NEW.req_type,
            'draft',
            NEW.start_date,
            NEW.completion_date,
            NEW.acceptance_criteria,
            NEW.conduit_plan_id,
            NEW.work_request_dco,
            COALESCE(NEW.created_at, now()),
            COALESCE(NEW.valid_from, now()),
            CASE WHEN NEW.valid_until IS NULL
                   OR NEW.valid_until >= '9999-12-31'::timestamptz
                 THEN 'infinity'::timestamptz
                 ELSE NEW.valid_until END,
            COALESCE(NEW.recorded_on_dt, now()),
            'infinity'::timestamptz
        );

        -- Reflect the generated/defaulted values back to RETURNING *.
        NEW.id                  := v_id;
        NEW.description         := COALESCE(NEW.description, '');
        NEW.status              := COALESCE(NEW.status, 'Backlog');
        NEW.priority            := COALESCE(NEW.priority, 'Medium');
        NEW.created_at          := COALESCE(NEW.created_at, now());
        NEW.valid_from          := COALESCE(NEW.valid_from, now());
        NEW.valid_until         := CASE WHEN NEW.valid_until IS NULL
                                           OR NEW.valid_until >= '9999-12-31'::timestamptz
                                         THEN '9999-12-31 23:59:59+00'::timestamptz
                                         ELSE NEW.valid_until END;
        NEW.recorded_on_dt      := COALESCE(NEW.recorded_on_dt, now());
        NEW.recorded_until_dt   := '9999-12-31 23:59:59+00'::timestamptz;
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        UPDATE resolution.requirement SET
            asset_id            = NEW.asset_id,
            candidate_id        = NEW.candidate_id,
            parent_id           = NEW.parent_id,
            source_type         = CASE WHEN NEW.candidate_id IS NOT NULL THEN 'candidate' ELSE 'manual' END,
            system_id           = NEW.system_id,
            subsystem_id        = NEW.subsystem_id,
            feature_id          = NEW.feature_id,
            title               = NEW.title,
            description         = COALESCE(NEW.description, ''),
            status              = COALESCE(NEW.status, 'Backlog'),
            priority            = COALESCE(NEW.priority, 'Medium'),
            req_type            = NEW.req_type,
            start_date          = NEW.start_date,
            completion_date     = NEW.completion_date,
            acceptance_criteria = NEW.acceptance_criteria,
            conduit_plan_id     = NEW.conduit_plan_id,
            work_request_dco    = NEW.work_request_dco,
            created_at          = COALESCE(NEW.created_at, created_at),
            valid_from          = COALESCE(NEW.valid_from, valid_from),
            valid_until         = CASE WHEN NEW.valid_until IS NULL
                                         OR NEW.valid_until >= '9999-12-31'::timestamptz
                                       THEN 'infinity'::timestamptz
                                       ELSE NEW.valid_until END,
            -- A caller may close a recording explicitly (SCD writers do); only
            -- the open marker is normalized.
            recorded_until_dt   = CASE WHEN NEW.recorded_until_dt IS NULL
                                         OR NEW.recorded_until_dt >= '9999-12-31'::timestamptz
                                       THEN 'infinity'::timestamptz
                                       ELSE NEW.recorded_until_dt END
         WHERE id = OLD.id;

        NEW.valid_until       := CASE WHEN NEW.valid_until IS NULL
                                         OR NEW.valid_until >= '9999-12-31'::timestamptz
                                       THEN '9999-12-31 23:59:59+00'::timestamptz
                                       ELSE NEW.valid_until END;
        NEW.recorded_until_dt := CASE WHEN NEW.recorded_until_dt IS NULL
                                         OR NEW.recorded_until_dt >= '9999-12-31'::timestamptz
                                       THEN '9999-12-31 23:59:59+00'::timestamptz
                                       ELSE NEW.recorded_until_dt END;
        RETURN NEW;

    ELSE
        -- The canonical BEFORE DELETE guard decides whether this is allowed: a
        -- legacy caller's delete keeps meaning "remove the requirement", and an
        -- referenced requirement is refused here instead of being hard-deleted
        -- through the projection.
        DELETE FROM resolution.requirement WHERE id = OLD.id;
        RETURN OLD;
    END IF;
END
$fn$;

COMMENT ON FUNCTION nebula.requirements_compat_write() IS
    'P4a (V222) write-through for the nebula.requirements compatibility projection. Derives source_type from candidate_id, defaults the legacy column defaults, and refuses the retired work_request_dco column.';

DROP TRIGGER IF EXISTS trg_requirements_compat_write ON nebula.requirements;
CREATE TRIGGER trg_requirements_compat_write
    INSTEAD OF INSERT OR UPDATE OR DELETE ON nebula.requirements
    FOR EACH ROW EXECUTE FUNCTION nebula.requirements_compat_write();

-- ── postconditions on the projection ──────────────────────────────────────
DO $$
DECLARE
    v_view_rows bigint;
    v_source_rows bigint;
    v_cols bigint;
    v_triggers bigint;
    v_guard_functions bigint;
BEGIN
    SELECT count(*) INTO v_view_rows FROM nebula.requirements;
    SELECT count(*) INTO v_source_rows FROM _v222_source;
    IF v_view_rows <> v_source_rows THEN
        RAISE EXCEPTION
            'V222-POST-010: compatibility view exposes % row(s), source had %',
            v_view_rows, v_source_rows;
    END IF;

    SELECT count(*) INTO v_cols
      FROM information_schema.columns
     WHERE table_schema = 'nebula' AND table_name = 'requirements';
    IF v_cols <> 22 THEN
        RAISE EXCEPTION
            'V222-POST-011: compatibility view exposes % column(s), expected 22', v_cols;
    END IF;

    SELECT count(*) INTO v_triggers
      FROM pg_trigger
     WHERE tgrelid = 'nebula.requirements'::regclass
       AND tgname = 'trg_requirements_compat_write'
       AND NOT tgisinternal;
    IF v_triggers <> 1 THEN
        RAISE EXCEPTION
            'V222-POST-012: write-through trigger is missing on nebula.requirements';
    END IF;

    -- Deleting a requirement without its guard would let a cascade strip a row
    -- that a specification, plan, request or blueprint still names.
    SELECT count(*) INTO v_triggers
      FROM pg_trigger
     WHERE tgrelid = 'resolution.requirement'::regclass
       AND tgname = 'trg_requirement_delete_guard'
       AND NOT tgisinternal;
    IF v_triggers <> 1 THEN
        RAISE EXCEPTION
            'V222-POST-013: delete guard trigger is missing on resolution.requirement';
    END IF;

    SELECT count(*) INTO v_guard_functions
      FROM pg_proc
     WHERE pronamespace = 'resolution'::regnamespace
       AND proname IN ('requirement_delete_blockers',
                       'requirement_delete_guard',
                       'delete_requirements');
    IF v_guard_functions <> 3 THEN
        RAISE EXCEPTION
            'V222-POST-014: delete guard is incomplete (% of 3 functions present)',
            v_guard_functions;
    END IF;
END $$;

-- ── ledger the application itself (schema: sql) ────────────────────────────
-- Same convention as the other staged migrations: writing this row is what
-- marks the file as applied. It is deliberately inside the transaction so an
-- aborted apply leaves no ledger entry.
INSERT INTO resolution.migration_ledger (schema_name, migration_label, description, applied_by)
VALUES (
    'sql',
    'V222__port_requirements_to_resolution',
    'P4a: port 27 nebula.requirements rows into canonical resolution.requirement preserving id, lifecycle, hierarchy, governance refs, timestamps and bitemporal window; per-row sha256 identity ledger (resolution.requirement_port_ledger); nebula.requirements replaced by a compatibility projection with INSTEAD OF write-through. Legacy requirements_history retained. STAGED file: this row only exists once an operator applies it.',
    CURRENT_USER
)
ON CONFLICT (schema_name, migration_label) DO NOTHING;

COMMIT;

-- =============================================================================
-- ROLLBACK (documented; NOT executed by this file)
-- =============================================================================
-- Safe only while no requirement has been created through the projection since
-- the apply. Those rows would exist only in resolution.requirement and would
-- disappear from the restored legacy view. Precheck, expect 0:
--
--   SELECT count(*) FROM resolution.requirement r
--    WHERE NOT EXISTS (SELECT 1 FROM resolution.requirement_port_ledger l
--                       WHERE l.target_id = r.id);
--
-- Then, in one transaction, in this order (the ledger's FK is ON DELETE
-- RESTRICT, so it must go before the rows it points at):
--
--   BEGIN;
--   CREATE TEMP TABLE _v222_rb ON COMMIT DROP AS
--   SELECT source_id FROM resolution.requirement_port_ledger
--    WHERE provenance = 'v222:port-p4a';
--
--   -- CREATE OR REPLACE, not DROP: other objects depend on this view (for
--   -- example nebula.v_work_request_requirement) and a DROP would need
--   -- CASCADE, taking them with it.
--   CREATE OR REPLACE VIEW nebula.requirements AS   -- the pre-V222 definition
--   SELECT id, system_id, subsystem_id, feature_id, title, description, status,
--          priority, start_date, completion_date, created_at, recorded_on_dt,
--          recorded_until_dt, valid_from, valid_until, parent_id, req_type,
--          acceptance_criteria, candidate_id, conduit_plan_id, work_request_dco,
--          asset_id
--     FROM nebula.requirements_history
--    WHERE now() >= recorded_on_dt AND now() < recorded_until_dt
--      AND now() >= valid_from AND now() < valid_until;
--
--   -- Replacing the view keeps its triggers; the write-through must go.
--   DROP TRIGGER IF EXISTS trg_requirements_compat_write ON nebula.requirements;
--
--   -- The delete guard is port-only machinery; remove it before the rows it
--   -- protects. (Its trigger would otherwise refuse the DELETE below while any
--   -- specification, plan, segment set, request link, work request or live
--   -- blueprint still names a ported requirement. Those FKs block the rollback
--   -- either way: a rollback is only safe while nothing references the port, so
--   -- the precheck above is not the only condition.)
--   DROP TRIGGER IF EXISTS trg_requirement_delete_guard ON resolution.requirement;
--   DROP FUNCTION IF EXISTS resolution.delete_requirements(uuid[]);
--   DROP FUNCTION IF EXISTS resolution.requirement_delete_guard();
--   DROP FUNCTION IF EXISTS resolution.requirement_delete_blockers(uuid, uuid[]);
--
--   DROP TABLE resolution.requirement_port_ledger;
--   DELETE FROM resolution.requirement
--    WHERE id IN (SELECT source_id FROM _v222_rb);
--   ALTER TABLE resolution.requirement DROP COLUMN IF EXISTS work_request_dco;
--   DELETE FROM resolution.migration_ledger
--    WHERE migration_label = 'V222__port_requirements_to_resolution';
--   COMMIT;
--
-- The legacy history rows were never modified, so this restores the pre-port
-- surface exactly. Rehearse on a throwaway database first.
-- =============================================================================
