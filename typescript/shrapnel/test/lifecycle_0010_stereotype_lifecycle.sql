-- ============================================================================
-- lifecycle_0010_stereotype_lifecycle.sql
--
-- Real-database checks for migration 0010: the retire / tombstone verb
-- (plan 8261661, completing plan 8261660).
--
-- These are REAL-DB checks on purpose. The properties under test are the ones
-- a mock cannot express:
--   * append-only triggers actually reject UPDATE/DELETE;
--   * a BEFORE INSERT trigger refuses a live parent at the moment of insert;
--   * the plan token is bound to observed head revision rows;
--   * existing object_instance rows are untouched by a lifecycle transition.
--
-- Run with: psql -f test/lifecycle_0010_stereotype_lifecycle.sql
-- Driven by test/lifecycle.test.js (needs SHRAPNEL_PG_DSN), or by hand:
--   psql "$SHRAPNEL_PG_DSN" -v ON_ERROR_STOP=0 -f <this file>
--
-- IDIOM: every assertion is one INSERT into a temp result table; the whole
-- table is printed once at the end. Negative cases are captured inside
-- plpgsql EXCEPTION blocks (ON_ERROR_STOP is OFF so they do not abort the
-- run). Asserting via RAISE NOTICE would be wrong: these runs set
-- client_min_messages=error, which silently swallows NOTICEs.
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
    shrapnel.stereotype_lifecycle_event,
    shrapnel.stereotype_source_owner,
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
-- emitter-a owns: LcRoot -> LcMid -> LcLeaf (a two-level chain), LcSolo,
-- LcOther, plus the negative-path fixtures LcNegHead / LcNegParent /
-- LcNegChild / LcNegReason.
-- emitter-b owns: LcForeign (used to prove retirement is owner-scoped).
-- ============================================================================
\echo '=== Fixture: reconcile + claim ownership ==='
BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcRoot', NULL, NULL, ARRAY['lc_root_f1']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'fixture: LcRoot created', 'created=' || :'created');

SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcMid',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('LcRoot')),
  'C2 rationale: LcMid extends LcRoot',
  ARRAY['lc_root_f1', 'lc_mid_f1']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'fixture: LcMid created', 'created=' || :'created');

SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcLeaf',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('LcMid')),
  'C2 rationale: LcLeaf extends LcMid',
  ARRAY['lc_root_f1', 'lc_mid_f1', 'lc_leaf_f1']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'fixture: LcLeaf created', 'created=' || :'created');

SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcSolo', NULL, NULL, ARRAY['lc_solo_f1']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'fixture: LcSolo created', 'created=' || :'created');

SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcOther', NULL, NULL, ARRAY['lc_other_f1']) \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'created'::boolean, 'fixture: LcOther created', 'created=' || :'created');

SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcNegHead', NULL, NULL, ARRAY['lc_neg_head_f1']) \gset
SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcNegReason', NULL, NULL, ARRAY['lc_neg_reason_f1']) \gset
SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcNegParent', NULL, NULL, ARRAY['lc_neg_parent_f1']) \gset
SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcNegChild',
  (SELECT head_revision_id FROM shrapnel.stereotype_resolve('LcNegParent')),
  'C2 rationale: LcNegChild extends LcNegParent',
  ARRAY['lc_neg_parent_f1', 'lc_neg_child_f1']) \gset

SELECT revision_id, created FROM shrapnel.stereotype_reconcile_owned(
  'emitter-b', 'LcForeign', NULL, NULL, ARRAY['lc_foreign_f1']) \gset
COMMIT;


-- ============================================================================
-- CHECK 1 — the default plan: every declared name reconciles
-- ============================================================================
\echo '=== Check 1: default plan is all-reconcile ==='
SELECT plan_token AS tok_default
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a',
         ARRAY['LcRoot','LcMid','LcLeaf','LcSolo','LcOther'],
         ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb)
 LIMIT 1 \gset
INSERT INTO tap (ok, label, detail) VALUES
  (:'tok_default' LIKE 'sha256:%', 'plan returns a sha256 plan token', left(:'tok_default', 20));
INSERT INTO tap (ok, label, detail)
SELECT bool_and(action = 'reconcile'),
       'all five declared names reconcile, none blocked',
       string_agg(name || '=' || action, ',' ORDER BY name)
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-a',
       ARRAY['LcRoot','LcMid','LcLeaf','LcSolo','LcOther'],
       ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb)
