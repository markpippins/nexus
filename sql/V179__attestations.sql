-- =============================================================================
-- V179: nebula.attestations — persisted attestation/greenlight lineage
-- (DBA) — STAGED INERT: nothing on live changes until applied on explicit
-- operator go. Same activation posture as V167/V169/V170/V171/V172.
-- =============================================================================
-- Motivation (DBA, 2026-09-17): the attestation-chain gate contract was
-- demonstrated on live capabilities (PR #308, commit 34fa5fc3; discussions
-- thread 0d2c2fb8) but lives as ephemeral demo output and forum prose. This
-- migration persists the chain — verification_request → attestation →
-- greenlight — as append-only rows, so "who verified this, with what
-- evidence, citing which attestation" becomes queryable evidence
-- (nebula.attestation_lineage) instead of narrative.
--
-- The #308 gate contract is DDL-encoded:
--   G1 self-attestation   (ATP0001): attester_role <> requested_by
--   G2 evidence-free      (ATP0002): kind='attestation' requires non-empty,
--                                    non-blank citable evidence
--   G3 capability         (ATP0003): attester must hold
--                                    can_verify_work_requests on nebula.roles
--   G4 citation identity  (ATP0004): greenlight must cite an existing
--                                    kind='attestation' row for the SAME
--                                    work_ref — an attestation is an event
--                                    that already happened; you cannot cite
--                                    one that never persisted (the DDL
--                                    expression of event-identity from #308)
--   ATP0005: an attestation that cites must cite the verification_request
--            for the SAME work_ref (physical chain integrity)
--
-- Posture: append-only house contract (ATP010/ATP011, V169 pattern) plus
-- ATP012: attestations are events — on an open row ONLY the supersede close
-- (recorded_until_dt) may change; business columns are frozen.
-- Idempotent (CREATE IF NOT EXISTS / OR REPLACE / DROP-first triggers).
-- Gates: ATP-GATE-001 (alien-shape refusal), ATP-GATE-002 (world without a
-- roles surface must not silently hold unenforceable attestations),
-- ATP-GATE-003 (postcondition).
-- =============================================================================

BEGIN;

-- ── 0. Gates ----------------------------------------------------------------
-- ATP-GATE-001: a pre-existing nebula.attestations without our kind
-- vocabulary is an alien shape — CREATE IF NOT EXISTS would silently skip
-- the DDL and leave the contract unenforced.
-- (to_regclass() returns NULL for a missing relation; the ::regclass cast
-- RAISES — the nested IF guarantees the cast only runs when it exists.)
DO $$ BEGIN
    IF to_regclass('nebula.attestations') IS NOT NULL THEN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = to_regclass('nebula.attestations')
              AND conname  = 'attestations_kind_check') THEN
            RAISE EXCEPTION 'ATP-GATE-001: nebula.attestations already exists without attestations_kind_check — alien shape, refusing to graft the contract'
                USING ERRCODE = 'P0001';
        END IF;
    END IF;
END $$;

-- ATP-GATE-002: G3 resolves capability against nebula.roles. A world with
-- no roles surface would hold attestations whose capability gate is
-- silently dead — fail loudly instead.
DO $$ BEGIN
    IF to_regclass('nebula.roles') IS NULL THEN
        RAISE EXCEPTION 'ATP-GATE-002: nebula.roles does not exist — G3 would be unenforceable; a world that cannot attest must not hold attestations'
            USING ERRCODE = 'P0001';
    END IF;
END $$;

