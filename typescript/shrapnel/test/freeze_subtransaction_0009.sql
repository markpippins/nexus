-- ============================================================================
-- freeze_subtransaction_0009.sql
--
-- Real-database checks for migration 0009: the stereotype_field freeze fix.
--
-- The gate on this fix is asymmetric on purpose. Proving the DEFECT is gone is
-- easy; the thing that actually matters is proving the FREEZE still holds. A
-- trigger that stopped rejecting everything would make every "the defect is
-- fixed" test pass. So the append-only assertions come first and are
-- deliberately as loud as the fix assertions.
--
--   0005's rule:  a revision's stereotype_field rows may be touched only while
--                 the revision's inserting transaction is still open.
--   0005's bug:   it implemented that as xid arithmetic, so a revision created
--                 inside a plpgsql SUBtransaction looked frozen.
--   0009's fix:   it asks pg_xact_status() whether the inserting transaction is
--                 still in progress. Committed -> still frozen. Fail-closed.
--
-- Run: psql -v ON_ERROR_STOP=0 -f test/freeze_subtransaction_0009.sql
--
-- IDIOM NOTES (all three learned the hard way; do not "simplify" them away)
--   * Every assertion is one INSERT into a temp result table, printed once at
--     the end. RAISE NOTICE is NOT used: these runs set
--     client_min_messages=error, which silently swallows NOTICEs and makes a
--     passing run look empty.
--   * psql does NOT substitute :variables inside dollar-quoted DO bodies, so
--     every block below resolves the ids it needs by name from the catalog
--     itself. An earlier version interpolated :r1 / :fa into DO bodies and the
--     assertions silently errored without ever reporting a result.
--   * Every verdict is wrapped in coalesce(v_err, ''). `NULL LIKE 'pattern'` is
--     NULL, not FALSE — so an assertion that expected an error but got none
--     would compute NULL, violate the tap.ok NOT NULL constraint, and DISAPPEAR
--     from the report instead of failing. A silently absent assertion is worse
--     than a red one.
-- ============================================================================

\set ON_ERROR_STOP off

DROP TABLE IF EXISTS tap;
CREATE TEMP TABLE tap (
  seq    serial PRIMARY KEY,
  ok     boolean NOT NULL,
  label  text    NOT NULL,
  detail text
);

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

-- A committed revision with known field rows, to attack in Part 1.
-- fs_c is registered up front even though FrozenShape does not declare it: the
-- post-contract-growth check needs a real target row, and an INSERT ... SELECT
-- matching nothing would never reach the trigger at all — a vacuous "the freeze
-- held" assertion.
INSERT INTO shrapnel.field
  (is_calculated, field_index, label, name, property_name, field_type_code)
VALUES (false, 0, 'fs_c', 'fs_c', 'fs_c', 2)
ON CONFLICT (property_name) DO NOTHING;

BEGIN;
SELECT shrapnel.stereotype_create_revision(
  'FrozenShape', NULL, NULL, ARRAY['fs_a', 'fs_b']) AS r1 \gset
COMMIT;
-- :r1 is captured OUTSIDE any dollar-quoted body, so the ONE plain-SQL use
-- below is safe. Every DO block resolves its own ids from the catalog; psql
-- does not substitute :variables inside dollar-quoted bodies, and an earlier
-- version of this file did exactly that and reported nothing at all.


-- ============================================================================
-- PART 1 — THE FREEZE STILL HOLDS (verified against the FIXED trigger)
--
-- Everything here also passed under 0005. If any of it now fails, the fix has
-- weakened the append-only contract, which is worse than the original bug.
-- ============================================================================
\echo '=== Part 1: the append-only freeze is NOT weakened ==='

-- Required-flag flip on a committed revision. The revision id is resolved
-- from the catalog INSIDE the block, because psql does not substitute
-- :variables inside a dollar-quoted body — using :'r1' here made the whole
-- block fail silently and report nothing.
DO $d$
DECLARE v_err text := NULL; v_rid bigint;
BEGIN
  SELECT r.id INTO v_rid
  FROM shrapnel.stereotype_revision r JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'FrozenShape';
  BEGIN
    UPDATE shrapnel.stereotype_field SET required = false
     WHERE stereotype_revision_id = v_rid
       AND field_id = (SELECT id FROM shrapnel.field WHERE property_name = 'fs_a');
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    coalesce(v_err, '') LIKE '%are frozen (append-only contract)%',
    'committed revision: required-flag flip still REJECTED',
    coalesce(v_err, 'ACCEPTED — the freeze was weakened'));
END;
$d$;

