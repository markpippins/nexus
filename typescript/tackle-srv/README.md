# tackle-srv

**REST API server** for the `tackle` PostgreSQL schema on port **3410**.

Owns the canonical, durable, source-of-truth state for the tackle subsystem:
AI configuration registry (providers/harnesses/models/role configs/bundles),
session ledger, role registry, agent scheduler, circuit-breaker / failure
recovery config, and the Role Memory Procedure Registry reader. All writeable
through REST; reads hit PG directly. The Role Memory reader also caches
behind a Redis layer kept warm by `role-memory-srv` (port 3500).

---

## Architecture

```
                                 ┌─────────────────────────────┐
                                 │  tackle-srv (:3410)         │
                                 │  Express REST               │
   HTTP clients                  │                             │
   (Operator chat, CLI,          │  /config/ai/*   (CRUD)        │
    admin UI, harness drivers) ──▶│  /sessions/*   (kill)        │
                                 │  /roles/*      (CRUD)        │
                                 │  /scheduler/*  (CRUD+due)    │
                                 │  /memory/*     (RP cache)    │
                                 │  /config/failure-recovery/*  │
                                 │  /health                     │
                                 └────────────┬────────────────┘
                                              │ PG
                                              ▼
                            ┌─────────────────────────────────┐
                            │ PostgreSQL  (schema: tackle)    │
                            │  providers, harnesses, models,  │
                            │  config_bundle, roles, sessions, │
                            │  agent_scheduler, circuit_breaker│
                            │  memory, role_memory,           │
                            │  schema_version                 │
                            │  + NEW (SQL migrations v7-v9):   │
                            │    prompts, tasks,              │
                            │    role_tool_access             │
                            └─────────────────────────────────┘
                                              ▲
                                              │ / POST /refresh
                                              │ proxied
                            ┌─────────────────┴───────────────┐
                            │ role-memory-srv (:3500) writes  │
                            │ the mem:* Redis namespace.       │
                            │ tackle-srv reads it via          │
                            │ src/memory.ts (read-only).       │
                            └─────────────────────────────────┘
```

---

## REST API — Existing endpoints (port 3410)

All routes return JSON. Errors are `{ "error": "<message>" }` with HTTP 4xx/5xx.
`/health` is unauthenticated. The rest follow the same shape — no auth
middleware is wired today; run this server behind a private network or a
reverse proxy with auth.

### Health
| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | `{ status, port, pid, timestamp }` |

### `/config/ai` — AI Configuration Registry (mounted from `routes/ai-config.ts`)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/config/ai` | Full snapshot — providers, harnesses, models, role configs, bundles |
| `GET` | `/config/ai/validate` | `{ valid, warnings[] }` |
| `POST` | `/config/ai/seed-defaults` | Idempotent seed; body `{ force?: boolean }` |
| `POST` | `/config/ai/import` | Bulk import; body `{ providers[], harnesses[], models[], roles[], bundles[] }` |
| `POST` | `/config/ai/test` | Spawn a test invocation; body `{ model_id, test_prompt }` (creates a session, streams `/log/:sessionId`) |

**Providers** (`/config/ai/providers`, `/config/ai/provider/:id`)
| `GET` | `/config/ai/providers` | List all |
| `GET` | `/config/ai/provider/:id` | 404 if missing |
| `POST` | `/config/ai/provider` | Upsert; body `{ id, name, type, endpoint_url?, api_key?, config_json? }` |
| `DELETE` | `/config/ai/provider/:id` | `{ deleted, id }` |

**Harnesses** (`/config/ai/harnesses`, `/config/ai/harness/:id`)
| `GET` | `/config/ai/harnesses` | List all |
| `GET` | `/config/ai/harness/:id` | 404 if missing |
| `POST` | `/config/ai/harness` | Upsert; body `{ id, name, invocation_semantics? }` |
| `DELETE` | `/config/ai/harness/:id` | `{ deleted, id }` |

