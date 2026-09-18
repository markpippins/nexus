import "dotenv/config";
import { Service, ServiceBroker, Context } from "moleculer";
import { v4 as uuidv4 } from "uuid";

/**
 * worker.pty — TTY process worker (Wave 4.2).
 *
 * THE single PTY process authority. Manages node-pty shell processes: each
 * spawn creates a pty session with an input/output buffer; write/resize push
 * input and terminal geometry; kill terminates; status reports live sessions.
 *
 * Transport (M3): the WebSocket TTY stream is a SEPARATELY governed adapter
 * (`worker.pty-transport` service). This worker owns the process lifecycle
 * and emits `pty.output` / `pty.exit` broker events so any transport can
 * bridge clients to the session registry without duplicating process
 * authority. Retiring pty-srv (legacy :3120) is gated on this worker being
 * the sole process + transport authority.
 */

import * as pty from "node-pty";

interface PtySession {
  id: string;
  pid: number;
  shell: string;
  startedAt: number;
  cols: number;
  rows: number;
  outputBuffer: string;
  status: "running" | "exited";
  exitCode: number | null;
  // The live node-pty process handle; write/resize target it. Absent once
  // the session has exited (the session row lingers briefly for status).
  proc: pty.IPty | null;
}

export default class PtyWorker extends Service {
  private sessions = new Map<string, PtySession>();

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "worker.pty",

      actions: {
        spawn: {
          params: {
            shell: { type: "string", optional: true },
            cols: { type: "number", optional: true },
            rows: { type: "number", optional: true },
            cwd: { type: "string", optional: true },
          },
          handler: (ctx: Context<{ shell?: string; cols?: number; rows?: number; cwd?: string }>) => this.spawn(ctx.params),
        },

        kill: {
          params: { id: "string" },
          handler: (ctx: Context<{ id: string }>) => this.kill(ctx.params.id),
        },

        write: {
          params: { id: "string", data: "string" },
          handler: (ctx: Context<{ id: string; data: string }>) => this.write(ctx.params.id, ctx.params.data),
        },

        resize: {
          params: { id: "string", cols: { type: "number", optional: true }, rows: { type: "number", optional: true } },
          handler: (ctx: Context<{ id: string; cols?: number; rows?: number }>) =>
            this.resize(ctx.params.id, ctx.params.cols, ctx.params.rows),
        },

        status: {
          params: { id: "string" },
          handler: (ctx: Context<{ id: string }>) => this.status(ctx.params.id),
        },

        list: {
          handler: () => {
            const sessions = Array.from(this.sessions.values()).map((s) => ({
              id: s.id,
              pid: s.pid,
              shell: s.shell,
              started_at: new Date(s.startedAt).toISOString(),
              status: s.status,
              exit_code: s.exitCode,
            }));
            return { count: sessions.length, sessions };
          },
        },
      },
    });
  }

  private spawn(params: { shell?: string; cols?: number; rows?: number; cwd?: string }): any {
    const shellPath = params.shell || process.env.SHELL || "/bin/bash";
    const cols = params.cols || 80;
    const rows = params.rows || 24;
    const id = uuidv4();

    const shellProcess = pty.spawn(shellPath, [], {
      name: "xterm-256color",
      cols,
      rows,
      cwd: params.cwd || process.env.HOME || "/home/codex",
      env: { ...(process.env as Record<string, string>), TERM: "xterm-256color" },
    });

    const session: PtySession = {
      id,
      pid: shellProcess.pid,
      shell: shellPath,
      startedAt: Date.now(),
      cols,
      rows,
      outputBuffer: "",
      status: "running",
      exitCode: null,
      proc: shellProcess,
    };

    shellProcess.onData((data: string) => {
      session.outputBuffer += data;
      if (session.outputBuffer.length > 1_000_000) {
        session.outputBuffer = session.outputBuffer.slice(-500_000);
      }
      // Transport bridge event — any connected pty-transport adapter (or
      // multiple) forwards this to its client(s) for this session id.
      this.broker.emit("pty.output", { id, data });
    });

    shellProcess.onExit(({ exitCode }: { exitCode: number }) => {
      session.status = "exited";
      session.exitCode = exitCode;
      session.proc = null;
      // Keep the session row briefly for status queries, then drop.
      setTimeout(() => this.sessions.delete(id), 60_000);
      this.broker.emit("pty.exit", { id, exitCode });
    });

    this.sessions.set(id, session);
    this.logger.info(`pty spawn id=${id.slice(0, 8)} pid=${session.pid} shell=${shellPath}`);

    return {
      id,
      pid: session.pid,
      shell: shellPath,
      cols,
      rows,
      status: "running",
      started_at: new Date(session.startedAt).toISOString(),
    };
  }

  private kill(id: string): any {
    const session = this.sessions.get(id);
    if (!session) throw new Error(`pty session ${id} not found`);
    if (session.status !== "running") return { id, status: session.status, exit_code: session.exitCode };
    try {
      process.kill(session.pid, "SIGTERM");
      setTimeout(() => {
        try { process.kill(session.pid, "SIGKILL"); } catch { /* gone */ }
      }, 3000);
    } catch { /* already gone */ }
    this.logger.info(`pty kill id=${id.slice(0, 8)} pid=${session.pid}`);
    return { id, status: "terminating", pid: session.pid };
  }

  private write(id: string, data: string): any {
    const session = this.sessions.get(id);
    if (!session) throw new Error(`pty session ${id} not found`);
    if (session.status !== "running" || !session.proc) {
      throw new Error(`pty session ${id} is not running`);
    }
    session.proc.write(data);
    return { id, status: "running" };
  }

  private resize(id: string, cols?: number, rows?: number): any {
    const session = this.sessions.get(id);
    if (!session) throw new Error(`pty session ${id} not found`);
    const c = cols ?? session.cols;
    const r = rows ?? session.rows;
    if (session.status === "running" && session.proc) {
      session.proc.resize(c, r);
    }
    session.cols = c;
    session.rows = r;
    return { id, cols: c, rows: r, status: session.status };
  }

  private status(id: string): any {
    const session = this.sessions.get(id);
    if (!session) throw new Error(`pty session ${id} not found`);
    return {
      id: session.id,
      pid: session.pid,
      shell: session.shell,
      status: session.status,
      exit_code: session.exitCode,
      cols: session.cols,
      rows: session.rows,
      started_at: new Date(session.startedAt).toISOString(),
      output_tail: session.outputBuffer.slice(-2000),
    };
  }
}
