# W-A — Duplicate-history reconciliation: M-sized package decomposition

**Plan:** 8261653 (child of 8261652; parent superseded by 8261653/8261654/8261655)
**Docket:** `c7c26160-5b22-463f-9ee0-6e4784bae0e8` (to-do life-cycle policy)
**Wave:** W-A — post-ST.01 promotion-identity tail
**State:** decomposition complete; inventory executable; ledger surfaces authored (V205, **not yet applied**)
**Owner of this document:** builder · **DB-layer signatory:** DBA · **Verification:** reviewer

---

## 0. Start condition — satisfied

| Gate | Evidence | Status |
|---|---|---|
| ST.01 — deterministic candidate-set identity + atomic batch claim | V198 live (`resolution.promotion_batch` exists), merged PR #493 | **met** |
| Envelope freeze C1–C4 | DBA record `e8faa405` | **met** |
| Parent plan 8261652 | superseded by children 8261653/8261654/8261655 | **no open parent dependency** |

`resolution.promotion_batch` exists but is **empty** at census time (0 rows), which is the
ratified ST.01 pin window — W-A does not depend on batch history being populated.

---

## 1. What W-A reconciles, and what it may never do

A **duplicate-history set** is a group of historical records that share an identity and
therefore cannot both stand as the canonical record — plus the two structurally identical
families that need an explicit disposition for the same reason:

| Set kind | Meaning | Why it needs a package |
|---|---|---|
| `duplicate` | two or more records share an identity | exactly one may be canonical; the rest need a disposition |
| `uncovered` | legacy rows carry **no** disposition at all | the wrap-not-drop lane has a gap (C4/C6) |
| `unidentified` | the identity column is unusable (NULL/empty) | the set cannot even be grouped until identity exists (C2) |
| `unverified` | identity exists but is not verifiable | drift must be surfaced, never silently normalized (C1) |

**Invariants (binding):**

1. **Append-only, wrap-not-drop.** Reconciliation reconciles *pointers and dispositions*.
   No record, receipt, ticket, manifest, candidate or asset is deleted or rewritten.
2. **Exactly three dispositions** — `merge`, `preserve`, `archive`. There is no fourth,
   and `discard` is not silence: a discarded legacy row is `archive`.
3. **A re-decision is a new generation, never an update.** The ledger and the fingerprint
   trail refuse `UPDATE`/`DELETE` at the database level (V205 triggers).
4. **Divergence is signal.** If two records share an identity but their content
   fingerprints differ, the duplicate is `preserve`, never `merge`.
5. **No authority expansion.** W-A does not touch `peb.decisions`, does not move
   `nexus/audit/CONDUIT_DATA/`, and does not alter canonical rows.
6. **DB-layer steps route through the DBA** (docket constraint 5). The builder emits the
   inventory and the exact SQL; the DBA applies it and signs the ledger row.

---

## 2. Evidence base (live census, 2026-10-22)

Reproduce with:

```bash
cd nexus
PYTHONPATH=python python3 -m nebula.cli inventory --json /tmp/wa-inventory.json
```

Census header recorded in the inventory:

| Surface | Rows |
|---|---:|
| `promotion-manifest` files (`state/promotion-flow/batch-*.json`) | 190 (0 undecodable) |
| `resolution.receipt` | 1,880 |
| `resolution.canonical_asset` | 13 |
| `resolution.execution_claim` | 114 |
| `resolution.keychain_event_outbox` | 334 |
| `resolution.migration_disposition` | 2,621 |
| `resolution.promotion_batch` / `_candidate` | 0 / 0 |
| `resolution.candidate` / `resolution.ticket` | 0 / 0 |
| `vision.receipts` | 1,893 |
| `vision.tickets` | 775 |

Inventory result: **100 duplicate-history sets → 100 packages, 528 members, all 100 open**
(nothing has been dispositioned yet; V205 is not applied).

> **Corpus provenance.** `state/promotion-flow/` is local, *untracked* pipeline state
> (190 manifests at census time). A fresh checkout that does not carry that sediment
> yields **90** sets — the 10 W-A1 manifest sets simply do not exist there. The detector
> and the package contract are identical either way; only the W-A1 family is
> corpus-dependent. The census below is the operator-machine snapshot, not a claim that
> the manifests are part of the repository.

