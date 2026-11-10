-- ═══════════════════════════════════════════════════════════════════════
--  Migration 034 — WRP Event Vocabulary Cutover
--
--  Pins the Q2 coherence scope from the reconciliation thread
--  (architect verification dc960759 + DBA ruling f0e4ed76, 2026-10-20,
--  Option A = dot-namespaced canonical).
--
--  1. Transcode pre-cutover WR_* rows to the canonical vocabulary BEFORE the
--     CHECK is narrowed. The exact runtime verb is preserved in
--     payload.runtime_event_type; transition rows carry payload.new_state.
--  2. Narrow conduit.work_request_events.event_type CHECK to the nine
--     canonical values. The ten WR_* WRP values leave the allowed set — a
--     stray WR_* insert now fails loudly at the constraint (no DB-side shim).
--  3. Create conduit.allowed_transitions(text), which was absent from every
--     schema even though the STATE.TRANSITION_COMMITTED projector branch
--     calls it. It is backed by an OWNED graph table,
--     conduit.work_request_state_graph, whose rows are the kernel-derived
--     LEDGER_STATE_GRAPH exported by typescript/conduit-mcp/src/runtime-kernel.ts
--     (built from TRANSITION_TABLE). This migration's verification block pins
--     the same 11 edges, and db.ts createSchema seeds the identical graph for
--     fresh schemas — so the two-authority problem cannot recur one layer down.
--  4. Add FK work_request_events.work_request_id → resolution.work_request(id)
--     (uuid = uuid). Added NOT VALID so legacy drift cannot block the cutover;
--     validated immediately when there are no orphan event rows.
--  5. Disposition non-joining conduit.work_request_state projection rows
--     (projections are rebuildable, never canonical) so the drift scan does
--     not stay permanently red.
--  6. Teach wind.trg_bridge_conduit_events the dot-namespaced vocabulary while
--     preserving the proven WR_* → wr.<verb> mapping via the runtime verb.
--
--  Emitter contract: WR_* is transcoded at the WRITE BOUNDARY
--  (typescript/conduit-mcp/src/db.ts insert sites + synthetic backfill,
--  python/cascade/event_store.py). Kernel-internal vocabulary (foldEvents,
--  reduce, DECISION_MAP, compilerOutputToEvent, Python WRP in-memory) is
--  unchanged; reads map back in dbEventsToRuntimeEvents.
--
--  Depends on: 016-event-sourcing-foundation.sql, 033-conduit-schema-fixes.sql
-- ═══════════════════════════════════════════════════════════════════════

BEGIN;

-- ═══════════════════════════════════════════════════════════════════════
--  1. TRANSCODE PRE-CUTOVER ROWS
-- ═══════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  v_count INTEGER;
BEGIN
  IF to_regclass('conduit.work_request_events') IS NULL THEN
    RAISE NOTICE 'conduit.work_request_events absent — migration 034 skipped';
    RETURN;
  END IF;

  UPDATE conduit.work_request_events e
  SET event_type = CASE e.event_type
        WHEN 'WR_SUBMITTED' THEN 'WORKREQUEST.CREATED'
        ELSE 'STATE.TRANSITION_COMMITTED'
      END,
      payload = e.payload
        || jsonb_build_object('runtime_event_type', e.event_type)
        || CASE e.event_type
             WHEN 'WR_SUBMITTED' THEN '{}'::jsonb
             WHEN 'WR_VALIDATED' THEN jsonb_build_object('new_state', 'PLANNING')
             WHEN 'WR_QUEUED' THEN jsonb_build_object('new_state', 'PENDING')
             WHEN 'WR_CLAIMED' THEN jsonb_build_object('new_state', 'IMPLEMENTING')
             WHEN 'WR_ACKED' THEN jsonb_build_object('new_state', 'REVIEW')
             WHEN 'WR_SETTLED' THEN jsonb_build_object('new_state', 'COMPLETED')
             WHEN 'WR_FAILED' THEN jsonb_build_object('new_state', 'FAILED')
             ELSE jsonb_build_object('new_state', 'CANCELLED')
           END
  WHERE e.event_type IN (
    'WR_SUBMITTED','WR_VALIDATED','WR_QUEUED','WR_CLAIMED','WR_ACKED',
    'WR_SETTLED','WR_REJECTED','WR_FAILED','WR_NOOP','WR_DEFERRED'
  );

  GET DIAGNOSTICS v_count = ROW_COUNT;
  RAISE NOTICE 'Migration 034: transcoded % legacy WR_* event rows', v_count;
