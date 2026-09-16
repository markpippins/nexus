-- =============================================================================
--  V171 — Blueprints: implementation plans renamed and collapsed (DBA)
--
--  Operator ruling 4a71a56d: a blueprint is an ASSET (asset_kind='blueprint'),
--  riding the asset-envelope pattern (discussions 34231b55 / 89f99cbe).
--  Design: agent record 6b45abde (blast-radius scout) + ea227d56 (staging R1).
--
--  What this migration does
--  ------------------------
--  1. Creates nebula.blueprints_history — the canonical bitemporal surface.
--     Column set mirrors nebula.implementation_plans_history, with the flat
--     spec_id/requirement_id columns folded into the payload JSONB as
--     spec_ref/requirement_ref (live fold stats: 0 spec_id, 4 requirement_id).
--     plan_number stays UNIQUE — 431 Conduit receipts key on it; the numbers
--     are preserved verbatim so receipt history stays coherent.
--  2. Renames asset_kind implementation_plan -> blueprint for the 24 live
--     rows' asset envelopes (created by V081). The 319 harvest-artifact
--     implementation_plan assets (plan .md file captures from the retired
--     filesystem pipeline) are deliberately LEFT AS HISTORY — they describe
--     artifacts, not this table's rows.
--  3. Backfills the 24 rows (plan numbers verbatim, payload folded).
--  4. Installs a mirror trigger: new INSERTs into the legacy
--     nebula.implementation_plans_history are mirrored into blueprints, so
--     Conduit-era writers keep landing data while the service routes flip in
--     a later PR. Legacy table becomes write-through, never authoritative.
--  5. Read view nebula.v_blueprints (bitemporal-current).
--  6. Compat: nebula.implementation_plans view re-created over the NEW
--     blueprint table with the exact legacy column contract, so nebula-srv
--     routes, nebula-ui, plurality-ui, surface-ui and conduit readers keep
--     working unchanged.
--  7. NEBULA_AUDIT statement triggers per the V156/V167/V169 house pattern.
--
--  Inert by construction: nothing on live changes until this migration is
--  applied on explicit operator go. Idempotent guards throughout.
-- =============================================================================

BEGIN;

-- ── 0. Sanity gates ──────────────────────────────────────────────────────────
DO $$
DECLARE
    v_missing text;
BEGIN
    IF to_regclass('nebula.implementation_plans_history') IS NULL THEN
        RAISE EXCEPTION 'V171: nebula.implementation_plans_history missing — refusing to bootstrap from nothing'
            USING ERRCODE = 'P0001';
    END IF;
    IF to_regclass('semantics.canonical_asset') IS NULL THEN
        RAISE EXCEPTION 'V171: semantics.canonical_asset missing — asset regime prerequisite (V065+) not applied'
            USING ERRCODE = 'P0001';
    END IF;
    -- Duplicate plan_numbers would violate the unique constraint on the new
    -- table. None exist on live (verified 2026-09-16); refuse loudly if a
    -- forked database drifts.
    SELECT string_agg(DISTINCT plan_number, ',') INTO v_missing
    FROM nebula.implementation_plans_history
    WHERE plan_number IS NOT NULL
    GROUP BY plan_number HAVING count(*) > 1 LIMIT 1;
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'V171: duplicate plan_number(s) in implementation_plans_history: %', v_missing
            USING ERRCODE = 'P0001';
    END IF;
END $$;

