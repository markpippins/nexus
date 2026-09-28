-- ============================================================================
-- reconcile_0008_stereotype_reconcile.sql
--
-- Real-database checks for migration 0008 (plan 8261660, steps 1-2).
--
-- These are REAL-DB checks on purpose. The failure mode that matters for a
-- reconciliation verb is SILENCE: a no-op branch writes no rows, so none of
-- the deferred constraint triggers (fingerprint, superset-v2, C1 depth, field
-- integrity) fire to check it. A mocked or hermetic test of a comparison whose
-- failure mode is silence is close to worthless. Everything below executes
-- against a real PostgreSQL with migrations 0001-0008 applied.
--
-- Run with: psql -f test/reconcile_0008_stereotype_reconcile.sql
-- Driven by test/reconcile.test.js (needs SHRAPNEL_PG_DSN), or by hand:
--   psql "$SHRAPNEL_PG_DSN" -v ON_ERROR_STOP=0 -f <this file>
--
-- IDIOM: every assertion is one INSERT into a temp result table, and the whole
-- table is printed once at the end. (Matching the \gset + \if style of
-- api_check_stereotype.sql, but collected so the driver can assert on a single
-- ordered block and so exception-capturing DO blocks report through the same
-- channel. Asserting via RAISE NOTICE would be wrong here: these runs set
-- client_min_messages=error to keep the log clean, which silently swallows
-- NOTICEs and would make a passing run look empty.)
--
-- ON_ERROR_STOP is OFF so the negative cases (which are SUPPOSED to raise) do
-- not abort the run. Expected failures are captured inside plpgsql EXCEPTION
-- blocks, which run in a subtransaction and leave the outer transaction usable.
-- ============================================================================

\set ON_ERROR_STOP off

DROP TABLE IF EXISTS tap;
CREATE TEMP TABLE tap (
  seq    serial PRIMARY KEY,
  ok     boolean NOT NULL,
  label  text    NOT NULL,
  detail text
);

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


-- ============================================================================
-- Fixture
--
-- Two INDEPENDENT roots (ReconRoot, ReconOther) plus a two-level chain
-- (ChainParent -> ChainChild). The independent roots exist so the rationale
-- check can assert "exactly one stereotype minted": editing a root's declared
-- fields must not disturb anything else, whereas editing a PARENT also moves
-- the parent's head and would legitimately mint the child too (that is the
-- parent-drift case, checked separately below).
-- ============================================================================
\echo '=== Fixture: apply the catalog once via reconcile ==='
BEGIN;

SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconRoot', NULL, NULL, ARRAY['rq_one', 'rq_two']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'first apply of ReconRoot CREATES', 'created=' || :'created');

SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconOther', NULL, NULL, ARRAY['ro_one']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'first apply of ReconOther CREATES', 'created=' || :'created');

SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ChainParent', NULL, NULL, ARRAY['cp_one']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'first apply of ChainParent CREATES', 'created=' || :'created');
\set cp_v1 :revision_id

SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ChainChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('ChainParent')),
  'C2 rationale: ChainChild extends ChainParent for field-level conformance',
  ARRAY['cp_one', 'cc_one'], ARRAY['cc_optional']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'first apply of ChainChild CREATES', 'created=' || :'created');
\set cc_v1 :revision_id

COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 4, 'fixture committed 4 revisions', 'count=' || count(*)
FROM shrapnel.stereotype_revision;


-- ============================================================================
-- CHECK 1 — idempotence: re-applying identical declared state writes nothing
-- ============================================================================
\echo '=== Check 1: re-apply is a no-op ==='
SELECT count(*) AS n_before FROM shrapnel.stereotype_revision \gset

BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconRoot', NULL, NULL, ARRAY['rq_one', 'rq_two']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (NOT :'created'::boolean, 're-apply of ReconRoot reports created=false', 'created=' || :'created');
INSERT INTO tap (ok, label, detail) SELECT
  :revision_id = (SELECT head_revision_id FROM shrapnel.stereotype_resolve('ReconRoot')),
  're-apply returns the CURRENT head revision id', 'got ' || :revision_id;
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT (SELECT count(*) FROM shrapnel.stereotype_revision) = :n_before,
       're-apply wrote ZERO new revisions',
       'before=' || :n_before || ' after=' ||
         (SELECT count(*) FROM shrapnel.stereotype_revision);

