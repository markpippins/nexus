# peb-srv — Push Event Bus API

> Port: **3111**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Push Event Bus: decisions, transactions, fleet health, events, entities, state, traces, and the SSE event stream.

**25 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/peb/binding-decisions` | GET /api/peb/binding-decisions?subject_id=&disposition=&decision_class=&limit=&offset= |
| GET | `/api/peb/binding-decisions/authority/:decisionClass` | activation). Returns the authority level carried by the durable peb.state row `binding_authority_mode` (migration V135) for the requested decision class. Fail-safe contract (mirrors peb-kernel binding_authority.py): no row, malformed content, a different class, or any DB error resolves to `advisory` |
| GET | `/api/peb/decisions` | List decisions GET /api/peb/decisions?status=&author_id=&adr_number=&affected_key=&limit=&offset= |
| POST | `/api/peb/decisions` | Create decision (ADR) POST /api/peb/decisions Body: { title, author_id, summary?, affected_keys?, entropy_class?, parent_decision_id?, rollback_of?, adr_number?, status?, transaction_id? } |
| GET | `/api/peb/decisions/:id` | Get decision by ID GET /api/peb/decisions/:id |
| PATCH | `/api/peb/decisions/:id` | Update decision PATCH /api/peb/decisions/:id Body: { title?, summary?, status?, affected_keys?, entropy_class?, parent_decision_id? } |
| GET | `/api/peb/decisions/:id/chain` | Chain traversal GET /api/peb/decisions/:id/chain?direction=ancestry\|rollback |
| POST | `/api/peb/decisions/:id/supersede` | Supersede a decision POST /api/peb/decisions/:id/supersede Body: { summary, author_id, affected_keys? } Creates a new decision that supersedes this one. |
| GET | `/api/peb/decisions/next-number` | Get next ADR number GET /api/peb/decisions/next-number |
| GET | `/api/peb/entities/:entity_id/capabilities` | Convenience: capabilities for an entity (list, with a status rollup) Not in the spec but useful for the UI to ground the gap view. |
| GET | `/api/peb/entities/:entity_id/capability-gap` | AND (c.expires_at IS NULL OR c.expires_at > v.created_at) gap_status: "active" : a live grant was in-window when the attempt fired "lapsed" : a grant had existed for this capability but had expired or been deactivated before the attempt "missing": no capability grant ever existed for this id + capab |
| GET | `/api/peb/events` | GET /api/peb/events?since=<cursor>&event_type=&plan_id=&agent_role=&limit=&offset= |
| GET | `/api/peb/events/:receipt_id` | GET /api/peb/events/{receipt_id} |
| POST | `/api/peb/events/:receipt_id/replay` | POST /api/peb/events/{receipt_id}/replay Spec: sets replayed_at, re-runs downstream effects. Implemented as: stamp replayed_at = now() (idempotent if already set), then publish a 'replay' event on /events/stream for any subscribers. |
| GET | `/api/peb/events/stream` | stream to plan_id / agent_role. Implementation note: we use a poll loop (1s) against the DB to surface new governance_events rows. A more elegant path is PG LISTEN/NOTIFY against a trigger on peb.governance_events after INSERT; we keep that as a TODO on the README and lean on the simpler poller for  |
| GET | `/api/peb/health/circuit-breakers` | GET /api/peb/health/circuit-breakers Spec: role_circuit_breaker, tripped-first sort |
| GET | `/api/peb/health/entropy` | GET /api/peb/health/entropy?group_by=entropy_class Spec: decisions.entropy_class over time — churn/stability signal. |
| GET | `/api/peb/health/violations/summary` | GET /api/peb/health/violations/summary?window=24h&group_by=severity\|violation_type\|entity_id |
| GET | `/api/peb/state/:key/diff` | (state_delta[K] overwrites prior value if present, removed markers null out a key from prior content). - Compare the two reconstructed snapshots for K. Return the diff: { from: <content>, to: <content>, added, removed, changed } - If `to` is the special value 'current', use the live peb.state row as |
| GET | `/api/peb/state/:key/versions` | dashboards that just want the version timeline. State diff: walk transactions that touched K in order, replay state_delta patches in order to derive the content at version N, and surface the diff between two user-supplied version ids. GET /api/peb/state/{key}/versions |
| GET | `/api/peb/traces/:id/tree` | A trace lives in `peb.traces` and may have a `parent_trace_id` pointing at another trace (could be in a different transaction). We return the full subtree rooted at `id` (including all descendants across transactions), followed by the chain of ancestors from `id` up to the root (so callers can rende |
| GET | `/api/peb/transactions` | GET /api/peb/transactions?entity_id=&tool_name=&admission_result=&since=&limit=&offset= |
| GET | `/api/peb/transactions/:id` | GET /api/peb/transactions/{id} |
| GET | `/api/peb/transactions/:id/lineage` | traces tree rooted at this transaction (via parent_trace_id), each node carrying confidence and rejected_alternatives any violations raised, joined against the capabilities the entity_id actually held at created_at (as-of join) governance_events with matching work_request_id/plan_id, ordered by crea |
| GET | `/health` |  |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->











---

# peb-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3111`. JSON in/out (CORS). Push Event Bus over the `peb`
> PostgreSQL schema: `governance_events`, `transactions`, `decisions`,
> `traces`, `violations`, `capabilities`, `state`, `role_circuit_breaker`.
> List endpoints use `limit` (default 100, clamp 1–500) + `offset`.
> Errors: `{ status: "error", message }` (400/404/500) or Express error-handler
> shapes with HTTP status.

