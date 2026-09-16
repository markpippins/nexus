-- =============================================================================
-- V167 (DBA pre-stage — role-adoption continuity): session_context_snapshots
-- canonical surface for cross-session context, per discussions thread 65fe85a8.
--
-- ⚠️  DRAFT — NOT APPLIED TO LIVE. ⚠️
-- This migration is the pre-staged artifact of the roundtable continuity
-- design. It MUST NOT be applied until the roundtable answers the three open
-- questions below (each is drafted with an explicit DBA position and a
-- deferrable alternative; ratification edits touch named constants only).
--
-- Design positions encoded (from the thread):
--   * CANONICAL-FIRST: snapshots are agent-record-class canonical rows in PG,
--     projected to Mongo like every other artifact. Restore paths read THIS
--     table; the Mongo projection is a derived cache, never continuity
--     authority (anti-pattern: WAL-archive-as-backup).
--   * NULLABLE lease_ref: unleased sessions may snapshot, but the row is
--     explicitly marked (lease_ref IS NULL ⇒ content blob, low-trust context;
--     leased ⇒ attestable artifact with scope + provenance).
--   * READ-SET MANIFEST: leased snapshots carry the Keychains read-set the
--     session was actually permitted to see (refs-only — manifests reference
--     grants, never embed content).
--   * LEVEL-FILTER PROVENANCE: which altitude constraints shaped the digest
--     ride with the row.
--   * MODEL IDENTITY: role vs model distinction per ruling R-1..R-3.
--
-- The three open roundtable questions, drafted as:
--   Q1 restore-time lease liveness: deferrable — the restore VIEW exposes
--      lease_status via LEFT JOIN (informational only); the v0.1 writer/reader
--      contract does not hard-require ACTIVE at restore time. Flipping to
--      strict is a one-line view change after ratification.
--   Q2 retention: deferrable — hard TTL OFF by default (rows keep the house
--      bitemporal sentinels); strict TTL is a single documented GUC flip.
--   Q3 cross-role visibility: enforced now — restore scope binds the session
--      role to the snapshot role (session GUC vision.session_role); digest
--      assembly for a role reads only that role's snapshots (plus any
--      all-scope grants the roundtable later defines).
-- =============================================================================

BEGIN;

-- ── 1. Canonical table ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nebula.session_context_snapshots (
    snapshot_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    role                text NOT NULL,
    model               text,
    lease_ref           uuid,            -- NULL ⇒ unleased content blob (marked)
    read_set_manifest   jsonb,           -- refs-only: keychain/grant refs, no content
    source_records      jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{record_id, as_of}]
    digest_payload      jsonb NOT NULL,  -- the compacted context itself
    level_filter_primary text,
    level_filter_allowed text,
    as_of               timestamptz NOT NULL DEFAULT now(),
    -- house bitemporal pair (Q2 deferrable: strict TTL is a constant flip)
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cks_session_context_snapshots_digest
        CHECK (jsonb_typeof(digest_payload) = 'object'),
    CONSTRAINT cks_session_context_snapshots_readset
        CHECK (read_set_manifest IS NULL OR jsonb_typeof(read_set_manifest) = 'object'),
    CONSTRAINT cks_session_context_snapshots_sources
        CHECK (jsonb_typeof(source_records) = 'array'),
    CONSTRAINT cks_session_context_snapshots_lease_scoped
        CHECK (lease_ref IS NULL OR read_set_manifest IS NOT NULL)
        -- a leased snapshot MUST carry its read-set manifest (attestable-artifact rule)
);

COMMENT ON TABLE nebula.session_context_snapshots IS
'Role-adoption continuity: canonical cross-session context snapshots (discussions 65fe85a8). Canonical-first — projected to Mongo, never sourced from it. lease_ref NULL marks an unleased content blob (low-trust context); leased rows carry read_set_manifest (refs-only) and are attestable artifacts. NOT APPLIED until roundtable answers Q1-Q3.';

