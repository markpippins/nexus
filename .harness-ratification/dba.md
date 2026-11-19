# DBA review request — Phase 3 representation-scoped bindings

Bounded schema review. Do not implement; return a verdict and binding amendments. Repo: `/home/codex/dev/nexus`. Read-only inspection is welcome. Write your decision as an agent record tagged `["to:engineer","type:decision","nebula-sol"]` (role `dba`) so it is citable, then print the same verdict on stdout.

## Context

Approved plan: `docs/nebula-sol-absorption.md` §6.3 (Phase 3), merged in PR #980. The plan gates this phase on Architect + DBA ratification. The registry migration V216 is staged inert and NOT applied.

Live measured facts (2026-11-12, database `nexus`):

- `resolution.concept_attribute_binding`: `attribute_id uuid PRIMARY KEY REFERENCES concept_attribute(id)`, plus `schema_name`, `table_name`, `column_name` (all NOT NULL). **7 rows.**
- `resolution.concept_relationship_binding`: `concept_relationship_id uuid PRIMARY KEY`, plus `from_schema/from_table/from_column/to_schema/to_table/to_column` and `notes`. **6 rows.**
- `resolution.representation`: `id uuid PK`, `concept_id`, `label`, `schema_name`, `table_name`, `owning_subsystem_id`, `owner`, `raw_metadata jsonb`, timestamps. **23 rows.** No unique constraint on `(concept_id, label)`.
- `resolution.representation_identity`: `representation_id uuid UNIQUE`, `identity_strategy_id`, `identity_expression`.
- Bindings are hand-authored, never inferred (migration v5/v6 headers).
- `DatabaseLoader.load_entities` (`python/SOLScript/solscript/database_loader.py:719`) skips representations whose schema is not `resolution`; `SolNativeAdapter.list_subjects` (`adapters/sol_native.py:114`) filters `r.schema_name = 'resolution'`.
- All 7 attribute bindings and 6 relationship bindings currently point at `resolution` tables; every concept with a binding has exactly one representation today.

The goal: one concept may have N representations (`nebula.assessments` and `resolution.assessment` for `Assessment`), each able to carry its own executable binding, without regressing the legacy single-representation path.

## Proposed delta (v1)

1. `resolution.representation` gains
   `evaluation_role text NOT NULL DEFAULT 'descriptive' CHECK (evaluation_role IN ('executable','descriptive'))`.
2. Both binding tables gain `representation_id uuid REFERENCES resolution.representation(id)`.
3. Replace each binding table's primary key with a surrogate `id uuid DEFAULT gen_random_uuid()`, and add:
   - `UNIQUE (representation_id, attribute_id) WHERE representation_id IS NOT NULL`
   - `UNIQUE (attribute_id) WHERE representation_id IS NULL`
   - the analogous pair on `(representation_id, concept_relationship_id)` / `(concept_relationship_id)`.
4. Backfill existing rows: set `representation_id` to the concept's single representation. Existing bindings must keep evaluating unchanged.
5. Resolution semantics: an evaluation may name a representation. If it does, its binding must exist or the evaluation fails closed. If it does not, use the concept's representation with `evaluation_role = 'executable'` **only when exactly one exists**; zero or more than one fails closed. A descriptive representation is never implicitly selected.
6. Loader/adapter: replace both hard-coded `resolution` filters with `evaluation_role = 'executable'`, and add an optional `read_adapter text` on `representation` (NULL today = native SQL). Unknown adapter names fail closed.
7. JSONB interiors: reachable only through an allow-listed `function_binding` template (new small allow-list table) or a generated column. No JSON path interpolation from concept metadata.

## Questions the verdict must answer

- **Q1 — key shape.** Surrogate PK + partial unique indexes, or a composite PK including `representation_id`? Consider the existing FK direction from `expression.attribute_id` and the NULL-leg problem.
- **Q2 — selector.** Is `evaluation_role text` (`executable|descriptive`) the right selector, or an `is_executable boolean`? Default must make nothing newly evaluable by accident.
- **Q3 — backfill.** Is `representation_id` nullable after backfill (legacy NULL leg retained) or should it become NOT NULL? If NOT NULL, what is the order of operations to avoid breaking live evaluation?
- **Q4 — default resolution.** Is "explicit representation, else exactly one executable" correct, or should the default be an explicit `is_default` flag on representation with its own uniqueness?
- **Q5 — staging.** Should this DDL be applied live in the delivery PR, or staged inert (V215/V216 precedent) with live application as a separate operator-gated step? State the trigger for application.
- **Q6 — hazards.** Locking, table rewrite, index-build, or trigger concerns on these table sizes.
- **Q7 — JSONB.** Function-binding allow-list vs generated column: which do you approve, and what exactly should the allow-list key on?

## Required output

```
OUTCOME: APPROVED | APPROVED_WITH_AMENDMENTS | REJECTED
```

Then, if amended, a numbered list where each item is directly implementable (exact column, constraint, index predicate, or resolution rule). Then risks and the migration-application stance. Be concrete; "looks fine" is not a verdict.
