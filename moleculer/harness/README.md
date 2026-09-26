# harness twin — `harness-srv` Moleculer port

Side-by-side Moleculer twin of `typescript/harness-srv` (incumbent `:3420`, canary `:4420`).

## Architecture

**Dispatch-through-Express** (tackle/conduit/execution twin pattern):

- `services/api.service.ts` exposes all 8 method/path contracts as literal aliases.
- `services/harness.service.ts` funnels every alias through one `harness.dispatch` action.
- `services/harness-core.ts` is the incumbent `index.ts` verbatim, minus listen/error-handling (moved to the broker), minus the process-level `uncaughtException` handler, and with `startWatchdog()` exported so the twin's service starts it in `started()`.
- `services/db.ts`, `model.ts`, `admission.ts`, `governance.ts` are verbatim (only `.js` import extensions added for NodeNext).
- The watchdog, failover ladders, job registry, SSE job streams, and admission/governance paths are the incumbent implementations — shared PG, Redis, and filesystem surfaces with the incumbent.

## Parity surface (8 routes, pinned to the incumbent's openapi.yaml)

| Route | Notes |
|---|---|
| `GET /health` | 200 `{status:"ok", port, uptime}` / 503 `{status:"error", error}` |
| `GET /sessions` | watchdog-tracked active sessions |
| `POST /run` | wind-task execution — **never canaried** |
| `POST /resolve-context` | dry-run context resolution |
| `POST /run-direct` | raw-prompt execution (sync/async) — **never canaried** |
| `GET /jobs/:jobId` | in-memory job envelope; 404 `{error:"job not found", job_id}` |
| `GET /jobs/:jobId/events` | replayable SSE; 404 for unknown job |
| `POST /jobs/:jobId/interrupt` | SIGTERM by pid — **never canaried** |

Unmatched routes: the incumbent has no catch-all 404 middleware, so Express finalhandler HTML is the parity shape (reproduced in the gateway `onError` for moleculer-web's own routing misses).

## Canary posture: reads + validation negatives ONLY

`POST /run` and `POST /run-direct` spawn agent processes and write PG events, governance receipts, and nebula records; `/jobs/:id/interrupt` kills children by PID. None of these run in the canary. The POST probes are malformed-body negatives (`{}` / missing `wind_task_id` / missing `role` / missing `prompt`) that return 400 before any admission or DB work. `GET /jobs/:jobId` probes use a random UUID absent from both in-memory registries. Normalized fields: `port`, `uptime`, `startedAt`/`elapsedSeconds`, and echo'd `job_id`/`jobId` UUIDs.

## Relationship to nexus-broker

`nexus-broker` (:4080, `worker.harness`) already mirrors a subset of this surface for the mesh — the same pattern as `worker.execution`/execution-srv. This twin mirrors the FULL legacy REST surface; cutover sequencing between the two moleculer consumers is an M1/architect decision (flagged in the PORT-MAP 4420 row).

## Verification

```bash
npm install
npm run build
npm test
SERVICE_PORT=4420 bash tools/canary-run.sh   # incumbent :3420 live
```

Evidence (2026-09-25): tsc strict clean; jest 7/7; drift gate `moleculer/harness` OK 8/8 (negative-tested: broken alias → FAIL exit 1, restored); live canary **12/12 byte-identical** vs :3420 (health, sessions, job-404s, 5 execute-route validation negatives incl. exact 400 strings, unmatched-route HTML); twin torn down after, :4420 free.
