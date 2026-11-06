# W-C — Consumer migration: writer fencing + Stage D gates

**Plan:** 8261665 (re-run of 8261655; child of 8261652, which is superseded by 8261653/8261654/8261655)
**Docket:** `c7c26160-5b22-463f-9ee0-6e4784bae0e8` (to-do life-cycle policy)
**Wave:** W-C — post-ST.01 promotion-identity tail
**State:** decomposition complete; consumer inventory frozen; no runtime claim
**Owner of this document:** builder · **DB-layer signatory:** DBA · **Verification:** reviewer

This document is the human-readable twin of
`python/nebula/wc_consumers.py` and its checked-in artifact
`docs/promotion-flow/w-c-consumer-inventory.json`. The module is the single
source of the registry; the JSON is the deterministic serialization of it and
is drift-checked by `python/nebula/tests/test_wc_consumers.py`.

> **Why this is a re-run.** The first W-C builder run (plan 8261655) exited 0
> with no persisted artifact; the reviewer rejected it on absence, not quality
> (`40c52f84`). Plan 8261665 re-dispatches the same acceptance criteria after
> the work-evidence gate landed. The census below was re-verified live on
> 2026-10-31 against `nexus@localhost:5432` and the operator manifest corpus.

---

## 0. Boundary — what this decomposition is, and what it is not

**Is:** a frozen inventory of every consumer of promotion identity, a
four-part identity model, seven writer/read fences, nine M-sized packages with
owner + dependencies + evidence outputs + non-authoritative stop conditions,
a Stage D gate checklist per package, a findings register, and migration
runbooks.

**Is not:** a consumer migration. No package in this wave is implemented here.
The plan is a decomposition, so its deliverable is the registry and the gate
contract. Runtime outcomes belong to the child execution WorkRequests that
claim W-C1…W-C8.

