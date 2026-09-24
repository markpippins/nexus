# moleculer/role-memory — canary twin of typescript/role-memory-srv

Moleculer port of [`typescript/role-memory-srv`](../../typescript/role-memory-srv)
(:3500) — the **Role Memory Procedure Registry**: PG→Redis sync
(`tackle.memory` + `tackle.role_memory` → `mem:proc:*` / `mem:idx:*` /
`mem:meta:last_updated`) with cache reads for MCP tools and callers.

**Twin port:** :4150 (band 41xx, per `PORT-MAP.md`).

## Surface (4 endpoints, pinned to the incumbent's openapi.yaml)

| Route | Behavior |
|---|---|
| `GET /health` | Redis ping + SCAN-based counts + stale ladder (1h threshold); 503 structured body when Redis unreachable |
| `GET /procedures/:role` | role index; **missing role → 200 `[]`** (incumbent quirk, deliberate) |
| `GET /procedure/:slug` | full card; missing slug → 404 `{"error":"Procedure not found"}` (exact string) |
| `POST /refresh` | `syncAll()` — full PG→Redis repopulation; `{procedures, roleIndices, timestamp}` |

## Parity contract

- `store.ts` / `sync.ts` are **verbatim ports** of the incumbent's
  `redis.ts` / `db.ts` / `sync.ts` — same Redis keys, same PG tables, same
  timestamp type-parsers, same ioredis retryStrategy doctrine.
- **Shared-state parity:** both twins read/write the SAME cache. The
  incumbent's own boot double-writes Redis (auto-heal race, documented in
  its index.ts: "the double write is harmless"); a twin `/refresh` is the
  same convergence class. Refresh parity = envelope + post-state equality.
- No auth gate on the incumbent → none here (parity is the contract).
- Envelopes: 500 `{error}` (the incumbent's catch shape), 404 exact string,
  Express finalhandler HTML on unmatched routes (kernel-port finding).

## Evidence

- **Hermetic:** 15 tests (jest, mocked store) — the `[]`-not-404 quirk,
  exact 404 string, health ok/degraded ladder, structured 503, refresh
  envelope + Redis write shape, corrupt-JSON → 500.
- **Drift gate:** `OK moleculer/role-memory: 4 endpoints (contract
  typescript/role-memory-srv)`; negative-tested (broken path → FAIL exit 2).
- **Live canary: 30/30 byte-identical** vs the running :3500
  (`tools/canary-diff.py`; result file `/tmp/role-memory-canary-*.json`):
  20 role indexes incl. missing-role `[]`, 6 cards + missing-slug 404,
  unmatched-route HTML parity, `POST /refresh` on both twins (timestamp
  normalized — per-call instant), post-refresh health parity.

## Run

Standalone canary (side-by-side with the incumbent, no mesh registration):

```bash
npm run build
SERVICE_PORT=4150 npx moleculer-runner --config moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/role-memory.service.js
```

Mesh mode (namespace `role-memory`, NATS :4222): `npm start`.

## Cutover notes (NOT cut over)

- Caller inventory: `typescript/tackle-srv/src/memory.ts` (procedure-card
  reads), `typescript/harness-srv`, `python/operator_svc`,
  `bin/mesh-register.py`, wrp conformance readiness test — all via
  `localhost:3500`/env; repoint is one env change per caller.
- No service-registry heartbeat in the incumbent → none ported.
- Cutover additionally requires the titanium-PG promotion gate (Ruling 2),
  green soak, and frozen caller inventory. The 4150 port-map row is
  submitted for Ruling 4 ratification with the port PR.