END $$;

-- ═══════════════════════════════════════════════════════════════════════
--  2. NARROW THE CHECK TO THE CANONICAL ENUMERANT SET
-- ═══════════════════════════════════════════════════════════════════════

ALTER TABLE conduit.work_request_events
  DROP CONSTRAINT IF EXISTS work_request_events_event_type_check;
ALTER TABLE conduit.work_request_events
  DROP CONSTRAINT IF EXISTS chk_event_type;
ALTER TABLE conduit.work_request_events
  ADD CONSTRAINT work_request_events_event_type_check
  CHECK (event_type IN (
    'WORKREQUEST.CREATED',
    'VISION.IR_PRODUCED',
    'STATE.TRANSITION_PROPOSED',
    'STATE.TRANSITION_APPROVED',
    'STATE.TRANSITION_COMMITTED',
    'EXECUTION.STARTED',
    'EXECUTION.COMPLETED',
    'EXECUTION.FAILED',
    'SYSTEM.CRON_TRIGGERED'
  ));

-- ═══════════════════════════════════════════════════════════════════════
--  3. OWNED LEDGER STATE GRAPH + allowed_transitions()
-- ═══════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS conduit.work_request_state_graph (
  from_state TEXT NOT NULL,
  to_state   TEXT NOT NULL,
  PRIMARY KEY (from_state, to_state)
);

COMMENT ON TABLE conduit.work_request_state_graph IS
  'Owned ledger state graph. Generated from the kernel TRANSITION_TABLE via '
  'LEDGER_STATE_GRAPH in typescript/conduit-mcp/src/runtime-kernel.ts. '
  'conduit.allowed_transitions() reads this table; migration 034 and the '
  'db.ts fresh-schema bootstrap pin parity with the TypeScript constant so '
  'the DB and kernel cannot drift.';

DELETE FROM conduit.work_request_state_graph;

INSERT INTO conduit.work_request_state_graph (from_state, to_state) VALUES
  -- DRAFT → VALIDATED / REJECTED
  ('PROPOSED',     'PLANNING'),
  ('PROPOSED',     'CANCELLED'),
  -- VALIDATED → QUEUED / REJECTED
  ('PLANNING',     'PENDING'),
  ('PLANNING',     'CANCELLED'),
  -- QUEUED → CLAIMED / DEFERRED
  ('PENDING',      'IMPLEMENTING'),
  ('PENDING',      'CANCELLED'),
  -- CLAIMED → ACKED / FAILED
  ('IMPLEMENTING', 'REVIEW'),
  ('IMPLEMENTING', 'FAILED'),
  -- ACKED → SETTLED / FAILED / NOOP
  ('REVIEW',       'COMPLETED'),
  ('REVIEW',       'FAILED'),
  ('REVIEW',       'CANCELLED');

CREATE OR REPLACE FUNCTION conduit.allowed_transitions(from_state TEXT)
RETURNS TEXT[] AS $$
BEGIN
  RETURN COALESCE(
    (SELECT array_agg(to_state ORDER BY to_state)
     FROM conduit.work_request_state_graph
     WHERE work_request_state_graph.from_state = allowed_transitions.from_state),
    ARRAY[]::TEXT[]
  );
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION conduit.allowed_transitions(TEXT) IS
  'Allowed ledger-state transitions, read from the owned graph table. '
  'Called by conduit.update_work_request_state() on STATE.TRANSITION_COMMITTED.';

