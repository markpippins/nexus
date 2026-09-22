import { Service, ServiceBroker, Context, Errors } from "moleculer";
import { Pool } from "pg";

/**
 * voyager — moleculer port of typescript/voyager-srv (read-only).
 *
 * PORTING CONTRACT (M1-style parity discipline):
 *   - Every action reproduces the incumbent's SQL *statement for statement*,
 *     including ordering, filter composition and per-endpoint pageSize defaults.
 *     Nothing is "improved" here: a reimplementation that quietly fixes the
 *     incumbent is not a port and cannot be cut over behind a drift gate.
 *   - Response shaping is identical: snake_case columns are camelCased, JS
 *     Dates become epoch millis, list envelopes are {items,total,page,pageSize}.
 *   - Error envelopes are identical: 404 -> {error:"<Thing> not found"},
 *     500 -> {error:"<db message>"}, 503 (health only) -> {status,message}.
 *
 * Self-contained by design: prod runs .ts source under the Node type-stripper,
 * which cannot resolve extensionless relative imports (same reason
 * moleculer/search keeps its helpers inline).
 *
 * Deliberate bug-for-bug parity — see README "Known incumbent oddities":
 * /entities/:id queries entity_drift by the URL id rather than the resolved
 * row id, unlike /entities/by-id/:entityId which uses the row id. Preserved.
 */

// Helpers are exported purely so the jest suite can assert their semantics
// without a database; the wire surface is the actions, not these.
export function toNumber(v: any, fallback: number): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export function camelCaseRow(row: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [key, value] of Object.entries(row)) {
    const camelKey = key.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
    if (value instanceof Date) {
      out[camelKey] = value.getTime();
    } else {
      out[camelKey] = value;
    }
  }
  return out;
}

export function camelCaseRows(rows: Record<string, any>[]): Record<string, any>[] {
  return rows.map(camelCaseRow);
}

/**
 * Page window for every list action — the incumbent's exact arithmetic:
 * page >= 1, pageSize clamped to [1, 100], per-endpoint default (20 for scan
 * epochs, 50 everywhere else). Extracted from the handlers only so the jest
 * suite can pin it; the values are identical to routes.ts.
 */
export function pageWindow(q: Record<string, any>, defaultPageSize: number) {
  const page = Math.max(1, toNumber(q.page, 1));
  const pageSize = Math.min(100, Math.max(1, toNumber(q.pageSize, defaultPageSize)));
  return { page, pageSize, offset: (page - 1) * pageSize };
}

function notFound(thing: string): Errors.MoleculerError {
  return new Errors.MoleculerError(`${thing} not found`, 404, "NOT_FOUND");
}

function dbFailure(err: any): Errors.MoleculerError {
  return new Errors.MoleculerError(err?.message ?? "internal error", 500, "DB_ERROR");
}

export default class VoyagerService extends Service {
  private pool!: Pool;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "voyager",

      created() {
        // Env names mirror typescript/voyager-srv/src/db.ts exactly so the same
        // .env / unit EnvironmentFile feeds either implementation unchanged.
        this.pool = new Pool({
          host: process.env.PG_HOST || process.env.PGHOST || "localhost",
          port: parseInt(process.env.PG_PORT || process.env.PGPORT || "5432", 10),
          user: process.env.PG_USER || process.env.PGUSER || "pguser",
          password: process.env.PG_PASSWORD || process.env.PGPASSWORD || "pgpass",
          database: process.env.PG_DB_NAME || process.env.PGDATABASE || "nexus",
          options: "-c search_path=voyager",
          max: 10,
          idleTimeoutMillis: 30000,
          connectionTimeoutMillis: 5000,
        });
      },

      async stopped() {
        if (this.pool) await this.pool.end().catch(() => undefined);
      },

