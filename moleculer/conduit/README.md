# conduit twin — `conduit-srv` Moleculer port

Side-by-side Moleculer twin of `typescript/conduit-srv` (incumbent `:3104`, canary `:4104`).

## Architecture

This port deliberately uses **dispatch-through-Express**, like the tackle twin:

- `services/api.service.ts` exposes the committed 20-route method/path surface as literal aliases.
- `services/conduit.service.ts` funnels every alias through one `conduit.dispatch` action.
- `services/express-app.ts` and `services/routes/*` carry the incumbent route implementations, preserving query/body parsing, response envelopes, Express finalhandler 404s, and the long-lived `/log/:sessionId` SSE route.
- `services/db/*` uses the same PostgreSQL DSN, schemas, and queries as the incumbent.
- The broker owns startup/shutdown. The incumbent heartbeat and process-level signal/exception handlers are intentionally omitted.

This shape avoids rewriting pipeline-control writes as ad hoc action envelopes. The twin shares live state with the incumbent; the incumbent remains the write authority and there is no cutover.

## Parity surface

All 20 method/path contracts from `typescript/conduit-srv/openapi.yaml` are registered. The API drift gate compares method + path, so renaming an alias or losing a route fails CI.

Preserved details include:

- structured health 200/503 envelopes;
- route-specific 400, 404, and 500 JSON envelopes;
- PostgreSQL timestamp parser behavior;
- non-destructive projection-drift queries;
- session ID path-traversal rejection;
- SSE headers, polling, and keepalive behavior;
- Express HTML 404 pages for unmatched routes.

## Verification

```bash
npm install
npm run build
npm test
```

Run the side-by-side canary while incumbent `conduit-srv` is healthy on `:3104`:

```bash
bash tools/canary-run.sh
```

The canary is **reads + validation negatives only**. It does not invoke ticket detection, governance replay, failure-recovery updates, or work-request upserts because those routes mutate shared pipeline state. The work-request POST probe omits `id` and therefore returns 400 before SQL. The SSE probe compares status and streaming headers only.

To run manually:

```bash
SERVICE_PORT=4104 npm run build
node_modules/.bin/moleculer-runner --config dist/moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/conduit.service.js
CANARY_BASE=http://localhost:3104 TWIN_BASE=http://localhost:4104 \
  python3 tools/canary-diff.py
```