-- Field-row deletion from a committed revision.
DO $d$
DECLARE v_err text := NULL; v_rid bigint;
BEGIN
  SELECT r.id INTO v_rid
  FROM shrapnel.stereotype_revision r JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'FrozenShape';
  BEGIN
    DELETE FROM shrapnel.stereotype_field
     WHERE stereotype_revision_id = v_rid
       AND field_id = (SELECT id FROM shrapnel.field WHERE property_name = 'fs_b');
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    coalesce(v_err, '') LIKE '%are frozen (append-only contract)%',
    'committed revision: field-row DELETE still REJECTED',
    coalesce(v_err, 'ACCEPTED — the freeze was weakened'));
END;
$d$;

-- Post-commit contract GROWTH. The case 0005's bug was closest to missing: a
-- correct fix must still refuse a NEW field row on a committed revision.
DO $d$
DECLARE v_err text := NULL; v_rid bigint;
BEGIN
  SELECT r.id INTO v_rid
  FROM shrapnel.stereotype_revision r JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'FrozenShape';
  BEGIN
    INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
    SELECT v_rid, id, true FROM shrapnel.field WHERE property_name = 'fs_c';
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    coalesce(v_err, '') LIKE '%are frozen (append-only contract)%',
    'committed revision: post-contract-growth INSERT still REJECTED',
    coalesce(v_err, 'ACCEPTED — the freeze was weakened'));
END;
$d$;

-- The revision row itself is immutable too (0004 no_update trigger). Assert the
-- fix did not accidentally make revisions mutable.
DO $d$
DECLARE v_err text := NULL; v_rid bigint;
BEGIN
  SELECT r.id INTO v_rid
  FROM shrapnel.stereotype_revision r JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'FrozenShape';
  BEGIN
    UPDATE shrapnel.stereotype_revision SET version = version + 1 WHERE id = v_rid;
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err IS NOT NULL, 'committed revision: revision row still immutable',
    coalesce(v_err, 'ACCEPTED'));
END;
$d$;

-- And a committed revision must not be reported as under construction.
INSERT INTO tap (ok, label, detail)
SELECT NOT shrapnel.stereotype_revision_under_construction(:r1),
       'committed revision reports under_construction = false',
       'got ' || shrapnel.stereotype_revision_under_construction(:r1)::text;


-- ============================================================================
-- PART 2 — THE DEFECT IS FIXED
--
-- Every case below creates a revision inside a plpgsql subtransaction. Under
-- 0005 all of them raised "are frozen" and the revision was silently lost.
-- ============================================================================
\echo '=== Part 2: revisions created inside subtransactions now persist ==='

-- Case A: the headline case — create_revision inside an EXCEPTION block, which
-- is the exact shape any retry / error-handling server code would use.
--
-- PERFORM, not `SELECT <column> INTO`: stereotype_create_revision returns a
-- scalar bigint, so it has no `revision_id` column to select.
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM shrapnel.stereotype_create_revision(
      'SubTxBasic', NULL, NULL, ARRAY['sb_one']);
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err IS NULL, 'create_revision inside a plpgsql EXCEPTION block succeeds',
    coalesce(v_err, 'ok'));
END;
$d$;

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 1, 'the subtransaction revision actually PERSISTED (not silently lost)',
       'SubTxBasic revisions=' || count(*)
FROM shrapnel.stereotype_revision r JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
WHERE s.name = 'SubTxBasic';

-- Its field rows must have landed too, not just the revision row.
INSERT INTO tap (ok, label, detail)
SELECT count(*) = 1, 'its stereotype_field rows landed too',
       'field rows=' || count(*)
FROM shrapnel.stereotype_field sf
JOIN shrapnel.stereotype_revision r ON r.id = sf.stereotype_revision_id
JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
WHERE s.name = 'SubTxBasic';

-- Case B: NESTED subtransactions — what a retry wrapper around a retry wrapper
-- produces.
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    BEGIN
      PERFORM shrapnel.stereotype_create_revision(
        'SubTxNested', NULL, NULL, ARRAY['sn_one']);
    EXCEPTION WHEN OTHERS THEN
      RAISE;  -- re-raise, so the outer handler observes it
    END;
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err IS NULL, 'create_revision inside NESTED subtransactions succeeds',
    coalesce(v_err, 'ok'));
END;
$d$;

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 1, 'the nested-subtransaction revision persisted',
       'SubTxNested revisions=' || count(*)
FROM shrapnel.stereotype_revision r JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
WHERE s.name = 'SubTxNested';

-- Case C: build one in a subtransaction, commit, then confirm the very same
-- revision is now frozen. This is the property the fix must not lose.
BEGIN;
DO $d$
BEGIN
  PERFORM shrapnel.stereotype_create_revision(
    'SubTxThenFreeze', NULL, NULL, ARRAY['stf_one']);
END;
$d$;
COMMIT;

-- The field the growth attempt will use, created up front so the INSERT below
-- is a meaningful attempt rather than a no-op.
INSERT INTO shrapnel.field
  (is_calculated, field_index, label, name, property_name, field_type_code)
