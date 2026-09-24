# kernel-srv — Event-Sourced Kernel API

> Port: **8100**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Kernel event model: transition lifecycle, receipts, causality chains, aggregate event streams, active policy, and health views.

**14 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` |  |
| GET | `/api/kernel/aggregates/:aggregate_type/:aggregate_id/events` | 7. GET /api/kernel/aggregates/{type}/{id}/events wraps v_aggregate_events |
| GET | `/api/kernel/events/stream` | 12. SSE /api/kernel/events/stream live kernel events |
| GET | `/api/kernel/health/receipt-integrity` | 11. GET /api/kernel/health/receipt-integrity orphan-check Receipts with no matching transition_event.receipt_id back-link. Per the thread analysis, this measures the "sole write surface" invariant: every receipt should have a back-link on its event. |
| GET | `/api/kernel/health/recent-events` | 10. GET /api/kernel/health/recent-events wraps v_recent_events |
| GET | `/api/kernel/plans/:plan_number/receipts` | 6. GET /api/kernel/plans/{plan_number}/receipts wraps v_plan_receipts |
| GET | `/api/kernel/policy/active` | 8. GET /api/kernel/policy/active wraps v_active_policy |
| GET | `/api/kernel/policy/maturity` | 9. GET /api/kernel/policy/maturity (compiled-vs-data-driven ratio) |
| POST | `/api/kernel/receipts` | 4. POST /api/kernel/receipts wraps sys_issue_receipt() |
| GET | `/api/kernel/receipts/:id/chain` | 5. GET /api/kernel/receipts/{id}/chain wraps v_receipt_chain |
| POST | `/api/kernel/transitions` | 1. POST /api/kernel/transitions wraps sys_transition() |
| GET | `/api/kernel/transitions/:event_id` | 2. GET /api/kernel/transitions/{event_id} |
| GET | `/api/kernel/transitions/:event_id/causality` | 3. GET /api/kernel/transitions/{event_id}/causality (v_causality_chain scoped) |
| GET | `/health` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->











---

# kernel-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:8100`. JSON in/out. Event-sourced kernel: transition
> lifecycle (`kernel.transition_event`), receipts (`kernel.receipt`), causality
> chains, aggregate event streams, policy views, and health. Most routes wrap
> kernel SQL functions/views directly, so row shapes are the view columns.

## Transition envelope (kernel.transition_event)

`POST /api/kernel/transitions` — append a transition (wraps `sys_transition()`).
Request body:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_type` | string | **yes** | Kernel event type (constrained by `kernel.event_type` enum). |
| `aggregate_type` | string | **yes** | Aggregate kind. |
| `aggregate_id` | string | **yes** | Aggregate id. |
| `actor` | string | **yes** | Actor (system/agent/user). |
| `payload` | object | no | JSON body. |
| `authority` | string | no | Authority claim. |
| `receipt` | string | no | Receipt hash/reference. |
| `causation_id` | uuid | no | Parent event id. |
| `correlation_id` | uuid | no | Workflow correlation id. |

Response — **201**: the `sys_transition()` result row (full `transition_event`
columns). **400** `{status:"error", message}` (missing field). **403**
`{status:"error", message}` (kernel trigger `45000` auth/validation).
**500** `{status:"error", code:"KERNEL_WRITE_FAILED", message}`.

`GET /api/kernel/transitions/:event_id` — single transition. **200** row ·
**400** non-UUID · **404** `{status:"error", message}`.

`GET /api/kernel/transitions/:event_id/causality` — the full causality chain
scoped to this event (via `v_causality_chain`, `path @> [event_id]`). **200**:

```json
{ "root_event_id": "<uuid>", "chain": [ { "…causality view columns…", "depth": 0 } ], "depth": 3 }
```

## Receipt envelope (kernel.receipt)

`POST /api/kernel/receipts` — issue a receipt (wraps `sys_issue_receipt()`).
Request body: `receipt_type` (**req**), `receipt_hash` (**req**), `event_id`
(**req**, uuid), `issued_by` (**req**), `plan_number` (no), `metadata` (no, object).

Response — **201**: `sys_issue_receipt()` result row · **400** missing field ·
**403** trigger rejection · **500** `code:"RECEIPT_ISSUE_FAILED"`.

`GET /api/kernel/receipts/:id/chain` — receipts causally linked to a starting
receipt (via `v_receipt_chain`). **200**:
`{ "receipt_id", "event_id", "chain": [ …v_receipt_chain rows… ] }` ·
**400** non-UUID · **404** no receipt for id.

## Plan receipts envelope

`GET /api/kernel/plans/:plan_number/receipts` — all receipts for a plan (via
`v_plan_receipts`). **200**:
`{ "plan_number", "summary": <first row>, "chains": [ …all rows… ] }` ·
**400** missing plan_number · **404** no receipts for plan.

## Aggregate events envelope

`GET /api/kernel/aggregates/:aggregate_type/:aggregate_id/events` — the
aggregate's event stream (via `v_aggregate_events`). **200**:
`{ "aggregate_type", "aggregate_id", "aggregates": <row> }` · **400** missing
params · **404** no events for aggregate.

## Policy envelopes

`GET /api/kernel/policy/active` — **200**:
`{ "active_rules": [ …v_active_policy rows… ], "count": N }` (ordered by priority).

`GET /api/kernel/policy/maturity` — **200** (compiled vs data-driven ratio):

```json
{ "total_rules": 0, "enabled_rules": 0, "compiled_enabled": 0, "data_driven_enabled": 0, "disabled_rules": 0, "data_driven_pct": "0", "compiled_pct": "0" }
```

## Health envelopes

`GET /api/kernel/health/recent-events?limit=20` (clamp 1–500) — **200**:
`{ "recent": [ …v_recent_events rows… ], "count": N }`.

`GET /api/kernel/health/receipt-integrity` — orphan receipts with no
back-link on their transition event. **200**:
`{ "orphan_count": N, "orphans": [ { receipt_id, receipt_type, receipt_hash, event_id, issued_by, created_at } ] }`.

`GET /api/kernel/events/stream` — SSE live feed (`text/event-stream`).
Events: `event: ready` (once) then `event: kernel_event` with
`data: <KernelEvent JSON>` per committed transition; `: keepalive` every 15s.

## Error envelope

All errors: `{ "status": "error", "message": "…" }` with optional `"code"`
(e.g. `KERNEL_WRITE_FAILED`, `RECEIPT_READ_FAILED`, `POLICY_READ_FAILED`).
HTTP status: 400 bad request, 403 trigger rejection, 404 not found, 500 server.
