-- =============================================================================
-- wave1-clone-baseline.sql — pinned clone-source baseline (DBA, Wave 1)
--
-- The architect's Wave-1 precondition (ratification c141dd7a): "pin the
-- clone source — snapshot the current analyst/engineer capability rows
-- from nebula.roles and annotate them as the baseline the clones mirror —
-- otherwise 'clone' could silently clone default-shaped rows."
--
-- This file IS that snapshot, executable: it hard-verifies the live
-- analyst/engineer rows still match the pinned values and REFUSES
-- (exception) on any drift. Run it before analyst-ii-grant-v0.1.sql and
-- engineer-ii-grant-v0.1.sql; the grant files' values are pinned to this
-- same baseline, so the chain of custody is: snapshot == live at pin time
-- == grant values.
--
-- Snapshotted: 2026-09-17 ~14:20Z (DBA, R1 3bb4306b) from nebula.roles
-- (the open current snapshot of nebula.roles_history).
-- Pinned: capability fields only — row id and the bitemporal columns
-- are row identity/lifecycle, not capability, and legitimately differ.
-- =============================================================================

BEGIN;

DO $baseline$
DECLARE
  v_analyst   nebula.roles_history%ROWTYPE;
  v_engineer  nebula.roles_history%ROWTYPE;
  v_drift     text[] := '{}';
