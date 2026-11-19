# Conduit MCP Code Assessment

Date: 2026-06-09

## Scope

This assessment covers the TypeScript MCP/SSE service in `nexus/typescript/conduit-mcp/`.
The review focused on:

- SQLite schema and receipt-derived state
- MCP tool handlers
- watcher reconciliation
- prompt and plan creation paths
- advertised build and test commands

## Executive Summary

`conduit-mcp` is the central authority for pipeline state. The service has a
useful separation between durable receipt-derived status and filesystem content
watching, and the public API is straightforward.

The main risks are around authority boundaries. Some tools write receipts with
synthetic ticket IDs that do not correspond to actual tickets, plan numbering is
computed from only part of the state, and metadata updates can become no-ops in
DB-primary mode. These issues can weaken auditability even when the service
continues to compile and pass current tests.

## Confirmed Strengths

- `plan_status` derives visible pipeline state from receipts rather than file
  location.
- `delete_plan` soft-deletes database state and attempts to cancel non-terminal
  tickets.
- The health endpoint includes an orphan scan for deleted DB rows with stale
  files and files without DB rows.
- Watcher events are increasingly treated as content synchronization instead
  of lifecycle authority.
- Prompt capture exists as a first-class MCP tool.
- TypeScript compile and Vitest pass.

## Findings

### 1. Tools insert receipts with synthetic ticket IDs

Severity: High

Several tool handlers insert receipts with a constructed `ticket-*` ID but do
not ensure that a matching ticket row exists:

- `tools.ts:481-495` in `create_plan`
- `tools.ts:581-593` in `issue_receipt`
- Similar patterns exist in `create_proposed_plan`, `revise_plan`, and
  `promote_plan`.

Impact:

- The receipt row can claim linkage to a ticket that was never issued as an
  authorization object.
- With foreign keys enabled, insertion can fail at runtime if the synthetic
  ticket row does not exist.
- On older or partially migrated schemas, the same pattern can silently degrade
  the ticket-first invariant instead of failing fast.
- Audit tooling that follows ticket lineage can show incomplete or false
  lineage.

Recommendation:

- Require a real ticket for receipt types that represent executable work.
- For system-created receipts such as `PROPOSED` and `PLAN_CREATE`, either
  create a real system ticket row first or explicitly allow `ticket_id = NULL`
  and document that these are non-ticket receipts.
- Add tests that assert receipt insertion fails or creates a ticket when a
  non-null ticket ID is supplied.

### 2. Plan number allocation ignores proposed and planning plans

Severity: High

`PipelineWatcher.createPlan()` computes `maxNum` from pending, active,
completed, blocked, and archived plans only:

- `watcher.ts:217-227`

It omits `proposed` and `planning`.

Impact:

- After a restart or when all newest plans are in proposed/planning, creating a
  new plan can reuse an existing plan number.
- This can collide with database primary keys, overwrite mental model in the UI,
  or produce files whose plan numbers disagree with persisted rows.

Recommendation:

- Allocate plan numbers from the database, not from watcher arrays.
- At minimum include every in-memory column: proposed, planning, pending,
  active, completed, blocked, and archived.
- Prefer a transaction-backed sequence table or `MAX(CAST(id AS INTEGER))`
  inside SQLite.

### 3. `update_plan` can silently fail in DB-primary mode

Severity: Medium

The `update_plan` tool delegates to `watcher.updatePlanMetadata()`:

- `tools.ts:520-551`
- `watcher.ts:254-285`

`updatePlanMetadata()` scans only filesystem directories:

- pending
- active
- completed
- blocked

It does not scan proposed/planning and does not update the SQLite row directly.

Impact:

- In DB-primary mode, or for proposed/planning plans, `update_plan` can return
  `updated: false` without changing durable metadata.
- Planning elucidation depends on reliable metadata updates for files affected,
  acceptance criteria, and dependencies.

Recommendation:

- Make `update_plan` update the database row first.
- Treat filesystem updates as an optional mirror.
- Include proposed and planning in the filesystem mirror scan.
- Emit a clear error for missing plans instead of a quiet `updated: false` when
  the caller expected mutation.

### 4. Schema migration order is noisy and fragile

Severity: Medium

`createSchema()` attempts to alter `sessions`, `circuit_breaker`, and `tickets`
before all of those tables are guaranteed to exist:

- `db.ts:62-78` alters `sessions` and `circuit_breaker`.
- `db.ts:206-243` alters `tickets`.
- `db.ts:346-378` creates `tickets` later.

Impact:

- Fresh database initialization logs migration errors that are expected rather
  than exceptional.
- Future migrations can accidentally depend on columns that were not present
  when earlier statements ran.
- This makes real migration failures harder to distinguish from harmless first
  startup noise.

Recommendation:

- Create baseline tables first.
- Run migrations after all target tables exist.
- Move each migration into a named idempotent function with a test for fresh DB
  and upgrade DB paths.

### 5. Receipt state machine and manager behavior are not aligned

Severity: Medium

`receipts.ts` allows `IMPLEMENTATION -> REVIEW_PASS | REVIEW_REJECT | REVIEW`.
`conduit/main.py` can emit `REVIEW_PASS` immediately after builder
success. That makes MCP state terminal even though MCP's transition model
expects review to follow implementation.

Impact:

- The MCP server can accurately enforce its local state machine but still
  accept semantically wrong receipts from the manager.
- The system needs cross-project contract tests, not only unit tests.

Recommendation:

- Add an end-to-end fixture that runs manager-style receipt sequences through
  MCP validation and asserts the expected plan column after each receipt.
- Consider requiring role/type compatibility in `issue_receipt`, for example
  only `agent_role = reviewer` may issue `REVIEW_PASS`.

### 6. Current tests cover only a narrow slice

Severity: Medium

The current Vitest suite focuses on `createNextTickets()` terminal guards and
`cancelTicketsByPlan()`. It does not cover:

- `create_plan`
- `create_proposed_plan`
- `promote_plan`
- `update_plan`
- `issue_receipt`
- prompt capture sequencing
- plan number allocation
- fresh DB migration logs/shape

Recommendation:

- Add tool-handler tests with a temporary `.pipeline` base directory.
- Add a fresh-DB schema test that asserts required columns exist without
  logging expected errors.
- Add receipt role/type compatibility tests.

## Verification Run

Commands run from `/home/codex/dev`:

```bash
cd nexus/typescript/conduit-mcp && npx tsc --noEmit
cd nexus/typescript/conduit-mcp && npx vitest run
```

Results:

- TypeScript compile passed.
- Vitest passed: 1 test file, 23 tests.

## Post-Assessment Updates (2026-06-10)

### Resolved / Addressed

1. **total_work_seconds migration (v090):** Added to sessions table for cumulative
   work-time tracking. Used by the Python conduit watchdog.

2. **AI configuration registry (v083–v085):** Full provider → harness → model →
   role-config hierarchy implemented with per-role assignment. Migration fixed
   provider_id from ai_harnesses to ai_models. Default seed data available via
   `seed_ai_config` tool.

3. **tickets_old FK fix (v084):** Resolved the lingering `tickets_old` reference
   from the v079 CHECK expansion migration. All receipts now point to `tickets`.

### Still Outstanding

1. Plan number allocation still computed from watcher arrays, not from SQLite.
2. Synthetic ticket IDs still used by tools (`create_plan`, `issue_receipt`, etc.)
   without guaranteeing matching ticket rows exist.
3. `update_plan` still filesystem-primary for proposed/planning plans.
4. Schema migration order still noisy on fresh databases.
5. No cross-project lifecycle contract tests with `pipeline-manager`.
