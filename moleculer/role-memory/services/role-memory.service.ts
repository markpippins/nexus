import { Service, ServiceBroker } from "moleculer";
import {
  healthHandler,
  proceduresHandler,
  procedureHandler,
  refreshHandler,
} from "./handlers";

/**
 * role-memory — moleculer port of typescript/role-memory-srv (:3500).
 *
 * PORTING CONTRACT (voyager/cascade/kernel/draft/knowledge discipline):
 *   - store.ts + sync.ts + handlers.ts are VERBATIM ports of the
 *     incumbent's redis.ts / db.ts / sync.ts / index.ts handlers: the twin
 *     reads and writes the SAME Redis keys (mem:proc:* / mem:idx:* /
 *     mem:meta:last_updated) and the SAME PG tables (tackle.memory,
 *     tackle.role_memory). Parity here is against shared live state — the
 *     database (and cache) is the arbiter.
 *   - The incumbent's own boot sequence double-writes Redis (auto-heal
 *     "ready" handler racing the explicit initial sync — see its index.ts
 *     comment "the double write is harmless"). A twin running POST
 *     /refresh is the same class of writer: converging on identical
 *     PG-derived state. Refresh parity is proven by result-envelope +
 *     post-state equality, not by cache isolation.
 *
 * Envelope parity (incumbent index.ts):
 *   /health       200 {status, redis, lastUpdated, procedureCount,
 *                     roleIndexCount, stale, staleThresholdMs, uptime}
 *                 503 structured {status:"error", redis:"unreachable", ...}
 *   /procedures/:role  200 [] when missing (NOT 404) | JSON array
 *                 500 {error}
 *   /procedure/:slug   404 {error:"Procedure not found"} when missing
 *                 500 {error}
 *   /refresh      200 {procedures, roleIndices, timestamp} | 500 {error}
 *
 * NOTE (parity quirks kept deliberately):
 *   - 500s throw {error} envelopes (NOT the kernel-style {status:"error"}
 *     shape — the incumbent's catch blocks emit {error}).
 *   - health 'uptime' is the twin process's own uptime (like the
 *     incumbent's), NOT a shared value — canary normalization covers it.
 *   - health 'stale' is time-dependent against the SHARED cache stamp:
 *     both twins see the same lastUpdated/counts, so parity holds.
 */
export default class RoleMemoryService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "role-memory",

      actions: {
        health: {
          handler: async () => healthHandler(),
        },
        procedures: {
          handler: async (ctx: any) => proceduresHandler(ctx),
        },
        procedure: {
          handler: async (ctx: any) => procedureHandler(ctx),
        },
        refresh: {
          handler: async () => refreshHandler(),
        },
      },

      async started() {
        this.logger.info("[role-memory twin] Initializing PG + Redis...");
        // Lazy imports keep the service module import-light for the hermetic
        // suite; store.ts is the verbatim port either way.
        const { initRedis, initDb } = await import("./store.js");
        initRedis();
        // Explicit PG init mirrors the incumbent's boot (fail fast on a bad
        // DSN), while boot sync is fire-and-forget (degraded boot allowed —
        // POST /refresh repairs).
        initDb();
        refreshHandler()
          .then((r) =>
            this.logger.info(
              `[role-memory twin] boot sync: ${r.procedures} procedures, ${r.roleIndices} role indices`
            )
          )
          .catch((err: any) =>
            this.logger.warn(
              `[role-memory twin] boot sync failed (Redis may be down; POST /refresh repairs): ${err?.message}`
            )
          );
      },

      async stopped() {
        const { closeRedis } = await import("./store.js");
        await closeRedis();
      },
    });
  }
}
