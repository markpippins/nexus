-- =============================================================================
-- V169 (DBA pre-stage — session affordance census): nebula.agent_connections
-- =============================================================================
--
-- ⚠️  DRAFT — NOT APPLIED TO LIVE. ⚠️
-- Additive, self-contained, inert-by-absence. No destructive statements; safe
-- to apply at any time; supersedes nothing. It records WHAT AN EXECUTION
-- CONTEXT COULD ACTUALLY SEE AND DO at session start — the affordance census
-- complement to the continuity digest (content vs capability), per the
-- operator's proposal in the continuity thread (65fe85a8).
--
-- ── The problem ─────────────────────────────────────────────────────────────
-- The continuity track documents what a role should KNOW. Nothing documents
-- what an execution context CAN DO. The harness (Freebuff) connects un-shimmed
-- to the execution context: a session may or may not have nebula-mcp reachable,
-- procedure cards loaded, an inbox, a lease, a handoff digest. That variance is
-- invisible to governance today: agent records claim role actions while the
-- context may have had no affordances at all — the governance gap behind the
-- crosstalk incident (2026-09-16, documented in discussions 228412e0).
--
-- ── The census (per the operator's enumerated affordances) ──────────────────
--   * session_id      — the harness session this record attests (nullable:
--                        un-shimmed contexts have no harness session id)
--   * model + role    — model identity per ruling R-1..R-3
--   * mcp_tools       — refs-only list of registered MCP tools at boot
--                       [{server, tool}] pairs
--   * procedure_cards — whether the role's procedure cards were visible and
--                       how many (the tackle-mcp index count; NULL = unknown)
--   * inbox_status    — pending count at boot (the R17 check), refs-only
--   * handoff_context — availability of prior-session context: the digest
--                       preview (counts + version); once V167 applies,
--                       snapshot availability rides here too
--   * keychains       — NO KEYCHAIN SURFACE EXISTS in the system today
--                       (repo-wide search 2026-09-16). Recorded as explicitly
--                       absent — {"available": false}, never faked.
--
-- ── Design positions ────────────────────────────────────────────────────────
--   * APPEND-ONLY: rows are boot-time attestations — history, not state.
--     UPDATE/DELETE blocked (CON010/CON011, house bitemporal contract).
--   * LEASE PROVENANCE: lease_ref nullable-but-explicit. When present it must
--     exist in tackle.role_leases and match the row role (CON0001/CON0002 —
--     mirrors SNAP001/002: a lease is a scope artifact, not an authority
--     grant). A leased boot with ZERO affordances is a valid — indeed the
--     most interesting — record; the census must be able to say "leased but
--     blind" honestly, so no constraint forces non-empty affordances.
--   * REFS-ONLY: no tool payloads, no card bodies, no record content — the
--     census describes WHAT WAS AVAILABLE, never WHAT IT SAID. Altitude-safe.
--   * ATTRIBUTABLE TRAIL: statement-level audit triggers (NEBULA_AUDIT)
--     piggyback the V156/V167 pattern.
--   * INERT BY ABSENCE: the table does not exist on live until applied; the
--     boot-shim wiring degrades gracefully when absent.
--
-- =============================================================================

BEGIN;

-- ── 1. Canonical table ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nebula.agent_connections (
    conn_id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id          text,             -- nullable: un-shimmed sessions have none
    role                text NOT NULL,
    model               text,
    channel             text,             -- lease channel (interactive/ui-fleet/...)
    lease_ref           uuid,             -- NULL ⇒ unleased boot (marked)
    mcp_tools           jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{server, tool}] refs
    procedure_cards     jsonb,            -- {available: bool, count: int, index: [slugs]}
    inbox_status        jsonb,            -- {available: bool, pending_count: int}
    handoff_context     jsonb,            -- {digest_available, digest_version, counts, snapshot_available}
    keychains           jsonb NOT NULL DEFAULT '{"available": false}'::jsonb,  -- explicitly absent today
    as_of               timestamptz NOT NULL DEFAULT now(),
    -- house bitemporal pair (mirrors V167; retention is a later policy decision)
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cks_agent_connections_tools
        CHECK (jsonb_typeof(mcp_tools) = 'array'),
    CONSTRAINT cks_agent_connections_cards
        CHECK (procedure_cards IS NULL OR jsonb_typeof(procedure_cards) = 'object'),
    CONSTRAINT cks_agent_connections_inbox
        CHECK (inbox_status IS NULL OR jsonb_typeof(inbox_status) = 'object'),
    CONSTRAINT cks_agent_connections_handoff
        CHECK (handoff_context IS NULL OR jsonb_typeof(handoff_context) = 'object'),
    CONSTRAINT cks_agent_connections_keychains
        CHECK (jsonb_typeof(keychains) = 'object')
);

COMMENT ON TABLE nebula.agent_connections IS
'Session affordance census (DBA pre-stage, continuity 65fe85a8): what an execution context could actually see and do at session start — MCP tools registered, procedure-card visibility, inbox status, handoff/digest availability, keychain availability. Refs-only by design: the census records WHAT WAS AVAILABLE, never WHAT IT SAID. Append-only boot-time attestations; NOT APPLIED until adopted by the boot shim flow.';

