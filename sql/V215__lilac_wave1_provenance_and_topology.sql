-- V215 — Lilac Wave 1 provenance, topology and parity (staged inert).
--
-- Repo-canonical counterpart: schemas/migrations/resolution/
-- resolution_migration_v38_lilac_wave1_provenance.sql. The two files carry a
-- byte-identical body below this header; only the header differs, and a guard
-- (bin/tests/test_lilac_wave1_v215_migration.py) fails if the bodies drift.
--
-- Plan 8261668 (Lilac W1-L2), child of 8261639. Additive DDL only: the five
-- provenance columns on the four canonical mutation surfaces,
-- resolution.mutation_provenance with a unique idempotency key,
-- resolution.executable_topology with a closed lifecycle vocabulary,
-- resolution.parity_evidence for the L6 dual-read measurement, lineage
-- columns on resolution.migration_disposition, and an opt-in enforce-mode
-- trigger whose default path is unchanged.
--
-- NOT APPLIED BY THIS CHANGE. Live application is a separate operator-gated
-- step: this DDL is posted to the Draft forum for admin approval and no live
-- ALTER runs on this plan's authority. It is proven by a double apply inside
-- a rolled-back transaction on a throwaway database that the guard creates
-- and drops.
--
-- This header is documentation. The authoritative text is the body below,
-- which is identical to the canonical migration's body.

BEGIN;

CREATE SCHEMA IF NOT EXISTS resolution;

-- ── 1. The provenance envelope on the four canonical mutation surfaces ──────
-- Column-by-column ADD COLUMN IF NOT EXISTS rather than one multi-column ALTER
-- per table: the second apply must be a no-op even when a partial apply was
-- interrupted, and ALTER TABLE ... ADD COLUMN IF NOT EXISTS is the only form
-- that makes each column independently idempotent.
ALTER TABLE resolution.receipt
    ADD COLUMN IF NOT EXISTS source_implementation text,
    ADD COLUMN IF NOT EXISTS runtime_route        text,
    ADD COLUMN IF NOT EXISTS source_identity      text,
    ADD COLUMN IF NOT EXISTS idempotency_key      text;

ALTER TABLE resolution.ticket
    ADD COLUMN IF NOT EXISTS source_implementation text,
    ADD COLUMN IF NOT EXISTS runtime_route        text,
    ADD COLUMN IF NOT EXISTS source_identity      text,
    ADD COLUMN IF NOT EXISTS idempotency_key      text;

ALTER TABLE resolution.ticket_transition
    ADD COLUMN IF NOT EXISTS source_implementation text,
    ADD COLUMN IF NOT EXISTS runtime_route        text,
    ADD COLUMN IF NOT EXISTS source_identity      text,
    ADD COLUMN IF NOT EXISTS idempotency_key      text;

ALTER TABLE resolution.fanout_transition
    ADD COLUMN IF NOT EXISTS source_implementation text,
    ADD COLUMN IF NOT EXISTS runtime_route        text,
    ADD COLUMN IF NOT EXISTS source_identity      text,
    ADD COLUMN IF NOT EXISTS idempotency_key      text;

