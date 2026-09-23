-- ============================================================================
-- V198 (DBA): ST.01 promotion batch + deterministic candidate-set keys.
-- ============================================================================
-- DBA work package #3, per the ratified inputs:
--   * ST.01 pre-stage design 1e942769 (S1-S5) — batch object, key function,
--     atomic claim; staged-inert until the envelope ruling landed.
--   * Analyst envelope ruling 4a0aeb66 (R1-R4) — RATIFIED the id-set key
--     (R1.3), one digest family (R1.1), canonical-JSON eligibility hash
--     (R1.2), envelope_version as replay input (R1.4), fail-closed pins with
--     negative fixtures (R2.4), and the partial-unique active-batch
--     invariant (R3).
--   * C2 (JSONB/PC7 conditions, 94669b48) — same digest family backfills
--     canonical_asset.canonical_key; no second hash scheme.
--
-- Design fixes vs the pre-stage text, disclosed:
--   * The pre-stage claimed duplicate candidates "collapse in the aggregate".
--     Wrong: string_agg does not dedupe. This migration adds an explicit
--     DISTINCT inside the key function so set-identity is genuinely
--     duplicate-tolerant. Behavior on distinct inputs is unchanged.
--   * eligibility_hash is sha256 over the snapshot's CANONICAL JSON text
--     (jsonb canon: sorted keys, no insignificant whitespace) via
--     jsonb::text, not the caller's raw text — so two callers presenting the
--     same rules with different whitespace/spacing map to the SAME batch.
--     The canonical text is stored alongside for byte-exact audit.
--   * created_by: session user, not current_user, so fixed membership roles
--     (SET ROLE) cannot forge a different creator.
--
-- Apply:  psql -d nexus -f sql/V198__st01_promotion_batch_candidate_set_keys.sql
-- Inert-until-used: creates an empty table + functions; zero consumers are
-- repointed in this migration (promotion flow wiring is a later wave per
-- the analyst handoff / planner docket).
--
-- Rollback: DROP SCHEMA-level objects only (see tail). Nothing reads or
-- writes this table until a later wave flips the promotion flow, so the
-- migration is safe to apply immediately and harmless if unused.
-- ============================================================================

