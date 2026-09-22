import path from "node:path";
import crypto from "node:crypto";
import {
  PipelineState,
  PlanCard,
  BuilderStatus,
  CircuitBreaker,
  AgentRole,
  AgentStatus,
  AgentState,
  PipelineMetrics,
} from "./types";
import { PlanWatcher } from "./watchers/plan-watcher";
import { BuilderWatcher } from "./watchers/builder-watcher";
import { CircuitBreakerWatcher } from "./watchers/cb-watcher";
import { AgentWatcher } from "./watchers/agent-watcher";
import { AnalyticsEngine } from "./watchers/analytics-engine";
import {
  initDb,
  getDb,
  getPlansGroupedByStatus,
  planRowToPlanCard,
  getReceiptCount,
  getRunningSessions,
  getBreaker,
  getPlanById,
  upsertPlan,
  qOne,
  qAll,
  qRun,
  getStaleSessions,
  getSession,
  endSession,
  releaseSessionTickets,
  createTicketIfMissing,
  checkpointWal,
  getNewestCompileVerdictForPlan,
  verdictBlocksBootstrap,
} from "./db";
import * as api from "./conduit-client";
import { receiptProvenanceMetadata } from "./receipts";
import { breakerRowToStatus } from "./watchers/cb-watcher";

export class PipelineWatcher {
  private listeners: Array<(event: any) => void> = [];
  private planWatcher: PlanWatcher;
  private builderWatcher: BuilderWatcher;
  private cbWatcher: CircuitBreakerWatcher;
  private agentWatcher: AgentWatcher;
  private analytics: AnalyticsEngine;
  baseDir: string;
  graphDir: string;

  constructor(baseDir: string, graphDir?: string) {
    this.baseDir = baseDir;
    this.graphDir = graphDir || path.resolve(baseDir, "../graph");
    const emit = (event: any) => this.emit(event);
    this.planWatcher = new PlanWatcher(this.graphDir, emit);
    this.builderWatcher = new BuilderWatcher(baseDir, emit);
    this.cbWatcher = new CircuitBreakerWatcher(baseDir, emit);
    this.agentWatcher = new AgentWatcher(baseDir, emit);
    this.analytics = new AnalyticsEngine();
  }

  onEvent(callback: (event: any) => void) {
    this.listeners.push(callback);
  }

  private emit(event: any) {
    for (const listener of this.listeners) {
      listener(event);
    }
  }

  /** Public emit for tools to fire SSE events (e.g., after receipt issuance) */
  emitToolEvent(event: any) {
    this.emit(event);
  }

  /** Remove a plan from all in-memory plan-watcher arrays. Used by delete_plan
   *  so the plan disappears from /state immediately without waiting for a rescan. */
  removePlanFromMemory(planNumber: string): void {
    const dirs = [
      "planning",
      "pending",
      "active",
      "completed",
      "blocked",
      "hold",
    ] as const;
    for (const dir of dirs) {
      const idx = this.planWatcher.plans[dir].findIndex(
        (p: PlanCard) => p.planNumber === planNumber,
      );
      if (idx !== -1) {
        this.planWatcher.plans[dir].splice(idx, 1);
      }
    }
  }

