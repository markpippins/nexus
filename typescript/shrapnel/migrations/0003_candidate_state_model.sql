-- ============================================================================
-- shrapnel migration 0003: value_long/value_string FK parity (idempotent)
--                                    + regression guard
--                                    + candidate-state SOL model seed
-- ----------------------------------------------------------------------------
-- Single-owner delivery, directive d6ffdc06 (Option C, revised single-owner).
-- ONE source applied to BOTH the `nexus` and `sol` databases via
-- `SHRAPNEL_PG_DSN=<dsn> npm run migrate`.
--
-- Contents:
--   1. FK fix (07b007c5 root fix, idempotent): reconcile value_long /
--      value_string to the same REFERENCES value(id) ON DELETE CASCADE
--      pattern as the other 5 extension tables, dropping the disjoint
--      default-sequence behavior on their id column. Safe to run when the
--      constraints are ALREADY present (checked via pg_constraint) — both
--      DBs currently carry them, so this is a no-op there.
--   2. Regression guard (07b007c5 #3): shrapnel.assert_value_extension_fk_parity()
--      raises when any of the 7 extension tables does not share/FK to
--      value.id; the DO block below proves it AND walks a shell+extension
--      insert for each type (savepointed, rolled back) so writes that used
--      to hard-fail on the disjoint sequences are exercised.
--   3. Candidate-state model seed (directive 96b22ed4 member wiring,
--      Analyst 5232aef7): get-or-create by NAME in resolution.* —
--      PromotionCandidate + ShrapnelFact concepts, asset_id + seed-member
--      attributes, and the candidate_has_state_record relationship bound on
--      asset_id == asset_id (attribute-level binding: empty schema/table,
--      columns only). Deterministic namespace ids (uuid5 of
--      "solscript:candidate-state:<logical-key>") so a fresh interpreter and
--      a seeded DB agree without carrying any database literals. Existing
--      rows (by name / by (concept_id,name) / by relationship triple) are
--      left untouched — this is purely additive and idempotent.
--
-- No nexus literals. The asset-id convention is shared: candidates and their
-- shrapnel state records tie on the asset_id member (canonical_asset rows
-- live in nexus semantics; sol keeps asset_id as a plain shrapnel string
-- member — both resolve through the same attribute-level binding).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. value_long / value_string FK parity (idempotent)
-- ----------------------------------------------------------------------------

-- Drop any disjoint sequence default on the id column. The writer inserts
-- the parent value(id) shell id explicitly (same pattern as the other 5
-- extension tables); DROP DEFAULT is a no-op when absent.
ALTER TABLE shrapnel.value_long  ALTER COLUMN id DROP DEFAULT;
ALTER TABLE shrapnel.value_string ALTER COLUMN id DROP DEFAULT;

-- Add the FKs on the PK only when missing (pg_constraint probe keeps this
-- a no-op on DBs that already carry the fix).
DO $$
DECLARE
    missing CONSTANT text := '0000';
    vname text;
    cnt   integer;
BEGIN
    SELECT count(*) INTO cnt FROM pg_constraint
    WHERE conrelid = 'shrapnel.value_long'::regclass
      AND contype = 'f'
      AND confrelid = 'shrapnel.value'::regclass;
    IF cnt = 0 THEN
        EXECUTE 'ALTER TABLE shrapnel.value_long ADD CONSTRAINT fk_value_long_value '
             || 'FOREIGN KEY (id) REFERENCES shrapnel.value(id) ON DELETE CASCADE';
    END IF;

    SELECT count(*) INTO cnt FROM pg_constraint
    WHERE conrelid = 'shrapnel.value_string'::regclass
      AND contype = 'f'
      AND confrelid = 'shrapnel.value'::regclass;
    IF cnt = 0 THEN
        EXECUTE 'ALTER TABLE shrapnel.value_string ADD CONSTRAINT fk_value_string_value '
             || 'FOREIGN KEY (id) REFERENCES shrapnel.value(id) ON DELETE CASCADE';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 2. Regression guard — FK parity + write-path smoke test
-- ----------------------------------------------------------------------------

-- Raises unless all 7 value_<type> extension tables share/FK their PK with
-- value(id). Failures surface the class of drift 07b007c5 fixed.
CREATE OR REPLACE FUNCTION shrapnel.assert_value_extension_fk_parity()
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    ext text;
    cnt integer;
    bad text[] := '{}';
BEGIN
    FOREACH ext IN ARRAY ARRAY[
        'value_long', 'value_string', 'value_double', 'value_boolean',
        'value_timestamp', 'value_jsonb', 'value_uuid'
    ]
    LOOP
        SELECT count(*) INTO cnt
        FROM pg_constraint
        WHERE conrelid = format('shrapnel.%s', ext)::regclass
          AND contype = 'f'
          AND confrelid = 'shrapnel.value'::regclass;
        IF cnt = 0 THEN
            bad := bad || ext;
        END IF;
    END LOOP;
    IF array_length(bad, 1) IS NOT NULL THEN
        RAISE EXCEPTION
            'shrapnel value-extension FK parity broken for: %', array_to_string(bad, ', ');
    END IF;
END;
$$;

-- Prove parity right now (fails the migration on drift).
SELECT shrapnel.assert_value_extension_fk_parity();

-- Prove the write path that used to hard-fail: for each of the 7 types,
-- insert parent value shell + extension row at the SAME id, then roll the
-- test row back via top-level SAVEPOINT/ROLLBACK TO SAVEPOINT (the runner
-- already wraps this file in a transaction, so an aborted test fails the
-- file loudly — exactly the regression-guard behavior wanted).
SAVEPOINT sp_ext_test_long;
INSERT INTO shrapnel.value (id, value_type_code) VALUES (900000001, 1);
INSERT INTO shrapnel.value_long (id, value) VALUES (900000001, 42);
ROLLBACK TO SAVEPOINT sp_ext_test_long;

SAVEPOINT sp_ext_test_string;
INSERT INTO shrapnel.value (id, value_type_code) VALUES (900000002, 2);
INSERT INTO shrapnel.value_string (id, value) VALUES (900000002, 'hello');
ROLLBACK TO SAVEPOINT sp_ext_test_string;

SAVEPOINT sp_ext_test_double;
INSERT INTO shrapnel.value (id, value_type_code) VALUES (900000003, 3);
INSERT INTO shrapnel.value_double (id, value) VALUES (900000003, 1.5);
ROLLBACK TO SAVEPOINT sp_ext_test_double;

SAVEPOINT sp_ext_test_boolean;
INSERT INTO shrapnel.value (id, value_type_code) VALUES (900000004, 4);
INSERT INTO shrapnel.value_boolean (id, value) VALUES (900000004, true);
ROLLBACK TO SAVEPOINT sp_ext_test_boolean;

SAVEPOINT sp_ext_test_timestamp;
INSERT INTO shrapnel.value (id, value_type_code) VALUES (900000005, 5);
INSERT INTO shrapnel.value_timestamp (id, value) VALUES (900000005, now());
ROLLBACK TO SAVEPOINT sp_ext_test_timestamp;

SAVEPOINT sp_ext_test_jsonb;
INSERT INTO shrapnel.value (id, value_type_code) VALUES (900000006, 6);
INSERT INTO shrapnel.value_jsonb (id, value) VALUES (900000006, '{"k": 1}');
ROLLBACK TO SAVEPOINT sp_ext_test_jsonb;

SAVEPOINT sp_ext_test_uuid;
INSERT INTO shrapnel.value (id, value_type_code) VALUES (900000007, 7);
INSERT INTO shrapnel.value_uuid (id, value) VALUES (900000007, '00000000-0000-4000-8000-000000000aaa');
ROLLBACK TO SAVEPOINT sp_ext_test_uuid;

-- ----------------------------------------------------------------------------
-- 3. Candidate-state model seed (resolution.*)
--    Deterministic namespace ids: uuid5(NAMESPACE_URL,
--    "solscript:candidate-state:<seed>") — identical to the in-memory
--    fallback ids candidate_state.py / database_loader.py derive, so a
--    DB-seeded interpreter and a from-scratch interpreter agree.
--    Everything is get-or-create by name — existing rows are preserved.
--
--    CROSS-DOMAIN DEPENDENCY: resolution.* is owned by the separate
--    resolution chain (schemas/migrations/resolution/). Brand-new
--    environments that have not run it yet do not have resolution.concept,
--    so this seed is skipped (NOTICE) — the deterministic uuid5 ids mean
--    the runtime's get-or-create path recreates identical rows on first
--    use. Environments that DO carry resolution.* are seeded exactly as
--    before (purely additive, idempotent).
-- ----------------------------------------------------------------------------

DO $$
BEGIN
  IF to_regclass('resolution.concept') IS NULL THEN
    RAISE NOTICE '0003: resolution.concept absent (resolution chain not applied) — skipping cross-domain candidate-state seed';
    RETURN;
  END IF;

-- Concepts. ON CONFLICT (name) DO NOTHING : if the logical concept already
-- exists (e.g. nexus PromotionCandidate at its historical id), keep it.
INSERT INTO resolution.concept (id, name, description)
VALUES
    ('7a5737d0-ad58-5bd2-a013-16420885081b', 'PromotionCandidate',
     'A candidate shrapnel state-record subject (tie via asset_id)'),
    ('8d1a9e53-08c2-5949-a404-c76e03582316', 'ShrapnelFact',
     'Shrapnel EAV fact objects (standalone facts store)')
ON CONFLICT (name) DO NOTHING;

-- asset_id on PromotionCandidate (if absent; keep any existing attrs).
INSERT INTO resolution.concept_attribute (id, concept_id, name, description, value_type, is_state_attribute)
SELECT 'f4abf885-6e1e-5baa-8638-efb3eec85c78'::uuid,
       c.id,
       'asset_id',
       'Canonical asset id shared with the shrapnel state record',
       'text',
       false
FROM resolution.concept c
WHERE c.name = 'PromotionCandidate'
  AND NOT EXISTS (
      SELECT 1 FROM resolution.concept_attribute ca
      WHERE ca.concept_id = c.id AND ca.name = 'asset_id'
  );

-- asset_id + Analyst seed members on ShrapnelFact (5232aef7), each
-- get-or-create by (concept_id, name).
INSERT INTO resolution.concept_attribute (id, concept_id, name, description, value_type, is_state_attribute)
SELECT v.id::uuid, c.id, v.name, v.description, v.value_type, false
FROM resolution.concept c
JOIN (VALUES
    ('fecd64c9-2320-5992-8d7b-01fd0f662432', 'asset_id',
     'Canonical asset id tying the state record to its candidate', 'text'),
    ('3d5b6a95-ae6d-54f9-91ac-3ade2951e02e', 'partial_implementation',
     'Deterministic candidate-state seed member: partial_implementation', 'boolean'),
    ('65137979-807a-5ba5-9f14-e83ec48d392a', 'detailed_analysis',
     'Deterministic candidate-state seed member: detailed_analysis', 'boolean'),
    ('80ff19b3-1b01-5062-9122-9faa7ea028a6', 'inspection_or_ir_exists',
     'Deterministic candidate-state seed member: inspection_or_ir_exists', 'boolean'),
    ('98ad35be-5a66-545a-8697-3b388d7f1a47', 'system_mapped',
     'Deterministic candidate-state seed member: system_mapped', 'boolean'),
    ('9c6d9752-ef50-527b-8f28-b06fe4260950', 'has_open_questions',
     'Deterministic candidate-state seed member: has_open_questions', 'boolean'),
    ('d904dc8c-e7d4-543e-ae5c-32032dfbb68d', 'sandbox_scaffolded',
     'Deterministic candidate-state seed member: sandbox_scaffolded', 'boolean')
) AS v(id, name, description, value_type)
ON c.name = 'ShrapnelFact'
WHERE NOT EXISTS (
    SELECT 1 FROM resolution.concept_attribute ca
    WHERE ca.concept_id = c.id AND ca.name = v.name
);

-- The asset-id tie relationship (get-or-create by triple). A relationship
-- row is added only when no active row already connects these two concepts
-- with this relationship_type.
INSERT INTO resolution.concept_relationship (id, from_concept_id, to_concept_id, relationship_type, path, notes)
SELECT 'bdfcd10d-d31a-505f-8d79-de9a2fb163fb'::uuid,
       fc.id, tc.id,
       'candidate_has_state_record',
       NULL,
       'Candidate -> shrapnel state record, bound on the shared asset_id attribute'
FROM resolution.concept fc
JOIN resolution.concept tc ON tc.name = 'ShrapnelFact'
WHERE fc.name = 'PromotionCandidate'
  AND NOT EXISTS (
      SELECT 1 FROM resolution.concept_relationship cr
      WHERE cr.from_concept_id = fc.id
        AND cr.to_concept_id = tc.id
        AND cr.relationship_type = 'candidate_has_state_record'
        AND cr.expired_at IS NULL
  );

-- Attribute-level binding for the relationship: attribute-to-attribute on
-- asset_id == asset_id (no physical schema/table — the compiler compares
-- entity attributes named asset_id). NOT NULL columns take '' for schema/
-- table; hydrated by the loader when the binding row exists.
INSERT INTO resolution.concept_relationship_binding (
    concept_relationship_id,
    from_schema, from_table, from_column,
    to_schema,   to_table,   to_column,
    notes
)
SELECT 'bdfcd10d-d31a-505f-8d79-de9a2fb163fb'::uuid,
       '', '', 'asset_id',
       '', '', 'asset_id',
       'Attribute-level binding: candidate.asset_id == state.asset_id'
WHERE NOT EXISTS (
    SELECT 1 FROM resolution.concept_relationship_binding
    WHERE concept_relationship_id = 'bdfcd10d-d31a-505f-8d79-de9a2fb163fb'
);

END
$$;