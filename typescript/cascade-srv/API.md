# cascade-srv — Event Query API

> Port: **3106**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Query service over the cascade event model: events with filtering, pagination and time-range aggregation, assessments, analytics.

**12 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Root health check |
| GET | `/cascade/analytics` | GET /analytics Aggregated event metrics for dashboards. |
| GET | `/cascade/assessments` | GET /assessments Assessment resolutions — how cascade assessors resolved events. |
| GET | `/cascade/events` | GET /events List events with filtering, pagination, and optional time-range aggregation. |
| GET | `/cascade/events/:id` | GET /events/:id Single event detail. |
| GET | `/cascade/events/:id/children` | GET /events/:id/children What events did this one trigger? (causation_id pointing back to this event) |
| GET | `/cascade/events/:id/lineage` | GET /events/:id/lineage Walk the causation chain backward (what triggered this event). |
| GET | `/cascade/health` | GET /health |
| GET | `/cascade/lineage` | GET /lineage Graph-style lineage query: nodes + edges between events. |
| GET | `/cascade/subscribers` | GET /subscribers List registered subscribers with their processing offsets. |
| GET | `/cascade/subscribers/:pattern` | GET /subscribers/:pattern Get a single subscriber by subject_pattern. |
| PATCH | `/cascade/subscribers/:pattern` | PATCH /subscribers/:pattern Update subscriber config (enable/disable). |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->














---

# cascade-srv — REST API & Envelope Spec (UI reference)

> **Hand-authored section — preserved across regeneration.** The endpoint table
> above is the generated inventory; everything below is the field-level contract
> for UI consumers. Base URL: `http://localhost:3106` (CORS enabled, JSON in/out).
>
> **Data model in one line:** `cascade.events` is the generic cross-system event
> ledger. Every row is an immutable **event envelope** describing one occurrence
> (`harvest.captured`, `candidate.discovered`, `harness.started`, …), stamped with
> aggregate identity, actor, and causation/correlation links for lineage replay.

## 1. The Event Envelope (canonical)

Every object returned by `/cascade/events`, `/cascade/events/:id`, and the lineage
endpoints is an event envelope with exactly these fields (column set of
`cascade.events`):