-- ── 1. The canonical blueprint surface ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS nebula.blueprints_history (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_number         text UNIQUE,              -- receipt key, preserved verbatim
    title               text NOT NULL,
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- payload contract (refs folded from legacy flat columns):
    --   goal, content, files_affected[], acceptance_criteria[],
    --   dependencies[], spec_ref, requirement_ref, tags[], project
    blueprint_status    text NOT NULL DEFAULT 'draft'
                        CHECK (blueprint_status IN
                          ('draft','pending','approved','work_requested',
                           'completed','archived')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    -- house bitemporal pair (V081 sentinel style)
    valid_from          timestamptz NOT NULL DEFAULT now(),
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    asset_id            uuid REFERENCES semantics.canonical_asset(id)
);

CREATE INDEX IF NOT EXISTS idx_blueprints_history_updated_at_id
    ON nebula.blueprints_history (updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_blueprints_history_status
    ON nebula.blueprints_history (blueprint_status)
    WHERE recorded_until_dt = 'infinity'::timestamptz;

COMMENT ON TABLE nebula.blueprints_history IS
'Canonical blueprint surface (V171): implementation plans renamed per operator ruling 4a71a56d — blueprint is an asset_kind on semantics.canonical_asset. plan_number preserved verbatim: 431 Conduit receipts key on it. Legacy flat columns folded into payload JSONB (spec_ref/requirement_ref refs-only per the class-3 pattern).';

COMMENT ON COLUMN nebula.blueprints_history.payload IS
'Blueprint instance data: goal, content, files_affected[], acceptance_criteria[], dependencies[], tags[], spec_ref, requirement_ref, project. Refs-only governance references — instance data is portable (Asset-envelopes doctrine: instance data projects, authority does not).';

COMMENT ON COLUMN nebula.blueprints_history.plan_number IS
'Legacy receipt key, UNIQUE, preserved verbatim from implementation_plans_history. New blueprint rows without a receipt past may have NULL plan_number.';

-- ── 2. Asset kind rename (identity continuity: SAME asset row) ──────────────
--    Renaming the kind (not creating new assets) keeps asset_relations,
--    receipts-by-number, and the asset_id FK chain continuous.
UPDATE semantics.canonical_asset ca
SET    asset_kind = 'blueprint'
FROM   nebula.implementation_plans_history iph
WHERE  iph.asset_id = ca.id
  AND  ca.asset_kind = 'implementation_plan'
  AND  iph.recorded_until_dt >= '9999-12-31'::timestamptz  -- current rows only (any house sentinel precision)
  AND  ca.expired_at IS NULL;

-- ── 3. Backfill: 24 rows, plan numbers verbatim, payload folded ─────────────
INSERT INTO nebula.blueprints_history
    (id, plan_number, title, payload, blueprint_status,
     created_at, updated_at, valid_from, valid_until,
     recorded_on_dt, recorded_until_dt, asset_id)
SELECT
    iph.id,
    iph.plan_number,
    iph.title,
    jsonb_build_object(
        'goal',               iph.goal,
        'content',            iph.content,
        'files_affected',     to_jsonb(iph.files_affected),
        'acceptance_criteria', iph.acceptance_criteria,
        'dependencies',       to_jsonb(iph.dependencies),
        'tags',               to_jsonb(iph.tags),
        'spec_ref',           iph.spec_id,
        'requirement_ref',    iph.requirement_id,
        'project',            iph.metadata->>'project'
    ),
    iph.status,
    iph.created_at, iph.updated_at,
    iph.valid_from, iph.valid_until,
    iph.recorded_on_dt, iph.recorded_until_dt,
    iph.asset_id
FROM nebula.implementation_plans_history iph
WHERE NOT EXISTS (SELECT 1 FROM nebula.blueprints_history b WHERE b.id = iph.id);

-- ── 4. Mirror trigger: legacy plan writers write through to blueprints ──────
CREATE OR REPLACE FUNCTION nebula.trg_plans_mirror_to_blueprints()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO nebula.blueprints_history
        (id, plan_number, title, payload, blueprint_status,
         created_at, updated_at, valid_from, valid_until,
         recorded_on_dt, recorded_until_dt, asset_id)
    VALUES
        (NEW.id,
         NEW.plan_number,
         NEW.title,
         jsonb_build_object(
             'goal',               NEW.goal,
             'content',            NEW.content,
             'files_affected',     to_jsonb(NEW.files_affected),
             'acceptance_criteria', NEW.acceptance_criteria,
             'dependencies',       to_jsonb(NEW.dependencies),
             'tags',               to_jsonb(NEW.tags),
             'spec_ref',           NEW.spec_id,
             'requirement_ref',    NEW.requirement_id,
             'project',            NEW.metadata->>'project'
         ),
         NEW.status,
         NEW.created_at, NEW.updated_at,
         NEW.valid_from, NEW.valid_until,
         NEW.recorded_on_dt, NEW.recorded_until_dt,
         NEW.asset_id)
    ON CONFLICT (id) DO NOTHING;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_plans_mirror_to_blueprints
    ON nebula.implementation_plans_history;
CREATE TRIGGER trg_plans_mirror_to_blueprints
AFTER INSERT ON nebula.implementation_plans_history
FOR EACH ROW EXECUTE FUNCTION nebula.trg_plans_mirror_to_blueprints();

COMMENT ON FUNCTION nebula.trg_plans_mirror_to_blueprints IS
'V171 write-through shim: legacy implementation_plans_history INSERTs are mirrored into nebula.blueprints_history. Legacy table is transitional (write-through, never authoritative); nebula-srv routes flip to blueprints in a follow-up PR, after which the mirror trigger retires.';

-- ── 5. Read view: bitemporal-current blueprints ─────────────────────────────
CREATE OR REPLACE VIEW nebula.v_blueprints AS
SELECT
    id, plan_number, title, payload, blueprint_status,
    created_at, updated_at, valid_from, valid_until,
    recorded_on_dt, recorded_until_dt, asset_id,
    payload->>'project' AS project
FROM nebula.blueprints_history
WHERE now() >= recorded_on_dt
  AND now() <  recorded_until_dt
  AND now() >= valid_from
  AND now() <  valid_until;

COMMENT ON VIEW nebula.v_blueprints IS
'Blueprint read scope: bitemporal-current rows only. The JSONB payload is the envelope instance data; project surfaced as a convenience column (previously a fabricated literal in the legacy plans view).';

-- ── 6. Compat view: legacy contract over the new surface ────────────────────
--    Exact column contract of the old implementation_plans view so
--    nebula-srv /api/plans, nebula-ui, plurality-ui, surface-ui and any
--    conduit reader keep working unchanged.
DROP VIEW IF EXISTS nebula.implementation_plans CASCADE;
CREATE VIEW nebula.implementation_plans AS
SELECT
    b.id,
    b.plan_number,
    (b.payload->>'spec_ref')::uuid      AS spec_id,
    (b.payload->>'requirement_ref')::uuid AS requirement_id,
    b.title,
    b.payload->>'goal'                  AS goal,
    b.payload->>'content'               AS content,
    ARRAY(SELECT jsonb_array_elements_text(
            COALESCE(b.payload->'files_affected','[]'::jsonb)))
                                        AS files_affected,
    COALESCE(b.payload->'acceptance_criteria','[]'::jsonb)
                                        AS acceptance_criteria,
    ARRAY(SELECT jsonb_array_elements_text(
            COALESCE(b.payload->'dependencies','[]'::jsonb)))
                                        AS dependencies,
    b.blueprint_status                  AS status,
    ARRAY(SELECT jsonb_array_elements_text(
            COALESCE(b.payload->'tags','[]'::jsonb))) AS tags,
    jsonb_build_object('project', b.payload->>'project') AS metadata,
    b.created_at,
    b.updated_at,
    b.valid_from,
    b.valid_until,
    b.recorded_on_dt,
    b.recorded_until_dt,
    b.asset_id
FROM nebula.blueprints_history b
WHERE now() >= b.recorded_on_dt
  AND now() <  b.recorded_until_dt
  AND now() >= b.valid_from
  AND now() <  b.valid_until;

COMMENT ON VIEW nebula.implementation_plans IS
'V171 compat view: legacy implementation-plans column contract served from nebula.blueprints_history. Read-side only — writers should target nebula.blueprints_history (or rely on the mirror trigger) during the transition window.';

-- ── 7. Attributable trail: NEBULA_AUDIT on the new surface ──────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'tackle' AND p.proname = 'fn_nebula_audit_log') THEN
        CREATE FUNCTION tackle.fn_nebula_audit_log(
          p_table text, p_op text, p_rows bigint, p_keys text) RETURNS void
        LANGUAGE sql AS $fn$
          INSERT INTO tackle.system_logs
            (id, timestamp, level, category, message, source, details)
          VALUES (
            gen_random_uuid()::text, now(), 'INFO', 'NEBULA_AUDIT',
            format('%s on %s (%s rows)%s', p_op, p_table, p_rows,
                   COALESCE(' [' || left(p_keys, 900) || ']', '')),
            'nebula-audit-trigger',
            jsonb_build_object('table', p_table, 'op', p_op, 'row_count', p_rows,
                               'keys', p_keys)
          );
        $fn$;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION nebula.trg_blueprints_audit()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_keys text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        PERFORM tackle.fn_nebula_audit_log(
            'nebula.blueprints_history', 'DELETE', 1,
            COALESCE(OLD.plan_number, OLD.id::text));
        RETURN OLD;
    END IF;
    v_keys := COALESCE(NEW.plan_number, NEW.id::text);
    PERFORM tackle.fn_nebula_audit_log(
        'nebula.blueprints_history', TG_OP, 1, v_keys);
    RETURN CASE WHEN TG_OP = 'INSERT' THEN NEW ELSE NEW END;
END;
$$;

DROP TRIGGER IF EXISTS trg_blueprints_audit ON nebula.blueprints_history;
CREATE TRIGGER trg_blueprints_audit
AFTER INSERT OR UPDATE OR DELETE ON nebula.blueprints_history
FOR EACH ROW EXECUTE FUNCTION nebula.trg_blueprints_audit();

-- ── 8. Post-apply verification (hard gates, V081 style) ─────────────────────
DO $$
DECLARE
    v_legacy   int;
    v_blue     int;
    v_kind_bad int;
    v_payload_bad int;
BEGIN
    SELECT count(*) INTO v_legacy FROM nebula.implementation_plans_history;
    SELECT count(*) INTO v_blue   FROM nebula.blueprints_history;

    IF v_blue < v_legacy THEN
        RAISE EXCEPTION 'V171 verify: blueprints (%) < legacy plans (%) — backfill incomplete',
            v_blue, v_legacy USING ERRCODE = 'P0001';
    END IF;

    SELECT count(*) INTO v_kind_bad
    FROM nebula.blueprints_history b
    JOIN semantics.canonical_asset ca ON ca.id = b.asset_id
    WHERE ca.asset_kind NOT IN ('blueprint','implementation_plan');
    IF v_kind_bad > 0 THEN
        RAISE EXCEPTION 'V171 verify: % blueprint rows carry a non-plan asset kind', v_kind_bad
            USING ERRCODE = 'P0001';
    END IF;

    SELECT count(*) INTO v_payload_bad
    FROM nebula.blueprints_history
    WHERE payload->>'goal' IS NULL AND title IS NOT NULL;
    IF v_payload_bad > 0 THEN
        RAISE EXCEPTION 'V171 verify: % blueprint rows lost goal in payload fold', v_payload_bad
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE '✅ V171 applied — % legacy plan rows mirrored into % blueprints; asset kinds renamed where linked; compat view live.',
        v_legacy, v_blue;
END $$;

COMMIT;
