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
-- ## Numbering is the DBA's to assign
--
-- CORRECTION to my first draft of this header: I previously wrote that this
-- directory still carries a DUPLICATE 055 (055-agent-records-tags-gin.sql and
-- 055-allow-supervisor-role.sql, thread 6bba5dd3). On the current base that is
-- no longer true — 055-allow-supervisor-role.sql was renumbered to
-- 058-allow-supervisor-role.sql, and only 055-agent-records-tags-gin.sql
-- remains at 055. The duplicate is resolved. I had read that directory on an
-- older commit and carried a stale claim into a DBA request.
--
-- Current numbered sequence: 001-058, then a jump to 067. The 059-066 range is
-- absent and I do not know why — worth the DBA confirming before 068 is used,
-- in case those numbers are reserved or were renumbered elsewhere.
--
-- The proposed number in this filename is 068, which is "next" only in the sense
-- that 067 is the highest present. That is a proposal, not an assignment.
--
-- ## The strongest reason this file stays unnumbered (SEV3, 2026-09-28)
--
-- Record 9a70c8c5: an engineer booted a worktree nebula-srv on a scratch port
-- with env pointed at the LIVE nexus database. The startup migration runner
-- applied pending migrations 056 and 057 to production. Self-reported, SEV3, open
-- with the DBA.
--
-- That is the same runner that would pick up a numbered file here, and the
-- hazard is not theoretical: booting a worktree service against the live
-- database has already applied unreviewed schema to it once today. A numbered
-- migration sitting in a worktree branch is one careless boot from production.
-- Unnumbered is the cheap mitigation available to me.
-- ## The store is an OPEN QUESTION for the DBA, not a settled decision
--
-- 6629b009 says "Execution record (new)" without naming a store. `nebula` is
-- the canonical agent/harvest/plan schema per dba-schema-migration-path, and
-- Tier 3 already lands in nebula.agent_records, so nebula.executions is the
-- consistent candidate — but that is my inference, not a ruling. **If the
-- execution record belongs elsewhere, this DDL is in the wrong schema and
-- should be redirected rather than adapted.**
--
-- Please also confirm `nebula.executions` is a free name. I could not verify
-- against live PG (no credentials in this worktree), and
-- dba-schema-migration-path warns that some nebula list targets are VIEWS
-- over base tables — worth ruling out before creating anything.
--
-- R9: operator answered YES — replicate to vanadium. See the accompanying
-- record. Applies only after the DBA authors the file and it is applied.
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
        ),

    -- ── Deliberately absent ───────────────────────────────────────────────
    -- No FK to vision.sessions (DBA condition 4). That table is empty and its
    -- one FK has 0/15253 populated references; binding to it would couple the
    -- execution identity to an unexercised surface. There is also no
    -- calendar_session_ref column (condition 3) — no ratified shape for it, and
    -- no exercised surface to bind to.
    -- No 1:1 session uniqueness, for the same reason as condition 2.
    -- No immutability trigger: the Architect did not rule one in, and adding
    -- one would be inventing policy. Raised as an open question instead.
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

-- ── Indexes ────────────────────────────────────────────────────────────────

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
