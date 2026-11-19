# W-B2 → W-B3 → W-B6 execution sequence (draft)

**Status:** IN PROGRESS (2026-11-17). P-WB2 exists as plan **8261674** with builder ticket
`ticket-8261674-builder-5be95a2a-46af-4042-9d7c-1966e6100a72` (closed `completed`) and
`plan_create` receipt `afdfe251-96a2-4178-82db-58043ddd6cf3`. **WR-WB2 is dispatched** as
`wr-8261674-1794937465-wb2ticketclass`, state `VALIDATED` — after the builder lane had already
run without it (§9.3). No W-B3 or W-B6 plan exists.
**Drafted:** 2026-11-17 · **Wave:** W-B · **Blueprint:** plan 8261654 (REVIEW_PASS)
**Rulings:** D1 (§7), D2 (§8) and `R-F1`–`R-F13` (§9) — operator-accepted 2026-11-17, binding.
**Docket:** `c7c26160-5b22-463f-9ee0-6e4784bae0e8` (planner, rating 4)
**Contract source:** `docs/promotion-flow/w-b-conduit-nebula-visibility-unification.md`
§3 (packages), §4 (evidence slots), §5 (W-B6 CD-1/CD-2/CD-3) and its machine twin
`bin/plan-gating-specs/w-decomposition.json`.

This document is the *sequence plan*. It names the exact plans and WorkRequests to be
created, their owners and dependencies, and the evidence each must produce. It also
records everything that must be resolved **before** creation, because several of the
blueprint's evidence slots cannot be filled against live state as it stands today.

---

## 0. Preconditions, verified read-only on 2026-11-17

| Check | Result |
|---|---|
| W-B6 gate plan `8261650` | `REVIEW_PASS` (conduit `/state` + `nebula.plan_status`) |
| W-B6 gate plan `8261651` | `REVIEW_PASS` |
| Blueprint `8261654` | `REVIEW_PASS`; Nebula `implementation_plans.status = completed` |
| Plan for W-B2 / W-B3 / W-B6 | **none exists** — no row in `nebula.implementation_plans` |
| WorkRequest for W-B2 / W-B3 / W-B6 | **none exists** — `resolution.work_request` has 47 rows, 0 with `plan_id` |
| `resolution.ticket` | 0 rows (the table that carries `generation` / `position`) — **not populated; ruling D1 §7** |
| `vision.tickets` census | cancelled 434 · completed 176 · expired 138 · failed 91 · closed 4 (843 rows) |
| `open`/`claimed`/`stale` tickets | **0 rows** (nothing live past `expires_at`) |
| `expired` tickets vs `expiry_detection` events | 138 vs 91 |
| `review_reject` receipts with a structured `scope` | 0 of 344 (all `(unset)`) |
| Terminal tickets with no `kernel.transition_event` | 577 total · **4** closed at/after the 2026-09-22 cutoff |
| `kernel.transition_event` payload for expiry | `{"reason","to_status","from_status"}` only; `event_type='transition.rejected'`, joined by `aggregate_id = vision.tickets.id` |
| `(plan_id, role, created_at)` ties in `vision.tickets` | **0** across 843 rows / 406 plans; `plan_id` null: 0 |
| Expired rows with an `expiry_detection` event | **49 of 138** (91 events over 89 distinct ticket ids; 40 of those ids are no longer `expired`) |
| `resolution.ticket` status CHECK | `open`/`claimed`/`stale`/`closed` only — cannot hold `expired`/`completed`/`cancelled`/`failed` |
| `resolution.ticket_transition` | 0 rows |

**The blueprint's §1 numbers are stale** (171 expired / 84 events / 338 rejects, snapshot
2026-10-24). Every evidence artifact in this sequence must cite a fresh read stamped with
its own `run_at`; do not carry the blueprint's counts forward as observed values.

---

## 1. Sequence at a glance

Three packages, three plans, three WorkRequests, five evidence slots.

| # | Artefact | Owner | Depends on | Produces | Gate to next step |
|---|---|---|---|---|---|
| 1 | Plan **P-WB2** — *W-B2: ticket-state semantics (`expired` ≠ `completed`)* | `engineer` | — | — | plan lands `pending` + `PLAN_CREATE` receipt |
| 2 | WR **WR-WB2** | `engineer` | P-WB2 | `WB2-TICKET-CLASS` + expiry-event enrichment | `WB2-TICKET-CLASS` filled, reviewer-verified, `REVIEW_PASS` |
| 3 | Plan **P-WB3** — *W-B3: generation-aware ticket hygiene + orphan sweep* | `reviewer` | **P-WB2 (plan number)** | — | plan lands `pending` |
| 4 | WR **WR-WB3** | `reviewer` | P-WB3 | `WB3-SWEEP-LOG` | `WB3-SWEEP-LOG` filled, planner-verified, `REVIEW_PASS` |
| 5 | Plan **P-WB6** — *W-B6: single generation-aware projection + expiry audit + reject routing* | `engineer` | **P-WB2, P-WB3, 8261650, 8261651** | — | plan lands `pending` |
| 6 | WR **WR-WB6** | `engineer` | P-WB6 | `WB6-GENERATION`, `WB6-EXPIRY`, `WB6-REJECT` | three slots filled + fixtures pass against the implementation |

Steps 1→2 and 3→4 are strict pairs: the plan and its WorkRequest are one lane, and the
sequence does not advance to the next package until that lane reaches `REVIEW_PASS`.
Step 5 may be **created** once P-WB2 and P-WB3 exist, but WR-WB6 must not be dispatched
until both predecessor lanes are terminal — the gate is the two `REVIEW_PASS` receipts,
not the plans' existence.

`8261654` is **lineage, not a dependency**: it is already `REVIEW_PASS`, and listing it
would add a permanently-satisfied edge that muddies the W-B6 gate reading.

`WB6-GENERATION` no longer waits on a ticket-store migration: its generation position is
derived from `vision.tickets` by the query in §7.2 (ruling D1), and the same derivation
feeds W-B3's sweep log. `WB3-SWEEP-LOG` is restated over the full 843-row surface by ruling
D2 (§8), because the slot query as written selects zero rows.

---

## 2. Step 1 — Plan P-WB2

Created with `nebula_create_plan` (conduit-mcp `create_plan` is a removed stub). Plan
number is auto-assigned and is the value later used for W-B3's dependency edge.

