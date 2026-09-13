import "dotenv/config";
import { Errors, Service, ServiceBroker, Context } from "moleculer";
import { Pool } from "pg";

/**
 * worker.execution — observability worker (Wave 4.3).
 *
 * Ports execution-srv's read-only surface (/api/execution/*) into broker
 * actions. All queries target the `execution` schema (unqualified table
 * names resolve via search_path=execution, matching the original pool).
 */

// moleculer-web delivers query params as strings, but the legacy routes
// parseInt() whatever arrives — so limit/offset are declared `any` here and
// normalized by clamp() (which handles "3" and 3 alike). Strict "number"
// params would 422 on the exact requests the legacy surface accepted.
interface ListParams {
  status?: string;
  type?: string;
  search?: string;
  limit?: any;
  offset?: any;
}

function clamp(v: unknown, min: number, max: number, dflt: number): number {
  const n = parseInt(String(v ?? ""), 10);
  if (Number.isNaN(n)) return dflt;
  return Math.min(Math.max(n, min), max);
}

// UUID guard matching the legacy route's requireUuid(): the pg UUID type
// rejects malformed input with a 500 and a stack trace in the logs; the
// legacy surface answers a clean 400 instead. Moleculer maps the thrown
// MoleculerError(400) to the same status the legacy service produced.
const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
function requireUuid(id: string, label = "id"): string {
  if (typeof id !== "string" || !UUID_RE.test(id)) {
    throw new Errors.MoleculerError(`${label} must be a UUID`, 400, "BAD_REQUEST");
  }
  return id;
}

export default class ExecutionWorker extends Service {
  private pool: Pool | null = null;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "worker.execution",

