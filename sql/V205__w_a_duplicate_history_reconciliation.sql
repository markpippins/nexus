-- ============================================================================
-- V205 (Builder): W-A duplicate-history reconciliation ledger (append-only).
-- ============================================================================
-- Plan 8261653 — W-A duplicate-history reconciliation.
-- Docket thread c7c26160-5b22-463f-9ee0-6e4784bae0e8 (to-do).
--
-- WHY THIS MIGRATION EXISTS
--   The W-A envelope rule is *wrap-not-drop*: when historical records
--   collide on identity, every one of them keeps an explicit, individually
--   addressable disposition carrying its record id, its fingerprint and a
--   reason. Nothing is deleted; nothing is silently merged.
--
--   The envelope freeze conditions C1-C4 (DBA record e8faa405) each name a
--   surface whose identity discipline is what makes a duplicate set
--   detectable:
--
--     C1  resolution.receipt.payload_fingerprint must be NOT NULL AND
--         deterministically verified, so payload/fingerprint drift is
--         structurally impossible.
--     C2  resolution.canonical_asset.canonical_key must be backfilled at
--         envelope-apply time; a NULL identity makes the set ungroupable.
--     C3  resolution.execution_claim.subject_ref must be promoted to a
--         normalized link (no JSONB-only lineage).
--     C4  legacy flat evidence (vision.receipts, vision.tickets) normalizes
--         wrap-not-drop with an EXPLICIT disposition; discard is a
--         first-class disposition, never silence.
--
--   The census taken for this plan (2026-10-22) found the ledger surfaces
--   do not exist yet, so there is nowhere to record a disposition:
--     * resolution.reconciliation_* — absent (this migration creates them).
--     * resolution.migration_disposition — present (2,621 rows) but carries
--       NO per-row fingerprint: 77 groups share one identical target_refs
--       value, so the ledger cannot prove which row it dispositioned. That
--       gap is exactly what resolution.fingerprint_audit closes.
--
-- WHAT THIS MIGRATION DOES NOT DO
--   It creates EMPTY ledger surfaces. It does not insert dispositions, does
--   not touch any canonical row, and does not delete or rewrite history.
--   The builder-side inventory (python/nebula) renders the dispositions;
--   the DBA lane applies them, because the docket routes DB-layer steps
--   through the DBA.
--
-- Apply:  psql -d nexus -f sql/V205__w_a_duplicate_history_reconciliation.sql
--
-- Idempotency: NOT idempotent, by design — the preflight refuses loudly
--   when the ledger surfaces already exist, so a double apply is a visible
--   error rather than a silently divergent second ledger. This mirrors the
--   V198 preflight ("already exists ... V198 applied twice").
--
-- Rollback: the ledger surfaces hold only reconciliation evidence, so
--   dropping them discards NO canonical history (the underlying receipts,
--   tickets, manifests and assets are untouched by this migration). See
--   the rollback sketch at the tail.
-- ============================================================================

-- ── Preflight: pgcrypto (gen_random_uuid; the live nexus DB and the
--    ci-bootstrap both carry it) ────────────────────────────────────────────
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE EXCEPTION
            'V205 PREFLIGHT FAIL: pgcrypto not installed — resolution.reconciliation_disposition needs gen_random_uuid(). Install pgcrypto (CREATE EXTENSION pgcrypto) before applying V205.';
    END IF;
END $$;

-- ── Preflight: the ledger surfaces must not already exist. A double apply
--    would create a second, divergent ledger — the exact failure mode this
--    plan exists to prevent. ────────────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('resolution.reconciliation_disposition') IS NOT NULL THEN
        RAISE EXCEPTION
            'V205 PREFLIGHT FAIL: resolution.reconciliation_disposition already exists — V205 applied twice or a conflicting definition landed first.';
    END IF;
    IF to_regclass('resolution.reconciliation_set') IS NOT NULL THEN
        RAISE EXCEPTION
            'V205 PREFLIGHT FAIL: resolution.reconciliation_set already exists — V205 applied twice or a conflicting definition landed first.';
    END IF;
    IF to_regclass('resolution.fingerprint_audit') IS NOT NULL THEN
        RAISE EXCEPTION
            'V205 PREFLIGHT FAIL: resolution.fingerprint_audit already exists — V205 applied twice or a conflicting definition landed first.';
    END IF;