CREATE INDEX IF NOT EXISTS idx_session_context_snapshots_role_asof
    ON nebula.session_context_snapshots (role, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_session_context_snapshots_lease
    ON nebula.session_context_snapshots (lease_ref) WHERE lease_ref IS NOT NULL;

-- ── 2. Restore eligibility view (Q1 deferrable, informational lease status) ─
CREATE OR REPLACE VIEW nebula.v_session_context_restore AS
SELECT
    s.snapshot_id,
    s.role,
    s.model,
    s.lease_ref,
    l.status        AS lease_status,   -- informational: strictness is Q1
    s.read_set_manifest,
    s.source_records,
    s.digest_payload,
    s.level_filter_primary,
    s.level_filter_allowed,
    s.as_of,
    (s.lease_ref IS NOT NULL) AS is_leased
FROM nebula.session_context_snapshots s
LEFT JOIN tackle.role_leases l ON l.id = s.lease_ref
WHERE current_setting('vision.session_role', true) = s.role
  AND s.valid_until > now()
  AND s.recorded_until_dt = 'infinity'::timestamptz
  AND (s.lease_ref IS NULL OR TRUE);  -- leased rows visible; trust marker exposed

COMMENT ON VIEW nebula.v_session_context_restore IS
'Restore scope for digest assembly: session role bound via GUC vision.session_role (Q3 enforced), bitemporal-current rows only. lease_status is informational (Q1 deferrable); is_leased distinguishes attestable artifacts from marked content blobs.';

-- ── 3. Provenance guard: leased rows must carry a real lease ref ───────────
CREATE OR REPLACE FUNCTION nebula.trg_session_context_snapshot_provenance()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.lease_ref IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM tackle.role_leases l WHERE l.id = NEW.lease_ref) THEN
            RAISE EXCEPTION 'SNAP001: lease_ref % does not exist in tackle.role_leases (provenance guard)', NEW.lease_ref
                USING ERRCODE = 'P0001';
        END IF;
        -- lease/role agreement: a leased snapshot belongs to the lease role
        IF EXISTS (SELECT 1 FROM tackle.role_leases l
                   WHERE l.id = NEW.lease_ref AND l.role IS NOT NULL AND l.role <> NEW.role) THEN
            RAISE EXCEPTION 'SNAP002: lease % role does not match snapshot role % (provenance guard)', NEW.lease_ref, NEW.role
                USING ERRCODE = 'P0001';
        END IF;
        -- FORM-CHECK ONLY (mirrors the WR guard precedent): liveness is the
        -- writer's concern (Q1). The lease must merely exist.
        IF NEW.read_set_manifest IS NULL THEN
            RAISE EXCEPTION 'SNAP003: leased snapshot requires read_set_manifest (attestable-artifact rule); lease_ref %', NEW.lease_ref
                USING ERRCODE = 'P0001';
        END IF;
    ELSE
        -- unleased rows must not fake a manifest without a lease context
        IF NEW.read_set_manifest IS NOT NULL THEN
            RAISE EXCEPTION 'SNAP004: read_set_manifest requires a lease_ref (no scope without a lease)'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_session_context_snapshot_provenance ON nebula.session_context_snapshots;
CREATE TRIGGER trg_session_context_snapshot_provenance
BEFORE INSERT OR UPDATE ON nebula.session_context_snapshots
FOR EACH ROW EXECUTE FUNCTION nebula.trg_session_context_snapshot_provenance();

-- ── 4. Append-only posture: recorded rows are immutable history ────────────
CREATE OR REPLACE FUNCTION nebula.trg_session_context_snapshot_immutability()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'SNAP010: session_context_snapshots is append-only (house bitemporal contract; supersede, never delete)'
            USING ERRCODE = 'P0001';
    END IF;
    -- recorded rows cannot be rewritten: recorded_until_dt closed ⇒ frozen
    IF OLD.recorded_until_dt <> 'infinity'::timestamptz THEN
        RAISE EXCEPTION 'SNAP011: recorded (superseded) snapshot rows are frozen'
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_session_context_snapshot_immutability ON nebula.session_context_snapshots;
CREATE TRIGGER trg_session_context_snapshot_immutability
BEFORE UPDATE OR DELETE ON nebula.session_context_snapshots
FOR EACH ROW EXECUTE FUNCTION nebula.trg_session_context_snapshot_immutability();

