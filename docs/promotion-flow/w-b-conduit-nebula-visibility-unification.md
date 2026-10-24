# W-B — Conduit/Nebula visibility unification: M-sized package decomposition

**Plan:** 8261654 (child of 8261652; parent superseded by 8261653/8261654/8261655)
**Docket:** `c7c26160-5b22-463f-9ee0-6e4784bae0e8` (to-do life-cycle policy)
**Wave:** W-B — post-ST.01 promotion-identity tail
**State:** decomposition blueprint complete; **no runtime claim** — Conduit/Nebula
agreement is delegated to child execution WorkRequests (see §6)
**Owner of this document:** builder · **Vocabulary ratification:** architect ·
**Verification:** reviewer

This document supersedes the 2026-09-22 builder artifact (agent record
`1840f9b6-181a-4119-b68b-61f301cb1d77`) **without overwriting it**. That record
and its receipts stay as history; this blueprint re-scopes AC4/AC5 per planner
response `b728da54` and architect ruling `1867c88d`.

The machine-readable twin of this document is
`bin/plan-gating-specs/w-decomposition.json` (`decomposition` block). The spec is
the evidence index; `bin/check_plan_gating.py` validates it (packages, vocabulary,
evidence-slot completeness, and the W-B6 fixtures). Neither artifact mutates
state.

---

## 0. Boundary — what this decomposition is, and what it is not

**Is:** an inventory of the two visibility surfaces, a unified status vocabulary,
six independently boundary-checked work packages (W-B1–W-B6), an evidence-slot
schema with an aggregate matrix, and the W-B6 contract with executable regression
fixtures.

**Is not:** a claim that Conduit `/state` and Nebula projections currently agree.
They do not (see §1). Every package's stop condition is a **package-level,
non-authoritative** stopping point; the wave end state is reached only when the
child WorkRequests execute and their evidence slots are filled with observed
results.

**Dependency surface:** W-B has **no ST.01 dependency** and may execute in
parallel with W-A. Its only hard prerequisites are the existing Conduit/Nebula
surfaces and the receipt/ticket stores listed in §7.

---

## 1. Inventory (AC1) — Conduit states × Nebula read-model mismatches

Snapshot: `2026-10-24T15:13Z`, read-only from
`GET :3100/state`, `nebula.plans`, `nebula.implementation_plans`,
`nebula.plan_status`, `nebula.receipts_unified`, `vision.tickets`,
`resolution.ticket`, `resolution.receipt`, `kernel.transition_event`.
Reproduction queries are in §7; the captured rows are summarized here.

### 1.1 Surfaces

| Surface | Authority | Shape observed |
|---|---|---|
| `GET /state` (conduit-mcp) | receipt-derived projection (`nebula.plan_status` → in-memory `watcher.getState`) | buckets: pending 16, active 3, completed 15, blocked 0, archived 0, planning 0, hold 0 (34 plans) |
| `nebula.plans` view | plan identity/fields (`deleted=0` live) | 35 live rows |
| `nebula.implementation_plans` view | blueprint lifecycle status (`pending` / `completed` / `archived`) | 41 rows: pending 19, completed 16, archived 6 |
| `nebula.receipts_unified` view | receipt chain (`resolution.receipt` ∪ conduit-lineage `execution.receipts`) | 2,105 receipts; drives `derived_status` |
| `vision.tickets` | the ticket surface the flow writes | 779 tickets over 395 plans |
| `resolution.ticket` | canonical ticket table (generation/claim columns) | **0 rows** — not the live surface |
| `kernel.transition_event` | durable state transitions (`aggregate_type='ticket'`) | 118 ticket events |

Union of the two plan surfaces = **41 plan numbers**; `/state` exposes **34** of
them. The seven-plan difference is explained by M3/M4 below.

### 1.2 Mismatch register