**Dependency surface:** W-A (`8261653`, REVIEW_PASS, PR #780) and W-B
(`8261654`, REVIEW_PASS, PR #799) are both closed. W-C is therefore startable
as a wave; individual packages still carry their own W-A/W-B sub-dependencies
below (for example W-C3 depends on W-A3's asset-identity dispositions).

---

## 1. Identity model (AC1 baseline)

Promotion identity is deliberately **not** one column. It is a four-part key;
a consumer that binds to any other value (a mutable UUID, a snapshot of
`canonical_key`, a short display id) is deflected at its fence.

| Part | Surface | Authority | Fence |
|---|---|---|---|
| `candidate_id` | `nebula.harvest_candidates.id` / `_history.id` | immutable UUID assigned at harvest intake | F2 |
| `candidate_set_key` | `promotion_common.candidate_set_identity` / `resolution.candidate_set_key(uuid[], text)` | `sha256` over the sorted candidate-id set; order-independent | F1 |
| `asset_key` | `semantics.canonical_asset.canonical_asset_id` | `asset:nexus:nebula_harvest_candidates:<candidate_id>`; `canonical_key` is versioned **content**, never identity | F3 |
| `requirement_id` | `nebula.requirements.id` via `requirements.candidate_id` | one requirement per promoted candidate; `conduit_plan_id` is lineage, not identity | F2 |

A **consumer** is a write-path boundary, not a file. Every file that reads or
writes through the same boundary is listed once; a file that reaches a second
boundary declares it in `also_touches`. That keeps "one consumer → one package"
honest while keeping packages M-sized.

---

## 2. Live census (AC1 evidence)

Reproduce (read-only; no write branch exists):

```bash
cd nexus
PYTHONPATH=python python3 -m nebula.wc_consumers --census \
  --manifest-dir state/promotion-flow
```

The census opens a `readonly=True` PostgreSQL session, issues only `SELECT`s,
and scans the manifest directory without writing it.

| Surface / counter | Value |
|---|---:|
| `semantics.canonical_asset` total / active | 47,987 / 43,091 |
| candidate assets active (`asset:nexus:nebula_harvest_candidates:%`) | 7,456 |
| candidate assets with `canonical_key` | 4,086 |
| candidate assets without `canonical_key` | 3,370 |
| candidate assets orphaned (no matching candidate) | 3,372 |
| `nebula.harvest_candidates` total | 11,672 |
| candidates with `asset_id` / without | 4,084 / 7,588 |
| candidates `status='promoted'` / `status IS NULL` | 21 / 11,624 |
| `nebula.requirements` total / with `candidate_id` | 26 / 21 |
| requirements linked to plan `0005` | 21 |
| `nebula.cross_references` `ag:spawns_plan` edges | 26 |
| `resolution.canonical_asset` / with `canonical_key` | 13 / 0 |
| `resolution.promotion_batch` / `_candidate` | 0 / 0 |
| `resolution.receipt` (append-only) | 1,909 |
| `resolution.ticket` | 0 |
| candidate state records (`shrapnel.object_attribute_value`) | 4,396 |
| `nebula.harvest_candidate_embeddings` | 753 |
| promotion manifests (`state/promotion-flow/batch-*.json`) | 190 |
| manifest candidate entries / distinct candidates | 3,800 / 788 |
| manifests with `identity_pin` / `superseded_by` / `executed` | 190 / 141 / 1 |
| manifest candidates flagged `promoted` | 21 |

The identity counters are stable across the 2026-10-26 capture and the
2026-10-31 re-verification; only append-only counters (`resolution.receipt`)
have grown. The manifest corpus is operator-machine sediment and is absent
from a clean worktree; pass `--manifest-dir` to reproduce it from the operator
checkout.

---

## 3. Consumer inventory (AC1/AC2)

**9 packages · 68 consumers.** Every consumer appears exactly once. "Runtime"
describes observed state at census time, not permission to run.

### 3.1 W-C0 — Consumer inventory freeze + write-path census

**Owner:** builder · **Fence:** F0 · **Gate:** WC-D0 · **Deps:** —

This package is the inventory itself. It is the only package with no runtime
consumer; its evidence is the registry, the JSON artifact, and a reproducible
`--census` run. `W-C0` stops when every consumer is listed once and every live
writer is classified.

### 3.2 W-C1 — Manifest writer fence (`state/promotion-flow`)

**Owner:** engineer · **Fence:** F1 · **Gate:** WC-D1 · **Deps:** W-A1, W-C8

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-MAN-01 | Promotion-flow shared adapter | writer+reader | live | `python/promotion-flow/promotion_common.py` |
| C-MAN-02 | Stage 1 shortlist | writer+reader | dormant | `python/promotion-flow/stage1_shortlist.py` |
| C-MAN-03 | Stage 3 gated executor | writer+reader | live | `python/promotion-flow/stage3_execute.py` |
| C-MAN-04 | Duplicate reconciliation (ST.02) | writer | manual | `python/promotion-flow/reconcile_duplicates.py` |
| C-MAN-05 | Promotion identity pin (W6.04) | writer | manual | `python/promotion-flow/pin_promotion_identity.py` |
| C-MAN-06 | Card re-emit | writer | manual | `python/promotion-flow/reemit_cards.py` |
| C-MAN-07 | W-A inventory collector | reader | manual | `python/nebula/collect.py` |
| C-MAN-08 | W-A duplicate-history detector | reader | manual | `python/nebula/duplicate_history.py` |
| C-MAN-09 | Manifest sediment | data | live | `state/promotion-flow/batch-*.json` |

**Evidence outputs:** single-writer adapter conformance test, atomic-write
crash test, 190-manifest checksum parity report, grep guard (no direct
`open(..., 'w')` in `python/promotion-flow`).

**Stop condition:** exactly one manifest write function; all 190 manifests
parse; checksums unchanged except additive identity/supersession fields; a
second concurrent writer is refused by the adapter lease.

### 3.3 W-C2 — Candidate lifecycle writer fence (status + hierarchy + requirement)

**Owner:** engineer · **Fence:** F2 · **Gate:** WC-D2 · **Deps:** W-C8, DBA roles

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-CAN-01 | Stage 3 `promote()` | writer | live | `python/promotion-flow/stage3_execute.py::promote` |
| C-CAN-02 | Stage 0 hierarchy mapper | writer | live | `python/promotion-flow/stage0_map.py` |
| C-CAN-03 | Nebula spawn-requirement route | writer | live | `typescript/nebula-srv/src/routes.ts` (`POST /api/harvest-candidates/:id/spawn-requirement`) |
| C-CAN-04 | Nebula candidate PATCH route | writer | live | `typescript/nebula-srv/src/routes.ts` (`PATCH /api/harvest-candidates/:id`) |
| C-CAN-05 | Nebula promote route | writer | live | `typescript/nebula-srv/src/routes.ts` (`POST /api/harvest-candidates/:id/promote`) |
| C-CAN-06 | Nebula MCP candidate tools | writer | live | `typescript/nebula-mcp/src/tools/index.ts` + `api/nebulaClient.ts` |
| C-CAN-07 | Control-edge candidate writers | writer | dormant | `adonisjs/nexus-control-edge/app/controllers/nebula_harvest_controller.ts` + `nebula_meta_controller.ts` |
| C-CAN-08 | Moleculer nebula candidate writers | writer | dormant | `moleculer/nebula/src/routes.ts` |
| C-CAN-09 | Tackle candidate promoter | writer | manual | `python/tackle/candidate_promote.py` |
| C-CAN-10 | Tackle planner greenlight | writer | manual | `python/tackle/planner.py` |
| C-CAN-11 | Candidate status transition function | writer | live | `nebula.set_candidate_status(uuid, text)` |

**Evidence outputs:** writer census with fence status; atomic
spawn-requirement + transition test; idempotent second promote; invalid
transition refusal; 21/21 promoted ↔ requirement parity.

**Stop condition:** one path owns candidate status and requirement creation;
spawn + status commit atomically; every promoted candidate resolves to exactly
one requirement; duplicate promotes are no-ops.

### 3.4 W-C3 — Canonical-asset mint fence (`semantics.canonical_asset`)

**Owner:** dba · **Fence:** F3 · **Gate:** WC-D3 · **Deps:** W-C2, W-A3

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-AST-01 | Candidate asset backfill | writer | manual | `bin/candidate-asset-backfill.py` |
| C-AST-02 | Agent-record asset backfill | writer | manual | `bin/canonical-asset-backfill-agent-records.py` |
| C-AST-03 | Voyager adapter mint | writer | offline | `python/voyager-adapter/db.py` |
| C-AST-04 | Voyager semantics adapter mint | writer | offline | `python/voyager/src/voyager_semantics_adapter.py` |
| C-AST-05 | Knowledge-graph asset backfill | writer | manual | `bin/import-knowledge-graph.sh` + `sql/V083__graph_entities_asset_id_backfill.sql` |
| C-AST-06 | Semantics-srv canonical-asset CRUD | writer+reader | live | `typescript/semantics-srv/src/routes/semantics.ts` |
| C-AST-07 | Moleculer semantics CRUD | writer+reader | dormant | `moleculer/semantics/services/*.ts` |
| C-AST-08 | System external-id backfill | writer | manual | `bin/populate_system_external_ids.sql` |
| C-AST-09 | Canonical asset table | data | live | `semantics.canonical_asset` |
| C-AST-10 | Asset revision table | data | live | `semantics.asset_revision` |

**Evidence outputs:** mint-path inventory + grant matrix; candidate asset
reconciliation ledger (7,456 active, 3,372 orphans, 7,588 missing links);
`canonical_key` policy test; duplicate active `canonical_asset_id` count = 0.

**Stop condition:** one mint path; `canonical_asset_id` is the only identity;
zero duplicate active keys; every orphan dispositioned and every missing
candidate link backfilled or explicitly deferred; direct DML revoked from app
roles.

### 3.5 W-C4 — Resolution mirror + batch identity fence

**Owner:** dba · **Fence:** F4 · **Gate:** WC-D4 · **Deps:** W-A2…W-A7, V205, V198

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-RES-01 | W-A canonical-asset collector | reader | manual | `python/nebula/collect.py::collect_canonical_assets` |
| C-RES-02 | W-A canonical-asset detector | reader | manual | `python/nebula/duplicate_history.py::canonical_asset_sets` |
| C-RES-03 | WR repoint verifier | reader | manual | `bin/wr-repoint-verify.py` |
| C-RES-04 | Aegis invoke-log FK | writer | manual | `python/aegis/invocations.sql` |
| C-RES-05 | ST.01 batch identity functions | writer | future | `sql/V198__st01_promotion_batch_candidate_set_keys.sql` |
| C-RES-06 | Resolution canonical-asset mirror | data | live | `resolution.canonical_asset` |

**Evidence outputs:** disposition ledger for the 13 keyless mirror rows;
`wr-repoint` S3/S4 crosswalk; V198 claim/concurrency test; reader census
proving no live consumer treats the mirror as authoritative.

**Stop condition:** all 13 keyless rows dispositioned by W-A3; one ledger
writer; `promotion_batch` identity written only through V198 functions; every
reader explicitly read-only or repointed.

### 3.6 W-C5 — Service read surfaces

**Owner:** engineer · **Fence:** F5 · **Gate:** WC-D5 · **Deps:** W-C3, W-B5, W-B6

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-RD-01 | Nebula-srv asset joins | reader | live | `typescript/nebula-srv/src/routes.ts` |
| C-RD-02 | Moleculer nebula asset joins | reader | dormant | `moleculer/nebula/src/routes.ts` |
| C-RD-03 | Semantics-srv read routes | reader | live | `typescript/semantics-srv/src/routes/semantics.ts` |
| C-RD-04 | Moleculer semantics read handlers | reader | dormant | `moleculer/semantics/services/handlers.ts` |
| C-RD-05 | Control-edge semantics/meta controllers | reader | dormant | `adonisjs/nexus-control-edge/app/controllers/semantics_controller.ts` + `nebula_meta_controller.ts` |
| C-RD-06 | Nebula MCP read tools | reader | live | `typescript/nebula-mcp/src/tools/index.ts` |
| C-RD-07 | Conduit steward recall | reader | live | `typescript/conduit-mcp/src/steward-recall.ts` |
| C-RD-08 | Vision / duality UI promotion cards | reader | dormant | `angular/vision-ui` + `angular/duality-ui` |

**Evidence outputs:** consumer → query matrix; contract tests for the asset
envelope, identity claims, relations, revisions; dual-read parity snapshot on
the 21 promoted candidates plus a random control sample; W-B projection
hydration proof.

**Stop condition:** every read surface resolves promotion identity through
`canonical_asset_id`; no reader binds a mutable UUID or retired surface;
parity errors zero on the promoted set and the sampled control set.

### 3.7 W-C6 — Offline and analytical readers

**Owner:** builder · **Fence:** F5 · **Gate:** WC-D6 · **Deps:** W-C3

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-OFF-01 | Candidate state record seeder | writer+reader | manual | `bin/seed-candidate-state-records.py` |
| C-OFF-02 | Mesh reconcile | reader | manual | `bin/mesh-reconcile.py` |
| C-OFF-03 | Epistemologist observation reader | reader | offline | `python/epistemologist/db.py` |
| C-OFF-04 | Auditor observation reader | reader | offline | `python/auditor/db.py` |
| C-OFF-05 | Semantic-layer embedder | reader | manual | `bin/embed_semantic_layers.py` |
| C-OFF-06 | Promotion readiness gate | reader | live | `python/promotion-flow/promotion_gate.py` |
| C-OFF-07 | Candidate embedding batch | writer+reader | manual | `bin/batch_embed_and_match.py` |
| C-OFF-08 | Unified semantic search | reader | live | `bin/unified_semantic_search.py` |
| C-OFF-09 | Cross-schema classifier | reader | offline | `bin/cross_schema_classifier.py` |
| C-OFF-10 | Cross-schema rematcher | reader | offline | `bin/batch_rematch_cross_schema.py` |

**Evidence outputs:** script inventory with mode; read-only DSN proof for
every analytical script; identity-resolution parity report; embedding-key plan.

**Stop condition:** every offline reader resolves identity through
`canonical_asset_id` or is marked explicitly non-promotion; no script writes an
identity surface; the embedding key gap is documented and fixed or accepted
with a reason.

### 3.8 W-C7 — Downstream lineage fence

**Owner:** engineer · **Fence:** F2 · **Gate:** WC-D7 · **Deps:** W-C2, architect lineage ruling

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-DWN-01 | Requirements candidate link | data | live | `nebula.requirements.candidate_id` |
| C-DWN-02 | Candidate → plan cross-references | data | live | `nebula.cross_references` (`rel_type='ag:spawns_plan'`) |
| C-DWN-03 | Requirement → plan lineage | data | live | `nebula.requirements.conduit_plan_id` |
| C-DWN-04 | Conduit plans / WorkRequests | reader | live | conduit plan pipeline |
| C-DWN-05 | Shrapnel candidate state records | data | live | `shrapnel.object_instance` (`asset_id` member) |
| C-DWN-06 | Harvest candidate embeddings | data | live | `nebula.harvest_candidate_embeddings` |
| C-DWN-07 | Assembly promotion threads/cards | reader | live | `assembly-srv` forums (`planning`, `harvest-candidates`) |
| C-DWN-08 | Tackle requirement promoter | writer+reader | manual | `python/tackle/intent_requirement_promote.py` |

**Evidence outputs:** lineage matrix (candidate → requirement → plan/WR →
state → embedding); the 21 requirements currently parked on plan `0005`;
disposition of the 26 `ag:spawns_plan` edges; 4,396 state records and 753
embeddings keyed or re-derived.

**Stop condition:** every promoted candidate resolves to exactly one
requirement and a per-candidate plan/WR link or an explicit "no plan" state;
the five extra cross-references are dispositioned; state and embedding keys
are documented and reproducible.

### 3.9 W-C8 — Scheduler and lifecycle ops fence

**Owner:** engineer · **Fence:** F6 · **Gate:** WC-D8 · **Deps:** DBA roles

| ID | Consumer | Mode | Runtime | Path |
|---|---|---|---|---|
| C-OPS-01 | Promotion executor timer | scheduler | live | `config/systemd/user/promotion-execute.{timer,service}` |
| C-OPS-02 | Promotion mapper timer | scheduler | live | `config/systemd/user/promotion-map.{timer,service}` |
| C-OPS-03 | Promotion shortlist timer | scheduler | dormant | `config/systemd/user/promotion-shortlist.{timer,service}` |
| C-OPS-04 | Nexus-janitor clone | writer+reader | dormant | `/home/codex/dev/nexus-janitor` |
| C-OPS-05 | Out-of-band psql / DBA scripts | writer | manual | manual SQL |
| C-OPS-06 | Promotion logs | reader | live | `/tmp/promotion-{map,shortlist,execute}.log` |

**Evidence outputs:** timer/process inventory; freeze/drain transcript;
exactly-one-executor proof; alternate-runtime tombstone or gate-parity proof;
7-day soak record.

**Stop condition:** exactly one live executor per boundary; shortlist
explicitly disabled or fenced; alternate runtimes cannot write without the same
gate; a freeze drill passes and the soak window is green.

---

## 4. Writer fencing design (AC3)

Fencing is per **consumer boundary**, not per table: the fence names the single
allowed write path and the cutover that makes it atomic.

| Fence | Boundary | Rule | Enforcement | Atomic cutover |
|---|---|---|---|---|
| **F0** | inventory | read-only: `SELECT` and file scans only | `readonly=True` session; no write branch in `wc_consumers.py` | n/a (no mutation) |
| **F1** | manifests | every write goes through `promotion_common.save_manifest()`; identity/batch/supersession are append-only | atomic temp file + `os.replace`; one `candidate_set_identity`; grep guard forbids direct `open(...,'w')` | freeze timers → checksum 190 manifests → deploy adapter → dry-run one batch → parse + additive-only diff → unfreeze |
| **F2** | candidate lifecycle | status transitions go through `nebula.set_candidate_status()`; requirement creation goes through one spawn-requirement transaction | `SECURITY DEFINER` RPC + column grants; alternate routes tombstoned; duplicate promote is a no-op; invalid transitions refused | freeze schedulers → drain in-flight → install RPC + grants → replay idempotency/invalid-transition fixtures → re-enable one writer |
| **F3** | canonical asset | one mint function owns `semantics.canonical_asset`; `canonical_asset_id` is identity, `canonical_key` is versioned content | single mint function; CHECK/trigger on the id convention; INSERT-only revisions; direct DML revoked | freeze asset writers → reconcile candidate key space → deploy mint function → dual-write shadow → parity → revoke direct DML → unfreeze |
| **F4** | resolution mirror | V205 is the only disposition writer; V198 functions are the only `promotion_batch` writer | append-only triggers; no parallel minting into `resolution.canonical_asset`; W-A3 gates the mirror | apply V205 → write dispositions via DBA lane → verify chain → open read mirror → announce |
| **F5** | read compatibility | readers bind `canonical_asset_id` and immutable candidate ids; never a mutable UUID or retired table | read-only DB role; compatibility views; contract tests resolve the same identity pre/post; `expired_at IS NULL` pin | snapshot parity → deploy readers behind a flag → dual-read compare → flip reads → retire the old path |
| **F6** | process/lifecycle | exactly one live executor per boundary; dormant runtimes cannot restart against production without the same gate | systemd single-instance + timer freeze; adapter `flock`; alternate runtime tombstones; process census before/after | stop timers → drain → deploy → prove zero competing processes → start one timer → soak |

**Atomicity rule.** A cutover is atomic only when (a) the new writer is the
sole writer, (b) the old path is refused rather than merely unused, and (c)
the freeze window is bounded by an evidence transcript. A cutover that relies
on "nobody will run the old script" is not a fence.

---

## 5. Stage D gates (AC4)

Every package has one gate named `WC-D<n>`, after decision `3d03c144` (Stage D
DBA signatory gates on DB-layer steps). Each gate records **read-path**
validation, **write-path** validation, **rollback**, and **integrity**, and
names its evidence slot. DB-layer gates require operator + architect + DBA;
non-DB gates require operator + architect with an explicit DBA waiver.

| Gate | Package | DB layer | Evidence slot | Signatories |
|---|---|---|---|---|
| WC-D0 | W-C0 inventory | no | `WC0-INVENTORY` | operator, architect (+DBA waiver) |
| WC-D1 | W-C1 manifest fence | no | `WC1-MANIFEST-FENCE` | operator, architect (+DBA waiver) |
| WC-D2 | W-C2 lifecycle fence | **yes** | `WC2-LIFECYCLE-FENCE` | operator, architect, **dba** |
| WC-D3 | W-C3 asset mint fence | **yes** | `WC3-ASSET-FENCE` | operator, architect, **dba** |
| WC-D4 | W-C4 resolution fence | **yes** | `WC4-RESOLUTION-FENCE` | operator, architect, **dba** |
| WC-D5 | W-C5 read surfaces | no | `WC5-READ-PARITY` | operator, architect (+DBA waiver) |
| WC-D6 | W-C6 offline readers | no | `WC6-OFFLINE-PARITY` | operator, architect (+DBA waiver) |
| WC-D7 | W-C7 lineage | no | `WC7-LINEAGE` | operator, architect (+DBA waiver) |
| WC-D8 | W-C8 ops fence | **yes** | `WC8-OPS-FENCE` | operator, architect, **dba** |

### Gate checklist (applied per package)

- **Read path** — resolve the same identity before and after the cutover; a
  reader that changes its answer is a failed gate, not a migration artefact.
- **Write path** — prove the single writer, prove the old path is refused,
  prove a duplicate/invalid operation is a no-op or a refusal (not a second
  write).
- **Rollback** — name the restore step; rollback must not delete canonical
  history and must not require a data migration in reverse.
- **Integrity** — row-count monotonicity, preserved ids, zero duplicate active
  keys, parity counts equal to the census.
- **Evidence slot** — the child WorkRequest fills the slot with observed
  results; an empty slot cannot pass review.

The full machine-readable checklist (per-gate read/write/rollback/integrity
lists) is in `w-c-consumer-inventory.json` under `gates`.

---

## 6. Findings register (13)

Observed, not fixed. Each finding is assigned to the package that must close
it.

| ID | Severity | Pkg | Finding |
|---|---|---|---|
| WCF-01 | high | W-C2 | At least six code paths can mutate candidate promotion status; four bypass `nebula.set_candidate_status` (stage3 PATCH, tackle `candidate_promote`, tackle `planner`, control-edge `cpfPromote`). |
| WCF-02 | high | W-C2 | Stage 3 promotes in two steps (spawn-requirement, then PATCH status); a crash between them leaves a requirement without a promoted candidate, or vice versa. |
| WCF-03 | high | W-C3 | Every service connects as `pguser`, which holds INSERT/UPDATE/DELETE/TRUNCATE on `nebula.harvest_candidates_history` and `semantics.canonical_asset`; "zero direct writers" cannot be certified until distinct app roles exist. |
| WCF-04 | high | W-C3 | Identity coverage is incomplete: 7,588/11,672 candidates have no `asset_id`; 3,372 active candidate assets are orphans; 3,370 candidate assets carry no `canonical_key`. |
| WCF-05 | high | W-C4 | `resolution.canonical_asset` holds 13 rows with `canonical_key = NULL` (the W-A3 unidentified set); the mirror cannot be authoritative until W-A3 dispositions land. |
| WCF-06 | high | W-C3 | Voyager mint paths UPDATE `canonical_key` in place on conflict, while candidate backfill uses DO NOTHING — two incompatible identity policies on one table. |
| WCF-07 | medium | W-C7 | All 21 promoted-candidate requirements link to conduit plan `0005` because stage 3 hard-codes `planRef='0005'`; there is no per-candidate plan/WR lineage. |
| WCF-08 | medium | W-C8 | The shortlist timer is disabled while stage 1 writers remain in the tree; a routine "start all timers" action re-introduces a second manifest emitter. |
| WCF-09 | medium | W-C8 | Alternate runtimes (`moleculer/nebula`, `adonisjs/nexus-control-edge`) and the `nexus-janitor` clone carry competing write surfaces but are not listening; they are deployable bypasses. |
| WCF-10 | medium | W-C2 | Candidate status vocabulary has drifted: 11,624 NULL, 21 promoted, plus active/approved/rejected/superseded/struck values outside the transition function's vocabulary. |
| WCF-11 | medium | W-C2 | `nebula.set_candidate_status` exists in the live DB and the CI bootstrap dump but has no versioned V-migration; the canonical transition function can drift from the repo. |
| WCF-12 | low | W-C7 | 26 candidate→plan cross-reference edges exist for 21 promoted candidates; five are legacy/extra and have no explicit disposition. |
| WCF-13 | low | W-C7 | `harvest_candidate_embeddings` are keyed by `harvest_id + candidate_index`, not `candidate_id`; embeddings cannot be joined to promotion identity without a title/index recipe. |

---

## 7. Migration runbooks (AC5)

Runbooks are ordered by dependency. A package may not start until its
dependencies' evidence slots are filled; a DB-layer step may not start until
its Stage D gate has the DBA signatory.

### 7.1 Order

```
W-C0 inventory (this artifact)
 └─ W-C8 ops freeze (prerequisite for every live cutover)
     ├─ W-C1 manifest fence
     ├─ W-C2 lifecycle fence  ── W-C7 lineage
     │   └─ W-C3 asset mint fence (with W-A3 dispositions)
     │       ├─ W-C4 resolution mirror (W-A2…W-A7, V205/V198)
     │       ├─ W-C5 service read surfaces (W-B5/W-B6)
     │       └─ W-C6 offline/analytical readers
```

### 7.2 Standard cutover sequence (per package)

1. **Freeze** — stop the package's schedulers/processes; capture a process and
   timer census. Record the freeze transcript.
2. **Baseline** — snapshot the read contract (ids, counts, checksums) and the
   write contract (who can write, what is refused).
3. **Deploy** — install the single writer/fence behind the existing path; do
   not retire the old path yet.
4. **Dual-run** — execute the old and new paths on a bounded set; compare
   identity resolution and row counts. Any divergence stops the cutover.
5. **Atomic switch** — revoke/refuse the old path, enable the new one, and
   re-run the parity check with the old path disabled.
6. **Gate** — fill the Stage D evidence slot; DBA signs DB-layer gates;
   reviewer verifies against the acceptance criteria.
7. **Soak** — observe for the package's stated window (7 days for W-C8;
   bounded run for others). Roll back on any integrity regression.

### 7.3 Package-specific notes

- **W-C1:** checksum 190 manifests *before* touching the adapter; the diff
  after deployment must be additive only. The shortlist writer stays dormant
  until F6 proves a single emitter.
- **W-C2:** deploy the RPC and grants in the same transaction; replay the
  invalid-transition fixture before re-enabling any writer. The spawn +
  status step must be one transaction, not two HTTP calls.
- **W-C3:** reconcile the candidate key space before the mint cutover. The
  3,372 orphans and 7,588 missing links are backfill/defer decisions, not
  silent deletes.
- **W-C4:** V205 is a W-A deliverable; W-C4 may not invent a second
  disposition ledger. The 13 keyless mirror rows stay untouched until W-A3
  dispositions them.
- **W-C5:** dual-read parity runs on the 21 promoted candidates plus a random
  control sample. Readers bind `canonical_asset_id`, never the mutable UUID.
- **W-C6:** analytical scripts move to a read-only DSN first; the state seeder
  is the only sanctioned writer, and only to shrapnel state records.
- **W-C7:** the plan-`0005` hard-code and the five extra cross-references are
  explicit dispositions; do not backfill a lineage that was never recorded.
- **W-C8:** the freeze drill is the gate. A correct freeze refuses a direct
  psql write while the timers are stopped.

---

## 8. Stop conditions (AC6)

**Package stop.** Each package stops at "gate satisfied and evidence slot
filled". That is the named owner's attested stopping point for that boundary,
not a claim that the runtime is universally correct.

**Wave stop (zero data loss).** W-C is complete only when all of the following
hold:

1. Every consumer in this registry has a package with a filled evidence slot.
2. Every package's read and write parity checks pass with the old path
   disabled.
3. Source row counts never decrease; expiry/archive rows are retained.
4. Every claimed `candidate_id`, `candidate_set_key`, `asset_key`, and
   `requirement_id` is preserved and still resolvable.
5. The 21 promoted candidates each resolve to exactly one requirement.
6. All 190 manifests remain readable with additive-only changes.
7. No W-C package executes `DELETE` or `TRUNCATE` against an identity surface.
8. A re-run of `--census` reproduces the identity counters (append-only
   counters may grow).

**Rollback.** Each gate names its own restore step. Rollback never deletes
canonical history: it re-grants the previous writer and restores the prior
code path for the drain window. A rollback that requires deleting rows is a
failed design, not a recovery.

---

## 9. What this decomposition deliberately does not do

- It does **not** implement any consumer migration; W-C1…W-C8 are child
  WorkRequests.
- It does **not** delete, archive-from-disk, or rewrite historical artifacts,
  manifests, or identity rows.
- It does **not** move or modify `nexus/audit/CONDUIT_DATA/`.
- It does **not** expand `peb.decisions` authority or absorb W-A/W-B; it only
  consumes their evidence.
- It does **not** treat a dormant runtime as retired: dormant writers are
  listed because they remain deployable bypasses.
- It does **not** claim Conduit/Nebula agreement; that is W-B's evidence, and
  W-C5 consumes it.

---

## 10. Evidence outputs and reproduction

| Output | Where | Proves |
|---|---|---|
| Machine-readable registry | `docs/promotion-flow/w-c-consumer-inventory.json` | deterministic packages, consumers, fences, gates, findings |
| Registry source | `python/nebula/wc_consumers.py` | single source; `--validate`, `--census`, `--write` |
| Structural check | `python/nebula/wc_consumers.py --validate` | one package per consumer; gate/dependency integrity; no cycles |
| Drift check | `python/nebula/tests/test_wc_consumers.py` | artifact equals the registry; validation is clean |
| Live census | `--census --manifest-dir state/promotion-flow` | every snapshot number is reproducible read-only |
| Docket post | thread `c7c26160-5b22-463f-9ee0-6e4784bae0e8` | results reported per to-do-lifecycle-policy |

```bash
cd nexus
PYTHONPATH=python python3 -m nebula.wc_consumers --validate
PYTHONPATH=python python3 -m nebula.wc_consumers --census \
  --manifest-dir state/promotion-flow
PYTHONPATH=python python3 -m unittest nebula.tests.test_wc_consumers -v
```

---

## 11. Lineage

- **Plan 8261655** — first W-C decomposition attempt; REVIEW_REJECT on artifact
  absence (`40c52f84`), not on quality.
- **Plan 8261665** — this re-run, dispatched after the work-evidence gate
  landed (`9d1e0ec4` / PR #824, with receipt integrity in PR #827).
- **W-A** — `8261653`, REVIEW_PASS, PR #780; supplies the disposition ledger
  and the append-only invariants W-C4 consumes.
- **W-B** — `8261654`, REVIEW_PASS, PR #799; supplies the unified visibility
  vocabulary and projection surfaces W-C5 consumes.
- **Docket** — `c7c26160-5b22-463f-9ee0-6e4784bae0e8`; binding constraints 1–5
  are applied throughout (append-only, no authority expansion, I1/I2, owner +
  dependencies + evidence + stop, DB-layer via DBA).
