# AC13 bookkeeping window — plan 8261650 (entered, verified, not outstanding)

**Status: COMPLETE — recorded 2026-11-17, re-verified 2026-11-18. Nothing in this
document is outstanding work.** It exists so a later pass does not read the AC13
clause of plan 8261650 as unmet and re-record dispositions (a second
`AC13-DISPOSITION:8261650` marker would break the verifier's
exactly-one-marker-per-thread invariant) or issue a third close-out receipt
(forbidden by the window).

## Authorization

| Field | Value |
|---|---|
| Window | live Stage-6 / AC13 bookkeeping window for plan 8261650 |
| Operator approval | 2026-11-17, quoted in R1 intent record `25ba5496-82d5-42d7-8118-52d66e39b858` |
| Reviewer gate review | `1fa6682d-a3a8-4993-8102-a1b307ca1b12` |
| R2 close-out | `4c926408-914d-4874-ae3a-c856d1d46ce1` |
| Scope | (a) the 133-thread AC13 disposition bookkeeping; (b) exactly two append-only conduit receipts — `IMPLEMENTATION` then `REVIEW_PASS`, prior `BLOCK` preserved; (c) re-running the read-only AC13 verifier and the V219/V220/Stage-6/V221 harnesses |
| Forbidden | criterion amendment, destructive operations, requirement/artifact-link/`plan_id` writes, replication, any additional receipt, any change to the `wr-8261673` disposition-ledger drift |
| Expiry / revocation | none recorded; the window is scoped by content, not by time |

## Evidence (live store, re-read 2026-11-18)

| Artefact | Key | Store | Timestamp |
|---|---|---|---|
| AC13 dispositions | 133 comments, 133 distinct To Do posts, `AC13-DISPOSITION:8261650:<post-id>` | `assembly.comments` | 2026-11-17T04:28:09.640Z – 04:29:07.163Z |
| Disposition mix | 14 plan/WR link, 28 closed, 91 explicitly deferred | verifier | — |
| AC13 close-out comments | `2cbc80e1-b07a-41f0-a832-95793b6671d4` (post `809c5d4a…`), `962a3089-3ae8-4e0b-a88a-4dea13c754e1` (post `c7c26160…`) | `assembly.comments` | 2026-11-17T04:32:16.568Z / .577Z |
| `IMPLEMENTATION` receipt | ledger `b506690a-8b1b-4cdb-9bfc-c003b43df025`, source `7c6a339d-79b6-45f9-8347-2198637de4f9` | `resolution.receipt` | 2026-11-17T05:25:34.516Z |
| `REVIEW_PASS` receipt | ledger `767141d2-789c-475a-abce-d2ed6e9a89bc`, source `6132a1f2-b638-4cfc-a1a4-b3b9d4118629` | `resolution.receipt` | 2026-11-17T05:26:55.955Z |
| Plan status | `nebula.plan_status.derived_status = REVIEW_PASS`; conduit `/state` bucket `completed` | `nebula` / conduit | live |
| Disposition evidence record | `b465794a-3db9-4708-999e-3dffc07c955d` | `nebula.agent_records` | 2026-11-17T04:27Z |
| Manifest correction / execution close-out | `7fa47dcd-9b67-418b-b979-9468c9628e1e` | `nebula.agent_records` | 2026-11-17T04:31Z |
| Read-back verification | `d4a2168e-d9ec-4edc-bcee-a9ebec720b64` | `nebula.agent_records` | 2026-11-18T00:23Z |

Blueprint dispositions recorded with the window: `8261668` → plan 8261668 / PR #952
(merged); `8261673` → plan 8261673 / PR #983 (merged) / receipt
`52ec9b77-de19-43c1-af29-c0aa4a03ba8e`; `8261650` → self-reference resolved by the
operator's AC13 ruling (the gate this document records).

Source artefacts live outside this repository and are pinned by digest:

| Artefact | Path | SHA-256 |
|---|---|---|
| Disposition manifest (133 rows) | `/home/codex/dev/ac13_manifest_8261650.md` | `7faef18118327d651af1c94283165a60b4f4397a7a99301a0500b01d73ecbe42` |
| Read-only verifier | `/home/codex/dev/ac13_verify_8261650.py` | see `sha256sum` at run time |
| Execution results (133) | `/home/codex/dev/ac13_execution_8261650.json` | see `sha256sum` at run time |

## How to confirm it is not outstanding

```bash
curl -s http://localhost:3100/state | jq '.plans.completed[] | select(.planNumber=="8261650") | .derivedStatus'
# REVIEW_PASS

python3 /home/codex/dev/ac13_verify_8261650.py
# AC13 PASS — 133/133 markers   (exit 0)

PGPASSWORD=pgpass psql -h localhost -U pguser -d nexus -Atc \
  "select count(*) from assembly.comments where text ~ 'AC13-DISPOSITION:8261650:[0-9a-f]{8}-';"
# 133
```

A `FAIL` from the verifier on `protected resolution counts` is the known
`wr-8261673` 47-vs-46 drift or the two #8261674 rows; it is not missing AC13
bookkeeping. The verifier's protected set is identity-pinned (47 baseline rows +
2 pinned out-of-window rows, `plan_id IS NULL`).

## Known residuals (deliberately not written inside this window)

1. **AC13 criterion text is not annotated on the plan.** `nebula.blueprints_history`
   (`nebula.implementation_plans` / `nebula.plan_status` are views over it) still
   stores the AC13 clause verbatim; AC1/AC2 carry close-out annotations. Criterion
   amendment is forbidden by the window.
2. **Blueprint statuses for 8261650 / 8261668 / 8261673 are `pending`** while
   `derived_status` is `REVIEW_PASS` from the receipt ledger. A plan-status write is
   outside the window.
3. **No `receipt.issued` kernel rows exist for the two AC13 receipts.** The live
   conduit kernel started 2026-11-17T13:59:15-05:00, before #1019 (`91aea5f0d`,
   merged 2026-11-18T00:12:39Z) added the emission. Re-issuing receipts is forbidden
   and restarting the kernel is out of scope.
4. **`bin/verify-workrequest-disposition.py` asserts that plan 8261650 projects
   `pending`** (row `6217db7f`). Any reconciliation of residual 2 must update that
   ratified ledger/verifier expectation in the same change or it will add a new
   failure. It is already red for the 49-vs-46 row drift.

## Bounded follow-on writes (NOT performed; need their own operator approval)

| Write | What | Gate |
|---|---|---|
| AC13 annotation | replace index 12 of the plan's 13 `acceptance_criteria` entries with the close-out evidence string, keeping the criterion verbatim inside it | operator approval + pre-image + rollback; `ac13_verify_8261650.py` must stay PASS |
| Blueprint reconciliation | set `blueprint_status = 'completed'` for 8261650 / 8261668 / 8261673 to match receipt-derived `REVIEW_PASS` | same approval; also update `verify-workrequest-disposition.py` expectation first (residual 4) |
| Live `receipt.issued` proof | emit/observe a `receipt.issued` kernel row from a new receipt | requires a kernel restart or a fresh receipt — neither is authorized |

The drafted SQL for the first two (guarded by exact pre-image, with rollback) was
prepared in the 2026-11-18 pass and is not applied.
