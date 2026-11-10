# Track B — Night-Shift readiness: bounded, non-authoritative rehearsal (plan 8261645)

**Track:** B — Night Shift
**Package:** B1 (rehearsal harness + bound) · B2 (receipt + rollback proof)
**Owner:** builder (B1) · reviewer (B2)
**State:** rehearsal **run 2026-11-10** against a throwaway PostgreSQL database
and an ephemeral loopback gateway (`bin/convergence_rehearsal.py`). Evidence slots
below are filled by that run. The result remains non-authoritative: no live receipt,
no merge, no attestation, no PEB or activation change.

> Continuity, not a wave. Night shift already has an approved doctrine
> (`docs/night-shift-doctrine.md`, 2026-09-05) and an executing pipeline. This
> document bounds a rehearsal of that flow; it does not invent a second flow.

---

## 1. The bound (what "bounded" means here)

Rehearsal is bounded by the doctrine's own POC v1 limits, restated so the
rehearsal cannot quietly expand:

| Bound | Value | Source |
|---|---|---|
| Work requests per cycle | ≤ 5 | `docs/night-shift-doctrine.md`, Planner step 1 |
| Batching | same-file items batch together | same |
| Grouping order | scope → severity/rule-class → risk | same (roundtable 2026-09-05) |
| Receipt target | dedicated test DB (`CONDUIT_PG_DSN` flip, `nexus_nightshift` or equivalent) | "Receipt isolation (option A)" |
| Prod contact | **forbidden** in scheduled runs | same |
| Build location | worktree `~/dev/nexus-worktrees/<topic>`, branch `<topic>`; never `main` | Builder step 2 |
| Merge | only on GitHub+Jenkins green; failing PR bounces to Planner | Reviewer step 4 |

A rehearsal that needs a 6th work request, a `main`-branch commit, or a live-DB
receipt is **out of bound**: stop and report, do not relax the bound.

## 2. End-to-end shape to rehearse (four consumers + state machine)

| Stage | Consumer | Input → output | Authoritative? |
|---|---|---|---|
| 1 Triage | Planner (sonar surface) | sonar items → ≤5 bounded batches | no (grouping only) |
| 2 Plan/ticket | Conduit pipeline | batch → plan + builder ticket (isolated DB) | **rehearsal copy only** |
| 3 Implement | Builder | ticket → worktree branch + open PR (draft if tests red) | branch is real; merge is not |
| 4 Gate | Critic | PR-as-change → `type:gate-pass` / `type:gate-bounce` record | gate, not merge authority |
| 5 Judge | Reviewer | green checks → merge judgement | skipped in rehearsal (see §3) |

**State machine to exercise:** Conduit plan receipts, in order
`PLAN_CREATE → IMPLEMENTATION → REVIEW_PASS` (happy path), plus one negative
path (`BLOCK` or `REVIEW_REJECT`). Ticket lifecycle to exercise:
`open → claimed → completed` and one `claimed → stale → open` takeover.

Vocabulary ids for these states/transitions:
`schemas/convergence/interaction-vocabulary.v1.json`
(`state.ticket.*`, `transition.plan.receipt`, `rule.adjacentcy` family).

## 3. Explicit non-goals

- **No merge.** The rehearsal stops at the Critic verdict. Merging is a
  production act and has no rehearsal form.
- **No live receipt.** The isolated `CONDUIT_PG_DSN` is a precondition; if the
  flip cannot be demonstrated, the rehearsal does not start.
- **No attestation.** A tester/attestation record is not produced by a rehearsal.
- **No Sonar writes.** `sonar_mark_fp` / `sonar_add_comment` / `sonar_mark_complete`
  are out of bound for a rehearsal (they mutate a shared service).

## 4. Evidence slots

| Slot | Required content | State |
|---|---|---|
| `consumer` | transcript of Planner→Conduit→Builder→Critic for one batched item, with the isolated DSN visible | filled: `docs/convergence/evidence/b1-rehearsal-transcript.md` |
| `state_machine` | receipt chain excerpt (`PLAN_CREATE`, `IMPLEMENTATION`, `REVIEW_PASS`) plus one negative-path receipt, all from the isolated store | filled: `docs/convergence/evidence/b2-receipt-chain-snapshot.txt` |
| `receipt` | proof the receipts landed in the test DB and **not** the live conduit DB (e.g. row counts in both, with timestamps) | filled: `docs/convergence/evidence/b2-receipt-chain-snapshot.txt` (`rehearsal_store_receipts=15`, `live_conduit_receipts_for_rehearsal_ids=0`) |
| `rollback` | the observed rollback action and its result: either `unblock_plan`/ticket release on the rehearsal plan, or branch revert + worktree removal | filled: `docs/convergence/evidence/b2-receipt-chain-snapshot.txt` (ticket `claimed->stale->open`, receipt `BLOCK->REQUEUED`, transaction `ROLLBACK`, `rehearsal_tables_remaining=0`, throwaway DB removed) |
| `artifact` | this specification | filled: `python/conduit/convergence/track-b-night-shift-rehearsal.md` |
| `seed_gate` | package D3's six seed-gate checks, run read-only | filled: `docs/convergence/evidence/d3-seed-gate-output.txt` (`6/6 checks pass`) |
| `verification_owner` | reviewer | — |

## 5. Stop conditions

1. **Pre-flight stop:** `CONDUIT_PG_DSN` isolation not demonstrated → do not run.
2. **Bound stop:** the cycle needs >5 work requests or a non-same-file merge of
   batches → stop, report to planner as a re-scope, never widen the bound.
3. **Evidence stop:** any of the four slots above cannot be filled with an
   observed result → the rehearsal is reported incomplete. It is never
   back-filled with intent.
4. **Rollback stop:** if rollback cannot be demonstrated, the readiness claim is
   **not** made, because an unrehearsed rollback is not a rollback.

## 6. Why this is non-authoritative

The rehearsal writes only to its isolated store and a worktree branch. Nothing it
produces changes plan state in the live pipeline, creates a production receipt,
or authorises a merge. The readiness claim this track can support is therefore
narrow and honest: *"the flow runs end to end inside its bounds, and rolls back"* —
not *"night shift is approved to run"*.

Related: `schemas/convergence/README.md` §2, `schemas/convergence/work-packages.v1.json` (B1, B2),
`docs/night-shift-doctrine.md`.
