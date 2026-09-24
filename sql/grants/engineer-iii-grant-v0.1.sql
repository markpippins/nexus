-- =============================================================================
--  engineer-iii-grant-v0.1.sql — FIRST FRESH-ROLE grant event (DBA)
--
--  Registers the engineer-iii role snapshot in nebula.roles_history. This is
--  the registration step V197 (vocabulary widening) deliberately did not
--  perform: V197 widened agent_records_role_check (live, scratch, pin) but
--  role REGISTRATION is a grant event per the grants-batch doctrine.
--
--  Incident provenance: Engineer III's freebuff-boot checkpoint advance failed
--  with a role-integrity violation (thread 957ead77) — the checkpoint trigger
--  (nebula.tg_coordination_checkpoints_role_fk) resolves roles through the
--  nebula.roles view, and no engineer-iii snapshot existed. The Assembly user
--  (engineer-iii@nexus.local, bcrypt) was provisioned separately on 2026-09-23.
--
--  FRESH-ROLE SEMANTICS (differs from engineer-ii-grant-v0.1.sql's
--  clone-close): no prior snapshot exists, so this event INSERTS the open
--  snapshot directly — there is nothing to close. If an open snapshot IS
--  found (replay against a world where one was minted another way), the
--  event falls back to the clone-close pattern: close, derive, re-insert.
--
--  GRANTED SPEC — clone-of engineer baseline (mirrors engineer-ii per the
--  ratified second-instance principle; the user commissioned Engineer III
--  "same config as Engineer II"):
--    owns_domains             = {implementation, build_verification}
--    can_verify_work_requests = TRUE   (mirrors engineer / engineer-ii)
--    can_greenlight           = false
--    can_create_questions     = false
--    can_resolve_questions    = false
--    can_create_agendas       = false
--    max_open_questions       = NULL (engineer baseline)
--    requires_approval_from   = NULL (engineer baseline)
--    escalates_to             = {architect}
--    escalation_triggers      = {design_concern}
--    level filters            = level <= 1 / level <= 2 (mirrors engineer)
--    visibility_scope         = {builder, all}
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── GRANT VALUES (clone-of engineer baseline) ────────────────────────
    v_role                  text    := 'engineer-iii';
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
    v_now      timestamptz := now();
    v_open     int;
    v_updated  int;
    v_closed   nebula.roles_history%ROWTYPE;
BEGIN
    -- ── SELF-IDEMPOTENCE PRE-FLIGHT (V180 rediff gate) ───────────────────
    -- Same role + same granted spec already open => replayed apply is a
    -- loud no-op (GRANT-APPLIED). Different spec => a NEW lawful grant
    -- event; proceeds. Nothing open => fresh registration; proceeds.
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

    SELECT count(*) INTO v_open
      FROM nebula.roles_history
     WHERE name = v_role
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open = 0 THEN
        -- ── FRESH-ROLE PATH: insert the open snapshot directly ──────────
        -- Nothing to close; created_at = valid_from = this event.
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
            v_role,
            'Engineer III',
            'Engineer III (commissioned 2026-09-24; V197 vocabulary widening). '
            || 'Fresh-role grant: capabilities mirror the engineer baseline '
            || '(clone-of-engineer, same config as Engineer II per the '
            || 'second-instance principle).',
            v_owns_domains, v_can_greenlight, v_can_create_questions,
            v_can_create_agendas, v_can_resolve_questions, v_can_verify_wrs,
            v_max_open_questions, v_requires_approval,
            false, NULL, NULL,
            v_escalates_to, v_escalation_triggers,
            v_level_primary, v_level_allowed, v_visibility,
            v_now, v_now,
            v_now, '9999-12-31 00:00:00+00'::timestamptz, v_now, '9999-12-31 00:00:00+00'::timestamptz
        );
        RAISE NOTICE 'GRANT: fresh-role registration — % opened as clone-of-engineer at %', v_role, v_now;
    ELSE
        -- ── RE-GRANT PATH: an open snapshot exists with a DIFFERENT spec ─
        -- Close it and mint the granted successor (engineer-ii pattern).
        UPDATE nebula.roles_history
           SET valid_until       = v_now,
               recorded_until_dt = v_now,
               updated_at        = v_now
         WHERE name = v_role
           AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
           AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
        GET DIAGNOSTICS v_updated = ROW_COUNT;
        IF v_updated <> 1 THEN
            RAISE EXCEPTION 'GRANT: multiple open snapshots for role % — refusing', v_role
                USING ERRCODE = 'P0001';
        END IF;

        SELECT * INTO v_closed FROM nebula.roles_history
         WHERE name = v_role AND valid_until = v_now AND recorded_until_dt = v_now;

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
        RAISE NOTICE 'GRANT: role % closed at % and re-opened with the granted clone-of-engineer spec', v_role, v_now;
    END IF;
END $$;

-- 4. Verification gate: exactly one open snapshot, granted values pinned
DO $$
DECLARE
    v_open   int;
    v_verify boolean;
    v_domains text[];
BEGIN
    SELECT count(*) INTO v_open
      FROM nebula.roles_history
     WHERE name = 'engineer-iii'
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
    IF v_open <> 1 THEN
        RAISE EXCEPTION 'GRANT verify: chain malformed (open=% — expected exactly 1)', v_open
            USING ERRCODE = 'P0001';
    END IF;

    SELECT can_verify_work_requests, owns_domains
      INTO v_verify, v_domains
      FROM nebula.roles_history
     WHERE name = 'engineer-iii'
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    IF NOT v_verify
       OR v_domains IS DISTINCT FROM ARRAY['implementation','build_verification'] THEN
        RAISE EXCEPTION 'GRANT verify: granted values do not match the engineer clone baseline'
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE 'GRANT verified — engineer-iii: exactly one open snapshot, clone-of-engineer values pinned (verify=TRUE mirrors engineer)';
END $$;

COMMIT;
