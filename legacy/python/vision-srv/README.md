# vision-srv — LOSM REST API Server

> **Port 8003** · FastAPI + PostgreSQL · Schema: `vision`

The Vision Service is the REST API backend for the **LOSM** (Layered Operational State Machine) — a work-request pipeline that compiles Work Requests into DAGs, tracks artifacts through a multi-pass compilation pipeline, and surfaces the compiled graph for UI consumption.

---

## Architecture

```
┌──────────────────────┐
│   UI / Console       │
│   (port 4200)        │
└───────┬──────────────┘
        │ HTTP REST
┌───────▼──────────────┐     ┌─────────────────┐
│   vision-srv         │────▶│   PostgreSQL     │
│   FastAPI :8003      │     │   schema: vision  │
└───────┬──────────────┘     └─────────────────┘
        │ imports
┌───────▼──────────────┐
│  losm-store          │  SQLAlchemy ORM models
│  losm-ir             │  Pydantic DAG models + compilation
└──────────────────────┘
```

### Key Libraries

| Module | Purpose |
|--------|---------|
| `losm_store` | SQLAlchemy ORM — `PlanningTask`, `Artifact`, `WorkRequestEdge`, `Branch`, lifecycle/governance events |
| `losm_ir` | Pydantic models for the DAG (`WorkRequestDAG`, `WorkRequestNode`, `DAGEdge`) and the 6-pass compilation pipeline |
| `FastAPI` | REST API framework; auto-generates OpenAPI docs at `/docs` |

---

## Quickstart

```bash
# Start the service
systemctl --user start vision-srv-py.service

# Health check
curl http://localhost:8003/health
# → {"status":"ok"}

# OpenAPI docs
open http://localhost:8003/docs
```

---

## REST API Reference

### Health

**`GET /health`** — Liveness check.

Response:
```json
{"status": "ok"}
```

---

### Work Requests

The core resource. A Work Request represents a unit of work moving through the LOSM lifecycle.

#### `GET /api/work-requests`

List work requests. Paginated.

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `limit` | int | 100 | Max results |
| `skip` | int | 0 | Offset for pagination |

Response: `Array<PlanningTask>`

```json
[
  {
    "wr_id": "a1b2c3d4-...",
    "parent_request_id": null,
    "intent": "Add dark mode toggle to all UIs",
    "constraints": {"must_support": ["nexus-console", "nebula-ui"]},
    "priority": 7,
    "context_data": {"source": "user-request", "channel": "assembly"},
    "status": "PLAN_GENERATION",
    "created_at": "2026-07-22T14:00:00Z",
    "recorded_on_dt": "2026-07-22T14:00:00Z",
    "recorded_until_dt": null
  }
]
```

#### `GET /api/work-requests/{wr_id}`

Get a single work request by its UUID.

Response: `PlanningTask` (single object, or 404)

#### `POST /api/work-requests` → 201

Create a new work request.

