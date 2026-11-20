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
-- even before each service is repointed. nebula-srv itself is repointed to the
-- base table in the same change, so the shim is not load-bearing for it.
--
-- `nebula.requirements_history` is RETAINED untouched as the pre-port audit
-- record. This file performs no DELETE, TRUNCATE, DROP TABLE or column drop.
--
-- Field mapping / representation decisions (no silent coercion):
--   * `source_type` (NOT NULL, no default on the target) is derived:
--       candidate_id IS NOT NULL -> 'candidate', else 'manual'.
--     The target CHECK is `source_type = 'candidate' OR candidate_id IS NULL`,
--     so both derivations are valid and no candidate link is lost.
--   * `compilation_status` has no legacy equivalent -> 'draft'.
--   * `sol_ir_expression_id` has no legacy equivalent -> NULL.
--   * `work_request_dco` (jsonb) has NO target column. It is non-null on 0 rows
--     and read by no code, so it is exposed as NULL in the compatibility view;
--     a non-null write to it raises instead of being silently dropped.
--   * `recorded_until_dt` becomes 'infinity' (the target's current-row marker);
--     the legacy '9999-12-31 23:59:59+00' was that table's marker for "current".
--
-- Idempotent: re-running inserts nothing (ON CONFLICT DO NOTHING), re-verifies
-- the ledger, replaces the view and recreates the triggers.
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
        'asset_id',            h.asset_id::text,
        'valid_from',          to_char(h.valid_from AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        'valid_until',         to_char(h.valid_until AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
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
               'asset_id',            r.asset_id::text,
               'valid_from',          to_char(r.valid_from AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
               'valid_until',         to_char(r.valid_until AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
           )::text, 'sha256'), 'hex') <> s.source_digest;

    IF v_drift > 0 THEN
        RAISE EXCEPTION
            'V222-PREFLIGHT-020: % target row(s) already exist with the same id but different content; reconcile before porting',
            v_drift;
    END IF;
END $$;

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
    h.created_at,
    h.valid_from,
    h.valid_until,
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
        'asset_id',            r.asset_id::text,
        'valid_from',          to_char(r.valid_from AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        'valid_until',         to_char(r.valid_until AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')
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
ON CONFLICT (source_relation, source_id) DO UPDATE
   SET target_digest = EXCLUDED.target_digest,
       verified_at   = now();

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
     WHERE s.source_digest <> t.target_digest;
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
       recorded_until_dt,
       valid_from,
       valid_until,
       parent_id,
       req_type,
       acceptance_criteria,
       candidate_id,
       conduit_plan_id,
       NULL::jsonb AS work_request_dco,
       asset_id
  FROM resolution.requirement;

COMMENT ON VIEW nebula.requirements IS
    'P4a (V222) compatibility projection over canonical resolution.requirement. Retention window for consumers not yet repointed (conduit-mcp, moleculer, adonisjs, planner MCP). Writes pass through INSTEAD OF triggers to resolution.requirement; work_request_dco is retired and always NULL. Do not add canonical logic here.';

COMMENT ON COLUMN nebula.requirements.work_request_dco IS
    'Retired legacy column. Always NULL; has no canonical target column. A non-null write raises V222-COMPAT-001.';

-- ── write-through so un-repointed services write the canonical table ──────
CREATE OR REPLACE FUNCTION nebula.requirements_compat_write()
RETURNS trigger
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_id uuid;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.work_request_dco IS NOT NULL THEN
            RAISE EXCEPTION
                'V222-COMPAT-001: nebula.requirements.work_request_dco is retired; no canonical column exists to receive it';
        END IF;

        v_id := COALESCE(NEW.id, gen_random_uuid());

        INSERT INTO resolution.requirement (
            id, asset_id, candidate_id, parent_id, source_type,
            system_id, subsystem_id, feature_id, title, description,
            status, priority, req_type, compilation_status,
            start_date, completion_date, acceptance_criteria, conduit_plan_id,
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
            COALESCE(NEW.created_at, now()),
            COALESCE(NEW.valid_from, now()),
            COALESCE(NEW.valid_until, 'infinity'::timestamptz),
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
        NEW.valid_until         := COALESCE(NEW.valid_until, 'infinity'::timestamptz);
        NEW.recorded_on_dt      := COALESCE(NEW.recorded_on_dt, now());
        NEW.recorded_until_dt   := 'infinity'::timestamptz;
        NEW.work_request_dco    := NULL;
        RETURN NEW;

    ELSIF TG_OP = 'UPDATE' THEN
        IF NEW.work_request_dco IS NOT NULL THEN
            RAISE EXCEPTION
                'V222-COMPAT-001: nebula.requirements.work_request_dco is retired; no canonical column exists to receive it';
        END IF;

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
            created_at          = COALESCE(NEW.created_at, created_at),
            valid_from          = COALESCE(NEW.valid_from, valid_from),
            valid_until         = COALESCE(NEW.valid_until, valid_until),
            recorded_until_dt   = 'infinity'::timestamptz
         WHERE id = OLD.id;

        NEW.work_request_dco  := NULL;
        NEW.recorded_until_dt := 'infinity'::timestamptz;
        RETURN NEW;

    ELSE
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
-- ROLLBACK (rehearsal only; NOT executed by this file)
-- =============================================================================
-- The port is reversible because nothing is deleted:
--   1. DROP TRIGGER trg_requirements_compat_write ON nebula.requirements;
--   2. CREATE OR REPLACE VIEW nebula.requirements AS <the pre-V222 definition
--      over nebula.requirements_history — see
--      sql/ci-bootstrap/nexus-ci-bootstrap.sql: requirements>;
--   3. DELETE FROM resolution.requirement
--       WHERE id IN (SELECT source_id FROM resolution.requirement_port_ledger
--                     WHERE provenance = 'v222:port-p4a');
--   4. DELETE FROM resolution.requirement_port_ledger WHERE provenance = 'v222:port-p4a';
--   5. DELETE FROM resolution.migration_ledger
--       WHERE migration_label = 'V222__port_requirements_to_resolution';
-- Rehearse on a throwaway database. The legacy history rows were never touched.
-- =============================================================================