## Governance event envelope (peb.governance_events)

`GET /api/peb/events?since=<cursor>&event_type=&plan_id=&agent_role=&work_request_id=&limit=&offset=`
— ordered by `id ASC`. **200**:

```json
{ "events": [ { "id": 1, "receipt_id": "…", "event_type": "receipt:PLAN_CREATE", "work_request_id": "…", "plan_id": "…", "agent_role": "…", "payload": {}, "created_at": "<ISO>", "replayed_at": null } ], "next_cursor": 45, "limit": 50, "offset": 0 }
```

`next_cursor` is the last row's `id` when the page is full, else `null` — use it
as the next `since` for cursor paging.

`GET /api/peb/events/:receipt_id` — latest event for a receipt. **200**
`{ "event": {…} }` · **404** `{ status:"error", message:"event not found" }`.

`POST /api/peb/events/:receipt_id/replay` — stamp `replayed_at = now()` +
broadcast a `replay` SSE event. **200** `{ "replayed": {…event row…} }`.

## Transaction envelope (peb.transactions)

`GET /api/peb/transactions?entity_id=&tool_name=&admission_result=&since=&limit=&offset=`
— **200**: `{ "transactions": [ { "id", "idempotency_key", "entity_id", "admission_result", "tool_name", "input", "output", "before_hash", "after_hash", "state_delta", "created_at", "committed_at", "kernel_event_id", "kernel_event_type" } ] }`.

`GET /api/peb/transactions/:id` — **200** `{ "transaction": {…} }` · **404**.

`GET /api/peb/transactions/:id/lineage` — full governance picture for a
transaction. **200**:

```json
{
  "transaction": { "…" },
  "decisions": [ { "…decisions row…" } ],
  "decision_chain": [ { "…", "depth": 0, "link": "direct|parent|rollback_of" } ],
  "traces": [ { "…traces row…" } ],
  "traces_tree": [ { "…", "children": [] } ],
  "violations": [ { "id", "violation_type", "severity", "capability_attempted", "context", "resolution", "created_at", "capability_grants_at_violation": [], "gap_detected": false } ],
  "governance_events": [ { "…" } ]
}
```

## Decision envelope (peb.decisions / ADR)

`GET /api/peb/decisions?status=&author_id=&adr_number=&affected_key=&limit=&offset=`
— **200**: `{ "decisions": [ { "id", "adr_number", "title", "status", "summary", "affected_keys", "entropy_class", "author_id", "parent_decision_id", "rollback_of", "created_at" } ], "limit", "offset" }`.
`affected_key` filters by array overlap on `affected_keys`.

`POST /api/peb/decisions` — body: `title` (**req**), `author_id` (**req**),
`summary?`, `affected_keys?`, `entropy_class?`, `parent_decision_id?`,
`rollback_of?`, `adr_number?`, `status?` (default `proposed`),
`transaction_id?`. Auto-assigns the next `ADR-###` number. **201** full row ·
**400** missing title/author_id.

