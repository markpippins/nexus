# wind-srv

REST API for the wind workflow schema. Serves the workflow data model for UI consumption — offices, titles, tasks, workflow graphs, runtime instances, tickets, and receipts.

**Port:** 3300  
**Base URL:** `http://localhost:3300/api`  
**Health:** `GET http://localhost:3300/health`

---

## Concepts

### Workflow Definition (what can happen)

```
Office
  └── Title          (a role-bound position: "who can do this work")
  └── Task           (a unit of work with input/output contracts)
        └── Outcome  (a possible result: "success", "rejected", etc.)

Workflow
  └── Version        (immutable snapshot of a graph)
        └── Node     (a step in the graph, bound to a Task)
        └── Edge     (routing: node + outcome → next node)
```

### Workflow Execution (what is happening)

```
Instance              (one run of a workflow version)
  └── Ticket          (a pending work assignment at a node)
  └── Receipt         (a completed work record with outcome)
```

### The Lifecycle

1. **Define** an office, titles, tasks, and outcomes
2. **Build** a workflow with versioned graphs (nodes + edges)
3. **Start** an instance → creates tickets at the entrypoint
4. **Advance** by completing tickets with outcomes → creates downstream tickets
5. **Complete** when all terminal nodes have receipts

---

## Quick Start

### 1. Create an office with a title

```bash
# Create office
curl -s -X POST http://localhost:3300/api/offices \
  -H 'Content-Type: application/json' \
  -d '{"name":"Feature Development","description":"Build new features"}'

# Get the office ID from the response, then create a title
# role_id should be a valid UUID from nebula.roles
curl -s -X POST http://localhost:3300/api/titles \
  -H 'Content-Type: application/json' \
  -d '{"office_id":"<office-uuid>","role_id":"<role-uuid>","display_name":"Implementer"}'
```

### 2. Create tasks with outcomes

```bash
# Create a task
curl -s -X POST http://localhost:3300/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"office_id":"<office-uuid>","title_id":"<title-uuid>","name":"Write Code","input_spec":{"language":"typescript"}}'

# Create outcomes for the task
curl -s -X POST http://localhost:3300/api/outcomes \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<task-uuid>","code":"approved","output_spec":{"files_changed":true}}'

curl -s -X POST http://localhost:3300/api/outcomes \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"<task-uuid>","code":"rejected","output_spec":{"reason":"string"}}'
```

### 3. Build a workflow graph

```bash
# Create workflow
curl -s -X POST http://localhost:3300/api/workflows \
  -H 'Content-Type: application/json' \
  -d '{"name":"Feature Pipeline"}'

# Create a version (auto-increments)
curl -s -X POST http://localhost:3300/api/versions \
  -H 'Content-Type: application/json' \
  -d '{"workflow_id":"<workflow-uuid>"}'

# Create nodes (use the version ID from above)
curl -s -X POST http://localhost:3300/api/nodes \
  -H 'Content-Type: application/json' \
  -d '{"workflow_version_id":"<version-uuid>","task_id":"<task-uuid>","name":"implement","is_entrypoint":true}'

curl -s -X POST http://localhost:3300/api/nodes \
  -H 'Content-Type: application/json' \
  -d '{"workflow_version_id":"<version-uuid>","task_id":"<task-uuid>","name":"done","is_terminal":true}'

# Create an edge: implement + approved → done
curl -s -X POST http://localhost:3300/api/edges \
  -H 'Content-Type: application/json' \
  -d '{"workflow_version_id":"<version-uuid>","from_node_id":"<implement-node>","from_task_id":"<task-uuid>","outcome_id":"<approved-outcome>","to_node_id":"<done-node>"}'

# Activate the version
curl -s -X POST http://localhost:3300/api/versions/<version-uuid>/activate
```

### 4. Validate the graph

```bash
curl -s http://localhost:3300/api/validate/<version-uuid>
```

### 5. Run it

```bash
# Start an instance
curl -s -X POST http://localhost:3300/api/instances \
  -H 'Content-Type: application/json' \
  -d '{"workflow_version_id":"<version-uuid>"}'

# Check the tickets
curl -s http://localhost:3300/api/tickets?instance_id=<instance-uuid>

# Advance: complete a ticket with an outcome
curl -s -X POST http://localhost:3300/api/instances/<instance-uuid>/advance \
  -H 'Content-Type: application/json' \
  -d '{"ticket_id":"<ticket-uuid>","outcome_id":"<approved-outcome>"}'

# Check instance status
curl -s http://localhost:3300/api/instances/<instance-uuid>
```

---

## Endpoints Reference

