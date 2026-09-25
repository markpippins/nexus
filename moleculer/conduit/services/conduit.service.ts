import { Service, ServiceBroker, Errors } from "moleculer";
import app from "./express-app.js";
import { dispatch } from "./dispatch.js";
import { closePool } from "./db/client.js";

/**
 * conduit — moleculer port of conduit-srv (:3104 → canary :4104).
 *
 * The database and filesystem surfaces are shared with the incumbent. The
 * Express app is the incumbent's route stack, so all response envelopes and
 * streaming behavior pass through the same implementations.
 */
export default class ConduitService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "conduit",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "dispatch requires $req/$res from the gateway onBeforeCall",
                500,
                "CONDUIT_DISPATCH_NO_REQ"
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      async started() {
        this.logger.info("[conduit twin] PostgreSQL pool ready (lazy connect)");
      },
      async stopped() {
        await closePool();
        this.logger.info("[conduit twin] closed");
      },
    });
  }
}
