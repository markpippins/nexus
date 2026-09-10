# wind-srv — Wind Workflow Schema API

> Port: **3300**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

REST API for the wind workflow schema: offices, titles, tasks, workflow graphs, runtime instances, tickets, and receipts.

**72 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/edges` | List edges for a version |
| POST | `/api/edges` | Create edge |
| DELETE | `/api/edges/:id` | Delete edge |
| GET | `/api/event-types` | List event types |
| POST | `/api/event-types` | Register a new event type |
| DELETE | `/api/event-types/:eventType` | Delete event type |
| GET | `/api/event-types/:eventType` | Get single event type |
| GET | `/api/events` | List events (with optional filters) |
| POST | `/api/events` | Create event |
| GET | `/api/events/:id` | Get single event |
| POST | `/api/events/poll` | Poll unconsumed events (FOR UPDATE SKIP LOCKED) |
| GET | `/api/execution-requests` | List immutable execution requests. |
| POST | `/api/execution-requests` | Create or replay an immutable request. Same idempotency key + same digest is a replay; same key + different material is a conflict, never an overwrite. |
| GET | `/api/execution-requests/:id` |  |
| GET | `/api/execution-requests/:id/attempts` | List attempts for one request. |
| POST | `/api/execution-requests/:id/attempts` | Record one final provider outcome. The route never calls the provider. |
| GET | `/api/execution-requests/:id/receipts` | Issue exactly one advisory receipt for an attempt; receipt status/digest must equal the immutable attempt, so malformed or contradictory results fail closed. |
| POST | `/api/execution-requests/:id/receipts` |  |
| GET | `/api/instances` | List instances (optionally filter by status or workflow_id) |
| POST | `/api/instances` | Start a workflow instance Creates an instance and tickets for the entrypoint node(s) |
| GET | `/api/instances/:id` | Get instance by ID (with tickets) |
| POST | `/api/instances/:id/advance` | Advance — complete a ticket with an outcome, create next tickets This is the core workflow traversal step |
| POST | `/api/instances/:id/execute` | Execute — run the harness for a ticket's task |
| POST | `/api/instances/:id/pause` | Pause an instance |
| POST | `/api/instances/:id/resume` | Resume a paused instance |
| POST | `/api/instances/:id/run` | Run an instance to completion (loop: execute → advance → … until terminal). |
| POST | `/api/instances/:id/stop` | Stop (cancel) an instance |
| GET | `/api/nodes` | List nodes for a version |
| POST | `/api/nodes` | Create node |
| DELETE | `/api/nodes/:id` | Delete node |
| GET | `/api/nodes/:id` | Get node by ID |
| PUT | `/api/nodes/:id` | Update node |
| GET | `/api/offices` | List all offices |
| POST | `/api/offices` | Create office |
| DELETE | `/api/offices/:id` | Delete office (cascade deletes titles, tasks, outcomes) |
| GET | `/api/offices/:id` | Get office by ID |
| PUT | `/api/offices/:id` | Update office |
| GET | `/api/outcomes` | List outcomes for a task |
| POST | `/api/outcomes` | Create outcome |
| DELETE | `/api/outcomes/:id` | Delete outcome |
| GET | `/api/outcomes/:id` | Get outcome by ID |
| GET | `/api/receipts` | List receipts (optionally filter by ticket) |
| GET | `/api/receipts/:id` | Get receipt by ID |
| GET | `/api/tasks` | List tasks (optionally filter by office) |
| POST | `/api/tasks` | Create task |
| DELETE | `/api/tasks/:id` | Delete task |
| GET | `/api/tasks/:id` | Get task by ID (with outcomes) |
| PUT | `/api/tasks/:id` | Update task |
| GET | `/api/tickets` | List tickets (optionally filter by instance, status, or title) |
| GET | `/api/tickets/:id` | Get ticket by ID |
| POST | `/api/tickets/:id/cancel` | Cancel a ticket |
| PUT | `/api/tickets/:id/status` | Update ticket status (e.g., PENDING → IN_PROGRESS) |
| GET | `/api/titles` | List titles (optionally filter by office) |
| POST | `/api/titles` | Create title |
| DELETE | `/api/titles/:id` | Delete title |
| GET | `/api/titles/:id` | Get title by ID |
| PUT | `/api/titles/:id` | Update title |
| GET | `/api/v-roles` | List roles (from nebula.roles via wind.v_roles view) |
| GET | `/api/v-roles/:name` | Get role by name |
| GET | `/api/validate/:version_id` | Validate a workflow version's graph integrity Uses the v_workflow_graph_validation view |
| POST | `/api/validate/:version_id/structure` | Validate a workflow version has required structure Checks: at least one entrypoint, at least one terminal, no orphaned nodes |
| GET | `/api/versions` | List versions for a workflow |
| POST | `/api/versions` | Create version (auto-increments version_number) |
| DELETE | `/api/versions/:id` | Delete version |
| GET | `/api/versions/:id` | Get version by ID (with nodes and edges) |
| POST | `/api/versions/:id/activate` | Activate a version (deactivate all others for that workflow) |
| GET | `/api/workflows` | List all workflows |
| POST | `/api/workflows` | Create workflow |
| DELETE | `/api/workflows/:id` | Delete workflow |
| GET | `/api/workflows/:id` | Get workflow by ID (with versions) |
| PUT | `/api/workflows/:id` | Update workflow |
| GET | `/health` | Health check |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->











---

# wind-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3300`. JSON in/out (CORS). Workflow schema + runtime over
> the `wind` PostgreSQL schema: `offices`, `titles`, `tasks`, `workflows`,
> `workflow_versions`, `workflow_nodes`, `workflow_edges`, `workflow_instances`,
> `tickets`, `receipts`, `event_types`, `events`. Errors: `{ status, message }`
> or `{ error, message }` with 400 (bad request), 404 (not found), 500.

