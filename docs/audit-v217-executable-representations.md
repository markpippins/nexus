# Audit — six live executable representations (V217)

**Date:** 2026-11-13
**Status:** audit only; no schema, data, or writer changes.
**Scope:** the six `resolution.representation` rows with
`evaluation_role = 'executable'` after V217.
**Database:** live `nexus` on PostgreSQL 17.11.
**Related:** `docs/nebula-sol-absorption.md` §6.3;
`docs/runbook-v217-representation-scoped-bindings-live-apply.md`.

This audit separates three claims that are easy to conflate:

1. **registry-executable** — `evaluation_role = 'executable'` and the
   representation is eligible for selection;
2. **identity-registered** — a `representation_identity` row names the
   canonical key strategy and expression;
3. **actually readable** — the loader/adapter applies that identity and
   materialises subjects.

All six are registry-executable. Only three are identity-registered. Neither
read path currently applies the registered identity expression.

## 1. Registry and table coverage

| Concept | Representation id | Physical table | Rows | `read_adapter` | Attribute bindings | Relationship bindings |
|---|---|---|---:|---|---:|---:|
| Answer | `6341ceaa-e991-4d74-aa2e-75e73f3b9406` | `resolution.open_question_answer` | 0 | NULL | 2 | 1 |
| ImplementationPlan | `6c4cf312-16b7-4a57-82ca-123924ecab0b` | `resolution.implementation_plan` | 0 | NULL | 1 | 0 |
| OpenQuestion | `538296cc-511e-47d8-9848-2d97d9b8ea73` | `resolution.open_question` | 0 | NULL | 0 | 1 |
| Requirement | `512853a3-b74b-4eda-8267-1960e9b8b7b1` | `resolution.requirement` | 0 | NULL | 1 | 1 |
| WorkRequest | `592c5fcf-d8e7-4f7f-bb92-31f415fb7cc3` | `resolution.work_request` | 46 | NULL | 1 | 1 |
| WorkRequestEdge | `7726b5cd-86c4-407b-988e-8168faee7a93` | `resolution.work_request_edge` | 0 | NULL | 0 | 1 |

Live totals: 6 executable representations, 5 scoped attribute bindings, 5
scoped relationship bindings. There is no non-native representation: **all 31
representations have `read_adapter = NULL`**. `NULL` is the loader/adapter's
native-SQL default (`NATIVE_READ_ADAPTERS = (None, 'native_sql')`); no row
explicitly says `native_sql`.

Five of six executable tables are empty. Only `resolution.work_request`
currently exercises a real read: 46 rows.

## 2. Identity coverage

| Concept | Identity row | Strategy | Expression | `id` column | `asset_id` column | Runtime key used |
|---|---|---|---|---|---|---|
| Answer | **missing** | — | — | yes | no | none (loader skips; adapter uses `id`) |
| ImplementationPlan | present | canonical asset id, active row | `asset_id` | yes | yes | `id` |
| OpenQuestion | **missing** | — | — | yes | no | none (loader skips; adapter uses `id`) |
| Requirement | present | canonical asset id, active row | `asset_id` | yes | yes | `id` |
| WorkRequest | present | canonical asset id, active row | `asset_id` | yes | yes | `id` |
| WorkRequestEdge | **missing** | — | — | yes | no | none (loader skips; adapter uses `id`) |

Measured coverage:

- **3/6** executable representations have a `representation_identity` row:
  ImplementationPlan, Requirement, WorkRequest.
- **3/6** do not: Answer, OpenQuestion, WorkRequestEdge.
- **0/6** actually execute the registered expression. `DatabaseLoader` builds
  `Entity(id=str(data_row["id"]))`; `SolNativeAdapter` uses
  `attrs.pop("id", "")`. The selected `ri.identity_expression` is read by
  `DatabaseLoader.load_entities` but never applied.
- `DatabaseLoader.load_entities` **inner-joins**
  `resolution.representation_identity`. A missing identity row is therefore a
  silent skip, not a fail-closed error. Answer, OpenQuestion and
  WorkRequestEdge would not be materialised by the loader even if their tables
  had rows.
- A live loader probe produced **46 entities total, all WorkRequest**. The
  other five executable concepts contributed zero entities (four empty tables
  plus two identity-missing tables; ImplementationPlan and Requirement are
  identity-present but empty).

### WorkRequest identity is not uniformly canonical

`resolution.work_request` has 46 rows:

- 46 distinct `id` values;
- only 6 distinct `asset_id` values;
- 40 rows with `asset_id IS NULL`;
- 6 rows where `id <> asset_id`.

The registered strategy says identity is resolved through the active
`resolution.canonical_asset.canonical_asset_id`, expression `asset_id`. The
runtime does not apply that expression; it keys all 46 rows on `id`. Even if it
did, 40 rows have no `asset_id` to resolve. The registration is therefore
ahead of both the data and the read path for WorkRequest.

## 3. Read-path coverage