WHERE name IN ('LcRoot','LcMid','LcLeaf','LcSolo','LcOther');

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 0,
       'unowned source sees no names it does not own',
       'rows=' || count(*)
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-c', ARRAY['LcSolo'], ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb)
WHERE name <> 'LcSolo';

-- The design record names phase 1 "report"; the implementation names it
-- "plan". They must be the same read, row for row and token for token, or an
-- approved report would not be an applicable plan.
INSERT INTO tap (ok, label, detail)
SELECT (
  SELECT jsonb_agg(to_jsonb(x) ORDER BY x.name)
    FROM shrapnel.stereotype_lifecycle_report(
           'emitter-a',
           ARRAY['LcRoot','LcMid','LcLeaf','LcSolo','LcOther'],
           ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb) x
) IS NOT DISTINCT FROM (
  SELECT jsonb_agg(to_jsonb(y) ORDER BY y.name)
    FROM shrapnel.stereotype_lifecycle_plan(
           'emitter-a',
           ARRAY['LcRoot','LcMid','LcLeaf','LcSolo','LcOther'],
           ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb) y
),
       'report is an exact alias of plan (same rows, actions and plan token)',
       'report rows = plan rows';


-- ============================================================================
-- CHECK 2 — annotations choose the action
-- ============================================================================
\echo '=== Check 2: @retired / @deprecated choose the action ==='
INSERT INTO tap (ok, label, detail)
SELECT action = 'retire' AND blocked_by IS NULL,
       '@retired on LcSolo plans a retirement',
       'action=' || action || ' blocked_by=' || coalesce(blocked_by, 'NULL')
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-a', ARRAY['LcSolo'], ARRAY['LcSolo'], ARRAY[]::text[],
       '{"LcSolo":{"reason":"no producers remain"}}'::jsonb)
WHERE name = 'LcSolo';

INSERT INTO tap (ok, label, detail)
SELECT action = 'deprecate' AND blocked_by IS NULL,
       '@deprecated on LcSolo plans a deprecation',
       'action=' || action || ' blocked_by=' || coalesce(blocked_by, 'NULL')
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-a', ARRAY['LcSolo'], ARRAY[]::text[], ARRAY['LcSolo'],
       '{"LcSolo":{"reason":"stop authoring"}}'::jsonb)
WHERE name = 'LcSolo';

-- Absence is never a retirement instruction: a @retired name that is not also
-- declared is rejected at the API boundary.
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM * FROM shrapnel.stereotype_lifecycle_plan(
      'emitter-a', ARRAY['LcOther'], ARRAY['LcSolo'], ARRAY[]::text[],
      '{"LcSolo":{"reason":"x"}}'::jsonb);
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%every @retired name must also be declared%',
    '@retired on an undeclared name is refused (absence never retires)',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM * FROM shrapnel.stereotype_lifecycle_plan(
      'emitter-a', ARRAY['LcSolo'], ARRAY['LcSolo'], ARRAY['LcSolo'],
      '{"LcSolo":{"reason":"x"}}'::jsonb);
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%cannot be annotated both%',
    'a name cannot be both @retired and @deprecated',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;


-- ============================================================================
-- CHECK 3 — ownership scopes the destructive verb
-- ============================================================================
\echo '=== Check 3: ownership is owner-scoped ==='
INSERT INTO tap (ok, label, detail)
SELECT action = 'blocked' AND blocked_by = 'not_owned',
       'another source cannot retire LcSolo',
       'action=' || action || ' blocked_by=' || coalesce(blocked_by, 'NULL')
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-b', ARRAY['LcSolo'], ARRAY['LcSolo'], ARRAY[]::text[],
       '{"LcSolo":{"reason":"x"}}'::jsonb)
WHERE name = 'LcSolo';

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM shrapnel.stereotype_claim_ownership('emitter-b', 'LcSolo');
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%already owned by source emitter-a%',
    'ownership cannot be transferred by a second claim',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

INSERT INTO tap (ok, label, detail) VALUES (
  shrapnel.stereotype_claim_ownership('emitter-a', 'LcSolo') IS NOT NULL,
  'the owner re-claiming is idempotent',
  'claim returned a stereotype id');


-- ============================================================================
-- CHECK 4 — absence is an advisory orphan_candidate, never an action
-- ============================================================================
\echo '=== Check 4: absence is advisory only ==='
SELECT plan_token AS tok_orphan
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a',
         ARRAY['LcRoot','LcMid','LcLeaf','LcOther'],
         ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb)
 LIMIT 1 \gset
