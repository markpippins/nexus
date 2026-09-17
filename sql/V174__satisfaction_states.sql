-- =============================================================================
--  V174 — Satisfaction-states extension (DBA)
--
--  Implements the analyst's refinement on the execution-profiles thread
--  (78ec5aae, 04:49Z): the satisfaction verdict must preserve
--  stale / unknown / refused / unreachable rather than collapsing every
--  non-satisfied case into 'unsatisfied'. Schema face of the same epistemic
--  discipline the auditor grant codified (79feb142):
--  UNREACHABLE DATA MUST NOT BE ATTESTED AS ABSENT.
--
--  Vocabulary (superset of V173 — purely additive; every V173 state keeps
--  its meaning, every consumer filtering `!= 'satisfied'` keeps working):
--
--    satisfied        active adapter, fresh observation (V173 semantics)
--    satisfied-stale  active adapter, evidence aged past the window — a
--                     stale CLAIM of satisfaction (V173 semantics kept)
--    unsatisfied      positive non-provision evidence (FAIL at any age),
--                     or the decay default when a fresh diagnostic ages out
--    unreachable      fresh UNREACHABLE observation: the measurement path
--                     failed — the claim survives UNVERIFIED, not refuted
--    refused          fresh REFUSED observation: provider reachable, the
--                     capability explicitly declined
--    unknown          no usable observation: never observed, or the check
--                     path itself is absent (SKIP) — we did not look, which
--                     is NOT the same as knowing the capability is absent.
--                     The V173 cell read 'unsatisfied' here: a false comfort.
--
--  Derivation: the FRESHEST observation across the capability's live
--  adapters decides the not-satisfied branch. Diagnostic states
--  (unreachable/refused) are fresh-only and decay to 'unsatisfied' — the
--  remediation loop's default for "no fresh positive evidence" — while
--  SKIP decays to 'unknown' at any age, because a skipped check was never
--  a measurement of the endpoint at all.
--
--  Freshness window = the same 7-day constant V173 pinned (roundtable-
--  tunable). Probe-side vocabulary + transition law live in
--  bin/adapter-health-probe.py (V174 slice): UNREACHABLE = connect-level
--  failure, REFUSED = reachable-but-declines, both degrade active adapter
--  health but never promote and never satisfy.
--
--  Additive view rebuild only; no destructive statements. Idempotent.
-- =============================================================================

BEGIN;

