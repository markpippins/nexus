import { BaseWatcher } from "./base";
import { AgentRole, AgentStatus, AgentState } from "../types";

export class AgentWatcher extends BaseWatcher {
  agents: Map<AgentRole, AgentState> = new Map();
  private interval: ReturnType<typeof setInterval> | null = null;

  async initialize(): Promise<void> {
    this.startHeartbeatCheck();
  }

  destroy(): void {
    if (this.interval) clearInterval(this.interval);
  }

  updateHeartbeat(
    role: AgentRole,
    status: AgentStatus,
    detail: string | null,
    pid: number | null,
  ) {
    const now = new Date().toISOString();
    const agent: AgentState = { role, status, detail, pid, lastHeartbeat: now };
    const prev = this.agents.get(role);
    this.agents.set(role, agent);
    if (!prev || prev.status !== status || prev.detail !== detail) {
      this.emit({ type: "agent_update", data: agent });
    }
  }

  updateFinished(role: AgentRole) {
    this.updateHeartbeat(role, "idle", null, null);
  }

  getAgents(): AgentState[] {
    return Array.from(this.agents.values());
  }

  private startHeartbeatCheck() {
    this.interval = setInterval(() => {
      const now = Date.now();
      let changed = false;
      for (const [role, agent] of this.agents) {
        if (agent.status === "gone") continue;
        if (!agent.lastHeartbeat) continue;
        const elapsed = (now - new Date(agent.lastHeartbeat).getTime()) / 1000;
        let newStatus: AgentStatus = agent.status;
        if (elapsed > 120) newStatus = "gone";
        else if (elapsed > 30 && agent.status === "working")
          newStatus = "stale";
        if (newStatus !== agent.status) {
          agent.status = newStatus;
          changed = true;
          this.emit({ type: "agent_update", data: { ...agent } });
        }
      }
      if (changed) {
        this.emit({
          type: "agent_update",
          data: Array.from(this.agents.values()),
        });
      }
    }, 5000);
  }
}
