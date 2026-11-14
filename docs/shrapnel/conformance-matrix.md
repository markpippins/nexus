# Shrapnel polymorphism and storage-adapter conformance matrix

**Plan:** 8261656 · **Project:** wrp · **Status:** staged-inert conformance
artifact — no production collection or schema migration.

**Goal.** One effective Shrapnel contract and its SolScript propositions produce
identical semantic results for equivalent PostgreSQL-JSONB and MongoDB-document
instance materializations, with authority, lineage, and a fail-closed boundary
for installations lacking both stores.

## 1. Statement of the invariant

```
effective contract (type definitions, storage-independent)
        │
        ├── PostgreSQL JSONB materialization ──┐
        │                                     ├── one normalize_instance() ──► semantic descriptor
        └── MongoDB document materialization ─┘                                      │
                                                                                     ├── type identity
                                                                                     ├── stereotype revision
                                                                                     ├── effective contract + inherited origin
                                                                                     ├── typed governed/unknown/unavailable/absent values
                                                                                     ├── lineage references
                                                                                     └── normalized evaluation fingerprint
```

The normalizer has **no backend, table, collection, or materialization branch**.
Adapters differ only in where they read the value container from. Everything
after extraction is shared, which is what makes the fingerprints comparable.

## 2. Artifact map

| Layer | Artifact | Nature |
|---|---|---|
| Normative implementation | `python/SOLScript/solscript/adapters/shrapnel_conformance.py` | staged-inert module (not wired to a loader) |
| Expression bridge | `python/expression/shrapnel_conformance.py` | deterministic projection into the evaluation model |
| TypeScript mirror | `typescript/solscript/src/shrapnel-conformance.ts` | staged-inert second-language statement |
| PostgreSQL proof | `typescript/shrapnel/conformance/pg_jsonb_proof.sql` | TEMP tables + `ROLLBACK`, outside `migrations/` |
| MongoDB proof | `MockMongoAdapter` (in-memory, ephemeral) | disposable, no collection created |
| Bounded fixtures | `docs/shrapnel/fixtures/conformance-fixtures.json` | WorkRequest family + Document, dual materializations |
| Automated proof | `bin/tests/test_shrapnel_storage_conformance.py` | bridge/parity/round-trip/unknown/lineage/fail-closed |

## 3. Acceptance matrix

| # | Acceptance criterion | How it is satisfied | Proof |
|---|---|---|---|
| 1 | Evaluation model exposes type identity, stereotype revision, effective contract, inherited field origin, typed unknown/unavailable semantics; bounded fixture covers WorkRequest base/dispatchable/tester-attestation + non-WorkRequest Document | `model_from_descriptor` emits `type_identity`, `stereotype_revision`, `effective_contract` with per-field `origin_*`/`inherited`, and `typed_values`; fixture declares revisions 1→2→3 and Document rev 4 | `test_bridge_exposes_identity_revision_contract_origin_and_typed_semantics`, `test_fixture_is_bounded_and_declares_both_materializations` |
| 2 | Equivalent JSONB/Mongo fixtures normalize to the same descriptor: identical type IDs, lineage references, governed values; no shadow store | storage-independent `type_id`; shared `build_lineage`; `normalize_instance` rejects undeclared values | `test_materializations_produce_identical_semantic_descriptors`, `test_instance_data_never_enters_type_definitions` |
| 3 | Mock JSONB/document and Mongo/document adapters yield identical disposition, origin attribution, normalized fingerprint without backend-specific branches | both adapters call `_MockAdapterBase._build_raw` → `normalize_instance`; bridge fingerprint excludes backend/object id | `test_evaluation_parity_across_materializations`, `test_normalizer_has_no_backend_branch` |
| 4 | Disposable PostgreSQL JSONB and ephemeral MongoDB proof layers preserve effective contract, typed unknown/unavailable, timestamps, lineage, lifecycle through round trips; identity remains storage-independent | mock round-trip (`write` → `read` → descriptor) twice + PG TEMP-table SELECT | `test_round_trip_preserves_contract_typing_lineage_and_timestamps`, `test_round_trip_fingerprint_is_stable_and_parity_preserving` |
| 5 | Authority separation observable: PG attestation/lineage authoritative, Mongo dispatch reconstructable, unavailable authority fails closed | `AuthorityPolicy.assess()` → `AuthorityState`; FAIL_CLOSED returns no descriptor/fingerprint | `test_fail_closed_never_promotes_local_state`, `test_authority_separation_and_dispatch_reconstruction` |
| 6 | Constrained install explicit: neither store = marked minimal fallback with declared durability/authority loss; ordinary installs keep instances outside type definitions | `AuthorityMode.MINIMAL_FALLBACK`, `durability="process_only"`, `authority_loss=(attestation,lineage,dispatch)`, `marked_fallback=True` | `test_minimal_fallback_is_marked_with_declared_loss`, `test_instance_data_never_enters_type_definitions` |
| 7 | Automated bridge/adapter-parity/round-trip/unknown/lineage/fail-closed coverage green and recorded as staged-inert artifact; no production migration | this document + module constants `STAGED_INERT=True`, `PRODUCTION_WIRING=False`; SQL proof outside `migrations/` | whole suite + `test_artifact_is_staged_inert_and_has_no_production_migration` |

