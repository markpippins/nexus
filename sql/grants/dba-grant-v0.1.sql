-- =============================================================================
-- dba-grant-v0.1.sql — DBA capability grant (authored by DBA, per 2dca56d4)
--
-- DRAFT — NOT APPLIED TO LIVE. Ratification path (architect record 2dca56d4):
--   DBA authors → Supervisor validates (registration, persona/card/config
--   parity, seed/live consistency, no placeholder fields) → Architect
--   ratifies (with operator as needed) → then and only then is this applied.
-- DBA must not be sole author and sole ratifier of its own authority.
--
-- Shape notes:
--   - Close-then-insert with the same guarded DO block the supervisor grant
--     (sql/grants/supervisor-grant-v0.1.sql) uses, adapted to the DBA's
--     actual starting state: dba already has ONE open snapshot — the generic
--     batch placeholder ("capabilities unassigned", all flags false). The
--     guard verifies the open row IS that exact placeholder shape before
--     closing it, refuses any other open shape, and no-ops if the chartered
--     row is already open. The placeholder must close, not survive alongside
--     a chartered row: architect acceptance for 2dca56d4 requires that "no
--     generic 'capabilities unassigned' DBA profile remains".
--   - Authority exercised is deliberately narrower than the role's day-to-day
--     practice: the capability flags here cover forum/judgement mechanics the
--     registry models. Domain authority (schema truth, backup/replication,
--     timers, DBA-domain rulings) is defined by the charter
--     (config/harnesses/opencode/agents/dba.md) and enforced by review, not by
--     these flags.
--   - No WorkRequest verification power. DBA never attests its own or others'
--     work; can_verify_work_requests stays false (separation of duties).
--   Apply (only AFTER architect ratification) — the session variable below
--   is a HARD PRECONDITION: without it the file raises and changes nothing:
--     psql -d nexus \
--       -c "SET app.dba_grant_ratified = '<architect-ratification-decision-uuid>'" \
--       -f sql/grants/dba-grant-v0.1.sql
--   This guard exists because a draft's dry-run was once executed for real
--   (2026-09-25 incident): test harnesses can mis-wire BEGIN/COMMIT; the
--   file itself must refuse to act without the ratified decision UUID.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    v_role text := 'dba';
    v_now timestamptz := now();
    v_open int;
    v_current nebula.roles_history%ROWTYPE;
    v_placeholder boolean;
    v_ratification text := current_setting('app.dba_grant_ratified', true);
BEGIN
    IF v_ratification IS NULL OR btrim(v_ratification) = '' THEN
        RAISE EXCEPTION
            'DBA GRANT: NOT RATIFIED — this grant applies only after architect ratification (path: DBA authors, Supervisor validates, Architect ratifies per 2dca56d4). To apply, SET app.dba_grant_ratified = ''<ratification decision uuid>'' in this session.'
            USING ERRCODE = 'P0001';
    END IF;
    IF v_ratification !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' THEN
        RAISE EXCEPTION
            'DBA GRANT: app.dba_grant_ratified must be the ratification decision UUID (got %)',
            left(v_ratification, 40)
            USING ERRCODE = 'P0001';
    END IF;
    SELECT count(*)
      INTO v_open
      FROM nebula.roles_history
     WHERE name = v_role
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open > 1 THEN
        RAISE EXCEPTION 'DBA GRANT: multiple open snapshots (open=%)', v_open
            USING ERRCODE = 'P0001';
    END IF;

    IF v_open = 1 THEN
        SELECT * INTO v_current
          FROM nebula.roles_history
         WHERE name = v_role
           AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

        v_placeholder :=
            v_current.owns_domains = '{}'::text[]
            AND v_current.can_greenlight IS FALSE
            AND v_current.can_create_questions IS FALSE
            AND v_current.can_create_agendas IS FALSE
            AND v_current.can_resolve_questions IS FALSE
            AND v_current.can_verify_work_requests IS FALSE
            AND v_current.max_open_questions IS NULL;

        IF v_current.description LIKE 'Ratified role vocabulary%capabilities unassigned%'
           AND v_placeholder THEN
            RAISE NOTICE 'DBA GRANT: closing generic placeholder snapshot for %.', v_role;
        ELSIF v_current.owns_domains = ARRAY[
                  'database_integrity', 'backup_replication', 'migration_ledger'
              ]::text[]
              AND v_current.can_verify_work_requests IS FALSE
              AND v_current.max_open_questions = 5 THEN
            RAISE NOTICE 'DBA GRANT: chartered row already open — no-op.';
            RETURN;
        ELSE
            RAISE EXCEPTION
                'DBA GRANT: open row is neither the known placeholder nor the chartered shape; inspect before granting (description=%)',
                left(v_current.description, 80)
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    -- Close the generic placeholder snapshot, then insert the chartered one.
    UPDATE nebula.roles_history
       SET valid_until   = v_now,
           recorded_until_dt = v_now
     WHERE name = v_role
       AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

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
        'DBA',
        'Database integrity governance: schema truth and migration ledgers, data-integrity enforcement, backup/replication capture verification, the drift/heartbeat timer cluster, and binding DBA-domain rulings (migration safety, constraint shape, ledger semantics, backup capture). Architecture, product, and pipeline judgement route to their owners; no WorkRequest verification power; no self-granted authority expansion.',
        ARRAY['database_integrity', 'backup_replication', 'migration_ledger'],
        false, true, false, true, false,
        5,
        ARRAY['architect'],
        false, NULL, NULL,
        ARRAY['architect'],
        ARRAY['schema_guarantee_broken', 'green_ledger_drift', 'backup_capture_unverified', 'reconstruction_surface_drift'],
        '<=4', '<=4', ARRAY['all'],
        v_now, v_now,
        v_now, '9999-12-31 00:00:00+00'::timestamptz,
        v_now, '9999-12-31 00:00:00+00'::timestamptz
    );

    RAISE NOTICE 'DBA GRANT: chartered capability row inserted for %.', v_role;
END $$;

COMMIT;