**Models** (`/config/ai/models`, `/config/ai/model/:id`)
| `GET` | `/config/ai/models` | List all |
| `GET` | `/config/ai/model/:id` | 404 if missing |
| `POST` | `/config/ai/model` | Upsert; body `{ id, name, harness_id, provider_id?, model_identifier }` |
| `DELETE` | `/config/ai/model/:id` | `{ deleted, id }` |

**Role configs** (`/config/ai/roles`, `/config/ai/role/:role`)
| `GET` | `/config/ai/roles` | List all |
| `GET` | `/config/ai/role/:role` | 404 if missing |
| `POST` | `/config/ai/role` | Upsert; body `{ id, role, provider_id, harness_id, model_id, extra_params?, bundles?[] }`. If `bundles` is a non-empty array, upserts the bundles via `upsertConfigBundles`; otherwise just the primary role config. |
| `DELETE` | `/config/ai/role/:role` | `{ deleted, role }` |

**Config bundles** (`/config/ai/bundles`, `/config/ai/bundle/:id`)
| `GET` | `/config/ai/bundles` | All bundles across all roles |
| `GET` | `/config/ai/bundles/:role` | Bundles for a role |
| `GET` | `/config/ai/bundle/:id` | 404 if missing |
| `POST` | `/config/ai/bundle` | Upsert one; body `{ id, name, role, model_id, provider_id?, harness_id?, priority?, invocation_mode?, command?, endpoint_url?, timeout_ms?, valid_from?, valid_to?, is_active?, metadata? }` |
| `DELETE` | `/config/ai/bundle/:id` | `{ deleted, id }` |
| `POST` | `/config/ai/bundles/:role` | Bulk upsert for a role; body `{ bundles: [...] }` |

**Resolved config** (`/config/ai/resolve/:role`)
| `GET` | `/config/ai/resolve/:role` | Resolves a role's active config bundle (priority + valid_from/to + is_active); 404 if none |

### `/sessions` — Session ledger (mounted from `routes/sessions.ts`)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/sessions` | All sessions (running + ended) |
| `POST` | `/sessions/:sessionId/kill` | Kill by PID (`SIGKILL` to process group, fallback to direct). 404 if missing; 400 if not running. Returns `{ killed, sessionId, pids[], errors?, timestamp }` |

### `/roles` — Role registry (mounted from `routes/roles.ts`)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/roles` | `{ count, roles[] }` |
| `GET` | `/roles/:id` | By id (UUID) or name; 404 if missing |
| `POST` | `/roles` | Upsert; body `{ id?, name, description? }` |
| `DELETE` | `/roles/:id` | `{ deleted, id }` |

### `/tasks` — Task registry (mounted from `routes/tasks.ts`)
Exposes the `tackle.tasks` table created by migration v7 and seeded by v8.
Each task binds a role + task_slug + scope + acceptance_criteria to a
specific prompt template (`prompt_id → tackle.prompts`).

| Method | Path | Notes |
|---|---|---|
| `GET` | `/tasks` | `{ count, tasks[] }`. Query params: `?role=<role>` (filter), `?all=true` (include inactive; default: active only) |
| `GET` | `/tasks/:task_slug` | Most-recent active task with the slug, else most-recent inactive; includes joined `prompt_role`/`prompt_slug`/`prompt_version`. 404 if missing |
| `GET` | `/tasks/inspector/dispatch` | **Inspector task dispatch wiring** — returns every active inspector task, each bundled with the FULL prompt `body_md` for the task's template (latest version of the `(role, slug)` referenced by `prompt_id`). Single document contains task definition + persona prompt body so a consumer can execute the task without a second round-trip. Response: `{ tasks: Array<task & { prompt_role, prompt_slug, prompt_version, prompt_body_md, prompt_title, prompt_parameter_schema, prompt_tags }> }` |

