import { Service, ServiceBroker, Context } from "moleculer";

/**
 * Worker tier — hosts the process-spawning services re-homed from the
 * Express fleet (harness-srv, pty-srv, execution-srv — Wave 4).
 *
 * Wave 4: real handlers now live in worker.harness / worker.pty /
 * worker.execution. This service provides the tier-level introspection
 * (list) and the generic process-control facade (spawn/kill/status)
 * dispatching to the domain workers.
 */
export default class WorkerService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "worker",

      actions: {
        list: {
          async handler(ctx: Context) {
            const info = (ctx.broker as any).getLocalNodeInfo();
            const services = (info.services || []) as { name: string }[];
            const statusFor = (name: string): string =>
              services.some((s) => s.name === name) ? "available" : "not-declared";
            return {
              workers: [
                { name: "worker.harness", status: statusFor("worker.harness"), wave: 4 },
                { name: "worker.pty", status: statusFor("worker.pty"), wave: 4 },
                { name: "worker.execution", status: statusFor("worker.execution"), wave: 4 },
                { name: "worker.pty-transport", status: statusFor("worker.pty-transport"), wave: 4 },
              ],
              nodeID: ctx.nodeID,
            };
          },
        },

        spawn: {
          params: {
            service: "string",
            args: { type: "object", optional: true },
          },
          async handler(ctx: Context<{ service: string; args?: Record<string, any> }>) {
            const svc = ctx.params.service.replace(/^worker\./, "");
            switch (svc) {
              case "harness":
                return ctx.call("worker.harness.run", ctx.params.args || {});
              case "pty":
                return ctx.call("worker.pty.spawn", ctx.params.args || {});
              case "execution":
                throw new Error("worker.execution is read-only (observability) — no spawn");
              default:
                throw new Error(`unknown worker service: ${ctx.params.service}`);
            }
          },
        },

        kill: {
          params: {
            service: "string",
            args: { type: "object", optional: true },
          },
          async handler(ctx: Context<{ service: string; args?: Record<string, any> }>) {
            const svc = ctx.params.service.replace(/^worker\./, "");
            switch (svc) {
              case "harness":
                return ctx.call("worker.harness.sessions", {});
              case "pty":
                return ctx.call("worker.pty.kill", ctx.params.args || {});
              case "execution":
                throw new Error("worker.execution is read-only (observability) — nothing to kill");
              default:
                throw new Error(`unknown worker service: ${ctx.params.service}`);
            }
          },
        },

        status: {
          params: {
            service: "string",
          },
          async handler(ctx: Context<{ service: string }>) {
            const svc = ctx.params.service.replace(/^worker\./, "");
            switch (svc) {
              case "harness":
                return ctx.call("worker.harness.health", {});
              case "pty":
                return ctx.call("worker.pty.list", {});
              case "execution":
                return ctx.call("worker.execution.health", {});
              default:
                throw new Error(`unknown worker service: ${ctx.params.service}`);
            }
          },
        },
      },
    });
  }
}
