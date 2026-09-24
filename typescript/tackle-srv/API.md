# tackle-srv — Tackle Role Memory + Agent Orchestration

> Port: **3410**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Tackle role memory and orchestration: AI config, sessions, roles, scheduler, memory, prompts, tool access, failure recovery, tasks, and logs.

**86 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/audit-trail` | GET /audit-trail — filtered listing with bounded default page |
| GET | `/audit-trail/recent` | GET /audit-trail/recent — convenience window (default last 24h, max 500) |
| GET | `/config/ai` | Full snapshot |
| POST | `/config/ai/bundle` |  |
| DELETE | `/config/ai/bundle/:id` |  |
| GET | `/config/ai/bundle/:id` |  |
| GET | `/config/ai/bundles` | Config Bundles |
| GET | `/config/ai/bundles/:role` |  |
| POST | `/config/ai/bundles/:role` |  |
| POST | `/config/ai/harness` |  |
| DELETE | `/config/ai/harness/:id` |  |
| GET | `/config/ai/harness/:id` |  |
| GET | `/config/ai/harnesses` | Harnesses |
| POST | `/config/ai/import` | Import full snapshot |
| POST | `/config/ai/model` |  |
| DELETE | `/config/ai/model/:id` |  |
| GET | `/config/ai/model/:id` |  |
| GET | `/config/ai/models` | Models |
| POST | `/config/ai/provider` |  |
| DELETE | `/config/ai/provider/:id` |  |
| GET | `/config/ai/provider/:id` |  |
| GET | `/config/ai/providers` | Providers |
| GET | `/config/ai/resolve/:role` | Resolved Config |
| POST | `/config/ai/role` |  |
| DELETE | `/config/ai/role/:role` |  |
| GET | `/config/ai/role/:role` |  |
| GET | `/config/ai/roles` | Role Configs |
| POST | `/config/ai/seed-defaults` | Seed defaults |
| POST | `/config/ai/test` | Test Invoke |
| GET | `/config/ai/tool-access` |  |
| POST | `/config/ai/tool-access` | POST /config/ai/tool-access — bulk-create allowlist rows for a role. Body: { role, tools: [{ mcp_id, tool_slug }] } or { role, tools: [tool_slug...] } (string entries are auto-wrapped with an empty mcp_id rollup). |
| PATCH | `/config/ai/tool-access/:id` |  |
| GET | `/config/ai/tool-access/:role` |  |
| POST | `/config/ai/tool-access/seed` | POST /config/ai/tool-access/seed — bulk-populate a role's allowlist from a template role (default-deny: new roles start with zero tools). Body: { role, fromRole } |
| GET | `/config/ai/validate` | Validate |
| POST | `/config/ai/verify` |  |
| GET | `/config/ai/verify/:sessionId` |  |
| POST | `/config/ai/verify/purge-unverified` | Purge unverified models from the inference chain Force-deactivates every config bundle (across all roles) whose model is unverified or missing, so no role's resolver queue can select a bundle whose model has not been certified. Returns per-role affected bundle counts. |
| GET | `/config/failure-recovery` |  |
| POST | `/config/failure-recovery` |  |
| GET | `/health` | Health |
| GET | `/health/history` | GET /health/history — time-series metrics |
| GET | `/health/metrics` | GET /health/metrics — current snapshot with full details |
| POST | `/health/simulate-load` | POST /health/simulate-load — no-op stub (live server has no load simulation) |
| GET | `/log/:sessionId` | Route mounting Session log SSE Stream nexus/logs/<sessionId>.log (test/verify invocations write there). Mirrors tackle-mcp's /log/:sessionId so the UI proxy chain (tackle-ui :4202 → tackle-srv :3410) can stream logs — previously the route only existed on tackle-mcp and the UI's log polls 404'd. |
| DELETE | `/logs` | DELETE /logs — clear all logs |
| GET | `/logs` | GET /logs — query with optional filters |
| POST | `/logs/emit` | POST /logs/emit — insert a single log entry |
| DELETE | `/memory/assign` | DELETE /memory/assign — unassign a procedure card from a role by expiring the active assignment (bitemporal-preserving soft delete). Body or query: { role, slug } |
| POST | `/memory/assign` | POST /memory/assign — assign procedure cards to a role. Body: { role, slugs: ["slug", ...] }. Writes tackle.role_memory, then triggers the PG→Redis refresh so the new assignments are live immediately. |
| POST | `/memory/check-since` |  |
| GET | `/memory/procedure/:slug` |  |
| GET | `/memory/procedures/:role` |  |
| POST | `/memory/refresh` |  |
| GET | `/memory/role-updates` |  |
| GET | `/projections` | Routes GET /projections — list all projection configs |
| POST | `/projections` | POST /projections — create new projection config |
| DELETE | `/projections/:id` | DELETE /projections/:id — delete a projection config |
| GET | `/projections/:id` | GET /projections/:id — get single projection config |
| PUT | `/projections/:id` | PUT /projections/:id — update projection config |
| POST | `/projections/:id/render` | POST /projections/:id/render — render a single projection |
| GET | `/projections/drift` | GET /projections/drift — compare on-disk sha vs last_sha256 for all enabled projections |
| POST | `/projections/render-all` | POST /projections/render-all — render all enabled projections |
| GET | `/prompts` |  |
| POST | `/prompts` |  |
| GET | `/prompts/:role` | GET /prompts/:role — list prompts for a role (wind-ui compat) |
| GET | `/prompts/:role/:slug` | GET /prompts/:role/:slug — single prompt by role+slug (wind-ui compat) |
| GET | `/roles` |  |
| POST | `/roles` |  |
| DELETE | `/roles/:id` |  |
| GET | `/roles/:id` |  |
| POST | `/roles/provision` | POST /roles/provision — atomic role setup orchestrator (Gap 1). Collapses role identity + config bundle + persona + tool access + procedure cards + nebula.roles sync + assembly user into one transaction, then returns the readiness report. See db.provisionRole for the spec shape. |
| GET | `/roles/readiness/:name` | GET /roles/readiness/:name — readiness checklist (must be registered BEFORE /:id so "readiness" isn't captured as an id). |
| GET | `/scheduler` |  |
| POST | `/scheduler` |  |
| DELETE | `/scheduler/:id` |  |
| GET | `/scheduler/:id` |  |
| PATCH | `/scheduler/:id` |  |
| GET | `/scheduler/due` |  |
| GET | `/sessions` |  |
| POST | `/sessions/:sessionId/kill` |  |
| GET | `/tasks` |  |
| POST | `/tasks` |  |
| DELETE | `/tasks/:task_slug` |  |
| GET | `/tasks/:task_slug` |  |
| GET | `/tasks/inspector/dispatch` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->












---

# tackle-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3410`. JSON in/out (CORS). Tackle role memory + agent
> orchestration over the `tackle` PostgreSQL schema: AI configuration
> (providers/models/harnesses/config bundles), roles, prompts, tool access,
> procedure memory, scheduler, sessions, logs, tasks, projections.
> Errors: `{ error: "<message>" }` with 400/404/500.