-- The whole catalog, re-applied in the same topological order, is still quiet.
\echo '=== Check 1b: full catalog re-apply is quiet ==='
BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconRoot', NULL, NULL, ARRAY['rq_one','rq_two']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (NOT :'created'::boolean, 'ReconRoot reconciles (created=false)', 'created=' || :'created');
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconOther', NULL, NULL, ARRAY['ro_one']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (NOT :'created'::boolean, 'ReconOther reconciles (created=false)', 'created=' || :'created');
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ChainParent', NULL, NULL, ARRAY['cp_one']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (NOT :'created'::boolean, 'ChainParent reconciles (created=false)', 'created=' || :'created');
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ChainChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('ChainParent')),
  'C2 rationale: ChainChild extends ChainParent for field-level conformance',
  ARRAY['cp_one','cc_one'], ARRAY['cc_optional']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (NOT :'created'::boolean, 'ChainChild reconciles (created=false)', 'created=' || :'created');
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 4, 'full re-apply left the catalog at exactly 4 revisions',
       'count=' || count(*)
FROM shrapnel.stereotype_revision;


-- ============================================================================
-- CHECK 2 — field-registry stability: a no-op run inserts ZERO field rows
--
-- This is the invariant that makes idempotence safe. If a no-op run minted
-- fields, the fingerprint (which is over field IDs) would be comparing
-- against a registry that had just moved underneath it.
-- ============================================================================
\echo '=== Check 2: no-op run creates no shrapnel.field rows ==='
SELECT count(*) AS f_before FROM shrapnel.field \gset

BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconRoot', NULL, NULL, ARRAY['rq_one','rq_two']) \gset
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ChainChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('ChainParent')),
  'C2 rationale: ChainChild extends ChainParent for field-level conformance',
  ARRAY['cp_one','cc_one'], ARRAY['cc_optional']) \gset
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT (SELECT count(*) FROM shrapnel.field) = :f_before,
       'no-op run created ZERO field rows',
       'before=' || :f_before || ' after=' ||
         (SELECT count(*) FROM shrapnel.field);


-- ============================================================================
-- CHECK 3 — a declared-field change mints exactly ONE version
--
-- The child must NOT move here: ReconRoot is an independent root, so editing
-- it changes nothing upstream of it.
-- ============================================================================
\echo '=== Check 3: a field change mints exactly one version ==='
SELECT count(*) AS n_before FROM shrapnel.stereotype_revision \gset

BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconRoot', NULL, NULL, ARRAY['rq_one','rq_two'], ARRAY['rq_note']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'field change CREATES a new version', 'created=' || :'created');
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT (SELECT count(*) FROM shrapnel.stereotype_revision) = :n_before + 1,
       'exactly ONE new revision minted',
       'before=' || :n_before || ' after=' ||
         (SELECT count(*) FROM shrapnel.stereotype_revision);

-- Restore, so later checks start from a known state.
BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ReconRoot', NULL, NULL, ARRAY['rq_one','rq_two']) \gset
COMMIT;


-- ============================================================================
-- CHECK 4 — parent drift: a new parent head legitimately mints the CHILD
--
-- The parent is resolved at apply time and p_extends_revision is inside the
-- fingerprint, so a parent v2 forces a child revision even when the child's
-- own declared fields are byte-identical. This is CORRECT (superset and depth
-- conformance are parent-relative) and is asserted as such, not as a bug.
-- ============================================================================
\echo '=== Check 4: parent drift mints the child (parent-relative correctness) ==='
BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ChainParent', NULL, NULL, ARRAY['cp_one'], ARRAY['cp_extra']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'ChainParent v2 CREATES', 'created=' || :'created');
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 2, 'ChainParent is at version 2', 'versions=' || count(*)
FROM shrapnel.stereotype_revision r
  JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'ChainParent';

BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'ChainChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('ChainParent')),
  'C2 rationale: ChainChild extends ChainParent for field-level conformance',
  ARRAY['cp_one','cc_one'], ARRAY['cc_optional']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'child mints a new version purely because the parent head moved',
   'created=' || :'created');
INSERT INTO tap (ok, label, detail) VALUES
  (:revision_id <> :cc_v1, 'child revision id actually changed',
   'old=' || :cc_v1 || ' new=' || :revision_id);
COMMIT;

-- The child's declared fields were unchanged, yet it re-derived. Assert depth
-- is still parent-relative and the deferred triggers passed at COMMIT.
INSERT INTO tap (ok, label, detail)
SELECT bool_and(r.depth = 1),
       'child committed at depth 1 (superset + fingerprint triggers passed)',
       'max depth=' || max(r.depth)
FROM shrapnel.stereotype_revision r
  JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'ChainChild';