CREATE INDEX IF NOT EXISTS idx_agent_connections_role_asof
    ON nebula.agent_connections (role, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_agent_connections_session
    ON nebula.agent_connections (session_id) WHERE session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_agent_connections_lease
    ON nebula.agent_connections (lease_ref) WHERE lease_ref IS NOT NULL;

-- ── 2. Provenance guard: leased rows must carry a real, agreeing lease ──────
CREATE OR REPLACE FUNCTION nebula.trg_agent_connections_provenance()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.lease_ref IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM tackle.role_leases l WHERE l.id = NEW.lease_ref) THEN
            RAISE EXCEPTION 'CON0001: lease_ref % does not exist in tackle.role_leases (provenance guard)', NEW.lease_ref
                USING ERRCODE = 'P0001';
        END IF;
        -- lease/role agreement: a leased connection record belongs to the lease role
        IF EXISTS (SELECT 1 FROM tackle.role_leases l
                   WHERE l.id = NEW.lease_ref AND l.role IS NOT NULL AND l.role <> NEW.role) THEN
            RAISE EXCEPTION 'CON0002: lease % role does not match connection record role % (provenance guard)', NEW.lease_ref, NEW.role
                USING ERRCODE = 'P0001';
        END IF;
        -- NOTE: no non-empty-affordance requirement. "Leased but blind" —
        -- zero tools, no cards, no inbox — is a valid and important record.
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_connections_provenance ON nebula.agent_connections;
CREATE TRIGGER trg_agent_connections_provenance
BEFORE INSERT OR UPDATE ON nebula.agent_connections
FOR EACH ROW EXECUTE FUNCTION nebula.trg_agent_connections_provenance();

-- ── 3. Append-only posture: boot attestations are immutable history ─────────
CREATE OR REPLACE FUNCTION nebula.trg_agent_connections_immutability()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'CON010: agent_connections is append-only (house bitemporal contract; supersede, never delete)'
            USING ERRCODE = 'P0001';
    END IF;
    IF OLD.recorded_until_dt <> 'infinity'::timestamptz THEN
        RAISE EXCEPTION 'CON011: recorded (superseded) connection rows are frozen'
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_connections_immutability ON nebula.agent_connections;
CREATE TRIGGER trg_agent_connections_immutability
BEFORE UPDATE OR DELETE ON nebula.agent_connections
FOR EACH ROW EXECUTE FUNCTION nebula.trg_agent_connections_immutability();

-- ── 4. Attributable trail: statement-level audit, NEBULA_AUDIT category ─────
-- Reuses tackle.fn_nebula_audit_log when present (V156/V167), else falls back
-- to a self-contained log write so this migration can bootstrap a fresh
-- database alone.
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

CREATE OR REPLACE FUNCTION nebula.trg_agent_connections_audit_ins()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'nebula.agent_connections', 'INSERT',
        (SELECT count(*) FROM new_rows),
        COALESCE((SELECT string_agg(r.role, ',') FROM new_rows r), ''));
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION nebula.trg_agent_connections_audit_del()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Unreachable while CON010 stands (delete is refused first); declared for
    -- pattern completeness, matching V167.
    PERFORM tackle.fn_nebula_audit_log(
        'nebula.agent_connections', 'DELETE',
        (SELECT count(*) FROM old_rows),
        COALESCE((SELECT string_agg(r.role, ',') FROM old_rows r), ''));
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_agent_connections_audit_ins ON nebula.agent_connections;
DROP TRIGGER IF EXISTS trg_agent_connections_audit_del ON nebula.agent_connections;
-- PostgreSQL: transition tables require single-event triggers — one per op.
CREATE TRIGGER trg_agent_connections_audit_ins
AFTER INSERT ON nebula.agent_connections
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION nebula.trg_agent_connections_audit_ins();
CREATE TRIGGER trg_agent_connections_audit_del
AFTER DELETE ON nebula.agent_connections
REFERENCING OLD TABLE AS old_rows
FOR EACH STATEMENT EXECUTE FUNCTION nebula.trg_agent_connections_audit_del();

-- ── 5. Census read view (role-scoped, mirrors v_session_context_restore) ────
CREATE OR REPLACE VIEW nebula.v_agent_connections AS
SELECT
    c.conn_id,
    c.session_id,
    c.role,
    c.model,
    c.channel,
    c.lease_ref,
    l.status        AS lease_status,   -- informational, mirrors Q1 posture
    c.mcp_tools,
    c.procedure_cards,
    c.inbox_status,
    c.handoff_context,
    c.keychains,
    c.as_of,
    (c.lease_ref IS NOT NULL) AS is_leased,
    -- convenience: the census verdict in one field
    (jsonb_array_length(c.mcp_tools) > 0
     OR COALESCE((c.procedure_cards->>'available')::boolean, false)
     OR COALESCE((c.inbox_status->>'available')::boolean, false)
     OR COALESCE((c.handoff_context->>'digest_available')::boolean, false)
    ) AS has_affordances
FROM nebula.agent_connections c
LEFT JOIN tackle.role_leases l ON l.id = c.lease_ref
WHERE current_setting('vision.session_role', true) = c.role
  AND c.valid_until > now()
  AND c.recorded_until_dt = 'infinity'::timestamptz;

COMMENT ON VIEW nebula.v_agent_connections IS
'Census read scope: session role bound via GUC vision.session_role (mirrors Q3 posture), bitemporal-current rows only. lease_status informational; has_affordances = false marks a "blind boot" — a session whose context had no reachable surfaces at all.';

COMMIT;
