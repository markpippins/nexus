-- =============================================================================
--  V180: applied_grants pre-flight view + grant_is_applied() helper
--
--  Motivation (2026-09-17, records bae6f566 / receipts dd89c825, 1d1e96e4,
--  3c10a807, af34875f, 33b38323, 2387bcc9): the grants batch absorbed 7+
--  post-completion replays of apply instructions. Each was answered with a
--  fresh verification receipt and a declined re-execution — the grant files
--  are repeatable executors, so a second run would close granted snapshots
--  and mint redundant grant events (audit-trail pollution for zero
--  capability change). Receipts protect the trail; they do not stop the
--  next replay. This migration makes apply instructions SELF-IDEMPOTENT:
--
--    * nebula.applied_grants        — one row per OPEN granted snapshot,
--      spec-bearing, sentinel-correct (open = valid_until '9999-12-31',
--      NOT NULL — see the bitemporal-sentinel lesson this batch banked).
--    * nebula.grant_is_applied(role, spec) — TRUE only when the role's
--      open row matches the proposed spec EXACTLY (the rediff gate: same
--      role + same spec = already applied; same role + different spec =
--      a NEW lawful grant event, must proceed).
--
--  Every sql/grants/*.sql gets a pre-flight block: same spec present ->
--  RAISE 'GRANT-APPLIED: ...' and the (repeatable) transaction aborts
--  before any close/insert. Re-running a merged apply is now a no-op BY
--  CONSTRUCTION, not by discipline.
--
--  Scope: views/functions only — no table changes, no triggers, no
--  backfill. Re-runnable (CREATE OR REPLACE / DROP IF EXISTS).
-- =============================================================================

BEGIN;

-- One row per OPEN granted role snapshot, with the full granted spec.
-- Sentinel-correct: "open" is valid_until = '9999-12-31' (house sentinel),
-- never NULL. Includes the recorded-time audit txid pair for receipts.
CREATE OR REPLACE VIEW nebula.applied_grants AS
SELECT r.name                         AS role,
       -- POSITIVE spec construction: exactly the grant-relevant columns,
       -- key-for-key identical to the pre-flight blocks in sql/grants/*.sql
       -- (jsonb equality is the gate; a negative strip list would silently
       -- desync the moment roles_history grows a new granted column).
       jsonb_build_object(
           'owns_domains',             to_jsonb(r.owns_domains),
           'can_greenlight',           to_jsonb(r.can_greenlight),
           'can_create_questions',     to_jsonb(r.can_create_questions),
           'can_create_agendas',       to_jsonb(r.can_create_agendas),
           'can_resolve_questions',    to_jsonb(r.can_resolve_questions),
           'can_verify_work_requests', to_jsonb(r.can_verify_work_requests),
           'max_open_questions',       to_jsonb(r.max_open_questions),
           'requires_approval_from',   to_jsonb(r.requires_approval_from),
           'escalates_to',             to_jsonb(r.escalates_to),
           'escalation_triggers',      to_jsonb(r.escalation_triggers),
           'visibility_scope',         to_jsonb(r.visibility_scope)
       )                               AS spec,
       -- NOTE: level_filter_primary/allowed are deliberately OUT of the spec:
       -- they are carry-forward (inherited) state per ee2c6961, not granted
       -- state, so they belong in no rediff.
       r.owns_domains,
       r.can_greenlight,
       r.can_create_questions,
       r.can_create_agendas,
       r.can_resolve_questions,
       r.can_verify_work_requests,
       r.max_open_questions,
       r.requires_approval_from,
       r.escalates_to,
       r.escalation_triggers,
       r.level_filter_primary,
       r.level_filter_allowed,
       r.visibility_scope,
       r.valid_from                   AS granted_at
FROM nebula.roles_history r
WHERE r.valid_until = '9999-12-31 00:00:00+00'::timestamptz
  AND r.recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;

COMMENT ON VIEW nebula.applied_grants IS
'One row per OPEN granted role snapshot (sentinel-correct, spec-bearing). Rediff gate: grant_is_applied(role, spec) is TRUE only on exact spec match — a re-apply of the same grant is refused as GRANT-APPLIED; a changed capability is a NEW lawful grant event.';

-- The rediff gate. Exact-match-only: JSONB equality over the spec-bearing
-- columns (any drift in any granted value = not applied = proceed).
CREATE OR REPLACE FUNCTION nebula.grant_is_applied(p_role text, p_spec jsonb)
RETURNS boolean
LANGUAGE plpgsql STABLE
AS $fn$
DECLARE
    v_open  int;
    v_match int;
    v_open_spec jsonb;
BEGIN
    SELECT count(*) INTO v_open
      FROM nebula.roles_history r
     WHERE r.name = p_role
       AND r.valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND r.recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;

    IF v_open = 0 THEN
        RETURN false;   -- nothing open -> not applied -> proceed
    END IF;

    -- The open row's GRANT spec (11 keys; carry-forward levels excluded —
    -- they are inherited state, not granted state). Built key-for-key with
    -- the same jsonb_build_object shape the grant files' pre-flight uses.
    SELECT jsonb_build_object(
               'owns_domains',             to_jsonb(r.owns_domains),
               'can_greenlight',           to_jsonb(r.can_greenlight),
               'can_create_questions',     to_jsonb(r.can_create_questions),
               'can_create_agendas',       to_jsonb(r.can_create_agendas),
               'can_resolve_questions',    to_jsonb(r.can_resolve_questions),
               'can_verify_work_requests', to_jsonb(r.can_verify_work_requests),
               'max_open_questions',       to_jsonb(r.max_open_questions),
               'requires_approval_from',   to_jsonb(r.requires_approval_from),
               'escalates_to',             to_jsonb(r.escalates_to),
               'escalation_triggers',      to_jsonb(r.escalation_triggers),
               'visibility_scope',         to_jsonb(r.visibility_scope)
           ) INTO v_open_spec
      FROM nebula.roles_history r
     WHERE r.name = p_role
       AND r.valid_until = '9999-12-31 00:00:00+00'::timestamptz
       AND r.recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;

    -- rediff gate: exact grant-spec match on the open row = already applied
    RETURN v_open_spec = p_spec;
END;
$fn$;

COMMENT ON FUNCTION nebula.grant_is_applied(text, jsonb) IS
'TRUE iff role has an OPEN snapshot whose granted spec matches p_spec exactly. Same role + different spec = false (a NEW grant event is lawful). Fail-closed on absent roles: no open row = not applied.';

COMMIT;
