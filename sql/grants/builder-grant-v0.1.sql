-- =============================================================================
--  builder-grant-v0.1.sql — canonical close-then-insert grant event (DBA)
--
--  Authority: architect Decision 7 (`fda24e67`, 2026-09-28) ratified the
--  Builder grant spec key-for-key and assigned SQL authorship to the DBA
--  ("Application (grant-event SQL) is DBA authorship, per Decision 1
--  (`628faa44`, grants are reviewed specifications, not inferable values)").
--  The reviewer half of that decision was authored as
--  sql/grants/reviewer-grant-v0.1.sql and applied 2026-11-08. The builder half
--  was never authored: `ls sql/grants | grep builder` returned nothing on
--  2026-11-10, and nebula.roles_history carried only CLOSED builder snapshots
--  (windows ending 2026-09-18 and 2026-09-27). The consequence was concrete —
--  harness admission answered `ROLE_EXPIRED` for every builder launch, so no
--  builder job could claim a ticket and no plan whose chain lacked an
--  IMPLEMENTATION transition could ever reach REVIEW_PASS.
--
--  This file is that missing half, and nothing more: it encodes Decision 7's
--  table verbatim. It does not widen builder authority (no greenlight, no
--  verification), and it does not backfill any other role.
--
--  Template notes (from tester-grant-v0.1.sql, the corrected template):
--
--  LESSON 1 — Write the HISTORY TABLE, never the view.
--      nebula.roles is a VIEW over nebula.roles_history with the bitemporal
--      current-row predicate. View-based UPDATE/INSERT silently match NOTHING
--      once the row's snapshot closes mid-transaction; all mechanics below
--      target nebula.roles_history directly.
--
--  LESSON 2 — The convention requires the V175 constraint shape (partial
--      open-snapshot unique). close-then-insert is impossible under a full
--      UNIQUE(name). This script's verify gate refuses a malformed chain.
--
--  RE-OPEN note: builder is a retired role (no open snapshot). The template
--  handles that explicitly — the successor is derived from the newest closed
--  snapshot, its identity columns carry forward, and valid_from = now() keeps
--  the retirement gap visible rather than fabricating continuity.
--
--  Verified gate (step 3) pins the Decision 7 values, so a drifted edit fails
--  the transaction instead of minting a quietly different grant.
-- =============================================================================

BEGIN;

DO $$
DECLARE
    -- ── GRANT VALUES (Decision 7, `fda24e67`, builder table) ─────────────
    v_role                  text    := 'builder';
    v_owns_domains          text[]  := ARRAY['implementation'];
    v_can_greenlight        boolean := false;
    v_can_create_questions  boolean := false;
    v_can_create_agendas    boolean := false;
    v_can_resolve_questions boolean := false;
    v_can_verify_wrs        boolean := FALSE;  -- Decision 1 separation
    v_max_open_questions    integer := NULL;   -- engineer baseline
    v_requires_approval     text[]  := NULL;   -- engineer baseline
    v_escalates_to          text[]  := ARRAY['architect'];
    v_escalation_triggers   text[]  := ARRAY['design_concern'];
    v_level_primary         text    := '<=1';  -- AGENTS.md Builder: L1 primary
    v_level_allowed         text    := '<=2';  -- L2 secondary
    v_visibility            text[]  := ARRAY['builder','all'];
    -- ─────────────────────────────────────────────────────────────────────
    v_now     timestamptz := now();
    v_closed  nebula.roles_history%ROWTYPE;
    v_updated int;
BEGIN

    -- ── 0. SEPARATION GATES (before any mutation) ───────────────────────
    IF v_can_verify_wrs THEN
        RAISE EXCEPTION 'GRANT verify: can_verify_work_requests must be FALSE — Decision 1 separation: no role verifies work it produced; verification is the tester''s'
            USING ERRCODE = 'P0001';
    END IF;
    IF v_can_greenlight THEN
        RAISE EXCEPTION 'GRANT verify: can_greenlight must be FALSE — builder implements; it does not greenlight merges or its own work'
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
        -- RE-OPEN path: the role is fully retired (builder's case). Derive the
        -- successor from the newest closed snapshot; its granted columns are
        -- REPLACED by the Decision 7 values, its identity/cron/created_at
        -- carry forward.
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
        gen_random_uuid(),                 -- history snapshots do NOT share ids
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
    r             nebula.roles_history%ROWTYPE;
    v_last_closed timestamptz;
BEGIN
    SELECT count(*) INTO v_open FROM nebula.roles_history
     WHERE name = 'builder' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;
    SELECT count(*) INTO v_closed FROM nebula.roles_history
     WHERE name = 'builder' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open <> 1 OR v_closed < 1 THEN
        RAISE EXCEPTION 'GRANT verify: chain malformed (open=%, closed=%)', v_open, v_closed
            USING ERRCODE = 'P0001';
    END IF;

    SELECT max(valid_until) INTO v_last_closed FROM nebula.roles_history
     WHERE name = 'builder' AND valid_until <> '9999-12-31 00:00:00+00'::timestamptz;

    SELECT * INTO r FROM nebula.roles_history
     WHERE name = 'builder' AND valid_until = '9999-12-31 00:00:00+00'::timestamptz;

    -- No overlap: the open episode begins at or after the prior one ended.
    IF r.valid_from < v_last_closed THEN
        RAISE EXCEPTION 'GRANT verify: open snapshot valid_from (%) precedes the prior close (%) — overlapping validity episodes', r.valid_from, v_last_closed
            USING ERRCODE = 'P0001';
    END IF;

    -- Decision 7 values, pinned key-for-key.
    IF r.owns_domains IS DISTINCT FROM ARRAY['implementation'] THEN
        RAISE EXCEPTION 'GRANT verify: owns_domains must be {implementation}, got %', r.owns_domains
            USING ERRCODE = 'P0001';
    END IF;
    IF r.can_greenlight IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'GRANT verify: can_greenlight must be FALSE (builder does not greenlight)' USING ERRCODE = 'P0001';
    END IF;
    IF r.can_verify_work_requests IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'GRANT verify: can_verify_work_requests must be FALSE (Decision 1 separation)'
            USING ERRCODE = 'P0001';
    END IF;
    IF r.can_create_questions IS DISTINCT FROM FALSE
       OR r.can_create_agendas IS DISTINCT FROM FALSE
       OR r.can_resolve_questions IS DISTINCT FROM FALSE THEN
        RAISE EXCEPTION 'GRANT verify: question/agenda booleans must all be FALSE (engineer baseline)'
            USING ERRCODE = 'P0001';
    END IF;
    IF r.max_open_questions IS NOT NULL THEN
        RAISE EXCEPTION 'GRANT verify: max_open_questions must be NULL, got %', r.max_open_questions
            USING ERRCODE = 'P0001';
    END IF;
    IF r.requires_approval_from IS NOT NULL THEN
        RAISE EXCEPTION 'GRANT verify: requires_approval_from must be NULL, got %', r.requires_approval_from
            USING ERRCODE = 'P0001';
    END IF;
    IF r.escalates_to IS DISTINCT FROM ARRAY['architect'] THEN
        RAISE EXCEPTION 'GRANT verify: escalates_to must be {architect}, got %', r.escalates_to
            USING ERRCODE = 'P0001';
    END IF;
    IF r.escalation_triggers IS DISTINCT FROM ARRAY['design_concern'] THEN
        RAISE EXCEPTION 'GRANT verify: escalation_triggers must be {design_concern}, got %', r.escalation_triggers
            USING ERRCODE = 'P0001';
    END IF;
    IF r.level_filter_primary IS DISTINCT FROM '<=1' THEN
        RAISE EXCEPTION 'GRANT verify: level_filter_primary must be <=1, got %', r.level_filter_primary
            USING ERRCODE = 'P0001';
    END IF;
    IF r.level_filter_allowed IS DISTINCT FROM '<=2' THEN
        RAISE EXCEPTION 'GRANT verify: level_filter_allowed must be <=2, got %', r.level_filter_allowed
            USING ERRCODE = 'P0001';
    END IF;
    IF r.visibility_scope IS DISTINCT FROM ARRAY['builder','all'] THEN
        RAISE EXCEPTION 'GRANT verify: visibility_scope must be {builder,all}, got %', r.visibility_scope
            USING ERRCODE = 'P0001';
    END IF;

    RAISE NOTICE 'GRANT verify: builder chain open=1 closed=%, Decision 7 values pinned', v_closed;
END $$;

COMMIT;
