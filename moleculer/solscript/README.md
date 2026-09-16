# moleculer-solscript — SOLScript read-only REST facade (Moleculer)

Moleculer facade over the `@nexus/solscript` TypeScript core (deterministic
in-memory resolution interpreter). Implements the contract at
`typespec/v1/solscript/typescript/operations.tsp` (reconcile `solscript`:
6/6).

## Topology

- **Port :4060**, NATS mesh member (`namespace: "solscript"` — isolated
  discovery on the shared :4222 bus, same substrate as search :4050 and
  nexus-broker :4080).
- `moleculer.config.js` is the ONLY prod-effective config (the `.ts` governs
  jest/build only — verified search precedent).

## Read-only posture (distribution/failover POC)

The interpreter library fully supports state transitions; the REST tier
disallows writes:

| Op | Verdict |
|---|---|
| evaluateProposition, checkRule, checkTransitionGuard, executeQuery | LIVE |
| health | LIVE |
| transition-entity | **405** (`api.transitionReadOnly405` — unlocks to JetStream when the write path lands) |

## Layout

```
services/solscript.service.ts   read actions wrapping the TS core
services/api.service.ts         moleculer-web gateway (/api/solscript/*)
moleculer.config.ts/.js         jest config + prod config
test/solscript.service.test.ts  8 tests (fixture mirrors
                                typescript/solscript/test/parity/scenario.json)
```

## Commands

```bash
npm test            # jest (NODE_OPTIONS=--experimental-vm-modules, ESM core)
npm run build       # tsc
npm run dev         # moleculer-runner hot-reload + REPL
```

Depends on `@nexus/solscript` via `file:` (built dist; the package root
exports dist since the facade port). Run `npm run build` in
`typescript/solscript` after core changes, then `npm install` here to
refresh the file: link.