      actions: {
        // ── health ───────────────────────────────────────────────────────
        health: {
          async handler() {
            try {
              const { rows } = await this.pool.query("SELECT 1 as ok");
              return { status: "ok", db: rows[0].ok === 1, service: "voyager-srv" };
            } catch (err: any) {
              // Incumbent returns 503 with a {status,message} envelope, not the
              // generic {error} shape — preserved in the gateway's error map.
              throw new Errors.MoleculerError(err.message, 503, "DB_UNAVAILABLE");
            }
          },
        },

        // ── scan epochs ──────────────────────────────────────────────────
        "scanEpochs.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, any>;
            const { page, pageSize, offset } = pageWindow(q, 20);
            try {
              const [countResult, { rows }] = await Promise.all([
                this.pool.query("SELECT COUNT(*)::int AS total FROM scan_epoch"),
                this.pool.query(
                  "SELECT * FROM scan_epoch ORDER BY started_at DESC LIMIT $1 OFFSET $2",
                  [pageSize, offset]
                ),
              ]);
              return {
                items: camelCaseRows(rows),
                total: countResult.rows[0].total,
                page,
                pageSize,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        "scanEpochs.get": {
          async handler(ctx: Context<{ id: string }>) {
            try {
              const { rows: [epoch] } = await this.pool.query(
                "SELECT * FROM scan_epoch WHERE id = $1",
                [ctx.params.id]
              );
              if (!epoch) throw notFound("Scan epoch");
              return camelCaseRow(epoch);
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── file observations ────────────────────────────────────────────
        "files.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, any>;
            const { page, pageSize, offset } = pageWindow(q, 50);
            const scanEpochId = q.scanEpochId;
            const pathFilter = q.path;
            const deviceId = q.deviceId;
            const inode = q.inode;

            const clauses: string[] = [];
            const vals: any[] = [];
            let i = 1;
            if (scanEpochId) { clauses.push(`scan_epoch_id = $${i++}`); vals.push(scanEpochId); }
            if (pathFilter) { clauses.push(`path ILIKE $${i++}`); vals.push(`%${pathFilter}%`); }
            if (deviceId) { clauses.push(`device_id = $${i++}`); vals.push(deviceId); }
            if (inode) { clauses.push(`inode = $${i++}`); vals.push(inode); }
            const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

            try {
              const [countResult, { rows }] = await Promise.all([
                this.pool.query(`SELECT COUNT(*)::int AS total FROM file_observation ${where}`, vals),
                this.pool.query(
                  `SELECT * FROM file_observation ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
                  [...vals, pageSize, offset]
                ),
              ]);
              return {
                items: camelCaseRows(rows),
                total: countResult.rows[0].total,
                page,
                pageSize,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // NOTE: route ordering (/by-id/:x before /:x) is an HTTP-layer concern —
        // see api.service.ts, where the aliases keep the incumbent's order.
        "files.byObservationId": {
          async handler(ctx: Context<{ observationId: string }>) {
            try {
              const { rows: [obs] } = await this.pool.query(
                "SELECT * FROM file_observation WHERE observation_id = $1",
                [ctx.params.observationId]
              );
              if (!obs) throw notFound("File observation");
              return camelCaseRow(obs);
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        "files.get": {
          async handler(ctx: Context<{ id: string }>) {
            try {
              const { rows: [obs] } = await this.pool.query(
                "SELECT * FROM file_observation WHERE id = $1",
                [ctx.params.id]
              );
              if (!obs) throw notFound("File observation");
              return camelCaseRow(obs);
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── directory observations ───────────────────────────────────────
        "directories.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, any>;
            const { page, pageSize, offset } = pageWindow(q, 50);
            const scanEpochId = q.scanEpochId;
            const pathFilter = q.path;

            const clauses: string[] = [];
            const vals: any[] = [];
            let i = 1;
            if (scanEpochId) { clauses.push(`scan_epoch_id = $${i++}`); vals.push(scanEpochId); }
            if (pathFilter) { clauses.push(`path ILIKE $${i++}`); vals.push(`%${pathFilter}%`); }
            const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

            try {
              const [countResult, { rows }] = await Promise.all([
                this.pool.query(`SELECT COUNT(*)::int AS total FROM directory_observation ${where}`, vals),
                this.pool.query(
                  `SELECT * FROM directory_observation ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
                  [...vals, pageSize, offset]
                ),
              ]);
              return {
                items: camelCaseRows(rows),
                total: countResult.rows[0].total,
                page,
                pageSize,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── topology signals ─────────────────────────────────────────────
        "signals.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, any>;
            const { page, pageSize, offset } = pageWindow(q, 50);
            const scanEpochId = q.scanEpochId;
            const structureType = q.structureType;

            const clauses: string[] = [];
            const vals: any[] = [];
            let i = 1;
            if (scanEpochId) { clauses.push(`scan_epoch_id = $${i++}`); vals.push(scanEpochId); }
            if (structureType) { clauses.push(`structure->>'type' = $${i++}`); vals.push(structureType); }
            const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

            try {
              const [countResult, { rows }] = await Promise.all([
                this.pool.query(`SELECT COUNT(*)::int AS total FROM topology_signal ${where}`, vals),
                this.pool.query(
                  `SELECT * FROM topology_signal ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
                  [...vals, pageSize, offset]
                ),
              ]);
              return {
                items: camelCaseRows(rows),
                total: countResult.rows[0].total,
                page,
                pageSize,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        "signals.get": {
          async handler(ctx: Context<{ id: string }>) {
            try {
              const { rows: [sig] } = await this.pool.query(
                "SELECT * FROM topology_signal WHERE id = $1",
                [ctx.params.id]
              );
              if (!sig) throw notFound("Topology signal");
              return camelCaseRow(sig);
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── observation edge hints ───────────────────────────────────────
        "edgeHints.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, any>;
            const { page, pageSize, offset } = pageWindow(q, 50);
            const evidenceType = q.evidenceType;
            const minConfidence = q.minConfidence;

            const clauses: string[] = [];
            const vals: any[] = [];
            let i = 1;
            if (evidenceType) { clauses.push(`evidence->>'type' = $${i++}`); vals.push(evidenceType); }
            if (minConfidence) { clauses.push(`confidence >= $${i++}`); vals.push(minConfidence); }
            const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

            try {
              const [countResult, { rows }] = await Promise.all([
                this.pool.query(`SELECT COUNT(*)::int AS total FROM observation_edge_hint ${where}`, vals),
                this.pool.query(
                  `SELECT * FROM observation_edge_hint ${where} ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
                  [...vals, pageSize, offset]
                ),
              ]);
              return {
                items: camelCaseRows(rows),
                total: countResult.rows[0].total,
                page,
                pageSize,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        // ── entities ─────────────────────────────────────────────────────
        "entities.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, any>;
            const { page, pageSize, offset } = pageWindow(q, 50);
            const minStability = q.minStability;
            const canonicalPath = q.canonicalPath;

            const clauses: string[] = [];
            const vals: any[] = [];
            let i = 1;
            if (minStability) { clauses.push(`stability_score >= $${i++}`); vals.push(minStability); }
            if (canonicalPath) { clauses.push(`state->>'canonical_path' ILIKE $${i++}`); vals.push(`%${canonicalPath}%`); }
            const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

            try {
              const [countResult, { rows }] = await Promise.all([
                this.pool.query(`SELECT COUNT(*)::int AS total FROM entity ${where}`, vals),
                this.pool.query(
                  `SELECT * FROM entity ${where} ORDER BY stability_score DESC LIMIT $${i} OFFSET $${i + 1}`,
                  [...vals, pageSize, offset]
                ),
              ]);
              return {
                items: camelCaseRows(rows),
                total: countResult.rows[0].total,
                page,
                pageSize,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        "entities.byEntityId": {
          async handler(ctx: Context<{ entityId: string }>) {
            try {
              const { rows: [ent] } = await this.pool.query(
                "SELECT * FROM entity WHERE entity_id = $1",
                [ctx.params.entityId]
              );
              if (!ent) throw notFound("Entity");
              const { rows: drifts } = await this.pool.query(
                "SELECT * FROM entity_drift WHERE entity_id = $1 ORDER BY discovered_at DESC",
                [ent.id]
              );
              return { ...camelCaseRow(ent), drifts: camelCaseRows(drifts) };
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        "entities.get": {
          async handler(ctx: Context<{ id: string }>) {
            try {
              const { rows: [ent] } = await this.pool.query(
                "SELECT * FROM entity WHERE id = $1",
                [ctx.params.id]
              );
              if (!ent) throw notFound("Entity");
              // PARITY: the incumbent filters drifts by the URL id here (not
              // ent.id as the by-id handler does). Preserved deliberately.
              const { rows: drifts } = await this.pool.query(
                "SELECT * FROM entity_drift WHERE entity_id = $1 ORDER BY discovered_at DESC",
                [ctx.params.id]
              );
              return { ...camelCaseRow(ent), drifts: camelCaseRows(drifts) };
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── metadata spans ───────────────────────────────────────────────
        "spans.list": {
          async handler(ctx: Context<any>) {
            const q = (ctx.params ?? {}) as Record<string, any>;
            const { page, pageSize, offset } = pageWindow(q, 50);
            const spanType = q.spanType;
            const markdownRole = q.markdownRole;
            const minConfidence = q.minConfidence;
            const observationId = q.observationId;

            const clauses: string[] = [];
            const vals: any[] = [];
            let i = 1;
            if (spanType) { clauses.push(`span_type = $${i++}`); vals.push(spanType); }
            if (markdownRole) { clauses.push(`markdown_role = $${i++}`); vals.push(markdownRole); }
            if (minConfidence) { clauses.push(`confidence >= $${i++}`); vals.push(minConfidence); }
            if (observationId) { clauses.push(`observation_id = $${i++}`); vals.push(observationId); }
            const where = clauses.length > 0 ? `WHERE ${clauses.join(" AND ")}` : "";

            // Explicit projection (not SELECT *) — matches the incumbent.
            const cols = `id, span_id, observation_id, span_type, text, start_pos, end_pos,
                  confidence, markdown_role, discourse_role, event_candidate,
                  provenance, discovered_at`;

            try {
              const [countResult, { rows }] = await Promise.all([
                this.pool.query(`SELECT COUNT(*)::int AS total FROM metadata_span ${where}`, vals),
                this.pool.query(
                  `SELECT ${cols} FROM metadata_span ${where}
           ORDER BY discovered_at DESC LIMIT $${i} OFFSET $${i + 1}`,
                  [...vals, pageSize, offset]
                ),
              ]);
              return {
                items: camelCaseRows(rows),
                total: countResult.rows[0].total,
                page,
                pageSize,
              };
            } catch (err: any) {
              throw dbFailure(err);
            }
          },
        },

        "spans.get": {
          async handler(ctx: Context<{ id: string }>) {
            try {
              const { rows: [span] } = await this.pool.query(
                `SELECT id, span_id, observation_id, span_type, text, start_pos, end_pos,
                confidence, markdown_role, discourse_role, event_candidate,
                provenance, discovered_at
         FROM metadata_span WHERE id = $1`,
                [ctx.params.id]
              );
              if (!span) throw notFound("Metadata span");
              return camelCaseRow(span);
            } catch (err: any) {
              if (err?.code === 404) throw err;
              throw dbFailure(err);
            }
          },
        },

        // ── stats ────────────────────────────────────────────────────────
        stats: {
          async handler() {
            const queries: [string, string][] = [
              ["file_observations", "SELECT COUNT(*)::int FROM file_observation"],
              ["directory_observations", "SELECT COUNT(*)::int FROM directory_observation"],
              ["topology_signals", "SELECT COUNT(*)::int FROM topology_signal"],
              ["edge_hints", "SELECT COUNT(*)::int FROM observation_edge_hint"],
              ["metadata_spans", "SELECT COUNT(*)::int FROM metadata_span"],
              ["scan_epochs", "SELECT COUNT(*)::int FROM scan_epoch"],
              ["latest_epoch", "SELECT id, status, started_at FROM scan_epoch ORDER BY started_at DESC LIMIT 1"],
              ["span_types", "SELECT span_type, COUNT(*)::int FROM metadata_span GROUP BY span_type ORDER BY count DESC"],
            ];

            const stats: Record<string, any> = {};
            for (const [key, sql] of queries) {
              try {
                const { rows } = await this.pool.query(sql);
                if (key === "span_types") {
                  stats[key] = rows;
                } else if (key === "latest_epoch") {
                  stats[key] = rows.length > 0 ? camelCaseRow(rows[0]) : null;
                } else {
                  stats[key] = rows[0]?.count ?? rows[0]?.count ?? 0;
                }
              } catch {
                stats[key] = null;
              }
            }
            return stats;
          },
        },
      },
    });
  }
}
