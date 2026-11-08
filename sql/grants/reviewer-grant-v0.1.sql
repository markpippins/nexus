-- =============================================================================
--  reviewer-grant-v0.1.sql — reviewer reinstatement grant (DBA)
--
--  Authority: Architect Decision 7 (record fda24e67, 2026-09-28, ratified) —
--  reviewer's 11-key grant spec. Reinstatement authorized by operator
--  instruction 2026-11-08, which reverses the 2026-11-07 retention and
--  supersedes Decision B (44c0eb95) that was holding the role retired.
--
--  THIS IS A RE-OPEN, NOT A STANDARD GRANT EVENT.
--  reviewer has NO open nebula.roles_history snapshot: the last snapshot
--  (5654c8e3, owns_domains {build_verification}) was closed at
--  2026-09-27T16:20:29Z and never re-opened. Every other grant file assumes
--  an open row to close (close-then-insert); run against this role they fail
--  with "no OPEN snapshot found". This file therefore handles both shapes:
--
--    * open snapshot present  -> close it, hand off with valid_from ==
--      closed.valid_until (the normal no-gap grant event), and
--    * no open snapshot       -> derive the successor from the newest CLOSED
--      snapshot and open it at now(), PRESERVING the retirement gap. The
--      role was not valid between 2026-09-27T16:20:29Z and the reinstatement
--      instant, and the chain must say so rather than fabricate continuity.
--
--  Template doctrine carried from sql/grants/auditor-grant-v0.1.sql lineage:
--  write the HISTORY TABLE never the view (nebula.roles is a bitemporal view;
--  view UPDATE/INSERT silently match nothing once the snapshot closes), and
--  the V175 partial open-snapshot unique is required (one open row per name,
--  closed history exempt).
--
--  Separation gates run BEFORE any mutation (Decision 1, hard):
--      can_verify_work_requests MUST be FALSE — reviewer judges, never
--      attests; the tester's attestation is the R8 gate, REVIEW_PASS is not.
--      can_greenlight MUST be TRUE — issuing REVIEW_PASS is a greenlight.
--
--  Note: roles_history carries no granted_by column — provenance lives here,
--  in the NEBULA_AUDIT trail (V177 triggers), and in the linked records.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── GRANT VALUES (Architect Decision 7 / fda24e67, reviewer) ──────────
    v_role                  text    := 'reviewer';
    v_owns_domains          text[]  := ARRAY['review_judgement'];
    v_can_greenlight        boolean := TRUE;
    v_can_create_questions  boolean := false;
    v_can_create_agendas    boolean := false;
    v_can_resolve_questions boolean := false;
    v_can_verify_wrs        boolean := FALSE;  -- Decision 1 separation
    v_max_open_questions    integer := 0;
    v_requires_approval     text[]  := ARRAY['architect'];
    v_escalates_to          text[]  := ARRAY['architect'];
    v_escalation_triggers   text[]  := ARRAY['greenlight_without_verification_attestation',
                                             'attestation_chain_bypass_pressure',
                                             'unattested_merge_pressure'];
    v_level_primary         text    := '<=2';
    v_level_allowed         text    := '<=3';
    v_visibility            text[]  := ARRAY['reviewer','builder','all'];
    -- ─────────────────────────────────────────────────────────────────────
    v_now     timestamptz := now();
    v_closed  nebula.roles_history%ROWTYPE;
    v_updated int;
BEGIN

    -- ── 0. SEPARATION GATES (before any mutation) ───────────────────────
    IF v_can_verify_wrs THEN
        RAISE EXCEPTION 'GRANT verify: can_verify_work_requests must be FALSE — Decision 1 separation: reviewer judges, never attests'
            USING ERRCODE = 'P0001';
    END IF;
    IF NOT v_can_greenlight THEN
        RAISE EXCEPTION 'GRANT verify: can_greenlight must be TRUE — a REVIEW_PASS is a greenlight over implementation work'
            USING ERRCODE = 'P0001';
    END IF;
    IF v_owns_domains IS NULL OR cardinality(v_owns_domains) = 0 THEN
        RAISE EXCEPTION 'GRANT verify: owns_domains must be non-empty — an empty grant is a denial grant (CAPABILITY_INSUFFICIENT)'
            USING ERRCODE = 'P0001';
    END IF;

    -- ── SELF-IDEMPOTENCE PRE-FLIGHT (V180 rediff gate) ──────────────────
    -- Same role + same granted spec already open => already applied: refuse
    -- before any close/insert, so a replay is a loud no-op. Different spec
    -- (or nothing open) => a NEW lawful grant event; proceeds.
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

    -- ── 1. Close the current open snapshot, if there is one ─────────────
    UPDATE nebula.roles_history
       SET valid_until       = v_now,
           recorded_until_dt = v_now,
           updated_at        = v_now
     WHERE name = v_role
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
    GET DIAGNOSTICS v_updated = ROW_COUNT;

    IF v_updated > 1 THEN
        RAISE EXCEPTION 'GRANT: % open snapshots for role % — exactly-one-open violated; refusing (V175 partial index should make this unreachable)', v_updated, v_role
            USING ERRCODE = 'P0001';
    ELSIF v_updated = 1 THEN
        -- Standard grant event: successor hands off with no gap.
        SELECT * INTO v_closed FROM nebula.roles_history
         WHERE name = v_role
           AND valid_until = v_now
           AND recorded_until_dt = v_now;
    ELSE
        -- RE-OPEN path: the role is fully retired. Derive the successor from
        -- the newest closed snapshot; its granted columns are REPLACED by the
        -- values above, its identity/cron/created_at carry forward.
        SELECT * INTO v_closed FROM nebula.roles_history
         WHERE name = v_role
         ORDER BY valid_until DESC, recorded_until_dt DESC
         LIMIT 1;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'GRANT: role % has no roles_history snapshot at all — nothing to reinstate; seed the role before granting', v_role
                USING ERRCODE = 'P0001';
        END IF;
        IF v_closed.valid_until >= v_now THEN
            RAISE EXCEPTION 'GRANT: newest snapshot for role % is not closed (valid_until=%) — refusing an ambiguous re-open', v_role, v_closed.valid_until
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    -- ── 2. INSERT the open successor ────────────────────────────────────
    --    valid_from = v_now in BOTH paths: on the re-open path this leaves the
    --    retirement gap visible (no fabricated continuity); on the standard
    --    path the close above used the same instant, so handoff is exact.
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

    RAISE NOTICE 'GRANT: role % opened with the Decision 7 capability set at % (prior snapshot closed at %)',
        v_role, v_now, v_closed.valid_until;
