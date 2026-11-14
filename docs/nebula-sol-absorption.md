# Nebula → SOL absorption: classification, then executable representations

**Status:** ratified design note (Phase 1).
**Measured:** 2026-11-12, re-verified 2026-11-13, against live `nexus`
(PostgreSQL 17.11). Counts are point-in-time; the table in §2 records the
re-verified figures.
**Plan:** approved 2026-11-12 in the Nebula/SOL absorption thread.

Three states are kept distinct throughout, because conflating them is how a
registry starts overclaiming:

| State | Meaning |
|---|---|
| **Reviewed text** | this note — a decision, not a capability |
| **Merged code** | Python/TypeScript on `main` — runs, but may take a legacy path |
| **Applied schema** | live DDL/data — what the running system can actually resolve |

Phase 2 (`sql/V216`) was **applied live on 2026-11-13** (ledger
`V216__nebula_sol_concept_registry`, applied by the DBA; verified post-state:
44 concepts, 20 identity strategies, 31 representations, 22 representation
identities, 72 attributes, 56 values, zero bindings). Phase 3 (`sql/V217`) was
**applied live on 2026-11-13** (ledger
`V217__representation_scoped_bindings`; PR #993 merge `60be1daf8`; DBA closeout
`7b103e30`). The selector and representation-scoped bindings are live; the
Python entity read paths require V217 and no longer carry the schema
allow-list fallback. Six representations are executable, one per concept:
Answer, OpenQuestion, Requirement, ImplementationPlan, WorkRequest and
WorkRequestEdge.

This note exists to stop a specific drift: registering a new durable
representation inside `resolution` because that is where SOL can currently read
it, then moving it to `nebula` later. WorkRequest already took that path. The
rule below makes the conceptual home the decision, and the physical home a
separate, reversible choice.

---

## 1. Decision rule

1. **Concept/representation is the default registration mechanism**, including
   for Tickets and Receipts. Domain nouns are not modelled as instances of a
   generic type/instance table, and a physical table keeps its own name.
2. **Collapse two tables only when they share lifecycle, authority, temporal
   semantics and shape.** A name match is not evidence. Differing intent labels
   over the same columns are one concept with a governed discriminator.
3. **Operational data lives in `nebula`.** `resolution` keeps the SOL evaluation
   model and the currently-canonical ticket/receipt/work_request tables, with no
   relocation in this effort.
4. **No new `resolution` domain tables.** New durable representations land in
   `nebula` (or their owning authority schema) and are *registered*, not copied.
5. **A representation that SOL cannot execute must say so.** Describing a
   representation is not the same as making it evaluable; the registry must not
   imply the second (see §4).

`vision.canonical_work_requests` (V170) is a draft in the wrong schema and is
withdrawn (see §6.7). Its relational-spine + `payload jsonb` shape is kept as the
migration template for the eventual `nebula` home.

---

## 2. Measured overlap with the agent-record types

`nebula.agent_records` is a **view** over `nebula.agent_records_history`
(current bitemporal slice). It holds **16,746** rows across the nine allowed
`record_type` values (a `CHECK` constraint on the history table, re-verified
2026-11-14). At the time of the original measurement (2026-11-13, before V216
was applied) no `Decision`, `Ticket`, `Receipt`, `AgentRecord`, `Report`,
`Inspection` or `Analysis` concept existed in `resolution.concept` and the only
matching concept among those names was `Assessment`. That sentence is now
superseded by the Phase 2 migration: `Ticket`, `Receipt`, `AgentRecord`,
`Decision`, `Agenda`, `AgendaItem` and `Assessment` are all registered as
concepts today (§6). The classification in this section is unchanged — what
changed is that the registry now *describes* the overlap instead of being
silent about it.

| `record_type` | Rows | Existing shape | Relationship |
|---|---:|---|---|
| `report` | 10,554 | none | No domain shape |
| `engineering_log` | 2,809 | `nebula.work_sessions` (6), `nebula.executions` (0) | Operational adjacency only; not governance |
| `response` | 644 | `Answer` concept → `resolution.open_question_answer` (0) | Partial — an answer is a settled statement, a response is narrative |
| `analysis` | 633 | `resolution.observation` (6), `nebula.observations` (0) | Adjacent: an observation is an event/finding, an analysis is reasoning over it |
| `assessment` | 612 | `nebula.assessments` (0), `resolution.assessment` (1) | **Same noun, different payload** — see §2.1 |
| `decision` | 530 | `peb.decisions` (16 ADRs) | **Same artifact, two representations, no identity link** — see §2.2 |
| `architecture_note` | 480 | `nebula.specifications` (1); `architect_specs` views in `nebula` and `scratch` | Adjacent: a specification is settled, a note is rationale |
| `inspection` | 351 | none named; assessment/attestation-shaped | **Subtype, not a table** |
| `prompt` | 133 | operator prompt store | Weak; provenance-only, never keychained |

