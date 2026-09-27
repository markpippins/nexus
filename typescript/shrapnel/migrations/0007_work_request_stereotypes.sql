-- =============================================================================
-- shrapnel migration 0007: work_request stereotype family
-- =============================================================================
-- Purpose: define WorkRequest in Shrapnel as the BASE request stereotype, so
-- specialized request types (TesterAttestationRequest first, per architect
-- thread 8d431a5d "Design: Shrapnel-typed MongoDB tester attestation
-- dispatcher (no new table)") derive from it through the existing 0004/0005
-- revision machinery: stereotype_extends / stereotype_chain /
-- stereotype_effective_contract give polymorphic conformance and SolScript
-- can evaluate propositions against the EFFECTIVE contract of any variant
-- without per-variant wiring.
--
-- Status: STAGED-INERT DRAFT. This migration registers TYPE CONTRACTS only —
-- no instances are classified, no Mongo store is created, no dispatcher is
-- wired, no runtime behavior changes. Conformance of real instances remains a
-- separate decision (object_classify + stereotype_conformance OAV). It is
-- safe to apply and reversible by design; the ARCHITECT STILL OWNS the field
-- set ruling (base vs dispatch tier split), and this draft is written to
-- make that ruling executable either way.
--
-- Design (DBA proposal on thread 8d431a5d, open for ruling):
--   work_request (base)          — shared minimum for ALL request types
--   dispatchable_request (mid)   — extends work_request: claim/lease/retry
--                                  semantics shared by any dispatched variant
--   tester_attestation_request   — extends dispatchable_request: attestation-
--                                  specific fields from the architect's table
--
-- Field-set notes:
--   - The base is the shared MINIMUM, deliberately NOT a projection of
--     vision.work_requests (whose 22 columns are WR-package material:
--     decomposition, inquiry, evidence_obligations, execution_linkage —
--     instance stores should not inherit that ballast).
--   - The architect's proposed TesterAttestationRequest table splits cleanly:
--     identity/idempotency/state -> base; claim/lease/retry -> dispatch tier;
--     attestation-specific -> leaf.
--   - Shrapnel 0005 field API takes property names and creates fields with
--     the DEFAULT field_type (String, code 2). Typed refinements (UUID vs
--     String, Timestamp vs String, Long vs String) are a separate, later
--     revision once the field-type registry path is exercised; this draft
--     pins NAMES and required/optional, not storage types. That is exactly
--     what the Mongo instance store needs: presence/optionality contract.
--   - The 0005 superset doctrine: a child revision must redeclare every
--     required field it inherits. The rehearsal proves effective_contract
--     resolution end-to-end.
--
-- House style (0001-0006): idempotent, guarded, no destructive ops.
-- =============================================================================

BEGIN;

-- 1. work_request — the base request stereotype -------------------------------
SELECT shrapnel.stereotype_create_revision(
    'work_request',
    NULL,                                -- root: no parent
    NULL,                                -- no rationale required for a root
    ARRAY[
        'request_id',        -- immutable instance identity (Mongo _id / PK)
        'request_key',       -- idempotency key (work ref + head, dedup)
        'work_ref',          -- what the request is ABOUT (pr:, rec:, artifact:)
        'requested_by',      -- authoring role (governance/attribution)
        'state',             -- lifecycle state (variant-controlled vocabulary)
        'created_at',
        'updated_at'
    ],
    ARRAY[
        'evidence',          -- pre-staged evidence refs offered to the processor
        'lineage_ref',       -- parent request / triggering record, if any
        'shape_version'      -- instance schema_version mirror
    ]
) AS base_rev \gset

-- 2. dispatchable_request — extends work_request (C1: rationale required) -----
SELECT shrapnel.stereotype_create_revision(
    'dispatchable_request',
    :base_rev::bigint,                   -- parent: work_request head
    'Dispatched request variants share claim/lease/retry semantics (fencing token, crash-retry lease, bounded attempts, terminal reason). Extending rather than duplicating keeps every dispatched type conformance-checkable as a work_request per stereotype_chain; rationale recorded per 0005 C1 doctrine.',
    ARRAY[
        -- superset doctrine: redeclare inherited required fields
        'request_id', 'request_key', 'work_ref', 'requested_by', 'state',
        'created_at', 'updated_at',
        -- dispatch tier additions
        'claim_token',       -- fencing token for the current worker
        'lease_until',       -- crash/retry lease expiry
        'attempts',          -- bounded invocation count
        'terminal_reason'    -- rejection/failure explanation
    ],
    ARRAY[
        'evidence', 'lineage_ref', 'shape_version',
        'claimed_at'         -- optional: when the current claim was taken
    ]
) AS dispatch_rev \gset

-- 3. tester_attestation_request — extends dispatchable_request ----------------
SELECT shrapnel.stereotype_create_revision(
    'tester_attestation_request',
    :dispatch_rev::bigint,               -- parent: dispatchable_request head
    'Tester attestation dispatch (architect thread 8d431a5d, revised no-new-table design): attestation-specific fields over the shared dispatchable contract. Instances live in Mongo (tester_attestation_requests) typed by THIS Shrapnel head revision; V179 nebula.attestations remains the canonical attestation event chain.',
    ARRAY[
        -- superset doctrine: redeclare everything inherited-required
        'request_id', 'request_key', 'work_ref', 'requested_by', 'state',
        'created_at', 'updated_at',
        'claim_token', 'lease_until', 'attempts', 'terminal_reason',
        -- attestation-specific required
        'head_sha'           -- exact code head being verified (idempotency pair)
    ],
    ARRAY[
        'evidence', 'lineage_ref', 'shape_version', 'claimed_at',
        -- attestation-specific optional
        'attestation_id',    -- V179 attestation event identity on completion
        'ci_ref'             -- CI run reference, if verification rode CI
    ]
) AS tester_rev \gset

COMMIT;

-- Post-conditions (assertion block, safe to re-run):
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

    -- chain: leaf -> dispatch -> base = 3 hops
    SELECT count(*) INTO v_chain
    FROM shrapnel.stereotype_chain(v_leaf);
    IF v_chain <> 3 THEN
        RAISE EXCEPTION '0007 post-condition failed: expected 3-hop chain, got %', v_chain;
    END IF;

    -- effective contract: leaf must expose all 12 required fields, with origin
    -- attribution intact (base fields originating from work_request)
    SELECT count(*) INTO v_effective
    FROM shrapnel.stereotype_effective_contract(v_leaf)
    WHERE required;
    IF v_effective <> 12 THEN
        RAISE EXCEPTION '0007 post-condition failed: expected 12 required fields effective, got %', v_effective;
    END IF;

    -- polymorphism proof: the leaf IS a work_request (transitive extends)
    IF NOT shrapnel.stereotype_extends(v_leaf, 'work_request') THEN
        RAISE EXCEPTION '0007 post-condition failed: tester_attestation_request does not extend work_request';
    END IF;

    RAISE NOTICE '0007 verified: 3-revision chain, 12 required effective fields, extends(work_request) = true';
END $$;
