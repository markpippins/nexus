# Architect ratification — Phase 3 representation-scoped bindings

Decision request. Do not implement; ratify or amend, then record the ruling as an agent record tagged `["to:engineer","type:decision","nebula-sol"]` (role `architect`) and print the same verdict on stdout.

## Where this stands

The approved plan (`docs/nebula-sol-absorption.md` §6.3, PR #980) gates Phase 3 on Architect + DBA. The DBA reviewed the concrete delta and returned **APPROVED_WITH_AMENDMENTS**, decision record `93d3f092-5e6f-476d-8ec7-41cf587360f9` (read it; it is the detailed spec). Its amendments:

1. Surrogate `id` PK on both binding tables; `representation_id uuid` NULLABLE; partial unique indexes on `(representation_id, attribute_id)` / `(representation_id, concept_relationship_id)` where non-NULL, plus legacy unique on the bare key where NULL.
2. `representation.evaluation_role text NOT NULL DEFAULT 'descriptive' CHECK (evaluation_role IN ('executable','descriptive'))`.
3. Keep `representation_id` NULLABLE; backfill per concept (0/1/2+ reps), never arbitrate a 2-rep concept.
4. Resolution: explicit rep → its binding or fail; no rep named → exactly one executable rep or fail; descriptive never implicit; no `is_default` flag.
5. DDL staged inert as V217, not applied live; operator-gated application.
6. No hazards at current sizes (7/6/23/13); use `CREATE INDEX CONCURRENTLY`.
7. Reuse the existing `resolution.function_binding` (6 rows) keyed on `function_name`; do not create a duplicate allow-list. Close the gap where `expression.function_name` is unconstrained.

Verified live facts that broke the original proposal:

- `Proposition`: **0 representations**, 2 attribute bindings.
- `ImplementationPlan`: 2 representations, 1 attribute binding (`resolution.implementation_plan.status`).
- `WorkRequest`: 2 representations, 1 attribute binding (`resolution.work_request.business_status`).
- `Answer` (1 rep, 2 bindings) and `Requirement` (1 rep, 1 binding) are the only other bound concepts.
- Relationship binding `PromotionCandidate -> ShrapnelFact` has empty schema/table and `..asset_id` columns (asset bridge); the other five are `resolution.*`.
- `expression.function_call` rows use `escalates_to` (NOT in `function_binding`), `lower`, `concat`.

## Decisions requested

**A1 — `evaluation_role` backfill.** The DBA requires a decision here: with the default `descriptive`, the legacy implicit-resolution path fails closed. Proposed rule:

- Backfill `binding.representation_id` by exact `(schema_name, table_name)` match against the binding concept's representations. (Answer, Requirement, ImplementationPlan, WorkRequest all match their `resolution.*` representation deterministically.)
- Set `evaluation_role = 'executable'` **only** on representations that received a backfilled binding; everything else stays `descriptive`.
- Leave `representation_id` NULL for `Proposition` (0 reps) and the `..asset_id` relationship bridge.

Ratify or amend. In particular: is "executable iff a binding matched it" right, or should single-representation concepts be marked executable even without bindings?

**A2 — NULL-leg semantics.** Proposed implicit resolution order:

1. explicit representation named → its binding must exist, else fail closed;
2. else the concept has exactly one `executable` representation → use it, else fail;
3. else the concept has **zero representations** → use the NULL-leg binding (legacy), else fail;
4. else fail closed (multiple reps, none selected).

This preserves `Proposition` and the asset-bridge relationship without silently promoting anything. Is rule 3 acceptable, or should `Proposition` get a representation instead?

**A3 — `function_name` enforcement.** A validated FK would fail today because `escalates_to` is unlisted. Options: (a) add the FK `NOT VALID` (new rows enforced, legacy tolerated) plus a runtime allow-list lookup that fails closed; (b) register `escalates_to` with a template (its SQL is unknown — would be fabrication); (c) defer the FK and enforce only at runtime. State which.

**A4 — Phase 4/5 coverage.** The plan gates the assessment-family DDL (Phase 4: `assessment_kind`, subject/evidence FKs, `agent_record_id` on `nebula.assessments_history`, 0 rows) and the Phase 5 `Decision` link column on the same ratification. Does this ratification cover them (implement in the same delivery), or do they stay gated for a separate decision?

**A5 — Application stance.** Confirm V217 is staged inert in the PR, with live application operator-gated (DBA amendment 5), and that the guards prove idempotency/rollback on a throwaway DB only.

## Required output

```
OUTCOME: RATIFIED | RATIFIED_WITH_AMENDMENTS | REJECTED
```

Then the binding answers to A1–A5 (amendments as exact, implementable rules), and any conditions. "Proceed" without answering A1–A5 is not a ratification.