-- ── 2. mutation_provenance: one row per mutation, one idempotency key ──────
-- The unique key is the contract, not an optimisation: a replay that reuses a
-- key must be refused by the database rather than by every caller's memory.
CREATE TABLE IF NOT EXISTS resolution.mutation_provenance (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    surface               text NOT NULL
                          CHECK (surface IN ('receipt', 'ticket',
                                             'ticket_transition', 'fanout_transition')),
    target_id             uuid,
    source_implementation text NOT NULL,
    runtime_route         text NOT NULL,
    source_identity       text NOT NULL,
    idempotency_key       text NOT NULL,
    contract_version      integer NOT NULL DEFAULT 1,
    recorded_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mutation_provenance_idempotency_key_key
        UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS mutation_provenance_surface_target_idx
    ON resolution.mutation_provenance (surface, target_id);

-- ── 3. executable_topology: the typed home for the W1-L1 inventory ─────────
-- The lifecycle CHECK is the point of the table: an inventory row whose
-- lifecycle is outside this vocabulary is unrepresentable, so the same
-- closed set the generator enforces is enforced by storage.
CREATE TABLE IF NOT EXISTS resolution.executable_topology (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    surface        text NOT NULL
                   CHECK (surface IN ('receipt', 'ticket',
                                      'ticket_transition', 'fanout_transition')),
    path           text NOT NULL,
    line           integer,
    role           text NOT NULL CHECK (role IN ('reader', 'writer')),
    language       text NOT NULL,
    db_target      text,
    service_unit   text,
    port           integer,
    pid            integer,
    invocation_id  text,
    working_tree   text NOT NULL DEFAULT 'self',
    route          text,
    lifecycle      text NOT NULL
                   CHECK (lifecycle IN ('live', 'test/worktree', 'retired',
                                        'compatibility', 'unknown')),
    disposition    text,
    generated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT executable_topology_identity_key
        UNIQUE (surface, path, line, role)
);

CREATE INDEX IF NOT EXISTS executable_topology_lifecycle_idx
    ON resolution.executable_topology (lifecycle, surface);

-- ── 4. parity_evidence: the L6 dual-read measurement, recorded not asserted ─
CREATE TABLE IF NOT EXISTS resolution.parity_evidence (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    wr_id        text NOT NULL,
    window_start timestamptz NOT NULL,
    window_end   timestamptz NOT NULL,
    matched      integer NOT NULL CHECK (matched >= 0),
    mismatched   integer NOT NULL CHECK (mismatched >= 0),
    unreadable   integer NOT NULL CHECK (unreadable >= 0),
    outcome      text NOT NULL CHECK (outcome IN ('pass', 'fail')),
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb,
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT parity_evidence_window_order CHECK (window_end >= window_start),
    CONSTRAINT parity_evidence_fails_closed CHECK (
        outcome = 'pass' OR mismatched > 0 OR unreadable > 0
    )
);

CREATE INDEX IF NOT EXISTS parity_evidence_wr_idx
    ON resolution.parity_evidence (wr_id, window_end DESC);

-- ── 5. migration_disposition: lineage and evidence for every legacy row ────
ALTER TABLE resolution.migration_disposition
    ADD COLUMN IF NOT EXISTS source_identity text,
    ADD COLUMN IF NOT EXISTS lineage         jsonb,
    ADD COLUMN IF NOT EXISTS evidence        jsonb;

-- ── 6. Enforce-mode guard: opt-in refusal, default path untouched ──────────
CREATE OR REPLACE FUNCTION resolution.lilac_require_provenance()
RETURNS trigger AS $$
BEGIN
    IF current_setting('lilac.provenance_mode', true) = 'enforce' THEN
        IF NEW.source_implementation IS NULL
           OR NEW.runtime_route IS NULL
           OR NEW.source_identity IS NULL
           OR NEW.idempotency_key IS NULL THEN
            RAISE EXCEPTION
                'lilac provenance enforcement: % write refused without a complete provenance envelope',
                TG_TABLE_NAME
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger creation without a teardown statement: check the catalog, create
-- only when absent. This is what keeps the file within the additive contract.
DO $$
DECLARE
    surface text;
BEGIN
    FOREACH surface IN ARRAY ARRAY['receipt', 'ticket',
                                   'ticket_transition', 'fanout_transition']
    LOOP
        IF NOT EXISTS (
            SELECT 1
              FROM pg_trigger
             WHERE tgname = 'trg_' || surface || '_lilac_provenance'
               AND tgrelid = format('resolution.%I', surface)::regclass
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE INSERT OR UPDATE ON resolution.%I '
                'FOR EACH ROW EXECUTE FUNCTION resolution.lilac_require_provenance()',
                'trg_' || surface || '_lilac_provenance',
                surface
            );
        END IF;
    END LOOP;
END;
$$;

COMMIT;
