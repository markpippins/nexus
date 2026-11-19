# Conduit MCP — Specification

## Functional Requirements

- Provide the MCP API surface for the pipeline system (port 3100)
- Issue receipts for every plan operation (receipt-first authority)
- Serve real-time pipeline state via Server-Sent Events
- Manage plan lifecycle: proposed, planning, pending, active, completed, blocked
- Own SQLite schema migrations for plans, receipts, sessions, tickets, and AI config
- Monitor circuit breaker state for downstream API health
- Provide health checks, orphan scans, and session history

## Non-Functional Requirements

- Receipt-first: writing plan files without receipts produces invisible orphan plans
- DB-primary resilience: if nexus/graph/IMPLEMENTATION_PLANS/ directory is absent, run in DB-only mode
- SSE-based live updates for connected clients
- All path configuration via .env (no hardcoded paths, no dotenv dependency)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /state | Full PipelineState JSON |
| GET | /events | Server-Sent Events stream |
| POST | /tools/call | Invoke an MCP tool (JSON-RPC) |
| GET | /tools | List available MCP tools |
| GET | /health | Health check with orphan scan |
| GET | /sessions | Session history |

## Data Model

- Plan: id (UUID), title (String), project (String), goal (String), filesAffected (String[]), acceptanceCriteria (String[]), status (String), deleted (Boolean)
- Receipt: id (UUID), planId (UUID), type (String), agentRole (String), sessionId (UUID), ticketId (UUID), createdAt (Instant)
- Session: id (UUID), planId (UUID), role (String), status (String), totalWorkSeconds (Long), costUsd (Float), startTime (Instant), endTime (Instant)
- CircuitBreaker: tripped (Boolean), retryAfter (Instant), paused (Boolean)
