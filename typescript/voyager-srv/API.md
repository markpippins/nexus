# voyager-srv — Filesystem / Entity Voyager API

> Port: **3114**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Voyager over filesystems and entities: scan epochs, file/directory observations, topology signals and edge hints, identity candidates, entities, spans, requirements, and stats.

**17 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/entities` | agent lives above Voyager in the semantics layer. See legacy/identity.py and legacy/losm.py for aspirational code. ENTITIES GET /api/entities — list entities |
| GET | `/api/entities/:id` | GET /api/entities/:id — single entity with drift history |
| GET | `/api/entities/by-id/:entityId` | GET /api/entities/by-id/:entityId — lookup by entity_id (UUID) NOTE: must be declared BEFORE /:id to avoid route collision |
| GET | `/api/health` |  |
| GET | `/api/observations/directories` | DIRECTORY OBSERVATIONS GET /api/observations/directories — list directory observations |
| GET | `/api/observations/files` | FILE OBSERVATIONS GET /api/observations/files — list file observations (paginated, filterable) |
| GET | `/api/observations/files/:id` | GET /api/observations/files/:id — single file observation by surrogate id |
| GET | `/api/observations/files/by-id/:observationId` | GET /api/observations/files/by-id/:observationId — lookup by observation_id (UUID) NOTE: must be declared BEFORE /:id to avoid route collision |
| GET | `/api/scan-epochs` | SCAN EPOCHS GET /api/scan-epochs — list scan epochs (most recent first) |
| GET | `/api/scan-epochs/:id` | GET /api/scan-epochs/:id — single scan epoch |
| GET | `/api/spans` | METADATA SPANS GET /api/spans — list metadata spans (paginated, filterable) |
| GET | `/api/spans/:id` | GET /api/spans/:id — single metadata span |
| GET | `/api/stats` | Requirement candidates removed (T04: claim extraction lives above Voyager). STATS — summary across all voyager tables |
| GET | `/api/topology/edge-hints` | OBSERVATION EDGE HINTS GET /api/topology/edge-hints — list observation edge hints |
| GET | `/api/topology/signals` | TOPOLOGY SIGNALS GET /api/topology/signals — list topology signals |
| GET | `/api/topology/signals/:id` | GET /api/topology/signals/:id |
| GET | `/health` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->

---

# voyager-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3114`. JSON in/out. Read-side filesystem/entity observer
> over the `voyager` PostgreSQL schema. **All timestamps are returned as epoch
> milliseconds (numbers)** and **all column names are camelCased** (snake_case
> → camelCase) by the service. Paginated lists share one envelope.

## Paginated list envelope (shared)

```json
{ "items": [ { "…camelCase row…" } ], "total": 123, "page": 1, "pageSize": 50 }
```

Query params on every list endpoint: `page` (default 1), `pageSize`
(default per-endpoint, clamp 1–100).

## Scan epoch envelope (scan_epoch)

`GET /api/scan-epochs?page=&pageSize=` (pageSize default 20) — **200** paginated
list of scan epochs (most recent first). `GET /api/scan-epochs/:id` — **200**
single epoch · **404** `{ error: "Scan epoch not found" }`.

## File observation envelope (file_observation)

`GET /api/observations/files?page=&pageSize=&scanEpochId=&path=&deviceId=&inode=`
(pageSize default 50; `path` is ILIKE) — **200** paginated list ordered by
`discovered_at DESC`.

`GET /api/observations/files/:id` — single by surrogate id · **404**.
`GET /api/observations/files/by-id/:observationId` — single by
`observation_id` (UUID) · **404**. (Route order matters: `by-id` is registered
before `:id`.)

## Directory observation envelope (directory_observation)

`GET /api/observations/directories?page=&pageSize=&scanEpochId=&path=`
(pageSize default 50) — **200** paginated list ordered by `discovered_at DESC`.

## Topology envelopes

`GET /api/topology/signals?page=&pageSize=&scanEpochId=&structureType=` —
**200** paginated list (`structureType` filters `structure->>'type'`).
`GET /api/topology/signals/:id` — **200** single · **404**.

`GET /api/topology/edge-hints?page=&pageSize=&evidenceType=&minConfidence=` —
**200** paginated list (`evidenceType` filters `evidence->>'type'`,
`minConfidence` numeric filter on `confidence`).

## Entity envelope (entity + entity_drift)

`GET /api/entities?page=&pageSize=&minStability=&canonicalPath=` — **200**
paginated list ordered by `stability_score DESC` (`minStability` numeric filter;
`canonicalPath` ILIKE on `state->>'canonical_path'`).

`GET /api/entities/:id` and `GET /api/entities/by-id/:entityId` — **200** single
entity **with drift history**:

```json
{ "…entity camelCase row…", "drifts": [ { "…entity_drift camelCase row…" } ] }
```

· **404** `{ error: "Entity not found" }`.

## Metadata span envelope (metadata_span)

`GET /api/spans?page=&pageSize=&spanType=&markdownRole=&minConfidence=&observationId=`
(pageSize default 50) — **200** paginated list. Span columns: `id`, `spanId`,
`observationId`, `spanType`, `text`, `startPos`, `endPos`, `confidence`,
`markdownRole`, `discourseRole`, `eventCandidate`, `provenance`, `discoveredAt`.
`GET /api/spans/:id` — **200** single · **404**.

## Stats envelope

`GET /api/stats` — counts + latest epoch + span-type distribution. **200**:

```json
{ "fileObservations": 0, "directoryObservations": 0, "topologySignals": 0, "edgeHints": 0, "metadataSpans": 0, "scanEpochs": 0,
  "latestEpoch": { "id", "status", "startedAt" } | null,
  "spanTypes": [ { "spanType": "…", "count": 0 } ] }
```

## Health

- `GET /api/health` — **200** `{ status: "ok", db: true, service: "voyager-srv" }` · **503** `{ status: "error", message }`.
- `GET /health` — process-level health.

## Notes

- Read-only — scan data is produced by the crawler pipeline, not this service.
- Errors: `{ "error": "<message>" }` with HTTP 404 (missing row) / 500 (SQL failure).