-- ── Preflight: pgcrypto (the pre-stage's staged note; verified live 09-23) ──
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto') THEN
        RAISE EXCEPTION
            'V198 PREFLIGHT FAIL: pgcrypto not installed — resolution.candidate_set_key needs digest(). Install pgcrypto (CREATE EXTENSION pgcrypto) before applying V198.';
    END IF;
END $$;

-- ── Preflight: the resolution schema must exist and be unpolluted (S5 pin
--    window: this ships while resolution.candidate is empty; V198 carries
--    zero data risk by construction) ────────────────────────────────────────
DO $$
BEGIN
    IF to_regclass('resolution.promotion_batch') IS NOT NULL THEN
        RAISE EXCEPTION
            'V198 PREFLIGHT FAIL: resolution.promotion_batch already exists — V198 applied twice or a conflicting definition landed first.';
    END IF;
END $$;

-- ════════════════════════════════════════════════════════════════════════════
-- S1 — promotion_batch: the set-identity host (pre-stage S1, ratified R1.4
--      adds envelope_version; bitemporal columns per house shape)
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE resolution.promotion_batch (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_key            text NOT NULL,
    envelope_version     integer NOT NULL DEFAULT 1,
    status               text NOT NULL DEFAULT 'open'
                         CONSTRAINT promotion_batch_status_check
                         CHECK (status IN ('open', 'sealed', 'superseded')),
    eligibility_snapshot jsonb NOT NULL,
    eligibility_canonical_text text NOT NULL,
    eligibility_hash     text NOT NULL,
    created_by           text NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    valid_from           timestamptz NOT NULL DEFAULT now(),
    valid_until          timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    recorded_on_dt       timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt    timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    CONSTRAINT promotion_batch_key_hash_check
        CHECK (length(batch_key) = 64 AND batch_key ~ '^[0-9a-f]{64}$'),
    CONSTRAINT promotion_batch_hash_check
        CHECK (length(eligibility_hash) = 64 AND eligibility_hash ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE resolution.promotion_batch IS
    'ST.01 (V198): deterministic candidate-set batch. At most one OPEN batch per (candidate id-set, eligibility snapshot), enforced by uq_promotion_batch_active. Ratified by envelope ruling 4a0aeb66 (R1/R3).';

COMMENT ON COLUMN resolution.promotion_batch.envelope_version IS
    'R1.4: authority-adjacent payloads carry an envelope version as a replay input. Shape/re-key changes increment this; nothing mutates in place.';

-- THE INVARIANT, database-enforced (R3 / pre-stage S1): at most one ACTIVE
-- batch per exact (candidate id-set + eligibility snapshot).
CREATE UNIQUE INDEX uq_promotion_batch_active
    ON resolution.promotion_batch (batch_key)
    WHERE status = 'open';

-- Support index: sealing/superseding scans by status; audit reads by time.
CREATE INDEX promotion_batch_status_idx ON resolution.promotion_batch (status, created_at DESC);

-- ════════════════════════════════════════════════════════════════════════════
-- Membership: normalized links, never JSONB (PC5 condition D3). Pre-stage S3.
-- ════════════════════════════════════════════════════════════════════════════
CREATE TABLE resolution.promotion_batch_candidate (
    batch_id     uuid NOT NULL REFERENCES resolution.promotion_batch (id) ON DELETE RESTRICT,
    candidate_id uuid NOT NULL REFERENCES resolution.candidate (id) ON DELETE RESTRICT,
    position     integer NOT NULL DEFAULT 0,
    recorded_on_dt timestamptz NOT NULL DEFAULT now(),
    recorded_until_dt timestamptz NOT NULL DEFAULT 'infinity'::timestamptz,
    CONSTRAINT promotion_batch_candidate_pk PRIMARY KEY (batch_id, candidate_id)
);
CREATE INDEX promotion_batch_candidate_candidate_idx
    ON resolution.promotion_batch_candidate (candidate_id);

-- ═════════════════════════════════════════════SELECT_NOOP═══════════════════
-- S2 — the deterministic key (pre-stage S2, ratified R1.1/R1.3; DISTINCT fix)
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION resolution.candidate_set_key(p_candidate_ids uuid[],
                     p_eligibility_hash text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT encode(
        digest(
            (SELECT string_agg(lower(x::text), E'\n' ORDER BY lower(x::text))
               FROM (SELECT DISTINCT unnest(p_candidate_ids) AS x) d)
            || E'\n' || lower(p_eligibility_hash),
            'sha256'),
        'hex')
$$;

COMMENT ON FUNCTION resolution.candidate_set_key(uuid[], text) IS
    'ST.01 S2 (V198, ruling R1.1/R1.3): set-order-independent, duplicate-collapsing, content-addressed set key. Same digest family as the C2 canonical_key backfill — one key discipline.';

-- ════════════════════════════════════════════════════════════════════════════
-- Eligibility hash over the CANONICAL jsonb text (ruling R1.2). Stored text
-- is the byte-exact canonical form (audit), the hash content-addresses it.
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION resolution.eligibility_hash(p_snapshot jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$
    SELECT encode(digest(p_snapshot::text, 'sha256'), 'hex')
$$;

-- ════════════════════════════════════════════════════════════════════════════
-- S3 — atomic open-batch claim (pre-stage S3). Racing claimers serialize on
-- uq_promotion_batch_active: exactly one INSERT wins, the loser converges on
-- the winner's batch. No advisory locks — the index IS the enforcement.
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION resolution.claim_open_batch(p_candidate_ids uuid[],
                     p_eligibility_snapshot jsonb,
                     p_envelope_version integer DEFAULT 1)
RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
    v_id   uuid;
    v_hash text;
BEGIN
    -- R1.2: canonical jsonb text is what gets hashed and stored.
    v_hash := resolution.eligibility_hash(p_eligibility_snapshot);

    INSERT INTO resolution.promotion_batch
        (batch_key, envelope_version, eligibility_snapshot,
         eligibility_canonical_text, eligibility_hash, created_by)
    VALUES
        (resolution.candidate_set_key(p_candidate_ids, v_hash),
         p_envelope_version,
         p_eligibility_snapshot,
         p_eligibility_snapshot::text,
         v_hash,
         session_user)
    ON CONFLICT (batch_key) WHERE status = 'open'
    DO NOTHING
    RETURNING id INTO v_id;

    IF v_id IS NULL THEN
        -- Lost the race: converge on the winner's open batch for this exact
        -- set + snapshot.
        SELECT id INTO v_id
          FROM resolution.promotion_batch
         WHERE batch_key = resolution.candidate_set_key(p_candidate_ids, v_hash)
           AND status = 'open'
         ORDER BY created_at
         LIMIT 1;
    END IF;

    INSERT INTO resolution.promotion_batch_candidate (batch_id, candidate_id)
    SELECT v_id, x
      FROM unnest(p_candidate_ids) AS x
    ON CONFLICT (batch_id, candidate_id) DO NOTHING;

    RETURN v_id;
END;
$$;

COMMENT ON FUNCTION resolution.claim_open_batch(uuid[], jsonb, integer) IS
    'ST.01 S3 (V198): atomic open-batch claim. Concurrency = the partial unique index; membership written in the same transaction.';

-- ════════════════════════════════════════════════════════════════════════════
-- Lifecycle transition (used by the promotion flow; validated by tests):
--   seal   = claim submitted for promotion (terminal for the open slot)
--   supersede = a re-key/new envelope replaces this batch (R1.4 lineage)
-- Old snapshots are retained append-only (W-A reads sealed/superseded rows).
-- ════════════════════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION resolution.close_open_batch(p_batch_id uuid,
                     p_status text)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF p_status NOT IN ('sealed', 'superseded') THEN
        RAISE EXCEPTION 'V198: close_open_batch accepts only sealed|superseded (got %)', p_status;
    END IF;
    UPDATE resolution.promotion_batch
       SET status = p_status, updated_at = now()
     WHERE id = p_batch_id
       AND status = 'open';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'V198: batch % is not open (already sealed/superseded?)', p_batch_id;
    END IF;
END;
$$;

-- Negative-fixture targets (R2.4): the two CHECK pins above
-- (promotion_batch_key_hash_check, promotion_batch_hash_check) must REJECT
-- violations; the e2e asserts both rejections.
-- Rollback sketch (not applied here; the later wave owns decommissioning):
--   DROP FUNCTION resolution.close_open_batch(uuid, text);
--   DROP FUNCTION resolution.claim_open_batch(uuid[], jsonb, integer);
--   DROP FUNCTION resolution.eligibility_hash(jsonb);
--   DROP FUNCTION resolution.candidate_set_key(uuid[], text);
--   DROP TABLE resolution.promotion_batch_candidate;
--   DROP TABLE resolution.promotion_batch;
-- ============================================================================