Run the proof:

```bash
python3 -m pytest bin/tests/test_shrapnel_storage_conformance.py -q
```

## 4. Typed value semantics

| Status | Meaning | Value carried | Example |
|---|---|---|---|
| `governed` | the effective contract governs a materialized value; JSON `null` is a *value* | yes (may be `null`) | `attestation_payload: null` |
| `unknown` | a **required** member was not materialized; never invented | no | missing `attested_at` |
| `unavailable` | the authority that owns the member could not be read | no | dispatch fields while Mongo is down |
| `absent` | an **optional** member has no value (typed absence, not null) | no | missing `attestation_evidence_ref` |

Any non-governed status carrying a value is rejected by `TypedValue.__post_init__`.
That is the structural guarantee against local-state promotion: an unknown or
unavailable member cannot smuggle a fallback value into the descriptor.

## 5. Authority matrix

| Data | Authority | Storage | On outage |
|---|---|---|---|
| Attestation records | **PostgreSQL (authoritative)** | `shrapnel`/`resolution` evidence + OAV facts | `FAIL_CLOSED` — evaluation refused, no descriptor |
| Lineage references | **PostgreSQL (authoritative)** | revision/attestation refs | `FAIL_CLOSED` — evaluation refused, no descriptor |
| Dispatch state | **MongoDB (reconstructable, non-authoritative)** | document `dispatch` block | `DEGRADED_RECONSTRUCTABLE`; dispatch members typed `unavailable`, marked reconstructable |
| Type definitions / effective contract | Shrapnel stereotype revisions | existing PG schema | unchanged by this plan |

`dispatch_authority` is `mongo_reconstructable` in the normal separated mode:
Mongo dispatch state is never promoted to authority even when readable.

## 6. Constrained-install boundary

| PostgreSQL | MongoDB | Mode | Disposition | Declared durability | Declared authority loss |
|---|---|---|---|---|---|
| available | available | `authority_separated` | `asserted` | `postgres+mongo` | none |
| available | absent | `degraded_reconstructable` | `asserted` | `postgres_only` | dispatch freshness |
| absent | available | `fail_closed` | `refused` | `unavailable` | attestation, lineage |
| absent | absent | `minimal_fallback` | `unevaluable` | `process_only` | attestation, lineage, dispatch |

The third row is deliberate: a readable Mongo document does **not** become the
authority when PostgreSQL is gone. Only the fourth row is called a fallback, and
it is marked (`marked_fallback=True`) with its durability and loss enumerated.

## 7. Fixture inventory (`fixtures/conformance-fixtures.json`)

| Fixture | Materialization | Purpose |
|---|---|---|
| `work_request` rev 1 (depth 0) | — | base contract; source of inherited `request_kind`, `lineage_ref` |
| `work_request_dispatchable` rev 2 (depth 1) | — | adds dispatch required members; source of inherited `dispatch_lease_until` |
| `work_request_tester_attestation` rev 3 (depth 2) | — | adds attestation members; effective contract at the leaf |
| `document` rev 4 (depth 0) | — | existing non-WorkRequest scenario |
| `tester_attestation_postgres_jsonb` | PostgreSQL JSONB | decoded OAV values container |
| `tester_attestation_mongo_document` | MongoDB document | document + dispatch block, same logical values/lineage |
| `document_postgres_jsonb` | PostgreSQL JSONB | non-WorkRequest parity |
| `document_mongo_document` | MongoDB document | non-WorkRequest parity |
| `tester_attestation_missing_required` | PostgreSQL JSONB | all four typed statuses in one descriptor |

## 8. Non-goals / staged-inert declaration

- No production collection is created in MongoDB.
- No schema migration is added; `pg_jsonb_proof.sql` is outside `migrations/`
  and uses `TEMP` tables plus `ROLLBACK`.
- No loader, interpreter, or REST surface imports the conformance module.
- No historical plan artifact, audit receipt, or `nexus/audit/CONDUIT_DATA/`
  content is modified.
- The TypeScript mirror is drift-checked as text/marker parity only; it is not
  wired into `src/index.ts`.
