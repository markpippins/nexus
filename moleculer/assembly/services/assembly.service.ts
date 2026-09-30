import { Service, ServiceBroker, Errors } from "moleculer";
import app, { shutdown } from "./express-app.js";
import { dispatch } from "./dispatch.js";

/**
 * assembly — moleculer port of typescript/assembly-srv (:3107 → canary :4107).
 *
 * The deliberation/social layer over nebula domain objects: forums,
 * threads, comments, feed, work-requests, plans, agendas, decisions,
 * users, counts/search/refresh-stats, and the read-only segment-set
 * evidence surface. The twin serves the full registered surface (88
 * endpoints / 73 paths) via dispatch-through-Express: the incumbent's
 * verbatim plain-JS route stack (23 route modules) so envelopes, the
 * custom zlib gzip middleware, AppError shapes, and the segment-sets
 * mount-order doctrine stay byte-identical.
 *
 * THE PROXY CHAIN (all READ-ONLY, preserved verbatim — the substance
 * cache doctrine the operator flagged):
 *   assembly → nebula  (nebula-proxy.js → NEBULA_SRV_BASE_URL, default
 *     :3101 — nebula-domain reads; nebula itself resolves segment-set
 *     evidence through substance)
 *   assembly → substance (substance-proxy.js → SUBSTANCE_BASE_URL,
 *     default :3115 — segment-set evidence reads; substance owns
 *     nebula.segment_sets AND its Redis read-through cache, so assembly
 *     must never query those tables directly)
 *   The twins implement the same contracts, so pointing the assembly
 *   twin's env at the TWIN bases (:4101/:4115) exercises the full
 *   canary chain; the defaults keep the incumbent bases. Either way
 *   the twin never touches nebula.segment_sets directly.
 *
 * CANARY POSTURE: reads + validation negatives ONLY. Write probes are
 * malformed-body negatives that reject before any DB work (e.g. POST
 * /api/forums/:slug/threads requires title/body/postedById BEFORE the
 * INSERT — verified in the incumbent's handler).
 *
 * BACKGROUND SUBSYSTEMS DELIBERATELY EXCLUDED (incumbent process
 * subsystems, not HTTP contract):
 *   - runMigration() (the twin NEVER mutates the schema)
 *   - startHeartbeat() (registry :8085, serviceId 110 — the twin is
 *     registry-invisible by design, like every fleet twin)
 */
export default class AssemblyService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "assembly",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "assembly.dispatch requires $req/$res context meta (moleculer-web route)",
                500,
                "DISPATCH_NO_REQRES",
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
      started: () => {
        // db.js creates its pool at module load (lazy connections); no
        // eager warm needed. No migration. No heartbeat.
      },
      stopped: async () => {
        await shutdown();
      },
    });
  }
}