| ID | Class | Affected plans | Observed on the two surfaces | Covered by |
|---|---|---|---|---|
| **M1** | receipt-derived status vs blueprint status | `0002`, `0005` | Conduit `active` / `IMPLEMENTATION`; Nebula `completed` | W-B1 |
| **M2** | receipt-derived status vs blueprint status | `8261653` | Conduit `completed` / `REVIEW_PASS`; Nebula `pending` | W-B1 |
| **M3** | archived visibility | `0003`, `0004`, `0006`, `0008`, `0015`, `8261652` | Canonical archive set = 6; `/state.archived` = **0**. `db.ts` queries the archived set, but `watcher.getState()` later **replaces** `result.archived` with the filesystem-derived list, discarding the DB bucket | W-B5, W-B6 |
| **M4** | unreachable plan (no bucket) | `8261662` | Pending in `nebula.implementation_plans`, present in `nebula.plans` (`deleted=0`) with `derived_status = NULL` (zero receipts in `nebula.receipts_unified`), so the `getPlansGroupedByStatus` switch drops it: the plan is invisible in `/state` while carrying 14 builder ticket generations | W-B4, W-B6 |
| **M5** | field hydration | all 34 `/state` plans | `filesAffected` and `acceptanceCriteria` are empty in `/state` for **34/34** plans, although the Nebula views carry them (e.g. 8261654: 6 files, 8 ACs). `planRowToPlanCard` calls `JSON.parse` on columns that PostgreSQL returns as `text[]`/`jsonb` (already parsed), so the parse throws and the field collapses to `[]` | W-B6 |
| **M6** | generation blindness | `8261654` (4 rows), `8261662` (14 rows), 8261653 (7 rows) | `/state` builds `ticketStatuses` keyed by **role only**, with `ORDER BY plan_id, role` — no generation/created_at ordering. A later row overwrites an earlier one arbitrarily, so an open newer generation can be hidden by a closed older one (CD-1) | W-B6 |
| **M7** | ticket-store split | all ticket-bearing plans | `/state` reads `vision.tickets` (779 rows); canonical `resolution.ticket` is empty (0 rows) and holds the `generation`/`predecessor_receipt_id` columns the flow needs | W-B2, W-B6 |
| **M8** | expiry audit gap | 171 `expired` tickets | Only **84** `expiry_detection` events exist for 171 expired tickets; 574 terminal tickets have **zero** `kernel.transition_event` rows (571 pre-cutoff historical, 1 post-cutoff DRIFT: `ticket-8261653-builder-0f68e14f`). Expiry therefore does not uniformly produce a durable per-generation audit record (CD-2) | W-B2, W-B6 |
| **M9** | reject scope absent | 338 `resolution.receipt` rows with `kind='review_reject'` (and 297 `execution.receipts` of type `REVIEW_REJECT`) | **No row carries a structured `scope`**; every reject currently resolves through the default. The blueprint-scope Planner-return edge therefore has no live instance yet (CD-3) | W-B6 |

Checked and **not** a mismatch at snapshot time (recorded so the inventory is not
re-litigated from stale notes): `get_plan_receipts` serves the live receipt chain
for sampled plans (8261654 → 3 receipts; 8261653 → 5, matching
`nebula.receipts_unified`). The earlier "dead store" finding is stale.

---

## 2. Unified status vocabulary (AC3)

Canonical vocabulary (the only states a projection may present):
**`pending` · `active` · `completed` · `archived`**.

### 2.1 Conduit receipt-chain state → canonical

`derived_status` values observed in `nebula.plan_status` map as:

| Canonical | Conduit receipt-chain states | Rule |
|---|---|---|
| `pending` | `PLAN_CREATE`, `PLANNING`, `REQUEUED`, `HOLD`, `CRITIQUE`, `CRITIQUE_PASS`, `CRITIQUE_REJECT` | pre-implementation or returned-to-planning; no implementation receipt has won the chain |
| `active` | `IMPLEMENTATION`, `REVIEW`, `REVIEW_REJECT`, `BLOCK`, `PLAN_BLOCK`, `API_LIMIT` | non-terminal work: implementation/review in flight, rework after reject, or an attention state that is not terminal |
| `completed` | `REVIEW_PASS` | terminal success (last terminal receipt is `REVIEW_PASS`) |
| `archived` | `CANCELLED`, `ABANDONED` | terminal non-success; also the explicit Nebula archive disposition |

Precedence follows the existing `nebula.plan_status` CASE chain, with one required
correction: **`REVIEW_REJECT` must not be folded into `completed`** — it is
`active` (rework) until a later `REVIEW_PASS` or a terminal non-success receipt.

### 2.2 Canonical → Nebula projection state

The Nebula blueprint lifecycle only expresses three states today. The projection
must be explicit about the lossy edge rather than silently inventing one:

| Canonical | `nebula.implementation_plans.status` projection | Note |
|---|---|---|
| `pending` | `pending` | 1:1 |
| `active` | `pending` (until a runtime `active` value is ratified) | **lossy**; sub-state stays in the receipt chain, not in the blueprint status |
| `completed` | `completed` | 1:1, but only after a terminal `REVIEW_PASS` |
| `archived` | `archived` | explicit archive disposition; must remain visible in `/state.archived` |

