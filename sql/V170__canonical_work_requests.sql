-- =============================================================================
-- V170 (DBA pre-stage — type-collapse deliverable): vision.canonical_work_requests
-- =============================================================================
--
-- ⚠️  DRAFT — NOT APPLIED TO LIVE. ⚠️
-- Additive, self-contained, inert-by-absence. No destructive statements; safe
-- to apply at any time; supersedes nothing. Companion to the type-collapse
-- disposition (discussions 89f99cbe, DBA comment d19a6a83): the ONE canonical
-- WorkRequest surface whose JSONB payload carries type-system-conforming
-- instances, per the operator's plan — spine relational, payload liftable to
-- MongoDB instance data keyed by the same logical identity.
--
-- ── Relationship to the existing groundwork ─────────────────────────────────
--   * DEPENDS ON V163 (#251): the shape registry + its active-shape resolver
--     (vision.canonical_work_request_shape_active) must exist — the payload
--     column DEFAULT and the WR0003 guard read them. Apply after V163.
--   * V163 (#251) put the LANDING GATE on legacy vision.work_requests:
--     business_key + relation_payload + shape_version columns, the
--     trg_canonical_wr_landing_guard, the sha256-pinned shape registry
--     (v0.1 ratified, record 41cc9b23), and canonical_wr_landing_refusals.
--     This migration builds the CLEAN class-3 canonical table that surface
--     converges onto post-ratification; the V163 gate's checks (P1020
--     relation_payload present, P1030 single-ratified shape) are the writer
--     contract this table expects, re-stated here as triggers so the table
--     is safe under ANY writer, not only the gate.
--   * The three roundtable inputs slot in as named constants (below); each
--     is a one-line edit at freeze time, not a redesign:
--       (a) WR Concept/Representation freeze  → payload envelope fingerprint
--       (b) Asset-envelope TypeSpec identity  → identity column set
--       (c) shape_registry v0.1 → v1.0        → DEFAULT_SHAPE_VERSION
--
-- ── Design positions (D1–D7) ────────────────────────────────────────────────
--   D1  CLASS-3 (PC7): normalized identity/authority/temporal spine; JSONB
--       payload holds type-conforming instance data ONLY — the shape
--       conformance authority is the registry + gate, never DDL CHECKs
--       (single authority; DDL cannot check a sha256-pinned artifact).
--   D2  BUSINESS IDENTITY: business_key is the stable dedup identity
--       (V163 C4 contract carried forward: UNIQUE partial index, NULLs
--       exempt — un-keyed drafts land without a business identity).
--   D3  LEASE PROVENANCE: lease_ref nullable-but-explicit; when present it
--       must exist in tackle.role_leases and agree on role (WR0001/WR0002,
--       mirroring SNAP001/002 + CON0001/0002: a lease is a scope artifact,
--       not an authority grant).
--   D4  APPEND-ONLY + SUPERSESSION: rows are immutable history (WR0010
--       delete-refusal, WR0011 frozen-row refusal). Lifecycle changes are
--       SUPERSESSION: close the current row (recorded_until_dt = now()) and
--       insert the successor — the house bitemporal contract, same as
--       V167/V169, and the mechanism that makes replay/recovery (the
--       capability-profile thread's profile 7) auditable.
--   D5  REFS-ONLY PAYLOAD INTERIOR (at the governance boundary): the payload
--       may carry whatever the frozen shape requires, but governance
--       references inside it (lease ids, record ids, artifact paths) are
--       refs — content authority lives at the referenced surfaces.
--   D6  AUDIT: statement-level NEBULA_AUDIT triggers (V156/V167/V169
--       pattern, self-contained fallback retained).
--   D7  READ SCOPE: v_canonical_work_requests binds vision.session_role
--       (Q3 posture) and exposes bitemporal-current rows only.
--
-- =============================================================================

BEGIN;

-- ── 0. Roundtable inputs — named constants (one-line edits at freeze) ──────
CREATE OR REPLACE FUNCTION vision.canonical_wr_default_shape_version()
RETURNS text LANGUAGE sql STABLE AS $$
    -- (c) registry promotion path: the ACTIVE ratified shape today.
    -- At v1.0 freeze this becomes a pinned constant (e.g. 'v1.0') if the
    -- roundtable wants the default decoupled from registry state.
    SELECT vision.canonical_work_request_shape_active();
$$;

-- ── 1. Canonical table ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vision.canonical_work_requests (
    wr_id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_key        text,             -- D2: stable dedup identity (NULL-exempt unique)
    role                text NOT NULL,    -- owning role (lease provenance binds here)
    model               text,             -- model identity per ruling R-1..R-3
    channel             text,             -- lease channel of the writing session
    lease_ref           uuid,             -- D3: NULL ⇒ unleased write (marked)
    shape_version       text NOT NULL DEFAULT vision.canonical_wr_default_shape_version(),
    payload_sha256      text CHECK (payload_sha256 IS NULL OR payload_sha256 ~ '^[0-9a-f]{64}$'),
    payload             jsonb NOT NULL DEFAULT '{}'::jsonb,  -- D1: type-conforming instance
    relation_payload    jsonb,            -- original-field preservation (P1020 pattern)
    status              text NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open','in_flight','blocked','resolved','superseded')),
    superseded_by       uuid,             -- set on the closed row when superseding
    as_of               timestamptz NOT NULL DEFAULT now(),
    -- house bitemporal pair (D4)
    valid_until         timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt      timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt   timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cks_canonical_wr_payload CHECK (jsonb_typeof(payload) = 'object'),
    CONSTRAINT cks_canonical_wr_relation CHECK (relation_payload IS NULL OR jsonb_typeof(relation_payload) = 'object')
);

COMMENT ON TABLE vision.canonical_work_requests IS
'Type-collapse canonical WorkRequest surface (V170 DBA pre-stage; disposition d19a6a83). ONE governed table: relational identity/authority/bitemporal spine + JSONB payload holding type-system-conforming instances (shape conformance authority = work_request_shape_registry + V163 landing gate, never DDL). Payload projects to MongoDB instance data keyed by the same logical identity; the store is a client of the type contract. Append-only with supersession (WR0010/0011). DRAFT — NOT APPLIED until the roundtable freezes the WR Concept, the TypeSpec fingerprint, and the v1.0 shape.';

-- D2: business_key is unique among ACTIVE (bitemporal-current) rows only.
-- The closed (superseded) row vacates the key for its successor — the
-- supersession helper's ordering depends on this predicate matching the
-- 'active' semantics exactly.
-- D2: business_key is unique among ACTIVE (bitemporal-current) rows only.
-- The closed (superseded) row vacates the key for its successor — the
-- supersession helper's ordering depends on this predicate matching the
-- 'active' semantics exactly.
CREATE UNIQUE INDEX IF NOT EXISTS uq_canonical_wr_business_key_active
    ON vision.canonical_work_requests (business_key)
    WHERE business_key IS NOT NULL
      AND recorded_until_dt = 'infinity'::timestamptz;
CREATE INDEX IF NOT EXISTS idx_canonical_wr_role_asof
    ON vision.canonical_work_requests (role, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_canonical_wr_lease
    ON vision.canonical_work_requests (lease_ref) WHERE lease_ref IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_canonical_wr_status
    ON vision.canonical_work_requests (status) WHERE recorded_until_dt = 'infinity'::timestamptz;
CREATE INDEX IF NOT EXISTS idx_canonical_wr_shape
    ON vision.canonical_work_requests (shape_version);

-- ── 2. Provenance guard: lease provenance + shape sanity (D3) ───────────────
CREATE OR REPLACE FUNCTION vision.trg_canonical_wr_provenance()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.lease_ref IS NOT NULL THEN
        IF NOT EXISTS (SELECT 1 FROM tackle.role_leases l WHERE l.id = NEW.lease_ref) THEN
            RAISE EXCEPTION 'WR0001: lease_ref % does not exist in tackle.role_leases (provenance guard)', NEW.lease_ref
                USING ERRCODE = 'P0001';
        END IF;
        IF EXISTS (SELECT 1 FROM tackle.role_leases l
                   WHERE l.id = NEW.lease_ref AND l.role IS NOT NULL AND l.role <> NEW.role) THEN
            RAISE EXCEPTION 'WR0002: lease % role does not match canonical WR role % (provenance guard)', NEW.lease_ref, NEW.role
                USING ERRCODE = 'P0001';
        END IF;
    END IF;
    -- shape sanity: a non-null shape_version must exist in the registry
    -- (full conformance is the GATE's authority — D1; this only refuses
    -- writes citing a shape the registry has never seen)
    IF NOT EXISTS (SELECT 1 FROM vision.work_request_shape_registry r
                   WHERE r.shape_version = NEW.shape_version) THEN
        RAISE EXCEPTION 'WR0003: shape_version % is not in work_request_shape_registry (conformance authority: registry + landing gate)', NEW.shape_version
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_canonical_wr_provenance ON vision.canonical_work_requests;
CREATE TRIGGER trg_canonical_wr_provenance
BEFORE INSERT OR UPDATE ON vision.canonical_work_requests
FOR EACH ROW EXECUTE FUNCTION vision.trg_canonical_wr_provenance();

-- ── 3. Append-only + supersession (D4) ──────────────────────────────────────
CREATE OR REPLACE FUNCTION vision.trg_canonical_wr_immutability()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'WR0010: canonical_work_requests is append-only (supersede via close+insert, never delete)'
            USING ERRCODE = 'P0001';
    END IF;
    IF OLD.recorded_until_dt <> 'infinity'::timestamptz THEN
        RAISE EXCEPTION 'WR0011: closed (superseded) canonical WR rows are frozen'
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_canonical_wr_immutability ON vision.canonical_work_requests;
CREATE TRIGGER trg_canonical_wr_immutability
BEFORE UPDATE OR DELETE ON vision.canonical_work_requests
FOR EACH ROW EXECUTE FUNCTION vision.trg_canonical_wr_immutability();

-- ── 4. Attributable trail (D6) ──────────────────────────────────────────────
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

CREATE OR REPLACE FUNCTION vision.trg_canonical_wr_audit_ins()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'vision.canonical_work_requests', 'INSERT',
        (SELECT count(*) FROM new_rows),
        COALESCE((SELECT string_agg(r.role, ',') FROM new_rows r), ''));
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION vision.trg_canonical_wr_audit_upd()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM tackle.fn_nebula_audit_log(
        'vision.canonical_work_requests', 'UPDATE',
        (SELECT count(*) FROM new_rows),
        COALESCE((SELECT string_agg(r.business_key, ',') FROM new_rows r), ''));
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION vision.trg_canonical_wr_audit_del()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    -- Unreachable while WR0010 stands; declared for pattern completeness.
    PERFORM tackle.fn_nebula_audit_log(
        'vision.canonical_work_requests', 'DELETE',
        (SELECT count(*) FROM old_rows),
        COALESCE((SELECT string_agg(r.business_key, ',') FROM old_rows r), ''));
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS trg_canonical_wr_audit_ins ON vision.canonical_work_requests;
DROP TRIGGER IF EXISTS trg_canonical_wr_audit_upd ON vision.canonical_work_requests;
DROP TRIGGER IF EXISTS trg_canonical_wr_audit_del ON vision.canonical_work_requests;
CREATE TRIGGER trg_canonical_wr_audit_ins
AFTER INSERT ON vision.canonical_work_requests
REFERENCING NEW TABLE AS new_rows
FOR EACH STATEMENT EXECUTE FUNCTION vision.trg_canonical_wr_audit_ins();
CREATE TRIGGER trg_canonical_wr_audit_upd
AFTER UPDATE ON vision.canonical_work_requests
REFERENCING NEW TABLE AS new_rows OLD TABLE AS old_rows
FOR EACH STATEMENT EXECUTE FUNCTION vision.trg_canonical_wr_audit_upd();
CREATE TRIGGER trg_canonical_wr_audit_del
AFTER DELETE ON vision.canonical_work_requests
REFERENCING OLD TABLE AS old_rows
FOR EACH STATEMENT EXECUTE FUNCTION vision.trg_canonical_wr_audit_del();

-- ── 5. Supersession helper (D4: the sanctioned lifecycle write) ─────────────
-- Ordering is load-bearing: the successor's wr_id is PRE-GENERATED, the
-- incumbent is closed WITH superseded_by = that id in one UPDATE (legal —
-- the row is not frozen yet), and only then is the successor inserted with
-- its explicit PK. This satisfies the unique active-business-key index at
-- every statement boundary (never two open rows with the same key) and
-- never back-patches a frozen row (WR0011 stays absolute).
CREATE OR REPLACE FUNCTION vision.supersede_canonical_work_request(
    p_wr_id uuid,
    p_successor_payload jsonb,
    p_actor_role text,
    p_actor_lease uuid DEFAULT NULL)
RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_old       vision.canonical_work_requests%ROWTYPE;
    v_new_id    uuid := gen_random_uuid();
BEGIN
    SELECT * INTO v_old FROM vision.canonical_work_requests
     WHERE wr_id = p_wr_id AND recorded_until_dt = 'infinity'::timestamptz
     FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'WR0012: no open canonical WR % to supersede', p_wr_id
            USING ERRCODE = 'P0001';
    END IF;
    -- close the incumbent, pointing at the successor-to-be
    UPDATE vision.canonical_work_requests
       SET recorded_until_dt = now(),
           status = 'superseded',
           superseded_by = v_new_id
     WHERE wr_id = p_wr_id;
    -- insert the successor carrying lineage (explicit wr_id = v_new_id)
    INSERT INTO vision.canonical_work_requests
        (wr_id, business_key, role, model, channel, lease_ref,
         shape_version, payload, relation_payload, status, as_of)
    VALUES
        (v_new_id, v_old.business_key, p_actor_role, v_old.model, v_old.channel,
         p_actor_lease, v_old.shape_version, p_successor_payload,
         v_old.relation_payload, 'open', now());
    RETURN v_new_id;
END;
$$;

-- ── 6. Read scope (D7) ──────────────────────────────────────────────────────
CREATE OR REPLACE VIEW vision.v_canonical_work_requests AS
SELECT
    w.wr_id, w.business_key, w.role, w.model, w.channel,
    w.lease_ref, l.status AS lease_status,
    w.shape_version, w.payload_sha256, w.payload, w.relation_payload,
    w.status, w.superseded_by, w.as_of,
    (w.lease_ref IS NOT NULL) AS is_leased
FROM vision.canonical_work_requests w
LEFT JOIN tackle.role_leases l ON l.id = w.lease_ref
WHERE current_setting('vision.session_role', true) = w.role
  AND w.valid_until > now()
  AND w.recorded_until_dt = 'infinity'::timestamptz;

COMMENT ON VIEW vision.v_canonical_work_requests IS
'Canonical WR read scope: session role bound via GUC vision.session_role (Q3 posture), bitemporal-current rows only, lease status informational. Consume via this view; direct table reads are for the gate and audit tooling.';

COMMIT;