Row counts are a snapshot, not a stable contract; the *relationship* column is
the claim this note is responsible for. `resolution.ticket` currently holds 0
rows and `resolution.receipt` 1,998 — the tables are canonical, the near-empty
one is still the physical home named by the plan.

### 2.1 Assessment family

`nebula.assessments` already has the JSONB + FK shape the plan asks for:
`outcome`, `confidence`, `impact_scope jsonb`, `open_questions jsonb`,
`analysis_detail`, plus `observation_id`, `agenda_id`, `auto_resolve_plan_id`
and `forum_post_id`. It holds **0 rows**, so adopting it has no backfill risk.

`resolution.assessment` holds 1 row and carries a different payload
(`rationale`, `dimensions_used`, `dimensions_total`, `resolved_at`). That is a
representation difference, not a second concept.

`inspection` (351 records) has no table of its own. It is assessment-shaped:
subject → finding → confidence → evidence. It becomes
`assessment_kind = 'inspection'`, not `nebula.inspections`.

Two neighbours stay separate because their lifecycles differ:

- `nebula.attestations` (10) is a chain artifact — `work_ref`, `attester_role`,
  `authority_role`, `evidence`, `cites_id`, `txid`. Authority and citation are
  part of its identity.
- `nebula.requirement_verifications` (0) expires: `expires_at`, `conditions`,
  `role`, `status`. Expiry makes it a different lifecycle from an assessment.

### 2.2 Decisions already use concept/representation in practice

`peb.decisions` holds **16** accepted ADRs (all rows `status = 'accepted'`).
ADR-015 is titled *"Architecture Decision Records live in PEB, not Nebula"* —
the authority home is already decided. **Fourteen** of the sixteen ADR titles
match a `decision` agent record; the match holds under raw equality as well as
case/trim-normalised equality, so it is not an artefact of the comparison.
**Zero** ids are shared. The pairing is real but unlinked:

| ADR | `peb.decisions.id` | `nebula.agent_records.id` |
|---|---|---|
| ADR-002 | `eacfd067-46af-4942-9d5a-3781c5bf0b4d` | `a0f11918-4838-4b7c-bbaf-92eae481438c` |
| ADR-003 | `0e5ff73a-18f1-4a3c-b9e4-aa8fb5944bc6` | `94e92e11-d9c3-4213-9ac2-07ef49f349c4` |
| ADR-004 | `fe1e01eb-7ad7-42e4-b168-00c7f068b6ae` | `278ad16e-0e87-456b-8006-e118a893fcea` |
| ADR-005 | `c942dbc8-697e-4eb5-9e53-5803dc5f3938` | `1b3b3a5d-5506-4b83-beaf-96dd2322757b` |
| ADR-006 | `dfcab57b-8598-4dc4-8682-f0c70cf5b78c` | `bc1087a7-4a30-4bba-bf1f-7258787cd90c` |
| ADR-007 | `59393114-0138-44dc-bec2-5939adec0b15` | `dbfb2e5c-bfd8-44c4-9768-f6c560702e86` |
| ADR-008 | `7545a428-c16d-438c-bf4d-f6131ac45352` | `9a158843-f71d-4d05-9d08-b5e9172f769b` |
| ADR-009 | `81b1f0dc-fdca-448c-a735-20a3566c4a83` | `03a4532a-8ef3-4f9c-a7f9-536f4c1fa77f` |
| ADR-010 | `12c9e44a-2542-47a5-8ad2-8d32e32ceed2` | `81d329df-a2a3-4802-8734-0499875991ff` |
| ADR-011 | `e7377b19-3a86-4bea-a825-0fc8072d9e5e` | `118a7f78-7607-492c-ad2b-dac8ae77c923` |
| ADR-012 | `2ed4d43d-36d8-4e25-9ae8-ee9aa2469f37` | `d708c452-b8b1-4589-af99-064d75185ec9` |
| ADR-013 | `cf55c14e-e2c0-4b42-a688-830954bc1843` | `196b2661-a280-465a-ab86-6ef6d8c4079e` |
| ADR-014 | `4a05e52d-3090-46e2-89f6-bd6530d12cb3` | `34560cd5-895e-4537-9e39-be700370f947` |
| ADR-015 | `c768f04d-961b-4feb-99f4-926d7c0d7643` | `f09aa36f-b05c-4f36-b7bc-34100a725ec8` |

