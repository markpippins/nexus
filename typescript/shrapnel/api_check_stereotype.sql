-- ============================================================================
-- shrapnel API checks: exercise the 0005 stereotype functions (positive and
-- negative), mirroring negative_path_check.sql / negative_path_check_stereotype.sql.
-- Run with: psql -f api_check_stereotype.sql
-- Assumes 0001-0005 applied. Truncates data tables first.
-- ============================================================================

\set ON_ERROR_STOP off
SET client_min_messages = 'error';

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

-- ── Build a 3-deep chain with the constructor: base -> child -> grandchild ──
\echo '=== Build: base shape (fields a,b) via stereotype_create_revision ==='
SELECT shrapnel.stereotype_create_revision('shape_base', NULL, NULL,
       ARRAY['a','b']) AS base_rev \gset

\echo '=== Build: child extends base (adds c) ==='
SELECT shrapnel.stereotype_create_revision('shape_child', :base_rev,
       'test: child of base', ARRAY['a','b','c']) AS child_rev \gset

\echo '=== Build: grandchild extends child (no new fields) ==='
SELECT shrapnel.stereotype_create_revision('shape_grand', :child_rev,
       'test: grandchild', ARRAY['a','b','c']) AS grand_rev \gset

\echo '=== Check: resolve returns head versions 1 ==='
SELECT stereotype_id, head_revision_id, version FROM shrapnel.stereotype_resolve('shape_base') \gset
\if :{?stereotype_id}
    \echo '  OK: stereotype_resolve works'
\else
    \echo '  FAIL: stereotype_resolve returned nothing'
\endif

\echo '=== Check: chain walk (grand -> child -> base) ==='
SELECT * FROM shrapnel.stereotype_chain(:grand_rev);
\echo '  (expect 3 rows, hops 0/1/2)'

\echo '=== Check: transitive extends ==='
SELECT shrapnel.stereotype_extends(:grand_rev, 'shape_base') AS to_base,
       shrapnel.stereotype_extends(:grand_rev, 'shape_grand') AS to_self,
       shrapnel.stereotype_extends(:base_rev, 'shape_grand') AS to_child;
\echo '  (expect true | false | false)'

\echo '=== Check: effective contract inherits parent fields ==='
SELECT property_name, required, origin_stereotype, origin_version
FROM shrapnel.stereotype_effective_contract(:grand_rev)
ORDER BY property_name;
\echo '  (expect a,b from shape_base; c from shape_child; all required=true)'

