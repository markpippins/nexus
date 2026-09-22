import "@typespec/openapi3";
import "@typespec/http";

using TypeSpec.Http;

/**
 * resolution-srv contract — REST over the canonical resolution.* schema.
 *
 * v1 is READ-ONLY by design: writes to resolution.* go through
 * producer-authorized paths (SOLScript evaluation, receipts, admission)
 * with their own idempotency contracts. Any non-GET method under /api
 * returns 405 `read_only`. Per-table write exposure requires an
 * authorization decision (To Do 6b3ca700).
 *
 * The table surface is registry-driven: every table registered in
 * src/tables.ts gets GET /api/{table} and GET /api/{table}/{id}. This
 * contract models the envelope shapes and the fixed routes; the concrete
 * table list is data, not contract.
 */

@service(#{ title: "Nexus Resolution SRV" })
@server("http://localhost:3171")
namespace org.nexus.resolutionsrv;

// ── Health ─────────────────────────────────────────────────────────────

model HealthResponse {
  service: "resolution-srv";
  /** "ok" when DB reachable and all required tables present; "degraded" otherwise. */
  status: "ok" | "degraded" | "error";
  database: "connected" | "unreachable";
  /** Tables from the health-check set that failed the presence probe. */
  missingTables?: string[];
  time?: utcDateTime;
  message?: string;
}

// ── Meta ───────────────────────────────────────────────────────────────

union TableGroup {
  registry: "registry",
  entity_proposition: "entity_proposition",
  reasoning: "reasoning",
  outcome: "outcome",
  lineage: "lineage",
}

model TableInfo {
  table: string;
  label: string;
  group: TableGroup;
  /** Active (non-expired) row count where the table has an expiry column. */
  active?: int32;
  total?: int32;
  /** Present when the per-table count query failed (table missing/migrating). */
  error?: string;
}

model MetaResponse {
  service: "resolution-srv";
  schema: "resolution";
  readOnly: true;
  tables: TableInfo[];
}

// ── Generic table surface ──────────────────────────────────────────────

/**
 * The item payload is the raw row (column names as stored in resolution.*).
 * Modeled as an open record because the registry drives the concrete set.
 */
model Row {
  ...Record<unknown>;
}

model TableItems {
  table: string;
  count: int32;
  limit: int32;
  offset: int32;
  items: Row[];
}

model ErrorResponse {
  error: string;
  message?: string;
  table?: string;
  id?: string;
  method?: string;
}

// ── Operations ─────────────────────────────────────────────────────────

@route("/health")
@get op health(): HealthResponse;

@route("/api/meta")
@get op meta(): MetaResponse;

/**
 * List active rows of a registry table. `table` must be a registry table;
 * unknown tables return 404 unknown_table.
 */
@route("/api/{table}")
@get op listRows(table: string, @query limit?: int32, @query offset?: int32): TableItems | ErrorResponse;

/**
 * Fetch one row by primary key (uuid as text).
 */
@route("/api/{table}/{id}")
@get op getRow(table: string, id: string): Row | ErrorResponse;

/**
 * Non-GET methods under /api are refused with 405 `read_only` — the write
 * policy is a contract property of this service, not an omission.
 */
model ReadOnlyPolicy {
  /** Always "read_only" — v1 exposes no mutation surface. */
  error: "read_only";
  message: string;
  table: string;
  method: string;
}