VALUES (false, 0, 'stf_extra', 'stf_extra', 'stf_extra', 2)
ON CONFLICT (property_name) DO NOTHING;

\echo '=== Part 2b: once committed, a subtransaction-made revision IS frozen ==='
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    INSERT INTO shrapnel.stereotype_field (stereotype_revision_id, field_id, required)
    SELECT r.id, f.id, true
    FROM shrapnel.stereotype_revision r
    JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
    JOIN shrapnel.field f ON f.property_name = 'stf_extra'
    WHERE s.name = 'SubTxThenFreeze';
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    coalesce(v_err, '') LIKE '%are frozen (append-only contract)%',
    'a COMMITTED subtransaction-made revision is frozen like any other',
    coalesce(v_err, 'ACCEPTED — the fix let a committed revision be modified'));
END;
$d$;


-- ============================================================================
-- PART 3 — THE PREREQUISITE FOR PLAN 8261661
--
-- The retire/tombstone design writes inside plpgsql EXCEPTION blocks so it can
-- refuse and roll back cleanly. 0009 is what makes that possible at all.
--
-- NOTE ON HOW THE REFUSAL IS OBSERVED. An earlier version of this block issued
-- SET CONSTRAINTS ALL IMMEDIATE inside the plpgsql subtransaction to force the
-- deferred superset trigger to fire where it could be caught. It does not work:
-- pending deferred constraint events are not flushed by that statement inside a
-- subtransaction, so nothing was ever raised and the assertion silently read
-- "IT COMMITTED" even on a correct build. Deferred-trigger refusals are
-- therefore observed BEHAVIOURALLY here — a real transaction whose COMMIT
-- fails, and a row count — exactly as 0008's Check 5 does. Trying to catch the
-- error in-process tests the wrong thing.
-- ============================================================================
\echo '=== Part 3a: the write now REACHES the trigger instead of being pre-empted ==='

-- Before 0009 this raised "are frozen" and never got as far as the superset
-- check. After 0009 the child is actually created, so whatever happens next is
-- the database's real verdict rather than the freeze bug.
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM shrapnel.stereotype_create_revision(
      'StrictProbe', NULL, NULL, ARRAY['sp_base']);
    PERFORM shrapnel.stereotype_create_revision(
      'StrictChild',
      (SELECT head_revision_id FROM shrapnel.stereotype_resolve('StrictProbe')),
      'C2 rationale for the strict child',
      ARRAY['sp_base', 'sc_one']);
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    coalesce(v_err, '') NOT LIKE '%are frozen%',
    'a write inside an EXCEPTION block is no longer pre-empted by the freeze bug',
    coalesce(v_err, 'ok'));
END;
$d$;

\echo '=== Part 3b: a non-conforming child is still refused, with nothing left behind ==='
BEGIN;
SELECT shrapnel.stereotype_create_revision(
  'RefuseParent', NULL, NULL, ARRAY['rp_base']) AS rp \gset
SELECT shrapnel.stereotype_create_revision(
  'RefuseChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('RefuseParent')),
  'C2 rationale for the refusing child', ARRAY['rp_base','rc_one']) AS rc \gset
COMMIT;

-- Parent gains a REQUIRED field the child never declared.
BEGIN;
SELECT shrapnel.stereotype_create_revision(
  'RefuseParent', NULL, NULL, ARRAY['rp_base','rp_new_required']) AS rp2 \gset
COMMIT;

-- Now attempt the non-conforming child. The deferred superset-v2 trigger
-- refuses this at COMMIT, which aborts the whole transaction.
BEGIN;
SELECT shrapnel.stereotype_create_revision(
  'RefuseChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('RefuseParent')),
  'C2 rationale for the refusing child', ARRAY['rp_base','rc_one']) AS rc2 \gset
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 1, 'the non-conforming child revision was REFUSED (still 1 revision)',
       'RefuseChild revisions=' || count(*)
FROM shrapnel.stereotype_revision r JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
WHERE s.name = 'RefuseChild';

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 0, 'no partial stereotype_field rows were left behind',
       'orphan field rows=' || count(*)
FROM shrapnel.stereotype_field sf
WHERE NOT EXISTS (SELECT 1 FROM shrapnel.stereotype_revision r WHERE r.id = sf.stereotype_revision_id);


-- ============================================================================
-- Report
-- ============================================================================
\echo ''
\echo '=== freeze_subtransaction_0009 results ==='
SELECT CASE WHEN ok THEN 'ok - ' ELSE 'NOT OK - ' END || label ||
       coalesce('   [' || detail || ']', '') AS result
FROM tap ORDER BY seq;

\echo ''
SELECT count(*) AS total,
       count(*) FILTER (WHERE ok) AS passed,
       count(*) FILTER (WHERE NOT ok) AS failed
FROM tap;