Ratification rule (W-B5): a plan's canonical state is **derived from its receipt
chain**; the blueprint status is a projection that may lag and may never be
silently used to override a terminal receipt. If the two disagree, the receipt
chain wins and the disagreement is itself an evidence item (M1/M2).

---

## 3. W-B packages (AC2)

Each package has **one owner**, explicit dependencies, evidence outputs drawn
from the slot registry (§4), and a **non-authoritative** stop condition (the named
owner records the package-level stopping point; the wave end state is the child
WorkRequests' observed results).

| Pkg | Title | Owner | Depends on | Evidence slots | Child WorkRequest |
|---|---|---|---|---|---|
| **W-B1** | Legacy/derived plan-status reconciliation | `dba` | — | `WB1-DISPOSITION` | W-B1 execution WR (delegated) |
| **W-B2** | Ticket-state semantics: `expired` ≠ `completed` | `engineer` | — (shares sweeper with W-B3) | `WB2-TICKET-CLASS` | W-B2 execution WR |
| **W-B3** | Generation-aware ticket hygiene + orphan sweep | `reviewer` | W-B2 | `WB3-SWEEP-LOG` | W-B3 execution WR |
| **W-B4** | Structured start-condition gating + family links | `planner` | — | `WB4-GATING` | W-B4 execution WR |
| **W-B5** | Unified vocabulary + archived visibility ratification | `architect` | W-B1 (dispositions), W-B4 (family) | `WB5-PROJECTION` | W-B5 execution WR |
| **W-B6** | Single generation-aware projection surface + expiry audit + reject routing | `engineer` | 8261650, 8261651; W-B2/W-B3 sweeper | `WB6-GENERATION`, `WB6-EXPIRY`, `WB6-REJECT` | W-B6 execution WR |

All six are independently reviewable: each slot's `verification_owner` differs
from the package owner where the owner is also the implementer, and no package's
stop condition requires another package's runtime result to be *asserted as
done* — it may depend on another package's **evidence slot being filled**, which
is the linkage the aggregate matrix records (§4.3).

### W-B1 — Legacy/derived plan-status reconciliation

- **Owner:** `dba`. **Dependencies:** none.
- **Evidence:** M1 (`0002`, `0005` — conduit `IMPLEMENTATION` vs nebula
  `completed`) and M2 (`8261653` — conduit `REVIEW_PASS` vs nebula `pending`).
- **Evidence output:** one disposition row per plan (`complete` / `archive` /
  `requeue`) citing the winning receipt and timestamp.
- **Non-authoritative stop condition:** every plan in the M1/M2 set carries a
  recorded disposition and no plan disagrees across the two surfaces without one.
  The `dba` records the disposition; it does **not** rewrite receipt history and
  does not assert that the projections now agree.

### W-B2 — Ticket-state semantics: `expired` ≠ `completed`

- **Owner:** `engineer`. **Dependencies:** none (shares the sweeper with W-B3).
- **Evidence:** M8 — 171 expired tickets vs 84 expiry events, plus 574 terminal
  tickets with zero transition events (1 post-cutoff DRIFT).
- **Evidence output:** classification table separating `terminal_outcome`
  (`completed` / `failed` / `cancelled`) from `attention_lost` (`expired` /
  `stale`) and the emitted-event count per generated ticket.
- **Non-authoritative stop condition:** no terminal plan displays `expired` as its
  outcome; every expired generation has a durable non-success transition (or an
  enumerated waiver). The `engineer` records the classification; the wave-level
  uniform audit is enforced only when the W-B6 child WR lands.

### W-B3 — Generation-aware ticket hygiene + orphan sweep

- **Owner:** `reviewer`. **Dependencies:** W-B2 (same sweeper/classification).
- **Evidence:** M6/M7 — `8261662` (14 builder generations: 13 expired, 1 open),
  `8261638` (builder `completed`, reviewer `expired` 09-23), `8261654` (gen-1
  completed/failed, gen-2 `cancelled` with planner-return reason, gen-3 `claimed`).
- **Evidence output:** sweep log — one row per ticket generation with
  `status`, `expires_at`, `closed_at`, `closure_reason`, and a sweep disposition
  for every open/claimed/stale row.
- **Non-authoritative stop condition:** no open/claimed generation past
  `expires_at` without a recorded sweep disposition; every generation closes with
  a reason. The `reviewer` attests the sweep log; it does not close plans.

### W-B4 — Structured start-condition gating + family links

- **Owner:** `planner`. **Dependencies:** none.
- **Evidence:** `8261652` archived as duplicate-parent; children
  `8261653`/`8261654`/`8261655` carry lineage, not gating; `8261654` was
  `IN-REWORK` until AC4/AC5 were re-scoped (planner `b728da54`, this blueprint).
- **Evidence output:** the gating spec runs
  (`bin/check_plan_gating.py`) with per-plan `gating` classes and no
  `BLOCKED-AND-OPEN` finding; the family/supersession links resolve.
- **Non-authoritative stop condition:** every W-family plan carries a structured
  start-condition block and a family link; no open builder ticket presents work
  its own ACs forbid. The prototype is evidence — schema ratification stays with
  the canonical blueprint (Architect/DBA), per planner D1.

### W-B5 — Unified vocabulary + archived visibility ratification

- **Owner:** `architect` (ratification); implementation delegated to the W-B5
  child WR. **Dependencies:** W-B1 dispositions, W-B4 family links.
- **Evidence:** M3 — canonical archive set of 6 vs `/state.archived` of 0. Root
  cause is precise: `db.ts::getPlansGroupedByStatus` now returns
  `archived` (W-B5 ruling, commit `f11f79edc`), but
  `watcher.getState()` overwrites `result.archived` with the filesystem-derived
  list, so the DB bucket is dead code at the HTTP surface.
- **Evidence output:** ratified vocabulary table (§2) + `GET /state` snapshot
  showing all 6 archived plans in `archived` and no unreachable plan.
- **Non-authoritative stop condition:** the vocabulary is published and the
  projection exposes archived + unreachable plans. The `architect` ratifies;
  the runtime change belongs to the child WR.

### W-B6 — Single generation-aware projection + expiry audit + reject routing

- **Owner:** `engineer`. **Dependencies:** 8261650 (WR canonicalization) and
  8261651 (caller repoint) — cross-reference, **do not duplicate**; plus the
  W-B2/W-B3 sweeper.
- **Evidence:** M4 (8261662 unreachable: `derived_status = NULL`), M5 (0/34
  field hydration), M6 (generation-blind ticket map), M7 (ticket-store split),
  M8 (expiry audit gap), M9 (no reject scope).
- **Evidence output:** the three slot rows `WB6-GENERATION`, `WB6-EXPIRY`,
  `WB6-REJECT`, each filled from the child WR's observed run.
- **Non-authoritative stop condition:** the three W-B6 regression fixtures
  (§5) pass against the child implementation, `/state` hydrates every
  plan field, every ticket generation has a durable expiry transition, and a
  blueprint-scope `REVIEW_REJECT` produces a Planner re-scope event instead of a
  builder ticket. The `engineer` records the fixture run; the fixtures passing in
  this blueprint's conformance harness is **not** evidence that runtime agrees.

---

## 4. Evidence slots and aggregate matrix (AC4)

### 4.1 Slot schema

Every slot names, and the checker enforces, exactly these five fields:

| Field | Meaning |
|---|---|
| `artifact_or_query` | the artifact path or read-only query that produces the evidence |
| `observed_field` | the field in the observed result that carries the verdict |
| `timestamp` | the timestamp field/source that dates the observation |
| `source_reference` | the receipt / record / table the observation is anchored to |
| `verification_owner` | the role that verifies the slot (not the package owner when the owner implements) |

No slot requires a **runtime projection-sync log**. Observations are produced by
the named query/artifact and are stamped at read time; the aggregate matrix is a
static linkage, not a synchronization trace.

### 4.2 Slot registry

| Slot | Pkg | Artifact or query | Observed field | Timestamp | Source reference | Verification owner |
|---|---|---|---|---|---|---|
| `WB1-DISPOSITION` | W-B1 | `select plan_number, status from nebula.implementation_plans where plan_number in ('0002','0005','8261653')` + `GET /state` | disposition (`complete`/`archive`/`requeue`) | `recorded_at` | winning receipt kind + `vision.tickets` row | `architect` |
| `WB2-TICKET-CLASS` | W-B2 | `select plan_id, role, status, count(*) from vision.tickets group by 1,2,3` | `terminal_outcome` vs `attention_lost` | `closed_at` | `vision.tickets` + `kernel.transition_event` | `reviewer` |
| `WB3-SWEEP-LOG` | W-B3 | `select id, plan_id, role, status, expires_at from vision.tickets where status in ('open','claimed','stale')` | `sweep_disposition` | `swept_at` | `vision.tickets` + sweep transition event | `planner` |
| `WB4-GATING` | W-B4 | `python3 bin/check_plan_gating.py bin/plan-gating-specs/w-decomposition.json` | `plans.<id>.gating` | `captured_at` (run stamp) | spec file + `nebula.receipts_unified` derived status | `dba` |
| `WB5-PROJECTION` | W-B5 | `GET /state` + `select plan_number,status from nebula.implementation_plans` | `projection_bucket` | `projected_at` | `nebula.plan_status` / `nebula.receipts_unified` | `reviewer` |
| `WB6-GENERATION` | W-B6 | `select plan_id, role, position, status from resolution.ticket order by plan_id, role, position` (fallback `vision.tickets`) | `open_generations` | `projected_at` | ticket rows + `kernel.transition_event` | `architect` |
| `WB6-EXPIRY` | W-B6 | `python3 bin/check_transition_attestation.py` + expiry-event census | `unattested` / `expiry_events` | `run_at` | `kernel.transition_event` + `vision.tickets` | `reviewer` |
| `WB6-REJECT` | W-B6 | `select kind, payload->>'scope' from resolution.receipt where kind='review_reject'` | `route` (`builder_rework_ticket`/`planner_rescope_event`) | `receipt.created_at` | `resolution.receipt` payload | `planner` |

### 4.3 Aggregate matrix

| Pkg | Slots | Package stop condition is satisfied when… |
|---|---|---|
| W-B1 | `WB1-DISPOSITION` | every M1/M2 plan has a disposition row |
| W-B2 | `WB2-TICKET-CLASS` | terminal vs attention-lost classification covers all tickets |
| W-B3 | `WB3-SWEEP-LOG` | every open/claimed generation has a disposition |
| W-B4 | `WB4-GATING` | the gating run finds no forbidden live work |
| W-B5 | `WB5-PROJECTION` | the ratified vocabulary is published; archived/unreachable plans are visible |
| W-B6 | `WB6-GENERATION`, `WB6-EXPIRY`, `WB6-REJECT` | all three W-B6 fixtures pass in the child implementation |

Matrix invariant: **all 8 slots are linked exactly once**, every linked slot
belongs to its package, and no row carries a `runtime_projection_sync_log`
requirement. The checker asserts this structure; it does not read projection
logs to do so.

---

## 5. W-B6 contract (AC5) — per architect ruling `1867c88d`

W-B6 is the only package whose specification is normative at the invariant level.
The three clauses below are binding on the child WorkRequest; this blueprint
carries the fixtures that pin them.

### 5.1 CD-1 — generation-aware `/state` projection

**Invariant:** `/state` must expose all open ticket generations. A closed older
generation must never hide a newer open row of the same role. The canonical
ticket/receipt stores remain authoritative; `/state` is a projection.

Required semantics: project per `(plan_id, role)` by **generation position**, not
by row order; expose the newest generation per role; report any open/claimed
generation that is not the newest as `hidden_open_generations` (a defect signal,
not a silent collapse). The current `watcher.getState()` role-keyed map
(`ORDER BY plan_id, role`, no generation) is the defect instance (M6).

### 5.2 CD-2 — uniform durable expiry audit for every generation

**Invariant:** Conduit core owns expiry semantics; the sweeper is the enforcement
mechanism. Every generation — including `REVIEW_REJECT` rework — must receive a
durable expiry transition/audit event at `expires_at`. Expiry must never imply
completion, and an expired unclaimed implementation ticket re-alerts/returns
according to the reject scope.

Required semantics: for each non-terminal (`open`/`claimed`/`stale`) generation
past `expires_at`, emit exactly one audit event carrying `plan_id`, `role`,
`generation`, `expires_at`, `from_status`, `to_status='expired'`,
`reason='expiry_detection'`, and `implies_completion=false`. Already-terminal
generations are untouched. Current gap: 171 expired rows vs 84 events (M8).

### 5.3 CD-3 — blueprint-scope REVIEW_REJECT → Planner re-scope

**Invariant:** classify `REVIEW_REJECT` by scope. `scope=implementation` routes
to a builder rework ticket. `scope=blueprint` routes to a **Planner re-scope /
repair event, not another builder ticket**, preserving the original evidence and
stopping the builder→review→reject loop until the Planner response is recorded.
When `scope` is absent, the documented default is `implementation` (all 338 live
reject receipts currently carry no scope — M9).

### 5.4 Regression fixtures

Fixtures are data in `bin/plan-gating-specs/w-decomposition.json`
(`decomposition.wb6_contract.fixtures`) and are asserted by
`bin/check_plan_gating.py`. They are pure input→expectation cases — no DB, no
runtime projection logs.

| Fixture | Clause | Input | Expected |
|---|---|---|---|
| `WB6-F1-hidden-generation-projection` | CD-1 | two cases: (a) open gen-2 row listed **before** completed gen-1; (b) completed gen-1 + cancelled gen-2 | (a) `open_generations = {builder: 2}`, `hidden_open_generations = []`; (b) newest projected `cancelled`, `open_generations = {}` |
| `WB6-F2-expiry-enforcement` | CD-2 | one open generation past expiry, one claimed generation inside its window, one completed generation past expiry | exactly one audit event for the expired open generation (generation carried, `implies_completion=false`), claimed untouched, terminal untouched, `every_expired_generation_has_event = true` |
| `WB6-F3-planner-return` | CD-3 | three reject receipts: `scope=blueprint`, `scope=implementation`, and scope absent | `planner_rescope_event` (no builder ticket, evidence preserved, loop stopped); `builder_rework_ticket`; absent → `implementation` default → `builder_rework_ticket` |

The checker's reference functions (`project_ticket_generations`,
`expiry_audit_events`, `route_review_reject`) exist **only** to assert these
fixtures. They are not wired into conduit-mcp; runtime implementation is the child
WorkRequest's deliverable.

