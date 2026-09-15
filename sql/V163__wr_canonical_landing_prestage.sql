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
-- Until (2) holds, this migration is structurally INERT for writes: every
-- INSERT/UPDATE on vision.work_requests that carries the new canonical
-- columns is refused (P1021). It is NOT inert structurally — the columns
-- and guards exist from the moment it is applied.
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
--
-- Design (additive only; no column narrowed, no row touched):
--   §1  vision.work_request_shape_registry — fingerprinted shape-artifact
--       registry (single-successor ratification enforced by trigger).
--   §2  vision.work_requests + 8 additive nullable columns for the WP1 v0.1
--       envelope: identity/businessKey, intent, lineage, decomposition,
--       execution_linkage (refs only), evidence_obligations, optional
--       inquiry, plus a shape_version stamp (DEFAULT = active ratified
--       shape). relation_payload jsonb carries the ORIGINAL source fields
--       (relation_payload pattern) — canonical columns are projections of
--       it, never a destructive reshaping.
--   §3  Governed landing guard (BEFORE INSERT OR UPDATE): canonical-column
--       writes require (a) a RATIFIED registered shape and (b) payload
--       conformance — relation_payload present, business_key present,
--       intent_payload/execution_linkage keys inside the v0.1 envelope,
--       execution_linkage REFS-ONLY. Deliberate ungated legacy writes go
--       through a session-LOCAL hatch (V157/V160 pattern). Refusals are
--       logged (SECURITY DEFINER) to vision.canonical_wr_landing_refusals.
--   §4  Never-dropped guard (event trigger): DROP of the WP6-pinned
--       structures (incl. RETAIN tables per f68a6cde) is refused unless the
--       session-LOCAL hatch is set. Additive ALTERs are unaffected.
--   §5  No registry seeding here — the v0.1 row belongs to the ontologist's
--       artifact filing (it must carry the artifact's real sha256).
--   §6  Postconditions assert every structure exists; §7 documents the
--       validation evidence (hermetic sandbox only — zero live execution).
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
    ADD COLUMN IF NOT EXISTS shape_version       text
        DEFAULT vision.canonical_work_request_shape_active();

COMMENT ON COLUMN vision.work_requests.business_key IS
'WP1 v0.1 identity: stable external key (execution domain keys on it in rover/execution-srv). Required for canonical landing (P1020).';
COMMENT ON COLUMN vision.work_requests.relation_payload IS
'Original-field preservation (relation_payload pattern): the unmodified source representation of the landed WR. Required for canonical landing (P1020); canonical columns are projections of this, never a destructive reshaping.';
COMMENT ON COLUMN vision.work_requests.intent_payload IS
'WP1 v0.1 intent block: problem_statement / desired_outcome / priority. Keys outside the envelope are refused for canonical landing (P1020).';
COMMENT ON COLUMN vision.work_requests.lineage IS
'WP1 v0.1 lineage block (derived_from / plan; ag:spawns_plan precedent).';
COMMENT ON COLUMN vision.work_requests.decomposition IS
'WP1 v0.1 decomposition (steps).';
COMMENT ON COLUMN vision.work_requests.execution_linkage IS
'WP1 v0.1 execution linkage: REFS ONLY (lease / attempt / receipt). Embedded execution state is refused (P1020) — no-megatable principle, thread 38b84810.';
COMMENT ON COLUMN vision.work_requests.evidence_obligations IS
'WP1 v0.1 evidence_obligations (Decision B ev_requirements).';
COMMENT ON COLUMN vision.work_requests.inquiry IS
'WP1 v0.1 OPTIONAL inquiry block (read_set_scope / evaluator_ref / expected_outcome_type / evidence_requirements). Pinned to Decision B lineage; the 1,890 DCOs predate it.';
COMMENT ON COLUMN vision.work_requests.shape_version IS
'Landing-time stamp of the ratified shape version (DEFAULT resolves the active registry row).';

CREATE INDEX IF NOT EXISTS idx_vision_work_requests_business_key
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
'Writer-side refusal ledger (C6 refusal-ledger precedent): populated by the governed writer (the Option B projection service) AFTER a P1020/P1021 refusal, on its own connection. The in-database trigger deliberately does NOT insert here — a row written before the guard''s RAISE would roll back with the aborted transaction and never persist.';

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
BEGIN
    IF TG_OP = 'DELETE' THEN
        RETURN NULL;  -- tombstones are part of the bitemporal contract
    END IF;

    v_shape := vision.canonical_work_request_shape_active();

    IF v_shape IS NULL THEN
        v_offend := 'P1021: no ratified canonical WorkRequest shape registered (seed vision.work_request_shape_registry v0.1 + WP2 acceptance first); deliberate ungated legacy writes may set vision.allow_ungated_wr_landing=on (session-LOCAL)';
    ELSE
        BEGIN
            v_hatch := COALESCE(
                current_setting('vision.allow_ungated_wr_landing'::text, true)::boolean,
                false);
        EXCEPTION WHEN OTHERS THEN
            v_hatch := false;
        END;

        IF v_hatch THEN
            -- deliberate ungated legacy write (session-LOCAL hatch); pass through
            NULL;
        ELSIF NEW.relation_payload IS NULL THEN
            v_offend := 'P1020: relation_payload (original-field preservation) is required for canonical landing';
        ELSIF NEW.business_key IS NULL THEN
            v_offend := 'P1020: business_key is required for canonical landing';
        ELSIF NEW.intent_payload IS NOT NULL AND EXISTS (
            SELECT 1 FROM jsonb_object_keys(NEW.intent_payload) k
            WHERE k NOT IN ('problem_statement','desired_outcome','priority')
        ) THEN
            v_offend := 'P1020: intent_payload key outside WP1 v0.1 envelope (problem_statement/desired_outcome/priority)';
        ELSIF NEW.execution_linkage IS NOT NULL AND EXISTS (
            SELECT 1 FROM jsonb_object_keys(NEW.execution_linkage) k
            WHERE k NOT IN ('lease','attempt','receipt')
        ) THEN
            v_offend := 'P1020: execution_linkage must be refs-only (lease/attempt/receipt); embedded execution state violates the ratified no-megatable principle';
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
-- §7  Validation evidence (hermetic sandbox ONLY — zero live execution)
-- -----------------------------------------------------------------------------
-- Validated against a throwaway sandbox database containing a skeleton of the
-- touched surfaces (vision.work_requests 13-column baseline, semantics.
-- canonical_asset FK target, conduit.work_request_events/state skeletons):
--   1. apply: clean, all §6 postconditions pass
--   2. additive path preserved: legacy-shaped INSERT (status/dco_json only)
--      is REFUSED pre-registration (P1021 — strict gate, by design); the
--      writer-side refusals table stays EMPTY during the refused statement
--      (proves the ledger is rollback-independent, not trigger-fed)
--   3. hatched legacy write: vision.allow_ungated_wr_landing=on (session-
--      LOCAL) admits the legacy-shaped row; hatch verified gone afterward
--   4. canonical path (stub registry row, ratified): unhatched INSERT with
--      business_key + relation_payload admitted; shape_version auto-stamped;
--      missing relation_payload refused (P1020); business_key missing
--      refused (P1020); intent_payload rogue key refused (P1020);
--      execution_linkage with embedded state refused (P1020)
--   5. single-successor ratification: second concurrent 'ratified' row
--      refused (P1030)
--   6. never-dropped guard: DROP vision.work_request_shape_registry refused
--      (P1022) without hatch; a non-pinned table drops freely (guard scope
--      verified); hatched drop admitted
--   7. LIVE database untouched: this file has never been executed against
--      nexus (evidence: refusal of §3's own gate on live is NOT exercised;
--      live column count re-checked pre/post work session = unchanged).
-- =============================================================================