-- ═══════════════════════════════════════════════════════════════════════
--  4. FK → resolution.work_request(id)
-- ═══════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  v_orphans INTEGER;
BEGIN
  IF to_regclass('resolution.work_request') IS NULL THEN
    RAISE NOTICE 'resolution.work_request absent — FK not added';
    RETURN;
  END IF;

  ALTER TABLE conduit.work_request_events
    DROP CONSTRAINT IF EXISTS work_request_events_work_request_id_fkey;
  ALTER TABLE conduit.work_request_events
    ADD CONSTRAINT work_request_events_work_request_id_fkey
    FOREIGN KEY (work_request_id) REFERENCES resolution.work_request(id) NOT VALID;

  SELECT count(*) INTO v_orphans
  FROM conduit.work_request_events e
  WHERE NOT EXISTS (SELECT 1 FROM resolution.work_request r WHERE r.id = e.work_request_id);

  IF v_orphans = 0 THEN
    ALTER TABLE conduit.work_request_events
      VALIDATE CONSTRAINT work_request_events_work_request_id_fkey;
    RAISE NOTICE 'Migration 034: FK validated (0 orphan event rows)';
  ELSE
    RAISE NOTICE
      'Migration 034: FK left NOT VALID — % legacy event rows do not join resolution.work_request (explicit drift-scan exception)',
      v_orphans;
  END IF;
END $$;

-- ═══════════════════════════════════════════════════════════════════════
--  5. DISPOSITION NON-JOINING PROJECTION ROWS
-- ═══════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  v_count INTEGER;
BEGIN
  IF to_regclass('resolution.work_request') IS NULL THEN
    RAISE NOTICE 'resolution.work_request absent — projection disposition skipped';
    RETURN;
  END IF;

  WITH gone AS (
    DELETE FROM conduit.work_request_state s
    WHERE NOT EXISTS (
      SELECT 1 FROM resolution.work_request r WHERE r.id = s.work_request_id
    )
    RETURNING 1
  )
  SELECT count(*) INTO v_count FROM gone;

  RAISE NOTICE 'Migration 034: disposed % non-joining work_request_state rows', v_count;
END $$;

-- ═══════════════════════════════════════════════════════════════════════
--  6. WIND BRIDGE — LEARN THE CANONICAL VOCABULARY
-- ═══════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION wind.trg_bridge_conduit_events() RETURNS trigger
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
  v_runtime_event_type TEXT;
BEGIN
  v_runtime_event_type := NEW.payload->>'runtime_event_type';

  IF v_runtime_event_type IS NOT NULL THEN
    -- Transcoded row: preserve the proven WR_* → wr.<verb> mapping.
    IF v_runtime_event_type NOT IN (
      'WR_SUBMITTED','WR_VALIDATED','WR_QUEUED','WR_CLAIMED','WR_ACKED',
      'WR_SETTLED','WR_REJECTED','WR_FAILED','WR_NOOP','WR_DEFERRED'
    ) THEN
      RETURN NEW;
    END IF;
    v_wind_event_type := 'wr.' || lower(replace(v_runtime_event_type, 'WR_', ''));
  ELSIF NEW.event_type IN (
    'WR_SUBMITTED','WR_VALIDATED','WR_QUEUED','WR_CLAIMED','WR_ACKED',
    'WR_SETTLED','WR_REJECTED','WR_FAILED','WR_NOOP','WR_DEFERRED'
  ) THEN
    -- Pre-cutover rows still present in the ledger.
    v_wind_event_type := 'wr.' || lower(replace(NEW.event_type, 'WR_', ''));
  ELSE
    v_wind_event_type := CASE NEW.event_type
      WHEN 'WORKREQUEST.CREATED' THEN 'wr.submitted'
      WHEN 'STATE.TRANSITION_PROPOSED' THEN 'wr.deferred'
      WHEN 'STATE.TRANSITION_APPROVED' THEN 'wr.validated'
      WHEN 'STATE.TRANSITION_COMMITTED' THEN
        CASE NEW.payload->>'new_state'
          WHEN 'PLANNING' THEN 'wr.validated'
          WHEN 'PENDING' THEN 'wr.queued'
          WHEN 'IMPLEMENTING' THEN 'wr.claimed'
          WHEN 'REVIEW' THEN 'wr.acked'
          WHEN 'COMPLETED' THEN 'wr.settled'
          WHEN 'FAILED' THEN 'wr.failed'
          WHEN 'CANCELLED' THEN 'wr.rejected'
          ELSE 'wr.acked'
        END
      WHEN 'EXECUTION.STARTED' THEN 'wr.claimed'
      WHEN 'EXECUTION.COMPLETED' THEN 'wr.settled'
      WHEN 'EXECUTION.FAILED' THEN 'wr.failed'
      ELSE NULL
    END;
    IF v_wind_event_type IS NULL THEN
      RETURN NEW;
    END IF;
  END IF;

  SELECT wr.wr_id, wr.title INTO v_wr_id, v_wr_title
  FROM vision.work_requests wr
  WHERE wr.work_request_uuid = NEW.work_request_id::text;

  v_subject := 'nexus.wind.v1.events.' || v_wind_event_type;

  v_payload := jsonb_build_object(
    'wr_id', COALESCE(v_wr_id, NEW.work_request_id::text),
    'title', v_wr_title,
    'event_type', NEW.event_type,
    'event_id', NEW.event_id,
    'actor_type', NEW.actor_type,
    'actor_id', NEW.actor_id
  ) || COALESCE(NEW.payload, '{}'::jsonb);

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