- **title:** `W-B2 — Ticket-state semantics: terminal outcome vs attention lost`
- **project:** `nexus`
- **goal:** Classify every ticket row into `terminal_outcome` (`completed` / `failed` /
  `cancelled` / `closed`) or `attention_lost` (`expired` / `stale`); prove no terminal
  plan presents `expired` as its outcome; and enrich the `expiry_detection` transition
  event with the per-generation fields W-B6's audit needs.
- **dependencies:** `[]`
- **filesAffected:**
  - `typescript/conduit-mcp/src/watcher.ts` (`detectExpiredTickets` payload enrichment)
  - `typescript/conduit-mcp/src/ticket-sweep.ts` (only if the sweep result shape changes)
  - `typescript/conduit-mcp/src/ticket-sweep.test.ts` (hermetic assertion of the payload)
  - `python/conduit/db_adapter.py` (only if it emits `expiry_detection` — it is in the
    event-emitting set and must not diverge)
  - `docs/promotion-flow/evidence/wb2-ticket-class-<plan-number>.json` (census artifact)
- **acceptance criteria:**
  1. **Classification census.** One artifact lists every `(plan_id, role, status)` bucket
     with `count`, a `class` of `terminal_outcome` / `attention_lost`, and the emitted
     `kernel.transition_event` count per row, from
     `select plan_id, role, status, count(*) from vision.tickets group by 1,2,3`.
     No status may be left unclassified — including `closed`, which the blueprint's §2
     vocabulary does not currently cover.
  2. **No completion implication.** For every plan whose derived status is
     `REVIEW_PASS`/`completed`, no ticket row carries `status='expired'` as its outcome —
     or each exception is enumerated with a waiver.
  3. **Event enrichment.** `expiry_detection` events carry `plan_id`, `role`,
     `generation`, `expires_at`, `from_status`, `to_status='expired'`,
     `reason='expiry_detection'`, `implies_completion=false`. Today the payload is
     `{reason, to_status, from_status}` — verified against live rows.
  4. **Attestation baseline unchanged.** `check_transition_attestation.py` run with its
     DSN shows no *new* post-cutoff `DRIFT` beyond the 4 rows measured at draft time.
  5. **No terminal mutation.** The lane writes no ticket status, closes no plan, and
     backfills no transition event. Classification is observed, not assigned.
- **Evidence slot:** `WB2-TICKET-CLASS` — observed field `terminal_outcome` vs
  `attention_lost`, timestamp `closed_at`, source `vision.tickets` +
  `kernel.transition_event`, **verification_owner `reviewer`**.
- **Stop condition (non-authoritative):** no terminal plan displays `expired` as its
  outcome and every expired generation has a durable non-success transition or an
  enumerated waiver. Uniform enforcement is W-B6's result, not this lane's claim.

## 3. Step 1b — WorkRequest WR-WB2

Submitted with `runtime_submit_work_request` (which rejects any payload carrying
execution fields — status, worker, scheduling).

- **wrId:** `wr-<P-WB2 plan number>-<epoch>-wb2ticketclass`
- **intent.type:** `ticket-state-classification`
- **intent.objective:** the P-WB2 goal verbatim; `intent.inputs.outputs[]` names the
  census artifact path and the enriched-event query.
- **constraints:** `deterministic: true`, `maxRetries: 1`,
  `timeoutPolicy: "single-pass-read-only-census"`,
  `resourceHints: ["postgres:nexus", "conduit-mcp"]`.
  Safety constraints carried into the payload: no ticket status mutation, no event
  backfill, no plan close, no seeding of historical rows.
- **opTrace:** the resolved ops for the census query, the payload-enrichment edit, and
  the hermetic test; `registryVersion` pinned to the version read at submission.
- **Owner:** `engineer`. **Verifier:** `reviewer` (records the slot attestation as an
  agent record with `plan_ref` = P-WB2).

---

## 4. Step 3 — Plan P-WB3

- **title:** `W-B3 — Generation-aware ticket hygiene and orphan sweep`
- **project:** `nexus`
- **goal:** Produce a sweep log with one row per ticket generation — `status`,
  `expires_at`, `closed_at`, `closure_reason`, `sweep_disposition` — so no open/claimed
  generation past `expires_at` is left without a disposition and every generation closes
  with a reason. Attest; do not close plans.
- **dependencies:** `["<P-WB2 plan number>"]` — the only machine-expressible edge. The
  package edge is *W-B3 depends on W-B2*; the stored `dependencies` jsonb holds plan
  numbers only, so the mapping is created here and recorded in the spec (§9 `R-F1`).
