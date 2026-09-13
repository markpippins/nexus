import "dotenv/config";
import { Service, ServiceBroker } from "moleculer";
import ApiGateway from "moleculer-web";

/**
 * Broker HTTP gateway (moleculer-web).
 *
 * Exposes the worker-tier control surface. The two AdonisJS edges are the
 * primary REST edges; this gateway is for broker-level introspection
 * (health + worker status) and, later, direct dispatch to worker actions
 * for the process-spawning services (Wave 4).
 */
export default class ApiService extends Service {
  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "api",
      mixins: [ApiGateway],

      settings: {
        port: process.env.SERVICE_PORT || 4080,
        ip: "0.0.0.0",

        routes: [
          {
            // ── Execution read catalog (M2) — dedicated route so error
            // formatting is legacy-exact without changing the error
            // contract the other workers' consumers already rely on.
            // Legacy execution-srv answers { error: message } with the
            // handler's status (400 bad UUID, 404 unknown id, 500 SQL
            // failure); moleculer's default error shape differs, so this
            // route overrides onError. Declared BEFORE /api so prefix
            // matching picks the specific route first. Read-only:
            // SELECTs only.
            path: "/api/workers/execution",

            whitelist: ["worker.execution.**"],

            aliases: {
              "GET /": "worker.execution.healthSimple",
              // Legacy GET /api/execution/health — the rich per-status probe
              // (scanned_at + per-status counts). Distinct from the simple
              // server health aliased at "/".
              "GET /health": "worker.execution.richHealth",
              // Legacy mounts the router at /api/execution; the worker
              // surface lives here. All 18 legacy routes map 1:1.
              "GET /requests": "worker.execution.listRequests",
              "GET /leases": "worker.execution.listLeases",
              "GET /attempts": "worker.execution.listAttempts",
              "GET /receipts": "worker.execution.listReceipts",
              "GET /requests/:id/state": "worker.execution.requestState",
              "GET /leases/stale": "worker.execution.staleLeases",
              "GET /leases/:id/lifecycle": "worker.execution.leaseLifecycle",
              "GET /health/integrity-scan": "worker.execution.integrityScan",
              "GET /requests/:id/attempts": "worker.execution.requestAttempts",
              "GET /requests/:id/receipts/lineage": "worker.execution.receiptsLineage",
              "GET /health/by-executor": "worker.execution.byExecutor",
              "GET /health/status-distribution": "worker.execution.statusDistribution",
              "GET /receipts/:id/pipeline-origin": "worker.execution.pipelineOrigin",
              "GET /witnessed-runs": "worker.execution.witnessed-runs.witnessedRuns",
              "GET /witnessed-runs/diagnostics": "worker.execution.witnessed-runs.witnessedRunDiagnostics",
              "GET /projections/witnessed-runs": "worker.execution.witnessed-runs.witnessedRunProjection",
              "GET /metrics": "worker.execution.witnessed-runs.governanceMetrics",
            },

            onError(req: any, res: any, err: any) {
              // Legacy shape: { error: message }, status from the thrown
              // MoleculerError code (400/404); anything else is 500 — the
              // legacy handlers' sendError() also surfaced the raw SQL
              // message for operator debugging.
              const code = (err as any)?.code;
              const status = Number.isInteger(code) && code >= 400 && code < 600 ? code : 500;
              res.setHeader("Content-Type", "application/json; charset=utf-8");
              res.statusCode = status;
              res.end(JSON.stringify({ error: (err as any)?.message ?? String(err) }));
            },
          },
          {
            path: "/api",

            whitelist: ["api.*", "worker.**", "keychain-snapshot.**"],

            aliases: {
              "GET /health": "api.health",
              "GET /workers": "worker.list",
              // (the execution read catalog moved to its own route above —
              // legacy-exact error formatting — see /api/workers/execution)
              "GET /workers/pty": "worker.pty.list",
              "POST /workers/pty": "worker.pty.spawn",
              "DELETE /workers/pty/:id": "worker.pty.kill",
              "GET /workers/harness": "worker.harness.health",
              "POST /workers/harness/run": "worker.harness.run",
              "POST /workers/harness/resolve-context": "worker.harness.resolveContext",
              "GET /workers/harness/sessions": "worker.harness.sessions",
              // Keychains — agent-record contextual layer (state vector + rewind)
              // (sol-ir-snapshot retired 2026-09-02 — sol_ir was the retired name for keychains)
              "GET /keychain-snapshot/status": "keychain-snapshot.status",
              "POST /keychain-snapshot/snapshot": "keychain-snapshot.snapshot",
              "GET /keychain-snapshot/agent-records/status": "keychain-snapshot.agentRecordsStatus",
              "POST /keychain-snapshot/agent-records/snapshot": "keychain-snapshot.agentRecordsSnapshot",
              "GET /keychain-snapshot/agent-records/transitions": "keychain-snapshot.agentRecordsTransitions",
              "GET /keychain-snapshot/agent-records/rewind": "keychain-snapshot.agentRecordsRewind",
            },

            bodyParsers: {
              json: {
                strict: false,
                limit: "5mb", // parity with legacy tier (raised for transcript docklang payloads)
              },
              urlencoded: {
                extended: true,
                limit: "5mb",
              },
            },

            cors: {
              origin: "*",
              methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
              allowedHeaders: "*",
              credentials: false,
              maxAge: 3600,
            },
          },
        ],

        log4XXResponses: false,
        logRequestParams: "info",
        logResponseData: "info",
      },

      actions: {
        health: {
          async handler() {
            return {
              status: "ok",
              service: "nexus-broker",
              namespace: "nexus",
              workers: ["worker.harness", "worker.pty", "worker.execution"],
              timestamp: new Date().toISOString(),
            };
          },
        },
      },
    });
  }
}
