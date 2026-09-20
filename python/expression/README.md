# Expression v0.1 POC

This package is the first executable Expression slice. It provides:

- deterministic transcript fingerprints;
- source-addressable segments grouped by role change or size;
- explicit reference observations for PRs, migration versions, URLs, and paths;
- explicit decision-language observations;
- source hashes, extraction revision, input fingerprint, and `unreviewed` disposition on every observation;
- conservative candidate links from an explicit alias catalog;
- non-authoritative proposition candidates describing source language;
- reproducible JSON review bundles through `expression.cli`;
- a pure evaluator adapter with explicit read-set/evaluator/ontology identity;
- deterministic replay comparison and fail-closed refusal/unevaluable results;
- an explicit compatibility and storage boundary: Expression converges with existing harvest/semantics/KG material, keeps observations regenerable staging data, and leaves canonical identity, lineage, disposition, and evaluation joins in Resolution;
- a read-only compatibility adapter for `nebula.harvests`/`harvest_candidates` and `semantics.source_observation` records, preserving source identities and surfacing conflicting hashes instead of overwriting history;
- a composable source-tag/metadata projection adapter that preserves namespaces and source revisions, attaches only by stable identity, and never creates a governed SOL tag.

It deliberately does **not** perform identity resolution, semantic inference, graph writes, proposition admission, or authority changes. Candidate links remain `proposed` or `ambiguous`; they are not confirmations. The evaluator callback is a seam for SOLScript/Resolution and is not an admission path.

## Compatibility and storage boundary

Expression is currently declared `converge` with the existing harvest/semantics/KG pipeline. It consumes immutable transcript assets and may use existing observations as inputs, but it does not create a second canonical observation authority. Expression observations are staging/query material: they must be regenerable, disposable, and source-linked. Resolution remains canonical for identity, lineage, disposition, and governed evaluation joins. A graph output is a regenerable projection, not an authority store. Changing this boundary requires an explicit compatibility/supersession record before implementation changes. The adapter does not call APIs or write databases; it consumes already-fetched records and returns a deterministic staging bundle.

Taxonomy helpers:

- `expression.taxonomy.EXPLICIT_KINDS`
- `expression.taxonomy.OBSERVATION_KIND_EXPECTATIONS`
- `expression.taxonomy.expected_observation_contract()`
- `expression.pipeline.validate_observations()`

## Tags and projected metadata

Source tags and metadata are represented as `ProjectedTagObservation` values. They retain the source identity, source revision, namespace, raw value, normalized value, and projection basis. They are `observed` and `projected`, not governed. A governed tag binding requires a later vocabulary and authority decision; `governed_tag_id` remains empty in this POC. Tag attachment uses stable source identity only, and unmatched tags remain explicit unresolved observations.

## Taxonomy and contract drift

Expression's current observation vocabulary is explicit and documented in `expression.taxonomy` and the TypeSpec `ExpressionTaxonomy` model. The POC currently expects `reference`, `version`, and `speech_act` observations to remain `unreviewed` and `non_authoritative`. Adding a new observation kind should be accompanied by an explicit taxonomy entry before the kind is treated as part of the contract. `validate_observations()` is a contract-gate helper: it reports mismatches relative to the current taxonomy instead of silently accepting new vocabulary.

## Validate

From the worktree root:

```bash
PYTHONPATH=python python3 -m unittest discover -s python/expression -p 'test_*.py' -v
PYTHONPATH=python python3 -m expression.cli python/expression/fixtures/sample.json
```

Validate the TypeSpec contract without emitter packages:

```bash
NODE_PATH=/home/codex/dev/nexus/typespec/v1/node_modules \
  /home/codex/dev/nexus/typespec/v1/node_modules/.bin/tsp \
  compile expression/main.tsp --config expression/tspconfig.yaml --no-emit
```

The TypeSpec contract is model-only for this slice. OpenAPI/client emitters should be enabled only after the Expression package is registered in the workspace dependency layout.

The taxonomy model is intentionally separate from the extraction pipeline so that new observation kinds surface as explicit contract changes before they are used as evidence or evaluation inputs.
