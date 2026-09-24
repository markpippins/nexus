# conduit-srv — WorkRequest Pipeline Orchestrator (REST surface)

> Port: **3104**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

REST surface of the conduit pipeline orchestrator: workflows, tickets, tokens, config (cron, failure-recovery), governance (replay, events), vision, and session log. The MCP tool surface is served separately (Streamable HTTP JSON-RPC on the same port).

**20 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Root health check |
| GET | `/config/cron` | GET /config/cron — pipeline cron interval |
| GET | `/config/failure-recovery` | GET /config/failure-recovery — from circuit_breaker row |
| POST | `/config/failure-recovery` | POST /config/failure-recovery — save failure recovery config |
| GET | `/governance/events` | GET /governance/events — list governance events with optional filters |
| POST | `/governance/replay` | POST /governance/replay — replay historical receipts into peb.governance_events |
| GET | `/health` |  |
| GET | `/log/:sessionId` |  |
| POST | `/tickets/detect` | POST /tickets/detect — detect stale and expired tickets |
| GET | `/tickets/lineage/:planId` | GET /tickets/lineage/:planId — ticket audit trail for a plan |
| GET | `/tokens/plan/:planId` | GET /tokens/plan/:planId |
| GET | `/tokens/role/:role` | GET /tokens/role/:role |
| GET | `/tokens/ticket/:ticketId` | GET /tokens/ticket/:ticketId |
| GET | `/vision/receipts` | GET /vision/receipts — list receipts for a plan |
| GET | `/vision/work-requests` | GET /vision/work-requests — list work requests with optional filters |
| POST | `/vision/work-requests` | POST /vision/work-requests — create or upsert a work request |
| GET | `/vision/work-requests/:id` | GET /vision/work-requests/:id — get a single work request |
| GET | `/workflows` |  |
| GET | `/wr/:id/projection-drift` |  |
| GET | `/wr/drift-scan` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->












---

# conduit-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3104`. JSON in/out (CORS). REST surface extracted from
> conduit-mcp (Architect decision: "No SQL in MCP Servers"). Routes are mounted
> at root (no `/api` prefix). Success responses carry `{ ok: true, … }`.

## Workflows envelope

`GET /workflows` — active sessions formatted for the UI. **200**:

```json
{
  "connected": true,
  "counts": { "running": 1, "completed": 0, "failed": 0, "cancelled": 0, "total": 1 },
  "workflows": [ { "workflowId": "plan-<id>-<role>", "runId": "<session id>", "status": "running", "startTime": "<ISO>", "closeTime": null, "planId": "", "role": "…", "pid": 123 } ]
}
```

## Ticket envelopes (vision.tickets)

`POST /tickets/detect` — mark stale (claimed + no activity 6h) and expired
(past `expires_at`) tickets; records kernel transitions. **200**:
`{ "detected": true, "stale": N, "expired": N, "timestamp": "<ISO>" }`.

`GET /tickets/lineage/:planId` — ticket audit trail for a plan. **200**:
`{ "plan_id": "…", "tickets": [ { "id", "role", "status", "tokens_used", "parent_ticket_id", "spawn_reason", "replacement_of", "closure_reason", "created_at", "closed_at" } ] }`.
**400** on invalid plan id.

## Token envelopes (vision.receipts / vision.tickets)

| Endpoint | Response — **200** |
|----------|-------------------|
| `GET /tokens/plan/:planId` | `{ "plan_id", "total_tokens": N, "receipts": N }` |
| `GET /tokens/role/:role` | `{ "role", "total_tokens": N, "receipts": N }` — role ∈ `builder\|reviewer\|planner\|critic`, else **400** |
| `GET /tokens/ticket/:ticketId` | `{ "ticket_id", "tokens_used": N }` |

## Config envelopes

`GET /config/cron` — **200**:
`{ "cron": "*/3", "intervalMinutes": 3, "description": "Every 3 minutes", "timestamp": "<ISO>" }`.

`GET /config/failure-recovery` — from the `circuit_breaker` row. **200**:
`{ "max_retries_per_model": 3, "retry_delay_seconds": 120, "max_fallbacks": 3, "push_back_to_pending": true, "circuit_breaker_retry_after": 1800 }`.

`POST /config/failure-recovery` — save config. Body (all optional): the five
fields above. **200** `{ "saved": true }` · **500** `{ error }`.

## Governance envelopes (peb.governance_events)

`POST /governance/replay` — backfill governance events for receipts missing
them. **200**: `{ "ok": true, "replayed": N }`.

`GET /governance/events?planId=&eventType=&limit=50` — **200**:
`{ "ok": true, "events": [ { "id", "receipt_id", "event_type", "work_request_id", "plan_id", "agent_role", "payload", "created_at", "replayed_at" } ] }`.

## Vision envelopes (vision.work_requests / vision.receipts)

`POST /vision/work-requests` — create/upsert a work request (atomic by `wr_id`).
Body: `id` (**req**), `work_request_uuid` (no), `dco_json` (no), `context`
(no, object), `status` (no, default `pending`), `title` (no), `entity_key` (no).
**200**: `{ "ok": true, "id", "work_request_uuid", "action": "created|updated" }` ·
**400** missing `id`.

`GET /vision/work-requests?status=&limit=50` — **200**:
`{ "ok": true, "work_requests": [ { "id", "wr_id", "work_request_uuid", "dco_json", "context", "status", "title", "recorded_on_dt", "updated_at" } ] }`.

`GET /vision/work-requests/:id` — single by `wr_id`. **200**
`{ "ok": true, "work_request": {…} }` · **404** `{ "ok": false, "error": "Not found" }`.

`GET /vision/receipts?planId=` (**required**) — **200**:
`{ "ok": true, "receipts": [ { "id", "plan_id", "type", "agent_role", "session_id", "ticket_id", "artifact_path", "summary", "metadata_json", "tokens_used", "created_at", "sequence" } ] }` ·
**400** missing `planId`.

## Session log (SSE)

`GET /log/:sessionId` — SSE stream of a session log file
(`text/event-stream`, `data:` frames). Meta frame:
`{ "type": "session_log_meta", "data": { sessionId, logFileExists, logPath } }`; then
`{ "type": "session_log", "data": { sessionId, line, timestamp, logType: "stdout|stderr" } }`
frames as the file grows; `: keepalive` every 15s. **400** invalid session id
(only `[a-zA-Z0-9_-]`).

## Projection-drift envelopes (vision.work_requests ↔ replay)

`GET /wr/:id/projection-drift` — live `work_request_state` vs event replay.
Accepts `wr_id` or `work_request_uuid`. **200**:
`{ "ok": true, "workRequestId": "<uuid>", "drift": { …expected vs live state/vision_stage/ir_version/last_event_id… } }` ·
**404** work request not found.

`GET /wr/drift-scan?limit=100&status=` — sweep active work requests.
**200**: `{ "ok": true, "scanned": N, "drifted": N, "findings": [ { "work_request_uuid", "wr_id", "status", "expected_state", "live_state", "expected_vision_stage", "live_vision_stage", "expected_vision_ir_version", "live_vision_ir_version", "expected_last_event_id", "live_last_event_id" } ] }`.

## Health & root

- `GET /health` — process + DB health.
- `GET /` — service banner: `{ name, version, port, source, description, endpoints: [...] }`.

## Error envelope

Most routes: `{ "ok": false, "error": "<message>" }` (vision/governance/tickets
family) or `{ "error": "<message>" }` (config/log/tokens family). Statuses:
400 bad request, 404 not found, 500 server.
