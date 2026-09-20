# WRP Kernel Runtime — REST API Reference

**Service:** WRP Kernel Runtime (FastAPI)
**Port:** `3103`
**Base URL:** `http://localhost:3103`
**OpenAPI:** `http://localhost:3103/docs` (Swagger UI) | `http://localhost:3103/openapi.json`

**Authentication:** Optional. Set `KERNEL_API_KEYS` or `KERNEL_API_KEY` environment variable to enable X-API-Key header validation. Auth bypass is automatic for `/healthz`, `/readyz`, `/metrics`, `/docs`, `/openapi.json`.

---

## Table of Contents

- [System Endpoints](#system-endpoints)
- [Delta Ingestion (POST /delta)](#1-delta-ingestion)
- [State Inspection (GET /state)](#2-state-inspection)
- [Replay (GET /replay)](#3-replay)
- [Admin (GET/PATCH/DELETE /admin)](#4-admin)
- [Sessions (GET/POST/PATCH /api/sessions)](#5-sessions)
- [Circuit Breaker (GET/POST /api/breaker)](#6-circuit-breaker)
- [Receipts (GET/POST/DELETE /api/receipts)](#7-receipts)

---

## System Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service root — returns service name, version, and docs URL |
| `GET` | `/healthz` | **Liveness probe** — returns `{"status": "alive"}` when the process is running |
| `GET` | `/readyz` | **Readiness probe** — checks DB connectivity + engine state. Returns 503 if DB is unreachable |
| `GET` | `/metrics` | **Prometheus metrics** — kernel request counters, duration histograms, and state gauges |

### Response: `GET /`

```json
{
  "service": "WRP Kernel Runtime",
  "version": "0.1.0",
  "docs": "/docs"
}
```

### Response: `GET /healthz`

```json
{"status": "alive"}
```

### Response: `GET /readyz`

```json
{"status": "ready", "kernel_version": 42}
```

On failure (503):
```json
{
  "error": {
    "code": "SERVICE_UNAVAILABLE",
    "message": "Database unreachable: could not connect to server"
  }
}
```

---

## 1. Delta Ingestion

**Prefix:** `/delta`

### `POST /delta` — Ingest a KernelDelta

Receives a KernelDelta JSON payload, validates it, and processes it through the full reduce pipeline (persist → reduce → lineage → snapshot).

#### Request Body

```json
{
  "delta_id": "delta-2026-07-25-001",
  "batch_id": "sync-cycle-42",
  "receipts": [
    {
      "id": "RCP-PLAN-0053-1",
      "plan_id": "plan_0053",
      "type": "PLAN_CREATE",
      "agent_role": "planner",
      "created_at": "2026-07-25T10:00:00Z"
    }
  ],
  "affected_plans": ["plan_0053"],
  "invalidated_plans": []
}
```

#### Response (200)

```json
{
  "success": true,
  "version": 43,
  "delta_id": "delta-2026-07-25-001",
  "plan_count": 12,
  "receipt_count": 87,
  "error": null
}
```

#### Response (failure)

```json
{
  "success": false,
  "version": 0,
  "delta_id": "delta-2026-07-25-001",
  "plan_count": 0,
  "receipt_count": 0,
  "error": "Invalid receipt type 'INVALID_TYPE' at position 0"
}
```

### `GET /delta/state` — State Summary

Returns a summary of current kernel state from the delta perspective.

#### Response

```json
{
  "version": 42,
  "plan_count": 12,
  "receipt_count": 87,
  "identity_count": 15,
  "graph_edge_count": 34,
  "lineage_event_count": 87
}
```

---

## 2. State Inspection

**Prefix:** `/state`

### `GET /state` — Inspect Kernel State

Returns the current KernelState. Supports summary and full views.

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `view` | string | `"summary"` | `"summary"` for counts, `"full"` for the entire state dict |

#### Response (summary)

```json
{
  "kernel_version": 42,
  "plan_count": 12,
  "receipt_count": 87,
  "identity_count": 15,
  "graph_edge_count": 34,
  "lineage_event_count": 87,
  "delta_log_count": 42
}
```

#### Response (full)

Returns all of the above plus the full serialized state in a `"state"` key.

### `GET /state/identity/{identity_id}` — Resolve Identity

Tries three lookup forms in order: canonical identity ID (`iden::plan_0053`), node ID (`plan_0053`), bare plan number (`0053` → prepends `plan_`).

#### Response

```json
{
  "id": "iden::plan_0053",
  "aliases": ["plan_0053", "0053"],
  "label": "Plan 0053",
  "edges_outgoing": [
    {"target": "iden::plan_0052", "relation": "wrp:depends_on", "metadata": {}}
  ],
  "edges_incoming": [
    {"source": "iden::plan_0054", "relation": "wrp:impacts_system", "metadata": {}}
  ]
}
```

**404** returned if identity is not found.

### `GET /state/receipt/{receipt_id}` — Get Receipt

Look up a single receipt by its receipt UUID.

#### Response

```json
{
  "id": "RCP-PLAN-0053-1",
  "receipt": {
    "id": "RCP-PLAN-0053-1",
    "plan_id": "plan_0053",
    "type": "PLAN_CREATE",
    "agent_role": "planner",
    "created_at": "2026-07-25T10:00:00Z"
  }
}
```

**404** if receipt not found.

### `GET /state/receipts-by-plan/{plan_num}` — Receipts by Plan

Returns all receipts for a given plan number.

#### Response

```json
{
  "plan_num": "plan_0053",
  "receipts": [
    {"id": "RCP-PLAN-0053-1", "plan_id": "plan_0053", "type": "PROPOSED", ...},
    {"id": "RCP-PLAN-0053-2", "plan_id": "plan_0053", "type": "PLAN_CREATE", ...}
  ],
  "count": 2
}
```

### `GET /state/graph` — Cross-Plan Graph

Returns all known identities as nodes plus typed relationship edges, with cursor-based pagination.

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `cursor` | string | `""` | Opaque cursor from previous response. Empty for first page |
| `limit` | int | `200` | Max edges to return (1–5000) |

#### Response

```json
{
  "nodes": [
    {"id": "iden::plan_0053", "aliases": ["plan_0053"], "label": "Plan 0053"},
    {"id": "iden::plan_0054", "aliases": ["plan_0054"], "label": "Plan 0054"}
  ],
  "edges": [
    {
      "source": "iden::plan_0053",
      "source_label": "Plan 0053",
      "target": "iden::plan_0054",
      "target_label": "Plan 0054",
      "relation": "wrp:depends_on",
      "metadata": {}
    }
  ],
  "total_edges": 34,
  "cursor": "iden::plan_0054",
  "limit": 200
}
```

### `GET /state/plan/{plan_num}` — Plan Detail

Returns a detailed profile of a single plan: identity, receipt timeline (chronological), current WRP state, valid next transitions, and graph edges.

#### Response

```json
{
  "plan_num": "plan_0053",
  "identity_id": "iden::plan_0053",
  "aliases": ["0053", "plan_0053"],
  "label": "Plan 0053",
  "receipt_count": 2,
  "current_wrp_state": "PLANNING",
  "valid_transitions": ["EXECUTING", "CANCELLED", "ARCHIVED"],
  "receipts": [
    {"id": "...", "type": "PROPOSED", "agent_role": "planner", "created_at": "...", "summary": "Initial proposal", "ticket_id": "TCK-..."},
    {"id": "...", "type": "PLAN_CREATE", "agent_role": "planner", "created_at": "...", "summary": "Plan created", "ticket_id": "TCK-..."}
  ],
  "edges_outgoing": [],
  "edges_incoming": []
}
```

**404** if plan not found.

### `GET /state/health` — Health Check

```json
{"status": "ok", "kernel_version": 42}
```

### `GET /state/lineage` — Lineage Events

Returns events from the append-only lineage event log.

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | None | Optional version filter |
| `limit` | int | `100` | Max events |

#### Response

```json
{
  "events": [
    {"id": 1, "version": 42, "delta_id": "delta-...", "step": "reduce", "event_type": "apply", "affected_plans": ["plan_0053"], "detail": "OK: 1 receipts"}
  ],
  "count": 1
}
```

---

## 3. Replay

**Prefix:** `/replay`

### `GET /replay` — Reconstruct State via KSRA

Reconstructs KernelState at any version using the formula:
```
KernelState(N) = Snapshot(K) + Replay(deltas K+1 → N)
```

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | None | Target version. `None` = latest |

#### Response

```json
{
  "version": 42,
  "plan_count": 12,
  "receipt_count": 87,
  "identity_count": 15,
  "graph_edge_count": 34,
  "lineage_event_count": 87,
  "reconstructed_from_version": 42
}
```

### `GET /replay/compare` — Compare Live vs Reconstructed

Deep structural comparison of live engine state vs reconstructed state at a given version.

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | **required** | Version to compare at |

#### Response

```json
{
  "match": true,
  "live_version": 42,
  "replay_version": 42,
  "live_plan_count": 12,
  "replay_plan_count": 12,
  "live_receipt_count": 87,
  "replay_receipt_count": 87,
  "live_identity_count": 15,
  "replay_identity_count": 15,
  "live_edge_count": 34,
  "replay_edge_count": 34,
  "diffs": []
}
```

If `match: false`, the `diffs` array contains descriptions of each discrepancy.

---

## 4. Admin

**Prefix:** `/admin`

### `GET /admin/identities` — List Identities

Paginated list of all known kernel identities.

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `cursor` | string | `""` | Cursor from previous response. Empty for first page |
| `limit` | int | `50` | Max items per page (1–500) |

#### Response

```json
{
  "identities": [
    {"id": "iden::plan_0053", "label": "Plan 0053", "aliases": ["plan_0053", "0053"], "node_ids": ["plan_0053"]}
  ],
  "total": 15,
  "cursor": "iden::plan_0054",
  "limit": 50
}
```

### `PATCH /admin/identities/{identity_id}` — Update Identity

Update identity metadata (label, aliases).

#### Request Body

```json
{
  "label": "Plan 0053 — Auth Module",
  "aliases": ["plan_0053", "0053", "auth-module-v2"]
}
```

#### Response

```json
{
  "id": "iden::plan_0053",
  "label": "Plan 0053 — Auth Module",
  "aliases": ["0053", "auth-module-v2", "plan_0053"],
  "updated": true
}
```

### `DELETE /admin/identities/{identity_id}` — Delete Identity

Removes an identity and its associated graph edges from the in-memory engine state. The delta log and receipt history are **preserved**.

#### Response

```json
{"ok": true, "identity_id": "iden::plan_0053"}
```

### `GET /admin/consistency` — Engine↔Delta-Store Alignment

Verifies the live engine state matches the persisted delta log.

#### Response

```json
{
  "aligned": true,
  "engine_version": 42,
  "delta_log_version": 42,
  "engine_plan_count": 12,
  "delta_log_count": 42,
  "details": [
    "Version aligned: engine=42 == delta_log=42",
    "Plans tracked: 12",
    "Delta log entries: 42"
  ]
}
```

---

## 5. Sessions

**Prefix:** `/api/sessions`

### `GET /api/sessions` — List All Sessions

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `running_only` | bool | `false` | Only return running sessions |

### `GET /api/sessions/running` — Running Sessions Only

### `GET /api/sessions/stale` — Detect Stale Sessions

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `threshold_seconds` | int | `3600` | Staleness threshold in seconds |

### `GET /api/sessions/{session_id}` — Get Single Session

### `PATCH /api/sessions/{session_id}/cost` — Update Session Cost

#### Request Body

```json
{"cost_usd": 1.50}
```

### `POST /api/sessions/{session_id}/heartbeat` — Update Heartbeat

#### Request Body (optional)

```json
{
  "role": "builder",
  "state": "running",
  "detail": "Executing plan 0053",
  "pid": 12345
}
```

### `POST /api/sessions/{session_id}/kill` — Kill Session

Force-kills a running session process.

#### Response

```json
{
  "killed": true,
  "sessionId": "sess-1234",
  "pids": [12345],
  "errors": [],
  "timestamp": "2026-07-25T10:00:00Z"
}
```

---

## 6. Circuit Breaker

**Prefix:** `/api/breaker`

### `GET /api/breaker` — Get Breaker State

```json
{
  "tripped": false,
  "paused": false,
  "retry_after": 1800,
  "source": "",
  "error": "",
  "detail": "",
  "tripped_at": null,
  "max_retries_per_model": 3,
  "retry_delay_seconds": 120,
  "max_fallbacks": 3,
  "push_back_to_pending": true
}
```

### `POST /api/breaker/trip` — Trip Breaker

#### Request Body (optional)

```json
{
  "reason": "BUDGET_EXCEEDED",
  "detail": "Agent budget ceiling ($500) reached",
  "retryAfter": 3600
}
```

### `POST /api/breaker/reset` — Reset Breaker + Abandoned Tickets

Resets the circuit breaker to untripped state and returns abandoned tickets to open status.

### `POST /api/breaker/pause` — Pause Conduit Orchestration

### `POST /api/breaker/resume` — Resume Conduit Orchestration

### `GET /api/breaker/failure-recovery` — Get Failure Recovery Config

### `POST /api/breaker/failure-recovery` — Save Failure Recovery Config

#### Request Body (all fields optional)

```json
{
  "max_retries_per_model": 5,
  "retry_delay_seconds": 300,
  "max_fallbacks": 3,
  "push_back_to_pending": true,
  "circuit_breaker_retry_after": 3600
}
```

---

## 7. Receipts

**Prefix:** `/api/receipts`

### `GET /api/receipts/{plan_id}` — Get Plan Receipts (Formatted)

Returns formatted receipts with parsed `metadata_json`.

### `GET /api/receipts/{plan_id}/raw` — Get Raw Receipts

Returns raw DB rows from `vision.receipts`.

### `GET /api/receipts/{plan_id}/latest-type` — Latest Receipt Type

```json
{"plan_id": "plan_0053", "latest_type": "PLAN_CREATE"}
```

### `POST /api/receipts` — Insert a Receipt

#### Request Body

```json
{
  "id": "RCP-UNIQUE-ID",
  "plan_id": "plan_0053",
  "type": "PLAN_CREATE",
  "agent_role": "planner",
  "session_id": "sess-1234",
  "ticket_id": "TCK-2026-0075",
  "artifact_path": "IMPLEMENTATION_PLANS/pending/my-plan.md",
  "summary": "Created plan for auth module",
  "metadata_json": "{\"key\": \"value\"}",
  "tokens_used": 1500,
  "created_at": "2026-07-25T10:00:00Z"
}
```

#### Response

```json
{"ok": true, "id": "RCP-UNIQUE-ID", "plan_id": "plan_0053"}
```

### `DELETE /api/receipts/{plan_id}` — Delete Receipts by Type (unblock family only)

> **Constrained endpoint.** Sole legitimate caller is conduit-mcp's
> `unblock_plan` tool (typescript/conduit-mcp/src/tools.ts ~L1905). It is a
> scoped, typed, operator-action-backed purge of the unblock receipt family —
> NOT a general-purpose receipt deleter. Receipt-immutability doctrine is
> untouched. Any type outside the unblock family is rejected with 400
> (architect ruling fcec95a2 #2).

#### Query Parameters

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `types` | string | **required** | Comma-separated receipt types to delete. Allowed: `BLOCK`, `PLAN_BLOCK`, `CANCELLED`, `ABANDONED` only. |

#### Response

```json
{"deleted": 2, "plan_id": "plan_0053", "types": ["BLOCK", "PLAN_BLOCK"]}
```

#### Errors

- `400` — any requested type outside the unblock family is rejected with a
  message enumerating the allowed set.

---

## Standard Error Envelope

All API errors use a consistent JSON envelope:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Identity not found: plan_9999",
    "details": null
  }
}
```

| HTTP Code | Error Code | Meaning |
|-----------|-----------|---------|
| 401 | `UNAUTHORIZED` | Missing or invalid `X-API-Key` header |
| 404 | `NOT_FOUND` | Resource not found |
| 422 | `VALIDATION_ERROR` | Request body validation failed |
| 500 | `INTERNAL_ERROR` | Server-side processing error |
| 503 | `SERVICE_UNAVAILABLE` | Database or engine not ready |

---

## Prometheus Metrics

The `/metrics` endpoint exposes the following counters and gauges:

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `kernel_requests_total` | Counter | method, path, status | Total HTTP requests |
| `kernel_request_duration_seconds` | Histogram | method, path | Request duration buckets |
| `kernel_version` | Gauge | — | Current kernel state version |
| `kernel_plan_count` | Gauge | — | Number of plans tracked |
| `kernel_receipt_count` | Gauge | — | Number of receipts stored |
| `kernel_identity_count` | Gauge | — | Number of resolved identities |
| `kernel_graph_edge_count` | Gauge | — | Number of graph edges |
| `kernel_lineage_event_count` | Gauge | — | Number of lineage events |
