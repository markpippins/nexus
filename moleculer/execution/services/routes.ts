// execution-srv routes — observability API over the PostgreSQL `execution` schema.
//
// Endpoints (see `REST API.md` at the project root):
//
//   0. Paginated list endpoints
//        GET /api/execution/requests  ?status=&search=&limit=20&offset=0
//        GET /api/execution/leases    ?status=&search=&limit=20&offset=0
//        GET /api/execution/attempts  ?status=&search=&limit=20&offset=0
//        GET /api/execution/receipts  ?type=&search=&limit=20&offset=0
//
//   1. Lifecycle state (per request — natural aggregate root)
//        GET /api/execution/requests/{id}/state
//
//   2. Lease integrity
//        GET /api/execution/leases/stale
//        GET /api/execution/leases/{id}/lifecycle
//
//   3. Cross-table consistency scan (generalized check_receipt_integrity)
//        GET /api/execution/health/integrity-scan
//
//   4. Attempt/lease/request tree
//        GET /api/execution/requests/{id}/attempts
//        GET /api/execution/requests/{id}/receipts/lineage
//
//   5. Fleet view
//        GET /api/execution/health/by-executor?executor_id=
//        GET /api/execution/health/status-distribution
//
//   Update: GET /api/execution/receipts/{id}/pipeline-origin
//        follows lineage_original_id → vision.receipts.id and returns both
//        records side by side, labeled by which audit trail each came from.
//
// All queries run as SELECTs only — there is no write path in this service.

import { Request, Response, Router } from 'express';
import { Pool, QueryResult } from 'pg';
import { governanceMetrics, METRIC_WITNESSED_RUN_STATUS, METRIC_RECEIPT_CORRELATION_INVALID, isUuidShape } from './metrics.js';

// ── Helpers ────────────────────────────────────────────────────────

// Validate that a path param looks like a UUID. The pg UUID type will
// reject bad input anyway, but this gives a clean 400 instead of a 500
// and keeps logs free of stack traces for malformed requests.
//
// Express types req.params[...] as `string | undefined` and our TS strict
// config widens that on certain code paths. We additionally tolerate the
// `string[]` shape that some express routers forward (query-string-style)
// by collapsing the first element. Restored to a plain string here.
const UUID_RE = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
function asString(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}
function isValidUuid(v: string | string[] | undefined): v is string {
  const s = asString(v);
  return typeof s === 'string' && UUID_RE.test(s);
}
function requireUuid(v: string | string[] | undefined, res: Response, label = 'id'): string | null {
  if (!isValidUuid(v)) {
    badRequest(res, `${label} must be a UUID`);
    return null;
  }
  // safe assertion: isValidUuid v is string only confirmed single string
  return asString(v) as string;
}

function badRequest(res: Response, msg: string): void {
  res.status(400).json({ error: msg });
}

function notFound(res: Response, msg: string): void {
  res.status(404).json({ error: msg });
}

// Generic error → 500 wrapper used by every handler. We deliberately keep
// the message in the response because this is an internal observability
// service, and operators debugging it benefit from the SQL message.
function sendError(res: Response, err: unknown): void {
  const message = err instanceof Error ? err.message : String(err);
  res.status(500).json({ error: message });
}

// ── Paginated List Helper ──────────────────────────────────────────
// Shared by all four list endpoints. Each table differs only in its
// filter column name, searchable columns, and sort column.

interface PaginatedListConfig {
  table: string;
  filterColumn: string;       // DB column for the eq filter ('status' or 'type')
  filterParam: string;        // query-string param name ('status' or 'type')
  searchColumns: string[];    // DB columns/expressions for ILIKE search
  orderBy: string;            // sort clause (column + direction)
}

function paginatedListHandler(pool: Pool, cfg: PaginatedListConfig) {
  return async (req: Request, res: Response) => {
    try {
      const filterVal = (req.query[cfg.filterParam] as string | undefined)?.trim();
      const search = (req.query.search as string | undefined)?.trim();
      const limit = Math.min(Math.max(parseInt(req.query.limit as string) || 20, 1), 100);
      const offset = Math.max(parseInt(req.query.offset as string) || 0, 0);

      const conditions: string[] = [];
      const values: any[] = [];
      let p = 1;

      if (filterVal) {
        conditions.push(`${cfg.filterColumn} = $${p++}`);
        values.push(filterVal);
      }
      if (search) {
        conditions.push(
          `(${cfg.searchColumns.map(c => `${c} ILIKE $${p}`).join(' OR ')})`
        );
        values.push(`%${search}%`);
        p++;
      }

      const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';
      const sql = `
        SELECT *, COUNT(*) OVER() AS full_count
        FROM ${cfg.table}
        ${where}
        ORDER BY ${cfg.orderBy}
        LIMIT $${p++} OFFSET $${p}
      `;
      values.push(limit, offset);

      const { rows } = await pool.query(sql, values);
      const total = rows.length > 0 ? parseInt(rows[0].full_count, 10) : 0;
      const items = rows.map(({ full_count: _fc, ...rest }) => rest);

      res.json({ total, limit, offset, items });
    } catch (err) {
      sendError(res, err);
    }
  };
}

