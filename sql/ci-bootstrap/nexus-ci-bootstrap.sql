-- nexus CI bootstrap — complete global schemas for DB-backed tests
-- Extracted from a live nexus DB via pg_dump --schema-only; regenerate with
-- refresh.sh when conduit adapter global reads/writes change.
CREATE SCHEMA IF NOT EXISTS execution;
CREATE SCHEMA IF NOT EXISTS vision;
CREATE SCHEMA IF NOT EXISTS nebula;
CREATE SCHEMA IF NOT EXISTS conduit;
CREATE SCHEMA IF NOT EXISTS semantics;
CREATE SCHEMA IF NOT EXISTS terrain;
CREATE SCHEMA IF NOT EXISTS peb;
CREATE SCHEMA IF NOT EXISTS registry;
CREATE SCHEMA IF NOT EXISTS resolution;
CREATE SCHEMA IF NOT EXISTS wind;
CREATE SCHEMA IF NOT EXISTS cascade;
CREATE SCHEMA IF NOT EXISTS tackle;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE OR REPLACE FUNCTION public.notify_member_expired()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  IF OLD.valid_until = '9999-12-31 00:00:00+00'::timestamptz
     AND NEW.valid_until != '9999-12-31 00:00:00+00'::timestamptz THEN
    PERFORM pg_notify('segment_expired', jsonb_build_object(
      'segment_id', NEW.segment_id::text,
      'segment_set_ids', to_jsonb(ARRAY[NEW.segment_set_id]),
      'reason', 'member_expired'
    )::text);
  END IF;
  RETURN NEW;
END;
$function$
;
--
-- PostgreSQL database dump
--

\restrict 0HIwe3Psx6fkmkqebO0G4oYVXiGKxQEhA2OhXP87VwRTOuqjNi2y2OHHC03DcLk

-- Dumped from database version 17.10 (Debian 17.10-1.pgdg12+1)
-- Dumped by pg_dump version 17.11 (Debian 17.11-0+deb13u1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: cascade; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: conduit; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: execution; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: nebula; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: peb; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: registry; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: resolution; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: SCHEMA resolution; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA resolution IS 'SOL sandbox: greenfield redevelopment of semantics + selected nebula domain tables. Zero blast radius to production.';


--
-- Name: semantics; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: tackle; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: terrain; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: vision; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: wind; Type: SCHEMA; Schema: -; Owner: -
--



--
-- Name: check_projection_drift(uuid); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.check_projection_drift(p_wr_id uuid) RETURNS TABLE(expected_state text, expected_vision_stage text, expected_vision_ir_version integer, expected_last_event_id uuid, live_state text, live_vision_stage text, live_vision_ir_version integer, live_last_event_id uuid, has_drift boolean)
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_event RECORD;
  v_state TEXT := 'PROPOSED';
  v_stage TEXT := NULL;
  v_ir_ver INTEGER := 0;
  v_last_event UUID := NULL;
  v_live RECORD;
BEGIN
  FOR v_event IN
    SELECT * FROM conduit.work_request_events
    WHERE work_request_id = p_wr_id
    ORDER BY sequence_number ASC
  LOOP
    IF v_event.event_type = 'WORKREQUEST.CREATED' THEN
      v_state := 'PROPOSED';
    END IF;
    IF v_event.event_type = 'STATE.TRANSITION_COMMITTED' THEN
      v_state := COALESCE(v_event.payload->>'new_state', v_state);
    END IF;
    IF v_event.event_type = 'VISION.IR_PRODUCED' THEN
      v_stage := v_event.payload->>'ir_stage';
      v_ir_ver := COALESCE((v_event.payload->>'ir_version')::integer, v_ir_ver);
    END IF;
    v_last_event := v_event.event_id;
  END LOOP;

  SELECT * INTO v_live
  FROM conduit.work_request_state
  WHERE work_request_id = p_wr_id;

  IF v_live IS NULL THEN
    expected_state := v_state;
    expected_vision_stage := v_stage;
    expected_vision_ir_version := v_ir_ver;
    expected_last_event_id := v_last_event;
    live_state := NULL;
    live_vision_stage := NULL;
    live_vision_ir_version := NULL;
    live_last_event_id := NULL;
    has_drift := TRUE;
  ELSE
    expected_state := v_state;
    expected_vision_stage := v_stage;
    expected_vision_ir_version := v_ir_ver;
    expected_last_event_id := v_last_event;
    live_state := v_live.current_state;
    live_vision_stage := v_live.vision_stage;
    live_vision_ir_version := v_live.vision_ir_version;
    live_last_event_id := v_live.last_event_id;
    has_drift := (
      v_state != v_live.current_state
      OR v_stage IS DISTINCT FROM v_live.vision_stage
      OR v_ir_ver != v_live.vision_ir_version
      OR v_last_event IS DISTINCT FROM v_live.last_event_id
    );
  END IF;

  RETURN NEXT;
END;
$$;


--
-- Name: enforce_state_transition(); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.enforce_state_transition() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  -- Only STATE.TRANSITION_COMMITTED may carry a new_state payload key.
  -- Any other event type that includes new_state is a state-mutation
  -- attempt disguised as a non-state event — reject unconditionally.
  IF NEW.event_type != 'STATE.TRANSITION_COMMITTED'
     AND NEW.payload ? 'new_state'
  THEN
    RAISE EXCEPTION
      'STATE_MUTATION_FORBIDDEN: event type % must not carry payload.new_state; '
      'only STATE.TRANSITION_COMMITTED may mutate state',
      NEW.event_type;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: projection_coverage(); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.projection_coverage() RETURNS TABLE(uncovered_count bigint, uncovered_wr_ids uuid[])
    LANGUAGE sql STABLE
    AS $$
  SELECT count(*)::bigint, COALESCE(array_agg(work_request_id), '{}')
  FROM (SELECT DISTINCT e.work_request_id FROM conduit.work_request_events e
        EXCEPT SELECT s.work_request_id FROM conduit.work_request_state s) x;
$$;


--
-- Name: FUNCTION projection_coverage(); Type: COMMENT; Schema: conduit; Owner: -
--

COMMENT ON FUNCTION conduit.projection_coverage() IS 'P5 (audit c977bafd item 2): WR event streams absent from state = silent coverage gap.';


--
-- Name: rebuild_all_state_projections(); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.rebuild_all_state_projections() RETURNS integer
    LANGUAGE plpgsql
    AS $$
        DECLARE
          v_count INTEGER := 0;
          v_wr_id UUID;
        BEGIN
          TRUNCATE conduit.work_request_state;
          FOR v_wr_id IN
            SELECT DISTINCT work_request_id FROM conduit.work_request_events
          LOOP
            PERFORM conduit.rebuild_work_request_state(v_wr_id);
            v_count := v_count + 1;
          END LOOP;
          RETURN v_count;
        END;
        $$;


--
-- Name: rebuild_work_request_state(uuid); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.rebuild_work_request_state(p_wr_id uuid) RETURNS text
    LANGUAGE plpgsql
    AS $$
        DECLARE
          v_event RECORD;
          v_state TEXT := 'PROPOSED';
          v_stage TEXT := NULL;
          v_ir_ver INTEGER := 0;
          v_last_event UUID := NULL;
          v_last_at TIMESTAMPTZ := NOW();
        BEGIN
          FOR v_event IN
            SELECT * FROM conduit.work_request_events
            WHERE work_request_id = p_wr_id
            ORDER BY sequence_number ASC
          LOOP
            IF v_event.event_type = 'WORKREQUEST.CREATED' THEN
              v_state := 'PROPOSED';
            END IF;
            IF v_event.event_type = 'STATE.TRANSITION_COMMITTED' THEN
              v_state := COALESCE(v_event.payload->>'new_state', v_state);
            END IF;
            IF v_event.event_type = 'VISION.IR_PRODUCED' THEN
              v_stage := v_event.payload->>'ir_stage';
              v_ir_ver := COALESCE((v_event.payload->>'ir_version')::integer, v_ir_ver);
            END IF;
            v_last_event := v_event.event_id;
            v_last_at := v_event.occurred_at;
          END LOOP;

          INSERT INTO conduit.work_request_state
            (work_request_id, current_state, vision_stage, vision_ir_version, last_event_id, updated_at)
          VALUES (p_wr_id, v_state, v_stage, v_ir_ver, v_last_event, v_last_at)
          ON CONFLICT (work_request_id) DO UPDATE SET
            current_state = EXCLUDED.current_state,
            vision_stage = EXCLUDED.vision_stage,
            vision_ir_version = EXCLUDED.vision_ir_version,
            last_event_id = EXCLUDED.last_event_id,
            updated_at = EXCLUDED.updated_at;

          RETURN v_state;
        END;
        $$;


--
-- Name: replay_from_checkpoint(uuid, bigint); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.replay_from_checkpoint(p_wr_id uuid, p_checkpoint bigint) RETURNS TABLE(event_id uuid, event_type text, event_version integer, sequence_number bigint, occurred_at timestamp with time zone, payload jsonb, actor_type text, actor_id text)
    LANGUAGE plpgsql
    AS $$
        BEGIN
          RETURN QUERY
          SELECT e.event_id, e.event_type, e.event_version, e.sequence_number,
                 e.occurred_at, e.payload, e.actor_type, e.actor_id
          FROM conduit.work_request_events e
          WHERE e.work_request_id = p_wr_id
            AND e.sequence_number > p_checkpoint
          ORDER BY e.sequence_number ASC;
        END;
        $$;


--
-- Name: replay_work_request_events(uuid); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.replay_work_request_events(p_wr_id uuid) RETURNS TABLE(event_id uuid, event_type text, event_version integer, sequence_number bigint, occurred_at timestamp with time zone, payload jsonb, actor_type text, actor_id text)
    LANGUAGE plpgsql
    AS $$
        BEGIN
          RETURN QUERY
          SELECT e.event_id, e.event_type, e.event_version, e.sequence_number,
                 e.occurred_at, e.payload, e.actor_type, e.actor_id
          FROM conduit.work_request_events e
          WHERE e.work_request_id = p_wr_id
          ORDER BY e.sequence_number ASC;
        END;
        $$;


--
-- Name: update_work_request_state(); Type: FUNCTION; Schema: conduit; Owner: -
--

CREATE FUNCTION conduit.update_work_request_state() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_new_state TEXT;
  v_allowed TEXT[];
  v_current_state TEXT;
  v_ir_stage TEXT;
  v_ir_version INTEGER;
BEGIN
  IF NEW.event_type = 'WORKREQUEST.CREATED' THEN
    INSERT INTO conduit.work_request_state (
      work_request_id, current_state, last_event_id, updated_at
    ) VALUES (
      NEW.work_request_id, 'PROPOSED', NEW.event_id, NOW()
    )
    ON CONFLICT (work_request_id) DO UPDATE SET
      last_event_id = NEW.event_id,
      updated_at = NOW();

  ELSIF NEW.event_type = 'STATE.TRANSITION_COMMITTED' THEN
    v_new_state := NEW.payload->>'new_state';

    IF v_new_state IS NULL THEN
      RAISE EXCEPTION 'STATE.TRANSITION_COMMITTED event must include payload.new_state';
    END IF;

    SELECT current_state INTO v_current_state
    FROM conduit.work_request_state
    WHERE work_request_id = NEW.work_request_id;

    IF v_current_state IS NULL THEN
      RAISE EXCEPTION 'No state projection found for work_request_id %', NEW.work_request_id;
    END IF;

    v_allowed := conduit.allowed_transitions(v_current_state);

    IF NOT (v_new_state = ANY(v_allowed)) THEN
      RAISE EXCEPTION 'INVALID_TRANSITION: % → % not allowed. Allowed: %',
        v_current_state, v_new_state, array_to_string(v_allowed, ', ');
    END IF;

    UPDATE conduit.work_request_state
    SET current_state = v_new_state,
        last_event_id = NEW.event_id,
        updated_at = NOW()
    WHERE work_request_id = NEW.work_request_id;

  ELSIF NEW.event_type = 'VISION.IR_PRODUCED' THEN
    v_ir_stage := NEW.payload->>'ir_stage';
    v_ir_version := (NEW.payload->>'ir_version')::INTEGER;

    UPDATE conduit.work_request_state
    SET vision_stage = COALESCE(v_ir_stage, vision_stage),
        vision_ir_version = COALESCE(v_ir_version, vision_ir_version),
        last_event_id = NEW.event_id,
        updated_at = NOW()
    WHERE work_request_id = NEW.work_request_id;

  ELSE
    UPDATE conduit.work_request_state
    SET last_event_id = NEW.event_id,
        updated_at = NOW()
    WHERE work_request_id = NEW.work_request_id;
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: check_attempt_consistency(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.check_attempt_consistency() RETURNS TABLE(check_name text, violation_count bigint, sample_ids uuid[])
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Check 1: attempt.lease_id references a lease with mismatched request_id
    RETURN QUERY
    SELECT 
        'attempt_lease_request_mismatch'::text,
        COUNT(*),
        ARRAY_AGG(a.id) FILTER (WHERE cnt <= 5)
    FROM (
        SELECT a.id, COUNT(*) OVER () AS cnt
        FROM execution.attempts a
        JOIN execution.leases l ON a.lease_id = l.id
        WHERE a.request_id != l.request_id
    ) a;

    -- Check 2: attempt references non-existent lease
    RETURN QUERY
    SELECT 
        'attempt_orphan_lease'::text,
        COUNT(*),
        ARRAY_AGG(a.id) FILTER (WHERE cnt <= 5)
    FROM (
        SELECT a.id, COUNT(*) OVER () AS cnt
        FROM execution.attempts a
        LEFT JOIN execution.leases l ON a.lease_id = l.id
        WHERE a.lease_id IS NOT NULL AND l.id IS NULL
    ) a;

    -- Check 3: attempt references non-existent request
    RETURN QUERY
    SELECT 
        'attempt_orphan_request'::text,
        COUNT(*),
        ARRAY_AGG(a.id) FILTER (WHERE cnt <= 5)
    FROM (
        SELECT a.id, COUNT(*) OVER () AS cnt
        FROM execution.attempts a
        LEFT JOIN execution.requests r ON a.request_id = r.id
        WHERE r.id IS NULL
    ) a;

    -- Check 4: lease has no attempts (orphan lease)
    RETURN QUERY
    SELECT 
        'lease_no_attempts'::text,
        COUNT(*),
        ARRAY_AGG(l.id) FILTER (WHERE cnt <= 5)
    FROM (
        SELECT l.id, COUNT(*) OVER () AS cnt
        FROM execution.leases l
        LEFT JOIN execution.attempts a ON a.lease_id = l.id
        WHERE a.id IS NULL
    ) l;
END;
$$;


--
-- Name: check_attempt_lease_consistency(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.check_attempt_lease_consistency() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.lease_id IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM execution.leases l
            WHERE l.id = NEW.lease_id AND l.request_id = NEW.request_id
        ) THEN
            RAISE EXCEPTION 'attempt_lease_request_mismatch: lease % has request_id %, attempt has request_id %',
                NEW.lease_id, 
                (SELECT l.request_id FROM execution.leases l WHERE l.id = NEW.lease_id),
                NEW.request_id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;


--
-- Name: check_receipt_integrity(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.check_receipt_integrity() RETURNS TABLE(kind text, request_id text, receipt_id text, detail text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- 1. Receipt references non-existent attempt
    RETURN QUERY
    SELECT 'RECEIPT_ORPHAN_ATTEMPT'::text, r.request_id::text, r.id::text,
        format('receipt %s references attempt %s which does not exist', r.id, r.attempt_id)
    FROM execution.receipts r
    LEFT JOIN execution.attempts a ON a.id = r.attempt_id
    WHERE r.attempt_id IS NOT NULL AND a.id IS NULL;

    -- 2. Receipt references non-existent request
    RETURN QUERY
    SELECT 'RECEIPT_ORPHAN_REQUEST'::text, r.request_id::text, r.id::text,
        format('receipt %s references request %s which does not exist', r.id, r.request_id)
    FROM execution.receipts r
    LEFT JOIN execution.requests req ON req.id = r.request_id
    WHERE req.id IS NULL;

    -- 3. Receipt.attempt_id.request_id doesn't match receipt.request_id
    RETURN QUERY
    SELECT 'RECEIPT_ATTEMPT_REQUEST_MISMATCH'::text, r.request_id::text, r.id::text,
        format('receipt %s has request_id %s but its attempt %s has request_id %s',
               r.id, r.request_id, r.attempt_id, a.request_id)
    FROM execution.receipts r
    JOIN execution.attempts a ON a.id = r.attempt_id
    WHERE r.request_id != a.request_id;

    -- 4. Receipt with partial lineage (one field set, other NULL)
    RETURN QUERY
    SELECT 'RECEIPT_PARTIAL_LINEAGE'::text, r.request_id::text, r.id::text,
        format('receipt %s has lineage_source=%s but lineage_original_id=%s (or vice versa)',
               r.id, r.lineage_source, r.lineage_original_id)
    FROM execution.receipts r
    WHERE (r.lineage_source IS NULL) != (r.lineage_original_id IS NULL);

    -- 5. Receipt with lineage_original_id but no lineage_source
    RETURN QUERY
    SELECT 'RECEIPT_LINEAGE_ORPHAN'::text, r.request_id::text, r.id::text,
        format('receipt %s has lineage_original_id=%s but lineage_source is NULL',
               r.id, r.lineage_original_id)
    FROM execution.receipts r
    WHERE r.lineage_original_id IS NOT NULL AND r.lineage_source IS NULL;
END;
$$;


--
-- Name: check_stale_leases(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.check_stale_leases() RETURNS TABLE(lease_id uuid, request_id uuid, executor_id text, overdue interval)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT l.id, l.request_id, l.executor_id, NOW() - l.expires_at
    FROM execution.leases l
    WHERE l.status = 'ACTIVE'
      AND l.expires_at < NOW();
END;
$$;


--
-- Name: receipt_governance_trigger(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.receipt_governance_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_plan_id TEXT;
  v_wr_id TEXT;
BEGIN
  SELECT r.source_plan_id, r.source_wr_id::text
    INTO v_plan_id, v_wr_id
  FROM execution.requests r
  WHERE r.id = NEW.request_id;

  INSERT INTO peb.governance_events
    (receipt_id, event_type, work_request_id, plan_id, agent_role, payload)
  VALUES (
    NEW.id::text,
    'receipt:' || NEW.type,
    v_wr_id,
    COALESCE(v_plan_id, COALESCE(NEW.lineage_original_id, 'unknown')),
    NEW.agent_role,
    COALESCE(NEW.metadata, '{}'::jsonb)
  )
  ON CONFLICT (receipt_id) DO NOTHING;
  RETURN NEW;
END;
$$;


--
-- Name: receipts_immutable_guard(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.receipts_immutable_guard() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  RAISE EXCEPTION 'IMMUTABLE: execution.receipts cannot be updated or deleted';
  RETURN NULL;
END;
$$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$;


--
-- Name: sweep_stale_leases(); Type: FUNCTION; Schema: execution; Owner: -
--

CREATE FUNCTION execution.sweep_stale_leases() RETURNS TABLE(lease_id uuid, request_id uuid, executor_id text, expired_at timestamp with time zone)
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Return the leases we're about to expire (for logging/audit)
    RETURN QUERY
    UPDATE execution.leases l
    SET status = 'EXPIRED',
        released_at = NOW()
    WHERE l.status = 'ACTIVE'
      AND l.expires_at < NOW()
    RETURNING l.id, l.request_id, l.executor_id, l.expires_at;
END;
$$;


--
-- Name: agenda_to_specification(uuid, text); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.agenda_to_specification(p_agenda_id uuid, p_revision_type text DEFAULT 'created'::text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_spec_id uuid;
    v_agenda nebula.agendas%ROWTYPE;
    v_revision_num integer;
    v_snapshot jsonb;
    v_agenda_items jsonb;
BEGIN
    -- Fetch the agenda
    SELECT * INTO v_agenda FROM nebula.agendas WHERE id = p_agenda_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Agenda not found: %', p_agenda_id;
    END IF;

    -- Snapshot agenda items
    SELECT jsonb_agg(jsonb_build_object(
        'id', ai.id,
        'source_type', ai.source_type,
        'source_id', ai.source_id,
        'title', ai.title,
        'body', ai.body,
        'decisions', ai.decisions,
        'open_questions', ai.open_questions,
        'supporting_refs', ai.supporting_refs,
        'included', ai.included,
        'planner_note', ai.planner_note
    )) INTO v_snapshot
    FROM nebula.agenda_items ai
    WHERE ai.agenda_id = p_agenda_id;

    IF v_snapshot IS NULL THEN
        v_snapshot := '[]'::jsonb;
    END IF;

    -- Determine revision number: count existing specs for this agenda + 1
    SELECT COALESCE(MAX(revision_number), 0) + 1 INTO v_revision_num
    FROM nebula.specifications
    WHERE agenda_id = p_agenda_id;

    -- Create the specification
    INSERT INTO nebula.specifications (
        agenda_id, revision_number, revision_type,
        item_snapshot, change_summary
    ) VALUES (
        p_agenda_id, v_revision_num, p_revision_type,
        v_snapshot,
        format('Agenda "%s" finalized as specification v%s',
            COALESCE(v_agenda.title, 'Untitled'), v_revision_num)
    )
    RETURNING id INTO v_spec_id;

    -- Move the agenda to specified status
    UPDATE nebula.agendas
    SET status = 'specified', updated_at = now()
    WHERE id = p_agenda_id;

    RETURN v_spec_id;
END;
$$;


--
-- Name: agent_records_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.agent_records_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.agent_records_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION agent_records_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.agent_records_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: agent_records_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.agent_records_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.agent_records_history
        (id, record_type, role, title, content, source_path,
         metadata, tags, system_id, subsystem_id, feature_id,
         plan_ref, created_at,
         level, visibility_scope,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.record_type, NEW.role, NEW.title, NEW.content,
         NEW.source_path, NEW.metadata, NEW.tags, NEW.system_id,
         NEW.subsystem_id, NEW.feature_id, NEW.plan_ref,
         COALESCE(NEW.created_at, NOW()),
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.level := COALESCE(NEW.level, 1);
    NEW.visibility_scope := COALESCE(NEW.visibility_scope, 'all');
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION agent_records_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.agent_records_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: agent_records_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.agent_records_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.agent_records_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.agent_records_history
        (id, record_type, role, title, content, source_path,
         metadata, tags, system_id, subsystem_id, feature_id,
         plan_ref, created_at,
         level, visibility_scope,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.record_type, NEW.role, NEW.title, NEW.content,
         NEW.source_path, NEW.metadata, NEW.tags, NEW.system_id,
         NEW.subsystem_id, NEW.feature_id, NEW.plan_ref,
         OLD.created_at,
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, record_type, role, title, content, source_path,
              metadata, tags, system_id, subsystem_id, feature_id,
              plan_ref, created_at,
              level, visibility_scope,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION agent_records_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.agent_records_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: assert_harvest_exists(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.assert_harvest_exists(p_conversation_id uuid) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM nebula.harvests WHERE id = p_conversation_id
    ) THEN
        RAISE EXCEPTION 'conversation_id % does not match any active harvest', p_conversation_id
              USING HINT = 'Every conversation_block, snapshot, reference, segment, and override must belong to an active (non-expired) harvest. Create the harvest first.';
    END IF;
END;
$$;


--
-- Name: assess_cpf(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.assess_cpf(p_candidate_id uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_candidate RECORD;
    v_dep RECORD;
    v_result jsonb;
    v_components jsonb := '{}'::jsonb;
    v_total numeric := 0;
    v_suggested jsonb := '[]'::jsonb;
    v_dep_ids uuid[];
    v_dep_resolved int := 0;
    v_dep_total int := 0;
BEGIN
    -- Get the candidate
    SELECT 
        id, title, intent_description, system_id, subsystem_id, feature_id,
        tags, implementation_notes, code_snippets, completed, status
    INTO v_candidate
    FROM nebula.harvest_candidates
    WHERE id = p_candidate_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'error', 'Candidate not found',
            'candidate_id', p_candidate_id
        );
    END IF;

    -- 1. intent_filled (0.20)
    IF v_candidate.intent_description IS NOT NULL 
       AND length(trim(v_candidate.intent_description)) > 0 THEN
        v_components := v_components || jsonb_build_object('intent_filled', 0.20);
        v_total := v_total + 0.20;
    ELSE
        v_components := v_components || jsonb_build_object('intent_filled', 0.0);
        v_suggested := v_suggested || jsonb_build_object(
            'component', 'intent_filled',
            'question', 'What is the goal or intent of "' || v_candidate.title || '"?',
            'category', 'MISSING_INFO',
            'priority', 'HIGH'
        );
    END IF;

    -- 2. hierarchy_mapped (0.20)
    DECLARE
        v_hier_score numeric := 0;
    BEGIN
        IF v_candidate.system_id IS NOT NULL THEN
            v_hier_score := v_hier_score + 0.10;
        ELSE
            v_suggested := v_suggested || jsonb_build_object(
                'component', 'hierarchy_mapped',
                'question', 'Which system does "' || v_candidate.title || '" belong to?',
                'category', 'AMBIGUITY',
                'priority', 'HIGH'
            );
        END IF;
        
        IF v_candidate.subsystem_id IS NOT NULL THEN
            v_hier_score := v_hier_score + 0.07;
        ELSE
            v_suggested := v_suggested || jsonb_build_object(
                'component', 'hierarchy_mapped',
                'question', 'Which subsystem does "' || v_candidate.title || '" belong to?',
                'category', 'AMBIGUITY',
                'priority', 'MEDIUM'
            );
        END IF;
        
        IF v_candidate.feature_id IS NOT NULL THEN
            v_hier_score := v_hier_score + 0.03;
        END IF;
        
        v_components := v_components || jsonb_build_object('hierarchy_mapped', v_hier_score);
        v_total := v_total + v_hier_score;
    END;

    -- 3. tagged (0.10)
    DECLARE
        v_tag_count int := 0;
        v_tag_score numeric := 0;
    BEGIN
        v_tag_count := array_length(v_candidate.tags, 1);
        
        IF v_tag_count >= 2 THEN
            v_tag_score := 0.10;
        ELSIF v_tag_count = 1 THEN
            v_tag_score := 0.03;
            v_suggested := v_suggested || jsonb_build_object(
                'component', 'tagged',
                'question', 'Add one more tag to "' || v_candidate.title || '" for better categorization.',
                'category', 'MISSING_INFO',
                'priority', 'LOW'
            );
        ELSE
            v_suggested := v_suggested || jsonb_build_object(
                'component', 'tagged',
                'question', 'What categories or tags apply to "' || v_candidate.title || '"?',
                'category', 'MISSING_INFO',
                'priority', 'MEDIUM'
            );
        END IF;
        
        v_components := v_components || jsonb_build_object('tagged', v_tag_score);
        v_total := v_total + v_tag_score;
    END;

    -- 4. has_artifacts (0.20)
    DECLARE
        v_art_score numeric := 0;
    BEGIN
        IF v_candidate.implementation_notes IS NOT NULL 
           AND jsonb_array_length(v_candidate.implementation_notes) > 0 THEN
            v_art_score := v_art_score + 0.10;
        ELSE
            v_suggested := v_suggested || jsonb_build_object(
                'component', 'has_artifacts',
                'question', 'Do we have implementation notes for "' || v_candidate.title || '"?',
                'category', 'MISSING_INFO',
                'priority', 'MEDIUM'
            );
        END IF;
        
        IF v_candidate.code_snippets IS NOT NULL 
           AND jsonb_array_length(v_candidate.code_snippets) > 0 THEN
            v_art_score := v_art_score + 0.10;
        END IF;
        
        v_components := v_components || jsonb_build_object('has_artifacts', v_art_score);
        v_total := v_total + v_art_score;
    END;

    -- 5. reconciled (0.10)
    IF v_candidate.completed = true THEN
        v_components := v_components || jsonb_build_object('reconciled', 0.10);
        v_total := v_total + 0.10;
    ELSE
        v_components := v_components || jsonb_build_object('reconciled', 0.0);
        v_suggested := v_suggested || jsonb_build_object(
            'component', 'reconciled',
            'question', 'Is "' || v_candidate.title || '" complete and reconciled across all sources?',
            'category', 'SCOPE',
            'priority', 'MEDIUM'
        );
    END IF;

    -- 6. deps_resolved (0.20)
    SELECT array_agg(depends_on_id) INTO v_dep_ids
    FROM nebula.candidate_dependencies
    WHERE candidate_id = p_candidate_id;

    IF v_dep_ids IS NULL OR array_length(v_dep_ids, 1) = 0 THEN
        -- No dependencies = fully resolved
        v_components := v_components || jsonb_build_object('deps_resolved', 0.20);
        v_total := v_total + 0.20;
    ELSE
        -- Check each dependency
        FOR v_dep IN 
            SELECT hc.id, hc.status, hc.completed
            FROM nebula.harvest_candidates hc
            WHERE hc.id = ANY(v_dep_ids)
        LOOP
            v_dep_total := v_dep_total + 1;
            IF v_dep.status = 'promoted' OR v_dep.completed = true THEN
                v_dep_resolved := v_dep_resolved + 1;
            END IF;
        END LOOP;
        
        DECLARE
            v_dep_score numeric;
        BEGIN
            v_dep_score := (v_dep_resolved::numeric / v_dep_total::numeric) * 0.20;
            v_components := v_components || jsonb_build_object('deps_resolved', round(v_dep_score, 3));
            v_total := v_total + v_dep_score;
            
            IF v_dep_resolved < v_dep_total THEN
                v_suggested := v_suggested || jsonb_build_object(
                    'component', 'deps_resolved',
                    'question', (v_dep_total - v_dep_resolved) || ' of ' || v_dep_total || ' dependencies unresolved for "' || v_candidate.title || '".',
                    'category', 'DEPENDENCY',
                    'priority', 'HIGH'
                );
            END IF;
        END;
    END IF;

    -- Build result
    v_result := jsonb_build_object(
        'candidate_id', p_candidate_id,
        'title', v_candidate.title,
        'status', v_candidate.status,
        'score', round(v_total, 3),
        'promotable', (v_total >= 0.7),
        'components', v_components,
        'suggested_questions', v_suggested,
        'question_count', jsonb_array_length(v_suggested),
        'assessed_at', now()
    );

    RETURN v_result;
END;
$$;


--
-- Name: FUNCTION assess_cpf(p_candidate_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.assess_cpf(p_candidate_id uuid) IS 'CPF scoring for harvest candidates. Returns score (0.0-1.0), component breakdown, and suggested open questions for gaps. Used by Planner for deterministic backlog grooming.';


--
-- Name: assess_cpf_batch(uuid[]); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.assess_cpf_batch(p_candidate_ids uuid[]) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_results jsonb := '[]'::jsonb;
    v_id uuid;
    v_ready int := 0;
    v_not_ready int := 0;
BEGIN
    FOREACH v_id IN ARRAY p_candidate_ids
    LOOP
        v_results := v_results || nebula.assess_cpf(v_id);
    END LOOP;

    -- Count ready vs not ready
    SELECT count(*) INTO v_ready
    FROM jsonb_array_elements(v_results) AS elem
    WHERE (elem->>'promotable')::boolean = true;
    
    v_not_ready := jsonb_array_length(v_results) - v_ready;

    RETURN jsonb_build_object(
        'assessments', v_results,
        'count', jsonb_array_length(v_results),
        'ready', v_ready,
        'not_ready', v_not_ready,
        'assessed_at', now()
    );
END;
$$;


--
-- Name: FUNCTION assess_cpf_batch(p_candidate_ids uuid[]); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.assess_cpf_batch(p_candidate_ids uuid[]) IS 'Batch CPF scoring for multiple candidates. Returns assessments with ready/not-ready counts.';


--
-- Name: assess_ripple(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.assess_ripple(p_requirement_id uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_req RECORD;
    v_result jsonb;
    v_children uuid[];
    v_all_descendants uuid[];
    v_depth int := 0;
    v_direct_open int := 0;
    v_inherited_open int := 0;
    v_affected_systems text[];
    v_affected_subsystems text[];
    v_related_wrs int := 0;
    v_risk text := 'LOW';
    v_suggested jsonb := '[]'::jsonb;
BEGIN
    -- Get the target requirement
    SELECT id, title, status, priority, system_id, subsystem_id, feature_id, parent_id
    INTO v_req
    FROM nebula.requirements
    WHERE id = p_requirement_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'error', 'Requirement not found',
            'requirement_id', p_requirement_id
        );
    END IF;

    -- Get direct children
    SELECT array_agg(id) INTO v_children
    FROM nebula.requirements
    WHERE parent_id = p_requirement_id;

    -- Recursively get all descendants (for blast radius)
    WITH RECURSIVE descendants AS (
        SELECT id, 1 as depth
        FROM nebula.requirements
        WHERE parent_id = p_requirement_id
        UNION ALL
        SELECT r.id, d.depth + 1
        FROM nebula.requirements r
        JOIN descendants d ON r.parent_id = d.id
        WHERE d.depth < 10  -- safety limit
    )
    SELECT array_agg(id), max(depth)
    INTO v_all_descendants, v_depth
    FROM descendants;

    -- Count open questions (direct + inherited)
    SELECT count(*) INTO v_direct_open
    FROM nebula.open_questions
    WHERE requirement_id = p_requirement_id
    AND status = 'OPEN'
    AND blocking = true;

    -- Inherited: open questions on descendants
    IF v_all_descendants IS NOT NULL THEN
        SELECT count(*) INTO v_inherited_open
        FROM nebula.open_questions
        WHERE requirement_id = ANY(v_all_descendants)
        AND status = 'OPEN'
        AND blocking = true;
    END IF;

    -- Affected systems/subsystems (using nebula temporal tables)
    SELECT array_agg(DISTINCT sys.name), array_agg(DISTINCT sub.name)
    INTO v_affected_systems, v_affected_subsystems
    FROM nebula.requirements r
    LEFT JOIN nebula.systems_history sys ON sys.id = r.system_id 
        AND now() >= sys.recorded_on_dt AND now() < sys.recorded_until_dt
    LEFT JOIN nebula.subsystems_history sub ON sub.id = r.subsystem_id
        AND now() >= sub.recorded_on_dt AND now() < sub.recorded_until_dt
    WHERE r.id = p_requirement_id
       OR (v_all_descendants IS NOT NULL AND r.id = ANY(v_all_descendants));

    -- Related work requests (by plan_id which stores the requirement reference)
    SELECT count(*) INTO v_related_wrs
    FROM nebula.work_requests
    WHERE plan_id IS NOT NULL AND plan_id::text = p_requirement_id::text;

    -- Risk assessment
    IF v_inherited_open > 0 THEN
        v_risk := 'CRITICAL';
    ELSIF v_direct_open > 2 THEN
        v_risk := 'HIGH';
    ELSIF v_direct_open > 0 OR (v_all_descendants IS NOT NULL AND array_length(v_all_descendants, 1) > 5) THEN
        v_risk := 'MEDIUM';
    ELSE
        v_risk := 'LOW';
    END IF;

    -- Build suggested questions
    IF v_direct_open > 0 THEN
        v_suggested := v_suggested || jsonb_build_object(
            'question', 'This requirement has ' || v_direct_open || ' blocking open questions. Resolve before greenlighting.',
            'priority', 'HIGH'
        );
    END IF;

    IF v_inherited_open > 0 THEN
        v_suggested := v_suggested || jsonb_build_object(
            'question', 'Descendant requirements have ' || v_inherited_open || ' blocking questions that propagate up.',
            'priority', 'CRITICAL'
        );
    END IF;

    IF v_depth > 3 THEN
        v_suggested := v_suggested || jsonb_build_object(
            'question', 'Deep dependency chain (' || v_depth || ' levels). Consider decomposing into smaller requirements.',
            'priority', 'MEDIUM'
        );
    END IF;

    IF v_related_wrs > 0 THEN
        v_suggested := v_suggested || jsonb_build_object(
            'question', v_related_wrs || ' work requests already linked. Verify they are complete or cancelled before new implementation.',
            'priority', 'MEDIUM'
        );
    END IF;

    -- Build result
    v_result := jsonb_build_object(
        'requirement_id', p_requirement_id,
        'title', v_req.title,
        'current_status', v_req.status,
        'blast_radius', jsonb_build_object(
            'direct_children', COALESCE(array_length(v_children, 1), 0),
            'total_descendants', COALESCE(array_length(v_all_descendants, 1), 0),
            'max_depth', COALESCE(v_depth, 0)
        ),
        'questions', jsonb_build_object(
            'direct_open', v_direct_open,
            'inherited_open', v_inherited_open,
            'total_blocking', v_direct_open + v_inherited_open
        ),
        'systems_impact', jsonb_build_object(
            'systems', COALESCE(v_affected_systems, ARRAY[]::text[]),
            'subsystems', COALESCE(v_affected_subsystems, ARRAY[]::text[])
        ),
        'related_work_requests', v_related_wrs,
        'risk_level', v_risk,
        'can_greenlight', (v_direct_open = 0 AND v_inherited_open = 0),
        'suggested_questions', v_suggested,
        'assessed_at', now()
    );

    RETURN v_result;
END;
$$;


--
-- Name: FUNCTION assess_ripple(p_requirement_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.assess_ripple(p_requirement_id uuid) IS 'Ripple assessment for requirement transitions. Returns structured blast radius, risk level, and suggested questions for Planner evaluation.';


--
-- Name: assess_ripple_batch(uuid[]); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.assess_ripple_batch(p_requirement_ids uuid[]) RETURNS jsonb
    LANGUAGE plpgsql STABLE
    AS $$
DECLARE
    v_results jsonb := '[]'::jsonb;
    v_id uuid;
BEGIN
    FOREACH v_id IN ARRAY p_requirement_ids
    LOOP
        v_results := v_results || jsonb_build_object(
            'assessment', nebula.assess_ripple(v_id)
        );
    END LOOP;

    RETURN jsonb_build_object(
        'assessments', v_results,
        'count', jsonb_array_length(v_results),
        'assessed_at', now()
    );
END;
$$;


--
-- Name: FUNCTION assess_ripple_batch(p_requirement_ids uuid[]); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.assess_ripple_batch(p_requirement_ids uuid[]) IS 'Batch ripple assessment for multiple requirements. Used by Planner for priority evaluation.';


--
-- Name: audit_files_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.audit_files_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.audit_files_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION audit_files_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.audit_files_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: audit_files_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.audit_files_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.audit_files_history
        (id, file_path, content, size_bytes,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.file_path, NEW.content, NEW.size_bytes,
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION audit_files_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.audit_files_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: audit_files_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.audit_files_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.audit_files_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.audit_files_history
        (id, file_path, content, size_bytes,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.file_path, NEW.content, NEW.size_bytes,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, file_path, content, size_bytes,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION audit_files_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.audit_files_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: can_complete_requirement(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.can_complete_requirement(req_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN NOT EXISTS (
        SELECT 1 
        FROM nebula.open_questions 
        WHERE requirement_id = req_id 
          AND blocking = TRUE 
          AND status IN ('OPEN', 'IN_DELIBERATION')
    );
END;
$$;


--
-- Name: FUNCTION can_complete_requirement(req_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.can_complete_requirement(req_id uuid) IS 'Returns TRUE if requirement has no blocking open questions.';


--
-- Name: can_role_perform(text, text); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.can_role_perform(role_name text, action text) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 
        FROM nebula.roles r
        WHERE r.name = role_name
          AND (
            (action = 'greenlight' AND r.can_greenlight = TRUE)
            OR (action = 'create_questions' AND r.can_create_questions = TRUE)
            OR (action = 'create_agendas' AND r.can_create_agendas = TRUE)
            OR (action = 'resolve_questions' AND r.can_resolve_questions = TRUE)
            OR (action = 'verify_work_requests' AND r.can_verify_work_requests = TRUE)
          )
    );
END;
$$;


--
-- Name: FUNCTION can_role_perform(role_name text, action text); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.can_role_perform(role_name text, action text) IS 'Validates if a role can perform a specific action.';


--
-- Name: can_transition_status(uuid, text); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.can_transition_status(req_id uuid, new_status text) RETURNS TABLE(allowed boolean, reason text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    current_status TEXT;
    has_blocking BOOLEAN;
BEGIN
    -- Get current status
    SELECT status INTO current_status
    FROM nebula.requirements_history
    WHERE id = req_id
      AND now() >= recorded_on_dt 
      AND now() < recorded_until_dt
      AND now() >= valid_from 
      AND now() < valid_until;
    
    -- Check blocking questions
    SELECT nebula.has_open_questions(req_id) INTO has_blocking;
    
    -- Validate transition
    RETURN QUERY
    SELECT 
        CASE
            -- Backlog → To Do (always allowed)
            WHEN current_status = 'Backlog' AND new_status = 'To Do' THEN TRUE
            
            -- To Do → In Progress (only if no blocking questions)
            WHEN current_status = 'To Do' AND new_status = 'In Progress' AND NOT has_blocking THEN TRUE
            
            -- To Do → Blocked (always allowed - Planner creating agenda)
            WHEN current_status = 'To Do' AND new_status = 'Blocked' THEN TRUE
            
            -- Blocked → To Do (only if no blocking questions)
            WHEN current_status = 'Blocked' AND new_status = 'To Do' AND NOT has_blocking THEN TRUE
            
            -- In Progress → Done (only if no blocking questions)
            WHEN current_status = 'In Progress' AND new_status = 'Done' AND NOT has_blocking THEN TRUE
            
            -- In Progress → Blocked (new questions arise)
            WHEN current_status = 'In Progress' AND new_status = 'Blocked' THEN TRUE
            
            -- Same status (no-op)
            WHEN current_status = new_status THEN TRUE
            
            -- Everything else blocked
            ELSE FALSE
        END as allowed,
        CASE
            WHEN current_status IS NULL THEN 'Requirement not found'
            WHEN has_blocking AND new_status IN ('In Progress', 'Done') THEN 
                'Cannot transition to ' || new_status || ' with blocking open questions'
            ELSE 'Invalid transition from ' || COALESCE(current_status, 'NULL') || ' to ' || new_status
        END as reason;
END;
$$;


--
-- Name: FUNCTION can_transition_status(req_id uuid, new_status text); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.can_transition_status(req_id uuid, new_status text) IS 'Validates if a status transition is allowed given blocking questions.';


--
-- Name: candidate_surrounding_discourse(uuid, integer); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.candidate_surrounding_discourse(p_candidate_id uuid, p_context_units integer DEFAULT 2) RETURNS TABLE(harvest_title text, conversation_id uuid, turn_index integer, heading text, role text, block_index integer, block_type text, content text, items text[], is_match boolean, proximity_group integer)
    LANGUAGE sql STABLE
    AS $$
    WITH candidate AS (
        SELECT hc.harvest_id, hc.title AS candidate_title
        FROM nebula.harvest_candidates hc WHERE hc.id = p_candidate_id
    ),
    scored_units AS (
        SELECT
            (du_elem #>> '{provenance,turn_index}')::int AS turn_index,
            du_elem #>> '{heading}' AS heading,
            du_elem #>> '{provenance,role}' AS role,
            du_elem #>> '{body}' AS body,
            (SELECT count(*) FROM regexp_split_to_table(lower(c.candidate_title), E'\\s+') AS w
             WHERE lower(du_elem #>> '{body}') LIKE '%' || w || '%') AS relevance
        FROM candidate c, nebula.harvests h,
             LATERAL jsonb_array_elements(h.docklang -> 'discourse_units') AS du_elem
        WHERE h.id = c.harvest_id AND h.docklang IS NOT NULL
    ),
    matched AS (
        SELECT * FROM (
            SELECT *, (relevance > 0) AS is_match,
                CASE WHEN relevance > 0 THEN turn_index
                ELSE (SELECT min(m2.turn_index) FROM scored_units m2
                      WHERE m2.relevance > 0 AND m2.turn_index BETWEEN su.turn_index - p_context_units AND su.turn_index + p_context_units)
                END AS proximity_group
            FROM scored_units su
        ) sub WHERE proximity_group IS NOT NULL
    )
    SELECT
        h.docklang #>> '{meta,title}' AS harvest_title,
        NULLIF(h.docklang #>> '{meta,provenance,conversation_id}', '')::uuid AS conversation_id,
        m.turn_index, m.heading, m.role,
        (b #>> '{provenance,block_index}')::int AS block_index,
        b #>> '{type}' AS block_type,
        CASE WHEN b ? 'content' THEN b #>> '{content}' END AS content,
        CASE WHEN b ? 'items' THEN ARRAY(SELECT elem FROM jsonb_array_elements_text(b -> 'items') AS elem) END AS items,
        m.is_match, m.proximity_group
    FROM candidate c
    JOIN nebula.harvests h ON h.id = c.harvest_id
    JOIN matched m ON true
    CROSS JOIN LATERAL jsonb_array_elements(h.docklang -> 'discourse_units') AS du_elem
    CROSS JOIN LATERAL jsonb_array_elements(du_elem -> 'blocks') AS b
    WHERE (du_elem #>> '{provenance,turn_index}')::int = m.turn_index
    ORDER BY m.proximity_group, m.turn_index, (b #>> '{provenance,block_index}')::int;
$$;


--
-- Name: FUNCTION candidate_surrounding_discourse(p_candidate_id uuid, p_context_units integer); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.candidate_surrounding_discourse(p_candidate_id uuid, p_context_units integer) IS 'Returns discourse context around a candidate. Scans all units for mentions of candidate title, returns matches plus p_context_units of adjacent discourse.';


--
-- Name: candidates_to_agenda(uuid[], text, text); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.candidates_to_agenda(p_candidate_ids uuid[], p_project text DEFAULT 'nexus'::text, p_goal text DEFAULT NULL::text) RETURNS TABLE(agenda_id uuid, agenda_title text, candidates_used integer, status_results text[])
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_agenda_id      uuid;
    v_title          text;
    v_planner_analysis text;
    v_candidate_count integer;
    v_results        text[] := '{}';
    v_status_result  text;
    v_candidate_row  record;
BEGIN
    SELECT count(*) INTO v_candidate_count
    FROM nebula.harvest_candidates
    WHERE id = ANY(p_candidate_ids);

    IF v_candidate_count = 0 THEN
        RAISE EXCEPTION 'No candidates found for the given IDs';
    END IF;

    IF p_project IS NOT NULL AND p_project <> '' THEN
        v_title := p_project;
    ELSE
        SELECT string_agg(DISTINCT c.title, ' + ' ORDER BY c.title) INTO v_title
        FROM nebula.harvest_candidates c
        WHERE c.id = ANY(p_candidate_ids);
    END IF;

    IF length(v_title) > 200 THEN
        v_title := left(v_title, 197) || '...';
    END IF;

    -- Build planner_analysis from candidate intents (full text, no truncation)
    SELECT string_agg(
        format('- **%s**: %s',
            COALESCE(c.title, 'Untitled'),
            COALESCE(c.intent_description, 'No intent description')
        ),
        E'\n'
    ) INTO v_planner_analysis
    FROM nebula.harvest_candidates c
    WHERE c.id = ANY(p_candidate_ids);

    INSERT INTO nebula.agendas (title, scope, status, source_count, planner_analysis, metadata)
    VALUES (
        v_title,
        'harvest',
        'draft',
        v_candidate_count,
        v_planner_analysis,
        jsonb_build_object(
            'source', 'harvest_pipeline',
            'project', COALESCE(p_project, 'nexus'),
            'goal', p_goal,
            'candidate_ids', p_candidate_ids
        )
    )
    RETURNING id INTO v_agenda_id;

    FOR v_candidate_row IN
        SELECT c.id, c.title, c.intent_description, c.open_questions
        FROM nebula.harvest_candidates c
        WHERE c.id = ANY(p_candidate_ids)
    LOOP
        INSERT INTO nebula.agenda_items (
            agenda_id, source_type, source_id, title, body,
            open_questions, included
        ) VALUES (
            v_agenda_id,
            'harvest_candidate',
            v_candidate_row.id,
            v_candidate_row.title,
            v_candidate_row.intent_description,
            v_candidate_row.open_questions,
            true
        );

        INSERT INTO nebula.cross_references (source_type, source_id, target_type, target_id, rel_type, metadata)
        SELECT 'harvest_candidate', v_candidate_row.id::text, 'agenda', v_agenda_id::text, 'promotes_to', '{}'::jsonb
        WHERE NOT EXISTS (
            SELECT 1 FROM nebula.cross_references
            WHERE source_type = 'harvest_candidate'
              AND source_id = v_candidate_row.id::text
              AND target_type = 'agenda'
              AND target_id = v_agenda_id::text
              AND rel_type = 'promotes_to'
        );
    END LOOP;

    FOR v_status_result IN
        SELECT result FROM nebula.set_candidate_status_batch(p_candidate_ids, 'promoted')
    LOOP
        v_results := array_append(v_results, v_status_result);
    END LOOP;

    agenda_id := v_agenda_id;
    agenda_title := v_title;
    candidates_used := v_candidate_count;
    status_results := v_results;
    RETURN NEXT;
END;
$$;


--
-- Name: conversation_blocks_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.conversation_blocks_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.conversation_blocks_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION conversation_blocks_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.conversation_blocks_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: conversation_blocks_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.conversation_blocks_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.conversation_blocks_history
        (id, conversation_id, snapshot_id, block_index, parent_turn_id,
         parent_block_id, block_type, content_md, content_hash,
         dom_path, dom_fingerprint, first_line_no, last_line_no, created_at,
         role,
         as_of_dt, expiration_dt)
    VALUES
        (new_id, NEW.conversation_id, NEW.snapshot_id, NEW.block_index,
         NEW.parent_turn_id, NEW.parent_block_id,
         COALESCE(NEW.block_type, 'paragraph'),
         COALESCE(NEW.content_md, ''),
         COALESCE(NEW.content_hash, ''),
         NEW.dom_path, NEW.dom_fingerprint,
         NEW.first_line_no, NEW.last_line_no,
         COALESCE(NEW.created_at, NOW()),
         NEW.role,
         NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    RETURN NEW;
END;
$$;


--
-- Name: conversation_blocks_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.conversation_blocks_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.conversation_blocks_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.conversation_blocks_history
        (id, conversation_id, snapshot_id, block_index, parent_turn_id,
         parent_block_id, block_type, content_md, content_hash,
         dom_path, dom_fingerprint, first_line_no, last_line_no, created_at,
         role,
         as_of_dt, expiration_dt)
    VALUES
        (OLD.id, NEW.conversation_id, NEW.snapshot_id, NEW.block_index,
         NEW.parent_turn_id, NEW.parent_block_id,
         COALESCE(NEW.block_type, OLD.block_type),
         COALESCE(NEW.content_md, OLD.content_md),
         COALESCE(NEW.content_hash, OLD.content_hash),
         COALESCE(NEW.dom_path, OLD.dom_path),
         COALESCE(NEW.dom_fingerprint, OLD.dom_fingerprint),
         COALESCE(NEW.first_line_no, OLD.first_line_no),
         COALESCE(NEW.last_line_no, OLD.last_line_no),
         OLD.created_at,
         COALESCE(NEW.role, OLD.role),
         NOW(), '9999-12-31 23:59:59+00')
    RETURNING id, conversation_id, snapshot_id, block_index, parent_turn_id,
              parent_block_id, block_type, content_md, content_hash,
              dom_path, dom_fingerprint, first_line_no, last_line_no,
              created_at, role INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION conversation_blocks_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.conversation_blocks_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: conversation_snapshots_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.conversation_snapshots_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.conversation_snapshots_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION conversation_snapshots_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.conversation_snapshots_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: conversation_snapshots_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.conversation_snapshots_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    -- Orphan guard
    PERFORM nebula.assert_harvest_exists(NEW.conversation_id);

    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.conversation_snapshots_history
        (id, conversation_id, snapshot_index, source_hash, capture_mode,
         block_count, created_by, created_at, as_of_dt, expiration_dt)
    VALUES
        (new_id, NEW.conversation_id, NEW.snapshot_index, NEW.source_hash,
         COALESCE(NEW.capture_mode, 'AFTER_ACTION'),
         COALESCE(NEW.block_count, 0), COALESCE(NEW.created_by, 'SYSTEM'),
         COALESCE(NEW.created_at, NOW()), NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    RETURN NEW;
END;
$$;


--
-- Name: conversation_snapshots_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.conversation_snapshots_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.conversation_snapshots_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.conversation_snapshots_history
        (id, conversation_id, snapshot_index, source_hash, capture_mode,
         block_count, created_by, created_at, as_of_dt, expiration_dt)
    VALUES
        (OLD.id, NEW.conversation_id, NEW.snapshot_index, NEW.source_hash,
         COALESCE(NEW.capture_mode, OLD.capture_mode),
         COALESCE(NEW.block_count, OLD.block_count),
         COALESCE(NEW.created_by, OLD.created_by),
         OLD.created_at, NOW(), '9999-12-31 23:59:59+00')
    RETURNING id, conversation_id, snapshot_index, source_hash, capture_mode,
              block_count, created_by, created_at INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION conversation_snapshots_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.conversation_snapshots_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: create_questions_from_cpf(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.create_questions_from_cpf(p_candidate_id uuid) RETURNS jsonb
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_assessment jsonb;
    v_question jsonb;
    v_created int := 0;
BEGIN
    -- Get CPF assessment
    v_assessment := nebula.assess_cpf(p_candidate_id);
    
    -- Check if there are suggested questions
    IF v_assessment->'suggested_questions' IS NULL 
       OR jsonb_array_length(v_assessment->'suggested_questions') = 0 THEN
        RETURN jsonb_build_object(
            'message', 'No gaps identified - candidate is ready',
            'candidate_id', p_candidate_id,
            'score', v_assessment->>'score',
            'created', 0
        );
    END IF;
    
    -- Create open questions for each gap
    FOR v_question IN 
        SELECT * FROM jsonb_array_elements(v_assessment->'suggested_questions')
    LOOP
        INSERT INTO nebula.open_questions (
            requirement_id,  -- We'll link to the candidate's requirement if it exists
            title,
            description,
            category,
            status,
            blocking,
            created_by
        ) VALUES (
            NULL,  -- Candidate may not have a requirement yet
            v_question->>'question',
            'Auto-generated from CPF assessment. Component: ' || (v_question->>'component') || 
            '. Current score: ' || (v_assessment->'components'->>(v_question->>'component')),
            v_question->>'category',
            'OPEN',
            true,
            'planner'
        );
        
        v_created := v_created + 1;
    END LOOP;
    
    RETURN jsonb_build_object(
        'candidate_id', p_candidate_id,
        'title', v_assessment->>'title',
        'score', v_assessment->>'score',
        'questions_created', v_created,
        'created_at', now()
    );
END;
$$;


--
-- Name: FUNCTION create_questions_from_cpf(p_candidate_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.create_questions_from_cpf(p_candidate_id uuid) IS 'Auto-generate open questions from CPF assessment gaps. Used by Planner for automated backlog grooming.';


--
-- Name: features_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.features_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.features_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION features_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.features_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: features_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.features_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.features_history
        (id, subsystem_id, name, description, readme, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.subsystem_id, NEW.name, NEW.description, NEW.readme,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION features_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.features_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: features_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.features_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.features_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.features_history
        (id, subsystem_id, name, description, readme, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.subsystem_id, NEW.name, NEW.description, NEW.readme,
         OLD.created_at,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, subsystem_id, name, description, readme, created_at,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION features_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.features_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: get_blocking_questions(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.get_blocking_questions(req_id uuid) RETURNS TABLE(question_id uuid, question_title text, category text, status text, source_requirement_id uuid, source_requirement_title text, is_inherited boolean)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH RECURSIVE descendants AS (
        -- Start with the requirement itself
        SELECT id, title FROM nebula.requirements_history 
        WHERE id = req_id
        
        UNION ALL
        
        -- Recurse to children
        SELECT r.id, r.title
        FROM nebula.requirements_history r
        INNER JOIN descendants d ON r.parent_id = d.id
    )
    SELECT 
        oq.id as question_id,
        oq.title as question_title,
        oq.category,
        oq.status,
        oq.requirement_id as source_requirement_id,
        d.title as source_requirement_title,
        (oq.requirement_id != req_id) as is_inherited
    FROM nebula.open_questions oq
    INNER JOIN descendants d ON d.id = oq.requirement_id
    WHERE oq.blocking = TRUE 
      AND oq.status IN ('OPEN', 'IN_DELIBERATION')
    ORDER BY is_inherited, oq.created_at;
END;
$$;


--
-- Name: FUNCTION get_blocking_questions(req_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.get_blocking_questions(req_id uuid) IS 'Returns all blocking questions (direct + inherited from children).';


--
-- Name: get_requirement_readiness(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.get_requirement_readiness(req_id uuid) RETURNS TABLE(can_complete boolean, open_blocking integer, in_deliberation_blocking integer, total_blocking integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT 
        NOT EXISTS (
            SELECT 1 
            FROM nebula.open_questions 
            WHERE requirement_id = req_id 
              AND blocking = TRUE 
              AND status IN ('OPEN', 'IN_DELIBERATION')
        ) as can_complete,
        COUNT(*) FILTER (WHERE status = 'OPEN')::INTEGER as open_blocking,
        COUNT(*) FILTER (WHERE status = 'IN_DELIBERATION')::INTEGER as in_deliberation_blocking,
        COUNT(*)::INTEGER as total_blocking
    FROM nebula.open_questions 
    WHERE requirement_id = req_id 
      AND blocking = TRUE;
END;
$$;


--
-- Name: FUNCTION get_requirement_readiness(req_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.get_requirement_readiness(req_id uuid) IS 'Returns detailed breakdown of blocking questions for a requirement.';


--
-- Name: get_requirement_readiness_v2(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.get_requirement_readiness_v2(req_id uuid) RETURNS TABLE(can_complete boolean, direct_open integer, inherited_open integer, total_blocking integer, child_count integer)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    WITH RECURSIVE descendants AS (
        SELECT id FROM nebula.requirements_history 
        WHERE id = req_id
        
        UNION ALL
        
        SELECT r.id 
        FROM nebula.requirements_history r
        INNER JOIN descendants d ON r.parent_id = d.id
    )
    SELECT 
        NOT EXISTS (
            SELECT 1 
            FROM nebula.open_questions oq
            WHERE oq.requirement_id IN (SELECT id FROM descendants)
              AND oq.blocking = TRUE 
              AND oq.status IN ('OPEN', 'IN_DELIBERATION')
        ) as can_complete,
        COUNT(*) FILTER (WHERE oq.requirement_id = req_id)::INTEGER as direct_open,
        COUNT(*) FILTER (WHERE oq.requirement_id != req_id)::INTEGER as inherited_open,
        COUNT(*)::INTEGER as total_blocking,
        (SELECT COUNT(*) - 1 FROM descendants)::INTEGER as child_count
    FROM nebula.open_questions oq
    WHERE oq.requirement_id IN (SELECT id FROM descendants)
      AND oq.blocking = TRUE 
      AND oq.status IN ('OPEN', 'IN_DELIBERATION');
END;
$$;


--
-- Name: FUNCTION get_requirement_readiness_v2(req_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.get_requirement_readiness_v2(req_id uuid) IS 'Returns detailed breakdown including inherited questions from children.';


--
-- Name: get_unanswered_by_role(text, integer); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.get_unanswered_by_role(p_role text, p_limit integer DEFAULT 5) RETURNS TABLE(id uuid, title text, description text, category text, blocking boolean, requirement_id uuid, candidate_id uuid, created_by text)
    LANGUAGE plpgsql
    AS $$
BEGIN
  RETURN QUERY
    SELECT oq.id, oq.title, oq.description, oq.category,
           oq.blocking, oq.requirement_id, oq.candidate_id, oq.created_by
    FROM nebula.open_questions oq
    WHERE oq.status IN ('OPEN', 'IN_DELIBERATION')
      AND NOT EXISTS (
        SELECT 1 FROM nebula.open_question_answers oqa
        WHERE oqa.question_id = oq.id AND oqa.role = p_role
      )
    ORDER BY oq.blocking DESC, oq.created_at ASC
    LIMIT p_limit;
END;
$$;


--
-- Name: harvest_blocks(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvest_blocks(p_harvest_id uuid DEFAULT NULL::uuid) RETURNS TABLE(harvest_id uuid, harvest_title text, conversation_id uuid, turn_index integer, heading text, role text, block_index integer, block_type text, content text, items text[])
    LANGUAGE sql STABLE
    AS $$
    SELECT
        h.id                                                       AS harvest_id,
        h.docklang #>> '{meta,title}'                              AS harvest_title,
        NULLIF(h.docklang #>> '{meta,provenance,conversation_id}', '')::uuid
                                                                    AS conversation_id,
        (du #>> '{provenance,turn_index}')::integer                 AS turn_index,
        du #>> '{heading}'                                          AS heading,
        du #>> '{provenance,role}'                                  AS role,
        (b #>> '{provenance,block_index}')::integer                 AS block_index,
        b #>> '{type}'                                              AS block_type,
        CASE
            WHEN b ? 'content' THEN b #>> '{content}'
            ELSE NULL::text
        END                                                         AS content,
        CASE
            WHEN b ? 'items' THEN ARRAY(
                SELECT elem
                FROM jsonb_array_elements_text(b -> 'items') AS elem
            )
            ELSE NULL::text[]
        END                                                         AS items
    FROM nebula.harvests h
    CROSS JOIN LATERAL jsonb_array_elements(h.docklang -> 'discourse_units') AS du
    CROSS JOIN LATERAL jsonb_array_elements(du -> 'blocks') AS b
    WHERE h.docklang IS NOT NULL
      AND h.docklang != '{}'::jsonb
      AND (p_harvest_id IS NULL OR h.id = p_harvest_id);
$$;


--
-- Name: FUNCTION harvest_blocks(p_harvest_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.harvest_blocks(p_harvest_id uuid) IS 'Flattens docklang discourse_units blocks into a queryable table.
     Returns one row per block with harvest context.
     Block types: paragraph (content), list (items[]), quote (content),
     code (content), diagram (content), separator (no content/items).

     Usage:
       SELECT * FROM nebula.harvest_blocks();               -- all blocks
       SELECT * FROM nebula.harvest_blocks(''some-uuid'');  -- one harvest';


--
-- Name: harvest_references_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvest_references_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.harvest_references_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION harvest_references_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.harvest_references_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: harvest_references_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvest_references_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    -- Orphan guard
    PERFORM nebula.assert_harvest_exists(NEW.conversation_id);

    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.harvest_references_history
        (id, conversation_id, snapshot_id, source_block_id, source_segment_id,
         target_block_id, target_segment_id, edge_type, confidence, state,
         source, reason, evidence_json, provenance_json, created_by,
         created_at, as_of_dt, expiration_dt)
    VALUES
        (new_id, NEW.conversation_id, NEW.snapshot_id,
         NEW.source_block_id, NEW.source_segment_id,
         NEW.target_block_id, NEW.target_segment_id,
         COALESCE(NEW.edge_type, 'implicit'),
         COALESCE(NEW.confidence, 0.0000),
         COALESCE(NEW.state, 'CANDIDATE'),
         COALESCE(NEW.source, 'HARVEST'),
         NEW.reason, NEW.evidence_json, NEW.provenance_json,
         COALESCE(NEW.created_by, 'SYSTEM'),
         COALESCE(NEW.created_at, NOW()), NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION harvest_references_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.harvest_references_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: harvest_references_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvest_references_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.harvest_references_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.harvest_references_history
        (id, conversation_id, snapshot_id, source_block_id, source_segment_id,
         target_block_id, target_segment_id, edge_type, confidence, state,
         source, reason, evidence_json, provenance_json, created_by,
         created_at, as_of_dt, expiration_dt)
    VALUES
        (OLD.id, NEW.conversation_id, NEW.snapshot_id,
         NEW.source_block_id, NEW.source_segment_id,
         NEW.target_block_id, NEW.target_segment_id,
         COALESCE(NEW.edge_type, OLD.edge_type),
         COALESCE(NEW.confidence, OLD.confidence),
         COALESCE(NEW.state, OLD.state),
         COALESCE(NEW.source, OLD.source),
         COALESCE(NEW.reason, OLD.reason),
         COALESCE(NEW.evidence_json, OLD.evidence_json),
         COALESCE(NEW.provenance_json, OLD.provenance_json),
         COALESCE(NEW.created_by, OLD.created_by),
         OLD.created_at, NOW(), '9999-12-31 23:59:59+00')
    RETURNING id, conversation_id, snapshot_id, source_block_id,
              source_segment_id, target_block_id, target_segment_id,
              edge_type, confidence, state, source, reason,
              evidence_json, provenance_json, created_by, created_at INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION harvest_references_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.harvest_references_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: harvests_auto_segment_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvests_auto_segment_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    snapshot_id    UUID;
    unit_elem      jsonb;
    block_elem     jsonb;
    block_index    INTEGER := 0;
    total_blocks   INTEGER;
    source_hash    TEXT;
    block_content  TEXT;
    block_hash     TEXT;
BEGIN
    IF EXISTS (SELECT 1 FROM nebula.conversation_snapshots
               WHERE conversation_id = NEW.id) THEN
        RETURN NEW;
    END IF;

    SELECT COALESCE(sum(jsonb_array_length(du -> 'blocks')), 0)
    INTO   total_blocks
    FROM   jsonb_array_elements(NEW.docklang -> 'discourse_units') AS du;

    IF total_blocks = 0 THEN
        RETURN NEW;
    END IF;

    source_hash := encode(sha256(convert_to(NEW.docklang::text, 'UTF8')), 'hex');

    INSERT INTO nebula.conversation_snapshots
        (conversation_id, snapshot_index, source_hash, capture_mode,
         block_count, created_by)
    VALUES (NEW.id, 0, substring(source_hash, 1, 16), 'AFTER_ACTION',
            total_blocks, 'SYSTEM')
    RETURNING id INTO snapshot_id;

    FOR unit_elem IN
        SELECT * FROM jsonb_array_elements(NEW.docklang -> 'discourse_units')
    LOOP
        FOR block_elem IN
            SELECT * FROM jsonb_array_elements(unit_elem -> 'blocks')
        LOOP
            IF block_elem #>> '{content}' IS NOT NULL AND block_elem #>> '{content}' != '' THEN
                block_content := block_elem #>> '{content}';
            ELSIF block_elem ? 'items' AND jsonb_array_length(block_elem -> 'items') > 0 THEN
                SELECT string_agg('- ' || item, CHR(10))
                INTO   block_content
                FROM   jsonb_array_elements_text(block_elem -> 'items') AS item;
            ELSE
                block_content := '';
            END IF;

            block_hash := substring(
                encode(sha256(convert_to(COALESCE(block_content, ''), 'UTF8')), 'hex'),
                1, 16
            );

            INSERT INTO nebula.conversation_blocks
                (conversation_id, snapshot_id, block_index, parent_turn_id,
                 block_type, content_md, content_hash)
            VALUES (
                NEW.id,
                snapshot_id,
                block_index,
                unit_elem #>> '{heading}',
                COALESCE(block_elem #>> '{type}', 'paragraph'),
                block_content,
                block_hash
            );
            block_index := block_index + 1;
        END LOOP;
    END LOOP;

    RAISE NOTICE 'Auto-segment: snapshot % for harvest % (% blocks)',
        snapshot_id, NEW.id, total_blocks;

    RETURN NEW;
END;
$$;


--
-- Name: harvests_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvests_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.harvests_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION harvests_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.harvests_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: harvests_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvests_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
    next_ver INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    SELECT COALESCE(MAX(h.version), 0) + 1 INTO next_ver
      FROM nebula.harvests_history h
     WHERE h.source_path = NEW.source_path
       AND h.model = NEW.model
       AND h.recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.harvests_history
        (id, source_path, source_filename, model, total_candidates,
         candidates, source_text, tags, metadata, created_at,
         level, visibility_scope, docklang,
         source_hash, file_size, version, run_metadata,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.source_path, NEW.source_filename, NEW.model,
         NEW.total_candidates, NEW.candidates, NEW.source_text,
         NEW.tags, NEW.metadata, COALESCE(NEW.created_at, NOW()),
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NEW.docklang,
         COALESCE(NEW.source_hash, MD5(COALESCE(NEW.source_path, '') || COALESCE(NEW.model, ''))),
         NEW.file_size,
         COALESCE(NEW.version, next_ver),
         COALESCE(NEW.run_metadata, '{}'::JSONB),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.level := COALESCE(NEW.level, 1);
    NEW.visibility_scope := COALESCE(NEW.visibility_scope, 'all');
    NEW.source_hash := COALESCE(NEW.source_hash, MD5(COALESCE(NEW.source_path, '') || COALESCE(NEW.model, '')));
    NEW.version := COALESCE(NEW.version, next_ver);
    NEW.run_metadata := COALESCE(NEW.run_metadata, '{}'::JSONB);
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION harvests_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.harvests_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: harvests_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.harvests_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.harvests_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.harvests_history
        (id, source_path, source_filename, model, total_candidates,
         candidates, source_text, tags, metadata, created_at,
         level, visibility_scope, docklang,
         source_hash, file_size, version, run_metadata,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.source_path, NEW.source_filename, NEW.model,
         NEW.total_candidates, NEW.candidates, NEW.source_text,
         NEW.tags, NEW.metadata, OLD.created_at,
         COALESCE(NEW.level, 1), COALESCE(NEW.visibility_scope, 'all'),
         NEW.docklang,
         COALESCE(NEW.source_hash, OLD.source_hash),
         COALESCE(NEW.file_size, OLD.file_size),
         COALESCE(NEW.version, OLD.version),
         COALESCE(NEW.run_metadata, OLD.run_metadata),
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, source_path, source_filename, model, total_candidates,
              candidates, source_text, tags, metadata, created_at,
              level, visibility_scope, docklang,
              source_hash, file_size, version, run_metadata,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION harvests_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.harvests_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: has_open_questions(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.has_open_questions(req_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN EXISTS (
        WITH RECURSIVE descendants AS (
            -- Start with the requirement itself
            SELECT id FROM nebula.requirements_history 
            WHERE id = req_id
            
            UNION ALL
            
            -- Recurse to children
            SELECT r.id 
            FROM nebula.requirements_history r
            INNER JOIN descendants d ON r.parent_id = d.id
        )
        SELECT 1 
        FROM nebula.open_questions oq
        WHERE oq.requirement_id IN (SELECT id FROM descendants)
          AND oq.blocking = TRUE 
          AND oq.status IN ('OPEN', 'IN_DELIBERATION')
    );
END;
$$;


--
-- Name: FUNCTION has_open_questions(req_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.has_open_questions(req_id uuid) IS 'Recursively checks if requirement OR any descendant has blocking open questions.';


--
-- Name: is_fully_verified(uuid, uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.is_fully_verified(req_id uuid, wr_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN NOT EXISTS (
        SELECT 1 
        FROM nebula.requirement_verifications
        WHERE requirement_id = req_id
          AND work_request_id = wr_id
          AND status != 'APPROVED'
    );
END;
$$;


--
-- Name: FUNCTION is_fully_verified(req_id uuid, wr_id uuid); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.is_fully_verified(req_id uuid, wr_id uuid) IS 'Returns TRUE if all roles have approved the Work Request for a requirement.';


--
-- Name: notify_open_question_event(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.notify_open_question_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  -- Skip when fired by the answer-insert trigger updating answered_by (not a status change)
  IF (TG_OP = 'UPDATE') THEN
    IF (NEW.status = OLD.status AND NEW.answered_by IS DISTINCT FROM OLD.answered_by) THEN
      RETURN NEW;  -- only answered_by changed → answer insert side-effect, nothing to emit
    END IF;

    -- Fire when status changes to RESOLVED
    IF (NEW.status = 'RESOLVED' AND OLD.status IS DISTINCT FROM 'RESOLVED') THEN
      PERFORM pg_notify('open_question_resolved', json_build_object(
        'event_type', 'question.resolved',
        'question_id', NEW.id,
        'title', NEW.title,
        'category', NEW.category,
        'requirement_id', NEW.requirement_id,
        'candidate_id', NEW.candidate_id,
        'timestamp', NOW()
      )::text);
    END IF;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: notify_segment_expired(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.notify_segment_expired() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  affected_set_ids uuid[];
  payload jsonb;
BEGIN
  -- Only fire on the transition from current → expired
  IF OLD.expiration_dt = '9999-12-31 23:59:59+00'::timestamptz
     AND NEW.expiration_dt != '9999-12-31 23:59:59+00'::timestamptz THEN

    SELECT array_agg(DISTINCT ssm.segment_set_id)
    INTO affected_set_ids
    FROM nebula.segment_set_members ssm
    WHERE ssm.segment_id = NEW.id
      AND ssm.included = true;

    IF affected_set_ids IS NOT NULL AND array_length(affected_set_ids, 1) > 0 THEN
      payload := jsonb_build_object(
        'segment_id', NEW.id::text,
        'segment_set_ids', to_jsonb(affected_set_ids),
        'expired_at', now()
      );
      PERFORM pg_notify('segment_expired', payload::text);
    END IF;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: projection_overrides_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.projection_overrides_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.projection_overrides_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION projection_overrides_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.projection_overrides_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: projection_overrides_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.projection_overrides_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    -- Orphan guard
    PERFORM nebula.assert_harvest_exists(NEW.conversation_id);

    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.projection_overrides_history
        (id, conversation_id, snapshot_id, target_type, target_id,
         projection_target, override_type, reason_code, notes_md,
         source, created_by, created_at, as_of_dt, expiration_dt)
    VALUES
        (new_id, NEW.conversation_id, NEW.snapshot_id,
         COALESCE(NEW.target_type, 'BLOCK'), NEW.target_id,
         COALESCE(NEW.projection_target, 'BP'),
         COALESCE(NEW.override_type, 'EXCLUDE'),
         COALESCE(NEW.reason_code, 'USER_OVERRIDE'),
         NEW.notes_md,
         COALESCE(NEW.source, 'USER'),
         COALESCE(NEW.created_by, 'USER'),
         COALESCE(NEW.created_at, NOW()), NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION projection_overrides_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.projection_overrides_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: projection_overrides_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.projection_overrides_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.projection_overrides_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.projection_overrides_history
        (id, conversation_id, snapshot_id, target_type, target_id,
         projection_target, override_type, reason_code, notes_md,
         source, created_by, created_at, as_of_dt, expiration_dt)
    VALUES
        (OLD.id, NEW.conversation_id, NEW.snapshot_id,
         COALESCE(NEW.target_type, OLD.target_type),
         COALESCE(NEW.target_id, OLD.target_id),
         COALESCE(NEW.projection_target, OLD.projection_target),
         COALESCE(NEW.override_type, OLD.override_type),
         COALESCE(NEW.reason_code, OLD.reason_code),
         COALESCE(NEW.notes_md, OLD.notes_md),
         COALESCE(NEW.source, OLD.source),
         COALESCE(NEW.created_by, OLD.created_by),
         OLD.created_at, NOW(), '9999-12-31 23:59:59+00')
    RETURNING id, conversation_id, snapshot_id, target_type, target_id,
              projection_target, override_type, reason_code, notes_md,
              source, created_by, created_at INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION projection_overrides_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.projection_overrides_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: projections_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.projections_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.projections_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION projections_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.projections_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: projections_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.projections_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.projections_history
        (id, name, type, description, source_query, template,
         target_path, model, schedule, metadata, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.name, NEW.type, NEW.description, NEW.source_query,
         NEW.template, NEW.target_path, NEW.model, NEW.schedule,
         NEW.metadata, COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION projections_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.projections_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: projections_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.projections_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.projections_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.projections_history
        (id, name, type, description, source_query, template,
         target_path, model, schedule, metadata, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.name, NEW.type, NEW.description, NEW.source_query,
         NEW.template, NEW.target_path, NEW.model, NEW.schedule,
         NEW.metadata, OLD.created_at,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, name, type, description, source_query, template,
              target_path, model, schedule, metadata, created_at,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION projections_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.projections_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: record_answer(uuid, text, text, text, text); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.record_answer(p_question_id uuid, p_role text, p_answer text, p_confidence text DEFAULT 'MEDIUM'::text, p_reasoning text DEFAULT NULL::text) RETURNS TABLE(out_id uuid, out_question_id uuid, out_role text, out_answer text, out_confidence text, out_reasoning text, out_version integer, out_answered_at timestamp with time zone)
    LANGUAGE plpgsql
    AS $$
DECLARE
  next_version integer;
BEGIN
  UPDATE nebula.open_question_answers_history
     SET valid_until = now(), recorded_until_dt = now()
   WHERE question_id = p_question_id
     AND role = p_role
     AND valid_until > now();

  SELECT COALESCE(MAX(version), 0) + 1
    INTO next_version
    FROM nebula.open_question_answers_history
   WHERE question_id = p_question_id
     AND role = p_role;

  RETURN QUERY
  INSERT INTO nebula.open_question_answers_history
    (question_id, role, answer, confidence, reasoning, version,
     valid_from, recorded_on_dt)
  VALUES
    (p_question_id, p_role, p_answer, p_confidence, p_reasoning, next_version,
     now(), now())
  RETURNING id, question_id, role, answer, confidence, reasoning, version, answered_at;

  UPDATE nebula.open_questions_history
     SET answered_by = p_role,
         answered_at = now()
   WHERE id = p_question_id;

  PERFORM pg_notify('open_question_answered', json_build_object(
    'event_type', 'question.answered',
    'question_id', p_question_id,
    'role', p_role,
    'version', next_version,
    'timestamp', now()
  )::text);
END;
$$;


--
-- Name: requirements_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.requirements_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.requirements_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION requirements_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.requirements_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: requirements_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.requirements_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());
    INSERT INTO nebula.requirements_history
        (id, system_id, subsystem_id, feature_id, title, description,
         status, priority, start_date, completion_date, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until,
         parent_id, req_type, acceptance_criteria, candidate_id, conduit_plan_id)
    VALUES
        (new_id, NEW.system_id, NEW.subsystem_id, NEW.feature_id,
         NEW.title, NEW.description, NEW.status, NEW.priority,
         NEW.start_date, NEW.completion_date,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()),
         COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'),
         NEW.parent_id, NEW.req_type, NEW.acceptance_criteria, NEW.candidate_id,
         NEW.conduit_plan_id)
    RETURNING * INTO NEW;
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION requirements_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.requirements_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: requirements_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.requirements_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- Close current version
    UPDATE nebula.requirements_history
    SET recorded_until_dt = NOW()
    WHERE id = OLD.id
      AND recorded_until_dt = '9999-12-31 23:59:59+00';
    -- Insert new version
    INSERT INTO nebula.requirements_history
        (id, system_id, subsystem_id, feature_id, title, description,
         status, priority, start_date, completion_date, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until,
         parent_id, req_type, acceptance_criteria, candidate_id, conduit_plan_id)
    VALUES
        (OLD.id,
         COALESCE(NEW.system_id, OLD.system_id),
         COALESCE(NEW.subsystem_id, OLD.subsystem_id),
         COALESCE(NEW.feature_id, OLD.feature_id),
         COALESCE(NEW.title, OLD.title),
         COALESCE(NEW.description, OLD.description),
         COALESCE(NEW.status, OLD.status),
         COALESCE(NEW.priority, OLD.priority),
         COALESCE(NEW.start_date, OLD.start_date),
         COALESCE(NEW.completion_date, OLD.completion_date),
         OLD.created_at,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until,
         COALESCE(NEW.parent_id, OLD.parent_id),
         COALESCE(NEW.req_type, OLD.req_type),
         COALESCE(NEW.acceptance_criteria, OLD.acceptance_criteria),
         COALESCE(NEW.candidate_id, OLD.candidate_id),
         COALESCE(NEW.conduit_plan_id, OLD.conduit_plan_id))
    RETURNING * INTO NEW;
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION requirements_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.requirements_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: roles_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.roles_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.roles_history
       SET valid_until = NOW()
     WHERE id = OLD.id
       AND valid_until = '9999-12-31 00:00:00+00';
    RETURN OLD;
END;
$$;


--
-- Name: segment_harvest(uuid); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.segment_harvest(p_harvest_id uuid) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    snapshot_id    UUID;
    unit_elem      jsonb;
    block_elem     jsonb;
    block_index    INTEGER := 0;
    total_blocks   INTEGER;
    source_hash    TEXT;
    block_content  TEXT;
    block_hash     TEXT;
    block_role     TEXT;
    docklang       jsonb;
BEGIN
    -- Load docklang for the given harvest
    SELECT h.docklang INTO docklang
    FROM nebula.harvests h
    WHERE h.id = p_harvest_id;

    IF docklang IS NULL OR NOT (docklang ? 'discourse_units') THEN
        RETURN NULL;
    END IF;

    -- ── Idempotency: skip if snapshot already exists ──────────────
    SELECT id INTO snapshot_id
    FROM nebula.conversation_snapshots
    WHERE conversation_id = p_harvest_id
    LIMIT 1;
    IF snapshot_id IS NOT NULL THEN
        RETURN snapshot_id;
    END IF;

    -- ── Count total blocks ────────────────────────────────────────
    SELECT COALESCE(sum(jsonb_array_length(du -> 'blocks')), 0)
    INTO   total_blocks
    FROM   jsonb_array_elements(docklang -> 'discourse_units') AS du;

    IF total_blocks = 0 THEN
        RETURN NULL;
    END IF;

    -- ── Compute source hash from full docklang ────────────────────
    source_hash := encode(sha256(convert_to(docklang::text, 'UTF8')), 'hex');

    -- ── Create snapshot (conversation_id = harvest id) ────────────
    INSERT INTO nebula.conversation_snapshots
        (conversation_id, snapshot_index, source_hash, capture_mode,
         block_count, created_by)
    VALUES (p_harvest_id, 0, substring(source_hash, 1, 16), 'AFTER_ACTION',
            total_blocks, 'SYSTEM')
    RETURNING id INTO snapshot_id;

    -- ── Insert blocks from each discourse unit ────────────────────
    FOR unit_elem IN
        SELECT * FROM jsonb_array_elements(docklang -> 'discourse_units')
    LOOP
        block_role := unit_elem #>> '{provenance,role}';

        FOR block_elem IN
            SELECT * FROM jsonb_array_elements(unit_elem -> 'blocks')
        LOOP
            IF block_elem #>> '{content}' IS NOT NULL AND block_elem #>> '{content}' != '' THEN
                block_content := block_elem #>> '{content}';
            ELSIF block_elem ? 'items' AND jsonb_array_length(block_elem -> 'items') > 0 THEN
                SELECT string_agg('- ' || item, CHR(10))
                INTO   block_content
                FROM   jsonb_array_elements_text(block_elem -> 'items') AS item;
            ELSE
                block_content := '';
            END IF;

            block_hash := substring(
                encode(sha256(convert_to(COALESCE(block_content, ''), 'UTF8')), 'hex'),
                1, 16
            );

            INSERT INTO nebula.conversation_blocks
                (conversation_id, snapshot_id, block_index, parent_turn_id,
                 block_type, content_md, content_hash, role)
            VALUES (
                p_harvest_id,
                snapshot_id,
                block_index,
                unit_elem #>> '{heading}',
                COALESCE(block_elem #>> '{type}', 'paragraph'),
                block_content,
                block_hash,
                block_role
            );
            block_index := block_index + 1;
        END LOOP;
    END LOOP;

    RAISE NOTICE 'segment_harvest: snapshot % for harvest % (% blocks)',
        snapshot_id, p_harvest_id, total_blocks;

    RETURN snapshot_id;
END;
$$;


--
-- Name: segments_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.segments_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.segments_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION segments_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.segments_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: segments_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.segments_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    -- Orphan guard
    PERFORM nebula.assert_harvest_exists(NEW.conversation_id);

    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.segments_history
        (id, conversation_id, snapshot_id, start_block_id, end_block_id,
         start_block_index, end_block_index, segment_type, state, source,
         title, notes_md, created_by, created_at, as_of_dt, expiration_dt)
    VALUES
        (new_id, NEW.conversation_id, NEW.snapshot_id,
         NEW.start_block_id, NEW.end_block_id,
         NEW.start_block_index, NEW.end_block_index,
         NEW.segment_type,
         COALESCE(NEW.state, 'PROPOSED'),
         COALESCE(NEW.source, 'USER'),
         NEW.title, NEW.notes_md,
         COALESCE(NEW.created_by, 'USER'),
         COALESCE(NEW.created_at, NOW()), NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION segments_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.segments_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: segments_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.segments_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.segments_history
    SET    expiration_dt = NOW()
    WHERE  id = OLD.id AND expiration_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.segments_history
        (id, conversation_id, snapshot_id, start_block_id, end_block_id,
         start_block_index, end_block_index, segment_type, state, source,
         title, notes_md, created_by, created_at, as_of_dt, expiration_dt)
    VALUES
        (OLD.id, NEW.conversation_id, NEW.snapshot_id,
         NEW.start_block_id, NEW.end_block_id,
         NEW.start_block_index, NEW.end_block_index,
         COALESCE(NEW.segment_type, OLD.segment_type),
         COALESCE(NEW.state, OLD.state),
         COALESCE(NEW.source, OLD.source),
         COALESCE(NEW.title, OLD.title),
         COALESCE(NEW.notes_md, OLD.notes_md),
         COALESCE(NEW.created_by, OLD.created_by),
         OLD.created_at, NOW(), '9999-12-31 23:59:59+00')
    RETURNING id, conversation_id, snapshot_id, start_block_id, end_block_id,
              start_block_index, end_block_index, segment_type, state, source,
              title, notes_md, created_by, created_at INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION segments_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.segments_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: set_candidate_status(uuid, text); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.set_candidate_status(p_candidate_id uuid, p_new_status text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_current_status text;
BEGIN
    SELECT status INTO v_current_status FROM nebula.harvest_candidates WHERE id = p_candidate_id;
    IF v_current_status IS NULL THEN
        RAISE EXCEPTION 'Candidate % not found', p_candidate_id;
    END IF;
    IF v_current_status = 'pending' AND p_new_status NOT IN ('linked', 'useful', 'rejected') THEN
        RAISE EXCEPTION 'Invalid: pending -> % (expected linked, useful, or rejected)', p_new_status;
    END IF;
    IF v_current_status = 'linked' AND p_new_status NOT IN ('useful', 'rejected') THEN
        RAISE EXCEPTION 'Invalid: linked -> % (expected useful or rejected)', p_new_status;
    END IF;
    IF v_current_status = 'useful' AND p_new_status NOT IN ('promoted', 'rejected') THEN
        RAISE EXCEPTION 'Invalid: useful -> % (expected promoted or rejected)', p_new_status;
    END IF;
    IF v_current_status IN ('promoted', 'rejected') THEN
        RAISE EXCEPTION 'Already terminal: %', v_current_status;
    END IF;
    UPDATE nebula.harvest_candidates SET status = p_new_status, updated_at = now() WHERE id = p_candidate_id;
    RETURN format('Candidate %s: %s -> %s', p_candidate_id, v_current_status, p_new_status);
END;
$$;


--
-- Name: FUNCTION set_candidate_status(p_candidate_id uuid, p_new_status text); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.set_candidate_status(p_candidate_id uuid, p_new_status text) IS 'Set candidate status with transition validation.';


--
-- Name: set_candidate_status_batch(uuid[], text); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.set_candidate_status_batch(p_candidate_ids uuid[], p_new_status text) RETURNS TABLE(candidate_id uuid, result text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_id uuid;
BEGIN
    FOREACH v_id IN ARRAY p_candidate_ids
    LOOP
        BEGIN
            candidate_id := v_id;
            result := nebula.set_candidate_status(v_id, p_new_status);
            RETURN NEXT;
        EXCEPTION WHEN OTHERS THEN
            candidate_id := v_id;
            result := format('ERROR: %s', SQLERRM);
            RETURN NEXT;
        END;
    END LOOP;
END;
$$;


--
-- Name: subsystems_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.subsystems_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.subsystems_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION subsystems_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.subsystems_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: subsystems_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.subsystems_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.subsystems_history
        (id, system_id, name, description, readme, color, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.system_id, NEW.name, NEW.description, NEW.readme, NEW.color,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION subsystems_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.subsystems_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: subsystems_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.subsystems_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.subsystems_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.subsystems_history
        (id, system_id, name, description, readme, color, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.system_id, NEW.name, NEW.description, NEW.readme,
         NEW.color, OLD.created_at,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, system_id, name, description, readme, color, created_at,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION subsystems_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.subsystems_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_folders_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_folders_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.system_folders_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION system_folders_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_folders_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_folders_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_folders_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.system_folders_history
        (id, system_id, name, category, note,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.system_id, NEW.name, NEW.category, NEW.note,
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION system_folders_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_folders_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_folders_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_folders_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.system_folders_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.system_folders_history
        (id, system_id, name, category, note,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.system_id, NEW.name, NEW.category, NEW.note,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, system_id, name, category, note,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION system_folders_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_folders_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_info_tabs_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_info_tabs_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.system_info_tabs_history
    SET    recorded_until_dt = NOW()
    WHERE  system_id = OLD.system_id AND tab_id = OLD.tab_id
      AND  recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION system_info_tabs_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_info_tabs_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_info_tabs_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_info_tabs_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO nebula.system_info_tabs_history
        (system_id, tab_id, content,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (NEW.system_id, NEW.tab_id, NEW.content,
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION system_info_tabs_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_info_tabs_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_info_tabs_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_info_tabs_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.system_info_tabs_history
    SET    recorded_until_dt = NOW()
    WHERE  system_id = OLD.system_id AND tab_id = OLD.tab_id
      AND  recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.system_info_tabs_history
        (system_id, tab_id, content,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.system_id, OLD.tab_id, NEW.content,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING system_id, tab_id, content,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION system_info_tabs_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_info_tabs_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_workspaces_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_workspaces_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.system_workspaces_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION system_workspaces_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_workspaces_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_workspaces_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_workspaces_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.system_workspaces_history
        (id, system_id, subsystem_id, workspace_path, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.system_id, NEW.subsystem_id, NEW.workspace_path,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION system_workspaces_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_workspaces_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: system_workspaces_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.system_workspaces_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.system_workspaces_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.system_workspaces_history
        (id, system_id, subsystem_id, workspace_path, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.system_id, NEW.subsystem_id, NEW.workspace_path,
         OLD.created_at,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, system_id, subsystem_id, workspace_path, created_at,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION system_workspaces_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.system_workspaces_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: systems_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.systems_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.systems_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION systems_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.systems_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: systems_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.systems_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.systems_history
        (id, name, description, readme, architecture, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.name, NEW.description, NEW.readme, NEW.architecture,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION systems_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.systems_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: systems_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.systems_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.systems_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.systems_history
        (id, name, description, readme, architecture, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.name, NEW.description, NEW.readme, NEW.architecture,
         OLD.created_at,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, name, description, readme, architecture, created_at,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION systems_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.systems_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: update_updated_at(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.update_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
      BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
      END;
      $$;


--
-- Name: FUNCTION update_updated_at(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.update_updated_at() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: user_preferences_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.user_preferences_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.user_preferences_history
    SET    recorded_until_dt = NOW()
    WHERE  user_id = OLD.user_id AND key = OLD.key
      AND  recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION user_preferences_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.user_preferences_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: user_preferences_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.user_preferences_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    INSERT INTO nebula.user_preferences_history
        (user_id, key, value,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (NEW.user_id, NEW.key, NEW.value,
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION user_preferences_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.user_preferences_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: user_preferences_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.user_preferences_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.user_preferences_history
    SET    recorded_until_dt = NOW()
    WHERE  user_id = OLD.user_id AND key = OLD.key
      AND  recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.user_preferences_history
        (user_id, key, value,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.user_id, OLD.key, NEW.value,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING user_id, key, value,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION user_preferences_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.user_preferences_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: validate_opcode_template(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.validate_opcode_template() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_op           TEXT;
    v_entry        JSONB;
    v_valid_ops    TEXT[] := ARRAY[
        -- Filesystem
        'CREATE_DIR', 'DELETE_DIR', 'MOVE_PATH', 'COPY_PATH',
        'WRITE_FILE', 'APPEND_FILE', 'READ_FILE', 'RENAME_PATH',
        -- Environment
        'INIT_VENV', 'INSTALL_DEPENDENCIES', 'SET_ENV_VAR',
        'CONFIGURE_RUNTIME', 'SELECT_PYTHON_VERSION', 'RUN_SHELL_COMMAND',
        -- Code construction
        'CREATE_MODULE', 'WRITE_SOURCE_FILE', 'APPLY_TEMPLATE',
        'GENERATE_CLASS', 'GENERATE_FUNCTION', 'PATCH_FILE',
        -- Service registration
        'REGISTER_SERVICE', 'UPDATE_SERVICE_REGISTRY', 'CONFIGURE_ROUTE',
        'DEFINE_ENDPOINT', 'BIND_PORT', 'DEPLOY_SERVICE',
        -- Validation
        'VALIDATE_SYNTAX', 'CHECK_DEPENDENCIES', 'RUN_TYPECHECK',
        'RUN_TEST_SUITE', 'VERIFY_SCHEMA', 'DRY_RUN_EXECUTION',
        -- Event / observability
        'EMIT_EVENT', 'LOG_ARTIFACT', 'REGISTER_TRACEPOINT', 'PUBLISH_STATE'
    ];
BEGIN
    -- Only validate if opcode_template is a JSON array
    IF jsonb_typeof(NEW.opcode_template) != 'array' THEN
        RAISE EXCEPTION 'opcode_template must be a JSON array, got %', jsonb_typeof(NEW.opcode_template);
    END IF;

    -- Iterate over each entry in the template
    FOR v_entry IN SELECT * FROM jsonb_array_elements(NEW.opcode_template)
    LOOP
        v_op := v_entry->>'op';
        IF v_op IS NULL THEN
            RAISE EXCEPTION 'Each template entry must have an "op" field';
        END IF;

        -- Check opcode is in the valid ISA set
        IF NOT (v_op = ANY(v_valid_ops)) THEN
            RAISE EXCEPTION 'Invalid opcode "%" in template entry. Must be one of: %',
                v_op, array_to_string(v_valid_ops, ', ');
        END IF;

        -- Check that target is present
        IF (v_entry->>'target') IS NULL OR (v_entry->>'target') = '' THEN
            RAISE EXCEPTION 'Template entry for opcode "%" is missing a "target" field', v_op;
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION validate_opcode_template(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.validate_opcode_template() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: work_sessions_delete_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.work_sessions_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE nebula.work_sessions_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION work_sessions_delete_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.work_sessions_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: work_sessions_insert_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.work_sessions_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id UUID;
BEGIN
    new_id := COALESCE(NEW.id, gen_random_uuid());

    INSERT INTO nebula.work_sessions_history
        (id, parent_id, parent_type, parent_name, context, platform,
         model, outcome, status, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (new_id, NEW.parent_id, NEW.parent_type, NEW.parent_name,
         NEW.context, NEW.platform, NEW.model, NEW.outcome, NEW.status,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00',
         COALESCE(NEW.valid_from, NOW()), COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00'));

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    NEW.valid_from := COALESCE(NEW.valid_from, NOW());
    NEW.valid_until := COALESCE(NEW.valid_until, '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION work_sessions_insert_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.work_sessions_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: work_sessions_update_trigger(); Type: FUNCTION; Schema: nebula; Owner: -
--

CREATE FUNCTION nebula.work_sessions_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    UPDATE nebula.work_sessions_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';

    INSERT INTO nebula.work_sessions_history
        (id, parent_id, parent_type, parent_name, context, platform,
         model, outcome, status, created_at,
         recorded_on_dt, recorded_until_dt, valid_from, valid_until)
    VALUES
        (OLD.id, NEW.parent_id, NEW.parent_type, NEW.parent_name,
         NEW.context, NEW.platform, NEW.model, NEW.outcome, NEW.status,
         OLD.created_at,
         NOW(), '9999-12-31 23:59:59+00',
         OLD.valid_from, OLD.valid_until)
    RETURNING id, parent_id, parent_type, parent_name,
              context, platform, model, outcome, status, created_at,
              recorded_on_dt, recorded_until_dt, valid_from, valid_until INTO r;

    RETURN r;
END;
$$;


--
-- Name: FUNCTION work_sessions_update_trigger(); Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON FUNCTION nebula.work_sessions_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: check_receipt_integrity(); Type: FUNCTION; Schema: peb; Owner: -
--

CREATE FUNCTION peb.check_receipt_integrity() RETURNS TABLE(kind text, entity_id text, detail text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    -- 1. Decision with no matching transaction
    RETURN QUERY
    SELECT 'DECISION_ORPHAN_TRANSACTION'::text, d.id::text,
        format('decision %s references transaction %s which does not exist', d.id, d.transaction_id)
    FROM peb.decisions d
    LEFT JOIN peb.transactions t ON t.id = d.transaction_id
    WHERE d.transaction_id IS NOT NULL AND t.id IS NULL;

    -- 2. Trace with no matching transaction
    RETURN QUERY
    SELECT 'TRACE_ORPHAN_TRANSACTION'::text, tr.id::text,
        format('trace %s references transaction %s which does not exist', tr.id, tr.transaction_id)
    FROM peb.traces tr
    LEFT JOIN peb.transactions t ON t.id = tr.transaction_id
    WHERE tr.transaction_id IS NOT NULL AND t.id IS NULL;

    -- 3. Violation with no matching transaction
    RETURN QUERY
    SELECT 'VIOLATION_ORPHAN_TRANSACTION'::text, v.id::text,
        format('violation %s references transaction %s which does not exist', v.id, v.transaction_id)
    FROM peb.violations v
    LEFT JOIN peb.transactions t ON t.id = v.transaction_id
    WHERE v.transaction_id IS NOT NULL AND t.id IS NULL;

    -- 4. Transaction committed but no decision (may be informational)
    RETURN QUERY
    SELECT 'TRANSACTION_NO_DECISION'::text, t.id::text,
        format('transaction %s (tool=%s) committed at %s has no associated decision', 
               t.id, t.tool_name, t.committed_at)
    FROM peb.transactions t
    WHERE t.committed_at IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM peb.decisions d WHERE d.transaction_id = t.id);

    -- 5. Governance event references non-existent vision.work_requests
    RETURN QUERY
    SELECT 'GOVERNANCE_EVENT_ORPHAN_WORK_REQUEST'::text, ge.plan_id::text,
        format('governance_event %s references work_request_id %s which does not exist in vision.work_requests',
               ge.receipt_id, ge.work_request_id)
    FROM peb.governance_events ge
    LEFT JOIN vision.work_requests wr ON wr.work_request_uuid = ge.work_request_id
    WHERE ge.work_request_id IS NOT NULL AND wr.id IS NULL;
END;
$$;


--
-- Name: forbid_binding_decision_mutation(); Type: FUNCTION; Schema: peb; Owner: -
--

CREATE FUNCTION peb.forbid_binding_decision_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'peb.binding_decision_evidence is append-only: % blocked for decision %', TG_OP, OLD.decision_id
        USING ERRCODE = 'restrict_violation';
END;
$$;


--
-- Name: notify_governance_event(); Type: FUNCTION; Schema: peb; Owner: -
--

CREATE FUNCTION peb.notify_governance_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM pg_notify('peb_governance_event_created', (
        jsonb_build_object(
            'event_id',       NEW.id,
            'event_type',     NEW.event_type,
            'work_request_id', NEW.work_request_id,
            'plan_id',        NEW.plan_id,
            'agent_role',     NEW.agent_role,
            'timestamp',      NEW.created_at,
            'aggregate_type', 'governance',
            'aggregate_id',   NEW.receipt_id
        ) || NEW.payload
    )::text);

    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION notify_governance_event(); Type: COMMENT; Schema: peb; Owner: -
--

COMMENT ON FUNCTION peb.notify_governance_event() IS 'Bridges peb.governance_events onto the peb_governance_event_created
     NOTIFY channel so obs_subscriber publishes them to NATS.';


--
-- Name: admit_and_record(uuid, text, text, text, jsonb, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.admit_and_record(p_transaction_id uuid, p_idempotency_key text, p_entity_id text, p_tool_name text, p_input jsonb, p_state_transition_id uuid) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_concept_name      text;
    v_check_entity_id   uuid;
    v_check             RECORD;
    v_admission_result  text;
BEGIN
    SELECT c.name INTO v_concept_name
    FROM resolution.concept_state_transition cst
    JOIN resolution.concept c ON c.id = cst.concept_id
    WHERE cst.id = p_state_transition_id;
    IF v_concept_name IS NULL THEN
        RAISE EXCEPTION 'no concept_state_transition for id %', p_state_transition_id;
    END IF;

    v_check_entity_id := resolution.resolve_entity_uuid(p_entity_id, v_concept_name);

    SELECT * INTO v_check FROM resolution.check_transition_guard(p_state_transition_id, v_check_entity_id);
    v_admission_result := CASE WHEN v_check.admitted THEN 'ADMITTED' ELSE 'REJECTED' END;

    INSERT INTO peb.transactions (id, idempotency_key, entity_id, admission_result, tool_name, input, created_at)
    VALUES (p_transaction_id, p_idempotency_key, p_entity_id, v_admission_result, p_tool_name, p_input, now());

    IF NOT v_check.admitted THEN
        INSERT INTO peb.violations (id, transaction_id, violation_type, severity, entity_id, context, resolution, created_at)
        VALUES (gen_random_uuid(), p_transaction_id,
                CASE v_check.rule_type WHEN 'invariant' THEN 'INVARIANT_VIOLATED' ELSE 'GUARD_FAILED' END,
                'hard', p_entity_id,
                jsonb_build_object('rule_name', v_check.rule_name, 'rule_type', v_check.rule_type,
                                    'reason', v_check.reason, 'compiled_sql', v_check.compiled_sql),
                'rejected', now());
    END IF;

    RETURN v_admission_result;
END;
$$;


--
-- Name: admit_verified_execution_claim(uuid, uuid, uuid, text, text, text, text, text, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.admit_verified_execution_claim(p_peb_transaction_id uuid, p_claim_id uuid, p_evidence_id uuid, p_policy_version_hash text, p_lease_id text, p_grant_id text, p_attempt_id text, p_source_system text DEFAULT 'git-verifier'::text, p_evidence_kind text DEFAULT 'git_ref_commit'::text) RETURNS TABLE(admitted boolean, reason text, receipt_id uuid)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_claim                 resolution.execution_claim%ROWTYPE;
    v_evidence              resolution.execution_evidence%ROWTYPE;
    v_link                  resolution.execution_claim_evidence%ROWTYPE;
    v_existing              resolution.execution_admission_receipt%ROWTYPE;
    v_admitted               boolean := true;
    v_reason                 text := 'verified Git evidence is eligible for PEB admission';
    v_receipt_id             uuid := gen_random_uuid();
BEGIN
    IF p_peb_transaction_id IS NULL OR p_claim_id IS NULL OR p_evidence_id IS NULL
       OR p_policy_version_hash IS NULL OR p_lease_id IS NULL
       OR p_grant_id IS NULL OR p_attempt_id IS NULL THEN
        RETURN QUERY SELECT false, 'MISSING_EXECUTION_ADMISSION_CONTEXT', NULL::uuid;
        RETURN;
    END IF;

    -- Idempotent replay is allowed only for the exact same correlation. A
    -- transaction id may never be reused to smuggle a different claim or
    -- evidence into the admission ledger.
    SELECT * INTO v_existing
    FROM resolution.execution_admission_receipt
    WHERE peb_transaction_id = p_peb_transaction_id;

    IF FOUND THEN
        IF v_existing.claim_id = p_claim_id
           AND v_existing.evidence_id = p_evidence_id
           AND v_existing.policy_version_hash = p_policy_version_hash
           AND v_existing.lease_id = p_lease_id
           AND v_existing.grant_id = p_grant_id
           AND v_existing.attempt_id = p_attempt_id
           AND v_existing.source_system = p_source_system
           AND v_existing.evidence_kind = p_evidence_kind THEN
            RETURN QUERY SELECT v_existing.admitted, v_existing.reason, v_existing.id;
        END IF;
        RETURN QUERY SELECT false, 'CONFLICTING_EXECUTION_ADMISSION_REPLAY', NULL::uuid;
        RETURN;
    END IF;

    SELECT * INTO v_claim
    FROM resolution.execution_claim
    WHERE id = p_claim_id;

    SELECT * INTO v_evidence
    FROM resolution.execution_evidence
    WHERE id = p_evidence_id;

    SELECT ce.* INTO v_link
    FROM resolution.execution_claim_evidence ce
    WHERE ce.claim_id = p_claim_id
      AND ce.evidence_id = p_evidence_id
      AND ce.role = 'supports'
      AND ce.verification_state = 'confirmed'
      AND ce.expired_at IS NULL
    ORDER BY ce.linked_at DESC
    LIMIT 1;

    IF NOT FOUND OR v_claim.id IS NULL OR v_evidence.id IS NULL THEN
        v_admitted := false;
        v_reason := 'CLAIM_EVIDENCE_LINK_NOT_CONFIRMED';
    ELSIF v_claim.disposition IN ('Rejected', 'Disputed', 'Stale', 'Retracted') THEN
        v_admitted := false;
        v_reason := 'CLAIM_SEMANTIC_DISPOSITION_NOT_ADMISSIBLE';
    ELSIF v_evidence.context_kind <> 'execution'
       OR v_evidence.policy_version_hash IS NULL
       OR v_evidence.lease_id IS NULL
       OR v_evidence.grant_id IS NULL
       OR v_evidence.attempt_id IS NULL THEN
        v_admitted := false;
        v_reason := 'EVIDENCE_MISSING_EXECUTION_CONTEXT';
    ELSIF v_evidence.policy_version_hash IS DISTINCT FROM p_policy_version_hash
       OR v_evidence.lease_id IS DISTINCT FROM p_lease_id
       OR v_evidence.grant_id IS DISTINCT FROM p_grant_id
       OR v_evidence.attempt_id IS DISTINCT FROM p_attempt_id
       OR v_claim.policy_version_hash IS DISTINCT FROM p_policy_version_hash
       OR v_claim.lease_id IS DISTINCT FROM p_lease_id
       OR v_claim.grant_id IS DISTINCT FROM p_grant_id
       OR v_claim.attempt_id IS DISTINCT FROM p_attempt_id THEN
        v_admitted := false;
        v_reason := 'EXECUTION_CONTEXT_MISMATCH';
    ELSIF v_evidence.source_system IS DISTINCT FROM p_source_system
       OR v_evidence.evidence_kind IS DISTINCT FROM p_evidence_kind THEN
        v_admitted := false;
        v_reason := 'UNEXPECTED_EVIDENCE_ADAPTER';
    ELSIF v_evidence.verifier_independence IS DISTINCT FROM true
       OR v_evidence.verifier_id IS NULL
       OR v_evidence.verifier_method IS NULL
       OR coalesce(v_evidence.payload->>'outcome', '') IS DISTINCT FROM 'verified' THEN
        v_admitted := false;
        v_reason := 'EVIDENCE_NOT_INDEPENDENTLY_VERIFIED';
    END IF;

    IF v_claim.id IS NOT NULL AND v_evidence.id IS NOT NULL THEN
        INSERT INTO resolution.execution_admission_receipt (
            id, peb_transaction_id, claim_id, evidence_id, evidence_kind,
            source_system, policy_version_hash, lease_id, grant_id, attempt_id,
            admitted, reason
        ) VALUES (
            v_receipt_id, p_peb_transaction_id, p_claim_id, p_evidence_id,
            p_evidence_kind, p_source_system, p_policy_version_hash, p_lease_id,
            p_grant_id, p_attempt_id, v_admitted, v_reason
        );
    END IF;

    RETURN QUERY SELECT v_admitted, v_reason,
        CASE WHEN v_claim.id IS NOT NULL AND v_evidence.id IS NOT NULL
             THEN v_receipt_id ELSE NULL::uuid END;
END;
$$;


--
-- Name: FUNCTION admit_verified_execution_claim(p_peb_transaction_id uuid, p_claim_id uuid, p_evidence_id uuid, p_policy_version_hash text, p_lease_id text, p_grant_id text, p_attempt_id text, p_source_system text, p_evidence_kind text); Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON FUNCTION resolution.admit_verified_execution_claim(p_peb_transaction_id uuid, p_claim_id uuid, p_evidence_id uuid, p_policy_version_hash text, p_lease_id text, p_grant_id text, p_attempt_id text, p_source_system text, p_evidence_kind text) IS 'Fail-closed execution admission assessment. Only confirmed, independently verified, context-matching Git evidence may return admitted=true; PEB records the final transaction result separately.';


--
-- Name: check_and_record_disagreement(uuid, text, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.check_and_record_disagreement(p_representation_comparison_id uuid, p_external_id text, p_relational_proposition_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_check      RECORD;
    v_concept_id uuid;
    v_asserted   uuid;
BEGIN
    SELECT * INTO v_check FROM resolution.detect_disagreement(p_representation_comparison_id, p_external_id);

    SELECT c.id INTO v_concept_id FROM resolution.concept c WHERE c.name = 'WorkRequest';
    INSERT INTO resolution.observation (trigger_type, asset_concept_id, source_artifact_id, payload, assessed)
    VALUES ('representation_disagreement', v_concept_id,
            resolution.resolve_entity_uuid(p_external_id, 'WorkRequest'),
            jsonb_build_object('from_repr', v_check.from_repr, 'to_repr', v_check.to_repr,
                                'from_value', v_check.from_value, 'to_value', v_check.to_value, 'agrees', v_check.agrees),
            true);

    SELECT cav.id INTO v_asserted
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Asserted';

    UPDATE resolution.proposition
    SET value = v_check.agrees, disposition_value_id = v_asserted, last_evaluated_at = now()
    WHERE id = p_relational_proposition_id;

    RETURN v_check.agrees;
END;
$$;


--
-- Name: check_expression_acyclic(); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.check_expression_acyclic() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_would_cycle boolean;
BEGIN
    -- would this edge (parent -> child) create a cycle? true iff parent
    -- is already reachable FROM child via existing edges.
    WITH RECURSIVE reachable(id) AS (
        SELECT NEW.child_expression_id
        UNION
        SELECT eo.child_expression_id
        FROM resolution.expression_operand eo
        JOIN reachable r ON eo.parent_expression_id = r.id
    )
    SELECT EXISTS (SELECT 1 FROM reachable WHERE id = NEW.parent_expression_id)
    INTO v_would_cycle;

    IF v_would_cycle THEN
        RAISE EXCEPTION 'expression_operand: edge % -> % would create a cycle',
            NEW.parent_expression_id, NEW.child_expression_id;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: check_relationship_rule(uuid, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.check_relationship_rule(p_concept_relationship_id uuid, p_from_entity_id uuid) RETURNS TABLE(admitted boolean, rule_name text, rule_type text, compiled_sql text, reason text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
    v_result boolean;
    v_sql    text;
BEGIN
    FOR r IN
        SELECT rl.name, rl.rule_type, rl.expression_id, rl.notes
        FROM resolution.rule rl
        WHERE rl.rule_type = 'conditional' AND rl.concept_relationship_id = p_concept_relationship_id
    LOOP
        IF r.expression_id IS NULL THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, NULL::text,
                'conditional has no expression_id wired up -- failing closed';
            RETURN;
        END IF;

        SELECT eg.result, eg.compiled_sql INTO v_result, v_sql
        FROM resolution.evaluate_relationship_guard(r.expression_id, p_from_entity_id) eg;

        IF NOT v_result THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, v_sql, coalesce(r.notes, 'conditional failed');
            RETURN;
        END IF;
    END LOOP;

    RETURN QUERY SELECT true, NULL::text, NULL::text, NULL::text,
        'all conditionals passed (or none registered) -- checked FROM-side only, see notes';
END;
$$;


--
-- Name: FUNCTION check_relationship_rule(p_concept_relationship_id uuid, p_from_entity_id uuid); Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON FUNCTION resolution.check_relationship_rule(p_concept_relationship_id uuid, p_from_entity_id uuid) IS 'Only evaluates against the relationship''s FROM-side entity. A conditional needing to reference BOTH sides (e.g. comparing the from and to entities'' attributes to each other) is not expressible yet -- evaluate_relationship_guard has exactly one root. Real two-sided conditionals will need that extended, not worked around here.';


--
-- Name: check_representation_rule(uuid, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.check_representation_rule(p_representation_id uuid, p_entity_id uuid) RETURNS TABLE(admitted boolean, rule_name text, rule_type text, compiled_sql text, reason text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
    v_result boolean;
    v_sql    text;
BEGIN
    FOR r IN
        SELECT rl.name, rl.rule_type, rl.expression_id, rl.notes
        FROM resolution.rule rl
        WHERE rl.representation_id = p_representation_id
    LOOP
        IF r.expression_id IS NULL THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, NULL::text,
                'representation rule has no expression_id wired up -- failing closed';
            RETURN;
        END IF;

        SELECT eg.result, eg.compiled_sql INTO v_result, v_sql
        FROM resolution.evaluate_relationship_guard(r.expression_id, p_entity_id) eg;

        IF NOT v_result THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, v_sql, coalesce(r.notes, 'representation rule failed');
            RETURN;
        END IF;
    END LOOP;

    RETURN QUERY SELECT true, NULL::text, NULL::text, NULL::text, 'all representation rules passed (or none registered)';
END;
$$;


--
-- Name: check_transition_guard(uuid, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.check_transition_guard(p_state_transition_id uuid, p_entity_id uuid) RETURNS TABLE(admitted boolean, rule_name text, rule_type text, compiled_sql text, reason text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_concept_id uuid;
    r RECORD;
    v_result boolean;
    v_sql    text;
BEGIN
    SELECT concept_id INTO v_concept_id
    FROM resolution.concept_state_transition WHERE id = p_state_transition_id;
    IF v_concept_id IS NULL THEN
        RAISE EXCEPTION 'no concept_state_transition for id %', p_state_transition_id;
    END IF;

    -- 1. guard-type rules attached specifically to this transition
    FOR r IN
        SELECT rl.name, rl.rule_type, rl.expression_id, rl.notes
        FROM resolution.rule rl
        WHERE rl.rule_type = 'guard' AND rl.state_transition_id = p_state_transition_id
    LOOP
        IF r.expression_id IS NULL THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, NULL::text,
                'guard has no expression_id wired up -- cannot evaluate, failing closed';
            RETURN;
        END IF;
        SELECT eg.result, eg.compiled_sql INTO v_result, v_sql
        FROM resolution.evaluate_relationship_guard(r.expression_id, p_entity_id) eg;
        IF NOT v_result THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, v_sql, coalesce(r.notes, 'guard failed');
            RETURN;
        END IF;
    END LOOP;

    -- 2. invariant-type rules on the CONCEPT this transition belongs to --
    -- these must hold no matter which transition is being attempted.
    FOR r IN
        SELECT rl.name, rl.rule_type, rl.expression_id, rl.notes
        FROM resolution.rule rl
        WHERE rl.rule_type = 'invariant' AND rl.concept_id = v_concept_id
    LOOP
        IF r.expression_id IS NULL THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, NULL::text,
                'invariant has no expression_id wired up -- cannot evaluate, failing closed';
            RETURN;
        END IF;
        SELECT eg.result, eg.compiled_sql INTO v_result, v_sql
        FROM resolution.evaluate_relationship_guard(r.expression_id, p_entity_id) eg;
        IF NOT v_result THEN
            RETURN QUERY SELECT false, r.name, r.rule_type, v_sql, coalesce(r.notes, 'invariant violated');
            RETURN;
        END IF;
    END LOOP;

    -- still narrow, worth stating plainly: concept_relationship-attached
    -- and representation-attached rules are NOT checked here. Those apply
    -- when a relationship instance or a physical write happens, not when
    -- a single entity transitions state -- a different trigger point this
    -- function doesn't cover yet.
    RETURN QUERY SELECT true, NULL::text, NULL::text, NULL::text,
        'all guards and invariants passed (or none registered)';
END;
$$;


--
-- Name: compile_condition(uuid, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.compile_condition(expr_id uuid, current_alias text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_kind          text;
    v_operator      text;
    v_literal       text;
    v_attr_id       uuid;
    v_function_name text;
    v_binding       resolution.concept_attribute_binding%ROWTYPE;
    v_fn_binding    resolution.function_binding%ROWTYPE;
    v_left_id       uuid;
    v_right_id      uuid;
    v_args          text[];
BEGIN
    SELECT kind, operator, literal_value, attribute_id, function_name
    INTO v_kind, v_operator, v_literal, v_attr_id, v_function_name
    FROM resolution.expression WHERE id = expr_id;

    IF v_kind = 'literal' THEN
        RETURN quote_literal(v_literal);
    ELSIF v_kind = 'attribute_ref' THEN
        SELECT * INTO v_binding FROM resolution.concept_attribute_binding WHERE attribute_id = v_attr_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'no concept_attribute_binding for attribute %', v_attr_id; END IF;
        RETURN format('%I.%I', current_alias, v_binding.column_name);
    ELSIF v_kind = 'relationship_ref' THEN
        RETURN resolution.compile_exists_chain(expr_id, resolution.correlation_ref(current_alias, expr_id));
    ELSIF v_kind = 'proposition_ref' THEN
        RETURN resolution.compile_proposition_ref(expr_id);
    ELSIF v_kind = 'function_call' THEN
        SELECT * INTO v_fn_binding FROM resolution.function_binding WHERE function_name = v_function_name;
        IF NOT FOUND THEN RAISE EXCEPTION 'no function_binding for function_name %', v_function_name; END IF;
        SELECT array_agg(resolution.compile_condition(eo.child_expression_id, current_alias) ORDER BY eo.position)
        INTO v_args FROM resolution.expression_operand eo WHERE eo.parent_expression_id = expr_id;
        IF coalesce(array_length(v_args, 1), 0) <> v_fn_binding.arg_count THEN
            RAISE EXCEPTION 'function % expects % arg(s), got %', v_function_name, v_fn_binding.arg_count, coalesce(array_length(v_args, 1), 0);
        END IF;
        RETURN format(v_fn_binding.sql_template, VARIADIC v_args);
    ELSIF v_kind = 'operator' THEN
        SELECT child_expression_id INTO v_left_id  FROM resolution.expression_operand WHERE parent_expression_id = expr_id AND position = 1;
        SELECT child_expression_id INTO v_right_id FROM resolution.expression_operand WHERE parent_expression_id = expr_id AND position = 2;
        IF v_left_id IS NULL OR v_right_id IS NULL THEN RAISE EXCEPTION 'operator node % missing an operand', expr_id; END IF;
        RETURN format('(%s %s %s)', resolution.compile_condition(v_left_id, current_alias), v_operator, resolution.compile_condition(v_right_id, current_alias));
    ELSE
        RAISE EXCEPTION 'compile_condition does not support kind %', v_kind;
    END IF;
END;
$$;


--
-- Name: compile_count_scalar(uuid, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.compile_count_scalar(expr_id uuid, parent_ref text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_crid      uuid;
    v_binding   resolution.concept_relationship_binding%ROWTYPE;
    v_alias     text;
    v_child_id  uuid;
    v_child_sql text;
BEGIN
    SELECT concept_relationship_id INTO v_crid FROM resolution.expression WHERE id = expr_id;

    SELECT * INTO v_binding
    FROM resolution.concept_relationship_binding WHERE concept_relationship_id = v_crid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no concept_relationship_binding for concept_relationship %', v_crid;
    END IF;

    v_alias := 't_' || replace(expr_id::text, '-', '');

    SELECT child_expression_id INTO v_child_id
    FROM resolution.expression_operand WHERE parent_expression_id = expr_id ORDER BY position LIMIT 1;

    IF v_child_id IS NOT NULL THEN
        v_child_sql := resolution.compile_condition(v_child_id, v_alias);
    ELSE
        v_child_sql := 'TRUE';
    END IF;

    RETURN format(
        '(SELECT count(*) FROM %I.%I %I WHERE %I.%I = %s AND (%s))',
        v_binding.to_schema, v_binding.to_table, v_alias,
        v_alias, v_binding.to_column, parent_ref, v_child_sql
    );
END;
$$;


--
-- Name: compile_exists_chain(uuid, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.compile_exists_chain(expr_id uuid, parent_ref text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_kind        text;
    v_crid        uuid;
    v_quantifier  text;
    v_binding     resolution.concept_relationship_binding%ROWTYPE;
    v_alias       text;
    v_child_id    uuid;
    v_child_sql   text;
BEGIN
    SELECT kind, concept_relationship_id, quantifier INTO v_kind, v_crid, v_quantifier
    FROM resolution.expression WHERE id = expr_id;

    IF v_kind IS NULL THEN
        RAISE EXCEPTION 'no expression row for id %', expr_id;
    ELSIF v_kind <> 'relationship_ref' THEN
        RAISE EXCEPTION 'compile_exists_chain only supports relationship_ref nodes, got % for %', v_kind, expr_id;
    END IF;

    SELECT * INTO v_binding
    FROM resolution.concept_relationship_binding WHERE concept_relationship_id = v_crid;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no concept_relationship_binding for concept_relationship %', v_crid;
    END IF;

    v_alias := 't_' || replace(expr_id::text, '-', '');

    SELECT child_expression_id INTO v_child_id
    FROM resolution.expression_operand
    WHERE parent_expression_id = expr_id
    ORDER BY position LIMIT 1;

    IF v_child_id IS NOT NULL THEN
        v_child_sql := resolution.compile_condition(v_child_id, v_alias);
    ELSE
        v_child_sql := 'TRUE';
    END IF;

    IF v_quantifier = 'EXISTS' THEN
        RETURN format(
            'EXISTS (SELECT 1 FROM %I.%I %I WHERE %I.%I = %s AND (%s))',
            v_binding.to_schema, v_binding.to_table, v_alias,
            v_alias, v_binding.to_column, parent_ref, v_child_sql
        );
    ELSIF v_quantifier = 'ALL' THEN
        -- universal quantification: no matching row may VIOLATE the
        -- condition. Vacuously true when there are no matching rows at all
        -- (e.g. a leaf requirement with no children).
        RETURN format(
            'NOT EXISTS (SELECT 1 FROM %I.%I %I WHERE %I.%I = %s AND NOT (%s))',
            v_binding.to_schema, v_binding.to_table, v_alias,
            v_alias, v_binding.to_column, parent_ref, v_child_sql
        );
    ELSE
        RAISE EXCEPTION 'quantifier % not implemented (COUNT still unimplemented)', v_quantifier;
    END IF;
END;
$$;


--
-- Name: compile_proposition_ref(uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.compile_proposition_ref(expr_id uuid) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_prop_id uuid;
    v_field   text;
BEGIN
    SELECT referenced_proposition_id, coalesce(proposition_ref_field, 'disposition')
    INTO v_prop_id, v_field
    FROM resolution.expression WHERE id = expr_id;

    IF v_prop_id IS NULL THEN
        RAISE EXCEPTION 'proposition_ref node % has no referenced_proposition_id', expr_id;
    END IF;

    IF v_field = 'value' THEN
        RETURN format('(SELECT p.value::text FROM resolution.proposition p WHERE p.id = %L)', v_prop_id);
    ELSE
        RETURN format(
            '(SELECT cav.value FROM resolution.proposition p JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id WHERE p.id = %L)',
            v_prop_id
        );
    END IF;
END;
$$;


--
-- Name: compile_root(uuid, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.compile_root(expr_id uuid, literal_root_ref text) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_kind       text;
    v_quantifier text;
    v_operator   text;
    v_literal    text;
    v_attr_id    uuid;
    v_binding    resolution.concept_attribute_binding%ROWTYPE;
    v_left_id    uuid;
    v_right_id   uuid;
BEGIN
    SELECT kind, quantifier, operator, literal_value, attribute_id
    INTO v_kind, v_quantifier, v_operator, v_literal, v_attr_id
    FROM resolution.expression WHERE id = expr_id;

    IF v_kind = 'relationship_ref' THEN
        IF v_quantifier IN ('EXISTS', 'ALL') THEN
            RETURN resolution.compile_exists_chain(expr_id, literal_root_ref);
        ELSIF v_quantifier = 'COUNT' THEN
            RETURN resolution.compile_count_scalar(expr_id, literal_root_ref);
        ELSE
            RAISE EXCEPTION 'unknown quantifier %', v_quantifier;
        END IF;
    ELSIF v_kind = 'attribute_ref' THEN
        SELECT * INTO v_binding FROM resolution.concept_attribute_binding WHERE attribute_id = v_attr_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'no concept_attribute_binding for attribute %', v_attr_id; END IF;
        RETURN format('(SELECT %I FROM %I.%I WHERE id = %s)', v_binding.column_name, v_binding.schema_name, v_binding.table_name, literal_root_ref);
    ELSIF v_kind = 'proposition_ref' THEN
        RETURN resolution.compile_proposition_ref(expr_id);
    ELSIF v_kind = 'operator' THEN
        SELECT child_expression_id INTO v_left_id  FROM resolution.expression_operand WHERE parent_expression_id = expr_id AND position = 1;
        SELECT child_expression_id INTO v_right_id FROM resolution.expression_operand WHERE parent_expression_id = expr_id AND position = 2;
        RETURN format('(%s %s %s)', resolution.compile_root(v_left_id, literal_root_ref), v_operator, resolution.compile_root(v_right_id, literal_root_ref));
    ELSIF v_kind = 'literal' THEN
        RETURN quote_literal(v_literal);
    ELSE
        RAISE EXCEPTION 'compile_root: unsupported root-level kind %', v_kind;
    END IF;
END;
$$;


--
-- Name: consume_binding_decision(uuid, text, text, text, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.consume_binding_decision(p_decision_evidence_id uuid, p_subject_id text, p_work_item_id text, p_transition_name text, p_idempotency_key text) RETURNS TABLE(transition_id uuid, transition_status text, reason text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_decision peb.binding_decision_evidence%ROWTYPE;
    v_existing resolution.binding_resolution_transition%ROWTYPE;
    v_id uuid;
BEGIN
    IF p_decision_evidence_id IS NULL OR p_subject_id IS NULL OR p_work_item_id IS NULL
       OR p_transition_name IS NULL OR p_idempotency_key IS NULL THEN
        RETURN QUERY SELECT NULL::uuid, 'refused', 'MISSING_TRANSITION_CONTEXT'; RETURN;
    END IF;
    SELECT * INTO v_decision FROM peb.binding_decision_evidence WHERE id = p_decision_evidence_id;
    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, 'refused', 'DECISION_EVIDENCE_NOT_FOUND'; RETURN;
    END IF;
    IF v_decision.authority_level <> 'advisory' THEN
        RETURN QUERY SELECT NULL::uuid, 'refused', 'NON_ADVISORY_AUTHORITY'; RETURN;
    END IF;
    IF v_decision.subject_id <> p_subject_id OR v_decision.work_item_id <> p_work_item_id THEN
        RETURN QUERY SELECT NULL::uuid, 'refused', 'TRANSITION_BINDING_MISMATCH'; RETURN;
    END IF;
    SELECT * INTO v_existing FROM resolution.binding_resolution_transition
    WHERE idempotency_key = p_idempotency_key;
    IF FOUND THEN
        IF v_existing.decision_evidence_id = p_decision_evidence_id
           AND v_existing.subject_id = p_subject_id
           AND v_existing.work_item_id = p_work_item_id
           AND v_existing.transition_name = p_transition_name THEN
            RETURN QUERY SELECT v_existing.id, v_existing.transition_status, 'IDEMPOTENT_REPLAY'; RETURN;
        END IF;
        RETURN QUERY SELECT NULL::uuid, 'refused', 'CONFLICTING_IDEMPOTENCY_REPLAY'; RETURN;
    END IF;
    v_id := gen_random_uuid();
    INSERT INTO resolution.binding_resolution_transition
        (id, decision_evidence_id, subject_id, work_item_id, transition_name,
         transition_status, idempotency_key)
    VALUES
        (v_id, p_decision_evidence_id, p_subject_id, p_work_item_id,
         p_transition_name,
         CASE WHEN v_decision.disposition = 'allow' THEN 'applied' ELSE 'refused' END,
         p_idempotency_key);
    RETURN QUERY SELECT v_id,
        CASE WHEN v_decision.disposition = 'allow' THEN 'applied' ELSE 'refused' END,
        CASE WHEN v_decision.disposition = 'allow' THEN 'BOUNDARY_RECORDED' ELSE 'NEGATIVE_DECISION_PRESERVED' END;
END;
$$;


--
-- Name: correlation_ref(text, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.correlation_ref(current_alias text, child_expr_id uuid) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_kind        text;
    v_crid        uuid;
    v_from_column text;
BEGIN
    SELECT kind, concept_relationship_id INTO v_kind, v_crid
    FROM resolution.expression WHERE id = child_expr_id;

    IF v_kind IS DISTINCT FROM 'relationship_ref' THEN
        RAISE EXCEPTION 'correlation_ref only applies to a relationship_ref child, got % for %', v_kind, child_expr_id;
    END IF;

    SELECT from_column INTO v_from_column
    FROM resolution.concept_relationship_binding WHERE concept_relationship_id = v_crid;
    IF v_from_column IS NULL THEN
        RAISE EXCEPTION 'no concept_relationship_binding for concept_relationship %', v_crid;
    END IF;

    RETURN format('%I.%I', current_alias, v_from_column);
END;
$$;


--
-- Name: derive_external_id(text, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.derive_external_id(p_concept_name text, p_entity_id uuid) RETURNS text
    LANGUAGE plpgsql
    AS $_$
DECLARE
    v_schema      text;
    v_table       text;
    v_asset_id    uuid;
    v_external_id text;
BEGIN
    SELECT r.schema_name, r.table_name INTO v_schema, v_table
    FROM resolution.representation r
    JOIN resolution.concept c ON c.id = r.concept_id AND c.name = p_concept_name
    JOIN resolution.representation_identity ri ON ri.representation_id = r.id;
    IF v_table IS NULL THEN
        RAISE EXCEPTION 'no identity-bearing representation for concept %', p_concept_name;
    END IF;

    EXECUTE format('SELECT asset_id FROM %I.%I WHERE id = $1', v_schema, v_table)
        INTO v_asset_id USING p_entity_id;
    IF v_asset_id IS NULL THEN
        RAISE EXCEPTION 'entity % (concept %) has no asset_id, cannot derive an external id', p_entity_id, p_concept_name;
    END IF;

    SELECT canonical_asset_id INTO v_external_id
    FROM resolution.canonical_asset WHERE id = v_asset_id AND expired_at IS NULL;
    RETURN v_external_id;
END;
$_$;


--
-- Name: detect_disagreement(uuid, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.detect_disagreement(p_representation_comparison_id uuid, p_external_id text) RETURNS TABLE(agrees boolean, from_value text, to_value text, from_repr text, to_repr text)
    LANGUAGE plpgsql
    AS $_$
DECLARE
    v_comp              RECORD;
    v_rr                RECORD;
    v_from_repr         RECORD;
    v_to_repr           RECORD;
    v_from_concept_name text;
    v_from_entity_id    uuid;
    v_from_value        text;
    v_to_value          text;
BEGIN
    SELECT * INTO v_comp FROM resolution.representation_comparison WHERE id = p_representation_comparison_id;
    SELECT * INTO v_rr   FROM resolution.representation_relationship WHERE id = v_comp.representation_relationship_id;
    SELECT * INTO v_from_repr FROM resolution.representation WHERE id = v_rr.from_representation_id;
    SELECT * INTO v_to_repr   FROM resolution.representation WHERE id = v_rr.to_representation_id;

    SELECT c.name INTO v_from_concept_name FROM resolution.concept c WHERE c.id = v_from_repr.concept_id;
    v_from_entity_id := resolution.resolve_entity_uuid(p_external_id, v_from_concept_name);

    EXECUTE format('SELECT %I::text FROM %I.%I WHERE id = $1', v_comp.from_column, v_from_repr.schema_name, v_from_repr.table_name)
        INTO v_from_value USING v_from_entity_id;

    EXECUTE format('SELECT %I::text FROM %I.%I WHERE work_request_uuid = $1', v_comp.to_column, v_to_repr.schema_name, v_to_repr.table_name)
        INTO v_to_value USING p_external_id;

    RETURN QUERY SELECT (v_from_value IS NOT DISTINCT FROM v_to_value), v_from_value, v_to_value, v_from_repr.label, v_to_repr.label;
END;
$_$;


--
-- Name: evaluate_proposition(uuid, text, jsonb); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.evaluate_proposition(p_proposition_id uuid, p_trigger_reason text DEFAULT 'manual'::text, p_context jsonb DEFAULT NULL::jsonb) RETURNS TABLE(disposition text, all_passed boolean, context_status text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_subject_entity_id    uuid;
    v_framed_dim_count     integer;
    v_ctx_val              text;
    v_resolved_ref_id      uuid;
    r                      RECORD;
    v_result               boolean;
    v_sql                  text;
    v_all_passed           boolean := true;
    v_relational_failed    boolean := false;
    v_disposition_value_id uuid;
    v_disposition          text;
BEGIN
    ------------------------------------------------------------------
    -- Gate: context discipline. Runs BEFORE anything is written.
    ------------------------------------------------------------------
    SELECT count(*) INTO v_framed_dim_count
    FROM resolution.proposition_frame_value
    WHERE proposition_id = p_proposition_id;

    IF v_framed_dim_count > 0 THEN
        -- framed but no context -> refuse, write nothing
        IF p_context IS NULL THEN
            RETURN QUERY SELECT NULL::text, NULL::boolean, 'context_required'::text;
            RETURN;
        END IF;

        -- admin review item 2: non-object JSON is a caller contract error,
        -- not an evaluation outcome. Fail loudly with a clear message.
        IF jsonb_typeof(p_context) <> 'object' THEN
            RAISE EXCEPTION 'evaluate_proposition: p_context must be a JSON object, got %', jsonb_typeof(p_context);
        END IF;

        -- D3 (narrowed per review item 1): FOR FRAMED propositions, unknown
        -- dimension names are a caller bug -> raise loudly. For UNFRAMED
        -- propositions context is wholly irrelevant (case not_scoped) and
        -- is neither validated nor consulted.
        FOR r IN SELECT jsonb_object_keys(p_context) AS k LOOP
            IF NOT EXISTS (
                SELECT 1 FROM resolution.frame_dimension WHERE name = r.k
            ) THEN
                RAISE EXCEPTION 'evaluate_proposition: context key % names no known frame_dimension', r.k;
            END IF;
        END LOOP;

        FOR r IN
            SELECT fd.name          AS dim_name,
                   fd.value_kind    AS value_kind,
                   fd.scalar_type   AS scalar_type,
                   fd.id            AS dim_id,
                   pfv.reference_value_id,
                   pfv.scalar_value
            FROM resolution.proposition_frame_value pfv
            JOIN resolution.frame_dimension fd ON fd.id = pfv.dimension_id
            WHERE pfv.proposition_id = p_proposition_id
        LOOP
            v_ctx_val := p_context ->> r.dim_name;

            IF v_ctx_val IS NULL THEN
                RETURN QUERY SELECT NULL::text, NULL::boolean, 'context_required'::text;
                RETURN;
            END IF;

            IF r.value_kind = 'governed_reference' THEN
                SELECT f.id INTO v_resolved_ref_id
                FROM resolution.frame_dimension_value f
                WHERE f.dimension_id = r.dim_id
                  AND f.value        = v_ctx_val;

                IF v_resolved_ref_id IS NULL
                   OR v_resolved_ref_id IS DISTINCT FROM r.reference_value_id THEN
                    RETURN QUERY SELECT NULL::text, NULL::boolean, 'context_mismatch'::text;
                    RETURN;
                END IF;

            ELSIF r.value_kind = 'typed_scalar' THEN
                BEGIN
                    IF NOT (
                        CASE r.scalar_type
                            WHEN 'integer'   THEN v_ctx_val::integer     = r.scalar_value::integer
                            WHEN 'numeric'   THEN v_ctx_val::numeric     = r.scalar_value::numeric
                            WHEN 'boolean'   THEN v_ctx_val::boolean     = r.scalar_value::boolean
                            WHEN 'timestamp' THEN v_ctx_val::timestamptz = r.scalar_value::timestamptz
                            ELSE v_ctx_val = r.scalar_value
                        END
                    ) THEN
                        RETURN QUERY SELECT NULL::text, NULL::boolean, 'context_mismatch'::text;
                        RETURN;
                    END IF;
                EXCEPTION WHEN OTHERS THEN
                    RETURN QUERY SELECT NULL::text, NULL::boolean, 'context_mismatch'::text;
                    RETURN;
                END;
            ELSE
                RAISE EXCEPTION 'evaluate_proposition: dimension % has unrecognized value_kind %',
                    r.dim_name, r.value_kind;
            END IF;
        END LOOP;
    END IF;

    ------------------------------------------------------------------
    -- Normal evaluation path
    ------------------------------------------------------------------
    SELECT subject_entity_id INTO v_subject_entity_id FROM resolution.proposition WHERE id = p_proposition_id;

    FOR r IN
        SELECT pa.rule_id, rl.expression_id, rl.is_relational_check
        FROM resolution.proposition_assertion pa
        JOIN resolution.rule rl ON rl.id = pa.rule_id
        WHERE pa.proposition_id = p_proposition_id
    LOOP
        IF r.expression_id IS NULL THEN
            v_result := false; v_sql := NULL;
        ELSE
            SELECT eg.result, eg.compiled_sql INTO v_result, v_sql
            FROM resolution.evaluate_relationship_guard(r.expression_id, v_subject_entity_id) eg;
            IF v_result IS NULL THEN
                v_result := false;   -- fail-closed: missing subject row yields NULL, treat as failure
            END IF;
        END IF;

        INSERT INTO resolution.assertion_evaluation (proposition_id, rule_id, result, compiled_sql, trigger_reason)
        VALUES (p_proposition_id, r.rule_id, v_result, v_sql, p_trigger_reason);

        IF NOT v_result THEN
            v_all_passed := false;
            IF r.is_relational_check THEN v_relational_failed := true; END IF;
        END IF;
    END LOOP;

    v_disposition := CASE
        WHEN v_all_passed THEN 'Asserted'
        WHEN v_relational_failed THEN 'Disputed'
        ELSE 'Rejected'
    END;

    SELECT cav.id INTO v_disposition_value_id
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = v_disposition;

    UPDATE resolution.proposition
    SET disposition_value_id = v_disposition_value_id, last_evaluated_at = now()
    WHERE id = p_proposition_id;

    RETURN QUERY SELECT
        v_disposition,
        v_all_passed,
        CASE WHEN v_framed_dim_count > 0 THEN 'scoped'::text ELSE 'not_scoped'::text END;
END;
$$;


--
-- Name: evaluate_relationship_guard(uuid, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.evaluate_relationship_guard(expr_id uuid, root_instance_id uuid) RETURNS TABLE(compiled_sql text, result boolean)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_sql text;
    v_result boolean;
BEGIN
    v_sql := resolution.compile_root(expr_id, quote_literal(root_instance_id::text));
    EXECUTE format('SELECT %s', v_sql) INTO v_result;
    RETURN QUERY SELECT v_sql, v_result;
END;
$$;


--
-- Name: execution_evidence_immutable(); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.execution_evidence_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'resolution.execution_evidence is immutable: % is not allowed', TG_OP;
END;
$$;


--
-- Name: is_stale(uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.is_stale(p_proposition_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_last_evaluated  timestamptz;
    v_tightest_window interval;
    v_type_default    interval;
BEGIN
    SELECT last_evaluated_at INTO v_last_evaluated FROM resolution.proposition WHERE id = p_proposition_id;
    IF v_last_evaluated IS NULL THEN
        RETURN false;
    END IF;

    SELECT min(rl.staleness_window) INTO v_tightest_window
    FROM resolution.proposition_assertion pa
    JOIN resolution.rule rl ON rl.id = pa.rule_id
    WHERE pa.proposition_id = p_proposition_id AND rl.staleness_window IS NOT NULL;

    IF v_tightest_window IS NULL THEN
        SELECT st.default_staleness_window INTO v_type_default
        FROM resolution.proposition p
        JOIN resolution.semantic_type st ON st.id = p.semantic_type_id
        WHERE p.id = p_proposition_id;
        v_tightest_window := v_type_default;
    END IF;

    IF v_tightest_window IS NULL THEN
        RETURN false;  -- no assertion override AND no semantic-type default -- never stale
    END IF;

    RETURN v_last_evaluated < now() - v_tightest_window;
END;
$$;


--
-- Name: is_well_framed(uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.is_well_framed(p_proposition_id uuid) RETURNS boolean
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_missing_count integer;
BEGIN
    SELECT count(*) INTO v_missing_count
    FROM resolution.semantic_type_required_dimension std
    JOIN resolution.proposition p ON p.semantic_type_id = std.semantic_type_id
    WHERE p.id = p_proposition_id
      AND NOT EXISTS (
          SELECT 1 FROM resolution.proposition_frame_value pfv
          WHERE pfv.proposition_id = p_proposition_id AND pfv.dimension_id = std.dimension_id
      );
    RETURN v_missing_count = 0;
END;
$$;


--
-- Name: on_change(text, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.on_change(p_concept_name text, p_entity_id uuid) RETURNS TABLE(proposition_id uuid, action_taken text, resulting_disposition text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    r      RECORD;
    v_eval RECORD;
    v_ext  text;
    v_reason text;
BEGIN
    FOR r IN
        SELECT p.id, cav.value AS current_disposition
        FROM resolution.proposition p
        JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
        JOIN resolution.concept c ON c.id = p.asset_concept_id
        WHERE cav.value IN ('Pending', 'Asserted', 'Rejected', 'Stale')
          AND c.name = p_concept_name AND p.subject_entity_id = p_entity_id
          AND EXISTS (SELECT 1 FROM resolution.proposition_assertion pa WHERE pa.proposition_id = p.id)
    LOOP
        v_reason := CASE WHEN r.current_disposition = 'Pending' THEN 'pending_created' ELSE 'upstream_changed' END;
        SELECT * INTO v_eval FROM resolution.evaluate_proposition(r.id, v_reason);
        RETURN QUERY SELECT r.id, 'event_evaluate'::text, v_eval.disposition;
    END LOOP;

    BEGIN
        v_ext := resolution.derive_external_id(p_concept_name, p_entity_id);
    EXCEPTION WHEN OTHERS THEN
        v_ext := NULL;
    END;

    IF v_ext IS NOT NULL THEN
        FOR r IN
            SELECT p.id FROM resolution.proposition p
            JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
            JOIN resolution.concept c ON c.id = p.asset_concept_id
            WHERE cav.value = 'Disputed' AND c.name = p_concept_name AND p.subject_entity_id = p_entity_id
              AND EXISTS (SELECT 1 FROM resolution.proposition_comparison pc WHERE pc.proposition_id = p.id)
        LOOP
            SELECT * INTO v_eval FROM resolution.reopen_disputed_proposition(r.id, v_ext);
            RETURN QUERY SELECT r.id, 'event_reopen'::text, v_eval.disposition;
        END LOOP;
    END IF;

    RETURN;
END;
$$;


--
-- Name: reopen_disputed_proposition(uuid, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.reopen_disputed_proposition(p_proposition_id uuid, p_external_id text) RETURNS TABLE(disposition text, comparators_agree boolean, assertions_passed boolean)
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_current_disposition text;
    v_comp                RECORD;
    v_relational_prop_id  uuid;
    v_all_agree           boolean := true;
    v_eval                RECORD;
    v_target_value        text;
    v_target_value_id     uuid;
BEGIN
    SELECT cav.value INTO v_current_disposition
    FROM resolution.proposition p JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
    WHERE p.id = p_proposition_id;

    IF v_current_disposition IS DISTINCT FROM 'Disputed' THEN
        RAISE EXCEPTION 'proposition % is not Disputed (currently %), nothing to reopen', p_proposition_id, v_current_disposition;
    END IF;

    FOR v_comp IN
        SELECT pc.representation_comparison_id
        FROM resolution.proposition_comparison pc WHERE pc.proposition_id = p_proposition_id
    LOOP
        -- refresh the Relational proposition for this comparison, if one
        -- exists, so evaluate_proposition below sees current data rather
        -- than whatever value was last recorded.
        SELECT p2.id INTO v_relational_prop_id
        FROM resolution.proposition p2
        JOIN resolution.proposition_comparison pc2
            ON pc2.proposition_id = p2.id AND pc2.representation_comparison_id = v_comp.representation_comparison_id
        JOIN resolution.concept_attribute_value gcav ON gcav.id = p2.grounding_status_value_id AND gcav.value = 'Relational'
        LIMIT 1;

        IF v_relational_prop_id IS NOT NULL THEN
            PERFORM resolution.check_and_record_disagreement(v_comp.representation_comparison_id, p_external_id, v_relational_prop_id);
            IF NOT (SELECT p3.value FROM resolution.proposition p3 WHERE p3.id = v_relational_prop_id) THEN
                v_all_agree := false;
            END IF;
        ELSIF NOT (SELECT agrees FROM resolution.detect_disagreement(v_comp.representation_comparison_id, p_external_id)) THEN
            -- no Relational proposition wired for this comparison -- fall
            -- back to a direct check
            v_all_agree := false;
        END IF;
    END LOOP;

    SELECT * INTO v_eval FROM resolution.evaluate_proposition(p_proposition_id);
    -- evaluate_proposition already wrote its own disposition based on the
    -- (now-refreshed) assertions -- nothing further to override here,
    -- since a failing relational assertion already yields Disputed and a
    -- clean pass already yields Asserted.

    SELECT cav.value INTO v_target_value
    FROM resolution.proposition p JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
    WHERE p.id = p_proposition_id;

    RETURN QUERY SELECT v_target_value, v_all_agree, v_eval.all_passed;
END;
$$;


--
-- Name: resolve_disputed_via_verification(uuid, uuid); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.resolve_disputed_via_verification(p_proposition_id uuid, p_verified_statement_id uuid) RETURNS text
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_current_disposition text;
    v_vs                  RECORD;
    v_proposition_concept uuid;
    v_asserted_value      uuid;
BEGIN
    SELECT cav.value INTO v_current_disposition
    FROM resolution.proposition p JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
    WHERE p.id = p_proposition_id;

    IF v_current_disposition IS DISTINCT FROM 'Disputed' THEN
        RAISE EXCEPTION 'proposition % is not Disputed (currently %)', p_proposition_id, v_current_disposition;
    END IF;

    SELECT * INTO v_vs FROM resolution.verified_statement WHERE id = p_verified_statement_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'no verified_statement %', p_verified_statement_id; END IF;

    SELECT id INTO v_proposition_concept FROM resolution.concept WHERE name = 'Proposition';

    IF v_vs.asset_concept_id IS DISTINCT FROM v_proposition_concept OR v_vs.target_asset_id IS DISTINCT FROM p_proposition_id THEN
        RAISE EXCEPTION 'verified_statement % does not target proposition % -- refusing to resolve on an unrelated verification',
            p_verified_statement_id, p_proposition_id;
    END IF;

    SELECT cav.id INTO v_asserted_value
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Asserted';

    UPDATE resolution.proposition
    SET disposition_value_id = v_asserted_value, last_evaluated_at = now()
    WHERE id = p_proposition_id;

    RETURN 'Asserted';
END;
$$;


--
-- Name: resolve_entity_uuid(text, text); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.resolve_entity_uuid(p_external_id text, p_concept_name text) RETURNS uuid
    LANGUAGE plpgsql
    AS $_$
DECLARE
    v_asset_id uuid;
    v_schema   text;
    v_table    text;
    v_result   uuid;
BEGIN
    SELECT id INTO v_asset_id FROM resolution.canonical_asset
    WHERE canonical_asset_id = p_external_id AND expired_at IS NULL;
    IF v_asset_id IS NULL THEN
        RAISE EXCEPTION 'no active canonical_asset for external id %', p_external_id;
    END IF;

    SELECT r.schema_name, r.table_name INTO v_schema, v_table
    FROM resolution.representation r
    JOIN resolution.concept c ON c.id = r.concept_id AND c.name = p_concept_name
    JOIN resolution.representation_identity ri ON ri.representation_id = r.id;
    IF v_table IS NULL THEN
        RAISE EXCEPTION 'no identity-bearing representation found for concept %', p_concept_name;
    END IF;

    EXECUTE format('SELECT id FROM %I.%I WHERE asset_id = $1', v_schema, v_table)
        INTO v_result USING v_asset_id;
    IF v_result IS NULL THEN
        RAISE EXCEPTION 'canonical_asset % has no matching row in %.%', p_external_id, v_schema, v_table;
    END IF;

    RETURN v_result;
END;
$_$;


--
-- Name: run_reconciliation_sweep(integer); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.run_reconciliation_sweep(p_batch_limit integer DEFAULT 50) RETURNS TABLE(proposition_id uuid, action_taken text, resulting_disposition text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT p.id, c.name AS concept_name, p.subject_entity_id
        FROM resolution.proposition p
        JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
        JOIN resolution.concept c ON c.id = p.asset_concept_id
        WHERE cav.value IN ('Pending', 'Disputed') AND p.subject_entity_id IS NOT NULL
        LIMIT p_batch_limit
    LOOP
        RETURN QUERY SELECT * FROM resolution.on_change(r.concept_name, r.subject_entity_id);
    END LOOP;

    RETURN QUERY SELECT * FROM resolution.run_staleness_sweep(p_batch_limit);
END;
$$;


--
-- Name: run_reconciliation_sweep(interval, integer); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.run_reconciliation_sweep(p_stale_after interval, p_batch_limit integer) RETURNS TABLE(proposition_id uuid, action_taken text, resulting_disposition text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    r            RECORD;
    v_eval       RECORD;
    v_stale_ids  uuid[];
    v_ext        text;
    v_value_id   uuid;
BEGIN
    SELECT array_agg(p.id) INTO v_stale_ids
    FROM resolution.proposition p
    JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
    WHERE cav.value = 'Stale';

    FOR r IN
        SELECT p.id FROM resolution.proposition p
        JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
        WHERE cav.value = 'Pending'
          AND EXISTS (SELECT 1 FROM resolution.proposition_assertion pa WHERE pa.proposition_id = p.id)
        LIMIT p_batch_limit
    LOOP
        SELECT * INTO v_eval FROM resolution.evaluate_proposition(r.id);
        RETURN QUERY SELECT r.id, 'mechanical_evaluate'::text, v_eval.disposition;
    END LOOP;

    SELECT cav.id INTO v_value_id
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Stale';

    FOR r IN
        SELECT p.id FROM resolution.proposition p
        JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
        WHERE cav.value = 'Asserted'
          AND (p.last_evaluated_at IS NULL OR p.last_evaluated_at < now() - p_stale_after)
        LIMIT p_batch_limit
    LOOP
        UPDATE resolution.proposition SET disposition_value_id = v_value_id WHERE id = r.id;
        RETURN QUERY SELECT r.id, 'marked_stale'::text, 'Stale'::text;
    END LOOP;

    SELECT cav.id INTO v_value_id
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Pending';

    IF v_stale_ids IS NOT NULL THEN
        FOR r IN SELECT unnest(v_stale_ids) AS id LOOP
            UPDATE resolution.proposition SET disposition_value_id = v_value_id WHERE id = r.id;
            SELECT * INTO v_eval FROM resolution.evaluate_proposition(r.id);
            RETURN QUERY SELECT r.id, 'reopened_from_stale'::text, v_eval.disposition;
        END LOOP;
    END IF;

    FOR r IN
        SELECT p.id, c.name AS concept_name, p.subject_entity_id
        FROM resolution.proposition p
        JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
        JOIN resolution.concept c ON c.id = p.asset_concept_id
        WHERE cav.value = 'Disputed'
          AND EXISTS (SELECT 1 FROM resolution.proposition_comparison pc WHERE pc.proposition_id = p.id)
        LIMIT p_batch_limit
    LOOP
        BEGIN
            v_ext := resolution.derive_external_id(r.concept_name, r.subject_entity_id);
            SELECT * INTO v_eval FROM resolution.reopen_disputed_proposition(r.id, v_ext);
            RETURN QUERY SELECT r.id, 'opportunistic_reopen'::text, v_eval.disposition;
        EXCEPTION WHEN OTHERS THEN
            RETURN QUERY SELECT r.id, 'reopen_skipped_no_external_id'::text, 'Disputed'::text;
        END;
    END LOOP;

    RETURN;
END;
$$;


--
-- Name: run_staleness_sweep(integer); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.run_staleness_sweep(p_batch_limit integer DEFAULT 50) RETURNS TABLE(proposition_id uuid, action_taken text, resulting_disposition text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    r           RECORD;
    v_eval      RECORD;
    v_stale_ids uuid[];
    v_value_id  uuid;
BEGIN
    -- snapshot ALREADY-stale propositions, oldest-checked first, so a
    -- proposition that's been waiting longest gets priority for reopening
    SELECT array_agg(p.id ORDER BY p.last_evaluated_at ASC NULLS FIRST) INTO v_stale_ids
    FROM resolution.proposition p
    JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
    WHERE cav.value = 'Stale';

    SELECT cav.id INTO v_value_id
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Stale';

    -- oldest-checked-first here too: without this ORDER BY, which N rows
    -- come back under LIMIT is whatever the planner happens to pick --
    -- under real load that means some propositions could go unchecked
    -- indefinitely while others get hit every cycle. Ordering by
    -- last_evaluated_at means each call makes genuine, fair progress
    -- rather than an arbitrary one.
    FOR r IN
        SELECT p.id FROM resolution.proposition p
        JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
        WHERE cav.value = 'Asserted' AND resolution.is_stale(p.id)
        ORDER BY p.last_evaluated_at ASC NULLS FIRST
        LIMIT p_batch_limit
    LOOP
        UPDATE resolution.proposition SET disposition_value_id = v_value_id WHERE id = r.id;
        RETURN QUERY SELECT r.id, 'marked_stale'::text, 'Stale'::text;
    END LOOP;

    SELECT cav.id INTO v_value_id
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Pending';

    IF v_stale_ids IS NOT NULL THEN
        FOR r IN SELECT unnest(v_stale_ids[1:p_batch_limit]) AS id LOOP
            UPDATE resolution.proposition SET disposition_value_id = v_value_id WHERE id = r.id;
            SELECT * INTO v_eval FROM resolution.evaluate_proposition(r.id, 'clock_stale_retry');
            RETURN QUERY SELECT r.id, 'reopened_from_stale'::text, v_eval.disposition;
        END LOOP;
    END IF;

    RETURN;
END;
$$;


--
-- Name: run_staleness_sweep(interval, integer); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.run_staleness_sweep(p_stale_after interval, p_batch_limit integer) RETURNS TABLE(proposition_id uuid, action_taken text, resulting_disposition text)
    LANGUAGE plpgsql
    AS $$
DECLARE
    r           RECORD;
    v_eval      RECORD;
    v_stale_ids uuid[];
    v_value_id  uuid;
BEGIN
    SELECT array_agg(p.id) INTO v_stale_ids
    FROM resolution.proposition p
    JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
    WHERE cav.value = 'Stale';

    SELECT cav.id INTO v_value_id
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Stale';

    FOR r IN
        SELECT p.id FROM resolution.proposition p
        JOIN resolution.concept_attribute_value cav ON cav.id = p.disposition_value_id
        WHERE cav.value = 'Asserted'
          AND (p.last_evaluated_at IS NULL OR p.last_evaluated_at < now() - p_stale_after)
        LIMIT p_batch_limit
    LOOP
        UPDATE resolution.proposition SET disposition_value_id = v_value_id WHERE id = r.id;
        RETURN QUERY SELECT r.id, 'marked_stale'::text, 'Stale'::text;
    END LOOP;

    SELECT cav.id INTO v_value_id
    FROM resolution.concept_attribute_value cav
    JOIN resolution.concept_attribute ca ON ca.id = cav.attribute_id AND ca.name = 'disposition'
    JOIN resolution.concept c ON c.id = ca.concept_id AND c.name = 'Proposition'
    WHERE cav.value = 'Pending';

    IF v_stale_ids IS NOT NULL THEN
        FOR r IN SELECT unnest(v_stale_ids) AS id LOOP
            UPDATE resolution.proposition SET disposition_value_id = v_value_id WHERE id = r.id;
            SELECT * INTO v_eval FROM resolution.evaluate_proposition(r.id);
            RETURN QUERY SELECT r.id, 'reopened_from_stale'::text, v_eval.disposition;
        END LOOP;
    END IF;

    RETURN;
END;
$$;


--
-- Name: validate_proposition_frame_value(); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.validate_proposition_frame_value() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_dim         resolution.frame_dimension%ROWTYPE;
    v_ref_dim_id  uuid;
BEGIN
    SELECT * INTO v_dim FROM resolution.frame_dimension WHERE id = NEW.dimension_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no frame_dimension %', NEW.dimension_id;
    END IF;

    IF v_dim.value_kind = 'governed_reference' THEN
        IF NEW.reference_value_id IS NULL THEN
            RAISE EXCEPTION 'dimension % requires a governed reference value, not a scalar', v_dim.name;
        END IF;
        SELECT dimension_id INTO v_ref_dim_id FROM resolution.frame_dimension_value WHERE id = NEW.reference_value_id;
        IF v_ref_dim_id IS DISTINCT FROM NEW.dimension_id THEN
            RAISE EXCEPTION 'reference_value_id % belongs to a different dimension than %', NEW.reference_value_id, v_dim.name;
        END IF;

    ELSIF v_dim.value_kind = 'typed_scalar' THEN
        IF NEW.scalar_value IS NULL THEN
            RAISE EXCEPTION 'dimension % requires a scalar value, not a governed reference', v_dim.name;
        END IF;
        BEGIN
            CASE v_dim.scalar_type
                WHEN 'integer'   THEN PERFORM NEW.scalar_value::integer;
                WHEN 'numeric'   THEN PERFORM NEW.scalar_value::numeric;
                WHEN 'boolean'   THEN PERFORM NEW.scalar_value::boolean;
                WHEN 'timestamp' THEN PERFORM NEW.scalar_value::timestamptz;
                ELSE NULL;  -- 'text' needs no cast check
            END CASE;
        EXCEPTION WHEN OTHERS THEN
            RAISE EXCEPTION 'scalar_value % is not a valid % for dimension %', NEW.scalar_value, v_dim.scalar_type, v_dim.name;
        END;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: verified_statement_immutable(); Type: FUNCTION; Schema: resolution; Owner: -
--

CREATE FUNCTION resolution.verified_statement_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'resolution.verified_statement is immutable: % is not allowed', TG_OP;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: asset_identity_claim; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.asset_identity_claim (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid NOT NULL,
    candidate_asset_id uuid,
    claim_type text NOT NULL,
    confidence real,
    basis text,
    status text DEFAULT 'open'::text NOT NULL,
    decided_by text,
    decided_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    CONSTRAINT asset_identity_claim_claim_type_check CHECK ((claim_type = ANY (ARRAY['identity'::text, 'supersession'::text, 'derivation'::text, 'consolidation'::text, 'split'::text]))),
    CONSTRAINT asset_identity_claim_status_check CHECK ((status = ANY (ARRAY['open'::text, 'resolved'::text, 'rejected'::text])))
);


--
-- Name: add_asset_identity_claim(uuid, uuid, uuid, text, real, text, text, text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_asset_identity_claim(p_id uuid DEFAULT NULL::uuid, p_asset_id uuid DEFAULT NULL::uuid, p_candidate_asset_id uuid DEFAULT NULL::uuid, p_claim_type text DEFAULT NULL::text, p_confidence real DEFAULT NULL::real, p_basis text DEFAULT NULL::text, p_status text DEFAULT 'open'::text, p_decided_by text DEFAULT NULL::text, p_decided_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.asset_identity_claim
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.asset_identity_claim%ROWTYPE;
BEGIN
    INSERT INTO semantics.asset_identity_claim
        (id, asset_id, candidate_asset_id, claim_type, confidence, basis,
         status, decided_by, decided_at, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_asset_id, p_candidate_asset_id,
         p_claim_type, p_confidence, p_basis, COALESCE(p_status, 'open'),
         p_decided_by, p_decided_at, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: asset_relation; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.asset_relation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    from_asset_id uuid NOT NULL,
    to_asset_id uuid NOT NULL,
    relation_type text NOT NULL,
    decided_by text,
    decided_at timestamp with time zone,
    effective_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    CONSTRAINT asset_relation_check CHECK ((from_asset_id <> to_asset_id)),
    CONSTRAINT asset_relation_relation_type_check CHECK ((relation_type = ANY (ARRAY['supersedes'::text, 'derives_from'::text, 'contradicts'::text, 'consolidates_into'::text, 'split_from'::text, 'owns'::text, 'member_of'::text, 'equivalent'::text])))
);


--
-- Name: add_asset_relation(uuid, uuid, uuid, text, text, timestamp with time zone, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_asset_relation(p_id uuid DEFAULT NULL::uuid, p_from_asset_id uuid DEFAULT NULL::uuid, p_to_asset_id uuid DEFAULT NULL::uuid, p_relation_type text DEFAULT NULL::text, p_decided_by text DEFAULT NULL::text, p_decided_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_effective_at timestamp with time zone DEFAULT now(), p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.asset_relation
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.asset_relation%ROWTYPE;
BEGIN
    INSERT INTO semantics.asset_relation
        (id, from_asset_id, to_asset_id, relation_type, decided_by,
         decided_at, effective_at, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_from_asset_id, p_to_asset_id,
         p_relation_type, p_decided_by, p_decided_at,
         COALESCE(p_effective_at, now()), p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: asset_revision; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.asset_revision (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    revision_id text NOT NULL,
    asset_id uuid NOT NULL,
    content_hash text,
    source_hash text,
    parent_revision_id uuid,
    recording_start timestamp with time zone,
    recording_end timestamp with time zone,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    CONSTRAINT asset_revision_asset_id_check CHECK ((asset_id IS NOT NULL))
);


--
-- Name: add_asset_revision(uuid, text, uuid, text, text, uuid, timestamp with time zone, timestamp with time zone, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_asset_revision(p_id uuid DEFAULT NULL::uuid, p_revision_id text DEFAULT NULL::text, p_asset_id uuid DEFAULT NULL::uuid, p_content_hash text DEFAULT NULL::text, p_source_hash text DEFAULT NULL::text, p_parent_revision_id uuid DEFAULT NULL::uuid, p_recording_start timestamp with time zone DEFAULT NULL::timestamp with time zone, p_recording_end timestamp with time zone DEFAULT NULL::timestamp with time zone, p_created_by text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.asset_revision
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.asset_revision%ROWTYPE;
BEGIN
    INSERT INTO semantics.asset_revision
        (id, revision_id, asset_id, content_hash, source_hash,
         parent_revision_id, recording_start, recording_end,
         created_by, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_revision_id, p_asset_id,
         p_content_hash, p_source_hash, p_parent_revision_id,
         p_recording_start, p_recording_end, p_created_by, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: canonical_asset; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.canonical_asset (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    canonical_asset_id text NOT NULL,
    asset_kind text NOT NULL,
    canonical_key jsonb,
    source_hash text,
    content_hash text,
    validity_start timestamp with time zone,
    validity_end timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: add_canonical_asset(uuid, text, text, jsonb, text, text, timestamp with time zone, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_canonical_asset(p_id uuid DEFAULT NULL::uuid, p_canonical_asset_id text DEFAULT NULL::text, p_asset_kind text DEFAULT NULL::text, p_canonical_key jsonb DEFAULT NULL::jsonb, p_source_hash text DEFAULT NULL::text, p_content_hash text DEFAULT NULL::text, p_validity_start timestamp with time zone DEFAULT NULL::timestamp with time zone, p_validity_end timestamp with time zone DEFAULT NULL::timestamp with time zone, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.canonical_asset
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.canonical_asset%ROWTYPE;
BEGIN
    INSERT INTO semantics.canonical_asset
        (id, canonical_asset_id, asset_kind, canonical_key, source_hash,
         content_hash, validity_start, validity_end, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_canonical_asset_id, p_asset_kind,
         p_canonical_key, p_source_hash, p_content_hash,
         p_validity_start, p_validity_end, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: drift_finding; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.drift_finding (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    observation_id uuid NOT NULL,
    description text NOT NULL,
    severity text NOT NULL,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    expired_at timestamp with time zone
);


--
-- Name: add_drift_finding(uuid, uuid, text, text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_drift_finding(p_id uuid DEFAULT NULL::uuid, p_observation_id uuid DEFAULT NULL::uuid, p_description text DEFAULT NULL::text, p_severity text DEFAULT NULL::text, p_resolved_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.drift_finding
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.drift_finding%ROWTYPE;
BEGIN
    INSERT INTO semantics.drift_finding
        (id, observation_id, description, severity, resolved_at, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_observation_id, p_description, p_severity, p_resolved_at, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: relationship_type; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.relationship_type (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    scope text,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: add_relationship_type(uuid, text, text, text, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_relationship_type(p_id uuid DEFAULT NULL::uuid, p_name text DEFAULT NULL::text, p_description text DEFAULT NULL::text, p_scope text DEFAULT NULL::text, p_notes text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.relationship_type
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.relationship_type%ROWTYPE;
BEGIN
    INSERT INTO semantics.relationship_type
        (id, name, description, scope, notes, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_name, p_description, p_scope, p_notes, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: snapshot; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.snapshot (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    label text NOT NULL,
    version integer NOT NULL,
    parent_id uuid,
    status text DEFAULT 'draft'::text NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text,
    expired_at timestamp with time zone
);


--
-- Name: add_snapshot(uuid, text, integer, uuid, text, text, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_snapshot(p_id uuid DEFAULT NULL::uuid, p_label text DEFAULT NULL::text, p_version integer DEFAULT NULL::integer, p_parent_id uuid DEFAULT NULL::uuid, p_status text DEFAULT 'draft'::text, p_created_by text DEFAULT NULL::text, p_notes text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.snapshot
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.snapshot%ROWTYPE;
BEGIN
    INSERT INTO semantics.snapshot
        (id, label, version, parent_id, status, created_by, notes, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_label, p_version, p_parent_id, p_status, p_created_by, p_notes, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: snapshot_observation; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.snapshot_observation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    snapshot_id uuid NOT NULL,
    representation_id uuid NOT NULL,
    lifecycle_state text NOT NULL,
    is_completed_fix boolean DEFAULT false NOT NULL,
    completed_fix_ref text,
    audit_reason text,
    safe_to_retire boolean,
    expired_at timestamp with time zone
);


--
-- Name: add_snapshot_observation(uuid, uuid, uuid, text, boolean, text, text, boolean, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_snapshot_observation(p_id uuid DEFAULT NULL::uuid, p_snapshot_id uuid DEFAULT NULL::uuid, p_representation_id uuid DEFAULT NULL::uuid, p_lifecycle_state text DEFAULT NULL::text, p_is_completed_fix boolean DEFAULT false, p_completed_fix_ref text DEFAULT NULL::text, p_audit_reason text DEFAULT NULL::text, p_safe_to_retire boolean DEFAULT NULL::boolean, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.snapshot_observation
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.snapshot_observation%ROWTYPE;
BEGIN
    INSERT INTO semantics.snapshot_observation
        (id, snapshot_id, representation_id, lifecycle_state, is_completed_fix,
         completed_fix_ref, audit_reason, safe_to_retire, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_snapshot_id, p_representation_id, p_lifecycle_state,
         p_is_completed_fix, p_completed_fix_ref, p_audit_reason, p_safe_to_retire, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: source_observation; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.source_observation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    revision_id uuid NOT NULL,
    platform text NOT NULL,
    platform_identifier text,
    namespace text,
    raw_location text,
    observed_at timestamp with time zone,
    ingestion_run_id uuid,
    raw_hash text,
    expired_at timestamp with time zone
);


--
-- Name: add_source_observation(uuid, uuid, text, text, text, text, timestamp with time zone, uuid, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.add_source_observation(p_id uuid DEFAULT NULL::uuid, p_revision_id uuid DEFAULT NULL::uuid, p_platform text DEFAULT NULL::text, p_platform_identifier text DEFAULT NULL::text, p_namespace text DEFAULT NULL::text, p_raw_location text DEFAULT NULL::text, p_observed_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_ingestion_run_id uuid DEFAULT NULL::uuid, p_raw_hash text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.source_observation
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.source_observation%ROWTYPE;
BEGIN
    INSERT INTO semantics.source_observation
        (id, revision_id, platform, platform_identifier, namespace,
         raw_location, observed_at, ingestion_run_id, raw_hash, expired_at)
    VALUES
        (COALESCE(p_id, gen_random_uuid()), p_revision_id, p_platform,
         p_platform_identifier, p_namespace, p_raw_location,
         p_observed_at, p_ingestion_run_id, p_raw_hash, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: check_statement_id(); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.check_statement_id() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    found boolean;
BEGIN
    CASE NEW.statement_type
        WHEN 'source_observation' THEN
            SELECT EXISTS(SELECT 1 FROM semantics.source_observation WHERE id = NEW.statement_id) INTO found;
        WHEN 'representation_relationship' THEN
            SELECT EXISTS(SELECT 1 FROM resolution.representation_relationship WHERE id = NEW.statement_id) INTO found;
        WHEN 'concept_relationship' THEN
            SELECT EXISTS(SELECT 1 FROM resolution.concept_relationship WHERE id = NEW.statement_id) INTO found;
        WHEN 'execution_claim' THEN
            SELECT EXISTS(SELECT 1 FROM resolution.execution_claim WHERE id = NEW.statement_id) INTO found;
        WHEN 'resolution_proposition' THEN
            SELECT EXISTS(SELECT 1 FROM resolution.proposition WHERE id = NEW.statement_id) INTO found;
        ELSE
            RAISE EXCEPTION 'Unknown statement_type: %', NEW.statement_type;
    END CASE;

    IF NOT found THEN
        RAISE EXCEPTION 'Polymorphic resolution failed: no row in % with id %',
            NEW.statement_type, NEW.statement_id;
    END IF;

    RETURN NEW;
END;
$$;


--
-- Name: ensure_registered_service_asset(bigint, text); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.ensure_registered_service_asset(p_service_id bigint, p_system_name text DEFAULT 'Services'::text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_asset_id        uuid;
    v_system_asset_id uuid;
    v_canonical_key   text;
BEGIN
    v_canonical_key := 'asset:nexus:registry_services:' || p_service_id::text;

    -- (a) ensure the canonical asset for this registry service exists
    INSERT INTO semantics.canonical_asset (canonical_asset_id, asset_kind)
    VALUES (v_canonical_key, 'service')
    ON CONFLICT (canonical_asset_id) WHERE expired_at IS NULL DO NOTHING;

    SELECT id INTO v_asset_id
    FROM semantics.canonical_asset
    WHERE canonical_asset_id = v_canonical_key
      AND expired_at IS NULL;

    IF v_asset_id IS NULL THEN
        RAISE EXCEPTION 'failed to create canonical asset for registry service %', p_service_id;
    END IF;

    -- (b) link the registry row to the asset
    UPDATE registry.services
       SET asset_id = v_asset_id
     WHERE id = p_service_id;

    -- (c) wire the owns edge from the named nebula system (if it exists)
    SELECT ns.asset_id INTO v_system_asset_id
    FROM nebula.systems ns
    JOIN semantics.canonical_asset ca ON ca.id = ns.asset_id
    WHERE ns.name = p_system_name
      AND ca.canonical_asset_id = 'asset:nexus:nebula_systems:' || ns.id::text
    LIMIT 1;

    IF v_system_asset_id IS NOT NULL THEN
        INSERT INTO semantics.asset_relation
            (from_asset_id, to_asset_id, relation_type, decided_by, effective_at)
        VALUES (v_system_asset_id, v_asset_id, 'owns', 'architect', now())
        ON CONFLICT (from_asset_id, to_asset_id, relation_type)
            WHERE expired_at IS NULL DO NOTHING;
    END IF;

    RETURN v_asset_id;
END $$;


--
-- Name: ensure_registered_work_request_asset(text); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.ensure_registered_work_request_asset(p_wr_uuid text) RETURNS uuid
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_asset_id      uuid;
    v_canonical_key text;
BEGIN
    v_canonical_key := 'asset:nexus:vision_work_requests:' || p_wr_uuid;

    -- (a) ensure the canonical asset for this work request exists
    INSERT INTO semantics.canonical_asset (canonical_asset_id, asset_kind)
    VALUES (v_canonical_key, 'work_request')
    ON CONFLICT (canonical_asset_id) WHERE expired_at IS NULL DO NOTHING;

    SELECT id INTO v_asset_id
    FROM semantics.canonical_asset
    WHERE canonical_asset_id = v_canonical_key
      AND expired_at IS NULL;

    IF v_asset_id IS NULL THEN
        RAISE EXCEPTION 'failed to create canonical asset for work request %', p_wr_uuid;
    END IF;

    RETURN v_asset_id;
END $$;


--
-- Name: registry_service_asset_link_trigger(); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.registry_service_asset_link_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_asset_id uuid;
BEGIN
    v_asset_id := semantics.ensure_registered_service_asset(NEW.id, 'Services');
    NEW.asset_id := v_asset_id;
    RETURN NEW;
END $$;


--
-- Name: resolve_drift_finding(uuid, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.resolve_drift_finding(p_id uuid, p_resolved_at timestamp with time zone DEFAULT now()) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_count integer;
BEGIN
    UPDATE semantics.drift_finding
    SET    resolved_at = p_resolved_at
    WHERE  id = p_id
      AND  expired_at IS NULL
      AND  resolved_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_asset_identity_claim(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_asset_identity_claim(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.asset_identity_claim SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_asset_relation(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_asset_relation(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.asset_relation SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_asset_revision(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_asset_revision(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.asset_revision SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_canonical_asset(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_canonical_asset(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.canonical_asset SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_drift_finding(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_drift_finding(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.drift_finding SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_relationship_type(text); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_relationship_type(p_name text) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.relationship_type SET expired_at = NOW()
    WHERE name = p_name AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_snapshot(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_snapshot(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.snapshot SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_snapshot_observation(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_snapshot_observation(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.snapshot_observation SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: soft_delete_source_observation(uuid); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.soft_delete_source_observation(p_id uuid) RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE v_count integer;
BEGIN
    UPDATE semantics.source_observation SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;


--
-- Name: update_asset_identity_claim(uuid, uuid, uuid, text, real, text, text, text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_asset_identity_claim(p_id uuid, p_asset_id uuid DEFAULT NULL::uuid, p_candidate_asset_id uuid DEFAULT NULL::uuid, p_claim_type text DEFAULT NULL::text, p_confidence real DEFAULT NULL::real, p_basis text DEFAULT NULL::text, p_status text DEFAULT 'open'::text, p_decided_by text DEFAULT NULL::text, p_decided_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.asset_identity_claim
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.asset_identity_claim%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.asset_identity_claim SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_asset_identity_claim: no active row with id %', p_id; END IF;
    INSERT INTO semantics.asset_identity_claim
        (id, asset_id, candidate_asset_id, claim_type, confidence, basis,
         status, decided_by, decided_at, expired_at)
    VALUES
        (gen_random_uuid(), p_asset_id, p_candidate_asset_id,
         p_claim_type, p_confidence, p_basis, COALESCE(p_status, 'open'),
         p_decided_by, p_decided_at, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_asset_relation(uuid, uuid, uuid, text, text, timestamp with time zone, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_asset_relation(p_id uuid, p_from_asset_id uuid DEFAULT NULL::uuid, p_to_asset_id uuid DEFAULT NULL::uuid, p_relation_type text DEFAULT NULL::text, p_decided_by text DEFAULT NULL::text, p_decided_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_effective_at timestamp with time zone DEFAULT now(), p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.asset_relation
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.asset_relation%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.asset_relation SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_asset_relation: no active row with id %', p_id; END IF;
    INSERT INTO semantics.asset_relation
        (id, from_asset_id, to_asset_id, relation_type, decided_by,
         decided_at, effective_at, expired_at)
    VALUES
        (gen_random_uuid(), p_from_asset_id, p_to_asset_id,
         p_relation_type, p_decided_by, p_decided_at,
         COALESCE(p_effective_at, now()), p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_asset_revision(uuid, text, uuid, text, text, uuid, timestamp with time zone, timestamp with time zone, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_asset_revision(p_id uuid, p_revision_id text DEFAULT NULL::text, p_asset_id uuid DEFAULT NULL::uuid, p_content_hash text DEFAULT NULL::text, p_source_hash text DEFAULT NULL::text, p_parent_revision_id uuid DEFAULT NULL::uuid, p_recording_start timestamp with time zone DEFAULT NULL::timestamp with time zone, p_recording_end timestamp with time zone DEFAULT NULL::timestamp with time zone, p_created_by text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.asset_revision
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.asset_revision%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.asset_revision SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_asset_revision: no active row with id %', p_id; END IF;
    INSERT INTO semantics.asset_revision
        (id, revision_id, asset_id, content_hash, source_hash,
         parent_revision_id, recording_start, recording_end,
         created_by, expired_at)
    VALUES
        (gen_random_uuid(), p_revision_id, p_asset_id,
         p_content_hash, p_source_hash, p_parent_revision_id,
         p_recording_start, p_recording_end, p_created_by, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_canonical_asset(uuid, text, text, jsonb, text, text, timestamp with time zone, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_canonical_asset(p_id uuid, p_canonical_asset_id text DEFAULT NULL::text, p_asset_kind text DEFAULT NULL::text, p_canonical_key jsonb DEFAULT NULL::jsonb, p_source_hash text DEFAULT NULL::text, p_content_hash text DEFAULT NULL::text, p_validity_start timestamp with time zone DEFAULT NULL::timestamp with time zone, p_validity_end timestamp with time zone DEFAULT NULL::timestamp with time zone, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.canonical_asset
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.canonical_asset%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.canonical_asset SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_canonical_asset: no active row with id %', p_id; END IF;
    INSERT INTO semantics.canonical_asset
        (id, canonical_asset_id, asset_kind, canonical_key, source_hash,
         content_hash, validity_start, validity_end, expired_at)
    VALUES
        (gen_random_uuid(), p_canonical_asset_id, p_asset_kind,
         p_canonical_key, p_source_hash, p_content_hash,
         p_validity_start, p_validity_end, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_drift_finding(uuid, uuid, text, text, timestamp with time zone, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_drift_finding(p_id uuid, p_observation_id uuid DEFAULT NULL::uuid, p_description text DEFAULT NULL::text, p_severity text DEFAULT NULL::text, p_resolved_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.drift_finding
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.drift_finding%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.drift_finding SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_drift_finding: no active row with id %', p_id; END IF;
    INSERT INTO semantics.drift_finding
        (id, observation_id, description, severity, resolved_at, expired_at)
    VALUES
        (gen_random_uuid(), p_observation_id, p_description, p_severity, p_resolved_at, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_relationship_type(text, text, text, text, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_relationship_type(p_name text, p_new_name text DEFAULT NULL::text, p_description text DEFAULT NULL::text, p_scope text DEFAULT NULL::text, p_notes text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.relationship_type
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.relationship_type%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.relationship_type SET expired_at = NOW()
    WHERE name = p_name AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_relationship_type: no active row named %', p_name; END IF;
    INSERT INTO semantics.relationship_type
        (id, name, description, scope, notes, expired_at)
    VALUES
        (gen_random_uuid(), p_new_name, p_description, p_scope, p_notes, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_snapshot(uuid, text, integer, uuid, text, text, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_snapshot(p_id uuid, p_label text DEFAULT NULL::text, p_version integer DEFAULT NULL::integer, p_parent_id uuid DEFAULT NULL::uuid, p_status text DEFAULT 'draft'::text, p_created_by text DEFAULT NULL::text, p_notes text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.snapshot
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.snapshot%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.snapshot SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_snapshot: no active row with id %', p_id; END IF;
    INSERT INTO semantics.snapshot
        (id, label, version, parent_id, status, created_by, notes, expired_at)
    VALUES
        (gen_random_uuid(), p_label, p_version, p_parent_id, p_status, p_created_by, p_notes, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_snapshot_observation(uuid, uuid, uuid, text, boolean, text, text, boolean, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_snapshot_observation(p_id uuid, p_snapshot_id uuid DEFAULT NULL::uuid, p_representation_id uuid DEFAULT NULL::uuid, p_lifecycle_state text DEFAULT NULL::text, p_is_completed_fix boolean DEFAULT false, p_completed_fix_ref text DEFAULT NULL::text, p_audit_reason text DEFAULT NULL::text, p_safe_to_retire boolean DEFAULT NULL::boolean, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.snapshot_observation
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.snapshot_observation%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.snapshot_observation SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_snapshot_observation: no active row with id %', p_id; END IF;
    INSERT INTO semantics.snapshot_observation
        (id, snapshot_id, representation_id, lifecycle_state, is_completed_fix,
         completed_fix_ref, audit_reason, safe_to_retire, expired_at)
    VALUES
        (gen_random_uuid(), p_snapshot_id, p_representation_id, p_lifecycle_state,
         p_is_completed_fix, p_completed_fix_ref, p_audit_reason, p_safe_to_retire, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: update_source_observation(uuid, uuid, text, text, text, text, timestamp with time zone, uuid, text, timestamp with time zone); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.update_source_observation(p_id uuid, p_revision_id uuid DEFAULT NULL::uuid, p_platform text DEFAULT NULL::text, p_platform_identifier text DEFAULT NULL::text, p_namespace text DEFAULT NULL::text, p_raw_location text DEFAULT NULL::text, p_observed_at timestamp with time zone DEFAULT NULL::timestamp with time zone, p_ingestion_run_id uuid DEFAULT NULL::uuid, p_raw_hash text DEFAULT NULL::text, p_expired_at timestamp with time zone DEFAULT NULL::timestamp with time zone) RETURNS semantics.source_observation
    LANGUAGE plpgsql
    AS $$
DECLARE v_row semantics.source_observation%ROWTYPE; v_count integer;
BEGIN
    UPDATE semantics.source_observation SET expired_at = NOW()
    WHERE id = p_id AND expired_at IS NULL;
    GET DIAGNOSTICS v_count = ROW_COUNT;
    IF v_count = 0 THEN RAISE EXCEPTION 'update_source_observation: no active row with id %', p_id; END IF;
    INSERT INTO semantics.source_observation
        (id, revision_id, platform, platform_identifier, namespace,
         raw_location, observed_at, ingestion_run_id, raw_hash, expired_at)
    VALUES
        (gen_random_uuid(), p_revision_id, p_platform,
         p_platform_identifier, p_namespace, p_raw_location,
         p_observed_at, p_ingestion_run_id, p_raw_hash, p_expired_at)
    RETURNING * INTO v_row;
    RETURN v_row;
END;
$$;


--
-- Name: vision_work_request_asset_trigger(); Type: FUNCTION; Schema: semantics; Owner: -
--

CREATE FUNCTION semantics.vision_work_request_asset_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF NEW.asset_id IS NULL THEN
        NEW.asset_id := semantics.ensure_registered_work_request_asset(NEW.work_request_uuid);
    END IF;
    RETURN NEW;
END $$;


--
-- Name: config_bundle_interactive_priority_pin(); Type: FUNCTION; Schema: tackle; Owner: -
--

CREATE FUNCTION tackle.config_bundle_interactive_priority_pin() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_min_launchable_priority integer;
BEGIN
  IF NEW.invocation_mode = 'INTERACTIVE' THEN
    SELECT MIN(priority) INTO v_min_launchable_priority
      FROM tackle.config_bundle
     WHERE role = NEW.role AND id <> NEW.id
       AND invocation_mode <> 'INTERACTIVE';
    IF v_min_launchable_priority IS NOT NULL
       AND NEW.priority >= v_min_launchable_priority THEN
      NEW.priority := v_min_launchable_priority - 1;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;


--
-- Name: config_bundle_verified_gate(); Type: FUNCTION; Schema: tackle; Owner: -
--

CREATE FUNCTION tackle.config_bundle_verified_gate() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          -- INTERACTIVE bundles never spawn a harness with the model id —
          -- the model is the human/CLI model driving Freebuff. The verified
          -- gate (which exists to stop opencode spawning dead model ids)
          -- does not apply to this channel.
          IF NEW.invocation_mode = 'INTERACTIVE' THEN
            RETURN NEW;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM tackle.models m
            WHERE m.id = NEW.model_id AND m.verified IS TRUE
          ) THEN
            NEW.is_active := 0;
          END IF;
          RETURN NEW;
        END;
        $$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: tackle; Owner: -
--

CREATE FUNCTION tackle.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$;


--
-- Name: artifacts_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.artifacts_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.artifacts_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION artifacts_delete_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.artifacts_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: artifacts_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.artifacts_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.artifacts_id_seq'));

    INSERT INTO vision.artifacts_history
        (id, artifact_id, type, content, confidence, provenance,
         wr_id, parent_artifact_id, template_metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.artifact_id, NEW.type, NEW.content, NEW.confidence,
         NEW.provenance, NEW.wr_id, NEW.parent_artifact_id,
         NEW.template_metadata, COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION artifacts_insert_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.artifacts_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: artifacts_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.artifacts_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD; BEGIN
    UPDATE vision.artifacts_history SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.artifacts_history
        (id, artifact_id, type, content, confidence, provenance,
         wr_id, parent_artifact_id, template_metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.artifact_id, NEW.type, NEW.content, NEW.confidence,
         NEW.provenance, NEW.wr_id, NEW.parent_artifact_id,
         NEW.template_metadata, OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, artifact_id, type, content, confidence, provenance,
              wr_id, parent_artifact_id, template_metadata, created_at,
              recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: FUNCTION artifacts_update_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.artifacts_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: artifacts_view_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.artifacts_view_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.artifacts_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: artifacts_view_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.artifacts_view_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.artifacts_id_seq'));
    
    INSERT INTO vision.artifacts_history
        (id, artifact_id, type, content, confidence, provenance,
         wr_id, parent_artifact_id, template_metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.artifact_id, NEW.type, NEW.content, NEW.confidence,
         NEW.provenance, NEW.wr_id, NEW.parent_artifact_id,
         NEW.template_metadata, COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');
    
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: artifacts_view_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.artifacts_view_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.artifacts_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    
    INSERT INTO vision.artifacts_history
        (id, artifact_id, type, content, confidence, provenance,
         wr_id, parent_artifact_id, template_metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.artifact_id, NEW.type, NEW.content, NEW.confidence,
         NEW.provenance, NEW.wr_id, NEW.parent_artifact_id,
         NEW.template_metadata, NEW.created_at,
         NOW(), '9999-12-31 23:59:59+00');
    
    RETURN NEW;
END;
$$;


--
-- Name: auto_update_vision_ir_artifact(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.auto_update_vision_ir_artifact() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF NEW.ir_version IS NULL OR NEW.ir_version = 0 THEN
            SELECT COALESCE(MAX(a.ir_version), 0) + 1
            INTO NEW.ir_version
            FROM vision.vision_ir_artifacts a
            WHERE a.work_request_id = NEW.work_request_id
              AND a.ir_stage = NEW.ir_stage;
          END IF;
          RETURN NEW;
        END;
        $$;


--
-- Name: branch_artifacts_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branch_artifacts_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.branch_artifacts_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION branch_artifacts_delete_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.branch_artifacts_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: branch_artifacts_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branch_artifacts_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.branch_artifacts_id_seq'));

    INSERT INTO vision.branch_artifacts_history
        (id, artifact_id, branch_id, wr_id, artifact_type, content,
         parent_artifact_id, score, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.artifact_id, NEW.branch_id, NEW.wr_id, NEW.artifact_type,
         NEW.content, NEW.parent_artifact_id, NEW.score,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION branch_artifacts_insert_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.branch_artifacts_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: branch_artifacts_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branch_artifacts_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD; BEGIN
    UPDATE vision.branch_artifacts_history SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.branch_artifacts_history
        (id, artifact_id, branch_id, wr_id, artifact_type, content,
         parent_artifact_id, score, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.artifact_id, NEW.branch_id, NEW.wr_id, NEW.artifact_type,
         NEW.content, NEW.parent_artifact_id, NEW.score, OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, artifact_id, branch_id, wr_id, artifact_type, content,
              parent_artifact_id, score, created_at,
              recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: FUNCTION branch_artifacts_update_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.branch_artifacts_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: branch_artifacts_view_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branch_artifacts_view_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.branch_artifacts_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: branch_artifacts_view_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branch_artifacts_view_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.branch_artifacts_id_seq'));
    INSERT INTO vision.branch_artifacts_history
        (id, artifact_id, branch_id, wr_id, artifact_type, content,
         parent_artifact_id, score, created_at, recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.artifact_id, NEW.branch_id, NEW.wr_id, NEW.artifact_type,
         NEW.content, NEW.parent_artifact_id, NEW.score,
         COALESCE(NEW.created_at, NOW()), NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: branch_artifacts_view_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branch_artifacts_view_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.branch_artifacts_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.branch_artifacts_history
        (id, artifact_id, branch_id, wr_id, artifact_type, content,
         parent_artifact_id, score, created_at, recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.artifact_id, NEW.branch_id, NEW.wr_id, NEW.artifact_type,
         NEW.content, NEW.parent_artifact_id, NEW.score, NEW.created_at,
         NOW(), '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: branches_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branches_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.branches_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION branches_delete_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.branches_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: branches_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branches_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.branches_id_seq'));
    INSERT INTO vision.branches_history
        (id, branch_id, wr_id, parent_branch_id, fork_point, label, score, status, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.branch_id, NEW.wr_id, NEW.parent_branch_id, NEW.fork_point,
         NEW.label, NEW.score, COALESCE(NEW.status, 'active'), COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION branches_insert_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.branches_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: branches_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branches_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD; BEGIN
    UPDATE vision.branches_history SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.branches_history
        (id, branch_id, wr_id, parent_branch_id, fork_point, label, score, status, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.branch_id, NEW.wr_id, NEW.parent_branch_id, NEW.fork_point,
         NEW.label, NEW.score, NEW.status, OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, branch_id, wr_id, parent_branch_id, fork_point, label,
              score, status, created_at, recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: FUNCTION branches_update_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.branches_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: branches_view_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branches_view_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.branches_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: branches_view_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branches_view_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.branches_id_seq'));
    INSERT INTO vision.branches_history
        (id, branch_id, wr_id, parent_branch_id, fork_point, label, score, status, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.branch_id, NEW.wr_id, NEW.parent_branch_id, NEW.fork_point,
         NEW.label, NEW.score, COALESCE(NEW.status, 'active'),
         COALESCE(NEW.created_at, NOW()), NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: branches_view_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.branches_view_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.branches_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.branches_history
        (id, branch_id, wr_id, parent_branch_id, fork_point, label, score, status, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.branch_id, NEW.wr_id, NEW.parent_branch_id, NEW.fork_point,
         NEW.label, NEW.score, NEW.status, NEW.created_at,
         NOW(), '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: check_receipt_integrity(integer); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.check_receipt_integrity(p_threshold_seconds integer DEFAULT 1800) RETURNS TABLE(kind text, plan_id text, ticket_id text, receipt_id text, detail text)
    LANGUAGE plpgsql STABLE
    AS $$
        BEGIN
          /* ── 1. Stuck ticket on deleted plan with NO terminal receipt ── */
          RETURN QUERY
          SELECT
            'STUCK_OPEN_TICKET_NO_TERMINAL_RECEIPT'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) on deleted plan %s has no terminal receipt',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM vision.tickets t
          JOIN nebula.plans p ON p.id = t.plan_id
          WHERE t.status IN ('open','claimed','stale','failed')
            AND p.deleted = 1
            AND NOT EXISTS (
              SELECT 1 FROM vision.receipts r
              WHERE r.plan_id = t.plan_id
                AND vision.is_terminal_receipt_type(r.type)
            );

          /* ── 2. Receipt whose plan row is gone AND no requirements link ── */
          RETURN QUERY
          SELECT
            'ORPHAN_RECEIPT_NO_PLAN'::text AS kind,
            r.plan_id::text AS plan_id,
            NULL::text AS ticket_id,
            r.id::text AS receipt_id,
            format(
              'receipt %s type=%s plan=%s has no live nebula.plans row and no conduit_plan_id linkage',
              r.id, r.type, r.plan_id
            ) AS detail
          FROM vision.receipts r
          WHERE NOT EXISTS (
            SELECT 1 FROM nebula.plans p
            WHERE p.id = r.plan_id AND p.deleted = 0
          )
          AND NOT EXISTS (
            SELECT 1 FROM nebula.requirements req
            WHERE req.conduit_plan_id = r.plan_id
          );

          /* ── 3. Deleted plan still has open tickets DESPITE terminal receipt ── */
          RETURN QUERY
          SELECT
            'DELETED_PLAN_HAS_OPEN_TICKETS_AFTER_TERMINAL_RECEIPT'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) left open on deleted plan %s despite terminal receipt',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM vision.tickets t
          JOIN nebula.plans p ON p.id = t.plan_id
          WHERE t.status IN ('open','claimed','stale','failed')
            AND p.deleted = 1
            AND EXISTS (
              SELECT 1 FROM vision.receipts r
              WHERE r.plan_id = t.plan_id
                AND vision.is_terminal_receipt_type(r.type)
            );

          /* ── 4. Ticket references a plan that has NO nebula.plans row at all ── */
          RETURN QUERY
          SELECT
            'ORPHAN_TICKET_NO_PLAN'::text AS kind,
            t.plan_id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'ticket %s (status=%s) references plan %s which has no row in nebula.plans',
              t.id, t.status, t.plan_id
            ) AS detail
          FROM vision.tickets t
          LEFT JOIN nebula.plans p ON p.id = t.plan_id
          WHERE p.id IS NULL
            AND t.status IN ('open','claimed','stale','failed');

          /* ── 5. Stuck-pending plan: ONLY PLAN_CREATE receipt(s), open builder ticket, age > threshold ── */
          /* Threshold comes from p_threshold_seconds parameter (default 1800s = 30 min). */
          /* Gated on deleted=0 so already-cleaned plans do not re-fire after the cleanup script. */
          RETURN QUERY
          SELECT
            'STUCK_PENDING_PLAN_AGE'::text AS kind,
            p.id::text AS plan_id,
            t.id::text AS ticket_id,
            NULL::text AS receipt_id,
            format(
              'plan %s stuck pending: only PLAN_CREATE receipt(s) for %ss (threshold=%ss), open builder ticket %s',
              p.id,
              EXTRACT(EPOCH FROM NOW() - MIN(r.created_at))::int,
              p_threshold_seconds,
              t.id
            ) AS detail
          FROM nebula.plans p
          JOIN vision.tickets t ON t.plan_id = p.id
          JOIN vision.receipts r ON r.plan_id = p.id
          WHERE p.deleted = 0
            AND t.role = 'builder'
            AND t.status IN ('open','claimed','stale','failed')
            AND r.type = 'PLAN_CREATE'
            AND NOT EXISTS (
              SELECT 1 FROM vision.receipts r2
              WHERE r2.plan_id = p.id
                AND r2.type != 'PLAN_CREATE'
            )
          GROUP BY p.id, t.id
          HAVING EXTRACT(EPOCH FROM NOW() - MIN(r.created_at))::int > p_threshold_seconds;
        END;
        $$;


--
-- Name: governance_events_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.governance_events_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.governance_events_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION governance_events_delete_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.governance_events_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: governance_events_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.governance_events_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.governance_events_id_seq'));

    INSERT INTO vision.governance_events_history
        (id, event_id, event_type, work_request_id, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.event_id, NEW.event_type, NEW.work_request_id,
         NEW.lineage_parent, NEW.payload, COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION governance_events_insert_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.governance_events_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: governance_events_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.governance_events_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD; BEGIN
    UPDATE vision.governance_events_history SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.governance_events_history
        (id, event_id, event_type, work_request_id, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.event_id, NEW.event_type, NEW.work_request_id,
         NEW.lineage_parent, NEW.payload, OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, event_id, event_type, work_request_id, lineage_parent,
              payload, created_at, recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: FUNCTION governance_events_update_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.governance_events_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: governance_events_view_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.governance_events_view_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.governance_events_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: governance_events_view_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.governance_events_view_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.governance_events_id_seq'));
    INSERT INTO vision.governance_events_history
        (id, event_id, event_type, work_request_id, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.event_id, NEW.event_type, NEW.work_request_id,
         NEW.lineage_parent, NEW.payload, COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: governance_events_view_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.governance_events_view_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.governance_events_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.governance_events_history
        (id, event_id, event_type, work_request_id, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.event_id, NEW.event_type, NEW.work_request_id,
         NEW.lineage_parent, NEW.payload, NEW.created_at,
         NOW(), '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: is_terminal_receipt_type(text); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.is_terminal_receipt_type(p_type text) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    AS $$
        BEGIN
          RETURN p_type IN ('REVIEW_PASS','BLOCK','PLAN_BLOCK','CANCELLED','ABANDONED');
        END;
        $$;


--
-- Name: lifecycle_events_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.lifecycle_events_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.lifecycle_events_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION lifecycle_events_delete_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.lifecycle_events_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: lifecycle_events_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.lifecycle_events_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.lifecycle_events_id_seq'));

    INSERT INTO vision.lifecycle_events_history
        (id, event_id, wr_id, from_state, to_state, actor, reason, metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.event_id, NEW.wr_id, NEW.from_state, NEW.to_state,
         NEW.actor, NEW.reason, NEW.metadata, COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');

    -- Bridge onto NOTIFY channel for observability subscriber
    PERFORM pg_notify('vision_lifecycle_event_created', (
        jsonb_build_object(
            'event_id',       NEW.event_id,
            'wr_id',          NEW.wr_id,
            'from_state',     NEW.from_state,
            'to_state',       NEW.to_state,
            'actor',          NEW.actor,
            'reason',         NEW.reason,
            'timestamp',      COALESCE(NEW.created_at, NOW()),
            'aggregate_type', 'lifecycle',
            'aggregate_id',   NEW.wr_id
        )
    )::text);

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION lifecycle_events_insert_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.lifecycle_events_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: lifecycle_events_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.lifecycle_events_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD; BEGIN
    UPDATE vision.lifecycle_events_history SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.lifecycle_events_history
        (id, event_id, wr_id, from_state, to_state, actor, reason, metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.event_id, NEW.wr_id, NEW.from_state, NEW.to_state,
         NEW.actor, NEW.reason, NEW.metadata, OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, event_id, wr_id, from_state, to_state, actor, reason,
              metadata, created_at, recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: FUNCTION lifecycle_events_update_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.lifecycle_events_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: lifecycle_events_view_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.lifecycle_events_view_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.lifecycle_events_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: lifecycle_events_view_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.lifecycle_events_view_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.lifecycle_events_id_seq'));
    INSERT INTO vision.lifecycle_events_history
        (id, event_id, wr_id, from_state, to_state, actor, reason, metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.event_id, NEW.wr_id, NEW.from_state, NEW.to_state,
         NEW.actor, NEW.reason, NEW.metadata, COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: lifecycle_events_view_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.lifecycle_events_view_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.lifecycle_events_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.lifecycle_events_history
        (id, event_id, wr_id, from_state, to_state, actor, reason, metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.event_id, NEW.wr_id, NEW.from_state, NEW.to_state,
         NEW.actor, NEW.reason, NEW.metadata, NEW.created_at,
         NOW(), '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: receipt_ingest_records_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.receipt_ingest_records_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.receipt_ingest_records_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION receipt_ingest_records_delete_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.receipt_ingest_records_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: receipt_ingest_records_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.receipt_ingest_records_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.receipt_ingest_records_id_seq'));

    INSERT INTO vision.receipt_ingest_records_history
        (id, receipt_id, work_request_id, executor_id, receipt_hash,
         result, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.receipt_id, NEW.work_request_id, NEW.executor_id,
         NEW.receipt_hash, NEW.result, NEW.lineage_parent, NEW.payload,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');

    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION receipt_ingest_records_insert_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.receipt_ingest_records_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: receipt_ingest_records_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.receipt_ingest_records_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD; BEGIN
    UPDATE vision.receipt_ingest_records_history SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.receipt_ingest_records_history
        (id, receipt_id, work_request_id, executor_id, receipt_hash,
         result, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.receipt_id, NEW.work_request_id, NEW.executor_id,
         NEW.receipt_hash, NEW.result, NEW.lineage_parent, NEW.payload,
         OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, receipt_id, work_request_id, executor_id, receipt_hash,
              result, lineage_parent, payload, created_at,
              recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: FUNCTION receipt_ingest_records_update_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.receipt_ingest_records_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: receipt_ingest_records_view_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.receipt_ingest_records_view_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.receipt_ingest_records_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: receipt_ingest_records_view_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.receipt_ingest_records_view_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.receipt_ingest_records_id_seq'));
    INSERT INTO vision.receipt_ingest_records_history
        (id, receipt_id, work_request_id, executor_id, receipt_hash,
         result, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.receipt_id, NEW.work_request_id, NEW.executor_id,
         NEW.receipt_hash, NEW.result, NEW.lineage_parent, NEW.payload,
         COALESCE(NEW.created_at, NOW()), NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: receipt_ingest_records_view_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.receipt_ingest_records_view_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.receipt_ingest_records_history 
    SET recorded_until_dt = NOW() 
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.receipt_ingest_records_history
        (id, receipt_id, work_request_id, executor_id, receipt_hash,
         result, lineage_parent, payload, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.receipt_id, NEW.work_request_id, NEW.executor_id,
         NEW.receipt_hash, NEW.result, NEW.lineage_parent, NEW.payload,
         NEW.created_at, NOW(), '9999-12-31 23:59:59+00');
    RETURN NEW;
END;
$$;


--
-- Name: receipts_assign_sequence(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.receipts_assign_sequence() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
          IF NEW.sequence IS NULL THEN
            SELECT COALESCE(MAX(r.sequence), -1) + 1
            INTO NEW.sequence
            FROM vision.receipts r
            WHERE r.plan_id = NEW.plan_id;
          END IF;
          RETURN NEW;
        END;
        $$;


--
-- Name: work_request_edges_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.work_request_edges_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.work_request_edges_history
    SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: work_request_edges_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.work_request_edges_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.work_request_edges_id_seq'));
    INSERT INTO vision.work_request_edges_history
        (id, edge_id, parent_wr_id, child_wr_id, edge_type, metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, COALESCE(NEW.edge_id, gen_random_uuid()::VARCHAR(36)),
         NEW.parent_wr_id, NEW.child_wr_id,
         COALESCE(NEW.edge_type, 'depends_on'), NEW.metadata,
         COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.edge_id := COALESCE(NEW.edge_id, gen_random_uuid()::VARCHAR(36));
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: work_request_edges_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.work_request_edges_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD;
BEGIN
    UPDATE vision.work_request_edges_history
    SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.work_request_edges_history
        (id, edge_id, parent_wr_id, child_wr_id, edge_type, metadata, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.edge_id, NEW.parent_wr_id, NEW.child_wr_id,
         NEW.edge_type, NEW.metadata, OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, edge_id, parent_wr_id, child_wr_id, edge_type, metadata,
              created_at, recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: work_requests_delete_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.work_requests_delete_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    UPDATE vision.work_requests_history
    SET    recorded_until_dt = NOW()
    WHERE  id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    RETURN OLD;
END;
$$;


--
-- Name: FUNCTION work_requests_delete_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.work_requests_delete_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: work_requests_insert_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.work_requests_insert_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_id INTEGER;
BEGIN
    new_id := COALESCE(NEW.id, nextval('vision.work_requests_id_seq'));
    INSERT INTO vision.work_requests_history
        (id, wr_id, intent, constraints, priority, context, status, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (new_id, NEW.wr_id, NEW.intent, NEW.constraints, COALESCE(NEW.priority, 5),
         NEW.context, COALESCE(NEW.status, 'NEW'), COALESCE(NEW.created_at, NOW()),
         NOW(), '9999-12-31 23:59:59+00');
    NEW.id := new_id;
    NEW.created_at := COALESCE(NEW.created_at, NOW());
    NEW.recorded_on_dt := NOW();
    NEW.recorded_until_dt := '9999-12-31 23:59:59+00';
    RETURN NEW;
END;
$$;


--
-- Name: FUNCTION work_requests_insert_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.work_requests_insert_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: work_requests_update_trigger(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.work_requests_update_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE r RECORD; BEGIN
    UPDATE vision.work_requests_history SET recorded_until_dt = NOW()
    WHERE id = OLD.id AND recorded_until_dt = '9999-12-31 23:59:59+00';
    INSERT INTO vision.work_requests_history
        (id, wr_id, intent, constraints, priority, context, status, created_at,
         recorded_on_dt, recorded_until_dt)
    VALUES
        (OLD.id, NEW.wr_id, NEW.intent, NEW.constraints, NEW.priority,
         NEW.context, NEW.status, OLD.created_at,
         clock_timestamp(), '9999-12-31 23:59:59+00')
    RETURNING id, wr_id, intent, constraints, priority, context, status,
              created_at, recorded_on_dt, recorded_until_dt INTO r;
    RETURN r;
END;
$$;


--
-- Name: FUNCTION work_requests_update_trigger(); Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON FUNCTION vision.work_requests_update_trigger() IS 'DBA 2026-08-13: unattached trigger-returning function retained as historical SCD-4/view-trigger residue pending ownership review; no active trigger binding was present when migration 043 ran.';


--
-- Name: wr_compile_verdicts_immutable(); Type: FUNCTION; Schema: vision; Owner: -
--

CREATE FUNCTION vision.wr_compile_verdicts_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        BEGIN
            RAISE EXCEPTION 'vision.wr_compile_verdicts is immutable: % not allowed on verdict rows', TG_OP;
        END;
        $$;


--
-- Name: bridge_instance_to_cascade(); Type: FUNCTION; Schema: wind; Owner: -
--

CREATE FUNCTION wind.bridge_instance_to_cascade() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_event_type TEXT;
  v_payload JSONB;
  v_workflow_name TEXT;
  v_ticket_count INTEGER;
  v_wr_id TEXT;
BEGIN
  -- Only fire for terminal transitions (ACTIVE → COMPLETED or ACTIVE → FAILED)
  IF OLD.status = 'ACTIVE' AND NEW.status IN ('COMPLETED', 'FAILED') THEN
    -- Determine event type
    v_event_type := 'wind.instance.' || lower(NEW.status);

    -- Resolve workflow name
    SELECT w.name INTO v_workflow_name
    FROM wind.workflow_versions wv
    JOIN wind.workflows w ON wv.workflow_id = w.id
    WHERE wv.id = NEW.workflow_version_id;

    -- Count tickets for this instance
    SELECT COUNT(*) INTO v_ticket_count
    FROM wind.tickets
    WHERE workflow_instance_id = NEW.id;

    -- Try to find the triggering event's wr_id
    SELECT e.payload->>'wr_id' INTO v_wr_id
    FROM wind.events e
    WHERE e.id = NEW.id  -- instance ID may match event ID in some flows
       OR e.payload->>'instance_id' = NEW.id::text
    ORDER BY e.created_at DESC
    LIMIT 1;

    -- Build payload
    v_payload := jsonb_build_object(
      'instance_id', NEW.id,
      'workflow_name', v_workflow_name,
      'workflow_version_id', NEW.workflow_version_id,
      'previous_status', OLD.status,
      'ticket_count', v_ticket_count
    );

    -- Add wr_id if found
    IF v_wr_id IS NOT NULL THEN
      v_payload := v_payload || jsonb_build_object('wr_id', v_wr_id);
    END IF;

    -- Insert into cascade.events
    INSERT INTO cascade.events (
      event_type, source, event_timestamp, payload,
      aggregate_type, aggregate_id,
      actor_type, actor_id
    ) VALUES (
      v_event_type,
      'wind-srv',
      COALESCE(NEW.updated_at, NOW()),
      v_payload,
      'workflow_instance',
      NEW.id::text,
      'system',
      'wind-srv'
    );
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: bridge_ticket_to_cascade(); Type: FUNCTION; Schema: wind; Owner: -
--

CREATE FUNCTION wind.bridge_ticket_to_cascade() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_event_type TEXT;
  v_payload JSONB;
  v_node_name TEXT;
  v_title_name TEXT;
BEGIN
  IF NEW.status IN ('COMPLETED', 'CANCELLED')
     AND (OLD.status IS DISTINCT FROM NEW.status)
  THEN
    v_event_type := 'wind.ticket.' || lower(NEW.status);

    SELECT name INTO v_node_name
    FROM wind.workflow_nodes
    WHERE id = NEW.node_id AND workflow_version_id = NEW.workflow_version_id;

    SELECT display_name INTO v_title_name
    FROM wind.titles
    WHERE id = NEW.assigned_title_id;

    v_payload := jsonb_build_object(
      'ticket_id', NEW.id,
      'ticket_status', NEW.status,
      'workflow_instance_id', NEW.workflow_instance_id,
      'node_name', v_node_name,
      'node_id', NEW.node_id,
      'assigned_title_name', v_title_name,
      'assigned_title_id', NEW.assigned_title_id,
      'input_artifact_type', NEW.input_artifact_type,
      'input_artifact_id', NEW.input_artifact_id
    );

    INSERT INTO cascade.events (
      event_type, source, event_timestamp, payload,
      aggregate_type, aggregate_id,
      actor_type, actor_id
    ) VALUES (
      v_event_type,
      'wind-srv',
      COALESCE(NEW.updated_at, NOW()),
      v_payload,
      'ticket',
      NEW.id::text,
      'system',
      'wind-srv'
    );
  END IF;

  RETURN NEW;
END;
$$;


--
-- Name: trg_bridge_conduit_events(); Type: FUNCTION; Schema: wind; Owner: -
--

CREATE FUNCTION wind.trg_bridge_conduit_events() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  v_wr_id TEXT;
  v_wr_title TEXT;
  v_wind_event_type TEXT;
  v_event_id TEXT;
  v_subject TEXT;
  v_payload JSONB;
  v_notify_payload TEXT;
BEGIN
  -- Only bridge recognized WRP runtime events
  IF NEW.event_type NOT IN (
    'WR_SUBMITTED', 'WR_VALIDATED', 'WR_QUEUED',
    'WR_CLAIMED', 'WR_ACKED', 'WR_SETTLED',
    'WR_REJECTED', 'WR_FAILED', 'WR_NOOP', 'WR_DEFERRED'
  ) THEN
    RETURN NEW;
  END IF;

  -- Convert WR_UPPERCASE to wr.lowercase (strip WR_ prefix)
  v_wind_event_type := 'wr.' || lower(replace(NEW.event_type, 'WR_', ''));

  -- Get the work request identifier and title from vision schema
  SELECT wr.wr_id, wr.title INTO v_wr_id, v_wr_title
  FROM vision.work_requests wr
  WHERE wr.work_request_uuid = NEW.work_request_id::text;

  -- Build subject
  v_subject := 'nexus.wind.v1.events.' || v_wind_event_type;

  -- Build event payload
  v_payload := jsonb_build_object(
    'wr_id', COALESCE(v_wr_id, NEW.work_request_id::text),
    'title', v_wr_title,
    'event_type', NEW.event_type,
    'event_id', NEW.event_id,
    'actor_type', NEW.actor_type,
    'actor_id', NEW.actor_id
  ) || COALESCE(NEW.payload, '{}'::jsonb);

  -- Insert into wind.events
  INSERT INTO wind.events (event_type, subject, payload, source, metadata)
  VALUES (
    v_wind_event_type,
    v_subject,
    v_payload,
    'conduit-runtime',
    jsonb_build_object(
      'conduit_event_type', NEW.event_type,
      'conduit_event_id', NEW.event_id,
      'conduit_work_request_id', NEW.work_request_id
    )
  )
  RETURNING id::text INTO v_event_id;

  -- Notify wind-srv so it can publish to NATS in real-time
  v_notify_payload := jsonb_build_object(
    'event_id', v_event_id,
    'event_type', v_wind_event_type,
    'subject', v_subject,
    'source', 'conduit-runtime',
    'payload', v_payload
  )::text;

  PERFORM pg_notify('wind_event_bridge', v_notify_payload);

  RETURN NEW;
END;
$$;


--
-- Name: concept; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.concept (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: events; Type: TABLE; Schema: cascade; Owner: -
--

CREATE TABLE cascade.events (
    event_id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_type text NOT NULL,
    source text NOT NULL,
    event_timestamp timestamp with time zone NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    aggregate_type text,
    aggregate_id text,
    actor_type text DEFAULT 'system'::text NOT NULL,
    actor_id text DEFAULT ''::text NOT NULL,
    correlation_id uuid,
    causation_id uuid,
    caused_by_event_type text,
    sequence_number bigint NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: events_sequence_number_seq; Type: SEQUENCE; Schema: cascade; Owner: -
--

ALTER TABLE cascade.events ALTER COLUMN sequence_number ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME cascade.events_sequence_number_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: lineage_edges; Type: TABLE; Schema: cascade; Owner: -
--

CREATE TABLE cascade.lineage_edges (
    id bigint NOT NULL,
    source_type text NOT NULL,
    source_id text NOT NULL,
    target_type text NOT NULL,
    target_id text NOT NULL,
    relationship text NOT NULL,
    created_by text DEFAULT 'system'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: lineage_edges_id_seq; Type: SEQUENCE; Schema: cascade; Owner: -
--

CREATE SEQUENCE cascade.lineage_edges_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: lineage_edges_id_seq; Type: SEQUENCE OWNED BY; Schema: cascade; Owner: -
--

ALTER SEQUENCE cascade.lineage_edges_id_seq OWNED BY cascade.lineage_edges.id;


--
-- Name: processing_offsets; Type: TABLE; Schema: cascade; Owner: -
--

CREATE TABLE cascade.processing_offsets (
    subscriber_id text NOT NULL,
    last_timestamp timestamp with time zone,
    processed_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: subscriptions; Type: TABLE; Schema: cascade; Owner: -
--

CREATE TABLE cascade.subscriptions (
    subject_pattern text NOT NULL,
    handler_name text NOT NULL,
    description text,
    enabled boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: agent_budgets; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.agent_budgets (
    agent_role text NOT NULL,
    ceiling_usd double precision,
    ceiling_tokens integer,
    current_usd double precision DEFAULT 0 NOT NULL,
    current_tokens integer DEFAULT 0 NOT NULL,
    reset_period text DEFAULT 'monthly'::text NOT NULL,
    reset_at timestamp with time zone,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT agent_budgets_reset_period_check CHECK ((reset_period = ANY (ARRAY['daily'::text, 'weekly'::text, 'monthly'::text])))
);


--
-- Name: bridge_checkpoint; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.bridge_checkpoint (
    id integer DEFAULT 1 NOT NULL,
    last_id text DEFAULT ''::text NOT NULL,
    last_recorded_on_dt timestamp with time zone NOT NULL,
    last_polled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT bridge_checkpoint_id_check CHECK ((id = 1))
);


--
-- Name: circuit_breaker; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.circuit_breaker (
    id integer DEFAULT 1 NOT NULL,
    tripped integer DEFAULT 0,
    tripped_at timestamp with time zone,
    retry_after integer DEFAULT 1800,
    error text,
    detail text,
    source text,
    fallback_model text,
    paused integer DEFAULT 0,
    max_retries_per_model integer DEFAULT 3,
    retry_delay_seconds integer DEFAULT 120,
    max_fallbacks integer DEFAULT 3,
    push_back_to_pending integer DEFAULT 1,
    updated_at timestamp with time zone,
    wake_requested_at timestamp with time zone,
    CONSTRAINT circuit_breaker_id_check CHECK ((id = 1))
);


--
-- Name: cost_logs; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.cost_logs (
    id bigint NOT NULL,
    session_id text NOT NULL,
    ticket_id text,
    model text NOT NULL,
    input_tokens integer DEFAULT 0 NOT NULL,
    output_tokens integer DEFAULT 0 NOT NULL,
    estimated_cost_usd real,
    actual_cost_usd real,
    recorded_at timestamp with time zone NOT NULL,
    tags text DEFAULT '[]'::text NOT NULL
);


--
-- Name: cost_logs_id_seq; Type: SEQUENCE; Schema: conduit; Owner: -
--

CREATE SEQUENCE conduit.cost_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cost_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: conduit; Owner: -
--

ALTER SEQUENCE conduit.cost_logs_id_seq OWNED BY conduit.cost_logs.id;


--
-- Name: kernel_delta_log; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.kernel_delta_log (
    delta_id text NOT NULL,
    batch_id text NOT NULL,
    payload jsonb NOT NULL,
    version integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT kernel_delta_log_version_check CHECK ((version >= 0))
);


--
-- Name: kernel_snapshot; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.kernel_snapshot (
    version integer NOT NULL,
    state jsonb NOT NULL,
    identity_hash text,
    graph_hash text,
    lineage_cursor integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT kernel_snapshot_version_check CHECK ((version >= 0))
);


--
-- Name: lineage_log; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.lineage_log (
    id integer NOT NULL,
    version integer NOT NULL,
    delta_id text NOT NULL,
    step text NOT NULL,
    event_type text DEFAULT 'apply'::text NOT NULL,
    affected_plans text DEFAULT '[]'::text NOT NULL,
    detail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: lineage_log_id_seq; Type: SEQUENCE; Schema: conduit; Owner: -
--

CREATE SEQUENCE conduit.lineage_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: lineage_log_id_seq; Type: SEQUENCE OWNED BY; Schema: conduit; Owner: -
--

ALTER SEQUENCE conduit.lineage_log_id_seq OWNED BY conduit.lineage_log.id;


--
-- Name: model_pricing; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.model_pricing (
    model_name text NOT NULL,
    provider text NOT NULL,
    input_price_per_token double precision NOT NULL,
    output_price_per_token double precision NOT NULL,
    cache_hit_price double precision,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: pipeline_cursor; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.pipeline_cursor (
    role text NOT NULL,
    last_processed_plan_id text,
    last_work_request_id text,
    updated_at timestamp with time zone NOT NULL
);


--
-- Name: role_circuit_breaker; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.role_circuit_breaker (
    role text NOT NULL,
    tripped integer DEFAULT 1 NOT NULL,
    tripped_at timestamp with time zone,
    retry_after integer,
    error text,
    detail text,
    source text,
    failure_count integer DEFAULT 1,
    updated_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: schema_version; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.schema_version (
    version integer NOT NULL,
    description text NOT NULL,
    applied_at timestamp with time zone NOT NULL
);


--
-- Name: sessions; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.sessions (
    id text NOT NULL,
    agent_role text NOT NULL,
    start_iso timestamp with time zone NOT NULL,
    end_iso timestamp with time zone,
    exit_code integer,
    retries_used integer DEFAULT 0,
    plans_processed text DEFAULT '[]'::text NOT NULL,
    plan_count integer DEFAULT 0,
    pid integer,
    is_running integer DEFAULT 1,
    last_activity timestamp with time zone,
    model text,
    fallback_used integer DEFAULT 0,
    cost_usd real,
    total_work_seconds real DEFAULT 0 NOT NULL,
    workflow_id text,
    run_id text,
    workflow_start_time timestamp with time zone,
    workflow_close_time timestamp with time zone,
    workflow_run_time_ms real,
    workflow_result text,
    created_at timestamp with time zone NOT NULL,
    tags text DEFAULT '[]'::text NOT NULL,
    last_heartbeat_at timestamp with time zone
);


--
-- Name: work_request_events; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.work_request_events (
    event_id uuid DEFAULT gen_random_uuid() NOT NULL,
    work_request_id uuid NOT NULL,
    event_type text NOT NULL,
    event_version integer DEFAULT 1 NOT NULL,
    correlation_id uuid,
    causation_id uuid,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    actor_type text DEFAULT 'system'::text NOT NULL,
    actor_id text DEFAULT ''::text NOT NULL,
    sequence_number bigint NOT NULL,
    CONSTRAINT work_request_events_event_type_check CHECK ((event_type = ANY (ARRAY['WORKREQUEST.CREATED'::text, 'VISION.IR_PRODUCED'::text, 'STATE.TRANSITION_PROPOSED'::text, 'STATE.TRANSITION_APPROVED'::text, 'STATE.TRANSITION_COMMITTED'::text, 'EXECUTION.STARTED'::text, 'EXECUTION.COMPLETED'::text, 'EXECUTION.FAILED'::text, 'SYSTEM.CRON_TRIGGERED'::text, 'WR_SUBMITTED'::text, 'WR_VALIDATED'::text, 'WR_QUEUED'::text, 'WR_CLAIMED'::text, 'WR_ACKED'::text, 'WR_SETTLED'::text, 'WR_REJECTED'::text, 'WR_FAILED'::text, 'WR_NOOP'::text, 'WR_DEFERRED'::text])))
);


--
-- Name: work_request_events_sequence_number_seq; Type: SEQUENCE; Schema: conduit; Owner: -
--

CREATE SEQUENCE conduit.work_request_events_sequence_number_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: work_request_events_sequence_number_seq; Type: SEQUENCE OWNED BY; Schema: conduit; Owner: -
--

ALTER SEQUENCE conduit.work_request_events_sequence_number_seq OWNED BY conduit.work_request_events.sequence_number;


--
-- Name: work_request_state; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.work_request_state (
    work_request_id uuid NOT NULL,
    current_state text DEFAULT 'PROPOSED'::text NOT NULL,
    vision_stage text,
    vision_ir_version integer DEFAULT 0 NOT NULL,
    last_event_id uuid,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT work_request_state_current_state_check CHECK ((current_state = ANY (ARRAY['PROPOSED'::text, 'PLANNING'::text, 'PENDING'::text, 'IMPLEMENTING'::text, 'REVIEW'::text, 'COMPLETED'::text, 'FAILED'::text, 'CANCELLED'::text]))),
    CONSTRAINT work_request_state_vision_stage_check CHECK ((vision_stage = ANY (ARRAY['PLAN_IR'::text, 'SPEC_IR'::text, 'EXECUTION_IR'::text, 'VALIDATION_IR'::text])))
);


--
-- Name: work_requests; Type: TABLE; Schema: conduit; Owner: -
--

CREATE TABLE conduit.work_requests (
    id text NOT NULL,
    plan_id text,
    title text DEFAULT ''::text NOT NULL,
    status text NOT NULL,
    dco_json text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    step_outputs text DEFAULT '{}'::text NOT NULL,
    asset_id uuid,
    entity_key text
);


--
-- Name: attempts; Type: TABLE; Schema: execution; Owner: -
--

CREATE TABLE execution.attempts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    lease_id uuid NOT NULL,
    request_id uuid NOT NULL,
    executor_id text NOT NULL,
    status text DEFAULT 'CREATED'::text NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    result jsonb DEFAULT '{}'::jsonb NOT NULL,
    error text,
    exit_code integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT attempts_status_check CHECK ((status = ANY (ARRAY['CREATED'::text, 'RUNNING'::text, 'SUCCEEDED'::text, 'FAILED'::text, 'TIMED_OUT'::text])))
);


--
-- Name: leases; Type: TABLE; Schema: execution; Owner: -
--

CREATE TABLE execution.leases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    request_id uuid NOT NULL,
    executor_id text NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    ttl_seconds integer DEFAULT 300 NOT NULL,
    acquired_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    released_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT leases_status_check CHECK ((status = ANY (ARRAY['ACTIVE'::text, 'EXPIRED'::text, 'RELEASED'::text])))
);


--
-- Name: receipts; Type: TABLE; Schema: execution; Owner: -
--

CREATE TABLE execution.receipts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    attempt_id uuid,
    request_id uuid NOT NULL,
    type text NOT NULL,
    agent_role text DEFAULT ''::text NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    lineage_source text,
    lineage_original_id text,
    issued_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_execution_receipts_type CHECK ((type = ANY (ARRAY['ABANDONED'::text, 'API_LIMIT'::text, 'BLOCK'::text, 'CANCELLED'::text, 'CCNF_EXECUTION'::text, 'CRITIQUE'::text, 'CRITIQUE_PASS'::text, 'CRITIQUE_REJECT'::text, 'EXECUTION_COMPLETE'::text, 'HOLD'::text, 'IMPLEMENTATION'::text, 'PLANNING'::text, 'PLAN_BLOCK'::text, 'PLAN_CREATE'::text, 'PROPOSED'::text, 'REQUEUED'::text, 'REVIEW'::text, 'REVIEW_PASS'::text, 'REVIEW_REJECT'::text]))),
    CONSTRAINT receipts_type_check CHECK ((type = ANY (ARRAY['API_LIMIT'::text, 'BLOCK'::text, 'CANCELLED'::text, 'EXECUTION_COMPLETE'::text, 'HOLD'::text, 'IMPLEMENTATION'::text, 'PLAN_CREATE'::text, 'PLANNING'::text, 'PROPOSED'::text, 'REQUEUED'::text, 'REVIEW'::text, 'REVIEW_PASS'::text, 'REVIEW_REJECT'::text])))
);


--
-- Name: requests; Type: TABLE; Schema: execution; Owner: -
--

CREATE TABLE execution.requests (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    business_key text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    intent_type text DEFAULT 'task'::text NOT NULL,
    objective text DEFAULT ''::text NOT NULL,
    inputs jsonb DEFAULT '{}'::jsonb NOT NULL,
    deterministic boolean DEFAULT true NOT NULL,
    max_retries integer,
    timeout_policy text,
    resource_hints text[] DEFAULT '{}'::text[],
    op_trace jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'DRAFT'::text NOT NULL,
    source_plan_id text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_wr_id uuid,
    CONSTRAINT requests_status_check CHECK ((status = ANY (ARRAY['DRAFT'::text, 'COMPILED'::text, 'VALIDATED'::text, 'ADMITTED'::text, 'READY'::text, 'COMPLETED'::text, 'FAILED'::text, 'CANCELLED'::text])))
);


--
-- Name: agendas_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.agendas_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    scope text,
    status text DEFAULT 'draft'::text NOT NULL,
    cohesion_score numeric(4,3),
    overlap_matrix jsonb,
    source_count integer,
    planner_analysis text,
    planner_conflicts jsonb,
    planner_gaps jsonb,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT agendas_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'ready_for_review'::text, 'in_review'::text, 'specified'::text, 'archived'::text])))
);


--
-- Name: TABLE agendas_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.agendas_history IS 'Deliberation sessions for resolving open questions. Created by Planner when requirements have ambiguities.';


--
-- Name: specifications_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.specifications_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agenda_id uuid NOT NULL,
    revision_number integer NOT NULL,
    revision_type text NOT NULL,
    superseded_by uuid,
    derived_from uuid[] DEFAULT '{}'::uuid[] NOT NULL,
    item_snapshot jsonb DEFAULT '[]'::jsonb NOT NULL,
    change_summary text,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT specifications_revision_type_check CHECK ((revision_type = ANY (ARRAY['created'::text, 'revised'::text, 'merged'::text, 'split'::text, 'retired'::text])))
);

ALTER TABLE ONLY nebula.specifications_history REPLICA IDENTITY FULL;


--
-- Name: active_specifications; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.active_specifications AS
 SELECT s.id,
    s.agenda_id,
    s.revision_number,
    s.revision_type,
    s.superseded_by,
    s.derived_from,
    s.item_snapshot,
    s.change_summary,
    s.valid_from,
    s.valid_until,
    s.created_at,
    a.title AS agenda_title,
    a.status AS agenda_status
   FROM (nebula.specifications_history s
     JOIN nebula.agendas_history a ON ((a.id = s.agenda_id)))
  WHERE ((now() >= s.valid_from) AND (now() < s.valid_until));


--
-- Name: agenda_item_questions; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.agenda_item_questions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agenda_item_id uuid NOT NULL,
    open_question_id uuid NOT NULL,
    contributed_by text NOT NULL,
    contributed_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: TABLE agenda_item_questions; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.agenda_item_questions IS 'Links open questions to agenda items for deliberation.';


--
-- Name: agenda_items_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.agenda_items_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agenda_id uuid NOT NULL,
    source_type text NOT NULL,
    source_id uuid NOT NULL,
    title text NOT NULL,
    body text,
    decisions jsonb DEFAULT '[]'::jsonb,
    open_questions jsonb DEFAULT '[]'::jsonb,
    supporting_refs jsonb DEFAULT '[]'::jsonb,
    included boolean,
    planner_note text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: TABLE agenda_items_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.agenda_items_history IS 'Links open questions to agendas for deliberation.';


--
-- Name: agenda_items; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.agenda_items AS
 SELECT id,
    agenda_id,
    source_type,
    source_id,
    title,
    body,
    decisions,
    open_questions,
    supporting_refs,
    included,
    planner_note,
    created_at,
    updated_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.agenda_items_history aih
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: agendas; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.agendas AS
 SELECT id,
    title,
    scope,
    status,
    cohesion_score,
    overlap_matrix,
    source_count,
    planner_analysis,
    planner_conflicts,
    planner_gaps,
    metadata,
    created_at,
    updated_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.agendas_history ah
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: agent_records_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.agent_records_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    record_type text NOT NULL,
    role text DEFAULT ''::text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    source_path text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    system_id uuid,
    subsystem_id uuid,
    feature_id uuid,
    plan_ref text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    level integer DEFAULT 1 NOT NULL,
    visibility_scope text DEFAULT 'all'::text NOT NULL,
    model text,
    candidate_id uuid,
    requirement_id uuid,
    CONSTRAINT agent_records_record_type_check CHECK ((record_type = ANY (ARRAY['report'::text, 'analysis'::text, 'assessment'::text, 'inspection'::text, 'prompt'::text, 'response'::text, 'engineering_log'::text, 'architecture_note'::text, 'decision'::text]))),
    CONSTRAINT agent_records_role_check CHECK (((role = ''::text) OR (role = ANY (ARRAY['architect'::text, 'planner'::text, 'builder'::text, 'reviewer'::text, 'critic'::text, 'analyst'::text, 'inspector'::text, 'engineer'::text, 'engineer-ii'::text, 'devops'::text, 'topologist'::text, 'auditor'::text, 'dba'::text, 'epistemologist'::text, 'operator'::text, 'sysadmin'::text, 'DBA'::text, 'tester'::text, 'analyst-ii'::text, 'design-synthesist'::text, 'layout-mechanic'::text])))),
    CONSTRAINT chk_agent_records_level CHECK (((level >= 1) AND (level <= 4)))
);


--
-- Name: COLUMN agent_records_history.candidate_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.agent_records_history.candidate_id IS 'FK to nebula.harvest_candidates.id — populated during promotion-flow stage-3.';


--
-- Name: COLUMN agent_records_history.requirement_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.agent_records_history.requirement_id IS 'FK to nebula.requirements.id — populated when a requirement spawns from a candidate.';


--
-- Name: agent_records; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.agent_records AS
 SELECT id,
    record_type,
    role,
    title,
    content,
    source_path,
    metadata,
    tags,
    system_id,
    subsystem_id,
    feature_id,
    plan_ref,
    candidate_id,
    requirement_id,
    created_at,
    level,
    visibility_scope,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    model
   FROM nebula.agent_records_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: architect_specs_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.architect_specs_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    requirement_id uuid NOT NULL,
    work_request_id uuid,
    content jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: TABLE architect_specs_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.architect_specs_history IS 'Architect specifications — audit trail for requirement analysis. Written by architect_process_todo cron.';


--
-- Name: architect_specs; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.architect_specs AS
 SELECT id,
    title,
    requirement_id,
    work_request_id,
    content,
    metadata,
    created_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.architect_specs_history ash
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: artifact_provenance_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.artifact_provenance_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subject_type text NOT NULL,
    subject_id uuid NOT NULL,
    source_type text NOT NULL,
    source_id uuid NOT NULL,
    source_version text,
    relationship text DEFAULT 'derived_from'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: TABLE artifact_provenance_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.artifact_provenance_history IS 'Lightweight version-level provenance: which exact source artifact did a derived object come from? Avoids composite temporal FKs while preserving the "which version" question.';


--
-- Name: artifact_provenance; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.artifact_provenance AS
 SELECT id,
    subject_type,
    subject_id,
    source_type,
    source_id,
    source_version,
    relationship,
    metadata,
    created_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.artifact_provenance_history aph
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: assessment_resolutions_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.assessment_resolutions_history (
    resolution_id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_id uuid NOT NULL,
    outcome text NOT NULL,
    confidence double precision,
    rationale jsonb,
    dimensions_used integer,
    dimensions_total integer,
    resolved_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: assessment_resolutions; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.assessment_resolutions AS
 SELECT resolution_id,
    event_id,
    outcome,
    confidence,
    rationale,
    dimensions_used,
    dimensions_total,
    resolved_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.assessment_resolutions_history arh
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: assessments_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.assessments_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    observation_id uuid NOT NULL,
    outcome text NOT NULL,
    confidence numeric(4,3),
    impact_scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    open_questions jsonb DEFAULT '[]'::jsonb NOT NULL,
    agenda_id uuid,
    auto_resolve_plan_id uuid,
    analysis_detail text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    forum_post_id uuid,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT assessments_outcome_check CHECK ((outcome = ANY (ARRAY['informational'::text, 'recommendation'::text, 'needs_deliberation'::text, 'policy_blocked'::text, 'auto_resolved'::text, 'rejected'::text])))
);


--
-- Name: TABLE assessments_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.assessments_history IS 'Captures automated analysis of an observation.';


--
-- Name: COLUMN assessments_history.outcome; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.assessments_history.outcome IS 'auto_resolved: system handled it, auto_resolve_plan_id set. needs_deliberation: requires organizational decision, agenda_id set. informational: awareness only, forum_post_id set. rejected: the trigger was invalid or below threshold.';


--
-- Name: COLUMN assessments_history.forum_post_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.assessments_history.forum_post_id IS 'Set when outcome=informational: an Assembly forum post was created for awareness (no agenda needed).';


--
-- Name: assessments; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.assessments AS
 SELECT id,
    observation_id,
    outcome,
    confidence,
    impact_scope,
    open_questions,
    agenda_id,
    auto_resolve_plan_id,
    analysis_detail,
    created_at,
    forum_post_id,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.assessments_history ah
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: audit_files_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.audit_files_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    file_path text NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    size_bytes integer DEFAULT 0 NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);


--
-- Name: audit_files; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.audit_files AS
 SELECT id,
    file_path,
    content,
    size_bytes,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until
   FROM nebula.audit_files_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: candidate_dependencies; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.candidate_dependencies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    candidate_id uuid NOT NULL,
    depends_on_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT candidate_dependencies_check CHECK ((candidate_id <> depends_on_id))
);


--
-- Name: candidate_segment_sets; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.candidate_segment_sets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    candidate_id uuid NOT NULL,
    segment_set_id uuid NOT NULL,
    role text DEFAULT 'primary'::text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT candidate_segment_sets_role_check CHECK ((role = ANY (ARRAY['primary'::text, 'supporting'::text])))
);


--
-- Name: harvest_candidates_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.harvest_candidates_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    harvest_id uuid NOT NULL,
    title text NOT NULL,
    intent_description text,
    implementation_notes jsonb DEFAULT '[]'::jsonb NOT NULL,
    code_snippets jsonb DEFAULT '[]'::jsonb NOT NULL,
    open_questions jsonb DEFAULT '[]'::jsonb NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    status text,
    system_id uuid,
    subsystem_id uuid,
    feature_id uuid,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    work_request_id uuid,
    completed boolean DEFAULT false NOT NULL,
    compilation_readiness numeric(4,3),
    type text DEFAULT 'requirement'::text NOT NULL,
    design_rationale jsonb DEFAULT '[]'::jsonb NOT NULL,
    provenance_block_indices jsonb DEFAULT '[]'::jsonb NOT NULL,
    needs_new_node boolean DEFAULT false NOT NULL,
    proposed_parent text,
    proposed_name text,
    placement_reason text,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    asset_id uuid,
    CONSTRAINT harvest_candidates_status_check CHECK (((status IS NULL) OR (status = ANY (ARRAY['pending'::text, 'linked'::text, 'useful'::text, 'rejected'::text, 'promoted'::text, 'superseded'::text, 'approved'::text, 'struck'::text, 'reviewed'::text, 'discarded'::text, 'active'::text])))),
    CONSTRAINT hc_type_check CHECK ((type = ANY (ARRAY['requirement'::text, 'principle'::text, 'rejected_alternative'::text, 'tension'::text, 'rationale'::text, 'mixed'::text])))
);


--
-- Name: COLUMN harvest_candidates_history.status; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.status IS 'Candidate lifecycle: pending -> linked (to system) -> useful (reviewed) -> promoted (-> plan) | rejected (discarded)';


--
-- Name: COLUMN harvest_candidates_history.type; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.type IS 'Candidate type: requirement, principle, rejected_alternative, tension, rationale, or mixed';


--
-- Name: COLUMN harvest_candidates_history.design_rationale; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.design_rationale IS 'Stated principles, rejected alternatives, or reasoning that shaped a decision — even when no concrete action item follows';


--
-- Name: COLUMN harvest_candidates_history.provenance_block_indices; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.provenance_block_indices IS 'List of DockLang block indices that support this candidate (may be non-contiguous)';


--
-- Name: COLUMN harvest_candidates_history.needs_new_node; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.needs_new_node IS 'True when Operation 2B cannot find a clean hierarchy match';


--
-- Name: COLUMN harvest_candidates_history.proposed_parent; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.proposed_parent IS 'Proposed parent node for needs_new_node candidates';


--
-- Name: COLUMN harvest_candidates_history.proposed_name; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.proposed_name IS 'Proposed name for needs_new_node candidates';


--
-- Name: COLUMN harvest_candidates_history.placement_reason; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.harvest_candidates_history.placement_reason IS 'Reason why needs_new_node was flagged';


--
-- Name: candidate_status_summary; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.candidate_status_summary AS
 SELECT status,
    count(*) AS count,
    count(*) FILTER (WHERE (system_id IS NOT NULL)) AS linked_to_system,
    (min(created_at))::date AS earliest,
    (max(created_at))::date AS latest
   FROM nebula.harvest_candidates_history
  GROUP BY status
  ORDER BY status;


--
-- Name: conversation_blocks_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.conversation_blocks_history (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    block_index integer NOT NULL,
    parent_turn_id text,
    parent_block_id uuid,
    block_type text DEFAULT 'paragraph'::text NOT NULL,
    content_md text DEFAULT ''::text NOT NULL,
    content_hash text DEFAULT ''::text NOT NULL,
    dom_path text,
    dom_fingerprint text,
    first_line_no integer,
    last_line_no integer,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    as_of_dt timestamp with time zone DEFAULT now() NOT NULL,
    expiration_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    role text
);


--
-- Name: COLUMN conversation_blocks_history.role; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.conversation_blocks_history.role IS 'Speaker role: "user" or "assistant". Populated from docklang discourse_unit.role.';


--
-- Name: conversation_blocks; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.conversation_blocks AS
 SELECT id,
    conversation_id,
    snapshot_id,
    block_index,
    parent_turn_id,
    parent_block_id,
    block_type,
    content_md,
    content_hash,
    dom_path,
    dom_fingerprint,
    first_line_no,
    last_line_no,
    created_at,
    role
   FROM nebula.conversation_blocks_history
  WHERE ((now() >= as_of_dt) AND (now() < expiration_dt));


--
-- Name: conversation_block_stats; Type: MATERIALIZED VIEW; Schema: nebula; Owner: -
--

CREATE MATERIALIZED VIEW nebula.conversation_block_stats AS
 SELECT conversation_id,
    COALESCE(jsonb_object_agg(block_type, cnt), '{}'::jsonb) AS type_counts,
    (sum(cnt))::integer AS block_count,
    (COALESCE(sum(cnt) FILTER (WHERE (block_type = 'code'::text)), (0)::bigint))::integer AS code_block_count
   FROM ( SELECT conversation_blocks.conversation_id,
            conversation_blocks.block_type,
            (count(*))::integer AS cnt
           FROM nebula.conversation_blocks
          GROUP BY conversation_blocks.conversation_id, conversation_blocks.block_type) t
  GROUP BY conversation_id
  WITH NO DATA;


--
-- Name: MATERIALIZED VIEW conversation_block_stats; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON MATERIALIZED VIEW nebula.conversation_block_stats IS 'Pre-computed block type statistics per conversation. Refresh with: REFRESH MATERIALIZED VIEW CONCURRENTLY nebula.conversation_block_stats';


--
-- Name: conversation_snapshots_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.conversation_snapshots_history (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    snapshot_index integer NOT NULL,
    source_hash text NOT NULL,
    capture_mode text DEFAULT 'AFTER_ACTION'::text NOT NULL,
    block_count integer DEFAULT 0 NOT NULL,
    created_by text DEFAULT 'SYSTEM'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    as_of_dt timestamp with time zone DEFAULT now() NOT NULL,
    expiration_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);


--
-- Name: conversation_snapshots; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.conversation_snapshots AS
 SELECT id,
    conversation_id,
    snapshot_index,
    source_hash,
    capture_mode,
    block_count,
    created_by,
    created_at
   FROM nebula.conversation_snapshots_history
  WHERE ((now() >= as_of_dt) AND (now() < expiration_dt));


--
-- Name: cross_references_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.cross_references_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_type text NOT NULL,
    source_id text NOT NULL,
    target_type text NOT NULL,
    target_id text NOT NULL,
    rel_type text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: cross_references; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.cross_references AS
 SELECT id,
    source_type,
    source_id,
    target_type,
    target_id,
    rel_type,
    metadata,
    created_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.cross_references_history crh
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: deliberation_participants; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.deliberation_participants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    open_question_id uuid NOT NULL,
    role text NOT NULL,
    participated_at timestamp with time zone DEFAULT now() NOT NULL,
    contribution text,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: TABLE deliberation_participants; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.deliberation_participants IS 'Tracks which roles participated in deliberating open questions.';


--
-- Name: features_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.features_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    subsystem_id uuid NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    readme text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    path text,
    asset_id uuid
);


--
-- Name: features; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.features AS
 SELECT id,
    subsystem_id,
    name,
    description,
    readme,
    created_at,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    path,
    asset_id
   FROM nebula.features_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: harvest_candidates; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.harvest_candidates AS
 SELECT id,
    harvest_id,
    title,
    intent_description,
    implementation_notes,
    code_snippets,
    open_questions,
    tags,
    status,
    system_id,
    subsystem_id,
    feature_id,
    valid_from,
    valid_until,
    created_at,
    updated_at,
    work_request_id,
    completed,
    compilation_readiness,
    type,
    design_rationale,
    provenance_block_indices,
    needs_new_node,
    proposed_parent,
    proposed_name,
    placement_reason,
    recorded_on_dt,
    recorded_until_dt,
    asset_id
   FROM nebula.harvest_candidates_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: harvest_references_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.harvest_references_history (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    source_block_id uuid,
    source_segment_id uuid,
    target_block_id uuid,
    target_segment_id uuid,
    edge_type text DEFAULT 'implicit'::text NOT NULL,
    confidence numeric(5,4) DEFAULT 0.0000 NOT NULL,
    state text DEFAULT 'CANDIDATE'::text NOT NULL,
    source text DEFAULT 'HARVEST'::text NOT NULL,
    reason text,
    evidence_json jsonb,
    provenance_json jsonb,
    created_by text DEFAULT 'SYSTEM'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    as_of_dt timestamp with time zone DEFAULT now() NOT NULL,
    expiration_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    evidence_item_id uuid
);


--
-- Name: harvest_references; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.harvest_references AS
 SELECT id,
    conversation_id,
    snapshot_id,
    source_block_id,
    source_segment_id,
    target_block_id,
    target_segment_id,
    edge_type,
    confidence,
    state,
    source,
    reason,
    evidence_json,
    evidence_item_id,
    provenance_json,
    created_by,
    created_at
   FROM nebula.harvest_references_history
  WHERE ((now() >= as_of_dt) AND (now() < expiration_dt));


--
-- Name: harvests_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.harvests_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_path text NOT NULL,
    source_filename text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    total_candidates integer DEFAULT 0 NOT NULL,
    candidates jsonb DEFAULT '[]'::jsonb NOT NULL,
    source_text text,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    level integer DEFAULT 1 NOT NULL,
    visibility_scope text DEFAULT 'all'::text NOT NULL,
    docklang jsonb,
    source_hash text,
    version integer DEFAULT 1 NOT NULL,
    run_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    file_size bigint,
    asset_id uuid,
    CONSTRAINT chk_harvests_level CHECK (((level >= 1) AND (level <= 4)))
);


--
-- Name: harvests; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.harvests AS
 SELECT id,
    source_path,
    source_filename,
    model,
    total_candidates,
    candidates,
    source_text,
    tags,
    metadata,
    created_at,
    level,
    visibility_scope,
    docklang,
    source_hash,
    file_size,
    version,
    run_metadata,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    asset_id
   FROM nebula.harvests_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: implementation_notes; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.implementation_notes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid NOT NULL,
    revision_id uuid,
    note_type text NOT NULL,
    content text NOT NULL,
    source_record_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT '-infinity'::timestamp with time zone NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL
);


--
-- Name: TABLE implementation_notes; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.implementation_notes IS 'Implementation notes on canonical assets — survives the harvest lifecycle.';


--
-- Name: COLUMN implementation_notes.asset_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.implementation_notes.asset_id IS 'FK to nebula.canonical_asset. The stable identity the note is about.';


--
-- Name: COLUMN implementation_notes.note_type; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.implementation_notes.note_type IS 'implementation_plan | architecture_note | decision | engineering_log';


--
-- Name: COLUMN implementation_notes.source_record_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.implementation_notes.source_record_id IS 'FK to nebula.agent_records — the originating audit entry.';


--
-- Name: implementation_plans_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.implementation_plans_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plan_number text,
    spec_id uuid,
    requirement_id uuid,
    title text NOT NULL,
    goal text,
    content text,
    files_affected text[] DEFAULT '{}'::text[],
    acceptance_criteria jsonb DEFAULT '[]'::jsonb,
    dependencies text[] DEFAULT '{}'::text[],
    status text DEFAULT 'draft'::text NOT NULL,
    tags text[] DEFAULT '{}'::text[],
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    asset_id uuid,
    CONSTRAINT implementation_plans_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'pending'::text, 'approved'::text, 'work_requested'::text, 'completed'::text, 'archived'::text])))
);

ALTER TABLE ONLY nebula.implementation_plans_history REPLICA IDENTITY FULL;


--
-- Name: TABLE implementation_plans_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.implementation_plans_history IS 'Detailed context-heavy implementation plans. Replaces nebula.plans.';


--
-- Name: implementation_plans; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.implementation_plans AS
 SELECT id,
    plan_number,
    spec_id,
    requirement_id,
    title,
    goal,
    content,
    files_affected,
    acceptance_criteria,
    dependencies,
    status,
    tags,
    metadata,
    created_at,
    updated_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt,
    asset_id
   FROM nebula.implementation_plans_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: runtime_posture; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.runtime_posture (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    checked_at timestamp with time zone DEFAULT now() NOT NULL,
    host text DEFAULT 'localhost'::text NOT NULL,
    services_total integer NOT NULL,
    services_healthy integer NOT NULL,
    services_unhealthy integer DEFAULT 0 NOT NULL,
    all_healthy boolean DEFAULT false NOT NULL,
    migration_checked boolean DEFAULT false NOT NULL,
    migration_ok boolean,
    duration_ms integer,
    probe_version text DEFAULT '1.0'::text NOT NULL,
    posture_json jsonb NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: latest_runtime_posture; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.latest_runtime_posture AS
 SELECT DISTINCT ON (host) id,
    checked_at,
    host,
    services_total,
    services_healthy,
    services_unhealthy,
    all_healthy,
    migration_checked,
    migration_ok,
    duration_ms,
    probe_version,
    posture_json,
    recorded_at,
    recorded_until,
    expired_at
   FROM nebula.runtime_posture
  WHERE (expired_at IS NULL)
  ORDER BY host, checked_at DESC;


--
-- Name: observations_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.observations_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trigger_type text NOT NULL,
    source_artifact_type text,
    source_artifact_id uuid,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    assessed boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY nebula.observations_history REPLICA IDENTITY FULL;


--
-- Name: TABLE observations_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.observations_history IS 'Records trigger events that may need assessment.';


--
-- Name: observations; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.observations AS
 SELECT id,
    trigger_type,
    source_artifact_type,
    source_artifact_id,
    payload,
    assessed,
    created_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.observations_history oh
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: op_registry_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.op_registry_history (
    id text NOT NULL,
    intent_id text NOT NULL,
    version text DEFAULT 'v1'::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    label text DEFAULT ''::text NOT NULL,
    match_patterns text[] DEFAULT '{}'::text[] NOT NULL,
    opcode_template jsonb DEFAULT '[]'::jsonb NOT NULL,
    required_params text[] DEFAULT '{}'::text[] NOT NULL,
    optional_params text[] DEFAULT '{}'::text[] NOT NULL,
    preconditions text[] DEFAULT '{}'::text[] NOT NULL,
    postconditions text[] DEFAULT '{}'::text[] NOT NULL,
    idempotency_key text DEFAULT ''::text NOT NULL,
    successor_id text,
    notes text DEFAULT ''::text NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    deleted_at text,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT op_registry_status_check CHECK ((status = ANY (ARRAY['active'::text, 'deprecated'::text, 'superseded'::text])))
);

ALTER TABLE ONLY nebula.op_registry_history REPLICA IDENTITY FULL;


--
-- Name: op_registry; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.op_registry AS
 SELECT id,
    intent_id,
    version,
    status,
    label,
    match_patterns,
    opcode_template,
    required_params,
    optional_params,
    preconditions,
    postconditions,
    idempotency_key,
    successor_id,
    notes,
    schema_version,
    created_at,
    updated_at,
    deleted_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.op_registry_history oph
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: open_question_answers_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.open_question_answers_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    question_id uuid NOT NULL,
    role text NOT NULL,
    answer text NOT NULL,
    confidence text DEFAULT 'MEDIUM'::text,
    reasoning text,
    answered_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: open_question_answers; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.open_question_answers AS
 SELECT id,
    question_id,
    role,
    answer,
    confidence,
    reasoning,
    answered_at,
    version,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt,
    metadata
   FROM nebula.open_question_answers_history
  WHERE ((now() >= valid_from) AND (now() < valid_until) AND (now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: open_questions_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.open_questions_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    requirement_id uuid,
    title text NOT NULL,
    description text,
    category text DEFAULT 'AMBIGUITY'::text NOT NULL,
    status text DEFAULT 'OPEN'::text NOT NULL,
    blocking boolean DEFAULT true NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    candidate_id uuid,
    answered_by text,
    answered_at timestamp with time zone,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT open_questions_category_check CHECK ((category = ANY (ARRAY['AMBIGUITY'::text, 'MISSING_INFO'::text, 'CONFLICT'::text, 'SCOPE'::text, 'DEPENDENCY'::text, 'DUPLICATE_CANDIDATE'::text, 'WORK_COMPLETED'::text, 'NEEDS_SPEC'::text]))),
    CONSTRAINT open_questions_status_check CHECK ((status = ANY (ARRAY['OPEN'::text, 'IN_DELIBERATION'::text, 'RESOLVED'::text, 'WONT_FIX'::text, 'DEFERRED'::text])))
);


--
-- Name: TABLE open_questions_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.open_questions_history IS 'Questions that arise during requirement analysis. Blocking questions prevent requirement completion.';


--
-- Name: COLUMN open_questions_history.requirement_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.open_questions_history.requirement_id IS 'Logical reference to nebula.requirements. No FK is installed because requirements_history has duplicate IDs across bitemporal revisions; see migration 042.';


--
-- Name: COLUMN open_questions_history.candidate_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.open_questions_history.candidate_id IS 'References harvest_candidates.id. Set by Planner for pre-promotion blocking questions (duplicates, evidence).';


--
-- Name: COLUMN open_questions_history.answered_by; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.open_questions_history.answered_by IS 'Role that provided the answer (e.g. analyst). Set by PUT /open-questions/:id/answer.';


--
-- Name: COLUMN open_questions_history.answered_at; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.open_questions_history.answered_at IS 'Timestamp when the answer was recorded.';


--
-- Name: open_questions; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.open_questions AS
 SELECT id,
    requirement_id,
    title,
    description,
    category,
    status,
    blocking,
    created_by,
    created_at,
    updated_at,
    candidate_id,
    answered_by,
    answered_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.open_questions_history oqh
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: plans; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.plans AS
 SELECT plan_number AS id,
    ''::text AS file_name,
    title,
    'wrp'::text AS project,
    COALESCE(goal, ''::text) AS goal,
    COALESCE(content, ''::text) AS content,
    COALESCE(array_to_string(files_affected, ','::text), ''::text) AS files_affected,
    COALESCE((acceptance_criteria)::text, '[]'::text) AS acceptance_criteria,
    COALESCE(array_to_string(dependencies, ','::text), ''::text) AS dependencies,
    ''::text AS prompt_ref,
    ''::text AS notes,
    0 AS priority,
    (created_at)::text AS created_at,
    (updated_at)::text AS updated_at,
        CASE
            WHEN (status = 'archived'::text) THEN 1
            ELSE 0
        END AS deleted,
    status
   FROM nebula.implementation_plans;


--
-- Name: receipts; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.receipts (
    id text NOT NULL,
    plan_id text NOT NULL,
    type text NOT NULL,
    agent_role text NOT NULL,
    session_id text,
    artifact_path text,
    summary text DEFAULT ''::text NOT NULL,
    metadata_json text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    ticket_id text,
    tokens_used integer DEFAULT 0,
    recorded_on_dt timestamp with time zone DEFAULT now(),
    recorded_until_dt timestamp with time zone,
    sequence integer NOT NULL,
    CONSTRAINT chk_receipts_sequence_non_negative CHECK ((sequence >= 0)),
    CONSTRAINT chk_vision_receipts_type CHECK ((type = ANY (ARRAY['ABANDONED'::text, 'API_LIMIT'::text, 'BLOCK'::text, 'CANCELLED'::text, 'CCNF_EXECUTION'::text, 'CRITIQUE'::text, 'CRITIQUE_PASS'::text, 'CRITIQUE_REJECT'::text, 'HOLD'::text, 'IMPLEMENTATION'::text, 'PLANNING'::text, 'PLAN_BLOCK'::text, 'PLAN_CREATE'::text, 'PROPOSED'::text, 'REQUEUED'::text, 'REVIEW'::text, 'REVIEW_PASS'::text, 'REVIEW_REJECT'::text])))
);

ALTER TABLE ONLY vision.receipts REPLICA IDENTITY FULL;


--
-- Name: TABLE receipts; Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON TABLE vision.receipts IS 'FROZEN (D-T19-2d): legacy receipt store, read-only for the pipeline — real receipts now write execution.receipts. Only the test/synthetic fallback path writes here. Archive after 7-day soak (confirm with ops).';


--
-- Name: receipts_unified; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.receipts_unified AS
 SELECT COALESCE(e.lineage_original_id, (e.id)::text) AS id,
    rq.source_plan_id AS plan_id,
    e.type,
    e.agent_role,
    (e.metadata ->> 'session_id'::text) AS session_id,
    (e.metadata ->> 'artifact_path'::text) AS artifact_path,
    e.summary,
    (e.metadata)::text AS metadata_json,
    e.issued_at AS created_at,
    (e.metadata ->> 'ticket_id'::text) AS ticket_id,
    COALESCE(((e.metadata ->> 'tokens_used'::text))::integer, 0) AS tokens_used,
    NULL::integer AS sequence,
    e.issued_at AS recorded_on_dt,
    NULL::timestamp with time zone AS recorded_until_dt
   FROM (execution.receipts e
     JOIN execution.requests rq ON ((rq.id = e.request_id)))
  WHERE (e.lineage_source = 'conduit'::text)
UNION ALL
 SELECT receipts.id,
    receipts.plan_id,
    receipts.type,
    receipts.agent_role,
    receipts.session_id,
    receipts.artifact_path,
    receipts.summary,
    receipts.metadata_json,
    receipts.created_at,
    receipts.ticket_id,
    receipts.tokens_used,
    receipts.sequence,
    receipts.recorded_on_dt,
    receipts.recorded_until_dt
   FROM vision.receipts;


--
-- Name: plan_status; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.plan_status AS
 SELECT id,
    file_name,
    title,
    project,
    goal,
    content,
    files_affected,
    acceptance_criteria,
    dependencies,
    prompt_ref,
    notes,
    priority,
    created_at,
    updated_at,
    deleted,
        CASE
            WHEN (EXISTS ( SELECT 1
               FROM nebula.receipts_unified r
              WHERE ((r.plan_id = p.id) AND (r.type = 'HOLD'::text) AND (NOT (EXISTS ( SELECT 1
                       FROM nebula.receipts_unified r2
                      WHERE ((r2.plan_id = p.id) AND (r2.type = ANY (ARRAY['CANCELLED'::text, 'ABANDONED'::text])) AND (r2.created_at > r.created_at)))))))) THEN 'HOLD'::text
            WHEN (( SELECT r.type
               FROM nebula.receipts_unified r
              WHERE ((r.plan_id = p.id) AND (r.type <> ALL (ARRAY['PLANNING'::text, 'HOLD'::text])))
              ORDER BY r.created_at DESC
             LIMIT 1) = 'REQUEUED'::text) THEN 'PLAN_CREATE'::text
            WHEN (EXISTS ( SELECT 1
               FROM nebula.receipts_unified r
              WHERE ((r.plan_id = p.id) AND (r.type = 'REVIEW_PASS'::text) AND (NOT (EXISTS ( SELECT 1
                       FROM nebula.receipts_unified r2
                      WHERE ((r2.plan_id = p.id) AND (r2.type = ANY (ARRAY['BLOCK'::text, 'PLAN_BLOCK'::text, 'CANCELLED'::text, 'ABANDONED'::text])) AND (r2.created_at > r.created_at)))))))) THEN 'REVIEW_PASS'::text
            WHEN (EXISTS ( SELECT 1
               FROM nebula.receipts_unified r
              WHERE ((r.plan_id = p.id) AND (r.type = 'REVIEW_REJECT'::text)))) THEN COALESCE(( SELECT r.type
               FROM nebula.receipts_unified r
              WHERE ((r.plan_id = p.id) AND (r.type <> 'BLOCK'::text))
              ORDER BY r.created_at DESC
             LIMIT 1), 'PLAN_CREATE'::text)
            ELSE COALESCE(( SELECT r.type
               FROM nebula.receipts_unified r
              WHERE ((r.plan_id = p.id) AND (r.type <> ALL (ARRAY['PLANNING'::text, 'HOLD'::text])))
              ORDER BY r.created_at DESC
             LIMIT 1), ( SELECT r.type
               FROM nebula.receipts_unified r
              WHERE (r.plan_id = p.id)
              ORDER BY r.created_at DESC
             LIMIT 1), NULL::text)
        END AS derived_status
   FROM nebula.plans p
  WHERE (deleted = 0);


--
-- Name: plans_by_status; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.plans_by_status AS
 SELECT id,
    file_name,
    title,
    project,
    goal,
    content,
    files_affected,
    acceptance_criteria,
    dependencies,
    prompt_ref,
    notes,
    priority,
    deleted,
    created_at,
    updated_at,
    derived_status AS status
   FROM nebula.plan_status ps;


--
-- Name: projection_overrides_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.projection_overrides_history (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    target_type text DEFAULT 'BLOCK'::text NOT NULL,
    target_id uuid NOT NULL,
    projection_target text DEFAULT 'BP'::text NOT NULL,
    override_type text DEFAULT 'EXCLUDE'::text NOT NULL,
    reason_code text DEFAULT 'USER_OVERRIDE'::text NOT NULL,
    notes_md text,
    source text DEFAULT 'USER'::text NOT NULL,
    created_by text DEFAULT 'USER'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    as_of_dt timestamp with time zone DEFAULT now() NOT NULL,
    expiration_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY nebula.projection_overrides_history REPLICA IDENTITY FULL;


--
-- Name: projection_overrides; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.projection_overrides AS
 SELECT id,
    conversation_id,
    snapshot_id,
    target_type,
    target_id,
    projection_target,
    override_type,
    reason_code,
    notes_md,
    source,
    created_by,
    created_at
   FROM nebula.projection_overrides_history
  WHERE ((now() >= as_of_dt) AND (now() < expiration_dt));


--
-- Name: projections_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.projections_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    type text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    source_query text DEFAULT ''::text NOT NULL,
    template text DEFAULT ''::text NOT NULL,
    target_path text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text,
    schedule text DEFAULT ''::text,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    CONSTRAINT projections_type_check CHECK ((type = ANY (ARRAY['deterministic'::text, 'inference'::text])))
);

ALTER TABLE ONLY nebula.projections_history REPLICA IDENTITY FULL;


--
-- Name: projections; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.projections AS
 SELECT id,
    name,
    type,
    description,
    source_query,
    template,
    target_path,
    model,
    schedule,
    metadata,
    created_at,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until
   FROM nebula.projections_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: requirement_segment_sets; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.requirement_segment_sets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    requirement_id uuid NOT NULL,
    segment_set_id uuid NOT NULL,
    role text DEFAULT 'primary'::text NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT requirement_segment_sets_role_check CHECK ((role = ANY (ARRAY['primary'::text, 'supporting'::text])))
);


--
-- Name: requirement_verifications_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.requirement_verifications_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    requirement_id uuid NOT NULL,
    work_request_id uuid,
    role text NOT NULL,
    status text DEFAULT 'PENDING'::text NOT NULL,
    feedback text,
    conditions text,
    verified_at timestamp with time zone,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT verification_status_check CHECK ((status = ANY (ARRAY['PENDING'::text, 'APPROVED'::text, 'REJECTED'::text, 'DEFERRED'::text])))
);


--
-- Name: TABLE requirement_verifications_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.requirement_verifications_history IS 'Tracks verification by Engineer, Topologist, and Architect before Work Request enters conduit.';


--
-- Name: requirement_verifications; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.requirement_verifications AS
 SELECT id,
    requirement_id,
    work_request_id,
    role,
    status,
    feedback,
    conditions,
    verified_at,
    expires_at,
    created_at,
    updated_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.requirement_verifications_history rvh
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: requirements_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.requirements_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    system_id uuid NOT NULL,
    subsystem_id uuid,
    feature_id uuid,
    title text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'Backlog'::text NOT NULL,
    priority text DEFAULT 'Medium'::text NOT NULL,
    start_date text,
    completion_date text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    parent_id uuid,
    req_type text,
    acceptance_criteria jsonb DEFAULT '[]'::jsonb,
    candidate_id uuid,
    conduit_plan_id character varying(32),
    work_request_dco jsonb,
    asset_id uuid,
    CONSTRAINT chk_requirements_req_type CHECK (((req_type IS NULL) OR (req_type = ANY (ARRAY['Epic'::text, 'Story'::text, 'Task'::text, 'Bug'::text])))),
    CONSTRAINT requirements_priority_check CHECK ((priority = ANY (ARRAY['Low'::text, 'Medium'::text, 'High'::text]))),
    CONSTRAINT requirements_status_check CHECK ((status = ANY (ARRAY['Backlog'::text, 'ToDo'::text, 'InProgress'::text, 'Active'::text, 'Blocked'::text, 'Done'::text, 'Cancelled'::text, 'Accepted'::text])))
);

ALTER TABLE ONLY nebula.requirements_history REPLICA IDENTITY FULL;


--
-- Name: COLUMN requirements_history.conduit_plan_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.requirements_history.conduit_plan_id IS 'Cross-reference to conduit plan_number that completed REVIEW_PASS';


--
-- Name: requirements; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.requirements AS
 SELECT id,
    system_id,
    subsystem_id,
    feature_id,
    title,
    description,
    status,
    priority,
    start_date,
    completion_date,
    created_at,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    parent_id,
    req_type,
    acceptance_criteria,
    candidate_id,
    conduit_plan_id,
    work_request_dco,
    asset_id
   FROM nebula.requirements_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: roles_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.roles_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    display_name text NOT NULL,
    description text,
    owns_domains text[] DEFAULT '{}'::text[] NOT NULL,
    can_greenlight boolean DEFAULT false NOT NULL,
    can_create_questions boolean DEFAULT false NOT NULL,
    can_create_agendas boolean DEFAULT false NOT NULL,
    can_resolve_questions boolean DEFAULT false NOT NULL,
    can_verify_work_requests boolean DEFAULT false NOT NULL,
    max_open_questions integer,
    requires_approval_from text[],
    cron_enabled boolean DEFAULT false NOT NULL,
    cron_expression text,
    cron_description text,
    escalates_to text[],
    escalation_triggers text[],
    level_filter_primary text DEFAULT 'level <= 2'::text NOT NULL,
    level_filter_allowed text DEFAULT 'level <= 3'::text NOT NULL,
    visibility_scope text[] DEFAULT '{planner,all}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT roles_name_check CHECK ((name ~ '^[a-z0-9_-]+$'::text))
);


--
-- Name: TABLE roles_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.roles_history IS 'Role definitions with capabilities, constraints, and cron configuration.';


--
-- Name: roles; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.roles AS
 SELECT id,
    name,
    display_name,
    description,
    owns_domains,
    can_greenlight,
    can_create_questions,
    can_create_agendas,
    can_resolve_questions,
    can_verify_work_requests,
    max_open_questions,
    requires_approval_from,
    cron_enabled,
    cron_expression,
    cron_description,
    escalates_to,
    escalation_triggers,
    level_filter_primary,
    level_filter_allowed,
    visibility_scope,
    created_at,
    updated_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.roles_history rh
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: schema_version; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.schema_version (
    version integer NOT NULL,
    description text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE schema_version; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.schema_version IS 'Forward ledger for nebula-srv numbered migrations. Versions 001-041 predate per-version ledger tracking and are represented by baseline version 41.';


--
-- Name: segment_set_members; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.segment_set_members (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    segment_set_id uuid NOT NULL,
    segment_id uuid NOT NULL,
    ordinal integer NOT NULL,
    included boolean DEFAULT true NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL
);


--
-- Name: TABLE segment_set_members; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.segment_set_members IS 'Ordered membership of a segment_set. "Candidate covers chunks 5-8 and 12-18" = two rows here, each pointing at a nebula.segments_history row. Excluding a digression toggles included=false rather than deleting.';


--
-- Name: segment_sets; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.segment_sets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text,
    description text,
    status text DEFAULT 'active'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    CONSTRAINT segment_sets_status_check CHECK ((status = ANY (ARRAY['active'::text, 'archived'::text])))
);


--
-- Name: TABLE segment_sets; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.segment_sets IS 'First-class, addressable, reusable collection of segments (possibly non-contiguous). Domain objects point at a segment_set instead of copying source text forward. Cached in Redis under nexus:segset:{id} for fast agent-tooling reads.';


--
-- Name: segments_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.segments_history (
    id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    snapshot_id uuid NOT NULL,
    start_block_id uuid NOT NULL,
    end_block_id uuid NOT NULL,
    start_block_index integer NOT NULL,
    end_block_index integer NOT NULL,
    segment_type text,
    state text DEFAULT 'PROPOSED'::text NOT NULL,
    source text DEFAULT 'USER'::text NOT NULL,
    title text,
    notes_md text,
    created_by text DEFAULT 'USER'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    as_of_dt timestamp with time zone DEFAULT now() NOT NULL,
    expiration_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY nebula.segments_history REPLICA IDENTITY FULL;


--
-- Name: segments; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.segments AS
 SELECT id,
    conversation_id,
    snapshot_id,
    start_block_id,
    end_block_id,
    start_block_index,
    end_block_index,
    segment_type,
    state,
    source,
    title,
    notes_md,
    created_by,
    created_at
   FROM nebula.segments_history
  WHERE ((now() >= as_of_dt) AND (now() < expiration_dt));


--
-- Name: spec_documents_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.spec_documents_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    candidate_id uuid,
    harvest_id uuid,
    system_id uuid,
    subsystem_id uuid,
    feature_id uuid,
    acceptance_criteria jsonb DEFAULT '[]'::jsonb NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    source_transcript_path text,
    source_model text,
    assembly_thread_id uuid,
    metadata jsonb DEFAULT '{}'::jsonb,
    tags text[] DEFAULT '{}'::text[],
    created_at timestamp with time zone DEFAULT now(),
    valid_from timestamp with time zone DEFAULT now(),
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone,
    recorded_on_dt timestamp with time zone DEFAULT now(),
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone
);


--
-- Name: TABLE spec_documents_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.spec_documents_history IS 'Problem-level specifications (the what). SCD Type 2 — bitemporal versioning.';


--
-- Name: spec_documents; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.spec_documents AS
 SELECT id,
    title,
    description,
    candidate_id,
    harvest_id,
    system_id,
    subsystem_id,
    feature_id,
    acceptance_criteria,
    status,
    source_transcript_path,
    source_model,
    assembly_thread_id,
    metadata,
    tags,
    created_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.spec_documents_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: VIEW spec_documents; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON VIEW nebula.spec_documents IS 'Current-row view of spec_documents_history.';


--
-- Name: spec_requirements; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.spec_requirements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    spec_id uuid NOT NULL,
    requirement_id uuid,
    criterion text NOT NULL,
    plan_id character varying,
    status text DEFAULT 'pending'::text NOT NULL,
    sort_order integer DEFAULT 0,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: TABLE spec_requirements; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.spec_requirements IS 'Maps each acceptance criterion to a Requirement and Implementation Plan.';


--
-- Name: specifications; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.specifications AS
 SELECT id,
    agenda_id,
    revision_number,
    revision_type,
    superseded_by,
    derived_from,
    item_snapshot,
    change_summary,
    valid_from,
    valid_until,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM nebula.specifications_history sh
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: specs; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.specs AS
 SELECT ai.id,
    ai.agenda_id,
    ai.source_type,
    ai.source_id,
    ai.title,
    ai.body,
    ai.decisions,
    ai.open_questions,
    ai.supporting_refs,
    ai.included,
    ai.planner_note,
    ai.created_at AS item_created_at,
    ai.updated_at AS item_updated_at,
    a.title AS agenda_title,
    a.status AS agenda_status
   FROM (nebula.agenda_items_history ai
     JOIN nebula.agendas_history a ON ((a.id = ai.agenda_id)))
  WHERE (ai.included = true);


--
-- Name: subsystems_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.subsystems_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    system_id uuid NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    readme text,
    color text DEFAULT '#3B82F6'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    path text,
    asset_id uuid
);

ALTER TABLE ONLY nebula.subsystems_history REPLICA IDENTITY FULL;


--
-- Name: subsystems; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.subsystems AS
 SELECT id,
    system_id,
    name,
    description,
    readme,
    color,
    created_at,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    path,
    asset_id
   FROM nebula.subsystems_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: system_folders_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.system_folders_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    system_id uuid NOT NULL,
    name text NOT NULL,
    category text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    CONSTRAINT system_folders_category_check CHECK ((category = ANY (ARRAY['UI'::text, 'Service'::text, 'Library'::text, 'Documentation'::text, 'Config'::text, 'data'::text, 'api'::text])))
);

ALTER TABLE ONLY nebula.system_folders_history REPLICA IDENTITY FULL;


--
-- Name: system_folders; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.system_folders AS
 SELECT id,
    system_id,
    name,
    category,
    note,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until
   FROM nebula.system_folders_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: system_info_tabs_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.system_info_tabs_history (
    system_id uuid NOT NULL,
    tab_id text NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY nebula.system_info_tabs_history REPLICA IDENTITY FULL;


--
-- Name: system_info_tabs; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.system_info_tabs AS
 SELECT system_id,
    tab_id,
    content,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until
   FROM nebula.system_info_tabs_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: system_workspaces_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.system_workspaces_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    system_id uuid NOT NULL,
    subsystem_id uuid,
    workspace_path text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY nebula.system_workspaces_history REPLICA IDENTITY FULL;


--
-- Name: system_workspaces; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.system_workspaces AS
 SELECT id,
    system_id,
    subsystem_id,
    workspace_path,
    created_at,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until
   FROM nebula.system_workspaces_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: systems_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.systems_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    readme text,
    architecture text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    path text,
    asset_id uuid
);

ALTER TABLE ONLY nebula.systems_history REPLICA IDENTITY FULL;


--
-- Name: systems; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.systems AS
 SELECT id,
    name,
    description,
    readme,
    architecture,
    created_at,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until,
    path,
    asset_id
   FROM nebula.systems_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: user_preferences_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.user_preferences_history (
    user_id text DEFAULT 'default'::text NOT NULL,
    key text NOT NULL,
    value jsonb DEFAULT '{}'::jsonb NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY nebula.user_preferences_history REPLICA IDENTITY FULL;


--
-- Name: user_preferences; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.user_preferences AS
 SELECT user_id,
    key,
    value,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until
   FROM nebula.user_preferences_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: v_latest_question_answer; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.v_latest_question_answer AS
 SELECT DISTINCT ON (question_id) id,
    question_id,
    role,
    answer,
    confidence,
    reasoning,
    answered_at
   FROM nebula.open_question_answers_history a
  ORDER BY question_id, answered_at DESC;


--
-- Name: v_question_answer_counts; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.v_question_answer_counts AS
 SELECT question_id,
    count(*) AS answer_count,
    count(DISTINCT role) AS role_count
   FROM nebula.open_question_answers_history
  GROUP BY question_id;


--
-- Name: v_role_capabilities; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.v_role_capabilities AS
 SELECT name,
    display_name,
    owns_domains,
        CASE
            WHEN can_greenlight THEN '✓'::text
            ELSE '·'::text
        END AS greenlight,
        CASE
            WHEN can_create_questions THEN '✓'::text
            ELSE '·'::text
        END AS questions,
        CASE
            WHEN can_create_agendas THEN '✓'::text
            ELSE '·'::text
        END AS agendas,
        CASE
            WHEN can_resolve_questions THEN '✓'::text
            ELSE '·'::text
        END AS resolve,
        CASE
            WHEN can_verify_work_requests THEN '✓'::text
            ELSE '·'::text
        END AS verify,
        CASE
            WHEN cron_enabled THEN cron_expression
            ELSE '·'::text
        END AS cron,
    escalates_to
   FROM nebula.roles_history
  ORDER BY name;


--
-- Name: services; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.services (
    id bigint NOT NULL,
    active_flag boolean,
    api_base_path character varying(255),
    created_at timestamp(6) without time zone,
    default_port integer,
    description character varying(1000),
    name character varying(255) NOT NULL,
    repository_url character varying(255),
    status character varying(255),
    updated_at timestamp(6) without time zone,
    version character varying(255),
    component_override_id bigint,
    framework_id bigint NOT NULL,
    parent_service_id bigint,
    service_type_id bigint NOT NULL,
    asset_id uuid,
    origin character varying(20)
);


--
-- Name: runnable_services; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.runnable_services (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    port integer,
    workspace_path character varying(255),
    service_type_id bigint,
    health_check_url character varying(255),
    status character varying(255),
    version character varying(255),
    description character varying(1000),
    repository_url character varying(255),
    active_flag boolean DEFAULT true NOT NULL,
    startup character varying(255),
    health character varying(255),
    syspass character varying(255),
    notes character varying(1000),
    is_internal boolean DEFAULT true NOT NULL,
    sysuser character varying(255),
    startup_script character varying(255),
    build_command character varying(255),
    asset_id uuid
);

ALTER TABLE ONLY terrain.runnable_services REPLICA IDENTITY FULL;


--
-- Name: v_system_inventory; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.v_system_inventory AS
 SELECT ns.id AS nebula_system_id,
    ns.name AS nebula_system,
    ns.path AS nebula_path,
    ns.description AS nebula_description,
    NULL::text AS terrain_match_method,
    NULL::numeric(3,2) AS terrain_match_confidence,
    NULL::text AS role_in_system,
    trs.id AS terrain_service_id,
    trs.name AS terrain_service,
    trs.port AS terrain_port,
    trs.workspace_path,
    trs.health_check_url,
    trs.status AS terrain_status,
    trs.startup_script,
    trs.build_command,
    trs.is_internal,
    rs.id AS registry_service_id,
    rs.name AS registry_service,
    rs.default_port AS registry_port,
    rs.status AS registry_status,
    rs.description AS registry_description,
    rs.version AS registry_version,
    rs.repository_url,
    rs.api_base_path
   FROM (((terrain.runnable_services trs
     JOIN semantics.asset_relation ar ON (((ar.to_asset_id = trs.asset_id) AND (ar.relation_type = 'owns'::text) AND (ar.expired_at IS NULL))))
     JOIN nebula.systems ns ON ((ns.asset_id = ar.from_asset_id)))
     LEFT JOIN registry.services rs ON ((rs.asset_id = trs.asset_id)));


--
-- Name: VIEW v_system_inventory; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON VIEW nebula.v_system_inventory IS 'Unified inventory terrain-anchored: one row per (nebula.system, terrain.service).
 Joins through asset_relation (V076 rewrite — replaces system_external_ids).
 Registry data via shared asset_id (identity_map replaced by canonical_asset sharing).
 Terrain services without a system owner (no asset_relation row) are excluded —
 those are the "unmapped" services that need manual system assignment.';


--
-- Name: v_system_inventory_registry_only; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.v_system_inventory_registry_only AS
 SELECT ns.id AS nebula_system_id,
    ns.name AS nebula_system,
    ns.path AS nebula_path,
    ns.description AS nebula_description,
    NULL::text AS terrain_match_method,
    NULL::numeric(3,2) AS terrain_match_confidence,
    NULL::text AS role_in_system,
    NULL::bigint AS terrain_service_id,
    NULL::text AS terrain_service,
    NULL::integer AS terrain_port,
    NULL::text AS workspace_path,
    NULL::text AS health_check_url,
    NULL::text AS terrain_status,
    NULL::text AS startup_script,
    NULL::text AS build_command,
    NULL::boolean AS is_internal,
    rs.id AS registry_service_id,
    rs.name AS registry_service,
    rs.default_port AS registry_port,
    rs.status AS registry_status,
    rs.description AS registry_description,
    rs.version AS registry_version,
    rs.repository_url,
    rs.api_base_path
   FROM ((registry.services rs
     JOIN semantics.asset_relation ar ON (((ar.to_asset_id = rs.asset_id) AND (ar.relation_type = 'owns'::text) AND (ar.expired_at IS NULL))))
     JOIN nebula.systems ns ON ((ns.asset_id = ar.from_asset_id)))
  WHERE (NOT (rs.asset_id IN ( SELECT trs.asset_id
           FROM terrain.runnable_services trs
          WHERE (trs.asset_id IS NOT NULL))));


--
-- Name: VIEW v_system_inventory_registry_only; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON VIEW nebula.v_system_inventory_registry_only IS 'Registry-only inventory: services in registry with no terrain counterpart.
 Joins through asset_relation (V076 rewrite).
 Excludes services sharing an asset_id with a terrain counterpart (appear in v_system_inventory).';


--
-- Name: work_requests_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.work_requests_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    description text,
    source_specification_id uuid,
    source_requirement_id uuid,
    business_status text DEFAULT 'DRAFT'::text NOT NULL,
    intent text,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    constraints jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    dco_json text,
    legacy_id text,
    plan_id text,
    step_outputs text DEFAULT '{}'::text NOT NULL,
    consumed_at timestamp with time zone,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 00:00:00+00'::timestamp with time zone NOT NULL,
    asset_id uuid,
    entity_key text,
    CONSTRAINT work_requests_business_status_check CHECK ((business_status = ANY (ARRAY['DRAFT'::text, 'APPROVED'::text, 'DISPATCHED'::text, 'COMPLETED'::text, 'CANCELLED'::text])))
);

ALTER TABLE ONLY nebula.work_requests_history REPLICA IDENTITY FULL;


--
-- Name: TABLE work_requests_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TABLE nebula.work_requests_history IS 'Canonical work request table. Replaces conduit.work_requests as of migration 029.';


--
-- Name: COLUMN work_requests_history.business_status; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.work_requests_history.business_status IS 'Business lifecycle state: Should this happen? DRAFT → APPROVED → DISPATCHED → COMPLETED/CANCELLED';


--
-- Name: COLUMN work_requests_history.dco_json; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.work_requests_history.dco_json IS 'Decomposition Command Object JSON. The compiled form of the work request.';


--
-- Name: COLUMN work_requests_history.legacy_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.work_requests_history.legacy_id IS 'Original TEXT ID from conduit.work_requests (e.g., wr-0130-1781781240). Preserved for knowledge graph compatibility.';


--
-- Name: COLUMN work_requests_history.plan_id; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.work_requests_history.plan_id IS 'Reference to the implementation plan. Matches conduit.work_requests.plan_id format.';


--
-- Name: COLUMN work_requests_history.step_outputs; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.work_requests_history.step_outputs IS 'JSON object tracking outputs from each execution step.';


--
-- Name: COLUMN work_requests_history.consumed_at; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.work_requests_history.consumed_at IS 'Idempotency marker for harvest process. NULL = not yet consumed. Set when work request is processed by execution layer.';


--
-- Name: COLUMN work_requests_history.entity_key; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON COLUMN nebula.work_requests_history.entity_key IS 'T22 (T08): deterministic entity identity key emitted at the WRP boundary. NULL = identity-unknown (historical rows, not retroactively computed).';


--
-- Name: work_requests; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.work_requests (
    id bigint NOT NULL,
    wr_id text,
    dco_json text DEFAULT '{}'::text NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    step_outputs text DEFAULT '{}'::text NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone,
    work_request_uuid text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    nexus_work_request_id uuid,
    asset_id uuid,
    entity_key text
);

ALTER TABLE ONLY vision.work_requests REPLICA IDENTITY FULL;


--
-- Name: COLUMN work_requests.nexus_work_request_id; Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON COLUMN vision.work_requests.nexus_work_request_id IS 'Links this LOSM work tracking record to the canonical nebula.work_requests business record.';


--
-- Name: v_work_request_overview; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.v_work_request_overview AS
 SELECT wr.id,
    wr.legacy_id,
    wr.title,
    wr.plan_id,
    wr.business_status,
    wr.consumed_at,
    wr.created_at AS business_created_at,
    er.status AS execution_status,
    er.id AS execution_request_id,
    er.business_key,
    vr.status AS runtime_status,
    vr.work_request_uuid AS vision_id,
        CASE
            WHEN (wr.business_status = 'CANCELLED'::text) THEN 'CANCELLED'::text
            WHEN (vr.status = 'rejected'::text) THEN 'REJECTED'::text
            WHEN (vr.status = 'failed'::text) THEN 'FAILED'::text
            WHEN ((wr.business_status = 'COMPLETED'::text) AND (vr.status = 'settled'::text)) THEN 'COMPLETE'::text
            WHEN (wr.business_status = 'COMPLETED'::text) THEN 'AWAITING_REVIEW'::text
            WHEN (vr.status = 'settled'::text) THEN 'SETTLED_AWAITING_BUSINESS'::text
            WHEN (vr.status = 'claimed'::text) THEN 'RUNNING'::text
            WHEN (vr.status = 'validated'::text) THEN 'VALIDATED'::text
            WHEN (vr.status = 'queued'::text) THEN 'QUEUED'::text
            WHEN (vr.status = 'deferred'::text) THEN 'DEFERRED'::text
            WHEN (vr.status = 'noop'::text) THEN 'NOOP'::text
            WHEN (er.status = 'READY'::text) THEN 'READY_FOR_EXECUTION'::text
            WHEN (er.status = 'ADMITTED'::text) THEN 'ADMITTED'::text
            WHEN (er.status = 'VALIDATED'::text) THEN 'EXECUTION_VALIDATED'::text
            WHEN (er.status = 'COMPLETED'::text) THEN 'EXECUTION_COMPLETE'::text
            WHEN (er.status = 'FAILED'::text) THEN 'EXECUTION_FAILED'::text
            WHEN (wr.business_status = 'DISPATCHED'::text) THEN 'DISPATCHED'::text
            WHEN (wr.business_status = 'APPROVED'::text) THEN 'APPROVED'::text
            WHEN ((wr.business_status = 'DRAFT'::text) AND (wr.consumed_at IS NULL)) THEN 'PENDING'::text
            WHEN (wr.business_status = 'DRAFT'::text) THEN 'DRAFT'::text
            ELSE 'UNKNOWN'::text
        END AS effective_status,
        CASE
            WHEN (wr.business_status = 'CANCELLED'::text) THEN 'Business intent cancelled'::text
            WHEN (vr.status = 'rejected'::text) THEN 'Execution rejected'::text
            WHEN (vr.status = 'failed'::text) THEN 'Execution failed'::text
            WHEN ((wr.business_status = 'COMPLETED'::text) AND (vr.status = 'settled'::text)) THEN 'Complete - business objective satisfied'::text
            WHEN (wr.business_status = 'COMPLETED'::text) THEN 'Awaiting business review'::text
            WHEN (vr.status = 'settled'::text) THEN 'Settled - awaiting business confirmation'::text
            WHEN (vr.status = 'claimed'::text) THEN 'Worker actively executing'::text
            WHEN (vr.status = 'validated'::text) THEN 'Validated - awaiting execution'::text
            WHEN (vr.status = 'queued'::text) THEN 'Queued for execution'::text
            WHEN (er.status = 'READY'::text) THEN 'Ready for execution'::text
            WHEN (wr.business_status = 'DISPATCHED'::text) THEN 'Dispatched to execution layer'::text
            WHEN (wr.business_status = 'APPROVED'::text) THEN 'Approved - awaiting compilation'::text
            WHEN (wr.consumed_at IS NULL) THEN 'Pending - not yet consumed'::text
            ELSE 'Draft'::text
        END AS effective_status_description
   FROM ((nebula.work_requests_history wr
     LEFT JOIN execution.requests er ON ((er.source_wr_id = wr.id)))
     LEFT JOIN vision.work_requests vr ON ((vr.work_request_uuid = (wr.id)::text)));


--
-- Name: VIEW v_work_request_overview; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON VIEW nebula.v_work_request_overview IS 'Unified view of work requests across business, execution, and runtime layers. effective_status is a projection for human consumption, not authority.';


--
-- Name: work_requests; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.work_requests AS
 SELECT id,
    title,
    description,
    source_specification_id,
    source_requirement_id,
    business_status,
    intent,
    context,
    constraints,
    created_by,
    created_at,
    updated_at,
    dco_json,
    legacy_id,
    plan_id,
    step_outputs,
    consumed_at,
    valid_from,
    valid_until,
    recorded_on_dt,
    recorded_until_dt,
    asset_id,
    entity_key
   FROM nebula.work_requests_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: work_sessions_history; Type: TABLE; Schema: nebula; Owner: -
--

CREATE TABLE nebula.work_sessions_history (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_id text NOT NULL,
    parent_type text NOT NULL,
    parent_name text DEFAULT ''::text NOT NULL,
    context text DEFAULT ''::text NOT NULL,
    platform text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    outcome text,
    status text DEFAULT 'Pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    CONSTRAINT work_sessions_parent_type_check CHECK ((parent_type = ANY (ARRAY['system'::text, 'subsystem'::text, 'feature'::text, 'requirement'::text]))),
    CONSTRAINT work_sessions_status_check CHECK ((status = ANY (ARRAY['Pending'::text, 'Completed'::text])))
);

ALTER TABLE ONLY nebula.work_sessions_history REPLICA IDENTITY FULL;


--
-- Name: work_sessions; Type: VIEW; Schema: nebula; Owner: -
--

CREATE VIEW nebula.work_sessions AS
 SELECT id,
    parent_id,
    parent_type,
    parent_name,
    context,
    platform,
    model,
    outcome,
    status,
    created_at,
    recorded_on_dt,
    recorded_until_dt,
    valid_from,
    valid_until
   FROM nebula.work_sessions_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt) AND (now() >= valid_from) AND (now() < valid_until));


--
-- Name: binding_decision_evidence; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.binding_decision_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    decision_id text NOT NULL,
    decision_class text NOT NULL,
    binding_contract_version integer NOT NULL,
    subject_id text NOT NULL,
    work_item_id text NOT NULL,
    disposition text NOT NULL,
    authority_level text NOT NULL,
    evaluation_fingerprint text NOT NULL,
    lineage_fingerprint text NOT NULL,
    replay_context text NOT NULL,
    as_of timestamp with time zone NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT binding_decision_evidence_authority_level_check CHECK ((authority_level = 'advisory'::text)),
    CONSTRAINT binding_decision_evidence_binding_contract_version_check CHECK ((binding_contract_version = 1)),
    CONSTRAINT binding_decision_evidence_disposition_check CHECK ((disposition = ANY (ARRAY['allow'::text, 'refused'::text, 'unknown'::text, 'stale'::text, 'drift'::text, 'quarantined'::text, 'superseded'::text, 'rolled_back'::text]))),
    CONSTRAINT binding_decision_evidence_evaluation_fingerprint_check CHECK ((evaluation_fingerprint ~ '^sha256:[0-9a-f]{64}$'::text)),
    CONSTRAINT binding_decision_evidence_lineage_fingerprint_check CHECK ((lineage_fingerprint ~ '^sha256:[0-9a-f]{64}$'::text))
);


--
-- Name: capabilities; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.capabilities (
    id uuid NOT NULL,
    entity_id character varying(128) NOT NULL,
    capability character varying(128) NOT NULL,
    granted_by character varying(128),
    expires_at timestamp with time zone,
    created_at timestamp with time zone NOT NULL,
    active boolean DEFAULT true NOT NULL
);

ALTER TABLE ONLY peb.capabilities REPLICA IDENTITY FULL;


--
-- Name: cir_violations; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.cir_violations (
    violation_id text NOT NULL,
    cer_id text,
    event_id text NOT NULL,
    rule_id text NOT NULL,
    rule_version text NOT NULL,
    severity text NOT NULL,
    description text NOT NULL,
    detected_at timestamp with time zone,
    blocking boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT cir_violations_severity_check CHECK ((severity = ANY (ARRAY['blocking'::text, 'warning'::text, 'info'::text])))
);


--
-- Name: decisions; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.decisions (
    id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    adr_number character varying(32),
    title character varying(256) NOT NULL,
    status character varying(32) NOT NULL,
    summary jsonb,
    affected_keys text[],
    entropy_class character varying(32),
    before_hash character varying(64),
    after_hash character varying(64),
    author_id character varying(128) NOT NULL,
    parent_decision_id uuid,
    rollback_of uuid,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY peb.decisions REPLICA IDENTITY FULL;


--
-- Name: governance_events; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.governance_events (
    id bigint NOT NULL,
    receipt_id text NOT NULL,
    event_type text NOT NULL,
    work_request_id text,
    plan_id text NOT NULL,
    agent_role text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    replayed_at timestamp with time zone
);

ALTER TABLE ONLY peb.governance_events REPLICA IDENTITY FULL;


--
-- Name: governance_events_id_seq; Type: SEQUENCE; Schema: peb; Owner: -
--

CREATE SEQUENCE peb.governance_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: governance_events_id_seq; Type: SEQUENCE OWNED BY; Schema: peb; Owner: -
--

ALTER SEQUENCE peb.governance_events_id_seq OWNED BY peb.governance_events.id;


--
-- Name: role_circuit_breaker; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.role_circuit_breaker (
    role text NOT NULL,
    tripped integer DEFAULT 0,
    tripped_at timestamp with time zone,
    retry_after integer DEFAULT 1800,
    error text,
    failure_count integer DEFAULT 0,
    updated_at timestamp with time zone
);

ALTER TABLE ONLY peb.role_circuit_breaker REPLICA IDENTITY FULL;


--
-- Name: state; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.state (
    id uuid NOT NULL,
    key character varying(64) NOT NULL,
    content jsonb NOT NULL,
    metadata jsonb,
    checksum character varying(64) NOT NULL,
    version bigint NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY peb.state REPLICA IDENTITY FULL;


--
-- Name: traces; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.traces (
    id uuid NOT NULL,
    transaction_id uuid NOT NULL,
    work_request_id character varying(128) NOT NULL,
    parent_trace_id uuid,
    stage character varying(64) NOT NULL,
    inputs jsonb,
    causal_entries jsonb,
    rejected_alternatives jsonb,
    confidence real NOT NULL,
    status character varying(16) DEFAULT 'observational'::character varying NOT NULL,
    created_at timestamp with time zone NOT NULL,
    CONSTRAINT peb_traces_status_check CHECK (((status)::text = 'observational'::text))
);

ALTER TABLE ONLY peb.traces REPLICA IDENTITY FULL;


--
-- Name: transactions; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.transactions (
    id uuid NOT NULL,
    idempotency_key character varying(128) NOT NULL,
    entity_id character varying(128) NOT NULL,
    admission_result character varying(16) NOT NULL,
    tool_name character varying(64) NOT NULL,
    input jsonb NOT NULL,
    output jsonb,
    before_hash character varying(64),
    after_hash character varying(64),
    state_delta jsonb,
    created_at timestamp with time zone NOT NULL,
    committed_at timestamp with time zone,
    kernel_event_id uuid,
    kernel_event_type character varying(32)
);

ALTER TABLE ONLY peb.transactions REPLICA IDENTITY FULL;


--
-- Name: COLUMN transactions.kernel_event_id; Type: COMMENT; Schema: peb; Owner: -
--

COMMENT ON COLUMN peb.transactions.kernel_event_id IS 'FK to kernel.transition_event.event_id — the kernel event recorded
     for this PEB governance decision. Set by PebGovernanceEngine
     when it calls kernel.sys_transition().';


--
-- Name: COLUMN transactions.kernel_event_type; Type: COMMENT; Schema: peb; Owner: -
--

COMMENT ON COLUMN peb.transactions.kernel_event_type IS 'The kernel event_type recorded (transition.requested,
     transition.committed, transition.rejected).';


--
-- Name: violations; Type: TABLE; Schema: peb; Owner: -
--

CREATE TABLE peb.violations (
    id uuid NOT NULL,
    transaction_id uuid,
    violation_type character varying(32) NOT NULL,
    severity character varying(8) NOT NULL,
    entity_id character varying(128),
    capability_attempted character varying(128),
    context jsonb,
    resolution character varying(16),
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY peb.violations REPLICA IDENTITY FULL;


--
-- Name: environment_type; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.environment_type (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    description character varying(1000),
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: framework_type; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.framework_type (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    description character varying(1000),
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: library_type; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.library_type (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    description character varying(500),
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: operating_systems; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.operating_systems (
    id bigint NOT NULL,
    active_flag boolean,
    architecture character varying(255),
    created_at timestamp(6) without time zone,
    description character varying(255),
    family character varying(255),
    lts_flag boolean,
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone,
    version character varying(255)
);


--
-- Name: server_type; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.server_type (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    description character varying(1000),
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: service_config_type; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.service_config_type (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: service_type; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.service_type (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    description character varying(1000),
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone,
    default_component_id bigint
);


--
-- Name: system_type; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.system_type (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    description character varying(1000),
    active_flag boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: categories; Type: VIEW; Schema: registry; Owner: -
--

CREATE VIEW registry.categories AS
 SELECT framework_type.id,
    framework_type.name,
    framework_type.description,
    framework_type.active_flag,
    framework_type.created_at,
    framework_type.updated_at,
    'framework_type'::text AS type,
    NULL::bigint AS default_component_id,
    NULL::character varying AS architecture,
    NULL::character varying AS family,
    NULL::boolean AS lts_flag,
    NULL::character varying AS version
   FROM registry.framework_type
UNION ALL
 SELECT server_type.id,
    server_type.name,
    server_type.description,
    server_type.active_flag,
    server_type.created_at,
    server_type.updated_at,
    'server_type'::text AS type,
    NULL::bigint AS default_component_id,
    NULL::character varying AS architecture,
    NULL::character varying AS family,
    NULL::boolean AS lts_flag,
    NULL::character varying AS version
   FROM registry.server_type
UNION ALL
 SELECT library_type.id,
    library_type.name,
    library_type.description,
    library_type.active_flag,
    library_type.created_at,
    library_type.updated_at,
    'library_type'::text AS type,
    NULL::bigint AS default_component_id,
    NULL::character varying AS architecture,
    NULL::character varying AS family,
    NULL::boolean AS lts_flag,
    NULL::character varying AS version
   FROM registry.library_type
UNION ALL
 SELECT environment_type.id,
    environment_type.name,
    environment_type.description,
    environment_type.active_flag,
    environment_type.created_at,
    environment_type.updated_at,
    'environment_type'::text AS type,
    NULL::bigint AS default_component_id,
    NULL::character varying AS architecture,
    NULL::character varying AS family,
    NULL::boolean AS lts_flag,
    NULL::character varying AS version
   FROM registry.environment_type
UNION ALL
 SELECT service_type.id,
    service_type.name,
    service_type.description,
    service_type.active_flag,
    service_type.created_at,
    service_type.updated_at,
    'service_type'::text AS type,
    service_type.default_component_id,
    NULL::character varying AS architecture,
    NULL::character varying AS family,
    NULL::boolean AS lts_flag,
    NULL::character varying AS version
   FROM registry.service_type
UNION ALL
 SELECT service_config_type.id,
    service_config_type.name,
    NULL::character varying AS description,
    service_config_type.active_flag,
    service_config_type.created_at,
    service_config_type.updated_at,
    'service_config_type'::text AS type,
    NULL::bigint AS default_component_id,
    NULL::character varying AS architecture,
    NULL::character varying AS family,
    NULL::boolean AS lts_flag,
    NULL::character varying AS version
   FROM registry.service_config_type
UNION ALL
 SELECT operating_systems.id,
    operating_systems.name,
    operating_systems.description,
    operating_systems.active_flag,
    operating_systems.created_at,
    operating_systems.updated_at,
    'operating_systems'::text AS type,
    NULL::bigint AS default_component_id,
    operating_systems.architecture,
    operating_systems.family,
    operating_systems.lts_flag,
    operating_systems.version
   FROM registry.operating_systems
UNION ALL
 SELECT system_type.id,
    system_type.name,
    system_type.description,
    system_type.active_flag,
    system_type.created_at,
    system_type.updated_at,
    'system_type'::text AS type,
    NULL::bigint AS default_component_id,
    NULL::character varying AS architecture,
    NULL::character varying AS family,
    NULL::boolean AS lts_flag,
    NULL::character varying AS version
   FROM registry.system_type;


--
-- Name: deployments; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.deployments (
    id bigint NOT NULL,
    active_flag boolean,
    container_name character varying(255),
    context_path character varying(255),
    created_at timestamp(6) without time zone,
    deployed_at timestamp(6) without time zone,
    deployment_path character varying(255),
    health_check_url character varying(255),
    health_status character varying(255),
    last_health_check timestamp(6) without time zone,
    port integer,
    process_id character varying(255),
    started_at timestamp(6) without time zone,
    status character varying(255),
    stopped_at timestamp(6) without time zone,
    updated_at timestamp(6) without time zone,
    version character varying(255),
    environment_id bigint NOT NULL,
    host_id bigint NOT NULL,
    service_id bigint NOT NULL
);


--
-- Name: deployments_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.deployments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.deployments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: environment_type_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.environment_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.environment_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: framework_type_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.framework_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.framework_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: frameworks; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.frameworks (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    current_version character varying(255),
    description character varying(1000),
    lts_version character varying(255),
    name character varying(255) NOT NULL,
    supports_broker_pattern boolean,
    updated_at timestamp(6) without time zone,
    url character varying(255),
    category_id bigint NOT NULL,
    language_id bigint NOT NULL,
    vendor_id bigint
);


--
-- Name: frameworks_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.frameworks ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.frameworks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: graph_view_connections; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.graph_view_connections (
    id bigint NOT NULL,
    created_at timestamp(6) without time zone,
    direction character varying(20) NOT NULL,
    source_node_id character varying(255) NOT NULL,
    target_node_id character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone,
    graph_view_id bigint NOT NULL
);


--
-- Name: graph_view_connections_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.graph_view_connections ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.graph_view_connections_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: graph_view_positions; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.graph_view_positions (
    id bigint NOT NULL,
    created_at timestamp(6) without time zone,
    node_id character varying(255) NOT NULL,
    position_x double precision NOT NULL,
    position_y double precision NOT NULL,
    position_z double precision NOT NULL,
    updated_at timestamp(6) without time zone,
    graph_view_id bigint NOT NULL,
    label character varying(255),
    description text,
    color character varying(255)
);


--
-- Name: graph_view_positions_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.graph_view_positions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.graph_view_positions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: graph_views; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.graph_views (
    id bigint NOT NULL,
    camera_position_x double precision NOT NULL,
    camera_position_y double precision NOT NULL,
    camera_position_z double precision NOT NULL,
    camera_target_x double precision NOT NULL,
    camera_target_y double precision NOT NULL,
    camera_target_z double precision NOT NULL,
    created_at timestamp(6) without time zone,
    description text,
    is_default boolean NOT NULL,
    name character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone,
    camera2_position_x double precision DEFAULT 0 NOT NULL,
    camera2_position_y double precision DEFAULT 40 NOT NULL,
    camera2_position_z double precision DEFAULT 120 NOT NULL,
    camera2_target_x double precision DEFAULT 0 NOT NULL,
    camera2_target_y double precision DEFAULT 15 NOT NULL,
    camera2_target_z double precision DEFAULT 0 NOT NULL,
    connections jsonb
);


--
-- Name: graph_views_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.graph_views ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.graph_views_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: languages; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.languages (
    id bigint NOT NULL,
    active_flag boolean,
    current_version character varying(255),
    description character varying(1000),
    lts_version character varying(255),
    name character varying(255) NOT NULL,
    url character varying(255)
);


--
-- Name: languages_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.languages ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.languages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: libraries; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.libraries (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    current_version character varying(255),
    description character varying(1000),
    license character varying(255),
    name character varying(255) NOT NULL,
    package_manager character varying(255),
    package_name character varying(255),
    repository_url character varying(255),
    updated_at timestamp(6) without time zone,
    url character varying(255),
    category_id bigint,
    language_id bigint
);


--
-- Name: libraries_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.libraries ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.libraries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: library_type_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.library_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.library_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: operating_systems_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.operating_systems ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.operating_systems_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: server_type_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.server_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.server_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: servers; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.servers (
    id bigint NOT NULL,
    active_flag boolean,
    cloud_provider character varying(255),
    cpu_cores integer,
    created_at timestamp(6) without time zone,
    description character varying(1000),
    disk character varying(255),
    hostname character varying(255) NOT NULL,
    ip_address character varying(255) NOT NULL,
    memory character varying(255),
    region character varying(255),
    status character varying(255),
    updated_at timestamp(6) without time zone,
    environment_type_id bigint NOT NULL,
    operating_system_id bigint NOT NULL,
    server_type_id bigint NOT NULL
);


--
-- Name: servers_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.servers ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.servers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_backends; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.service_backends (
    id bigint NOT NULL,
    backend_deployment_id bigint NOT NULL,
    created_at timestamp(6) without time zone,
    description character varying(500),
    is_active boolean NOT NULL,
    priority integer,
    role character varying(255) NOT NULL,
    routing_key character varying(100),
    service_deployment_id bigint NOT NULL,
    updated_at timestamp(6) without time zone,
    weight integer,
    CONSTRAINT service_backends_role_check CHECK (((role)::text = ANY ((ARRAY['PRIMARY'::character varying, 'BACKUP'::character varying, 'ARCHIVE'::character varying, 'CACHE'::character varying, 'SHARD'::character varying, 'READ_REPLICA'::character varying])::text[])))
);


--
-- Name: service_backends_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.service_backends ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.service_backends_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_config_type_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.service_config_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.service_config_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_configs; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.service_configs (
    id bigint NOT NULL,
    active_flag boolean,
    config_key character varying(255) NOT NULL,
    config_type_id bigint NOT NULL,
    config_value character varying(255) NOT NULL,
    created_at timestamp(6) without time zone,
    description character varying(1000),
    environment_id bigint NOT NULL,
    service_id bigint NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: service_configs_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.service_configs ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.service_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_dependencies; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.service_dependencies (
    id bigint NOT NULL,
    active_flag boolean,
    created_at timestamp(6) without time zone,
    criticality character varying(255),
    description character varying(1000),
    service_id bigint NOT NULL,
    target_service_id bigint NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: service_dependencies_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.service_dependencies ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.service_dependencies_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_type_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.service_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.service_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: services_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.services ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.services_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: status_events; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.status_events (
    id bigint NOT NULL,
    changed_at timestamp(6) without time zone NOT NULL,
    error_message text,
    new_state character varying(50) NOT NULL,
    old_state character varying(50),
    reason character varying(255),
    response_time_ms bigint,
    service_name character varying(255) NOT NULL
);


--
-- Name: status_events_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.status_events ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.status_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: system_services; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.system_services (
    id bigint NOT NULL,
    system_id bigint NOT NULL,
    service_id bigint NOT NULL,
    role_in_system character varying(100),
    active_flag boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: system_services_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.system_services ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.system_services_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: system_type_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.system_type ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.system_type_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: systems; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.systems (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    system_type_id bigint NOT NULL,
    description character varying(1000),
    active_flag boolean DEFAULT true,
    created_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    asset_id uuid
);


--
-- Name: systems_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.systems ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.systems_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: vendors; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.vendors (
    id bigint NOT NULL,
    active_flag boolean,
    description character varying(1000),
    name character varying(255) NOT NULL,
    url character varying(255)
);


--
-- Name: vendors_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.vendors ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.vendors_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: visual_components; Type: TABLE; Schema: registry; Owner: -
--

CREATE TABLE registry.visual_components (
    id bigint NOT NULL,
    color_class character varying(255),
    created_at timestamp(6) without time zone,
    default_color bigint NOT NULL,
    description text,
    geometry character varying(255) NOT NULL,
    icon_class character varying(255),
    is_system boolean,
    name character varying(255) NOT NULL,
    scale double precision NOT NULL,
    type character varying(255) NOT NULL,
    updated_at timestamp(6) without time zone
);


--
-- Name: visual_components_id_seq; Type: SEQUENCE; Schema: registry; Owner: -
--

ALTER TABLE registry.visual_components ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME registry.visual_components_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: assertion_evaluation; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.assertion_evaluation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    proposition_id uuid NOT NULL,
    rule_id uuid NOT NULL,
    result boolean NOT NULL,
    compiled_sql text,
    evaluated_at timestamp with time zone DEFAULT now() NOT NULL,
    trigger_reason text DEFAULT 'manual'::text,
    CONSTRAINT assertion_evaluation_trigger_reason_check CHECK ((trigger_reason = ANY (ARRAY['pending_created'::text, 'upstream_changed'::text, 'explicit_repair'::text, 'clock_stale_retry'::text, 'manual'::text])))
);


--
-- Name: assessment; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.assessment (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    observation_id uuid NOT NULL,
    outcome text NOT NULL,
    confidence numeric(4,3),
    impact_scope jsonb DEFAULT '{}'::jsonb NOT NULL,
    analysis_detail text,
    rationale jsonb,
    dimensions_used integer,
    dimensions_total integer,
    agenda_id uuid,
    auto_resolve_plan_id uuid,
    forum_post_id uuid,
    resolved_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT assessment_outcome_check CHECK ((outcome = ANY (ARRAY['informational'::text, 'recommendation'::text, 'needs_deliberation'::text, 'policy_blocked'::text, 'auto_resolved'::text, 'rejected'::text])))
);


--
-- Name: binding_resolution_transition; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.binding_resolution_transition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    decision_evidence_id uuid NOT NULL,
    subject_id text NOT NULL,
    work_item_id text NOT NULL,
    transition_name text NOT NULL,
    transition_status text NOT NULL,
    idempotency_key text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP NOT NULL,
    CONSTRAINT binding_resolution_transition_transition_status_check CHECK ((transition_status = ANY (ARRAY['applied'::text, 'refused'::text])))
);


--
-- Name: candidate; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.candidate (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    harvest_id uuid NOT NULL,
    title text NOT NULL,
    intent_description text,
    implementation_notes jsonb DEFAULT '[]'::jsonb NOT NULL,
    code_snippets jsonb DEFAULT '[]'::jsonb NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    status text,
    type text DEFAULT 'requirement'::text NOT NULL,
    design_rationale jsonb DEFAULT '[]'::jsonb NOT NULL,
    compilation_readiness numeric(4,3),
    completed boolean DEFAULT false NOT NULL,
    needs_new_node boolean DEFAULT false NOT NULL,
    proposed_parent text,
    proposed_name text,
    placement_reason text,
    system_id uuid,
    subsystem_id uuid,
    feature_id uuid,
    work_request_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT candidate_status_check CHECK (((status IS NULL) OR (status = ANY (ARRAY['pending'::text, 'linked'::text, 'useful'::text, 'rejected'::text, 'promoted'::text, 'superseded'::text])))),
    CONSTRAINT candidate_type_check CHECK ((type = ANY (ARRAY['requirement'::text, 'principle'::text, 'rejected_alternative'::text, 'tension'::text, 'rationale'::text, 'mixed'::text])))
);


--
-- Name: candidate_segment_set; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.candidate_segment_set (
    candidate_id uuid NOT NULL,
    segment_set_id uuid NOT NULL,
    role text DEFAULT 'primary'::text NOT NULL,
    CONSTRAINT candidate_segment_set_role_check CHECK ((role = ANY (ARRAY['primary'::text, 'supporting'::text])))
);


--
-- Name: candidate_source_chunk; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.candidate_source_chunk (
    candidate_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    "position" integer NOT NULL
);


--
-- Name: canonical_asset; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.canonical_asset (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    canonical_asset_id text NOT NULL,
    asset_kind text NOT NULL,
    canonical_key jsonb,
    source_hash text,
    content_hash text,
    validity_start timestamp with time zone,
    validity_end timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: concept_attribute; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.concept_attribute (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    concept_id uuid NOT NULL,
    name text NOT NULL,
    description text,
    value_type text NOT NULL,
    is_state_attribute boolean DEFAULT false NOT NULL
);


--
-- Name: concept_attribute_binding; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.concept_attribute_binding (
    attribute_id uuid NOT NULL,
    schema_name text NOT NULL,
    table_name text NOT NULL,
    column_name text NOT NULL
);


--
-- Name: concept_attribute_value; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.concept_attribute_value (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    attribute_id uuid NOT NULL,
    value text NOT NULL,
    description text
);


--
-- Name: concept_relationship; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.concept_relationship (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    from_concept_id uuid NOT NULL,
    to_concept_id uuid NOT NULL,
    relationship_type text NOT NULL,
    path text,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: concept_relationship_binding; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.concept_relationship_binding (
    concept_relationship_id uuid NOT NULL,
    from_schema text NOT NULL,
    from_table text NOT NULL,
    from_column text NOT NULL,
    to_schema text NOT NULL,
    to_table text NOT NULL,
    to_column text NOT NULL,
    notes text
);


--
-- Name: concept_state_transition; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.concept_state_transition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    concept_id uuid NOT NULL,
    from_value_id uuid,
    to_value_id uuid NOT NULL,
    name text NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: consumer_operation; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.consumer_operation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    representation_id uuid NOT NULL,
    consumer_name text NOT NULL,
    operation text NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: enforcement_posture; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.enforcement_posture (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    family text NOT NULL,
    mode text NOT NULL,
    authorized_by text,
    effective_from timestamp with time zone NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT enforcement_posture_mode_check CHECK ((mode = ANY (ARRAY['enforced'::text, 'shadow'::text])))
);


--
-- Name: execution_admission_receipt; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.execution_admission_receipt (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    peb_transaction_id uuid NOT NULL,
    claim_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    evidence_kind text NOT NULL,
    source_system text NOT NULL,
    policy_version_hash text NOT NULL,
    lease_id text NOT NULL,
    grant_id text NOT NULL,
    attempt_id text NOT NULL,
    admitted boolean NOT NULL,
    reason text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE execution_admission_receipt; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.execution_admission_receipt IS 'Resolution-side assessment of whether independently verified execution evidence is eligible for PEB admission. It is not a PEB settlement receipt.';


--
-- Name: execution_claim; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.execution_claim (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    claim_key text NOT NULL,
    proposition_id uuid,
    subject_kind text NOT NULL,
    subject_ref jsonb DEFAULT '{}'::jsonb NOT NULL,
    predicate text NOT NULL,
    object_value jsonb DEFAULT '{}'::jsonb NOT NULL,
    policy_version_hash text,
    lease_id text,
    grant_id text,
    attempt_id text,
    declared_by text NOT NULL,
    declared_at timestamp with time zone DEFAULT now() NOT NULL,
    observed_at timestamp with time zone,
    disposition text DEFAULT 'Proposed'::text NOT NULL,
    verification_method text,
    verified_by text,
    verified_at timestamp with time zone,
    verification_summary jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT execution_claim_check CHECK (((disposition <> 'Asserted'::text) OR ((verification_method IS NOT NULL) AND (verified_by IS NOT NULL) AND (verified_at IS NOT NULL)))),
    CONSTRAINT execution_claim_disposition_check CHECK ((disposition = ANY (ARRAY['Proposed'::text, 'Pending'::text, 'Asserted'::text, 'Disputed'::text, 'Rejected'::text, 'Stale'::text, 'Retracted'::text])))
);


--
-- Name: TABLE execution_claim; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.execution_claim IS 'SOL execution claim. Inserted claims are proposals; Asserted requires resolution evaluation plus independent evidence. PEB settlement/acceptance is a separate authoritative receipt.';


--
-- Name: execution_claim_evidence; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.execution_claim_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    claim_id uuid NOT NULL,
    evidence_id uuid NOT NULL,
    role text NOT NULL,
    verification_state text DEFAULT 'candidate'::text NOT NULL,
    strength numeric,
    linked_by text NOT NULL,
    linked_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    CONSTRAINT execution_claim_evidence_role_check CHECK ((role = ANY (ARRAY['supports'::text, 'contradicts'::text, 'contextualizes'::text, 'originated_from'::text, 'supersedes'::text]))),
    CONSTRAINT execution_claim_evidence_strength_check CHECK (((strength IS NULL) OR ((strength >= (0)::numeric) AND (strength <= (1)::numeric)))),
    CONSTRAINT execution_claim_evidence_verification_state_check CHECK ((verification_state = ANY (ARRAY['candidate'::text, 'confirmed'::text, 'contested'::text, 'superseded'::text])))
);


--
-- Name: TABLE execution_claim_evidence; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.execution_claim_evidence IS 'Evidence is related to a claim with polarity and verification state. Active links are append-only/expire-not-delete; the link does not itself settle a PEB execution outcome.';


--
-- Name: execution_evidence; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.execution_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    evidence_key text NOT NULL,
    evidence_kind text NOT NULL,
    source_system text NOT NULL,
    source_ref jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_hash text NOT NULL,
    captured_at timestamp with time zone NOT NULL,
    captured_by text NOT NULL,
    context_kind text DEFAULT 'provenance'::text NOT NULL,
    policy_version_hash text,
    lease_id text,
    grant_id text,
    attempt_id text,
    verifier_id text,
    verifier_independence boolean,
    verifier_method text,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT execution_evidence_check CHECK (((context_kind <> 'execution'::text) OR ((policy_version_hash IS NOT NULL) AND (lease_id IS NOT NULL) AND (grant_id IS NOT NULL) AND (attempt_id IS NOT NULL)))),
    CONSTRAINT execution_evidence_context_kind_check CHECK ((context_kind = ANY (ARRAY['execution'::text, 'provenance'::text])))
);


--
-- Name: TABLE execution_evidence; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.execution_evidence IS 'Immutable execution observation. Content identity is source_system + evidence_kind + source_hash; evidence alone never establishes claim authority.';


--
-- Name: expression; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.expression (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    kind text NOT NULL,
    operator text,
    literal_value text,
    attribute_id uuid,
    function_name text,
    return_type text NOT NULL,
    label text,
    concept_relationship_id uuid,
    quantifier text,
    referenced_proposition_id uuid,
    proposition_ref_field text,
    CONSTRAINT expression_kind_check CHECK ((kind = ANY (ARRAY['literal'::text, 'attribute_ref'::text, 'operator'::text, 'function_call'::text, 'relationship_ref'::text, 'proposition_ref'::text]))),
    CONSTRAINT expression_kind_fields_check CHECK ((((kind = 'literal'::text) AND (literal_value IS NOT NULL) AND (attribute_id IS NULL) AND (function_name IS NULL) AND (concept_relationship_id IS NULL) AND (operator IS NULL) AND (referenced_proposition_id IS NULL)) OR ((kind = 'attribute_ref'::text) AND (attribute_id IS NOT NULL) AND (literal_value IS NULL) AND (function_name IS NULL) AND (concept_relationship_id IS NULL) AND (operator IS NULL) AND (referenced_proposition_id IS NULL)) OR ((kind = 'operator'::text) AND (operator IS NOT NULL) AND (attribute_id IS NULL) AND (literal_value IS NULL) AND (function_name IS NULL) AND (concept_relationship_id IS NULL) AND (referenced_proposition_id IS NULL)) OR ((kind = 'function_call'::text) AND (function_name IS NOT NULL) AND (attribute_id IS NULL) AND (literal_value IS NULL) AND (concept_relationship_id IS NULL) AND (operator IS NULL) AND (referenced_proposition_id IS NULL)) OR ((kind = 'relationship_ref'::text) AND (concept_relationship_id IS NOT NULL) AND (quantifier IS NOT NULL) AND (attribute_id IS NULL) AND (literal_value IS NULL) AND (function_name IS NULL) AND (operator IS NULL) AND (referenced_proposition_id IS NULL)) OR ((kind = 'proposition_ref'::text) AND (referenced_proposition_id IS NOT NULL) AND (attribute_id IS NULL) AND (literal_value IS NULL) AND (function_name IS NULL) AND (concept_relationship_id IS NULL) AND (operator IS NULL)))),
    CONSTRAINT expression_operator_whitelist_check CHECK (((operator IS NULL) OR (operator = ANY (ARRAY['='::text, '<>'::text, '>'::text, '<'::text, '>='::text, '<='::text, 'AND'::text, 'OR'::text])))),
    CONSTRAINT expression_proposition_ref_field_check CHECK (((proposition_ref_field IS NULL) OR (proposition_ref_field = ANY (ARRAY['disposition'::text, 'value'::text])))),
    CONSTRAINT expression_quantifier_check CHECK (((quantifier IS NULL) OR (quantifier = ANY (ARRAY['EXISTS'::text, 'ALL'::text, 'COUNT'::text]))))
);


--
-- Name: expression_operand; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.expression_operand (
    parent_expression_id uuid NOT NULL,
    child_expression_id uuid NOT NULL,
    "position" integer NOT NULL,
    CONSTRAINT expression_operand_check CHECK ((parent_expression_id <> child_expression_id))
);


--
-- Name: frame_dimension; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.frame_dimension (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text,
    value_kind text NOT NULL,
    scalar_type text,
    CONSTRAINT frame_dimension_check CHECK ((((value_kind = 'typed_scalar'::text) AND (scalar_type IS NOT NULL)) OR ((value_kind = 'governed_reference'::text) AND (scalar_type IS NULL)))),
    CONSTRAINT frame_dimension_scalar_type_check CHECK (((scalar_type IS NULL) OR (scalar_type = ANY (ARRAY['text'::text, 'integer'::text, 'boolean'::text, 'timestamp'::text, 'numeric'::text])))),
    CONSTRAINT frame_dimension_value_kind_check CHECK ((value_kind = ANY (ARRAY['governed_reference'::text, 'typed_scalar'::text])))
);


--
-- Name: frame_dimension_meaning; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.frame_dimension_meaning (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    proposition_id uuid NOT NULL,
    dimension_id uuid,
    frame_dimension_value_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT frame_dimension_meaning_check CHECK ((((dimension_id IS NOT NULL) AND (frame_dimension_value_id IS NULL)) OR ((dimension_id IS NULL) AND (frame_dimension_value_id IS NOT NULL))))
);


--
-- Name: TABLE frame_dimension_meaning; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.frame_dimension_meaning IS 'Links a proposition that describes the meaning of a frame dimension (or one of its values). This is the DESCRIPTION half of frame semantics; v31/v32 is the ENFORCEMENT half. Meaning propositions are about context, not scoped by it, so they carry no proposition_frame_value rows.';


--
-- Name: frame_dimension_value; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.frame_dimension_value (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    dimension_id uuid NOT NULL,
    value text NOT NULL,
    description text
);


--
-- Name: function_binding; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.function_binding (
    function_name text NOT NULL,
    sql_template text NOT NULL,
    arg_count integer NOT NULL,
    return_type text NOT NULL,
    notes text
);


--
-- Name: governance_threshold; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.governance_threshold (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    value numeric NOT NULL,
    effective_from timestamp with time zone DEFAULT now() NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE governance_threshold; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.governance_threshold IS 'Versioned governed thresholds. Current value = max(effective_from) <= now(). Threshold changes are rows, not constant edits.';


--
-- Name: harvest; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.harvest (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    source_path text NOT NULL,
    source_filename text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    total_candidates integer DEFAULT 0 NOT NULL,
    source_text text,
    docklang jsonb,
    source_hash text,
    version integer DEFAULT 1 NOT NULL,
    run_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    file_size bigint,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    level integer DEFAULT 1 NOT NULL,
    visibility_scope text DEFAULT 'all'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT harvest_level_check CHECK (((level >= 1) AND (level <= 4)))
);


--
-- Name: identity_strategy; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.identity_strategy (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    concept_id uuid NOT NULL,
    canonical_key_description text NOT NULL,
    notes text
);


--
-- Name: implementation_plan; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.implementation_plan (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    plan_number text,
    specification_id uuid,
    requirement_id uuid,
    title text NOT NULL,
    goal text,
    content text,
    files_affected text[] DEFAULT '{}'::text[],
    acceptance_criteria jsonb DEFAULT '[]'::jsonb,
    dependencies text[] DEFAULT '{}'::text[],
    status text DEFAULT 'draft'::text NOT NULL,
    tags text[] DEFAULT '{}'::text[],
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT implementation_plan_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'pending'::text, 'approved'::text, 'work_requested'::text, 'completed'::text, 'archived'::text])))
);


--
-- Name: keychain_evaluation_manifest; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.keychain_evaluation_manifest (
    manifest_id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_namespace text NOT NULL,
    evaluation_id text NOT NULL,
    evaluation_kind text NOT NULL,
    target_id text NOT NULL,
    as_of timestamp with time zone NOT NULL,
    visibility_scope text DEFAULT 'all'::text NOT NULL,
    permissions jsonb DEFAULT '{}'::jsonb NOT NULL,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_refs jsonb DEFAULT '[]'::jsonb NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    manifest_digest text NOT NULL,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    status text DEFAULT 'captured'::text NOT NULL,
    result jsonb,
    completed_at timestamp with time zone,
    evaluator_id text,
    CONSTRAINT keychain_eval_manifest_status_ck CHECK ((status = ANY (ARRAY['captured'::text, 'completed'::text, 'failed'::text])))
);


--
-- Name: TABLE keychain_evaluation_manifest; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.keychain_evaluation_manifest IS 'Pre-evaluation read-set receipts for Keychains rewind/replay; source content remains authoritative elsewhere.';


--
-- Name: COLUMN keychain_evaluation_manifest.source_refs; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON COLUMN resolution.keychain_evaluation_manifest.source_refs IS 'References to concepts, entities, rules, frames, assets, and revisions available to the evaluator.';


--
-- Name: COLUMN keychain_evaluation_manifest.result; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON COLUMN resolution.keychain_evaluation_manifest.result IS 'Compact serialized evaluation response used for source-scoped idempotent retries.';


--
-- Name: keychain_event_outbox; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.keychain_event_outbox (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    source_namespace text NOT NULL,
    source_event_id text NOT NULL,
    event_kind text NOT NULL,
    outcome text NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL,
    aggregate_id text,
    causation_id text,
    correlation_id text,
    actor text,
    contract_id text,
    evaluator_id text,
    law_id text,
    effective_at timestamp with time zone,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    read_set jsonb DEFAULT '{}'::jsonb NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    checkpoint_status text DEFAULT 'pending'::text NOT NULL,
    delivery_attempts integer DEFAULT 0 NOT NULL,
    delivered_at timestamp with time zone,
    last_error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    claimed_at timestamp with time zone,
    CONSTRAINT keychain_event_outbox_checkpoint_status_ck CHECK ((checkpoint_status = ANY (ARRAY['pending'::text, 'delivering'::text, 'delivered'::text, 'not_applicable'::text, 'failed'::text]))),
    CONSTRAINT keychain_event_outbox_outcome_ck CHECK ((outcome = ANY (ARRAY['committed'::text, 'refused'::text, 'rejected'::text, 'unknown'::text, 'stale'::text, 'drift'::text, 'quarantined'::text, 'superseded'::text, 'rolled_back'::text, 'failed'::text])))
);


--
-- Name: TABLE keychain_event_outbox; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.keychain_event_outbox IS 'Append-only Keychains source events and delivery state; source authority remains Resolution/SOL.';


--
-- Name: COLUMN keychain_event_outbox.read_set; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON COLUMN resolution.keychain_event_outbox.read_set IS 'Stable identities, versions, hashes, cursors, and access scope available to the evaluator.';


--
-- Name: migration_ledger; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.migration_ledger (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    schema_name text NOT NULL,
    migration_label text NOT NULL,
    description text,
    applied_by text DEFAULT CURRENT_USER NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE migration_ledger; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.migration_ledger IS 'Per-host ledger of applied migrations. A host that cannot answer "which migrations have I applied?" from this table is not verifiable. Backfill entries carry approximate applied_at from session records; see DBA findings 7777e2c1.';


--
-- Name: observation; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.observation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trigger_type text NOT NULL,
    asset_concept_id uuid,
    source_artifact_id uuid,
    predicate_type text,
    predicate_id uuid,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    assessed boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT observation_predicate_type_check CHECK (((predicate_type IS NULL) OR (predicate_type = ANY (ARRAY['concept_attribute'::text, 'concept_relationship'::text, 'expression'::text]))))
);


--
-- Name: TABLE observation; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.observation IS 'predicate_type now includes expression: a candidate rejection reason can point directly at the SOL IR expression node that failed, not just a concept_attribute/concept_relationship.';


--
-- Name: observation_source_chunk; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.observation_source_chunk (
    observation_id uuid NOT NULL,
    chunk_id uuid NOT NULL,
    "position" integer NOT NULL
);


--
-- Name: open_question; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.open_question (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    description text,
    blocking boolean DEFAULT true NOT NULL,
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    category_value_id uuid,
    status_value_id uuid,
    assessment_id uuid
);


--
-- Name: TABLE open_question; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.open_question IS 'Ported from nebula.open_questions_history. category/status REPLACED by governed concept_attribute_value + concept_state_transition (real lifecycle: OPEN -> IN_DELIBERATION -> RESOLVED/WONT_FIX/DEFERRED, instead of a flat CHECK). requirement_id/candidate_id direct columns DROPPED — that was drift, duplicating open_question_entities; all linkage now goes through open_question_entity, the same predicate-reference pattern used on observation.';


--
-- Name: open_question_answer; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.open_question_answer (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    question_id uuid NOT NULL,
    role text NOT NULL,
    answer text NOT NULL,
    confidence text DEFAULT 'MEDIUM'::text,
    reasoning text,
    answered_at timestamp with time zone DEFAULT now() NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL
);


--
-- Name: open_question_entity; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.open_question_entity (
    open_question_id uuid NOT NULL,
    asset_concept_id uuid NOT NULL,
    entity_id uuid NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL
);


--
-- Name: owning_subsystem; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.owning_subsystem (
    id smallint NOT NULL,
    name text NOT NULL,
    description text
);


--
-- Name: proposition; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.proposition (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    title text NOT NULL,
    description text,
    asset_concept_id uuid,
    subject_entity_id uuid,
    disposition_value_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    last_evaluated_at timestamp with time zone,
    grounding_status_value_id uuid,
    value boolean,
    semantic_type_id uuid
);


--
-- Name: proposition_assertion; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.proposition_assertion (
    proposition_id uuid NOT NULL,
    rule_id uuid NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: proposition_comparison; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.proposition_comparison (
    proposition_id uuid NOT NULL,
    representation_comparison_id uuid NOT NULL,
    added_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: proposition_frame_value; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.proposition_frame_value (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    proposition_id uuid NOT NULL,
    dimension_id uuid NOT NULL,
    reference_value_id uuid,
    scalar_value text,
    CONSTRAINT proposition_frame_value_check CHECK ((((reference_value_id IS NOT NULL) AND (scalar_value IS NULL)) OR ((reference_value_id IS NULL) AND (scalar_value IS NOT NULL))))
);


--
-- Name: representation; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.representation (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    concept_id uuid NOT NULL,
    label text NOT NULL,
    schema_name text,
    table_name text,
    owning_subsystem_id smallint,
    owner text,
    raw_metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: representation_comparison; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.representation_comparison (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    representation_relationship_id uuid NOT NULL,
    from_column text NOT NULL,
    to_column text NOT NULL,
    notes text
);


--
-- Name: representation_identity; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.representation_identity (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    representation_id uuid NOT NULL,
    identity_strategy_id uuid NOT NULL,
    identity_expression text NOT NULL,
    notes text
);


--
-- Name: representation_relationship; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.representation_relationship (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    from_representation_id uuid NOT NULL,
    to_representation_id uuid NOT NULL,
    relationship_type text NOT NULL,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    CONSTRAINT representation_relationship_check CHECK ((from_representation_id <> to_representation_id))
);


--
-- Name: requirement; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.requirement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    candidate_id uuid,
    parent_id uuid,
    source_type text NOT NULL,
    system_id uuid,
    subsystem_id uuid,
    feature_id uuid,
    title text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'Backlog'::text NOT NULL,
    priority text DEFAULT 'Medium'::text NOT NULL,
    req_type text,
    compilation_status text DEFAULT 'draft'::text NOT NULL,
    sol_ir_expression_id uuid,
    start_date text,
    completion_date text,
    acceptance_criteria jsonb DEFAULT '[]'::jsonb,
    conduit_plan_id character varying(32),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT requirement_check CHECK (((source_type = 'candidate'::text) OR (candidate_id IS NULL))),
    CONSTRAINT requirement_compilation_status_check CHECK ((compilation_status = ANY (ARRAY['draft'::text, 'compiled'::text, 'rejected'::text]))),
    CONSTRAINT requirement_priority_check CHECK ((priority = ANY (ARRAY['Low'::text, 'Medium'::text, 'High'::text]))),
    CONSTRAINT requirement_req_type_check CHECK (((req_type IS NULL) OR (req_type = ANY (ARRAY['Epic'::text, 'Story'::text, 'Task'::text, 'Bug'::text])))),
    CONSTRAINT requirement_source_type_check CHECK ((source_type = ANY (ARRAY['candidate'::text, 'manual'::text]))),
    CONSTRAINT requirement_status_check CHECK ((status = ANY (ARRAY['Backlog'::text, 'ToDo'::text, 'InProgress'::text, 'Active'::text, 'Blocked'::text, 'Done'::text, 'Cancelled'::text, 'Accepted'::text])))
);


--
-- Name: requirement_segment_set; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.requirement_segment_set (
    requirement_id uuid NOT NULL,
    segment_set_id uuid NOT NULL,
    role text DEFAULT 'primary'::text NOT NULL,
    CONSTRAINT requirement_segment_set_role_check CHECK ((role = ANY (ARRAY['primary'::text, 'supporting'::text])))
);


--
-- Name: rule; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.rule (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    rule_type text NOT NULL,
    expression_id uuid,
    severity text DEFAULT 'hard'::text NOT NULL,
    concept_id uuid,
    concept_relationship_id uuid,
    representation_id uuid,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    state_transition_id uuid,
    is_relational_check boolean DEFAULT false NOT NULL,
    staleness_window interval,
    CONSTRAINT rule_check CHECK (((((((concept_id IS NOT NULL))::integer + ((concept_relationship_id IS NOT NULL))::integer) + ((representation_id IS NOT NULL))::integer) + ((state_transition_id IS NOT NULL))::integer) = 1)),
    CONSTRAINT rule_rule_type_check CHECK ((rule_type = ANY (ARRAY['invariant'::text, 'guard'::text, 'conditional'::text, 'derivation'::text]))),
    CONSTRAINT rule_severity_check CHECK ((severity = ANY (ARRAY['hard'::text, 'soft'::text])))
);


--
-- Name: semantic_type; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.semantic_type (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text,
    default_staleness_window interval
);


--
-- Name: semantic_type_required_dimension; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.semantic_type_required_dimension (
    semantic_type_id uuid NOT NULL,
    dimension_id uuid NOT NULL
);


--
-- Name: specification; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.specification (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    requirement_id uuid,
    agenda_id uuid NOT NULL,
    revision_number integer NOT NULL,
    revision_type text NOT NULL,
    superseded_by uuid,
    item_snapshot jsonb DEFAULT '[]'::jsonb NOT NULL,
    change_summary text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT specification_revision_type_check CHECK ((revision_type = ANY (ARRAY['created'::text, 'revised'::text, 'merged'::text, 'split'::text, 'retired'::text])))
);


--
-- Name: TABLE specification; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.specification IS 'Ported from nebula.specifications_history. derived_from uuid[] REPLACED by resolution.specification_lineage — merge/split lineage is a DAG and wants to be queried relationally (same reasoning as candidate_source_chunk).';


--
-- Name: specification_lineage; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.specification_lineage (
    specification_id uuid NOT NULL,
    derived_from_id uuid NOT NULL,
    CONSTRAINT specification_lineage_check CHECK ((specification_id <> derived_from_id))
);


--
-- Name: t24_graph_edge_evidence; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.t24_graph_edge_evidence (
    evidence_id uuid NOT NULL,
    graph_edge_id uuid NOT NULL,
    source_section text NOT NULL,
    source_id text NOT NULL,
    relation_type text NOT NULL,
    target_section text,
    target_id text NOT NULL,
    edge_properties jsonb DEFAULT '{}'::jsonb NOT NULL,
    source_migration_id uuid,
    graph_resolution text DEFAULT 'unknown'::text NOT NULL,
    unresolved_reason text,
    graph_created_at timestamp with time zone,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT t24_graph_edge_evidence_check CHECK ((((graph_resolution = 'unresolved'::text) AND (unresolved_reason IS NOT NULL)) OR (graph_resolution <> 'unresolved'::text))),
    CONSTRAINT t24_graph_edge_evidence_check1 CHECK (((graph_resolution <> 'unresolved'::text) OR (target_section IS NULL))),
    CONSTRAINT t24_graph_edge_evidence_graph_resolution_check CHECK ((graph_resolution = ANY (ARRAY['resolved'::text, 'unresolved'::text, 'unknown'::text])))
);


--
-- Name: TABLE t24_graph_edge_evidence; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.t24_graph_edge_evidence IS 'Lossless T24 graph-edge provenance attached to immutable SOL execution_evidence. Resolved endpoint identity is not semantic truth or execution acceptance.';


--
-- Name: v_t24_execution_evidence; Type: VIEW; Schema: resolution; Owner: -
--

CREATE VIEW resolution.v_t24_execution_evidence AS
 SELECT ee.id AS evidence_id,
    ee.evidence_key,
    ee.evidence_kind,
    ee.source_system,
    ee.source_hash,
    ee.captured_at,
    ee.captured_by,
    ee.context_kind,
    ee.policy_version_hash,
    ee.lease_id,
    ee.grant_id,
    ee.attempt_id,
    ee.verifier_id,
    ee.verifier_independence,
    ge.graph_edge_id,
    ge.source_section,
    ge.source_id,
    ge.relation_type,
    ge.target_section,
    ge.target_id,
    ge.edge_properties,
    ge.source_migration_id,
    ge.graph_resolution,
    ge.unresolved_reason,
    ce.claim_id,
    ce.role AS claim_evidence_role,
    ce.verification_state
   FROM ((resolution.execution_evidence ee
     JOIN resolution.t24_graph_edge_evidence ge ON ((ge.evidence_id = ee.id)))
     LEFT JOIN resolution.execution_claim_evidence ce ON ((ce.evidence_id = ee.id)));


--
-- Name: verified_statement; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.verified_statement (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    answer_id uuid NOT NULL,
    expression_id uuid NOT NULL,
    asset_concept_id uuid NOT NULL,
    target_asset_id uuid NOT NULL,
    verified_by text NOT NULL,
    verified_at timestamp with time zone DEFAULT now() NOT NULL,
    notes text
);


--
-- Name: TABLE verified_statement; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.verified_statement IS 'The compile step. A verified answer becomes an asserted SOL IR fact about target_asset_id — this is what closes the loop back to expression/predicate_type=''expression'' on observation.';


--
-- Name: work_request; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.work_request (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    asset_id uuid,
    title text NOT NULL,
    description text,
    source_specification_id uuid,
    source_requirement_id uuid,
    business_status text DEFAULT 'DRAFT'::text NOT NULL,
    intent text,
    context jsonb DEFAULT '{}'::jsonb NOT NULL,
    constraints jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_by text,
    dco_json text,
    legacy_id text,
    plan_id text,
    step_outputs text DEFAULT '{}'::text NOT NULL,
    consumed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT work_request_business_status_check CHECK ((business_status = ANY (ARRAY['DRAFT'::text, 'APPROVED'::text, 'DISPATCHED'::text, 'COMPLETED'::text, 'CANCELLED'::text])))
);


--
-- Name: work_request_edge; Type: TABLE; Schema: resolution; Owner: -
--

CREATE TABLE resolution.work_request_edge (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    parent_work_request_id uuid NOT NULL,
    child_work_request_id uuid NOT NULL,
    edge_type text DEFAULT 'depends_on'::text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT 'infinity'::timestamp with time zone NOT NULL,
    CONSTRAINT work_request_edge_check CHECK ((parent_work_request_id <> child_work_request_id))
);


--
-- Name: TABLE work_request_edge; Type: COMMENT; Schema: resolution; Owner: -
--

COMMENT ON TABLE resolution.work_request_edge IS 'Ported from vision.work_request_edges_history. parent = upstream/prerequisite, child = downstream/dependent, matching vision''s own work_request_dag traversal direction. Only ''depends_on'' is a confirmed edge_type from the DDL alone -- others may exist in production and are not invented here.';


--
-- Name: evidence_item; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.evidence_item (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    evidence_type_id uuid NOT NULL,
    uri text,
    excerpt text,
    note text,
    origin text,
    captured_at timestamp with time zone,
    source_hash text,
    metadata jsonb,
    valid_from timestamp with time zone,
    valid_to timestamp with time zone,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    verification_state text DEFAULT 'candidate'::text NOT NULL,
    source_observation_id uuid,
    CONSTRAINT evidence_item_verification_state_check CHECK ((verification_state = ANY (ARRAY['candidate'::text, 'confirmed'::text, 'contested'::text, 'superseded'::text])))
);


--
-- Name: evidence_type; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.evidence_type (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text NOT NULL,
    origin_category text,
    notes text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone
);


--
-- Name: statement_evidence; Type: TABLE; Schema: semantics; Owner: -
--

CREATE TABLE semantics.statement_evidence (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    evidence_item_id uuid NOT NULL,
    statement_type text NOT NULL,
    statement_id uuid NOT NULL,
    role text NOT NULL,
    strength numeric,
    comment text,
    effective_at timestamp with time zone DEFAULT now() NOT NULL,
    expired_at timestamp with time zone,
    CONSTRAINT statement_evidence_strength_check CHECK (((strength IS NULL) OR ((strength >= (0)::numeric) AND (strength <= (1)::numeric)))),
    CONSTRAINT statement_evidence_type_check CHECK (((statement_type = ANY (ARRAY['source_observation'::text, 'representation_relationship'::text, 'concept_relationship'::text, 'execution_claim'::text, 'resolution_proposition'::text])) OR (expired_at IS NOT NULL)))
);


--
-- Name: agent_scheduler; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.agent_scheduler (
    id integer NOT NULL,
    role text NOT NULL,
    model_id text DEFAULT 'ollama/qwen2.5-coder'::text,
    harness text DEFAULT 'opencode'::text NOT NULL,
    agent_config text,
    schedule_type text DEFAULT 'interval'::text NOT NULL,
    schedule_value integer DEFAULT 300 NOT NULL,
    enabled integer DEFAULT 1 NOT NULL,
    project_dir text DEFAULT '/home/codex/dev'::text NOT NULL,
    last_run_at timestamp with time zone,
    last_run_status text,
    metadata text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    task_slug text,
    cron_expr text,
    event_criteria jsonb,
    CONSTRAINT agent_scheduler_schedule_type_check CHECK ((schedule_type = ANY (ARRAY['interval'::text, 'cron'::text, 'manual'::text, 'event'::text])))
);

ALTER TABLE ONLY tackle.agent_scheduler REPLICA IDENTITY FULL;


--
-- Name: agent_scheduler_id_seq; Type: SEQUENCE; Schema: tackle; Owner: -
--

CREATE SEQUENCE tackle.agent_scheduler_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: agent_scheduler_id_seq; Type: SEQUENCE OWNED BY; Schema: tackle; Owner: -
--

ALTER SEQUENCE tackle.agent_scheduler_id_seq OWNED BY tackle.agent_scheduler.id;


--
-- Name: agent_timeclock; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.agent_timeclock (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    role text NOT NULL,
    model text NOT NULL,
    session_id text,
    clock_in timestamp with time zone DEFAULT now() NOT NULL,
    clock_out timestamp with time zone,
    status text DEFAULT 'active'::text NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    valid_from timestamp with time zone DEFAULT now() NOT NULL,
    valid_until timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);


--
-- Name: circuit_breaker; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.circuit_breaker (
    id integer NOT NULL,
    tripped integer DEFAULT 0 NOT NULL,
    tripped_at timestamp with time zone,
    error text,
    detail text,
    source text,
    retry_after integer DEFAULT 1800,
    paused integer DEFAULT 0 NOT NULL,
    wake_requested_at timestamp with time zone,
    max_retries_per_model integer DEFAULT 3 NOT NULL,
    retry_delay_seconds integer DEFAULT 120 NOT NULL,
    max_fallbacks integer DEFAULT 3 NOT NULL,
    push_back_to_pending integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone
);

ALTER TABLE ONLY tackle.circuit_breaker REPLICA IDENTITY FULL;


--
-- Name: config_bundle; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.config_bundle (
    id text NOT NULL,
    name text NOT NULL,
    role text NOT NULL,
    model_id text NOT NULL,
    provider_id text,
    harness_id text,
    priority integer DEFAULT 0 NOT NULL,
    invocation_mode text DEFAULT 'CLI'::text NOT NULL,
    command text,
    endpoint_url text,
    timeout_ms integer,
    valid_from timestamp with time zone,
    valid_to timestamp with time zone,
    is_active integer DEFAULT 1 NOT NULL,
    metadata text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT config_bundle_invocation_mode_check CHECK ((invocation_mode = ANY (ARRAY['CLI'::text, 'HTTP'::text, 'SDK'::text, 'MCP'::text, 'INTERACTIVE'::text])))
);

ALTER TABLE ONLY tackle.config_bundle REPLICA IDENTITY FULL;


--
-- Name: harnesses; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.harnesses (
    id text NOT NULL,
    name text NOT NULL,
    invocation_semantics text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY tackle.harnesses REPLICA IDENTITY FULL;


--
-- Name: memory; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.memory (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    slug text NOT NULL,
    title text NOT NULL,
    summary text DEFAULT ''::text NOT NULL,
    body_md text DEFAULT ''::text NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    triggers text[] DEFAULT '{}'::text[] NOT NULL,
    mcp_tools text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY tackle.memory REPLICA IDENTITY FULL;


--
-- Name: models; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.models (
    id text NOT NULL,
    name text NOT NULL,
    harness_id text NOT NULL,
    provider_id text,
    model_identifier text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    verified boolean DEFAULT false
);

ALTER TABLE ONLY tackle.models REPLICA IDENTITY FULL;


--
-- Name: projection_configs; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.projection_configs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    type text DEFAULT 'deterministic'::text NOT NULL,
    source_query text DEFAULT ''::text NOT NULL,
    template text DEFAULT ''::text NOT NULL,
    parameter_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    target_path text NOT NULL,
    schedule text DEFAULT ''::text NOT NULL,
    enabled integer DEFAULT 1 NOT NULL,
    last_rendered_at timestamp with time zone,
    last_sha256 text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT projection_configs_type_check CHECK ((type = ANY (ARRAY['deterministic'::text, 'inference'::text])))
);


--
-- Name: prompts; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.prompts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    role text NOT NULL,
    slug text NOT NULL,
    version integer DEFAULT 1 NOT NULL,
    title text NOT NULL,
    body_md text DEFAULT ''::text NOT NULL,
    parameter_schema jsonb DEFAULT '{}'::jsonb NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_prompts_version_positive CHECK ((version >= 1))
);


--
-- Name: providers; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.providers (
    id text NOT NULL,
    name text NOT NULL,
    type text NOT NULL,
    endpoint_url text,
    api_key text,
    config_json text DEFAULT '{}'::text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    CONSTRAINT providers_type_check CHECK ((type = ANY (ARRAY['openai'::text, 'anthropic'::text, 'google'::text, 'ollama'::text, 'opencode'::text, 'codex'::text, 'spring_ai'::text, 'lm_server'::text, 'custom'::text])))
);

ALTER TABLE ONLY tackle.providers REPLICA IDENTITY FULL;


--
-- Name: role_leases; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.role_leases (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    role text NOT NULL,
    channel text DEFAULT 'interactive'::text NOT NULL,
    model text,
    window_start timestamp with time zone DEFAULT now() NOT NULL,
    window_end timestamp with time zone NOT NULL,
    budget_units integer,
    consumed_units integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'ACTIVE'::text NOT NULL,
    acquired_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    released_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    release_reason text,
    CONSTRAINT role_leases_channel_check CHECK ((channel = ANY (ARRAY['interactive'::text, 'opencode'::text, 'ollama'::text, 'unknown'::text]))),
    CONSTRAINT role_leases_release_reason_check CHECK ((release_reason = ANY (ARRAY['revoked'::text, 'exhausted'::text, 'expired'::text]))),
    CONSTRAINT role_leases_status_check CHECK ((status = ANY (ARRAY['ACTIVE'::text, 'EXPIRED'::text, 'RELEASED'::text])))
);


--
-- Name: role_memory; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.role_memory (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    memory_id uuid NOT NULL,
    role text NOT NULL,
    as_of_dt timestamp with time zone DEFAULT now() NOT NULL,
    expiration_dt timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY tackle.role_memory REPLICA IDENTITY FULL;


--
-- Name: role_tool_access; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.role_tool_access (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    role text NOT NULL,
    mcp_id text NOT NULL,
    tool_slug text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: roles; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.roles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);

ALTER TABLE ONLY tackle.roles REPLICA IDENTITY FULL;


--
-- Name: schema_version; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.schema_version (
    version integer NOT NULL,
    description text NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: session_logs; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.session_logs (
    id bigint NOT NULL,
    session_id text NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    level text DEFAULT 'INFO'::text NOT NULL,
    line text NOT NULL
);

ALTER TABLE ONLY tackle.session_logs REPLICA IDENTITY FULL;


--
-- Name: session_logs_id_seq; Type: SEQUENCE; Schema: tackle; Owner: -
--

CREATE SEQUENCE tackle.session_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: session_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: tackle; Owner: -
--

ALTER SEQUENCE tackle.session_logs_id_seq OWNED BY tackle.session_logs.id;


--
-- Name: sessions; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.sessions (
    id text NOT NULL,
    agent_role text DEFAULT 'test'::text NOT NULL,
    start_iso timestamp with time zone NOT NULL,
    end_iso timestamp with time zone,
    exit_code integer,
    pid integer,
    is_running integer DEFAULT 1 NOT NULL,
    error_info text,
    model text,
    plans_processed text DEFAULT '[]'::text NOT NULL,
    plan_count integer DEFAULT 0 NOT NULL,
    cost_usd real DEFAULT 0,
    workflow_id text,
    run_id text,
    created_at timestamp with time zone NOT NULL
);

ALTER TABLE ONLY tackle.sessions REPLICA IDENTITY FULL;


--
-- Name: system_logs; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.system_logs (
    id text DEFAULT (gen_random_uuid())::text NOT NULL,
    "timestamp" timestamp with time zone DEFAULT now() NOT NULL,
    level text NOT NULL,
    category text NOT NULL,
    message text NOT NULL,
    source text,
    details jsonb,
    CONSTRAINT system_logs_level_check CHECK ((level = ANY (ARRAY['INFO'::text, 'WARN'::text, 'ERROR'::text, 'DEBUG'::text])))
);


--
-- Name: tasks; Type: TABLE; Schema: tackle; Owner: -
--

CREATE TABLE tackle.tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    role text NOT NULL,
    task_slug text NOT NULL,
    scope text DEFAULT ''::text NOT NULL,
    acceptance_criteria text[] DEFAULT '{}'::text[] NOT NULL,
    prompt_id uuid NOT NULL,
    active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: broker_profiles; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.broker_profiles (
    id bigint NOT NULL,
    auto_connect boolean,
    broker_url character varying(255),
    health_check_delay_minutes integer,
    image_url character varying(255),
    name character varying(255) NOT NULL,
    profile_id character varying(255) NOT NULL
);

ALTER TABLE ONLY terrain.broker_profiles REPLICA IDENTITY FULL;


--
-- Name: broker_profiles_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

ALTER TABLE terrain.broker_profiles ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME terrain.broker_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: cli_tools; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.cli_tools (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    tool_path character varying(255),
    description character varying(1000),
    invocation character varying(255),
    language character varying(255),
    category character varying(255),
    startup character varying(255),
    health character varying(255),
    sysuser character varying(255),
    syspass character varying(255),
    notes character varying(1000),
    is_internal boolean DEFAULT true NOT NULL,
    active_flag boolean DEFAULT true NOT NULL,
    startup_script character varying(255),
    build_command character varying(255),
    asset_id uuid
);

ALTER TABLE ONLY terrain.cli_tools REPLICA IDENTITY FULL;


--
-- Name: cli_tools_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

CREATE SEQUENCE terrain.cli_tools_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: cli_tools_id_seq; Type: SEQUENCE OWNED BY; Schema: terrain; Owner: -
--

ALTER SEQUENCE terrain.cli_tools_id_seq OWNED BY terrain.cli_tools.id;


--
-- Name: mcp_servers; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.mcp_servers (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    port integer,
    workspace_path character varying(255),
    service_type_id bigint,
    health_check_url character varying(255),
    status character varying(255),
    transport_type character varying(255),
    version character varying(255),
    description character varying(1000),
    repository_url character varying(255),
    active_flag boolean DEFAULT true NOT NULL,
    startup character varying(255),
    health character varying(255),
    syspass character varying(255),
    notes character varying(1000),
    is_internal boolean DEFAULT true NOT NULL,
    sysuser character varying(255),
    startup_script character varying(255),
    build_command character varying(255),
    asset_id uuid
);

ALTER TABLE ONLY terrain.mcp_servers REPLICA IDENTITY FULL;


--
-- Name: mcp_servers_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

CREATE SEQUENCE terrain.mcp_servers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: mcp_servers_id_seq; Type: SEQUENCE OWNED BY; Schema: terrain; Owner: -
--

ALTER SEQUENCE terrain.mcp_servers_id_seq OWNED BY terrain.mcp_servers.id;


--
-- Name: registry_server_profiles; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.registry_server_profiles (
    id bigint NOT NULL,
    description text,
    image_url character varying(255),
    is_active boolean,
    name character varying(255) NOT NULL,
    profile_id character varying(255) NOT NULL,
    registry_server_url character varying(255)
);

ALTER TABLE ONLY terrain.registry_server_profiles REPLICA IDENTITY FULL;


--
-- Name: registry_server_profiles_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

ALTER TABLE terrain.registry_server_profiles ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME terrain.registry_server_profiles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: runnable_services_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

CREATE SEQUENCE terrain.runnable_services_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: runnable_services_id_seq; Type: SEQUENCE OWNED BY; Schema: terrain; Owner: -
--

ALTER SEQUENCE terrain.runnable_services_id_seq OWNED BY terrain.runnable_services.id;


--
-- Name: servers; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.servers (
    id bigint NOT NULL,
    hostname character varying(255) NOT NULL,
    ip_address character varying(255),
    os character varying(255),
    status character varying(255),
    active_flag boolean DEFAULT true NOT NULL,
    startup character varying(255),
    health character varying(255),
    syspass character varying(255),
    notes character varying(1000),
    is_internal boolean DEFAULT true NOT NULL,
    sysuser character varying(255),
    startup_script character varying(255),
    build_command character varying(255)
);

ALTER TABLE ONLY terrain.servers REPLICA IDENTITY FULL;


--
-- Name: servers_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

CREATE SEQUENCE terrain.servers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: servers_id_seq; Type: SEQUENCE OWNED BY; Schema: terrain; Owner: -
--

ALTER SEQUENCE terrain.servers_id_seq OWNED BY terrain.servers.id;


--
-- Name: service_dependencies; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.service_dependencies (
    id bigint NOT NULL,
    source_type character varying(255) NOT NULL,
    source_id bigint NOT NULL,
    target_type character varying(255) NOT NULL,
    target_id bigint NOT NULL,
    criticality character varying(255),
    description character varying(1000)
);

ALTER TABLE ONLY terrain.service_dependencies REPLICA IDENTITY FULL;


--
-- Name: service_dependencies_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

CREATE SEQUENCE terrain.service_dependencies_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: service_dependencies_id_seq; Type: SEQUENCE OWNED BY; Schema: terrain; Owner: -
--

ALTER SEQUENCE terrain.service_dependencies_id_seq OWNED BY terrain.service_dependencies.id;


--
-- Name: service_endpoints; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.service_endpoints (
    id uuid NOT NULL,
    host character varying(255) NOT NULL,
    instance character varying(255) NOT NULL,
    ip inet NOT NULL,
    last_heartbeat timestamp(6) with time zone,
    port integer NOT NULL,
    scheme character varying(255) NOT NULL,
    status character varying(255) NOT NULL,
    unit character varying(255) NOT NULL
);


--
-- Name: service_types; Type: TABLE; Schema: terrain; Owner: -
--

CREATE TABLE terrain.service_types (
    id bigint NOT NULL,
    name character varying(255) NOT NULL
);

ALTER TABLE ONLY terrain.service_types REPLICA IDENTITY FULL;


--
-- Name: service_types_id_seq; Type: SEQUENCE; Schema: terrain; Owner: -
--

CREATE SEQUENCE terrain.service_types_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: service_types_id_seq; Type: SEQUENCE OWNED BY; Schema: terrain; Owner: -
--

ALTER SEQUENCE terrain.service_types_id_seq OWNED BY terrain.service_types.id;


--
-- Name: artifacts_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.artifacts_history (
    id integer NOT NULL,
    artifact_id character varying(36) NOT NULL,
    type character varying(32) NOT NULL,
    content jsonb NOT NULL,
    confidence double precision,
    provenance jsonb,
    wr_id character varying(36),
    parent_artifact_id character varying(36),
    template_metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY vision.artifacts_history REPLICA IDENTITY FULL;


--
-- Name: artifacts; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.artifacts AS
 SELECT id,
    artifact_id,
    type,
    content,
    confidence,
    provenance,
    wr_id,
    parent_artifact_id,
    template_metadata,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.artifacts_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: artifacts_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.artifacts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: branch_artifacts_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.branch_artifacts_history (
    id integer NOT NULL,
    artifact_id character varying(36) NOT NULL,
    branch_id character varying(36) NOT NULL,
    wr_id character varying(36) NOT NULL,
    artifact_type character varying(32) NOT NULL,
    content text NOT NULL,
    parent_artifact_id character varying(36),
    score double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY vision.branch_artifacts_history REPLICA IDENTITY FULL;


--
-- Name: branch_artifacts; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.branch_artifacts AS
 SELECT id,
    artifact_id,
    branch_id,
    wr_id,
    artifact_type,
    content,
    parent_artifact_id,
    score,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.branch_artifacts_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: branch_artifacts_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.branch_artifacts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: branches_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.branches_history (
    id integer NOT NULL,
    branch_id character varying(36) NOT NULL,
    wr_id character varying(36) NOT NULL,
    parent_branch_id character varying(36),
    fork_point character varying(36),
    label character varying(64),
    score double precision,
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY vision.branches_history REPLICA IDENTITY FULL;


--
-- Name: branches; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.branches AS
 SELECT id,
    branch_id,
    wr_id,
    parent_branch_id,
    fork_point,
    label,
    score,
    status,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.branches_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: branches_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.branches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: governance_events_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.governance_events_history (
    id integer NOT NULL,
    event_id character varying(36) NOT NULL,
    event_type character varying(64) NOT NULL,
    work_request_id character varying(64),
    lineage_parent character varying(128),
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY vision.governance_events_history REPLICA IDENTITY FULL;


--
-- Name: governance_events; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.governance_events AS
 SELECT id,
    event_id,
    event_type,
    work_request_id,
    lineage_parent,
    payload,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.governance_events_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: governance_events_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.governance_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: lifecycle_events_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.lifecycle_events_history (
    id integer NOT NULL,
    event_id character varying(36) NOT NULL,
    wr_id character varying(36) NOT NULL,
    from_state character varying(32),
    to_state character varying(32) NOT NULL,
    actor character varying(128) NOT NULL,
    reason character varying(256),
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY vision.lifecycle_events_history REPLICA IDENTITY FULL;


--
-- Name: lifecycle_events; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.lifecycle_events AS
 SELECT id,
    event_id,
    wr_id,
    from_state,
    to_state,
    actor,
    reason,
    metadata,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.lifecycle_events_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: lifecycle_events_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.lifecycle_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: receipt_ingest_records_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.receipt_ingest_records_history (
    id integer NOT NULL,
    receipt_id character varying(36) NOT NULL,
    work_request_id character varying(64) NOT NULL,
    executor_id character varying(128) NOT NULL,
    receipt_hash character varying(64) NOT NULL,
    result character varying(16) NOT NULL,
    lineage_parent character varying(128) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);

ALTER TABLE ONLY vision.receipt_ingest_records_history REPLICA IDENTITY FULL;


--
-- Name: receipt_ingest_records; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.receipt_ingest_records AS
 SELECT id,
    receipt_id,
    work_request_id,
    executor_id,
    receipt_hash,
    result,
    lineage_parent,
    payload,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.receipt_ingest_records_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: receipt_ingest_records_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.receipt_ingest_records_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: tickets; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.tickets (
    id text NOT NULL,
    plan_id text NOT NULL,
    role text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    session_id text,
    created_by_receipt text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone NOT NULL,
    claimed_at timestamp with time zone,
    closed_at timestamp with time zone,
    token_budget integer,
    tokens_used integer,
    objective text,
    completion_criteria text,
    owner text DEFAULT ''::text NOT NULL,
    parent_ticket_id text,
    spawn_reason text,
    last_activity text,
    expires_at timestamp with time zone,
    deadline timestamp with time zone,
    confidence real,
    closure_reason text,
    replacement_of text,
    recorded_on_dt timestamp with time zone DEFAULT now(),
    recorded_until_dt timestamp with time zone
);

ALTER TABLE ONLY vision.tickets REPLICA IDENTITY FULL;


--
-- Name: vision_ir_artifacts; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.vision_ir_artifacts (
    artifact_id uuid DEFAULT gen_random_uuid() NOT NULL,
    work_request_id uuid NOT NULL,
    event_id uuid NOT NULL,
    ir_stage text NOT NULL,
    ir_version integer DEFAULT 1 NOT NULL,
    artifact_type text NOT NULL,
    content jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vision_ir_artifacts_ir_stage_check CHECK ((ir_stage = ANY (ARRAY['PLAN_IR'::text, 'SPEC_IR'::text, 'EXECUTION_IR'::text, 'VALIDATION_IR'::text])))
);

ALTER TABLE ONLY vision.vision_ir_artifacts REPLICA IDENTITY FULL;


--
-- Name: work_request_edges_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.work_request_edges_history (
    id integer NOT NULL,
    edge_id character varying(36) NOT NULL,
    parent_wr_id character varying(36) NOT NULL,
    child_wr_id character varying(36) NOT NULL,
    edge_type character varying(32) DEFAULT 'depends_on'::character varying NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL
);


--
-- Name: work_request_edges; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.work_request_edges AS
 SELECT id,
    edge_id,
    parent_wr_id,
    child_wr_id,
    edge_type,
    metadata,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.work_request_edges_history
  WHERE ((now() >= recorded_on_dt) AND (now() < recorded_until_dt));


--
-- Name: work_requests_history; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.work_requests_history (
    id integer NOT NULL,
    wr_id character varying(36) NOT NULL,
    intent text NOT NULL,
    constraints jsonb,
    priority integer DEFAULT 5 NOT NULL,
    context jsonb,
    status character varying(32) DEFAULT 'NEW'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    recorded_on_dt timestamp with time zone DEFAULT now() NOT NULL,
    recorded_until_dt timestamp with time zone DEFAULT '9999-12-31 23:59:59+00'::timestamp with time zone NOT NULL,
    parent_request_id character varying(36)
);

ALTER TABLE ONLY vision.work_requests_history REPLICA IDENTITY FULL;


--
-- Name: work_request_dag; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.work_request_dag AS
 WITH RECURSIVE dag_tree AS (
         SELECT wr.wr_id AS node_wr_id,
            wr.wr_id AS root_wr_id,
            (wr.wr_id)::text AS path,
            0 AS depth,
            false AS is_cycle,
            wr.intent,
            wr.status,
            wr.priority,
            wr.parent_request_id,
            NULL::character varying(36) AS parent_wr_id,
            NULL::character varying(32) AS edge_type,
            wr.created_at
           FROM vision.work_requests_history wr
          WHERE ((wr.parent_request_id IS NULL) AND (wr.recorded_until_dt = '9999-12-31 23:59:59+00'::timestamp with time zone))
        UNION ALL
         SELECT child.wr_id AS node_wr_id,
            dt.root_wr_id,
            ((dt.path || '→'::text) || (child.wr_id)::text) AS path,
            (dt.depth + 1) AS depth,
            ((child.wr_id)::text = ANY (string_to_array(dt.path, '→'::text))) AS is_cycle,
            child.intent,
            child.status,
            child.priority,
            child.parent_request_id,
            e.parent_wr_id,
            e.edge_type,
            child.created_at
           FROM ((dag_tree dt
             JOIN vision.work_request_edges e ON (((e.parent_wr_id)::text = (dt.node_wr_id)::text)))
             JOIN vision.work_requests_history child ON (((child.wr_id)::text = (e.child_wr_id)::text)))
          WHERE ((dt.depth < 50) AND (child.recorded_until_dt = '9999-12-31 23:59:59+00'::timestamp with time zone))
        )
 SELECT node_wr_id,
    root_wr_id,
    path,
    depth,
    is_cycle,
    intent,
    status,
    priority,
    parent_wr_id,
    edge_type,
    created_at
   FROM dag_tree
  WHERE (NOT is_cycle);


--
-- Name: work_request_edges_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.work_request_edges_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: work_requests_id_seq; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.work_requests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: work_requests_id_seq1; Type: SEQUENCE; Schema: vision; Owner: -
--

CREATE SEQUENCE vision.work_requests_id_seq1
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: work_requests_id_seq1; Type: SEQUENCE OWNED BY; Schema: vision; Owner: -
--

ALTER SEQUENCE vision.work_requests_id_seq1 OWNED BY vision.work_requests.id;


--
-- Name: work_requests_losm; Type: VIEW; Schema: vision; Owner: -
--

CREATE VIEW vision.work_requests_losm AS
 SELECT id,
    wr_id,
    parent_request_id,
    intent,
    constraints,
    priority,
    context,
        CASE
            WHEN ((status)::text = ANY ((ARRAY['NEW'::character varying, 'INTAKE'::character varying, 'PLAN_GENERATION'::character varying, 'PLAN_REVIEW'::character varying, 'PLAN_APPROVAL_GATE'::character varying, 'SPEC_GENERATION'::character varying, 'EXECUTION'::character varying, 'VALIDATION'::character varying, 'COMPLETION'::character varying, 'BLOCKED'::character varying, 'FAILED'::character varying])::text[])) THEN status
            WHEN ((status)::text = 'pending'::text) THEN 'NEW'::character varying
            WHEN ((status)::text = 'completed'::text) THEN 'COMPLETION'::character varying
            WHEN ((status)::text = 'cancelled'::text) THEN 'FAILED'::character varying
            ELSE 'NEW'::character varying
        END AS status,
    created_at,
    recorded_on_dt,
    recorded_until_dt
   FROM vision.work_requests_history
  WHERE (recorded_until_dt = '9999-12-31 23:59:59+00'::timestamp with time zone);


--
-- Name: wr_compile_verdicts; Type: TABLE; Schema: vision; Owner: -
--

CREATE TABLE vision.wr_compile_verdicts (
    verdict_id text NOT NULL,
    entity_key text NOT NULL,
    wr_id text,
    plan_id text,
    verdict_type text NOT NULL,
    rule_version text NOT NULL,
    description text NOT NULL,
    detected_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    route text,
    CONSTRAINT wr_compile_verdicts_verdict_type_check CHECK ((verdict_type = ANY (ARRAY['WR_COMPILE_PASS'::text, 'WR_COMPILE_FAIL'::text])))
);


--
-- Name: COLUMN wr_compile_verdicts.route; Type: COMMENT; Schema: vision; Owner: -
--

COMMENT ON COLUMN vision.wr_compile_verdicts.route IS 'classification.route (conduit | conduit-review | reserved); the D5 bootstrap gate blocks PASS verdicts when reserved';


--
-- Name: event_types; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.event_types (
    event_type text NOT NULL,
    description text,
    schema jsonb,
    workflow_id uuid,
    dedup_key_template text,
    enabled boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: TABLE event_types; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON TABLE wind.event_types IS 'Registry of event types and their associated workflows';


--
-- Name: COLUMN event_types.workflow_id; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON COLUMN wind.event_types.workflow_id IS 'Workflow to trigger when this event occurs';


--
-- Name: COLUMN event_types.dedup_key_template; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON COLUMN wind.event_types.dedup_key_template IS 'JSON path expression to extract dedup key from payload, e.g. $.harvest_id';


--
-- Name: events; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.events (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    event_type text NOT NULL,
    subject text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    source text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    consumed_at timestamp with time zone,
    metadata jsonb DEFAULT '{}'::jsonb
);


--
-- Name: TABLE events; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON TABLE wind.events IS 'Event log — all events that can trigger workflows';


--
-- Name: COLUMN events.subject; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON COLUMN wind.events.subject IS 'Routing key for subject-based queries, e.g. harvest.4859a5c2';


--
-- Name: COLUMN events.consumed_at; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON COLUMN wind.events.consumed_at IS 'NULL = unconsumed; set when Wind processes the event';


--
-- Name: offices; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.offices (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: receipts; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.receipts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    ticket_id uuid NOT NULL,
    ticket_task_id uuid NOT NULL,
    outcome_id uuid NOT NULL,
    work_request_id uuid NOT NULL,
    output_artifact_type character varying(100),
    output_artifact_id uuid,
    completed_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: task_outcomes; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.task_outcomes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    task_id uuid NOT NULL,
    code character varying(50) NOT NULL,
    description text,
    output_spec jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: tasks; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.tasks (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    office_id uuid NOT NULL,
    title_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    input_spec jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    tackle_task_id uuid
);


--
-- Name: COLUMN tasks.tackle_task_id; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON COLUMN wind.tasks.tackle_task_id IS 'Links to tackle.tasks for agent role/prompt/scope resolution. NULL = workflow-only task (no agent execution).';


--
-- Name: tickets; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.tickets (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_instance_id uuid NOT NULL,
    workflow_version_id uuid NOT NULL,
    node_id uuid NOT NULL,
    node_task_id uuid NOT NULL,
    assigned_title_id uuid NOT NULL,
    status character varying(30) DEFAULT 'PENDING'::character varying NOT NULL,
    input_artifact_type character varying(100) NOT NULL,
    input_artifact_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    CONSTRAINT chk_ticket_status CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'IN_PROGRESS'::character varying, 'COMPLETED'::character varying, 'CANCELLED'::character varying])::text[])))
);


--
-- Name: titles; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.titles (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    office_id uuid NOT NULL,
    role_id uuid NOT NULL,
    display_name character varying(100) NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: v_roles; Type: VIEW; Schema: wind; Owner: -
--

CREATE VIEW wind.v_roles AS
 SELECT id,
    name,
    display_name,
    description,
    owns_domains,
    can_greenlight,
    can_create_questions,
    can_create_agendas,
    can_resolve_questions,
    can_verify_work_requests,
    max_open_questions,
    requires_approval_from,
    cron_enabled,
    cron_expression,
    cron_description,
    escalates_to,
    escalation_triggers,
    level_filter_primary,
    level_filter_allowed,
    visibility_scope,
    created_at,
    updated_at
   FROM nebula.roles_history;


--
-- Name: workflow_edges; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.workflow_edges (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_version_id uuid NOT NULL,
    from_node_id uuid NOT NULL,
    from_task_id uuid NOT NULL,
    outcome_id uuid NOT NULL,
    to_node_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: workflow_nodes; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.workflow_nodes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_version_id uuid NOT NULL,
    task_id uuid NOT NULL,
    name character varying(100) NOT NULL,
    is_entrypoint boolean DEFAULT false NOT NULL,
    is_terminal boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: v_workflow_graph_validation; Type: VIEW; Schema: wind; Owner: -
--

CREATE VIEW wind.v_workflow_graph_validation AS
 WITH node_outcomes AS (
         SELECT wn.workflow_version_id,
            wn.id AS node_id,
            wn.name AS node_name,
            t.id AS task_id,
            t.name AS task_name,
            o.id AS outcome_id,
            o.code AS outcome_code,
            o.output_spec
           FROM ((wind.workflow_nodes wn
             JOIN wind.tasks t ON ((wn.task_id = t.id)))
             JOIN wind.task_outcomes o ON ((o.task_id = t.id)))
          WHERE (wn.is_terminal = false)
        ), edge_analysis AS (
         SELECT no.workflow_version_id,
            no.node_id,
            no.node_name,
            no.outcome_code,
            we.id AS edge_id,
            we.to_node_id,
            downstream_task.input_spec AS downstream_input_spec,
            no.output_spec AS upstream_output_spec
           FROM (((node_outcomes no
             LEFT JOIN wind.workflow_edges we ON (((no.workflow_version_id = we.workflow_version_id) AND (no.node_id = we.from_node_id) AND (no.outcome_id = we.outcome_id))))
             LEFT JOIN wind.workflow_nodes downstream_node ON ((we.to_node_id = downstream_node.id)))
             LEFT JOIN wind.tasks downstream_task ON ((downstream_node.task_id = downstream_task.id)))
        )
 SELECT edge_analysis.workflow_version_id,
    'UNHANDLED_OUTCOME'::text AS issue_type,
    edge_analysis.node_id,
    format('Node "%s" leaves outcome "%s" unhandled. No edge defined.'::text, edge_analysis.node_name, edge_analysis.outcome_code) AS details
   FROM edge_analysis
  WHERE (edge_analysis.edge_id IS NULL)
UNION ALL
 SELECT wn.workflow_version_id,
    'UNREACHABLE_NODE'::text AS issue_type,
    wn.id AS node_id,
    format('Node "%s" is neither an entrypoint nor reached by any edge.'::text, wn.name) AS details
   FROM wind.workflow_nodes wn
  WHERE ((wn.is_entrypoint = false) AND (NOT (wn.id IN ( SELECT workflow_edges.to_node_id
           FROM wind.workflow_edges))))
UNION ALL
 SELECT edge_analysis.workflow_version_id,
    'DATA_CONTRACT_MISMATCH'::text AS issue_type,
    edge_analysis.node_id,
    format('Node "%s" outcome "%s" output_spec does not satisfy downstream task input_spec.'::text, edge_analysis.node_name, edge_analysis.outcome_code) AS details
   FROM edge_analysis
  WHERE ((edge_analysis.edge_id IS NOT NULL) AND (NOT (edge_analysis.upstream_output_spec @> edge_analysis.downstream_input_spec)));


--
-- Name: workflow_instances; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.workflow_instances (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_version_id uuid NOT NULL,
    status character varying(30) DEFAULT 'ACTIVE'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    updated_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    dedup_key text,
    event_id uuid,
    CONSTRAINT chk_instance_status CHECK (((status)::text = ANY ((ARRAY['ACTIVE'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying, 'PAUSED'::character varying])::text[])))
);


--
-- Name: COLUMN workflow_instances.dedup_key; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON COLUMN wind.workflow_instances.dedup_key IS 'Deduplication key — prevents duplicate instances for the same event';


--
-- Name: COLUMN workflow_instances.event_id; Type: COMMENT; Schema: wind; Owner: -
--

COMMENT ON COLUMN wind.workflow_instances.event_id IS 'The event that triggered this instance';


--
-- Name: workflow_versions; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.workflow_versions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    workflow_id uuid NOT NULL,
    version_number integer NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: workflows; Type: TABLE; Schema: wind; Owner: -
--

CREATE TABLE wind.workflows (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL
);


--
-- Name: lineage_edges id; Type: DEFAULT; Schema: cascade; Owner: -
--

ALTER TABLE ONLY cascade.lineage_edges ALTER COLUMN id SET DEFAULT nextval('cascade.lineage_edges_id_seq'::regclass);


--
-- Name: cost_logs id; Type: DEFAULT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.cost_logs ALTER COLUMN id SET DEFAULT nextval('conduit.cost_logs_id_seq'::regclass);


--
-- Name: lineage_log id; Type: DEFAULT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.lineage_log ALTER COLUMN id SET DEFAULT nextval('conduit.lineage_log_id_seq'::regclass);


--
-- Name: work_request_events sequence_number; Type: DEFAULT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.work_request_events ALTER COLUMN sequence_number SET DEFAULT nextval('conduit.work_request_events_sequence_number_seq'::regclass);


--
-- Name: governance_events id; Type: DEFAULT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.governance_events ALTER COLUMN id SET DEFAULT nextval('peb.governance_events_id_seq'::regclass);


--
-- Name: agent_scheduler id; Type: DEFAULT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.agent_scheduler ALTER COLUMN id SET DEFAULT nextval('tackle.agent_scheduler_id_seq'::regclass);


--
-- Name: session_logs id; Type: DEFAULT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.session_logs ALTER COLUMN id SET DEFAULT nextval('tackle.session_logs_id_seq'::regclass);


--
-- Name: cli_tools id; Type: DEFAULT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.cli_tools ALTER COLUMN id SET DEFAULT nextval('terrain.cli_tools_id_seq'::regclass);


--
-- Name: mcp_servers id; Type: DEFAULT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.mcp_servers ALTER COLUMN id SET DEFAULT nextval('terrain.mcp_servers_id_seq'::regclass);


--
-- Name: runnable_services id; Type: DEFAULT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.runnable_services ALTER COLUMN id SET DEFAULT nextval('terrain.runnable_services_id_seq'::regclass);


--
-- Name: servers id; Type: DEFAULT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.servers ALTER COLUMN id SET DEFAULT nextval('terrain.servers_id_seq'::regclass);


--
-- Name: service_dependencies id; Type: DEFAULT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.service_dependencies ALTER COLUMN id SET DEFAULT nextval('terrain.service_dependencies_id_seq'::regclass);


--
-- Name: service_types id; Type: DEFAULT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.service_types ALTER COLUMN id SET DEFAULT nextval('terrain.service_types_id_seq'::regclass);


--
-- Name: work_requests id; Type: DEFAULT; Schema: vision; Owner: -
--

ALTER TABLE ONLY vision.work_requests ALTER COLUMN id SET DEFAULT nextval('vision.work_requests_id_seq1'::regclass);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: cascade; Owner: -
--

ALTER TABLE ONLY cascade.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (event_id);


--
-- Name: lineage_edges lineage_edges_pkey; Type: CONSTRAINT; Schema: cascade; Owner: -
--

ALTER TABLE ONLY cascade.lineage_edges
    ADD CONSTRAINT lineage_edges_pkey PRIMARY KEY (id);


--
-- Name: processing_offsets processing_offsets_pkey; Type: CONSTRAINT; Schema: cascade; Owner: -
--

ALTER TABLE ONLY cascade.processing_offsets
    ADD CONSTRAINT processing_offsets_pkey PRIMARY KEY (subscriber_id);


--
-- Name: subscriptions subscriptions_pkey; Type: CONSTRAINT; Schema: cascade; Owner: -
--

ALTER TABLE ONLY cascade.subscriptions
    ADD CONSTRAINT subscriptions_pkey PRIMARY KEY (subject_pattern);


--
-- Name: agent_budgets agent_budgets_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.agent_budgets
    ADD CONSTRAINT agent_budgets_pkey PRIMARY KEY (agent_role);


--
-- Name: bridge_checkpoint bridge_checkpoint_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.bridge_checkpoint
    ADD CONSTRAINT bridge_checkpoint_pkey PRIMARY KEY (id);


--
-- Name: circuit_breaker circuit_breaker_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.circuit_breaker
    ADD CONSTRAINT circuit_breaker_pkey PRIMARY KEY (id);


--
-- Name: cost_logs cost_logs_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.cost_logs
    ADD CONSTRAINT cost_logs_pkey PRIMARY KEY (id);


--
-- Name: kernel_delta_log kernel_delta_log_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.kernel_delta_log
    ADD CONSTRAINT kernel_delta_log_pkey PRIMARY KEY (delta_id);


--
-- Name: kernel_snapshot kernel_snapshot_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.kernel_snapshot
    ADD CONSTRAINT kernel_snapshot_pkey PRIMARY KEY (version);


--
-- Name: lineage_log lineage_log_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.lineage_log
    ADD CONSTRAINT lineage_log_pkey PRIMARY KEY (id);


--
-- Name: model_pricing model_pricing_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.model_pricing
    ADD CONSTRAINT model_pricing_pkey PRIMARY KEY (model_name);


--
-- Name: pipeline_cursor pipeline_cursor_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.pipeline_cursor
    ADD CONSTRAINT pipeline_cursor_pkey PRIMARY KEY (role);


--
-- Name: role_circuit_breaker role_circuit_breaker_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.role_circuit_breaker
    ADD CONSTRAINT role_circuit_breaker_pkey PRIMARY KEY (role);


--
-- Name: schema_version schema_version_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.schema_version
    ADD CONSTRAINT schema_version_pkey PRIMARY KEY (version);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: work_request_events work_request_events_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.work_request_events
    ADD CONSTRAINT work_request_events_pkey PRIMARY KEY (event_id);


--
-- Name: work_request_state work_request_state_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.work_request_state
    ADD CONSTRAINT work_request_state_pkey PRIMARY KEY (work_request_id);


--
-- Name: work_requests work_requests_pkey; Type: CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.work_requests
    ADD CONSTRAINT work_requests_pkey PRIMARY KEY (id);


--
-- Name: attempts attempts_pkey; Type: CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.attempts
    ADD CONSTRAINT attempts_pkey PRIMARY KEY (id);


--
-- Name: leases leases_pkey; Type: CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.leases
    ADD CONSTRAINT leases_pkey PRIMARY KEY (id);


--
-- Name: receipts receipts_pkey; Type: CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.receipts
    ADD CONSTRAINT receipts_pkey PRIMARY KEY (id);


--
-- Name: requests requests_business_key_key; Type: CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.requests
    ADD CONSTRAINT requests_business_key_key UNIQUE (business_key);


--
-- Name: requests requests_pkey; Type: CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.requests
    ADD CONSTRAINT requests_pkey PRIMARY KEY (id);


--
-- Name: agenda_item_questions agenda_item_questions_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.agenda_item_questions
    ADD CONSTRAINT agenda_item_questions_pkey PRIMARY KEY (id);


--
-- Name: agenda_items_history agenda_items_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.agenda_items_history
    ADD CONSTRAINT agenda_items_pkey PRIMARY KEY (id);


--
-- Name: agendas_history agendas_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.agendas_history
    ADD CONSTRAINT agendas_pkey PRIMARY KEY (id);


--
-- Name: agent_records_history agent_records_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.agent_records_history
    ADD CONSTRAINT agent_records_history_pkey PRIMARY KEY (id, recorded_on_dt);


--
-- Name: architect_specs_history architect_specs_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.architect_specs_history
    ADD CONSTRAINT architect_specs_pkey PRIMARY KEY (id);


--
-- Name: artifact_provenance_history artifact_provenance_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.artifact_provenance_history
    ADD CONSTRAINT artifact_provenance_pkey PRIMARY KEY (id);


--
-- Name: assessment_resolutions_history assessment_resolutions_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.assessment_resolutions_history
    ADD CONSTRAINT assessment_resolutions_pkey PRIMARY KEY (resolution_id);


--
-- Name: assessments_history assessments_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.assessments_history
    ADD CONSTRAINT assessments_pkey PRIMARY KEY (id);


--
-- Name: audit_files_history audit_files_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.audit_files_history
    ADD CONSTRAINT audit_files_history_pkey PRIMARY KEY (id, recorded_on_dt);


--
-- Name: candidate_dependencies candidate_dependencies_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.candidate_dependencies
    ADD CONSTRAINT candidate_dependencies_pkey PRIMARY KEY (id);


--
-- Name: candidate_segment_sets candidate_segment_sets_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.candidate_segment_sets
    ADD CONSTRAINT candidate_segment_sets_pkey PRIMARY KEY (id);


--
-- Name: conversation_blocks_history conversation_blocks_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.conversation_blocks_history
    ADD CONSTRAINT conversation_blocks_history_pkey PRIMARY KEY (id, as_of_dt);


--
-- Name: conversation_snapshots_history conversation_snapshots_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.conversation_snapshots_history
    ADD CONSTRAINT conversation_snapshots_history_pkey PRIMARY KEY (id, as_of_dt);


--
-- Name: cross_references_history cross_references_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.cross_references_history
    ADD CONSTRAINT cross_references_pkey PRIMARY KEY (id);


--
-- Name: deliberation_participants deliberation_participants_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.deliberation_participants
    ADD CONSTRAINT deliberation_participants_pkey PRIMARY KEY (id);


--
-- Name: features_history features_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.features_history
    ADD CONSTRAINT features_history_pkey PRIMARY KEY (id, recorded_on_dt);


--
-- Name: harvest_candidates_history harvest_candidates_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.harvest_candidates_history
    ADD CONSTRAINT harvest_candidates_pkey PRIMARY KEY (id);


--
-- Name: harvest_references_history harvest_references_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.harvest_references_history
    ADD CONSTRAINT harvest_references_history_pkey PRIMARY KEY (id, as_of_dt);


--
-- Name: harvests_history harvests_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.harvests_history
    ADD CONSTRAINT harvests_history_pkey PRIMARY KEY (id, recorded_on_dt);


--
-- Name: implementation_notes implementation_notes_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.implementation_notes
    ADD CONSTRAINT implementation_notes_pkey PRIMARY KEY (id);


--
-- Name: implementation_plans_history implementation_plans_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.implementation_plans_history
    ADD CONSTRAINT implementation_plans_pkey PRIMARY KEY (id);


--
-- Name: observations_history observations_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.observations_history
    ADD CONSTRAINT observations_pkey PRIMARY KEY (id);


--
-- Name: open_question_answers_history open_question_answers_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.open_question_answers_history
    ADD CONSTRAINT open_question_answers_pkey PRIMARY KEY (id);


--
-- Name: open_questions_history open_questions_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.open_questions_history
    ADD CONSTRAINT open_questions_pkey PRIMARY KEY (id);


--
-- Name: requirement_segment_sets requirement_segment_sets_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.requirement_segment_sets
    ADD CONSTRAINT requirement_segment_sets_pkey PRIMARY KEY (id);


--
-- Name: requirement_verifications_history requirement_verifications_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.requirement_verifications_history
    ADD CONSTRAINT requirement_verifications_pkey PRIMARY KEY (id);


--
-- Name: requirement_verifications_history requirement_verifications_requirement_id_work_request_id_ro_key; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.requirement_verifications_history
    ADD CONSTRAINT requirement_verifications_requirement_id_work_request_id_ro_key UNIQUE (requirement_id, work_request_id, role);


--
-- Name: requirements_history requirements_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.requirements_history
    ADD CONSTRAINT requirements_history_pkey PRIMARY KEY (id, recorded_on_dt);


--
-- Name: roles_history roles_name_open_key; Type: INDEX; Schema: nebula; Owner: -
--
-- V175: partial open-snapshot unique replaces the V081-era full UNIQUE(name)
-- (roles_name_key), which permitted only one snapshot per role EVER and made
-- the architect-ruled close-then-insert grant convention structurally
-- impossible. Fresh bootstraps carry the repaired shape from birth.

CREATE UNIQUE INDEX roles_name_open_key
    ON nebula.roles_history USING btree (name)
    WHERE valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone;


--
-- Name: roles_history roles_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.roles_history
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: runtime_posture runtime_posture_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.runtime_posture
    ADD CONSTRAINT runtime_posture_pkey PRIMARY KEY (id);


--
-- Name: schema_version schema_version_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.schema_version
    ADD CONSTRAINT schema_version_pkey PRIMARY KEY (version);


--
-- Name: segment_set_members segment_set_members_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.segment_set_members
    ADD CONSTRAINT segment_set_members_pkey PRIMARY KEY (id);


--
-- Name: segment_sets segment_sets_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.segment_sets
    ADD CONSTRAINT segment_sets_pkey PRIMARY KEY (id);


--
-- Name: spec_documents_history spec_documents_history_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.spec_documents_history
    ADD CONSTRAINT spec_documents_history_pkey PRIMARY KEY (id);


--
-- Name: spec_requirements spec_requirements_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.spec_requirements
    ADD CONSTRAINT spec_requirements_pkey PRIMARY KEY (id);


--
-- Name: specifications_history specifications_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.specifications_history
    ADD CONSTRAINT specifications_pkey PRIMARY KEY (id);


--
-- Name: artifact_provenance_history uq_artifact_provenance_pair; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.artifact_provenance_history
    ADD CONSTRAINT uq_artifact_provenance_pair UNIQUE (subject_type, subject_id, source_type, source_id, relationship);


--
-- Name: implementation_plans_history uq_implementation_plans_plan_number; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.implementation_plans_history
    ADD CONSTRAINT uq_implementation_plans_plan_number UNIQUE (plan_number);


--
-- Name: work_requests_history uq_work_requests_entity_key_active; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.work_requests_history
    ADD CONSTRAINT uq_work_requests_entity_key_active EXCLUDE USING gist (entity_key WITH =, tstzrange(valid_from, valid_until) WITH &&);


--
-- Name: work_requests_history work_requests_pkey; Type: CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.work_requests_history
    ADD CONSTRAINT work_requests_pkey PRIMARY KEY (id);


--
-- Name: binding_decision_evidence binding_decision_evidence_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.binding_decision_evidence
    ADD CONSTRAINT binding_decision_evidence_pkey PRIMARY KEY (id);


--
-- Name: capabilities capabilities_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.capabilities
    ADD CONSTRAINT capabilities_pkey PRIMARY KEY (id);


--
-- Name: cir_violations cir_violations_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.cir_violations
    ADD CONSTRAINT cir_violations_pkey PRIMARY KEY (violation_id);


--
-- Name: decisions decisions_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.decisions
    ADD CONSTRAINT decisions_pkey PRIMARY KEY (id);


--
-- Name: role_circuit_breaker role_circuit_breaker_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.role_circuit_breaker
    ADD CONSTRAINT role_circuit_breaker_pkey PRIMARY KEY (role);


--
-- Name: state state_key_key; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.state
    ADD CONSTRAINT state_key_key UNIQUE (key);


--
-- Name: state state_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.state
    ADD CONSTRAINT state_pkey PRIMARY KEY (id);


--
-- Name: traces traces_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.traces
    ADD CONSTRAINT traces_pkey PRIMARY KEY (id);


--
-- Name: transactions transactions_idempotency_key_key; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.transactions
    ADD CONSTRAINT transactions_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- Name: binding_decision_evidence uq_binding_decision_identity; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.binding_decision_evidence
    ADD CONSTRAINT uq_binding_decision_identity UNIQUE (decision_id, evaluation_fingerprint);


--
-- Name: violations violations_pkey; Type: CONSTRAINT; Schema: peb; Owner: -
--

ALTER TABLE ONLY peb.violations
    ADD CONSTRAINT violations_pkey PRIMARY KEY (id);


--
-- Name: deployments deployments_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.deployments
    ADD CONSTRAINT deployments_pkey PRIMARY KEY (id);


--
-- Name: environment_type environment_type_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.environment_type
    ADD CONSTRAINT environment_type_pkey PRIMARY KEY (id);


--
-- Name: framework_type framework_type_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.framework_type
    ADD CONSTRAINT framework_type_pkey PRIMARY KEY (id);


--
-- Name: frameworks frameworks_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.frameworks
    ADD CONSTRAINT frameworks_pkey PRIMARY KEY (id);


--
-- Name: graph_view_connections graph_view_connections_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.graph_view_connections
    ADD CONSTRAINT graph_view_connections_pkey PRIMARY KEY (id);


--
-- Name: graph_view_positions graph_view_positions_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.graph_view_positions
    ADD CONSTRAINT graph_view_positions_pkey PRIMARY KEY (id);


--
-- Name: graph_views graph_views_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.graph_views
    ADD CONSTRAINT graph_views_pkey PRIMARY KEY (id);


--
-- Name: languages languages_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.languages
    ADD CONSTRAINT languages_pkey PRIMARY KEY (id);


--
-- Name: libraries libraries_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.libraries
    ADD CONSTRAINT libraries_pkey PRIMARY KEY (id);


--
-- Name: library_type library_type_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.library_type
    ADD CONSTRAINT library_type_pkey PRIMARY KEY (id);


--
-- Name: operating_systems operating_systems_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.operating_systems
    ADD CONSTRAINT operating_systems_pkey PRIMARY KEY (id);


--
-- Name: server_type server_type_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.server_type
    ADD CONSTRAINT server_type_pkey PRIMARY KEY (id);


--
-- Name: servers servers_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.servers
    ADD CONSTRAINT servers_pkey PRIMARY KEY (id);


--
-- Name: service_backends service_backends_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_backends
    ADD CONSTRAINT service_backends_pkey PRIMARY KEY (id);


--
-- Name: service_config_type service_config_type_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_config_type
    ADD CONSTRAINT service_config_type_pkey PRIMARY KEY (id);


--
-- Name: service_configs service_configs_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_configs
    ADD CONSTRAINT service_configs_pkey PRIMARY KEY (id);


--
-- Name: service_dependencies service_dependencies_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_dependencies
    ADD CONSTRAINT service_dependencies_pkey PRIMARY KEY (id);


--
-- Name: service_type service_type_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_type
    ADD CONSTRAINT service_type_pkey PRIMARY KEY (id);


--
-- Name: services services_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.services
    ADD CONSTRAINT services_pkey PRIMARY KEY (id);


--
-- Name: status_events status_events_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.status_events
    ADD CONSTRAINT status_events_pkey PRIMARY KEY (id);


--
-- Name: system_services system_services_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_services
    ADD CONSTRAINT system_services_pkey PRIMARY KEY (id);


--
-- Name: system_type system_type_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_type
    ADD CONSTRAINT system_type_pkey PRIMARY KEY (id);


--
-- Name: systems systems_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.systems
    ADD CONSTRAINT systems_pkey PRIMARY KEY (id);


--
-- Name: server_type uk47axffj92sknemn43enyga706; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.server_type
    ADD CONSTRAINT uk47axffj92sknemn43enyga706 UNIQUE (name);


--
-- Name: service_type uk5xqxi6a47dld2eppt6hl3jl50; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_type
    ADD CONSTRAINT uk5xqxi6a47dld2eppt6hl3jl50 UNIQUE (name);


--
-- Name: service_config_type uk9bsd5aydmwe35ibcedgtasxwy; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_config_type
    ADD CONSTRAINT uk9bsd5aydmwe35ibcedgtasxwy UNIQUE (name);


--
-- Name: system_services uk_system_service; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_services
    ADD CONSTRAINT uk_system_service UNIQUE (system_id, service_id);


--
-- Name: system_type uk_system_type_name; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_type
    ADD CONSTRAINT uk_system_type_name UNIQUE (name);


--
-- Name: systems uk_systems_name; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.systems
    ADD CONSTRAINT uk_systems_name UNIQUE (name);


--
-- Name: graph_view_positions uka8kw2ljt6cy8jmv8igfmdhi6f; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.graph_view_positions
    ADD CONSTRAINT uka8kw2ljt6cy8jmv8igfmdhi6f UNIQUE (graph_view_id, node_id);


--
-- Name: framework_type ukc1d1cvgpplad70dw9p62crfp; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.framework_type
    ADD CONSTRAINT ukc1d1cvgpplad70dw9p62crfp UNIQUE (name);


--
-- Name: systems ukcsju7iu71w27t5020jc47ps2e; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.systems
    ADD CONSTRAINT ukcsju7iu71w27t5020jc47ps2e UNIQUE (name);


--
-- Name: operating_systems ukedxi99omc5r0xbmu5mx5e2x5a; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.operating_systems
    ADD CONSTRAINT ukedxi99omc5r0xbmu5mx5e2x5a UNIQUE (name);


--
-- Name: languages ukf6axmaokhmrbmm746866v0uyu; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.languages
    ADD CONSTRAINT ukf6axmaokhmrbmm746866v0uyu UNIQUE (name);


--
-- Name: servers ukfe6lnv95hlungn045bxlmyao6; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.servers
    ADD CONSTRAINT ukfe6lnv95hlungn045bxlmyao6 UNIQUE (hostname);


--
-- Name: services ukh4rqgjwnqidx6mvj4i22dxwxe; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.services
    ADD CONSTRAINT ukh4rqgjwnqidx6mvj4i22dxwxe UNIQUE (name);


--
-- Name: frameworks ukhjbth9dc342cn7dqo0l2s1ftq; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.frameworks
    ADD CONSTRAINT ukhjbth9dc342cn7dqo0l2s1ftq UNIQUE (name);


--
-- Name: vendors ukhtlq499orm9wp8cxglyrhk5mt; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.vendors
    ADD CONSTRAINT ukhtlq499orm9wp8cxglyrhk5mt UNIQUE (name);


--
-- Name: visual_components ukhuht02ls158vi4s7gx7v921l9; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.visual_components
    ADD CONSTRAINT ukhuht02ls158vi4s7gx7v921l9 UNIQUE (type);


--
-- Name: library_type uki0o1frdg48p9ull5u8ul2jt2j; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.library_type
    ADD CONSTRAINT uki0o1frdg48p9ull5u8ul2jt2j UNIQUE (name);


--
-- Name: graph_view_connections ukir3o7kaq62jwx4ffwt3uts9k9; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.graph_view_connections
    ADD CONSTRAINT ukir3o7kaq62jwx4ffwt3uts9k9 UNIQUE (graph_view_id, source_node_id, target_node_id);


--
-- Name: environment_type ukj5cp8tm0isdx97b48sadhdo49; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.environment_type
    ADD CONSTRAINT ukj5cp8tm0isdx97b48sadhdo49 UNIQUE (name);


--
-- Name: system_type ukj5yn8lpoiv7wfij8f2j8ef0m1; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_type
    ADD CONSTRAINT ukj5yn8lpoiv7wfij8f2j8ef0m1 UNIQUE (name);


--
-- Name: system_services uklim8p3jweiydk986ocmtg9ruf; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_services
    ADD CONSTRAINT uklim8p3jweiydk986ocmtg9ruf UNIQUE (system_id, service_id);


--
-- Name: libraries uko22n7ao1v8c3pu4e57qc39g38; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.libraries
    ADD CONSTRAINT uko22n7ao1v8c3pu4e57qc39g38 UNIQUE (name);


--
-- Name: vendors vendors_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.vendors
    ADD CONSTRAINT vendors_pkey PRIMARY KEY (id);


--
-- Name: visual_components visual_components_pkey; Type: CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.visual_components
    ADD CONSTRAINT visual_components_pkey PRIMARY KEY (id);


--
-- Name: assertion_evaluation assertion_evaluation_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.assertion_evaluation
    ADD CONSTRAINT assertion_evaluation_pkey PRIMARY KEY (id);


--
-- Name: assessment assessment_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.assessment
    ADD CONSTRAINT assessment_pkey PRIMARY KEY (id);


--
-- Name: binding_resolution_transition binding_resolution_transition_idempotency_key_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.binding_resolution_transition
    ADD CONSTRAINT binding_resolution_transition_idempotency_key_key UNIQUE (idempotency_key);


--
-- Name: binding_resolution_transition binding_resolution_transition_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.binding_resolution_transition
    ADD CONSTRAINT binding_resolution_transition_pkey PRIMARY KEY (id);


--
-- Name: candidate candidate_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.candidate
    ADD CONSTRAINT candidate_pkey PRIMARY KEY (id);


--
-- Name: candidate_segment_set candidate_segment_set_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.candidate_segment_set
    ADD CONSTRAINT candidate_segment_set_pkey PRIMARY KEY (candidate_id, segment_set_id);


--
-- Name: candidate_source_chunk candidate_source_chunk_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.candidate_source_chunk
    ADD CONSTRAINT candidate_source_chunk_pkey PRIMARY KEY (candidate_id, chunk_id);


--
-- Name: canonical_asset canonical_asset_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.canonical_asset
    ADD CONSTRAINT canonical_asset_pkey PRIMARY KEY (id);


--
-- Name: concept_attribute_binding concept_attribute_binding_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute_binding
    ADD CONSTRAINT concept_attribute_binding_pkey PRIMARY KEY (attribute_id);


--
-- Name: concept_attribute concept_attribute_concept_id_name_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute
    ADD CONSTRAINT concept_attribute_concept_id_name_key UNIQUE (concept_id, name);


--
-- Name: concept_attribute concept_attribute_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute
    ADD CONSTRAINT concept_attribute_pkey PRIMARY KEY (id);


--
-- Name: concept_attribute_value concept_attribute_value_attribute_id_value_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute_value
    ADD CONSTRAINT concept_attribute_value_attribute_id_value_key UNIQUE (attribute_id, value);


--
-- Name: concept_attribute_value concept_attribute_value_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute_value
    ADD CONSTRAINT concept_attribute_value_pkey PRIMARY KEY (id);


--
-- Name: concept concept_name_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept
    ADD CONSTRAINT concept_name_key UNIQUE (name);


--
-- Name: concept concept_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept
    ADD CONSTRAINT concept_pkey PRIMARY KEY (id);


--
-- Name: concept_relationship_binding concept_relationship_binding_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_relationship_binding
    ADD CONSTRAINT concept_relationship_binding_pkey PRIMARY KEY (concept_relationship_id);


--
-- Name: concept_relationship concept_relationship_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_relationship
    ADD CONSTRAINT concept_relationship_pkey PRIMARY KEY (id);


--
-- Name: concept_state_transition concept_state_transition_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_state_transition
    ADD CONSTRAINT concept_state_transition_pkey PRIMARY KEY (id);


--
-- Name: consumer_operation consumer_operation_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.consumer_operation
    ADD CONSTRAINT consumer_operation_pkey PRIMARY KEY (id);


--
-- Name: enforcement_posture enforcement_posture_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.enforcement_posture
    ADD CONSTRAINT enforcement_posture_pkey PRIMARY KEY (id);


--
-- Name: execution_admission_receipt execution_admission_receipt_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_admission_receipt
    ADD CONSTRAINT execution_admission_receipt_pkey PRIMARY KEY (id);


--
-- Name: execution_claim_evidence execution_claim_evidence_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_claim_evidence
    ADD CONSTRAINT execution_claim_evidence_pkey PRIMARY KEY (id);


--
-- Name: execution_claim execution_claim_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_claim
    ADD CONSTRAINT execution_claim_pkey PRIMARY KEY (id);


--
-- Name: execution_evidence execution_evidence_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_evidence
    ADD CONSTRAINT execution_evidence_pkey PRIMARY KEY (id);


--
-- Name: expression_operand expression_operand_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.expression_operand
    ADD CONSTRAINT expression_operand_pkey PRIMARY KEY (parent_expression_id, "position");


--
-- Name: expression expression_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.expression
    ADD CONSTRAINT expression_pkey PRIMARY KEY (id);


--
-- Name: frame_dimension_meaning frame_dimension_meaning_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_meaning
    ADD CONSTRAINT frame_dimension_meaning_pkey PRIMARY KEY (id);


--
-- Name: frame_dimension_meaning frame_dimension_meaning_proposition_id_dimension_id_frame_d_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_meaning
    ADD CONSTRAINT frame_dimension_meaning_proposition_id_dimension_id_frame_d_key UNIQUE (proposition_id, dimension_id, frame_dimension_value_id);


--
-- Name: frame_dimension frame_dimension_name_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension
    ADD CONSTRAINT frame_dimension_name_key UNIQUE (name);


--
-- Name: frame_dimension frame_dimension_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension
    ADD CONSTRAINT frame_dimension_pkey PRIMARY KEY (id);


--
-- Name: frame_dimension_value frame_dimension_value_dimension_id_value_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_value
    ADD CONSTRAINT frame_dimension_value_dimension_id_value_key UNIQUE (dimension_id, value);


--
-- Name: frame_dimension_value frame_dimension_value_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_value
    ADD CONSTRAINT frame_dimension_value_pkey PRIMARY KEY (id);


--
-- Name: function_binding function_binding_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.function_binding
    ADD CONSTRAINT function_binding_pkey PRIMARY KEY (function_name);


--
-- Name: governance_threshold governance_threshold_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.governance_threshold
    ADD CONSTRAINT governance_threshold_pkey PRIMARY KEY (id);


--
-- Name: harvest harvest_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.harvest
    ADD CONSTRAINT harvest_pkey PRIMARY KEY (id);


--
-- Name: identity_strategy identity_strategy_concept_id_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.identity_strategy
    ADD CONSTRAINT identity_strategy_concept_id_key UNIQUE (concept_id);


--
-- Name: identity_strategy identity_strategy_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.identity_strategy
    ADD CONSTRAINT identity_strategy_pkey PRIMARY KEY (id);


--
-- Name: implementation_plan implementation_plan_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.implementation_plan
    ADD CONSTRAINT implementation_plan_pkey PRIMARY KEY (id);


--
-- Name: implementation_plan implementation_plan_plan_number_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.implementation_plan
    ADD CONSTRAINT implementation_plan_plan_number_key UNIQUE (plan_number);


--
-- Name: keychain_evaluation_manifest keychain_eval_manifest_source_eval_uq; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.keychain_evaluation_manifest
    ADD CONSTRAINT keychain_eval_manifest_source_eval_uq UNIQUE (source_namespace, evaluation_id);


--
-- Name: keychain_evaluation_manifest keychain_evaluation_manifest_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.keychain_evaluation_manifest
    ADD CONSTRAINT keychain_evaluation_manifest_pkey PRIMARY KEY (manifest_id);


--
-- Name: keychain_event_outbox keychain_event_outbox_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.keychain_event_outbox
    ADD CONSTRAINT keychain_event_outbox_pkey PRIMARY KEY (id);


--
-- Name: keychain_event_outbox keychain_event_outbox_source_event_uq; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.keychain_event_outbox
    ADD CONSTRAINT keychain_event_outbox_source_event_uq UNIQUE (source_namespace, source_event_id);


--
-- Name: migration_ledger migration_ledger_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.migration_ledger
    ADD CONSTRAINT migration_ledger_pkey PRIMARY KEY (id);


--
-- Name: migration_ledger migration_ledger_schema_name_migration_label_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.migration_ledger
    ADD CONSTRAINT migration_ledger_schema_name_migration_label_key UNIQUE (schema_name, migration_label);


--
-- Name: observation observation_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.observation
    ADD CONSTRAINT observation_pkey PRIMARY KEY (id);


--
-- Name: observation_source_chunk observation_source_chunk_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.observation_source_chunk
    ADD CONSTRAINT observation_source_chunk_pkey PRIMARY KEY (observation_id, chunk_id);


--
-- Name: open_question_answer open_question_answer_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question_answer
    ADD CONSTRAINT open_question_answer_pkey PRIMARY KEY (id);


--
-- Name: open_question_entity open_question_entity_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question_entity
    ADD CONSTRAINT open_question_entity_pkey PRIMARY KEY (open_question_id, asset_concept_id, entity_id);


--
-- Name: open_question open_question_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question
    ADD CONSTRAINT open_question_pkey PRIMARY KEY (id);


--
-- Name: owning_subsystem owning_subsystem_name_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.owning_subsystem
    ADD CONSTRAINT owning_subsystem_name_key UNIQUE (name);


--
-- Name: owning_subsystem owning_subsystem_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.owning_subsystem
    ADD CONSTRAINT owning_subsystem_pkey PRIMARY KEY (id);


--
-- Name: proposition_assertion proposition_assertion_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_assertion
    ADD CONSTRAINT proposition_assertion_pkey PRIMARY KEY (proposition_id, rule_id);


--
-- Name: proposition_comparison proposition_comparison_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_comparison
    ADD CONSTRAINT proposition_comparison_pkey PRIMARY KEY (proposition_id, representation_comparison_id);


--
-- Name: proposition_frame_value proposition_frame_value_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_frame_value
    ADD CONSTRAINT proposition_frame_value_pkey PRIMARY KEY (id);


--
-- Name: proposition_frame_value proposition_frame_value_proposition_id_dimension_id_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_frame_value
    ADD CONSTRAINT proposition_frame_value_proposition_id_dimension_id_key UNIQUE (proposition_id, dimension_id);


--
-- Name: proposition proposition_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition
    ADD CONSTRAINT proposition_pkey PRIMARY KEY (id);


--
-- Name: representation_comparison representation_comparison_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_comparison
    ADD CONSTRAINT representation_comparison_pkey PRIMARY KEY (id);


--
-- Name: representation_identity representation_identity_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_identity
    ADD CONSTRAINT representation_identity_pkey PRIMARY KEY (id);


--
-- Name: representation_identity representation_identity_representation_id_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_identity
    ADD CONSTRAINT representation_identity_representation_id_key UNIQUE (representation_id);


--
-- Name: representation representation_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation
    ADD CONSTRAINT representation_pkey PRIMARY KEY (id);


--
-- Name: representation_relationship representation_relationship_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_relationship
    ADD CONSTRAINT representation_relationship_pkey PRIMARY KEY (id);


--
-- Name: requirement requirement_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.requirement
    ADD CONSTRAINT requirement_pkey PRIMARY KEY (id);


--
-- Name: requirement_segment_set requirement_segment_set_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.requirement_segment_set
    ADD CONSTRAINT requirement_segment_set_pkey PRIMARY KEY (requirement_id, segment_set_id);


--
-- Name: rule rule_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.rule
    ADD CONSTRAINT rule_pkey PRIMARY KEY (id);


--
-- Name: semantic_type semantic_type_name_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.semantic_type
    ADD CONSTRAINT semantic_type_name_key UNIQUE (name);


--
-- Name: semantic_type semantic_type_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.semantic_type
    ADD CONSTRAINT semantic_type_pkey PRIMARY KEY (id);


--
-- Name: semantic_type_required_dimension semantic_type_required_dimension_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.semantic_type_required_dimension
    ADD CONSTRAINT semantic_type_required_dimension_pkey PRIMARY KEY (semantic_type_id, dimension_id);


--
-- Name: specification_lineage specification_lineage_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.specification_lineage
    ADD CONSTRAINT specification_lineage_pkey PRIMARY KEY (specification_id, derived_from_id);


--
-- Name: specification specification_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.specification
    ADD CONSTRAINT specification_pkey PRIMARY KEY (id);


--
-- Name: t24_graph_edge_evidence t24_graph_edge_evidence_graph_edge_id_evidence_id_key; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.t24_graph_edge_evidence
    ADD CONSTRAINT t24_graph_edge_evidence_graph_edge_id_evidence_id_key UNIQUE (graph_edge_id, evidence_id);


--
-- Name: t24_graph_edge_evidence t24_graph_edge_evidence_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.t24_graph_edge_evidence
    ADD CONSTRAINT t24_graph_edge_evidence_pkey PRIMARY KEY (evidence_id);


--
-- Name: enforcement_posture uq_enforcement_posture_family_effective; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.enforcement_posture
    ADD CONSTRAINT uq_enforcement_posture_family_effective UNIQUE (family, effective_from);


--
-- Name: verified_statement verified_statement_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.verified_statement
    ADD CONSTRAINT verified_statement_pkey PRIMARY KEY (id);


--
-- Name: work_request_edge work_request_edge_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request_edge
    ADD CONSTRAINT work_request_edge_pkey PRIMARY KEY (id);


--
-- Name: work_request work_request_pkey; Type: CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request
    ADD CONSTRAINT work_request_pkey PRIMARY KEY (id);


--
-- Name: asset_identity_claim asset_identity_claim_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_identity_claim
    ADD CONSTRAINT asset_identity_claim_pkey PRIMARY KEY (id);


--
-- Name: asset_relation asset_relation_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_relation
    ADD CONSTRAINT asset_relation_pkey PRIMARY KEY (id);


--
-- Name: asset_revision asset_revision_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_revision
    ADD CONSTRAINT asset_revision_pkey PRIMARY KEY (id);


--
-- Name: canonical_asset canonical_asset_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.canonical_asset
    ADD CONSTRAINT canonical_asset_pkey PRIMARY KEY (id);


--
-- Name: drift_finding drift_finding_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.drift_finding
    ADD CONSTRAINT drift_finding_pkey PRIMARY KEY (id);


--
-- Name: evidence_item evidence_item_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.evidence_item
    ADD CONSTRAINT evidence_item_pkey PRIMARY KEY (id);


--
-- Name: evidence_type evidence_type_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.evidence_type
    ADD CONSTRAINT evidence_type_pkey PRIMARY KEY (id);


--
-- Name: relationship_type relationship_type_name_key; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.relationship_type
    ADD CONSTRAINT relationship_type_name_key UNIQUE (name);


--
-- Name: relationship_type relationship_type_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.relationship_type
    ADD CONSTRAINT relationship_type_pkey PRIMARY KEY (id);


--
-- Name: snapshot_observation snapshot_observation_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.snapshot_observation
    ADD CONSTRAINT snapshot_observation_pkey PRIMARY KEY (id);


--
-- Name: snapshot snapshot_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.snapshot
    ADD CONSTRAINT snapshot_pkey PRIMARY KEY (id);


--
-- Name: source_observation source_observation_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.source_observation
    ADD CONSTRAINT source_observation_pkey PRIMARY KEY (id);


--
-- Name: statement_evidence statement_evidence_pkey; Type: CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.statement_evidence
    ADD CONSTRAINT statement_evidence_pkey PRIMARY KEY (id);


--
-- Name: agent_scheduler agent_scheduler_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.agent_scheduler
    ADD CONSTRAINT agent_scheduler_pkey PRIMARY KEY (id);


--
-- Name: agent_timeclock agent_timeclock_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.agent_timeclock
    ADD CONSTRAINT agent_timeclock_pkey PRIMARY KEY (id);


--
-- Name: circuit_breaker circuit_breaker_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.circuit_breaker
    ADD CONSTRAINT circuit_breaker_pkey PRIMARY KEY (id);


--
-- Name: config_bundle config_bundle_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.config_bundle
    ADD CONSTRAINT config_bundle_pkey PRIMARY KEY (id);


--
-- Name: config_bundle config_bundle_role_model_id_key; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.config_bundle
    ADD CONSTRAINT config_bundle_role_model_id_key UNIQUE (role, model_id);


--
-- Name: harnesses harnesses_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.harnesses
    ADD CONSTRAINT harnesses_pkey PRIMARY KEY (id);


--
-- Name: memory memory_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.memory
    ADD CONSTRAINT memory_pkey PRIMARY KEY (id);


--
-- Name: memory memory_slug_key; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.memory
    ADD CONSTRAINT memory_slug_key UNIQUE (slug);


--
-- Name: models models_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.models
    ADD CONSTRAINT models_pkey PRIMARY KEY (id);


--
-- Name: projection_configs projection_configs_name_key; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.projection_configs
    ADD CONSTRAINT projection_configs_name_key UNIQUE (name);


--
-- Name: projection_configs projection_configs_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.projection_configs
    ADD CONSTRAINT projection_configs_pkey PRIMARY KEY (id);


--
-- Name: prompts prompts_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.prompts
    ADD CONSTRAINT prompts_pkey PRIMARY KEY (id);


--
-- Name: providers providers_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.providers
    ADD CONSTRAINT providers_pkey PRIMARY KEY (id);


--
-- Name: role_leases role_leases_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.role_leases
    ADD CONSTRAINT role_leases_pkey PRIMARY KEY (id);


--
-- Name: role_memory role_memory_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.role_memory
    ADD CONSTRAINT role_memory_pkey PRIMARY KEY (id);


--
-- Name: role_tool_access role_tool_access_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.role_tool_access
    ADD CONSTRAINT role_tool_access_pkey PRIMARY KEY (id);


--
-- Name: roles roles_name_key; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.roles
    ADD CONSTRAINT roles_name_key UNIQUE (name);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: schema_version schema_version_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.schema_version
    ADD CONSTRAINT schema_version_pkey PRIMARY KEY (version);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- Name: system_logs system_logs_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.system_logs
    ADD CONSTRAINT system_logs_pkey PRIMARY KEY (id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: prompts uq_prompts_role_slug_version; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.prompts
    ADD CONSTRAINT uq_prompts_role_slug_version UNIQUE (role, slug, version);


--
-- Name: role_tool_access uq_role_tool_access_role_mcp_tool; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.role_tool_access
    ADD CONSTRAINT uq_role_tool_access_role_mcp_tool UNIQUE (role, mcp_id, tool_slug);


--
-- Name: tasks uq_tasks_role_task_slug; Type: CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.tasks
    ADD CONSTRAINT uq_tasks_role_task_slug UNIQUE (role, task_slug);


--
-- Name: runnable_services runnable_services_name_key; Type: CONSTRAINT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.runnable_services
    ADD CONSTRAINT runnable_services_name_key UNIQUE (name);


--
-- Name: runnable_services runnable_services_pkey; Type: CONSTRAINT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.runnable_services
    ADD CONSTRAINT runnable_services_pkey PRIMARY KEY (id);


--
-- Name: service_endpoints service_endpoints_pkey; Type: CONSTRAINT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.service_endpoints
    ADD CONSTRAINT service_endpoints_pkey PRIMARY KEY (id);


--
-- Name: service_endpoints uq_service_endpoints_unit_instance; Type: CONSTRAINT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.service_endpoints
    ADD CONSTRAINT uq_service_endpoints_unit_instance UNIQUE (unit, instance);


--
-- Name: receipts receipts_pkey; Type: CONSTRAINT; Schema: vision; Owner: -
--

ALTER TABLE ONLY vision.receipts
    ADD CONSTRAINT receipts_pkey PRIMARY KEY (id);


--
-- Name: tickets tickets_pkey; Type: CONSTRAINT; Schema: vision; Owner: -
--

ALTER TABLE ONLY vision.tickets
    ADD CONSTRAINT tickets_pkey PRIMARY KEY (id);


--
-- Name: vision_ir_artifacts vision_ir_artifacts_pkey; Type: CONSTRAINT; Schema: vision; Owner: -
--

ALTER TABLE ONLY vision.vision_ir_artifacts
    ADD CONSTRAINT vision_ir_artifacts_pkey PRIMARY KEY (artifact_id);


--
-- Name: work_request_edges_history work_request_edges_history_pkey; Type: CONSTRAINT; Schema: vision; Owner: -
--

ALTER TABLE ONLY vision.work_request_edges_history
    ADD CONSTRAINT work_request_edges_history_pkey PRIMARY KEY (id, recorded_on_dt);


--
-- Name: wr_compile_verdicts wr_compile_verdicts_pkey; Type: CONSTRAINT; Schema: vision; Owner: -
--

ALTER TABLE ONLY vision.wr_compile_verdicts
    ADD CONSTRAINT wr_compile_verdicts_pkey PRIMARY KEY (verdict_id);


--
-- Name: event_types event_types_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.event_types
    ADD CONSTRAINT event_types_pkey PRIMARY KEY (event_type);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: offices offices_name_key; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.offices
    ADD CONSTRAINT offices_name_key UNIQUE (name);


--
-- Name: offices offices_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.offices
    ADD CONSTRAINT offices_pkey PRIMARY KEY (id);


--
-- Name: receipts receipts_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.receipts
    ADD CONSTRAINT receipts_pkey PRIMARY KEY (id);


--
-- Name: receipts receipts_ticket_id_key; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.receipts
    ADD CONSTRAINT receipts_ticket_id_key UNIQUE (ticket_id);


--
-- Name: task_outcomes task_outcomes_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.task_outcomes
    ADD CONSTRAINT task_outcomes_pkey PRIMARY KEY (id);


--
-- Name: tasks tasks_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tasks
    ADD CONSTRAINT tasks_pkey PRIMARY KEY (id);


--
-- Name: tickets tickets_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tickets
    ADD CONSTRAINT tickets_pkey PRIMARY KEY (id);


--
-- Name: titles titles_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.titles
    ADD CONSTRAINT titles_pkey PRIMARY KEY (id);


--
-- Name: workflow_edges uq_deterministic_edge; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_edges
    ADD CONSTRAINT uq_deterministic_edge UNIQUE (workflow_version_id, from_node_id, outcome_id);


--
-- Name: workflow_instances uq_instance_version_composite; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_instances
    ADD CONSTRAINT uq_instance_version_composite UNIQUE (id, workflow_version_id);


--
-- Name: workflow_nodes uq_node_task_composite; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_nodes
    ADD CONSTRAINT uq_node_task_composite UNIQUE (id, task_id);


--
-- Name: workflow_nodes uq_node_version_composite; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_nodes
    ADD CONSTRAINT uq_node_version_composite UNIQUE (id, workflow_version_id);


--
-- Name: tasks uq_office_task_name; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tasks
    ADD CONSTRAINT uq_office_task_name UNIQUE (office_id, name);


--
-- Name: titles uq_office_title; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.titles
    ADD CONSTRAINT uq_office_title UNIQUE (office_id, display_name);


--
-- Name: task_outcomes uq_task_outcome_code; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.task_outcomes
    ADD CONSTRAINT uq_task_outcome_code UNIQUE (task_id, code);


--
-- Name: task_outcomes uq_task_outcome_composite; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.task_outcomes
    ADD CONSTRAINT uq_task_outcome_composite UNIQUE (id, task_id);


--
-- Name: tickets uq_ticket_task_composite; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tickets
    ADD CONSTRAINT uq_ticket_task_composite UNIQUE (id, node_task_id);


--
-- Name: workflow_versions uq_version_composite; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_versions
    ADD CONSTRAINT uq_version_composite UNIQUE (id, workflow_id);


--
-- Name: workflow_nodes uq_version_node_name; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_nodes
    ADD CONSTRAINT uq_version_node_name UNIQUE (workflow_version_id, name);


--
-- Name: workflow_versions uq_workflow_version; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_versions
    ADD CONSTRAINT uq_workflow_version UNIQUE (workflow_id, version_number);


--
-- Name: workflow_edges workflow_edges_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_edges
    ADD CONSTRAINT workflow_edges_pkey PRIMARY KEY (id);


--
-- Name: workflow_instances workflow_instances_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_instances
    ADD CONSTRAINT workflow_instances_pkey PRIMARY KEY (id);


--
-- Name: workflow_nodes workflow_nodes_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_nodes
    ADD CONSTRAINT workflow_nodes_pkey PRIMARY KEY (id);


--
-- Name: workflow_versions workflow_versions_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_versions
    ADD CONSTRAINT workflow_versions_pkey PRIMARY KEY (id);


--
-- Name: workflows workflows_name_key; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflows
    ADD CONSTRAINT workflows_name_key UNIQUE (name);


--
-- Name: workflows workflows_pkey; Type: CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflows
    ADD CONSTRAINT workflows_pkey PRIMARY KEY (id);


--
-- Name: idx_events_aggregate; Type: INDEX; Schema: cascade; Owner: -
--

CREATE INDEX idx_events_aggregate ON cascade.events USING btree (aggregate_type, aggregate_id);


--
-- Name: idx_events_causation; Type: INDEX; Schema: cascade; Owner: -
--

CREATE INDEX idx_events_causation ON cascade.events USING btree (causation_id) WHERE (causation_id IS NOT NULL);


--
-- Name: idx_events_correlation; Type: INDEX; Schema: cascade; Owner: -
--

CREATE INDEX idx_events_correlation ON cascade.events USING btree (correlation_id) WHERE (correlation_id IS NOT NULL);


--
-- Name: idx_events_event_type; Type: INDEX; Schema: cascade; Owner: -
--

CREATE INDEX idx_events_event_type ON cascade.events USING btree (event_type);


--
-- Name: idx_events_timestamp; Type: INDEX; Schema: cascade; Owner: -
--

CREATE INDEX idx_events_timestamp ON cascade.events USING btree (event_timestamp);


--
-- Name: idx_lineage_source; Type: INDEX; Schema: cascade; Owner: -
--

CREATE INDEX idx_lineage_source ON cascade.lineage_edges USING btree (source_type, source_id);


--
-- Name: idx_lineage_target; Type: INDEX; Schema: cascade; Owner: -
--

CREATE INDEX idx_lineage_target ON cascade.lineage_edges USING btree (target_type, target_id);


--
-- Name: idx_lineage_unique_edge; Type: INDEX; Schema: cascade; Owner: -
--

CREATE UNIQUE INDEX idx_lineage_unique_edge ON cascade.lineage_edges USING btree (source_type, source_id, target_type, target_id, relationship);


--
-- Name: idx_cost_logs_session; Type: INDEX; Schema: conduit; Owner: -
--

CREATE INDEX idx_cost_logs_session ON conduit.cost_logs USING btree (session_id);


--
-- Name: idx_cost_logs_ticket; Type: INDEX; Schema: conduit; Owner: -
--

CREATE INDEX idx_cost_logs_ticket ON conduit.cost_logs USING btree (ticket_id);


--
-- Name: idx_execution_attempts_lease; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_attempts_lease ON execution.attempts USING btree (lease_id);


--
-- Name: idx_execution_attempts_request; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_attempts_request ON execution.attempts USING btree (request_id);


--
-- Name: idx_execution_attempts_status; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_attempts_status ON execution.attempts USING btree (status);


--
-- Name: idx_execution_leases_active_per_request; Type: INDEX; Schema: execution; Owner: -
--

CREATE UNIQUE INDEX idx_execution_leases_active_per_request ON execution.leases USING btree (request_id) WHERE (status = 'ACTIVE'::text);


--
-- Name: idx_execution_leases_executor; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_leases_executor ON execution.leases USING btree (executor_id);


--
-- Name: idx_execution_leases_request; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_leases_request ON execution.leases USING btree (request_id);


--
-- Name: idx_execution_receipts_attempt; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_receipts_attempt ON execution.receipts USING btree (attempt_id);


--
-- Name: idx_execution_receipts_conduit_lineage; Type: INDEX; Schema: execution; Owner: -
--

CREATE UNIQUE INDEX idx_execution_receipts_conduit_lineage ON execution.receipts USING btree (lineage_original_id) WHERE (lineage_source = 'conduit'::text);


--
-- Name: idx_execution_receipts_issued_at_id; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_receipts_issued_at_id ON execution.receipts USING btree (issued_at DESC, id DESC);


--
-- Name: idx_execution_receipts_request; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_receipts_request ON execution.receipts USING btree (request_id);


--
-- Name: idx_execution_receipts_type; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_receipts_type ON execution.receipts USING btree (type);


--
-- Name: idx_execution_requests_source_plan; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_requests_source_plan ON execution.requests USING btree (source_plan_id) WHERE (source_plan_id IS NOT NULL);


--
-- Name: idx_execution_requests_source_wr; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_requests_source_wr ON execution.requests USING btree (source_wr_id) WHERE (source_wr_id IS NOT NULL);


--
-- Name: idx_execution_requests_status; Type: INDEX; Schema: execution; Owner: -
--

CREATE INDEX idx_execution_requests_status ON execution.requests USING btree (status);


--
-- Name: idx_agent_records_history_candidate_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_agent_records_history_candidate_id ON nebula.agent_records_history USING btree (candidate_id) WHERE (candidate_id IS NOT NULL);


--
-- Name: idx_agent_records_history_created_at_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_agent_records_history_created_at_id ON nebula.agent_records_history USING btree (created_at DESC, id DESC);


--
-- Name: idx_agent_records_history_requirement_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_agent_records_history_requirement_id ON nebula.agent_records_history USING btree (requirement_id) WHERE (requirement_id IS NOT NULL);


--
-- Name: idx_architect_specs_requirement; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_architect_specs_requirement ON nebula.architect_specs_history USING btree (requirement_id);


--
-- Name: idx_architect_specs_work_request; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_architect_specs_work_request ON nebula.architect_specs_history USING btree (work_request_id) WHERE (work_request_id IS NOT NULL);


--
-- Name: idx_artifact_provenance_source; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_artifact_provenance_source ON nebula.artifact_provenance_history USING btree (source_type, source_id);


--
-- Name: idx_artifact_provenance_subject; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_artifact_provenance_subject ON nebula.artifact_provenance_history USING btree (subject_type, subject_id);


--
-- Name: idx_harvests_history_created_at_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_harvests_history_created_at_id ON nebula.harvests_history USING btree (created_at DESC NULLS LAST, id DESC);


--
-- Name: idx_hc_harvest_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_hc_harvest_id ON nebula.harvest_candidates_history USING btree (harvest_id);


--
-- Name: idx_hc_needs_new_node; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_hc_needs_new_node ON nebula.harvest_candidates_history USING btree (needs_new_node) WHERE (needs_new_node = true);


--
-- Name: idx_hc_type; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_hc_type ON nebula.harvest_candidates_history USING btree (type);


--
-- Name: idx_implementation_notes_asset_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_implementation_notes_asset_id ON nebula.implementation_notes USING btree (asset_id);


--
-- Name: idx_implementation_notes_note_type_asset; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_implementation_notes_note_type_asset ON nebula.implementation_notes USING btree (note_type, asset_id);


--
-- Name: idx_implementation_notes_source_record; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_implementation_notes_source_record ON nebula.implementation_notes USING btree (source_record_id) WHERE (source_record_id IS NOT NULL);


--
-- Name: idx_implementation_plans_history_updated_at_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_implementation_plans_history_updated_at_id ON nebula.implementation_plans_history USING btree (updated_at DESC, id DESC);


--
-- Name: idx_open_questions_blocking; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_open_questions_blocking ON nebula.open_questions_history USING btree (blocking) WHERE (blocking = true);


--
-- Name: idx_open_questions_candidate; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_open_questions_candidate ON nebula.open_questions_history USING btree (candidate_id) WHERE (candidate_id IS NOT NULL);


--
-- Name: idx_open_questions_history_candidate_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_open_questions_history_candidate_id ON nebula.open_questions_history USING btree (candidate_id);


--
-- Name: idx_open_questions_history_requirement_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_open_questions_history_requirement_id ON nebula.open_questions_history USING btree (requirement_id);


--
-- Name: idx_open_questions_history_status; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_open_questions_history_status ON nebula.open_questions_history USING btree (status);


--
-- Name: idx_open_questions_requirement; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_open_questions_requirement ON nebula.open_questions_history USING btree (requirement_id);


--
-- Name: idx_open_questions_status; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_open_questions_status ON nebula.open_questions_history USING btree (status);


--
-- Name: idx_oqa_question; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_oqa_question ON nebula.open_question_answers_history USING btree (question_id);


--
-- Name: idx_oqa_question_role; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_oqa_question_role ON nebula.open_question_answers_history USING btree (question_id, role);


--
-- Name: idx_oqa_role; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_oqa_role ON nebula.open_question_answers_history USING btree (role);


--
-- Name: idx_requirements_history_status; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_requirements_history_status ON nebula.requirements_history USING btree (status) WHERE (valid_until = '9999-12-31 23:59:59+00'::timestamp with time zone);


--
-- Name: idx_requirements_history_system; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_requirements_history_system ON nebula.requirements_history USING btree (system_id) WHERE (valid_until = '9999-12-31 23:59:59+00'::timestamp with time zone);


--
-- Name: idx_requirements_history_valid; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_requirements_history_valid ON nebula.requirements_history USING btree (valid_from, valid_until);


--
-- Name: idx_runtime_posture_checked_at; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_runtime_posture_checked_at ON nebula.runtime_posture USING btree (checked_at DESC, host) WHERE (expired_at IS NULL);


--
-- Name: idx_runtime_posture_healthy; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_runtime_posture_healthy ON nebula.runtime_posture USING btree (host, all_healthy, checked_at DESC) WHERE (expired_at IS NULL);


--
-- Name: idx_runtime_posture_unhealthy; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_runtime_posture_unhealthy ON nebula.runtime_posture USING btree (host, checked_at DESC) WHERE ((expired_at IS NULL) AND (all_healthy = false));


--
-- Name: idx_segment_set_members_set; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_segment_set_members_set ON nebula.segment_set_members USING btree (segment_set_id) WHERE included;


--
-- Name: idx_spec_docs_candidate; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_spec_docs_candidate ON nebula.spec_documents_history USING btree (candidate_id);


--
-- Name: idx_spec_docs_harvest; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_spec_docs_harvest ON nebula.spec_documents_history USING btree (harvest_id);


--
-- Name: idx_spec_docs_status; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_spec_docs_status ON nebula.spec_documents_history USING btree (status);


--
-- Name: idx_spec_reqs_requirement; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_spec_reqs_requirement ON nebula.spec_requirements USING btree (requirement_id);


--
-- Name: idx_spec_reqs_spec; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_spec_reqs_spec ON nebula.spec_requirements USING btree (spec_id);


--
-- Name: idx_verification_requirement; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_verification_requirement ON nebula.requirement_verifications_history USING btree (requirement_id);


--
-- Name: idx_verification_status; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_verification_status ON nebula.requirement_verifications_history USING btree (status);


--
-- Name: idx_verification_work_request; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_verification_work_request ON nebula.requirement_verifications_history USING btree (work_request_id);


--
-- Name: idx_work_requests_business_status; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_work_requests_business_status ON nebula.work_requests_history USING btree (business_status);


--
-- Name: idx_work_requests_consumed_at; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_work_requests_consumed_at ON nebula.work_requests_history USING btree (consumed_at) WHERE (consumed_at IS NULL);


--
-- Name: idx_work_requests_history_entity_key; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_work_requests_history_entity_key ON nebula.work_requests_history USING btree (entity_key) WHERE (entity_key IS NOT NULL);


--
-- Name: idx_work_requests_legacy_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_work_requests_legacy_id ON nebula.work_requests_history USING btree (legacy_id) WHERE (legacy_id IS NOT NULL);


--
-- Name: idx_work_requests_plan_id; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX idx_work_requests_plan_id ON nebula.work_requests_history USING btree (plan_id) WHERE (plan_id IS NOT NULL);


--
-- Name: nebula_idx_assessment_event; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX nebula_idx_assessment_event ON nebula.assessment_resolutions_history USING btree (event_id);


--
-- Name: nebula_idx_assessment_outcome; Type: INDEX; Schema: nebula; Owner: -
--

CREATE INDEX nebula_idx_assessment_outcome ON nebula.assessment_resolutions_history USING btree (outcome);


--
-- Name: uq_agenda_item_questions_current; Type: INDEX; Schema: nebula; Owner: -
--

CREATE UNIQUE INDEX uq_agenda_item_questions_current ON nebula.agenda_item_questions USING btree (agenda_item_id, open_question_id) WHERE (valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone);


--
-- Name: uq_candidate_dependencies_current; Type: INDEX; Schema: nebula; Owner: -
--

CREATE UNIQUE INDEX uq_candidate_dependencies_current ON nebula.candidate_dependencies USING btree (candidate_id, depends_on_id) WHERE (valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone);


--
-- Name: uq_candidate_segment_sets_current; Type: INDEX; Schema: nebula; Owner: -
--

CREATE UNIQUE INDEX uq_candidate_segment_sets_current ON nebula.candidate_segment_sets USING btree (candidate_id, segment_set_id) WHERE (valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone);


--
-- Name: uq_cross_ref_current; Type: INDEX; Schema: nebula; Owner: -
--

CREATE UNIQUE INDEX uq_cross_ref_current ON nebula.cross_references_history USING btree (source_type, source_id, target_type, target_id, rel_type) WHERE (valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone);


--
-- Name: uq_deliberation_participants_current; Type: INDEX; Schema: nebula; Owner: -
--

CREATE UNIQUE INDEX uq_deliberation_participants_current ON nebula.deliberation_participants USING btree (open_question_id, role) WHERE (valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone);


--
-- Name: uq_requirement_segment_sets_current; Type: INDEX; Schema: nebula; Owner: -
--

CREATE UNIQUE INDEX uq_requirement_segment_sets_current ON nebula.requirement_segment_sets USING btree (requirement_id, segment_set_id) WHERE (valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone);


--
-- Name: uq_segment_set_members_current; Type: INDEX; Schema: nebula; Owner: -
--

CREATE UNIQUE INDEX uq_segment_set_members_current ON nebula.segment_set_members USING btree (segment_set_id, segment_id) WHERE (valid_until = '9999-12-31 00:00:00+00'::timestamp with time zone);


--
-- Name: idx_binding_decision_disposition; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_binding_decision_disposition ON peb.binding_decision_evidence USING btree (disposition, created_at DESC);


--
-- Name: idx_binding_decision_subject_created; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_binding_decision_subject_created ON peb.binding_decision_evidence USING btree (subject_id, created_at DESC);


--
-- Name: idx_cir_violations_detected_at; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_cir_violations_detected_at ON peb.cir_violations USING btree (detected_at);


--
-- Name: idx_cir_violations_event_id; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_cir_violations_event_id ON peb.cir_violations USING btree (event_id);


--
-- Name: idx_cir_violations_rule_id; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_cir_violations_rule_id ON peb.cir_violations USING btree (rule_id);


--
-- Name: idx_cir_violations_severity; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_cir_violations_severity ON peb.cir_violations USING btree (severity);


--
-- Name: idx_peb_governance_events_created_at; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_peb_governance_events_created_at ON peb.governance_events USING btree (created_at);


--
-- Name: idx_peb_governance_events_event_type; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_peb_governance_events_event_type ON peb.governance_events USING btree (event_type);


--
-- Name: idx_peb_governance_events_plan_id; Type: INDEX; Schema: peb; Owner: -
--

CREATE INDEX idx_peb_governance_events_plan_id ON peb.governance_events USING btree (plan_id);


--
-- Name: idx_peb_governance_events_receipt_id; Type: INDEX; Schema: peb; Owner: -
--

CREATE UNIQUE INDEX idx_peb_governance_events_receipt_id ON peb.governance_events USING btree (receipt_id);


--
-- Name: idx_system_services_service; Type: INDEX; Schema: registry; Owner: -
--

CREATE INDEX idx_system_services_service ON registry.system_services USING btree (service_id);


--
-- Name: idx_system_services_system; Type: INDEX; Schema: registry; Owner: -
--

CREATE INDEX idx_system_services_system ON registry.system_services USING btree (system_id);


--
-- Name: idx_systems_type; Type: INDEX; Schema: registry; Owner: -
--

CREATE INDEX idx_systems_type ON registry.systems USING btree (system_type_id);


--
-- Name: governance_threshold_name_effective_key; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX governance_threshold_name_effective_key ON resolution.governance_threshold USING btree (name, effective_from DESC);


--
-- Name: idx_assertion_evaluation_proposition; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_assertion_evaluation_proposition ON resolution.assertion_evaluation USING btree (proposition_id, evaluated_at DESC);


--
-- Name: idx_canonical_asset_active_canonical_asset_id; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX idx_canonical_asset_active_canonical_asset_id ON resolution.canonical_asset USING btree (canonical_asset_id) WHERE (expired_at IS NULL);


--
-- Name: idx_execution_admission_receipt_claim; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_execution_admission_receipt_claim ON resolution.execution_admission_receipt USING btree (claim_id, evidence_id);


--
-- Name: idx_execution_admission_receipt_peb_tx; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX idx_execution_admission_receipt_peb_tx ON resolution.execution_admission_receipt USING btree (peb_transaction_id);


--
-- Name: idx_execution_claim_active_key; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX idx_execution_claim_active_key ON resolution.execution_claim USING btree (claim_key) WHERE ((recorded_until_dt = 'infinity'::timestamp with time zone) AND (valid_until = 'infinity'::timestamp with time zone));


--
-- Name: idx_execution_claim_attempt; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_execution_claim_attempt ON resolution.execution_claim USING btree (attempt_id) WHERE (attempt_id IS NOT NULL);


--
-- Name: idx_execution_claim_disposition; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_execution_claim_disposition ON resolution.execution_claim USING btree (disposition);


--
-- Name: idx_execution_claim_evidence_active; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX idx_execution_claim_evidence_active ON resolution.execution_claim_evidence USING btree (claim_id, evidence_id, role) WHERE (expired_at IS NULL);


--
-- Name: idx_execution_claim_evidence_claim; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_execution_claim_evidence_claim ON resolution.execution_claim_evidence USING btree (claim_id);


--
-- Name: idx_execution_claim_evidence_evidence; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_execution_claim_evidence_evidence ON resolution.execution_claim_evidence USING btree (evidence_id);


--
-- Name: idx_execution_claim_grant; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_execution_claim_grant ON resolution.execution_claim USING btree (grant_id) WHERE (grant_id IS NOT NULL);


--
-- Name: idx_execution_evidence_active_key; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX idx_execution_evidence_active_key ON resolution.execution_evidence USING btree (evidence_key) WHERE ((recorded_until_dt = 'infinity'::timestamp with time zone) AND (valid_until = 'infinity'::timestamp with time zone));


--
-- Name: idx_execution_evidence_content; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX idx_execution_evidence_content ON resolution.execution_evidence USING btree (source_system, evidence_kind, source_hash);


--
-- Name: idx_execution_evidence_source; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_execution_evidence_source ON resolution.execution_evidence USING btree (source_system, evidence_kind);


--
-- Name: idx_keychain_eval_manifest_target; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_keychain_eval_manifest_target ON resolution.keychain_evaluation_manifest USING btree (target_id, recorded_at);


--
-- Name: idx_keychain_event_outbox_pending; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_keychain_event_outbox_pending ON resolution.keychain_event_outbox USING btree (checkpoint_status, recorded_at) WHERE (checkpoint_status = ANY (ARRAY['pending'::text, 'failed'::text]));


--
-- Name: idx_oq_entity_concept_id; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_oq_entity_concept_id ON resolution.open_question_entity USING btree (asset_concept_id, entity_id);


--
-- Name: idx_oqa_question; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_oqa_question ON resolution.open_question_answer USING btree (question_id);


--
-- Name: idx_oqa_question_role; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_oqa_question_role ON resolution.open_question_answer USING btree (question_id, role);


--
-- Name: idx_resolution_requirement_parent; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_resolution_requirement_parent ON resolution.requirement USING btree (parent_id) WHERE (valid_until = 'infinity'::timestamp with time zone);


--
-- Name: idx_resolution_requirement_status; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_resolution_requirement_status ON resolution.requirement USING btree (status) WHERE (valid_until = 'infinity'::timestamp with time zone);


--
-- Name: idx_resolution_requirement_valid; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_resolution_requirement_valid ON resolution.requirement USING btree (valid_from, valid_until);


--
-- Name: idx_resolution_work_request_business_status; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_resolution_work_request_business_status ON resolution.work_request USING btree (business_status);


--
-- Name: idx_resolution_work_request_legacy_id; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_resolution_work_request_legacy_id ON resolution.work_request USING btree (legacy_id) WHERE (legacy_id IS NOT NULL);


--
-- Name: idx_resolution_work_request_plan_id; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_resolution_work_request_plan_id ON resolution.work_request USING btree (plan_id) WHERE (plan_id IS NOT NULL);


--
-- Name: idx_t24_graph_edge_evidence_edge; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_t24_graph_edge_evidence_edge ON resolution.t24_graph_edge_evidence USING btree (graph_edge_id);


--
-- Name: idx_t24_graph_edge_evidence_endpoint; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_t24_graph_edge_evidence_endpoint ON resolution.t24_graph_edge_evidence USING btree (source_section, source_id, target_section, target_id);


--
-- Name: idx_t24_graph_edge_evidence_migration; Type: INDEX; Schema: resolution; Owner: -
--

CREATE INDEX idx_t24_graph_edge_evidence_migration ON resolution.t24_graph_edge_evidence USING btree (source_migration_id) WHERE (source_migration_id IS NOT NULL);


--
-- Name: idx_work_request_edge_active_pair; Type: INDEX; Schema: resolution; Owner: -
--

CREATE UNIQUE INDEX idx_work_request_edge_active_pair ON resolution.work_request_edge USING btree (parent_work_request_id, child_work_request_id, edge_type) WHERE (valid_until = 'infinity'::timestamp with time zone);


--
-- Name: idx_asset_identity_claim_active_pair; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_asset_identity_claim_active_pair ON semantics.asset_identity_claim USING btree (asset_id, claim_type) WHERE (expired_at IS NULL);


--
-- Name: idx_asset_relation_active_edge; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_asset_relation_active_edge ON semantics.asset_relation USING btree (from_asset_id, to_asset_id, relation_type) WHERE (expired_at IS NULL);


--
-- Name: idx_asset_revision_active_revision_id; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_asset_revision_active_revision_id ON semantics.asset_revision USING btree (revision_id) WHERE (expired_at IS NULL);


--
-- Name: idx_canonical_asset_active_canonical_asset_id; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_canonical_asset_active_canonical_asset_id ON semantics.canonical_asset USING btree (canonical_asset_id) WHERE (expired_at IS NULL);


--
-- Name: idx_evidence_item_active_dedup; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_evidence_item_active_dedup ON semantics.evidence_item USING btree (evidence_type_id, source_hash, public.digest(excerpt, 'sha256'::text)) WHERE ((recorded_until_dt = '9999-12-31 23:59:59+00'::timestamp with time zone) AND (expired_at IS NULL));


--
-- Name: idx_evidence_item_uri; Type: INDEX; Schema: semantics; Owner: -
--

CREATE INDEX idx_evidence_item_uri ON semantics.evidence_item USING btree (uri) WHERE ((recorded_until_dt = '9999-12-31 23:59:59+00'::timestamp with time zone) AND (expired_at IS NULL));


--
-- Name: idx_evidence_item_verification_state; Type: INDEX; Schema: semantics; Owner: -
--

CREATE INDEX idx_evidence_item_verification_state ON semantics.evidence_item USING btree (verification_state) WHERE ((recorded_until_dt = '9999-12-31 23:59:59+00'::timestamp with time zone) AND (expired_at IS NULL));


--
-- Name: idx_evidence_type_active_name; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_evidence_type_active_name ON semantics.evidence_type USING btree (name) WHERE (expired_at IS NULL);


--
-- Name: idx_snapshot_observation_active_pair; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_snapshot_observation_active_pair ON semantics.snapshot_observation USING btree (snapshot_id, representation_id) WHERE (expired_at IS NULL);


--
-- Name: idx_statement_evidence_active; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_statement_evidence_active ON semantics.statement_evidence USING btree (evidence_item_id, statement_type, statement_id, role) WHERE (expired_at IS NULL);


--
-- Name: idx_statement_evidence_by_statement; Type: INDEX; Schema: semantics; Owner: -
--

CREATE INDEX idx_statement_evidence_by_statement ON semantics.statement_evidence USING btree (statement_type, statement_id) WHERE (expired_at IS NULL);


--
-- Name: idx_statement_evidence_proposition_unique; Type: INDEX; Schema: semantics; Owner: -
--

CREATE UNIQUE INDEX idx_statement_evidence_proposition_unique ON semantics.statement_evidence USING btree (evidence_item_id, statement_id) WHERE ((statement_type = 'resolution_proposition'::text) AND (expired_at IS NULL));


--
-- Name: agent_timeclock_clock_in_idx; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX agent_timeclock_clock_in_idx ON tackle.agent_timeclock USING btree (clock_in);


--
-- Name: agent_timeclock_role_idx; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX agent_timeclock_role_idx ON tackle.agent_timeclock USING btree (role);


--
-- Name: agent_timeclock_status_idx; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX agent_timeclock_status_idx ON tackle.agent_timeclock USING btree (status);


--
-- Name: idx_agent_scheduler_due; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_agent_scheduler_due ON tackle.agent_scheduler USING btree (enabled, last_run_at);


--
-- Name: idx_agent_scheduler_task_slug; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_agent_scheduler_task_slug ON tackle.agent_scheduler USING btree (task_slug);


--
-- Name: idx_projection_configs_enabled; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_projection_configs_enabled ON tackle.projection_configs USING btree (enabled);


--
-- Name: idx_projection_configs_name; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_projection_configs_name ON tackle.projection_configs USING btree (name);


--
-- Name: idx_prompts_role; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_prompts_role ON tackle.prompts USING btree (role);


--
-- Name: idx_prompts_slug; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_prompts_slug ON tackle.prompts USING btree (slug);


--
-- Name: idx_role_leases_active_per_role; Type: INDEX; Schema: tackle; Owner: -
--

CREATE UNIQUE INDEX idx_role_leases_active_per_role ON tackle.role_leases USING btree (role) WHERE (status = 'ACTIVE'::text);


--
-- Name: idx_role_leases_active_per_role_channel; Type: INDEX; Schema: tackle; Owner: -
--

CREATE UNIQUE INDEX idx_role_leases_active_per_role_channel ON tackle.role_leases USING btree (role, channel) WHERE (status = 'ACTIVE'::text);


--
-- Name: idx_role_leases_status; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_role_leases_status ON tackle.role_leases USING btree (status, expires_at);


--
-- Name: idx_role_memory_as_of; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_role_memory_as_of ON tackle.role_memory USING btree (role, as_of_dt DESC);


--
-- Name: idx_role_memory_expiration; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_role_memory_expiration ON tackle.role_memory USING btree (role, expiration_dt DESC);


--
-- Name: idx_role_tool_access_mcp_id; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_role_tool_access_mcp_id ON tackle.role_tool_access USING btree (mcp_id);


--
-- Name: idx_role_tool_access_tool_slug; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_role_tool_access_tool_slug ON tackle.role_tool_access USING btree (tool_slug);


--
-- Name: idx_sessions_agent_role; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_sessions_agent_role ON tackle.sessions USING btree (agent_role);


--
-- Name: idx_sessions_created_at; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_sessions_created_at ON tackle.sessions USING btree (created_at DESC);


--
-- Name: idx_system_logs_category; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_system_logs_category ON tackle.system_logs USING btree (category);


--
-- Name: idx_system_logs_level; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_system_logs_level ON tackle.system_logs USING btree (level);


--
-- Name: idx_system_logs_timestamp; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_system_logs_timestamp ON tackle.system_logs USING btree ("timestamp" DESC);


--
-- Name: idx_tackle_session_logs_session_id; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_tackle_session_logs_session_id ON tackle.session_logs USING btree (session_id);


--
-- Name: idx_tasks_prompt; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_tasks_prompt ON tackle.tasks USING btree (prompt_id);


--
-- Name: idx_tasks_role; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_tasks_role ON tackle.tasks USING btree (role);


--
-- Name: idx_tasks_role_active; Type: INDEX; Schema: tackle; Owner: -
--

CREATE INDEX idx_tasks_role_active ON tackle.tasks USING btree (role, active);


--
-- Name: idx_edges_active_pair; Type: INDEX; Schema: vision; Owner: -
--

CREATE UNIQUE INDEX idx_edges_active_pair ON vision.work_request_edges_history USING btree (parent_wr_id, child_wr_id, edge_type) WHERE (recorded_until_dt = '9999-12-31 23:59:59+00'::timestamp with time zone);


--
-- Name: idx_edges_history_child; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_edges_history_child ON vision.work_request_edges_history USING btree (child_wr_id);


--
-- Name: idx_edges_history_parent; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_edges_history_parent ON vision.work_request_edges_history USING btree (parent_wr_id);


--
-- Name: idx_edges_history_type; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_edges_history_type ON vision.work_request_edges_history USING btree (edge_type);


--
-- Name: idx_receipts_plan_sequence; Type: INDEX; Schema: vision; Owner: -
--

CREATE UNIQUE INDEX idx_receipts_plan_sequence ON vision.receipts USING btree (plan_id, sequence);


--
-- Name: idx_vision_ir_content; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_vision_ir_content ON vision.vision_ir_artifacts USING gin (content);


--
-- Name: idx_vision_ir_stage; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_vision_ir_stage ON vision.vision_ir_artifacts USING btree (ir_stage);


--
-- Name: idx_vision_ir_wr_stage_ver; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_vision_ir_wr_stage_ver ON vision.vision_ir_artifacts USING btree (work_request_id, ir_stage, ir_version);


--
-- Name: idx_vision_tickets_open; Type: INDEX; Schema: vision; Owner: -
--

CREATE UNIQUE INDEX idx_vision_tickets_open ON vision.tickets USING btree (plan_id, role) WHERE (status = 'open'::text);


--
-- Name: idx_vision_work_requests_status; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_vision_work_requests_status ON vision.work_requests USING btree (status);


--
-- Name: idx_vision_work_requests_uuid; Type: INDEX; Schema: vision; Owner: -
--

CREATE UNIQUE INDEX idx_vision_work_requests_uuid ON vision.work_requests USING btree (work_request_uuid);


--
-- Name: idx_wr_compile_verdicts_entity_key; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_wr_compile_verdicts_entity_key ON vision.wr_compile_verdicts USING btree (entity_key, created_at DESC);


--
-- Name: idx_wr_compile_verdicts_plan; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_wr_compile_verdicts_plan ON vision.wr_compile_verdicts USING btree (plan_id);


--
-- Name: idx_wr_compile_verdicts_wr_id; Type: INDEX; Schema: vision; Owner: -
--

CREATE INDEX idx_wr_compile_verdicts_wr_id ON vision.wr_compile_verdicts USING btree (wr_id);


--
-- Name: uq_work_requests_wr_id; Type: INDEX; Schema: vision; Owner: -
--

CREATE UNIQUE INDEX uq_work_requests_wr_id ON vision.work_requests USING btree (wr_id);


--
-- Name: idx_wind_events_source; Type: INDEX; Schema: wind; Owner: -
--

CREATE INDEX idx_wind_events_source ON wind.events USING btree (source, created_at);


--
-- Name: idx_wind_events_subject; Type: INDEX; Schema: wind; Owner: -
--

CREATE INDEX idx_wind_events_subject ON wind.events USING btree (subject);


--
-- Name: idx_wind_events_type; Type: INDEX; Schema: wind; Owner: -
--

CREATE INDEX idx_wind_events_type ON wind.events USING btree (event_type);


--
-- Name: idx_wind_events_unconsumed; Type: INDEX; Schema: wind; Owner: -
--

CREATE INDEX idx_wind_events_unconsumed ON wind.events USING btree (created_at) WHERE (consumed_at IS NULL);


--
-- Name: idx_wind_tasks_tackle_task; Type: INDEX; Schema: wind; Owner: -
--

CREATE INDEX idx_wind_tasks_tackle_task ON wind.tasks USING btree (tackle_task_id);


--
-- Name: idx_wind_workflow_instances_dedup; Type: INDEX; Schema: wind; Owner: -
--

CREATE UNIQUE INDEX idx_wind_workflow_instances_dedup ON wind.workflow_instances USING btree (workflow_version_id, dedup_key) WHERE (dedup_key IS NOT NULL);


--
-- Name: work_request_events trg_bridge_to_wind_events; Type: TRIGGER; Schema: conduit; Owner: -
--

CREATE TRIGGER trg_bridge_to_wind_events AFTER INSERT ON conduit.work_request_events FOR EACH ROW EXECUTE FUNCTION wind.trg_bridge_conduit_events();


--
-- Name: work_request_events trg_enforce_state_transition; Type: TRIGGER; Schema: conduit; Owner: -
--

CREATE TRIGGER trg_enforce_state_transition BEFORE INSERT ON conduit.work_request_events FOR EACH ROW EXECUTE FUNCTION conduit.enforce_state_transition();


--
-- Name: work_request_events trg_update_wr_state; Type: TRIGGER; Schema: conduit; Owner: -
--

CREATE TRIGGER trg_update_wr_state AFTER INSERT ON conduit.work_request_events FOR EACH ROW EXECUTE FUNCTION conduit.update_work_request_state();


--
-- Name: attempts trg_attempt_lease_consistency; Type: TRIGGER; Schema: execution; Owner: -
--

CREATE TRIGGER trg_attempt_lease_consistency BEFORE INSERT OR UPDATE ON execution.attempts FOR EACH ROW EXECUTE FUNCTION execution.check_attempt_lease_consistency();


--
-- Name: requests trg_execution_requests_updated_at; Type: TRIGGER; Schema: execution; Owner: -
--

CREATE TRIGGER trg_execution_requests_updated_at BEFORE UPDATE ON execution.requests FOR EACH ROW EXECUTE FUNCTION execution.set_updated_at();


--
-- Name: receipts trg_receipt_governance; Type: TRIGGER; Schema: execution; Owner: -
--

CREATE TRIGGER trg_receipt_governance AFTER INSERT ON execution.receipts FOR EACH ROW EXECUTE FUNCTION execution.receipt_governance_trigger();


--
-- Name: receipts trg_receipts_immutable; Type: TRIGGER; Schema: execution; Owner: -
--

CREATE TRIGGER trg_receipts_immutable BEFORE DELETE OR UPDATE ON execution.receipts FOR EACH ROW EXECUTE FUNCTION execution.receipts_immutable_guard();


--
-- Name: conversation_blocks trg_conversation_blocks_insert; Type: TRIGGER; Schema: nebula; Owner: -
--

CREATE TRIGGER trg_conversation_blocks_insert INSTEAD OF INSERT ON nebula.conversation_blocks FOR EACH ROW EXECUTE FUNCTION nebula.conversation_blocks_insert_trigger();


--
-- Name: conversation_snapshots trg_conversation_snapshots_insert; Type: TRIGGER; Schema: nebula; Owner: -
--

CREATE TRIGGER trg_conversation_snapshots_insert INSTEAD OF INSERT ON nebula.conversation_snapshots FOR EACH ROW EXECUTE FUNCTION nebula.conversation_snapshots_insert_trigger();


--
-- Name: harvests_history trg_harvests_history_auto_segment; Type: TRIGGER; Schema: nebula; Owner: -
--

CREATE TRIGGER trg_harvests_history_auto_segment AFTER INSERT ON nebula.harvests_history FOR EACH ROW WHEN (((new.docklang IS NOT NULL) AND (new.docklang <> '{}'::jsonb) AND (new.docklang ? 'discourse_units'::text))) EXECUTE FUNCTION nebula.harvests_auto_segment_trigger();


--
-- Name: segment_set_members trg_member_expired; Type: TRIGGER; Schema: nebula; Owner: -
--

CREATE TRIGGER trg_member_expired AFTER UPDATE ON nebula.segment_set_members FOR EACH ROW EXECUTE FUNCTION public.notify_member_expired();


--
-- Name: open_questions_history trg_notify_open_question_event; Type: TRIGGER; Schema: nebula; Owner: -
--

CREATE TRIGGER trg_notify_open_question_event AFTER UPDATE ON nebula.open_questions_history FOR EACH ROW EXECUTE FUNCTION nebula.notify_open_question_event();


--
-- Name: roles trg_roles_soft_delete; Type: TRIGGER; Schema: nebula; Owner: -
--

CREATE TRIGGER trg_roles_soft_delete INSTEAD OF DELETE ON nebula.roles FOR EACH ROW EXECUTE FUNCTION nebula.roles_delete_trigger();


--
-- Name: segments_history trg_segment_expired; Type: TRIGGER; Schema: nebula; Owner: -
--

CREATE TRIGGER trg_segment_expired AFTER UPDATE ON nebula.segments_history FOR EACH ROW EXECUTE FUNCTION nebula.notify_segment_expired();


--
-- Name: TRIGGER trg_segment_expired ON segments_history; Type: COMMENT; Schema: nebula; Owner: -
--

COMMENT ON TRIGGER trg_segment_expired ON nebula.segments_history IS 'Emits pg_notify(''segment_expired'', json) when a segment is superseded so substance can invalidate cached segment sets.';


--
-- Name: binding_decision_evidence trg_binding_decision_no_delete; Type: TRIGGER; Schema: peb; Owner: -
--

CREATE TRIGGER trg_binding_decision_no_delete BEFORE DELETE ON peb.binding_decision_evidence FOR EACH ROW EXECUTE FUNCTION peb.forbid_binding_decision_mutation();


--
-- Name: binding_decision_evidence trg_binding_decision_no_update; Type: TRIGGER; Schema: peb; Owner: -
--

CREATE TRIGGER trg_binding_decision_no_update BEFORE UPDATE ON peb.binding_decision_evidence FOR EACH ROW EXECUTE FUNCTION peb.forbid_binding_decision_mutation();


--
-- Name: governance_events trg_notify_governance_event; Type: TRIGGER; Schema: peb; Owner: -
--

CREATE TRIGGER trg_notify_governance_event AFTER INSERT ON peb.governance_events FOR EACH ROW EXECUTE FUNCTION peb.notify_governance_event();


--
-- Name: services trg_registry_service_asset_link; Type: TRIGGER; Schema: registry; Owner: -
--

CREATE TRIGGER trg_registry_service_asset_link BEFORE INSERT ON registry.services FOR EACH ROW EXECUTE FUNCTION semantics.registry_service_asset_link_trigger();


--
-- Name: execution_evidence trg_execution_evidence_immutable; Type: TRIGGER; Schema: resolution; Owner: -
--

CREATE TRIGGER trg_execution_evidence_immutable BEFORE DELETE OR UPDATE ON resolution.execution_evidence FOR EACH ROW EXECUTE FUNCTION resolution.execution_evidence_immutable();


--
-- Name: expression_operand trg_expression_operand_acyclic; Type: TRIGGER; Schema: resolution; Owner: -
--

CREATE TRIGGER trg_expression_operand_acyclic BEFORE INSERT OR UPDATE ON resolution.expression_operand FOR EACH ROW EXECUTE FUNCTION resolution.check_expression_acyclic();


--
-- Name: proposition_frame_value trg_validate_proposition_frame_value; Type: TRIGGER; Schema: resolution; Owner: -
--

CREATE TRIGGER trg_validate_proposition_frame_value BEFORE INSERT OR UPDATE ON resolution.proposition_frame_value FOR EACH ROW EXECUTE FUNCTION resolution.validate_proposition_frame_value();


--
-- Name: verified_statement trg_verified_statement_immutable; Type: TRIGGER; Schema: resolution; Owner: -
--

CREATE TRIGGER trg_verified_statement_immutable BEFORE DELETE OR UPDATE ON resolution.verified_statement FOR EACH ROW EXECUTE FUNCTION resolution.verified_statement_immutable();


--
-- Name: statement_evidence trg_statement_evidence_check_statement; Type: TRIGGER; Schema: semantics; Owner: -
--

CREATE TRIGGER trg_statement_evidence_check_statement BEFORE INSERT OR UPDATE ON semantics.statement_evidence FOR EACH ROW EXECUTE FUNCTION semantics.check_statement_id();


--
-- Name: config_bundle trg_config_bundle_interactive_priority_pin; Type: TRIGGER; Schema: tackle; Owner: -
--

CREATE TRIGGER trg_config_bundle_interactive_priority_pin BEFORE INSERT OR UPDATE ON tackle.config_bundle FOR EACH ROW EXECUTE FUNCTION tackle.config_bundle_interactive_priority_pin();


--
-- Name: config_bundle trg_config_bundle_verified_gate; Type: TRIGGER; Schema: tackle; Owner: -
--

CREATE TRIGGER trg_config_bundle_verified_gate BEFORE INSERT OR UPDATE ON tackle.config_bundle FOR EACH ROW EXECUTE FUNCTION tackle.config_bundle_verified_gate();


--
-- Name: roles trg_tackle_roles_updated_at; Type: TRIGGER; Schema: tackle; Owner: -
--

CREATE TRIGGER trg_tackle_roles_updated_at BEFORE UPDATE ON tackle.roles FOR EACH ROW EXECUTE FUNCTION tackle.set_updated_at();


--
-- Name: artifacts trg_artifacts_view_delete; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_artifacts_view_delete INSTEAD OF DELETE ON vision.artifacts FOR EACH ROW EXECUTE FUNCTION vision.artifacts_view_delete_trigger();


--
-- Name: artifacts trg_artifacts_view_insert; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_artifacts_view_insert INSTEAD OF INSERT ON vision.artifacts FOR EACH ROW EXECUTE FUNCTION vision.artifacts_view_insert_trigger();


--
-- Name: artifacts trg_artifacts_view_update; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_artifacts_view_update INSTEAD OF UPDATE ON vision.artifacts FOR EACH ROW EXECUTE FUNCTION vision.artifacts_view_update_trigger();


--
-- Name: vision_ir_artifacts trg_auto_ir_version; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_auto_ir_version BEFORE INSERT ON vision.vision_ir_artifacts FOR EACH ROW EXECUTE FUNCTION vision.auto_update_vision_ir_artifact();


--
-- Name: branch_artifacts trg_branch_artifacts_view_delete; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_branch_artifacts_view_delete INSTEAD OF DELETE ON vision.branch_artifacts FOR EACH ROW EXECUTE FUNCTION vision.branch_artifacts_view_delete_trigger();


--
-- Name: branch_artifacts trg_branch_artifacts_view_insert; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_branch_artifacts_view_insert INSTEAD OF INSERT ON vision.branch_artifacts FOR EACH ROW EXECUTE FUNCTION vision.branch_artifacts_view_insert_trigger();


--
-- Name: branch_artifacts trg_branch_artifacts_view_update; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_branch_artifacts_view_update INSTEAD OF UPDATE ON vision.branch_artifacts FOR EACH ROW EXECUTE FUNCTION vision.branch_artifacts_view_update_trigger();


--
-- Name: branches trg_branches_view_delete; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_branches_view_delete INSTEAD OF DELETE ON vision.branches FOR EACH ROW EXECUTE FUNCTION vision.branches_view_delete_trigger();


--
-- Name: branches trg_branches_view_insert; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_branches_view_insert INSTEAD OF INSERT ON vision.branches FOR EACH ROW EXECUTE FUNCTION vision.branches_view_insert_trigger();


--
-- Name: branches trg_branches_view_update; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_branches_view_update INSTEAD OF UPDATE ON vision.branches FOR EACH ROW EXECUTE FUNCTION vision.branches_view_update_trigger();


--
-- Name: governance_events trg_governance_events_view_delete; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_governance_events_view_delete INSTEAD OF DELETE ON vision.governance_events FOR EACH ROW EXECUTE FUNCTION vision.governance_events_view_delete_trigger();


--
-- Name: governance_events trg_governance_events_view_insert; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_governance_events_view_insert INSTEAD OF INSERT ON vision.governance_events FOR EACH ROW EXECUTE FUNCTION vision.governance_events_view_insert_trigger();


--
-- Name: governance_events trg_governance_events_view_update; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_governance_events_view_update INSTEAD OF UPDATE ON vision.governance_events FOR EACH ROW EXECUTE FUNCTION vision.governance_events_view_update_trigger();


--
-- Name: lifecycle_events trg_lifecycle_events_view_delete; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_lifecycle_events_view_delete INSTEAD OF DELETE ON vision.lifecycle_events FOR EACH ROW EXECUTE FUNCTION vision.lifecycle_events_view_delete_trigger();


--
-- Name: lifecycle_events trg_lifecycle_events_view_insert; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_lifecycle_events_view_insert INSTEAD OF INSERT ON vision.lifecycle_events FOR EACH ROW EXECUTE FUNCTION vision.lifecycle_events_view_insert_trigger();


--
-- Name: lifecycle_events trg_lifecycle_events_view_update; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_lifecycle_events_view_update INSTEAD OF UPDATE ON vision.lifecycle_events FOR EACH ROW EXECUTE FUNCTION vision.lifecycle_events_view_update_trigger();


--
-- Name: receipt_ingest_records trg_receipt_ingest_records_view_delete; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_receipt_ingest_records_view_delete INSTEAD OF DELETE ON vision.receipt_ingest_records FOR EACH ROW EXECUTE FUNCTION vision.receipt_ingest_records_view_delete_trigger();


--
-- Name: receipt_ingest_records trg_receipt_ingest_records_view_insert; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_receipt_ingest_records_view_insert INSTEAD OF INSERT ON vision.receipt_ingest_records FOR EACH ROW EXECUTE FUNCTION vision.receipt_ingest_records_view_insert_trigger();


--
-- Name: receipt_ingest_records trg_receipt_ingest_records_view_update; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_receipt_ingest_records_view_update INSTEAD OF UPDATE ON vision.receipt_ingest_records FOR EACH ROW EXECUTE FUNCTION vision.receipt_ingest_records_view_update_trigger();


--
-- Name: receipts trg_receipts_assign_sequence; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_receipts_assign_sequence BEFORE INSERT ON vision.receipts FOR EACH ROW EXECUTE FUNCTION vision.receipts_assign_sequence();


--
-- Name: work_requests trg_vision_work_requests_asset; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_vision_work_requests_asset BEFORE INSERT ON vision.work_requests FOR EACH ROW EXECUTE FUNCTION semantics.vision_work_request_asset_trigger();


--
-- Name: work_request_edges trg_work_request_edges_delete; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_work_request_edges_delete INSTEAD OF DELETE ON vision.work_request_edges FOR EACH ROW EXECUTE FUNCTION vision.work_request_edges_delete_trigger();


--
-- Name: work_request_edges trg_work_request_edges_insert; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_work_request_edges_insert INSTEAD OF INSERT ON vision.work_request_edges FOR EACH ROW EXECUTE FUNCTION vision.work_request_edges_insert_trigger();


--
-- Name: work_request_edges trg_work_request_edges_update; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_work_request_edges_update INSTEAD OF UPDATE ON vision.work_request_edges FOR EACH ROW EXECUTE FUNCTION vision.work_request_edges_update_trigger();


--
-- Name: wr_compile_verdicts trg_wr_compile_verdicts_immutable; Type: TRIGGER; Schema: vision; Owner: -
--

CREATE TRIGGER trg_wr_compile_verdicts_immutable BEFORE DELETE OR UPDATE ON vision.wr_compile_verdicts FOR EACH ROW EXECUTE FUNCTION vision.wr_compile_verdicts_immutable();


--
-- Name: workflow_instances trg_bridge_instance_to_cascade; Type: TRIGGER; Schema: wind; Owner: -
--

CREATE TRIGGER trg_bridge_instance_to_cascade AFTER UPDATE OF status ON wind.workflow_instances FOR EACH ROW WHEN ((((old.status)::text = 'ACTIVE'::text) AND ((new.status)::text = ANY ((ARRAY['COMPLETED'::character varying, 'FAILED'::character varying])::text[])))) EXECUTE FUNCTION wind.bridge_instance_to_cascade();


--
-- Name: tickets trg_bridge_ticket_to_cascade; Type: TRIGGER; Schema: wind; Owner: -
--

CREATE TRIGGER trg_bridge_ticket_to_cascade AFTER UPDATE OF status ON wind.tickets FOR EACH ROW WHEN ((((old.status)::text IS DISTINCT FROM (new.status)::text) AND ((new.status)::text = ANY ((ARRAY['COMPLETED'::character varying, 'CANCELLED'::character varying])::text[])))) EXECUTE FUNCTION wind.bridge_ticket_to_cascade();


--
-- Name: work_requests work_requests_asset_id_fkey; Type: FK CONSTRAINT; Schema: conduit; Owner: -
--

ALTER TABLE ONLY conduit.work_requests
    ADD CONSTRAINT work_requests_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: attempts attempts_lease_id_fkey; Type: FK CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.attempts
    ADD CONSTRAINT attempts_lease_id_fkey FOREIGN KEY (lease_id) REFERENCES execution.leases(id);


--
-- Name: attempts attempts_request_id_fkey; Type: FK CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.attempts
    ADD CONSTRAINT attempts_request_id_fkey FOREIGN KEY (request_id) REFERENCES execution.requests(id);


--
-- Name: leases leases_request_id_fkey; Type: FK CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.leases
    ADD CONSTRAINT leases_request_id_fkey FOREIGN KEY (request_id) REFERENCES execution.requests(id);


--
-- Name: receipts receipts_attempt_id_fkey; Type: FK CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.receipts
    ADD CONSTRAINT receipts_attempt_id_fkey FOREIGN KEY (attempt_id) REFERENCES execution.attempts(id);


--
-- Name: receipts receipts_request_id_fkey; Type: FK CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.receipts
    ADD CONSTRAINT receipts_request_id_fkey FOREIGN KEY (request_id) REFERENCES execution.requests(id);


--
-- Name: requests requests_source_wr_id_fkey; Type: FK CONSTRAINT; Schema: execution; Owner: -
--

ALTER TABLE ONLY execution.requests
    ADD CONSTRAINT requests_source_wr_id_fkey FOREIGN KEY (source_wr_id) REFERENCES nebula.work_requests_history(id) ON DELETE SET NULL;


--
-- Name: agenda_item_questions agenda_item_questions_agenda_item_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.agenda_item_questions
    ADD CONSTRAINT agenda_item_questions_agenda_item_id_fkey FOREIGN KEY (agenda_item_id) REFERENCES nebula.agenda_items_history(id) ON DELETE CASCADE;


--
-- Name: agenda_item_questions agenda_item_questions_open_question_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.agenda_item_questions
    ADD CONSTRAINT agenda_item_questions_open_question_id_fkey FOREIGN KEY (open_question_id) REFERENCES nebula.open_questions_history(id) ON DELETE CASCADE;


--
-- Name: agenda_items_history agenda_items_agenda_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.agenda_items_history
    ADD CONSTRAINT agenda_items_agenda_id_fkey FOREIGN KEY (agenda_id) REFERENCES nebula.agendas_history(id) ON DELETE CASCADE;


--
-- Name: deliberation_participants deliberation_participants_open_question_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.deliberation_participants
    ADD CONSTRAINT deliberation_participants_open_question_id_fkey FOREIGN KEY (open_question_id) REFERENCES nebula.open_questions_history(id) ON DELETE CASCADE;


--
-- Name: features_history features_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.features_history
    ADD CONSTRAINT features_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: work_requests_history fk_work_requests_plan; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.work_requests_history
    ADD CONSTRAINT fk_work_requests_plan FOREIGN KEY (plan_id) REFERENCES nebula.implementation_plans_history(plan_number);


--
-- Name: work_requests_history fk_work_requests_specification; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.work_requests_history
    ADD CONSTRAINT fk_work_requests_specification FOREIGN KEY (source_specification_id) REFERENCES nebula.specifications_history(id);


--
-- Name: harvest_candidates_history harvest_candidates_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.harvest_candidates_history
    ADD CONSTRAINT harvest_candidates_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: harvests_history harvests_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.harvests_history
    ADD CONSTRAINT harvests_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: implementation_plans_history implementation_plans_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.implementation_plans_history
    ADD CONSTRAINT implementation_plans_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: open_question_answers_history open_question_answers_question_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.open_question_answers_history
    ADD CONSTRAINT open_question_answers_question_id_fkey FOREIGN KEY (question_id) REFERENCES nebula.open_questions_history(id) ON DELETE CASCADE;


--
-- Name: open_questions_history open_questions_candidate_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.open_questions_history
    ADD CONSTRAINT open_questions_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES nebula.harvest_candidates_history(id);


--
-- Name: requirements_history requirements_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.requirements_history
    ADD CONSTRAINT requirements_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: spec_requirements spec_requirements_spec_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.spec_requirements
    ADD CONSTRAINT spec_requirements_spec_id_fkey FOREIGN KEY (spec_id) REFERENCES nebula.spec_documents_history(id) ON DELETE CASCADE;


--
-- Name: subsystems_history subsystems_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.subsystems_history
    ADD CONSTRAINT subsystems_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: systems_history systems_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.systems_history
    ADD CONSTRAINT systems_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: work_requests_history work_requests_history_asset_id_fkey; Type: FK CONSTRAINT; Schema: nebula; Owner: -
--

ALTER TABLE ONLY nebula.work_requests_history
    ADD CONSTRAINT work_requests_history_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: services fk2fes77vwb94ts6y42h468sim9; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.services
    ADD CONSTRAINT fk2fes77vwb94ts6y42h468sim9 FOREIGN KEY (framework_id) REFERENCES registry.frameworks(id);


--
-- Name: deployments fk31ghp39ky3m6uv4dwrp9y8hom; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.deployments
    ADD CONSTRAINT fk31ghp39ky3m6uv4dwrp9y8hom FOREIGN KEY (environment_id) REFERENCES registry.environment_type(id);


--
-- Name: services fk4iybqudd280va8uljo00pf91b; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.services
    ADD CONSTRAINT fk4iybqudd280va8uljo00pf91b FOREIGN KEY (parent_service_id) REFERENCES registry.services(id);


--
-- Name: graph_view_connections fk4qidiqhl0xrv9j84ihh2elqlk; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.graph_view_connections
    ADD CONSTRAINT fk4qidiqhl0xrv9j84ihh2elqlk FOREIGN KEY (graph_view_id) REFERENCES registry.graph_views(id);


--
-- Name: frameworks fk7w2ku9iv21spgvkwqltnpr7ep; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.frameworks
    ADD CONSTRAINT fk7w2ku9iv21spgvkwqltnpr7ep FOREIGN KEY (category_id) REFERENCES registry.framework_type(id);


--
-- Name: system_services fk_system_services_service; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_services
    ADD CONSTRAINT fk_system_services_service FOREIGN KEY (service_id) REFERENCES registry.services(id);


--
-- Name: system_services fk_system_services_system; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.system_services
    ADD CONSTRAINT fk_system_services_system FOREIGN KEY (system_id) REFERENCES registry.systems(id);


--
-- Name: systems fk_systems_type; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.systems
    ADD CONSTRAINT fk_systems_type FOREIGN KEY (system_type_id) REFERENCES registry.system_type(id);


--
-- Name: service_backends fka3ilddabb3ujwg594v7b3q30e; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_backends
    ADD CONSTRAINT fka3ilddabb3ujwg594v7b3q30e FOREIGN KEY (service_deployment_id) REFERENCES registry.deployments(id);


--
-- Name: services fka79h7xirkkbbb0u98d7nl8dvt; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.services
    ADD CONSTRAINT fka79h7xirkkbbb0u98d7nl8dvt FOREIGN KEY (service_type_id) REFERENCES registry.service_type(id);


--
-- Name: libraries fkad57pvl3x0ut35gfv5byv7f1t; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.libraries
    ADD CONSTRAINT fkad57pvl3x0ut35gfv5byv7f1t FOREIGN KEY (category_id) REFERENCES registry.library_type(id);


--
-- Name: services fkawln2ig0mrcoa96mb0q17uxbu; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.services
    ADD CONSTRAINT fkawln2ig0mrcoa96mb0q17uxbu FOREIGN KEY (component_override_id) REFERENCES registry.visual_components(id);


--
-- Name: servers fkc2uk08w7lh4f2s33xk8doi2g5; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.servers
    ADD CONSTRAINT fkc2uk08w7lh4f2s33xk8doi2g5 FOREIGN KEY (operating_system_id) REFERENCES registry.operating_systems(id);


--
-- Name: graph_view_positions fkdv025otjvrpen730g70w8wrx4; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.graph_view_positions
    ADD CONSTRAINT fkdv025otjvrpen730g70w8wrx4 FOREIGN KEY (graph_view_id) REFERENCES registry.graph_views(id);


--
-- Name: deployments fke579yt6en9uf2cejowaita4cf; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.deployments
    ADD CONSTRAINT fke579yt6en9uf2cejowaita4cf FOREIGN KEY (host_id) REFERENCES registry.servers(id);


--
-- Name: service_backends fkk8tuspc4vxrvqy1q1gwal62g6; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_backends
    ADD CONSTRAINT fkk8tuspc4vxrvqy1q1gwal62g6 FOREIGN KEY (backend_deployment_id) REFERENCES registry.deployments(id);


--
-- Name: deployments fkm614maqtnw25mj3ta6q7syty5; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.deployments
    ADD CONSTRAINT fkm614maqtnw25mj3ta6q7syty5 FOREIGN KEY (service_id) REFERENCES registry.services(id);


--
-- Name: service_configs fkmj6v9bwaqk1bif7o856tf1utu; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_configs
    ADD CONSTRAINT fkmj6v9bwaqk1bif7o856tf1utu FOREIGN KEY (service_id) REFERENCES registry.services(id);


--
-- Name: libraries fkmp8xded7o3hksi9dc8bcecuqe; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.libraries
    ADD CONSTRAINT fkmp8xded7o3hksi9dc8bcecuqe FOREIGN KEY (language_id) REFERENCES registry.languages(id);


--
-- Name: service_type fkmujidjp4lxq6rhsdyebtrnn14; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_type
    ADD CONSTRAINT fkmujidjp4lxq6rhsdyebtrnn14 FOREIGN KEY (default_component_id) REFERENCES registry.visual_components(id);


--
-- Name: frameworks fknaaiorasjp1o3ehlhbx0j29on; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.frameworks
    ADD CONSTRAINT fknaaiorasjp1o3ehlhbx0j29on FOREIGN KEY (vendor_id) REFERENCES registry.vendors(id);


--
-- Name: service_dependencies fkovopbib4xy8ynrv0o6lt8e0gt; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_dependencies
    ADD CONSTRAINT fkovopbib4xy8ynrv0o6lt8e0gt FOREIGN KEY (service_id) REFERENCES registry.services(id);


--
-- Name: service_dependencies fkqp5nm5w4lhj6ol7ycdol2x4tn; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.service_dependencies
    ADD CONSTRAINT fkqp5nm5w4lhj6ol7ycdol2x4tn FOREIGN KEY (target_service_id) REFERENCES registry.services(id);


--
-- Name: servers fkqtoachyo5mh9nqamnu2o4khf8; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.servers
    ADD CONSTRAINT fkqtoachyo5mh9nqamnu2o4khf8 FOREIGN KEY (server_type_id) REFERENCES registry.server_type(id);


--
-- Name: frameworks fkqy32p17da1r14yq2ir757adnu; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.frameworks
    ADD CONSTRAINT fkqy32p17da1r14yq2ir757adnu FOREIGN KEY (language_id) REFERENCES registry.languages(id);


--
-- Name: servers fkt8mu0jhprog6qowm9e8fl7jt5; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.servers
    ADD CONSTRAINT fkt8mu0jhprog6qowm9e8fl7jt5 FOREIGN KEY (environment_type_id) REFERENCES registry.environment_type(id);


--
-- Name: services services_asset_id_fkey; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.services
    ADD CONSTRAINT services_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: systems systems_asset_id_fkey; Type: FK CONSTRAINT; Schema: registry; Owner: -
--

ALTER TABLE ONLY registry.systems
    ADD CONSTRAINT systems_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: assertion_evaluation assertion_evaluation_proposition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.assertion_evaluation
    ADD CONSTRAINT assertion_evaluation_proposition_id_fkey FOREIGN KEY (proposition_id) REFERENCES resolution.proposition(id);


--
-- Name: assertion_evaluation assertion_evaluation_rule_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.assertion_evaluation
    ADD CONSTRAINT assertion_evaluation_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES resolution.rule(id);


--
-- Name: assessment assessment_observation_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.assessment
    ADD CONSTRAINT assessment_observation_id_fkey FOREIGN KEY (observation_id) REFERENCES resolution.observation(id);


--
-- Name: binding_resolution_transition binding_resolution_transition_decision_evidence_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.binding_resolution_transition
    ADD CONSTRAINT binding_resolution_transition_decision_evidence_id_fkey FOREIGN KEY (decision_evidence_id) REFERENCES peb.binding_decision_evidence(id);


--
-- Name: candidate candidate_asset_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.candidate
    ADD CONSTRAINT candidate_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES resolution.canonical_asset(id);


--
-- Name: candidate candidate_harvest_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.candidate
    ADD CONSTRAINT candidate_harvest_id_fkey FOREIGN KEY (harvest_id) REFERENCES resolution.harvest(id);


--
-- Name: candidate_segment_set candidate_segment_set_candidate_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.candidate_segment_set
    ADD CONSTRAINT candidate_segment_set_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES resolution.candidate(id);


--
-- Name: candidate_source_chunk candidate_source_chunk_candidate_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.candidate_source_chunk
    ADD CONSTRAINT candidate_source_chunk_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES resolution.candidate(id);


--
-- Name: concept_attribute_binding concept_attribute_binding_attribute_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute_binding
    ADD CONSTRAINT concept_attribute_binding_attribute_id_fkey FOREIGN KEY (attribute_id) REFERENCES resolution.concept_attribute(id);


--
-- Name: concept_attribute concept_attribute_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute
    ADD CONSTRAINT concept_attribute_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES resolution.concept(id);


--
-- Name: concept_attribute_value concept_attribute_value_attribute_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_attribute_value
    ADD CONSTRAINT concept_attribute_value_attribute_id_fkey FOREIGN KEY (attribute_id) REFERENCES resolution.concept_attribute(id);


--
-- Name: concept_relationship_binding concept_relationship_binding_concept_relationship_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_relationship_binding
    ADD CONSTRAINT concept_relationship_binding_concept_relationship_id_fkey FOREIGN KEY (concept_relationship_id) REFERENCES resolution.concept_relationship(id);


--
-- Name: concept_relationship concept_relationship_from_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_relationship
    ADD CONSTRAINT concept_relationship_from_concept_id_fkey FOREIGN KEY (from_concept_id) REFERENCES resolution.concept(id);


--
-- Name: concept_relationship concept_relationship_to_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_relationship
    ADD CONSTRAINT concept_relationship_to_concept_id_fkey FOREIGN KEY (to_concept_id) REFERENCES resolution.concept(id);


--
-- Name: concept_state_transition concept_state_transition_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_state_transition
    ADD CONSTRAINT concept_state_transition_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES resolution.concept(id);


--
-- Name: concept_state_transition concept_state_transition_from_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_state_transition
    ADD CONSTRAINT concept_state_transition_from_value_id_fkey FOREIGN KEY (from_value_id) REFERENCES resolution.concept_attribute_value(id);


--
-- Name: concept_state_transition concept_state_transition_to_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.concept_state_transition
    ADD CONSTRAINT concept_state_transition_to_value_id_fkey FOREIGN KEY (to_value_id) REFERENCES resolution.concept_attribute_value(id);


--
-- Name: consumer_operation consumer_operation_representation_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.consumer_operation
    ADD CONSTRAINT consumer_operation_representation_id_fkey FOREIGN KEY (representation_id) REFERENCES resolution.representation(id);


--
-- Name: execution_admission_receipt execution_admission_receipt_claim_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_admission_receipt
    ADD CONSTRAINT execution_admission_receipt_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES resolution.execution_claim(id);


--
-- Name: execution_admission_receipt execution_admission_receipt_evidence_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_admission_receipt
    ADD CONSTRAINT execution_admission_receipt_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES resolution.execution_evidence(id);


--
-- Name: execution_claim_evidence execution_claim_evidence_claim_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_claim_evidence
    ADD CONSTRAINT execution_claim_evidence_claim_id_fkey FOREIGN KEY (claim_id) REFERENCES resolution.execution_claim(id);


--
-- Name: execution_claim_evidence execution_claim_evidence_evidence_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_claim_evidence
    ADD CONSTRAINT execution_claim_evidence_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES resolution.execution_evidence(id);


--
-- Name: execution_claim execution_claim_proposition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.execution_claim
    ADD CONSTRAINT execution_claim_proposition_id_fkey FOREIGN KEY (proposition_id) REFERENCES resolution.proposition(id);


--
-- Name: expression expression_attribute_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.expression
    ADD CONSTRAINT expression_attribute_id_fkey FOREIGN KEY (attribute_id) REFERENCES resolution.concept_attribute(id);


--
-- Name: expression expression_concept_relationship_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.expression
    ADD CONSTRAINT expression_concept_relationship_id_fkey FOREIGN KEY (concept_relationship_id) REFERENCES resolution.concept_relationship(id);


--
-- Name: expression_operand expression_operand_child_expression_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.expression_operand
    ADD CONSTRAINT expression_operand_child_expression_id_fkey FOREIGN KEY (child_expression_id) REFERENCES resolution.expression(id);


--
-- Name: expression_operand expression_operand_parent_expression_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.expression_operand
    ADD CONSTRAINT expression_operand_parent_expression_id_fkey FOREIGN KEY (parent_expression_id) REFERENCES resolution.expression(id);


--
-- Name: expression expression_referenced_proposition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.expression
    ADD CONSTRAINT expression_referenced_proposition_id_fkey FOREIGN KEY (referenced_proposition_id) REFERENCES resolution.proposition(id);


--
-- Name: frame_dimension_meaning frame_dimension_meaning_dimension_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_meaning
    ADD CONSTRAINT frame_dimension_meaning_dimension_id_fkey FOREIGN KEY (dimension_id) REFERENCES resolution.frame_dimension(id);


--
-- Name: frame_dimension_meaning frame_dimension_meaning_frame_dimension_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_meaning
    ADD CONSTRAINT frame_dimension_meaning_frame_dimension_value_id_fkey FOREIGN KEY (frame_dimension_value_id) REFERENCES resolution.frame_dimension_value(id);


--
-- Name: frame_dimension_meaning frame_dimension_meaning_proposition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_meaning
    ADD CONSTRAINT frame_dimension_meaning_proposition_id_fkey FOREIGN KEY (proposition_id) REFERENCES resolution.proposition(id);


--
-- Name: frame_dimension_value frame_dimension_value_dimension_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.frame_dimension_value
    ADD CONSTRAINT frame_dimension_value_dimension_id_fkey FOREIGN KEY (dimension_id) REFERENCES resolution.frame_dimension(id);


--
-- Name: harvest harvest_asset_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.harvest
    ADD CONSTRAINT harvest_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES resolution.canonical_asset(id);


--
-- Name: identity_strategy identity_strategy_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.identity_strategy
    ADD CONSTRAINT identity_strategy_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES resolution.concept(id);


--
-- Name: implementation_plan implementation_plan_asset_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.implementation_plan
    ADD CONSTRAINT implementation_plan_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES resolution.canonical_asset(id);


--
-- Name: implementation_plan implementation_plan_requirement_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.implementation_plan
    ADD CONSTRAINT implementation_plan_requirement_id_fkey FOREIGN KEY (requirement_id) REFERENCES resolution.requirement(id);


--
-- Name: implementation_plan implementation_plan_specification_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.implementation_plan
    ADD CONSTRAINT implementation_plan_specification_id_fkey FOREIGN KEY (specification_id) REFERENCES resolution.specification(id);


--
-- Name: observation observation_asset_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.observation
    ADD CONSTRAINT observation_asset_concept_id_fkey FOREIGN KEY (asset_concept_id) REFERENCES resolution.concept(id);


--
-- Name: observation_source_chunk observation_source_chunk_observation_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.observation_source_chunk
    ADD CONSTRAINT observation_source_chunk_observation_id_fkey FOREIGN KEY (observation_id) REFERENCES resolution.observation(id);


--
-- Name: open_question_answer open_question_answer_question_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question_answer
    ADD CONSTRAINT open_question_answer_question_id_fkey FOREIGN KEY (question_id) REFERENCES resolution.open_question(id) ON DELETE CASCADE;


--
-- Name: open_question open_question_assessment_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question
    ADD CONSTRAINT open_question_assessment_id_fkey FOREIGN KEY (assessment_id) REFERENCES resolution.assessment(id);


--
-- Name: open_question open_question_category_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question
    ADD CONSTRAINT open_question_category_value_id_fkey FOREIGN KEY (category_value_id) REFERENCES resolution.concept_attribute_value(id);


--
-- Name: open_question_entity open_question_entity_asset_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question_entity
    ADD CONSTRAINT open_question_entity_asset_concept_id_fkey FOREIGN KEY (asset_concept_id) REFERENCES resolution.concept(id);


--
-- Name: open_question_entity open_question_entity_open_question_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question_entity
    ADD CONSTRAINT open_question_entity_open_question_id_fkey FOREIGN KEY (open_question_id) REFERENCES resolution.open_question(id) ON DELETE CASCADE;


--
-- Name: open_question open_question_status_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.open_question
    ADD CONSTRAINT open_question_status_value_id_fkey FOREIGN KEY (status_value_id) REFERENCES resolution.concept_attribute_value(id);


--
-- Name: proposition_assertion proposition_assertion_proposition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_assertion
    ADD CONSTRAINT proposition_assertion_proposition_id_fkey FOREIGN KEY (proposition_id) REFERENCES resolution.proposition(id);


--
-- Name: proposition_assertion proposition_assertion_rule_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_assertion
    ADD CONSTRAINT proposition_assertion_rule_id_fkey FOREIGN KEY (rule_id) REFERENCES resolution.rule(id);


--
-- Name: proposition proposition_asset_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition
    ADD CONSTRAINT proposition_asset_concept_id_fkey FOREIGN KEY (asset_concept_id) REFERENCES resolution.concept(id);


--
-- Name: proposition_comparison proposition_comparison_proposition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_comparison
    ADD CONSTRAINT proposition_comparison_proposition_id_fkey FOREIGN KEY (proposition_id) REFERENCES resolution.proposition(id);


--
-- Name: proposition_comparison proposition_comparison_representation_comparison_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_comparison
    ADD CONSTRAINT proposition_comparison_representation_comparison_id_fkey FOREIGN KEY (representation_comparison_id) REFERENCES resolution.representation_comparison(id);


--
-- Name: proposition proposition_disposition_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition
    ADD CONSTRAINT proposition_disposition_value_id_fkey FOREIGN KEY (disposition_value_id) REFERENCES resolution.concept_attribute_value(id);


--
-- Name: proposition_frame_value proposition_frame_value_dimension_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_frame_value
    ADD CONSTRAINT proposition_frame_value_dimension_id_fkey FOREIGN KEY (dimension_id) REFERENCES resolution.frame_dimension(id);


--
-- Name: proposition_frame_value proposition_frame_value_proposition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_frame_value
    ADD CONSTRAINT proposition_frame_value_proposition_id_fkey FOREIGN KEY (proposition_id) REFERENCES resolution.proposition(id);


--
-- Name: proposition_frame_value proposition_frame_value_reference_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition_frame_value
    ADD CONSTRAINT proposition_frame_value_reference_value_id_fkey FOREIGN KEY (reference_value_id) REFERENCES resolution.frame_dimension_value(id);


--
-- Name: proposition proposition_grounding_status_value_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition
    ADD CONSTRAINT proposition_grounding_status_value_id_fkey FOREIGN KEY (grounding_status_value_id) REFERENCES resolution.concept_attribute_value(id);


--
-- Name: proposition proposition_semantic_type_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.proposition
    ADD CONSTRAINT proposition_semantic_type_id_fkey FOREIGN KEY (semantic_type_id) REFERENCES resolution.semantic_type(id);


--
-- Name: representation_comparison representation_comparison_representation_relationship_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_comparison
    ADD CONSTRAINT representation_comparison_representation_relationship_id_fkey FOREIGN KEY (representation_relationship_id) REFERENCES resolution.representation_relationship(id);


--
-- Name: representation representation_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation
    ADD CONSTRAINT representation_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES resolution.concept(id);


--
-- Name: representation_identity representation_identity_identity_strategy_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_identity
    ADD CONSTRAINT representation_identity_identity_strategy_id_fkey FOREIGN KEY (identity_strategy_id) REFERENCES resolution.identity_strategy(id);


--
-- Name: representation_identity representation_identity_representation_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_identity
    ADD CONSTRAINT representation_identity_representation_id_fkey FOREIGN KEY (representation_id) REFERENCES resolution.representation(id);


--
-- Name: representation representation_owning_subsystem_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation
    ADD CONSTRAINT representation_owning_subsystem_id_fkey FOREIGN KEY (owning_subsystem_id) REFERENCES resolution.owning_subsystem(id);


--
-- Name: representation_relationship representation_relationship_from_representation_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_relationship
    ADD CONSTRAINT representation_relationship_from_representation_id_fkey FOREIGN KEY (from_representation_id) REFERENCES resolution.representation(id);


--
-- Name: representation_relationship representation_relationship_to_representation_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.representation_relationship
    ADD CONSTRAINT representation_relationship_to_representation_id_fkey FOREIGN KEY (to_representation_id) REFERENCES resolution.representation(id);


--
-- Name: requirement requirement_asset_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.requirement
    ADD CONSTRAINT requirement_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES resolution.canonical_asset(id);


--
-- Name: requirement requirement_candidate_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.requirement
    ADD CONSTRAINT requirement_candidate_id_fkey FOREIGN KEY (candidate_id) REFERENCES resolution.candidate(id);


--
-- Name: requirement requirement_parent_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.requirement
    ADD CONSTRAINT requirement_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES resolution.requirement(id);


--
-- Name: requirement_segment_set requirement_segment_set_requirement_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.requirement_segment_set
    ADD CONSTRAINT requirement_segment_set_requirement_id_fkey FOREIGN KEY (requirement_id) REFERENCES resolution.requirement(id);


--
-- Name: requirement requirement_sol_ir_expression_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.requirement
    ADD CONSTRAINT requirement_sol_ir_expression_id_fkey FOREIGN KEY (sol_ir_expression_id) REFERENCES resolution.expression(id);


--
-- Name: rule rule_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.rule
    ADD CONSTRAINT rule_concept_id_fkey FOREIGN KEY (concept_id) REFERENCES resolution.concept(id);


--
-- Name: rule rule_concept_relationship_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.rule
    ADD CONSTRAINT rule_concept_relationship_id_fkey FOREIGN KEY (concept_relationship_id) REFERENCES resolution.concept_relationship(id);


--
-- Name: rule rule_expression_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.rule
    ADD CONSTRAINT rule_expression_id_fkey FOREIGN KEY (expression_id) REFERENCES resolution.expression(id);


--
-- Name: rule rule_representation_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.rule
    ADD CONSTRAINT rule_representation_id_fkey FOREIGN KEY (representation_id) REFERENCES resolution.representation(id);


--
-- Name: rule rule_state_transition_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.rule
    ADD CONSTRAINT rule_state_transition_id_fkey FOREIGN KEY (state_transition_id) REFERENCES resolution.concept_state_transition(id);


--
-- Name: semantic_type_required_dimension semantic_type_required_dimension_dimension_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.semantic_type_required_dimension
    ADD CONSTRAINT semantic_type_required_dimension_dimension_id_fkey FOREIGN KEY (dimension_id) REFERENCES resolution.frame_dimension(id);


--
-- Name: semantic_type_required_dimension semantic_type_required_dimension_semantic_type_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.semantic_type_required_dimension
    ADD CONSTRAINT semantic_type_required_dimension_semantic_type_id_fkey FOREIGN KEY (semantic_type_id) REFERENCES resolution.semantic_type(id);


--
-- Name: specification specification_asset_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.specification
    ADD CONSTRAINT specification_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES resolution.canonical_asset(id);


--
-- Name: specification_lineage specification_lineage_derived_from_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.specification_lineage
    ADD CONSTRAINT specification_lineage_derived_from_id_fkey FOREIGN KEY (derived_from_id) REFERENCES resolution.specification(id);


--
-- Name: specification_lineage specification_lineage_specification_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.specification_lineage
    ADD CONSTRAINT specification_lineage_specification_id_fkey FOREIGN KEY (specification_id) REFERENCES resolution.specification(id);


--
-- Name: specification specification_requirement_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.specification
    ADD CONSTRAINT specification_requirement_id_fkey FOREIGN KEY (requirement_id) REFERENCES resolution.requirement(id);


--
-- Name: specification specification_superseded_by_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.specification
    ADD CONSTRAINT specification_superseded_by_fkey FOREIGN KEY (superseded_by) REFERENCES resolution.specification(id);


--
-- Name: t24_graph_edge_evidence t24_graph_edge_evidence_evidence_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.t24_graph_edge_evidence
    ADD CONSTRAINT t24_graph_edge_evidence_evidence_id_fkey FOREIGN KEY (evidence_id) REFERENCES resolution.execution_evidence(id);


--
-- Name: verified_statement verified_statement_answer_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.verified_statement
    ADD CONSTRAINT verified_statement_answer_id_fkey FOREIGN KEY (answer_id) REFERENCES resolution.open_question_answer(id);


--
-- Name: verified_statement verified_statement_asset_concept_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.verified_statement
    ADD CONSTRAINT verified_statement_asset_concept_id_fkey FOREIGN KEY (asset_concept_id) REFERENCES resolution.concept(id);


--
-- Name: verified_statement verified_statement_expression_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.verified_statement
    ADD CONSTRAINT verified_statement_expression_id_fkey FOREIGN KEY (expression_id) REFERENCES resolution.expression(id);


--
-- Name: work_request work_request_asset_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request
    ADD CONSTRAINT work_request_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES resolution.canonical_asset(id);


--
-- Name: work_request_edge work_request_edge_child_work_request_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request_edge
    ADD CONSTRAINT work_request_edge_child_work_request_id_fkey FOREIGN KEY (child_work_request_id) REFERENCES resolution.work_request(id);


--
-- Name: work_request_edge work_request_edge_parent_work_request_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request_edge
    ADD CONSTRAINT work_request_edge_parent_work_request_id_fkey FOREIGN KEY (parent_work_request_id) REFERENCES resolution.work_request(id);


--
-- Name: work_request work_request_plan_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request
    ADD CONSTRAINT work_request_plan_id_fkey FOREIGN KEY (plan_id) REFERENCES resolution.implementation_plan(plan_number);


--
-- Name: work_request work_request_source_requirement_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request
    ADD CONSTRAINT work_request_source_requirement_id_fkey FOREIGN KEY (source_requirement_id) REFERENCES resolution.requirement(id);


--
-- Name: work_request work_request_source_specification_id_fkey; Type: FK CONSTRAINT; Schema: resolution; Owner: -
--

ALTER TABLE ONLY resolution.work_request
    ADD CONSTRAINT work_request_source_specification_id_fkey FOREIGN KEY (source_specification_id) REFERENCES resolution.specification(id);


--
-- Name: asset_identity_claim asset_identity_claim_asset_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_identity_claim
    ADD CONSTRAINT asset_identity_claim_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: asset_identity_claim asset_identity_claim_candidate_asset_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_identity_claim
    ADD CONSTRAINT asset_identity_claim_candidate_asset_id_fkey FOREIGN KEY (candidate_asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: asset_relation asset_relation_from_asset_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_relation
    ADD CONSTRAINT asset_relation_from_asset_id_fkey FOREIGN KEY (from_asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: asset_relation asset_relation_to_asset_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_relation
    ADD CONSTRAINT asset_relation_to_asset_id_fkey FOREIGN KEY (to_asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: asset_revision asset_revision_asset_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_revision
    ADD CONSTRAINT asset_revision_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: asset_revision asset_revision_parent_revision_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.asset_revision
    ADD CONSTRAINT asset_revision_parent_revision_id_fkey FOREIGN KEY (parent_revision_id) REFERENCES semantics.asset_revision(id);


--
-- Name: drift_finding drift_finding_observation_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.drift_finding
    ADD CONSTRAINT drift_finding_observation_id_fkey FOREIGN KEY (observation_id) REFERENCES semantics.snapshot_observation(id);


--
-- Name: evidence_item evidence_item_evidence_type_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.evidence_item
    ADD CONSTRAINT evidence_item_evidence_type_id_fkey FOREIGN KEY (evidence_type_id) REFERENCES semantics.evidence_type(id);


--
-- Name: evidence_item evidence_item_source_observation_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.evidence_item
    ADD CONSTRAINT evidence_item_source_observation_id_fkey FOREIGN KEY (source_observation_id) REFERENCES semantics.source_observation(id);


--
-- Name: snapshot_observation snapshot_observation_snapshot_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.snapshot_observation
    ADD CONSTRAINT snapshot_observation_snapshot_id_fkey FOREIGN KEY (snapshot_id) REFERENCES semantics.snapshot(id);


--
-- Name: snapshot snapshot_parent_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.snapshot
    ADD CONSTRAINT snapshot_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES semantics.snapshot(id);


--
-- Name: source_observation source_observation_revision_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.source_observation
    ADD CONSTRAINT source_observation_revision_id_fkey FOREIGN KEY (revision_id) REFERENCES semantics.asset_revision(id);


--
-- Name: statement_evidence statement_evidence_evidence_item_id_fkey; Type: FK CONSTRAINT; Schema: semantics; Owner: -
--

ALTER TABLE ONLY semantics.statement_evidence
    ADD CONSTRAINT statement_evidence_evidence_item_id_fkey FOREIGN KEY (evidence_item_id) REFERENCES semantics.evidence_item(id);


--
-- Name: agent_scheduler fk_agent_scheduler_role; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.agent_scheduler
    ADD CONSTRAINT fk_agent_scheduler_role FOREIGN KEY (role) REFERENCES tackle.roles(name);


--
-- Name: config_bundle fk_config_bundle_role; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.config_bundle
    ADD CONSTRAINT fk_config_bundle_role FOREIGN KEY (role) REFERENCES tackle.roles(name);


--
-- Name: prompts fk_prompts_role; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.prompts
    ADD CONSTRAINT fk_prompts_role FOREIGN KEY (role) REFERENCES tackle.roles(name);


--
-- Name: role_memory fk_role_memory_role; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.role_memory
    ADD CONSTRAINT fk_role_memory_role FOREIGN KEY (role) REFERENCES tackle.roles(name);


--
-- Name: role_tool_access fk_role_tool_access_role; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.role_tool_access
    ADD CONSTRAINT fk_role_tool_access_role FOREIGN KEY (role) REFERENCES tackle.roles(name);


--
-- Name: sessions fk_sessions_agent_role; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.sessions
    ADD CONSTRAINT fk_sessions_agent_role FOREIGN KEY (agent_role) REFERENCES tackle.roles(name);


--
-- Name: tasks fk_tasks_prompt; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.tasks
    ADD CONSTRAINT fk_tasks_prompt FOREIGN KEY (prompt_id) REFERENCES tackle.prompts(id) ON DELETE RESTRICT;


--
-- Name: tasks fk_tasks_role; Type: FK CONSTRAINT; Schema: tackle; Owner: -
--

ALTER TABLE ONLY tackle.tasks
    ADD CONSTRAINT fk_tasks_role FOREIGN KEY (role) REFERENCES tackle.roles(name);


--
-- Name: cli_tools cli_tools_asset_id_fkey; Type: FK CONSTRAINT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.cli_tools
    ADD CONSTRAINT cli_tools_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: mcp_servers mcp_servers_asset_id_fkey; Type: FK CONSTRAINT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.mcp_servers
    ADD CONSTRAINT mcp_servers_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: runnable_services runnable_services_asset_id_fkey; Type: FK CONSTRAINT; Schema: terrain; Owner: -
--

ALTER TABLE ONLY terrain.runnable_services
    ADD CONSTRAINT runnable_services_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: work_requests work_requests_asset_id_fkey; Type: FK CONSTRAINT; Schema: vision; Owner: -
--

ALTER TABLE ONLY vision.work_requests
    ADD CONSTRAINT work_requests_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES semantics.canonical_asset(id);


--
-- Name: event_types event_types_workflow_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.event_types
    ADD CONSTRAINT event_types_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES wind.workflows(id);


--
-- Name: events events_event_type_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.events
    ADD CONSTRAINT events_event_type_fkey FOREIGN KEY (event_type) REFERENCES wind.event_types(event_type);


--
-- Name: workflow_edges fk_edge_from_node_task; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_edges
    ADD CONSTRAINT fk_edge_from_node_task FOREIGN KEY (from_node_id, from_task_id) REFERENCES wind.workflow_nodes(id, task_id);


--
-- Name: workflow_edges fk_edge_outcome_task; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_edges
    ADD CONSTRAINT fk_edge_outcome_task FOREIGN KEY (outcome_id, from_task_id) REFERENCES wind.task_outcomes(id, task_id);


--
-- Name: workflow_edges fk_edge_version_from; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_edges
    ADD CONSTRAINT fk_edge_version_from FOREIGN KEY (from_node_id, workflow_version_id) REFERENCES wind.workflow_nodes(id, workflow_version_id) ON DELETE CASCADE;


--
-- Name: workflow_edges fk_edge_version_to; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_edges
    ADD CONSTRAINT fk_edge_version_to FOREIGN KEY (to_node_id, workflow_version_id) REFERENCES wind.workflow_nodes(id, workflow_version_id) ON DELETE CASCADE;


--
-- Name: receipts fk_receipt_outcome_task; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.receipts
    ADD CONSTRAINT fk_receipt_outcome_task FOREIGN KEY (outcome_id, ticket_task_id) REFERENCES wind.task_outcomes(id, task_id);


--
-- Name: receipts fk_receipt_ticket_task; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.receipts
    ADD CONSTRAINT fk_receipt_ticket_task FOREIGN KEY (ticket_id, ticket_task_id) REFERENCES wind.tickets(id, node_task_id) ON DELETE CASCADE;


--
-- Name: tasks fk_tasks_tackle_task; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tasks
    ADD CONSTRAINT fk_tasks_tackle_task FOREIGN KEY (tackle_task_id) REFERENCES tackle.tasks(id) ON DELETE SET NULL;


--
-- Name: tickets fk_ticket_instance_version; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tickets
    ADD CONSTRAINT fk_ticket_instance_version FOREIGN KEY (workflow_instance_id, workflow_version_id) REFERENCES wind.workflow_instances(id, workflow_version_id) ON DELETE CASCADE;


--
-- Name: tickets fk_ticket_node_task; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tickets
    ADD CONSTRAINT fk_ticket_node_task FOREIGN KEY (node_id, node_task_id) REFERENCES wind.workflow_nodes(id, task_id);


--
-- Name: tickets fk_ticket_node_version; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tickets
    ADD CONSTRAINT fk_ticket_node_version FOREIGN KEY (node_id, workflow_version_id) REFERENCES wind.workflow_nodes(id, workflow_version_id);


--
-- Name: task_outcomes task_outcomes_task_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.task_outcomes
    ADD CONSTRAINT task_outcomes_task_id_fkey FOREIGN KEY (task_id) REFERENCES wind.tasks(id) ON DELETE CASCADE;


--
-- Name: tasks tasks_office_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tasks
    ADD CONSTRAINT tasks_office_id_fkey FOREIGN KEY (office_id) REFERENCES wind.offices(id);


--
-- Name: tasks tasks_title_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tasks
    ADD CONSTRAINT tasks_title_id_fkey FOREIGN KEY (title_id) REFERENCES wind.titles(id);


--
-- Name: tickets tickets_assigned_title_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.tickets
    ADD CONSTRAINT tickets_assigned_title_id_fkey FOREIGN KEY (assigned_title_id) REFERENCES wind.titles(id);


--
-- Name: titles titles_office_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.titles
    ADD CONSTRAINT titles_office_id_fkey FOREIGN KEY (office_id) REFERENCES wind.offices(id);


--
-- Name: titles titles_role_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.titles
    ADD CONSTRAINT titles_role_id_fkey FOREIGN KEY (role_id) REFERENCES nebula.roles_history(id);


--
-- Name: workflow_instances workflow_instances_event_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_instances
    ADD CONSTRAINT workflow_instances_event_id_fkey FOREIGN KEY (event_id) REFERENCES wind.events(id);


--
-- Name: workflow_instances workflow_instances_workflow_version_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_instances
    ADD CONSTRAINT workflow_instances_workflow_version_id_fkey FOREIGN KEY (workflow_version_id) REFERENCES wind.workflow_versions(id);


--
-- Name: workflow_nodes workflow_nodes_task_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_nodes
    ADD CONSTRAINT workflow_nodes_task_id_fkey FOREIGN KEY (task_id) REFERENCES wind.tasks(id);


--
-- Name: workflow_nodes workflow_nodes_workflow_version_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_nodes
    ADD CONSTRAINT workflow_nodes_workflow_version_id_fkey FOREIGN KEY (workflow_version_id) REFERENCES wind.workflow_versions(id) ON DELETE CASCADE;


--
-- Name: workflow_versions workflow_versions_workflow_id_fkey; Type: FK CONSTRAINT; Schema: wind; Owner: -
--

ALTER TABLE ONLY wind.workflow_versions
    ADD CONSTRAINT workflow_versions_workflow_id_fkey FOREIGN KEY (workflow_id) REFERENCES wind.workflows(id);


--
-- PostgreSQL database dump complete
--

\unrestrict 0HIwe3Psx6fkmkqebO0G4oYVXiGKxQEhA2OhXP87VwRTOuqjNi2y2OHHC03DcLk

