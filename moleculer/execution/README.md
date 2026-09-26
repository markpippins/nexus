# execution twin — `execution-srv` Moleculer port

Side-by-side Moleculer twin of `typescript/execution-srv` (incumbent `:3110`, canary `:4110`).

## Architecture

This port uses **dispatch-through-Express** (tackle/conduit twin pattern):

- `services/api.service.ts` exposes the committed 19-route method/path surface as literal aliases.
- `services/execution.service.ts` funnels every alias through one `execution.dispatch` action.
- `services/routes.ts` and `services/metrics.ts` are the incumbent's `src/routes.ts` / `src/metrics.ts` verbatim (only the metrics import gains a `.js` extension for NodeNext compilation).
- `services/db.ts` carries the incumbent's pg Pool config (search_path pinned to `execution`).
- The broker owns startup/shutdown; `stopped()` closes the pool. The incumbent's heartbeat and process-level signal/exception handlers are intentionally omitted.

The service is **read-only** — every route is a SELECT over `execution.*`
(plus cross-schema reads of `nebula.receipts_unified` and `resolution.*`);
there is no write path to guard. The incumbent remains the serving
authority and there is no cutover.

Note: the moleculer broker tier (`nexus-broker` :4080, `worker.execution`)
already mirrors a SUBSET of this surface for execution-ui. This twin
mirrors the FULL legacy REST surface; cutover sequencing between the two
moleculer consumers is a M1/architect decision, out of scope here.

## Parity surface

All 19 method/path contracts from `typescript/execution-srv/openapi.yaml`
are registered (18 under `/api/execution` + root `/health`). The API drift
gate compares method + path, so renaming an alias or losing a route fails CI.

Preserved details include:

- paginated list envelope `{ total, limit, offset, items }` with `full_count` stripped;
- exact 400 (`{error: "id must be a UUID"}`, `{error: "workflow_instance_id and node_id are required"}`);
- exact 404 strings (`request not found`, `lease not found`, `execution.receipt not found`, `witnessed run not found`);
- 500 `{error: message}` envelopes carrying the SQL message;
- two-level `/health` (200 `{status, db, schema, counts}` / 503 `{status:"error", db:false, message}`);
- the read-only **JSON** catch-all 404 (NOT finalhandler HTML — the incumbent registers an app-level 404 middleware); the gateway `onError` reproduces it for unmatched paths;
- witnessed-run classifier, governed projection versioning (`projectionVersion: 3`), and in-process metrics registry.

## Verification

```bash
npm install
npm run build
npm test
```

Run the side-by-side canary while incumbent `execution-srv` is healthy on `:3110`:

```bash
bash tools/canary-run.sh
```

The canary exercises **every endpoint live** (the incumbent is read-only, so
nothing can mutate shared state): all four paginated catalogs with filters,
stale-lease and fleet views, integrity scan, UUID-parametrized lifecycle /
lineage / pipeline-origin lookups, witnessed-run validation negatives, and
both unmatched-route classes. `scanned_at`/`generatedAt` timestamps are the
only normalizations; both services read the same database, so row windows
match.

To run manually:

```bash
SERVICE_PORT=4110 npm run build
node_modules/.bin/moleculer-runner --config moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/execution.service.js
CANARY_BASE=http://localhost:3110 TWIN_BASE=http://localhost:4110 \
  python3 tools/canary-diff.py
```