`PATCH /api/peb/decisions/:id` — partial update (`title`, `status`,
`entropy_class`, `parent_decision_id`, `affected_keys`, `summary`).
Summary changes recompute `before_hash`/`after_hash`. **200** updated row ·
**404**.

`POST /api/peb/decisions/:id/supersede` — body: `summary` (**req**),
`author_id` (**req**), `title?`, `affected_keys?`. Creates a new `accepted`
decision and marks the old one `superseded`. **201**:
`{ "superseded": { id, adr_number }, "decision": {…new row…} }`.

`GET /api/peb/decisions/:id/chain?direction=ancestry|rollback` — recursive
chain walk (depth < 50). **200** `{ "direction", "chain": [ {…, "depth"} ] }`.

`GET /api/peb/decisions/next-number` — **200**
`{ "next": "ADR-004", "last": "ADR-003" }`.

## Entity governance envelopes

`GET /api/peb/entities/:entity_id/capability-gap?limit=&offset=` — violations
overlaid with capability grants at the moment of the attempt. **200**:

```json
{
  "entity_id": "…",
  "capability_gaps": [ { "violation_id", "violation_type", "severity", "capability_attempted", "context", "resolution", "violation_created_at", "active_grants_at_violation": [], "lapsed_grants_at_violation": [], "gap_status": "active|lapsed|missing" } ],
  "summary": { "active": 0, "lapsed": 0, "missing": 0 }
}
```

`GET /api/peb/entities/:entity_id/capabilities` — **200**:
`{ "entity_id", "capabilities": [ { "id", "entity_id", "capability", "granted_by", "expires_at", "active", "created_at", "status": "active|expired" } ] }`.

## Trace envelope (peb.traces)

`GET /api/peb/traces/:id/tree` — subtree + ancestors rooted at `id` (recursive
`parent_trace_id`). **200**:
`{ "root_id": "…", "node_count": N, "tree": [ { "id", "transaction_id", "work_request_id", "parent_trace_id", "stage", "inputs", "causal_entries", "rejected_alternatives", "confidence", "status", "created_at", "depth", "children": [] } ] }`
· **404** trace not found.

## State envelope (peb.state)

`GET /api/peb/state/:key/versions` — current value + version timeline derived
from `transactions.state_delta`. **200**:

```json
{ "key": "…", "current": { "id", "key", "content", "metadata", "checksum", "version", "created_at", "updated_at" } | null,
  "historical_versions": [ { "transaction_id", "created_at", "committed_at", "before_hash", "after_hash", "touched_key": true } ],
  "version_count": 3 }
```

`GET /api/peb/state/:key/diff?from=<tx_id>&to=<tx_id|current>` — reconstructed
snapshot diff. **200**:
`{ "key", "from": { transaction_id, content }, "to": { transaction_id, content }, "diff": { "added": {}, "removed": [], "changed": [{ "key", "from", "to" }] } }`
· **400** missing/invalid params · **404** no transactions touch key.

## Fleet-health envelopes

`GET /api/peb/health/circuit-breakers` — **200**:
`{ "circuit_breakers": [ { "role", "tripped", "tripped_at", "retry_after", "error", "failure_count", "updated_at", "state": "OPEN|RECOVERING|CLOSED" } ] }`.

`GET /api/peb/health/violations/summary?window=24h&group_by=severity|violation_type|entity_id`
— **200**: `{ "group_by", "window", "summary": [ { "key", "total", "resolved_total" } ] }`.
**400** invalid `group_by`.

`GET /api/peb/health/entropy?window=&group_by=entropy_class|author_id|status`
— **200**:
`{ "group_by", "window", "summary": [ { "key", "total", "last_seen", "first_seen" } ], "trend": [ { "day", "key", "total" } ] }`.
**400** invalid `group_by`.

## SSE event stream

`GET /api/peb/events/stream?plan_id=&agent_role=` — `text/event-stream`. Sends
`event: ready` first, then one event per new `governance_events` row (1s poll
loop), plus bus events (e.g. `replay`); `: keepalive` every 15s. Filters apply
to `plan_id` / `agent_role`.

## Health

`GET /health` — **200** `{ ok: true, dsn: "<masked>", port: 3111 }`.
