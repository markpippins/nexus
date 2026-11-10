# B1 rehearsal transcript — plan 8261645 (track B, Night Shift)

**Non-authoritative.** This is a bounded rehearsal, not a production run:
no merge, no attestation, no live receipt, no PEB or activation change, no sonar write.

## Provenance

- produced by: `python3 bin/convergence_rehearsal.py`
- run at: `2026-11-10T23:03:56+00:00`
- git: branch `main`, head `1a3d9b583`
- isolated gateway: `http://127.0.0.1:44477` (ephemeral loopback port; live :3100/:3103 untouched)
- isolated store: throwaway database `nexus_ns_rehearsal_3423998_20261110T230355Z` (created for the run, dropped after)
- transition law: read at run time from `typescript/conduit-mcp/src/receipts.ts` ALLOWED map
- live conduit receipt rows for these rehearsal ids: 0 (read-only check)

## Bound

- work requests in the cycle: 3 (bound: <= 5)
- batching: `rehearsal-R1` and `rehearsal-R2` batched as one same-file item

## Consumers exercised

planner (triage/batch) -> conduit gateway (plan + receipt) -> builder (claim, implement, complete)
-> critic (artifact CRITIQUE / gate-pass, and one bounce) -> reviewer (REVIEW / REVIEW_PASS, judgement only)

## Observed events

| at (UTC) | stage | role | action | target | status |
|---|---|---|---|---|---|
| 2026-11-10T23:03:56.075+00:00 | plan | planner | create_plan | rehearsal-R1 (PLAN_CREATE) | 201 |
| 2026-11-10T23:03:56.076+00:00 | plan | planner | create_plan | rehearsal-R2 (PLAN_CREATE) | 201 |
| 2026-11-10T23:03:56.077+00:00 | plan | planner | create_plan | rehearsal-R3 (PLAN_CREATE) | 201 |
| 2026-11-10T23:03:56.077+00:00 | ticket | builder | claim | ticket-rehearsal-R1-builder (open->claimed) | 200 |
| 2026-11-10T23:03:56.078+00:00 | receipt | builder | IMPLEMENTATION | rehearsal-R1 (IMPLEMENTATION) | 200 |
| 2026-11-10T23:03:56.079+00:00 | ticket | builder | completed | ticket-rehearsal-R1-builder (claimed->completed) | 200 |
| 2026-11-10T23:03:56.079+00:00 | ticket | builder | claim | ticket-rehearsal-R2-builder (open->claimed) | 200 |
| 2026-11-10T23:03:56.080+00:00 | receipt | builder | IMPLEMENTATION | rehearsal-R2 (IMPLEMENTATION) | 200 |
| 2026-11-10T23:03:56.080+00:00 | ticket | builder | completed | ticket-rehearsal-R2-builder (claimed->completed) | 200 |
| 2026-11-10T23:03:56.081+00:00 | receipt | critic | CRITIQUE | rehearsal-R1 (CRITIQUE) | 200 |
| 2026-11-10T23:03:56.081+00:00 | receipt | critic | CRITIQUE_PASS | rehearsal-R1 (CRITIQUE_PASS) | 200 |
| 2026-11-10T23:03:56.082+00:00 | receipt | reviewer | REVIEW | rehearsal-R1 (REVIEW) | 200 |
| 2026-11-10T23:03:56.082+00:00 | receipt | reviewer | REVIEW_PASS | rehearsal-R1 (REVIEW_PASS) | 200 |
| 2026-11-10T23:03:56.083+00:00 | receipt | critic | CRITIQUE | rehearsal-R2 (CRITIQUE) | 200 |
| 2026-11-10T23:03:56.083+00:00 | receipt | critic | CRITIQUE_PASS | rehearsal-R2 (CRITIQUE_PASS) | 200 |
| 2026-11-10T23:03:56.084+00:00 | receipt | reviewer | REVIEW | rehearsal-R2 (REVIEW) | 200 |
| 2026-11-10T23:03:56.084+00:00 | receipt | reviewer | REVIEW_PASS | rehearsal-R2 (REVIEW_PASS) | 200 |
| 2026-11-10T23:03:56.085+00:00 | ticket | builder | claim | ticket-rehearsal-R3-builder (open->claimed) | 200 |
| 2026-11-10T23:03:56.085+00:00 | ticket | builder | stale | ticket-rehearsal-R3-builder (claimed->stale) | 200 |
| 2026-11-10T23:03:56.086+00:00 | ticket | planner | open | ticket-rehearsal-R3-builder (stale->open) | 200 |
| 2026-11-10T23:03:56.086+00:00 | receipt | critic | BLOCK | rehearsal-R3 (BLOCK) | 200 |
| 2026-11-10T23:03:56.087+00:00 | receipt | planner | REQUEUED | rehearsal-R3 (REQUEUED) | 200 |
