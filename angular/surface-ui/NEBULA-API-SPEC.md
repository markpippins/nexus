# nebula-srv REST API Specification

> **Server:** Express.js on port `3101`  
> **Base URL:** `http://localhost:3101/api`  
> **Database:** PostgreSQL (`nebula` schema, search_path=nebula)  
> **Cache:** Redis (block segmentation, inbox pointers, session state)  
> **Auth:** None (internal service)

---

## Table of Contents

1. [Pagination Convention](#1-pagination-convention)
2. [Health](#2-health)
3. [Systems](#3-systems)
4. [Subsystems](#4-subsystems)
5. [Features](#5-features)
6. [Requirements](#6-requirements)
7. [Requirement Dependencies](#7-requirement-dependencies)
8. [Requirement Kanban Moves](#8-requirement-kanban-moves)
9. [Requirement Compilation](#9-requirement-compilation-workrequest-ir)
10. [System Folders](#10-system-folders)
11. [Work Sessions](#11-work-sessions)
12. [Complex Operations](#12-complex-operations-transactional)
13. [Workspaces](#13-workspaces)
14. [Docs Files (Disk Reads)](#14-docs-files-disk-reads)
15. [Plans Display](#15-plans-display)
16. [Implementation Plans By Hierarchy](#16-implementation-plans-by-hierarchy)
17. [Audit Files](#17-audit-files)
18. [User Preferences](#18-user-preferences)
19. [Harvests](#19-harvests)
20. [Harvest Candidates](#20-harvest-candidates)
21. [Specifications](#22-specifications)
22. [Agendas](#23-agendas)
23. [Assessments](#24-assessments)
24. [Observations](#25-observations)
25. [Roles](#26-roles)
26. [Open Questions](#27-open-questions)
27. [Search](#28-search)
28. [Counts](#29-counts)
29. [Block Segmentation (Internal Services)](#30-block-segmentation-internal-services)
30. [Cross-References](#31-cross-references)
31. [Evidence Links](#32-evidence-links)
32. [Agent Records](#33-agent-records)
33. [OP Registry](#34-op-registry)
34. [Knowledge Entities](#35-knowledge-entities)
35. [Knowledge Edges](#36-knowledge-edges)
36. [Projections](#37-projections)
37. [Conduit Deleted Plans](#38-conduit-deleted-plans)
38. [Execution Requests](#39-execution-requests)
39. [Execution Receipts](#40-execution-receipts)
40. [Architect Specs](#41-architect-specs)
41. [Artifact Provenance](#42-artifact-provenance)
42. [System Info Tabs](#43-system-info-tabs)
43. [Inbox Pointers](#44-inbox-pointers)
44. [CPF — Compilation Readiness Framework](#45-cpf--compilation-readiness-framework)
45. [Import / Seed](#46-import--seed)
46. [Refresh Materialized Views](#47-refresh-materialized-views)
47. [Additional Harvest Candidate Operations](#48-additional-harvest-candidate-operations)
48. [Additional Agenda Operations](#49-additional-agenda-operations)
49. [Additional Specification Operations](#50-additional-specification-operations)
50. [Agent Record Search](#51-agent-record-search)
51. [Additional OP Registry Operations](#52-additional-op-registry-operations)
52. [Additional Execution Endpoints](#53-additional-execution-endpoints)

---

## 1. Pagination Convention

All list endpoints follow a consistent pagination pattern via query parameters.

**Query Parameters:**

- `page` — page number (1-indexed, default 1)
- `pageSize` — items per page (default 100, max 100)

**Response Envelope:**

```json
{
  "items": [...],
  "total": 42,
  "page": 1,
  "pageSize": 100
}
```

`items` contains the page of results. `total` is the total count across all pages.

---

## 2. Health

### `GET /health`

### `GET /api/health`

**Purpose:** Liveness check that also verifies database connectivity.

**Response `200`:**

```json
{ "status": "ok", "db": true }
```

**Response `503`:**

```json
{ "status": "error", "message": "ECONNREFUSED ..." }
```

---

## 3. Systems

### `GET /api/systems`

**Purpose:** List all systems with their full nested hierarchy (folders, subsystems → features). Paginated.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope with nested hierarchy items.

### `GET /api/systems/:id`

**Purpose:** Fetch a single system with full nested hierarchy (folders, subsystems → features).

**Response `200`:** Same shape as an item in the list response.

```json
{
  "id": "uuid",
  "name": "string",
  "description": "string",
  "readme": "string|null",
  "architecture": "string|null",
  "createdAt": 1234567890000,
  "folders": [...],
  "subsystems": [...]
}
```

**Paginated List Response:**

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "string",
      "description": "string",
      "readme": "string|null",
      "architecture": "string|null",
      "createdAt": 1234567890000,
      "folders": [{ "id": "uuid", "name": "string", "category": "string", "note": "string" }],
      "subsystems": [
        {
          "id": "uuid",
          "systemId": "uuid",
          "name": "string",
          "description": "string",
          "readme": "string|null",
          "color": "#HEX",
          "createdAt": 1234567890000,
          "features": [
            {
              "id": "uuid",
              "subsystemId": "uuid",
              "name": "string",
              "description": "string",
              "readme": "string|null",
              "createdAt": 1234567890000
            }
          ]
        }
      ]
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `POST /api/systems`

        ]
      }
    ]

}
]

````

### `POST /api/systems`

**Purpose:** Create a new system.

**Request Body:**
```json
{
  "name": "string (required)",
  "description": "string (default '')",
  "readme": "string|null (default null)",
  "architecture": "string|null (default null)"
}
````

**Response `201`:** Full system object with empty `folders:[]` and `subsystems:[]`.

### `PATCH /api/systems/:id`

**Purpose:** Update system name, description, readme, and/or architecture. Only supplied fields are updated.

**Request Body:** (all optional)

```json
{
  "name": "string",
  "description": "string",
  "readme": "string|null",
  "architecture": "string|null"
}
```

**Response `200`:** Updated system object.

### `DELETE /api/systems/:id`

**Purpose:** Delete a system and all associated work_sessions (cascade). Subsystems, features, folders, requirements are cascade-deleted by DB foreign keys.

**Response `200`:**

```json
{ "ok": true }
```

---

## 4. Subsystems

### `GET /api/subsystems/:id`

**Purpose:** Fetch a single subsystem with its features.

**Response `200`:**

```json
{
  "id": "uuid",
  "systemId": "uuid",
  "name": "string",
  "description": "string",
  "readme": "string|null",
  "color": "#HEX",
  "created_at": 1234567890000,
  "features": [...]
}
```

### `POST /api/subsystems`

**Purpose:** Create a subsystem under a system. Auto-assigns an unused color from a 12-color palette.

**Request Body:**

```json
{
  "systemId": "uuid (required)",
  "name": "string (required)",
  "description": "string (default '')",
  "readme": "string|null (default null)"
}
```

**Response `201`:** Full subsystem object with `features:[]` and auto-assigned `color`.

### `PATCH /api/subsystems/:id`

**Purpose:** Update subsystem name, description, readme, or color.

**Request Body:** (all optional)

```json
{ "name": "string", "description": "string", "readme": "string|null", "color": "#HEX" }
```

**Response `200`:** Updated subsystem object.

### `DELETE /api/subsystems/:id`

**Purpose:** Delete a subsystem and its associated requirements (cascade). Features are cascade-deleted by DB.

**Response `200`:**

```json
{ "ok": true }
```

---

## 5. Features

### `GET /api/features/:id`

**Purpose:** Fetch a single feature.

**Response `200`:**

```json
{
  "id": "uuid",
  "subsystemId": "uuid",
  "name": "string",
  "description": "string",
  "readme": "string|null",
  "created_at": 1234567890000
}
```

### `POST /api/features`

**Purpose:** Create a feature under a subsystem.

**Request Body:**

```json
{
  "subsystemId": "uuid (required)",
  "name": "string (required)",
  "description": "string (default '')",
  "readme": "string|null (default null)"
}
```

**Response `201`:** Full feature object.

### `PATCH /api/features/:id`

**Purpose:** Update feature name, description, or readme.

**Request Body:** (all optional)

```json
{ "name": "string", "description": "string", "readme": "string|null" }
```

**Response `200`:** Updated feature object.

### `DELETE /api/features/:id`

**Purpose:** Delete a feature and its associated requirements (cascade).

**Response `200`:**

```json
{ "ok": true }
```

---

## 6. Requirements

### `GET /api/requirements`

**Purpose:** List requirements with optional filters. Returns newest first.

**Query Parameters:** (all optional)

- `systemId` — filter by system
- `subsystemId` — filter by subsystem
- `featureId` — filter by feature

**Response `200`:** Array of requirement objects.

### `GET /api/requirements/:id/children`

**Purpose:** Fetch direct child requirements (those where `parent_id = :id`).

**Response `200`:** Array of child requirement objects ordered by `created_at ASC`.

### `POST /api/requirements`

**Purpose:** Create a requirement. Status is normalized through a lookup map (accepts `"todo"`, `"in progress"`, `"wip"`, etc.). The eight canonical statuses: `Backlog`, `ToDo`, `InProgress`, `Active`, `Blocked`, `Done`, `Cancelled`, `Accepted`.

**Request Body:**

```json
{
  "systemId": "uuid (required)",
  "subsystemId": "uuid|null (default null)",
  "featureId": "uuid|null (default null)",
  "title": "string (required)",
  "description": "string (default '')",
  "status": "string (default 'Backlog')",
  "priority": "string (default 'Medium')",
  "startDate": "ISO date|null",
  "completionDate": "ISO date|null",
  "parentId": "uuid|null",
  "reqType": "Epic|Story|Task|Bug|null",
  "acceptanceCriteria": "array|object|null",
  "candidateId": "uuid|null"
}
```

**Response `201`:** Full requirement object with normalized fields.

### `PATCH /api/requirements/batch`

**Purpose:** Batch status update for multiple requirements. **Must be registered before `/:id` route in Express.**

**Request Body:**

```json
{
  "ids": ["uuid", "uuid", ...],
  "status": "string (one of canonical 8)"
}
```

**Response `200`:**

```json
{ "ok": true, "updated": 3 }
```

### `PATCH /api/requirements/:id`

**Purpose:** Partial update of a requirement. On transition to `ToDo`, fires an async self-compilation webhook (`POST /api/requirements/:id/compile` with `createPlan: true`).

**Request Body:** (all optional)

```json
{
  "title": "string",
  "description": "string",
  "status": "string",
  "priority": "string",
  "startDate": "ISO date|null",
  "completionDate": "ISO date|null",
  "systemId": "uuid",
  "subsystemId": "uuid|null",
  "featureId": "uuid|null",
  "parentId": "uuid|null",
  "reqType": "Epic|Story|Task|Bug|null",
  "acceptanceCriteria": "array|object|null",
  "candidateId": "uuid|null",
  "conduitPlanId": "string|null"
}
```

**Side Effects:** If `status` transitions to `"ToDo"`, fire-and-forget `POST /api/requirements/:id/compile` with `createPlan: true`.

**Response `200`:** Updated requirement object.

### `DELETE /api/requirements/:id`

**Purpose:** Delete a requirement.

**Response `200`:**

```json
{ "ok": true }
```

---

## 7. Requirement Dependencies

### `GET /api/requirements/:id/dependencies`

**Purpose:** List all incoming and outgoing dependency links (blocks / depends_on) for a requirement, expressed via `nebula.cross_references`.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "relType": "req:blocks|req:depends_on",
      "sourceType": "requirement",
      "sourceId": "uuid",
      "targetType": "requirement",
      "targetId": "uuid",
      "direction": "outgoing|incoming",
      "otherId": "uuid",
      "metadata": {},
      "createdAt": 1234567890000
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `POST /api/requirements/:id/dependencies`

**Purpose:** Create a dependency link between two requirements. Idempotent (WHERE NOT EXISTS).

**Request Body:**

```json
{
  "targetId": "uuid (required)",
  "relType": "req:blocks|req:depends_on (default 'req:blocks')"
}
```

**Response `201`:**

```json
{ "id": "uuid", "source_type": "requirement", ... }
```

Or if already exists:

```json
{ "ok": true, "message": "Dependency already exists" }
```

### `DELETE /api/requirements/:id/dependencies/:depId`

**Purpose:** Remove a dependency link. Verifies the cross-ref belongs to the specified requirement and is a requirement-to-requirement block/depends_on relationship.

**Response `200`:**

```json
{ "ok": true }
```

---

## 8. Requirement Kanban Moves

### `POST /api/requirements/:id/move`

**Purpose:** Kanban-style single-requirement status move with optimistic concurrency control. Locks the row in `requirements_history` to detect concurrent modifications.

**Request Body:**

```json
{
  "targetStatus": "string (required, canonical 8)",
  "expectedCurrentStatus": "string (optional, canonical 8)"
}
```

**Response `200`:** Updated requirement object.

**Response `409`:**

```json
{
  "error": "Current status does not match expectedCurrentStatus",
  "currentStatus": "ToDo",
  "expectedCurrentStatus": "InProgress"
}
```

---

## 9. Requirement Compilation (WorkRequest IR)

### `POST /api/requirements/:id/compile`

**Purpose:** Two-stage compiler that converts a requirement into an operational WorkRequest Intermediate Representation (IR). Stage 1 normalizes semantics; Stage 2 matches against `op_registry` and generates an opcode sequence. Optionally creates a conduit plan.

**Request Body:**

```json
{
  "stage1Only": false,
  "createPlan": false,
  "dryRun": false
}
```

**Stage 1** — Semantic Normalization:

- Fetches requirement with full hierarchy context (system, subsystem, feature names/descriptions)
- Normalizes acceptance criteria from JSON
- Resolves cross-references
- Synthesizes intent summary

**Stage 2** — Engineering Compilation:

- Matches intent text against `op_registry` regex patterns
- Generates opcode sequence (WRITE_FILE, WRITE_SOURCE_FILE, VALIDATE_SYNTAX, etc.)
- Resolves files affected, dependencies, idempotency keys
- Writes journal entry to `agent_records`
- Optionally calls `conduit-mcp` via `POST /tools/call` to create a plan
- Creates `compiles_to` cross-reference from requirement to plan

**Response `200`:**

```json
{
  "ok": true,
  "stage": 2,
  "stage1": { ... },
  "stage2": {
    "requirement_id": "uuid",
    "intent_id": "REQ-xxxx|matched",
    "registry_version": "default|v1",
    "op_sequence": [...],
    "files_affected": ["..."],
    "dependencies": ["..."],
    "acceptance_criteria": ["..."],
    "idempotency_key": "...",
    "matched_op_registry_id": "uuid|null"
  },
  "journal_entry_id": "uuid",
  "plan_number": "PLN-xxx|null"
}
```

---

## 10. System Folders

### `POST /api/systems/:id/folders`

**Purpose:** Create a folder within a system (organizational grouping).

**Request Body:**

```json
{
  "name": "string (required)",
  "category": "string (required)",
  "note": "string (default '')"
}
```

**Response `201`:**

```json
{ "id": "uuid", "name": "string", "category": "string", "note": "string" }
```

### `DELETE /api/systems/:systemId/folders/:folderId`

**Purpose:** Delete a folder.

**Response `200`:**

```json
{ "ok": true }
```

---

## 11. Work Sessions

### `GET /api/sessions`

**Purpose:** List all work sessions, newest first.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "parentId": "uuid",
      "parentType": "string",
      "parentName": "string",
      "context": "string",
      "platform": "string",
      "model": "string",
      "outcome": "string|null",
      "status": "string",
      "createdAt": "epoch ms"
    }
  ],
  "total": 42,
  "page": 1,
  "pageSize": 100
}
```

### `POST /api/sessions`

**Purpose:** Create a work session record associated with a parent entity (system, subsystem, feature, requirement).

**Request Body:**

```json
{
  "parentId": "uuid (required)",
  "parentType": "string (required, auto-lowercased)",
  "parentName": "string (default '')",
  "context": "string (default '')",
  "platform": "string (default '')",
  "model": "string (default '')",
  "outcome": "string|null",
  "status": "string (default 'Pending')"
}
```

**Response `201`:** Session object.

### `PATCH /api/sessions/:id`

**Purpose:** Update session outcome and/or status.

**Request Body:**

```json
{ "outcome": "string", "status": "string" }
```

**Response `200`:** Updated session object.

### `DELETE /api/sessions/:id`

**Purpose:** Delete a work session.

**Response `200`:**

```json
{ "ok": true }
```

---

## 12. Complex Operations (Transactional)

### `POST /api/features/move`

**Purpose:** Re-parent a feature to a different subsystem (transactional). Also cascades system_id/subsystem_id updates to all requirements under that feature.

**Request Body:**

```json
{
  "featureId": "uuid (required)",
  "targetSystemId": "uuid (required)",
  "targetSubsystemId": "uuid (required)"
}
```

**Response `200`:**

```json
{ "ok": true }
```

### `POST /api/subsystems/move`

**Purpose:** Re-parent a subsystem to a different system (transactional). Also updates `system_id` on all requirements under that subsystem.

**Request Body:**

```json
{
  "subsystemId": "uuid (required)",
  "targetSystemId": "uuid (required)"
}
```

**Response `200`:**

```json
{ "ok": true }
```

### `POST /api/systems/demote/:id`

**Purpose:** Demote a system into a subsystem of another system (transactional). The source system's subsystems become features under the new subsystem. All requirements and hierarchy are preserved.

**Request Body:**

```json
{
  "targetSystemId": "uuid (required)"
}
```

**Response `200`:**

```json
{ "ok": true, "newSubsystemId": "uuid" }
```

---

## 13. Workspaces

### `GET /api/workspaces`

**Purpose:** List all workspace paths with their associated system/subsystem names.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "systemId": "uuid",
      "subsystemId": "uuid|null",
      "workspacePath": "string",
      "createdAt": "epoch ms"
    }
  ],
  "total": 12,
  "page": 1,
  "pageSize": 100
}
```

### `POST /api/workspaces`

**Purpose:** Register a workspace directory path for a system/subsystem.

**Request Body:**

```json
{
  "systemId": "uuid (required)",
  "subsystemId": "uuid|null",
  "workspacePath": "string (required, relative path)"
}
```

**Response `201`:** Workspace object.

### `DELETE /api/workspaces/:id`

**Purpose:** Remove a workspace registration.

**Response `200`:**

```json
{ "ok": true }
```

---

## 14. Docs Files (Disk Reads)

All docs endpoints read from disk within `/home/codex/dev/nexus`. Known files: `README.md`, `ARCHITECTURE.md`, `README.markdown`, `SPEC.md`, `REFERENCE.md`. All have path traversal protection.

### `GET /api/docs`

**Purpose:** Read known doc files from a workspace directory.

**Query Parameters:**

- `workspacePath` (required) — relative to nexus root, e.g. `typescript/conduit-mcp`

**Response `200`:**

```json
{
  "workspacePath": "typescript/conduit-mcp",
  "files": [
    { "filename": "README.md", "content": "..." },
    { "filename": "ARCHITECTURE.md", "content": "..." }
  ],
  "found": 2
}
```

### `GET /api/subsystems/:id/docs`

**Purpose:** Read doc files from all workspace paths associated with a subsystem.

**Response `200`:**

```json
{ "subsystemId": "uuid", "docs": [...], "found": 1 }
```

### `GET /api/systems/:id/docs`

**Purpose:** Read doc files from all workspace paths for a system (including subsystem workspaces).

**Response `200`:**

```json
{ "systemId": "uuid", "docs": [...], "found": 2 }
```

---

## 15. Plans Display

### `GET /api/plans`

**Purpose:** List implementation plans from `nebula.implementation_plans`. Filterable by status.

**Query Parameters:**

- `status` — filter by status value (or `"all"` for no filter)
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "PLN-xxx",
      "title": "string",
      "goal": "string",
      "content": "string",
      "files_affected": "string[]",
      "acceptance_criteria": "string[]",
      "dependencies": "string[]",
      "status": "string",
      "metadata": {},
      "created_at": "ISO timestamp",
      "updated_at": "ISO timestamp",
      "sizeBytes": 1234,
      "modifiedAt": "ISO timestamp"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/plans/:id`

**Purpose:** Fetch a single implementation plan by `plan_number`.

**Response `200`:** Single plan object.

### `GET /api/implementation-plans/statuses`

**Purpose:** Get distinct status values for filter tabs.

**Response `200`:**

```json
{ "statuses": ["accepted", "archived", "backlog", "done", "in_progress", "pending"] }
```

---

## 16. Implementation Plans By Hierarchy

### `GET /api/systems/:id/implementation-plans`

**Purpose:** Get plans linked to a system via cross-references through harvest_candidates (spawns_plan relationship).

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "systemId": "uuid",
  "items": [
    {
      "id": "PLN-xxx",
      "title": "string",
      "goal": "string",
      "content": "string",
      "files_affected": "string[]",
      "acceptance_criteria": "string[]",
      "dependencies": "string[]",
      "status": "string",
      "metadata": {},
      "created_at": "ISO timestamp",
      "updated_at": "ISO timestamp",
      "sizeBytes": 1234,
      "modifiedAt": "ISO timestamp"
    }
  ],
  "total": 3,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/subsystems/:id/implementation-plans`

**Purpose:** Get plans linked to a subsystem.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope with `subsystemId` instead of `systemId`. Same item shape.

### `GET /api/features/:id/implementation-plans`

**Purpose:** Get plans linked to a feature.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope with `featureId` instead of `systemId`. Same item shape.

---

## 17. Audit Files

Audit endpoints manage the projection of filesystem `.md` audit files into the `audit_files` database table. The source directory is `/home/codex/dev/nexus/audit`.

### `GET /api/audit`

**Purpose:** List all audit file records (metadata only, no content).

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "filePath": "IMPLEMENTATION_PLANS/...",
      "content": "",
      "sizeBytes": 1234,
      "updatedAt": 1234567890000
    }
  ],
  "total": 10,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/audit/graph`

**Purpose:** Return agent records as nodes and cross-references as edges for a graph visualization. **Must be registered before `/audit/:id` in Express routing.**

**Query Parameters:**

- `limit` — max results (default 200, max 500)

**Response `200`:**

```json
{
  "entities": [...],
  "edges": [...],
  "entityCount": 42,
  "edgeCount": 67
}
```

### `GET /api/audit/:id`

**Purpose:** Get a single audit file record with full content.

**Response `200`:**

```json
{
  "id": "uuid",
  "filePath": "IMPLEMENTATION_PLANS/...",
  "content": "# Full markdown...",
  "sizeBytes": 1234,
  "updatedAt": 1234567890000
}
```

### `POST /api/audit/sync`

**Purpose:** Scan the filesystem audit directory and upsert all `.md` files into the database. Uses bitemporal history (`audit_files_history`) — closes existing current records and inserts new ones.

**Response `200`:** Array of file objects (content emptied for performance).

### `POST /api/audit/:id/regenerate`

**Purpose:** Re-read a specific audit file from disk and update its database record. Has path traversal protection.

**Response `200`:** Updated file object with full content.

---

## 18. User Preferences

### `GET /api/preferences`

**Purpose:** Get all preferences for the default user as a flat key-value map.

**Response `200`:**

```json
{ "theme": "dark", "kanbanView": "swimlane", ... }
```

### `PUT /api/preferences/:key`

**Purpose:** Set a single preference value (bitemporal upsert).

**Request Body:**

```json
{ "value": "any JSON value" }
```

**Response `200`:**

```json
{ "ok": true }
```

### `DELETE /api/preferences/:key`

**Purpose:** Delete a single preference (reset to default).

**Response `200`:**

```json
{ "ok": true }
```

---

## 19. Harvests

### `GET /api/harvests`

**Purpose:** List all harvests with sort/filter support and pagination. Supports computed analytics (code blocks, turns, blocks per turn, user turns) via docklang introspection.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)
- `sort` — sort field (default `created_at`). Valid values:
  - `created_at` — by creation timestamp
  - `candidate_count` — by total candidates extracted
  - `code_blocks` — by number of code blocks in the harvest
  - `turns` — by discourse unit count
  - `block_density` — by blocks per turn ratio
  - `collaboration` — by user turns in the conversation
  - `tag_frequency` — by tag overlap frequency
  - `keyword_hits` — by keyword match count (requires `keyword` param)
- `model` — filter by model name (e.g. `gpt-4o`, `claude-sonnet-4-20250514`)
- `version` — filter by harvest version (integer)
- `sourceHash` — filter by source hash
- `level` — filter by abstraction level (integers 1–4)
- `visibilityScope` — filter by visibility scope
- `tag` — filter by tag (checks if tag is present in the array)
- `keyword` — search keyword for `sort=keyword_hits` mode

**Response `200`:** Paginated envelope with computed analytics fields.

```json
{
  "items": [
    {
      "id": "uuid",
      "sourcePath": "string",
      "sourceFilename": "string",
      "model": "string",
      "totalCandidates": 5,
      "tags": ["tag1", "tag2"],
      "metadata": {},
      "createdAt": "ISO timestamp",
      "level": 1,
      "visibilityScope": "builder",
      "sourceHash": "string",
      "fileSize": 12345,
      "version": 2,
      "runMetadata": {},
      "codeBlocks": 12,
      "turns": 8,
      "blocksPerTurn": 1.5,
      "userTurns": 3,
      "keywordHits": 0,
      "tagFrequency": 0
    }
  ],
  "total": 42,
  "page": 1,
  "pageSize": 100,
  "sort": "created_at"
}
```

### `GET /api/harvests/:id`

**Purpose:** Fetch a full harvest record with its candidates.

**Response `200`:** Full harvest object with `candidates` array nested. Fields include: `id`, `sourcePath`, `sourceFilename`, `model`, `totalCandidates`, `candidates`, `sourceText`, `tags`, `metadata`, `level`, `visibilityScope`, `docklang`, `sourceHash`, `fileSize`, `version`, `runMetadata`, `createdAt`.

### `GET /api/harvests/:id/transcript`

**Purpose:** Reconstructed conversation transcript with code blocks and diagrams, grouped by turn.

**Response `200`:**

```json
{
  "id": "uuid",
  "sourceFilename": "string",
  "title": "string",
  "turns": [...],
  "candidates": [...],
  "stats": {}
}
```

### `GET /api/harvests/distribution`

**Purpose:** Analytics histograms across all harvests — tag frequency, block type distribution, aggregate stats.

**Response `200`:**

```json
{
  "tagDistribution": [{ "tag": "string", "count": 5 }],
  "blockTypeDistribution": [{ "type": "code", "count": 42 }],
  "aggregates": {
    "totalHarvests": 100,
    "totalCandidates": 250,
    "avgCandidatesPerHarvest": 2.5
  }
}
```

### `POST /api/harvests`

**Purpose:** Create a new harvest record and unpack candidates into `harvest_candidates` (dual-write: JSONB preserved for Rover + relational for linking).

**Request Body:**

```json
{
  "sourcePath": "string",
  "sourceFilename": "string",
  "model": "string",
  "totalCandidates": 5,
  "candidates": [{ "title": "...", "intent_description": "...", ... }],
  "sourceText": "string",
  "tags": ["tag1", "tag2"],
  "metadata": {},
  "level": 1,
  "visibilityScope": "all",
  "sourceHash": "sha256...",
  "fileSize": 1234,
  "runMetadata": {},
  "docklang": {}
}
```

**Response `201`:** Created harvest object with created candidate IDs.

### `PATCH /api/harvests/:id`

**Purpose:** Update harvest source_text.

### `DELETE /api/harvests/:id`

**Purpose:** Delete a harvest and cascade to its candidates.

---

## 20. Harvest Candidates

### `GET /api/harvest-candidates`

**Purpose:** List all harvest candidates with pagination.

**Query Parameters:**

- `page`, `pageSize` — pagination

**Response `200`:** Standard paginated envelope with candidate objects.

### `GET /api/harvest-candidates/:id`

**Purpose:** Fetch a single harvest candidate with full detail.

**Response `200`:**

```json
{
  "id": "uuid",
  "harvestId": "uuid",
  "title": "string",
  "intentDescription": "string|null",
  "implementationNotes": {},
  "codeSnippets": {},
  "openQuestions": {},
  "tags": [],
  "status": "string|null",
  "systemId": "uuid|null",
  "subsystemId": "uuid|null",
  "featureId": "uuid|null",
  "workRequestId": "uuid|null",
  "completed": false,
  "compilationReadiness": 0.8,
  "createdAt": "ISO timestamp",
  "updatedAt": "ISO timestamp",
  "harvestSourceFilename": "string|null"
}
```

### `GET /api/harvest-candidates/:id/dependencies`

**Purpose:** List all other candidates that this candidate depends on (from `nebula.candidate_dependencies`).

**Response `200`:**

```json
{
  "candidateId": "uuid",
  "dependencies": [
    {
      "id": "uuid",
      "candidateId": "uuid",
      "dependsOnId": "uuid",
      "createdAt": "ISO timestamp"
    }
  ],
  "count": 3
}
```

### `POST /api/harvest-candidates/:id/promote`

**Purpose:** Mark a candidate as promoted/useful (sets `completed = true` and links to a system).

### `POST /api/harvest-candidates/promote-to-plan`

**Purpose:** Collate multiple useful candidates into a conduit plan. Calls `candidates_to_plan()` DB function.

### `GET /api/plans/:planRef/candidates`

**Purpose:** Reverse lookup — find all harvest candidates linked to a conduit plan via cross-references.

---

## 22. Specifications

### `GET /api/specifications`

**Purpose:** List all specifications (bitemporal revisions) with pagination.

**Query Parameters:**

- `page`, `pageSize` — pagination

**Response `200`:**

```json
{
  "items": [
    {
      "id": "uuid",
      "agendaId": "uuid",
      "revisionNumber": 1,
      "revisionType": "string",
      "supersededBy": "uuid|null",
      "derivedFrom": [],
      "itemSnapshot": {},
      "changeSummary": "string|null",
      "validFrom": "ISO timestamp",
      "validUntil": "ISO timestamp",
      "createdAt": "ISO timestamp"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/specifications/:id`

**Purpose:** Fetch a single specification revision.

---

## 23. Agendas

### `GET /api/agendas`

**Purpose:** List all agendas with pagination.

**Query Parameters:**

- `page`, `pageSize` — pagination

**Response `200`:**

```json
{
  "items": [
    {
      "id": "uuid",
      "title": "string",
      "scope": "string|null",
      "status": "string",
      "cohesionScore": 0.85,
      "sourceCount": 10,
      "plannerAnalysis": "string|null",
      "plannerConflicts": {},
      "plannerGaps": {},
      "overlapMatrix": {},
      "metadata": {},
      "createdAt": "ISO timestamp",
      "updatedAt": "ISO timestamp"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/agendas/:id`

**Purpose:** Fetch a single agenda.

### `GET /api/agendas/:id/items`

**Purpose:** Fetch all agenda items for a given agenda.

**Response `200`:** Array of agenda item objects:

```json
[
  {
    "id": "uuid",
    "agendaId": "uuid",
    "sourceType": "string",
    "sourceId": "uuid",
    "title": "string",
    "body": "string|null",
    "decisions": {},
    "openQuestions": {},
    "supportingRefs": {},
    "included": true,
    "plannerNote": "string|null",
    "createdAt": "ISO timestamp",
    "updatedAt": "ISO timestamp"
  }
]
```

---

## 24. Assessments

### `GET /api/assessments`

**Purpose:** List all assessments with pagination.

**Query Parameters:**

- `page`, `pageSize` — pagination

**Response `200`:**

```json
{
  "items": [
    {
      "id": "uuid",
      "observationId": "uuid",
      "outcome": "string",
      "confidence": 0.9,
      "impactScope": {},
      "openQuestions": {},
      "agendaId": "uuid|null",
      "autoResolvePlanId": "uuid|null",
      "forumPostId": "uuid|null",
      "analysisDetail": "string|null",
      "createdAt": "ISO timestamp"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/assessments/:id`

**Purpose:** Fetch a single assessment.

### Assessment Resolutions (`nebula.assessment_resolutions`)

**Purpose:** Records the resolution outcome of an assessment event. Each row captures a single resolution pass — the outcome reached, confidence level, and optional rationale.

**Table Schema:**

| Column             | Type               | Notes                                                          |
| ------------------ | ------------------ | -------------------------------------------------------------- |
| `resolution_id`    | `uuid` (PK)        | Auto-generated with `gen_random_uuid()`                        |
| `event_id`         | `uuid` (NOT NULL)  | Foreign key to the assessment or event being resolved          |
| `outcome`          | `text` (NOT NULL)  | Resolution outcome string                                      |
| `confidence`       | `double precision` | Confidence level of the resolution (nullable)                  |
| `rationale`        | `jsonb`            | Structured reasoning supporting the resolution (nullable)      |
| `dimensions_used`  | `integer`          | Number of evaluation dimensions or criteria applied (nullable) |
| `dimensions_total` | `integer`          | Total available dimensions considered (nullable)               |
| `resolved_at`      | `timestamptz`      | Auto-set to `now()` on creation                                |

**Relationships:**

- `resolution_id` is the primary key.
- `event_id` references the assessment event being resolved (no explicit FK constraint currently).
- This table stores the output of a resolution pass, while `nebula.assessments` stores the original assessment artifacts.

**Note:** No dedicated REST endpoints exist yet for this table. The data is consumed via the assessment lifecycle workflow or database queries.

---

## 25. Observations

### `GET /api/observations`

**Purpose:** List all observations with pagination.

**Query Parameters:**

- `page`, `pageSize` — pagination

**Response `200`:**

```json
{
  "items": [
    {
      "id": "uuid",
      "triggerType": "string",
      "sourceArtifactType": "string|null",
      "sourceArtifactId": "uuid|null",
      "payload": {},
      "assessed": false,
      "createdAt": "ISO timestamp"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/observations/:id`

**Purpose:** Fetch a single observation.

---

## 26. Roles

### `GET /api/roles`

**Purpose:** List all role definitions (governance roles with capabilities, visibility scopes, and cron schedules). Paginated.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "architect",
      "displayName": "Architect",
      "description": "Owns architecture decisions, generates specifications...",
      "ownsDomains": ["architecture_decisions", "specifications"],
      "canGreenlight": false,
      "canCreateQuestions": true,
      "canCreateAgendas": false,
      "canResolveQuestions": true,
      "canVerifyWorkRequests": true,
      "maxOpenQuestions": null,
      "requiresApprovalFrom": [],
      "cronEnabled": false,
      "cronExpression": null,
      "cronDescription": null,
      "escalatesTo": ["topologist"],
      "escalationTriggers": ["topology_conflict"],
      "levelFilterPrimary": "level <= 3",
      "levelFilterAllowed": "level = 4",
      "visibilityScope": ["architect", "all"],
      "createdAt": "ISO timestamp",
      "updatedAt": "ISO timestamp"
    }
  ],
  "total": 8,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/roles/:id`

**Purpose:** Fetch a single role definition by UUID.

---

## 27. Open Questions

### `GET /api/open-questions`

**Purpose:** List open questions with pagination and optional entity-type filter.

**Query Parameters:**

- `page`, `pageSize` — pagination
- `entityType` — filter by linked entity type (`requirement`, `candidate`, `harvest`, `agent_record`, etc.)
- `entityId` — filter by linked entity UUID
- `resolved` — if `true`, returns only resolved questions

**Response `200`:** Paginated envelope with question objects.

### `GET /api/open-questions/:id`

**Purpose:** Fetch a single open question with linked entity context.

**Response `200`:**

```json
{
  "id": "uuid",
  "requirementId": "uuid|null",
  "candidateId": "uuid|null",
  "title": "string",
  "description": "string|null",
  "category": "string",
  "status": "OPEN|RESOLVED",
  "blocking": false,
  "createdBy": "string",
  "createdAt": "ISO timestamp",
  "updatedAt": "ISO timestamp",
  "resolvedAt": "ISO timestamp|null",
  "entityType": "string|null",
  "entityId": "uuid|null",
  "entityTitle": "string|null"
}
```

### `POST /api/open-questions`

**Purpose:** Create a new open question with optional entity linking.

**Request Body:**

```json
{
  "title": "string (required)",
  "description": "string",
  "category": "AMBIGUITY|MISSING_INFO|CONFLICT|SCOPE|DEPENDENCY|DUPLICATE_CANDIDATE|WORK_COMPLETED",
  "blocking": false,
  "requirementId": "uuid|null",
  "candidateId": "uuid|null",
  "entityType": "string|null",
  "entityId": "uuid|null"
}
```

**Response `201`:** `{ "id": "uuid" }`

### `PUT /api/open-questions/:id/resolve`

**Purpose:** Mark an open question as resolved. Requires the question to have at least one answer in `open_question_answers`.

**Request Body:**

```json
{ "resolvedBy": "string (required)" }
```

### `GET /api/open-questions/:id/answers`

**Purpose:** List all answers for a question.

**Response `200`:**

```json
{
  "answers": [
    {
      "id": "uuid",
      "questionId": "uuid",
      "role": "string",
      "answer": "string",
      "confidence": "HIGH|MEDIUM|LOW",
      "reasoning": "string|null",
      "answeredAt": "ISO timestamp"
    }
  ],
  "count": 2
}
```

### `POST /api/open-questions/:id/answers`

**Purpose:** Add or upsert an answer for a question (one answer per role).

**Request Body:**

```json
{
  "answer": "string (required)",
  "role": "string (required)",
  "confidence": "HIGH|MEDIUM|LOW",
  "reasoning": "string|null"
}
```

**Response `201`:** The created/updated answer object.

### `GET /api/open-questions/:id/timeline`

**Purpose:** Get a timeline of events for a question (creation, status changes, agent records).

**Response `200`:** Array of timeline events with `type`, `label`, `description`, `timestamp`, `actor`, `icon` fields.

### `GET /api/open-questions/:id/participants`

**Purpose:** List deliberation participants for a question (from `nebula.deliberation_participants`).

**Response `200`:**

```json
{
  "openQuestionId": "uuid",
  "participants": [
    {
      "id": "uuid",
      "openQuestionId": "uuid",
      "role": "string",
      "participatedAt": "ISO timestamp",
      "contribution": "string|null"
    }
  ],
  "count": 2
}
```

### `POST /api/open-questions/:id/participants`

**Purpose:** Record a deliberation participant for a question.

**Request Body:**

```json
{
  "role": "string (required)",
  "contribution": "string|null"
}
```

---

## 27. Search

### `POST /api/search/semantic`

**Purpose:** Vector similarity search against the knowledge graph. Accepts a pre-embedded query vector (768-dim, matching nomic-embed-text) and returns the most similar indexed entities.

**Request Body:**

```json
{
  "queryEmbedding": [0.001, -0.002, ...],
  "limit": 10,
  "targetSection": "string|null"
}
```

`queryEmbedding` is required — an array of 768 floats. `limit` defaults to 10 (max 100). `targetSection` optionally restricts results to a specific entity section.

**Response `200`:** Matching entities with similarity scores.

```json
{
  "query": {
    "limit": 10,
    "targetSection": null
  },
  "results": [
    {
      "section": "string",
      "entityId": "string",
      "name": "string",
      "description": "string",
      "similarity": 0.85
    }
  ],
  "total": 10
}
```

---

### `GET /api/search?q=...`

**Purpose:** Cross-entity full-text search across 13 entity types. Returns unified results.

**Query Parameters:**

- `q` — search query (required, minimum 2 characters)

**Searched Entities (using `ILIKE` with `ESCAPE` for SQL safety):**

- Threads (`assembly.posts` — title + body)
- Requirements (`nebula.requirements` — title + description)
- Agendas (`nebula.agendas` — title + planner_analysis)
- Harvest Candidates (`nebula.harvest_candidates` — title + intent_description)
- Harvests (`nebula.harvests` — source_filename + source_text)
- Open Questions (`nebula.open_questions` — title + description)
- Assessments (`nebula.assessments` — outcome + analysis_detail)
- Observations (`nebula.observations` — trigger_type + payload)
- Agent Records (`nebula.agent_records` — title + content)
- Specifications (`nebula.specifications` — change_summary + revision_type)
- Plans (`nebula.plans` — title + goal + content) _(migrated from `conduit.plan_status`)_
- Assembly Users (`assembly.users` — alias + email)

All 13 queries run in parallel via `Promise.all`. Results are capped at 100 total items.

**Response `200`:**

```json
{
  "query": "database",
  "results": [
    {
      "type": "requirement",
      "id": "uuid",
      "title": "string",
      "description": "string (truncated to 200 chars)",
      "status": "string|null",
      "href": "/requirements/{id}"
    }
  ],
  "total": 15
}
```

Each result has a `type` discriminator (`thread`, `requirement`, `agenda`, `candidate`, `harvest`, `open_question`, `assessment`, `observation`, `agent_record`, `specification`, `plan`, `user`) and an `href` for frontend routing. Hrefs use hyphenated route paths (e.g., `open-question` → `/open-questions/{id}`).

---

## 28. Counts

### `GET /api/counts`

**Purpose:** Return aggregate row counts across 13 tables in a single response. All queries run in parallel via `Promise.all`.

**Response `200`:**

```json
{
  "threads": 42,
  "requirements": 85,
  "agendas": 3,
  "candidates": 30,
  "harvests": 25,
  "openQuestions": 15,
  "assessments": 8,
  "observations": 20,
  "agentRecords": 150,
  "specifications": 6,
  "plans": 18,
  "users": 10
}
```

**Counted tables:**

| Response Key     | Table                       |
| ---------------- | --------------------------- |
| `threads`        | `assembly.posts`            |
| `requirements`   | `nebula.requirements`       |
| `agendas`        | `nebula.agendas`            |
| `candidates`     | `nebula.harvest_candidates` |
| `harvests`       | `nebula.harvests`           |
| `openQuestions`  | `nebula.open_questions`     |
| `assessments`    | `nebula.assessments`        |
| `observations`   | `nebula.observations`       |
| `agentRecords`   | `nebula.agent_records`      |
| `specifications` | `nebula.specifications`     |
| `plans`          | `nebula.plans`              | _(migrated from `conduit.plan_status`)_ |
| `users`          | `assembly.users`            |

---

## 29. Block Segmentation (Internal Services)

Block segmentation operates on conversation snapshots, blocks, segments, projection overrides, and harvest references. These are called internally by the service layer but exposed through the `block-segmentation.service.ts` module.

### `listSnapshots(pool, conversationId)`

**Purpose:** List all snapshots for a conversation.

### `listBlocks(pool, snapshotId, diffFrom?)`

**Purpose:** List all blocks for a snapshot, with optional diff against a previous snapshot. Also returns segments and overrides.

### `createSnapshot(pool, params)`

**Purpose:** Create a snapshot and optionally its blocks (transactional).

### `createSegment(pool, params)`

**Purpose:** Commit a block range as a segment. Resolves legacy `"block-<index>"` ID formats to UUIDs.

### `updateSegment(pool, segmentId, updates)`

**Purpose:** Update segment type, state, title, or notes.

### `supersedeSegment(pool, segmentId)`

**Purpose:** Delete a segment (hard-delete; no bitemporal close).

### `createProjectionOverride(pool, params)`

**Purpose:** Add a projection override (exclude/include a block/segment from projection).

### `removeProjectionOverride(pool, overrideId)`

**Purpose:** Delete a projection override.

### `getProjection(pool, snapshotId, projectionTarget)`

**Purpose:** Get the BP (Blueprint) projection — blocks, segments, and active overrides.

### `listReferences(pool, snapshotId, filters?)`

**Purpose:** List harvest references for a snapshot, filterable by state, edge_type, and min_confidence.

---

## 31. Cross-References

### `GET /api/cross-references`

**Purpose:** List all cross-references from `nebula.cross_references` with optional filters and pagination. Cross-references link entities across the knowledge graph (requirements → plans, harvests → candidates, etc.).

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)
- `sourceType` — filter by source entity type (e.g. `requirement`, `harvest_candidate`)
- `sourceId` — filter by source entity UUID
- `targetType` — filter by target entity type (e.g. `plan`, `requirement`)
- `targetId` — filter by target entity UUID
- `relType` — filter by relationship type (e.g. `req:blocks`, `spawns_plan`, `compiles_to`)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "sourceType": "string",
      "sourceId": "uuid",
      "targetType": "string",
      "targetId": "uuid",
      "relType": "string",
      "metadata": {},
      "createdAt": "epoch ms"
    }
  ],
  "total": 42,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/cross-references/:id`

**Purpose:** Fetch a single cross-reference by UUID.

**Response `200`:** Single cross-reference object.

For the complete taxonomy of valid `rel_type` values, see [Cross-Reference Taxonomy](#cross-reference-taxonomy).

---

## 32. Evidence Links

### `GET /api/evidence-links`

**Purpose:** List all evidence links from `knowledge.evidence_links` with optional filters and pagination. Evidence links connect knowledge graph entities to their source evidence in harvests, candidates, or other artifacts.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)
- `knowledgeEntityId` — filter by knowledge graph entity UUID
- `nebulaHarvestId` — filter by harvest UUID
- `nebulaCandidateId` — filter by harvest candidate UUID
- `linkType` — filter by link type (e.g. `supports`, `contradicts`, `informs`)
- `provenance` — filter by provenance source
- `minConfidence` — minimum confidence filter (float)
- `maxConfidence` — maximum confidence filter (float)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "knowledgeEntityId": "uuid",
      "nebulaHarvestId": "uuid|null",
      "nebulaCandidateId": "uuid|null",
      "linkType": "string",
      "provenance": "string|null",
      "confidence": 0.85,
      "metadata": {},
      "createdAt": "epoch ms"
    }
  ],
  "total": 15,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/evidence-links/:id`

**Purpose:** Fetch a single evidence link by UUID.

**Response `200`:** Single evidence link object.

Valid `linkType` values: `supports`, `refines`, `instantiates`, `contradicts`, `supersedes`, `mentions`, `informs`, `validates`.

---

## 33. Agent Records

### `GET /api/agent-records`

**Purpose:** List agent records with optional filters and pagination. Agent records are durable audit entries created by AI agents during operation.

**Query Parameters:** (all optional)

- `type` — filter by record type (`report`, `analysis`, `assessment`, `decision`, `engineering_log`)
- `role` — filter by role (`architect`, `engineer`, `planner`, `reviewer`, `inspector`, `analyst`)
- `systemId` — filter by linked system UUID
- `subsystemId` — filter by linked subsystem UUID
- `featureId` — filter by linked feature UUID
- `planRef` — filter by plan reference
- `tag` — filter by tag (single value, comma-separated for OR, multiple `?tag=` params for AND conjunction)
- `search` — text search across title and content (ILIKE)
- `createdAfter` — records created after this ISO timestamp
- `createdBefore` — records created before this ISO timestamp
- `level` — filter by abstraction level (1–4)
- `visibilityScope` — filter by visibility scope
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "recordType": "report",
      "role": "engineer",
      "title": "string",
      "sourcePath": "string|null",
      "tags": ["tag1", "tag2"],
      "systemId": "uuid|null",
      "subsystemId": "uuid|null",
      "featureId": "uuid|null",
      "planRef": "string|null",
      "level": 1,
      "visibilityScope": "builder",
      "createdAt": "ISO timestamp"
    }
  ],
  "total": 42,
  "page": 1,
  "pageSize": 100
}
```

> Note: The `content` field is excluded from list responses for performance. Use `GET /api/agent-records/:id` for full record content.

### `GET /api/agent-records/:id`

**Purpose:** Fetch a single agent record with full content.

**Response `200`:** Full agent record object including `content` field.

---

## 34. OP Registry

The OP Registry (`nebula.op_registry`) stores operation definitions — known intents with associated opcode sequences. Entries are versioned by `intent_id` and soft-deleted.

### `GET /api/op-registry`

**Purpose:** List operation registry entries with pagination. Returns only non-deleted entries (`deleted_at IS NULL` (DB column name)).

**Query Parameters:** (all optional)

- `intentId` — filter by intent identifier string
- `status` — filter by status value
- `search` — text search across label, intentId, and notes (ILIKE)
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope, ordered by `intent_id, version DESC`.

```json
{
  "items": [
    {
      "id": "uuid",
      "intentId": "WRITE_FILE",
      "version": 1,
      "label": "Write file operation",
      "description": "string",
      "notes": "string|null",
      "opSequence": ["CREATE_FILE", "VALIDATE_SYNTAX"],
      "status": "active",
      "metadata": {},
      "deletedAt": null,
      "createdAt": "ISO timestamp",
      "updatedAt": "ISO timestamp"
    }
  ],
  "total": 12,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/op-registry/:id`

**Purpose:** Fetch a single registry entry by UUID.

**Response `200`:** Single registry entry object.

### `GET /api/op-registry/:id/lineage`

**Purpose:** Fetch version lineage for a specific `intent_id`, showing all historical versions (including soft-deleted).

---

## 35. Knowledge Entities

Knowledge entities (`knowledge.graph_entities`) represent nodes in the knowledge graph — concepts, documents, or artifacts indexed for cross-referencing.

### `GET /api/knowledge/entities`

**Purpose:** List knowledge graph entities with optional filters and pagination.

**Query Parameters:** (all optional)

- `section` — filter by section name
- `entity_type` — filter by entity type
- `search` — text search across name and description (ILIKE, first 500 chars of description only)
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "section": "string",
      "entityId": "uuid",
      "name": "string",
      "entityType": "string",
      "status": "string",
      "descriptionAbbr": "string (first 500 chars)",
      "createdAt": "ISO timestamp",
      "updatedAt": "ISO timestamp"
    }
  ],
  "total": 25,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/knowledge/entities/:section/:entityId`

**Purpose:** Fetch a single entity by its composite key (section + entity_id). Returns full description.

**Response `200`:** Full entity object.

### `GET /api/knowledge/entities/:section/:entityId/relations`

**Purpose:** List inbound and outbound edge relations for a specific entity, both with pagination. Outbound edges go FROM this entity TO others; inbound edges come FROM others TO this entity.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

Both outbound and inbound use the same page/pageSize.

**Response `200`:**

```json
{
  "entity": {
    "section": "string",
    "entityId": "uuid"
  },
  "outbound": {
    "items": [
      {
        "id": "uuid",
        "relationType": "string",
        "targetSection": "string",
        "targetId": "uuid",
        "targetName": "string",
        "properties": {}
      }
    ],
    "total": 5,
    "page": 1,
    "pageSize": 100
  },
  "inbound": {
    "items": [
      {
        "id": "uuid",
        "relationType": "string",
        "sourceSection": "string",
        "sourceId": "uuid",
        "sourceName": "string",
        "properties": {}
      }
    ],
    "total": 3,
    "page": 1,
    "pageSize": 100
  }
}
```

---

## 36. Knowledge Edges

Knowledge edges (`knowledge.graph_edges`) represent directed relationships between nodes in the knowledge graph.

### `GET /api/knowledge/cross-references`

**Purpose:** List cross-references for graph overlay visualization. Unions knowledge graph cross-references with harvest_candidate spawn-plan cross-references from `nebula.cross_references`.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "mapName": "string",
      "sourceSection": "string",
      "sourceId": "uuid",
      "targetSection": "string",
      "targetId": "uuid",
      "weight": 1.0
    }
  ],
  "total": 50,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/knowledge/summary`

**Purpose:** Knowledge graph aggregate statistics — total entity/edge/cross-reference counts, per-section entity breakdown, per-type edge breakdown, and embedding coverage from `knowledge.v_graph_summary`.

**Response `200`:**

```json
{
  "entityCount": 100,
  "edgeCount": 250,
  "crossReferenceCount": 50,
  "bySection": [
    { "section": "requirement", "count": 42 },
    { "section": "api", "count": 15 }
  ],
  "byRelationType": [
    { "relation_type": "depends_on", "count": 80 },
    { "relation_type": "implements", "count": 30 }
  ],
  "embeddingSummary": [
    { "section": "api", "entity_count": 15, "embedded_count": 10 },
    { "section": "requirement", "entity_count": 42, "embedded_count": 30 }
  ]
}
```

### `GET /api/knowledge/edges`

**Purpose:** List knowledge graph edges with optional filters and pagination.

**Query Parameters:** (all optional)

- `source_section` — filter by source entity section
- `source_id` — filter by source entity UUID
- `target_section` — filter by target entity section
- `target_id` — filter by target entity UUID
- `relation_type` — filter by relationship type
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "sourceSection": "string",
      "sourceId": "uuid",
      "sourceName": "string",
      "targetSection": "string",
      "targetId": "uuid",
      "targetName": "string",
      "relationType": "string",
      "weight": 1.0,
      "metadata": {},
      "createdAt": "epoch ms",
      "updatedAt": "epoch ms"
    }
  ],
  "total": 50,
  "page": 1,
  "pageSize": 100
}
```

---

## 37. Projections

Projections (`nebula.projections`) define deterministic or inference-based views that render database state into markdown files on disk.

### `GET /api/projections`

**Purpose:** List all projection configs with pagination.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "string",
      "type": "deterministic|inference",
      "description": "string|null",
      "targetPath": "string",
      "model": "string|null",
      "schedule": "string|null",
      "createdAt": "ISO timestamp"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `POST /api/projections`

**Purpose:** Create a new projection config.

**Request Body:**

```json
{
  "name": "string (required)",
  "type": "deterministic|inference (required)",
  "description": "string",
  "sourceQuery": "string",
  "template": "string",
  "targetPath": "string",
  "model": "string",
  "schedule": "string",
  "metadata": {}
}
```

**Response `201`:** Created projection object.

### `POST /api/projections/:id/render`

**Purpose:** Execute a projection and write output files to disk.

### `DELETE /api/projections/:id`

**Purpose:** Delete a projection config.

---

## 38. Deleted Plans (was Conduit Deleted Plans)

> **Migration in progress:** `conduit.plans` has been eliminated. `conduit.plan_status` and `conduit.plans_by_status` are migrating to `nebula`. This endpoint reflects the current routes.ts state and will be updated once the migration completes.

### `GET /api/conduit/deleted-plans`

**Purpose:** Shortcut to find all soft-deleted plans (`deleted = 1`). Returns newest updated first.

**Query Parameters:**

- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope from `nebula.plans WHERE deleted = 1`.

```json
{
  "items": [
    {
      "id": "PLN-xxx",
      "title": "string",
      "goal": "string",
      "status": "string",
      "deleted": 1,
      "updatedAt": "ISO timestamp"
    }
  ],
  "total": 3,
  "page": 1,
  "pageSize": 100
}
```

---

## 39. Execution Requests

### `GET /api/execution/requests`

**Purpose:** List execution work requests with optional status filter and pagination.

**Query Parameters:** (all optional)

- `status` — filter by status (e.g. `DRAFT`, `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`)
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope.

```json
{
  "items": [
    {
      "id": "uuid",
      "businessKey": "string",
      "title": "string",
      "intentType": "task|...",
      "objective": "string|null",
      "inputs": {},
      "deterministic": true,
      "maxRetries": null,
      "timeoutPolicy": null,
      "resourceHints": [],
      "opTrace": {},
      "status": "DRAFT",
      "sourcePlanId": "uuid|null",
      "sourceWrId": "uuid|null",
      "createdAt": "ISO timestamp",
      "updatedAt": "ISO timestamp"
    }
  ],
  "total": 10,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/execution/requests/:id`

**Purpose:** Fetch a single execution request by UUID.

**Response `200`:** Single execution request object.

### `POST /api/execution/requests`

**Purpose:** Create a new execution request with a unique `businessKey`.

**Request Body:**

```json
{
  "businessKey": "string (required, unique)",
  "title": "string",
  "intentType": "task|...",
  "objective": "string",
  "inputs": {},
  "deterministic": true,
  "maxRetries": null,
  "timeoutPolicy": null,
  "resourceHints": [],
  "opTrace": {},
  "status": "DRAFT",
  "sourcePlanId": "uuid|null",
  "sourceWrId": "uuid|null"
}
```

**Response `201`:** Created execution request object.

**Response `409`:** Business key conflict.

---

## 40. Execution Receipts

### `GET /api/execution/receipts`

**Purpose:** List execution receipts with optional filters and pagination. Receipts record the outcome of execution attempts.

**Query Parameters:** (all optional)

- `requestId` — filter by linked request UUID
- `type` — filter by receipt type (e.g. `EXECUTION_COMPLETE`, `EXECUTION_FAILED`)
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope, ordered by `issued_at DESC`.

```json
{
  "items": [
    {
      "id": "uuid",
      "attemptId": "uuid",
      "requestId": "uuid",
      "type": "EXECUTION_COMPLETE",
      "agentRole": "string",
      "summary": "string",
      "metadata": {},
      "issuedAt": "ISO timestamp"
    }
  ],
  "total": 15,
  "page": 1,
  "pageSize": 100
}
```

### `POST /api/execution/receipts`

**Purpose:** Issue a receipt for an execution attempt. The receipt type is auto-determined from the attempt's status if not provided.

**Request Body:**

```json
{
  "attemptId": "uuid (required)",
  "type": "EXECUTION_COMPLETE|EXECUTION_FAILED",
  "agentRole": "string",
  "summary": "string",
  "metadata": {}
}
```

**Response `201`:** Created receipt object.

**Response `404`:** Attempt not found.

---

## 41. Architect Specs

Architect specs (`nebula.architect_specs`) are lightweight audit-trail specifications written by the Architect cron. They record requirement analysis with optional work request linkage.

### `GET /api/architect-specs`

**Purpose:** List architect specs with optional filter and pagination.

**Query Parameters:** (all optional)

- `requirement_id` — filter by requirement UUID
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope, ordered by `created_at DESC`.

```json
{
  "items": [
    {
      "id": "uuid",
      "title": "string",
      "requirementId": "uuid",
      "workRequestId": "uuid|null",
      "content": {},
      "metadata": {},
      "createdAt": "ISO timestamp"
    }
  ],
  "total": 5,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/architect-specs/:id`

**Purpose:** Fetch a single architect spec by UUID.

**Response `200`:** Single architect spec object.

### `POST /api/architect-specs`

**Purpose:** Create a new architect spec record.

**Request Body:**

```json
{
  "title": "string (required)",
  "requirementId": "uuid (required)",
  "workRequestId": "uuid|null",
  "content": {},
  "metadata": {}
}
```

**Response `201`:** Created architect spec object.

### `DELETE /api/architect-specs/:id`

**Purpose:** Delete an architect spec.

**Response `200`:** `{ "ok": true }`

---

## 42. Artifact Provenance

Artifact provenance (`nebula.artifact_provenance`) traces "which exact source artifact did this derived object come from?" — version-level provenance tracking avoiding composite temporal foreign keys.

### `GET /api/artifact-provenance`

**Purpose:** List provenance records with optional filters and pagination.

**Query Parameters:** (all optional)

- `subject_type` — filter by subject entity type (e.g. `harvest_candidate`, `requirement`)
- `subject_id` — filter by subject entity UUID
- `source_type` — filter by source entity type (e.g. `harvest`, `requirement`)
- `source_id` — filter by source entity UUID
- `page` — page number (default 1, minimum 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope, ordered by `created_at DESC`.

```json
{
  "items": [
    {
      "id": "uuid",
      "subjectType": "harvest_candidate",
      "subjectId": "uuid",
      "sourceType": "harvest",
      "sourceId": "uuid",
      "sourceVersion": "string|null",
      "relationship": "derived_from",
      "metadata": {},
      "createdAt": "ISO timestamp"
    }
  ],
  "total": 10,
  "page": 1,
  "pageSize": 100
}
```

### `GET /api/artifact-provenance/:id`

**Purpose:** Fetch a single provenance record by UUID.

**Response `200`:** Single provenance record object.

### `POST /api/artifact-provenance`

**Purpose:** Create a provenance record (idempotent upsert — updates metadata if the subject-source pair already exists).

**Request Body:**

```json
{
  "subjectType": "string (required)",
  "subjectId": "uuid (required)",
  "sourceType": "string (required)",
  "sourceId": "uuid (required)",
  "sourceVersion": "string|null",
  "relationship": "derived_from|inspired_by|extracted_from",
  "metadata": {}
}
```

**Response `201`:** Created/updated provenance record.

### `DELETE /api/artifact-provenance/:id`

**Purpose:** Delete a provenance record.

**Response `200`:** `{ "ok": true }`

---

## Redis Cache Layer

The `block-segmentation-redis.service.ts` module provides a caching layer with these key patterns:

| Pattern                                           | Description                   | TTL     |
| ------------------------------------------------- | ----------------------------- | ------- |
| `nebula:session:{convId}`                         | Session context (hash)        | 1 hour  |
| `nebula:snapshot:{snapId}:block:{blockId}`        | Block metadata (hash)         | 2 hours |
| `nebula:snapshot:{snapId}:segment_candidates`     | Pending segments (hash)       | 1 hour  |
| `nebula:graph:{snapId}:out:{nodeId}`              | Forward graph adjacency (set) | —       |
| `nebula:graph:{snapId}:in:{nodeId}`               | Reverse graph adjacency (set) | —       |
| `nebula:snapshot:{snapId}:bp_projection:{target}` | BP projection (JSON string)   | 1 hour  |
| `inbox:pointer:{role}`                            | Per-role inbox watermark      | —       |

All cache entries are **recomputable from PostgreSQL** and are never a source of truth.

---

## Cross-Reference Taxonomy

The `crossref-taxonomy.ts` module defines the formal enumeration of all valid `rel_type` values:

| Namespace       | Types                                                                                                   |
| --------------- | ------------------------------------------------------------------------------------------------------- |
| **WRP**         | `wrp:depends_on`, `wrp:implements`, `wrp:tracked_by`, `wrp:impacts_system`, `wrp:supersedes`            |
| **Agent**       | `ag:references_plan`, `ag:same_thread_as`, `ag:prompted_by`, `ag:spawns_plan`, `ag:evidences_candidate` |
| **Knowledge**   | `kv:sourced_from`, `kv:informs`, `kv:cross_schema`, `kv:name_overlap`, `kv:description_overlap`         |
| **Requirement** | `req:blocks`, `req:depends_on`                                                                          |

**Evidence Link Types** (separate taxonomy for `knowledge.evidence_links`): `supports`, `refines`, `instantiates`, `contradicts`, `supersedes`, `mentions`, `informs`, `validates`.

---

## Status Normalization Map

The following input strings are normalized to the eight canonical statuses:

| Input Variant                                                    | Canonical    |
| ---------------------------------------------------------------- | ------------ |
| `backlog`, `new`                                                 | `Backlog`    |
| `todo`, `to-do`, `to do`                                         | `ToDo`       |
| `inprogress`, `in progress`, `in-progress`, `in_progress`, `wip` | `InProgress` |
| `active`                                                         | `Active`     |
| `blocked`                                                        | `Blocked`    |
| `done`, `complete`, `completed`, `resolved`                      | `Done`       |
| `cancelled`, `cancel`, `canceled`                                | `Cancelled`  |
| `accepted`, `accept`                                             | `Accepted`   |

---

## Error Response Format

All endpoints return errors in a consistent format:

```json
{ "error": "Human-readable error message" }
```

**HTTP Status Codes Used:**

- `200` — Success
- `201` — Created
- `400` — Bad request (missing/invalid fields)
- `403` — Forbidden (path traversal detected)
- `404` — Resource not found
- `409` — Conflict (concurrent modification detected)
- `500` — Internal server error

---

## 43. System Info Tabs

Bitemporal info tabs associated with systems. Maintains full version history in `nebula.system_info_tabs_history`.

### `GET /api/systems/:id/info`

**Purpose:** List all active info tabs for a system. Paginated.

**Query Parameters:**

- `page` — page number (default 1, min 1)
- `pageSize` — results per page (default 100, max 100)

**Response `200`:** Paginated envelope with tab objects.

### `PUT /api/systems/:id/info/:tabId`

**Purpose:** Upsert an info tab with versioned history. Closes the current active record in `system_info_tabs_history` and inserts a new one. If `tabId` is `harvest_context` and content is emptied, unlinks all candidates from the system.

**Request Body:**

```json
{ "content": "markdown string" }
```

**Response `200`:**

```json
{ "ok": true }
```

### `DELETE /api/systems/:id/info/:tabId`

**Purpose:** Soft-delete an info tab by closing its active history record. If `tabId` is `harvest_context`, unlinks all candidates from the system.

**Response `200`:**

```json
{ "ok": true }
```

---

## 44. Inbox Pointers

Redis-backed per-role inbox pointers used by the AGENTS.md inbox query protocol. Each role maintains a timestamp pointer indicating the last-seen message.

### `GET /api/inbox-pointer/:role`

**Purpose:** Get the inbox pointer timestamp for a specific role.

**Response `200`:** Pointer object from Redis, or null.

### `PUT /api/inbox-pointer/:role`

**Purpose:** Update the inbox pointer timestamp for a specific role.

**Request Body:**

```json
{ "timestamp": "ISO 8601 timestamp string" }
```

**Response `200`:**

```json
{ "ok": true }
```

### `GET /api/inbox-pointers`

**Purpose:** Debugging endpoint — retrieve all inbox pointers from Redis.

**Response `200`:** Object mapping role names to pointer values.

---

## 45. CPF — Compilation Readiness Framework

The Compilation Readiness Framework (`cpf`) scores harvest candidates by their readiness for promotion to conduit plans. Uses the `compilation_readiness` score on `nebula.harvest_candidates`.

### `GET /api/cpf`

**Purpose:** List harvest candidates with compilation readiness scoring. Supports filtering by threshold, candidate ID, system, and subsystem. Returns candidates with a `promotable` flag (score ≥ 0.7).

**Query Parameters:**

- `threshold` — minimum readiness score (float, default 0.0)
- `candidateId` — filter by candidate UUID
- `system` — filter by linked system UUID
- `subsystem` — filter by linked subsystem UUID
- `limit` — max results (default 50)
- `offset` — pagination offset (default 0)

### `GET /api/cpf/count`

**Purpose:** Return candidate counts grouped by readiness bands.

**Query Parameters:**

- `system` — optional system filter
- `subsystem` — optional subsystem filter

**Response `200`:**

```json
{
  "ready": 5,
  "promoted": 3,
  "nearMiss": 2,
  "low": 10
}
```

### `POST /api/cpf/promote`

**Purpose:** Promote a specific candidate if it passes readiness checks (status check + score ≥ 0.7). Updates the candidate status to `promoted`.

**Request Body:**

```json
{ "candidateId": "uuid (required)" }
```

**Response `200`:**

```json
{ "ok": true, "promoted": true }
```

---

## 46. Import / Seed

Utility endpoints for data import and seeding.

### `POST /api/import`

**Purpose:** Import data into the system. Accepts a JSON payload containing harvests, candidates, or other entity records for bulk ingestion.

**Request Body:** Structured JSON payload with entities to import.

**Response `200`:**

```json
{ "ok": true, "imported": { "harvests": 1, "candidates": 5 } }
```

### `POST /api/seed`

**Purpose:** Seed the database with initial test/demo data. Creates sample systems, subsystems, features, and requirements for development and testing.

**Response `200`:**

```json
{ "ok": true, "seeded": { "systems": 3, "requirements": 10 } }
```

---

## 47. Refresh Materialized Views

### `POST /api/refresh-stats`

**Purpose:** Refresh all materialized views in the `nebula` schema. Discovers views via `pg_matviews` and refreshes each concurrently to avoid table locks.

**Response `200`:**

```json
{ "ok": true, "refreshed": 5 }
```

---

## 48. Additional Harvest Candidate Operations

### `POST /api/harvest-candidates/:id/spawn-plan`

**Purpose:** Create a conduit plan from a single harvest candidate. Creates a cross-reference (`spawns_plan` rel_type) and attempts to generate a conduit plan from the candidate's intent and implementation data.

**Response `200`:**

```json
{ "ok": true, "planNumber": "PLN-xxx", "xrefId": "uuid" }
```

### `POST /api/harvest-candidates/discover`

**Purpose:** Run candidate discovery against unprocessed harvests. Scans harvest records for new potential candidates based on configured patterns and creates candidate records.

**Response `200`:**

```json
{ "ok": true, "discovered": 3, "totalCandidates": 15 }
```

### `PATCH /api/harvest-candidates/:id`

**Purpose:** Update a harvest candidate's metadata (tags, status, system/subsystem/feature links).

**Request Body:** Fields to update.

---

## 49. Additional Agenda Operations

### `POST /api/agendas/:id/finalize`

**Purpose:** Finalize an agenda — sets its status to finalized and generates specification revisions from the agenda's items.

**Response `200`:** Updated agenda object.

---

## 50. Additional Specification Operations

### `POST /api/specifications/:id/link-requirements`

**Purpose:** Link requirements to a specification. Creates cross-references between the spec and the specified requirements.

**Request Body:**

```json
{ "requirementIds": ["uuid", "uuid", ...] }
```

**Response `200`:**

```json
{ "ok": true, "linked": 3 }
```

---

## 51. Agent Record Search

### `POST /api/agent-records/search`

**Purpose:** Advanced text search across agent records with flexible filtering (by type, role, tag, date range, and full-text content matching).

**Request Body:**

```json
{
  "query": "search text",
  "type": "report|analysis|assessment|decision|engineering_log|null",
  "role": "architect|engineer|planner|reviewer|inspector|analyst|null",
  "tags": ["tag1", "tag2"],
  "createdAfter": "ISO timestamp|null",
  "createdBefore": "ISO timestamp|null",
  "limit": 50,
  "offset": 0
}
```

**Response `200`:**

```json
{
  "items": [...],
  "total": 15,
  "limit": 50,
  "offset": 0
}
```

---

## 52. Additional OP Registry Operations

### `POST /api/op-registry/fork`

**Purpose:** Fork an existing registry entry to create a new version under a different `intent_id`. Copies the opcode template and pattern set.

**Request Body:**

```json
{
  "sourceId": "uuid (required)",
  "newIntentId": "string (required)",
  "newLabel": "string (required)"
}
```

### `PATCH /api/op-registry/:id/deprecate`

**Purpose:** Mark a registry entry as deprecated (sets `status = 'deprecated'`). The entry remains in the database but is excluded from active matching.

**Response `200`:** Updated registry entry.

### `PATCH /api/op-registry/:id/supersede`

**Purpose:** Mark a registry entry as superseded and link it to its replacement entry.

**Request Body:**

```json
{ "supersededById": "uuid (required)" }
```

**Response `200`:** Updated registry entry.

---

## 53. Additional Execution Endpoints

### `POST /api/execution/leases/acquire`

**Purpose:** Acquire a lease on an `ADMITTED` or `READY` work request for exclusive execution. Validates the request exists and no `ACTIVE` lease already exists.

**Request Body:**

```json
{
  "requestId": "uuid (required)",
  "owner": "string (required)",
  "ttlSeconds": 300
}
```

**Response `200`:** Lease object with `id`, `status: "ACTIVE"`, `expiresAt`.

### `POST /api/execution/leases/:id/renew`

**Purpose:** Extend the `expires_at` time of an `ACTIVE` lease.

**Request Body:**

```json
{ "ttlSeconds": 300 }
```

**Response `200`:** Updated lease object.

### `POST /api/execution/leases/:id/release`

**Purpose:** Release an `ACTIVE` lease, setting its status to `RELEASED`.

**Response `200`:**

```json
{ "ok": true, "status": "RELEASED" }
```

### `POST /api/execution/attempts`

**Purpose:** Submit an execution attempt for a lease-related request. Tracks status, result, and exit code.

**Request Body:**

```json
{
  "requestId": "uuid (required)",
  "leaseId": "uuid",
  "executorId": "string",
  "status": "RUNNING|SUCCEEDED|FAILED"
}
```

**Response `201`:** Created attempt object.

### `GET /api/execution/state`

**Purpose:** Return an aggregate summary of the execution domain — counts of requests, leases, and attempts grouped by status, plus receipts grouped by type.

**Response `200`:**

```json
{
  "requests": { "DRAFT": 3, "COMPILED": 5, "ADMITTED": 2, "READY": 1 },
  "leases": { "ACTIVE": 1, "RELEASED": 4 },
  "attempts": { "RUNNING": 1, "SUCCEEDED": 3, "FAILED": 1 },
  "receipts": { "EXECUTION_STARTED": 4, "EXECUTION_COMPLETED": 3 },
  "totalRequests": 11,
  "activeLeases": 1
}
```

---

## Cross-Reference Taxonomy

All valid `rel_type` values for `nebula.cross_references`, organized by domain:

### WRP Domain

| Type                 | Source       | Target       | Description                       |
| -------------------- | ------------ | ------------ | --------------------------------- |
| `wrp:depends_on`     | plan         | plan         | Plan depends on another plan      |
| `wrp:implements`     | plan         | work_request | Plan implements a work request    |
| `wrp:tracked_by`     | work_request | plan         | Work request is tracked by a plan |
| `wrp:impacts_system` | plan         | system       | Plan impacts a system             |
| `wrp:supersedes`     | plan         | plan         | Plan supersedes another plan      |

### Agent Domain

| Type                     | Source            | Target            | Description                                    |
| ------------------------ | ----------------- | ----------------- | ---------------------------------------------- |
| `ag:references_plan`     | agent_record      | plan              | Agent record references a plan                 |
| `ag:same_thread_as`      | agent_record      | agent_record      | Two records belong to same conversation thread |
| `ag:prompted_by`         | agent_record      | prompt            | Record was prompted by a specific prompt       |
| `ag:spawns_plan`         | harvest_candidate | plan              | Candidate spawned a conduit plan               |
| `ag:evidences_candidate` | agent_record      | harvest_candidate | Record provides evidence for a candidate       |

### Knowledge Domain

| Type                     | Source           | Target           | Description                          |
| ------------------------ | ---------------- | ---------------- | ------------------------------------ |
| `kv:sourced_from`        | knowledge_entity | harvest          | Entity was sourced from a harvest    |
| `kv:informs`             | harvest          | knowledge_entity | Harvest informs entity definition    |
| `kv:cross_schema`        | embedding        | embedding        | Cross-schema embedding similarity    |
| `kv:name_overlap`        | knowledge_entity | knowledge_entity | Name overlap between entities        |
| `kv:description_overlap` | knowledge_entity | knowledge_entity | Description overlap between entities |

### Requirement Domain

| Type             | Source      | Target      | Description                    |
| ---------------- | ----------- | ----------- | ------------------------------ |
| `req:blocks`     | requirement | requirement | Requirement blocks another     |
| `req:depends_on` | requirement | requirement | Requirement depends on another |

Additional valid type: `compiles_to` (requirement → plan, created by the requirement compiler).

---

## Status Normalization Map

The following status string variants are normalized to one of the eight canonical values:

| Input Variant                                                    | Normalized To |
| ---------------------------------------------------------------- | ------------- |
| `backlog`, `new`                                                 | `Backlog`     |
| `todo`, `to-do`, `to do`                                         | `ToDo`        |
| `inprogress`, `in progress`, `in-progress`, `in_progress`, `wip` | `InProgress`  |
| `active`                                                         | `Active`      |
| `blocked`                                                        | `Blocked`     |
| `done`, `complete`, `completed`, `resolved`                      | `Done`        |
| `cancelled`, `cancel`, `canceled`                                | `Cancelled`   |
| `accepted`, `accept`                                             | `Accepted`    |

**Canonical Eight:** `Backlog`, `ToDo`, `InProgress`, `Active`, `Blocked`, `Done`, `Cancelled`, `Accepted`

---

## Error Response Format

All errors return a JSON object with a single `error` key:

```json
{ "error": "Human-readable error message" }
```

### HTTP Status Codes

- `200` — Success
- `201` — Created
- `400` — Bad request (missing/invalid fields)
- `403` — Forbidden (path traversal detected)
- `404` — Resource not found
- `409` — Conflict (concurrent modification detected)
- `500` — Internal server error
