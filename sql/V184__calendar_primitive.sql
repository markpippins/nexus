-- =============================================================================
-- V184 (DBA pre-stage — Calendar primitive): vision.calendars,
-- vision.calendar_events, vision.sessions — the storage for the TypeSpec
-- contract merged as #331 (typespec/v1/calendar), per design thread a330914e.
--
-- ⚠️  STAGED INERT — NOT APPLIED TO LIVE. ⚠️
-- Apply waits on explicit operator go AFTER the roundtable answers:
--   Q1  storage direction (this PG-vision pre-stage vs Concepts/shrapnel/
--       Mongo-first per the Type-collapse plan — this file is the PG answer,
--       ready either way: adapter-satisfiable via the provider-adapter
--       registry if the roundtable later designates another provider);
--   Q2  deterministic event ids (encoded here as the PRIMARY KEY contract:
--       uuid5(machine|emitter|window_start) — flipping to random uuid is a
--       one-line DEFAULT change + backfill, but the PK stays event identity).
-- Posture identical to V167/V169/V171: pre-stage now, never idle the design.
--
-- Contract parity (#331 vocabulary — drift is a review failure):
--   CalendarEvent: eventId, calendarId, kind(scheduled|occurred|observed),
--     source{machine,emitter}, title, window{start,end?}, participants[]
--     (AgentRef: role/model/sessionId/machine — wire key `model`),
--     sessionRef?, payload, provenance{recordedAt, recordedBy, consolidatedFrom?}
--   Session: sessionId, title, address{host,port}, window, participants[],
--     contextBundle{snapshotRefs, digestRefs, keychains, procedureCards},
--     calendarId, reconcileState(open|closed|reconciled)
--
-- House patterns carried:
--   * bitemporal pair on both tables (valid/recorded axes, infinity sentinels)
--   * append-only posture: immutability + no-TRUNCATE guards (V167 family)
--   * NEBULA_AUDIT statement triggers (ins/upd/del) with the self-contained
--     fallback fn_nebula_audit_log (V167 family)
--   * idempotent (IF NOT EXISTS / OR REPLACE / DROP-then-CREATE triggers)
-- =============================================================================

BEGIN;

-- ── 0. Gates ────────────────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('vision.calendar_events') IS NOT NULL
       OR to_regclass('vision.sessions') IS NOT NULL THEN
        RAISE EXCEPTION 'V184-GATE-001: calendar surfaces already exist — refusing to double-apply'
            USING ERRCODE = 'P0001';
    END IF;
END $$;

-- ── 1. vision.calendars — accumulation surface ─────────────────────────────
CREATE TABLE IF NOT EXISTS vision.calendars (
    calendar_id         uuid PRIMARY KEY,
    title               text NOT NULL,
    scope               text NOT NULL
        CONSTRAINT cks_calendars_scope CHECK (scope IN ('local', 'shared')),
    owner               text,           -- machine hostname for scope='local'
    -- house bitemporal pair
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cks_calendars_owner
        CHECK (owner IS NOT NULL OR scope = 'shared')
);

COMMENT ON TABLE vision.calendars IS
'Calendar primitive: accumulation surface (design a330914e, contract #331). local = one machine''s rhythm; shared = a gathering calendar multiple machines contribute to. STAGED INERT — apply waits on roundtable Q1/Q2.';

-- ── 2. vision.calendar_events — the atom ───────────────────────────────────
CREATE TABLE IF NOT EXISTS vision.calendar_events (
    event_id            uuid PRIMARY KEY,
        -- Q2: deterministic uuid5(machine|emitter|window_start). The PK IS
        -- the dedupe: re-emission of the same occurrence is a no-op by
        -- construction, cross-machine consolidation dedupes without
        -- coordination.
    calendar_id         uuid NOT NULL REFERENCES vision.calendars (calendar_id),
    kind                text NOT NULL
        CONSTRAINT cks_calendar_events_kind CHECK (kind IN ('scheduled', 'occurred', 'observed')),
    source_machine      text NOT NULL,
    source_emitter      text NOT NULL,
    title               text NOT NULL,
    window_start        timestamptz NOT NULL,
    window_end          timestamptz,     -- NULL => extendable window
    participants        jsonb NOT NULL DEFAULT '[]'::jsonb,
        -- AgentRef[] wire-shaped: [{"role"?,"model"?,"sessionId"?,"machine"?}]
        -- `model` is the wire key (contract @encodedName parity, V169 census)
    session_ref         uuid,
        -- FK to vision.sessions added in section 3b (sessions declared below;
        -- FK targets must exist at CREATE time)
    payload             jsonb,           -- kind-specific: recurrence, digests, outcomes
    -- provenance
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    recorded_by         text NOT NULL,
    consolidated_from   jsonb,           -- source event ids folded in (observed)
    -- house bitemporal pair
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cks_calendar_events_participants
        CHECK (jsonb_typeof(participants) = 'array'),
    CONSTRAINT cks_calendar_events_consolidated
        CHECK (consolidated_from IS NULL OR jsonb_typeof(consolidated_from) = 'array'),
    CONSTRAINT cks_calendar_events_window
        CHECK (window_end IS NULL OR window_end >= window_start),
    CONSTRAINT cks_calendar_events_kind_epistemics
        -- observed events must cite what they were consolidated from
        CHECK (kind <> 'observed' OR consolidated_from IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_calendar_start
    ON vision.calendar_events (calendar_id, window_start DESC);
CREATE INDEX IF NOT EXISTS idx_calendar_events_emitter
    ON vision.calendar_events (source_machine, source_emitter, window_start DESC);
CREATE INDEX IF NOT EXISTS idx_calendar_events_session
    ON vision.calendar_events (session_ref) WHERE session_ref IS NOT NULL;

-- ── 3. vision.sessions — the co-incident context ───────────────────────────
CREATE TABLE IF NOT EXISTS vision.sessions (
    session_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title               text NOT NULL,
    address_host        text NOT NULL,
    address_port        int  NOT NULL,   -- Q5 open: ownership with Wind owners
    window_start        timestamptz NOT NULL,
    window_end          timestamptz,     -- NULL => open/extendable
    participants        jsonb NOT NULL DEFAULT '[]'::jsonb,
    context_bundle      jsonb NOT NULL,
        -- refs-only (V167/V169 stance): {snapshotRefs[], digestRefs[],
        -- keychains[], procedureCards[]} — payloads resolved at adoption,
        -- never inlined
    calendar_id         uuid NOT NULL REFERENCES vision.calendars (calendar_id),
    reconcile_state     text NOT NULL DEFAULT 'open'
        CONSTRAINT cks_sessions_reconcile CHECK (reconcile_state IN ('open', 'closed', 'reconciled')),
    -- house bitemporal pair
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cks_sessions_port CHECK (address_port BETWEEN 1 AND 65535),
    CONSTRAINT cks_sessions_participants CHECK (jsonb_typeof(participants) = 'array'),
    CONSTRAINT cks_sessions_bundle CHECK (jsonb_typeof(context_bundle) = 'object'),
    CONSTRAINT cks_sessions_window
        CHECK (window_end IS NULL OR window_end >= window_start)
);

CREATE INDEX IF NOT EXISTS idx_sessions_calendar
    ON vision.sessions (calendar_id);
CREATE INDEX IF NOT EXISTS idx_sessions_state
    ON vision.sessions (reconcile_state) WHERE reconcile_state <> 'reconciled';

COMMENT ON TABLE vision.sessions IS
'Session object: a shared, co-incident context with temporal boundaries and its own address (a330914e). Joiners attach; starters spawn; both write connection records bound here. Close-out reconciliation produces minutes. STAGED INERT — apply waits on roundtable Q1/Q2 (Q5 addressing ownership open with Wind).';

-- ── 3b. Deferred FK fix: events -> sessions (sessions now exist) ───────────
-- The events table was declared first for reading order; the session_ref FK
-- target did not exist yet. Added here, inside the same transaction.
DO $$
BEGIN
    IF to_regclass('vision.sessions') IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM pg_constraint
           WHERE conname = 'fk_calendar_events_session') THEN
        -- drop the broken placeholder constraint if it somehow exists
        ALTER TABLE vision.calendar_events
            DROP CONSTRAINT IF EXISTS fk_calendar_events_session_placeholder;
        ALTER TABLE vision.calendar_events
            ADD CONSTRAINT fk_calendar_events_session
            FOREIGN KEY (session_ref) REFERENCES vision.sessions (session_id);
    END IF;
END $$;

-- ── 4. Append-only posture (V167 family) ───────────────────────────────────
CREATE OR REPLACE FUNCTION vision.trg_calendar_no_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'CAL011: calendar surfaces are append-only — DELETE refused (supersede via bitemporal close)'
        USING ERRCODE = 'P0001';
END;
$$;

CREATE OR REPLACE FUNCTION vision.trg_calendar_no_truncate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'CAL012: calendar surfaces refuse TRUNCATE (append-only)'
        USING ERRCODE = 'P0001';
END;
$$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['calendar_events', 'sessions', 'calendars'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_no_delete ON vision.%I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_no_delete BEFORE DELETE ON vision.%I FOR EACH ROW EXECUTE FUNCTION vision.trg_calendar_no_delete()', t, t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_no_truncate ON vision.%I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_no_truncate BEFORE TRUNCATE ON vision.%I FOR EACH STATEMENT EXECUTE FUNCTION vision.trg_calendar_no_truncate()', t, t);
    END LOOP;
END $$;

-- ── 5. Attributable trail: NEBULA_AUDIT statement triggers (V167 family) ───
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                   WHERE n.nspname = 'tackle' AND p.proname = 'fn_nebula_audit_log') THEN
        CREATE FUNCTION tackle.fn_nebula_audit_log(
          p_table text, p_op text, p_rows bigint, p_keys text) RETURNS void
        LANGUAGE sql AS $fn$
          INSERT INTO tackle.system_logs
            (id, timestamp, level, category, message, source, details)
          VALUES (
            gen_random_uuid()::text, now(), 'INFO', 'NEBULA_AUDIT',
            format('%s on %s (%s rows)%s', p_op, p_table, p_rows,
                   COALESCE(' [' || left(p_keys, 900) || ']', '')),
            'nebula-audit-trigger',
            jsonb_build_object('table', p_table, 'op', p_op, 'row_count', p_rows,
                               'keys', p_keys)
          );
        $fn$;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION vision.trg_calendar_events_audit_ins()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'vision.calendar_events', 'INSERT',
        (SELECT count(*) FROM new_rows),
        COALESCE((SELECT string_agg(r.source_machine || '/' || r.source_emitter, ',')
                  FROM new_rows r), ''));
    RETURN NULL;
