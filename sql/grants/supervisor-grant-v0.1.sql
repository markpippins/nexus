-- =============================================================================
-- supervisor-grant-v0.1.sql — initial Supervisor capability grant (DBA)
--
-- DRAFT — NOT APPLIED TO LIVE. Apply only after the role-memory restore/V199
-- incident is resolved and with explicit operator authorization.
--
-- Initial authority: role-system administration only. The Supervisor may own
-- role_administration metadata, but this grant deliberately grants no
-- WorkRequest execution, receipt, review, greenlight, or verification power.
-- PEB/kernel, SOL, review, and DBA authority remain with their owners.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    v_role text := 'supervisor';
    v_now timestamptz := now();
    v_open int;
    v_current nebula.roles_history%ROWTYPE;
BEGIN
    SELECT count(*)
      INTO v_open
      FROM nebula.roles_history
     WHERE name = v_role
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open > 1 THEN
        RAISE EXCEPTION 'SUPERVISOR GRANT: multiple open snapshots (open=%)', v_open
            USING ERRCODE = 'P0001';
    END IF;

    IF v_open = 1 THEN
        SELECT * INTO v_current
          FROM nebula.roles_history
         WHERE name = v_role
           AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

        IF v_current.owns_domains IS DISTINCT FROM ARRAY['role_administration']::text[]
           OR v_current.can_greenlight IS DISTINCT FROM false
           OR v_current.can_create_questions IS DISTINCT FROM false
           OR v_current.can_create_agendas IS DISTINCT FROM false
           OR v_current.can_resolve_questions IS DISTINCT FROM false
           OR v_current.can_verify_work_requests IS DISTINCT FROM false
           OR v_current.max_open_questions IS DISTINCT FROM 0 THEN
            RAISE EXCEPTION
                'SUPERVISOR GRANT: an open row exists with a different capability shape; inspect and grant through an explicit close-then-insert event'
                USING ERRCODE = 'P0001';
        END IF;

        RAISE NOTICE 'SUPERVISOR GRANT: exact initial grant already open — no-op.';
        RETURN;
    END IF;

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
        'Supervisor',
        'Role-system administrator: role registration, configuration, doctrine and OpenCode projection regeneration, and role-surface verification. WorkRequest execution authority is deferred until Conduit resumes.',
        ARRAY['role_administration'],
        false, false, false, false, false,
        0,
        ARRAY['architect'],
        false, NULL, NULL,
        ARRAY['architect'],
        ARRAY['architecture_scope_change', 'role_contract_drift', 'generation_failure'],
        '<=3', '<=4', ARRAY['all'],
        v_now, v_now,
        v_now, '9999-12-31 00:00:00+00'::timestamptz,
        v_now, '9999-12-31 00:00:00+00'::timestamptz
    );

    RAISE NOTICE 'SUPERVISOR GRANT: initial role_administration grant opened.';
END $$;

-- Verification gate: the initial grant exists once and grants no pipeline or
-- verification authority. Any future expansion must use a new explicit grant
-- event rather than editing this historical file.
DO $$
DECLARE
    v_row nebula.roles_history%ROWTYPE;
    v_open int;
BEGIN
    SELECT count(*)
      INTO v_open
      FROM nebula.roles_history
     WHERE name = 'supervisor'
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open <> 1 THEN
        RAISE EXCEPTION 'SUPERVISOR GRANT verify: expected one open row, found %', v_open
            USING ERRCODE = 'P0001';
    END IF;

    SELECT * INTO v_row
      FROM nebula.roles_history
     WHERE name = 'supervisor'
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    IF v_row.owns_domains IS DISTINCT FROM ARRAY['role_administration']::text[]
       OR v_row.can_greenlight IS DISTINCT FROM false
       OR v_row.can_create_questions IS DISTINCT FROM false
       OR v_row.can_create_agendas IS DISTINCT FROM false
       OR v_row.can_resolve_questions IS DISTINCT FROM false
       OR v_row.can_verify_work_requests IS DISTINCT FROM false
       OR v_row.max_open_questions IS DISTINCT FROM 0
       OR v_row.cron_enabled IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'SUPERVISOR GRANT verify: capability shape drifted'
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE 'SUPERVISOR GRANT verified: role_administration only; execution/receipt/verification authority remains false.';
END $$;

COMMIT;
