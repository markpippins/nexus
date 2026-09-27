import { Service, ServiceBroker, Errors } from "moleculer";
import app from "./express-app.js";
import { dispatch } from "./dispatch.js";
import { pool } from "./db.js";

/**
 * peb — moleculer port of typescript/peb-srv (:3111 → canary :4111).
 *
 * The Push Event Bus: decisions (ADR lifecycle incl. supersede chains),
 * transactions, fleet health (circuit breakers/entropy/violations), governance
 * events (receipt stream + replay), entities (capability projections), state
 * (version/diff), and traces (lineage trees).
 *
 * The Express app is the incumbent's route stack (verbatim JS files, sole
 * deviation: a day-one rate limiter — see express-app.ts header). The
 * database is shared with the incumbent, so response envelopes pass through
 * the same implementations.
 */
export default class PebService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "peb",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "dispatch requires $req/$res from the gateway onBeforeCall",
                500,
                "PEB_DISPATCH_NO_REQ",
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      async started() {
        this.logger.info("[peb twin] PostgreSQL pool ready (lazy connect)");
      },
      async stopped() {
        // db.js is verbatim (no closePool export) — close via the pool itself.
        await pool.end().catch(() => {});
        this.logger.info("[peb twin] closed");
      },
    });
  }
}