## Offices → Titles → Tasks hierarchy

The wind org model is three levels. Deleting an office cascade-deletes its
titles, tasks, and outcomes.

| Endpoint | Request body | Response — **200/201** |
|----------|--------------|------------------------|
| `GET /api/offices` | — | `[ { "id", "name", "description", "created_at" } ]` |
| `POST /api/offices` | `{ name (**req**), description? }` | **201** created office |
| `GET /api/offices/:id` | — | single office |
| `PUT /api/offices/:id` | `{ name?, description? }` | updated office |
| `DELETE /api/offices/:id` | — | `{ deleted: true, id }` |
| `GET /api/titles?office_id=` | — | titles (optionally filtered by office) |
| `POST /api/titles` | `{ name (**req**), office_id (**req**), description? }` | **201** created title |
| `GET /api/titles/:id` | — | single title |
| `PUT /api/titles/:id` | `{ name?, office_id?, description? }` | updated title |
| `DELETE /api/titles/:id` | — | `{ deleted: true, id }` |
| `GET /api/tasks?office_id=` | — | tasks (optionally filtered by office) |
| `POST /api/tasks` | `{ title_id (**req**), task_slug (**req**), scope?, acceptance_criteria?, prompt_id?, role? }` | **201** created task |
| `GET /api/tasks/:id` | — | task **with outcomes** |
| `PUT /api/tasks/:id` | task fields | updated task |
| `DELETE /api/tasks/:id` | — | `{ deleted: true, id }` |

## Workflow graph envelopes

| Endpoint | Request body | Response |
|----------|--------------|----------|
| `GET /api/workflows` | — | `[ { "id", "name", "description", "created_at", "version_count", "active_version" } ]` |
| `POST /api/workflows` | `{ name (**req**), description? }` | **201** `{ id, name, description, created_at }` |
| `GET /api/workflows/:id` | — | workflow **with versions** |
| `PUT /api/workflows/:id` | `{ name?, description? }` | updated workflow |
| `DELETE /api/workflows/:id` | — | `{ deleted: true, id, name }` |
| `GET /api/versions?workflow_id=` | — | versions for a workflow |
| `POST /api/versions` | `{ workflow_id (**req**) }` | **201** version (auto-incremented `version_number`) |
| `GET /api/versions/:id` | — | version **with nodes and edges** |
| `DELETE /api/versions/:id` | — | `{ deleted: true, id }` |
| `POST /api/versions/:id/activate` | — | activates this version, deactivates others in the workflow |
| `GET /api/nodes?workflow_version_id=` | — | nodes for a version |
| `POST /api/nodes` | node fields | **201** created node |
| `GET /api/nodes/:id` | — | single node |
| `PUT /api/nodes/:id` | node fields | updated node |
| `DELETE /api/nodes/:id` | — | `{ deleted: true, id }` |
| `GET /api/edges?workflow_version_id=` | — | edges for a version |
| `POST /api/edges` | edge fields (`from_node_id`, `to_node_id`, `outcome_id`, `workflow_version_id`) | **201** created edge |
| `DELETE /api/edges/:id` | — | `{ deleted: true, id }` |

**Validation:**

`GET /api/validate/:version_id` — graph integrity via
`v_workflow_graph_validation`. **200** validation report.

`POST /api/validate/:version_id/structure` — structural checks (≥1 entrypoint,
≥1 terminal, no orphaned nodes). **200** `{ valid, issues: [...] }`.

## Event & event-type envelopes

| Endpoint | Request body | Response |
|----------|--------------|----------|
| `GET /api/event-types` | — | registered event types |
| `POST /api/event-types` | event-type fields | **201** registered |
| `GET /api/event-types/:eventType` | — | single event type |
| `DELETE /api/event-types/:eventType` | — | `{ deleted: true }` |
| `GET /api/events?event_type=&consumed=&limit=50` | — | `[ { "id", "event_type", "subject", "payload", "source", "created_at", "consumed_at", "metadata" } ]` (`consumed=true/false` filters by `consumed_at` null/not-null) |
| `POST /api/events` | `{ event_type (**req**), subject?, payload?, source? }` | **201** event; also published to NATS |
| `GET /api/events/:id` | — | single event · **404** |
| `POST /api/events/poll` | `{ limit? }` (default 10, clamp 100) | unconsumed events (`FOR UPDATE SKIP LOCKED`), oldest first |

