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
- a read-only compatibility adapter for harvests, harvest candidates, semantics observations, and cross-reference material, preserving source identities, emitting proposed/ambiguous identity candidates, and surfacing missing identity, conflicting hashes, and supersession lineage instead of overwriting history;
- a composable source-tag/metadata projection adapter that preserves namespaces and source revisions, attaches only by stable identity, and never creates a governed SOL tag;
- an E1 canonical contract normalizer and semantic fingerprint shared by Python and future TypeScript consumers;
- a bounded E2 redacted corpus builder with deterministic replay and committed input/artifact fingerprints.

It deliberately does **not** perform identity resolution, semantic inference, graph writes, proposition admission, or authority changes. Candidate links remain `proposed` or `ambiguous`; they are not confirmations. The evaluator callback is a seam for SOLScript/Resolution and is not an admission path.

## E2 corpus boundary

`expression.corpus` builds the committed `fixtures/e2-corpus.json` through a deterministic redaction policy: control bytes are removed, secret-shaped values are replaced, and prompt-injection markers are labeled and replaced. It emits canonical Expression bundles without database, graph, Aspects, Resolution, or authority writes. `fixtures/e2-corpus-manifest.json` records the input fingerprint, generated artifact fingerprint, byte count, and replay rule. The corpus is synthetic/redacted architecture material, not a live transcript authority.

## E1 canonical contract and Aspects boundary

`expression.contract` is the E1 contract gate. `canonicalize_bundle()` converts the internal extraction dictionaries into the exact v0.1 wire shape and rejects contract, authority, candidate-link, or boundary drift. `contract_manifest()` and `contract_fingerprint()` fingerprint semantic fields rather than source formatting or file paths.

The active v0.1 vocabulary is deliberately limited to `reference`, `version`, and `speech_act`. Candidate links are only `proposed` or `ambiguous`; `confirmed` belongs to a governed Resolution/Aspects path. Proposition candidates carry `predicate_status: unresolved` and cannot claim a governed relation. Projected tags retain `governed_tag_id: null` and `authority_status: projected`; Aspects owns later binding.

The TypeSpec `CanonicalExpressionBundle` mirrors this normalized shape. The package remains model-only (`emit: []`) until the workspace registers the correct generated-client emitter; the committed semantic fingerprint and reconciliation tests are the interim E1 boundary, not an implied generated API. `canonicalize_tag_bundle()` explicitly maps the Python/Aspects adapter field `namespace` to the TypeSpec field `tag_namespace` and rejects any pre-populated governed tag id. Aspects therefore receives a stable projected-tag candidate, not an accidental authority claim.

## E4 evaluator boundary

E4 wraps the existing SOLScript/Resolution evaluator seam without making Expression an authority. Requests pin the Expression contract, source, ontology, evaluator, authority owner, and read-set fingerprints; `mutation_policy` is always `forbidden`. Archived Resolution dispositions map to the stable wire vocabulary (`Asserted` → `asserted`, `Disputed` → `disputed`, `Rejected` → `rejected`, `Pending` → `pending`, `Proposed` → `advisory`, `Stale` → `stale`, `Retracted` → `refused`). Missing read sets are `unevaluable`; unavailable evaluators are `pending`; uncertain/advisory results remain non-authoritative; unsupported outcomes fail closed to `refused`.

Callbacks receive deep copies, and the adapter never invokes transition, persistence, admission, or graph APIs. Replay compares evaluation fingerprints using the same pinned inputs.

## E3 compatibility boundary

The E3 adapter accepts already-fetched records shaped like `nebula.harvests`, `nebula.harvest_candidates`, `semantics.source_observation`, and `nebula.cross_references`. It preserves stable source identity before considering registered aliases. A stable match yields a `proposed` candidate; an alias collision yields `ambiguous`; missing identity is `unresolved`; a changed hash under the same identity is `conflict`; and supersession is recorded as `declared` or `dangling` lineage evidence. None of these outcomes resolves canonical identity or changes source state. Resolution remains canonical for identity, lineage, disposition, and evaluation joins.

The adapter is deliberately compatible with the consolidated Resolution direction: it reads legacy/source-shaped material but does not recreate a legacy table or claim that harvest storage is canonical. It returns deterministic staging data only.

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

## E5 bounded persistence and projection boundary

`expression.e5` builds a deterministic, write-free E5 artifact from one E4 evaluation envelope. The artifact separates three layers: compact evaluation receipts owned canonically by Resolution, a regenerable graph projection, and a Keychains context manifest containing only source/read-set/evaluator identities and references. It does not store source content, call PostgreSQL, call MongoDB, write the graph, or mutate authority.

`rollback_slice()` appends rollback lineage without deleting the prior artifact. `replay_slice()` rebuilds the artifact from the same pinned bundle, read set, and source run and compares fingerprints. Retention is explicit (`bounded_review_fixture` or `operational_review`); arbitrary indefinite retention is rejected.

## E6 live Resolution persistence

`expression.persistence` is the E6 writer: it turns one E5 artifact's canonical receipts into real `resolution.receipt` rows using the V139 R4/Q3 contract shared with the Lilac adapter. Idempotency is `(source_system='expression', source_receipt_id)` with `payload_fingerprint` equivalence; the same id with a different fingerprint is a fail-closed `conflict` carrying both fingerprints. Producer grants are enforced by the DB trigger — the `expression-pipeline` producer registered by `sql/V194__expression_register_producer.sql` holds exactly one kind (`expression_evaluation`), so Expression cannot write lifecycle or admission kinds. Outcome classes match the Lilac vocabulary: `accepted`, `duplicate-equivalent`, `conflict`, `refused`.

`ResolutionReceiptWriter` takes an injectable connection factory and never mutates the producer registry or boundary; the DB is the per-write authority. Admission receipts remain append-only and are never touched by Expression.

## E7 live SOLScript evaluator adapter

`expression.solscript_adapter` binds the E4 evaluation envelope to the real in-memory `ResolutionInterpreter` (python/SOLScript). Live dispositions map into the E4 wire vocabulary; context-gate outcomes become explicit results: `context_required` → `unevaluable`, `context_mismatch` → `refused`, unknown context keys on framed propositions → `refused` (`invalid_context`). Propositions absent from the interpreter are `pending` (`proposition_not_in_interpreter`) rather than silently refused, and interpreter exceptions fail closed. `evaluate_bundle_with_interpreter()` and `replay_bundle_with_interpreter()` produce E4 envelopes pinned to `solscript-resolution-interpreter-v32`; the adapter never mutates interpreter state, invokes transitions, or persists — the E6 writer remains the only persistence path.

## E8.2 database-loaded interpreter adapter

`expression.loaded_interpreter.LoadedInterpreter` wraps a `DatabaseLoader`-populated interpreter (E8.1 loader: real dispositions, assertions, frame values) and adds the identity seam: Expression candidates carry deterministic digest IDs, DB propositions carry UUIDs, and evaluation happens ONLY through a pinned `register_candidate(expression_id, db_proposition_id)` mapping. Unregistered candidates are `pending` (`proposition_not_registered`) — never guessed, never silently aliased, even when the candidate ID happens to equal a DB UUID. Registration requires the DB proposition to actually be loaded and refuses conflicting re-registration.

Every envelope pins `loaded_population_fingerprint` (propositions with dispositions/assertions/frame values, frame dimensions, and dimension values — excluding mutable runtime state) so replay detects population drift. Context is passed through the seam explicitly; gate outcomes (`context_required` → `unevaluable`, `context_mismatch` → `refused`) surface unchanged from E7. The adapter remains evaluation-only; E6 stays the only persistence path.

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