INSERT INTO tap (ok, label, detail)
SELECT action = 'orphan_candidate' AND blocked_by IS NULL,
       'LcSolo omitted from the source is an orphan_candidate',
       'action=' || action
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-a',
       ARRAY['LcRoot','LcMid','LcLeaf','LcOther'],
       ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb)
WHERE name = 'LcSolo';

SELECT count(*) AS ev_before FROM shrapnel.stereotype_lifecycle_event \gset
BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_orphan',
  ARRAY['LcRoot','LcMid','LcLeaf','LcOther'],
  ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb);
COMMIT;
INSERT INTO tap (ok, label, detail)
SELECT (SELECT count(*) FROM shrapnel.stereotype_lifecycle_event) = :ev_before
       AND shrapnel.stereotype_current_state(
             (SELECT id FROM shrapnel.stereotype WHERE name = 'LcSolo')) = 'active',
       'applying an orphan plan writes ZERO events and leaves LcSolo active',
       'events before=' || :ev_before || ' after=' ||
         (SELECT count(*) FROM shrapnel.stereotype_lifecycle_event);


-- ============================================================================
-- CHECK 5 — a live parent cannot be retired; reverse-topological order works
-- ============================================================================
\echo '=== Check 5: live parent refused; subtree retires children-first ==='
INSERT INTO tap (ok, label, detail)
SELECT action = 'blocked' AND blocked_by LIKE 'live_children:%'
       AND blocked_by LIKE '%LcMid%' AND blocked_by LIKE '%LcLeaf%',
       'retiring LcRoot alone is blocked by its live descendants',
       'blocked_by=' || coalesce(blocked_by, 'NULL')
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-a', ARRAY['LcRoot','LcMid','LcLeaf'],
       ARRAY['LcRoot'], ARRAY[]::text[],
       '{"LcRoot":{"reason":"x"}}'::jsonb)
WHERE name = 'LcRoot';

SELECT plan_token AS tok_subtree
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcRoot','LcMid','LcLeaf'],
         ARRAY['LcRoot','LcMid','LcLeaf'], ARRAY[]::text[],
         '{"LcRoot":{"reason":"retire subtree"},
           "LcMid":{"reason":"retire subtree"},
           "LcLeaf":{"reason":"retire subtree"}}'::jsonb)
 LIMIT 1 \gset
INSERT INTO tap (ok, label, detail)
SELECT bool_and(action = 'retire' AND blocked_by IS NULL),
       'the whole subtree plans cleanly when every level is annotated',
       string_agg(name || '=' || action, ',' ORDER BY name)
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-a', ARRAY['LcRoot','LcMid','LcLeaf'],
       ARRAY['LcRoot','LcMid','LcLeaf'], ARRAY[]::text[],
       '{"LcRoot":{"reason":"retire subtree"},
         "LcMid":{"reason":"retire subtree"},
         "LcLeaf":{"reason":"retire subtree"}}'::jsonb)
WHERE name IN ('LcRoot','LcMid','LcLeaf');

BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_subtree',
  ARRAY['LcRoot','LcMid','LcLeaf'],
  ARRAY['LcRoot','LcMid','LcLeaf'], ARRAY[]::text[],
  '{"LcRoot":{"reason":"retire subtree"},
    "LcMid":{"reason":"retire subtree"},
    "LcLeaf":{"reason":"retire subtree"}}'::jsonb);
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT bool_and(shrapnel.stereotype_current_state(s.id) = 'retired'),
       'all three subtree stereotypes are retired',
       string_agg(s.name || '=' || shrapnel.stereotype_current_state(s.id), ',' ORDER BY s.name)
FROM shrapnel.stereotype s
WHERE s.name IN ('LcRoot','LcMid','LcLeaf');

INSERT INTO tap (ok, label, detail)
SELECT max(ev_leaf.event_id) < min(ev_mid.event_id)
       AND max(ev_mid.event_id) < min(ev_root.event_id),
       'retirement events are written children-before-parents',
       'leaf=' || (SELECT max(event_id) FROM shrapnel.stereotype_lifecycle_event ev
                    JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id
                   WHERE s.name = 'LcLeaf')
       || ' mid=' || (SELECT max(event_id) FROM shrapnel.stereotype_lifecycle_event ev
                       JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id
                      WHERE s.name = 'LcMid')
       || ' root=' || (SELECT max(event_id) FROM shrapnel.stereotype_lifecycle_event ev
                        JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id
                       WHERE s.name = 'LcRoot')