## AI config envelopes (/config/ai)

Config resource families, each with standard CRUD:

| Resource | List | Create | Get | Delete |
|----------|------|--------|-----|--------|
| Providers | `GET /config/ai/providers` | `POST /config/ai/provider` | `GET /config/ai/provider/:id` | `DELETE /config/ai/provider/:id` |
| Models | `GET /config/ai/models` | `POST /config/ai/model` | `GET /config/ai/model/:id` | `DELETE /config/ai/model/:id` |
| Harnesses | `GET /config/ai/harnesses` | `POST /config/ai/harness` | `GET /config/ai/harness/:id` | `DELETE /config/ai/harness/:id` |
| Config bundles | `GET /config/ai/bundles` | `POST /config/ai/bundle` | `GET /config/ai/bundle/:id` | `DELETE /config/ai/bundle/:id` |
| Role configs | `GET /config/ai/roles` | `POST /config/ai/role` | `GET /config/ai/role/:role` | `DELETE /config/ai/role/:role` |

**Snapshot & bulk:**

| Endpoint | Purpose |
|----------|---------|
| `GET /config/ai` | Full config snapshot (all families). |
| `POST /config/ai/import` | Import a full snapshot (replace). |
| `POST /config/ai/seed-defaults` | Seed default config. |
| `GET /config/ai/resolve/:role` | **Resolved config** for a role — the effective model/harness/bundle after priority + fallback resolution (what harness-srv uses). |
| `GET /config/ai/validate` | Validate config integrity. |
| `POST /config/ai/test` | Test-invoke a model. |
| `POST /config/ai/verify` | Verify a config (returns a session id). |
| `GET /config/ai/verify/:sessionId` | Verification result by session. |

**Tool access (default-deny allowlist):**