  async getState(): Promise<PipelineState> {
    const now = new Date().toISOString();
    const grouped = await getPlansGroupedByStatus();

    // Circuit breaker from database (authoritative)
    const breakerRow = await getBreaker();
    const cbStatus = breakerRowToStatus(breakerRow);

    const fsDirs: Array<"pending" | "active" | "completed" | "blocked"> = [
      "pending",
      "active",
      "completed",
      "blocked",
    ];

    const result = {
      pending: [] as PlanCard[],
      active: [] as PlanCard[],
      completed: [] as PlanCard[],
      blocked: [] as PlanCard[],
      archived: [] as PlanCard[],
      planning: [] as PlanCard[],
      hold: [] as PlanCard[],
    };
    const placed = new Set<string>();

    // Receipts are the sole authority for pending/active/completed/blocked.
    // Plans are placed according to their derived_status from the DB —
    // no filesystem fallback. If a plan file is in the wrong directory,
    // it does NOT override the receipt chain.
    for (const dir of fsDirs) {
      for (const row of grouped[dir]) {
        const card = planRowToPlanCard(row);
        placed.add(card.planNumber);
        result[dir].push(card);
      }
    }

    // Planning and hold: populated from DB (receipt-driven like other states).
    // The plan_status view now correctly derives PLANNING and HOLD, so
    // these states are receipt-authoritative — no filesystem fallback needed.
    for (const dir of ["planning", "hold"] as const) {
      for (const row of grouped[dir]) {
        const card = planRowToPlanCard(row);
        if (!placed.has(card.planNumber)) {
          result[dir].push(card);
          placed.add(card.planNumber);
        }
      }
    }

    // Archived from filesystem (.bak/completed-plans/), deduplicated.
    // Guard: skip soft-deleted plans (deleted=1) for consistency with
    // the proposed/planning guard above.
    const archivedChecks = await Promise.all(
      this.planWatcher.plans.archived.map(async (p) => ({
        p,
        deleted: !placed.has(p.planNumber) ? (await getPlanById(p.planNumber))?.deleted : true,
      }))
    );
    result.archived = archivedChecks.filter(({deleted}) => !deleted).map(({p}) => p);

    // ── Attach ticket statuses to every placed plan ──
    // Batch-query all non-terminal tickets so the UI can show per-role
    // status on plan cards without N+1 queries.
    const allPlanIds = [...placed];
    if (allPlanIds.length > 0) {
      const ticketRows = await qAll(
        `SELECT plan_id, role, status, id, created_at, expires_at, objective, claimed_at, session_id FROM vision.tickets
         WHERE plan_id = ANY(@planIds)
         AND status IN ('open','claimed','completed','failed','expired','stale','cancelled','abandoned')
         ORDER BY plan_id, role`,
        { planIds: allPlanIds }
      ) as Array<{ plan_id: string; role: string; status: string; id: string; created_at: string; expires_at: string | null; objective: string | null; claimed_at: string | null; session_id: string | null }>;

      // Build map: plan_id → { role: { status, id, created_at, expires_at, objective, claimedAt?, claimedSession? } }
      const ticketMap = new Map<string, Record<string, { status: string; id: string; created_at: string; expires_at?: string; objective?: string; claimedAt?: string; claimedSession?: string }>>();
      for (const t of ticketRows) {
        if (!ticketMap.has(t.plan_id)) ticketMap.set(t.plan_id, {});
        ticketMap.get(t.plan_id)![t.role] = {
          status: t.status,
          id: t.id,
          created_at: t.created_at,
          expires_at: t.expires_at || undefined,
          objective: t.objective || undefined,
          claimedAt: t.claimed_at || undefined,
          claimedSession: t.session_id || undefined,
        };
      }

      // Merge into all plan arrays
      for (const dir of fsDirs) {
        for (const card of result[dir]) {
          card.ticketStatuses = ticketMap.get(card.planNumber);
        }
      }
      for (const dir of ["planning", "hold"] as const) {
        for (const card of result[dir]) {
          card.ticketStatuses = ticketMap.get(card.planNumber);
        }
      }
      for (const card of result.archived) {
        card.ticketStatuses = ticketMap.get(card.planNumber);
      }
    }

    // Sort columns: pending/active/blocked ascending, completed/archived descending
    // (most recently completed/archived at top, oldest pending/active/blocked at top)
    const sortAsc = (a: PlanCard, b: PlanCard) => {
      const an = parseInt(a.planNumber, 10);
      const bn = parseInt(b.planNumber, 10);
      if (isNaN(an) && isNaN(bn))
        return a.planNumber.localeCompare(b.planNumber);
      if (isNaN(an)) return 1;
      if (isNaN(bn)) return -1;
      return an - bn;
    };
    const sortDesc = (a: PlanCard, b: PlanCard) => sortAsc(b, a);
    result.pending.sort(sortAsc);
    result.active.sort(sortAsc);
    result.blocked.sort(sortAsc);
    result.planning.sort(sortAsc);
    result.hold.sort(sortAsc);
    result.completed.sort(sortDesc);
    result.archived.sort(sortDesc);

    return {
      plans: result,
      builder: this.builderWatcher.status,
      circuitBreaker: cbStatus,
      agents: this.agentWatcher.getAgents(),
      receiptStats: await getReceiptCount(),
      prompts: this.getPrompts(),
      lastUpdated: now,
    };
  }

