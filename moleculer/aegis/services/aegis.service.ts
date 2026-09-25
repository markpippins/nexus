import { Service, ServiceBroker, Errors } from "moleculer";
import app from "./express-app.js";
import { dispatch } from "./dispatch.js";
import { pool } from "./db.js";

/**
 * aegis — moleculer port of typescript/aegis-srv (:3116 → canary :4116).
 *
 * TLA+ state-machine registry over the aegis.* schema: registries CRUD,
 * revisions, Phase-A validate, TLC model-check (spawns java — gated at
 * canary), wind compilations (plan-only), and the constants / variables /
 * states / transitions / invariants / mappings CRUD families.
 *
 * The Express app is the incumbent's route stack (verbatim TS files, sole
 * deviations: .js import extensions, the day-one rate limiter, and the
 * tlc-runner timeout clamp — see file headers). The database is shared with
 * the incumbent, so response envelopes pass through the same implementations.
 */
export default class AegisService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "aegis",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "dispatch requires $req/$res from the gateway onBeforeCall",
                500,
                "AEGIS_DISPATCH_NO_REQ",
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      async started() {
        this.logger.info("[aegis twin] PostgreSQL pool ready (lazy connect)");
      },
      async stopped() {
        await pool.end().catch(() => {});
        this.logger.info("[aegis twin] closed");
      },
    });
  }
}