-- ── 1. Rebuild the satisfaction view with the extended verdict ──────────────
--    Carries forward every V173 element (COALESCEd satisfying_providers,
--    evidence_age, last_observed_at) and adds the freshest-observation
--    argmax used by the not-satisfied branch. The argmax pairs (result,
--    observed_at) are aggregated over the SAME rows with the SAME sort key,
--    so element [1] of each array is one coherent observation.
CREATE OR REPLACE VIEW nebula.v_capability_satisfaction AS
SELECT
    -- CREATE OR REPLACE VIEW is positional: V173's thirteen columns keep
    -- their exact positions and names (agg.* would interleave the new
    -- inner column mid-list); the new argmax pair is APPENDED, last
    agg.capability_id,
    agg.capability,
    agg.description,
    agg.active_adapters,
    agg.degraded_adapters,
    agg.declared_adapters,
    agg.retired_adapters,
    agg.satisfied,
    agg.satisfying_providers,
    agg.concept_id,
    agg.last_observed_at,
    now() - agg.last_observed_at AS evidence_age,
    -- the remediation loop queries THIS column.
    CASE
        WHEN agg.satisfied
             AND agg.last_observed_at IS NOT NULL
             AND now() - agg.last_observed_at <= interval '7 days'
            THEN 'satisfied'
        WHEN agg.satisfied THEN 'satisfied-stale'
        ELSE
            CASE (agg.freshest ->> 1)
                WHEN 'UNREACHABLE'
                    THEN CASE WHEN (agg.freshest ->> 0)::timestamptz
                                   >= now() - interval '7 days'
                              THEN 'unreachable' ELSE 'unsatisfied' END
                WHEN 'REFUSED'
                    THEN CASE WHEN (agg.freshest ->> 0)::timestamptz
                                   >= now() - interval '7 days'
                              THEN 'refused' ELSE 'unsatisfied' END
                WHEN 'FAIL' THEN 'unsatisfied'   -- positive non-provision, any age
                WHEN 'PASS' THEN 'unsatisfied'   -- evidence says provided, registry disagrees: operator state outranks
                WHEN 'SKIP' THEN 'unknown'       -- never a measurement, at any age
                -- no RESULT = no observation ever happened (the timestamp
                -- fallback exists for staleness math, not as evidence):
                -- we did not look, which is not knowing it's absent
                ELSE CASE WHEN agg.freshest ->> 1 IS NULL
                          THEN 'unknown' ELSE 'unsatisfied' END
            END
    END AS satisfaction_state,
    agg.freshest
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
        -- V173 positional contract: concept_id precedes last_observed_at
        c.concept_id,
        -- V173 staleness driver, upgraded in V174: the age of the last
        -- SUCCESSFUL observation (last PASS entry's embedded observed_at),
        -- not the last run — a trailing SKIP or FAIL is not evidence FOR
        -- satisfaction, and last_checked_at advances on every probe run
        -- regardless of outcome (the exact false-comfort pattern the
        -- analyst's refinement targets). Legacy fallback: V173-era wrapped
        -- evidence has result=PASS but no observed_at → inherit
        -- last_checked_at. Adapters with NO PASS never contribute (NULL),
        -- so an active row with zero PASS evidence reads satisfied-stale.
        max(
            COALESCE(pa.pass_observed_at,
                     CASE WHEN pa.pass_count > 0
                          THEN a.last_checked_at END)
        ) FILTER (WHERE a.adapter_status IN ('active','degraded'))   AS last_observed_at,
        -- V174: freshest observation across ALL live statuses (declared rows
        -- participate), as ONE coherent (observed_at, result) argmax pair —
        -- a single aggregate over a jsonb pair, so the timestamp and its
        -- result can never misalign across two independent sorts
        (array_agg(
             jsonb_build_array(
                 COALESCE((a.evidence -> -1 ->> 'observed_at')::timestamptz,
                          a.last_checked_at),
                 (a.evidence -> -1 ->> 'result'))
             ORDER BY COALESCE((a.evidence -> -1 ->> 'observed_at')::timestamptz,
                               a.last_checked_at) DESC NULLS LAST
         ))[1]                                                          AS freshest
    FROM nebula.capabilities c
    LEFT JOIN nebula.adapters a
           ON a.capability_id = c.id
          AND a.recorded_until_dt = 'infinity'::timestamptz
          AND a.valid_until = 'infinity'::timestamptz
    LEFT JOIN LATERAL (
        SELECT
            max((e->>'observed_at')::timestamptz)
                FILTER (WHERE e->>'result' = 'PASS'
                         AND e->>'observed_at' IS NOT NULL)      AS pass_observed_at,
            count(*) FILTER (WHERE e->>'result' = 'PASS')        AS pass_count
        FROM jsonb_array_elements(COALESCE(a.evidence, '[]'::jsonb)) e
    ) pa ON true
    WHERE c.recorded_until_dt = 'infinity'::timestamptz
      AND c.valid_until = 'infinity'::timestamptz
    GROUP BY c.id, c.name, c.description, c.concept_id
) agg;

COMMENT ON VIEW nebula.v_capability_satisfaction IS
'Capability satisfaction lattice (V172 + V173 + V174): adapter counts by status, the satisfied verdict, satisfying_providers (never NULL), evidence_age, and the six-valued satisfaction_state — satisfied / satisfied-stale / unsatisfied (V173) extended with unreachable (measurement failure: the claim survives UNVERIFIED, not refuted), refused (provider reachable, capability declined), and unknown (never observed or check path absent — the V173 cell read unsatisfied here, a false comfort). Derivation: freshest observation across all live adapters; diagnostics (unreachable/refused) are fresh-only and decay to unsatisfied; SKIP is unknown at any age. Freshness window = 7-day constant in the view definition (ADAPTER_STALENESS_THRESHOLD on the probe side; roundtable-tunable). UNREACHABLE DATA MUST NOT BE ATTESTED AS ABSENT.';

-- ── 2. Post-apply verification gate ─────────────────────────────────────────
DO $$
DECLARE
    v_bad bigint;
BEGIN
    -- the view must compile and every verdict must sit inside the ratified
    -- vocabulary (proves the column exists, is non-null, value set closed)
    SELECT count(*) INTO v_bad
    FROM nebula.v_capability_satisfaction
    WHERE satisfaction_state IS NULL
       OR satisfaction_state NOT IN ('satisfied', 'satisfied-stale',
                                     'unsatisfied', 'unreachable',
                                     'refused', 'unknown');
    IF v_bad > 0 THEN
        RAISE EXCEPTION 'V174 verify: % satisfaction rows outside the ratified vocabulary', v_bad
            USING ERRCODE = 'P0001';
    END IF;
    RAISE NOTICE '✅ V174 applied — satisfaction_state carries stale/unknown/refused/unreachable; unreachable is not absence.';
END $$;

COMMIT;
