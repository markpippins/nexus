import { Service, ServiceBroker, Errors } from "moleculer";
import { pool, redis } from "./db.js";
import {
  app,
  startWatchdog,
} from "./harness-core.js";
import { dispatch } from "./dispatch.js";

/**
 * harness — moleculer port of typescript/harness-srv (:3420 → canary :4420).
 *
 * The Express app, db.ts (PG + Redis), model.ts, admission.ts, and
 * governance.ts are the incumbent's files verbatim (sole deviations: .js
 * import extensions for NodeNext, and listen/uncaughtException/startWatchdog
 * moved out of module scope — the broker owns lifecycle; the watchdog starts
 * in started()). The database, Redis, and filesystem surfaces are shared with
 * the incumbent, so parity is measured against shared live state.
 *
 * Canary posture: reads + validation negatives ONLY (see api.service.ts
 * header) — /run, /run-direct, and /jobs/:id/interrupt spawn agent processes,
 * mutate PG/Redis/nebula state, and kill children by PID; /resolve-context is
 * read-only but hits the same admission context resolution as /run.
 */
export default class HarnessService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "harness",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "dispatch requires $req/$res from the gateway onBeforeCall",
                500,
                "HARNESS_DISPATCH_NO_REQ"
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      async started() {
        this.logger.info("[harness twin] starting runaway watchdog (incumbent parity)...");
        startWatchdog();
        this.logger.info("[harness twin] PG + Redis ready (lazy connect)");
      },
      async stopped() {
        try {
          await redis.quit();
        } catch {
          /* already closed */
        }
        try {
          await pool.end();
        } catch {
          /* pool not initialized */
        }
        this.logger.info("[harness twin] closed");
      },
    });
  }
}
