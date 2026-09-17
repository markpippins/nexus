-- =============================================================================
--  engineer-ii-grant-v0.1.sql — Wave-1 clone grant event (DBA)
--
--  Fourth use of the corrected grant template (auditor-grant-v0.1.sql
--  lineage, #299/#301). DRAFT — NOT APPLIED TO LIVE; apply waits on
--  explicit operator go. Ratified in the grants batch (architect decision
--  c141dd7a, Wave 1: "mechanical clones"), clone source pinned by
--  wave1-clone-baseline.sql (run it first).
--
--  CLONE SEMANTICS (differs from a fresh-role grant): engineer-ii already
--  exists with an open, capability-empty row (12-role batch, matrix
--  ruling 4e42f470). This event CLOSES that empty snapshot and opens the
--  granted successor pinned to the engineer clone-source baseline
--  (snapshot 2026-09-17, R1 3bb4306b):
--
--    clone-of engineer v0.1:
--      owns_domains            = {implementation, build_verification}
--      can_verify_work_requests = TRUE   (mirrors engineer — see note)
--      can_greenlight          = false
--      can_create_questions    = false
--      can_resolve_questions   = false
--      can_create_agendas      = false
--      max_open_questions      = NULL (engineer baseline)
--      requires_approval_from  = NULL (engineer baseline)
--      escalates_to            = {architect}            (mirrors engineer)
--      escalation_triggers     = {design_concern}       (mirrors engineer)
--      level filters           = level <= 1 / level <= 2 (mirrors engineer)
--      visibility_scope        = {builder, all}          (mirrors engineer)
--
--  NOTE on the mirrored TRUE: engineer already holds
--  can_verify_work_requests=TRUE; the clone mirrors it per the ratified
--  second-instance principle ("clones mirror their originals"). WR
--  verification is therefore held by engineer, engineer-ii, AND tester —
--  the attestation-authority claim is per-role attestable capability,
--  not exclusive possession. A correction record on the tester
--  commissioning note ("currently the ONLY role") is filed with this PR.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── GRANT VALUES (clone-of engineer, pinned to wave1-clone-baseline) ─
    v_role                  text    := 'engineer-ii';
    v_owns_domains          text[]  := ARRAY['implementation','build_verification'];
    v_can_greenlight        boolean := false;
    v_can_create_questions  boolean := false;
    v_can_create_agendas    boolean := false;
    v_can_resolve_questions boolean := false;
    v_can_verify_wrs        boolean := TRUE;   -- mirrors engineer baseline
    v_max_open_questions    integer := NULL;   -- engineer baseline
    v_requires_approval     text[]  := NULL;   -- engineer baseline
    v_escalates_to          text[]  := ARRAY['architect'];
    v_escalation_triggers   text[]  := ARRAY['design_concern'];
    v_level_primary         text    := 'level <= 1';
    v_level_allowed         text    := 'level <= 2';
    v_visibility            text[]  := ARRAY['builder','all'];
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
    -- 1. CLOSE the current open snapshot (history table; never the view)
    UPDATE nebula.roles_history
       SET valid_until       = v_now,
           recorded_until_dt = v_now,
           updated_at        = v_now
     WHERE name = v_role
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
    GET DIAGNOSTICS v_updated = ROW_COUNT;

    IF v_updated <> 1 THEN
        RAISE EXCEPTION 'GRANT: no OPEN snapshot found for role % (rows=%) — refusing', v_role, v_updated
            USING ERRCODE = 'P0001';
    END IF;

    -- 2. Derive the successor from the just-closed row
    SELECT * INTO v_closed FROM nebula.roles_history
     WHERE name = v_role AND valid_until = v_now AND recorded_until_dt = v_now;

    -- 3. INSERT the open successor (new uuid; handoff-exact; non-granted
    --    columns carry forward)
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
        gen_random_uuid(),
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

    RAISE NOTICE 'GRANT: role % closed at % and re-opened as the granted clone-of-engineer', v_role, v_now;
END $$;

-- 4. Verification gate: chain shape, handoff exactness, granted values
DO $$
DECLARE
    v_open int; v_closed int; v_gap int;
    v_verify boolean; v_domains text[];
BEGIN
    SELECT count(*) INTO v_open   FROM nebula.roles_history
     WHERE name = 'engineer-ii' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    SELECT count(*) INTO v_closed FROM nebula.roles_history
     WHERE name = 'engineer-ii' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;
    IF v_open <> 1 OR v_closed < 1 THEN
        RAISE EXCEPTION 'GRANT verify: chain malformed (open=%, closed=%)', v_open, v_closed
            USING ERRCODE = 'P0001';
    END IF;

    SELECT count(*) INTO v_gap
    FROM nebula.roles_history o
    JOIN nebula.roles_history c ON c.name = o.name
      AND c.valid_until <> '9999-12-31 00:00:00+00'::timestamptz
      AND c.valid_until = (SELECT max(valid_until) FROM nebula.roles_history
                           WHERE name = 'engineer-ii'
                             AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz)
    WHERE o.name = 'engineer-ii'
      AND o.valid_until = '9999-12-31 00:00:00+00'::timestamptz
      AND o.valid_from IS DISTINCT FROM c.valid_until;
    IF v_gap > 0 THEN
        RAISE EXCEPTION 'GRANT verify: handoff not exact' USING ERRCODE = 'P0001';
    END IF;

    SELECT can_verify_work_requests, owns_domains
      INTO v_verify, v_domains
      FROM nebula.roles_history
     WHERE name = 'engineer-ii' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    IF NOT v_verify
       OR v_domains IS DISTINCT FROM ARRAY['implementation','build_verification'] THEN
        RAISE EXCEPTION 'GRANT verify: granted values do not match the engineer clone baseline'
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE 'GRANT verified — engineer-ii chain: closed=% open=%, handoff exact, clone-of-engineer values pinned (verify=TRUE mirrors engineer)', v_closed, v_open;
END $$;

COMMIT;