      actions: {
        listRequests: {
          params: {
            status: { type: "string", optional: true },
            search: { type: "string", optional: true },
            limit: { type: "any", optional: true },
            offset: { type: "any", optional: true },
          },
          handler: (ctx: Context<ListParams>) => this.paginated(ctx, "requests", "status", ["business_key", "title", "objective", "id::text", "inputs::text"], "created_at DESC"),
        },

        listLeases: {
          params: {
            status: { type: "string", optional: true },
            search: { type: "string", optional: true },
            limit: { type: "any", optional: true },
            offset: { type: "any", optional: true },
          },
          handler: (ctx: Context<ListParams>) => this.paginated(ctx, "leases", "status", ["executor_id", "request_id::text", "id::text"], "created_at DESC"),
        },

        listAttempts: {
          params: {
            status: { type: "string", optional: true },
            search: { type: "string", optional: true },
            limit: { type: "any", optional: true },
            offset: { type: "any", optional: true },
          },
          handler: (ctx: Context<ListParams>) => this.paginated(ctx, "attempts", "status", ["executor_id", "error", "request_id::text", "lease_id::text", "id::text"], "created_at DESC"),
        },

        listReceipts: {
          params: {
            type: { type: "string", optional: true },
            search: { type: "string", optional: true },
            limit: { type: "any", optional: true },
            offset: { type: "any", optional: true },
          },
          handler: (ctx: Context<ListParams>) => this.paginated(ctx, "receipts", "type", ["agent_role", "summary", "request_id::text", "attempt_id::text", "id::text"], "issued_at DESC"),
        },

        requestState: {
          params: { id: "string" },
          handler: (ctx: Context<{ id: string }>) => this.requestState(requireUuid(ctx.params.id)),
        },

        staleLeases: {
          handler: () => this.staleLeases(),
        },

        leaseLifecycle: {
          params: { id: "string" },
          handler: (ctx: Context<{ id: string }>) => this.leaseLifecycle(requireUuid(ctx.params.id)),
        },

        integrityScan: {
          handler: () => this.integrityScan(),
        },

        requestAttempts: {
          params: { id: "string" },
          handler: (ctx: Context<{ id: string }>) => this.requestAttempts(requireUuid(ctx.params.id)),
        },

        receiptsLineage: {
          params: { id: "string" },
          handler: (ctx: Context<{ id: string }>) => this.receiptsLineage(requireUuid(ctx.params.id)),
        },

        byExecutor: {
          params: {
            executor_id: { type: "string", optional: true },
          },
          handler: (ctx: Context<{ executor_id?: string }>) => this.byExecutor(ctx.params.executor_id),
        },

        statusDistribution: {
          handler: () => this.statusDistribution(),
        },

        pipelineOrigin: {
          params: { id: "string" },
          handler: (ctx: Context<{ id: string }>) => this.pipelineOrigin(requireUuid(ctx.params.id)),
        },

        healthSimple: {
          handler: () => this.health(),
        },

        richHealth: {
          handler: () => this.richHealth(),
        },
      },
    });
  }

  private async getPool(): Promise<Pool> {
    if (!this.pool) {
      this.pool = new Pool({
        host: process.env.PG_HOST || "localhost",
        port: Number(process.env.PG_PORT || 5432),
        user: process.env.PG_USER || "pguser",
        password: process.env.PG_PASSWORD || "pgpass",
        database: process.env.PG_DB_NAME || "nexus",
        options: "-c search_path=execution",
        max: 5,
        idleTimeoutMillis: 30000,
        connectionTimeoutMillis: 5000,
      });
    }
    return this.pool;
  }

  async stopped(): Promise<void> {
    if (this.pool) await this.pool.end();
  }

  private async paginated(ctx: Context<ListParams>, table: string, filterColumn: string, searchColumns: string[], orderBy: string): Promise<any> {
    const pool = await this.getPool();
    const filterVal = ctx.params.status || ctx.params.type || "";
    const search = ctx.params.search?.trim() || "";
    const limit = clamp(ctx.params.limit, 1, 100, 20);
    const offset = Math.max(0, ctx.params.offset || 0);

    const conditions: string[] = [];
    const values: any[] = [];
    let p = 1;

    if (filterVal) {
      conditions.push(`${filterColumn} = $${p++}`);
      values.push(filterVal);
    }
    if (search) {
      conditions.push(`(${searchColumns.map((c) => `${c} ILIKE $${p}`).join(" OR ")})`);
      values.push(`%${search}%`);
      p++;
    }

    const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";
    const sql = `
      SELECT *, COUNT(*) OVER() AS full_count
      FROM ${table}
      ${where}
      ORDER BY ${orderBy}
      LIMIT $${p++} OFFSET $${p}
    `;
    values.push(limit, offset);

    const { rows } = await pool.query(sql, values);
    const total = rows.length > 0 ? parseInt(rows[0].full_count, 10) : 0;
    const items = rows.map(({ full_count: _fc, ...rest }: any) => rest);
    return { total, limit, offset, items };
  }

  private async requestState(id: string): Promise<any> {
    const pool = await this.getPool();
    const requestQ = await pool.query("SELECT * FROM requests WHERE id = $1", [id]);
    if (requestQ.rowCount === 0) throw new Errors.MoleculerError("request not found", 404, "NOT_FOUND");
    const request = requestQ.rows[0];

    const leaseQ = await pool.query(
      `SELECT * FROM leases
       WHERE request_id = $1
       ORDER BY (status = 'ACTIVE') DESC, acquired_at DESC
       LIMIT 1`,
      [id],
    );
    const currentLease = leaseQ.rows[0] ?? null;

    const attemptQ = await pool.query(
      `SELECT * FROM attempts
       WHERE request_id = $1
       ORDER BY created_at DESC, started_at DESC NULLS LAST
       LIMIT 1`,
      [id],
    );
    const latestAttempt = attemptQ.rows[0] ?? null;

    const receiptsQ = await pool.query(
      `SELECT * FROM receipts WHERE request_id = $1 ORDER BY issued_at ASC`,
      [id],
    );

    return {
      request,
      current_lease: currentLease,
      latest_attempt: latestAttempt,
      receipts: receiptsQ.rows,
      receipt_count: receiptsQ.rowCount,
    };
  }

  private async staleLeases(): Promise<any> {
    const pool = await this.getPool();
    const { rows } = await pool.query(
      `SELECT
          l.id            AS lease_id,
          l.request_id,
          l.executor_id,
          l.ttl_seconds,
          l.acquired_at,
          l.expires_at,
          l.created_at,
          r.business_key,
          r.title,
          r.status        AS request_status,
          EXTRACT(EPOCH FROM (NOW() - l.expires_at))::int AS overdue_seconds
       FROM leases l
       JOIN requests r ON r.id = l.request_id
       WHERE l.status = 'ACTIVE'
         AND l.expires_at < NOW()
       ORDER BY l.expires_at ASC`,
    );
    return { count: rows.length, stale_leases: rows };
  }

  private async leaseLifecycle(id: string): Promise<any> {
    const pool = await this.getPool();
    const { rows } = await pool.query(
      `SELECT
          *,
          EXTRACT(EPOCH FROM (expires_at - acquired_at))::int                AS promised_ttl_seconds,
          EXTRACT(EPOCH FROM (COALESCE(released_at, NOW()) - acquired_at))::int AS actual_held_seconds,
          CASE
            WHEN status = 'RELEASED' AND released_at > expires_at
              THEN EXTRACT(EPOCH FROM (released_at - expires_at))::int
            WHEN status = 'ACTIVE' AND NOW() > expires_at
              THEN EXTRACT(EPOCH FROM (NOW() - expires_at))::int
            ELSE 0
          END AS overdue_seconds,
          CASE
            WHEN status = 'RELEASED' THEN 'released'
            WHEN status = 'EXPIRED'  THEN 'expired_unreleased'
            WHEN status = 'ACTIVE' AND NOW() > expires_at THEN 'stale_active'
            WHEN status = 'ACTIVE'  THEN 'live'
            ELSE status
          END AS lifecycle_state
       FROM leases
       WHERE id = $1`,
      [id],
    );
    if (rows.length === 0) throw new Errors.MoleculerError("lease not found", 404, "NOT_FOUND");
    return rows[0];
  }

  private async integrityScan(): Promise<any> {
    const pool = await this.getPool();
    const scans: { kind: string; sql: string }[] = [
      {
        kind: "orphan_lease_request_mismatch",
        sql: `SELECT l.id AS entity_id, l.request_id, NULL AS detail
                FROM leases l
           LEFT JOIN requests r ON r.id = l.request_id
               WHERE r.id IS NULL`,
      },
      {
        kind: "stale_active_lease",
        sql: `SELECT l.id AS entity_id, l.request_id,
                     'overdue by ' || EXTRACT(EPOCH FROM (NOW() - l.expires_at))::int || 's' AS detail
                FROM leases l
               WHERE l.status = 'ACTIVE' AND l.expires_at < NOW()`,
      },
      {
        kind: "attempt_orphan_no_lease",
        sql: `SELECT a.id AS entity_id, a.lease_id, NULL AS detail
                FROM attempts a
           LEFT JOIN leases l ON l.id = a.lease_id
               WHERE l.id IS NULL`,
      },
      {
        kind: "attempt_status_diverges_from_request",
        sql: `SELECT a.id AS entity_id, a.request_id,
                     'attempt=' || a.status || ' request=' || r.status AS detail
                FROM attempts a
                JOIN requests r ON r.id = a.request_id
               WHERE (r.status = 'COMPLETED' AND a.status IN ('CREATED','RUNNING'))
                  OR (r.status IN ('READY') AND a.status = 'SUCCEEDED'
                      AND NOT EXISTS (
                        SELECT 1 FROM receipts rc
                         WHERE rc.attempt_id = a.id
                           AND rc.type IN ('EXECUTION_COMPLETE','EXECUTION_FAILED')))`,
      },
      {
        kind: "receipt_request_mismatch",
        sql: `SELECT rc.id AS entity_id, rc.request_id, NULL AS detail
                FROM receipts rc
           LEFT JOIN requests r ON r.id = rc.request_id
               WHERE r.id IS NULL`,
      },
      {
        kind: "receipt_attempt_mismatch",
        // Backfilled pipeline receipts intentionally have no execution
        // attempt. Only native execution receipts are eligible for this
        // invariant; legacy vision/conduit lineage is reported separately.
        sql: `SELECT rc.id AS entity_id, rc.attempt_id, rc.lineage_source AS detail
                FROM receipts rc
           LEFT JOIN attempts a ON a.id = rc.attempt_id
               WHERE rc.lineage_source IS NULL
                 AND rc.attempt_id IS NOT NULL
                 AND a.id IS NULL`,
      },
      {
        kind: "unreleased_lease_for_terminal_request",
        sql: `SELECT l.id AS entity_id, l.request_id,
                     'request=' || r.status || ' lease=' || l.status AS detail
                FROM leases l
                JOIN requests r ON r.id = l.request_id
               WHERE r.status IN ('COMPLETED','CANCELLED','FAILED')
                 AND l.status = 'ACTIVE'`,
      },
      {
        kind: "attempted_no_completion",
        sql: `SELECT a.id AS entity_id, a.request_id,
                     'request=' || r.status || ' attempt=' || a.status AS detail
                FROM attempts a
                JOIN requests r ON r.id = a.request_id
               WHERE r.status NOT IN ('COMPLETED','CANCELLED','FAILED')
                 AND a.status = 'CREATED'
                 AND NOT EXISTS (
                      SELECT 1 FROM attempts a2
                       WHERE a2.request_id = a.request_id
                         AND a2.status IN ('SUCCEEDED','FAILED','TIMED_OUT'))`,
      },
    ];

    const results: any[] = [];
    for (const scan of scans) {
      const { rows } = await pool.query(scan.sql);
      results.push({ kind: scan.kind, count: rows.length, samples: rows.slice(0, 50) });
    }

    const totals = results.reduce(
      (acc, r) => ({ anomalies: acc.anomalies + r.count, kinds_fired: acc.kinds_fired + (r.count > 0 ? 1 : 0) }),
      { anomalies: 0, kinds_fired: 0 },
    );

    return { scanned_at: new Date().toISOString(), schema: "execution", totals, scans: results };
  }

  private async requestAttempts(id: string): Promise<any> {
    const pool = await this.getPool();
    const requestQ = await pool.query("SELECT id, business_key, title, status FROM requests WHERE id = $1", [id]);
    if (requestQ.rowCount === 0) throw new Errors.MoleculerError("request not found", 404, "NOT_FOUND");

    const { rows } = await pool.query(
      `SELECT
          a.*,
          row_to_json(l) AS lease
         FROM attempts a
         JOIN leases l    ON l.id = a.lease_id
        WHERE a.request_id = $1
        ORDER BY a.created_at ASC, a.started_at ASC NULLS LAST`,
      [id],
    );
    return { request: requestQ.rows[0], attempt_count: rows.length, attempts: rows };
  }

  private async receiptsLineage(id: string): Promise<any> {
    const pool = await this.getPool();
    const requestQ = await pool.query("SELECT id, business_key, title FROM requests WHERE id = $1", [id]);
    if (requestQ.rowCount === 0) throw new Errors.MoleculerError("request not found", 404, "NOT_FOUND");

    const { rows } = await pool.query(
      `SELECT * FROM receipts WHERE request_id = $1 ORDER BY issued_at ASC`,
      [id],
    );

    const buckets: { native: any[]; backfilled: any[]; unknown: any[] } = { native: [], backfilled: [], unknown: [] };
    for (const row of rows) {
      if (row.lineage_source === null || row.lineage_source === "") buckets.native.push(row);
      else if (row.lineage_source === "vision.receipts") buckets.backfilled.push(row);
      else buckets.unknown.push(row);
    }

    return {
      request: requestQ.rows[0],
      receipt_count: rows.length,
      native_count: buckets.native.length,
      backfilled_count: buckets.backfilled.length,
      unknown_count: buckets.unknown.length,
      lineage_buckets: buckets,
    };
  }

  private async byExecutor(executorId?: string): Promise<any> {
    const pool = await this.getPool();

    if (!executorId) {
      const { rows } = await pool.query(
        `SELECT
            executor_id,
            COUNT(*) FILTER (WHERE status = 'ACTIVE')   AS active_leases,
            COUNT(*) FILTER (WHERE status = 'RELEASED') AS released_leases,
            COUNT(*) FILTER (WHERE status = 'EXPIRED')  AS expired_leases,
            COUNT(*) AS total_leases
           FROM leases
           GROUP BY executor_id
           ORDER BY active_leases DESC NULLS LAST, executor_id`,
      );
      return { scope: "fleet", executor_count: rows.length, executors: rows };
    }

    const leasesQ = await pool.query(
      `SELECT l.*,
              r.business_key,
              r.title,
              r.status AS request_status
         FROM leases l
         JOIN requests r ON r.id = l.request_id
        WHERE l.executor_id = $1
          AND l.status = 'ACTIVE'
        ORDER BY l.acquired_at DESC`,
      [executorId],
    );

    const attemptsQ = await pool.query(
      `SELECT a.*
         FROM attempts a
         JOIN leases l ON l.id = a.lease_id
        WHERE l.executor_id = $1
          AND a.status IN ('CREATED','RUNNING')
        ORDER BY a.created_at DESC`,
      [executorId],
    );

    const summaryQ = await pool.query(
      `SELECT
          COUNT(*) FILTER (WHERE l.status = 'ACTIVE')   AS active_leases,
          COUNT(*) FILTER (WHERE l.status = 'RELEASED') AS released_leases,
          COUNT(*) FILTER (WHERE l.status = 'EXPIRED')  AS expired_leases,
          COUNT(DISTINCT l.request_id) FILTER (WHERE l.status = 'ACTIVE') AS requests_held,
          COUNT(*) AS total_leases
         FROM leases l
        WHERE l.executor_id = $1`,
      [executorId],
    );

    return {
      scope: "executor",
      executor_id: executorId,
      summary: summaryQ.rows[0] ?? {},
      active_leases: leasesQ.rows,
      in_progress_attempts: attemptsQ.rows,
    };
  }

  private async statusDistribution(): Promise<any> {
    const pool = await this.getPool();
    const [requestsQ, leasesQ, attemptsQ, receiptsQ] = await Promise.all([
      pool.query(`SELECT status, count(*) AS count FROM requests GROUP BY status ORDER BY count DESC`),
      pool.query(`SELECT status, count(*) AS count FROM leases   GROUP BY status ORDER BY count DESC`),
      pool.query(`SELECT status, count(*) AS count FROM attempts  GROUP BY status ORDER BY count DESC`),
      pool.query(`SELECT type   AS status, count(*) AS count FROM receipts GROUP BY type    ORDER BY count DESC`),
    ]);
    const staleQ = await pool.query(
      `SELECT count(*) AS count FROM leases WHERE status = 'ACTIVE' AND expires_at < NOW()`,
    );

    return {
      scanned_at: new Date().toISOString(),
      requests: requestsQ.rows,
      leases: leasesQ.rows,
      attempts: attemptsQ.rows,
      receipts_by_type: receiptsQ.rows,
      stale_active_leases: parseInt(staleQ.rows[0].count, 10) || 0,
    };
  }

  private async pipelineOrigin(id: string): Promise<any> {
    const pool = await this.getPool();
    const localQ = await pool.query(`SELECT * FROM receipts WHERE id = $1`, [id]);
    if (localQ.rowCount === 0) throw new Errors.MoleculerError("execution.receipt not found", 404, "NOT_FOUND");
    const local = localQ.rows[0];

    let vision: any = null;
    const relationship: string =
      local.lineage_source === "vision.receipts" ? "backfilled_from_vision" :
      local.lineage_source === null || local.lineage_source === "" ? "native_execution_only" :
      `unknown_source:${local.lineage_source}`;

    if (local.lineage_source === "vision.receipts" && local.lineage_original_id) {
      const visionQ = await pool.query(
        `SELECT *
           FROM vision.receipts
          WHERE id::text = $1
          LIMIT 1`,
        [local.lineage_original_id],
      );
      vision = visionQ.rows[0] ?? null;
    }

    return {
      local_execution_record: { audit_trail: "execution.receipts", record: local },
      remote_vision_record: vision ? { audit_trail: "vision.receipts", record: vision } : null,
      relationship,
    };
  }

  private async health(): Promise<any> {
    const pool = await this.getPool();
    const { rows } = await pool.query(
      `SELECT
         (SELECT count(*) FROM requests)   AS requests,
         (SELECT count(*) FROM leases)     AS leases,
         (SELECT count(*) FROM attempts)   AS attempts,
         (SELECT count(*) FROM receipts)   AS receipts`,
    );
    return { status: "ok", db: true, schema: "execution", counts: rows[0] };
  }

  /**
   * Legacy GET /api/execution/health — per-status request counts plus the
   * attempt/lease states the simple probe misses (scanned_at + flat count
   * row). Missing alias discovered in post-deploy live parity (2026-09-12):
   * the catalog shipped 18 aliases but the legacy router has 19 routes.
   */
  private async richHealth(): Promise<any> {
    const pool = await this.getPool();
    const { rows } = await pool.query(
      `SELECT
          (SELECT count(*) FROM requests)                                                  AS requests,
          (SELECT count(*) FROM requests WHERE status = 'READY')                            AS ready_requests,
          (SELECT count(*) FROM requests WHERE status = 'COMPLETED')                         AS completed_requests,
          (SELECT count(*) FROM leases)                                                     AS leases,
          (SELECT count(*) FROM leases    WHERE status = 'ACTIVE' AND expires_at < NOW())   AS stale_active_leases,
          (SELECT count(*) FROM attempts)                                                   AS attempts,
          (SELECT count(*) FROM attempts  WHERE status = 'RUNNING')                          AS running_attempts,
          (SELECT count(*) FROM receipts)                                                   AS receipts`
    );
    return {
      scanned_at: new Date().toISOString(),
      ...rows[0],
    };
  }
}
