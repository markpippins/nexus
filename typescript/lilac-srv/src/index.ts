import { loadEnv } from "./env";
import { createApp } from "./app";
import { assertConnected, poolDb } from "./db";
import { startHeartbeat } from "heartbeat-client";

loadEnv();

const PORT = parseInt(process.env.LILAC_SRV_PORT || "3172", 10);
// 0 disables heartbeats; set LILAC_SRV_SERVICE_ID once the service is
// registered in the service registry (port 8085).
const HEARTBEAT_SERVICE_ID = parseInt(process.env.LILAC_SRV_SERVICE_ID || "0", 10);

process.on("uncaughtException", (err: Error & { code?: string }) => {
  if (err.code === "EADDRINUSE") {
    console.error(`lilac-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    process.exit(1);
  }
  if (err.code === "EPIPE" || err.code === "ECONNRESET" || err.code === "ETIMEDOUT") {
    console.warn("[lilac-srv] uncaughtException (connection noise):", err.code, err.message);
    return;
  }
  console.error("[lilac-srv] uncaughtException:", err.message);
});

async function start(): Promise<void> {
  const db = poolDb();
  // Fail fast under systemd rather than serving 500s against a broken DSN.
  await assertConnected(db);

  const app = createApp({ db });
  const server = app.listen(PORT, () => {
    console.log(`[lilac-srv] typed Lilac surface listening on http://localhost:${PORT}`);
    console.log("[lilac-srv] staged-inert: no live caller is repointed at this service");

    if (HEARTBEAT_SERVICE_ID > 0) {
      startHeartbeat({
        serviceId: HEARTBEAT_SERVICE_ID,
        serviceName: "lilac-srv",
        interval: 30,
        log: (...args: unknown[]) => console.log(new Date().toISOString(), "[heartbeat lilac-srv]", ...args),
      });
    }
  });

  const shutdown = () => {
    server.close(() => process.exit(0));
  };
  process.on("SIGTERM", shutdown);
  process.on("SIGINT", shutdown);
}

start().catch((err) => {
  console.error("[lilac-srv] failed to start:", err instanceof Error ? err.message : err);
  process.exit(1);
});
