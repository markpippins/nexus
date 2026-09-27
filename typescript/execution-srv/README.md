# execution-srv

Express observability API server over the PostgreSQL `execution` schema — read-only views for requests, leases, attempts, receipts, and cross-table integrity scans.

- **Port:** 3110
- **Schema:** `execution` (tables: `requests`, `leases`, `attempts`, `receipts`)
- **Cross-schema reads:** `vision.receipts` (for the `pipeline-origin` lineage endpoint)
- **Pattern:** Express + `pg.Pool` + TypeScript + tsx watcher (modeled on `vision-srv`)
- **Source spec:** [`REST API.md`](./REST%20API.md)

## Why this service exists

The `execution` schema holds 323 requests / 321 leases / 321 attempts / 1558 receipts as of 2026-07-21. There is no live API surface over it — answering "where does request X stand right now?" or "what's executor Y holding?" currently requires hand-written joins nobody's written yet. This service exposes those joins as named endpoints, alongside an integrity scanner that generalizes `vision.check_receipt_integrity()` for the execution schema.

This service is **strictly read-only**. All mutation goes through `conduit-mcp` (port 3100), which owns the write path. There are no `POST`, `PUT`, `PATCH`, or `DELETE` routes mounted.

## Endpoints

All routes are prefixed with `/api/execution`.

### 1. Lifecycle state — the natural aggregate root

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/execution/requests/{id}/state` | The request, its current lease (if any), its latest attempt, and all of its receipts — a single "where does this stand right now" view. |

### 2. Lease integrity — the expiry gap, made visible

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/execution/leases/stale` | All `ACTIVE` leases with `expires_at < now()` — the enforcement gap made queryable. |
| `GET` | `/api/execution/leases/{id}/lifecycle` | `acquired_at → expires_at → released_at`, with actual vs promised TTL and `lifecycle_state` (`live`, `released`, `stale_active`, `expired_unreleased`). |

### 3. Cross-table consistency scan — a growing list of named pathologies

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/execution/health/integrity-scan` | Scans for specific pathologies and returns `{ kind, count, samples[] }` for each. Not a "health score" — a named, growing list of specific gaps the day we find them. |

Initial kinds (extend this list when new pathologies are discovered):

- `orphan_lease_request_mismatch` — `lease.request_id` has no matching `execution.requests` row
- `stale_active_lease` — `lease.status=ACTIVE AND expires_at < now()`
- `attempt_orphan_no_lease` — `attempt.lease_id` has no matching `execution.leases` row
- `attempt_status_diverges_from_request` — `request.status=COMPLETED` but `attempt.status in (CREATED,RUNNING)`; or `request.status=READY` and an attempt is `SUCCEEDED` without an `EXECUTION_COMPLETE` receipt
- `receipt_request_mismatch` — `receipt.request_id` has no matching `execution.requests` row
- `receipt_attempt_mismatch` — `receipt.attempt_id` has no matching `execution.attempts` row
- `unreleased_lease_for_terminal_request` — `request.status in (COMPLETED,CANCELLED,FAILED)` but `lease.status=ACTIVE`
- `attempted_no_completion` — `request.status not terminal` AND all attempts are `CREATED` (no `SUCCEEDED/FAILED/TIMED_OUT`)

When a new pathology is found in production, add it as another scan block in `src/routes.ts` in the `GET /health/integrity-scan` handler.

### 4. Attempt/lease/request tree

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/execution/requests/{id}/attempts` | Every attempt for the request, each with its parent lease joined in, chronological. |
| `GET` | `/api/execution/requests/{id}/receipts/lineage` | All receipts for the request split into `native` / `backfilled` (from `vision.receipts`) / `unknown` lineage buckets. |

### 5. Fleet view

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/execution/health/by-executor` | Without `?executor_id=`: fleet-wide summary per executor. With `?executor_id=foo`: that executor's active leases, in-progress attempts, and summary counters. |
| `GET` | `/api/execution/health/status-distribution` | Count of requests/leases/attempts/receipts per status — the drift-over-time signal for snapshots. |

### Update endpoint — pipeline origin (lineage-honest)

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/execution/receipts/{id}/pipeline-origin` | Follows `lineage_original_id → vision.receipts.id` and returns **both** records side by side, explicitly labeled by which audit trail each came from (`execution.receipts` vs `vision.receipts`). Doesn't pretend there's one canonical receipt — it shows the seam. |

The `relationship` field is:
- `native_execution_only` — no `lineage_source` (this receipt was born in `execution.receipts`)
- `backfilled_from_vision` — `lineage_source = 'vision.receipts'` and a `vision.receipts` row was found
- `unknown_source:<value>` — any other `lineage_source` value (future migration sources)

### Helpers

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Root-level readiness check with live counts per table. |
| `GET` | `/api/execution/health` | Inline health summary — counts and the three key operational signals: `ready_requests`, `stale_active_leases`, `running_attempts`. |

## Running

```bash
# dev (watcher)
npm run dev

# build + start
npm run build
npm start
```

The systemd unit (`execution-srv.service`) is registered in `nexus/bin/start-nexus-services.sh` and will be picked up by `bin/start-nexus-services.sh start`.

## Configuration

Environment variables (all optional — defaults in `src/index.ts`):

| Var | Default | Notes |
|---|---|---|
| `PORT` | `3110` | HTTP listen port |
| `PGHOST` | `localhost` | PostgreSQL host |
| `PGPORT` | `5432` | PostgreSQL port |
| `PGUSER` | `pguser` | PostgreSQL user |
| `PGPASSWORD` | `pgpass` | PostgreSQL password |
| `PGDATABASE` | `nexus` | Database name |

The pool pins `search_path=execution` as the default namespace. Cross-schema reads (the `pipeline-origin` endpoint) qualify their target explicitly (`vision.receipts`).

## Rate limiting

An app-level limiter caps requests at **300 req/min/IP**, and a second
router-level limiter sits under `/api/execution` so the surface stays covered
wherever the router is mounted. Both advertise draft-7 `RateLimit` /
`RateLimit-Policy` headers; exceeding the ceiling returns `429` with
`{ "error": "execution-srv rate limit exceeded" }`.

This is a CodeQL `js/missing-rate-limiting` remediation. The ceiling sits well
above normal operator/agent polling — this service is read-only observability —
so it only bites on resource-exhaustion floods.

Unknown paths return a JSON `404` envelope (`{ "error": "not_found", "hint": ... }`)
rather than Express's default HTML page.

## Tests

```bash
npm test        # vitest — rate limiter + 404 envelope
npm run typecheck
```

The Express app lives in `src/app.ts` and the process lifecycle (listen,
heartbeat, signal handlers) in `src/index.ts`, so tests import the real app
without binding a port. `src/routes.test.ts` and `src/metrics.test.ts` are
standalone `tsx` conformance scripts, not vitest suites.

## Related

- [`conduit-mcp`](../conduit-mcp/) — owns the write path and pipeline lifecycle for `execution.*`
- [`025-execution-schema.sql`](../conduit-mcp/migrations/025-execution-schema.sql) — schema definition
- [`vision-srv`](../vision-srv/) — sibling service (template) over the `vision` schema
- [`REST API.md`](./REST%20API.md) — original spec, by-chat transcript action


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