-- ============================================================================
-- CHECK 5 — reconciliation cannot launder a superset violation
--
-- A parent that gains a REQUIRED field, with a child that does not declare it,
-- must be REFUSED by the deferred superset-v2 trigger. If reconcile were
-- bypassing the trigger apparatus this case would silently commit.
--
-- SET CONSTRAINTS ALL IMMEDIATE fires the initially-deferred triggers inside
-- the plpgsql block so the refusal is observable and catchable within the
-- subtransaction, leaving the outer transaction usable.
-- ============================================================================
\echo '=== Check 5: superset oracle still refuses a non-conforming child ==='
BEGIN;
SELECT revision_id FROM shrapnel.stereotype_reconcile(
  'StrictParent', NULL, NULL, ARRAY['sp_base']) \gset
SELECT revision_id FROM shrapnel.stereotype_reconcile(
  'StrictChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('StrictParent')),
  'C2 rationale for the strict child', ARRAY['sp_base','sc_one']) \gset
COMMIT;

-- The parent grows a REQUIRED field the child does not declare.
BEGIN;
SELECT revision_id FROM shrapnel.stereotype_reconcile(
  'StrictParent', NULL, NULL, ARRAY['sp_base','sp_new_required']) \gset
COMMIT;

-- Case A: the child does NOT declare the parent's new required field. The
-- deferred superset-v2 trigger must refuse the write at COMMIT.
--
-- Deliberately NOT wrapped in a plpgsql EXCEPTION block. Rows inserted in a
-- plpgsql subtransaction get an xmin that differs from txid_current(), which
-- trips 0005's forbid_stereotype_field_mutation heuristic and falsely reports
-- the row's OWN field rows as frozen. That is a pre-existing 0005 defect (it
-- reproduces against 0005's original, untouched create_revision) and it is
-- documented at the bottom of this file. Catching the error that way would
-- test the wrong failure, so the refusal is observed BEHAVIOURALLY instead:
-- either a revision appears or it does not.
BEGIN;
SELECT revision_id FROM shrapnel.stereotype_reconcile(
  'StrictChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('StrictParent')),
  'C2 rationale for the strict child', ARRAY['sp_base','sc_one']) \gset
COMMIT;  -- expected to raise the superset refusal here

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 1,
       'non-conforming child REFUSED: no revision was written',
       'StrictChild revisions=' || count(*)
FROM shrapnel.stereotype_revision r
  JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'StrictChild';

-- Case B (the differential): the SAME child, now declaring the parent's new
-- required field, must succeed. Together with case A this pins the refusal on
-- the superset rule specifically, rather than on reconcile being unable to
-- write a revision at all.
BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'StrictChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('StrictParent')),
  'C2 rationale for the strict child',
  ARRAY['sp_base','sc_one','sp_new_required']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean,
   'conforming child (declares the new required field) CREATES',
   'created=' || :'created');
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 2,
       'refusal was the superset rule, not an inability to write',
       'StrictChild revisions=' || count(*)
FROM shrapnel.stereotype_revision r
  JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  WHERE s.name = 'StrictChild';


-- ============================================================================
-- CHECK 6 — C1 rationale guard applies on the reconcile path too
-- ============================================================================
\echo '=== Check 6: C1 rationale guard ==='
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM * FROM shrapnel.stereotype_reconcile(
      'NoRationale',
      (SELECT head_revision_id FROM shrapnel.stereotype_resolve('ChainParent')),
      NULL, ARRAY['cp_one']);
  EXCEPTION WHEN OTHERS THEN
    v_err := SQLERRM;
  END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err IS NOT NULL AND v_err LIKE '%requires a rationale%',
    'reconcile enforces the C1 rationale guard',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;


-- ============================================================================
-- CHECK 7 — create_revision is UNCHANGED: still mints on every call
--
-- The whole design rests on create_revision keeping its exact prior behavior.
-- If it had been made idempotent in place, these checks would fail and the
-- committed-migration contract would be broken.
-- ============================================================================
\echo '=== Check 7: create_revision regression (must still always mint) ==='
SELECT count(*) AS n_before FROM shrapnel.stereotype_revision \gset

BEGIN;
SELECT shrapnel.stereotype_create_revision(
  'LegacyCaller', NULL, NULL, ARRAY['lc_one']) AS r1 \gset
SELECT shrapnel.stereotype_create_revision(
  'LegacyCaller', NULL, NULL, ARRAY['lc_one']) AS r2 \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:r1 <> :r2, 'create_revision mints a NEW revision on identical repeat calls',
   'r1=' || :r1 || ' r2=' || :r2);
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT (SELECT count(*) FROM shrapnel.stereotype_revision) = :n_before + 2,
       'create_revision wrote 2 revisions for 2 identical calls (not idempotent)',
       'before=' || :n_before || ' after=' ||
         (SELECT count(*) FROM shrapnel.stereotype_revision);

