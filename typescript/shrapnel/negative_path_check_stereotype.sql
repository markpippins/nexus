-- ============================================================================
-- shrapnel negative-path checks: prove the 0004 stereotype model contract
-- (fingerprint, required-field superset, depth, immutability, membership
-- pair, evidence-gated classification) does its job.
-- Run with: psql -f negative_path_check_stereotype.sql
-- (intentionally designed so the negative cases raise; uses \if/\endif to
-- report OK / FAIL inside psql, mirroring negative_path_check.sql).
--
-- Deferred constraint triggers (fingerprint, superset) fire at COMMIT, so
-- every multi-statement case is wrapped in an explicit BEGIN/COMMIT and the
-- verdict is read from the COMMIT statement's :ERROR.
-- ============================================================================

\set ON_ERROR_STOP off
SET client_min_messages = 'error';

-- Start clean.
TRUNCATE
    shrapnel.stereotype_field,
    shrapnel.stereotype_revision,
    shrapnel.stereotype,
    shrapnel.object_attribute_value,
    shrapnel.value_long, shrapnel.value_string, shrapnel.value_double,
    shrapnel.value_boolean, shrapnel.value_timestamp, shrapnel.value_jsonb,
    shrapnel.value_uuid,
    shrapnel.value, shrapnel.object_instance, shrapnel.field
    RESTART IDENTITY CASCADE;

\echo '=== Setup: test fields, conformance field, base shape S1 v1 ==='
BEGIN;

INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)
VALUES (false, 0, 'A', 'A', 'test_a', 2) RETURNING id AS fa \gset
INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)
VALUES (false, 0, 'B', 'B', 'test_b', 2) RETURNING id AS fb \gset
INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)
VALUES (false, 0, 'C', 'C', 'test_c', 2) RETURNING id AS fc \gset
INSERT INTO shrapnel.field (is_calculated, field_index, label, name, property_name, field_type_code)
VALUES (false, 0, 'StereoType Conformance', 'StereoType Conformance', 'stereotype_conformance', 2)
RETURNING id AS fconf \gset

INSERT INTO shrapnel.stereotype (name, description)
VALUES ('shape_base', 'negative-path base shape') RETURNING id AS s1 \gset

-- Root revision v1 over required fields {A, B}, then its field rows, then
-- COMMIT — the deferred fingerprint trigger verifies the complete contract.
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:s1, 1, NULL, NULL, NULL, 0,
        (SELECT shrapnel.stereotype_canonical_contract(:s1, NULL, NULL,
          (SELECT jsonb_agg(jsonb_build_object('id', x, 'required', true) ORDER BY x)
           FROM unnest(ARRAY[:fa, :fb]::bigint[]) AS x))))
RETURNING id AS r1 \gset
INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
VALUES (:r1, :fa, true), (:r1, :fb, true);
COMMIT;
\if :ERROR
    \echo '  FAIL: base shape S1 v1 did not commit'
\else
    \echo '  OK: base shape S1 v1 committed (positive control)'
\endif

\echo '=== Case 1: fingerprint mismatch is rejected at COMMIT ==='
BEGIN;
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:s1, 2, NULL, NULL, NULL, 0, 'sha256:0000000000000000000000000000000000000000000000000000000000000000');
INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
VALUES ((SELECT id FROM shrapnel.stereotype_revision WHERE stereotype_id = :s1 AND version = 2), :fa, true);
COMMIT;
\if :ERROR
    \echo '  OK: fingerprint mismatch rejected'
\else
    \echo '  FAIL: fingerprint mismatch accepted'
\endif

\echo '=== Case 2: child missing a parent-required field is rejected (superset) ==='
-- S2 extends S1 v1 but declares only {A} (missing required B).
INSERT INTO shrapnel.stereotype (name, description)
VALUES ('shape_child', 'negative-path child shape') RETURNING id AS s2 \gset
BEGIN;
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:s2, 1, :r1, :s1, 'test: child of base', 1,
        (SELECT shrapnel.stereotype_canonical_contract(:s2, :r1, 'test: child of base',
          (SELECT jsonb_agg(jsonb_build_object('id', x, 'required', true) ORDER BY x)
           FROM unnest(ARRAY[:fa]::bigint[]) AS x))));
INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
VALUES ((SELECT id FROM shrapnel.stereotype_revision WHERE stereotype_id = :s2 AND version = 1), :fa, true);
COMMIT;
\if :ERROR
    \echo '  OK: non-superset child contract rejected'
\else
    \echo '  FAIL: non-superset child contract accepted'
\endif

\echo '=== Case 3: extends without rationale is rejected (immediate CHECK) ==='
INSERT INTO shrapnel.stereotype (name, description)
VALUES ('shape_norationale', 'negative-path no-rationale shape') RETURNING id AS s3 \gset
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:s3, 1, :r1, :s1, NULL, 1,
        (SELECT shrapnel.stereotype_canonical_contract(:s3, :r1, NULL,
          (SELECT jsonb_agg(jsonb_build_object('id', x, 'required', true) ORDER BY x)
           FROM unnest(ARRAY[:fa, :fb]::bigint[]) AS x))));
\if :ERROR
    \echo '  OK: extends-without-rationale rejected'
\else
    \echo '  FAIL: extends-without-rationale accepted'
\endif

\echo '=== Setup: depth chain S1 -> S2 -> S3 -> S4 (depth 3 allowed) ==='
BEGIN;
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:s2, 2, :r1, :s1, 'test: depth 1', 1,
        (SELECT shrapnel.stereotype_canonical_contract(:s2, :r1, 'test: depth 1',
          (SELECT jsonb_agg(jsonb_build_object('id', x, 'required', true) ORDER BY x)
           FROM unnest(ARRAY[:fa, :fb]::bigint[]) AS x))))
RETURNING id AS r2 \gset
INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
VALUES (:r2, :fa, true), (:r2, :fb, true);

INSERT INTO shrapnel.stereotype (name, description)
VALUES ('shape_d2', 'depth-2 shape') RETURNING id AS sd2 \gset
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:sd2, 1, :r2, :s2, 'test: depth 2', 2,
        (SELECT shrapnel.stereotype_canonical_contract(:sd2, :r2, 'test: depth 2',
          (SELECT jsonb_agg(jsonb_build_object('id', x, 'required', true) ORDER BY x)
           FROM unnest(ARRAY[:fa, :fb]::bigint[]) AS x))))
RETURNING id AS rd2 \gset
INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
VALUES (:rd2, :fa, true), (:rd2, :fb, true);

INSERT INTO shrapnel.stereotype (name, description)
VALUES ('shape_d3', 'depth-3 shape') RETURNING id AS sd3 \gset
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:sd3, 1, :rd2, :sd2, 'test: depth 3', 3,
        (SELECT shrapnel.stereotype_canonical_contract(:sd3, :rd2, 'test: depth 3',
          (SELECT jsonb_agg(jsonb_build_object('id', x, 'required', true) ORDER BY x)
           FROM unnest(ARRAY[:fa, :fb]::bigint[]) AS x))))
RETURNING id AS rd3 \gset
INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
VALUES (:rd3, :fa, true), (:rd3, :fb, true);
COMMIT;
\if :ERROR
    \echo '  FAIL: depth-3 chain should be allowed'
\else
    \echo '  OK: depth-3 chain committed'
\endif

\echo '=== Case 4: depth 4 is rejected (C1 shallow hierarchy) ==='
-- The acyclicity trigger is IMMEDIATE: the verdict must be read from the
-- INSERT itself (a failed statement aborts the tx; the trailing COMMIT then
-- succeeds as ROLLBACK and would reset :ERROR).
INSERT INTO shrapnel.stereotype (name, description)
VALUES ('shape_d4', 'depth-4 shape') RETURNING id AS sd4 \gset
BEGIN;
INSERT INTO shrapnel.stereotype_revision
    (stereotype_id, version, parent_revision_id, parent_stereotype_id,
     extends_rationale, depth, contract_fingerprint)