-- ── 5. Attributable trail: statement-level audit, NEBULA_AUDIT category ────
-- Statement triggers with transition tables; reuses tackle.fn_nebula_audit_log
-- when present (V156), else falls back to a self-contained log write so this
-- migration can bootstrap a fresh database alone.
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

-- One audit function per event: transition tables are only in scope for the
-- events that declare them, so a shared dispatching function cannot reference
-- both (PL/pgSQL plans every branch).
CREATE OR REPLACE FUNCTION nebula.trg_session_context_snapshot_audit_ins()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'nebula.session_context_snapshots', 'INSERT',
        (SELECT count(*) FROM new_rows),
        COALESCE((SELECT string_agg(r.role, ',') FROM new_rows r), ''));
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION nebula.trg_session_context_snapshot_audit_upd()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'nebula.session_context_snapshots', 'UPDATE',
        (SELECT count(*) FROM new_rows),
        COALESCE((SELECT string_agg(r.role, ',') FROM new_rows r), ''));
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION nebula.trg_session_context_snapshot_audit_del()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'nebula.session_context_snapshots', 'DELETE',
        (SELECT count(*) FROM old_rows),
        COALESCE((SELECT string_agg(r.role, ',') FROM old_rows r), ''));
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_session_context_snapshots_audit_ins ON nebula.session_context_snapshots;
DROP TRIGGER IF EXISTS trg_session_context_snapshots_audit_upd ON nebula.session_context_snapshots;
DROP TRIGGER IF EXISTS trg_session_context_snapshots_audit_del ON nebula.session_context_snapshots;
-- PostgreSQL: transition tables require single-event triggers — one per op,
-- all sharing the dispatching function above.
CREATE TRIGGER trg_session_context_snapshots_audit_ins
AFTER INSERT ON nebula.session_context_snapshots
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION nebula.trg_session_context_snapshot_audit_ins();
CREATE TRIGGER trg_session_context_snapshots_audit_upd
AFTER UPDATE ON nebula.session_context_snapshots
REFERENCING NEW TABLE AS new_rows OLD TABLE AS old_rows
FOR EACH STATEMENT EXECUTE FUNCTION nebula.trg_session_context_snapshot_audit_upd();
CREATE TRIGGER trg_session_context_snapshots_audit_del
AFTER DELETE ON nebula.session_context_snapshots
REFERENCING OLD TABLE AS old_rows
FOR EACH STATEMENT EXECUTE FUNCTION nebula.trg_session_context_snapshot_audit_del();

-- TRUNCATE bypasses row-level triggers entirely — refuse it at statement level
-- so the append-only contract has no bypass (SNAP012).
CREATE OR REPLACE FUNCTION nebula.trg_session_context_snapshot_no_truncate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'SNAP012: session_context_snapshots refuses TRUNCATE (append-only surface)'
        USING ERRCODE = 'P0001';
END;
$$;

DROP TRIGGER IF EXISTS trg_session_context_snapshots_no_truncate ON nebula.session_context_snapshots;
CREATE TRIGGER trg_session_context_snapshots_no_truncate
BEFORE TRUNCATE ON nebula.session_context_snapshots
FOR EACH STATEMENT EXECUTE FUNCTION nebula.trg_session_context_snapshot_no_truncate();

-- ── 6. Postconditions ───────────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('nebula.session_context_snapshots') IS NULL THEN
        RAISE EXCEPTION 'V167 postcondition failed: table missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_views WHERE schemaname='nebula' AND viewname='v_session_context_restore') THEN
        RAISE EXCEPTION 'V167 postcondition failed: restore view missing';
    END IF;
    IF (SELECT count(*) FROM pg_trigger
        WHERE tgrelid = 'nebula.session_context_snapshots'::regclass
          AND NOT tgisinternal) < 4 THEN
        RAISE EXCEPTION 'V167 postcondition failed: guards/audit triggers missing';
    END IF;
    RAISE NOTICE 'V167 postconditions: all passed';
END;
$$;

COMMIT;
