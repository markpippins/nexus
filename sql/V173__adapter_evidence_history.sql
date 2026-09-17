-- =============================================================================
--  V173 — Adapter evidence history + satisfaction staleness (DBA)
--
--  Implements the evidence-refresh design (DBA R1 d849ff31; design comments
--  2f6fc70d on 3fce57ce / 40b6f691 on 78ec5aae) and repairs the two defects
--  the lifecycle drill (R2 ac51713e, comment b771c3ec) found empirically:
--
--  Drill finding 1 (view wart): satisfying_providers is NULL — not '{}' —
--  when zero adapters are active (array_agg(...) FILTER over no rows), so
--  client concatenations NULL-collapse. Fixed with COALESCE in the view.
--
--  Drill finding 2 (evidence shape): merging transition evidence with `||`
--  on a flat object silently overwrites same-named keys across transitions.
--  Evidence is therefore normalized to an ARRAY OF OBSERVATIONS
--  (append + truncate to last ADAPTER_PROBE_HISTORY=10), enforced by a CHECK
--  so the flat-object shape can never silently return.
--
--  Plus the design's view slice: evidence_age + three-valued
--  satisfaction_state — stale evidence is NOT satisfaction, it is a stale
--  claim of satisfaction. The remediation loop queries the three-valued
--  column.
--
--  Additive/repair only; no destructive statements. Safe to apply whenever
--  V172 is applied. Idempotent on re-apply.
-- =============================================================================

BEGIN;

-- ── 1. Normalize evidence to observation arrays ─────────────────────────────
--    Flat object (V172 registration/drill shape) → one-element array; '{}' → '[]'.
UPDATE nebula.adapters
SET evidence = CASE
    WHEN evidence IS NULL OR evidence = '{}'::jsonb THEN '[]'::jsonb
    ELSE jsonb_build_array(evidence)
END
WHERE jsonb_typeof(evidence) IS DISTINCT FROM 'array';

-- ── 2. Enforce the array shape at the storage layer ─────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'chk_adapters_evidence_array'
                     AND conrelid = 'nebula.adapters'::regclass) THEN
        ALTER TABLE nebula.adapters
            ADD CONSTRAINT chk_adapters_evidence_array
            CHECK (jsonb_typeof(evidence) = 'array');
    END IF;
END $$;

COMMENT ON COLUMN nebula.adapters.evidence IS
'Array of observations, newest LAST, truncated to the most recent ADAPTER_PROBE_HISTORY (10). Each element: {kind, observed_at, observer, check?, result?, row_counts?, source?, synthetic?...}. Synthetic transitions (drills) MUST carry synthetic=true — a drill that pretends to be an observation poisons the evidence discipline. Enforced array-shaped by chk_adapters_evidence_array (V173): flat-object merges silently overwrite same-named keys across transitions (drill finding 2) and are structurally rejected from here on.';

-- ── 3. Satisfaction view: COALESCE fix + staleness slice ────────────────────
CREATE OR REPLACE VIEW nebula.v_capability_satisfaction AS
SELECT
    capability_id,
    capability,
    description,
    active_adapters,
    degraded_adapters,
    declared_adapters,
    retired_adapters,
    satisfied,
    satisfying_providers,
    concept_id,
    last_observed_at,
    now() - last_observed_at AS evidence_age,
    -- three-valued state: the remediation loop queries THIS column.
    -- ADAPTER_STALENESS_THRESHOLD: 7 days (roundtable-tunable constant).
    CASE
        WHEN NOT satisfied THEN 'unsatisfied'
        WHEN last_observed_at IS NULL
             OR now() - last_observed_at > interval '7 days'
            THEN 'satisfied-stale'
        ELSE 'satisfied'
    END AS satisfaction_state
FROM (
    SELECT
        c.id              AS capability_id,
        c.name            AS capability,
        c.description,
        count(a.id) FILTER (WHERE a.adapter_status = 'active')    AS active_adapters,
        count(a.id) FILTER (WHERE a.adapter_status = 'degraded')  AS degraded_adapters,
        count(a.id) FILTER (WHERE a.adapter_status = 'declared')  AS declared_adapters,
        count(a.id) FILTER (WHERE a.adapter_status = 'retired')   AS retired_adapters,
        (count(a.id) FILTER (WHERE a.adapter_status = 'active') > 0) AS satisfied,
        -- drill finding 1: COALESCE so zero-active yields '{}' not NULL
        COALESCE(
            array_remove(array_agg(DISTINCT a.provider) FILTER (
                WHERE a.adapter_status = 'active'), NULL), '{}')       AS satisfying_providers,
        -- freshest observation across the capability's live adapters; NULL
        -- (infinitely stale) when no adapter has ever been checked
        max(a.last_checked_at) FILTER (
            WHERE a.adapter_status IN ('active','degraded'))           AS last_observed_at,
        c.concept_id
    FROM nebula.capabilities c
    LEFT JOIN nebula.adapters a
           ON a.capability_id = c.id
          AND a.recorded_until_dt = 'infinity'::timestamptz
          AND a.valid_until = 'infinity'::timestamptz
    WHERE c.recorded_until_dt = 'infinity'::timestamptz
      AND c.valid_until = 'infinity'::timestamptz
    GROUP BY c.id, c.name, c.description, c.concept_id
) agg;

COMMENT ON VIEW nebula.v_capability_satisfaction IS
'Capability satisfaction lattice (V172 + V173): per capability, adapter counts by status, the satisfied verdict (any active adapter over any provider), satisfying_providers (COALESCEd — never NULL, drill finding 1), and the staleness slice: evidence_age plus the three-valued satisfaction_state (satisfied / satisfied-stale / unsatisfied). Stale evidence is NOT satisfaction — it is a stale claim of satisfaction. ADAPTER_STALENESS_THRESHOLD is the 7-day interval constant in the view definition (roundtable-tunable).';

-- ── 4. Post-apply verification gate ─────────────────────────────────────────
DO $$
DECLARE
    v_bad text;
BEGIN
    -- an active adapter with an EMPTY observation history is the new
    -- "active without evidence" — refuse it at apply time
    SELECT string_agg(DISTINCT provider, ',') INTO v_bad
    FROM nebula.adapters
    WHERE adapter_status = 'active'
      AND (evidence = '[]'::jsonb OR jsonb_array_length(evidence) = 0)
    HAVING count(*) > 0;
    IF v_bad IS NOT NULL THEN
        RAISE EXCEPTION 'V173 verify: active adapters with empty evidence history for providers: %', v_bad
            USING ERRCODE = 'P0001';
    END IF;
    RAISE NOTICE '✅ V173 applied — evidence is observation history; satisfaction view carries staleness; providers COALESCEd.';
END $$;

COMMIT;
