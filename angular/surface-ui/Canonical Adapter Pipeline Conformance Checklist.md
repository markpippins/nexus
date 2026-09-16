Canonical Adapter Pipeline Conformance Checklist
1. Mission & Boundary
Mission: Transform payloads → capability contracts via declarative pipelines.

Must:

Be fully declarative and inspectable.

Must not:

embed opaque imperative logic.

2. Structure
Must:

Represent pipelines as ordered steps (select, map, filter, sortBy, groupBy, distinct, coalesce, default, format, semanticMap).

Use <unknown> placeholders until Studio fills them.

Must not:

Hard-code payload paths in compiler.

3. Capability Alignment
Must:

Produce outputs that conform to capability contracts (MetricSeries, EntityCollection, etc.).

Must not:

emit fields outside contract schemas.

4. Safety & Determinism
Must:

Be deterministic for given payload + pipeline.

Be side-effect-free (no network, no global state).

Must not:

depend on time, randomness, or external mutable state.

5. Studio Integration
Must:

Accept compiler stubs.

Let users bind <unknown> to real payload paths and fields.

Must not:

bypass compiler-generated stubs entirely.

6. Runtime Execution
Must:

Execute pipelines per payload event.

Validate outputs against contracts.

Must not:

mutate pipeline definitions at runtime.