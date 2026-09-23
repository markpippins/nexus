# execution-srv — Execution Observability API

> Port: **3110**  
> REST reference: `API.md` · OpenAPI spec: [`openapi.yaml`](./openapi.yaml)

Read-only API over the execution schema: requests, leases, attempts, receipts, integrity scans, and cross-schema lineage.

**19 endpoints** — inventory generated from source route registrations (`nexus/tools/api-docs/`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/execution/attempts` |  |
| GET | `/api/execution/health` | Health inline — GET /api/execution/health Lightweight entry point distinct from /health (the root-level check). Returns just the live counts + the three key operational signals. |
| GET | `/api/execution/health/by-executor` | 5. FLEET VIEW GET /api/execution/health/by-executor?executor_id= What is this executor currently holding / running. Returns active leases + their in-progress attempts + a summary counter for the executor. |
| GET | `/api/execution/health/integrity-scan` | GET /api/execution/health/integrity-scan Same shape as Vision's check_receipt_integrity() — a named, growing list of specific pathologies, each one a query you write the day you find the gap. Not a generic "health score." |
| GET | `/api/execution/health/status-distribution` | GET /api/execution/health/status-distribution Count of requests per status, leases per status, attempts per status — the drift-over-time signal. One call returns everything for snapshotting. |
| GET | `/api/execution/leases` |  |
| GET | `/api/execution/leases/:id/lifecycle` | GET /api/execution/leases/{id}/lifecycle acquired_at → expires_at → released_at, actual vs promised. Computes how long the lease was actually held (or how long it's been held so far if still ACTIVE), and how that compares to the promised TTL. |
| GET | `/api/execution/leases/stale` | 2. LEASE INTEGRITY — the expiry gap, made visible GET /api/execution/leases/stale Active leases whose expires_at < now() — the enforcement gap made queryable. Returns each stale lease joined to its request so callers see the executor that is holding dead ground. |
| GET | `/api/execution/metrics` | GET /api/execution/metrics JSON snapshot of governance metric families. Values are derived from live classification/query outcomes and correlate to envelope and receipt identities — never payloads. |
| GET | `/api/execution/projections/witnessed-runs` | W3.08 — versioned governed projection for downstream consumers. |
| GET | `/api/execution/receipts` |  |
| GET | `/api/execution/receipts/:id/pipeline-origin` | Follows lineage_original_id → vision.receipts.id and returns both records side by side, explicitly labeled by which audit trail each came from. Doesn't pretend there's one canonical receipt — it shows the seam. |
| GET | `/api/execution/requests` | GET /api/execution/receipts ?type=&search=&limit=20&offset=0 Each returns { total, limit, offset, items: [...] } with DB-native column shapes. See DRIFT.md in execution-ui for field-name differences from the UI's expected TypeScript types. |
| GET | `/api/execution/requests/:id/attempts` | 4. ATTEMPT/LEASE/REQUEST TREE GET /api/execution/requests/{id}/attempts Every attempt for this request, each attempt's lease, chronological. |
| GET | `/api/execution/requests/:id/receipts/lineage` | GET /api/execution/requests/{id}/receipts/lineage Split by lineage_source: native vs backfilled vs unknown. |
| GET | `/api/execution/requests/:id/state` | GET /api/execution/requests/{id}/state Returns the request, its current lease (if any), its latest attempt, and all of its receipts — the "where does this stand right now" view that currently requires four joins nobody's written yet. |
| GET | `/api/execution/witnessed-runs` | This endpoint deliberately exposes nullable lineage fields. The execution schema currently owns request/attempt/receipt identity; envelope, manifest, SOL, evidence, and replay identities are returned only when persisted in existing JSON metadata. No browser-side join or authority decision is perform |
| GET | `/api/execution/witnessed-runs/diagnostics` | Server-derived health summary for one witnessed run: authoritative status, enumerated missing lineage elements, receipt correlation validity, replay state. Correlates to immutable envelope and receipt identities — never reconstructs authority browser-side (AC4). |
| GET | `/health` | Health Check Two-level health: process-up + DB-reachable. The integrity-scan endpoint (/api/execution/health/integrity-scan) is the deeper check. |

## Regeneration

```bash
cd nexus && python3 tools/api-docs/extract_routes.py --out /tmp/api_inventory.json
python3 tools/api-docs/gen_openapi.py --inventory /tmp/api_inventory.json   # (vision-srv also refreshes from the live FastAPI spec)
```

<!-- API-SPEC-BEGIN -->












---

# execution-srv — REST & Envelope Spec

> **Hand-authored section — preserved across regeneration.** Base URL:
> `http://localhost:3110`. JSON in/out. **Read-only** observability over the
> `execution` PostgreSQL schema: `requests`, `leases`, `attempts`, `receipts`.
> All list endpoints return `{ total, limit, offset, items }` with DB-native
> column shapes.

## List envelopes (paginated)

`GET /api/execution/requests` · `GET /api/execution/leases` ·
`GET /api/execution/attempts` · `GET /api/execution/receipts`

Query params: `status` (or `type` for receipts) — exact filter; `search` —
ILIKE over id/text/business fields; `limit` (default 20, clamp 1–100);
`offset` (default 0).

Response — **200**:

```json
{ "total": 123, "limit": 20, "offset": 0, "items": [ { "…full row…" } ] }
```

| Endpoint | Filter param | Search columns | Sort |
|----------|-------------|----------------|------|
| `/requests` | `status` | `business_key`, `title`, `objective`, `id::text`, `inputs::text` | `created_at DESC` |
| `/leases` | `status` | `executor_id`, `request_id::text`, `id::text` | `created_at DESC` |
| `/attempts` | `status` | `executor_id`, `error`, `request_id::text`, `lease_id::text`, `id::text` | `created_at DESC` |
| `/receipts` | `type` | `agent_role`, `summary`, `request_id::text`, `attempt_id::text`, `id::text` | `issued_at DESC` |

## Lifecycle state envelope

`GET /api/execution/requests/:id/state` — "where does this request stand now"
(request + current lease + latest attempt + all receipts). **200**:

```json
{
  "request": { "…requests row…" },
  "current_lease": { "…leases row…" } | null,
  "latest_attempt": { "…attempts row…" } | null,
  "receipts": [ { "…receipts row…" } ],
  "receipt_count": 3
}
```

**400** non-UUID id · **404** `{ error: "request not found" }`.

## Lease integrity envelopes

`GET /api/execution/leases/stale` — active leases past `expires_at`, joined to
their request. **200**:

```json
{ "count": 2, "stale_leases": [ { "lease_id", "request_id", "executor_id", "ttl_seconds", "acquired_at", "expires_at", "created_at", "business_key", "title", "request_status", "overdue_seconds": 12 } ] }
```

`GET /api/execution/leases/:id/lifecycle` — actual vs promised lease lifetime.
**200** single lease row plus computed fields:

```json
{ "…lease columns…", "promised_ttl_seconds": 300, "actual_held_seconds": 120, "overdue_seconds": 0, "lifecycle_state": "live|released|expired_unreleased|stale_active" }
```

**400** non-UUID · **404** `{ error: "lease not found" }`.

## Integrity scan envelope

`GET /api/execution/health/integrity-scan` — named cross-table pathologies.
**200**:

```json
{
  "scanned_at": "<ISO>", "schema": "execution",
  "totals": { "anomalies": 0, "kinds_fired": 0 },
  "scans": [ { "kind": "stale_active_lease", "count": 0, "samples": [ { "entity_id", "request_id", "detail" } ] } ]
}
```

Kinds: `orphan_lease_request_mismatch`, `stale_active_lease`,
`attempt_orphan_no_lease`, `attempt_status_diverges_from_request`,
`receipt_request_mismatch`, `receipt_attempt_mismatch`,
`unreleased_lease_for_terminal_request`, `attempted_no_completion`.

## Request tree envelopes

`GET /api/execution/requests/:id/attempts` — every attempt + its embedded lease
(`row_to_json`). **200**:
`{ "request": {id, business_key, title, status}, "attempt_count": N, "attempts": [ { "…attempt…", "lease": {…} } ] }`.

`GET /api/execution/requests/:id/receipts/lineage` — receipts split by
provenance. **200**:

```json
{ "request": {…}, "receipt_count": 3, "native_count": 2, "backfilled_count": 1, "unknown_count": 0,
  "lineage_buckets": { "native": [], "backfilled": [], "unknown": [] } }
```

## Fleet view envelopes

`GET /api/execution/health/by-executor?executor_id=` — per-executor (or whole
fleet when the param is omitted). **200**:

```json
{ "scope": "fleet", "executor_count": 3, "executors": [ { "executor_id", "active_leases", "released_leases", "expired_leases", "total_leases" } ] }
```

With `executor_id`:
`{ "scope": "executor", "executor_id", "summary": {active/released/expired/total/requests_held}, "active_leases": [], "in_progress_attempts": [] }`.

`GET /api/execution/health/status-distribution` — snapshot counts. **200**:
`{ "scanned_at", "requests": [{status,count}], "leases": [], "attempts": [], "receipts_by_type": [], "stale_active_leases": N }`.

## Pipeline-origin envelope

`GET /api/execution/receipts/:id/pipeline-origin` — the receipt and its
cross-schema original (if backfilled from `vision.receipts`). **200**:

```json
{
  "local_execution_record": { "audit_trail": "execution.receipts", "record": {…} },
  "remote_vision_record": { "audit_trail": "vision.receipts", "record": {…} } | null,
  "relationship": "backfilled_from_vision|native_execution_only|unknown_source:<src>"
}
```

## Health

`GET /api/execution/health` — **200**: `{ "scanned_at", "requests", "ready_requests", "completed_requests", "leases", "stale_active_leases", "attempts", "running_attempts", "receipts" }`.

`GET /health` — process/DB health (root-level, two-level check).

## Error envelope

All errors: `{ "error": "<message>" }` — **400** (bad UUID/param),
**404** (missing row), **500** (SQL failure; message retained for debugging).