FROM (SELECT max(event_id) AS event_id FROM shrapnel.stereotype_lifecycle_event ev
        JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id WHERE s.name = 'LcLeaf') ev_leaf,
     (SELECT max(event_id) AS event_id FROM shrapnel.stereotype_lifecycle_event ev
        JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id WHERE s.name = 'LcMid') ev_mid,
     (SELECT max(event_id) AS event_id FROM shrapnel.stereotype_lifecycle_event ev
        JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id WHERE s.name = 'LcRoot') ev_root;


-- ============================================================================
-- CHECK 6 — retiring a live parent DIRECTLY is refused by the DB, not the verb
-- ============================================================================
\echo '=== Check 6: the trigger refuses a live parent even without apply ==='
-- Deprecate the parent validly, then attempt the retirement event directly.
SELECT plan_token AS tok_neg_parent
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcNegParent','LcNegChild'],
         ARRAY[]::text[], ARRAY['LcNegParent'],
         '{"LcNegParent":{"reason":"stop authoring"}}'::jsonb)
 LIMIT 1 \gset
BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_neg_parent',
  ARRAY['LcNegParent','LcNegChild'],
  ARRAY[]::text[], ARRAY['LcNegParent'],
  '{"LcNegParent":{"reason":"stop authoring"}}'::jsonb);
COMMIT;

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    INSERT INTO shrapnel.stereotype_lifecycle_event
      (stereotype_id, from_state, to_state, reason, source, at_revision_id)
    SELECT s.id, 'deprecated', 'retired', 'bypass apply', 'emitter-a',
           r.head_revision_id
      FROM shrapnel.stereotype s,
           LATERAL shrapnel.stereotype_resolve(s.name) r
     WHERE s.name = 'LcNegParent';
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%live children%LcNegChild%',
    'a direct retirement event on a live parent is refused by the trigger',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;


-- ============================================================================
-- CHECK 7 — two-phase: the plan token is required and is bound to heads
-- ============================================================================
\echo '=== Check 7: plan token is required and bound to observed heads ==='
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM * FROM shrapnel.stereotype_lifecycle_apply(
      'emitter-a', NULL, ARRAY['LcSolo'], ARRAY['LcSolo'], ARRAY[]::text[],
      '{"LcSolo":{"reason":"x"}}'::jsonb);
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%p_plan_token is required%',
    'apply without a plan token is refused',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

SELECT plan_token AS tok_stale
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcSolo','LcOther'],
         ARRAY['LcSolo'], ARRAY[]::text[],
         '{"LcSolo":{"reason":"x"}}'::jsonb)
 LIMIT 1 \gset

-- Move the catalog between the two phases: LcOther's declared contract
-- changes, so its head revision moves and the token must go stale.
BEGIN;
SELECT * FROM shrapnel.stereotype_reconcile_owned(
  'emitter-a', 'LcOther', NULL, NULL, ARRAY['lc_other_f1','lc_other_f2']);
COMMIT;

CREATE TEMP TABLE IF NOT EXISTS lc_stale(tok text);
TRUNCATE lc_stale;
INSERT INTO lc_stale VALUES (:'tok_stale');

DO $d$
DECLARE v_err text := NULL; v_tok text;
BEGIN
  SELECT tok INTO v_tok FROM lc_stale;
  BEGIN
    PERFORM * FROM shrapnel.stereotype_lifecycle_apply(
      'emitter-a', v_tok, ARRAY['LcSolo','LcOther'],
      ARRAY['LcSolo'], ARRAY[]::text[],
      '{"LcSolo":{"reason":"x"}}'::jsonb);
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%plan token is stale%',
    'a plan token observed before a head revision moved is refused',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;


-- ============================================================================
-- CHECK 8 — the destructive verb: retire, refuse reconcile, refuse classify
-- ============================================================================
\echo '=== Check 8: retire LcSolo and observe both write-path guards ==='
-- Classify an instance BEFORE retirement, so we can prove history survives.
BEGIN;
INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id AS solo_obj \gset
INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id AS solo_v1 \gset
INSERT INTO shrapnel.value_string (id, value) VALUES (:solo_v1, 'present');
INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
  SELECT :solo_obj, f.id, :solo_v1 FROM shrapnel.field f
   WHERE f.property_name = 'lc_solo_f1';
