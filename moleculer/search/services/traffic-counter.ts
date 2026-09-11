/**
 * M1 traffic canary — moleculer side.
 *
 * Counts local action invocations per action name so the cutover
 * observation window is queryable. Paired with the broker-gateway
 * GET /api/v1/broker/traffic/counts endpoint: legacy counts must stay
 * flat while moleculer counts carry the traffic.
 *
 * State is module-level (single process, single broker) and in-memory by
 * design, mirroring the gateway counters: a restart resets the window.
 * startedAt marks the window; observation windows must not span restarts.
 */

const counts = new Map<string, number>();
const startedAt = new Date().toISOString();

export interface TrafficSnapshot {
  startedAt: string;
  total: number;
  counts: Record<string, number>;
}

export function trafficSnapshot(): TrafficSnapshot {
  let total = 0;
  const out: Record<string, number> = {};
  counts.forEach((n, action) => {
    out[action] = n;
    total += n;
  });
  return { startedAt, total, counts: out };
}

/**
 * Broker middleware: wraps every local action invocation and counts it by
 * action name (e.g. "google-search.simpleSearch"). Register in the broker
 * `middlewares` array (moleculer.config.ts). Transparent: errors and return
 * values pass through untouched.
 */
export function trafficCounterMiddleware() {
  // NOTE: no `name` field — moleculer's Middleware typings reject it
  // (runtime tolerates it, the type doesn't). Identified in code by import.
  return {
    localAction(next: any) {
      return async function countedAction(ctx: any) {
        const actionName: string =
          (ctx && ctx.action && ctx.action.name) || "unknown";
        counts.set(actionName, (counts.get(actionName) ?? 0) + 1);
        return next(ctx);
      };
    },
  };
}
