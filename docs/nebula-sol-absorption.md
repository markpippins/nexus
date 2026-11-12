# Nebula → SOL absorption: classification, then executable representations

**Status:** ratified design note (Phase 1). Companion migration `sql/V216` is
staged inert. Phases 3 and 4 remain gated on Architect + DBA ratification.
**Measured:** 2026-11-12 against live `nexus` (PostgreSQL 17.11).
**Plan:** approved 2026-11-12 in the Nebula/SOL absorption thread.

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

`nebula.agent_records` holds 16,605 live records across the nine allowed
`record_type` values. No `Decision`, `Ticket`, `Receipt`, `AgentRecord`,
`Report`, `Inspection` or `Analysis` concept exists in `resolution.concept`
today.

| `record_type` | Rows | Existing shape | Relationship |
|---|---:|---|---|
| `report` | 10,519 | none | No domain shape |
| `engineering_log` | 2,740 | `nebula.work_sessions`, `nebula.executions` | Operational adjacency only; not governance |
| `response` | 641 | `resolution.Answer` | Partial — an answer is a settled statement, a response is narrative |
| `analysis` | 628 | `resolution.observation` (6), `nebula.observations` (0) | Adjacent: an observation is an event/finding, an analysis is reasoning over it |
| `assessment` | 595 | `nebula.assessments` (0), `resolution.assessment` (1) | **Same noun, different payload** — see §2.1 |
| `decision` | 525 | `peb.decisions` (16 ADRs) | **Same artifact, two representations, no identity link** — see §2.2 |
| `architecture_note` | 479 | `nebula.specifications`, `architect_specs` | Adjacent: a specification is settled, a note is rationale |
| `inspection` | 345 | none named; assessment/attestation-shaped | **Subtype, not a table** |
| `prompt` | 133 | operator prompt store | Weak; provenance-only, never keychained |

### 2.1 Assessment family

`nebula.assessments` already has the JSONB + FK shape the plan asks for:
`outcome`, `confidence`, `impact_scope jsonb`, `open_questions jsonb`,
`analysis_detail`, plus `observation_id`, `agenda_id`, `auto_resolve_plan_id`
and `forum_post_id`. It holds **0 rows**, so adopting it has no backfill risk.

`resolution.assessment` holds 1 row and carries a different payload
(`rationale`, `dimensions_used`, `dimensions_total`, `resolved_at`). That is a
representation difference, not a second concept.

`inspection` (345 records) has no table of its own. It is assessment-shaped:
subject → finding → confidence → evidence. It becomes
`assessment_kind = 'inspection'`, not `nebula.inspections`.

Two neighbours stay separate because their lifecycles differ:

- `nebula.attestations` (10) is a chain artifact — `work_ref`, `attester_role`,
  `authority_role`, `evidence`, `cites_id`, `txid`. Authority and citation are
  part of its identity.
- `nebula.requirement_verifications` (0) expires: `expires_at`, `conditions`,
  `role`, `status`. Expiry makes it a different lifecycle from an assessment.

### 2.2 Decisions already use concept/representation in practice

`peb.decisions` holds 16 accepted ADRs. ADR-015 is titled *"Architecture
Decision Records live in PEB, not Nebula"* — the authority home is already
decided. Fourteen of the sixteen ADR titles are byte-identical (case/trim) to a
`decision` agent record; **zero** ids are shared. The pairing is real but
unlinked:

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
| Shrapnel EAV | **Index/projection only** | Never authority. The agent-record bridge is an index. |
| MongoDB | **Instance storage only** | Not reachable by SOL evaluation (see §4). |

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

These are properties of the current implementation, not of the registry.

1. **The in-memory loader skips non-`resolution` representations.**
   `DatabaseLoader.load_entities` inner-joins `representation_identity`, then
   `if schema not in ("resolution",): continue`
   (`python/SOLScript/solscript/database_loader.py:719`). Metadata can describe a
   `nebula` representation; the interpreter will not materialise its rows.
2. **Subject listing is hard-filtered to `resolution`.**
   `SolNativeAdapter.list_subjects` selects with
   `r.schema_name = 'resolution'`
   (`python/SOLScript/solscript/adapters/sol_native.py:114`).
3. **Bindings are one-per-attribute and one-per-relationship.**
   `concept_attribute_binding.attribute_id` is the primary key and
   `concept_relationship_binding.concept_relationship_id` is the primary key
   (`schemas/migrations/resolution/resolution_migration_v6.sql:12`,
   `resolution_migration_v5.sql:27`). A concept with N representations can have
   exactly one executable realization per attribute. The others are descriptive.
