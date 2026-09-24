# knowledge-srv — Knowledge Graph REST API

> Port: **3109**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Knowledge graph surface: entities by section, relation edges, cross-references, migrations, and summary.

**17 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Root health check |
| GET | `/health` |  |
| GET | `/knowledge/cross-references` | GET /knowledge/cross-references?map_name=&source_section=&target_id=&limit=&offset= |
| POST | `/knowledge/cross-references` | graph_cross_references POST /knowledge/cross-references — create a cross-reference mapping |
| DELETE | `/knowledge/cross-references/:id` | DELETE /knowledge/cross-references/:id — delete a cross-reference by UUID |
| GET | `/knowledge/edges` | graph_edges GET /knowledge/edges?source_section=&source_id=&target_section=&target_id=&relation_type=&limit=&offset= |
| POST | `/knowledge/edges` | graph_cross_references POST /knowledge/edges — create a graph edge |
| DELETE | `/knowledge/edges/:id` | DELETE /knowledge/edges/:id — delete a graph edge by UUID |
| DELETE | `/knowledge/entities` | DELETE /knowledge/entities?section=... — purge an entire section (and its edges via cascade). The canonical eviction path for non-canonical sections (e.g. work_requests, plans). |
| GET | `/knowledge/entities` | graph_entities GET /knowledge/entities?section=&entity_type=&status=&search=&limit=&offset= |
| POST | `/knowledge/entities` | POST /knowledge/entities — create a graph entity (upsert on (section, entity_id)) |
| DELETE | `/knowledge/entities/:section/:entity_id` | DELETE /knowledge/entities/:section/:entity_id — delete a single graph entity (cascades edges) |
| GET | `/knowledge/entities/:section/:entity_id` | GET /knowledge/entities/:section/:entity_id |
| PUT | `/knowledge/entities/:section/:entity_id` | PUT /knowledge/entities/:section/:entity_id — update a graph entity |
| GET | `/knowledge/entities/:section/:entity_id/relations` | GET /knowledge/entities/:section/:entity_id/relations |
| GET | `/knowledge/migrations` | graph_migrations GET /knowledge/migrations?limit= |
| GET | `/knowledge/summary` | summary GET /knowledge/summary |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->












---

# knowledge-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3109` (CORS enabled, JSON in/out). Read-side knowledge
> graph over `knowledge.postgres`: `graph_entities`, `graph_edges`,
> `graph_cross_references`, `graph_migrations`.

## Entity envelope (graph_entities)

`GET /knowledge/entities` — list with filters. Query params:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `section` | string | — | Exact match on `section`. |
| `entity_type` | string | — | Exact match on `entity_type`. |
| `status` | string | — | Exact match on `status`. |
| `search` | string | — | `ILIKE %…%` over `name` and `description`. |
| `limit` | int | `100` | Clamped 1–500. |
| `offset` | int | `0` | Page offset. |

Response — **200**:

```json
{
  "entities": [ { "id": "…", "section": "plans", "entity_id": "…", "name": "…", "entity_type": "…", "status": "…", "description_abbr": "…", "created_at": "<ISO>", "updated_at": "<ISO>" } ],
  "count": 2380, "limit": 100, "offset": 0
}
```

List rows abbreviate `description` to 500 chars (`description_abbr`). The full
row (all columns) is returned by the detail route below.

`GET /knowledge/entities/:section/:entity_id` — single entity, all columns.
**200** full row · **404** `{ error: "Entity not found: …" }`.

`GET /knowledge/entities/:section/:entity_id/relations` — edges touching an entity:

```json
{
  "entity": { "section": "…", "entity_id": "…" },
  "outbound": { "count": 1, "edges": [ { "id": "…", "relation_type": "implements", "target_section": "…", "target_id": "…", "properties": {}, "target_name": "…" } ] },
  "inbound":  { "count": 0, "edges": [] }
}
```

## Edge envelope (graph_edges)

`GET /knowledge/edges` — filters: `source_section`, `source_id`, `target_section`,
`target_id`, `relation_type`, `limit` (default 100, clamp 1–500), `offset` (0).
Response — **200**:

```json
{
  "edges": [ { "id": "…", "source_section": "…", "source_id": "…", "relation_type": "implements", "target_section": "…", "target_id": "…", "properties": {}, "created_at": "<ISO>", "source_name": "…", "target_name": "…" } ],
  "count": 3907, "limit": 100, "offset": 0
}
```

## Cross-reference envelope (graph_cross_references)

`GET /knowledge/cross-references` — filters: `map_name`, `source_section`,
`target_id`, `limit` (default 100, clamp 1–500), `offset` (0). Response — **200**:
`{ "crossReferences": [ { id, map_name, source_section, source_id, target_section, target_id, weight, created_at } ], "count", "limit", "offset" }`.

## Migration envelope (graph_migrations)

`GET /knowledge/migrations?limit=20` (clamp 1–100). Response — **200**:
`{ "migrations": [ { id, source_file, file_checksum, entity_count, edge_count, cross_ref_count, version, migrated_at } ], "count", "limit" }`.

## Summary envelope (GET /knowledge/summary)

Live counts + distributions. Response — **200**:

```json
{
  "entityCount": 2380, "edgeCount": 3907, "crossReferenceCount": 0, "migrationCount": 19,
  "bySection": [ { "section": "work_requests", "count": 1932 } ],
  "byRelationType": [ { "relation_type": "implements", "count": 1907 } ]
}
```

## Root & health

- `GET /` — `{ name, version, port, source, endpoints: [...] }`
- `GET /health` — **200** `{ status: "healthy", port, db: "up" }` · **503** `{ status: "unhealthy", error }`.

## Notes

- All error responses are `{ error, message }` with HTTP 500 (or 404 for missing entities).
- Read-only — writes happen through the harvest/migration pipeline, not this service.
