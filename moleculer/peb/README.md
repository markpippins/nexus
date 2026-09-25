# moleculer/peb — twin of `typescript/peb-srv`

Thirteenth moleculer twin: the Push Event Bus (:3111 → canary **:4111**).

peb-srv is the observability REST API over the `peb` PostgreSQL schema:
ADR decision lifecycle (create / patch / supersede / chain), transaction
ledger + lineage, fleet health (circuit breakers, entropy, violation
summaries), governance events (receipt stream + replay), entity capability
projections, state versioning/diff, trace trees, and a replayable SSE event
stream (`/api/peb/events/stream`, 1s DB poller + in-process SSE bus).

## Pattern: dispatch-through-Express

Identical to the tackle/conduit/execution/harness twins: the incumbent's
Express app is built verbatim in `services/express-app.ts` and every gateway
alias funnels through one `peb.dispatch` action. There is no per-route
re-implementation — parsing, envelopes, error mapping (ApiError → status,
PG 23505/23503/22P02 → 409/409/400), pagination clamps, and the JSON
catch-all 404 ride along unchanged.

## Files

| File | Provenance |
|---|---|
| `services/routes/*.js` (11), `services/lib/{pagination,sse-bus}.js`, `services/{db,errors,error-handler}.js` | **Verbatim** from `typescript/peb-srv/src/` (zero edits) |
| `services/express-app.ts` | Incumbent `index.js` minus `listen`/heartbeat/process handlers; imports get `.js` extensions; **one additive limiter** (below) |
| `services/{api,peb}.service.ts`, `services/dispatch.ts`, `services/index.ts` | Twin infrastructure (standard fleet template) |
| `test/peb.test.ts` | Hermetic jest — `db.js` mocked at the module boundary |
| `tools/canary-diff.py`, `tools/canary-run.sh` | Canary parity harness (32 cases) |

## Deliberate deviation: day-one rate limiter

`services/express-app.ts` mounts a global `express-rate-limit`
(300 req/min/IP) **on day one**. Rationale: the peb incumbent carries 23
open `js/missing-rate-limiting` alerts; a verbatim twin would re-introduce
every one of them at new paths and trip CodeQL's alert-delta gate — the
exact failure that blocked #531/#555/#559/#570 until the option-A
hardening (PR #575). The limiter matches the posture landed there
(`nebula-srv` precedent). Incumbent peb-srv hardening is tracked
separately; envelopes are unchanged below the 429 ceiling.

## Canary posture: reads + validation negatives ONLY

The surface has four write routes: `POST /api/peb/decisions`,
`PATCH /api/peb/decisions/:id`, `POST /api/peb/decisions/:id/supersede`
(ADR chain mutations), and `POST /api/peb/events/:receipt_id/replay`
(stamps `replayed_at`, publishes on the SSE bus). The canary exercises
them exclusively with malformed-body negatives (`title is required`,
`author_id is required`, `summary is required`, `invalid id`,
`invalid receipt_id`) that 400 **before** any DB work — no
`peb.decisions` / `peb.governance_events` rows are touched. The SSE
stream endpoint is header-parity-probed via a `GET /api/peb/events`
validation case instead of holding a stream open.

## Run

```bash
npm install && npm run build && npm test          # hermetic suite
SERVICE_PORT=4111 bash tools/canary-run.sh        # live canary vs :3111
python3 tools/api-docs/check_drift.py             # fleet drift gate
```

Standalone (no NATS): `moleculer.config.standalone.js` (twin root, fleet
convention — it is not compiled). Fleet mode: `moleculer.config.js` (NATS
transporter, `peb` namespace, nodeID `peb-twin-1`).