### `/scheduler` — Agent scheduler (mounted from `routes/scheduler.ts`)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/scheduler` | All entries |
| `GET` | `/scheduler/due` | Entries due to run (enabled + last_run_at past schedule) |
| `GET` | `/scheduler/:id` | Single entry |
| `POST` | `/scheduler` | Create; body `{ role, model_id?, harness?, agent_config?, schedule_type?, schedule_value?, project_dir?, enabled? }` |
| `PATCH` | `/scheduler/:id` | Update fields |
| `DELETE` | `/scheduler/:id` | `{ deleted, id }` |

### `/memory` — Role Memory Procedure Registry reader (mounted from `routes/memory.ts`)
Reads the `mem:*` Redis namespace populated by `role-memory-srv` (:3500).
PostgreSQL is the canonical source; Redis is a read cache. `POST /refresh`
proxies to `role-memory-srv` to trigger a PG → Redis sync.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/memory/procedures/:role` | Procedure index for a role: `{ role, count, procedures[] }` |
| `GET` | `/memory/procedure/:slug` | Full `ProcedureCard`; 404 if not cached |
| `POST` | `/memory/check-since` | Body `{ role, since }`; returns `{ role, since, changed }` (queries PG directly, indexed on `role, as_of_dt DESC`) |
| `POST` | `/memory/refresh` | Proxy to `role-memory-srv` `POST /refresh`; returns `{ refreshed, procedures, roleIndices, timestamp }` |

### `/config/failure-recovery` — Circuit breaker (mounted from `routes/failure-recovery.ts`)
| Method | Path | Notes |
|---|---|---|
| `GET` | `/config/failure-recovery` | Current breaker state: `{ max_retries_per_model, retry_delay_seconds, max_fallbacks, push_back_to_pending, circuit_breaker_retry_after }` |
| `POST` | `/config/failure-recovery` | Save config; body same shape as the GET response |

---

## Boot + schema management

`src/db.ts` owns schema lifecycle:

1. `initDb()` connects to PG with `search_path=tackle`, calls `createSchema()` (DDL, `CREATE TABLE IF NOT EXISTS`), then `runMigrations()`.
2. `runMigrations()` acquires a `pg_advisory_lock(873492874)`, reads `MAX(version)` from `tackle.schema_version`, and applies any pending entries from the in-process `migrations[]` array (currently v1–v6 — see below). Each entry records its row in `schema_version`.
3. The in-process `migrations[]` array is the **authoritative** list of migrations tackle-srv will auto-apply at boot. Any migration NOT listed here is invisible to the auto-migrator.

### Migrations tackle-srv knows about at boot

| v | Description |
|---|---|
| 1 | Baseline (no-op — DDL in `createSchema()` is the source of truth) |
| 2 | Add missing PKs + UNIQUE on providers/roles/harnesses/models/config_bundle/circuit_breaker/memory/role_memory |
| 3 | PKs on `sessions` and `agent_scheduler` |
| 4 | Performance indexes on `sessions(created_at DESC, agent_role)` and `agent_scheduler(enabled, last_run_at)` |
| 5 | Seed default circuit breaker, roles, and memory procedures (idempotent) |
| 6 | Migrate TEXT → TIMESTAMPTZ for tackle-owned tables (shared tables handled by conduit-mcp v27) |
| 7 | Create `tackle.prompts`, `tackle.tasks`, `tackle.role_tool_access` — loads `prompts_tasks_tool_access.sql` from `nexus/schemas/migrations/tackle/` at runtime |
| 8 | Seed 11 prompt rows (9 personas + operator system-prompt BASE/TAIL) + 1 inspector task + `builder-fallback` and `operator` roles — loads `seed_prompts.sql` from `nexus/schemas/migrations/tackle/` at runtime |
| 9 | `DEFAULT NOW()` on `tackle.roles.created_at` / `updated_at` — loads `roles_default_timestamps.sql` from `nexus/schemas/migrations/tackle/` at runtime |

### Operational notes for v7–v9

- v7–v9 load their SQL from `nexus/schemas/migrations/tackle/` at runtime via
  `path.resolve(__dirname, "../../../schemas/migrations/tackle/<file>.sql")`. The SQL
  files are idempotent (CREATE TABLE IF NOT EXISTS, ON CONFLICT DO
  NOTHING/UPDATE) and self-stamp `tackle.schema_version` at the bottom.
- `runMigrations` stamps `schema_version` after each `up()` too — that
  wrapper stamp uses `ON CONFLICT (version) DO UPDATE` so v7–v9's
  self-stamp doesn't cause a PK violation on green-field installs.
- The v8 SQL file was patched to seed the `operator` role alongside
  `builder-fallback`; previously the operator role existed only via a
  manual insert on the dev DB, which masked an FK ordering bug for
  green-field installs.

---

## Environment Variables

| Variable | Default | Used by | Description |
|---|---|---|---|
| `TACKLE_SRV_PORT` | `3410` | `index.ts` | REST listen port |
| `TACKLE_PG_DSN` | `process.env.CONDUIT_PG_DSN` → `postgresql://pguser:pgpass@localhost:5432/nexus` | `db.ts` | PostgreSQL DSN |
| `CONDUIT_PG_DSN` | (fallback) | `db.ts` | Fallback DSN |
| `MEMORY_REDIS_URL` | `redis://localhost:6379` | `memory.ts` | Redis connection for the Role Memory reader |
| `MEMORY_SRV_URL` | `http://localhost:3500` | `memory.ts` | `role-memory-srv` URL for `POST /memory/refresh` proxy |

