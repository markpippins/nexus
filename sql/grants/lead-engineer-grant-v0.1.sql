-- =============================================================================
--  lead-engineer-grant-v0.1.sql — Wave-3 grant event (DBA)
--
--  Sixth use of the corrected close-then-insert grant template
--  (sql/grants/auditor-grant-v0.1.sql lineage; uses #299/#301/#306/#307).
--  DRAFT — NOT APPLIED TO LIVE; apply waits on explicit operator go AFTER
--  architect review of this PR (Wave-3 ruling, discussions thread
--  0d2c2fb8, comment of 2026-09-17T19:29:54Z: "I will review the PR
--  itself before it merges").
--
--  ── THE RATIFIED SHAPE ─────────────────────────────────────────────────────
--    owns_domains              {implementation_supervision}
--    can_greenlight            TRUE   — the point of the grant; FIRST TRUE
--                                     can_greenlight outside planner
--    can_verify_work_requests  FALSE  — the separation IS the point: the
--                                     greenlight authority CITES gate-passed
--                                     attestations; it is not itself an
--                                     attestation authority (ratified
--                                     event-identity contract, G1–G4)
--    requires_approval_from    {architect}
--    escalates_to              {architect}
--
--  ── THE EVIDENCE BAR (ratified in the same ruling) ─────────────────────────
--  The attestation-chain gate contract — G1 self-attestation refused,
--  G2 evidence-free refused, G3 capability-gated, G4 citation identity —
--  and its event-identity semantics ("an attestation is an event, not a
--  reconstructible value") are the evidence bar that makes this the
--  highest-bar grant in the batch. A lead-engineer greenlight is lawful
--  only when it cites an attestation that passed those gates. Once V179
--  (PR #309) is applied, the citation is a queryable
--  nebula.attestations row (ATP0004); until then the contract is
--  procedural and this grant's triggers route bypass pressure to the
--  architect.
--
--  Repeatability: GRANT-EVENT executor, not a one-shot migration — every
--  application closes the current open snapshot and opens the granted
--  successor (handoff-exact), leaving an auditable chain.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── GRANT VALUES (the ratified Wave-3 shape) ─────────────────────────
    v_role                  text    := 'lead-engineer';
    v_owns_domains          text[]  := ARRAY['implementation_supervision'];
    v_can_greenlight        boolean := TRUE;    -- the point of the grant
    v_can_create_questions  boolean := false;   -- agenda/questions stay with planner (batch invariant)
    v_can_create_agendas    boolean := false;
    v_can_resolve_questions boolean := false;
    v_can_verify_wrs        boolean := FALSE;   -- the separation: cites attestations, never attests
    v_max_open_questions    integer := 0;
    v_requires_approval     text[]  := ARRAY['architect'];
    v_escalates_to          text[]  := ARRAY['architect'];
    v_escalation_triggers   text[]  := ARRAY['greenlight_without_verification_attestation',
                                             'attestation_chain_bypass_pressure',
                                             'unattested_merge_pressure'];
    v_level_primary         text    := '<=3';   -- supervises L1-L3 implementation evidence
    v_level_allowed         text    := '<=4';
    v_visibility            text[]  := ARRAY['all'];
    -- ─────────────────────────────────────────────────────────────────────
    v_now     timestamptz := now();
    v_closed  nebula.roles_history%ROWTYPE;
    v_updated int;
BEGIN

    -- ── SELF-IDEMPOTENCE PRE-FLIGHT (V180 rediff gate) ──────────────────
    -- Same role + same granted spec already open => this grant event is
    -- already live: refuse BEFORE any close/insert, so a replayed apply is
    -- a loud no-op (GRANT-APPLIED) instead of a redundant grant event.
    -- Different spec => a NEW lawful grant event; proceeds.
    IF nebula.grant_is_applied(v_role, jsonb_build_object(
           'owns_domains',             to_jsonb(v_owns_domains),
           'can_greenlight',           to_jsonb(v_can_greenlight),
           'can_create_questions',     to_jsonb(v_can_create_questions),
           'can_create_agendas',       to_jsonb(v_can_create_agendas),
           'can_resolve_questions',    to_jsonb(v_can_resolve_questions),
           'can_verify_work_requests', to_jsonb(v_can_verify_wrs),
           'max_open_questions',       to_jsonb(v_max_open_questions),
           'requires_approval_from',   to_jsonb(v_requires_approval),
           'escalates_to',             to_jsonb(v_escalates_to),
           'escalation_triggers',      to_jsonb(v_escalation_triggers),
           'visibility_scope',         to_jsonb(v_visibility))) THEN
        RAISE EXCEPTION 'GRANT-APPLIED: role % already holds this exact granted spec — self-idempotent no-op, refusing to mint a redundant grant event (see nebula.applied_grants)', v_role
            USING ERRCODE = 'P0001';
    END IF;
    -- ── 1. CLOSE the current open snapshot (on the history table) ───────
    UPDATE nebula.roles_history
       SET valid_until       = v_now,
           recorded_until_dt = v_now,
           updated_at        = v_now
     WHERE name = v_role
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
    GET DIAGNOSTICS v_updated = ROW_COUNT;

    IF v_updated <> 1 THEN
        IF v_updated = 0 THEN
            RAISE EXCEPTION 'GRANT: no OPEN snapshot found for role % — either never granted, already closed (re-run?), or the database still carries the pre-V175 full unique shape; run V175 first and verify state before granting', v_role
                USING ERRCODE = 'P0001';
        ELSE
            RAISE EXCEPTION 'GRANT: % open snapshots for role % — exactly-one-open violated; refusing (V175 partial index should make this unreachable)', v_updated, v_role
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    -- ── 2. Derive the new snapshot from the just-closed row ─────────────
    SELECT * INTO v_closed FROM nebula.roles_history
     WHERE name = v_role
       AND valid_until = v_now
       AND recorded_until_dt = v_now;

    -- ── 3. INSERT the open successor (new uuid, handoff-exact times) ────
    INSERT INTO nebula.roles_history (
        id, name, display_name, description,
        owns_domains, can_greenlight, can_create_questions,
        can_create_agendas, can_resolve_questions, can_verify_work_requests,
        max_open_questions, requires_approval_from,
        cron_enabled, cron_expression, cron_description,
        escalates_to, escalation_triggers,
        level_filter_primary, level_filter_allowed, visibility_scope,
        created_at, updated_at,
        valid_from, valid_until, recorded_on_dt, recorded_until_dt
    ) VALUES (
        gen_random_uuid(),                 -- history snapshots do NOT share ids (V175 finding)
        v_closed.name, v_closed.display_name, v_closed.description,
        v_owns_domains, v_can_greenlight, v_can_create_questions,
        v_can_create_agendas, v_can_resolve_questions, v_can_verify_wrs,
        v_max_open_questions, v_requires_approval,
        v_closed.cron_enabled, v_closed.cron_expression, v_closed.cron_description,
        v_escalates_to, v_escalation_triggers,
        v_level_primary, v_level_allowed, v_visibility,
        v_closed.created_at, v_now,
        v_now, '9999-12-31 00:00:00+00'::timestamptz, v_now, '9999-12-31 00:00:00+00'::timestamptz
    );

    RAISE NOTICE 'GRANT: role % closed at % and re-opened as the greenlight authority (verify/greenlight separation preserved)', v_role, v_now;
END $$;

-- ── 4. Verification gate: the ratified shape, including the separation ─────
DO $$
DECLARE
    v_open       int;
    v_closed     int;
    v_gap        int;
    v_domains    text;
    v_greenlight boolean;
    v_verify     boolean;
    v_gl_holders text;
BEGIN
    SELECT count(*) INTO v_open FROM nebula.roles_history
     WHERE name = 'lead-engineer' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    SELECT count(*) INTO v_closed FROM nebula.roles_history
     WHERE name = 'lead-engineer' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open <> 1 OR v_closed < 1 THEN
        RAISE EXCEPTION 'GRANT verify: chain malformed (open=%, closed=%)', v_open, v_closed
            USING ERRCODE = 'P0001';
    END IF;

    -- handoff exactness: no gap, no overlap between the newest closed row
    -- and the open successor
    SELECT count(*) INTO v_gap
    FROM nebula.roles_history o
    JOIN nebula.roles_history c ON c.name = o.name AND c.valid_until <> '9999-12-31 00:00:00+00'::timestamptz
    WHERE o.name = 'lead-engineer' AND o.valid_until = '9999-12-31 00:00:00+00'::timestamptz
      AND c.valid_until = (SELECT max(valid_until) FROM nebula.roles_history
                           WHERE name = 'lead-engineer' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz)
      AND o.valid_from IS DISTINCT FROM c.valid_until;
    IF v_gap > 0 THEN
        RAISE EXCEPTION 'GRANT verify: closed/open snapshot handoff is not exact (gap or overlap)'
            USING ERRCODE = 'P0001';
    END IF;

    -- granted values: domains present, greenlight TRUE...
    SELECT array_to_string(owns_domains, ','), can_greenlight, can_verify_work_requests
      INTO v_domains, v_greenlight, v_verify
    FROM nebula.roles_history
    WHERE name = 'lead-engineer' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    IF v_domains IS NULL OR v_domains = '' THEN
        RAISE EXCEPTION 'GRANT verify: open snapshot carries no granted domains'
            USING ERRCODE = 'P0001';
    END IF;
    IF v_greenlight IS NOT TRUE THEN
        RAISE EXCEPTION 'GRANT verify: can_greenlight must be TRUE on the open snapshot (the point of the grant)'
            USING ERRCODE = 'P0001';
    END IF;
    -- ...and the SEPARATION: a greenlight authority must not be an attestation
    -- authority (ratified verify/greenlight separation; ATP0004 semantics)
    IF v_verify IS NOT FALSE THEN
        RAISE EXCEPTION 'GRANT verify: can_verify_work_requests must be FALSE on the open snapshot — the greenlight authority cites gate-passed attestations, it does not attest (ratified separation)'
            USING ERRCODE = 'P0001';
    END IF;

    -- informational: the greenlight holder set after this grant
    SELECT array_to_string(array_agg(name ORDER BY name), ',') INTO v_gl_holders
    FROM nebula.roles
    WHERE can_greenlight;
    RAISE NOTICE '✅ GRANT verified — lead-engineer chain: closed=% open=%, handoff exact, domains={%}, greenlight=TRUE verify=FALSE; greenlight holders now: %',
        v_closed, v_open, v_domains, v_gl_holders;
END $$;

COMMIT;
