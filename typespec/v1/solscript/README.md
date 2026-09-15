# solscript — TypeSpec Contract

Extracted from the reference implementation at `python/SOLScript/solscript/`
(models.py, events.py, adapters/contract.py). SOLScript is an **in-memory
interpreter for the resolution schema language** — a library, not an HTTP
service — so this is a **model-only, route-free contract** (`nats-envelope`
precedent).

## Layout

```
solscript/
  python/
    main.tsp          # @service entry; imports models + operations
    models.tsp        # ontology, storage contract, Keychains events, API descriptor
    operations.tsp    # intentionally route-free (reconciler must find zero routes)
  scripts/
    solscript_codegen.py            # deterministic python stub regenerator (stdlib-only)
    run-solscript-regen-proof.py    # P2 proof: emit → regenerate → parity-assert
```

Registered in the root aggregate (`typespec/v1/main.tsp`).

## Regeneration proof (P2)

```bash
python3 typespec/v1/scripts/run-solscript-regen-proof.py
# PARITY PROVEN — 33 surfaces match the reference field-for-field.
```

Chain of custody: `.tsp` (canonical) → OpenAPI JSON projection (emitted into
`staging/`, never committed) → `solscript_codegen.py` regenerates Python
contract stubs → parity script asserts field-set equality per model and
value-set equality per enum against the reference dataclasses/enums.
Deterministic: repeated runs produce byte-identical stubs.

## Deliberate exclusions

- **Hybrid techniques reasoning lane** (operator directive, 2026-09-15):
  `reasoning/` pattern library, `LLMIntegrationLayer`, `HybridReasoner` are
  NOT part of the porting target. The deterministic core is.
- **`FunctionBinding.python_func`**: executable `Callable` binding is not
  wire data; tracked as a named exclusion in the proof script
  (`KNOWN_WIRE_EXCLUSIONS`), which fails if the reference adds unexplained
  fields or an exclusion goes stale.

## Consumers

- **Python reference** — parity-proven against `python/SOLScript`.
- **TypeScript port (planned)** — deterministic core projected from these
  same models, packaged library-first for a Moleculer REST facade.
- **Moleculer facade (planned)** — declares its own `operations.tsp` over
  `models.tsp`; the `SolInterpreterOperation` union names the actions 1:1
  (`evaluate_proposition`, `check_rule`, `check_transition_guard`,
  `transition_entity`, `execute_query`).