| Endpoint | Purpose |
|----------|---------|
| `GET /config/ai/tool-access` | All allowlist rows. |
| `GET /config/ai/tool-access/:role` | A role's allowlist. |
| `POST /config/ai/tool-access` | Bulk-create. Body: `{ role, tools: [{ mcp_id, tool_slug }] }` or `{ role, tools: ["slug", …] }` (strings auto-wrap with empty `mcp_id`). |
| `PATCH /config/ai/tool-access/:id` | Update one row. |
| `POST /config/ai/tool-access/seed` | Copy a template role's allowlist. Body: `{ role, fromRole }`. |

## Role envelopes (/roles)

| Endpoint | Purpose |
|----------|---------|
| `GET /roles` | List roles (with config bundles). |
| `POST /roles` | Create a role. |
| `GET /roles/:id` | Single role · **404**. |
| `DELETE /roles/:id` | Delete role. |
| `GET /roles/readiness/:name` | **Readiness checklist** for a role — which of identity/bundle/persona/tools/memory/nebula-sync/assembly-user are present. Registered before `/:id`. |
| `POST /roles/provision` | **Atomic role setup** — collapses role identity + config bundle + persona + tool access + procedure cards + nebula.roles sync + assembly user into one transaction; returns the readiness report. |

## Prompt envelopes (/prompts)

`GET /prompts` — all prompt templates. `POST /prompts` — create/update template.
`GET /prompts/:role` — prompts for a role (wind-ui compat). `GET /prompts/:role/:slug`
— single prompt by role+slug.

## Memory envelopes (/memory)

| Endpoint | Purpose |
|----------|---------|
| `GET /memory/procedures/:role` | Procedure cards assigned to a role. |
| `GET /memory/procedure/:slug` | Single procedure card · **404** if missing. |
| `POST /memory/assign` | Assign cards. Body: `{ role, slugs: ["…"] }` — writes `tackle.role_memory` then triggers PG→Redis refresh. |
| `DELETE /memory/assign` | Unassign a card (bitemporal-preserving soft delete). Body or query: `{ role, slug }`. |
| `POST /memory/refresh` | Refresh Redis from PG. |
| `POST /memory/check-since` | Check for updates since a timestamp. |
| `GET /memory/role-updates` | Role memory update stream. |

## Scheduler envelopes (/scheduler)

| Endpoint | Purpose |
|----------|---------|
| `GET /scheduler` | Scheduled entries. |
| `POST /scheduler` | Create entry. |
| `GET /scheduler/:id` | Single entry. |
| `PATCH /scheduler/:id` | Update entry. |
| `DELETE /scheduler/:id` | Delete entry. |
| `GET /scheduler/due` | Entries currently due (for the scheduler loop). |

## Sessions & logs

- `GET /sessions` — active agent sessions.
- `POST /sessions/:sessionId/kill` — kill a session.
- `GET /log/:sessionId` — **SSE stream** of `nexus/logs/<sessionId>.log`.
- `GET /logs` — query logs (optional filters). `POST /logs/emit` — insert one log
  entry. `DELETE /logs` — clear all logs.

## Tasks envelopes (/tasks)

| Endpoint | Purpose |
|----------|---------|
| `GET /tasks` | List tasks. |
| `POST /tasks` | Create task. |
| `GET /tasks/:task_slug` | Single task · **404**. |
| `DELETE /tasks/:task_slug` | Delete task. |
| `GET /tasks/inspector/dispatch` | Inspector dispatch queue. |

## Projection envelopes (/projections)

| Endpoint | Purpose |
|----------|---------|
| `GET /projections` | List projection configs. |
| `POST /projections` | Create config. |
| `GET /projections/:id` | Single config. |
| `PUT /projections/:id` | Update config. |
| `DELETE /projections/:id` | Delete config. |
| `POST /projections/:id/render` | Render one projection (DB → markdown file). |
| `POST /projections/render-all` | Render all enabled projections. |
| `GET /projections/drift` | Compare on-disk sha vs `last_sha256` for enabled projections — the drift signal. |

## Failure recovery & health

- `GET/POST /config/failure-recovery` — read/update retry config
  (`max_retries_per_model`, `retry_delay_seconds`, `max_fallbacks`, `push_back_to_pending`, `retry_after`).
- `GET /health` — process + DB/Redis health.
- `GET /health/metrics` — current snapshot with details.
- `GET /health/history` — time-series metrics.
- `POST /health/simulate-load` — no-op stub.

## Notes

- **Provider ranking:** resolve uses a fixed preference ladder
  (Nvidia → OpenRouter → OpenCode Go → OpenCode → Ollama → DeepSeek) to pick
  the primary bundle among active bundles for a role.
- **Default-deny:** new roles start with **zero** tool access until seeded from
  a template role.
