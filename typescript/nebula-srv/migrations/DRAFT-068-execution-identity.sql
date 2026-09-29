-- =============================================================================
-- DRAFT — NOT APPLIED, NOT AUTHORISED TO RUN. DO NOT RENAME INTO THE RUNNER.
-- =============================================================================
--
-- CDLC A2a — execution identity (Tier 1) and the Tier-2 census marker.
--
-- Implements DBA ruling dffa404e. Implements the Tier-1/Tier-2 halves of the
-- three-tier split in 6629b009. Shape matches lib/execution-identity.ts
-- (PR #639) and the contract in typespec/v1/nexus-broker/typescript/models.tsp.
--
-- ## Why this file is NOT numbered
--
-- src/migrate.ts selects files with /^(\d{3})-.*\.sql$/ and applies every
-- numbered file above the recorded version in ascending order. A number here
-- would therefore make this APPLY AUTOMATICALLY on next boot. It has no
-- number, so the runner cannot pick it up. That is deliberate.
--
-- Reason: the ruling states —
--   "This ruling authorizes schema design for A2a only. It does not authorize
--    any migration to be applied."
--
-- ## All DBA questions are now ANSWERED (records d15c197f, 3aa390da, 57420389)
--
-- This header used to carry open questions. They are ruled. Refreshed per the DBA's
-- F3 so the file stops asking things that have been settled.
--
-- Q1 store      -> **nebula, YES**, binding on store placement. Stronger ground than
--                  "nebula is canonical": the singular `execution` SCHEMA is the
--                  receipt-stream family (`execution.attempts/.leases/.receipts/
--                  .requests`) -- a transport/claims layer of append-only streams.
--                  A durable execution ENTITY is a different concept and belongs in
--                  nebula, not there, or it would conflate stream with subject.
-- Q2 free name  -> **YES**, verified: zero tables or views in `nebula` matching
--                  %execution%. CAUTION kept below: the `execution` schema is
--                  singular and this table is `nebula.executions` plural.
-- Q3 number     -> **068**. Duplicate 055 resolved. 059-066 are DEAD: the runner is
--                  forward-only from MAX(ledger version) and the live ledger is 67, so
--                  a 059-066 file would be silently skipped forever while the runner
--                  reported healthy. The DBA ceded its own claim and renumbered its
--                  forensic draft 068 -> 069, so no second claimant remains.
-- Q4 immutability -> **YES, append-only**, hard trigger. Added below.
-- Design decisions 1-3 -> **all three accepted** (`''`-forbidding CHECK, CASE over
--                  AND for rate bounds, NOT NULL preservation noted as load-bearing).
--
-- ## Still unnumbered on purpose, and the gate that changes that
--
-- Record 5ab78e30: the DBA's target-identity gate for the startup runner, prompted by
-- SEV3 9a70c8c5 (a worktree nebula-srv booted against the LIVE database and the runner
-- applied migrations 056/057 to production) and endorsed in 3aa390da. Root cause is
-- worse than a mistake: `index.ts` builds the Pool with defaults localhost:5432/nexus,
-- and on this host **the default IS production**. The scratch PORT in the incident was
-- the HTTP port and had no bearing on the PG target. There is no target-identity
-- awareness at all.
--
-- Per 57420389, land the gate first or in the same PR as numbering. Numbering this
-- file before the gate exists re-creates the hazard the gate is meant to close.
--
-- ## Verification status — read this before applying
--
-- I could NOT parse or execute this DDL locally: no PostgreSQL socket and no
-- credentials in the worktree. My first merged version carried a trailing comma after
-- the last table constraint, which made it **unexecutable** -- the DBA caught it by
-- applying the merged file to a throwaway PostgreSQL 17.11 database, and confirmed
-- "not parsed by any PostgreSQL" was the correct caveat. The F1 fix is that one
-- character. Everything below is what the DBA verified in their scratch DB, not me.
--
-- Re-verification is needed after the F1/F2/F3 changes in this revision.
--
-- ## R9
-- Operator answered YES -- replicate to vanadium. Held, not actioned: per the
-- dba-schema-migration-path, R9 follows application, not the authoring of this file.
--
-- =============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS nebula.executions (

    -- ── Identity ──────────────────────────────────────────────────────────
    -- DBA condition 1: distinct, independently minted. Never derived from, or
    -- normalized against, session_id. The default exists so a caller may mint
    -- at walk start (6629b009) and supply it explicitly, or let the database
    -- assign one for an out-of-band insert; both yield an independent uuid.
    execution_id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- DBA condition 2: a text REFERENCE, not a parent. Many executions per
    -- session. No FK, no uniqueness, no parent lookup is implied by this
    -- column. Carries the existing named-session convention
    -- (e.g. 'reviewer-20260703-112402-831cbac9'), or NULL when un-shimmed.
    --
    -- DBA condition 3: a calendar session would need its OWN separately named
    -- column with its own FK. There is deliberately NO calendar_session_ref
    -- here. A uuid-shaped value in session_id is the vision.sessions concept
    -- colliding with this reference family, so the application validator
    -- (lib/execution-identity.ts) refuses one; this CHECK additionally forbids
    -- the empty string, so a uuid cannot be smuggled in as text.
    session_id         text,

    -- ── Tier-2 census marker ──────────────────────────────────────────────
    -- 6629b009: explicit tri-state, ABSENCE IS ILLEGAL, never a durable
    -- record-store row. NOT NULL is how absence becomes structurally
    -- impossible. A5's threshold is "30% sampled-missing", which is only
    -- computable if a missing marker can never be read as a disabled census.
    --
    -- The three states:
    --   census_enabled=false                          -> census not enabled
    --   census_enabled=true, census_sampled=false      -> enabled, not sampled
    --   census_enabled=true, census_sampled=true       -> sampled
    --
    -- These are columns on the execution record itself, NOT rows in
    -- nebula.agent_records. A canonical write per execution to record "nothing
    -- happened" would invert the cost model and abuse the artifact store as a
    -- telemetry sink; that is exactly what the ruling forbids.
    census_enabled     boolean NOT NULL,
    census_sampled     boolean NOT NULL,

    -- Non-null exactly when sampled. The join key to the single Tier-3 census
    -- report:  census_id != null  <=>  sampled  <=>  exactly one census report
    -- (enforced by assertCensusJoin in lib/execution-identity.ts).
    census_id          uuid,

    -- Effective base rate as 'N/M'. NULL means master-switch-only, which is
    -- 1/1. Shape is checked here; the numeric bounds are checked below via
    -- CASE, because a bare cast in a CHECK is not guaranteed to short-circuit
    -- and would raise rather than return false on a malformed value.
    census_rate        text,

    -- ── What ran ──────────────────────────────────────────────────────────
    -- Named source_namespace, not `namespace` (reserved TypeSpec keyword), and
    -- matching the existing house convention in the doctrine transition
    -- records and the census read endpoint's query param.
    -- No closed vocabulary is ratified for this yet, so it is required text.
    source_namespace   text NOT NULL,
    ticket_id          text,
    work_item_id       text,

    -- ── Doctrine in force ─────────────────────────────────────────────────
    -- B1/B3 provenance. Content-addressed snapshot of the doctrine set that
    -- governed this execution, so census findings can be attributed to a frame
    -- rather than to "whatever was loaded at the time".
    doctrine_snapshot_id text NOT NULL,
    procedure_card_set_hash text,

    -- ── Outcome ───────────────────────────────────────────────────────────
    -- Free text and required. Deliberately unconstrained: no outcome vocabulary
    -- has been ratified (6629b009 scopes A2a to identity), so this table
    -- refuses to invent one and would create a second source of truth.
    outcome_status     text NOT NULL,
    outcome_detail     text,

    created_at         timestamptz NOT NULL DEFAULT now(),

    -- ── Constraints ───────────────────────────────────────────────────────

    -- The empty-string defect the DBA found while ruling on 42c1de7c: 934 of
    -- 2097 nebula.receipts_unified rows carry session_id = '' rather than NULL,
    -- so "NULL means un-shimmed" silently misreads 45% of rows. This new table
    -- starts clean rather than inheriting the defect. Part of that remediation
    -- was a CHECK forbidding ''; applying it here costs nothing and stops the
    -- new surface from re-opening the split.
    CONSTRAINT executions_session_id_not_empty
        CHECK (session_id IS NULL OR session_id <> ''),

    -- Tri-state, part 1: sampled implies enabled.
    CONSTRAINT executions_census_sampled_implies_enabled
        CHECK (NOT census_sampled OR census_enabled),

    -- Tri-state, part 2: a disabled census carries no rate.
    CONSTRAINT executions_census_disabled_is_inert
        CHECK (census_enabled OR (NOT census_sampled AND census_rate IS NULL)),

    -- The join invariant, Tier-1/Tier-2 half: census_id is non-null exactly
    -- when sampled. This is what stops an unsampled execution from carrying a
    -- census_id, or a sampled one from omitting it.
    CONSTRAINT executions_census_id_iff_sampled
        CHECK ((census_id IS NULL) = (NOT census_sampled)),

    -- Rate shape, then bounds. CASE is used for the bounds because PostgreSQL
    -- does not guarantee AND short-circuit evaluation inside a CHECK, so a
    -- malformed value would raise instead of returning false.
    CONSTRAINT executions_census_rate_shape
        CHECK (census_rate IS NULL OR census_rate ~ '^[0-9]+/[0-9]+$'),

    CONSTRAINT executions_census_rate_bounds
        CHECK (
            CASE
                WHEN census_rate ~ '^[0-9]+/[0-9]+$' THEN
                    split_part(census_rate, '/', 2)::int >= 1
                    AND split_part(census_rate, '/', 1)::int <= split_part(census_rate, '/', 2)::int
                ELSE false
            END
        )

    -- ── Deliberately absent ───────────────────────────────────────────────
    -- No FK to vision.sessions (DBA condition 4). That table is empty and its
    -- one FK has 0/15253 populated references; binding to it would couple the
    -- execution identity to an unexercised surface. There is also no
    -- calendar_session_ref column (condition 3) — no ratified shape for it, and
    -- no exercised surface to bind to.
    -- No 1:1 session uniqueness, for the same reason as condition 2.
    -- Immutability is NOT absent -- it is REQUIRED and created below, per the
    -- DBA's Q4 answer (append-only, hard trigger).
);

-- NOTE ON A REAL FRAGILITY, for whoever edits this later:
-- every tri-state CHECK above is only sound BECAUSE census_enabled and
-- census_sampled are NOT NULL. A CHECK constraint evaluates to NULL — and
-- therefore PASSES — on a NULL input. So if anyone relaxes either NOT NULL,
-- all three census constraints silently start admitting malformed rows while
-- still appearing to be enforced. The NOT NULLs are load-bearing, not
-- decoration. If they must be relaxed, these CHECKs need rewriting first.


COMMENT ON TABLE nebula.executions IS
  'CDLC A2a Tier-1 execution identity + Tier-2 census marker. session_id is a text '
  'reference (many-per-session), NOT a parent and NOT a uuid. session_id = '' is '
  'forbidden, per the dffa404e finding that 45% of receipts_unified rows violate the '
  'NULL-means-un-shimmed sentinel. No FK to vision.sessions by ruling. DRAFT: not yet '
  'authorised to run; this file is deliberately outside the NNN-*.sql runner pattern.';

COMMENT ON COLUMN nebula.executions.census_enabled IS
  'Tier-2 marker part 1. NOT NULL by design: absence is illegal so a missing marker '
  'cannot be confused with a disabled census (A5 measures 30% sampled-missing).';

COMMENT ON COLUMN nebula.executions.census_id IS
  'Join key to the single Tier-3 census report. Non-null exactly when census_sampled.';

COMMENT ON COLUMN nebula.executions.session_id IS
  'Opaque text reference to a harness session, NULL when un-shimmed. Never a uuid: '
  'a uuid here is the vision.sessions concept colliding with the reference family '
  '(DBA ruling dffa404e condition 3).';

-- ── Append-only (DBA answers d15c197f Q4: YES, hard trigger) ─────────────────
-- Execution records are audit-class. Mutating one would silently rewrite the very
-- identity A2a exists to establish, so UPDATE and DELETE are refused outright
-- rather than guarded by convention or a soft column. There is no retirement
-- path today; when one is needed it becomes an explicit later migration.
--
-- Combined with the NOT NULL census columns above, this also keeps the tri-state
-- CHECKs sound for good: an appended row cannot later be edited into a state the
-- constraints would have rejected at insert time.

CREATE OR REPLACE FUNCTION nebula.fn_executions_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'nebula.executions is append-only (DBA answers d15c197f Q4); % is refused', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS trg_executions_append_only ON nebula.executions;
CREATE TRIGGER trg_executions_append_only
    BEFORE UPDATE OR DELETE ON nebula.executions
    FOR EACH ROW EXECUTE FUNCTION nebula.fn_executions_append_only();

-- ── Indexes ─────────────────────────────────────────────────────────────────

-- Many-per-session lookup. This mirrors the partial-index shape already used on
-- nebula.agent_connections, and is only safe because executions_session_id_not_empty
-- makes '' impossible. That partial index on agent_connections is NOT safe for the
-- same reason, which is part of why 934 rows escaped notice.
CREATE INDEX IF NOT EXISTS idx_executions_session
    ON nebula.executions (session_id) WHERE session_id IS NOT NULL;

-- The join key into Tier 3.
CREATE INDEX IF NOT EXISTS idx_executions_census_id
    ON nebula.executions (census_id) WHERE census_id IS NOT NULL;

-- B1/B3 cohort queries: "all executions under doctrine set X".
CREATE INDEX IF NOT EXISTS idx_executions_doctrine_snapshot
    ON nebula.executions (doctrine_snapshot_id);

CREATE INDEX IF NOT EXISTS idx_executions_created
    ON nebula.executions (created_at DESC);

-- Q6 retention instrumentation reads census volume by day; this supports it.
CREATE INDEX IF NOT EXISTS idx_executions_census_day
    ON nebula.executions (created_at DESC) WHERE census_sampled;

COMMIT;

-- =============================================================================
-- Post-apply checklist for whoever runs this
--
-- 1. Ask R9 before treating the change as safe. Operator has answered YES for
--    vanadium (the current canonical off-machine target, since 2026-09-05).
-- 2. Verify the file is numbered and placed per DBA convention, and that the
--    duplicate 055 is resolved or the numbering scheme tolerates it.
-- 3. Confirm the new columns match the contract in
--    typespec/v1/nexus-broker/typescript/models.tsp — a divergence there
--    misdescribes the wire format, which is the same class of defect as the
--    contract-casing ratchet.
-- 4. Nothing writes to this table until A4 emits envelopes. An empty table is
--    the expected state immediately after migration.
-- =============================================================================