END; $$;

CREATE OR REPLACE FUNCTION vision.trg_calendar_events_audit_upd()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'vision.calendar_events', 'UPDATE',
        (SELECT count(*) FROM old_rows),
        COALESCE((SELECT string_agg(r.source_machine || '/' || r.source_emitter, ',')
                  FROM old_rows r), ''));
    RETURN NULL;
END; $$;

CREATE OR REPLACE FUNCTION vision.trg_sessions_audit_ins()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'vision.sessions', 'INSERT',
        (SELECT count(*) FROM new_rows),
        COALESCE((SELECT string_agg(left(r.title, 60), ',') FROM new_rows r), ''));
    RETURN NULL;
END; $$;

CREATE OR REPLACE FUNCTION vision.trg_sessions_audit_upd()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'vision.sessions', 'UPDATE',
        (SELECT count(*) FROM old_rows),
        COALESCE((SELECT string_agg(left(r.title, 60), ',') FROM old_rows r), ''));
    RETURN NULL;
END; $$;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['calendar_events', 'sessions'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_audit_ins ON vision.%I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_audit_ins AFTER INSERT ON vision.%I REFERENCING NEW TABLE AS new_rows FOR EACH STATEMENT EXECUTE FUNCTION vision.trg_%s_audit_ins()', t, t, t);
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_audit_upd ON vision.%I', t, t);
        EXECUTE format('CREATE TRIGGER trg_%s_audit_upd AFTER UPDATE ON vision.%I REFERENCING NEW TABLE AS new_rows OLD TABLE AS old_rows FOR EACH STATEMENT EXECUTE FUNCTION vision.trg_%s_audit_upd()', t, t, t);
    END LOOP;
END $$;

-- ── 6. Postcondition ───────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('vision.calendars') IS NULL
       OR to_regclass('vision.calendar_events') IS NULL
       OR to_regclass('vision.sessions') IS NULL THEN
        RAISE EXCEPTION 'V184-POSTCONDITION: calendar surfaces missing after apply';
    END IF;
END $$;

COMMIT;
