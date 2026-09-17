-- =============================================================================
--  devops-grant-v0.1.sql — Wave-2 grant event (DBA)
--
--  DRAFT — NOT APPLIED TO LIVE; apply waits on explicit operator go.
--  Ratified as-designed in the grants batch (architect decision c141dd7a,
--  Wave 2). Grant-event executor per the corrected template
--  (sql/grants/auditor-grant-v0.1.sql lineage, #299): closes the role's
--  current open snapshot, opens the granted successor — handoff-exact,
--  repeatable (re-run = another grant event), refuses loudly on the
--  pre-V175 shape or a missing role. Template doctrine (write the
--  HISTORY table, never the view; V175 shape required) carries from the
--  template header.
--
--  Ratified design for devops:
--      {environment_operations} domain only — backup/restore, timers,
--      service fleet. No flags: operational domain ownership, no authority.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── GRANT VALUES (ratified Wave-2 design) ───────────────────────────
    v_role                  text    := 'devops';
    v_owns_domains          text[]  := ARRAY['environment_operations'];
    v_can_greenlight        boolean := false;
    v_can_create_questions  boolean := false;
    v_can_create_agendas    boolean := false;
    v_can_resolve_questions boolean := false;
    v_can_verify_wrs        boolean := false;
    v_max_open_questions    integer := 0;
    v_requires_approval     text[]  := ARRAY['architect'];
    v_escalates_to          text[]  := ARRAY['architect'];
    v_escalation_triggers   text[]  := ARRAY[]::text[];
    v_level_primary         text;   -- carry-forward: assigned after v_closed is loaded
    v_level_allowed         text;   -- (live columns are NOT NULL; DECLARE defaults run before that)
    v_visibility            text[]  := ARRAY['all'];
    -- ─────────────────────────────────────────────────────────────────────
    v_now     timestamptz := now();
    v_closed  nebula.roles_history%ROWTYPE;
    v_updated int;
BEGIN
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

    SELECT * INTO v_closed FROM nebula.roles_history
     WHERE name = v_role AND valid_until = v_now AND recorded_until_dt = v_now;

    -- carry forward the untouched columns BEFORE building the successor
    v_level_primary := v_closed.level_filter_primary;
    v_level_allowed := v_closed.level_filter_allowed;

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

    RAISE NOTICE 'GRANT: role % closed at % and re-opened with the ratified Wave-2 capability set', v_role, v_now;
END $$;

-- Verification gate: chain shape, handoff exactness, granted values
DO $$
DECLARE
    v_open int; v_closed int; v_gap int;
    v_cq boolean; v_rq boolean; v_moq int; v_domains text[];
BEGIN
    SELECT count(*) INTO v_open   FROM nebula.roles_history
     WHERE name = 'devops' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    SELECT count(*) INTO v_closed FROM nebula.roles_history
     WHERE name = 'devops' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;
    IF v_open <> 1 OR v_closed < 1 THEN
        RAISE EXCEPTION 'GRANT verify: chain malformed (open=%, closed=%)', v_open, v_closed
            USING ERRCODE = 'P0001';
    END IF;

    SELECT count(*) INTO v_gap
    FROM nebula.roles_history o
    JOIN nebula.roles_history c ON c.name = o.name
      AND c.valid_until <> '9999-12-31 00:00:00+00'::timestamptz
      AND c.valid_until = (SELECT max(valid_until) FROM nebula.roles_history
                           WHERE name = 'devops'
                             AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz)
    WHERE o.name = 'devops'
      AND o.valid_until = '9999-12-31 00:00:00+00'::timestamptz
      AND o.valid_from IS DISTINCT FROM c.valid_until;
    IF v_gap > 0 THEN
        RAISE EXCEPTION 'GRANT verify: handoff not exact' USING ERRCODE = 'P0001';
    END IF;

    SELECT can_create_questions, can_resolve_questions, max_open_questions,
           owns_domains
      INTO v_cq, v_rq, v_moq, v_domains
      FROM nebula.roles_history
     WHERE name = 'devops' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    IF v_cq IS DISTINCT FROM false
       OR v_rq IS DISTINCT FROM false
       OR v_moq IS DISTINCT FROM 0
       OR v_domains IS DISTINCT FROM ARRAY['environment_operations'] THEN
        RAISE EXCEPTION 'GRANT verify: granted values do not match the ratified Wave-2 design'
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE 'GRANT verified — devops chain: closed=% open=%, handoff exact, ratified Wave-2 values pinned', v_closed, v_open;
END $$;

COMMIT;
