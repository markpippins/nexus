import { BaseWatcher } from "./base";
import { BuilderStatus } from "../types";
import { getRunningSessions } from "../db";

const BUILDER_CHECK_INTERVAL = 5000;
const BUILDER_STALE_DEAD = 600;

export class BuilderWatcher extends BaseWatcher {
  status: BuilderStatus = { pid: null, status: "idle" };
  private interval: ReturnType<typeof setInterval> | null = null;
  private previousStatus: string = "idle";
  private startedAt: number | null = null;

  async initialize(): Promise<void> {
    this.status = await this.readBuilderStatus();
    this.startHealthCheck();
  }

  destroy(): void {
    if (this.interval) clearInterval(this.interval);
  }

  private async readBuilderStatus(): Promise<BuilderStatus> {
    // Read from sessions table — the authoritative source for builder state
    const running = await getRunningSessions();
    const builderSession = running.find(
      (s) => s.agent_role === "builder" && s.is_running === 1,
    );

    if (!builderSession) {
      return { pid: null, status: "idle" };
    }

    const pid = builderSession.pid;
    const now = Math.floor(Date.now() / 1000);
    const lastActivity = builderSession.last_activity
      ? Math.floor(new Date(builderSession.last_activity).getTime() / 1000)
      : 0;
    const secondsSinceActivity = lastActivity ? now - lastActivity : 0;

    // Check if the PID is still alive
    if (pid) {
      try {
        process.kill(pid, 0);

        let status: BuilderStatus["status"] = "running";
        if (secondsSinceActivity > BUILDER_STALE_DEAD) status = "stale";

        return {
          pid,
          status,
          startedAt: builderSession.start_iso,
          lastActivity: builderSession.last_activity ?? undefined,
          elapsedSeconds: this.startedAt ? now - this.startedAt : undefined,
          workflowId: builderSession.workflow_id ?? undefined,
          runId: builderSession.run_id ?? undefined,
        };
      } catch {
        return {
          pid,
          status: "killed",
          workflowId: builderSession.workflow_id ?? undefined,
          runId: builderSession.run_id ?? undefined,
        };
      }
    }

    // Session exists but no PID — builder hasn't started yet or is between retries
    return {
      pid: null,
      status: "running",
      startedAt: builderSession.start_iso,
      lastActivity: builderSession.last_activity ?? undefined,
      workflowId: builderSession.workflow_id ?? undefined,
      runId: builderSession.run_id ?? undefined,
    };
  }

  private startHealthCheck() {
    this.interval = setInterval(async () => {
      const newStatus = await this.readBuilderStatus();
      if (newStatus.status === "running" && this.status.status !== "running") {
        this.startedAt = Math.floor(Date.now() / 1000);
      }
      if (newStatus.status === "idle") this.startedAt = null;
      this.previousStatus = newStatus.status;
      this.status = newStatus;
      this.emit({ type: "builder_update", data: newStatus });
    }, BUILDER_CHECK_INTERVAL);
  }
}