  getAgents(): AgentState[] {
    return this.agentWatcher.getAgents();
  }

  getArchiveEntries(): any[] {
    // Archives are historical audit artifacts on filesystem — not read back for operational state.
    return [];
  }

  getInspections(): any[] {
    // Inspections are audit artifacts on filesystem — not read back for operational state.
    return [];
  }

  getPrompts(): any[] {
    // Prompts are audit artifacts on filesystem — not read back for operational state.
    return [];
  }

  getChangeReports(): any[] {
    // Change reports are audit artifacts on filesystem — not read back for operational state.
    return [];
  }

  updateAgentHeartbeat(
    role: AgentRole,
    status: AgentStatus,
    detail: string | null,
    pid: number | null,
  ) {
    this.agentWatcher.updateHeartbeat(role, status, detail, pid);
  }

  updateAgentFinished(role: AgentRole) {
    this.agentWatcher.updateFinished(role);
  }

  // Periodic full-state heartbeat — keeps clients caught up at 10s intervals
  startStateHeartbeat() {
    setInterval(async () => {
      const state = await this.getState();
      this.emit({ type: "state_full", data: state });
    }, 10000);
  }

  // ── Auto-bootstrap: detect nebula-created plans (no receipts/tickets) ──
  // When nebula-mcp creates plans via POST /api/plans, they land in
  // nebula.implementation_plans with status='pending' but no PLAN_CREATE
  // receipt or builder ticket. This method detects those and bootstraps
  // the execution pipeline (receipt + ticket) so they become visible.
  private bootstrapTimer: ReturnType<typeof setTimeout> | null = null;
  private bootstrapInFlight: Promise<{ bootstrapped: number; failed: number }> | null = null;
  private bootstrapStarted = false;
  private bootstrapFailureCount = 0;
  private readonly BOOTSTRAP_INTERVAL_MS = 30_000; // 30s, matches PlanWatcher refresh
  private readonly BOOTSTRAP_MAX_BACKOFF_MS = 5 * 60_000;

  /**
   * Run one bootstrap pass. Calls are single-flight so a manual invocation
   * cannot overlap the scheduled pass and create duplicate work.
   */
  async bootstrapUnclaimedPlans(): Promise<{ bootstrapped: number; failed: number }> {
    if (this.bootstrapInFlight) return this.bootstrapInFlight;

    const run = this.runBootstrapPass();
    this.bootstrapInFlight = run;
    try {
      return await run;
    } finally {
      if (this.bootstrapInFlight === run) this.bootstrapInFlight = null;
    }
  }

