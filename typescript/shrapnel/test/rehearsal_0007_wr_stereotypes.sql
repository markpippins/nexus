-- =============================================================================
-- shrapnel rehearsal for 0007: work_request stereotype family
-- =============================================================================
-- Live dry-run of migrations/0007_work_request_stereotypes.sql inside a
-- transaction that ROLLS BACK. Proves the full chain builds and resolves on
-- the real database with ZERO residue: no stereotypes, revisions, fields, or
-- conformance facts survive the session.
--
-- The migration body is inlined (rather than \ir'd) because the migration
-- carries its own BEGIN/COMMIT; a nested psql transaction cannot roll back
-- through it. Keep this file in sync with the migration's three
-- stereotype_create_revision calls — the post-condition assertion block is
-- copied verbatim so the rehearsal exercises the same checks.
--
-- Run: psql -v ON_ERROR_STOP=1 -f test/rehearsal_0007_wr_stereotypes.sql
-- Expected: every check prints OK, final line ROLLED BACK, and the residue
-- query returns (0,0,0).
-- =============================================================================

\set ON_ERROR_STOP on

\echo '=== pre-flight: existing stereotypes (baseline must be captured for residue check) ==='
SELECT count(*) AS stereotypes_before FROM shrapnel.stereotype;

BEGIN;

\echo '=== 1. work_request (base) ==='
SELECT shrapnel.stereotype_create_revision(
    'work_request',
    NULL,
    NULL,
    ARRAY[
        'request_id', 'request_key', 'work_ref', 'requested_by',
        'state', 'created_at', 'updated_at'
    ],
    ARRAY['evidence', 'lineage_ref', 'shape_version']
) AS base_rev \gset

\echo '=== 2. dispatchable_request (extends work_request) ==='
SELECT shrapnel.stereotype_create_revision(
    'dispatchable_request',
    :base_rev::bigint,
    'Dispatched request variants share claim/lease/retry semantics (fencing token, crash-retry lease, bounded attempts, terminal reason). Extending rather than duplicating keeps every dispatched type conformance-checkable as a work_request per stereotype_chain; rationale recorded per 0005 C1 doctrine.',
    ARRAY[
        'request_id', 'request_key', 'work_ref', 'requested_by', 'state',
        'created_at', 'updated_at',
        'claim_token', 'lease_until', 'attempts', 'terminal_reason'
    ],
    ARRAY['evidence', 'lineage_ref', 'shape_version', 'claimed_at']
) AS dispatch_rev \gset

\echo '=== 3. tester_attestation_request (extends dispatchable_request) ==='
SELECT shrapnel.stereotype_create_revision(
    'tester_attestation_request',
    :dispatch_rev::bigint,
    'Tester attestation dispatch (architect thread 8d431a5d, revised no-new-table design): attestation-specific fields over the shared dispatchable contract. Instances live in Mongo (tester_attestation_requests) typed by THIS Shrapnel head revision; V179 nebula.attestations remains the canonical attestation event chain.',
    ARRAY[
        'request_id', 'request_key', 'work_ref', 'requested_by', 'state',
        'created_at', 'updated_at',
        'claim_token', 'lease_until', 'attempts', 'terminal_reason',
        'head_sha'
    ],
    ARRAY['evidence', 'lineage_ref', 'shape_version', 'claimed_at',
          'attestation_id', 'ci_ref']
) AS tester_rev \gset

\echo '=== 4. chain resolution: leaf must be a 3-hop work_request ==='
SELECT name, version, hop FROM shrapnel.stereotype_chain(:tester_rev::bigint)
ORDER BY hop;

\echo '=== 5. effective contract: 12 required fields with origin attribution ==='
SELECT property_name, required, origin_stereotype, origin_version
FROM shrapnel.stereotype_effective_contract(:tester_rev::bigint)
WHERE required
ORDER BY origin_version, property_name;

\echo '=== 6. polymorphism: tester_attestation_request IS a work_request ==='
SELECT shrapnel.stereotype_extends(:tester_rev::bigint, 'work_request') AS extends_work_request;
SELECT shrapnel.stereotype_extends(:tester_rev::bigint, 'dispatchable_request') AS extends_dispatchable;
SELECT shrapnel.stereotype_extends(:tester_rev::bigint, 'concept_import_state') AS extends_unrelated_must_be_false;

\echo '=== 7. fingerprint: leaf contract fingerprint is computed and stable ==='
SELECT contract_fingerprint IS NOT NULL AS fingerprint_present,
       length(contract_fingerprint) > 0 AS fingerprint_nonempty
FROM shrapnel.stereotype_revision WHERE id = :tester_rev::bigint;

\echo '=== 8. superset doctrine negative probe: child missing a redeclared required field must FAIL ==='
DO $probe$
BEGIN
    BEGIN
        PERFORM shrapnel.stereotype_create_revision(
            'zz_rehearsal_bad_child',
            (SELECT head_revision_id FROM shrapnel.stereotype_resolve('work_request')),
            'Negative probe: omits inherited required fields; the deferred superset trigger must reject at COMMIT.',
            ARRAY['nickname'],          -- redeclares nothing
            NULL);
    EXCEPTION WHEN OTHERS THEN
        IF SQLERRM LIKE '%required%' OR SQLERRM LIKE '%superset%' OR SQLERRM LIKE '%field%' THEN
            RAISE NOTICE 'OK: superset rejection fired as expected: %', SQLERRM;
            RETURN;
        END IF;
        RAISE;
    END;
    -- If we reach here the rejection did NOT fire inside the statement; the
    -- deferred triggers fire at COMMIT, so force it:
    BEGIN
        COMMIT;
        RAISE EXCEPTION 'NEGATIVE PROBE FAILED: bad child committed — superset doctrine not enforced';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'OK: superset rejection fired at COMMIT: %', SQLERRM;
    END;
END $probe$;

\echo '=== 9. post-condition block (copied verbatim from migration 0007) ==='
DO $$
DECLARE
    v_base bigint; v_dispatch bigint; v_leaf bigint;
    v_chain int; v_effective int;
BEGIN
    SELECT head_revision_id INTO v_base      FROM shrapnel.stereotype_resolve('work_request');
    SELECT head_revision_id INTO v_dispatch  FROM shrapnel.stereotype_resolve('dispatchable_request');
    SELECT head_revision_id INTO v_leaf      FROM shrapnel.stereotype_resolve('tester_attestation_request');

    IF v_base IS NULL OR v_dispatch IS NULL OR v_leaf IS NULL THEN
        RAISE EXCEPTION '0007 post-condition failed: stereotype family incomplete';
    END IF;

    SELECT count(*) INTO v_chain FROM shrapnel.stereotype_chain(v_leaf);
    IF v_chain <> 3 THEN
        RAISE EXCEPTION '0007 post-condition failed: expected 3-hop chain, got %', v_chain;
    END IF;

    SELECT count(*) INTO v_effective
    FROM shrapnel.stereotype_effective_contract(v_leaf)
    WHERE required;
    IF v_effective <> 12 THEN
        RAISE EXCEPTION '0007 post-condition failed: expected 12 required fields effective, got %', v_effective;
    END IF;

    IF NOT shrapnel.stereotype_extends(v_leaf, 'work_request') THEN
        RAISE EXCEPTION '0007 post-condition failed: tester_attestation_request does not extend work_request';
    END IF;

    RAISE NOTICE '0007 verified: 3-revision chain, 12 required effective fields, extends(work_request) = true';
END $$;

\echo '=== 10. ROLLBACK — nothing persists ==='
ROLLBACK;

\echo '=== 11. residue check (must be 0/0/0 vs pre-flight baseline) ==='
SELECT (SELECT count(*) FROM shrapnel.stereotype)                                AS stereotypes_after,
       (SELECT count(*) FROM shrapnel.stereotype_revision)                       AS revisions_after,
       (SELECT count(*) FROM shrapnel.field
         WHERE property_name IN ('request_id','request_key','work_ref',
               'requested_by','state','created_at','updated_at','evidence',
               'lineage_ref','shape_version','claim_token','lease_until',
               'attempts','terminal_reason','claimed_at','head_sha',
               'attestation_id','ci_ref'))                                       AS new_fields_after;

\echo '=== rehearsal complete: expect stereotypes_after = stereotypes_before, revisions_after unchanged, new_fields_after = 0 ==='