---

## 6. Decomposition stop condition, linkage, and delegation (AC6, AC7)

**Decomposition stop condition (this plan):**

> W-B1–W-B6 are linked through the package registry and the aggregate evidence
> matrix; each is independently reviewable (one owner, explicit dependencies,
> evidence outputs, non-authoritative stop condition); and **no open builder
> ticket can claim the unsatisfiable end-state AC** — because the end-state AC is
> not in this plan's scope. Runtime Conduit/Nebula agreement is delegated to the
> child execution WorkRequests and is evidenced only by their filled slots.

The unsatisfiable end-state (old AC5, "Conduit and Nebula agree on plan status for
all active/pending/archived plans") is **not** an acceptance claim of this
blueprint. It is represented as the aggregate of the six package stop conditions
and the eight evidence slots, each owned by a child WR. The blueprint spec encodes
this as `stop_condition.claims_runtime_agreement = false` with
`stop_condition.delegation.runtime_agreement` naming the child WRs; the checker
rejects a blueprint that flips that flag or drops a delegation.

**Linkage:** each package's `child_work_request` field names the execution WR;
each evidence slot is linked exactly once in the aggregate matrix; the six
packages cover all nine mismatch classes in §1.2 (M1–M9).

**ST.01:** W-B has no ST.01 dependency (`delegation.st01_dependency = false`) and
may run in parallel with W-A.

---

## 7. Reproduction queries (read-only)

```sql
-- Plan surfaces
SELECT plan_number, status FROM nebula.implementation_plans ORDER BY plan_number;
SELECT id, deleted, derived_status FROM nebula.plan_status ORDER BY id;

-- Receipt chain / derived status source
SELECT plan_id, type, created_at FROM nebula.receipts_unified
 WHERE plan_id LIKE '8261654%' ORDER BY created_at;

-- Ticket generations and terminal/attention split
SELECT plan_id, role, status, count(*) FROM vision.tickets GROUP BY 1,2,3 ORDER BY 1,2,3;
SELECT count(*) FROM resolution.ticket;

-- Expiry audit gap
SELECT count(*) FROM vision.tickets WHERE status = 'expired';
SELECT count(*) FROM kernel.transition_event WHERE payload::text LIKE '%expiry_detection%';
SELECT count(*) FROM vision.tickets t
 WHERE t.status IN ('cancelled','superseded','abandoned','expired','failed','completed')
   AND t.closed_at IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM kernel.transition_event e
                    WHERE e.aggregate_type = 'ticket' AND e.aggregate_id = t.id);

-- Reject scope (currently unset on every row)
SELECT coalesce(payload->>'scope','(unset)'), count(*)
  FROM resolution.receipt WHERE kind = 'review_reject' GROUP BY 1;
```

Tools: `python3 bin/check_transition_attestation.py` (attestation drift),
`python3 bin/census_expiry_exposure.py` (expiry/re-alert yield),
`python3 bin/check_plan_gating.py bin/plan-gating-specs/w-decomposition.json`
(plan gating + blueprint conformance).
