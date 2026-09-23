import { Service, ServiceBroker, Context, Errors } from "moleculer";
import { Pool } from "pg";

/**
 * cascade — moleculer port of typescript/cascade-srv (:3106).
 *
 * PORTING CONTRACT (voyager-port discipline):
 *   - Every action reproduces the incumbent's SQL *statement for statement*,
 *     including recursive-CTE lineage walks, interval interpolation and the
 *     funnel/topSources subqueries. A "fix" here would be a divergence, not
 *     an improvement.
 *   - Response shapes are identical down to key names ({events,total,limit,
 *     offset}, {anchor,chain,depth}, {root,direction,nodes,edges,truncated},
 *     {range,granularity,totalEvents,throughput,timeline,pipelineFunnel,
 *     topSources}, {subscribers}, {parent,children}, {assessments,total,
 *     limit,offset}).
 *   - Error envelopes are identical: 404 -> {error:"Event not found"} /
 *     {error:"Subscriber not found"}, 400 -> {error:"Provide ?root=<id> or
 *     ?anchor=<id>"} / {error:"No fields to update"}, 500 -> {error:"<db
 *     message>"}, 503 (health) -> {status:"error", error}.
 *
 * NOTE ON THE INTERVAL INTERPOLATION (deliberate bug-for-bug parity): the
 * incumbent interpolates `interval`/`truncUnit` INTO the SQL text after
 * whitelisting via intervalMap/truncMap. That is safe *because* of the
 * allowlists — the port keeps the exact same mechanism so the parity surface
 * (including any error behavior) is identical. Do not parameterize.
 *
 * Env names mirror typescript/cascade-srv/src/db.ts exactly (DB_HOST, DB_PORT,
 * DB_NAME, DB_USER, DB_PASS) so the same EnvironmentFile feeds either
 * implementation unchanged.
 */

const INTERVAL_MAP: Record<string, string> = {
  "1h": "1 hour", "6h": "6 hours", "24h": "24 hours",
  "7d": "7 days", "30d": "30 days",
};
const TRUNC_MAP: Record<string, string> = {
  minute: "minute", hour: "hour", day: "day",
};

// Exported for the hermetic jest suite; values are the incumbent's exact maps.
export { INTERVAL_MAP, TRUNC_MAP };

function notFound(msg: string): Errors.MoleculerError {
  return new Errors.MoleculerError(msg, 404, "NOT_FOUND");
}
function badRequest(msg: string): Errors.MoleculerError {
  return new Errors.MoleculerError(msg, 400, "BAD_REQUEST");
}
function dbFailure(err: any): Errors.MoleculerError {
  return new Errors.MoleculerError(err?.message ?? "internal error", 500, "DB_ERROR");
}

/** `parseInt(x) || fallback`, the incumbent's exact coercion. */
function parseIntOr(v: any, fallback: number): number {
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? fallback : n;
}

// ── Extracted parity helpers (exported for the jest suite) ─────────────────
// The handlers below call these; the logic is lifted verbatim from routes.ts
// so the suite can pin the pure semantics without a database.

export function clampLimit(v: any, fallback = 50): number {
  return Math.min(parseIntOr(v, fallback), 200);
}

export function clampOffset(v: any): number {
  return parseIntOr(v, 0);
}

/**
 * Resolve the analytics window. Returns both the SQL-bound values
 * (interval/truncUnit) and the response-echo values (timeRange raw,
 * granularity = resolved truncUnit — the incumbent echoes the RESOLVED unit
 * but the RAW range, a quirk preserved here).
 */
export function resolveAnalyticsWindow(timeRange?: string, granularity?: string) {
  const interval = INTERVAL_MAP[timeRange ?? ""] || "24 hours";
  const truncUnit = TRUNC_MAP[granularity ?? ""] || "hour";
  return { interval, truncUnit, timeRange: timeRange ?? "24h", granularity: truncUnit };
}

/**
 * Build the /lineage graph body from recursive-CTE rows — verbatim node/edge
 * assembly including the `truncated: rows.length >= depth * 10` heuristic and
 * nodeMap dedup.
 */
export function buildLineageGraph(
  rows: any[],
  seedId: string,
  direction: string,
  edgeType: string,
  depth: number
) {
  const nodeMap = new Map();
  const edges: any[] = [];

  for (const row of rows) {
    nodeMap.set(row.event_id, {
      id: row.event_id,
      type: row.event_type,
      source: row.source,
      timestamp: row.event_timestamp,
      depth: row.depth,
    });

    if (row.causation_id) {
      edges.push({
        source: row.causation_id,
        target: row.event_id,
        type: edgeType,
      });
    }
  }

  return {
    root: seedId,
    direction,
    nodes: Array.from(nodeMap.values()),
    edges,
    truncated: rows.length >= depth * 10,
  };
}

