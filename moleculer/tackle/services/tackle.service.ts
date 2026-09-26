import { Service, ServiceBroker, Errors } from "moleculer";
import app, { initTackleApp, closeTackleApp } from "./express-app.js";
import { dispatch } from "./dispatch.js";

/**
 * tackle — moleculer port of typescript/tackle-srv (:3410 → canary :4410).
 *
 * PORTING CONTRACT (voyager/cascade/kernel/draft/knowledge/role-memory/
 * semantics discipline):
 *   - db.ts / memory.ts / env.ts / routes/*.ts are VERBATIM copies of the
 *     incumbent. The express-app is the incumbent's index.ts minus
 *     listen/heartbeat. The database, Redis, and filesystem surfaces
 *     (PIPELINE_ROOT/nexus/logs SSE reads) are shared with the incumbent,
 *     so parity is measured against shared live state — the DB is the
 *     arbiter (write-canary ruling 59f8e2af).
 *   - dispatch: every gateway alias funnels into this ONE action, which
 *     hands the real req/res to the real express app (see api.service.ts
 *     header for the mechanics and why).
 *
 * Boot parity note: initDb() runs the tackle migration chain (v1..v18)
 * exactly like the incumbent's start(). All migrations are idempotent and
 * the live DB already carries them; the advisory lock (MIGRATION_LOCK_KEY)
 * makes concurrent boot with the incumbent/tackle-mcp safe — this is the
 * same coexistence pattern tackle-srv and tackle-mcp already run in
 * production. v7-9 load SQL from nexus/schemas/migrations/tackle/ via a
 * path relative to the COMPILED file (../../../ from dist/services/) —
 * resolved from the repo checkout the twin runs in, identical to the
 * incumbent's resolution from its own dist.
 *
 * Canary posture: reads + negatives ONLY (see api.service.ts header).
 */
export default class TackleService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "tackle",

      actions: {
        dispatch: {
          // Single funnel for all 86 aliases. The real req/res arrive via
          // ctx.meta (stashed by onBeforeCall); the express app answers on
          // the real res. Resolves when the response is finished (or
          // rejects on dispatch failure before any handler ran).
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "dispatch requires $req/$res from the gateway onBeforeCall",
                500,
                "TACKLE_DISPATCH_NO_REQ"
              );
            }
            await dispatch(app, req, res);
          },
        },
      },

      async started() {
        this.logger.info("[tackle twin] Initializing PG (tackle schema) + Redis...");
        await initTackleApp();
        this.logger.info("[tackle twin] PG initialized (tackle schema), Redis client up (lazy)");
      },

      async stopped() {
        await closeTackleApp();
        this.logger.info("[tackle twin] closed");
      },
    });
  }
}