ADR-001 and ADR-016 have no matching agent record. No merge, rewrite or
backfill is authorised by this note; Phase 5 adds the link.

---

## 3. Classification: which shape, which mechanism

| Surface | Mechanism | Decision |
|---|---|---|
| `peb.decisions` + `nebula.agent_records(decision)` | **Concept/representation** — authority + narrative | `Decision` concept, two representations. No new table. |
| `nebula.assessments` | **JSONB + FKs** (already present) | Operational `Assessment` table; absorbs `inspection` as a kind. 0 rows. |
| `resolution.assessment` | **Concept/representation** | Second representation of `Assessment`; evaluation shape, not operational home. |
| `nebula.attestations` | **Concept/representation** | Keep the V179 chain; do not merge. |
| `nebula.requirement_verifications` | **Concept/representation** | Keep the expiry lifecycle. |
| `nebula.agent_records` | **Concept/representation + existing JSONB/FKs** | `AgentRecord` for the narrative family; `record_type` is a governed value, not a table. |
| `resolution.ticket` | **Concept/representation** | Table already carries the right name; register, do not relocate. |
| `resolution.receipt` | **Concept/representation** | Producer-registered, kind-discriminated, append-only; register, do not relocate. |
| `nebula.agendas` / `nebula.agenda_items` | **TypeSpec + Concept/representation** | Deliberation contract; JSONB already present. No new table. |
| `resolution.work_request` | **Concept/representation** | Keep as the temporary physical home; explicit deferral to `nebula`. |
| Shrapnel EAV | **Index/projection only** | Never authority. The V152 bridge admits seven named members; a generic EAV property is not a concept attribute. |
| MongoDB (`python/keychains`, `atomic-mongodb`) | **Index/projection only** | One-way `PG → Mongo`; instance storage and context, never authority, never evaluated by SOL (see §4 item 5). |

The narrative family (`report`, `analysis`, `architecture_note`,
`engineering_log`, `prompt`, `response`) shares `AgentRecord`. They differ only
by intent label; lifecycle, authority, temporal columns and foreign keys are the
same. Collapsing them is the doctrine working as intended, not a trick.

`Ticket` and `Receipt` do **not** collapse into a generic operational-artifact
concept: `ticket` carries `workflow_ref`, `role`, `position`, `status`,
`predecessor_receipt_id`, `generation`; `receipt` is producer-registered and
append-only with a `kind` discriminator. Different lifecycles, different
authorities, different tables — and both already have the right names.

---

## 4. What SOL cannot do today (non-capabilities)

These are properties of the current implementation, not of the registry. Each
is stated against its real state: **merged code** or **applied schema**.

| # | Non-capability | Evidence |
|---|---|---|
| 1 | **In-memory SOL reads only executable representations.** | `DatabaseLoader.load_entities` filters `evaluation_role = 'executable'`; the old `if schema not in ("resolution",): continue` allow-list is gone. A `nebula`/`peb` representation is materialised only when it is executable; descriptive representations remain described, not evaluated. |
| 2 | **Subject listing uses the same executable selector.** | `SolNativeAdapter.list_subjects` reads only executable representations, rejects an unknown `read_adapter`, and fails closed when more than one executable representation claims a concept without an explicit selection. |
| 3 | **Bindings are representation-scoped.** | V217 replaces the old `PRIMARY KEY (attribute_id)` / `PRIMARY KEY (concept_relationship_id)` keys with surrogate ids, nullable `representation_id`, and partial unique indexes on `(representation_id, attribute_id)` / `(representation_id, concept_relationship_id)` plus the legacy NULL leg. A concept still has one executable realization per attribute; other representations stay descriptive. |
| 4 | **No cross-database reads.** | `compile_root`/`compile_condition` emit `%I.%I` from the binding: any table in *this* database with an `id` compiles, but there is no FDW, no second DSN, and no remote query path. |
| 5 | **No Mongo evaluation.** | Nothing in the SQL compiler or `DatabaseLoader` reads MongoDB. `python/keychains` is an explicit one-way `PG → Mongo` projection and Mongo is never read back as operational state (D-2026-08-14-003); Mongo is not a representation SOL can resolve. |
| 6 | **JSONB interiors need function bindings.** | `attribute_ref` compiles the whole column. A key inside `impact_scope` or `open_questions` needs an allow-listed `function_binding` template or a generated column. Interpolating a path from concept metadata is unsupported and must not be added — the template, not the metadata, is the trust boundary. |

