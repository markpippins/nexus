# moleculer/aegis — twin of `typescript/aegis-srv`

Fourteenth moleculer twin: the TLA+ state-machine registry (:3116 → canary
**:4116**).

aegis-srv serves the `aegis.*` schema: registry CRUD + revisions, Phase-A
structural validation, TLC model-check runs (spawns `java` against the
bundled tla2tools.jar), wind compilation planning, and full CRUD families
for constants, variables, states, transitions, invariants, properties,
temporal-properties, attribute/concept/relationship mappings, and
execution-log — 71 registered routes behind one router.

## Pattern: dispatch-through-Express

Identical to the tackle/conduit/execution/harness/peb twins: the incumbent's
Express app is built verbatim in `services/express-app.ts` and every gateway
alias funnels through one `aegis.dispatch` action. There is no per-route
re-implementation — `isUuid` gates, `requireRegistry`/`requireChild` 404s,
`pick` column allowlists, `jsonbCoerce`, `pgError` mapping
(23505→409, 23503→400, 23514→400, 22P02→400), and the JSON catch-all 404
(`{error:"not found"}`) ride along unchanged.

## Files

| File | Provenance |
|---|---|
| `services/routes.ts` | Verbatim except `.js` import extensions (sole source edit: none) |
| `services/{db,model-checker,phase-a,wind-compiler}.ts` | **Verbatim** (zero edits) |
| `services/tlc-runner.ts` | Verbatim + one documented twin-only clamp (below) |
| `services/express-app.ts` | Incumbent `index.ts` minus `listen`/schema-preflight/process handlers; imports get `.js` extensions; **day-one rate limiter** (below) |
| `services/{api,aegis}.service.ts`, `services/dispatch.ts`, `services/index.ts` | Twin infrastructure (standard fleet template) |
| `test/aegis.test.ts` | Hermetic jest — `db.js` mocked at the module boundary |
| `tools/canary-diff.py`, `tools/canary-run.sh` | Canary parity harness (34 cases) |

Note: `tlc-runner.ts` resolves `TLC_JAR` relative to `__dirname`
(`../tla/tla2tools.jar`); the twin compiles to the same layout, so copy (or
symlink) `typescript/aegis-srv/tla/` alongside the twin for live model-check
runs. The canary never spawns TLC.

## Deliberate deviations (documented, additive)

1. **Day-one rate limiter** (`express-app.ts`): 300 req/min/IP, the PR #575
   fleet posture. The incumbent has one open CodeQL alert
   (`js/resource-exhaustion`, `tlc-runner.ts:184`); the limiter hardens the
   surface generally while the targeted fix is item 2.
2. **TLC timeout clamp** (`tlc-runner.ts`): `timeoutMs` is request-influenced
   and flows into the child-process kill timer; the twin clamps it to
   [1s, 30min] before the timer is armed. Zero envelope impact — timeouts
   surface as the same `TLC timed out after ${timeoutMs}ms` error status.
3. No schema preflight (`SELECT 1 FROM aegis.registry`) at startup — the
   broker owns lifecycle; the pool connects lazily exactly like the other
   twins.

## Canary posture: reads + validation negatives ONLY

The surface has 40 write routes, including `model-check` (spawns java) and
`validate` (Phase-A checker). The canary exercises write routes exclusively
with malformed/absent-id negatives that reject before any DB mutation, and
never reaches the TLC spawn. No `aegis.*` rows are created or mutated.

## Run

```bash
npm install && npm run build && npm test          # hermetic suite
SERVICE_PORT=4116 bash tools/canary-run.sh        # live canary vs :3116
python3 tools/api-docs/check_drift.py             # fleet drift gate
```

Standalone (no NATS): `moleculer.config.standalone.js` (twin root, fleet
convention — it is not compiled). Fleet mode: `moleculer.config.js` (NATS
transporter, `aegis` namespace, nodeID `aegis-twin-1`).
