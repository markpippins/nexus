import { Service, ServiceBroker, Context, Errors } from "moleculer";
import { Pool } from "pg";
import {
  subscribe,
  startNotifyListener,
  stopNotifyListener,
  isNotifyAlive,
  getSubscriberCount,
  type KernelEvent,
} from "./notify";

/**
 * kernel — moleculer port of typescript/kernel-srv (:8100).
 *
 * PORTING CONTRACT (voyager/cascade discipline):
 *   - Every action reproduces the incumbent's SQL *statement for statement*,
 *     including the sys_transition()/sys_issue_receipt() named-argument calls,
 *     the v_causality_chain path-array containment filter and the
 *     receipt-integrity orphan LEFT JOIN. A "fix" here is divergence.
 *   - Envelope parity (incumbent routes.ts helpers):
 *       400 -> {status:"error", message}
 *       404 -> {status:"error", message}
 *       403 -> {status:"error", message}      (PG 45000 from kernel triggers)
 *       500 -> {status:"error", code:"<KERNEL_*_FAILED>", message}
 *       health 503 -> {status:"error", message}
 *   - Success envelopes identical: 201 raw row; reads exactly as routes.ts.
 *
 * SSE NOTE: /api/kernel/events/stream is an SSE endpoint backed by the
 * pg_notify LISTEN loop (notify.ts, ported verbatim). moleculer-web can serve
 * it through an alias to the `kernel.eventsStream` action which returns a
 * Node Readable stream — moleculer-web pipes returned streams to the response.
 *
 * Env names mirror typescript/kernel-srv/src/index.ts exactly (DB_HOST,
 * DB_PORT, DB_USER, DB_PASS, DB_NAME; no search_path pinning — kernel.* is
 * fully qualified in every statement, as in the incumbent).
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isValidUuid(s: string): boolean {
  return UUID_RE.test(s);
}

export function badRequest(message: string): Errors.MoleculerError {
  return new Errors.MoleculerError(message, 400, "BAD_REQUEST");
}
export function notFound(message: string): Errors.MoleculerError {
  return new Errors.MoleculerError(message, 404, "NOT_FOUND");
}
export function forbidden(message: string): Errors.MoleculerError {
  return new Errors.MoleculerError(message, 403, "FORBIDDEN");
}
export function serverError(message: string, code: string): Errors.MoleculerError {
  return new Errors.MoleculerError(message, 500, code);
}
export function dbFailure(err: unknown, code: string): Errors.MoleculerError {
  const e = err as { code?: string; message?: string };
  if (e && typeof e === "object" && e.code === "45000") return forbidden(e.message ?? "forbidden");
  const msg = e && typeof e === "object" && e.message ? e.message : String(err);
  return serverError(msg, code);
}

export function clamp(v: number, min: number, max: number): number {
  if (Number.isNaN(v)) return min;
  return Math.max(min, Math.min(max, v));
}

export default class KernelService extends Service {
  private pool!: Pool;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "kernel",

      created() {
        this.pool = new Pool({
          host: process.env.DB_HOST || "localhost",
          port: parseInt(process.env.DB_PORT || "5432", 10),
          user: process.env.DB_USER || "pguser",
          password: process.env.DB_PASS || "pgpass",
          database: process.env.DB_NAME || "nexus",
          max: 10,
          idleTimeoutMillis: 30000,
          connectionTimeoutMillis: 5000,
        });
        // Same convention as the incumbent index.ts: start the pg_notify
        // LISTEN loop on the shared pool (kernel_transition_committed).
        startNotifyListener(this.pool);
      },

      async stopped() {
        await stopNotifyListener();
        if (this.pool) await this.pool.end().catch(() => undefined);
      },

      actions: {
        // ── 1. POST /transitions — wraps kernel.sys_transition() ────────
        "transitions.create": {
          async handler(ctx: Context<any>) {
            const b = (ctx.params.body ?? ctx.params) as Record<string, any>;
            const required: string[] = ["event_type", "aggregate_type", "aggregate_id", "actor"];
            for (const f of required) {
              if (b[f] === undefined || b[f] === null || String(b[f]).trim() === "") {
                throw badRequest(`Missing required field: ${f}`);
              }
            }
            try {
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.sys_transition(
            p_event_type     := $1::kernel.event_type,
            p_aggregate_type := $2,
            p_aggregate_id   := $3,
            p_actor          := $4,
            p_payload        := $5::jsonb,
            p_authority      := $6,
            p_receipt        := $7,
            p_causation_id   := $8::uuid,
            p_correlation_id := $9::uuid
        );`,
                [
                  b.event_type,
                  b.aggregate_type,
                  b.aggregate_id,
                  b.actor,
                  JSON.stringify(b.payload ?? {}),
                  b.authority ?? null,
                  b.receipt ?? null,
                  b.causation_id ?? null,
                  b.correlation_id ?? null,
                ]
              );
              (ctx.meta as any).$statusCode = 201;
              return rows[0];
            } catch (err: unknown) {
              throw dbFailure(err, "KERNEL_WRITE_FAILED");
            }
          },
        },

        // ── 2. GET /transitions/:event_id ───────────────────────────────
        "transitions.get": {
          async handler(ctx: Context<{ event_id: string }>) {
            const event_id = String(ctx.params.event_id);
            if (!isValidUuid(event_id)) throw badRequest("event_id must be a UUID");
            try {
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.transition_event WHERE event_id = $1::uuid`,
                [event_id]
              );
              if (rows.length === 0) throw notFound(`No transition_event for event_id ${event_id}`);
              return rows[0];
            } catch (err: unknown) {
              if ((err as any)?.code === 404 || (err as any)?.code === 400) throw err;
              throw dbFailure(err, "KERNEL_READ_FAILED");
            }
          },
        },

        // ── 3. GET /transitions/:event_id/causality ─────────────────────
        "transitions.causality": {
          async handler(ctx: Context<{ event_id: string }>) {
            const event_id = String(ctx.params.event_id);
            if (!isValidUuid(event_id)) throw badRequest("event_id must be a UUID");
            try {
              // Confirm existence first so 404 is clean (verbatim).
              const exists = await this.pool.query(
                `SELECT 1 FROM kernel.transition_event WHERE event_id = $1::uuid`,
                [event_id]
              );
              if (exists.rows.length === 0) throw notFound(`No transition_event for event_id ${event_id}`);
              // The view re-runs the recursive CTE; filter on the path column.
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.v_causality_chain
         WHERE path @> ARRAY[$1::text]
         ORDER BY depth;`,
                [event_id]
              );
              return {
                root_event_id: event_id,
                chain: rows,
                depth: rows.length > 0 ? Math.max(...rows.map((r: any) => r.depth)) : 0,
              };
            } catch (err: unknown) {
              if ((err as any)?.code === 404 || (err as any)?.code === 400) throw err;
              throw dbFailure(err, "KERNEL_READ_FAILED");
            }
          },
        },

        // ── 4. POST /receipts — wraps kernel.sys_issue_receipt() ────────
        "receipts.create": {
          async handler(ctx: Context<any>) {
            const b = (ctx.params.body ?? ctx.params) as Record<string, any>;
            const required: string[] = ["receipt_type", "receipt_hash", "event_id", "issued_by"];
            for (const f of required) {
              if (b[f] === undefined || b[f] === null || String(b[f]).trim() === "") {
                throw badRequest(`Missing required field: ${f}`);
              }
            }
            try {
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.sys_issue_receipt(
            p_receipt_type := $1,
            p_receipt_hash := $2,
            p_event_id     := $3::uuid,
            p_issued_by    := $4,
            p_plan_number  := $5,
            p_metadata     := $6::jsonb
        );`,
                [
                  b.receipt_type,
                  b.receipt_hash,
                  b.event_id,
                  b.issued_by,
                  b.plan_number ?? null,
                  JSON.stringify(b.metadata ?? {}),
                ]
              );
              (ctx.meta as any).$statusCode = 201;
              return rows[0];
            } catch (err: unknown) {
              throw dbFailure(err, "RECEIPT_ISSUE_FAILED");
            }
          },
        },

        // ── 5. GET /receipts/:id/chain — wraps v_receipt_chain ──────────
        "receipts.chain": {
          async handler(ctx: Context<{ id: string }>) {
            const id = String(ctx.params.id);
            if (!isValidUuid(id)) throw badRequest("id must be a UUID");
            try {
              const start = await this.pool.query(`SELECT * FROM kernel.receipt WHERE id = $1::uuid`, [id]);
              if (start.rows.length === 0) throw notFound(`No receipt for id ${id}`);
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.v_receipt_chain WHERE event_id = $1::uuid ORDER BY receipt_created_at;`,
                [start.rows[0].event_id]
              );
              return {
                receipt_id: id,
                event_id: start.rows[0].event_id,
                chain: rows,
              };
            } catch (err: unknown) {
              if ((err as any)?.code === 404 || (err as any)?.code === 400) throw err;
              throw dbFailure(err, "RECEIPT_READ_FAILED");
            }
          },
        },

        // ── 6. GET /plans/:plan_number/receipts — wraps v_plan_receipts ─
        "plans.receipts": {
          async handler(ctx: Context<{ plan_number: string }>) {
            const plan_number = String(ctx.params.plan_number);
            if (!plan_number || plan_number.trim() === "") throw badRequest("plan_number required");
            try {
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.v_plan_receipts WHERE plan_number = $1;`,
                [plan_number]
              );
              if (rows.length === 0) throw notFound(`No receipts found for plan_number ${plan_number}`);
              return {
                plan_number,
                summary: rows[0],
                chains: rows,
              };
            } catch (err: unknown) {
              if ((err as any)?.code === 404 || (err as any)?.code === 400) throw err;
              throw dbFailure(err, "PLAN_RECEIPTS_READ_FAILED");
            }
          },
        },

        // ── 7. GET /aggregates/:type/:id/events — wraps v_aggregate_events
        "aggregates.events": {
          async handler(ctx: Context<{ aggregate_type: string; aggregate_id: string }>) {
            const aggregate_type = String(ctx.params.aggregate_type);
            const aggregate_id = String(ctx.params.aggregate_id);
            if (!aggregate_type || !aggregate_id) {
              throw badRequest("aggregate_type and aggregate_id required");
            }
            try {
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.v_aggregate_events
         WHERE aggregate_type = $1 AND aggregate_id = $2;`,
                [aggregate_type, aggregate_id]
              );
              if (rows.length === 0) {
                throw notFound(`No events for aggregate ${aggregate_type}/${aggregate_id}`);
              }
              return { aggregate_type, aggregate_id, aggregates: rows[0] };
            } catch (err: unknown) {
              if ((err as any)?.code === 404 || (err as any)?.code === 400) throw err;
              throw dbFailure(err, "AGGREGATE_READ_FAILED");
            }
          },
        },

        // ── 8. GET /policy/active — wraps v_active_policy ───────────────
        "policy.active": {
          async handler() {
            try {
              const { rows } = await this.pool.query(`SELECT * FROM kernel.v_active_policy ORDER BY priority;`);
              return { active_rules: rows, count: rows.length };
            } catch (err: unknown) {
              throw dbFailure(err, "POLICY_READ_FAILED");
            }
          },
        },

        // ── 9. GET /policy/maturity ─────────────────────────────────────
        "policy.maturity": {
          async handler() {
            try {
              const { rows } = await this.pool.query(`SELECT * FROM kernel.v_policy_maturity;`);
              return (
                rows[0] ?? {
                  total_rules: 0,
                  enabled_rules: 0,
                  compiled_enabled: 0,
                  data_driven_enabled: 0,
                  disabled_rules: 0,
                  data_driven_pct: "0",
                  compiled_pct: "0",
                }
              );
            } catch (err: unknown) {
              throw dbFailure(err, "POLICY_MATURITY_READ_FAILED");
            }
          },
        },

        // ── 10. GET /health/recent-events — wraps v_recent_events ───────
        "health.recentEvents": {
          async handler(ctx: Context<any>) {
            const limit = clamp(parseInt(String((ctx.params as any).limit ?? "20"), 10), 1, 500);
            try {
              const { rows } = await this.pool.query(
                `SELECT * FROM kernel.v_recent_events ORDER BY event_timestamp DESC LIMIT $1;`,
                [limit]
              );
              return { recent: rows, count: rows.length };
            } catch (err: unknown) {
              throw dbFailure(err, "RECENT_EVENTS_READ_FAILED");
            }
          },
        },

        // ── 11. GET /health/receipt-integrity — orphan check ────────────
        "health.receiptIntegrity": {
          async handler() {
            try {
              const { rows } = await this.pool.query(
                `SELECT
            r.id            AS receipt_id,
            r.receipt_type,
            r.receipt_hash,
            r.event_id,
            r.issued_by,
            r.created_at
         FROM kernel.receipt r
         LEFT JOIN kernel.transition_event te ON te.event_id = r.event_id
         WHERE te.receipt IS NULL
         ORDER BY r.created_at DESC;`
              );
              return {
                orphan_count: rows.length,
                orphans: rows,
              };
            } catch (err: unknown) {
              throw dbFailure(err, "RECEIPT_INTEGRITY_READ_FAILED");
            }
          },
        },

        // ── 12. GET /events/stream — SSE over pg_notify ─────────────────
        // Returns a Readable stream; moleculer-web pipes it to the response.
        // notify.ts (ported verbatim) LISTENs on kernel_transition_committed.
        eventsStream: {
          handler(ctx: Context) {
            const { PassThrough } = require("stream") as typeof import("stream");
            const out = new PassThrough();
            // Same headers the incumbent writes via res.writeHead(200, ...).
            // moleculer-web pipes the returned stream and applies these.
            (ctx.meta as any).$responseHeaders = {
              "Content-Type": "text/event-stream",
              "Cache-Control": "no-cache, no-transform",
              Connection: "keep-alive",
              "X-Accel-Buffering": "no",
            };
            out.write(
              `event: ready\ndata: ${JSON.stringify({ channel: "kernel_transition_committed" })}\n\n`
            );

            const unsub = subscribe((evt: KernelEvent) => {
              out.write(`event: kernel_event\ndata: ${JSON.stringify(evt)}\n\n`);
            });

            const keepalive = setInterval(() => {
              try {
                out.write(`: keepalive ${Date.now()}\n\n`);
              } catch {
                /* closed */
              }
            }, 15000);

            out.on("close", () => {
              clearInterval(keepalive);
              unsub();
            });

            return out;
          },
        },

        // ── health (GET /health and /api/health) ────────────────────────
        health: {
          async handler() {
            try {
              const { rows } = await this.pool.query("SELECT 1 as ok");
              return {
                status: "ok",
                db: rows[0].ok === 1,
                pgNotify: isNotifyAlive(),
                subscribers: getSubscriberCount(),
              };
            } catch (err: unknown) {
              const msg = (err as Error)?.message ?? String(err);
              throw new Errors.MoleculerError(msg, 503, "DB_UNAVAILABLE");
            }
          },
        },
      },
    });
  }
}