BEGIN
  IF to_regclass('nebula.roles_history') IS NULL THEN
    RAISE EXCEPTION 'W1-BASELINE-GATE-001: nebula.roles_history does not exist';
  END IF;

  SELECT * INTO v_analyst FROM nebula.roles_history
   WHERE name = 'analyst'
     AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
     AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'W1-BASELINE-GATE-002: no open analyst snapshot found';
  END IF;

  SELECT * INTO v_engineer FROM nebula.roles_history
   WHERE name = 'engineer'
     AND valid_until = '9999-12-31 00:00:00+00'::timestamptz
     AND recorded_until_dt = '9999-12-31 00:00:00+00'::timestamptz;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'W1-BASELINE-GATE-002: no open engineer snapshot found';
  END IF;

  -- ── analyst pinned values (snapshot 2026-09-17) ──────────────────────
  IF v_analyst.display_name            IS DISTINCT FROM 'Analyst' THEN
    v_drift := array_append(v_drift, 'analyst.display_name');
  END IF;
  IF v_analyst.description             IS DISTINCT FROM 'Triages issues, resolves ambiguities, provides detail for unclear requirements.' THEN
    v_drift := array_append(v_drift, 'analyst.description');
  END IF;
  IF v_analyst.owns_domains            IS DISTINCT FROM ARRAY['issue_triage','ambiguity_resolution'] THEN
    v_drift := array_append(v_drift, 'analyst.owns_domains');
  END IF;
  IF v_analyst.can_greenlight          IS DISTINCT FROM false THEN
    v_drift := array_append(v_drift, 'analyst.can_greenlight');
  END IF;
  IF v_analyst.can_verify_work_requests IS DISTINCT FROM false THEN
    v_drift := array_append(v_drift, 'analyst.can_verify_work_requests');
  END IF;
  IF v_analyst.can_create_questions    IS DISTINCT FROM true THEN
    v_drift := array_append(v_drift, 'analyst.can_create_questions');
  END IF;
  IF v_analyst.can_create_agendas      IS DISTINCT FROM false THEN
    v_drift := array_append(v_drift, 'analyst.can_create_agendas');
  END IF;
  IF v_analyst.can_resolve_questions   IS DISTINCT FROM true THEN
    v_drift := array_append(v_drift, 'analyst.can_resolve_questions');
  END IF;
  IF v_analyst.max_open_questions      IS DISTINCT FROM NULL THEN
    v_drift := array_append(v_drift, 'analyst.max_open_questions');
  END IF;
  IF v_analyst.requires_approval_from  IS DISTINCT FROM NULL THEN
    v_drift := array_append(v_drift, 'analyst.requires_approval_from');
  END IF;
  IF v_analyst.escalates_to            IS DISTINCT FROM ARRAY['architect','planner'] THEN
    v_drift := array_append(v_drift, 'analyst.escalates_to');
  END IF;
  IF v_analyst.escalation_triggers     IS DISTINCT FROM ARRAY['requirement_unclear'] THEN
    v_drift := array_append(v_drift, 'analyst.escalation_triggers');
  END IF;
  IF v_analyst.level_filter_primary    IS DISTINCT FROM 'level <= 3' THEN
    v_drift := array_append(v_drift, 'analyst.level_filter_primary');
  END IF;
  IF v_analyst.level_filter_allowed    IS DISTINCT FROM 'level <= 3' THEN
    v_drift := array_append(v_drift, 'analyst.level_filter_allowed');
  END IF;
  IF v_analyst.visibility_scope        IS DISTINCT FROM ARRAY['analyst','all'] THEN
    v_drift := array_append(v_drift, 'analyst.visibility_scope');
  END IF;

  -- ── engineer pinned values (snapshot 2026-09-17) ─────────────────────
  IF v_engineer.display_name            IS DISTINCT FROM 'Engineer' THEN
    v_drift := array_append(v_drift, 'engineer.display_name');
  END IF;
  IF v_engineer.description             IS DISTINCT FROM 'Implements features, verifies work requests for buildability.' THEN
    v_drift := array_append(v_drift, 'engineer.description');
  END IF;
  IF v_engineer.owns_domains            IS DISTINCT FROM ARRAY['implementation','build_verification'] THEN
    v_drift := array_append(v_drift, 'engineer.owns_domains');
  END IF;
  IF v_engineer.can_greenlight          IS DISTINCT FROM false THEN
    v_drift := array_append(v_drift, 'engineer.can_greenlight');
  END IF;
  IF v_engineer.can_verify_work_requests IS DISTINCT FROM true THEN
    v_drift := array_append(v_drift, 'engineer.can_verify_work_requests');
  END IF;
  IF v_engineer.can_create_questions    IS DISTINCT FROM false THEN
    v_drift := array_append(v_drift, 'engineer.can_create_questions');
  END IF;
  IF v_engineer.can_create_agendas      IS DISTINCT FROM false THEN
    v_drift := array_append(v_drift, 'engineer.can_create_agendas');
  END IF;
  IF v_engineer.can_resolve_questions   IS DISTINCT FROM false THEN
    v_drift := array_append(v_drift, 'engineer.can_resolve_questions');
  END IF;
  IF v_engineer.max_open_questions      IS DISTINCT FROM NULL THEN
    v_drift := array_append(v_drift, 'engineer.max_open_questions');
  END IF;
  IF v_engineer.requires_approval_from  IS DISTINCT FROM NULL THEN
    v_drift := array_append(v_drift, 'engineer.requires_approval_from');
  END IF;
  IF v_engineer.escalates_to            IS DISTINCT FROM ARRAY['architect'] THEN
    v_drift := array_append(v_drift, 'engineer.escalates_to');
  END IF;
  IF v_engineer.escalation_triggers     IS DISTINCT FROM ARRAY['design_concern'] THEN
    v_drift := array_append(v_drift, 'engineer.escalation_triggers');
  END IF;
  IF v_engineer.level_filter_primary    IS DISTINCT FROM 'level <= 1' THEN
    v_drift := array_append(v_drift, 'engineer.level_filter_primary');
  END IF;
  IF v_engineer.level_filter_allowed    IS DISTINCT FROM 'level <= 2' THEN
    v_drift := array_append(v_drift, 'engineer.level_filter_allowed');
  END IF;
  IF v_engineer.visibility_scope        IS DISTINCT FROM ARRAY['builder','all'] THEN
    v_drift := array_append(v_drift, 'engineer.visibility_scope');
  END IF;

  IF array_length(v_drift, 1) > 0 THEN
    RAISE EXCEPTION 'W1-BASELINE-GATE-002: live rows drifted from the pinned Wave-1 clone baseline — resolve before granting clones. Drifted fields: %', array_to_string(v_drift, ', ');
  END IF;

  RAISE NOTICE 'W1 baseline verified: analyst + engineer live rows match the pinned clone-source values (2026-09-17 snapshot)';
END
$baseline$;

COMMIT;