Two supporting facts that constrain the registry rather than block it:

- **Bindings are hand-authored, never inferred.** Both migration headers say so;
  Phase 3 keeps that stance and only scopes an existing binding to a
  representation. Nothing derives a binding from a table's columns.
- **Shrapnel storage is generic; Shrapnel evaluation is not.** The in-memory
  loader materialises every EAV object as a `ShrapnelFact` (4,399 objects live),
  but the DB-side bridge (`sql/V152__shrapnel_bridge_readthrough.sql`) is a fixed
  seven-member allow-list keyed on `asset_id`, the generic loader registers no
  `ConceptAttribute`s for EAV property names, and a new member or subject needs a
  bespoke bridge. Storing in Shrapnel does not make a property evaluable.

The consequence for this strategy: "registered representation" means
**described**, not **evaluable**. Phase 2 registers several representations that
SOL cannot yet read; that is deliberate and recorded here so the registry does
not overclaim. Phase 3 is applied live (ledger
`V217__representation_scoped_bindings`, 2026-11-13); executable representations
are read through the flag, not through a schema allow-list, while descriptive
representations remain described and are not materialised.

---

## 5. Keychain citable set

The keychain cites governed conclusions, not every artifact:

- `decision` — the ruling, and its `peb.decisions` authority row
- `assessment`, including `inspection` — findings with confidence
- `analysis` and `architecture_note` — reasoning that supports a ruling
- cited `report` — only when a decision or assessment cites it

Explicitly **not** citable: `prompt`, `response`, `engineering_log`. They are
provenance, and `prompt`/`response` are immutable history under I4.

**Boundary, not current behaviour.** This is a policy the plan sets, and two
limits on the claim should be stated plainly:

- Nothing enforces it today. `python/keychains/projection.py` has no
  `record_type` filter — `_state_vector` groups by whatever record types the
  records carry, and the store is a one-way projection, never authority.
- The nearest written proposal (`docs/architecture-reports-keychain-index.md`
  §9, 2026-10-31) proposes a wider set: "decisions, attestations/assessments,
  inspections, notes, and only the reports a decision cites". The plan's set
  above is the narrower, ratified one; **attestations are deliberately excluded
  here**, and reconciling that difference belongs to the keychain work, not to
  this absorption effort.

---

## 6. Phase status

| Phase | Status |
|---|---|
| 1. Design note | **This document.** |
| 2. Registry completion | **`sql/V216__nebula_sol_concept_registry.sql`**, merged in PR #980 (`3b99da08a`) and **applied live 2026-11-13**. Concepts, identity strategies, representations, identities and governed values only; no DDL, no data movement, zero bindings. The six new concepts are present; the existing `Assessment` registration is preserved. Ledger `V216__nebula_sol_concept_registry`. Procedure: `docs/runbook-v216-sol-registry-live-apply.md`. |
| 3. Executable representations | **Applied live 2026-11-13.** `sql/V217__representation_scoped_bindings.sql` and its Python/TS consumers merged as PR #982 (`7914983a6`) plus the fallback removal in PR #993 (`60be1daf8`); ledger `V217__representation_scoped_bindings`; selector and scoped bindings present (2 selector columns, 4 unique indexes, 3 resolver functions). Ratified: DBA `93d3f092-5e6f-476d-8ec7-41cf587360f9`, Architect `9b532211-da40-4b52-9729-b273523ff3ab`, amendment `e150413d-c540-493b-aeb1-e355b4a7e59f`, DBA final `2f325760-f4ac-4365-88eb-b2e025505aa0`. |
| 4. Assessment family DDL | **Still gated** — the Phase 3 ratification explicitly does not cover it. `assessment_kind` + subject/evidence FKs + `agent_record_id` on `nebula.assessments_history`; single writer. |
| 5. Decision identity link | **Read-only reconciliation complete** (§2.2). The link column is gated with Phase 4/5. |
| 6. Agenda TypeSpec | **`typespec/v1/agenda/`**, model-only, imported by the root umbrella. No table. |
| 7. WorkRequest | No new table; deferral recorded; V170 withdrawn (§6.7). |

### 6.3 The Phase 3 delta, as ratified and implemented