## Instance runtime envelopes

`GET /api/instances?status=&workflow_id=` — **200**:
`[ { "id", "workflow_version_id", "status", "created_at", "updated_at", "workflow_name", "version_number" } ]`.

`POST /api/instances` — body `{ workflow_version_id (**req**) }`. Creates an
instance (`ACTIVE`) + PENDING tickets for entrypoint node(s). **201**:
`{ "id", "workflow_version_id", "status": "ACTIVE", "created_at", "updated_at", "tickets": [ { id, status, created_at } ] }`.
**400** missing version id / no entrypoint · **404** version not found.

`GET /api/instances/:id` — instance **with tickets**:
`{ "…instance…", "tickets": [ { "id", "status", "input_artifact_type", "input_artifact_id", "created_at", "updated_at", "node_name", "title_name" } ] }`.

`POST /api/instances/:id/pause` — `ACTIVE → PAUSED`. **200** `{ id, status, updated_at }`.
`POST /api/instances/:id/resume` — `PAUSED → ACTIVE`. **200**.
`POST /api/instances/:id/stop` — `ACTIVE|PAUSED → FAILED`. **200**.

`POST /api/instances/:id/execute` — body `{ ticket_id (**req**), context? }`.
Marks ticket `IN_PROGRESS` and calls harness-srv `/run`. **200**:

```json
{ "ticket_id": "…", "node_name": "…",
  "harness": { "job_id", "role", "exit_code", "stdout", "stderr", "duration_ms" },
  "outcome": { "code", "id", "confidence" } | null, "outcomes": [] }
```

**400** missing ticket_id / ticket not PENDING · **404** instance/ticket not found.

`POST /api/instances/:id/advance` — body `{ ticket_id (**req**), outcome_id (**req**) }`.
Completes the ticket, writes a receipt, and creates PENDING tickets for the
outcome's downstream edges; completes the instance when a terminal node is
reached and no tickets remain. **200**:
`{ "ticket_id", "outcome": "<code>", "new_tickets": [ { id, status, created_at } ] }`.

`POST /api/instances/:id/run` — body `{ max_steps?` (default 10), `timeout_ms?`
(default 120000) `}`. Fully automatic loop: execute → advance → repeat until
terminal/no PENDING tickets. **200**:

```json
{ "instance_id": "…", "final_status": "COMPLETED",
  "steps_executed": 3,
  "steps": [ { "step": 0, "action": "execute", "node_name": "…", "role": "…", "exit_code": 0, "outcome": "…", "confidence": "…", "duration_ms": 1000 } ] }
```

## Ticket & receipt envelopes

| Endpoint | Request body | Response |
|----------|--------------|----------|
| `GET /api/tickets?instance_id=&status=&title=` | — | ticket list |
| `GET /api/tickets/:id` | — | single ticket |
| `PUT /api/tickets/:id/status` | `{ status }` | updated ticket |
| `POST /api/tickets/:id/cancel` | — | cancelled ticket |
| `GET /api/receipts?ticket_id=` | — | receipts (optionally filtered) |
| `GET /api/receipts/:id` | — | single receipt |

Ticket statuses: `PENDING`, `IN_PROGRESS`, `COMPLETED`, `CANCELLED`, `stale`, `expired`.

## Advisory provider invocation contract

Before an execution request is persisted, Wind validates the provider envelope.
Version 1 requires provider and adapter identity, provider version, input and
output schema SHA-256 digests, an explicit invocation mode (`CLI`, `HTTP`,
`SDK`, or `MCP`), a timeout from 1 to 900000 ms, cancellation mode, retry
budget from 0 to 10, and an evidence-reference policy. Unavailable, timeout,
malformed-result, and stale cases must map to truthful non-success outcomes.

The validator is pure and performs no provider or harness I/O. It refuses
non-advisory authority and lifecycle admission/mutation fields. Dispatch is a
future bounded slice; Resolution/PEB remains the sole admission boundary.

## Roles view

`GET /api/v-roles` — **200** roles (from `nebula.roles` via `wind.v_roles`).
`GET /api/v-roles/:name` — single role by name · **404**.

## Health

`GET /health` — **200** `{ status: "ok", port: 3300, … }`.

## Notes

- Wind is both a schema store and a runtime: instance routes (`execute`,
  `advance`, `run`) drive harness-srv executions and produce
  `wind.ticket.completed` / `wind.instance.*` events.
- Events created via `POST /api/events` are also published to NATS
  (`nexus.wind.v1.*` subjects).