| Path | Identity join | Key used | Result on live data |
|---|---|---|---|
| `DatabaseLoader.load_entities` | inner join required | physical `id` | 46 entities, all WorkRequest |
| `SolNativeAdapter.list_subjects` | no identity join | physical `id` | Answer 0, ImplementationPlan 0, OpenQuestion 0, Requirement 0, WorkRequest 46, WorkRequestEdge 0 |

The adapter reads all six tables but returns zero subjects for five because
those tables are empty. That is **table coverage**, not identity coverage.
The adapter also does not verify that a row's `id` is the representation's
registered canonical key.

### Adapter pool-lifecycle caveat

`SolNativeAdapter._rows` calls `self._pool.acquire()` and
`await acq.__aenter__()` but never calls `__aexit__`. With the DSN-only path
each call opens and closes a connection, and the six-concept probe completed.
With a real `asyncpg` pool, the connection is never returned; a multi-concept
probe exhausted the pool and timed out. This is a read-path defect independent
of V217's identity registration, but it means "adapter coverage" should not be
claimed for pooled service use until the acquisition is released
(`async with self._pool.acquire() as conn: ...`).

## 4. Binding coverage

| Concept | Attribute bindings | Relationship bindings | Notes |
|---|---:|---:|---|
| Answer | 2 (`confidence`, `role`) | 1 (Answer → VerifiedStatement) | relationship-only path also present |
| ImplementationPlan | 1 (`status`) | 0 | attribute-only |
| OpenQuestion | 0 | 1 (OpenQuestion → Answer) | relationship-only |
| Requirement | 1 (`compilation_status`) | 1 (Requirement → Requirement) | both |
| WorkRequest | 1 (`business_status`) | 1 (WorkRequest → WorkRequestEdge) | both |
| WorkRequestEdge | 0 | 1 (WorkRequestEdge → WorkRequest) | relationship-only |

All five scoped attribute bindings match their representation's physical
table/column. All five scoped relationship bindings use the expected from/to
tables. No scoped attribute binding names a different table. The missing
attribute bindings on OpenQuestion and WorkRequestEdge are not errors: those
concepts are represented through relationships, not a state column.

## 5. Findings

1. **Registry executability is not identity completeness.** Three executable
   representations have no identity row. The loader's inner join silently
   skips them; that is worse than a deliberate fail-closed error because the
   registry says "executable" while the read path says nothing.
2. **The identity expression is registered but unused.** Both read paths key
   on physical `id`. If `asset_id` is the intended canonical key, the current
   behavior is a silent identity substitution.
3. **WorkRequest's canonical identity is mostly absent.** 40/46 rows have no
   `asset_id`, and 6 rows prove `id` and `asset_id` are not interchangeable.
   Applying the registered expression without a null policy would not produce
   46 entities.
4. **Read-adapter coverage is nominal.** All representations are native/NULL;
   no non-native adapter exists. The native path works with a DSN but leaks
   pooled connections.
5. **Only one executable representation has data.** The other five are
   executable by flag, but no live read exercises them. Any claim of six
   equivalent executable surfaces is registry-level, not data-level.

## 6. Recommended follow-ups

These are follow-ups, not changes made by this audit:

1. **Identity completeness (P0):** add/ratify `representation_identity` for
   Answer, OpenQuestion and WorkRequestEdge, or explicitly mark them as
   identity-unsupported. Make `load_entities` fail closed on a missing identity
   rather than silently skipping the representation.
2. **Identity semantics (P0):** decide whether the runtime key is physical
   `id` or registered `asset_id`/canonical-asset resolution. If the latter,
   implement the expression and define behavior for the 40 NULL WorkRequest
   `asset_id` rows; if the former, change the registration so it does not claim
   a canonical key the runtime ignores.
3. **Adapter lifecycle (P1):** release pooled acquisitions in
   `SolNativeAdapter._rows`, and add a real-pool test that makes more than one
   concept request.
4. **Read coverage (P2):** add non-empty fixtures/tests for the five empty
   executable representations before treating their read path as proven.
5. **Do not change V218/Phase 4–5** as part of this audit. No schema or data
   repair was performed here.

## 7. Evidence

- Registry, identity and binding tables read from `resolution.representation`,
  `resolution.representation_identity`, `resolution.identity_strategy`,
  `resolution.concept_attribute_binding` and
  `resolution.concept_relationship_binding`.
- Table row counts read directly from the six physical tables.
- `work_request`: 46 rows, 46 distinct `id`, 6 distinct `asset_id`, 40 NULL
  `asset_id`, 6 `id <> asset_id`.
- Loader probe: `DatabaseLoader.load_entities()` → 46 entities, all
  WorkRequest.
- Adapter probe with DSN-only connections:
  Answer 0, ImplementationPlan 0, OpenQuestion 0, Requirement 0,
  WorkRequest 46, WorkRequestEdge 0.
- Code trace: `DatabaseLoader.load_entities` inner-joins
  `representation_identity` and constructs `Entity(id=data_row["id"])`;
  `SolNativeAdapter.list_subjects` selects all executable representations,
  checks `read_adapter`, and constructs subjects from `attrs["id"]`.