## Rate limiting

An app-level limiter caps requests at **300 req/min/IP** and advertises the
draft-7 `RateLimit` / `RateLimit-Policy` headers; exceeding the ceiling returns
`429`. This is a CodeQL `js/missing-rate-limiting` remediation — the ceiling is
well above normal operator/agent polling, so it only bites on floods.

## Session log path containment

`/log/:sessionId` (SSE) and the `/config/ai/test` + `/config/ai/verify` session
logs validate `sessionId` against `/^[a-zA-Z0-9_-]+$/` and then resolve the path
defensively. Because a lexical `startsWith` guard does not cover a symlink swapped
in *after* the check, both paths also re-resolve with `fs.realpathSync` and confirm
the real target is still inside the logs/sessions directory. This is a CodeQL
`js/path-injection` remediation.

## Tests

```bash
npm test        # vitest
npm run typecheck
```

The Express app lives in `src/app.ts` and the process lifecycle (DB init, Redis,
listen, heartbeat, signal handlers) in `src/index.ts`, so tests import the real
app without binding a port.

---

## Source File Map

| File | Purpose |
|---|---|
| `src/index.ts` | Express server, health, route mounting, graceful shutdown |
| `src/db.ts` | PG pool, `initDb()`, `createSchema()`, `migrations[]`, AI-config + sessions + roles + scheduler + tasks CRUD |
| `src/memory.ts` | Redis reader for the `mem:*` namespace + `triggerRefresh()` proxy to `role-memory-srv` |
| `src/routes/ai-config.ts` | `/config/ai/*` router (providers/harnesses/models/roles/bundles/resolve/test) |
| `src/routes/sessions.ts` | `/sessions/*` router |
| `src/routes/roles.ts` | `/roles/*` router |
| `src/routes/scheduler.ts` | `/scheduler/*` router |
| `src/routes/memory.ts` | `/memory/*` router |
| `src/routes/tasks.ts` | `/tasks/*` router — task registry + inspector dispatch |
| `src/routes/failure-recovery.ts` | `/config/failure-recovery/*` router |
| `src/env.ts` | `.env` loader |
| `src/errors.ts` | Error helpers |

---

# Recent REST API Changes

This section documents the new REST endpoints that landed across the tackle
service family over the recent commits (`bc0f1f6`, `d7dd3e1`, `3166311`).
The new endpoints live across **three** servers — they were placed where
their access pattern fits best, rather than forcing everything through
tackle-srv (:3410). This README records them so the full REST surface is
visible in one place.

## 1. `tackle-mcp` — new endpoint on port **3400**