VALUES (:sd4, 1, :rd3, :sd3, 'test: depth 4', 4,
        (SELECT shrapnel.stereotype_canonical_contract(:sd4, :rd3, 'test: depth 4',
          (SELECT jsonb_agg(jsonb_build_object('id', x, 'required', true) ORDER BY x)
           FROM unnest(ARRAY[:fa, :fb]::bigint[]) AS x))));
\if :ERROR
    \echo '  OK: depth-4 revision rejected'
\else
    \echo '  FAIL: depth-4 revision accepted'
\endif
ROLLBACK;

\echo '=== Case 5: revisions are append-only ==='
UPDATE shrapnel.stereotype_revision SET depth = 0 WHERE id = :r1;
\if :ERROR
    \echo '  OK: revision UPDATE rejected'
\else
    \echo '  FAIL: revision UPDATE accepted'
\endif
DELETE FROM shrapnel.stereotype_revision WHERE id = :r1;
\if :ERROR
    \echo '  OK: revision DELETE rejected'
\else
    \echo '  FAIL: revision DELETE accepted'
\endif

\echo '=== Case 5b: committed revision field rows are frozen (0005 section 0c) ==='
UPDATE shrapnel.stereotype_field SET required = false WHERE stereotype_revision_id = :r1 AND field_id = :fa;
\if :ERROR
    \echo '  OK: required-flag flip on committed revision rejected'
\else
    \echo '  FAIL: required-flag flip on committed revision accepted'
\endif
DELETE FROM shrapnel.stereotype_field WHERE stereotype_revision_id = :r1 AND field_id = :fb;
\if :ERROR
    \echo '  OK: field-row removal from committed revision rejected'
\else
    \echo '  FAIL: field-row removal from committed revision accepted'
\endif
INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
VALUES (:r1, :fc, true);
\if :ERROR
    \echo '  OK: post-commit contract growth rejected'
\else
    \echo '  FAIL: post-commit contract growth accepted'
\endif

\echo '=== Case 6: membership pair check (stereotype without revision) ==='
INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id AS oid1 \gset
UPDATE shrapnel.object_instance SET stereotype_id = :s1 WHERE id = :oid1;
\if :ERROR
    \echo '  OK: half-formed membership rejected'
\else
    \echo '  FAIL: half-formed membership accepted'
\endif

\echo '=== Case 7: classification without conformance evidence is rejected ==='
UPDATE shrapnel.object_instance
   SET stereotype_id = :s1, stereotype_revision_id = :r1
 WHERE id = :oid1;
\if :ERROR
    \echo '  OK: unevidenced classification rejected'
\else
    \echo '  FAIL: unevidenced classification accepted'
\endif

\echo '=== Case 8: nonconformant evidence does not admit classification ==='
-- Give the object a NONCONFORMANT evidence row, then flip it to conformant
-- to prove the gate reads the CURRENT evidence, not just its presence.
INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id AS vid_nc \gset
INSERT INTO shrapnel.value_string (id, value) VALUES (:vid_nc, 'nonconformant');
INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
VALUES (:oid1, :fconf, :vid_nc);
UPDATE shrapnel.object_instance
   SET stereotype_id = :s1, stereotype_revision_id = :r1
 WHERE id = :oid1;
\if :ERROR
    \echo '  OK: nonconformant classification rejected'
\else
    \echo '  FAIL: nonconformant classification accepted'
\endif

\echo '=== Case 9: conformant evidence admits classification (positive) ==='
UPDATE shrapnel.value_string SET value = 'conformant' WHERE id = :vid_nc;
UPDATE shrapnel.object_instance
   SET stereotype_id = :s1, stereotype_revision_id = :r1
 WHERE id = :oid1;
\if :ERROR
    \echo '  FAIL: conformant classification rejected'
\else
    \echo '  OK: conformant classification accepted'
\endif

\echo '=== Result ==='
\echo 'Script complete. Every OK line above is a contract guarantee holding.'
