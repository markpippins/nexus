import { Service, ServiceBroker, Errors } from "moleculer";
import app, { ensureDb, shutdown } from "./express-app.js";
import { dispatch } from "./dispatch.js";

/**
 * nebula — moleculer port of typescript/nebula-srv (:3101 → canary :4101).
 *
 * The agent-record backbone: agent_records, harvests, plans, agendas,
 * systems, attestations, role-leases, inbox pointers (Redis), and the
 * block-segmentation cache tier. The twin serves the FULL registered
 * surface (231 endpoints across 182 paths) via dispatch-through-Express:
 * every literal alias funnels into the incumbent's verbatim routes.ts
 * (9,505 lines) so envelopes, validation, and route ordering stay
 * byte-identical.
 *
 * SUBSTANCE CACHE COUPLING (read-only, preserved verbatim): the
 * segment-set evidence reads (GET /api/segment-sets, /api/segment-sets/:id,
 * /api/harvest-candidates/:id/segment-sets, /api/requirements/:id/
 * segment-sets) route through src/substance-proxy.ts to SUBSTANCE_BASE_URL
 * (default http://localhost:3115). The substance twin (:4115) implements
 * the same contract, so pointing the twin's SUBSTANCE_BASE_URL at :4115
 * exercises the canary chain end-to-end; pointing it at the incumbent
 * keeps the incumbent's Redis-cached resolution. Either way the twin
 * itself never touches nebula.segment_sets — the proxy's read-only
 * doctrine carries over unchanged.
 *
 * CANARY POSTURE: reads + validation negatives ONLY. Write probes are
 * malformed/UUID-absent negatives that reject before any DB work.
 *
 * BACKGROUND SUBSYSTEMS DELIBERATELY EXCLUDED (incumbent process
 * subsystems, not HTTP contract):
 *   - runMigrations(): the twin NEVER mutates the schema (fail-closed
 *     migrate gate stays in the incumbent process)
 *   - sweepRoleLeases() 10-min self-call (state mutation; incumbent
 *     enforces lease expiry regardless of which process serves reads)
 *   - Redis init stays lazy (initRedis() is lazy-connect in the
 *     verbatim service; inbox-pointer reads connect on first use)
 */
export default class NebulaService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "nebula",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "nebula.dispatch requires $req/$res context meta (moleculer-web route)",
                500,
                "DISPATCH_NO_REQRES",
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      started: () => {
        // Warm the DB pool at broker start (incumbent preflights in
        // start() BEFORE migrations; the twin runs no migrations).
        ensureDb();
      },
      stopped: async () => {
        await shutdown();
      },
    });
  }
}