**Commit `bc0f1f6`** — `feat(tackle-mcp): add /api/mcp/memory/role-updates`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/mcp/memory/role-updates` | Checkpoint endpoint. Returns per-role `{ last_active }` timestamps derived from the `mem:idx:*` Redis keys. Used by agents at turn start to see which roles have recent activity without polling individual indices. Response: `{ status, timestamp, checkpoints: { "<role>": { last_active: <ISO> } } }` |

The same commit also hardened tackle-mcp's `src/memory.ts`:
- **Weak-reference fallback** — `memory_get_procedures`, `memory_get_procedure`, and `memory_check_since` now try Redis first and fall back to PostgreSQL when the cache is cold or stale, so agents still receive data on a cache miss instead of erroring.
- **Connection timeout guard** — Redis client now initialized with `connectTimeout: 10000`, `keepAlive: 30000`, and lifecycle event logging (`connect`/`ready`/`error`/`close`/`reconnecting`).

## 2. `tackle-prompt-sync-srv` — new REST server on port **3501**

**Commit `d7dd3e1`** — `feat(tackle): add tackle-prompt-sync-srv + tackle-prompt-bridge`.

A new PG-to-Redis sync server for `tackle.prompts` + `tackle.tasks`,
mirroring `role-memory-srv`'s architecture but on a **disjoint key
namespace** (`prompt:*` / `task:*`, NOT `mem:*`) so the two sync servers
coexist without colliding. Reads from Redis so live agents can assemble
prompts at launch time without hitting PG.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | `{ status, lastUpdated, uptime, namespace: "prompt:" }` |
| `GET` | `/prompts/:role` | Role's `PromptIndexEntry[]` from `prompt:idx:{role}`; `[]` if not cached |
| `GET` | `/prompt/:role/:slug` | Full latest-version `PromptCard` from `prompt:proc:{role}::{slug}`; 404 if not cached |
| `GET` | `/tasks/:role` | Role's active `TaskIndexEntry[]` from `task:idx:{role}` (only `active=TRUE` rows — default-allowlist semantics); `[]` if not cached |
| `POST` | `/refresh` | Full PG → Redis sync. Idempotent. Returns `{ prompts, rolePromptIndices, tasks, roleTaskIndices, timestamp }`. Auto-invoked on every Redis `ready` event for self-healing after outages. |

The cache is populated by `syncAll()` in `src/sync.ts`:
- **`fetchLatestPrompts()`** runs `SELECT DISTINCT ON (role, slug) ... ORDER BY role, slug, version DESC` — MAX(version) resolution per (role, slug) — so launching agents always get the newest template revision.
- **`fetchActiveTasks()`** pulls only `active=TRUE` rows; retired tasks stay in PG for audit but never reach the cache.
- The pipeline resolves the `prompt_id → slug` join at sync time so task entries ship a precomputed `prompt_slug` (one Redis GET per task instead of a second PG round-trip).

## 3. `tackle-prompt-bridge` — stdio MCP server (NOT a REST server)

**Commit `d7dd3e1`** — MCP server that exposes the cached `tackle.prompts`
templates as **MCP prompt resources** (not tools). It is itself a stdio
MCP — `mcp-bridge` re-exposes it over SSE on **port 3135** (see §4 below).
No direct REST endpoints; consumes the Redis `prompt:*` cache populated by
`tackle-prompt-sync-srv`.

| MCP Method | Behavior |
|---|---|
| `prompts/list` | Enumerates prompts; optional `role` parameter scopes the listing. Names are `"{role}/{slug}"` so a single `prompts/get` resolves without out-of-band role state. |
| `prompts/get` | Returns the latest `body_md` as a `user`-turn text message plus a `_tackle` metadata block (`parameter_schema`, `version`, `tags`, timestamps). **Parameter substitution is deliberately the caller's responsibility** — the same template is reused across many task scopes. |
| Capabilities | Advertises only `prompts: {}` — no tools. |

## 4. `mcp-bridge` — new SSE target on port **3135**

**Commit `3166311`** — `feat(mcp-bridge): forward prompts/list + prompts/get, wire tackle-prompt-bridge on port 3135`.

Previously `mcp-bridge` (port 3131–3134) only forwarded `tools/list` and
`tools/call`. With this change it advertises **both** `tools` and `prompts`
capabilities and forwards all four RPC methods to the spawned stdio child:

| MCP Method | Forwarding | Notes |
|---|---|---|
| `tools/list` | through `client.listTools()` | Existing; verbatim; child owns schemas |
| `tools/call` | through `client.callTool()` | Existing; no arg validation on the bridge |
| `prompts/list` | through `client.listPrompts(params)` | New; pass-through `params` for per-role scope |
| `prompts/get` | through `client.getPrompt(params)` | New; no placeholder rendering on the bridge |

Tool-only children (knowledge/vision/peb/terrain) simply won't return
prompts — a mistargeted `prompts/get` errors on the child side rather than
masking the gap with a fake empty list.

**New target in the systemd unit:**
```
Environment=MCP_BRIDGE_PROMPT_PORT=3135
Environment=MCP_BRIDGE_PROMPT_CMD=npx
Environment=MCP_BRIDGE_PROMPT_ARGS=tsx;src/index.ts
Environment=MCP_BRIDGE_PROMPT_CWD=/home/codex/dev/nexus/typescript/tackle-prompt-bridge
Environment=MCP_BRIDGE_PROMPT_ENV_PROMPT_REDIS_URL=redis://localhost:6379
```

---

# Cross-server topology summary

The four recent commits spread REST/MCP surface across four services. The map
below keeps the whole picture legible:

| Server | Port | Namespace | Role |
|---|---|---|---|
| **tackle-srv** | 3410 | PG schema `tackle` (canonical) | REST for AI config, sessions, roles, scheduler, role-memory reader, breaker |
| **tackle-mcp** | 3400 | Redis `mem:*` (procedure cache) | MCP JSON-RPC + REST; new role-updates checkpoint endpoint |
| **tackle-prompt-sync-srv** | 3501 | Redis `prompt:*` / `task:*` (prompt cache) | New REST: read prompts/tasks cache + trigger PG → Redis sync |
| **tackle-prompt-bridge** | (stdio → SSE) | reads `prompt:*` | New stdio MCP server; re-exposed by `mcp-bridge` on **3135** |
| `role-memory-srv` | 3500 | writes `mem:*` | Pre-existing; not modified |
| `mcp-bridge` | 3131–3135 | spawns stdio MCPs | Pre-existing for tools; now also forwards `prompts/*`; new 3135 target |

---

# End-to-end flow (verified live on 2026-07-25)

```
[tackle.prompts in PG]  ──tackle-prompt-sync-srv POST /refresh──▶  [Redis prompt:*]
                                                                    │
                            .opencode/agents/<role>.md (pointer)   │
                                          │                         │
                                          ▼ stdio (spawn)           │
                            tackle-prompt-bridge ◀─────────────────┘
                                          │
                                          ▼ spawns + bridges via
                            mcp-bridge (port 3135 SSE)
                                          │
                                          ▼ SSE JSON-RPC
                            agents call prompts/get "<role>/opencode-persona"
                                          │
                                          ▼ return body_md + _tackle metadata
                            agent substitutes body into system-prompt slot
```

Smoke verified: `prompts/list` returns all 11 seeded prompts;
`prompts/get "engineer/opencode-persona"` returns the v2 engineer persona
(`version: 2`, post-conduit plan-routing disambiguation) with `_tackle`
metadata. See git log for `3166311` for the smoke artifact.


---

## REST API & OpenAPI

- Endpoint inventory: [`API.md`](./API.md) (generated from source route registrations)
- OpenAPI 3.0 spec: [`openapi.yaml`](./openapi.yaml) (generated from source route registrations)

Regenerate after route changes:

```bash
cd nexus
python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json
```