  private async runBootstrapPass(): Promise<{ bootstrapped: number; failed: number }> {
    const db = getDb();
    let bootstrapped = 0;
    let failed = 0;

    try {
      // Find plans in nebula.implementation_plans with status='pending'
      // that have NO receipts in nebula.receipts_unified
      const { rows } = await db.query(
        `SELECT p.plan_number, p.title
         FROM nebula.implementation_plans p
         WHERE p.status = 'pending'
           AND NOT EXISTS (
             SELECT 1 FROM nebula.receipts_unified r
             WHERE r.plan_id = p.plan_number
           )
         ORDER BY p.created_at ASC
         LIMIT 50`
      );
      if (rows.length > 0) {
        console.log(
          `[bootstrap] discovery: ${rows.length} nebula-first plan(s) awaiting lifecycle: ${rows.map((r: any) => r.plan_number).join(", ")}`
        );
      }

      for (const plan of rows) {
        try {
          // D5 gate — consult the newest compile verdict before bootstrapping.
          // Newest-FAIL never gets a ticket; a PASS on a reserved (R3/R4)
          // route is held (R-A-003: never auto-armed); PASS on conduit/
          // conduit-review is release-eligible; no verdict = legacy unchanged.
          // The lookup resolves the plan's compile-unit entityKey (via
          // work_requests context.plan_id) and also matches release-time
          // re-parented verdicts (plan_id) — newest wins.
          const verdict = await getNewestCompileVerdictForPlan(plan.plan_number);
          if (verdictBlocksBootstrap(verdict)) {
            const why =
              verdict!.verdict_type === "WR_COMPILE_FAIL"
                ? "newest compile verdict is WR_COMPILE_FAIL"
                : `newest compile verdict is PASS on reserved route`;
            console.log(
              `Auto-bootstrap blocked for plan ${plan.plan_number}: ${why}`
            );
            continue;
          }

          const now = new Date().toISOString();
          const receiptId = crypto.randomUUID();

          // Create builder ticket so the conduit can pick this up.
          // A stale open ticket (earlier half-completed bootstrap: ticket
          // written, receipt lost) raises 23505 on the (plan_id, role)
          // WHERE status='open' index. That is NOT a failure — reuse the
          // existing ticket and continue to the receipt, which is the
          // missing half. Silent-skip here is what permanently wedged
          // nebula-first plans (see ruling on escalation 3cf0b72e).
          let ticketId: string | null = null;
          try {
            ticketId = await createTicketIfMissing(
              plan.plan_number,
              "builder",
              receiptId,
              now,
              plan.title,
              "",
              "builder",
            );
          } catch (tickErr: any) {
            if (tickErr?.code !== "23505") throw tickErr;
            const ex = await db.query(
              `SELECT id FROM vision.tickets
               WHERE plan_id = $1 AND role = 'builder' AND status = 'open'
               ORDER BY created_at DESC LIMIT 1`,
              [plan.plan_number]
            );
            ticketId = ex.rows[0]?.id ?? null;
            console.warn(
              `[bootstrap] plan ${plan.plan_number}: reused stale open ticket ${ticketId} (23505 on insert)`
            );
          }

          // Issue PLAN_CREATE receipt with ticket reference
          // C1 gate 1 (Lilac): declaring-producer provenance rides in metadata.
          await api.insertReceipt({
            id: receiptId,
            plan_id: plan.plan_number,
            type: "PLAN_CREATE",
            agent_role: "planner",
            session_id: "",
            ticket_id: ticketId,
            artifact_path: null,
            summary: `Auto-bootstrapped: ${plan.title}`,
            metadata_json: JSON.stringify({
              auto_bootstrapped: true,
            }),
            tokens_used: 0,
            created_at: now,
            producer_id: "conduit-mcp",
            source_channel: "conduit-mcp-http",
            correlation_id: receiptId,
          });

          checkpointWal();

          console.log(
            `[${now}] Auto-bootstrapped plan ${plan.plan_number}: ${plan.title}` +
            (ticketId ? ` (ticket ${ticketId})` : "")
          );

          // SSE: notify clients so the plan appears in the UI immediately
          this.emit({
            type: "plan_state_changed",
            data: {
              planNumber: plan.plan_number,
              planTitle: plan.title,
              receiptType: "PLAN_CREATE",
              agentRole: "planner",
              newDerivedStatus: "PLAN_CREATE",
              timestamp: now,
            },
          });

          bootstrapped++;
        } catch (planErr: any) {
          // 23505 = unique_violation — another bootstrapper already handled this plan
          if (planErr?.code === "23505") {
            console.warn(
              `[bootstrap] plan ${plan.plan_number}: 23505 during bootstrap pass (skipped)`
            );
            continue;
          }
          failed++;
          console.warn(
            `Auto-bootstrap failed for plan ${plan.plan_number}:`,
            planErr?.message || planErr,
          );
        }
      }

      if (failed > 0) {
        console.warn(
          `Auto-bootstrap pass completed with ${failed} failed plan(s); ` +
          "they will be retried on the next pass",
        );
      }
    } catch (err: any) {
      // Surface query/connection failures to the scheduler so it can apply
      // backoff instead of silently treating an unavailable database as an
      // empty backlog.
      console.warn("Auto-bootstrap query failed:", err?.message || err);
      throw err;
    }

    return { bootstrapped, failed };
  }

