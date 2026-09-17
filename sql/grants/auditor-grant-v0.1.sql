-- =============================================================================
--  auditor-grant-v0.1.sql — canonical close-then-insert grant example (DBA)
--
--  The corrected, tracked successor of the untracked staging file
--  (nexus/tmp/auditor-grant-v0.1.sql) used for the live auditor grant of
--  2026-09-17 (R2 79feb142). The staging file taught two lessons the hard
--  way; this version encodes both, and doubles as the TEMPLATE for every
--  future capability grant under the architect-ruled convention (thread
--  225dfbbb):
--
--  LESSON 1 — Write the HISTORY TABLE, never the view.
--      nebula.roles is a VIEW over nebula.roles_history with the bitemporal
--      current-row predicate (now() >= recorded_on_dt AND now() <
--      recorded_until_dt AND now() >= valid_from AND now() < valid_until).
--      View-based UPDATE/INSERT silently match NOTHING once the row's
--      snapshot closes mid-transaction (UPDATE 0 / INSERT 0 0) — the row
--      vanishes from the view the moment you close it. All mechanics below
--      target nebula.roles_history directly.
--
--  LESSON 2 — The convention requires the V175 constraint shape.
--      close-then-insert is impossible under a full UNIQUE(name) (one
--      snapshot per role, ever). V175 dropped it in favor of the partial
--      open-snapshot unique (roles_name_open_key: exactly one OPEN row per
--      name, closed history exempt). This script REFUSES to run against the
--      old shape (the gate detects UPDATE 0 and aborts).
--
--  Provenance for this specific grant: architect ratification in thread
--  225dfbbb (roles_history carries no granted_by column — provenance lives
--  here, in the NEBULA_AUDIT trail, and in agent records 1de343e2/927cecc8).
--
--  Repeatability note: this file is a GRANT-EVENT executor, not a one-shot
--  migration. Re-running closes the role's current open snapshot and opens a
--  successor with the values as edited — each run is one attributable grant
--  event, and the bitemporal chain accumulates (V175 makes that possible).
--  A run against a role that does not exist, or against the pre-V175 shape,
--  fails loudly (close UPDATE matches nothing / successor INSERT hits the
--  full unique) — never a silent no-op. The verify gate makes every outcome
--  explicit.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── GRANT VALUES (edit per grant) ────────────────────────────────────
    v_role                  text    := 'auditor';
    v_owns_domains          text[]  := ARRAY['audit-trails','attestation'];
    v_can_greenlight        boolean := false;
    v_can_create_questions  boolean := false;
    v_can_create_agendas    boolean := false;
    v_can_resolve_questions boolean := false;
    v_can_verify_wrs        boolean := false;
    v_max_open_questions    integer := 0;
    v_requires_approval     text[]  := ARRAY['architect'];
    v_escalates_to          text[]  := ARRAY['architect'];
    v_escalation_triggers   text[]  := ARRAY['attestation_failure','silent_no_op_evidence',
                                             'audit_gap','retention_violation'];
    v_level_primary         text    := '<=2';
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
       SET valid_until       = v_now,          -- validity ends: the grant changes
           recorded_until_dt = v_now,          -- record corrected: successor follows
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
    --    valid_from == closed.valid_until: no gap, no overlap, no instant
    --    of false currentness. Non-granted columns carry forward.
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

    RAISE NOTICE 'GRANT: role % closed at % and re-opened with the granted capability set', v_role, v_now;
END $$;

-- ── 4. Verification gate: the chain must be exactly the ruled shape ────────
DO $$
DECLARE
    v_open     int;
    v_closed   int;
    v_gap      int;
    v_domains  text;
BEGIN
    SELECT count(*) INTO v_open FROM nebula.roles_history
     WHERE name = 'auditor' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    SELECT count(*) INTO v_closed FROM nebula.roles_history
     WHERE name = 'auditor' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open <> 1 OR v_closed < 1 THEN
        RAISE EXCEPTION 'GRANT verify: chain malformed (open=%, closed=%)', v_open, v_closed
            USING ERRCODE = 'P0001';
    END IF;

    -- handoff exactness: the open row's valid_from must meet the newest
    -- closed row's valid_until (no gap, no overlap)
    SELECT count(*) INTO v_gap
    FROM nebula.roles_history o
    JOIN nebula.roles_history c ON c.name = o.name AND c.valid_until <> '9999-12-31 00:00:00+00'::timestamptz
    WHERE o.name = 'auditor' AND o.valid_until = '9999-12-31 00:00:00+00'::timestamptz
      AND c.valid_until = (SELECT max(valid_until) FROM nebula.roles_history
                           WHERE name = 'auditor' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz)
      AND o.valid_from IS DISTINCT FROM c.valid_until;
    IF v_gap > 0 THEN
        RAISE EXCEPTION 'GRANT verify: closed/open snapshot handoff is not exact (gap or overlap)'
            USING ERRCODE = 'P0001';
    END IF;

    SELECT array_to_string(owns_domains, ',') INTO v_domains
    FROM nebula.roles_history
    WHERE name = 'auditor' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    IF v_domains IS NULL OR v_domains = '' THEN
        RAISE EXCEPTION 'GRANT verify: open snapshot carries no granted domains'
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE '✅ GRANT verified — auditor chain: closed=% open=%, handoff exact, domains={%}',
        v_closed, v_open, v_domains;
END $$;

COMMIT;