COMMIT;
SELECT shrapnel.object_classify(
         :solo_obj,
         (SELECT head_revision_id FROM shrapnel.stereotype_resolve('LcSolo')),
         'before retirement') AS cls \gset
INSERT INTO tap (ok, label, detail) VALUES (
  (:'cls')::jsonb ->> 'lifecycle_state' = 'active',
  'object_classify reports lifecycle_state=active before retirement',
  :'cls');

SELECT plan_token AS tok_retire
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcSolo'], ARRAY['LcSolo'], ARRAY[]::text[],
         '{"LcSolo":{"reason":"no producers remain","reference":"NEXUS-1234"}}'::jsonb)
 LIMIT 1 \gset

BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_retire', ARRAY['LcSolo'],
  ARRAY['LcSolo'], ARRAY[]::text[],
  '{"LcSolo":{"reason":"no producers remain","reference":"NEXUS-1234"}}'::jsonb);
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT shrapnel.stereotype_current_state(s.id) = 'retired',
       'LcSolo is retired',
       'state=' || shrapnel.stereotype_current_state(s.id)
FROM shrapnel.stereotype s WHERE s.name = 'LcSolo';

INSERT INTO tap (ok, label, detail)
SELECT count(*) = 2
       AND bool_and(source = 'emitter-a')
       AND bool_and(at_revision_id = (SELECT head_revision_id FROM shrapnel.stereotype_resolve('LcSolo'))),
       'retirement wrote exactly two events (deprecate then retire), both owner-scoped and head-pinned',
       'events=' || count(*)
FROM shrapnel.stereotype_lifecycle_event ev
JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id
WHERE s.name = 'LcSolo' AND ev.to_state IN ('deprecated','retired');

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    PERFORM * FROM shrapnel.stereotype_reconcile('LcSolo', NULL, NULL, ARRAY['lc_solo_f1']);
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%is retired; reactivate it before reconciling%',
    'stereotype_reconcile refuses a retired name (never silently resurrects)',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

CREATE TEMP TABLE IF NOT EXISTS lc_solo(obj bigint);
TRUNCATE lc_solo;
INSERT INTO lc_solo VALUES (:solo_obj);

DO $d$
DECLARE v_err text := NULL; v_obj bigint;
BEGIN
  SELECT obj INTO v_obj FROM lc_solo;
  BEGIN
    PERFORM shrapnel.object_classify(
      v_obj,
      (SELECT head_revision_id FROM shrapnel.stereotype_resolve('LcSolo')),
      'after retirement');
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%classification into a retired stereotype is refused%',
    'object_classify refuses a retired stereotype',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

INSERT INTO tap (ok, label, detail)
SELECT o.stereotype_id IS NOT NULL
       AND o.stereotype_revision_id IS NOT NULL
       AND shrapnel.object_conformance(o.id) ->> 'classified' = 'true',
       'the already-classified instance is untouched and still conformant',
       'stereotype_id=' || o.stereotype_id || ' revision=' || o.stereotype_revision_id
FROM shrapnel.object_instance o WHERE o.id = :solo_obj;

-- Re-applying the same retirement plan is a no-op: the transition set is
-- exhausted, not replayed.
SELECT count(*) AS ev_retired_before FROM shrapnel.stereotype_lifecycle_event \gset
SELECT plan_token AS tok_retire_again
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcSolo'], ARRAY['LcSolo'], ARRAY[]::text[],
         '{"LcSolo":{"reason":"no producers remain","reference":"NEXUS-1234"}}'::jsonb)
 LIMIT 1 \gset
BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_retire_again', ARRAY['LcSolo'],
  ARRAY['LcSolo'], ARRAY[]::text[],
  '{"LcSolo":{"reason":"no producers remain","reference":"NEXUS-1234"}}'::jsonb);
COMMIT;
INSERT INTO tap (ok, label, detail)
SELECT (SELECT count(*) FROM shrapnel.stereotype_lifecycle_event) = :ev_retired_before,
       're-applying an already-retired plan writes ZERO new events',
       'before=' || :ev_retired_before || ' after=' ||
         (SELECT count(*) FROM shrapnel.stereotype_lifecycle_event);


