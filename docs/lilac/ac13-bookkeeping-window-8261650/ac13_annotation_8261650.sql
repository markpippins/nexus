-- ac13_annotation_8261650.sql — DRAFT, NOT EXECUTED.
-- Operator-approved AC13 annotation + pending-blueprint reconciliation for plan 8261650.
-- Target: nebula.blueprints_history (canonical row; nebula.implementation_plans and
-- nebula.plan_status are views over it).
-- Run:  PGPASSWORD=pgpass psql -h localhost -U pguser -d nexus -v ON_ERROR_STOP=1 -f this file
-- Expect exactly "UPDATE 1" for step 1 and "UPDATE 3" for step 2, then COMMIT.

\set ON_ERROR_STOP on
BEGIN;

-- 0) Lock the three rows and prove the expected pre-state.
SELECT plan_number, blueprint_status,
       left(payload->'acceptance_criteria'->>-1, 60) AS ac13_head,
       md5(payload::text) AS payload_md5
  FROM nebula.blueprints_history
 WHERE plan_number IN ('8261650','8261668','8261673')
 ORDER BY plan_number
   FOR UPDATE;

DO $guard$
DECLARE n_pending int; n_expected int; ac13 text;
BEGIN
  SELECT count(*), count(*) FILTER (WHERE blueprint_status = 'pending')
    INTO n_expected, n_pending
    FROM nebula.blueprints_history
   WHERE plan_number IN ('8261650','8261668','8261673');
  IF n_expected <> 3 OR n_pending <> 3 THEN
    RAISE EXCEPTION 'pre-state mismatch: % rows, % pending (want 3/3)', n_expected, n_pending;
  END IF;
  SELECT payload->'acceptance_criteria'->>-1 INTO ac13
    FROM nebula.blueprints_history WHERE plan_number = '8261650';
  IF ac13 IS NULL OR ac13 NOT LIKE 'Cutover marker:%' THEN
    RAISE EXCEPTION 'pre-state mismatch: AC13 entry is not the verbatim criterion (already annotated?)';
  END IF;
END
$guard$;