-- ── 1. Table ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS nebula.attestations (
    attestation_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_ref          text NOT NULL,        -- agent record / PR / artifact id of the work
    kind              text NOT NULL
        CONSTRAINT attestations_kind_check
            CHECK (kind IN ('verification_request', 'attestation', 'greenlight')),
    requested_by      text NOT NULL,        -- the work's AUTHORING role (G1 anchor)
    attester_role     text,                 -- kind='attestation'
    authority_role    text,                 -- kind='greenlight'
    evidence          jsonb NOT NULL DEFAULT '[]'::jsonb,  -- citable artifacts (G2)
    cites_id          uuid REFERENCES nebula.attestations (attestation_id)
                          ON DELETE RESTRICT,  -- greenlight → its gate-passed attestation
    session_id        text,                 -- nullable: un-shimmed sessions have none (V169 idiom)
    agent_record_id   uuid,                 -- soft pointer to the documenting agent record
    txid              text NOT NULL DEFAULT pg_current_xact_id()::text,
    recorded_on_dt    timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    -- kind/shape agreement: requests root chains; attestations may cite their
    -- request; only greenlights cite attestations
    CONSTRAINT attestations_chain_shape_check CHECK (
        (kind = 'verification_request' AND cites_id IS NULL)
     OR (kind = 'attestation')
     OR (kind = 'greenlight' AND cites_id IS NOT NULL)),
    -- role-column agreement per kind
    CONSTRAINT attestations_role_shape_check CHECK (
        (kind = 'attestation'       AND attester_role  IS NOT NULL AND authority_role IS NULL)
     OR (kind = 'greenlight'        AND authority_role IS NOT NULL AND attester_role  IS NULL)
     OR (kind = 'verification_request'))
);

CREATE INDEX IF NOT EXISTS idx_attestations_work_ref
    ON nebula.attestations (work_ref);
CREATE INDEX IF NOT EXISTS idx_attestations_cites
    ON nebula.attestations (cites_id) WHERE cites_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_attestations_kind
    ON nebula.attestations (kind);

-- ── 2. Chain guards: the #308 gate contract, enforced on every write ────────
CREATE OR REPLACE FUNCTION nebula.trg_attestations_chain_guards()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.kind = 'attestation' THEN
        -- G1: the authoring role cannot attest its own work
        IF NEW.attester_role = NEW.requested_by THEN
            RAISE EXCEPTION 'ATP0001: [G1-self-attestation] role % authored the work (%) and cannot attest it — verification must come from a second role',
                NEW.attester_role, NEW.work_ref
                USING ERRCODE = 'P0001';
        END IF;
        -- G2: an attestation with no citable evidence is void
        IF jsonb_array_length(NEW.evidence) = 0
           OR EXISTS (SELECT 1
                      FROM jsonb_array_elements_text(NEW.evidence) AS e(e)
                      WHERE btrim(e) = '') THEN
            RAISE EXCEPTION 'ATP0002: [G2-evidence-free] attestation for % carries no citable evidence — ''tests pass'' without named runs/artifacts is not verification',
                NEW.work_ref
                USING ERRCODE = 'P0001';
        END IF;
        -- G3: the attester must hold can_verify_work_requests on live roles.
        -- Missing role = capability absence = refusal (fail-closed; matches
        -- the #308 resolver's absent-not-attestable semantics).
        IF NOT EXISTS (
            SELECT 1 FROM nebula.roles r
            WHERE r.name = NEW.attester_role
              AND r.can_verify_work_requests) THEN
            RAISE EXCEPTION 'ATP0003: [G3-capability] role % does not hold can_verify_work_requests on live — not a valid attester',
                NEW.attester_role
                USING ERRCODE = 'P0001';
        END IF;
        -- ATP0005: an attestation that cites must cite the verification_request
        -- for the same work_ref (physical chain integrity)
        IF NEW.cites_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM nebula.attestations c
            WHERE c.attestation_id = NEW.cites_id
              AND c.kind = 'verification_request'
              AND c.work_ref = NEW.work_ref) THEN
            RAISE EXCEPTION 'ATP0005: attestation cites a row that is not the verification_request for % — chains must link physically',
                NEW.work_ref
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    IF NEW.kind = 'greenlight' THEN
        -- G4: greenlight must cite an EXISTING kind='attestation' row for the
        -- same work_ref. The subquery can only see attestations already
        -- inserted — the DDL expression of event-identity: an attestation is
        -- an event that happened, not a value the greenlight reconstructs.
        IF NEW.cites_id IS NULL
           OR NOT EXISTS (
               SELECT 1 FROM nebula.attestations c
               WHERE c.attestation_id = NEW.cites_id
                 AND c.kind = 'attestation'
                 AND c.work_ref = NEW.work_ref) THEN
            RAISE EXCEPTION 'ATP0004: [greenlight_without_verification_attestation] greenlight for % cites no gate-passed attestation row for that work_ref — attestation is not self-declared',
                NEW.work_ref
                USING ERRCODE = 'P0001';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_attestations_chain_guards ON nebula.attestations;
CREATE TRIGGER trg_attestations_chain_guards
BEFORE INSERT ON nebula.attestations
FOR EACH ROW EXECUTE FUNCTION nebula.trg_attestations_chain_guards();

