-- =============================================================================
-- V163 (WP6 migration-guidance pre-stage — DBA): governed landing envelope for
-- canonical WorkRequest rows on vision.work_requests.
--
-- ⚠️  DRAFT — NOT APPLIED TO LIVE. ⚠️
-- This file is the pre-staged, reviewed artifact of the WP6 migration
-- guidance. It MUST NOT be applied until:
--   1. WP2 mapping reports ZERO unmapped fields against the WP1 v0.1
--      artifact (or the artifact is bumped to v0.2 via the amendment
--      protocol), AND
--   2. the shape author (ontologist) has INSERTED the v0.1 row into
--      vision.work_request_shape_registry with the artifact's real sha256,
--      and WP2 acceptance has moved ratification_state to 'ratified'.
-- Until (2) holds, this migration is behaviorally permissive for
-- legacy-shaped writes only through the session-LOCAL hatch (see §3):
-- unhatched writes that carry any canonical column are refused (P1021).
-- It is NOT inert structurally — the columns and guards exist from the
-- moment it is applied.
--
-- Provenance:
--   - Architect record 5cb95b2f (WP6 migration guidance: land canonical WR
--     rows in the vision PG schema via the governed projection (Option B),
--     keyed on businessKey, original-field preservation (relation_payload
--     pattern), never-dropped).
--   - DBA confirmation f68a6cde (matrix confirmed live; amendment: vision.
--     work_requests_history is RETAIN (landing-adjacent storage), the dag/
--     edges/losm views read it; landing target is vision.work_requests).
--   - Fingerprint regime: sha256-pinned artifact registry, same precedent as
--     relation-vocabulary-map (pinned Decision B artifact).
--   - Analyst review 51462bfd (PR #251, request-changes): the five blocking
--     findings below are the correction set; §3/§2 implement them.
--   - Architect ruling 258979c4 (WRP Path 2): conduit.work_request_events /
--     conduit.work_request_state are RETAINED as the WR runtime substrate —
--     this ratifies the cross-schema never-drop pins in §4.
--
-- Review corrections applied (analyst 51462bfd):
--   C1. execution_linkage keys aligned to the ratified artifact
--       (lease_ref / attempt_refs / receipt_refs) with refs-only type checks.
--   C2. session hatch evaluated BEFORE any refusal — pre-ratification legacy
--       hatch semantics now match the documentation.
--   C3. NEW.shape_version must equal the active ratified shape on canonical
--       writes (P1023); arbitrary or mismatched versions refused; canonical
--       landing requires an explicit non-null shape_version.
--   C4. business_key partial index is now UNIQUE (stable dedup key); safe
--       because every legacy row carries business_key NULL (NULLs are
--       exempt from unique enforcement) — verified against live (6 rows,
--       all business_key NULL; column does not exist pre-application).
--   C5. full v0.1 conformance per schemas/work-request/canonical-shape.v0.1.json:
--       required fields, JSON types, object/array shapes, and enum domains
--       are enforced (P1020), not just key-name membership.
--
-- Design (additive only; no column narrowed, no row touched):
--   §1  vision.work_request_shape_registry — fingerprinted shape-artifact
--       registry (single-successor ratification enforced by trigger).
--   §2  vision.work_requests + 9 additive nullable columns for the WP1 v0.1
--       envelope: identity/businessKey, intent, lineage, decomposition,
--       execution_linkage (refs only), evidence_obligations, optional
--       inquiry, plus a shape_version stamp. relation_payload jsonb carries
--       the ORIGINAL source fields (relation_payload pattern) — canonical
--       columns are projections of it, never a destructive reshaping.
--   §3  Governed landing guard (BEFORE INSERT OR UPDATE): hatch first; then
--       canonical-column writes require (a) a RATIFIED registered shape
--       matching NEW.shape_version and (b) payload conformance — full v0.1
--       required fields/types per the canonical-shape artifact, relation_
--       payload present, business_key present, execution_linkage REFS-ONLY.
--       Deliberate ungated legacy writes go through a session-LOCAL hatch
--       (V157/V160 pattern). Refusals are logged (SECURITY DEFINER) by the
--       writer to vision.canonical_wr_landing_refusals post-exception.
--   §4  Never-dropped guard (event trigger): DROP of the WP6-pinned
--       structures (incl. RETAIN tables per f68a6cde and the Path-2-retained
--       conduit runtime tables per 258979c4) is refused unless the
--       session-LOCAL hatch is set. Additive ALTERs are unaffected.
--   §5  No registry seeding here — the v0.1 row belongs to the ontologist's
--       artifact filing (it must carry the artifact's real sha256).
--   §6  Postconditions assert every structure exists; §7 documents the
--       validation evidence (hermetic sandbox + executable regression suite
--       python/nexus_core/wrp/tests/test_conformance_wr_landing_prestage.py).
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- §1  Shape-artifact registry (fingerprint-pinned, bitemporal)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vision.work_request_shape_registry (
    shape_version      text PRIMARY KEY,
    artifact_path      text NOT NULL,
    artifact_sha256    text NOT NULL CHECK (artifact_sha256 ~ '^[0-9a-f]{64}$'),
    ratification_state text NOT NULL DEFAULT 'proposed'
                       CHECK (ratification_state IN ('proposed','ratified','superseded')),
    ratified_at        timestamptz,
    ratified_by        text,
    notes              jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    recorded_on_dt     timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt  timestamptz NOT NULL DEFAULT 'infinity'::timestamptz
);

COMMENT ON TABLE vision.work_request_shape_registry IS
'WP1 canonical WorkRequest shape artifact registry. v0.1 row (schemas/work-request/canonical-shape.v0.1.json, sha256-pinned) is INSERTED by the shape author when the artifact lands; ratification_state flips to ''ratified'' only via WP2 zero-unmapped acceptance. Single-successor ratification enforced by trigger.';

-- Active ratified shape resolver (created before §2: the shape_version column
-- DEFAULT calls it at ALTER time).
CREATE OR REPLACE FUNCTION vision.canonical_work_request_shape_active()
RETURNS text LANGUAGE sql STABLE AS $$
    SELECT shape_version
    FROM vision.work_request_shape_registry
    WHERE ratification_state = 'ratified'
      AND recorded_until_dt = 'infinity'::timestamptz
    LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION vision.trg_work_request_shape_registry_single_ratified()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.ratification_state = 'ratified' THEN
        IF EXISTS (
            SELECT 1 FROM vision.work_request_shape_registry r
            WHERE r.shape_version <> NEW.shape_version
              AND r.ratification_state = 'ratified'
              AND r.recorded_until_dt = 'infinity'::timestamptz
        ) THEN
            RAISE EXCEPTION 'P1030: another shape_version is already ratified (single-successor ratification; supersede the incumbent first)'
                USING ERRCODE = 'P0001';
        END IF;
        IF NEW.ratified_at IS NULL THEN
            NEW.ratified_at := now();
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_wr_shape_registry_single_ratified ON vision.work_request_shape_registry;
CREATE TRIGGER trg_wr_shape_registry_single_ratified
BEFORE INSERT OR UPDATE ON vision.work_request_shape_registry
FOR EACH ROW EXECUTE FUNCTION vision.trg_work_request_shape_registry_single_ratified();

-- -----------------------------------------------------------------------------
-- §2  Additive envelope columns on vision.work_requests (WP1 v0.1)
--     identity/businessKey, intent, lineage, decomposition,
--     execution_linkage (refs-only), evidence_obligations, inquiry?
-- -----------------------------------------------------------------------------
ALTER TABLE vision.work_requests
    ADD COLUMN IF NOT EXISTS business_key        text,
    ADD COLUMN IF NOT EXISTS relation_payload    jsonb,
    ADD COLUMN IF NOT EXISTS intent_payload      jsonb,
    ADD COLUMN IF NOT EXISTS lineage             jsonb,
    ADD COLUMN IF NOT EXISTS decomposition       jsonb,
    ADD COLUMN IF NOT EXISTS execution_linkage   jsonb,
    ADD COLUMN IF NOT EXISTS evidence_obligations jsonb,
    ADD COLUMN IF NOT EXISTS inquiry             jsonb,
    ADD COLUMN IF NOT EXISTS shape_version       text;

COMMENT ON COLUMN vision.work_requests.business_key IS
'WP1 v0.1 identity: stable external key (execution domain keys on it in rover/execution-srv). Required for canonical landing (P1020). Uniqueness enforced by a partial UNIQUE index (C4) — NULL (legacy rows) exempt.';
COMMENT ON COLUMN vision.work_requests.relation_payload IS
'Original-field preservation (relation_payload pattern): the unmodified source representation of the landed WR. Required for canonical landing (P1020); canonical columns are projections of this, never a destructive reshaping.';
COMMENT ON COLUMN vision.work_requests.intent_payload IS
'WP1 v0.1 intent block: problem_statement (string, required) / desired_outcome (string, required) / priority (enum low|medium|high, optional). Keys outside the envelope are refused for canonical landing (P1020).';
COMMENT ON COLUMN vision.work_requests.lineage IS
'WP1 v0.1 lineage block: derived_from (array, optional), plan (string, optional); ag:spawns_plan precedent.';
COMMENT ON COLUMN vision.work_requests.decomposition IS
'WP1 v0.1 decomposition: steps (array, optional).';
COMMENT ON COLUMN vision.work_requests.execution_linkage IS
'WP1 v0.1 execution linkage, REFS ONLY and key-aligned to the artifact (C1): lease_ref (string) / attempt_refs (array) / receipt_refs (array). Embedded execution state is refused (P1020) — no-megatable principle, thread 38b84810.';
COMMENT ON COLUMN vision.work_requests.evidence_obligations IS
'WP1 v0.1 evidence_obligations: array (Decision B ev_requirements).';
COMMENT ON COLUMN vision.work_requests.inquiry IS
'WP1 v0.1 OPTIONAL inquiry block (read_set_scope object / evaluator_ref string / expected_outcome_type string / evidence_requirements array) — exactly these four fields when present, per-field types enforced (P1020); the 1,890 DCOs predate it.';
COMMENT ON COLUMN vision.work_requests.shape_version IS
'Landing-time stamp of the ratified shape version. Canonical landing must carry the ACTIVE ratified version explicitly (C3 — mismatch P1023); legacy (hatched) writes may leave it NULL.';

-- C4: stable deduplication key — UNIQUE partial index. Safe to create on the
-- live table: all legacy rows carry business_key NULL (unique exempts NULLs),
-- verified pre-application (6 rows, zero non-null business keys; the column
-- itself does not exist until this migration adds it).
DROP INDEX IF EXISTS idx_vision_work_requests_business_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_vision_work_requests_business_key
    ON vision.work_requests (business_key)
    WHERE business_key IS NOT NULL;

-- -----------------------------------------------------------------------------
-- §3  Governed landing guard
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vision.canonical_wr_landing_refusals (
    refusal_id    bigserial PRIMARY KEY,
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    table_name    text NOT NULL,
    operation     text NOT NULL,
    refusal_code  text NOT NULL,
    shape_version text,
    business_key  text,
    detail        text NOT NULL
);

COMMENT ON TABLE vision.canonical_wr_landing_refusals IS
'Writer-side refusal ledger (C6 refusal-ledger precedent): populated by the governed writer (the Option B projection service) AFTER a P1020/P1021/P1023 refusal, on its own connection. The in-database trigger deliberately does NOT insert here — a row written before the guard''s RAISE would roll back with the aborted transaction and never persist.';

CREATE OR REPLACE FUNCTION vision.trg_canonical_wr_landing_guard()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, vision
AS $$
DECLARE
    v_shape  text;
    v_hatch  boolean;
    v_offend text;
    v_k      text;
    v_val    jsonb;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RETURN NULL;  -- tombstones are part of the bitemporal contract
    END IF;

    -- C2: the hatch is evaluated FIRST. A session that deliberately opts out
    -- of the governed gate (vision.allow_ungated_wr_landing = on, session-
    -- LOCAL) passes unimpeded — including pre-ratification, when no shape is
    -- registered. This matches the documented legacy-hatch semantics.
    BEGIN
        v_hatch := COALESCE(
            current_setting('vision.allow_ungated_wr_landing'::text, true)::boolean,
            false);
    EXCEPTION WHEN OTHERS THEN
        v_hatch := false;
    END;
    IF v_hatch THEN
        RETURN NEW;
    END IF;

    -- Canonical detection: does the write carry any canonical envelope column?
    IF NEW.relation_payload IS NULL
       AND NEW.business_key IS NULL
       AND NEW.intent_payload IS NULL
       AND NEW.lineage IS NULL
       AND NEW.decomposition IS NULL
       AND NEW.execution_linkage IS NULL
       AND NEW.evidence_obligations IS NULL
       AND NEW.inquiry IS NULL
       AND NEW.shape_version IS NULL THEN
        -- Legacy-shaped write. Pre-ratification (no active shape) the gate is
        -- closed for canonical landing but legacy writes are admitted as
        -- today (strict-gate policy: refusing legacy INSERTs pre-registration
        -- would have stranded the 6 live rows' writers — see §7 evidence 2
        -- and the regression suite's legacy-admission case).
        v_shape := vision.canonical_work_request_shape_active();
        IF v_shape IS NULL THEN
            RETURN NEW;  -- no shape ratified: legacy writes pass untouched
        END IF;
        RETURN NEW;
    END IF;

    -- Canonical write: a ratified shape must exist and NEW.shape_version must
    -- name it explicitly (C3). The stamp is enforced, not auto-filled: an
    -- un-stamped canonical write is a shape-blind write.
    v_shape := vision.canonical_work_request_shape_active();
    IF v_shape IS NULL THEN
        v_offend := 'P1021: no ratified canonical WorkRequest shape registered (seed vision.work_request_shape_registry v0.1 + WP2 acceptance first); deliberate ungated legacy writes may set vision.allow_ungated_wr_landing=on (session-LOCAL)';
    ELSIF NEW.shape_version IS NULL THEN
        v_offend := 'P1023: canonical landing requires an explicit shape_version matching the active ratified shape (got NULL)';
    ELSIF NEW.shape_version <> v_shape THEN
        v_offend := 'P1023: shape_version ' || NEW.shape_version || ' does not match the active ratified shape ' || v_shape;
    -- C5: full v0.1 conformance (schemas/work-request/canonical-shape.v0.1.json)
    ELSIF NEW.relation_payload IS NULL THEN
        v_offend := 'P1020: relation_payload (original-field preservation) is required for canonical landing';
    ELSIF jsonb_typeof(NEW.relation_payload) <> 'object' THEN
        v_offend := 'P1020: relation_payload must be a JSON object';
    ELSIF NEW.business_key IS NULL OR NEW.business_key = '' THEN
        v_offend := 'P1020: business_key (non-empty string) is required for canonical landing';
    ELSIF NEW.intent_payload IS NULL THEN
        v_offend := 'P1020: intent_payload is required for canonical landing (WP1 v0.1 intent block)';
    ELSIF jsonb_typeof(NEW.intent_payload) <> 'object' THEN
        v_offend := 'P1020: intent_payload must be a JSON object';
    ELSIF NOT (NEW.intent_payload ? 'problem_statement')
          OR jsonb_typeof(NEW.intent_payload->'problem_statement') IS DISTINCT FROM 'string'
          OR (NEW.intent_payload->>'problem_statement') = '' THEN
        v_offend := 'P1020: intent_payload.problem_statement (non-empty string) is required';
    ELSIF NOT (NEW.intent_payload ? 'desired_outcome')
          OR jsonb_typeof(NEW.intent_payload->'desired_outcome') IS DISTINCT FROM 'string'
          OR (NEW.intent_payload->>'desired_outcome') = '' THEN
        v_offend := 'P1020: intent_payload.desired_outcome (non-empty string) is required';
    ELSIF NEW.intent_payload ? 'priority'
          AND (jsonb_typeof(NEW.intent_payload->'priority') IS DISTINCT FROM 'string'
               OR NEW.intent_payload->>'priority' NOT IN ('low','medium','high')) THEN
        v_offend := 'P1020: intent_payload.priority must be one of low|medium|high';
    ELSIF EXISTS (
        SELECT 1 FROM jsonb_object_keys(NEW.intent_payload) k
        WHERE k NOT IN ('problem_statement','desired_outcome','priority')
    ) THEN
        v_offend := 'P1020: intent_payload key outside WP1 v0.1 envelope (problem_statement/desired_outcome/priority)';
    -- lineage: optional, but when present must be an object with only
    -- derived_from (array) and/or plan (string)
    ELSIF NEW.lineage IS NOT NULL THEN
        IF jsonb_typeof(NEW.lineage) <> 'object' THEN
            v_offend := 'P1020: lineage must be a JSON object';
        ELSIF EXISTS (
            SELECT 1 FROM jsonb_object_keys(NEW.lineage) k
            WHERE k NOT IN ('derived_from','plan')
        ) THEN
            v_offend := 'P1020: lineage key outside WP1 v0.1 envelope (derived_from/plan)';
        ELSIF NEW.lineage ? 'derived_from'
              AND jsonb_typeof(NEW.lineage->'derived_from') IS DISTINCT FROM 'array' THEN
            v_offend := 'P1020: lineage.derived_from must be an array';
        ELSIF NEW.lineage ? 'plan'
              AND jsonb_typeof(NEW.lineage->'plan') IS DISTINCT FROM 'string' THEN
            v_offend := 'P1020: lineage.plan must be a string';
        END IF;
    END IF;

    IF v_offend IS NULL AND NEW.decomposition IS NOT NULL THEN
        IF jsonb_typeof(NEW.decomposition) <> 'object'
           OR (NEW.decomposition ? 'steps'
               AND jsonb_typeof(NEW.decomposition->'steps') IS DISTINCT FROM 'array') THEN
            v_offend := 'P1020: decomposition must be an object whose steps (when present) is an array';
        END IF;
    END IF;

    -- C1 + C5: execution_linkage — key-aligned to the ratified artifact
    -- (lease_ref / attempt_refs / receipt_refs) and strictly refs-only.
    IF v_offend IS NULL AND NEW.execution_linkage IS NOT NULL THEN
        IF jsonb_typeof(NEW.execution_linkage) <> 'object' THEN
            v_offend := 'P1020: execution_linkage must be a JSON object';
        ELSE
            FOR v_k IN SELECT jsonb_object_keys(NEW.execution_linkage) LOOP
                IF v_k NOT IN ('lease_ref','attempt_refs','receipt_refs') THEN
                    v_offend := 'P1020: execution_linkage must be refs-only with artifact keys (lease_ref/attempt_refs/receipt_refs); got key ''' || v_k || '''';
                    EXIT;
                END IF;
                v_val := NEW.execution_linkage -> v_k;
                IF v_k = 'lease_ref' THEN
                    IF jsonb_typeof(v_val) IS DISTINCT FROM 'string' THEN
                        v_offend := 'P1020: execution_linkage.lease_ref must be a string ref';
                        EXIT;
                    END IF;
                ELSE
                    IF jsonb_typeof(v_val) IS DISTINCT FROM 'array' THEN
                        v_offend := 'P1020: execution_linkage.' || v_k || ' must be an array of refs';
                        EXIT;
                    END IF;
                    IF EXISTS (
                        SELECT 1 FROM jsonb_array_elements(v_val) e
                        WHERE jsonb_typeof(e) IS DISTINCT FROM 'string'
                    ) THEN
                        v_offend := 'P1020: execution_linkage.' || v_k || ' must contain only string refs (embedded execution state violates the ratified no-megatable principle)';
                        EXIT;
                    END IF;
                END IF;
            END LOOP;
        END IF;
    END IF;

    IF v_offend IS NULL AND NEW.evidence_obligations IS NOT NULL THEN
        IF jsonb_typeof(NEW.evidence_obligations) IS DISTINCT FROM 'array' THEN
            v_offend := 'P1020: evidence_obligations must be an array (Decision B ev_requirements)';
        END IF;
    END IF;

    -- inquiry: optional, but exactly the four pinned fields when present,
    -- each with the artifact's type (no fifth field — WorkRequest-gravity guard).
    IF v_offend IS NULL AND NEW.inquiry IS NOT NULL THEN
        IF jsonb_typeof(NEW.inquiry) <> 'object' THEN
            v_offend := 'P1020: inquiry must be a JSON object';
        ELSE
            FOR v_k IN SELECT jsonb_object_keys(NEW.inquiry) LOOP
                IF v_k NOT IN ('read_set_scope','evaluator_ref','expected_outcome_type','evidence_requirements') THEN
                    v_offend := 'P1020: inquiry must contain exactly read_set_scope/evaluator_ref/expected_outcome_type/evidence_requirements (no fifth field — WorkRequest-gravity guard); got key ''' || v_k || '''';
                    EXIT;
                END IF;
                v_val := NEW.inquiry -> v_k;
                IF v_k = 'read_set_scope' THEN
                    IF jsonb_typeof(v_val) IS DISTINCT FROM 'object' THEN
                        v_offend := 'P1020: inquiry.read_set_scope must be an object';
                        EXIT;
                    END IF;
                ELSIF v_k = 'evidence_requirements' THEN
                    IF jsonb_typeof(v_val) IS DISTINCT FROM 'array' THEN
                        v_offend := 'P1020: inquiry.evidence_requirements must be an array';
                        EXIT;
                    END IF;
                ELSIF jsonb_typeof(v_val) IS DISTINCT FROM 'string' THEN
                    v_offend := 'P1020: inquiry.' || v_k || ' must be a string';
                    EXIT;
                END IF;
            END LOOP;
        END IF;
    END IF;

    IF v_offend IS NOT NULL THEN
        -- No in-trigger logging: a row written here would roll back with the
        -- aborted statement/transaction. The writer records refusals to
        -- vision.canonical_wr_landing_refusals post-exception (C6 precedent).
        RAISE EXCEPTION '%', v_offend USING ERRCODE = 'P0001';
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_canonical_wr_landing_guard ON vision.work_requests;
CREATE TRIGGER trg_canonical_wr_landing_guard
BEFORE INSERT OR UPDATE ON vision.work_requests
FOR EACH ROW EXECUTE FUNCTION vision.trg_canonical_wr_landing_guard();

-- -----------------------------------------------------------------------------
-- §4  Never-dropped guard (WP6-pinned structures)
--     shelve = documented + frozen, NEVER dropped (architect 5cb95b2f §4);
--     RETAIN tables protected equally (f68a6cde amendment 1: the dag/edges/
--     losm views read vision.work_requests_history — the landing-adjacent
--     RETAIN storage; losing it orphans the views and the landing plan).
--     conduit pins ratified by Architect Path 2 ruling 258979c4 (auth C:
--     retain work_request_events/state as the WR runtime substrate).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION vision.refuse_wr_struct_drop()
RETURNS event_trigger LANGUAGE plpgsql AS $$
DECLARE
    v_obj   record;
    v_hatch boolean;
BEGIN
    -- NB: event triggers have no TG_OP (that is a row-trigger variable); this
    -- function fires only on the sql_drop event, so no event-type check is
    -- needed (or valid) here.

    BEGIN
        v_hatch := COALESCE(
            current_setting('vision.allow_wr_object_drop'::text, true)::boolean,
            false);
    EXCEPTION WHEN OTHERS THEN
        v_hatch := false;
    END;

    IF v_hatch THEN
        RETURN;
    END IF;

    FOR v_obj IN
        SELECT * FROM pg_event_trigger_dropped_objects()
        WHERE object_type IN ('table','view')
          AND (
               (schema_name = 'vision' AND object_identity IN (
                    'vision.work_requests',
                    'vision.work_requests_history',
                    'vision.work_request_shape_registry',
                    'vision.canonical_wr_landing_refusals',
                    'vision.work_request_edges_history'))
            OR (schema_name = 'conduit' AND object_identity IN (
                    'conduit.work_request_events',
                    'conduit.work_request_state'))
          )
    LOOP
        RAISE EXCEPTION 'P1022: % is under the never-dropped clause (WP6 disposition retain/shelve; deliberate removal may set vision.allow_wr_object_drop=on, session-LOCAL)',
            v_obj.object_identity
            USING ERRCODE = 'P0001';
    END LOOP;
END;
$$;

DROP EVENT TRIGGER IF EXISTS trg_never_drop_wr_structs;
CREATE EVENT TRIGGER trg_never_drop_wr_structs ON sql_drop
EXECUTE FUNCTION vision.refuse_wr_struct_drop();

-- -----------------------------------------------------------------------------
-- §6  Postconditions (fail loud at migration time, not silently at write time)
-- -----------------------------------------------------------------------------
DO $$
DECLARE
    n int;
BEGIN
    SELECT count(*) INTO n FROM information_schema.columns
    WHERE table_schema='vision' AND table_name='work_requests'
      AND column_name IN ('business_key','relation_payload','intent_payload',
                          'lineage','decomposition','execution_linkage',
                          'evidence_obligations','inquiry','shape_version');
    IF n <> 9 THEN
        RAISE EXCEPTION 'V163 postcondition failed: expected 9 new envelope columns, found %', n;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                   WHERE table_schema='vision' AND table_name='work_request_shape_registry') THEN
        RAISE EXCEPTION 'V163 postcondition failed: shape registry missing';
    END IF;

    -- C4: the UNIQUE partial index must exist (dedup key).
    IF NOT EXISTS (SELECT 1 FROM pg_indexes
                   WHERE schemaname = 'vision'
                     AND tablename = 'work_requests'
                     AND indexname = 'uq_vision_work_requests_business_key') THEN
        RAISE EXCEPTION 'V163 postcondition failed: unique business_key index missing';
    END IF;

    SELECT count(*) INTO n FROM pg_proc p
    JOIN pg_namespace ns ON ns.oid = p.pronamespace
    WHERE ns.nspname = 'vision'
      AND p.proname IN ('canonical_work_request_shape_active',
                        'trg_canonical_wr_landing_guard',
                        'trg_work_request_shape_registry_single_ratified',
                        'refuse_wr_struct_drop');
    IF n <> 4 THEN
        RAISE EXCEPTION 'V163 postcondition failed: expected 4 functions, found %', n;
    END IF;

    SELECT count(*) INTO n FROM pg_trigger
    WHERE NOT tgisinternal
      AND tgrelid = 'vision.work_requests'::regclass
      AND tgname = 'trg_canonical_wr_landing_guard';
    IF n <> 1 THEN
        RAISE EXCEPTION 'V163 postcondition failed: landing guard trigger missing';
    END IF;

    IF EXISTS (SELECT 1 FROM vision.work_request_shape_registry) THEN
        RAISE EXCEPTION 'V163 postcondition failed: registry must ship EMPTY (no seeding — the v0.1 row belongs to the artifact filing)';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'vision.work_requests'::regclass
                     AND contype = 'f'
                     AND confrelid = 'semantics.canonical_asset'::regclass) THEN
        RAISE EXCEPTION 'V163 postcondition failed: asset FK (work_requests_asset_id_fkey) missing — landing surface must stay asset-wired';
    END IF;

    RAISE NOTICE 'V163 postconditions: all passed';
END;
$$;

COMMIT;

-- -----------------------------------------------------------------------------
-- §7  Validation evidence
-- -----------------------------------------------------------------------------
-- Validated by the executable regression suite (hermetic per-run throwaway
-- DATABASE on the local PG instance — no live-DB DDL, nothing persists):
--   python/nexus_core/wrp/tests/test_conformance_wr_landing_prestage.py
--   CONDUIT_PG_DSN=postgresql://pguser:pgpass@localhost:5432/postgres \
--     python3 -m pytest python/nexus_core/wrp/tests/test_conformance_wr_landing_prestage.py -v
--
-- Coverage (C-numbers = review corrections, analyst 51462bfd):
--   1. apply: clean, all §6 postconditions pass
--   2. legacy admission pre-ratification: legacy-shaped INSERT (status/
--      dco_json only) passes WITHOUT the hatch (C2 correction of the old
--      strict-gate behavior — legacy writers are not stranded pre-
--      ratification); hatch also admits legacy writes (session-LOCAL, gone
--      afterward)
--   3. canonical write pre-ratification refused P1021; hatch-first verified:
--      the SAME canonical write WITH the hatch pre-ratification is admitted
--      (C2 — hatch evaluated before any refusal)
--   4. C1: execution_linkage accepts lease_ref/attempt_refs/receipt_refs
--      (artifact keys); old trigger keys lease/attempt/receipt now REFUSED;
--      embedded objects in attempt_refs refused (refs-only)
--   5. C3: shape_version mismatch refused P1023; NULL shape_version on a
--      canonical write refused P1023; matching active version admitted;
--      arbitrary future version refused
--   6. C4: duplicate business_key second INSERT fails unique violation;
--      two NULL business_key legacy rows coexist (NULLs exempt)
--   7. C5: relation_payload missing/non-object refused; business_key
--      missing/empty refused; intent_payload missing/non-object/missing
--      problem_statement/empty desired_outcome refused; priority outside
--      low|medium|high refused; rogue intent key refused; lineage rogue key
--      refused; decomposition steps non-array refused; evidence_obligations
--      non-array refused; inquiry fifth field refused; inquiry field type
--      mismatch refused; fully conforming v0.1 payload ADMITTED
--   8. single-successor ratification: second concurrent 'ratified' row
--      refused (P1030)
--   9. never-dropped guard: DROP vision.work_request_shape_registry refused
--      (P1022) without hatch; hatched drop admitted
--  10. LIVE database untouched: never executed against nexus.
-- =============================================================================