END $$;

BEGIN;

-- ════════════════════════════════════════════════════════════════════════════
-- S1 — reconciliation_set: the catalog of duplicate-history sets
--
-- One row per detected duplicate-history set. This is a CATALOG (mutable
-- status), not a ledger: the append-only surfaces below carry the history.
--
-- set_kind vocabulary (the four families the W-A inventory produces):
--   duplicate    — two or more records share an identity.
--   uncovered    — legacy records exist that carry NO disposition at all.
--   unidentified — records whose identity column is unusable (C2's NULL
--                  canonical_key), so the set cannot even be grouped.
--   unverified   — records carry an identity that is not verifiable against
--                  the canonical function (C1's missing DB-layer check).
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE resolution.reconciliation_set (
    set_id          text PRIMARY KEY,
    surface         text NOT NULL,
    set_kind        text NOT NULL
                    CONSTRAINT reconciliation_set_kind_check
                    CHECK (set_kind IN ('duplicate', 'uncovered',
                                        'unidentified', 'unverified')),
    condition_ref   text NOT NULL
                    CONSTRAINT reconciliation_set_condition_check
                    CHECK (length(btrim(condition_ref)) > 0),
    identity_kind   text NOT NULL,
    identity_value  text NOT NULL,
    member_count    integer NOT NULL DEFAULT 0
                    CONSTRAINT reconciliation_set_member_count_check
                    CHECK (member_count >= 0),
    status          text NOT NULL DEFAULT 'identified'
                    CONSTRAINT reconciliation_set_status_check
                    CHECK (status IN ('identified', 'dispositioning',
                                      'dispositioned', 'verified')),
    detected_at     timestamptz NOT NULL DEFAULT now(),
    verified_at     timestamptz,
    recorded_by     text NOT NULL DEFAULT 'builder',
    CONSTRAINT reconciliation_set_identity_key
                    UNIQUE (surface, set_kind, condition_ref, identity_kind,
                            identity_value)
);

COMMENT ON TABLE resolution.reconciliation_set IS
    'V205 (W-A): catalog of duplicate-history sets detected across the resolution and legacy flat surfaces. One row per set; the append-only evidence lives in reconciliation_disposition and fingerprint_audit.';

-- ════════════════════════════════════════════════════════════════════════════
-- S2 — reconciliation_disposition: the append-only disposition ledger
--
-- THE wrap-not-drop ledger. Every historical record that is merged,
-- preserved or archived gets exactly one disposition per generation,
-- carrying: the record reference, its fingerprint, the reason, and — for a
-- merge — the primary it was folded into.
--
-- APPEND-ONLY, enforced by trigger (S4). A re-decision is a NEW row with
-- generation+1, never an UPDATE: the earlier decision stays readable and
-- the history of the decision itself is preserved.
--
-- Disposition vocabulary (exactly three — the W-A contract):
--   merge   — the duplicate is folded into the primary. Requires a
--             primary_ref; content identity must already be proven by the
--             fingerprint.
--   preserve — the record keeps its own identity (content diverged, or the
--             set has no proven primary yet).
--   archive  — the record is retired from active views and retained as
--             history.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE resolution.reconciliation_disposition (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    set_id            text NOT NULL
                      REFERENCES resolution.reconciliation_set(set_id),
    member_ref        text NOT NULL
                      CONSTRAINT reconciliation_disposition_member_check
                      CHECK (length(btrim(member_ref)) > 0),
    member_fingerprint text NOT NULL
                      CONSTRAINT reconciliation_disposition_fingerprint_check
                      CHECK (length(btrim(member_fingerprint)) > 0),
    disposition       text NOT NULL
                      CONSTRAINT reconciliation_disposition_kind_check
                      CHECK (disposition IN ('merge', 'preserve', 'archive')),
    reason            text NOT NULL
                      CONSTRAINT reconciliation_disposition_reason_check
                      CHECK (length(btrim(reason)) > 0),
    primary_ref       text,
    generation        integer NOT NULL DEFAULT 1
                      CONSTRAINT reconciliation_disposition_generation_check
                      CHECK (generation >= 1),
    recorded_by       text NOT NULL DEFAULT 'builder',
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reconciliation_disposition_unique
                    UNIQUE (set_id, member_ref, generation),
    CONSTRAINT reconciliation_disposition_merge_primary
                    CHECK (disposition <> 'merge' OR primary_ref IS NOT NULL),
    CONSTRAINT reconciliation_disposition_not_self_merge
                    CHECK (primary_ref IS NULL OR primary_ref <> member_ref)
);

CREATE INDEX idx_reconciliation_disposition_set
    ON resolution.reconciliation_disposition (set_id);
CREATE INDEX idx_reconciliation_disposition_member
    ON resolution.reconciliation_disposition (member_ref);
CREATE INDEX idx_reconciliation_disposition_fingerprint
    ON resolution.reconciliation_disposition (member_fingerprint);

COMMENT ON TABLE resolution.reconciliation_disposition IS
    'V205 (W-A): append-only ledger of duplicate-history dispositions (merge | preserve | archive). Each row carries the record ref, its fingerprint and a reason. A re-decision is a new generation, never an update.';

-- ════════════════════════════════════════════════════════════════════════════
-- S3 — fingerprint_audit: the per-row fingerprint trail
--
-- resolution.migration_disposition records WHICH legacy row it dispositioned
-- (source_schema/source_table/source_pk) and WHAT it pointed at
-- (target_refs) — but it carries no fingerprint of the row itself, so two
-- distinct legacy rows sharing one target_refs value are indistinguishable
-- in the audit trail. The census for this plan found 77 such groups.
--
-- This table closes that gap: one fingerprint per audited record, append-only,
-- so every disposition is individually addressable by content.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE resolution.fingerprint_audit (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    surface      text NOT NULL
                 CONSTRAINT fingerprint_audit_surface_check
                 CHECK (length(btrim(surface)) > 0),
    source_ref   text NOT NULL
                 CONSTRAINT fingerprint_audit_source_check
                 CHECK (length(btrim(source_ref)) > 0),
    fingerprint  text NOT NULL
                 CONSTRAINT fingerprint_audit_fingerprint_check
                 CHECK (length(btrim(fingerprint)) > 0),
    set_id       text
                 REFERENCES resolution.reconciliation_set(set_id),
    generation   integer NOT NULL DEFAULT 1
                 CONSTRAINT fingerprint_audit_generation_check
                 CHECK (generation >= 1),
    recorded_by  text NOT NULL DEFAULT 'builder',
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fingerprint_audit_unique
                 UNIQUE (surface, source_ref, fingerprint, generation)
);

CREATE INDEX idx_fingerprint_audit_set
    ON resolution.fingerprint_audit (set_id);
CREATE INDEX idx_fingerprint_audit_fingerprint
    ON resolution.fingerprint_audit (fingerprint);

COMMENT ON TABLE resolution.fingerprint_audit IS
    'V205 (W-A): append-only per-row fingerprint trail. Closes the gap where resolution.migration_disposition carries no fingerprint of the row it dispositioned, so identical target_refs groups stay distinguishable.';

-- ════════════════════════════════════════════════════════════════════════════
-- S4 — append-only enforcement
--
-- The ledger and the fingerprint trail are EVIDENCE: rewriting them would
-- destroy the very history this plan is reconciling. Updates and deletes
-- are refused at the database level, not by convention.
--
-- Mirrors resolution.forbid_canonical_admission_mutation() (the Stage B
-- append-only precedent on resolution.receipt).
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION resolution.forbid_reconciliation_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'resolution.% rows are append-only (W-A reconciliation ledger, V205): % blocked',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER trg_reconciliation_disposition_append_only
    BEFORE UPDATE OR DELETE ON resolution.reconciliation_disposition
    FOR EACH ROW
    EXECUTE FUNCTION resolution.forbid_reconciliation_mutation();

CREATE TRIGGER trg_fingerprint_audit_append_only
    BEFORE UPDATE OR DELETE ON resolution.fingerprint_audit
    FOR EACH ROW
    EXECUTE FUNCTION resolution.forbid_reconciliation_mutation();

COMMENT ON FUNCTION resolution.forbid_reconciliation_mutation() IS
    'V205 (W-A): refuses UPDATE/DELETE on the reconciliation ledger and the fingerprint trail. Evidence is append-only — a re-decision is a new generation, never an edit.';

-- ════════════════════════════════════════════════════════════════════════════
-- S5 — set registration + the atomic disposition writer
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION resolution.upsert_reconciliation_set(
    p_set_id text,
    p_surface text,
    p_set_kind text,
    p_condition_ref text,
    p_identity_kind text,
    p_identity_value text,
    p_member_count integer,
    p_recorded_by text DEFAULT 'builder'
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_set_kind NOT IN ('duplicate', 'uncovered', 'unidentified', 'unverified') THEN
        RAISE EXCEPTION
            'V205: upsert_reconciliation_set kind must be duplicate|uncovered|unidentified|unverified (got %)', p_set_kind;
    END IF;
    IF p_member_count < 0 THEN
        RAISE EXCEPTION
            'V205: upsert_reconciliation_set member_count must be >= 0 (got %)', p_member_count;
    END IF;

    INSERT INTO resolution.reconciliation_set
        (set_id, surface, set_kind, condition_ref, identity_kind,
         identity_value, member_count, status, recorded_by)
    VALUES
        (p_set_id, p_surface, p_set_kind, p_condition_ref, p_identity_kind,
         p_identity_value, p_member_count, 'identified', p_recorded_by)
    ON CONFLICT (set_id) DO UPDATE
        SET surface        = EXCLUDED.surface,
            set_kind       = EXCLUDED.set_kind,
            condition_ref  = EXCLUDED.condition_ref,
            identity_kind  = EXCLUDED.identity_kind,
            identity_value = EXCLUDED.identity_value,
            member_count   = EXCLUDED.member_count;
END;
$$;

CREATE OR REPLACE FUNCTION resolution.record_reconciliation_disposition(
    p_set_id text,
    p_member_ref text,
    p_member_fingerprint text,
    p_disposition text,
    p_reason text,
    p_primary_ref text DEFAULT NULL,
    p_recorded_by text DEFAULT 'builder',
    p_generation integer DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_surface  text;
    v_kind     text;
    v_generation integer;
    v_id       uuid;
    v_dispositioned integer;
BEGIN
    SELECT surface, set_kind INTO v_surface, v_kind
      FROM resolution.reconciliation_set
     WHERE set_id = p_set_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'V205: no reconciliation_set % — register the set before recording a disposition', p_set_id;
    END IF;

    IF p_disposition NOT IN ('merge', 'preserve', 'archive') THEN
        RAISE EXCEPTION
            'V205: disposition must be merge|preserve|archive (got %)', p_disposition;
    END IF;

    IF p_reason IS NULL OR length(btrim(p_reason)) = 0 THEN
        RAISE EXCEPTION
            'V205: a disposition requires a reason — no silent records';
    END IF;

    IF p_disposition = 'merge' AND (p_primary_ref IS NULL OR p_primary_ref = p_member_ref) THEN
        RAISE EXCEPTION
            'V205: a merge disposition requires a distinct primary_ref (member %)', p_member_ref;
    END IF;

    IF p_generation IS NULL THEN
        SELECT COALESCE(MAX(generation), 0) + 1 INTO v_generation
          FROM resolution.reconciliation_disposition
         WHERE set_id = p_set_id AND member_ref = p_member_ref;
    ELSE
        v_generation := p_generation;
        IF v_generation < 1 THEN
            RAISE EXCEPTION
                'V205: generation must be >= 1 (got %)', v_generation;
        END IF;
    END IF;

    INSERT INTO resolution.reconciliation_disposition
        (set_id, member_ref, member_fingerprint, disposition, reason,
         primary_ref, generation, recorded_by)
    VALUES
        (p_set_id, p_member_ref, p_member_fingerprint, p_disposition,
         p_reason, p_primary_ref, v_generation, p_recorded_by)
    RETURNING id INTO v_id;

    INSERT INTO resolution.fingerprint_audit
        (surface, source_ref, fingerprint, set_id, generation, recorded_by)
    VALUES
        (v_surface, p_member_ref, p_member_fingerprint, p_set_id,
         v_generation, p_recorded_by)
    ON CONFLICT (surface, source_ref, fingerprint, generation) DO NOTHING;

    SELECT count(DISTINCT member_ref) INTO v_dispositioned
      FROM resolution.reconciliation_disposition
     WHERE set_id = p_set_id;

    UPDATE resolution.reconciliation_set
       SET status = CASE
                      WHEN v_dispositioned >= member_count AND member_count > 0
                        THEN 'dispositioned'
                      WHEN v_dispositioned > 0 THEN 'dispositioning'
                      ELSE 'identified'
                    END,
           verified_at = CASE
                            WHEN v_dispositioned >= member_count AND member_count > 0
                              THEN now()
                            ELSE verified_at
                          END
     WHERE set_id = p_set_id;

    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION resolution.reconciliation_effective_disposition(
    p_set_id text,
    p_member_ref text
) RETURNS TABLE (
    member_ref text,
    member_fingerprint text,
    disposition text,
    reason text,
    primary_ref text,
    generation integer,
    recorded_at timestamptz
)
LANGUAGE sql
STABLE
AS $$
    SELECT d.member_ref, d.member_fingerprint, d.disposition, d.reason,
           d.primary_ref, d.generation, d.recorded_at
      FROM resolution.reconciliation_disposition d
     WHERE d.set_id = p_set_id
       AND d.member_ref = p_member_ref
     ORDER BY d.generation DESC
     LIMIT 1;
$$;

COMMENT ON FUNCTION resolution.record_reconciliation_disposition(text, text, text, text, text, text, text, integer) IS
    'V205 (W-A): atomically records one disposition plus its fingerprint-audit row and advances the set status. Append-only: a re-decision writes the next generation.';
COMMENT ON FUNCTION resolution.reconciliation_effective_disposition(text, text) IS
    'V205 (W-A): the current (highest generation) disposition for one member of a set.';

-- ════════════════════════════════════════════════════════════════════════════
-- Postconditions: the three ledger surfaces exist, are empty, and are
-- append-only. The e2e (python/nebula/tests/test_v205_w_a_reconciliation_e2e.py)
-- asserts exactly this, plus the negative fixtures.
-- ════════════════════════════════════════════════════════════════════════════
DO $$
BEGIN
    IF to_regclass('resolution.reconciliation_set') IS NULL THEN
        RAISE EXCEPTION 'V205: resolution.reconciliation_set is absent after apply';
    END IF;
    IF to_regclass('resolution.reconciliation_disposition') IS NULL THEN
        RAISE EXCEPTION 'V205: resolution.reconciliation_disposition is absent after apply';
    END IF;
    IF to_regclass('resolution.fingerprint_audit') IS NULL THEN
        RAISE EXCEPTION 'V205: resolution.fingerprint_audit is absent after apply';
    END IF;
    IF EXISTS (SELECT 1 FROM resolution.reconciliation_disposition) THEN
        RAISE EXCEPTION 'V205: reconciliation_disposition must be EMPTY on apply — dispositions are applied by the DBA lane, not by the migration';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_reconciliation_disposition_append_only') THEN
        RAISE EXCEPTION 'V205: the append-only trigger on reconciliation_disposition is missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'trg_fingerprint_audit_append_only') THEN
        RAISE EXCEPTION 'V205: the append-only trigger on fingerprint_audit is missing';
    END IF;
END $$;

COMMIT;

-- Rollback sketch (not applied here):
--   DROP TRIGGER trg_reconciliation_disposition_append_only ON resolution.reconciliation_disposition;
--   DROP TRIGGER trg_fingerprint_audit_append_only ON resolution.fingerprint_audit;
--   DROP FUNCTION resolution.reconciliation_effective_disposition(text, text);
--   DROP FUNCTION resolution.record_reconciliation_disposition(text, text, text, text, text, text, text, integer);
--   DROP FUNCTION resolution.upsert_reconciliation_set(text, text, text, text, text, text, integer, text);
--   DROP FUNCTION resolution.forbid_reconciliation_mutation();
--   DROP TABLE resolution.fingerprint_audit;
--   DROP TABLE resolution.reconciliation_disposition;
--   DROP TABLE resolution.reconciliation_set;
-- Dropping the ledger discards NO canonical history: this migration never
-- touched a receipt, ticket, manifest, asset or disposition row.
-- ============================================================================