| Family | Surface | Kind | Condition | Sets | Members | Open |
|---|---|---|---:|---:|---:|---:|
| W-A1 | `promotion-manifest` | duplicate | ST.02 | 10 | 151 | 10 |
| W-A2 | `resolution.receipt` | unverified | C1 | 0 | 0 | 0 |
| W-A3 | `resolution.canonical_asset` | unidentified | C2 | 1 | 13 | 1 |
| W-A4 | `resolution.execution_claim` | unverified | C3 | 1 | 1 | 1 |
| W-A5 | `resolution.keychain_event_outbox` | duplicate | PC7 | 1 | 2 | 1 |
| W-A6 | `resolution.migration_disposition` | unverified | C4 | 77 | 314 | 77 |
| W-A7 | `vision.receipts` + `vision.tickets` | uncovered | C4 / C6 | 10 | 47 | 10 |

**W-A2 currently has no sets** — every receipt in the census has a fingerprint that
recomputes correctly. The package still ships because the detector is the guard that
keeps that statement true; it re-runs after the envelope freeze is applied.

---

## 3. Package model — one set, one atomic package

Every detected set derives a deterministic package id:

```
W-A/<first 12 hex chars of the set id>
set id = sha256(canonical_json([surface, kind, condition, identity_kind, identity_value]))
```

The id is stable across runs, machines, and set order (the inventory sorts by set id and
breaks member ties by `created_at`, then record ref). The same inputs therefore produce a
byte-identical inventory and a byte-identical disposition log.

The **seven family packages below are the scheduling envelopes** (owner, dependencies,
evidence, stop condition). The 100 per-set ids are the atomic units: a family is done only
when every one of its per-set packages is dispositioned.

---

## 4. M-sized packages

Each package is independently reviewable, has one accountable owner, and has a stop
condition that is *evidence-based, not aspirational*. "Prepare" work (inventory + log +
SQL) is builder-owned; "apply" work (ledger rows) is DBA-owned.

### W-A1 — Promotion-manifest duplicates (ST.02 sediment)

| Field | Value |
|---|---|
| **Sets / members** | 10 / 151 |
| **Owner** | builder (inventory + log); DBA applies |
| **Dependencies** | ST.02 precedent (complete); V205 applied |
| **Inputs** | `state/promotion-flow/batch-*.json`, 190 files |
| **Disposition rule** | earliest emission → `preserve`; content-identical duplicate → `merge`; already `superseded_by` → `archive`; diverged → `preserve` |
| **Evidence outputs** | inventory slice; reconciliation JSONL entries; `reconcile --sql` statements; DBA ledger receipt; fingerprint-audit rows |
| **Stop condition** | every W-A1 set has `dispositioned = true` in a re-run inventory, the log chain verifies, and the DBA ledger count equals the log's W-A1 entry count |

### W-A2 — Receipt fingerprint verification (C1)

| Field | Value |
|---|---|
| **Sets / members** | 0 / 0 at census (guard package) |
| **Owner** | DBA (fingerprint recomputation is DB-layer); builder supplies inventory |
| **Dependencies** | envelope freeze C1; V205 applied |
| **Inputs** | `resolution.receipt.payload`, `payload_fingerprint` |
| **Disposition rule** | a mismatched/missing fingerprint is `unverified` and `preserve` until the DBA proves the canonical value; never silently rewritten |
| **Evidence outputs** | zero-unverified inventory receipt; re-run census; fingerprint-audit rows |
| **Stop condition** | `receipt_sets()` yields **zero** `unverified` sets in a re-run after the C1 DB-layer verification is live |

### W-A3 — Canonical-asset identity (C2)

| Field | Value |
|---|---|
| **Sets / members** | 1 / 13 |
| **Owner** | DBA (identity backfill); builder supplies inventory |
| **Dependencies** | C2 `canonical_key` backfill at envelope-apply time; V205 applied |
| **Inputs** | `resolution.canonical_asset.canonical_key` (JSONB) |
| **Disposition rule** | one asset per key → `preserve`; extra assets sharing a key are `preserve` until the DBA proves content identity, then `merge`; NULL/empty keys are `unidentified` and stay `preserve` |
| **Evidence outputs** | key backfill evidence; inventory slice; fingerprint-audit rows |
| **Stop condition** | zero `unidentified` sets **and** every duplicate-key set dispositioned |