END $$;

-- ── 3. Verification gate: chain shape + Decision 7 values pinned ───────────
DO $$
DECLARE
    v_open        int;
    v_closed      int;
    v_overlap     int;
    r             nebula.roles_history%ROWTYPE;
    v_last_closed timestamptz;
BEGIN
    SELECT count(*) INTO v_open FROM nebula.roles_history
     WHERE name = 'reviewer' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    SELECT count(*) INTO v_closed FROM nebula.roles_history
     WHERE name = 'reviewer' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open <> 1 OR v_closed < 1 THEN
        RAISE EXCEPTION 'GRANT verify: chain malformed (open=%, closed=%)', v_open, v_closed
            USING ERRCODE = 'P0001';
    END IF;

    SELECT max(valid_until) INTO v_last_closed FROM nebula.roles_history
     WHERE name = 'reviewer' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;

    SELECT * INTO r FROM nebula.roles_history
     WHERE name = 'reviewer' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    -- No overlap: the open episode begins at or after the prior one ended.
    -- (Strict '>' on a re-open is the retirement gap; '=' is the standard
    -- no-gap handoff. '<' would be a fabricated overlap and is refused.)
    IF r.valid_from < v_last_closed THEN
        RAISE EXCEPTION 'GRANT verify: open snapshot valid_from (%) precedes the prior close (%) — overlapping validity episodes', r.valid_from, v_last_closed
            USING ERRCODE = 'P0001';
    END IF;

    -- Decision 7 values, pinned key-for-key.
    IF r.owns_domains IS DISTINCT FROM ARRAY['review_judgement'] THEN
        RAISE EXCEPTION 'GRANT verify: owns_domains must be {review_judgement}, got %', r.owns_domains
            USING ERRCODE = 'P0001';
    END IF;
    IF r.can_greenlight IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION 'GRANT verify: can_greenlight must be TRUE' USING ERRCODE = 'P0001';
    END IF;
    IF r.can_verify_work_requests IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'GRANT verify: can_verify_work_requests must be FALSE (Decision 1 separation)'
            USING ERRCODE = 'P0001';
    END IF;
    IF r.can_create_questions IS DISTINCT FROM FALSE
       OR r.can_create_agendas IS DISTINCT FROM FALSE
       OR r.can_resolve_questions IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'GRANT verify: question/agenda booleans must all be FALSE (judgement role)'
            USING ERRCODE = 'P0001';
    END IF;
    IF r.max_open_questions IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION 'GRANT verify: max_open_questions must be 0, got %', r.max_open_questions
            USING ERRCODE = 'P0001';
    END IF;
    IF r.requires_approval_from IS DISTINCT FROM ARRAY['architect']
       OR r.escalates_to IS DISTINCT FROM ARRAY['architect'] THEN
        RAISE EXCEPTION 'GRANT verify: requires_approval_from/escalates_to must be {architect}'
            USING ERRCODE = 'P0001';
    END IF;
    IF r.escalation_triggers IS DISTINCT FROM ARRAY[
           'greenlight_without_verification_attestation',
           'attestation_chain_bypass_pressure',
           'unattested_merge_pressure'] THEN
        RAISE EXCEPTION 'GRANT verify: escalation_triggers do not match Decision 7, got %', r.escalation_triggers
            USING ERRCODE = 'P0001';
    END IF;
    IF r.level_filter_primary IS DISTINCT FROM '<=2'
       OR r.level_filter_allowed IS DISTINCT FROM '<=3' THEN
        RAISE EXCEPTION 'GRANT verify: level filters must be <=2 / <=3, got % / %', r.level_filter_primary, r.level_filter_allowed
            USING ERRCODE = 'P0001';
    END IF;
    IF r.visibility_scope IS DISTINCT FROM ARRAY['reviewer','builder','all'] THEN
        RAISE EXCEPTION 'GRANT verify: visibility_scope must be {reviewer,builder,all}, got %', r.visibility_scope
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE '✅ GRANT verified — reviewer reinstated: closed=% open=1, prior close=%, new valid_from=%, domains={review_judgement}, greenlight=TRUE verify=FALSE',
        v_closed, v_last_closed, r.valid_from;
END $$;

COMMIT;
