# Conduit MCP

MCP server and SSE event bus for the pipeline system. Runs on port 3100
and provides the API surface for `conduit-ui` and `conduit`.

**Receipt-first authority:** Every plan operation goes through an MCP tool that
issues a receipt. Writing `.md` files directly to `nexus/graph/IMPLEMENTATION_PLANS/` is
an anti-pattern — the plan will have no `derived_status` and will be invisible.

The MCP server is the sole schema authority for the shared SQLite database.
It owns migrations for the `plans`, `receipts`, `sessions`, `tickets`,
`circuit_breaker`, and AI config tables. The Python conduit validates required
columns on startup and fails fast if the MCP server hasn't run migrations.

## Quick Start

```bash
# 1. Copy and edit the environment file
cp .env.example .env

# 2. Install dependencies
npm install

# 3. Start the server
npx tsx src/index.ts
```

## Creating Plans

Always use MCP tools, never write files directly:

```bash
# Capture an idea (goes to proposed/, issues PROPOSED receipt)
curl -X POST http://localhost:3100/tools/call \
  -H 'Content-Type: application/json' \
  -d '{"name":"create_proposed_plan","arguments":{"title":"My feature"}}'

# Create directly into implementation (goes to pending/, issues PLAN_CREATE)
curl -X POST http://localhost:3100/tools/call \
  -H 'Content-Type: application/json' \
  -d '{"name":"create_plan","arguments":{"title":"My feature"}}'
```

## Environment

All paths are read from `.env` (or environment variables). See `.env.example`
for the complete list.

| Variable       | Default              | Purpose                           |
|----------------|----------------------|-----------------------------------|
| `PIPELINE_DIR` | `../../nexus/.conduit-data` | Root of the conduit data directory |
| `PORT`         | `3100`               | HTTP server port                  |

The `.env` loader lives in `src/env.ts` — a shared module. No `dotenv` dependency.

## Key Tools

| Tool | Receipt | Description |
|------|---------|-------------|
| `create_proposed_plan` | `PROPOSED` | Capture an idea |
| `create_plan` | `PLAN_CREATE` | Create directly into pending |
| `promote_plan` | `PLANNING` | Promote proposed → planning |
| `revise_plan` | `PLANNING` | Copy completed/blocked for revision |
| `update_plan` | — | Edit plan metadata (title, goal, files, criteria, deps) |
| `delete_plan` | — | Soft-delete (marks deleted=1 in DB) |
| `issue_receipt` | Any | Manually record a pipeline event |
| `get_plan_receipts` | — | View receipt chain |
| `query_pipeline_state` | — | Full state JSON |
| `query_inspections` | — | Search/filter inspection reports |
| `query_prompts` | — | Search captured prompts with lineage |
| `query_changes` | — | Search change reports |
| `query_analytics` | — | Pipeline metrics |
| `save_prompt` | — | Persist a prompt to the audit trail |
| `agent_heartbeat` | — | Agent liveness ping |
| `agent_finished` | — | Agent completion signal |
| `seed_ai_config` | — | Seed default AI provider/harness/model/config |

## Endpoints

| Endpoint       | Method | Description                          |
|---------------|--------|--------------------------------------|
| `/state`      | GET    | Full PipelineState JSON              |
| `/events`     | GET    | Server-Sent Events stream            |
| `/tools/call` | POST   | Invoke an MCP tool (JSON-RPC style)  |
| `/tools`      | GET    | List available MCP tools             |
| `/health`     | GET    | Health check + orphan scan           |
| `/sessions`   | GET    | Session history                      |
| `/plans/sync` | POST   | Sync plan files from filesystem to DB |

## Project Structure

```
src/
├── index.ts          # Express server entry point (port 3100)
├── env.ts            # Shared .env loader (no dotenv dependency)
├── tools.ts          # MCP tool definitions & handlers
├── watcher.ts        # PipelineWatcher — coordinates sub-watchers, DB-authoritative plan creation
├── db.ts             # SQLite schema (migrations v068–v090), plan_status view, full CRUD
├── receipts.ts       # Receipt validation (state machine rules)
├── validate.ts       # Generic argument validation
├── errors.ts         # MCP error helpers
├── parser.ts         # .md plan file parser
├── types.ts          # Shared TypeScript types (PipelineState, PlanCard, etc.)
└── watchers/
    ├── plan-watcher.ts          # Watches nexus/graph/IMPLEMENTATION_PLANS/ dirs via chokidar
    ├── builder-watcher.ts       # Monitors builder output and result files
    ├── cb-watcher.ts            # Circuit breaker state monitoring & auto-reset
    ├── archive-watcher.ts       # Archives old plans to .bak/
    ├── agent-watcher.ts         # Tracks agent heartbeat and completion signals
    ├── inspection-watcher.ts    # Records inspection reports
    ├── prompt-watcher.ts        # Captures prompt audit trail
    ├── changes-watcher.ts       # Records change reports
    └── analytics-engine.ts      # Pipeline metrics (throughput, tokens, cycle times)
```

For the full architecture, see the [Conduit ARCHITECTURE.md](../nexus/legacy/python/conduit/ARCHITECTURE.md).