### W-A4 — Execution-claim lineage (C3)

| Field | Value |
|---|---|
| **Sets / members** | 1 / 1 |
| **Owner** | DBA (normalized link); builder supplies inventory |
| **Dependencies** | C3 `subject_ref` → normalized `attempt_id` link; V205 applied |
| **Inputs** | `resolution.execution_claim.subject_ref` (JSONB), `attempt_id` |
| **Disposition rule** | JSONB and column agree → `preserve`; disagree or empty → `unverified`/`preserve` until the DBA reconciles the link |
| **Evidence outputs** | divergence inventory slice; normalized-link evidence; fingerprint-audit rows |
| **Stop condition** | zero `unverified` claim sets |

### W-A5 — Keychain outbox read-set collisions (PC7)

| Field | Value |
|---|---|
| **Sets / members** | 1 / 2 |
| **Owner** | DBA (outbox payload lineage); builder supplies inventory |
| **Dependencies** | PC7 JSONB classification; V205 applied |
| **Inputs** | `resolution.keychain_event_outbox.read_set` |
| **Disposition rule** | identical `read_set` → later row `merge` or `archive` after delivery evidence; divergent → `preserve` |
| **Evidence outputs** | collision inventory slice; delivery/checkpoint evidence; fingerprint-audit rows |
| **Stop condition** | every PC7 set dispositioned |

### W-A6 — Disposition fingerprint-audit gap (C4)

| Field | Value |
|---|---|
| **Sets / members** | 77 / 314 |
| **Owner** | builder (backfill inventory + log); DBA applies |
| **Dependencies** | V205 applied (creates `resolution.fingerprint_audit`) |
| **Inputs** | `resolution.migration_disposition.target_refs` (2,621 rows; 77 groups share one identical value) |
| **Disposition rule** | each legacy disposition row gets its own fingerprint; shared `target_refs` rows are `preserve` (they are distinct sources, not duplicates to collapse) |
| **Evidence outputs** | one fingerprint per audited row in `resolution.fingerprint_audit`; inventory slice; chain-verified log |
| **Stop condition** | zero groups where an identical `target_refs` cannot be distinguished by a per-row fingerprint |

> W-A6 is deliberately ordered **before** W-A7: coverage dispositions are only provable
> when the ledger can name the row it dispositioned.

### W-A7 — Legacy flat coverage (C4 receipts / C6 tickets)

| Field | Value |
|---|---|
| **Sets / members** | 10 / 47 (6 receipts, 41 tickets) |
| **Owner** | DBA (disposition rows); builder supplies inventory |
| **Dependencies** | W-A6 (per-row fingerprint trail); V205 applied |
| **Inputs** | `vision.receipts` (1,893), `vision.tickets` (775), `resolution.migration_disposition` |
| **Disposition rule** | every uncovered row gets an explicit row — live rows `preserve`, terminal/expired rows `archive`; no row is left silent |
| **Evidence outputs** | one disposition per uncovered row; coverage query (`uncovered = 0`); fingerprint-audit rows |
| **Stop condition** | `legacy_coverage_sets()` returns zero sets for both lanes |

---

## 5. Disposition policy (single source of truth)

The policy is implemented once in
`python/nebula/reconciliation_log.py::dispositions_for` and mirrors the three-value
contract:

| Situation | Disposition | Rationale |
|---|---|---|
| first member of a set (the primary) | `preserve` | it is the surviving record |
| duplicate, content-identical, no supersession marker | `merge` | fingerprint already proves identity |
| duplicate, content diverged | `preserve` | divergence is signal, never overwritten |
| duplicate already marked `superseded_by` | `archive` | retired from active views, retained as history |
| `uncovered` / `unidentified` / `unverified` member | `preserve` | no proven primary — kept open for judgement |

`merge` requires a distinct `primary_ref`; `archive`/`preserve` require none; every
disposition requires a non-empty `reason`. These are enforced in **both** layers:
`ReconciliationLog.append()` (builder side) and
`resolution.record_reconciliation_disposition()` (database side).

