# @nexus/solscript

SOLScript TypeScript core — in-memory interpreter for the resolution schema
language. Deterministic core ported from `python/SOLScript/solscript`
(parity-proven; see `test/parity/`). **Library-first**: zero runtime
dependencies, no server coupling.

The hybrid techniques reasoning lane (`reasoning/`, `LLMIntegrationLayer`,
`HybridReasoner`) is **excluded by operator directive** (2026-09-15).

## Layout

```
src/
  models.ts              # data models; enum values identical to the Python reference
  expression-compiler.ts # expression trees → callables; canonical JSON + structural hash
  interpreter.ts         # ResolutionInterpreter: rules, guards, transitions, frame discipline
  query-builder.ts       # fluent queries + snapshot/rollback/commit TransactionContext
  events.ts              # Keychains events; dependency-free SHA-256 stableDigest (byte-parity)
  port.ts                # SolStoragePort interface + InMemorySolStorage adapter
test/
  run-solscript-ts-tests.ts    # 12 unit tests (edge cases)
  run-solscript-ts-parity.ts   # cross-runtime parity driver (Python vs TS, canonical JSON)
  parity/scenario.json         # the shared fixture both runtimes execute
```

## Commands

```bash
npx tsc --noEmit              # typecheck (strict)
npx tsx test/run-solscript-ts-tests.ts    # unit tests
npx tsx test/run-solscript-ts-parity.ts   # full parity proof (runs the Python side too)
npm run build                 # emit dist/ (js + d.ts) via tsconfig.build.json
```

`node_modules` is a symlink to an in-repo install (disposable; never commit it).

## Consuming from a Moleculer REST facade

The contract for the future facade lives at
`typespec/v1/solscript/typescript/` (`Status: planned` — flip to
`implemented` only when the service exists). The wiring pattern:

```ts
import { Service, ActionHandler } from "moleculer";
import { ResolutionInterpreter } from "@nexus/solscript";

export class SolScriptService extends Service {
  constructor(broker) {
    super(broker);
    this.interpreter = new ResolutionInterpreter(); // or load via SolStoragePort
    this.parseServiceSchema({
      name: "solscript",
      actions: {
        "evaluate-proposition": {
          params: { propositionId: "string", context: "object|undefined" },
          handler: (ctx) => this.evaluate(ctx.params),
        },
        "transition-entity": { handler: (ctx) => this.transition(ctx.params) },
        // check-rule, check-transition-guard, execute-query, health …
      },
    });
  }
}
```

Key semantics for facade implementers:

- **Transitions never throw on guard failure** — refused/rejected outcomes
  are returned with the durable `KeychainEvent`; negative governed outcomes
  are evidence (persist before dispatching).
- **Frame context gates** (`context_required`, `context_mismatch`) return a
  null disposition plus a status, not an error — surface as 422 with the
  typed error codes from the facade contract.
- **Guard results keep snake_case** (`rule_id`, `rule_name`, `passed`,
  `reason`) — they flow into event read_sets whose digests must match the
  Python reference.
- `moleculer` is an **optional peer dependency**: the library itself must
  stay dependency-free; only the facade service imports moleculer.
