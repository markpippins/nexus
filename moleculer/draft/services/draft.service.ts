import { Service, ServiceBroker, Context, Errors } from "moleculer";
import { getDriver, isEngineAvailable, listCapabilities } from "./drivers/registry";
import { ConnSpec } from "./drivers/types";

/**
 * draft — moleculer port of typescript/draft-srv (:3170), the DB Workbench API
 * backing angular/data-explorer-ui.
 *
 * PORTING CONTRACT (voyager/cascade/kernel discipline):
 *   - The drivers are COPIED VERBATIM from the incumbent (services/drivers/*,
 *     diff-verified): same SQL, same pool-cache TTL/eviction, same TLS policy,
 *     same PG_TYPES map. A "fix" here is divergence — file a draft-srv PR.
 *   - Envelope parity (incumbent routes/db.ts + index.ts):
 *       unknown engine          -> 400 {success:false,message} (test-connection)
 *                                  400 {error} (databases/schemas/query)
 *       engine not enabled      -> 501 {success:false,message} / 501 {error}
 *       driver failure          -> 502 {success:false,message} / 502 {error}
 *       query transport error   -> 500 {columns,rows:[],rowCount:0,…,status:'error',…}
 *       missing internal secret -> 503 {error:'service misconfigured: missing internal secret'}
 *       bad/absent secret       -> 403 {error:'forbidden'}
 *       health                  -> 200 {status,service,component,engines,timestamp}
 *   - The X-Nexus-Internal fleet secret gate is REPLICATED at the action layer:
 *     /api/health exempt, 503 when unconfigured (fail-closed), 403 on mismatch.
 *     Callers that already pass the header (data-explorer-ui/server.ts proxy)
 *     keep working unchanged; the gateway also enforces it per-route.
 *
 * Env names mirror the incumbent exactly (NEXUS_INTERNAL_SECRET, and the
 * drivers read NEXUS_PG_TLS_INSECURE themselves).
 */

export function notImplemented(err: any): boolean {
  return err?.code === "ENGINE_NOT_IMPLEMENTED";
}

/** Query-error result body — byte-shape of the incumbent's 500 handler. */
export function queryErrorBody(err: any): Record<string, unknown> {
  return {
    columns: [],
    rows: [],
    rowCount: 0,
    executionTimeMs: 0,
    status: "error",
    error: err?.message || "Query failed",
    timestamp: new Date().toLocaleTimeString(),
  };
}