\echo '=== Check: nearest declaration wins (optional->required upgrade is legal) ==='
-- base_opt: a required, b OPTIONAL (exercises the constructor's optional array).
SELECT shrapnel.stereotype_create_revision('shape_opt_base', NULL, NULL,
       ARRAY['a'], ARRAY['b']) AS opt_base_rev \gset
-- child_opt extends base_opt, upgrading b optional->required (monotonic, legal).
SELECT shrapnel.stereotype_create_revision('shape_opt_child', :opt_base_rev,
       'test: tighten b', ARRAY['a','b']) AS opt_child_rev \gset
SELECT property_name, required, origin_stereotype, origin_version
FROM shrapnel.stereotype_effective_contract(:opt_child_rev)
ORDER BY property_name;
\echo '  (expect a required origin shape_opt_base; b required origin shape_opt_child)'

\echo '=== Negative: required->optional downgrade is rejected (superset v2) ==='
BEGIN;
SELECT shrapnel.stereotype_create_revision('shape_opt_bad', :opt_child_rev,
       'test: illegal downgrade', ARRAY['a'], ARRAY['b']);
COMMIT;
\if :ERROR
    \echo '  OK: required->optional downgrade rejected'
\else
    \echo '  FAIL: required->optional downgrade accepted'
\endif

\echo '=== Negative: extending without rationale is rejected by the constructor ==='
SELECT shrapnel.stereotype_create_revision('shape_bad', :base_rev, NULL, ARRAY['a']);
\if :ERROR
    \echo '  OK: rationale-less extends rejected'
\else
    \echo '  FAIL: rationale-less extends accepted'
\endif

\echo '=== Negative: constructor with unknown parent revision ==='
SELECT shrapnel.stereotype_create_revision('shape_bad2', 999999, 'why not', ARRAY['a']);
\if :ERROR
    \echo '  OK: unknown parent rejected'
\else
    \echo '  FAIL: unknown parent accepted'
\endif

\echo '=== Check: depth-3 via constructor is allowed (base=0, child=1, grand=2) ==='
SELECT shrapnel.stereotype_create_revision('shape_g4', :grand_rev, 'test: depth 3', ARRAY['a','b','c']) AS g4_rev \gset
\if :{?g4_rev}
    \echo '  OK: depth-3 via constructor allowed'
\else
    \echo '  FAIL: depth-3 via constructor rejected'
\endif

\echo '=== Negative: depth guard through the constructor (5th level = depth 4) ==='
SELECT shrapnel.stereotype_create_revision('shape_g5', :g4_rev, 'test: too deep', ARRAY['a','b','c']);
\if :ERROR
    \echo '  OK: depth-4 via constructor rejected'
\else
    \echo '  FAIL: depth-4 via constructor accepted'
\endif

\echo '=== Classify: object meets the child contract ==='
INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id AS obj \gset
-- Give the object members a, b, c (string values).
INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id AS v1 \gset
INSERT INTO shrapnel.value_string (id, value) VALUES (:v1, 'va');
INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
VALUES (:obj, (SELECT id FROM shrapnel.field WHERE property_name='a'), :v1);
INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id AS v2 \gset
INSERT INTO shrapnel.value_string (id, value) VALUES (:v2, 'vb');
INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
VALUES (:obj, (SELECT id FROM shrapnel.field WHERE property_name='b'), :v2);
INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id AS v3 \gset
INSERT INTO shrapnel.value_string (id, value) VALUES (:v3, 'vc');
INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
VALUES (:obj, (SELECT id FROM shrapnel.field WHERE property_name='c'), :v3);

SELECT shrapnel.object_conformance(:obj) AS pre;
\echo '  (expect classified=false)'

SELECT shrapnel.object_classify(:obj, :child_rev, 'seeded-by-api-check') AS result;
\echo '  (expect classified=true, stereotype shape_child)'

\echo '=== Check: classification produced evidence + disposition facts ==='
SELECT f.property_name, vs.value
FROM shrapnel.object_attribute_value oav
JOIN shrapnel.field f ON f.id = oav.field_id
JOIN shrapnel.value v ON v.id = oav.value_id
JOIN shrapnel.value_string vs ON vs.id = v.id
WHERE oav.object_id = :obj AND f.property_name LIKE 'stereotype_conformance%'
ORDER BY f.property_name;
\echo '  (expect stereotype_conformance=conformant and disposition row)'

\echo '=== Check: object_conformance after classification ==='
SELECT shrapnel.object_conformance(:obj);
\echo '  (expect classified=true, conformant=true, missing=[])'

\echo '=== Negative: classify without required members aborts ==='
INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id AS obj2 \gset
INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id AS v4 \gset
INSERT INTO shrapnel.value_string (id, value) VALUES (:v4, 'va');
INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
VALUES (:obj2, (SELECT id FROM shrapnel.field WHERE property_name='a'), :v4);
SELECT shrapnel.object_classify(:obj2, :child_rev, 'should-fail');
\if :ERROR
    \echo '  OK: unevidenced classification rejected (no silent repair)'
\else
    \echo '  FAIL: unevidenced classification accepted'
\endif

\echo '=== Check: failed classify left no evidence or membership ==='
SELECT (SELECT count(*) FROM shrapnel.object_attribute_value oav
        JOIN shrapnel.field f ON f.id=oav.field_id
        WHERE oav.object_id = :obj2 AND f.property_name LIKE 'stereotype_conformance%') AS evidence_rows,
       (SELECT stereotype_revision_id FROM shrapnel.object_instance WHERE id = :obj2) AS membership;
\echo '  (expect 0 | empty — atomic abort left nothing behind)'

\echo '=== Check: views ==='
SELECT count(*) AS contract_rows FROM shrapnel.v_stereotype_contract;
SELECT count(*) AS classified_rows FROM shrapnel.v_object_stereotype;
\echo '=== Result ==='
\echo 'Script complete. Every OK line above is an API guarantee holding.'