| Field | Type | Nullable | Description |
|-------|------|----------|-------------|
| `event_id` | `uuid` | no | Globally unique event id. |
| `event_type` | `string` | no | Dotted type: `<domain>.<entity>.<verb>` e.g. `harvest.captured`, `harness.started`. |
| `source` | `string` | no | Producer identity, e.g. `rover.planner`, `harness-srv.run`, `wind-srv`. |
| `event_timestamp` | `string` (ISO 8601) | no | When the occurrence happened (UTC). |
| `payload` | `object` | no | Event-specific body (jsonb, `{}` if empty). Shape varies by `event_type`. |
| `aggregate_type` | `string` | yes | Entity kind the event applies to: `harvest`, `harvest_candidate`, `harness_job`, `open_question`, … |
| `aggregate_id` | `string` | yes | The entity's id (uuid for most aggregates). |
| `actor_type` | `string` | no | `user` \| `agent` \| `system`. Default `system`. |
| `actor_id` | `string` | no | Actor name/id, e.g. `harness-srv`, `rover.planner`. Empty when unknown. |
| `correlation_id` | `uuid` | yes | Links all events of one workflow instance (same logical work). |
| `causation_id` | `uuid` | yes | The event that *directly* caused this one (immediate parent). |
| `caused_by_event_type` | `string` | yes | Semantic cause (parent's `event_type`), denormalized for filterability. |
| `sequence_number` | `string` (int64) | no | Monotonic per-row identity. ⚠ **Serialized as a string** (pg `bigint` → JSON) — parse with `Number()` if you need arithmetic. |
| `received_at` | `string` (ISO 8601) | no | When cascade persisted the row (≈ `event_timestamp` for live writes). |

**Conventions**

- **Naming:** `domain.entity.verb_past_tense` — `harvest.captured`,
  `candidate.discovered`, `candidate.promoted`, `intent_record.created`,
  `requirement.promoted_to_plan`, `harness.completed`, `admission.denied`.
- **Lineage:** `causation_id` → the triggering event. `correlation_id` → the whole
  workflow. A root event has `causation_id = null` and usually `correlation_id = null`.
- **Payload keys are not fixed** — they are defined per `event_type` by the
  producer. Common keys seen in the live ledger: `title`, `harvest_id`, `cpf`
  (candidate priority float), `from_state`/`to_state`, `risk_level`, `error`,
  `session_id`, `receipt`, `plan_id`.

### 1.1 Example envelope (live)

```json
{
  "event_id": "72c5e1a6-31f9-4c1f-a0ae-c3adb0f22a69",
  "event_type": "candidate.discovered",
  "source": "rover.batch_file_candidates",
  "event_timestamp": "2026-08-17T14:26:54.203Z",
  "payload": { "cpf": null, "title": "Investigate frontend transcript truncation limit", "harvest_id": "7ab3e5e7-41f3-4795-bd5e-8adc4e9f8073" },
  "aggregate_type": "harvest_candidate",
  "aggregate_id": "921176b5-7906-4266-9d68-518d5e807273",
  "actor_type": "agent",
  "actor_id": "",
  "correlation_id": null,
  "causation_id": null,
  "caused_by_event_type": null,
  "sequence_number": "17645",
  "received_at": "2026-08-17T14:26:54.214Z"
}
```

### 1.2 Known event types (live vocabulary)

| Event type | Typical source | Aggregate |
|-----------|----------------|-----------|
| `harvest.captured` | `rover.batch_harvest_to_db` | `harvest` |
| `candidate.discovered` | `rover.batch_file_candidates` | `harvest_candidate` |
| `candidate.assessed` | `rover.planner` | `harvest_candidate` |
| `candidate.escalated` | `rover.planner` | `harvest_candidate` |
| `candidate.greenlit` | `rover.planner` | `harvest_candidate` |
| `candidate.promoted` | `rover.candidate_promote` | `harvest_candidate` |
| `ripple.assessed` | `rover.planner` | `requirement` |
| `question.created` | `rover.planner` | `open_question` |
| `intent_record.created` | `rover.candidate_promote` | `intent_record` |
| `agenda.item_added` | `rover.candidate_promote` | `agenda_item` |
| `requirement.promoted_to_plan` | `rover.req_compiler` | `requirement` |
| `harness.started` / `harness.completed` / `harness.failed` / `harness.error` | `harness-srv.run*` | `harness_job` |
| `admission.denied` | `harness-srv.run-direct*` | — |
| `wind.ticket.completed` | `wind-srv` | — |
| `wind.instance.failed` / `wind.instance.completed` | `wind-srv` | — |
| `evaluation.started` / `evaluation.completed` / `evaluation.failed` | `rover.architect_process_todo` | — |
| `test.event` | `rover.event_emitter.cli` | — |

> UI tip: don't hard-code this list. `/cascade/analytics` returns `throughput`
> grouped by `event_type`, and `/cascade/events?type=…` filters by it.

## 2. REST Endpoints

All routes are mounted under `/cascade` (except `/`). Errors are always
`{ "error": "<message>" }` with the matching HTTP status (`400` bad request,
`404` not found, `500` server error, `503` health-down).

### 2.1 `GET /` — root info

```json
{ "name": "cascade-srv", "version": "1.0.0", "port": 3106 }
```

### 2.2 `GET /cascade/health` — health check

| Status | Body |
|--------|------|
| 200 | `{ "status": "ok", "schema": "cascade", "totalEvents": 17641, "time": "<ISO>", "port": 3106 }` |
| 503 | `{ "status": "error", "error": "<message>" }` (DB unreachable) |

### 2.3 `GET /cascade/events` — list events

Query parameters (all optional):

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `type` | string | — | Exact match on `event_type`. |
| `source` | string | — | Exact match on `source`. |
| `aggregate_id` | string | — | Exact match on `aggregate_id`. |
| `aggregate_type` | string | — | Exact match on `aggregate_type`. |
| `correlation_id` | uuid | — | Exact match on `correlation_id`. |
| `since` | ISO 8601 | — | `event_timestamp >= since`. |
| `until` | ISO 8601 | — | `event_timestamp <= until`. |
| `limit` | int | `50` | Page size, clamped to `≤ 200`. |
| `offset` | int | `0` | Page offset. |

Response — **200** (newest first by `event_timestamp DESC`):

```json
{
  "events": [ { "…event envelope…" } ],
  "total": 17641,
  "limit": 50,
  "offset": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `events` | array | Full event envelopes (see §1). |
| `total` | int | Total rows matching the filter (ignores paging). |
| `limit` / `offset` | int | The effective paging values used. |

### 2.4 `GET /cascade/events/:id` — single event

- **200** → one event envelope (full, §1).
- **404** → `{ "error": "Event not found" }`.

### 2.5 `GET /cascade/events/:id/children` — what this event triggered

Events whose `causation_id` points back at `:id` (newest-first by
`event_timestamp ASC`). Response — **200**:

```json
{
  "parent": "<event_id>",
  "children": [ { "event_id": "…", "event_type": "…", "aggregate_type": "…", "aggregate_id": "…", "source": "…", "event_timestamp": "…" } ]
}
```

⚠ Children rows are **reduced** (6 fields) — not full envelopes. Use
`/cascade/events/:id` per child for full detail.

### 2.6 `GET /cascade/events/:id/lineage` — causation chain (backward)

Walks `causation_id` from `:id` toward the root. Query params:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `maxDepth` | int | `10` | Max chain length, clamped to `≤ 20`. |

Response — **200**:

```json
{
  "anchor": "<event_id>",
  "chain": [ { "…event envelope + depth…" } ],
  "depth": 3
}
```

| Field | Type | Description |
|-------|------|-------------|
| `anchor` | string | The requested starting event id. |
| `chain` | array | Ancestors from `anchor` (depth 0) back through `causation_id` links, `ORDER BY depth ASC`. Each item is an event envelope **plus** `"depth": <int>`. |
| `depth` | int | Number of rows in the chain. |

### 2.7 `GET /cascade/lineage` — graph-style lineage

Nodes + edges for a subgraph. Query params:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `root` | uuid | — | Seed id; walks **forward** (what the seed caused). |
| `anchor` | uuid | — | Seed id; walks **backward** (what caused the seed). |
| `maxDepth` | int | `5` | Traversal depth, clamped to `≤ 15`. |
| `edgeType` | string | `caused_by` | Label applied to every edge. |

- **400** if neither `root` nor `anchor` is provided:
  `{ "error": "Provide ?root=<id> or ?anchor=<id>" }`.

Response — **200**:

```json
{
  "root": "<seed_id>",
  "direction": "forward" | "backward",
  "nodes": [ { "id": "…", "type": "…", "source": "…", "timestamp": "…", "depth": 0 } ],
  "edges": [ { "source": "<causation_id>", "target": "<event_id>", "type": "caused_by" } ],
  "truncated": false
}
```

| Field | Type | Description |
|-------|------|-------------|
| `root` | string | The seed id used. |
| `direction` | string | `forward` when `?root=`, `backward` when `?anchor=`. |
| `nodes` | array | `{ id, type, source, timestamp, depth }` — one per visited event. |
| `edges` | array | Directed edges `{ source, target, type }`; `source` is the causation parent, `target` the caused event. |
| `truncated` | bool | Heuristic flag when the traversal hit the depth guard (≈ `rows ≥ depth × 10`). |

### 2.8 `GET /cascade/analytics` — dashboard metrics

Query params:

| Param | Type | Default | Allowed |
|-------|------|---------|---------|
| `range` | string | `24h` | `1h` `6h` `24h` `7d` `30d` |
| `granularity` | string | `hour` | `minute` `hour` `day` |

Response — **200**:

```json
{
  "range": "24h",
  "granularity": "hour",
  "totalEvents": 1234,
  "throughput": [ { "event_type": "harvest.captured", "count": 300 } ],
  "timeline": [ { "bucket": "<ISO hour boundary>", "event_type": "…", "count": 12 } ],
  "pipelineFunnel": { "harvests": 0, "candidates": 0, "promoted": 0, "intent_records": 0, "plans": 0 },
  "topSources": [ { "source": "rover.planner", "count": 900 } ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `range` / `granularity` | string | Echo of the effective params (granularity normalized). |
| `totalEvents` | int | Event count in the window. |
| `throughput` | array | `{ event_type, count }` sorted by count desc. |
| `timeline` | array | `{ bucket, event_type, count }` bucketed by granularity, ascending. |
| `pipelineFunnel` | object | Distinct-aggregate counts across pipeline stages: `harvests` (`harvest.captured`), `candidates` (`candidate.discovered`), `promoted` (`candidate.promoted`), `intent_records` (`intent_record.created`), `plans` (`requirement.promoted_to_plan`). |
| `topSources` | array | `{ source, count }`, top 10 by volume. |

### 2.9 `GET /cascade/subscribers` — list subscribers

Response — **200**:

```json
{
  "subscribers": [
    {
      "subject_pattern": "nexus.cascade.v1.*",
      "handler_name": "projection_updater",
      "description": "…",
      "enabled": true,
      "created_at": "<ISO>",
      "last_processed": "<ISO>",
      "processed_ids": ["…"],
      "last_processed_at": "<ISO>",
      "lag": 12
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `subject_pattern` | string | Subscriber identity (also the PK). NATS subject pattern, e.g. `nexus.kernel.v1.transition.*`. |
| `handler_name` | string | Handler/daemon name. |
| `description` | string \| null | Human description. |
| `enabled` | bool | Whether the subscription is active. |
| `created_at` | ISO | Subscription creation time. |
| `last_processed` | ISO \| null | Offset's `last_timestamp` — the newest event the subscriber has consumed. |
| `processed_ids` | array | Recently processed event ids (jsonb, dedup window). |
| `last_processed_at` | ISO \| null | When the offset row was last updated. |
| `lag` | int | Events newer than `last_processed` (unconsumed backlog). `null` offset → counts all events. |

### 2.10 `GET /cascade/subscribers/:pattern` — single subscriber

- **200** → the subscriber row (§2.9 fields, with `last_timestamp` /
  `last_offset_at` naming instead of `last_processed`/`last_processed_at`).
- **404** → `{ "error": "Subscriber not found" }`.

### 2.11 `PATCH /cascade/subscribers/:pattern` — enable/disable

Request body:

```json
{ "enabled": true }
```

| Status | Response |
|--------|----------|
| 200 | `{ "subject_pattern": "…", "handler_name": "…", "enabled": true }` |
| 400 | `{ "error": "No fields to update" }` (missing `enabled`) |
| 404 | `{ "error": "Subscriber not found" }` |

### 2.12 `GET /cascade/assessments` — assessment resolutions

How cascade assessors resolved events (`nebula.assessment_resolutions` joined
with the event). Query params:

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `outcome` | string | — | Exact match on resolution outcome. |
| `event_id` | uuid | — | Filter to one event. |
| `limit` | int | `50` | Page size, clamped to `≤ 200`. |
| `offset` | int | `0` | Page offset. |

Response — **200** (newest first by `resolved_at DESC`):

```json
{
  "assessments": [
    {
      "resolution_id": "<uuid>",
      "event_id": "<uuid>",
      "outcome": "…",
      "confidence": 0.85,
      "rationale": { "…" },
      "dimensions_used": 3,
      "dimensions_total": 5,
      "resolved_at": "<ISO>",
      "event_type": "…",
      "source": "…",
      "payload": { "…" }
    }
  ],
  "total": 0,
  "limit": 50,
  "offset": 0
}
```

| Field | Type | Description |
|-------|------|-------------|
| `resolution_id` | uuid | Assessment resolution id. |
| `event_id` | uuid | The assessed event. |
| `outcome` | string | Resolution outcome label. |
| `confidence` | number | Confidence score 0–1. |
| `rationale` | object | Structured rationale (jsonb). |
| `dimensions_used` / `dimensions_total` | int | How many evaluation dimensions were exercised vs. available. |
| `resolved_at` | ISO | When resolved. |
| `event_type` / `source` / `payload` | mixed | Denormalized event fields (null if the event row was removed). |

## 3. Subscriber & offset tables (read-side reference)

The subscribers API is a join of two tables:

| Table | Columns |
|-------|---------|
| `cascade.subscriptions` | `subject_pattern` (PK), `handler_name`, `description`, `enabled`, `created_at` |
| `cascade.processing_offsets` | `subscriber_id` (PK, = `subject_pattern`), `last_timestamp`, `processed_ids` (jsonb array), `updated_at` |

`lag` in §2.9 = `COUNT(events WHERE event_timestamp > last_timestamp)` (all events
when the offset is null).

## 4. Notes for UI integration

- **CORS** is enabled; JSON is the only content type in/out.
- **Pagination:** always respect `limit`/`offset` and re-query with `total` for
  "N of M" displays. `limit` is capped at 200 server-side.
- **Time filters** (`since`/`until`) take ISO 8601 strings; the comparison is
  against `event_timestamp` (UTC).
- **`sequence_number` is a string** (int64) — do not do JS arithmetic on it
  without `Number()`.
- **Reduced shapes:** `/events/:id/children` returns 6-field rows; lineage chain
  items are envelopes + `depth`. Only `/events` and `/events/:id` return the full
  envelope.
- **This service is read-only.** Writes happen through producers
  (`rover.*`, `harness-srv`, `wind-srv`, kernel bridges) — see
  [`docs/events/stage3-canonical-event-types.md`](../../docs/events/stage3-canonical-event-types.md)
  for the canonical event-type and NATS-subject taxonomy.
