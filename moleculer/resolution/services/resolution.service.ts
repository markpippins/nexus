import { Service, ServiceBroker, Errors } from "moleculer";
import app, { shutdown } from "./express-app.js";
import { dispatch } from "./dispatch.js";

/**
 * resolution — moleculer port of typescript/resolution-srv (:3171 → canary :4171).
 *
 * READ-ONLY registry-driven surface over the canonical resolution.* schema:
 * GET /api/{table} + GET /api/{table}/{id} for all 48 registered tables,
 * /api/meta (registry overview + row counts), /api/health + /health, and
 * the uniform 405 read_only guard on every non-GET method (v1 doctrine —
 * Decision 32 Ruling 3: the 405 boundary IS part of the canary surface).
 *
 * Dispatch-through-Express: the incumbent's verbatim route stack (routes/
 * resolution.ts registers per-table loops from tables.ts; the twin's
 * gateway aliases them as literals generated from the same tables.ts).
 *
 * CANARY POSTURE (Ruling 3): reads + boundary negatives ONLY. The write
 * family is exercised exclusively via POST-must-405 probes — resolution.*
 * rows are governance-canonical and are never created or mutated by the
 * twin or its tests. Unknown-table probes return 404 unknown_table on
 * both sides without DB work.
 *
 * BACKGROUND SUBSYSTEMS DELIBERATELY EXCLUDED (incumbent process
 * subsystems, not HTTP contract):
 *   - startHeartbeat() (env-gated service-registry heartbeat; the twin
 *     is registry-invisible by design, like every fleet twin)
 *   - the DB preflight in start() (the twin's express-app warm-up
 *     covers pool readiness; no eager schema probing at boot)
 */
export default class ResolutionService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "resolution",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "resolution.dispatch requires $req/$res context meta (moleculer-web route)",
                500,
                "DISPATCH_NO_REQRES",
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      started: () => {
        // db.ts creates its pool at module load (lazy connections); the
        // incumbent preflights in start(); no migration exists in this
        // service at all (schema is producer-owned).
      },
      stopped: async () => {
        await shutdown();
      },
    });
  }
}