  private scheduleAutoBootstrap(delayMs: number): void {
    if (!this.bootstrapStarted) return;
    if (this.bootstrapTimer) clearTimeout(this.bootstrapTimer);
    this.bootstrapTimer = setTimeout(() => {
      this.bootstrapTimer = null;
      void this.runScheduledBootstrap();
    }, delayMs);
  }

  private async runScheduledBootstrap(): Promise<void> {
    if (!this.bootstrapStarted) return;

    let nextDelay = this.BOOTSTRAP_INTERVAL_MS;
    try {
      const result = await this.bootstrapUnclaimedPlans();
      if (result.failed > 0) {
        throw new Error(`${result.failed} plan bootstrap(s) failed`);
      }
      this.bootstrapFailureCount = 0;
      if (result.bootstrapped > 0) {
        console.log(`Auto-bootstrap: bootstrapped ${result.bootstrapped} plan(s)`);
      }
    } catch (err: any) {
      this.bootstrapFailureCount++;
      const exponent = Math.min(this.bootstrapFailureCount - 1, 10);
      nextDelay = Math.min(
        this.BOOTSTRAP_INTERVAL_MS * 2 ** exponent,
        this.BOOTSTRAP_MAX_BACKOFF_MS,
      );
      console.warn(
        `Auto-bootstrap pass ${this.bootstrapFailureCount} failed; ` +
        `retrying in ${nextDelay}ms: ${err?.message || err}`,
      );
    } finally {
      this.scheduleAutoBootstrap(nextDelay);
    }
  }

  startAutoBootstrap() {
    // Idempotent: initialization/restart hooks must not create multiple loops.
    if (this.bootstrapStarted) return;
    this.bootstrapStarted = true;
    this.bootstrapFailureCount = 0;
    void this.runScheduledBootstrap();
  }

  stopAutoBootstrap() {
    this.bootstrapStarted = false;
    if (this.bootstrapTimer) {
      clearTimeout(this.bootstrapTimer);
      this.bootstrapTimer = null;
    }
  }

  // Stale session sweeper — runs every 60s, kills sessions whose last_heartbeat_at
  // exceeds STALE_SESSION_THRESHOLD_SECONDS (default 300s = 5 min).
  private staleSweepInterval: ReturnType<typeof setInterval> | null = null;
  private readonly STALE_SESSION_THRESHOLD_SECONDS = parseInt(
    process.env.PIPELINE_STALE_SESSION_THRESHOLD || "300", 10
  );

  startStaleSessionSweep() {
    this.staleSweepInterval = setInterval(async () => {
      try {
        const stale = await getStaleSessions(this.STALE_SESSION_THRESHOLD_SECONDS);
        for (const session of stale) {
          const now = new Date().toISOString();
          const sessionId = session.id;
          let killedPids: number[] = [];
          const errors: string[] = [];

          // Try to SIGKILL the process group, then the individual PID
          if (session.pid) {
            try {
              process.kill(-session.pid, "SIGKILL");
              killedPids.push(session.pid);
            } catch (e: any) {
              try {
                process.kill(session.pid, "SIGKILL");
                killedPids.push(session.pid);
              } catch (e2: any) {
                errors.push(`PID ${session.pid}: ${e2.message}`);
              }
            }
          }

          // Close the session in DB
          await endSession(sessionId, 137, now);

          // Release any tickets held by the session
          try {
            await releaseSessionTickets(sessionId);
          } catch (_) { /* best-effort */ }

          // Update in-memory agent state
          const role = session.agent_role as AgentRole;
          this.agentWatcher.updateHeartbeat(role, "gone", `stale-session-reaped (${sessionId})`, null);

          console.log(
            `[stale-sweep] reaped session ${sessionId} (role=${session.agent_role}, `
            + `PID=${session.pid}, killed=${killedPids.join(",")}, errors=${errors.join(";")})`
          );

          this.emit({
            type: "session_killed",
            data: { sessionId, reason: "stale", killedPids, errors },
          });
        }
      } catch (e: any) {
        console.error(`[stale-sweep] error: ${e.message}`);
      }
    }, 60000);
  }

