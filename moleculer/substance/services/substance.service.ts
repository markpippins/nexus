import { Service, ServiceBroker, Errors } from "moleculer";
import app, { ensureDb } from "./express-app.js";
import { dispatch } from "./dispatch.js";

/**
 * substance — moleculer port of typescript/substance-srv (:3115 → canary :4115).
 *
 * Segment-set resolution over nebula.segment_sets: CRUD, members add/remove,
 * domain links (candidates | requirements), from-segments ingest,
 * Redis-cached resolve with TTL-bound staleness.
 *
 * Dispatch-through-Express (fleet twin pattern): the incumbent serves 11
 * registered endpoints (8 paths: /healthz + /segment-sets families + the
 * root-mounted /{domain_type}/{domain_id}/segment-sets links surface).
 * Every literal alias funnels through the verbatim Express app so parsing,
 * FastAPI-shaped envelopes ({detail} errors, 422 validation), and route
 * ordering stay byte-identical.
 *
 * CANARY POSTURE: reads + validation negatives ONLY. The write routes mutate
 * nebula.segment_sets rows and invalidate Redis cache entries; canary
 * POST/PATCH/DELETE probes are malformed/UUID-absent negatives that reject
 * before any DB or cache work.
 *
 * BACKGROUND SUBSYSTEMS DELIBERATELY EXCLUDED: the registry heartbeat
 * (:8085, service id 117) and the segment_expired pg-notify cache
 * invalidation listener are incumbent process subsystems, not HTTP
 * contract. The twin leaves them to the incumbent; the Redis TTL remains
 * the staleness safety net (exactly as the listener module itself
 * documents for missed notifications).
 */
export default class SubstanceService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "substance",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "substance.dispatch requires $req/$res context meta (moleculer-web route)",
                500,
                "DISPATCH_NO_REQRES",
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      started: () => {
        // Warm the DB pool at broker start (incumbent preflights in start()).
        ensureDb();
      },
    });
  }
}