Request body:
```json
{
  "intent": "Add dark mode toggle to all UIs",
  "constraints": {"must_support": ["nexus-console"]},
  "priority": 7,
  "context_data": {"source": "user-request"}
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `intent` | string | **yes** | — | Goal / description of the work |
| `constraints` | object | no | null | Optional constraints (any JSON) |
| `priority` | int | no | 5 | Numeric priority (higher = more urgent) |
| `context_data` | object | no | null | Arbitrary context metadata |

Response: `PlanningTask` (created)

#### `PATCH /api/work-requests/{wr_id}`

Partially update a work request. All fields are optional; only provided fields are updated.

Request body (all fields optional):
```json
{
  "intent": "Updated intent string",
  "constraints": {"new_key": "value"},
  "priority": 3,
  "context_data": {"updated": true}
}
```

Response: `PlanningTask` (updated) or 404

#### `DELETE /api/work-requests/{wr_id}`

Hard-delete a work request. Irreversible.

Response: `{"detail": "Work request {wr_id} deleted"}` or 404

---

### Branches

Branches represent alternative execution paths for a work request.

#### `GET /api/branches`

List branches. Optionally filter by work request.

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `wr_id` | string | — | Filter to a specific work request |
| `limit` | int | 100 | Max results |
| `skip` | int | 0 | Offset |

Response: `Array<Branch>`

```json
[
  {
    "branch_id": "br-001-...",
    "wr_id": "a1b2c3d4-...",
    "parent_branch_id": null,
    "fork_point": null,
    "label": "alternative-approach",
    "score": 0.85,
    "status": "active",
    "created_at": "2026-07-22T14:30:00Z"
  }
]
```

#### `POST /api/branches` → 201

Create a new branch on a work request.

Request body:
```json
{
  "branch_id": "br-001-...",
  "wr_id": "a1b2c3d4-...",
  "label": "alternative-approach",
  "parent_branch_id": null,
  "fork_point": null
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `branch_id` | string | no | Branch UUID (auto-generated if omitted) |
| `wr_id` | string | **yes** | Parent work request UUID |
| `label` | string | no | Human-readable branch label |
| `parent_branch_id` | string | no | Parent branch (for nested branches) |
| `fork_point` | string | no | Artifact ID or event ID where the fork occurred |

Response: `Branch` (created)

---

### Artifacts

Artifacts are structured outputs produced during the LOSM lifecycle — plans, critiques, specs, execution records, patches, and summaries.

#### `GET /api/artifacts`

List artifacts. Optionally filter by work request.

| Query Param | Type | Default | Description |
|-------------|------|---------|-------------|
| `wr_id` | string | — | Filter to a specific work request |
| `limit` | int | 100 | Max results |
| `skip` | int | 0 | Offset |

Response: `Array<Artifact>`

```json
[
  {
    "artifact_id": "art-001-...",
    "type": "PLAN",
    "content": {"steps": [...], "estimates": {...}},
    "confidence": 0.92,
    "provenance": {"model": "nvidia/nemotron-3-ultra-550b-a55b", "role": "planner"},
    "wr_id": "a1b2c3d4-...",
    "parent_artifact_id": null,
    "template_metadata": {"template": "implementation-plan-v2"},
    "created_at": "2026-07-22T14:05:00Z"
  }
]
```

#### `POST /api/artifacts` → 201

Create a new artifact.

Request body:
```json
{
  "type": "PLAN",
  "content": {"steps": ["step1", "step2"]},
  "confidence": 0.92,
  "provenance": {"model": "nvidia/nemotron-3-ultra-550b-a55b"},
  "wr_id": "a1b2c3d4-...",
  "parent_artifact_id": null,
  "template_metadata": {"template": "implementation-plan-v2"}
}
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `type` | string | **yes** | — | One of: `PLAN`, `CRITIQUE`, `SPEC`, `EXECUTION`, `PATCH`, `SUMMARY` |
| `content` | object | **yes** | — | The artifact payload (any JSON) |
| `confidence` | float | no | null | Confidence score (0.0–1.0) |
| `provenance` | object | no | null | Origin metadata (model, role, source) |
| `wr_id` | string | no | null | Associated work request UUID |
| `parent_artifact_id` | string | no | null | Parent artifact (for artifact chains) |
| `template_metadata` | object | no | null | Template/schema used to generate |

Response: `Artifact` (created)

---

### DAG Compilation & Analysis

The DAG API compiles work requests and their edges into a traversable graph, runs the 6-pass pipeline, and exposes path-finding and validation.

#### `GET /api/work-requests/{wr_id}/dag`

Compile the full WorkRequestDAG rooted at a given work request. Gathers the root WR + all descendants via edges, then runs the 6-pass compilation pipeline.

Response:
```json
{
  "dag_id": "dag-xyz-...",
  "root_wr_id": "root-wr-uuid",
  "nodes": {
    "wr-uuid-1": {
      "wr_id": "wr-uuid-1",
      "parent_request_id": null,
      "intent": "Root work request",
      "status": "PLAN_GENERATION",
      "priority": 7,
      "depth": 0,
      "children": ["wr-uuid-2"],
      "edge_type": null,
      "metadata": {},
      "compiled_properties": {}
    }
  },
  "edges": [
    {
      "edge_id": "edge-uuid",
      "parent_wr_id": "wr-uuid-1",
      "child_wr_id": "wr-uuid-2",
      "edge_type": "depends_on",
      "metadata": {},
      "created_at": "2026-07-22T14:00:00Z"
    }
  ],
  "tenant_id": "vision-srv",
  "trace_id": null,
  "kernel_id": null,
  "depth": 2,
  "total_nodes": 5,
  "compilation_status": "compiled",
  "compilation_errors": [],
  "compiled_at": "2026-07-22T14:10:00Z",
  "metadata": {},
  "_metadata": {
    "node_count": 5,
    "edge_count": 4,
    "compilation_time_ms": 12.5
  }
}
```

#### `GET /api/work-requests/{wr_id}/dag/path/{target_wr_id}`

Find the shortest path between two work requests in the compiled DAG.

Response:
```json
{
  "source_wr_id": "wr-uuid-1",
  "target_wr_id": "wr-uuid-3",
  "path": ["wr-uuid-1", "wr-uuid-2", "wr-uuid-3"],
  "length": 3,
  "exists": true
}
```

Returns 404 if either WR is not found, 500 if DAG compilation fails.

#### `GET /api/work-requests/{wr_id}/dag/validate`

Run structural validation on a WR's DAG without full compilation. Checks for cycles, orphans, depth violations, duplicate edges, and missing parents.

Response:
```json
{
  "wr_id": "wr-uuid-1",
  "valid": true,
  "issues": [],
  "warnings": [],
  "node_count": 5,
  "edge_count": 4
}
```

When invalid:
```json
{
  "wr_id": "wr-uuid-1",
  "valid": false,
  "issues": [
    {
      "wr_id": "wr-uuid-2",
      "issue_type": "cycle",
      "message": "Cycle detected: wr-uuid-2 → wr-uuid-3 → wr-uuid-2",
      "detail": {"cycle_nodes": ["wr-uuid-2", "wr-uuid-3"]}
    }
  ],
  "warnings": [],
  "node_count": 5,
  "edge_count": 5
}
```

---

## Data Model Reference

### `PlanningTask` (table: `vision.work_requests_losm`)

The core work-request entity.

| Column | Type | Description |
|--------|------|-------------|
| `wr_id` | UUID string (36) | Primary identifier |
| `parent_request_id` | UUID string | Optional parent WR (lineage) |
| `intent` | text | Human-readable goal |
| `constraints` | JSON | Optional constraints |
| `priority` | int | 1–10, higher = more urgent |
| `context_data` | JSON | Arbitrary context data (DB column: `context`) |
| `status` | WorkStatus enum | Current lifecycle state (see below) |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last-updated timestamp (mapped to `recorded_on_dt`) |
| `recorded_on_dt` | timestamptz | Bitemporal: when recorded |
| `recorded_until_dt` | timestamptz | Bitemporal: soft-delete marker |

### `Artifact` (table: `vision.artifacts`)

Structured outputs from the LOSM lifecycle.

| Column | Type | Description |
|--------|------|-------------|
| `artifact_id` | UUID string (36) | Primary identifier |
| `type` | ArtifactType enum | `PLAN` / `CRITIQUE` / `SPEC` / `EXECUTION` / `PATCH` / `SUMMARY` |
| `content` | JSON | The artifact payload |
| `confidence` | float | Confidence score (0.0–1.0) |
| `provenance` | JSON | Origin metadata (model, role, etc.) |
| `wr_id` | UUID string | Associated work request |
| `parent_artifact_id` | UUID string | Parent artifact for chains |
| `template_metadata` | JSON | Template schema info |

### `WorkRequestEdge` (table: `vision.work_request_edges`)

Explicit directed edges between work requests. **No direct REST API** — edges are created by the pipeline and traversed implicitly via the `/dag`, `/dag/path`, and `/dag/validate` endpoints.

| Column | Type | Description |
|--------|------|-------------|
| `edge_id` | UUID string (36) | Primary identifier |
| `parent_wr_id` | UUID string | Source node |
| `child_wr_id` | UUID string | Target node |
| `edge_type` | string (32) | Semantic type (see EdgeType below) |
| `metadata` | JSON | Edge-level annotations |

### `Branch` (table: `vision.branches`)

Alternative execution paths for work requests.

| Column | Type | Description |
|--------|------|-------------|
| `branch_id` | UUID string (36) | Primary identifier |
| `wr_id` | UUID string | Parent work request |
| `parent_branch_id` | UUID string | Parent branch (nested) |
| `fork_point` | UUID string | Artifact/event ID where fork occurred |
| `label` | string (64) | Human-readable label |
| `score` | float | Quality/compatibility score |
| `status` | string (32) | `active`, `merged`, `abandoned` |

### `LifecycleEvent` (table: `vision.lifecycle_events`)

Audit trail of work-request state transitions.

| Column | Type | Description |
|--------|------|-------------|
| `event_id` | UUID string (36) | Primary identifier |
| `wr_id` | UUID string | Which work request |
| `from_state` | WorkStatus | Previous state (null for creation) |
| `to_state` | WorkStatus | New state |
| `actor` | string (128) | Who/what triggered the transition |
| `reason` | string (256) | Why the transition occurred |
| `metadata` | JSON | Additional context |

### `GovernanceEvent` (table: `vision.governance_events`)

Policy/compliance events for work requests.

| Column | Type | Description |
|--------|------|-------------|
| `event_id` | UUID string (36) | Primary identifier |
| `event_type` | string (64) | Event classification |
| `work_request_id` | UUID string | Associated WR |
| `lineage_parent` | string (128) | Parent in event chain |
| `payload` | JSON | Event data |

### `ReceiptIngestRecord` (table: `vision.receipt_ingest_records`)

Records of receipt ingestion from the pipeline.

| Column | Type | Description |
|--------|------|-------------|
| `receipt_id` | UUID string (36) | Primary identifier |
| `work_request_id` | string (64) | Associated WR |
| `executor_id` | string (128) | Executor that produced the receipt |
| `receipt_hash` | string (64) | Content hash (unique) |
| `result` | string (16) | Outcome (e.g., `SUCCESS`, `FAILURE`) |
| `lineage_parent` | string (128) | Parent receipt in chain |
| `payload` | JSON | Full receipt data |

---

## Event Model

### `WorkStatus` — Operational Lifecycle

The canonical state machine for work requests. Each state gates a transition.

```
NEW ──▶ INTAKE ──▶ PLAN_GENERATION ──▶ PLAN_REVIEW ──▶ PLAN_APPROVAL_GATE
                                                              │
                                                              ▼
FAILED ◀── BLOCKED ◀── COMPLETION ◀── VALIDATION ◀── EXECUTION ◀── SPEC_GENERATION
```

| Status | Phase | Description |
|--------|-------|-------------|
| `NEW` | New | Just created, not yet triaged |
| `INTAKE` | New | Being triaged / classified |
| `PLAN_GENERATION` | Plan Done | A plan is being generated |
| `PLAN_REVIEW` | Plan Done | Plan is under review |
| `PLAN_APPROVAL_GATE` | Plan Done | Plan awaiting approval |
| `SPEC_GENERATION` | Spec Ready | Specification is being written |
| `EXECUTION` | Executed | Work is being executed |
| `VALIDATION` | Validated | Results being validated |
| `COMPLETION` | Complete | Work completed successfully |
| `BLOCKED` | Blocked | Work is blocked |
| `FAILED` | Failed | Work has failed |

### `WorkflowState` — Lifecycle Phase (Projection)

A compressed, IR-level projection of `WorkStatus` for high-level reporting:

| WorkflowState | Maps from WorkStatus values |
|---------------|---------------------------|
| `NEW` | `NEW`, `INTAKE` |
| `PLAN_DONE` | `PLAN_GENERATION`, `PLAN_REVIEW`, `PLAN_APPROVAL_GATE` |
| `SPEC_READY` | `SPEC_GENERATION` |
| `EXECUTED` | `EXECUTION` |
| `VALIDATED` | `VALIDATION` |
| `COMPLETE` | `COMPLETION` |
| `BLOCKED` | `BLOCKED` |
| `FAILED` | `FAILED` |

### `EventEnvelope` — Operation Context

Every DAG operation is wrapped in an `EventEnvelope` providing distributed tracing context:

```json
{
  "event_id": "evt-001-...",
  "wrp_id": "1.1",
  "type": "dag.compiled",
  "timestamp": "2026-07-22T14:10:00Z",
  "version": 1,
  "causation_id": "evt-000-...",
  "correlation_id": "corr-001-...",
  "tenant_id": "vision-srv",
  "trace_id": "trace-abc-...",
  "kernel_id": "kernel-01"
}
```

| Field | Description |
|-------|-------------|
| `event_id` | Unique ID for this envelope |
| `wrp_id` | Protocol version (`"1.1"`) |
| `type` | Event type (mirrors the operation) |
| `timestamp` | When emitted |
| `causation_id` | ID of the event that *caused* this one |
| `correlation_id` | Groups related events into a conversation |
| `tenant_id` | Multi-tenant namespace |
| `trace_id` | Distributed tracing ID |
| `kernel_id` | Logical execution-unit ID |

### `EdgeType` — DAG Edge Semantics

Edges between work requests carry semantic meaning:

| EdgeType | Meaning |
|----------|---------|
| `depends_on` | Child depends on parent completing first |
| `parent_of` | Parent contains child |
| `child_of` | Child belongs to parent |
| `derived_from` | Child was derived from parent |
| `supersedes` | Child replaces/obsoletes parent |
| `branches_from` | Child branches off parent |
| `references` | Child references parent (non-blocking) |
| `triggered_by` | Child was triggered by parent |

---

## Compilation Pipeline (6 Passes)

The DAG is compiled through 6 deterministic passes:

| Pass | Name | Description |
|------|------|-------------|
| 1 | `normalize` | Sanitize and validate raw node/edge data |
| 2 | `tenant_bind` | Bind nodes to tenant/namespace scope |
| 3 | `dag_construct` | Build the DAG graph from nodes + edges |
| 4 | `structural_validate` | Check for cycles, orphans, depth violations, duplicate edges |
| 5 | `execution_compatibility` | Verify execution constraints are satisfiable |
| 6 | `policy_annotate` | Annotate nodes/edges with governance policies |

The compiled result (`CompilationResult`) includes success/failure status, the DAG (if successful), errors, warnings, and timing.

---

## UI Integration Patterns

### 1. Work Request List View

```bash
curl http://localhost:8003/api/work-requests?limit=50
```

Render as a filterable/sortable table. Key columns: `wr_id`, `intent`, `status`, `priority`, `created_at`.

### 2. Work Request Detail + DAG Visualization

```bash
# Fetch the WR
curl http://localhost:8003/api/work-requests/{wr_id}

# Fetch the full DAG for graph visualization
curl http://localhost:8003/api/work-requests/{wr_id}/dag
```

Use the DAG response to render a node-edge graph. Color nodes by `status`. Label edges by `edge_type`.

### 3. Status Timeline from Lifecycle Events

Lifecycle events are tracked in the `vision.lifecycle_events` table. Query them for a timeline view of a WR's state transitions. (No REST endpoint currently — access via direct DB query or add an endpoint.)

### 4. Validation Dashboard

```bash
curl http://localhost:8003/api/work-requests/{wr_id}/dag/validate
```

Show validation status (valid/invalid) with a list of issues if any exist. Each issue includes the affected `wr_id`, `issue_type`, and a human-readable `message`.

### 5. Path Finding

```bash
curl http://localhost:8003/api/work-requests/{source}/dag/path/{target}
```

Use for dependency chain visualization — show the user how two WRs are connected through the graph.

---

## Database Schema

All tables live in the `vision` PostgreSQL schema:

| Table | Purpose | REST API |
|-------|---------|----------|
| `work_requests_losm` | Core work requests (PlanningTask) | ✅ Full CRUD + DAG endpoints |
| `artifacts` | Structured artifacts (plans, specs, etc.) | ✅ GET list + POST create |
| `work_request_edges` | DAG edges between WRs | ⚠️ **Deprecated** — 0-row parallel store; WR lineage is column-based (`work_requests.plan_id`). See T22 Step 5.4 ruling. No direct REST API. |
| `branches` | Alternative execution paths | ✅ GET list + POST create |
| `branch_artifacts` | Artifacts scoped to branches | ❌ DB-only (no REST endpoint) |
| `lifecycle_events` | State transition audit trail | ❌ DB-only (no REST endpoint) |
| `governance_events` | Policy/compliance events | ❌ DB-only (no REST endpoint) |
| `receipt_ingest_records` | Receipt ingestion records | ❌ DB-only (no REST endpoint) |

All tables use **semi-bitemporal** tracking: `recorded_on_dt` (when the row became visible) and `recorded_until_dt` (null = current; set on soft-delete).

---

## Configuration

| Env Variable | Default | Description |
|-------------|---------|-------------|
| `PYTHONPATH` | `src` | Module search path |
| Database URL | (from losm-store config) | PostgreSQL connection |

Service runs via systemd: `vision-srv-py.service` on port 8003.


---

## REST API & OpenAPI

- Endpoint inventory: [`API.md`](./API.md) (generated from source route registrations)
- OpenAPI spec: [`openapi.yaml`](./openapi.yaml) (FastAPI-native spec captured from the live service's `/openapi.json`)
- The running service also serves its spec natively at `http://localhost:8003/openapi.json`
  and its interactive docs at `http://localhost:8003/docs`

Regenerate after route changes:

```bash
cd nexus
python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json
```