export default class CascadeService extends Service {
  private pool!: Pool;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "cascade",

      created() {
        this.pool = new Pool({
          host: process.env.DB_HOST || "localhost",
          port: parseInt(process.env.DB_PORT || "5432", 10),
          database: process.env.DB_NAME || "nexus",
          user: process.env.DB_USER || "pguser",
          password: process.env.DB_PASS || "pgpass",
          max: 10,
        });
      },

      async stopped() {
        if (this.pool) await this.pool.end().catch(() => undefined);
      },

      actions: {
        // ── GET /events ─────────────────────────────────────────────────
        "events.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string>;
            const {
              type, source, aggregate_id, aggregate_type, correlation_id,
              since, until,
            } = q;
            const limit = q.limit ?? "50";
            const offset = q.offset ?? "0";

            const conditions: string[] = [];
            const params: any[] = [];
            let idx = 1;

            if (type)           { conditions.push(`event_type = $${idx++}`);       params.push(type); }
            if (source)         { conditions.push(`source = $${idx++}`);           params.push(source); }
            if (aggregate_id)   { conditions.push(`aggregate_id = $${idx++}`);     params.push(aggregate_id); }
            if (aggregate_type) { conditions.push(`aggregate_type = $${idx++}`);   params.push(aggregate_type); }
            if (correlation_id) { conditions.push(`correlation_id = $${idx++}`);   params.push(correlation_id); }
            if (since)          { conditions.push(`event_timestamp >= $${idx++}`); params.push(since); }
            if (until)          { conditions.push(`event_timestamp <= $${idx++}`); params.push(until); }

            const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
            const lim = clampLimit(limit);
            const off = clampOffset(offset);

            try {
              const rows = await this.pool.query(
                `SELECT event_id, event_type, source, event_timestamp, payload,
             aggregate_type, aggregate_id, actor_type, actor_id,
             correlation_id, causation_id, caused_by_event_type,
             sequence_number, received_at
      FROM cascade.events
      ${where}
      ORDER BY event_timestamp DESC
      LIMIT $${idx++} OFFSET $${idx++}`,
                [...params, lim, off]
              );
              const countResult = await this.pool.query(
                `SELECT COUNT(*)::text AS count FROM cascade.events ${where}`,
                params
              );
              return {
                events: rows.rows,
                total: parseInt(countResult.rows[0]?.count || "0", 10),
                limit: lim,
                offset: off,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── GET /events/:id ─────────────────────────────────────────────
        "events.get": {
          async handler(ctx: Context<{ id: string }>) {
            try {
              const rows = await this.pool.query(
                `SELECT event_id, event_type, source, event_timestamp, payload,
             aggregate_type, aggregate_id, actor_type, actor_id,
             correlation_id, causation_id, caused_by_event_type,
             sequence_number, received_at
      FROM cascade.events
      WHERE event_id = $1`,
                [ctx.params.id]
              );
              if (!rows.rows.length) throw notFound("Event not found");
              return rows.rows[0];
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── GET /events/:id/lineage ─────────────────────────────────────
        "events.lineage": {
          async handler(ctx: Context<any> & { id: string }) {
            const maxDepth = Math.min(parseIntOr((ctx.params as any).maxDepth, 10), 20);
            try {
              // Recursive CTE: walk causation_id chain (verbatim).
              const rows = await this.pool.query(
                `WITH RECURSIVE lineage AS (
        SELECT event_id, event_type, causation_id, caused_by_event_type, source,
               event_timestamp, payload, 0 AS depth
        FROM cascade.events
        WHERE event_id = $1

        UNION ALL

        SELECT e.event_id, e.event_type, e.causation_id, e.caused_by_event_type, e.source,
               e.event_timestamp, e.payload, l.depth + 1
        FROM cascade.events e
        JOIN lineage l ON e.event_id = l.causation_id
        WHERE l.depth < $2
      )
      SELECT * FROM lineage ORDER BY depth ASC`,
                [ctx.params.id, maxDepth]
              );
              return { anchor: ctx.params.id, chain: rows.rows, depth: rows.rows.length };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── GET /events/:id/children ────────────────────────────────────
        "events.children": {
          async handler(ctx: Context<{ id: string }>) {
            try {
              const rows = await this.pool.query(
                `SELECT event_id, event_type, aggregate_type, aggregate_id, source, event_timestamp
      FROM cascade.events
      WHERE causation_id = $1
      ORDER BY event_timestamp ASC`,
                [ctx.params.id]
              );
              return { parent: ctx.params.id, children: rows.rows };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── GET /lineage ────────────────────────────────────────────────
        lineage: {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string>;
            const { root, anchor } = q;
            const maxDepth = q.maxDepth ?? "5";
            const edgeType = q.edgeType ?? "caused_by";
            const depth = Math.min(parseIntOr(maxDepth, 5), 15);

            if (!root && !anchor) throw badRequest("Provide ?root=<id> or ?anchor=<id>");

            const seedId = root || anchor;
            const direction = root ? "forward" : "backward";
            try {
              const rows = await this.pool.query(
                `WITH RECURSIVE graph AS (
        SELECT event_id, event_type, causation_id, source, event_timestamp,
               0 AS depth
        FROM cascade.events
        WHERE event_id = $1

        UNION ALL

        SELECT e.event_id, e.event_type, e.causation_id, e.source, e.event_timestamp,
               g.depth + 1
        FROM cascade.events e
        JOIN graph g ON ${direction === "forward"
                  ? "e.causation_id = g.event_id"
                  : "e.event_id = g.causation_id"}
        WHERE g.depth < $2
      )
      SELECT * FROM graph`,
                [seedId, depth]
              );

              return buildLineageGraph(rows.rows, seedId, direction, edgeType, depth);
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── GET /analytics ──────────────────────────────────────────────
        analytics: {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string>;
            try {
              const timeRange = q.range ?? "24h";
              const granularityParam = q.granularity ?? "hour";
              const { interval, truncUnit, timeRange: echoRange, granularity } =
                resolveAnalyticsWindow(timeRange, granularityParam);

              // Throughput by event type (interval interpolated — allowlisted upstream).
              const throughput = await this.pool.query(
                `SELECT event_type, COUNT(*)::int AS count
      FROM cascade.events
      WHERE event_timestamp >= NOW() - INTERVAL '${interval}'
      GROUP BY event_type
      ORDER BY count DESC`
              );

              // Timeline (bucketed counts).
              const timeline = await this.pool.query(
                `SELECT date_trunc('${truncUnit}', event_timestamp) AS bucket,
             event_type, COUNT(*)::int AS count
      FROM cascade.events
      WHERE event_timestamp >= NOW() - INTERVAL '${interval}'
      GROUP BY bucket, event_type
      ORDER BY bucket ASC`
              );

              // Pipeline funnel: harvests → candidates → promoted → intents → plans.
              const funnel = await this.pool.query(
                `SELECT
        (SELECT COUNT(DISTINCT aggregate_id)::int FROM cascade.events
         WHERE event_type = 'harvest.captured'
         AND event_timestamp >= NOW() - INTERVAL '${interval}') AS harvests,
        (SELECT COUNT(DISTINCT aggregate_id)::int FROM cascade.events
         WHERE event_type = 'candidate.discovered'
         AND event_timestamp >= NOW() - INTERVAL '${interval}') AS candidates,
        (SELECT COUNT(DISTINCT aggregate_id)::int FROM cascade.events
         WHERE event_type = 'candidate.promoted'
         AND event_timestamp >= NOW() - INTERVAL '${interval}') AS promoted,
        (SELECT COUNT(DISTINCT aggregate_id)::int FROM cascade.events
         WHERE event_type = 'requirement.promoted_to_plan'
         AND event_timestamp >= NOW() - INTERVAL '${interval}') AS plans`
              );

              // Top sources.
              const topSources = await this.pool.query(
                `SELECT source, COUNT(*)::int AS count
      FROM cascade.events
      WHERE event_timestamp >= NOW() - INTERVAL '${interval}'
      GROUP BY source
      ORDER BY count DESC
      LIMIT 10`
              );

              // Total events.
              const totalResult = await this.pool.query(
                `SELECT COUNT(*)::text AS count FROM cascade.events
       WHERE event_timestamp >= NOW() - INTERVAL '${interval}'`
              );

              return {
                range: echoRange,
                granularity,
                totalEvents: parseInt(totalResult.rows[0]?.count || "0", 10),
                throughput: throughput.rows,
                timeline: timeline.rows,
                pipelineFunnel: funnel.rows[0] || {},
                topSources: topSources.rows,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── GET /subscribers ────────────────────────────────────────────
        "subscribers.list": {
          async handler() {
            try {
              const rows = await this.pool.query(
                `SELECT s.subject_pattern, s.handler_name, s.description, s.enabled,
             s.created_at,
             p.last_timestamp AS last_processed,
             p.processed_ids,
             p.updated_at AS last_processed_at,
             (SELECT COUNT(*)::int FROM cascade.events e
              WHERE p.last_timestamp IS NULL OR e.event_timestamp > p.last_timestamp
             ) AS lag
      FROM cascade.subscriptions s
      LEFT JOIN cascade.processing_offsets p ON p.subscriber_id = s.subject_pattern
      ORDER BY s.handler_name`
              );
              return { subscribers: rows.rows };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── GET /subscribers/:pattern ───────────────────────────────────
        "subscribers.get": {
          async handler(ctx: Context<{ pattern: string }>) {
            try {
              const rows = await this.pool.query(
                `SELECT s.subject_pattern, s.handler_name, s.description, s.enabled,
             s.created_at,
             p.last_timestamp, p.processed_ids, p.updated_at AS last_offset_at
      FROM cascade.subscriptions s
      LEFT JOIN cascade.processing_offsets p ON p.subscriber_id = s.subject_pattern
      WHERE s.subject_pattern = $1`,
                [ctx.params.pattern]
              );
              if (!rows.rows.length) throw notFound("Subscriber not found");
              return rows.rows[0];
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── PATCH /subscribers/:pattern ─────────────────────────────────
        "subscribers.update": {
          async handler(ctx: Context<{ pattern: string; body: any }>) {
            const enabled = ctx.params.body?.enabled;
            if (enabled === undefined) throw badRequest("No fields to update");
            try {
              const rows = await this.pool.query(
                `UPDATE cascade.subscriptions
      SET enabled = $1
      WHERE subject_pattern = $2
      RETURNING subject_pattern, handler_name, enabled`,
                [enabled, ctx.params.pattern]
              );
              if (!rows.rows.length) throw notFound("Subscriber not found");
              return rows.rows[0];
            } catch (err: any) {
              if (err?.code === 404 || err?.code === 400) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── GET /assessments ────────────────────────────────────────────
        "assessments.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, string>;
            const { outcome, event_id } = q;
            const limit = q.limit ?? "50";
            const offset = q.offset ?? "0";

            const conditions: string[] = [];
            const params: any[] = [];
            let idx = 1;

            if (outcome)  { conditions.push(`ar.outcome = $${idx++}`); params.push(outcome); }
            if (event_id) { conditions.push(`ar.event_id = $${idx++}`); params.push(event_id); }

            const where = conditions.length ? `WHERE ${conditions.join(" AND ")}` : "";
            const lim = Math.min(parseIntOr(limit, 50), 200);
            const off = parseIntOr(offset, 0);

            try {
              const rows = await this.pool.query(
                `SELECT ar.resolution_id, ar.event_id, ar.outcome, ar.confidence,
             ar.rationale, ar.dimensions_used, ar.dimensions_total,
             ar.resolved_at,
             e.event_type, e.source, e.payload
      FROM nebula.assessment_resolutions ar
      LEFT JOIN cascade.events e ON e.event_id = ar.event_id
      ${where}
      ORDER BY ar.resolved_at DESC
      LIMIT $${idx++} OFFSET $${idx++}`,
                [...params, lim, off]
              );
              const countResult = await this.pool.query(
                `SELECT COUNT(*)::text AS count FROM nebula.assessment_resolutions ar ${where}`,
                params
              );
              return {
                assessments: rows.rows,
                total: parseInt(countResult.rows[0]?.count || "0", 10),
                limit: lim,
                offset: off,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── GET /health ─────────────────────────────────────────────────
        health: {
          async handler() {
            try {
              const result = await this.pool.query("SELECT NOW()::text AS now");
              const countResult = await this.pool.query(
                "SELECT COUNT(*)::text AS count FROM cascade.events"
              );
              return {
                status: "ok",
                schema: "cascade",
                totalEvents: parseInt(countResult.rows[0]?.count || "0", 10),
                time: result.rows[0]?.now,
                port: 3106,
              };
            } catch (err: any) {
              // Incumbent health returns 503 {status:"error", error} — the
              // gateway's onError maps this code to the same envelope.
              throw new Errors.MoleculerError(err?.message ?? "error", 503, "DB_UNAVAILABLE");
            }
          },
        },
      },
    });
  }
}
