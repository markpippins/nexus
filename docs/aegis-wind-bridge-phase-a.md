# Aegis → Wind bridge (Phase A)

The Aegis/Wind bridge is a compilation and lineage surface. Aegis remains a mutable design-time registry and advisory verifier; Wind remains the workflow/runtime owner; Resolution/PEB remains the admission authority.

```text
mutable aegis registry
        ↓ snapshot
immutable aegis.registry_revision
        ↓ advisory verification evidence
revision-scoped bridge mappings
        ↓ compile
immutable lineage record → wind.workflow_version/nodes/edges
        ↓ runtime
wind workflow instances and tickets
```

## V149 contract

Migration: `sql/V149__aegis_wind_bridge_revision_scoped.sql`

### Revision-scoped mappings

`aegis.wind_task_mapping` maps an Aegis state to a Wind task. It includes `registry_id` and `registry_revision_id`, so the mapping is tied to one immutable Aegis snapshot. `is_check_only` must match the Wind convention: a check-only task has `wind.tasks.tackle_task_id IS NULL`.

`aegis.wind_outcome_mapping` maps one Aegis transition to one enumerable outcome of the source state's mapped Wind task. The database enforces the task/outcome pair and validates the source-state mapping for the same revision.

Mappings are immutable once created. A changed authoring model receives a new registry revision and new mappings.

### Compilation lineage

`aegis.wind_compilation` records the relationship between one Aegis revision and one Wind workflow version. It stores:

- Aegis source and model digests
- Wind graph digest
- compiler version and compiler configuration digest
- Wind workflow/version identity and version number
- validation reference and optional validation digest
- lowercase lifecycle status: `succeeded`, `failed`, `stale`, or `invalid`

The insert trigger verifies that the source/model digests match the referenced revision and that the workflow version belongs to the declared workflow.

`aegis.compiled_node` and `aegis.compiled_edge` preserve state/transition-to-Wind-node/edge lineage. Their registry, revision, compilation, and Wind-version identities are all bound by composite foreign keys. They are append-only and cannot be deleted through normal DML while their compilation remains.

## Lifecycle rules

- Mutable Aegis authoring rows are not execution artifacts.
- A registry revision is immutable and content-addressed.
- Verification evidence is append-only and advisory-only.
- A compilation is immutable lineage, not runtime authority.
- Wind workflow artifacts are referenced by real cross-schema FKs where the identity is available.
- Aegis rows cannot be deleted merely to erase compilation lineage; use a new revision or retire the authoring registry.
- No bridge operation mutates Resolution or PEB.

## Compiler and API slice

The first compiler slice is implemented in `typescript/aegis-srv/src/wind-compiler.ts` and exposed through `POST /api/registries/{id}/wind-compilations`. It is deliberately fail-closed: the revision must have exactly one initial state, every state must map once to an existing Wind task, every transition must map once to an outcome of the source state's task, and check-only flags must agree with `tackle_task_id` nullability. Validation runs before the first Wind row is inserted and the transaction rolls back on any later error.

Successful compilation creates a new, inactive Wind workflow version, deterministic nodes and edges, and the Aegis compilation/node/edge lineage rows. `GET` collection/item endpoints expose the resulting lineage for audit and replay. Version allocation is serialized per workflow to prevent concurrent `MAX()+1` collisions. The compiler does not create or mutate Wind task definitions, activate a version, invoke work, mutate Resolution/PEB, or grant authority.

V150 adds database triggers to reject update/delete of Wind workflow versions, nodes, and edges once an Aegis compilation references the version. This protects the graph digest's referent while retaining normal design-time mutation for uncompiled artifacts.

## Explicit boundary

This phase proves the revision-pinned compiler/bridge seam and Wind artifact immutability only. It still does not implement task provisioning, runtime invocation, receipt admission, or PEB authority. `check`/`invoke` remain advisory and fail-closed until the authorized Resolution/PEB admission boundary consumes them.
