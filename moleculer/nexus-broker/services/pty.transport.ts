import "dotenv/config";
import { Service, ServiceBroker, Context } from "moleculer";
import { WebSocketServer, WebSocket as WsWebSocket, RawData } from "ws";
import * as http from "http";

/**
 * worker.pty-transport — WebSocket TTY transport adapter (M3 Day 4).
 *
 * SEPARATELY governed transport layer. The PROCESS AUTHORITY stays in
 * `worker.pty` (single authority); this adapter only bridges xterm.js
 * clients to that registry:
 *
 *   - On connection: calls `worker.pty.spawn` to create a session, replies
 *     with a JSON `{ type: "spawned", id, ... }` frame, then forwards raw
 *     PTY output to the client and subscribes to the broker's `pty.output`
 *     events for that session.
 *   - Client → server: `{ type: "input", data }` → `worker.pty.write`;
 *     `{ type: "resize", cols, rows }` → `worker.pty.resize`.
 *   - On `pty.exit` or client disconnect: closes the socket and kills the
 *     session so no orphan process leaks.
 *
 * Protocol is wire-compatible with the legacy pty-srv (:3120) — raw shell
 * output on the wire, JSON control frames from the client — so the existing
 * nexus-console terminal component can be re-pointed at this adapter with no
 * client-side protocol change.
 *
 * Listens on PTY_WS_PORT (default 3130); health on PTY_WS_PORT + 1 (3131)
 * mirroring pty-srv's health-port convention.
 */

interface WsClient {
  ws: WsWebSocket;
  sessionId: string;
  alive: boolean;
}

export default class PtyTransportService extends Service {
  private wss: WebSocketServer | null = null;
  private healthServer: http.Server | null = null;
  private clients = new Map<WsWebSocket, WsClient>();

  private get port(): number {
    return parseInt(process.env.PTY_WS_PORT || "3130", 10);
  }
  private get host(): string {
    return process.env.PTY_WS_HOST || "127.0.0.1";
  }

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "worker.pty-transport",

      started: () => this.onStarted(),
      stopped: () => this.onStopped(),

      actions: {
        health: {
          handler: () => ({
            status: "ok",
            service: "worker.pty-transport",
            ws: `ws://${this.host}:${this.port}`,
            sessions: this.clients.size,
          }),
        },
      },

      events: {
        // Routing of worker.pty output/exit to connected clients keyed by
        // session id. Moleculer schema events (broker.on/off were removed in
        // v0.14) — worker.pty emits these on the broker bus; this adapter
        // receives them and forwards to the matching WebSocket client.
        "pty.output": {
          handler: (payload: { id: string; data: string }) => this.onPtyOutput(payload),
        },
        "pty.exit": {
          handler: (payload: { id: string; exitCode: number }) => this.onPtyExit(payload),
        },
      },
    });
  }

  private async onStarted(): Promise<void> {
    this.wss = new WebSocketServer({ host: this.host, port: this.port });
    this.wss.on("connection", (ws) => this.onConnection(ws));
    this.logger.info(`pty-transport ws listening on ws://${this.host}:${this.port}`);

    this.healthServer = http.createServer((_req, res) => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", service: "worker.pty-transport", sessions: this.clients.size }));
    });
    // Secondary endpoint — never let a failed health bind crash the worker
    // tier. The gateway /health + /workers/pty-transport/health are the
    // authoritative probes; this standalone health port is a convenience.
    this.healthServer.on("error", (err: Error) => {
      this.logger.warn(`pty-transport health bind skipped: ${err.message}`);
    });
    this.healthServer.listen(this.port + 1, this.host, () => {
      this.logger.info(`pty-transport health on http://${this.host}:${this.port + 1}/`);
    });
  }

  private async onStopped(): Promise<void> {
    if (this.wss) {
      for (const { ws } of this.clients.values()) {
        try { ws.close(); } catch { /* ignore */ }
      }
      this.clients.clear();
      this.wss.close();
    }
    if (this.healthServer) this.healthServer.close();
  }

  private async onConnection(ws: WsWebSocket): Promise<void> {
    let spawnedId: string;
    try {
      const spawned: any = await this.broker.call("worker.pty.spawn", {});
      spawnedId = spawned.id;
    } catch (err: any) {
      this.logger.warn(`pty-transport spawn failed: ${err?.message}`);
      ws.close(1011, `spawn failed: ${err?.message}`);
      return;
    }

    const client: WsClient = { ws, sessionId: spawnedId, alive: true };
    this.clients.set(ws, client);

    // Ack the spawn with the session id so the client can correlate.
    ws.send(JSON.stringify({ type: "spawned", id: spawnedId }));

    ws.on("message", (raw: RawData) => {
      const text = raw.toString();
      let msg: any;
      try {
        msg = JSON.parse(text);
      } catch {
        // Legacy pty-srv fell back to writing raw bytes on non-JSON input.
        this.dispatchWrite(spawnedId, text);
        return;
      }
      if (msg.type === "input" && typeof msg.data === "string") {
        this.dispatchWrite(spawnedId, msg.data);
      } else if (msg.type === "resize") {
        this.dispatchResize(spawnedId, msg.cols, msg.rows);
      }
    });

    ws.on("close", () => {
      this.clients.delete(ws);
      this.dispatchKill(spawnedId);
    });

    ws.on("error", () => {
      this.clients.delete(ws);
      this.dispatchKill(spawnedId);
    });

    ws.on("pong", () => {
      client.alive = true;
    });
  }

  private async dispatchWrite(sessionId: string, data: string): Promise<void> {
    try {
      await this.broker.call("worker.pty.write", { id: sessionId, data });
    } catch (err: any) {
      this.logger.warn(`pty-transport write ${sessionId.slice(0, 8)}: ${err?.message}`);
    }
  }

  private async dispatchResize(sessionId: string, cols?: number, rows?: number): Promise<void> {
    try {
      await this.broker.call("worker.pty.resize", { id: sessionId, cols, rows });
    } catch (err: any) {
      this.logger.warn(`pty-transport resize ${sessionId.slice(0, 8)}: ${err?.message}`);
    }
  }

  private async dispatchKill(sessionId: string): Promise<void> {
    try {
      await this.broker.call("worker.pty.kill", { id: sessionId });
    } catch { /* already gone / not found */ }
  }

  private onPtyOutput(payload: { id: string; data: string }): void {
    for (const client of this.clients.values()) {
      if (client.sessionId === payload.id) {
        try { client.ws.send(payload.data); } catch { /* client gone */ }
      }
    }
  }

  private onPtyExit(payload: { id: string; exitCode: number }): void {
    for (const client of this.clients.values()) {
      if (client.sessionId === payload.id) {
        try {
          client.ws.send(JSON.stringify({ type: "exit", exitCode: payload.exitCode }));
          client.ws.close();
        } catch { /* client gone */ }
      }
    }
  }
}