-- 1) Annotate AC13 on plan 8261650 (index 12 of 13). Guarded by the exact pre-image.
UPDATE nebula.blueprints_history
   SET payload = jsonb_set(payload, '{acceptance_criteria}', $after$["AC1 request_kind gate: MET — V219 applied live 2026-11-16, ledger 0dec13c8-bc01-47ec-b0f0-f02827578372, postchecks 47 rows / 0 null kind / 47 attested / 3 registry values. Stored plan text \"GATE UNMET\" is stale and superseded.", "V186 applies only under an explicit approval record citing the combined ruling (thread 402d8a0d); its gate refuses double-apply and the refusal path is proven non-mutating. [AC2 CLOSEOUT — V186 documentary ledger row status: INSERTED AND VERIFIED (was AUTHORIZED BUT UNEXECUTED). Row UUID: 923ade60-2770-4c48-be43-1d0543f75da8; label V186__work_request_primitive; historical applied_at 2026-09-20 03:22:49+00 (applied_by dba). Documentary-only ledger insert — NOT a schema migration apply; V186 was not applied again today (2026-11-15). Read-only verify: ledger count 34 (was 33); exactly 1 V186; 0 V187; 0 V215; plan 8261650 stays pending; no DDL or plan status writes. R9 vanadium replication: official backup 2026-11-15 09:41:01–09:45:50, remote checksum verification OK; --verify-last VERIFY OK at 09:47:37 on pg-backups/titanium/manifest__20261115_094101.txt (all listed archives OK). Original criterion preserved: insert only on explicit approval; idempotent refusal if already executed or re-apply attempted. Planner decision 41c45543-fea5-4e48-8ccd-b8d10fa924bd.]", "AC3 crosswalk: V186 two-column crosswalk verified vs DBA audit `69bc6cb7-b5c6-4f90-9874-d92dd250b10a` — 6/9 vision-prefixed rows verify, 1/1 nebula-prefixed row verifies, all 6 asset mirrors identical on `asset:nexus:vision_work_requests:<canonical id>`; 3 Nebula-history rows (`09f7f5c9`, `0ec94c16`, `e16cec45`) are mislabeled with the vision prefix and carried as a documented residual (no source freeze; zero drift unproven; no repair applied in this gate).", "All three write-sites (conduit db_adapter, cascade admission_subscriber, losm_store repository) write resolution.work_request with their legacy suites green (sealed-SQLite for losm per the PR #366 test pattern)", "V187 demotion: every legacy WR name answers as a pass-through view preserving column names; conduit db_adapter read smoke passes; execution.requests.source_wr_id FK remains resolvable through the crosswalk; the trigger CASCADE audit is documented before apply", "vision-srv retirement happens only after the PR #366 cutover window proves read parity, with start-nexus-services and both service registries updated in the same change", "Every stage boundary has a documented rollback rehearsal; re-running V186/V187 is idempotent", "To-do/backlog is an eligibility projection over open, unclaimed, role-eligible WorkRequests, kind-agnostic — not `category IS NULL`, not `DRAFT`, and not a nullness proxy.", "`request_kind` initial registry values are `build`, `attestation`, `decision`; extension is a registry row after the same relational-layout comparison, not a migration. `inquiry` remains an optional envelope payload block (decision 1ba40aa0), not a kind.", "`request_kind` and `lifecycle_status` remain independent axes; any cross-kind summary/superstate is derived deterministically from the single authoritative event fold and is never a second mutable status authority.", "Replace the single `source_requirement_id` shortcut with a normalized M2M `request_requirement_link(request_id, requirement_id, relation, provenance)`; no single-source shortcut.", "Replace the polymorphic `(kind, ref)` artefact edge with `artifact(id, kind, canonical_ref)` plus `request_artifact_link(request_id, artifact_id, role, ...)`; no polymorphic edge.", "AC13 cutover marker: MET — live Stage-6 / AC13 bookkeeping window, operator authorisation record 25ba5496-82d5-42d7-8118-52d66e39b858 (2026-11-17T05:25Z). Evidence: 133/133 open To Do threads carry exactly one AC13-DISPOSITION:8261650 marker (14 plan/WR link, 28 closed, 91 explicitly deferred; 0 missing, 0 duplicate; manifest ac13_manifest_8261650.md SHA-256 7faef18118327d651af1c94283165a60b4f4397a7a99301a0500b01d73ecbe42). Pending blueprints dispositioned: 8261668 -> plan 8261668 / PR #952 (merged); 8261673 -> plan 8261673 / PR #983 (merged) / receipt 52ec9b77-de19-43c1-af29-c0aa4a03ba8e; 8261650 -> self-reference resolved by the operator's AC13 ruling (this gate). Gates green: conduit 8261649/8261650/8261668/8261673 all REVIEW_PASS via append-only receipts b506690a-8b1b-4cdb-9bfc-c003b43df025 (IMPLEMENTATION) and 767141d2-789c-475a-abce-d2ed6e9a89bc (REVIEW_PASS), prior BLOCK preserved. W-A->W-B->W-C order respected; 0 WorkRequests carry plan_id, so no WorkRequest was emitted without blueprint decomposition and acceptance criteria. Re-verified read-only 2026-11-18: ac13_verify_8261650.py -> AC13 PASS 133/133, exit 0 (record d4a2168e-d9ec-4edc-bcee-a9ebec720b64). Original criterion preserved verbatim in this entry."]$after$::jsonb, false),
       updated_at = now()
 WHERE plan_number = '8261650'
   AND payload->'acceptance_criteria' = $before$["AC1 request_kind gate: MET — V219 applied live 2026-11-16, ledger 0dec13c8-bc01-47ec-b0f0-f02827578372, postchecks 47 rows / 0 null kind / 47 attested / 3 registry values. Stored plan text \"GATE UNMET\" is stale and superseded.", "V186 applies only under an explicit approval record citing the combined ruling (thread 402d8a0d); its gate refuses double-apply and the refusal path is proven non-mutating. [AC2 CLOSEOUT — V186 documentary ledger row status: INSERTED AND VERIFIED (was AUTHORIZED BUT UNEXECUTED). Row UUID: 923ade60-2770-4c48-be43-1d0543f75da8; label V186__work_request_primitive; historical applied_at 2026-09-20 03:22:49+00 (applied_by dba). Documentary-only ledger insert — NOT a schema migration apply; V186 was not applied again today (2026-11-15). Read-only verify: ledger count 34 (was 33); exactly 1 V186; 0 V187; 0 V215; plan 8261650 stays pending; no DDL or plan status writes. R9 vanadium replication: official backup 2026-11-15 09:41:01–09:45:50, remote checksum verification OK; --verify-last VERIFY OK at 09:47:37 on pg-backups/titanium/manifest__20261115_094101.txt (all listed archives OK). Original criterion preserved: insert only on explicit approval; idempotent refusal if already executed or re-apply attempted. Planner decision 41c45543-fea5-4e48-8ccd-b8d10fa924bd.]", "AC3 crosswalk: V186 two-column crosswalk verified vs DBA audit `69bc6cb7-b5c6-4f90-9874-d92dd250b10a` — 6/9 vision-prefixed rows verify, 1/1 nebula-prefixed row verifies, all 6 asset mirrors identical on `asset:nexus:vision_work_requests:<canonical id>`; 3 Nebula-history rows (`09f7f5c9`, `0ec94c16`, `e16cec45`) are mislabeled with the vision prefix and carried as a documented residual (no source freeze; zero drift unproven; no repair applied in this gate).", "All three write-sites (conduit db_adapter, cascade admission_subscriber, losm_store repository) write resolution.work_request with their legacy suites green (sealed-SQLite for losm per the PR #366 test pattern)", "V187 demotion: every legacy WR name answers as a pass-through view preserving column names; conduit db_adapter read smoke passes; execution.requests.source_wr_id FK remains resolvable through the crosswalk; the trigger CASCADE audit is documented before apply", "vision-srv retirement happens only after the PR #366 cutover window proves read parity, with start-nexus-services and both service registries updated in the same change", "Every stage boundary has a documented rollback rehearsal; re-running V186/V187 is idempotent", "To-do/backlog is an eligibility projection over open, unclaimed, role-eligible WorkRequests, kind-agnostic — not `category IS NULL`, not `DRAFT`, and not a nullness proxy.", "`request_kind` initial registry values are `build`, `attestation`, `decision`; extension is a registry row after the same relational-layout comparison, not a migration. `inquiry` remains an optional envelope payload block (decision 1ba40aa0), not a kind.", "`request_kind` and `lifecycle_status` remain independent axes; any cross-kind summary/superstate is derived deterministically from the single authoritative event fold and is never a second mutable status authority.", "Replace the single `source_requirement_id` shortcut with a normalized M2M `request_requirement_link(request_id, requirement_id, relation, provenance)`; no single-source shortcut.", "Replace the polymorphic `(kind, ref)` artefact edge with `artifact(id, kind, canonical_ref)` plus `request_artifact_link(request_id, artifact_id, role, ...)`; no polymorphic edge.", "Cutover marker: all 124 open To Do threads have a disposition (plan/WR link, requirement, closed, or explicitly deferred); all 19 pending blueprints are mapped to a plan/WR or explicitly dispositioned; 8261649 and 8261650 gates are green; W-A→W-B→W-C order is respected; and no WorkRequest is emitted without blueprint decomposition and acceptance criteria."]$before$::jsonb;
