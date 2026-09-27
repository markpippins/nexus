import { Service, ServiceBroker, Errors } from "moleculer";
import app from "./express-app.js";
import { dispatch } from "./dispatch.js";
import { closePool } from "./db.js";

/**
 * execution — moleculer port of typescript/execution-srv (:3110 → canary :4110).
 *
 * Read-only observability over the PostgreSQL `execution` schema. The
 * database is shared with the incumbent; the Express app is the incumbent's
 * route stack, so response envelopes pass through the same implementations.
 * Every route is a SELECT — there is no write path in this service.
 */
export default class ExecutionService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "execution",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "dispatch requires $req/$res from the gateway onBeforeCall",
                500,
                "EXECUTION_DISPATCH_NO_REQ"
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      async started() {
        this.logger.info("[execution twin] PostgreSQL pool ready (lazy connect)");
      },
      async stopped() {
        await closePool();
        this.logger.info("[execution twin] closed");
      },
    });
  }
}