-- ═══════════════════════════════════════════════════════════════════════
--  7. VERIFICATION
-- ═══════════════════════════════════════════════════════════════════════

DO $$
DECLARE
  v_count INTEGER;
  v_edges INTEGER;
  v_bad TEXT;
BEGIN
  -- allowed_transitions exists.
  SELECT count(*) INTO v_count
  FROM pg_proc p
  JOIN pg_namespace n ON n.oid = p.pronamespace
  WHERE n.nspname = 'conduit'
    AND p.proname = 'allowed_transitions';
  IF v_count <> 1 THEN
    RAISE EXCEPTION 'conduit.allowed_transitions missing (found %)', v_count;
  END IF;

  -- Owned graph matches the kernel-derived LEDGER_STATE_GRAPH (11 edges).
  SELECT count(*) INTO v_edges FROM conduit.work_request_state_graph;
  IF v_edges <> 11 THEN
    RAISE EXCEPTION 'work_request_state_graph has % edges, expected 11', v_edges;
  END IF;

  SELECT string_agg(pair, ', ') INTO v_bad
  FROM (
    SELECT from_state || '->' || to_state AS pair
    FROM conduit.work_request_state_graph
    EXCEPT
    SELECT * FROM (VALUES
      ('PROPOSED->PLANNING'), ('PROPOSED->CANCELLED'),
      ('PLANNING->PENDING'), ('PLANNING->CANCELLED'),
      ('PENDING->IMPLEMENTING'), ('PENDING->CANCELLED'),
      ('IMPLEMENTING->REVIEW'), ('IMPLEMENTING->FAILED'),
      ('REVIEW->COMPLETED'), ('REVIEW->FAILED'), ('REVIEW->CANCELLED')
    ) AS expected(pair)
  ) drift;
  IF v_bad IS NOT NULL THEN
    RAISE EXCEPTION 'work_request_state_graph drift vs kernel TRANSITION_TABLE: %', v_bad;
  END IF;

  -- The narrowed CHECK must reject a WR_* value (probe rolled back).
  BEGIN
    INSERT INTO conduit.work_request_events
      (work_request_id, event_type, payload)
    VALUES
      ('00000000-0000-0000-0000-000000000000', 'WR_SUBMITTED', '{}'::jsonb);
    RAISE EXCEPTION 'CHECK did not reject WR_SUBMITTED';
  EXCEPTION
    WHEN check_violation THEN
      NULL; -- expected: stray WR_* fails loudly at the CHECK
    WHEN foreign_key_violation THEN
      RAISE EXCEPTION
        'WR_SUBMITTED reached the FK, not the CHECK — vocabulary narrowing not applied';
  END;

  RAISE NOTICE 'Migration 034 complete — canonical event vocabulary pinned';
  RAISE NOTICE '   CHECK: 9 canonical types, WR_* rejected';
  RAISE NOTICE '   allowed_transitions(): backed by owned 11-edge graph';
  RAISE NOTICE '   FK: work_request_events.work_request_id → resolution.work_request(id)';
  RAISE NOTICE '   wind bridge: dot-namespaced vocabulary learned';
END $$;

COMMIT;