### Workflow Entities

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/offices` | List offices |
| POST | `/api/offices` | Create office |
| GET | `/api/offices/:id` | Get office |
| PUT | `/api/offices/:id` | Update office |
| DELETE | `/api/offices/:id` | Delete office (cascades) |
| GET | `/api/titles` | List titles (filter: `?office_id=`) |
| POST | `/api/titles` | Create title (needs `office_id`, `role_id`, `display_name`) |
| GET | `/api/titles/:id` | Get title |
| PUT | `/api/titles/:id` | Update title |
| DELETE | `/api/titles/:id` | Delete title |
| GET | `/api/tasks` | List tasks (filter: `?office_id=`) |
| POST | `/api/tasks` | Create task (needs `office_id`, `title_id`, `name`) |
| GET | `/api/tasks/:id` | Get task with outcomes |
| PUT | `/api/tasks/:id` | Update task |
| DELETE | `/api/tasks/:id` | Delete task (cascades) |
| GET | `/api/outcomes` | List outcomes (filter: `?task_id=`) |
| POST | `/api/outcomes` | Create outcome (needs `task_id`, `code`) |
| GET | `/api/outcomes/:id` | Get outcome |
| DELETE | `/api/outcomes/:id` | Delete outcome |

### Graph Definition

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/workflows` | List workflows |
| POST | `/api/workflows` | Create workflow |
| GET | `/api/workflows/:id` | Get workflow with versions |
| PUT | `/api/workflows/:id` | Update workflow |
| DELETE | `/api/workflows/:id` | Delete workflow (cascades) |
| GET | `/api/versions` | List versions (filter: `?workflow_id=`) |
| POST | `/api/versions` | Create version (auto-increments number) |
| GET | `/api/versions/:id` | Get version with nodes and edges |
| POST | `/api/versions/:id/activate` | Activate version (deactivates others) |
| DELETE | `/api/versions/:id` | Delete version (cascades) |
| GET | `/api/nodes` | List nodes (filter: `?version_id=`) |
| POST | `/api/nodes` | Create node |
| GET | `/api/nodes/:id` | Get node |
| PUT | `/api/nodes/:id` | Update node |
| DELETE | `/api/nodes/:id` | Delete node (cascades) |
| GET | `/api/edges` | List edges (filter: `?version_id=`) |
| POST | `/api/edges` | Create edge |
| DELETE | `/api/edges/:id` | Delete edge |

### Runtime Execution

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/instances` | List instances (filter: `?status=`, `?workflow_id=`) |
| POST | `/api/instances` | **Start** instance (needs `workflow_version_id`) |
| GET | `/api/instances/:id` | Get instance with tickets |
| POST | `/api/instances/:id/pause` | Pause active instance |
| POST | `/api/instances/:id/resume` | Resume paused instance |
| POST | `/api/instances/:id/stop` | Stop (cancel) instance |
| POST | `/api/instances/:id/advance` | **Advance** (needs `ticket_id`, `outcome_id`) |
| GET | `/api/tickets` | List tickets (filter: `?instance_id=`, `?status=`, `?title_id=`) |
| GET | `/api/tickets/:id` | Get ticket |
| PUT | `/api/tickets/:id/status` | Update ticket status |
| POST | `/api/tickets/:id/cancel` | Cancel ticket |
| GET | `/api/receipts` | List receipts (filter: `?ticket_id=`) |
| GET | `/api/receipts/:id` | Get receipt |

### Phase B execution-request / receipt seam

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/execution-requests` | List immutable advisory execution requests |
| POST | `/api/execution-requests` | Create or idempotently replay a revision-pinned request |
| GET | `/api/execution-requests/:id` | Request with attempts and receipts |
| GET | `/api/execution-requests/:id/attempts` | List attempts |
| POST | `/api/execution-requests/:id/attempts` | Record one truthful provider outcome; does not invoke a provider |
| GET | `/api/execution-requests/:id/receipts` | List advisory receipts |
| POST | `/api/execution-requests/:id/receipts` | Issue or replay one immutable receipt for an attempt |
| POST | `/api/execution-requests/:id/dispatch` | Reserve, invoke one persisted schema-verified adapter, and append a truthful advisory outcome |

Outcome classes are `SUCCEEDED`, `FAILED`, `UNAVAILABLE`, `STALE`, and `INVALID`.
Identical idempotency material replays; conflicting material returns `409`.
Requests, attempts, and receipts are append-only and constrained to
`authority_level = advisory`. Provider dispatch is restricted to active,
schema-verified rows in `wind.provider_contracts`; credential and endpoint
columns contain environment-variable names only. Dispatch records an
`IN_FLIGHT` reservation before acting and appends a terminal observed attempt
and receipt afterward. It does not activate workflows, mutate instances/tickets,
or admit lifecycle transitions; Resolution/PEB remains the only admission
authority. See [`PHASE-B.md`](./PHASE-B.md).

### Validation

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/validate/:version_id` | Graph integrity check |
| POST | `/api/validate/:version_id/structure` | Structural validation |

### Views

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v-roles` | List all roles |
| GET | `/api/v-roles/:name` | Get role by name |

---

## Instance Status Values

| Status | Meaning |
|--------|---------|
| `ACTIVE` | Running — tickets are being processed |
| `PAUSED` | Suspended — no tickets advance until resumed |
| `COMPLETED` | All terminal nodes have receipts |
| `FAILED` | Stopped or errored |

## Ticket Status Values

| Status | Meaning |
|--------|---------|
| `PENDING` | Waiting to be picked up |
| `IN_PROGRESS` | Being worked on |
| `COMPLETED` | Done — has a receipt |
| `CANCELLED` | Will not be processed |

---

## Notes for UI

- **Titles reference `nebula.roles`** via UUID. The `/api/v-roles` endpoint lists available roles for dropdowns.
- **Graph visualization:** GET `/api/versions/:id` returns nodes and edges in a format suitable for D3 or similar. Nodes have `name`, `is_entrypoint`, `is_terminal`. Edges have `from_node_id`, `to_node_id`, `outcome_code`.
- **The advance endpoint is the only way to progress a workflow.** It returns `new_tickets` so the UI can immediately show what happened next.
- **Validation should be called before starting an instance** to catch graph issues early.
- **CORS is enabled** — no proxy needed for local development.


---

## REST API & OpenAPI

- Endpoint inventory: [`API.md`](./API.md) (generated from source route registrations)
- OpenAPI 3.0 spec: [`openapi.yaml`](./openapi.yaml) (generated from source route registrations)

Regenerate after route changes:

```bash
cd nexus
python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json
```