- **filesAffected:**
  - `docs/promotion-flow/evidence/wb3-sweep-log-<plan-number>.json`
  - `bin/todo_lifecycle_sweep.py` — **only if** the plan establishes that the To Do
    forum sweep and the ticket sweep must share the disposition vocabulary; the ticket
    sweeper itself is `typescript/conduit-mcp/src/ticket-sweep.ts`
  - `bin/transition-attestation-waivers.json` (only if new waivers are architect-ruled —
    the file grows by ruling, never by an implementer's convenience)
- **acceptance criteria:**
  1. **Sweep log completeness (S1).** One row per `(plan_id, role, generation)` for the
     **whole** ticket surface — `logged_rows = 843` (406 plans, 0 null `plan_id`), 0
     duplicate keys, 0 unlogged rows — with the derivation (§7.2) recorded in the artifact.
  2. **Backlog disposition (S2, S3).** All 138 disposition-required rows carry a
     vocabulary-bound `sweep_disposition`; the 89 event-absent rows carry either a
     reconciled event or an architect-ruled `waiver_ref`.
  3. **Effective reason (S4).** Every non-open/claimed row carries
     `closure_reason_effective`, computed in the log from
     `coalesce(closure_reason, 'expiry_detection', 'status:'||status)` — never written back
     to the ticket — with `reason_source` (`ticket` 468 · `expiry_event` 49 ·
     `status_fallback` 326) reported alongside it.
  4. **Live-path proof (S5).** Because live has **zero** `open`/`claimed`/`stale` rows, the
     open-generation branch is proven on a seeded throwaway DB per §8.3; a fixture or a
     `live_open_generations = 0` read is not a substitute.
  5. **No plan closure.** The lane attests the log; it closes no plan, creates no builder
     ticket, and writes no ticket status, closure reason or transition event.
- **Evidence slot:** `WB3-SWEEP-LOG` — observed field `sweep_disposition`, timestamp
  `swept_at`, source `vision.tickets` + sweep transition event,
  **verification_owner `planner`**.
- **Stop condition (non-authoritative):** restated by ruling D2 (§8.2) — every one of the
  843 ticket rows logged with a derived generation and an effective reason; every one of
  the 138 disposition-required rows carrying a vocabulary-bound disposition with 0
  unwaived event-absent rows; `live_open_generations` recorded as 0 rather than claimed as
  coverage; and the open-generation branch proven on a seeded database. The old wording
  ("no open/claimed generation past `expires_at`…") is withdrawn: with 0 live rows it was
  satisfiable by an empty set while being literally false about reasons.

## 5. Step 3b — WorkRequest WR-WB3

- **wrId:** `wr-<P-WB3 plan number>-<epoch>-wb3sweeplog`
- **intent.type:** `ticket-generation-sweep`
- **intent.objective:** the P-WB3 goal verbatim; outputs are the sweep-log artifact and
  the seeded-DB proof report path.
- **constraints:** `deterministic: true`, `maxRetries: 1`,
  `timeoutPolicy: "bounded-backlog-sweep"`,
  `resourceHints: ["postgres:nexus", "postgres:throwaway", "conduit-mcp"]`.
  Safety: no bulk close, no respawn, no event synthesis, no waiver file edit without a
  recorded architect ruling.
- **Owner:** `reviewer` — with the separation ruling in §9 `R-F10`: the identity that
  owns W-B3 must not be the one attesting W-B2's slot.
- **Verifier:** `planner` (records the slot attestation, `plan_ref` = P-WB3).

---

## 6. Step 5 — Plan P-WB6 and WorkRequest WR-WB6

- **title:** `W-B6 — Single generation-aware projection surface, uniform expiry audit, reject-scope routing`
- **project:** `nexus`
- **goal:** Implement CD-1 (generation-aware `/state` projection, `hidden_open_generations`
  as a defect signal), CD-2 (one durable audit event per non-terminal generation at
  `expires_at`, `implies_completion=false`), and CD-3 (`REVIEW_REJECT` scope routing:
  `blueprint` → Planner re-scope event and no builder ticket; `implementation` → builder
  rework ticket; absent → `implementation`). Hydrate every plan field in `/state`.
- **dependencies:** `["<P-WB2>", "<P-WB3>", "8261650", "8261651"]` — all four must read
  `REVIEW_PASS` before dispatch.
- **filesAffected:** `typescript/conduit-mcp/src/watcher.ts` (CD-1 projection + field
  hydration, M5/M6), `typescript/conduit-mcp/src/db.ts` (`planRowToPlanCard` JSON.parse on
  `text[]`/`jsonb`), `typescript/conduit-mcp/src/db.ts::getPlansGroupedByStatus` (archived
  bucket, M3), the reject-routing path for CD-3, plus the matching `*.test.ts` harnesses
  (`ticket-sweep.test.ts`, `plans-grouping.wb5.test.ts`).
- **acceptance criteria:**
  1. **CD-1** — `/state` projects per `(plan_id, role)` by generation position (derivation: §7.2); a newer
     open generation is never hidden by an older closed row; hidden open generations are
     reported, not silently collapsed. Fixtures `WB6-F1` (a) and (b) pass **against the
     implementation**, not just against the spec's reference functions.
  2. **CD-2** — exactly one audit event per non-terminal generation past `expires_at`,
     carrying the enriched payload; claimed-inside-window and already-terminal rows
     untouched; `WB6-F2` passes.
  3. **CD-3** — `WB6-F3` passes; blueprint scope creates no builder ticket and preserves
     evidence; absent scope defaults to `implementation`.
  4. **Hydration** — `filesAffected` and `acceptanceCriteria` are non-empty for every
     `/state` plan where the Nebula views carry them (currently 0/34, M5).
  5. **No regression** — the V219/V220/Stage-6 guard harnesses and the W-B conformance
     harness stay green.
- **WorkRequest WR-WB6:** `wr-<P-WB6 plan number>-<epoch>-wb6projection`;
  `intent.type: projection-unification`; constraints `deterministic: true`,
  `maxRetries: 2`, `resourceHints: ["conduit-mcp", "postgres:nexus"]`; safety: no
  receipt-history rewrite, no plan-status mutation outside `issue_receipt`, no ticket
  row deleted.
- **Owner:** `engineer`. **Slot verifiers:** `architect` (`WB6-GENERATION`), `reviewer`
  (`WB6-EXPIRY`), `planner` (`WB6-REJECT`) — three distinct roles, matching the registry.

---

## 7. Ruling D1 — `WB6-GENERATION` derives from `vision.tickets`; `resolution.ticket` stays empty

**Decision (settled, binding on WR-WB6):** the generation position is **derived** from
`vision.tickets` with a deterministic window function. `resolution.ticket` is **not**
populated. The slot's `artifact_or_query` is replaced by §7.2. Option (a) of the former
F2 — an M7 data migration — is rejected and needs no DBA ruling.

### 7.1 Why derivation, not population

Six reasons, each verified read-only on 2026-11-17:

1. **The target cannot hold the live vocabulary.** `resolution.ticket.ticket_status_check`
   admits only `open | claimed | stale | closed`. The live surface carries `cancelled` 434,
   `completed` 176, `expired` 138, `failed` 91, `closed` 4. Loading it would force exactly
   the mapping W-B2 exists to prohibit — `expired` and `completed` collapsing onto one
   member. That is a vocabulary ruling (W-B5 territory), not a lane deliverable.
2. **Mandatory columns have no live source.** `workflow_ref`, `contract_version`,`position`,
   `generation` are `NOT NULL`, and `trg_ticket_lilac_provenance` requires
   `source_implementation`, `runtime_route`, `source_identity` on every INSERT. None of
   these exists in `vision.tickets`; backfilling them means *fabricating* provenance for
   843 historical rows.
3. **The audit half of the store is empty too.** `resolution.ticket_transition` has 0 rows,
   so populating tickets alone would still leave CD-2 without a generation-aware transition
   surface — two migrations, not one.
4. **A second live writer is a divergence risk.** `vision.tickets` is what conduit writes
   today. A populated `resolution.ticket` converts M7 (a visibility gap) into an ongoing
   dual-write reconciliation problem with no owner.
5. **Derivation is deterministic on the real data.** 0 `(plan_id, role, created_at)` ties
   across 843 rows in 406 plans, 0 null `plan_id`; `id` is kept as a strict tiebreak so the
   ordering stays total if a future row ties.
6. **Derived positions reproduce the count the blueprint already relies on.** `8261662` →
   14 builder generations (matching §3 M6/M7), `8261654` → builder 3 / reviewer 2,
   `8261651` → builder 2 / reviewer 1, `8261650` → builder 2.

**Consequence, stated plainly:** derived `generation` is *positional*. It is recomputed on
every read and must never be stored, cached, or backfilled — a historical row inserted
later would renumber its successors. Evidence records `generation` together with
`ticket_id` and `created_at`, so any shift is visible rather than silent.

### 7.2 Replacement query (verbatim slot content)

```sql
-- WB6-GENERATION — derived generation position, newest-per-role projection.
-- Read-only. observed_field: open_generations. timestamp: projected_at.
WITH ranked AS (
    SELECT
        t.plan_id,
        t.role,
        t.id                                             AS ticket_id,
        t.status,
        t.created_at,
        t.expires_at,
        t.closed_at,
        t.closure_reason,
        row_number() OVER (
            PARTITION BY t.plan_id, t.role
            ORDER BY t.created_at, t.id
        )                                                AS generation,
        count(*) OVER (PARTITION BY t.plan_id, t.role)    AS generations_total
    FROM vision.tickets t
    WHERE t.plan_id IS NOT NULL
)
SELECT
    r.plan_id,
    coalesce(
        jsonb_object_agg(r.role, r.generation)
            FILTER (WHERE r.status IN ('open','claimed','stale')
                      AND r.generation = r.generations_total),
        '{}'::jsonb)                                     AS open_generations,
    coalesce(
        jsonb_agg(jsonb_build_object('role', r.role, 'generation', r.generation)
                  ORDER BY r.role, r.generation)
            FILTER (WHERE r.status IN ('open','claimed','stale')
                      AND r.generation < r.generations_total),
        '[]'::jsonb)                                     AS hidden_open_generations,
    jsonb_object_agg(r.role, r.status)
        FILTER (WHERE r.generation = r.generations_total) AS newest_status,
    jsonb_object_agg(r.role, r.generation)               AS generations_by_role,
    now()                                                AS projected_at
FROM ranked r
GROUP BY r.plan_id
ORDER BY r.plan_id;
```

The per-generation spine — shared with W-B3's sweep log, one derivation for two lanes. The
CTE is repeated so this runs as a standalone statement (a bare `FROM ranked` would fail):

```sql
WITH ranked AS (
    SELECT
        t.plan_id,
        t.role,
        t.id                                             AS ticket_id,
        t.status,
        t.created_at,
        t.expires_at,
        t.closed_at,
        t.closure_reason,
        row_number() OVER (
            PARTITION BY t.plan_id, t.role
            ORDER BY t.created_at, t.id
        )                                                AS generation,
        count(*) OVER (PARTITION BY t.plan_id, t.role)    AS generations_total
    FROM vision.tickets t
    WHERE t.plan_id IS NOT NULL
)
SELECT plan_id, role, generation, ticket_id, status,
       (status IN ('open','claimed','stale')) AS is_open_generation,
       (generation = generations_total)       AS is_newest,
       created_at, expires_at, closed_at, closure_reason
FROM ranked
ORDER BY plan_id, role, generation;
```

`WHERE plan_id IS NOT NULL` is a projection filter, not a coverage claim: coverage is the
table's `count(*)` against the spine's row count (843 vs 843 today, 0 null `plan_id`). If a
null ever appears the two counts diverge and S1 fails visibly instead of the row vanishing.
`generations_by_role` is the only per-role column the slot needs — it lets a derived
position be audited from the artifact alone; `max_generations_per_role` was dropped as
derivable from it.

### 7.3 What the query was verified to produce

| Input | `open_generations` | `hidden_open_generations` | `newest_status` |
|---|---|---|---|
| `WB6-F1` case (a) — open gen-2 listed *before* completed gen-1, plus failed reviewer gen-1 | `{"builder": 2}` | `[]` | `{"builder":"open","reviewer":"failed"}` |
| `WB6-F1` case (b) — completed gen-1 + cancelled gen-2 | `{}` | `[]` | `{"builder":"cancelled"}` |
| live `8261654` | `{}` | `[]` | `{"builder":"completed","reviewer":"completed"}` (builder 3 gens, reviewer 2) |
| live `8261662` | `{}` | `[]` | `{"builder":"completed","reviewer":"cancelled"}` (builder 14, reviewer 1) |
| live `8261650` | `{}` | `[]` | `{"builder":"cancelled"}` (builder 2) |
| live `8261651` | `{}` | `[]` | `{"builder":"completed","reviewer":"completed"}` (builder 2, reviewer 1) |

Both fixture cases were run through the query verbatim in a session-local temp table (no
persistent write) and match `decomposition.wb6_contract.fixtures[WB6-F1].cases[*].expect`
exactly.

**Honest limit:** live has zero open/claimed/stale rows, so `open_generations = {}`
everywhere is a *baseline*, not a demonstration that a newer open generation wins. The M6
defect can only be shown by `WB6-F1` running against the **implementation** — the same
standing as D2's seeded proof. A live read that prints `{}` proves nothing about the fix
and must not be cited as if it did.

### 7.4 Effect on the slot and on W-B6's ACs

- Slot `WB6-GENERATION`: `artifact_or_query` = §7.2; `observed_field` = `open_generations`
  (with `hidden_open_generations` as the defect signal); `timestamp` = `projected_at`;
  `source_reference` = `vision.tickets` + `kernel.transition_event`; verifier `architect`,
  unchanged. **`resolution.ticket` no longer appears in any W-B evidence path.**
- P-WB6 AC1 becomes dischargeable: CD-1 is proven by `WB6-F1` against the implementation
  plus the derived projection, with no M7 migration prerequisite.

---

## 8. Ruling D2 — W-B3's stop condition restated, with the seeded-DB proof contract

### 8.1 Why the old text is unusable

Old: "no open/claimed generation past `expires_at` without a recorded sweep disposition;
every generation closes with a reason." Live today: `open` 0, `claimed` 0, `stale` 0 — the
first clause is satisfied by an empty set while the second is *false* under any literal
reading, because all 138 `expired` rows carry `closed_at IS NULL` **and**
`closure_reason IS NULL`. A condition that is simultaneously vacuous and false gates
nothing.

### 8.2 Restated stop condition (replaces the blueprint wording for this lane)

W-B3 stops only when all five predicates hold, each measured in the sweep log with its own
stamped read:

- **S1 — total coverage, not just the interesting rows.** Exactly one log row per
  `(plan_id, role, generation)` across the whole surface: `logged_rows = live_rows`
  (baseline **843 rows / 406 plans**, `plan_id` null: 0), 0 duplicate keys, 0 unlogged
  rows. This replaces the slot query's `where status in ('open','claimed','stale')`, which
  selects nothing.
- **S2 — disposition completeness over the backlog.** The *disposition-required set* is
  `status IN ('expired','stale')` ∪ (`status IN ('open','claimed')` AND
  `expires_at < swept_at`). Every row in it carries a non-null `sweep_disposition` from the
  closed vocabulary {`expired_with_event`, `expired_without_event_waived`,
  `open_past_expiry_disposed`, `no_disposition_required`}: 0 nulls, 0 out-of-vocabulary
  values. `no_disposition_required` covers every row not in the disposition-required set —
  terminal rows, open/claimed rows still inside their window, and open/claimed rows with
  `expires_at IS NULL` (never due, which the log reports as such rather than treating as
  due, since the S2 predicate is `NULL` and would otherwise silently exclude them).
  Baseline: **138** rows require a disposition.
- **S3 — no unwaived loss.** `expired_without_event_waived` requires a non-null
  `waiver_ref` naming an architect ruling, and
  `count(disposition = 'expired_without_event_waived' AND waiver_ref IS NULL) = 0`.
  Baseline: **89 of 138** expired rows have no `expiry_detection` event, so the lane must
  produce 89 waivers or 89 reconciled events — the work the old wording let slide.
- **S4 — attributable reason, computed in the log, never written to the ticket.** Each row
  carries `reason_source` ∈ {`ticket`, `expiry_event`, `status_fallback`}: `ticket` when
  `t.closure_reason` is non-null, `expiry_event` when it is null and an `expiry_detection`
  event exists, `status_fallback` otherwise. The join is
  `kernel.transition_event.aggregate_id = vision.tickets.id` — the payload carries only
  `{from_status, to_status, reason}`, so a `payload->>'ticket_id'` join matches nothing.
  `closure_reason_effective = coalesce(t.closure_reason, 'expiry_detection',
  'status:'||t.status)`. Non-nullness is guaranteed by that coalesce and therefore proves
  nothing — the predicate is on the **source census**, not on nullness: all three counts are
  reported, and the `status_fallback` rows are enumerated and either accepted by ruling or
  raised as a W-B2 follow-up. Baseline `ticket` **468** · `expiry_event` **49** ·
  `status_fallback` **326**. No `UPDATE`, no event synthesis.
- **S5 — the forward branch is observed, not assumed.** The log records
  `live_open_generations` (baseline **0**) alongside `swept_at`. The branch's behaviour is
  proven on the seeded DB (§8.3); the zero is reported as a zero, never inherited as
  coverage.

Three counting corrections the log must carry, because the stale figures hide them: (a)
expiry events are **91 events over 89 distinct ticket ids** — one cancelled ticket carries
two; (b) only **49** of those ids are currently `expired`, the other **40** reached another
status afterwards (`expired_then_recovered`), so 91 events ≠ 91 covered backlog rows and
neither number is 138; (c) **375** rows carry no recorded `closure_reason`, not 138 — the
138 figure counts only `expired` rows and silently treats every `cancelled` / `completed` /
`failed` row without a reason as covered.

**Stop condition, one sentence:** every one of the 843 ticket rows is logged with a derived
generation and an effective reason; all 138 disposition-required rows carry a
vocabulary-bound disposition with 0 unwaived event-absent rows; `live_open_generations` is
recorded as 0 rather than claimed as coverage; and the open-generation branch is proven on
a seeded database, not on production.

### 8.3 What the seeded-DB proof must demonstrate

A throwaway database (proposed `nexus_wb3_seed_<epoch>`) built from the repository's own
schema code — `initDb`/`createSchema` in `typescript/conduit-mcp/src/db.ts` — pointed at
the seed with `CONDUIT_PG_DSN=postgresql://…/nexus_wb3_seed_<epoch>`; **never** a dump of
production. The sweep is driven by the implementation under test,
`new TicketExpirySweeper({ detectStaleTickets, detectExpiredTickets }).runOnce()` calling
`db.ts`, not by SQL re-written for the report: a hand-written
`UPDATE … SET status='expired'` proves the seed script, not the lane.

Expected events keep the live shape — `event_type='transition.rejected'`,
`payload.reason='expiry_detection'`, `aggregate_id = <ticket_id>` (91 rows today; CD-2 does
not re-type the event). Because A2 asserts W-B2's enriched payload, the run happens against
W-B2's merged code after its `REVIEW_PASS`.

Ten seeded cases with known expected outcomes:

| Case | Seeded row | Expected disposition | Events emitted |
|---|---|---|---|
| C1 | builder gen-1 `open`, `expires_at` past, no prior event | `open_past_expiry_disposed` | 1 |
| C2 | builder gen-2 `claimed`, past expiry | `open_past_expiry_disposed` | 1, `from_status='claimed'` |
| C3 | builder gen-3 `stale`, past expiry | `open_past_expiry_disposed` | 1 |
| C4 | builder gen-4 `open`, expiry in the future | `no_disposition_required` | 0 |
| C5 | reviewer gen-1 `completed`, past expiry | `no_disposition_required` | 0 |
| C6 | builder gen-5 `cancelled` (plan already closed), past expiry | `no_disposition_required` | 0, no respawn |
| C7 | builder gen-6 `expired`, no kernel event (legacy gap) | `expired_without_event_waived` + `waiver_ref` | 0 — evidence is never synthesized |
| C8 | builder gen-7 `expired`, with a seeded `expiry_detection` event | `expired_with_event` | 0 new |
| C9 | reviewer gen-2 `open`, past expiry, predecessor receipt `REVIEW_REJECT` (rework generation) | `open_past_expiry_disposed` | 1, `implies_completion=false`, generation carried |
| C10 | builder gen-8 `open`, `expires_at IS NULL` | `no_disposition_required` | 0 — never due, distinct from not-yet-due |

Assertions the proof report must contain — exact numbers, never "at least one":

1. **A1 exact per-case counts.** Emitted events = 1 for C1, C2, C3, C9 and 0 for C4–C8 and
   C10; each case lists its `ticket_id`, derived `generation` and the resulting event id.
2. **A2 payload completeness.** Every emitted event carries all eight CD-2 fields
   (`plan_id`, `role`, `generation`, `expires_at`, `from_status`, `to_status='expired'`,
   `reason='expiry_detection'`, `implies_completion=false`), typed boolean `false` — not
   `"false"`, not absent.
3. **A3 no completion implication.** No ticket moves to `completed` and the plan's derived
   status projection is identical before and after the sweep. Expiry must never make a
   plan look done.
4. **A4 idempotence.** A second sweep emits **0** new events and changes no disposition;
   the event count is stable across runs (exactly-one-per-generation).
5. **A5 window respected.** C4 and C10 are both untouched, for different reasons —
   `expires_at > swept_at` and `expires_at IS NULL` — and the report shows both comparisons,
   so "not due" is never conflated with "never due".
6. **A6 one derivation, two lanes.** The §7.2 spine query run on the seed yields exactly
   the generation numbers C1–C10 assume, proving W-B3 and W-B6 share a derivation.
7. **A7 report contract and isolation.** One machine-readable report names the seed DB,
   schema revision/hash, seed script path, `run_at`, per-case expected-vs-observed and
   teardown result — reproducible, not narrative — and shows `vision.tickets` (843 rows /
   138 expired), `kernel.transition_event` (91 expiry events) and `resolution.ticket` (0) on
   `nexus` unchanged across the lane. The seed DB is dropped and the drop is recorded.

**If a throwaway database cannot be created, W-B3 does not pass.** S5 then has no proof and
the lane must be reported blocked rather than signed off on an empty production read. This
is the one authorization the lane needs up front — its exact shape is §9.2.

---

## 9. Rulings — operator-accepted, binding (2026-11-17)

Every open flag is now ruled. D1 (§7) and D2 (§8) were settled earlier; the operator accepted
the recommended ruling for each remaining flag on 2026-11-17, so none of F1, F3, F4, F6–F13 is
open any more. They are recorded as `R-F<n>` — the number is the flag, not a sequence — so they
never collide with the D-series, and each states its **binding effect**: what a lane must do,
not merely what was decided. These are decisions, not state; nothing was created to record
them.

| Ruling | Covers | Binding effect |
|---|---|---|
| `R-F1` | F1 | Exactly **one** later edit to `bin/plan-gating-specs/w-decomposition.json`: the package→plan mapping **and** the §7.2 replacement for `WB6-GENERATION.artifact_or_query`, written once the three plan numbers exist, owned by the W-B6 lane, planner-attested. |
| `R-F2` | F2 (= D1) | `WB6-GENERATION` derives from `vision.tickets`; `resolution.ticket` stays empty. Its spec-string residual is discharged by `R-F1`. |
| `R-F3` | F3 | W-B2's AC3 enrichment is a hard prerequisite for `WB6-EXPIRY`; a W-B2 that lands without it leaves W-B6 **blocked**, never waived. |
| `R-F4` | F4 | `WB6-REJECT` evidence = the live negative (0 of 344 carry `scope`) **plus** a seeded receipt in the throwaway DB. A production `review_reject` is never written to manufacture evidence. |
| `R-F5` | F5 (= D2) | Stop condition = S1–S5 and the ten-case seeded proof. Its authorization residual is §9.2; without it W-B3 reports **blocked**. |
| `R-F6` | F6 | `closed` is its own `terminal_outcome` member, never folded into `completed`. |
| `R-F7` | F7 | Attestation evidence cites the exact invocation and cutoff; no code change. |
| `R-F8` | F8 | Plans are created **serially**, one lane at a time, each gated on the previous `REVIEW_PASS`. |
| `R-F9` | F9 | Gating readings come from `/state` + `nebula.plan_status`, never from the checker's `UNKNOWN`. |
| `R-F10` | F10 | Distinct `reviewer` identities for WR-WB3 and the `WB2-TICKET-CLASS` attestation; `planner` fallback recorded in the slot. |
| `R-F11` | F11 | `wr-8261673` stays untouched by every W-B lane. |
| `R-F12` | F12 | Every artifact cites its own stamped read; blueprint §1 numbers are never observations. |
| `R-F13` | F13 | `check_plan_gating.py`'s exit status is not W-B evidence until W-B4 repairs `expected_gating`. |

### 9.1 What the table does not cover

The table holds each ruling's binding effect. Below is only the evidence behind it — why the
ruling is shaped that way — so the two do not restate each other.

**`R-F1`.** The numbers do not exist until creation, so the edit cannot precede it; and the
spec file is the only machine-readable home for the linkage, since
`packages.*.child_work_request` is prose (`"W-B2 execution WR (docket c7c26160)"`),
`resolution.work_request` has no docket column, and `entity_key` is null on every row. It is
planner-attested because it is a spec claim, not an implementation one.

**`R-F2`.** No separate text: §7 carries the derivation, the replacement query and the verified
outputs. Recorded here only to keep the flag list closed.

**`R-F3`.** All 91 live expiry events carry `{reason, to_status, from_status}` — no `plan_id`,
`role`, `generation`, `expires_at`, `implies_completion`. Both the blueprint and
`docs/design-cd2-expiry-sweeper.md` §1 call the enrichment REQUIRED and it is unimplemented,
which is what makes W-B2's AC3 a gate rather than a parallel nicety. A fixture that supplies
fields the runtime never emits does not satisfy it.

**`R-F4`.** The slot named a live `select`, which is why fixture-only observation was refused in
the first place; live has 344 `review_reject` receipts, 0 with `scope`, and
`planner_rescope_event` has never executed. A seeded receipt is a real receipt against the real
schema — admissible where a manufactured production one is not.

**`R-F5`.** See §8. The stop condition, the ten cases and A1–A7 are unchanged.

**`R-F6`.** Four rows (plans 1285, 1291, 1303, 1308; two with no `closed_at`). Blueprint §2's
vocabulary has no `closed` member and neither does the receipt map, so W-B5's ratification must
either inherit the member or W-B2 must record it as outside the ratified set — the census cannot
leave that bucket implicit while demanding a bucket for every row.

**`R-F7`.** `--dsn` defaults to `$CONDUIT_PG_DSN` and exits 2 when empty; `--since` defaults to
the adoption cutover; `--json` for the artifact. Baseline: 577 unattested terminal tickets, 4
post-cutoff. The 4 must be named as the starting point, or a reader cannot tell improvement from
drift.

**`R-F8`.** Creation is not inert: `nebula_create_plan` lands `pending` and conduit bootstraps
`PLAN_CREATE` plus a builder ticket within ~30s. So "how many plans do we create at once" is
really "how many unstartable tickets do we open".

**`R-F9`.** (a) `8261654` is `REVIEW_PASS` per `/state` + `nebula.plan_status` while the checker
reports `UNKNOWN` — a post-completion quirk with no live ticket row. (b) `dependencies` holds
plan numbers only, so the package edge becomes expressible only once `R-F8`'s serial creation
supplies them.

**`R-F10`.** The matrix is satisfiable on paper by one identity holding both roles, which is
exactly how the separation collapses. The ruling makes it an identity claim rather than a role
claim so no agent can attest its own lane.

**`R-F11`.** 47 live rows vs 46 ratified; the extra row is `66b9d2e0…` /
`wr-8261673-1794650830-98004713`, DRAFT, 2026-11-14. Cleaning it needs its own authorization — a
W-B lane that "tidies" it while writing evidence would be mutating an unrelated ledger under
cover of this one.

**`R-F12`.** Blueprint §1's 171/84/338/574 against live 138/91/344/577 is four weeks of ticket
history, not noise. Quoting it as an observation would misdate every artifact that copied it.

**`R-F13`.** `decomposition.valid = true` with two `consistent: false` entries: `8261654` reads
`UNKNOWN` against expected `IN-REWORK`, `8261655` reads `IN-REWORK` against expected
`BLOCKED-WITH-EXPIRED`. Both are stale prose, not gates; repairing them belongs to whoever owns
the W-B4 spec edit.

### 9.2 The one authorization still owed — throwaway seed database for W-B3

Measured facts that scope the grant: `pguser@localhost:5432` already holds `rolcreatedb` (and
`rolsuper`), and no `nexus_wb3_seed*` database exists. **No privilege change is required — what
is missing is authorization, not capability.** The grant should therefore be narrow and explicit:

1. **Permission, in words, for one named database.** Authorization to create and drop a
   database named `nexus_wb3_seed_<epoch>` on the local cluster, for the duration of one W-B3
   run. Nothing else on that cluster is in scope.
2. **The window.** A start and end time — one working session is enough, ≤24h. After the window
   the authority lapses; a leftover `nexus_wb3_seed_*` database is then a reported defect, not
   silently reused.
3. **`nexus` stays read-only for the lane.** A7's isolation proof reads `vision.tickets`,
   `kernel.transition_event` and `resolution.ticket` counts on `nexus`; the grant confirms those
   reads and states that no W-B3 step writes to `nexus`. No DDL, `INSERT`, `UPDATE` or `DELETE`
   on `nexus` is covered.
4. **No production copy, either direction.** The seed DB is built by `createSchema` from the
   repository, never by `pg_dump`/restore of `nexus`, and nothing is copied back. This also
   answers R9 for this lane: no schema change lands on `nexus`, so there is nothing to replicate
   to vanadium, and the lane must say that rather than leave the question open.
5. **Credential handling.** The seed DSN reaches the lane through `CONDUIT_PG_DSN` in its
   environment. It is never written into an artifact, committed, or echoed into a report;
   artifacts name the database, the schema revision/hash and `run_at`, not the DSN.
6. **Teardown is part of the grant.** The database is dropped at the end of the run and the drop
   is recorded (A7). If the drop fails, that is reported with the database name — the
   authorization does not extend to leaving it behind.
7. **How to grant it minimally.** One operator sentence, or one `decision` record, naming items
   1–4 is sufficient; the lane quotes it by id in the W-B3 artifact. No secret is sent and no
   role is altered. If least privilege is preferred, a dedicated `nexus_seed_owner` role with
   `CREATEDB` for the window is acceptable and must hold no membership in whatever owns `nexus`.

**What the grant does not cover:** seeding rows into `nexus`; writing receipts, tickets or
transition events into `nexus` — including `R-F4`'s blueprint-scope receipt, which belongs in the
seed DB; waiving S5 (if the database cannot be created, W-B3 reports **blocked**); or clearing
`wr-8261673` (`R-F11`).

**Without it:** W-B3 still produces S1–S4 over the 843 live rows — sweep log, 138-row disposition
backlog, 89 event-absent rows, reason census — but S5 and A1–A7 have no subject, so the lane is
reported blocked rather than passed. That is the intended failure mode, not a formality.

---

### 9.3 Execution log and the WR-submit blocker

| When (UTC) | Event |
|---|---|
| 17:32:17 | **P-WB2 created** via `POST /api/plans` → plan **8261674**, `pending`, 4 code paths + the evidence artifact. |
| 17:32:23 | `update_plan` resolved the draft's placeholder: `docs/promotion-flow/evidence/wb2-ticket-class-8261674.json`. |
| 17:32:35 | conduit bootstrapped `plan_create` receipt `afdfe251-96a2-4178-82db-58043ddd6cf3` (`request_id 48c9b57a-9c70-4587-a9e5-5702f61a0acd`) and builder ticket `ticket-8261674-builder-5be95a2a-46af-4042-9d7c-1966e6100a72`, `open`, expiring 2026-11-18 17:32:35. |
| by 17:34 | that ticket moved to **`claimed`** — the plan lane is live ahead of its WorkRequest, exactly the sequencing hazard R-F8 accepted. |
| 17:33:03 | conduit also wrote a **plan-mirror row** `wr-8261674-1794954783-7b623473` (DRAFT) into `resolution.work_request`. It is **not** WR-WB2: no `intent.type`, no constraints or `opTrace`, no event stream, no `conduit.work_request_state` row, and it is invisible in the `vision.work_requests` view because it has no legacy-map entry. All 46 prior rows have the same shape — they are the pre-WR mirror, not submitted WorkRequests. |
| 17:33 | **WR-WB2 dispatch blocked.** `runtime_submit_work_request` returns `-32603 cannot insert into view "work_requests"`. |

**The blocker, precisely.** `vision.work_requests` is currently a **view** over
`vision.work_request_legacy_id_map ⋈ resolution.work_request`
(`legacy_id = 'vision.work_requests:' || wr_id`) with no `INSTEAD OF` trigger, so it is not
insertable. `createWorkRequest` (`typescript/conduit-mcp/src/db.ts:5050`) inserts into
`${VISION_SCHEMA}.work_requests`; the same file's migration 951 exists precisely to *drop that
view and create a table*, so the running code expects the table form while the live schema has
been put back into the view form by the LOSM/V187 work. Code and schema disagree. There is no
second tool — `runtime_submit_work_request` is the only WR-writing tool — and no conduit path
touches `resolution.work_request`, so no supported workaround exists. Writing the row by hand
would produce a WR with no `WR_SUBMITTED` event, invisible to `runtime_get_work_request`: a record
with no lifecycle, which is the failure this pipeline exists to prevent.

**Two candidate fixes, neither in this sequence's authority.** (a) Point `createWorkRequest` at
`resolution.work_request` (plus the legacy-id map) so the view projects the new row — a code
change, and the shape the migration and view already imply. (b) Restore the table form the
migration expects — a live schema change needing its own authorization and an R9 replication
answer. Until one lands, §1's steps 2, 4 and 6 are unreachable; **P-WB3 and P-WB6 stay uncreated**,
per R-F8.

**Resolved 17:44Z by fix (a).** `createWorkRequest` now detects the relation shape per process
(`pg_class.relkind`) and, for the view, writes `resolution.work_request` plus a
`vision.work_request_legacy_id_map` row in one transaction; `appendEvent` projects runtime status
onto the CHECK-constrained `business_status` instead of updating the view; and
`runtime_submit_work_request` now passes `intent.inputs.plan_id` through as `context.plan_id`.
conduit-mcp restarted on 3100 with the change. WR-WB2 = `wr-8261674-1794937465-wb2ticketclass`,
`VALIDATED`, first event `WORKREQUEST.CREATED` (sequence 124), canonical row
`6bfebc75-f9e2-4118-a86e-dae9c3ff5a32`. Note `resolution.work_request.plan_id` stays NULL: it is
FK-bound to `resolution.implementation_plan`, which holds 0 rows, so the plan linkage lives in
`context.plan_id` = `8261674`.

**The lane had already run without it.** The bootstrap builder ticket was claimed 17:33:03 and a
worker session closed it `completed` at 17:38:42, having modified all five planned paths and
written `docs/promotion-flow/evidence/wb2-ticket-class-8261674.json` (~145 KB). It then spawned a
reviewer ticket, which was **cancelled** at 17:42:45Z (event `1787d5bf-7001-45ca-8b9b-02d12b42b1bb`) with
`closure_reason = 'lane_stopped_no_work_request'` and an audited transition event. So W-B2's
implementation now exists in the working tree, uncommitted and unreviewed, and WR-WB2 was
dispatched *after* the fact — the work is ahead of its WorkRequest, not behind it. That inversion
must be settled (re-run under the WR, or accept the tree as the lane's output and review it)
before W-B3.

**Settled 2026-11-18: accept the tree, do not re-run.** The delta was reviewed against the plan's
`files_affected` and WR-WB2, and the lane work that belongs to plan 8261674 was lifted out of the
dirty root worktree into a clean one off `origin/main` (`f661b6309`): branch
`wb2-impl-8261674` @ `fd7964f4`, 7 files, everything else left where it was. A re-run would have
reproduced the same code from the same plan while discarding the census the lane actually
observed, so the tree is accepted as the lane's output with three repairs: the Python emitter
serializes `expires_at` with `.isoformat()` (psycopg2 returns a `datetime` and
`_record_kernel_transition` `json.dumps` the payload, so the raw value raised `TypeError` inside
the sweep transaction); `src/ticket-sweep.test.ts` is now in `npm test`, which named three files
and never ran it; and the artifact's `work_request_id` now names WR-WB2
(`wr-8261674-1794937465-wb2ticketclass`) with the plan-mirror id kept as
`run_recorded_work_request_id`. The inversion is recorded, not rewritten; the census itself was
not restamped — a `census_drift_note` carries the 2026-11-18 re-read (R-F12).

**What the settlement leaves open.** Two reviewer calls travel with the PR rather than being
decided by the lane: the comment-only `watcher.ts` edit, which satisfies the plan's five-path
list without behaviour and hides the fact that the enrichment lands in `db.ts`; and AC2, which
still rests on the unratified waiver for `8261671` builder gen-2. Raised as **PR #1026**
(`wb2-impl-8261674`) for the tester's WB2-TICKET-CLASS attestation. Fix (a) from the blocker
above travels separately as **PR #1027** (`wr-submit-projection-view`, one commit off
`origin/main`): it is dispatch infrastructure for every lane, not W-B2 evidence, and it changes
no schema and no data, so it owes no R9 replication answer. P-WB3 and P-WB6 remain uncreated per
R-F8 until #1026 is attested; the §9.2 seed-database authorization is still the only
authorization owed.

---

## 10. Explicit non-claims

- Nothing here creates a plan, WorkRequest, receipt, ticket, waiver, spec edit or docket
  post. All artefacts named above are *proposals* with proposed titles and ids. The §9
  rulings are decisions, not state: they were written into this document, nowhere else.
- The only authorization still owed is §9.2 (a throwaway `nexus_wb3_seed_*` database).
  Until it is granted, W-B3 can produce S1–S4 but not S5, and must report blocked.
- This sequence does not claim Conduit and Nebula agree, and it does not make the wave
  end-state an acceptance claim of any single lane — that stays delegated per blueprint §6.
- Passing `bin/check_plan_gating.py bin/plan-gating-specs/w-decomposition.json` after the
  lanes exist is W-B4-style *structural* conformance, not runtime evidence.