4. **Bindings are hand-authored, never inferred.** Both migration headers say
   so; Phase 3 keeps that stance and only scopes the binding to a
   representation.
5. **The SQL compiler is schema-qualified but PostgreSQL-local.** `compile_root`
   and `compile_condition` emit `%I.%I` from the binding, so any same-database
   table with an `id` compiles — but there is no cross-database read, no FDW and
   no MongoDB path.
6. **JSONB interiors are not addressable.** `attribute_ref` selects the whole
   column. A key inside `impact_scope` needs an allow-listed `function_binding`
   template or a generated column; interpolating a path from concept metadata is
   not supported and must not be added.
7. **Shrapnel storage is generic; Shrapnel evaluation is not.** Any concept's
   data can be stored as EAV, and the in-memory loader materialises every object
   as a `ShrapnelFact`. But the DB-side bridge
   (`sql/V152__shrapnel_bridge_readthrough.sql`) has a fixed seven-member
   allow-list keyed on `asset_id`, the generic loader registers no
   `ConceptAttribute`s for EAV property names, and a new subject needs a
   bespoke bridge.

The consequence for this strategy: until Phase 3 lands, "registered
representation" must be read as *described*, not *evaluable*. Phase 2 therefore
registers several representations that SOL cannot yet read; that is deliberate
and recorded here so the registry does not overclaim.

---

## 5. Keychain citable set

The keychain cites governed conclusions, not every artifact:

- `decision` — the ruling, and its `peb.decisions` authority row
- `assessment`, including `inspection` — findings with confidence
- `analysis` and `architecture_note` — reasoning that supports a ruling
- cited `report` — only when a decision or assessment cites it

Explicitly **not** citable: `prompt`, `response`, `engineering_log`. They are
provenance, and `prompt`/`response` are immutable history under I4.

---

## 6. Phase status

| Phase | Status |
|---|---|
| 1. Design note | **This document.** |
| 2. Registry completion | **`sql/V216__nebula_sol_concept_registry.sql`**, staged inert. Concepts, identity strategies, representations, identities and governed values only. No DDL, no data movement, not applied live. |
| 3. Executable representations | **Implemented, staged inert** in `sql/V217__representation_scoped_bindings.sql`. Ratified: DBA `93d3f092-5e6f-476d-8ec7-41cf587360f9`, Architect `9b532211-da40-4b52-9729-b273523ff3ab`. Live application is operator-gated. |
| 4. Assessment family DDL | **Still gated** — the Phase 3 ratification explicitly does not cover it. `assessment_kind` + subject/evidence FKs + `agent_record_id` on `nebula.assessments_history`; single writer. |
| 5. Decision identity link | **Read-only reconciliation complete** (§2.2). The link column is gated with Phase 4/5. |
| 6. Agenda TypeSpec | **`typespec/v1/agenda/`**, model-only, imported by the root umbrella. No table. |
| 7. WorkRequest | No new table; deferral recorded; V170 withdrawn (§6.7). |

### 6.3 The Phase 3 delta, as ratified and implemented

Ratification: DBA `93d3f092-5e6f-476d-8ec7-41cf587360f9`
(APPROVED_WITH_AMENDMENTS) adopted by Architect
`9b532211-da40-4b52-9729-b273523ff3ab` (RATIFIED_WITH_AMENDMENTS). Staged
inert as `sql/V217__representation_scoped_bindings.sql`; not applied live.

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
  Both retain a pre-V217 fallback so merged code works before the staged
  migration is applied.
- Function allow-list: `expression.function_name` is FK-constrained to
  `resolution.function_binding` `NOT VALID`, and the migration refuses to
  apply while any `function_call` still names an unregistered function
  (today: `escalates_to`).

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

- `bin/tests/test_nebula_sol_concept_registry.py` proves V216 is data-only,
  idempotent, non-destructive, resolves every representation to a real
  relation, and rolls back cleanly — on a throwaway database, never live.
- `bin/tests/test_v217_representation_bindings.py` proves V217 applies twice,
  rolls back clean, scopes bindings only on a unique match, keeps the live
  topology resolving, evaluates `nebula.assessments` and
  `resolution.assessment` identically through `compile_root`, and fails
  closed on an unbound or ambiguous representation.
- `python/SOLScript/tests/test_representation_scope.py` covers the loader
  half: ambiguity and unknown `read_adapter` fail closed, and the pre-V217
  fallback keeps the resolution-only policy.
- `bin/tests/test_agenda_typespec.py` pins the contract shape and compiles it
  when the TypeSpec toolchain is present.
- `bin/tests/test_resolution_registry_closure.py` continues to guard the
  resolution twin; V216 touches none of its surfaces.
