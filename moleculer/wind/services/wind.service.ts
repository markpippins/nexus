import { Service, ServiceBroker, Errors } from "moleculer";
import app from "./express-app.js";
import { dispatch } from "./dispatch.js";

/**
 * wind — moleculer port of typescript/wind-srv (:3300 → canary :4118).
 *
 * Scheduling / DAG orchestration over the wind.* schema: workflows, nodes +
 * requirements resolver, edges, instances (advance/execute/pause/resume/
 * run/stop), execution-requests + attempts + dispatch + receipts, events +
 * event-types, tickets, offices, titles, v-roles, provider-contracts,
 * tasks, outcomes, validate.
 *
 * Dispatch-through-Express (fleet twin pattern): the incumbent serves 87
 * registered endpoints (57 paths under /api + root /health). Every literal
 * alias funnels through the verbatim Express app so parsing, envelopes, and
 * error shapes stay byte-identical.
 *
 * CANARY POSTURE: reads + validation negatives ONLY. The write routes mutate
 * wind.* rows, and the instance lifecycle routes (advance/execute/run/stop,
 * dispatch) drive execution state — the canary's POST/PUT/PATCH/DELETE
 * probes are malformed/UUID-absent negatives that reject before any DB work.
 *
 * BACKGROUND SUBSYSTEMS DELIBERATELY EXCLUDED: the incumbent process also
 * runs the rover scheduler (nebula.harvest polling → wind.events + NATS),
 * the wind.events processor, and pg-notify/NATS listeners. They are not part
 * of the HTTP contract and no route module imports them; a canary must not
 * double-publish harvest.created events or double-poll the events queue
 * alongside the incumbent (see services/express-app.ts header).
 */
export default class WindService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);
    this.parseServiceSchema({
      name: "wind",
      actions: {
        dispatch: {
          handler: async (ctx: any) => {
            const req = ctx.meta.$req;
            const res = ctx.meta.$res;
            if (!req || !res) {
              throw new Errors.MoleculerError(
                "wind.dispatch requires $req/$res context meta (moleculer-web route)",
                500,
                "DISPATCH_NO_REQRES",
              );
            }
            await dispatch(app, req, res);
          },
        },
      },
    });
  }
}
