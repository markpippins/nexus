# tackle-prompt-sync-srv — Prompt + Task Registry Sync

> Port: **3501**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Reads prompt templates and active tasks from PostgreSQL and populates the Redis prompt:* / task:* caches for live agents.

**5 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/prompt/:role/:slug` |  |
| GET | `/prompts/:role` |  |
| POST | `/refresh` |  |
| GET | `/tasks/:role` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->











---

# tackle-prompt-sync-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3501`. JSON in/out. Reads prompt templates and active tasks
> from PostgreSQL and populates the Redis `prompt:*` / `task:*` caches consumed
> by tackle-prompt-bridge and tackle-mcp (`/prompts/get`).

## Prompt index envelope (GET /prompts/:role)

Role's prompt-template index (Redis `prompt:idx:{role}`). Response — **200** (array; empty `[]` when absent):

```json
[
  { "slug": "engineer", "version": 3, "summary": "…" }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `slug` | string | Prompt template slug (used with `GET /prompt/:role/:slug`). |
| `version` | number | Template version. |
| `summary` | string | One-line description. |

## Full prompt envelope (GET /prompt/:role/:slug)

Latest cached version of a prompt template (Redis `prompt:proc:{role}:{slug}`).
Response — **200**: `{ slug, version, body_md, … }` (schema from `tackle.prompts`).
**404** `{ error: "Prompt not found" }` when the role/slug pair is not cached.

## Task index envelope (GET /tasks/:role)

Role's active task index (Redis `task:idx:{role}`). Response — **200** (array; empty `[]` when absent):

```json
[
  { "id": "…", "task_slug": "…", "title": "…", "role": "…" }
]
```

## Refresh (POST /refresh)

Full PG→Redis sync (`syncAll()`), idempotent. Response — **200**:

```json
{ "prompts": 12, "tasks": 5, "rolePromptIndices": 3, "roleTaskIndices": 3 }
```

Auto-sync also runs on every Redis `ready` event (outage auto-heal).

## Health (GET /health)

**200** `{ status: "ok", lastUpdated: "<ISO>" | null, uptime, namespace: "prompt:" }` ·
**503** `{ status: "error", message }`.

## Notes

- Read-only cache service; mutations happen through `POST /refresh`.
- Redis key namespace: `prompt:idx:{role}`, `prompt:proc:{role}:{slug}`, `task:idx:{role}` — distinct from role-memory-srv's `mem:*` namespace.