  async computeAnalytics(): Promise<PipelineMetrics> {
    const receiptStats = await getReceiptCount();
    const implCount = receiptStats.find((r) => r.type === "IMPLEMENTATION")?.count ?? 0;
    const killedRow = await qOne(
      "SELECT COUNT(*) as count FROM vision.tickets WHERE status IN ('failed', 'abandoned')",
    );
    const killedCount = killedRow?.count ?? 0;
    return this.analytics.compute(this.planWatcher.plans, {
      totalBuildersLaunched: implCount,
      totalBuildersKilled: killedCount,
    });
  }

  async createPlan(meta: {
    title: string;
    project: string;
    goal: string;
    filesAffected: string[];
    acceptanceCriteria: string[];
    dependencies: string[];
    promptRef?: string;
  }): Promise<{ planNumber: string; fileName: string; filePath: string }> {
    const row = await qOne("SELECT MAX(CAST(id AS INTEGER)) as max_id FROM nebula.plans");
    const maxId = row?.max_id ?? 0;
    const nextNum = String(maxId + 1).padStart(4, "0");
    const slug = meta.title
      .toLowerCase()
      .replace(/[^a-z0-9\s-]/g, "")
      .trim()
      .replace(/\s+/g, "-")
      .slice(0, 50);
    const fileName = `${slug}-v${nextNum}.md`;
    const filePath = "";
    return { planNumber: nextNum, fileName, filePath };
  }

  async updatePlanMetadata(
    planNumber: string,
    updates: {
      title?: string;
      project?: string;
      goal?: string;
      filesAffected?: string[];
      acceptanceCriteria?: string[];
      dependencies?: string[];
    },
  ): Promise<{ found: boolean; filePath?: string }> {
    const now = new Date().toISOString();
    const dbPlan = await getPlanById(planNumber);
    if (dbPlan) {
      await upsertPlan({
        id: dbPlan.id,
        file_name: dbPlan.file_name,
        title: updates.title ?? dbPlan.title,
        project: updates.project ?? dbPlan.project,
        goal: updates.goal ?? dbPlan.goal,
        content: dbPlan.content,
        files_affected:
          updates.filesAffected !== undefined
            ? JSON.stringify(updates.filesAffected)
            : dbPlan.files_affected,
        acceptance_criteria:
          updates.acceptanceCriteria !== undefined
            ? JSON.stringify(updates.acceptanceCriteria)
            : dbPlan.acceptance_criteria,
        dependencies:
          updates.dependencies !== undefined
            ? JSON.stringify(updates.dependencies)
            : dbPlan.dependencies,
        prompt_ref: dbPlan.prompt_ref,
        notes: dbPlan.notes,
        priority: dbPlan.priority,
        created_at: dbPlan.created_at,
        updated_at: now,
      });
    }
    return { found: dbPlan !== undefined };
  }

  async initialize() {
    await initDb(this.baseDir);
    await this.planWatcher.initialize();
    await this.builderWatcher.initialize();
    await this.cbWatcher.initialize();
    await this.agentWatcher.initialize();
    this.startStateHeartbeat();
    this.startStaleSessionSweep();
    this.startAutoBootstrap();
  }

  destroy() {
    this.stopAutoBootstrap();
    this.planWatcher.destroy();
    this.builderWatcher.destroy();
    this.cbWatcher.destroy();
    this.agentWatcher.destroy();
    if (this.staleSweepInterval) clearInterval(this.staleSweepInterval);
  }
}