export function createRoutes(pool: Pool): Router {
  const router = Router();

  // ═══════════════════════════════════════════════════════════════════
  // 0. PAGINATED LIST ENDPOINTS
  //
  //    GET /api/execution/requests  ?status=&search=&limit=20&offset=0
  //    GET /api/execution/leases    ?status=&search=&limit=20&offset=0
  //    GET /api/execution/attempts  ?status=&search=&limit=20&offset=0
  //    GET /api/execution/receipts  ?type=&search=&limit=20&offset=0
  //
  //    Each returns { total, limit, offset, items: [...] } with DB-native
  //    column shapes. See DRIFT.md in execution-ui for field-name
  //    differences from the UI's expected TypeScript types.
  // ═══════════════════════════════════════════════════════════════════

  router.get('/requests', paginatedListHandler(pool, {
    table: 'requests',
    filterColumn: 'status',
    filterParam: 'status',
    searchColumns: ['business_key', 'title', 'objective', 'id::text', 'inputs::text'],
    orderBy: 'created_at DESC',
  }));

  router.get('/leases', paginatedListHandler(pool, {
    table: 'leases',
    filterColumn: 'status',
    filterParam: 'status',
    searchColumns: ['executor_id', 'request_id::text', 'id::text'],
    orderBy: 'created_at DESC',
  }));

  router.get('/attempts', paginatedListHandler(pool, {
    table: 'attempts',
    filterColumn: 'status',
    filterParam: 'status',
    searchColumns: ['executor_id', 'error', 'request_id::text', 'lease_id::text', 'id::text'],
    orderBy: 'created_at DESC',
  }));

  router.get('/receipts', paginatedListHandler(pool, {
    table: 'receipts',
    filterColumn: 'type',
    filterParam: 'type',
    searchColumns: ['agent_role', 'summary', 'request_id::text', 'attempt_id::text', 'id::text'],
    orderBy: 'issued_at DESC',
  }));

  // ═══════════════════════════════════════════════════════════════════
  // 1. WITNESSED-RUN PROJECTION — read-only normalized provenance surface
  //
  //    GET /api/execution/witnessed-runs?workflow_instance_id=&node_id=
  //
  //    This endpoint deliberately exposes nullable lineage fields. The
  //    execution schema currently owns request/attempt/receipt identity;
  //    envelope, manifest, SOL, evidence, and replay identities are returned
  //    only when persisted in existing JSON metadata. No browser-side join or
  //    authority decision is performed here.
  // ═══════════════════════════════════════════════════════════════════
  router.get('/witnessed-runs', witnessedRunHandler(pool));

  // W3.08 — versioned governed projection for downstream consumers.
  router.get('/projections/witnessed-runs', witnessedRunProjectionHandler(pool));

  // ═══════════════════════════════════════════════════════════════════
  // 1b. GOVERNANCE METRICS — server-derived counters (W3.05)
  //
  //     GET /api/execution/metrics
  //
  //     JSON snapshot of governance metric families. Values are derived
  //     from live classification/query outcomes and correlate to envelope
  //     and receipt identities — never payloads.
  // ═══════════════════════════════════════════════════════════════════
  router.get('/metrics', (_req: Request, res: Response) => {
    res.json(governanceMetrics.snapshot());
  });

  // ═══════════════════════════════════════════════════════════════════
  // 1c. WITNESSED-RUN DIAGNOSTICS — structured per-run health (W3.05)
  //
  //     GET /api/execution/witnessed-runs/diagnostics?workflow_instance_id=&node_id=
  //
  //     Server-derived health summary for one witnessed run: authoritative
  //     status, enumerated missing lineage elements, receipt correlation
  //     validity, replay state. Correlates to immutable envelope and receipt
  //     identities — never reconstructs authority browser-side (AC4).
  // ═══════════════════════════════════════════════════════════════════
  router.get('/witnessed-runs/diagnostics', witnessedRunDiagnosticsHandler(pool));

  // ═══════════════════════════════════════════════════════════════════
  // 2. LIFECYCLE STATE — the natural aggregate root
  //
  //    GET /api/execution/requests/{id}/state
  //
  //    Returns the request, its current lease (if any), its latest attempt,
  //    and all of its receipts — the "where does this stand right now" view
  //    that currently requires four joins nobody's written yet.
  // ═══════════════════════════════════════════════════════════════════

  router.get('/requests/:id/state', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const requestId = requireUuid(id, res);
      if (!requestId) return;

      // request row
      const requestQ = await pool.query(
        'SELECT * FROM requests WHERE id = $1',
        [requestId]
      );
      if (requestQ.rowCount === 0) return notFound(res, 'request not found');
      const request = requestQ.rows[0];

      // current active lease (only one ACTIVE lease is allowed per request
      // by idx_execution_leases_active_per_request — fall back to most
      // recent lease otherwise, ordered by acquired_at DESC).
      const leaseQ = await pool.query(
        `SELECT * FROM leases
         WHERE request_id = $1
         ORDER BY (status = 'ACTIVE') DESC, acquired_at DESC
         LIMIT 1`,
        [requestId]
      );
      const currentLease = leaseQ.rows[0] ?? null;

      // latest attempt
      const attemptQ = await pool.query(
        `SELECT * FROM attempts
         WHERE request_id = $1
         ORDER BY created_at DESC, started_at DESC NULLS LAST
         LIMIT 1`,
        [requestId]
      );
      const latestAttempt = attemptQ.rows[0] ?? null;

      // all receipts, chronological
      const receiptsQ = await pool.query(
        `SELECT * FROM receipts WHERE request_id = $1 ORDER BY issued_at ASC`,
        [requestId]
      );

      res.json({
        request,
        current_lease: currentLease,
        latest_attempt: latestAttempt,
        receipts: receiptsQ.rows,
        receipt_count: receiptsQ.rowCount,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  // 2. LEASE INTEGRITY — the expiry gap, made visible
  // ═══════════════════════════════════════════════════════════════════

  // GET /api/execution/leases/stale
  // Active leases whose expires_at < now() — the enforcement gap made
  // queryable. Returns each stale lease joined to its request so callers
  // see the executor that is holding dead ground.
  router.get('/leases/stale', async (_req: Request, res: Response) => {
    try {
      const { rows }: QueryResult = await pool.query(
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
         ORDER BY l.expires_at ASC`
      );
      res.json({
        count: rows.length,
        stale_leases: rows,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // GET /api/execution/leases/{id}/lifecycle
  // acquired_at → expires_at → released_at, actual vs promised.
  // Computes how long the lease was actually held (or how long it's been
  // held so far if still ACTIVE), and how that compares to the promised TTL.
  router.get('/leases/:id/lifecycle', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const requestId = requireUuid(id, res);
      if (!requestId) return;

      const { rows }: QueryResult = await pool.query(
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
        [requestId]
      );
      if (rows.length === 0) return notFound(res, 'lease not found');
      res.json(rows[0]);
    } catch (err) {
      sendError(res, err);
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  // 3. CROSS-TABLE CONSISTENCY SCAN
  //
  //    GET /api/execution/health/integrity-scan
  //
  //    Same shape as Vision's check_receipt_integrity() — a named, growing
  //    list of specific pathologies, each one a query you write the day you
  //    find the gap. Not a generic "health score."
  // ═══════════════════════════════════════════════════════════════════

  router.get('/health/integrity-scan', async (_req: Request, res: Response) => {
    try {
      // Each scan returns { kind, count, sample_ids[] } so callers get both
      // scale and concrete examples to drill into. The kinds are intentionally
      // explicit names — when we discover a new pathology in production, we
      // add a kind here rather than abstracting into "anomaly_score".
      const scans: { kind: string; sql: string }[] = [
        {
          kind: 'orphan_lease_request_mismatch',
          sql: `SELECT l.id AS entity_id, l.request_id, NULL AS detail
                  FROM leases l
             LEFT JOIN requests r ON r.id = l.request_id
                 WHERE r.id IS NULL`,
        },
        {
          kind: 'stale_active_lease',
          sql: `SELECT l.id AS entity_id, l.request_id,
                       'overdue by ' || EXTRACT(EPOCH FROM (NOW() - l.expires_at))::int || 's' AS detail
                  FROM leases l
                 WHERE l.status = 'ACTIVE' AND l.expires_at < NOW()`,
        },
        {
          kind: 'attempt_orphan_no_lease',
          sql: `SELECT a.id AS entity_id, a.lease_id, NULL AS detail
                  FROM attempts a
             LEFT JOIN leases l ON l.id = a.lease_id
                 WHERE l.id IS NULL`,
        },
        {
          kind: 'attempt_status_diverges_from_request',
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
          kind: 'receipt_request_mismatch',
          sql: `SELECT rc.id AS entity_id, rc.request_id, NULL AS detail
                  FROM receipts rc
             LEFT JOIN requests r ON r.id = rc.request_id
                 WHERE r.id IS NULL`,
        },
        {
          kind: 'receipt_attempt_mismatch',
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
          kind: 'unreleased_lease_for_terminal_request',
          sql: `SELECT l.id AS entity_id, l.request_id,
                       'request=' || r.status || ' lease=' || l.status AS detail
                  FROM leases l
                  JOIN requests r ON r.id = l.request_id
                 WHERE r.status IN ('COMPLETED','CANCELLED','FAILED')
                   AND l.status = 'ACTIVE'`,
        },
        {
          kind: 'attempted_no_completion',
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
        const { rows }: QueryResult = await pool.query(scan.sql);
        results.push({
          kind: scan.kind,
          count: rows.length,
          samples: rows.slice(0, 50),
        });
      }

      const totals = results.reduce(
        (acc, r) => ({ anomalies: acc.anomalies + r.count, kinds_fired: acc.kinds_fired + (r.count > 0 ? 1 : 0) }),
        { anomalies: 0, kinds_fired: 0 }
      );

      res.json({
        scanned_at: new Date().toISOString(),
        schema: 'execution',
        totals,
        scans: results,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  // 4. ATTEMPT/LEASE/REQUEST TREE
  // ═══════════════════════════════════════════════════════════════════

  // GET /api/execution/requests/{id}/attempts
  // Every attempt for this request, each attempt's lease, chronological.
  router.get('/requests/:id/attempts', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const requestId = requireUuid(id, res);
      if (!requestId) return;

      const requestQ = await pool.query(
        'SELECT id, business_key, title, status FROM requests WHERE id = $1',
        [requestId]
      );
      if (requestQ.rowCount === 0) return notFound(res, 'request not found');

      const { rows }: QueryResult = await pool.query(
        `SELECT
            a.*,
            row_to_json(l) AS lease
           FROM attempts a
           JOIN leases l    ON l.id = a.lease_id
          WHERE a.request_id = $1
          ORDER BY a.created_at ASC, a.started_at ASC NULLS LAST`,
        [requestId]
      );
      res.json({
        request: requestQ.rows[0],
        attempt_count: rows.length,
        attempts: rows,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // GET /api/execution/requests/{id}/receipts/lineage
  // Split by lineage_source: native vs backfilled vs unknown.
  router.get('/requests/:id/receipts/lineage', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const requestId = requireUuid(id, res);
      if (!requestId) return;

      const requestQ = await pool.query(
        'SELECT id, business_key, title FROM requests WHERE id = $1',
        [requestId]
      );
      if (requestQ.rowCount === 0) return notFound(res, 'request not found');

      const { rows }: QueryResult = await pool.query(
        `SELECT * FROM receipts WHERE request_id = $1 ORDER BY issued_at ASC`,
        [requestId]
      );

      const buckets = {
        native: [] as any[],     // NULL lineage_source — entry was born in execution.receipts
        backfilled: [] as any[], // lineage_source references vision.receipts
        unknown: [] as any[],    // any other non-null value (future migration sources)
      };
      for (const row of rows) {
        if (row.lineage_source === null || row.lineage_source === '') {
          buckets.native.push(row);
        } else if (row.lineage_source === 'vision.receipts') {
          buckets.backfilled.push(row);
        } else {
          buckets.unknown.push(row);
        }
      }

      res.json({
        request: requestQ.rows[0],
        receipt_count: rows.length,
        native_count: buckets.native.length,
        backfilled_count: buckets.backfilled.length,
        unknown_count: buckets.unknown.length,
        lineage_buckets: buckets,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  // 5. FLEET VIEW
  // ═══════════════════════════════════════════════════════════════════

  // GET /api/execution/health/by-executor?executor_id=
  // What is this executor currently holding / running. Returns active
  // leases + their in-progress attempts + a summary counter for the executor.
  router.get('/health/by-executor', async (req: Request, res: Response) => {
    try {
      const executorId = (req.query.executor_id as string | undefined)?.trim();

      if (!executorId) {
        // No filter → group report across the whole fleet.
        const { rows }: QueryResult = await pool.query(
          `SELECT
              executor_id,
              COUNT(*) FILTER (WHERE status = 'ACTIVE')   AS active_leases,
              COUNT(*) FILTER (WHERE status = 'RELEASED') AS released_leases,
              COUNT(*) FILTER (WHERE status = 'EXPIRED')  AS expired_leases,
              COUNT(*) AS total_leases
             FROM leases
             GROUP BY executor_id
             ORDER BY active_leases DESC NULLS LAST, executor_id`
        );
        res.json({
          scope: 'fleet',
          executor_count: rows.length,
          executors: rows,
        });
        return;
      }

      // scoped to one executor
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
        [executorId]
      );

      const attemptsQ = await pool.query(
        `SELECT a.*
           FROM attempts a
           JOIN leases l ON l.id = a.lease_id
          WHERE l.executor_id = $1
            AND a.status IN ('CREATED','RUNNING')
          ORDER BY a.created_at DESC`,
        [executorId]
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
        [executorId]
      );

      res.json({
        scope: 'executor',
        executor_id: executorId,
        summary: summaryQ.rows[0] ?? {},
        active_leases: leasesQ.rows,
        in_progress_attempts: attemptsQ.rows,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // GET /api/execution/health/status-distribution
  // Count of requests per status, leases per status, attempts per status —
  // the drift-over-time signal. One call returns everything for snapshotting.
  router.get('/health/status-distribution', async (_req: Request, res: Response) => {
    try {
      const [requestsQ, leasesQ, attemptsQ, receiptsQ] = await Promise.all([
        pool.query(`SELECT status, count(*) AS count FROM requests GROUP BY status ORDER BY count DESC`),
        pool.query(`SELECT status, count(*) AS count FROM leases   GROUP BY status ORDER BY count DESC`),
        pool.query(`SELECT status, count(*) AS count FROM attempts  GROUP BY status ORDER BY count DESC`),
        pool.query(`SELECT type   AS status, count(*) AS count FROM receipts GROUP BY type    ORDER BY count DESC`),
      ]);

      // stale lease signal — the enforcement gap I want to see trending to zero
      const staleQ = await pool.query(
        `SELECT count(*) AS count FROM leases WHERE status = 'ACTIVE' AND expires_at < NOW()`
      );

      res.json({
        scanned_at: new Date().toISOString(),
        requests: requestsQ.rows,
        leases: leasesQ.rows,
        attempts: attemptsQ.rows,
        receipts_by_type: receiptsQ.rows,
        stale_active_leases: parseInt(staleQ.rows[0].count, 10) || 0,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  // UPDATE: pipeline-origin — the lineage-honest endpoint
  //
  //   GET /api/execution/receipts/{id}/pipeline-origin
  //
  //   Follows lineage_original_id → vision.receipts.id and returns both
  //   records side by side, explicitly labeled by which audit trail each
  //   came from. Doesn't pretend there's one canonical receipt — it shows
  //   the seam.
  // ═══════════════════════════════════════════════════════════════════

  router.get('/receipts/:id/pipeline-origin', async (req: Request, res: Response) => {
    try {
      const { id } = req.params;
      const requestId = requireUuid(id, res);
      if (!requestId) return;

      // The execution.receipt row is the local record.
      const localQ = await pool.query(
        `SELECT * FROM receipts WHERE id = $1`,
        [requestId]
      );
      if (localQ.rowCount === 0) return notFound(res, 'execution.receipt not found');
      const local = localQ.rows[0];

      // Resolve the lineage. Two cases:
      //   1. lineage_source = 'vision.receipts' → cross-schema join
      //   2. lineage_source IS NULL            → this receipt is the only
      //      record (native execution audit trail, no upstream)
      let vision: any = null;
      const relationship: string =
        local.lineage_source === 'vision.receipts' ? 'backfilled_from_vision' :
        (local.lineage_source === null || local.lineage_source === '') ? 'native_execution_only' :
        `unknown_source:${local.lineage_source}`;

      if (local.lineage_source === 'vision.receipts' && local.lineage_original_id) {
        // Cross-schema lookup. lineage_original_id is TEXT (the original
        // vision.receipts.id is stored as text), so cast it for the JOIN.
        const visionQ = await pool.query(
          `SELECT *
             FROM nebula.receipts_unified
            WHERE id::text = $1
            LIMIT 1`,
          [local.lineage_original_id]
        );
        vision = visionQ.rows[0] ?? null;
      }

      res.json({
        local_execution_record: {
          audit_trail: 'execution.receipts',
          record: local,
        },
        remote_vision_record: vision
          ? { audit_trail: 'vision.receipts', record: vision }
          : null,
        relationship,
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  // ═══════════════════════════════════════════════════════════════════
  // Health inline — GET /api/execution/health
  // Lightweight entry point distinct from /health (the root-level check).
  // Returns just the live counts + the three key operational signals.
  // ═══════════════════════════════════════════════════════════════════

  router.get('/health', async (_req: Request, res: Response) => {
    try {
      const { rows }: QueryResult = await pool.query(
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
      res.json({
        scanned_at: new Date().toISOString(),
        ...rows[0],
      });
    } catch (err) {
      sendError(res, err);
    }
  });

  return router;
}

// ── v3 assessment/evidence assembly (ruling ffa4ffc5) ─────────────────
// The SQL returns receipt/evidence columns flat; these build the response
// shapes. Assessment derives from the admission receipt: disposition is
// ar.admitted verbatim (t/f); status renders 'admitted'/'rejected' (never
// 'refused' — that classifier state is reserved for governance decisions);
// reason/source/policy hash carry through. No receipt → all null.
export function buildAssessmentFromRow(row: Record<string, unknown>): Record<string, unknown> {
  const admitted = row.assessment_admitted;
  if (typeof admitted !== "boolean") {
    return { disposition: null, status: null, reason: null, sourceSystem: null, policyVersionHash: null };
  }
  return {
    disposition: admitted,
    status: admitted ? "admitted" : "rejected",
    reason: (row.assessment_reason as string | null) ?? null,
    sourceSystem: (row.assessment_source_system as string | null) ?? null,
    policyVersionHash: (row.assessment_policy_hash as string | null) ?? null,
  };
}

// Evidence identity (id + source_hash fingerprint) from resolution.
// execution_evidence via the receipt's evidence_id; the verification payload
// rides the provenance surface only (/witnessed-runs), never the governed
// projection.
interface WitnessedEvidence {
  ids: string[];
  fingerprint: string | null;
  payload: Record<string, unknown> | null;
}

export function buildEvidenceFromRow(row: Record<string, unknown>): WitnessedEvidence {
  const id = row.evidence_id;
  if (typeof id !== "string" || id.length === 0) return { ids: [], fingerprint: null, payload: null };
  return {
    ids: [id],
    fingerprint: (row.evidence_fingerprint as string | null) ?? null,
    payload: (row.evidence_payload as Record<string, unknown> | null) ?? null,
  };
}

/**
 * GET /api/execution/witnessed-runs handler — extracted so the conformance
 * test (src/routes.test.ts) can drive it with a mocked pg Pool without
 * faking the whole Express Router.
 *
 * v3 (ruling ffa4ffc5): identity matches business_key; assessment/evidence
 * read the attempt's admission receipt + its resolution.execution_evidence
 * row; envelope/manifest/law/replay render null until the producer contract
 * (6677c394 R2) backs them. Receipt legs (peb_admission/conduit_transition)
 * stay as re-pointed by 6677c394 R2/R3.
 */
export function witnessedRunHandler(pool: Pool) {
  return async (req: Request, res: Response) => {
    try {
      const workflowInstanceId = (req.query.workflow_instance_id as string | undefined)?.trim();
      const nodeId = (req.query.node_id as string | undefined)?.trim();
      if (!workflowInstanceId || !nodeId) return badRequest(res, 'workflow_instance_id and node_id are required');

      const { rows } = await pool.query(
        `SELECT
           r.id AS request_id,
           r.business_key AS workflow_instance_id,
           -- v3 (ruling ffa4ffc5): the metadata legs are gone — requests/attempts
           -- never had metadata columns (canonical DDL and the live DB agree; the
           -- family 500'd at plan time). Standing rule: no query references a
           -- column absent from canonical DDL. envelope/manifest/law/replay
           -- render null until the producer contract (6677c394 R2) backs them.
           NULL::jsonb AS envelope,
           NULL::jsonb AS manifest,
           NULL::jsonb AS law,
           ar.admitted AS assessment_admitted,
           ar.reason AS assessment_reason,
           ar.source_system AS assessment_source_system,
           ar.policy_version_hash AS assessment_policy_hash,
           ev.id::text AS evidence_id,
           ev.source_hash AS evidence_fingerprint,
           ev.payload AS evidence_payload,
           -- Receipt correlation legs kept per ffa4ffc5 (mapping from 6677c394 R2/R3):
           --   peb_admission: the attempt's PEB admission receipt transaction id
           --   conduit_transition: the attempt's latest native EXECUTION_COMPLETE receipt
           --     (a live lane under chk_execution_receipts_type; the retired conduit
           --     types stay gone per R1 — the wind seam V151 is the eventual carrier)
           (SELECT ar2.peb_transaction_id::text
              FROM resolution.execution_admission_receipt ar2
             WHERE ar2.attempt_id = a.id::text
             ORDER BY ar2.created_at DESC
             LIMIT 1) AS peb_admission,
           (SELECT rec.id::text
              FROM receipts rec
             WHERE rec.attempt_id = a.id
               AND rec.type = 'EXECUTION_COMPLETE'
             ORDER BY rec.issued_at DESC
             LIMIT 1) AS conduit_transition
         FROM requests r
         LEFT JOIN LATERAL (
           SELECT * FROM attempts a0 WHERE a0.request_id = r.id ORDER BY a0.created_at DESC LIMIT 1
         ) a ON true
         -- v3 (ruling ffa4ffc5): assessment + evidence come from the attempt's
         -- latest admission receipt joined to its immutable evidence row.
         LEFT JOIN LATERAL (
           SELECT ar0.* FROM resolution.execution_admission_receipt ar0
           WHERE ar0.attempt_id = a.id::text
           ORDER BY ar0.created_at DESC
           LIMIT 1
         ) ar ON true
         LEFT JOIN resolution.execution_evidence ev ON ev.id = ar.evidence_id
         WHERE r.business_key = $1
         LIMIT 1`,
        // v3 (ffa4ffc5): node_id is output-echo only — it is no longer a query
        // parameter (the only SQL parameter is the business_key match).
        [workflowInstanceId],
      );
      if (rows.length === 0) return notFound(res, 'witnessed run not found');
      const row = rows[0];
      // v3 (ffa4ffc5): envelope/manifest/law/replay are null at the source —
      // `{}` keeps the response shape stable with every field rendering null.
      const envelope = {} as Record<string, unknown>;
      const manifest = {} as Record<string, unknown>;
      const law = {} as Record<string, unknown>;
      const assessment = buildAssessmentFromRow(row);
      const evidence = buildEvidenceFromRow(row);
      const replay = {} as Record<string, unknown>;
      res.json({
        projection: {
          workflow: { instanceId: workflowInstanceId, nodeId },
          envelope: {
            id: envelope.id ?? envelope.envelope_id ?? null,
            evaluationFingerprint: envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint ?? null,
            contractId: envelope.contractId ?? envelope.contract_id ?? null,
            contractVersion: envelope.contractVersion ?? envelope.contract_version ?? null,
            contractDigest: envelope.contractDigest ?? envelope.contract_digest ?? null,
          },
          manifest: {
            id: manifest.id ?? manifest.artifactId ?? manifest.artifact_id ?? null,
            version: manifest.version ?? manifest.artifactVersion ?? manifest.artifact_version ?? null,
            digest: manifest.digest ?? manifest.artifactDigest ?? manifest.artifact_digest ?? null,
          },
          law: {
            propositionIds: law.propositionIds ?? law.proposition_ids ?? [],
            doctrineIds: law.doctrineIds ?? law.doctrine_ids ?? [],
            evaluatorId: law.evaluatorId ?? law.evaluator_id ?? null,
          },
          assessment,
          receipts: { pebAdmission: row.peb_admission, conduitTransition: row.conduit_transition },
          evidence: { ids: evidence.ids, fingerprint: evidence.fingerprint, payload: evidence.payload },
          replay: { fixtureId: replay.fixtureId ?? replay.fixture_id ?? null, status: replay.status ?? null },
          status: (() => {
            const status = classifyWitnessedRunStatus({
              envelope: envelope as Record<string, unknown>,
              manifest: manifest as Record<string, unknown>,
              assessment: assessment as Record<string, unknown>,
              replay: replay as Record<string, unknown>,
              row: { ...row, evidence } as Record<string, unknown>,
            });
            governanceMetrics.inc(METRIC_WITNESSED_RUN_STATUS, { status });
            // W3.05: flag malformed receipt correlation ids seen in the wild.
            for (const rid of [row.peb_admission, row.conduit_transition]) {
              if (rid != null && !isUuidShape(rid)) {
                governanceMetrics.inc(METRIC_RECEIPT_CORRELATION_INVALID);
              }
            }
            return status;
          })(),
        },
      });
    } catch (err) {
      sendError(res, err);
    }
  };
}

/**
 * GET /api/execution/witnessed-runs/diagnostics handler (W3.05).
 *
 * Server-derived health summary for one witnessed run. Enumerates exactly
 * which lineage elements are missing, validates receipt correlation ids,
 * and correlates to the immutable envelope/receipt identities — never
 * reconstructing authority browser-side (AC4).
 */
export function witnessedRunDiagnosticsHandler(pool: Pool) {
  return async (req: Request, res: Response) => {
    try {
      const workflowInstanceId = (asString(req.query.workflow_instance_id as string | string[] | undefined) ?? '').trim();
      const nodeId = (asString(req.query.node_id as string | string[] | undefined) ?? '').trim();
      if (!workflowInstanceId || !nodeId) return badRequest(res, 'workflow_instance_id and node_id are required');

      const { rows } = await pool.query(
        `SELECT
           r.id AS request_id,
           ar.admitted AS assessment_admitted,
           ar.reason AS assessment_reason,
           ar.source_system AS assessment_source_system,
           ar.policy_version_hash AS assessment_policy_hash,
           ev.id::text AS evidence_id,
           ev.source_hash AS evidence_fingerprint,
           ev.payload AS evidence_payload,
           -- Receipt correlation legs kept per ffa4ffc5 (mapping from 6677c394 R2/R3):
           --   peb_admission: the attempt's PEB admission receipt transaction id
           --   conduit_transition: the attempt's latest native EXECUTION_COMPLETE receipt
           --     (a live lane under chk_execution_receipts_type; the retired conduit
           --     types stay gone per R1 — the wind seam V151 is the eventual carrier)
           (SELECT ar2.peb_transaction_id::text
              FROM resolution.execution_admission_receipt ar2
             WHERE ar2.attempt_id = a.id::text
             ORDER BY ar2.created_at DESC
             LIMIT 1) AS peb_admission,
           (SELECT rec.id::text
              FROM receipts rec
             WHERE rec.attempt_id = a.id
               AND rec.type = 'EXECUTION_COMPLETE'
             ORDER BY rec.issued_at DESC
             LIMIT 1) AS conduit_transition
         FROM requests r
         LEFT JOIN LATERAL (
           SELECT * FROM attempts a0 WHERE a0.request_id = r.id ORDER BY a0.created_at DESC LIMIT 1
         ) a ON true
         -- v3 (ruling ffa4ffc5): assessment + evidence come from the attempt's
         -- latest admission receipt joined to its immutable evidence row.
         LEFT JOIN LATERAL (
           SELECT ar0.* FROM resolution.execution_admission_receipt ar0
           WHERE ar0.attempt_id = a.id::text
           ORDER BY ar0.created_at DESC
           LIMIT 1
         ) ar ON true
         LEFT JOIN resolution.execution_evidence ev ON ev.id = ar.evidence_id
         WHERE r.business_key = $1
         LIMIT 1`,
        // v3 (ffa4ffc5): node_id is output-echo only — it is no longer a query
        // parameter (the only SQL parameter is the business_key match).
        [workflowInstanceId],
      );
      if (rows.length === 0) return notFound(res, 'witnessed run not found');
      const row = rows[0];
      const envelope = {} as Record<string, unknown>;
      const manifest = {} as Record<string, unknown>;
      const assessment = buildAssessmentFromRow(row);
      const evidence = buildEvidenceFromRow(row);
      const replay = {} as Record<string, unknown>;

      const status = classifyWitnessedRunStatus({
        envelope, manifest, assessment, replay, row: { ...row, evidence } as Record<string, unknown>,
      });
      governanceMetrics.inc(METRIC_WITNESSED_RUN_STATUS, { status });

      // Enumerate exactly which lineage elements are missing (W3.05 acceptance:
      // queryable correlation to immutable envelope and receipt identities).
      const envelopeId = (envelope.id ?? envelope.envelope_id) as string | undefined;
      const fingerprint = envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint;
      const manifestId = (manifest.id ?? manifest.artifact_id ?? manifest.artifactId) as string | undefined;
      const evidenceIds = evidence.ids;
      const missing: string[] = [];
      if (!(envelopeId && fingerprint)) missing.push('envelope');
      if (!manifestId) missing.push('manifest');
      if (!row.peb_admission) missing.push('peb_admission_receipt');
      if (!row.conduit_transition) missing.push('conduit_transition_receipt');
      if (!evidenceIds || evidenceIds.length === 0) missing.push('evidence');

      // Correlation validity of the receipt references (W3.05 acceptance).
      const correlation = {
        pebAdmission: { id: row.peb_admission ?? null, valid: row.peb_admission ? isUuidShape(row.peb_admission) : null },
        conduitTransition: { id: row.conduit_transition ?? null, valid: row.conduit_transition ? isUuidShape(row.conduit_transition) : null },
      };
      for (const side of [correlation.pebAdmission, correlation.conduitTransition]) {
        if (side.id !== null && side.valid === false) governanceMetrics.inc(METRIC_RECEIPT_CORRELATION_INVALID);
      }

      res.json({
        projection: {
          workflow: { instanceId: workflowInstanceId, nodeId },
          request_id: row.request_id,
          status,
          missing_lineage_elements: missing,
          receipt_correlation: correlation,
          envelope_id: envelopeId ?? null,
          evaluation_fingerprint: fingerprint ?? null,
          replay_status: replay.status ?? null,
          assessment: { disposition: assessment.disposition ?? null, status: assessment.status ?? null },
        },
      });
    } catch (err) {
      sendError(res, err);
    }
  };
}

/**
 * W3.08 — Governed downstream projection (read-only, versioned).
 *
 * GET /api/execution/projections/witnessed-runs?workflow_instance_id=&node_id=
 *
 * A STABLE, VERSIONED projection surface for downstream consumers (including
 * projection-core). Contract:
 *  - `projectionVersion` is bumped only on breaking shape changes; consumers
 *    pin to it and fail closed on mismatch (see projection client).
 *  - Everything is SERVER-DERIVED: authoritative status via
 *    `classifyWitnessedRunStatus`, enumerated missing lineage elements,
 *    receipt correlation ids. No client-side authority reconstruction.
 *  - Identity correlation only — governance payloads are never included.
 *  - Read-only: SELECTs only, no write path.
 */
// Bump only on breaking shape changes to the projection payload (W3.08).
// v3 (ruling ffa4ffc5): phantom metadata legs removed — identity matches
// business_key, evidence/assessment re-pointed at resolution.*, and
// envelope/manifest/law/replay render null until the producer contract backs
// them. v2 (6677c394 R3) re-pointed the receipt slots; that mapping stays.
export const WITNESSED_RUN_PROJECTION_VERSION = 3;

export function witnessedRunProjectionHandler(pool: Pool) {
  return async (req: Request, res: Response) => {
    try {
      const workflowInstanceId = (req.query.workflow_instance_id as string | undefined)?.trim();
      const nodeId = (req.query.node_id as string | undefined)?.trim();
      if (!workflowInstanceId || !nodeId) return badRequest(res, 'workflow_instance_id and node_id are required');

      const { rows } = await pool.query(
        `SELECT
           r.id AS request_id,
           r.business_key AS workflow_instance_id,
           r.updated_at AS updated_at,
           -- v3 (ffa4ffc5): assessment + evidence columns — the projection
           -- renders them from the attempt's latest admission receipt joined to
           -- its immutable evidence row. These were omitted from this handler's
           -- SELECT in #226 (the other two witnessed-run handlers carry them),
           -- so assessment/evidenceIds rendered null for every receipt-bearing
           -- run. Exposed by the first marker adoption (#229 E2E).
           ar.admitted AS assessment_admitted,
           ar.reason AS assessment_reason,
           ar.source_system AS assessment_source_system,
           ar.policy_version_hash AS assessment_policy_hash,
           ev.id::text AS evidence_id,
           ev.source_hash AS evidence_fingerprint,
           ev.payload AS evidence_payload,
           -- Receipt correlation legs kept per ffa4ffc5 (mapping from 6677c394 R2/R3):
           --   peb_admission: the attempt's PEB admission receipt transaction id
           --   conduit_transition: the attempt's latest native EXECUTION_COMPLETE receipt
           --     (a live lane under chk_execution_receipts_type; the retired conduit
           --     types stay gone per R1 — the wind seam V151 is the eventual carrier)
           (SELECT ar2.peb_transaction_id::text
              FROM resolution.execution_admission_receipt ar2
             WHERE ar2.attempt_id = a.id::text
             ORDER BY ar2.created_at DESC
             LIMIT 1) AS peb_admission,
           (SELECT rec.id::text
              FROM receipts rec
             WHERE rec.attempt_id = a.id
               AND rec.type = 'EXECUTION_COMPLETE'
             ORDER BY rec.issued_at DESC
             LIMIT 1) AS conduit_transition
         FROM requests r
         LEFT JOIN LATERAL (
           SELECT * FROM attempts a0 WHERE a0.request_id = r.id ORDER BY a0.created_at DESC LIMIT 1
         ) a ON true
         -- v3 (ruling ffa4ffc5): assessment + evidence come from the attempt's
         -- latest admission receipt joined to its immutable evidence row.
         LEFT JOIN LATERAL (
           SELECT ar0.* FROM resolution.execution_admission_receipt ar0
           WHERE ar0.attempt_id = a.id::text
           ORDER BY ar0.created_at DESC
           LIMIT 1
         ) ar ON true
         LEFT JOIN resolution.execution_evidence ev ON ev.id = ar.evidence_id
         WHERE r.business_key = $1
         LIMIT 1`,
        // v3 (ffa4ffc5): node_id is output-echo only — it is no longer a query
        // parameter (the only SQL parameter is the business_key match).
        [workflowInstanceId],
      );
      if (rows.length === 0) return notFound(res, 'witnessed run not found');
      const row = rows[0];
      const envelope = {} as Record<string, unknown>;
      const manifest = {} as Record<string, unknown>;
      const assessment = buildAssessmentFromRow(row);
      const evidence = buildEvidenceFromRow(row);
      const replay = {} as Record<string, unknown>;

      const envelopeId = (envelope.id ?? envelope.envelope_id ?? null) as string | null;
      const evaluationFingerprint = (envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint ?? null) as string | null;
      const manifestId = (manifest.id ?? manifest.artifactId ?? manifest.artifact_id ?? null) as string | null;
      const evidenceIds = evidence.ids;
      const pebAdmission = row.peb_admission ?? null;
      const conduitTransition = row.conduit_transition ?? null;

      const status = classifyWitnessedRunStatus({
        envelope: envelope,
        manifest: manifest,
        assessment: assessment,
        replay: replay,
        row: { ...row, evidence } as Record<string, unknown>,
      });

      // Enumerate the missing lineage elements (server-derived diagnostics).
      const missingLineage: string[] = [];
      if (!envelopeId) missingLineage.push('envelope_id');
      if (!evaluationFingerprint) missingLineage.push('evaluation_fingerprint');
      if (!manifestId) missingLineage.push('manifest_id');
      if (!pebAdmission) missingLineage.push('peb_admission_receipt');
      if (!conduitTransition) missingLineage.push('conduit_transition_receipt');
      if (!evidenceIds || evidenceIds.length === 0) missingLineage.push('evidence_ids');

      res.set('Cache-Control', 'no-store');
      res.json({
        projectionVersion: WITNESSED_RUN_PROJECTION_VERSION,
        projection: 'witnessed-run',
        generatedAt: new Date().toISOString(),
        sourceUpdatedAt: row.updated_at ?? null,
        workflow: { instanceId: workflowInstanceId, nodeId },
        request: { id: row.request_id ?? null },
        identities: {
          envelopeId,
          evaluationFingerprint,
          manifestId,
          pebAdmissionReceiptId: pebAdmission,
          conduitTransitionReceiptId: conduitTransition,
          evidenceIds,
        },
        assessment: {
          disposition: assessment.disposition ?? null,
          status: assessment.status ?? null,
        },
        replay: { fixtureId: replay.fixtureId ?? replay.fixture_id ?? null, status: replay.status ?? null },
        status,
        missingLineage,
      });
    } catch (err) {
      sendError(res, err);
    }
  };
}

/**
 * Canonical witnessed-run state vocabulary, shared with the projection-core
 * (typescript/projection-core/src/runtime/witnessedRun.ts — `WitnessedRunStatus`).
 *
 * This classifier is the AUTHORITATIVE join-state derivation for the read-only
 * projection: the route calls it server-side and the Projection Manual Mode/provenance
 * consumers rely on its output rather than re-deriving the join in the browser
 * (AC4 — no browser-owned reconstruction).
 *
 * States:
 *  - complete:           full authority-relevant lineage present
 *  - missing_lineage:    SOME lineage present but a required element is absent
 *                        (envelope/manifest, PEB admission, Conduit transition,
 *                        or evidence) — a partial witnessed run
 *  - unknown:            row exists but carries NONE of the lineage elements that
 *                        would let us classify further (indeterminate)
 *  - stale:              replay/assessment marked stale
 *  - refusal:            assessment refused / disposition refuse
 *  - drift:              replay/assessment marked drift
 *  - duplicate_retry:    replay flagged duplicate retry
 */
export type WitnessedRunStatus =
  | 'complete'
  | 'missing_lineage'
  | 'unknown'
  | 'stale'
  | 'refusal'
  | 'drift'
  | 'duplicate_retry';

export function classifyWitnessedRunStatus(input: {
  envelope: Record<string, unknown>;
  manifest: Record<string, unknown>;
  assessment: Record<string, unknown>;
  replay: Record<string, unknown>;
  row: Record<string, unknown>;
}): WitnessedRunStatus {
  const { envelope, manifest, assessment, replay, row } = input;
  // Failure/edge states first — replay is authority-ranked for the conservative verdicts.
  if (replay.status === 'stale' || assessment.status === 'stale') return 'stale';
  if (replay.status === 'drift' || assessment.status === 'drift') return 'drift';
  if (replay.status === 'duplicate_retry') return 'duplicate_retry';
  if (assessment.status === 'refused' || assessment.disposition === 'refuse') return 'refusal';

  // Lineage completeness (AC1 complete join vs partial/missing lineage).
  //
  // Three-way distinction (PR #70 review finding: `unknown` was unreachable):
  //   - none of the lineage elements are present  -> 'unknown' (indeterminate:
  //     we cannot tell a blank row apart from a partial witnessed run, and the
  //     projection vocabulary reserves `unknown` for exactly this case)
  //   - some elements present but a required one is absent -> 'missing_lineage'
  //   - all elements present -> 'complete'
  const envelopeId = (envelope.id ?? envelope.envelope_id) as string | undefined;
  const fingerprint = envelope.evaluationFingerprint ?? envelope.evaluation_fingerprint;
  const manifestId =
    (manifest.id ?? manifest.artifact_id ?? manifest.artifactId) as string | undefined;
  const evidenceBlob = row.evidence as Record<string, unknown> | undefined;
  const evidenceIds = (evidenceBlob?.ids ?? evidenceBlob?.evidence_ids) as string[] | undefined;
  const hasEnvelope = Boolean(envelopeId && fingerprint);
  const hasManifest = Boolean(manifestId);
  const hasReceipts = Boolean(row.peb_admission && row.conduit_transition);
  const hasEvidence = Boolean(evidenceIds && evidenceIds.length > 0);

  if (!hasEnvelope && !hasManifest && !hasReceipts && !hasEvidence) return 'unknown';
  if (!hasEnvelope || !hasManifest || !hasReceipts || !hasEvidence) return 'missing_lineage';

  return 'complete';
}