Ratification: DBA `93d3f092-5e6f-476d-8ec7-41cf587360f9`
(APPROVED_WITH_AMENDMENTS) adopted by Architect
`9b532211-da40-4b52-9729-b273523ff3ab` (RATIFIED_WITH_AMENDMENTS); fallback
removal and runtime one-executable amendment: Architect
`e150413d-c540-493b-aeb1-e355b4a7e59f`; DBA final gate
`2f325760-f4ac-4365-88eb-b2e025505aa0`. Applied live on 2026-11-13 as
`sql/V217__representation_scoped_bindings.sql` (ledger
`V217__representation_scoped_bindings`; PR #993 merge `60be1daf8`; DBA closeout
`7b103e30`). Operator procedure and window evidence:
`docs/runbook-v217-representation-scoped-bindings-live-apply.md`.

- `representation`: adds `evaluation_role text NOT NULL DEFAULT 'descriptive'
  CHECK (IN ('executable','descriptive'))` and `read_adapter text`.
- Both binding tables: surrogate `id` primary key, nullable
  `representation_id uuid REFERENCES representation(id)`, partial unique
  indexes on `(representation_id, attribute_id|concept_relationship_id)` and
  on the bare key where `representation_id IS NULL`.
- Backfill is deterministic and never arbitrates: a binding is scoped only
  when its `(schema_name, table_name)` matches **exactly one** representation
  of its concept. A representation becomes `executable` only if a binding was
  scoped to it.
- Resolution is fail-closed: an explicit selection (session setting
  `resolution.representation_id`) must own a binding; otherwise exactly one
  executable representation is used; a concept with **zero** representations
  keeps its legacy NULL-leg binding; any other ambiguity raises.
- Relationship bindings resolve against the from-concept's executable
  representation, not the session selector, because a hop crosses concepts.
- Compiler: `compile_condition`, `compile_root`, `compile_exists_chain` and
  `correlation_ref` now look bindings up through
  `attribute_binding_for` / `relationship_binding_for`.
- Loader/adapter: `DatabaseLoader` and `SolNativeAdapter` read only
  executable representations and fail closed on an unknown `read_adapter`.
  The pre-V217 schema allow-list fallback and the `_representation_scoped`
  catalog probe are gone; entity, attribute and relationship loads all use the
  V217 selector/resolver.