-- ============================================================================
-- CHECK 9 — deprecated is a forward gate, not destruction
-- ============================================================================
\echo '=== Check 9: deprecated remains usable ==='
-- LcOther was reconciled above; deprecate it.
SELECT plan_token AS tok_dep
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcOther'], ARRAY[]::text[], ARRAY['LcOther'],
         '{"LcOther":{"reason":"stop authoring new ones"}}'::jsonb)
 LIMIT 1 \gset
BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_dep', ARRAY['LcOther'],
  ARRAY[]::text[], ARRAY['LcOther'],
  '{"LcOther":{"reason":"stop authoring new ones"}}'::jsonb);
COMMIT;
INSERT INTO tap (ok, label, detail)
SELECT shrapnel.stereotype_current_state(s.id) = 'deprecated',
       'LcOther is deprecated',
       'state=' || shrapnel.stereotype_current_state(s.id)
FROM shrapnel.stereotype s WHERE s.name = 'LcOther';

BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'LcOther', NULL, NULL, ARRAY['lc_other_f1','lc_other_f2']) \gset
COMMIT;
INSERT INTO tap (ok, label, detail) VALUES (
  NOT :'created'::boolean,
  'stereotype_reconcile still works on a deprecated name (forward gate, still usable)',
  'created=' || :'created');

-- Classify a fresh instance into the deprecated stereotype: allowed, flagged.
BEGIN;
INSERT INTO shrapnel.object_instance DEFAULT VALUES RETURNING id AS dep_obj \gset
INSERT INTO shrapnel.value (value_type_code) VALUES (2) RETURNING id AS dep_v1 \gset
INSERT INTO shrapnel.value_string (id, value) VALUES (:dep_v1, 'present');
INSERT INTO shrapnel.object_attribute_value (object_id, field_id, value_id)
  SELECT :dep_obj, f.id, :dep_v1 FROM shrapnel.field f
   WHERE f.property_name IN ('lc_other_f1','lc_other_f2');
COMMIT;
SELECT shrapnel.object_classify(
         :dep_obj,
         (SELECT head_revision_id FROM shrapnel.stereotype_resolve('LcOther')),
         'deprecated but usable') AS dep_cls \gset
INSERT INTO tap (ok, label, detail) VALUES (
  (:'dep_cls')::jsonb ->> 'lifecycle_state' = 'deprecated',
  'object_classify into a deprecated stereotype succeeds and reports lifecycle_state=deprecated',
  :'dep_cls');


-- ============================================================================
-- CHECK 10 — un-retiring restores the name and the full history remains
-- ============================================================================
\echo '=== Check 10: reactivation restores the name ==='
SELECT plan_token AS tok_activate
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcSolo'], ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb)
 LIMIT 1 \gset
INSERT INTO tap (ok, label, detail)
SELECT action = 'activate',
       'declaring LcSolo active again plans a reactivation',
       'action=' || action
FROM shrapnel.stereotype_lifecycle_plan(
       'emitter-a', ARRAY['LcSolo'], ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb)
WHERE name = 'LcSolo';

BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_activate', ARRAY['LcSolo'],
  ARRAY[]::text[], ARRAY[]::text[], '{}'::jsonb);
COMMIT;

INSERT INTO tap (ok, label, detail)
SELECT shrapnel.stereotype_current_state(s.id) = 'active',
       'LcSolo is active again',
       'state=' || shrapnel.stereotype_current_state(s.id)
FROM shrapnel.stereotype s WHERE s.name = 'LcSolo';

INSERT INTO tap (ok, label, detail)
SELECT bool_and(h.to_state = expected.to_state) AND count(*) = 3,
       'the event log preserves the full active->deprecated->retired->active history',
       string_agg(h.from_state || '->' || h.to_state, ',' ORDER BY h.seq)
FROM (
  SELECT ev.from_state, ev.to_state,
         row_number() OVER (ORDER BY ev.event_id) AS seq
    FROM shrapnel.stereotype_lifecycle_event ev
    JOIN shrapnel.stereotype s ON s.id = ev.stereotype_id
   WHERE s.name = 'LcSolo'
) h
JOIN (VALUES (1,'deprecated'), (2,'retired'), (3,'active')) AS expected(seq, to_state)
  ON expected.seq = h.seq;

BEGIN;
SELECT revision_id, created FROM shrapnel.stereotype_reconcile(
  'LcSolo', NULL, NULL, ARRAY['lc_solo_f1']) \gset
COMMIT;
INSERT INTO tap (ok, label, detail) VALUES (
  NOT :'created'::boolean,
  'reconcile works again after reactivation',
  'created=' || :'created');