-- The \gset chaining idiom that motivated keeping the verbs separate.
\echo '=== Check 7b: create_revision return value still chains as a parent ==='
BEGIN;
SELECT shrapnel.stereotype_create_revision('ChainBase', NULL, NULL, ARRAY['cb_one']) AS b \gset
SELECT shrapnel.stereotype_create_revision(
  'ChainKid', :b, 'C2 rationale', ARRAY['cb_one','ck_one']) AS k \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:k > :b, 'chained child revision id differs from parent',
   'parent=' || :b || ' child=' || :k);
-- The load-bearing assertion: the child is parented to EXACTLY the revision
-- create_revision returned, not merely to some revision of the same stereotype.
-- This is the contract that keeping create and reconcile as separate verbs is
-- there to protect.
INSERT INTO tap (ok, label, detail)
SELECT (SELECT r.parent_revision_id FROM shrapnel.stereotype_revision r WHERE r.id = :k) = :b,
       'chained child is parented to the revision create_revision RETURNED',
       'child parent=' ||
         (SELECT r.parent_revision_id FROM shrapnel.stereotype_revision r WHERE r.id = :k) ||
         ' expected=' || :b;
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT bool_and(r.depth = 1) AND count(*) = 1,
       'chained child committed at depth 1 under the right parent',
       'rows=' || count(*)
FROM shrapnel.stereotype_revision r
  JOIN shrapnel.stereotype s ON s.id = r.stereotype_id
  JOIN shrapnel.stereotype_revision p ON p.id = r.parent_revision_id
  JOIN shrapnel.stereotype ps ON ps.id = p.stereotype_id
  WHERE s.name = 'ChainKid' AND ps.name = 'ChainBase';

-- Duplicate-field error text must be preserved byte-for-byte.
\echo '=== Check 7c: error text preserved, and reconcile names itself ==='
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM * FROM shrapnel.stereotype_create_revision(
      'DupCheck', NULL, NULL, ARRAY['dc_one'], ARRAY['dc_one']);
  EXCEPTION WHEN OTHERS THEN
    v_err := SQLERRM;
  END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err = 'stereotype_create_revision: field dc_one declared more than once',
    'create_revision duplicate-field error text unchanged', coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM * FROM shrapnel.stereotype_reconcile(
      'DupCheck2', NULL, NULL, ARRAY['dc2_one'], ARRAY['dc2_one']);
  EXCEPTION WHEN OTHERS THEN
    v_err := SQLERRM;
  END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err = 'stereotype_reconcile: field dc2_one declared more than once',
    'reconcile reports ITSELF in the duplicate-field error', coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;


-- ============================================================================
-- Report
-- ============================================================================
\echo ''
\echo '=== reconcile_0008 results ==='
SELECT CASE WHEN ok THEN 'ok - ' ELSE 'NOT OK - ' END || label ||
       coalesce('   [' || detail || ']', '') AS result
FROM tap ORDER BY seq;

\echo ''
SELECT count(*) AS total,
       count(*) FILTER (WHERE ok) AS passed,
       count(*) FILTER (WHERE NOT ok) AS failed
FROM tap;

-- ============================================================================
-- KNOWN PRE-EXISTING DEFECT (0005, NOT introduced by 0008) — reported, not fixed
--
-- shrapnel.forbid_stereotype_field_mutation (0005 §0c) decides a row is
-- frozen with:
--
--   r.xmin::text::bigint <> (txid_current() % 4294967296)::bigint
--
-- A row inserted inside a plpgsql subtransaction is stamped with the
-- SUBtransaction's xid, while txid_current() returns the TOP-LEVEL
-- transaction's xid. They differ, so the trigger concludes a brand-new
-- revision is frozen and rejects the field rows that create_revision is
-- inserting for it right now.
--
-- Observed consequence: stereotype_create_revision CANNOT be called from
-- inside a plpgsql EXCEPTION block. It raises
--   "stereotype_field rows for revision N are frozen (append-only contract)"
-- and the revision is not created. Verified against 0005's ORIGINAL,
-- untouched create_revision, so this predates 0008.
--
-- Impact: any server-side code that wraps a catalog write in a retry or
-- exception handler silently fails to create the revision. Check 5 above is
-- written to avoid this trap deliberately.
--
-- NOT fixed here on purpose: 0008's scope is the reconcile verb, and
-- changing a freeze trigger on the append-only contract is a separate change
-- that needs its own risk assessment. A candidate fix is to compare against
-- the revision's own inserting transaction via pg_current_xact_id() /
-- xmin semantics rather than the top-level txid, or to key the check on
-- whether the revision row is visible in the current snapshot.
-- ============================================================================