- Function allow-list: `expression.function_name` is FK-constrained to
  `resolution.function_binding` `NOT VALID`, and the migration refuses to
  apply while any `function_call` still names an unregistered function.
  That preflight condition is now **satisfied**: the orphaned `escalates_to`
  expression was removed under v39 (PR #983, `44723cfb0`), tombstoned in
  `resolution.expression_cleanup_tombstone`, and the unregistered-function
  count is 0. Satisfying a preflight does not apply V217; it only stops the
  migration from refusing.

Acceptance (proven in `bin/tests/test_v217_representation_bindings.py`):
`nebula.assessments` and `resolution.assessment` evaluate identically for one
`Assessment` concept; an unbound representation fails closed; two executable
defaults fail closed; the live binding topology still resolves.

**Interpretation note.** The Architect's illustrative "ImplementationPlan and
WorkRequest stay NULL-leg" conflicts with its own determinism rule: each has
two representations but only **one** matching its binding's `(schema, table)`
(`conduit.wrp_dag_nodes` and `vision.work_requests` do not match). The
operative rule — unique match, no arbitration — was implemented, so both are
scoped to their `resolution` representation and keep evaluating. No
arbitration occurred and nothing was promoted beyond the exact table+column
that already evaluated.

### 6.7 WorkRequest

- `resolution.work_request`: **46 rows**, `business_status` in
  `DRAFT|COMPLETED|CANCELLED`. It stays the physical home for now.
- `nebula.work_requests`: a **4-row view** over history. Not canonical yet.
- `vision.canonical_work_requests` (V170): withdrawn. It is a draft in the
  wrong schema, and applying it would add a third physical home. The file keeps
  its body as the template for the eventual `nebula` table: normalized
  identity/authority/temporal spine, `payload jsonb`, business key, supersession.
- The move itself is **not** in this effort. No dual-write and no new
  `vision` table.

---

## 7. Non-goals

Moving operational tables; backfilling or deleting records; keychaining
`prompt`/`response`/`engineering_log`; Track B scoring; applying V170;
automatic deduplication or rewriting of the decision pairs; making every
registered representation executable in one step.

## 8. Verification

### 8.1 How the claims above were measured

Every number and physical surface in §2–§3 was re-read from the live `nexus`
database on 2026-11-13, not carried over from the 2026-11-12 pass:

- `nebula.agent_records` (view) grouped by `record_type` → the nine rows in §2,
  total 16,746.
- `peb.decisions` → 16 rows, all `accepted`; joined to `decision` agent records
  on title → 14 pairs under both raw and case/trim-normalised equality; joined
  on `id` → 0 shared ids.
- `information_schema.columns` → `nebula.assessments` columns, and
  `resolution.representation.evaluation_role` / `read_adapter` **present**
  (V217 applied 2026-11-13).
- `pg_indexes` / `pg_constraint` → binding surrogate `id` and
  `representation_id` present; four partial unique indexes
  (`cab_rep_attr_uq`, `cab_attr_legacy_uq`, `crb_rep_rel_uq`,
  `crb_rel_legacy_uq`) present.
- `resolution.concept` → `Assessment` present; the six V216 concepts present
  (live since 2026-11-13); V217 markers present and ledger row written.
- **Re-verified 2026-11-14 after V216/V217 went live.** The `record_type`
  counts and the `resolution.receipt` total drifted upward with normal use and
  were refreshed in place (`report` 10,521→10,554, `engineering_log`
  2,756→2,809, `response` 641→644, `analysis` 629→633, `assessment` 596→612,
  `decision` 526→530, `inspection` 346→351, total 16,628→16,746, `receipt`
  1,997→1,998). `architecture_note` (480) and `prompt` (133) were unchanged.
  The §2.2 decision figures reproduced **exactly** on re-measurement: 16 ADRs,
  all `accepted`, 14 title matches under both raw and case/trim-normalised
  equality, 0 shared ids. `nebula.assessments` 0, `resolution.assessment` 1,
  `resolution.ticket` 0, `nebula.work_sessions` 6, `nebula.executions` 0,
  `resolution.open_question_answer` 0, `resolution.observation` 6,
  `nebula.observations` 0, `nebula.specifications` 1, `nebula.attestations` 10,
  `nebula.requirement_verifications` 0 and `shrapnel.object_instance` 4,399
  all still hold. One §2 sentence was factually stale and is corrected above:
  it claimed no `Decision`/`Ticket`/`Receipt`/`AgentRecord` concept existed,
  which V216's live apply had already made untrue. No other claim required
  correction, and no figure in this note was carried over unverified.
- `pg_class`/`pg_get_viewdef` → `nebula.agent_records`, `nebula.assessments`,
  `nebula.work_requests`, `nebula.architect_specs` are views over `*_history`;
  `Answer` resolves to `resolution.open_question_answer`.
- `shrapnel.object_instance` → 4,399 objects, for the §4 Shrapnel claim.
- Source reads: `database_loader.py` (`_require_native_adapter`,
  `load_entities`, `load_attributes`, `load_relationships`,
  `load_shrapnel_facts`), `adapters/sol_native.py` (`list_subjects`),
  `sql/V216`, `sql/V217`, `sql/V152__shrapnel_bridge_readthrough.sql`,
  `python/keychains/projection.py`.

**Evidence limitation.** "Title-identical" is a title comparison, not proof that
an ADR and an agent record are the same artifact — it is the strongest signal
available without an identity link, which is exactly why Phase 5 exists. The
keychain boundary in §5 is a plan decision; no test or constraint enforces it
today, and it is recorded as intent rather than capability.

### 8.2 What the tests cover (throwaway databases, never live)

- `bin/tests/test_nebula_sol_concept_registry.py` proves V216 is data-only,
  idempotent, non-destructive, resolves every representation to a real
  relation, and rolls back cleanly — on a throwaway database, never live.
- `bin/tests/test_v217_representation_bindings.py` proves V217 applies twice,
  rolls back clean, scopes bindings only on a unique match, keeps the live
  topology resolving, evaluates `nebula.assessments` and
  `resolution.assessment` identically through `compile_root`, and fails
  closed on an unbound or ambiguous representation.
- `python/SOLScript/tests/test_representation_scope.py` covers the loader
  half: ambiguity and unknown `read_adapter` fail closed, and executable
  non-`resolution` representations are loaded without a schema allow-list.
- `bin/tests/test_agenda_typespec.py` pins the contract shape and compiles it
  when the TypeSpec toolchain is present.
- `bin/tests/test_resolution_registry_closure.py` continues to guard the
  resolution twin; V216 touches none of its surfaces.