-- ============================================================================
-- CHECK 11 — append-only and semantic validation, directly at the table
-- ============================================================================
\echo '=== Check 11: the event log is append-only and self-consistent ==='
DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    UPDATE shrapnel.stereotype_lifecycle_event SET reason = 'tampered' WHERE event_id = 1;
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%append-only; UPDATE rejected%',
    'UPDATE on the lifecycle log is refused',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    DELETE FROM shrapnel.stereotype_lifecycle_event WHERE event_id = 1;
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%append-only; DELETE rejected%',
    'DELETE on the lifecycle log is refused',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    INSERT INTO shrapnel.stereotype_lifecycle_event
      (stereotype_id, from_state, to_state, reason, source, at_revision_id)
    SELECT s.id, 'active', 'deprecated', 'wrong from_state', 'emitter-a',
           r.head_revision_id
      FROM shrapnel.stereotype s,
           LATERAL shrapnel.stereotype_resolve(s.name) r
     WHERE s.name = 'LcOther';
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%from_state active does not match current state deprecated%',
    'an event whose from_state does not match the catalog is refused',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

-- Retirement requires a pinned head. Deprecate LcNegHead first, then attempt
-- the retirement with no at_revision_id: the CHECK constraint refuses it.
SELECT plan_token AS tok_neg_head
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcNegHead'], ARRAY[]::text[], ARRAY['LcNegHead'],
         '{"LcNegHead":{"reason":"stop authoring"}}'::jsonb)
 LIMIT 1 \gset
BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_neg_head', ARRAY['LcNegHead'],
  ARRAY[]::text[], ARRAY['LcNegHead'],
  '{"LcNegHead":{"reason":"stop authoring"}}'::jsonb);
COMMIT;

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    INSERT INTO shrapnel.stereotype_lifecycle_event
      (stereotype_id, from_state, to_state, reason, source, at_revision_id)
    SELECT s.id, 'deprecated', 'retired', 'no head pinned', 'emitter-a', NULL
      FROM shrapnel.stereotype s WHERE s.name = 'LcNegHead';
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%ck_sterev_lifecycle_retired_head%'
    OR v_err LIKE '%at_revision_id%',
    'retirement without a pinned head is refused',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;

-- Retirement requires a reason. Deprecate LcNegReason first, then attempt the
-- retirement with a blank reason.
SELECT plan_token AS tok_neg_reason
  FROM shrapnel.stereotype_lifecycle_plan(
         'emitter-a', ARRAY['LcNegReason'], ARRAY[]::text[], ARRAY['LcNegReason'],
         '{"LcNegReason":{"reason":"stop authoring"}}'::jsonb)
 LIMIT 1 \gset
BEGIN;
SELECT * FROM shrapnel.stereotype_lifecycle_apply(
  'emitter-a', :'tok_neg_reason', ARRAY['LcNegReason'],
  ARRAY[]::text[], ARRAY['LcNegReason'],
  '{"LcNegReason":{"reason":"stop authoring"}}'::jsonb);
COMMIT;

DO $d$
DECLARE v_err text := NULL;
BEGIN
  BEGIN
    INSERT INTO shrapnel.stereotype_lifecycle_event
      (stereotype_id, from_state, to_state, reason, source, at_revision_id)
    SELECT s.id, 'deprecated', 'retired', '   ', 'emitter-a', r.head_revision_id
      FROM shrapnel.stereotype s,
           LATERAL shrapnel.stereotype_resolve(s.name) r
     WHERE s.name = 'LcNegReason';
  EXCEPTION WHEN OTHERS THEN v_err := SQLERRM; END;
  INSERT INTO tap (ok, label, detail) VALUES (
    v_err LIKE '%non-empty reason%'
    OR v_err LIKE '%ck_sterev_lifecycle_reason%',
    'retirement without a reason is refused',
    coalesce(v_err, 'NO ERROR RAISED'));
END;
$d$;


-- ============================================================================
-- Report
-- ============================================================================
\echo ''
\echo '=== lifecycle_0010 results ==='
SELECT CASE WHEN ok THEN 'ok - ' ELSE 'NOT OK - ' END || label ||
       coalesce('   [' || detail || ']', '') AS result
FROM tap ORDER BY seq;

\echo ''
SELECT count(*) AS total,
       count(*) FILTER (WHERE ok) AS passed,
       count(*) FILTER (WHERE NOT ok) AS failed
FROM tap;