-- ── 3. Append-only posture: attestations are immutable history ──────────────
CREATE OR REPLACE FUNCTION nebula.trg_attestations_immutability()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ATP010: attestations is append-only (house bitemporal contract; supersede, never delete)'
            USING ERRCODE = 'P0001';
    END IF;
    IF OLD.recorded_until_dt <> 'infinity'::timestamptz THEN
        RAISE EXCEPTION 'ATP011: recorded (superseded) attestation rows are frozen'
            USING ERRCODE = 'P0001';
    END IF;
    -- ATP012: an open attestation is an event — only the supersede close may
    -- change; every business column is frozen (evidence rewrites are refusals)
    IF to_jsonb(NEW) - 'recorded_until_dt'
       IS DISTINCT FROM to_jsonb(OLD) - 'recorded_until_dt' THEN
        RAISE EXCEPTION 'ATP012: attestations are events — only the supersede close (recorded_until_dt) may change on an open row'
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_attestations_immutability ON nebula.attestations;
CREATE TRIGGER trg_attestations_immutability
BEFORE UPDATE OR DELETE ON nebula.attestations
FOR EACH ROW EXECUTE FUNCTION nebula.trg_attestations_immutability();

-- ── 4. Lineage: the chain as queryable evidence ─────────────────────────────
-- One row per chain node, rooted at each verification_request, with depth and
-- cycle-guarded traversal (the self-FK + append-only already forbid cycles;
-- the path guard is belt and braces).
CREATE OR REPLACE VIEW nebula.attestation_lineage AS
WITH RECURSIVE chain AS (
    SELECT a.attestation_id AS root_id,
           a.attestation_id AS node_id,
           0                 AS depth,
           ARRAY[a.attestation_id] AS path
    FROM nebula.attestations a
    WHERE a.cites_id IS NULL
    UNION ALL
    SELECT c.root_id,
           n.attestation_id,
           c.depth + 1,
           c.path || n.attestation_id
    FROM nebula.attestations n
    JOIN chain c ON n.cites_id = c.node_id
    WHERE NOT n.attestation_id = ANY (c.path)
)
SELECT chain.root_id,
       chain.node_id   AS attestation_id,
       chain.depth,
       a.work_ref,
       a.kind,
       a.requested_by,
       a.attester_role,
       a.authority_role,
       a.evidence,
       a.cites_id,
       a.txid,
       a.recorded_on_dt
FROM chain
JOIN nebula.attestations a ON a.attestation_id = chain.node_id;

-- ── 5. Postcondition ────────────────────────────────────────────────────────
DO $$
DECLARE
    trg_count int;
    gate_keys int;
BEGIN
    SELECT count(*) INTO trg_count
    FROM pg_trigger
    WHERE tgrelid = 'nebula.attestations'::regclass AND NOT tgisinternal;
    IF trg_count <> 2 THEN
        RAISE EXCEPTION 'ATP-GATE-003: expected 2 non-internal triggers on nebula.attestations (guards + immutability), found %', trg_count
            USING ERRCODE = 'P0001';
    END IF;

    SELECT count(*) INTO gate_keys
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'nebula'
      AND p.proname = 'trg_attestations_chain_guards'
      AND p.prosrc LIKE '%ATP0001%'
      AND p.prosrc LIKE '%ATP0002%'
      AND p.prosrc LIKE '%ATP0003%'
      AND p.prosrc LIKE '%ATP0004%'
      AND p.prosrc LIKE '%ATP0005%';
    IF gate_keys <> 1 THEN
        RAISE EXCEPTION 'ATP-GATE-003: chain-guard function does not carry the four gate keys (ATP0001..0004)'
            USING ERRCODE = 'P0001';
    END IF;

    IF to_regclass('nebula.attestation_lineage') IS NULL THEN
        RAISE EXCEPTION 'ATP-GATE-003: nebula.attestation_lineage view missing after apply'
            USING ERRCODE = 'P0001';
    END IF;
END $$;

DO $$
DECLARE
    chain_rows int;
BEGIN
    SELECT count(*) INTO chain_rows FROM nebula.attestations;
    RAISE NOTICE 'V179 applied: nebula.attestations + lineage view installed; % chain event(s) recorded', chain_rows;
END $$;

COMMIT;