---

## 6. Evidence outputs (what a reviewer can inspect)

| Output | Where | Proves |
|---|---|---|
| Inventory JSON (`--json`) | operator-chosen path; `/tmp/wa-inventory.json` in this census | deterministic set/package ids, member fingerprints, census header |
| Reconciliation log (JSONL) | operator-chosen path (`reconcile --log`) | append-only dispositions; each entry hash-chains its predecessor |
| Chain verification | `python3 -m nebula.cli verify-log --log <path>` | no entry was rewritten or removed |
| DBA-lane SQL | `python3 -m nebula.cli sql --log <path>` | exact `INSERT`s for `reconciliation_set` / `reconciliation_disposition` / `fingerprint_audit` |
| Ledger migration | `sql/V205__w_a_duplicate_history_reconciliation.sql` | three surfaces + append-only triggers |
| Ledger provenance | `sql/migration_ledger_retro_V205.sql` | the retro-ledger text the DBA inserts |
| E2E proof | `python/nebula/tests/test_v205_w_a_reconciliation_e2e.py` | apply, constraints, append-only, status transitions, generations, fingerprints |
| Detector proof | `python/nebula/tests/test_duplicate_history.py`, `test_reconciliation_log.py` | grouping, identity agreement with production, determinism, chain integrity |

Apply path (DBA lane only):

```bash
psql -d nexus -f sql/V205__w_a_duplicate_history_reconciliation.sql
# then, from the builder-supplied log:
python3 -m nebula.cli sql --log <path> | psql -d nexus
```

---

## 7. Global stop condition

W-A is complete when **all** of the following hold:

1. `python3 -m nebula.cli inventory` reports `open_package_count = 0` (all 100 sets
   dispositioned, including every primary).
2. `verify-log` reports `chain verify: OK` for the reconciliation log.
3. V205 is applied and the DBA ledger count equals the number of dispositions in the log
   (no builder-side disposition without a ledger row, and none the other way).
4. W-A6's fingerprint trail individually addresses every row it dispositioned.
5. No record was deleted: the census row counts for the source surfaces are unchanged
   (append-only proof).

**Non-authoritative stop.** A package stops at *dispositioned*, not at *resolved*. Records
whose content genuinely diverges are `preserve`d and remain a visible, open question for
the owning role — W-A does not manufacture consensus, and a divergence surviving the wave
is a correct outcome, not an incomplete one.

**Rollback.** The ledger holds only reconciliation evidence. Dropping the three V205
surfaces discards no canonical history; the rollback sketch is at the tail of the
migration. The builder-side log is append-only and must never be truncated (a superseding
log is a new file, not an edit).

---

## 8. Execution order

```
D1  DBA applies V205 (empty ledger surfaces)          ← blocking for all applies
 ├─ W-A2 (C1 receipt guard)        ─┐
 ├─ W-A3 (C2 asset identity)        │ prepare in parallel after D1
 ├─ W-A4 (C3 claim lineage)         │
 ├─ W-A5 (PC7 outbox collisions)   ─┘
 ├─ W-A6 (C4 fingerprint trail)     ← before W-A7
 ├─ W-A7 (C4/C6 legacy coverage)    ← depends on W-A6
 └─ W-A1 (ST.02 manifest sediment)  ← independent; may run first
```

Each package's deliverable is a DBA-applied, chain-verified disposition set plus a re-run
inventory showing its sets closed. No package edits canonical rows, and no package may be
started by widening its scope: a set that does not appear in the inventory is out of W-A.

---

## 9. What this decomposition deliberately does not do

- It does **not** apply V205 or insert dispositions (DBA lane, per docket constraint 5).
- It does **not** delete, archive-from-disk, or rewrite any historical artifact.
- It does **not** decide divergent-content questions; those are `preserve`d and named.
- It does **not** claim the `nexus/sql/migrations/` directory as the V-series home; the
  migration is flat at `nexus/sql/` for the reasons recorded in
  `nexus/sql/migrations/README.md`.
- It does **not** expand `peb.decisions` authority or absorb W-B/W-C.
