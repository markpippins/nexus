import { Service, ServiceBroker } from "moleculer";
import {
  healthHandler,
  promptsHandler,
  promptHandler,
  tasksHandler,
  refreshHandler,
} from "./handlers";

/**
 * prompt-sync — moleculer port of typescript/tackle-prompt-sync-srv (:3501).
 *
 * PORTING CONTRACT (role-memory twin discipline — same PG→Redis registry
 * family):
 *   - store.ts + sync.ts + handlers.ts are VERBATIM ports of the
 *     incumbent's redis.ts / db.ts / sync.ts / index.ts handlers: the twin
 *     reads and writes the SAME Redis keys (prompt:proc:{role}::{slug} /
 *     prompt:idx:{role} / prompt:meta:last_updated / task:idx:{role}) and
 *     the SAME PG tables (tackle.prompts, tackle.tasks). Parity here is
 *     against shared live state — the database (and cache) is the arbiter.
 *   - The incumbent's boot runs an initial sync AND an auto-heal sync on
 *     every Redis "ready" event ("boots-degraded, POST /refresh repairs").
 *     A twin boot sync is the same convergence class of writer: identical
 *     PG-derived state into the identical shared key space. Refresh parity
 *     is proven by result-envelope + post-state equality, not cache
 *     isolation.
 *
 * Envelope parity (incumbent index.ts):
 *   /health         200 {status:"ok", lastUpdated, uptime, namespace:"prompt:"}
 *                   503 {status:"error", message} (Redis unreachable)
 *   /prompts/:role  200 [] when missing (NOT 404) | JSON array | 500 {error}
 *   /prompt/:role/:slug  404 {error:"Prompt not found"} when missing
 *                   | JSON card | 500 {error}
 *   /tasks/:role    200 [] when missing | JSON array | 500 {error}
 *   POST /refresh   200 {prompts, rolePromptIndices, tasks, roleTaskIndices,
 *                        timestamp} | 500 {error}
 *
 * NOTE (parity quirks kept deliberately):
 *   - 500s throw {error} envelopes (NOT the kernel-style {status:"error"}
 *     shape — the incumbent's catch blocks emit {error}).
 *   - health 'uptime' is the twin process's own uptime (like the
 *     incumbent's), NOT a shared value — canary normalization covers it.
 *   - health 'lastUpdated' is the SHARED cache stamp: both twins see the
 *     same value, so parity holds (role-memory precedent).
 */
export default class PromptSyncService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "prompt-sync",

      actions: {
        health: {
          handler: async () => healthHandler(),
        },
        prompts: {
          handler: async (ctx: any) => promptsHandler(ctx),
        },
        prompt: {
          handler: async (ctx: any) => promptHandler(ctx),
        },
        tasks: {
          handler: async (ctx: any) => tasksHandler(ctx),
        },
        refresh: {
          handler: async () => refreshHandler(),
        },
      },

      async started() {
        this.logger.info("[prompt-sync twin] Initializing PG + Redis...");
        // Lazy imports keep the service module import-light for the hermetic
        // suite; store.ts is the verbatim port either way.
        const { initRedis, initDb } = await import("./store.js");
        initRedis();
        // Explicit PG init mirrors the incumbent's boot (fail fast on a bad
        // DSN), while boot sync is fire-and-forget (degraded boot allowed —
        // POST /refresh repairs), also mirroring the incumbent.
        initDb();
        refreshHandler()
          .then((r) =>
            this.logger.info(
              `[prompt-sync twin] boot sync: ${r.prompts} prompts across ${r.rolePromptIndices} role indices, ${r.tasks} tasks across ${r.roleTaskIndices} role indices`
            )
          )
          .catch((err: any) =>
            this.logger.warn(
              `[prompt-sync twin] boot sync failed (Redis may be down; POST /refresh repairs): ${err?.message}`
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