export default class DraftService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "draft",

      actions: {
        // ── GET /api/health (secret-exempt) ─────────────────────────────
        health: {
          handler() {
            return {
              status: "ok",
              service: "draft-srv",
              component: "db-workbench",
              engines: listCapabilities(),
              timestamp: new Date().toISOString(),
            };
          },
        },

        // ── GET /api/db/engines ─────────────────────────────────────────
        "db.engines": {
          handler() {
            return { engines: listCapabilities() };
          },
        },

        // ── POST /api/db/test-connection ────────────────────────────────
        "db.testConnection": {
          async handler(ctx: Context<any>) {
            const spec = (ctx.params.body ?? ctx.params) || {};
            const engine = getDriver(spec.engine);
            if (!engine) {
              throw new Errors.MoleculerError(
                `Unknown engine "${spec.engine}"`, 400, "BAD_REQUEST",
                { envelope: { success: false, message: `Unknown engine "${spec.engine}"` } }
              );
            }
            if (!engine.capabilities.available) {
              throw new Errors.MoleculerError(
                `Engine "${engine.capabilities.id}" is provisioned but not enabled. Missing: ${engine.capabilities.missingDeps.join(", ") || "implementation"}`,
                501, "ENGINE_NOT_ENABLED",
                { envelope: { success: false, message: `Engine "${engine.capabilities.id}" is provisioned but not enabled. Missing: ${engine.capabilities.missingDeps.join(", ") || "implementation"}` } }
              );
            }
            try {
              const result = await engine.testConnection(spec as ConnSpec);
              if (!result.success) {
                throw new Errors.MoleculerError(result.message, 502, "UPSTREAM_FAILED", { envelope: result });
              }
              return result;
            } catch (err: any) {
              if (err instanceof Errors.MoleculerError) throw err;
              const status = notImplemented(err) ? 501 : 500;
              throw new Errors.MoleculerError(
                err?.message || "test-connection failed", status, "DRIVER_ERROR",
                { envelope: { success: false, message: err?.message || "test-connection failed" } }
              );
            }
          },
        },

        // ── POST /api/db/databases ──────────────────────────────────────
        "db.databases": {
          async handler(ctx: Context<any>) {
            const spec = (ctx.params.body ?? ctx.params) || {};
            const engine = getDriver(spec.engine);
            if (!engine) {
              throw new Errors.MoleculerError(`Unknown engine "${spec.engine}"`, 400, "BAD_REQUEST",
                { envelope: { error: `Unknown engine "${spec.engine}"` } });
            }
            if (!isEngineAvailable(spec.engine)) {
              throw new Errors.MoleculerError(
                `Engine "${engine.capabilities.id}" is not enabled yet`, 501, "ENGINE_NOT_ENABLED",
                { envelope: { error: `Engine "${engine.capabilities.id}" is not enabled yet` } });
            }
            try {
              return await engine.discoverDatabases(spec as ConnSpec);
            } catch (err: any) {
              const status = notImplemented(err) ? 501 : 502;
              throw new Errors.MoleculerError(
                err?.message || "Database discovery failed", status, "DRIVER_ERROR",
                { envelope: { error: err?.message || "Database discovery failed" } });
            }
          },
        },

        // ── POST /api/db/schemas ────────────────────────────────────────
        "db.schemas": {
          async handler(ctx: Context<any>) {
            const spec = (ctx.params.body ?? ctx.params) || {};
            const engine = getDriver(spec.engine);
            if (!engine) {
              throw new Errors.MoleculerError(`Unknown engine "${spec.engine}"`, 400, "BAD_REQUEST",
                { envelope: { error: `Unknown engine "${spec.engine}"` } });
            }
            if (!isEngineAvailable(spec.engine)) {
              throw new Errors.MoleculerError(
                `Engine "${engine.capabilities.id}" is not enabled yet`, 501, "ENGINE_NOT_ENABLED",
                { envelope: { error: `Engine "${engine.capabilities.id}" is not enabled yet` } });
            }
            try {
              return await engine.discoverSchemas(spec as ConnSpec);
            } catch (err: any) {
              const status = notImplemented(err) ? 501 : 502;
              throw new Errors.MoleculerError(
                err?.message || "Schema discovery failed", status, "DRIVER_ERROR",
                { envelope: { error: err?.message || "Schema discovery failed" } });
            }
          },
        },

        // ── POST /api/db/query ──────────────────────────────────────────
        "db.query": {
          async handler(ctx: Context<any>) {
            const body = (ctx.params.body ?? ctx.params) || {};
            const { connection, sql } = body;
            if (!connection || !sql) {
              throw new Errors.MoleculerError("connection and sql are required", 400, "BAD_REQUEST",
                { envelope: { error: "connection and sql are required" } });
            }
            const engine = getDriver(connection.engine);
            if (!engine) {
              throw new Errors.MoleculerError(`Unknown engine "${connection.engine}"`, 400, "BAD_REQUEST",
                { envelope: { error: `Unknown engine "${connection.engine}"` } });
            }
            if (!isEngineAvailable(connection.engine)) {
              throw new Errors.MoleculerError(
                `Engine "${engine.capabilities.id}" is not enabled yet`, 501, "ENGINE_NOT_ENABLED",
                { envelope: { error: `Engine "${engine.capabilities.id}" is not enabled yet` } });
            }
            try {
              // Errors arrive as status:'error' result bodies (incumbent contract).
              return await engine.execute(connection as ConnSpec, String(sql));
            } catch (err: any) {
              const status = notImplemented(err) ? 501 : 500;
              throw new Errors.MoleculerError(err?.message || "Query failed", status, "DRIVER_ERROR",
                { envelope: queryErrorBody(err) });
            }
          },
        },

        // ── the fleet-secret gate, as an action (gateway aliases it) ────
        // Encodes the incumbent's fail-closed posture for programmatic
        // callers; the HTTP enforcement lives in the gateway's onRequest.
        "db.authGate": {
          handler(ctx: Context) {
            const secret = process.env.NEXUS_INTERNAL_SECRET;
            if (!secret) {
              throw new Errors.MoleculerError(
                "service misconfigured: missing internal secret", 503, "MISCONFIGURED");
            }
            const provided = (ctx.meta as any)?.requestHeaders?.["x-nexus-internal"];
            if (provided !== secret) {
              throw new Errors.MoleculerError("forbidden", 403, "FORBIDDEN");
            }
            return true;
          },
        },
      },
    });
  }
}