-- MUST report: UPDATE 1

-- 2) Reconcile the three pending blueprint statuses with the receipt-derived REVIEW_PASS.
UPDATE nebula.blueprints_history
   SET blueprint_status = 'completed',
       updated_at = now()
 WHERE plan_number IN ('8261650','8261668','8261673')
   AND blueprint_status = 'pending';
-- MUST report: UPDATE 3

-- 3) Postconditions inside the transaction. Any failure rolls the whole thing back.
DO $post$
DECLARE bad int;
BEGIN
  SELECT count(*) INTO bad FROM nebula.blueprints_history
   WHERE plan_number IN ('8261650','8261668','8261673')
     AND blueprint_status <> 'completed';
  IF bad <> 0 THEN RAISE EXCEPTION 'postcondition: % rows not completed', bad; END IF;
  SELECT count(*) INTO bad FROM nebula.blueprints_history
   WHERE plan_number = '8261650'
     AND payload->'acceptance_criteria'->>-1 NOT LIKE 'AC13 cutover marker: MET%';
  IF bad <> 0 THEN RAISE EXCEPTION 'postcondition: AC13 annotation missing'; END IF;
END
$post$;

COMMIT;

-- ── ROLLBACK (run only on operator instruction; append-only audit rows stay) ──
-- BEGIN;
-- UPDATE nebula.blueprints_history
--    SET payload = jsonb_set(payload, '{acceptance_criteria}', $before$["AC1 request_kind gate: MET — V219 applied live 2026-11-16, ledger 0dec13c8-bc01-47ec-b0f0-f02827578372, postchecks 47 rows / 0 null kind / 47 attested / 3 registry values. Stored plan text \"GATE UNMET\" is stale and superseded.", "V186 applies only under an explicit approval record citing the combined ruling (thread 402d8a0d); its gate refuses double-apply and the refusal path is proven non-mutating. [AC2 CLOSEOUT — V186 documentary ledger row status: INSERTED AND VERIFIED (was AUTHORIZED BUT UNEXECUTED). Row UUID: 923ade60-2770-4c48-be43-1d0543f75da8; label V186__work_request_primitive; historical applied_at 2026-09-20 03:22:49+00 (applied_by dba). Documentary-only ledger insert — NOT a schema migration apply; V186 was not applied again today (2026-11-15). Read-only verify: ledger count 34 (was 33); exactly 1 V186; 0 V187; 0 V215; plan 8261650 stays pending; no DDL or plan status writes. R9 vanadium replication: official backup 2026-11-15 09:41:01–09:45:50, remote checksum verification OK; --verify-last VERIFY OK at 09:47:37 on pg-backups/titanium/manifest__20261115_094101.txt (all listed archives OK). Original criterion preserved: insert only on explicit approval; idempotent refusal if already executed or re-apply attempted. Planner decision 41c45543-fea5-4e48-8ccd-b8d10fa924bd.]", "AC3 crosswalk: V186 two-column crosswalk verified vs DBA audit `69bc6cb7-b5c6-4f90-9874-d92dd250b10a` — 6/9 vision-prefixed rows verify, 1/1 nebula-prefixed row verifies, all 6 asset mirrors identical on `asset:nexus:vision_work_requests:<canonical id>`; 3 Nebula-history rows (`09f7f5c9`, `0ec94c16`, `e16cec45`) are mislabeled with the vision prefix and carried as a documented residual (no source freeze; zero drift unproven; no repair applied in this gate).", "All three write-sites (conduit db_adapter, cascade admission_subscriber, losm_store repository) write resolution.work_request with their legacy suites green (sealed-SQLite for losm per the PR #366 test pattern)", "V187 demotion: every legacy WR name answers as a pass-through view preserving column names; conduit db_adapter read smoke passes; execution.requests.source_wr_id FK remains resolvable through the crosswalk; the trigger CASCADE audit is documented before apply", "vision-srv retirement happens only after the PR #366 cutover window proves read parity, with start-nexus-services and both service registries updated in the same change", "Every stage boundary has a documented rollback rehearsal; re-running V186/V187 is idempotent", "To-do/backlog is an eligibility projection over open, unclaimed, role-eligible WorkRequests, kind-agnostic — not `category IS NULL`, not `DRAFT`, and not a nullness proxy.", "`request_kind` initial registry values are `build`, `attestation`, `decision`; extension is a registry row after the same relational-layout comparison, not a migration. `inquiry` remains an optional envelope payload block (decision 1ba40aa0), not a kind.", "`request_kind` and `lifecycle_status` remain independent axes; any cross-kind summary/superstate is derived deterministically from the single authoritative event fold and is never a second mutable status authority.", "Replace the single `source_requirement_id` shortcut with a normalized M2M `request_requirement_link(request_id, requirement_id, relation, provenance)`; no single-source shortcut.", "Replace the polymorphic `(kind, ref)` artefact edge with `artifact(id, kind, canonical_ref)` plus `request_artifact_link(request_id, artifact_id, role, ...)`; no polymorphic edge.", "Cutover marker: all 124 open To Do threads have a disposition (plan/WR link, requirement, closed, or explicitly deferred); all 19 pending blueprints are mapped to a plan/WR or explicitly dispositioned; 8261649 and 8261650 gates are green; W-A→W-B→W-C order is respected; and no WorkRequest is emitted without blueprint decomposition and acceptance criteria."]$before$::jsonb, false),
--        updated_at = now()
--  WHERE plan_number = '8261650';
-- UPDATE nebula.blueprints_history
--    SET blueprint_status = 'pending', updated_at = now()
--  WHERE plan_number IN ('8261650','8261668','8261673');
-- COMMIT;
