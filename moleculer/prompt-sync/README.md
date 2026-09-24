# moleculer/prompt-sync — canary twin of typescript/tackle-prompt-sync-srv

Moleculer port of
[`typescript/tackle-prompt-sync-srv`](../../typescript/tackle-prompt-sync-srv)
(:3501) — the **Prompt Registry**: PG→Redis sync
(`tackle.prompts` + `tackle.tasks` → `prompt:proc:{role}::{slug}` /
`prompt:idx:{role}` / `prompt:meta:last_updated` / `task:idx:{role}`) with
cache reads for the prompt bridge, CLI, and agent-launch lookups.

**Twin port:** :4501 (+1000 band, per `PORT-MAP.md`).

## Surface (5 endpoints, pinned to the incumbent's openapi.yaml)

| Route | Behavior |
|---|---|
| `GET /health` | `{status:"ok", lastUpdated, uptime, namespace:"prompt:"}`; 503 `{status:"error", message}` when Redis unreachable |
| `GET /prompts/:role` | role prompt index; **missing role → 200 `[]`** (incumbent quirk, deliberate) |
| `GET /prompt/:role/:slug` | full prompt card; miss → 404 `{"error":"Prompt not found"}` (exact string) |
| `GET /tasks/:role` | role task index; missing role → 200 `[]` |
| `POST /refresh` | `syncAll()` — full PG→Redis repopulation; `{prompts, rolePromptIndices, tasks, roleTaskIndices, timestamp}` |

## Parity contract

- `store.ts` / `sync.ts` are **verbatim ports** of the incumbent's
  `redis.ts` + `db.ts` / `sync.ts` — same Redis keys, same PG tables, same
  timestamp type-parsers, same ioredis retryStrategy doctrine, same
  pipeline-failure surfacing (no fake success).
- **Shared-state parity:** both twins read/write the SAME cache. The
  incumbent's boot sync + Redis-"ready" auto-heal double-write is the
  documented harmless race (role-memory precedent); a twin `/refresh` is
  the same convergence class. Refresh parity = envelope + post-state
  equality.
- No auth gate (and no CORS) on the incumbent → none here (parity is the
  contract).
- Envelopes: 500 `{error}` (the incumbent's catch shape), 404 exact string,
  503 structured health body, Express finalhandler HTML on unmatched routes
  (kernel-port finding).

## Deviations from the incumbent

- Heartbeat (`startHeartbeat`, serviceId 118) dropped — the broker owns
  lifecycle (sibling-twin convention).
- `process.on(uncaughtException/SIGINT/SIGTERM)` process-level handlers
  dropped — same rationale; moleculer broker + runner handle shutdown
  (`stopped()` closes Redis).

## Run (standalone canary)

```bash
cd moleculer/prompt-sync
npm install && npx tsc
SERVICE_PORT=4501 node node_modules/.bin/moleculer-runner \
  --config moleculer.config.standalone.js \
  dist/services/api.service.js dist/services/prompt-sync.service.js
```

## Evidence

- **Hermetic:** 15 tests (jest, mocked store) — the `[]`-not-404 quirk,
  exact 404 string, health ok/503 shapes, refresh envelope + exact shared
  key shapes, prompt_id→prompt_slug resolution, PG-failure and
  pipeline-failure 500s, corrupt-JSON → 500, alias-map parity vs the
  incumbent's index.ts.
- **Drift gate:** `OK moleculer/prompt-sync: 5 endpoints (contract
  typescript/tackle-prompt-sync-srv)`; negative-tested (broken alias path →
  FAIL exit 1).
- **Live canary:** `tools/canary-diff.py` — health, every cached
  prompt/task role, sampled cards, 404 string, unmatched-route HTML,
  POST /refresh convergence, post-refresh health. Result file
  `/tmp/prompt-sync-canary-*.json`.
