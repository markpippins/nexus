# role-memory-srv — Role Memory Procedure Registry

> Port: **3500**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Role Memory Procedure Registry: procedure cards and indexes, with a PG→Redis refresh endpoint.

**4 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` |  |
| GET | `/procedure/:slug` |  |
| GET | `/procedures/:role` |  |
| POST | `/refresh` | Refresh (repopulate Redis from PG) |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->












---

# role-memory-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3500`. JSON in/out. Role Memory Procedure Registry:
> procedure cards and per-role indexes, cached in Redis and synced from
> PostgreSQL (`tackle.memory` / `tackle.role_memory`).

## Procedure index envelope (GET /procedures/:role)

Returns the role's procedure-card index (Redis `mem:idx:{role}`). Response — **200** (array; empty `[]` when the role has no index):

```json
[
  { "slug": "agent-config-template", "summary": "…", "tags": ["reference", "config"] }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `slug` | string | Procedure card slug (used for `GET /procedure/:slug`). |
| `summary` | string | One-line description of the card. |
| `tags` | array[string] | Card tags. |

## Procedure card envelope (GET /procedure/:slug)

Full procedure card (Redis `mem:proc:{slug}`). Response — **200**: the stored
card JSON — typically `{ slug, title, summary, tags, body, triggers, mcp_tools, steps, … }`
(fields defined by the card schema in PG). **404** `{ error: "Procedure not found" }`
when the slug is not cached.

## Refresh (POST /refresh)

Triggers a full PG→Redis sync (`syncAll()`). Response — **200**:

```json
{ "procedures": 42, "roleIndices": 6 }
```

The service also auto-syncs on every Redis `ready` event, so the cache
repopulates after outages without manual refresh.

## Health (GET /health)

**200** `{ status: "ok", lastUpdated: "<ISO>" | null, uptime }` · **503**
`{ status: "error", message }` (Redis unreachable).

## Notes

- Read-only cache service; all mutations happen through `POST /refresh`.
- Redis key namespace: `mem:meta:last_updated`, `mem:idx:{role}`, `mem:proc:{slug}`.
