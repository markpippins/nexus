import express from "express";
import cors from "cors";
import path from "path";
import crypto from "crypto";
import fs from "fs";
import { PipelineWatcher } from "./watcher";
import { registerToolHandlers, toolDefinitions } from "./tools";
import { createError, createSuccess } from "./errors";
import {
  getAllSessions,
  getSession,
  endSession,
  updateSessionCost,
  tripBreaker,
  clearBreaker,
  setConduitPaused,
  getBreaker,
  getDb,
  getPlanById,
  releaseSessionTickets,
  resetAbandonedTickets,
  detectStaleTickets,
  detectExpiredTickets,
  supersedeTicket,
  cancelTicket,
  getTokenUsageByPlan,
  getTokenUsageByRole,
  getTokenUsageByTicket,
  getTicketLineage,
  getAIConfigSnapshot,
  getAIProviders,
  getAIHarnesses,
  getAIModels,
  getAIRoleConfigs,
  upsertAIProvider,
  upsertAIHarness,
  upsertAIModel,
  upsertAIRoleConfig,
  upsertRoleModels,
  deleteAIProvider,
  deleteAIHarness,
  deleteAIModel,
  seedDefaultAIConfig,
  importAIConfig,
  validateAIConfig,
  requestSchedulerWake,
  startSession,
  updateSessionPid,
  replayGovernanceEvents,
  listGovernanceEvents,
  createWorkRequest,
  getWorkRequest,
  listWorkRequests,
  listReceiptsByPlan,
  insertReceipt,
} from "./db";
import http from "http";
import { loadEnv } from "./env"; // shared .env loader (no dotenv dependency)
import {
  ensureTemporalConnected,
  startPlanWorkflow,
  startTestInvokeWorkflow,
  signalWorkflowCancel,
  listWorkflows,
  sessionToWorkflowId,
} from "./temporal-client";

// .env already loaded by env.ts at module evaluation time

const PORT = parseInt(process.env.PORT || "3100", 10);
const PIPELINE_DIR =
  process.env.PIPELINE_DIR ||
  path.resolve(__dirname, "../../../../nexus/.conduit-data");
const GRAPH_DIR =
  process.env.GRAPH_DIR ||
  path.resolve(PIPELINE_DIR, "../graph");

const app = express();
app.use(cors());
app.use(express.json());

// ── MCP JSON-RPC endpoint (POST /) ─────────────────────────────
// Standard MCP protocol via Streamable HTTP transport (rmcp client).
app.post("/", express.json(), async (req, res) => {
  const msg = req.body;
  if (!msg || msg.jsonrpc !== "2.0" || !msg.method) {
    res
      .status(400)
      .json({
        jsonrpc: "2.0",
        error: { code: -32600, message: "Invalid Request" },
        id: null,
      });
    return;
  }

  const { method, params, id } = msg;

  // Notifications have no id — no response expected
  if (
    method === "notifications/initialized" ||
    method === "notifications/cancelled"
  ) {
    res.status(202).end();
    return;
  }

  const respond = (result: any) => res.json({ jsonrpc: "2.0", result, id });
  const respondError = (code: number, message: string) =>
    res.json({ jsonrpc: "2.0", error: { code, message }, id });

  try {
    switch (method) {
      case "initialize":
        respond({
          protocolVersion: "2025-03-26",
          capabilities: {
            tools: {},
            resources: {},
          },
          serverInfo: {
            name: "conduit-mcp",
            version: "1.0.0",
          },
        });
        break;

      case "tools/list": {
        respond({ tools: toolDefinitions });
        break;
      }

      case "tools/call": {
        const { name, arguments: args } = params || {};
        if (!name || !toolHandlers[name]) {
          respondError(-32601, `Unknown tool: ${name}`);
          return;
        }
        const result = await toolHandlers[name](args || {});
        respond({ content: [{ type: "text", text: JSON.stringify(result) }] });
        break;
      }

      case "resources/list":
        respond({ resources: [] });
        break;

      default:
        respondError(-32601, `Method not found: ${method}`);
    }
  } catch (err: any) {
    console.error(`[MCP] Error in ${method}:`, err.message);
    respondError(-32603, err.message || "Internal error");
  }
});

// Request logging middleware
app.use((req, res, next) => {
  const start = Date.now();
  res.on("finish", () => {
    const duration = Date.now() - start;
    console.log(
      `[${new Date().toISOString()}] ${req.method} ${req.path} ${res.statusCode} ${duration}ms`,
    );
  });
  next();
});

// SSE clients
interface SSEClient {
  id: number;
  res: express.Response;
}
const sseClients: SSEClient[] = [];
let clientIdCounter = 0;

// Initialize watcher
const watcher = new PipelineWatcher(PIPELINE_DIR, GRAPH_DIR);
// Create emitter for tools to emit SSE events (e.g., on receipt issuance)
const toolEmitter = (event: any) => watcher.emitToolEvent(event);
const toolHandlers = registerToolHandlers(watcher, toolEmitter);

// SSE endpoint
app.get("/events", async (req, res) => {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });

  res.write(
    `data: ${JSON.stringify({ type: "connected", message: "SSE connected" })}\n\n`,
  );

  // Push full state immediately so reconnecting clients get fresh data
  try {
    const initialState = await watcher.getState();
    res.write(
      `data: ${JSON.stringify({ type: "state_full", data: initialState })}\n\n`,
    );
  } catch {
    // state not ready yet, client will get it on next heartbeat
  }

  const clientId = ++clientIdCounter;
  const client: SSEClient = { id: clientId, res };
  sseClients.push(client);

  req.on("close", () => {
    const idx = sseClients.findIndex((c) => c.id === clientId);
    if (idx !== -1) sseClients.splice(idx, 1);
  });
});

// Broadcast SSE events
watcher.onEvent((event: any) => {
  const data = `data: ${JSON.stringify(event)}\n\n`;
  for (const client of sseClients) {
    client.res.write(data);
  }
});

// State endpoint
app.get("/state", async (_req, res) => {
  const state = await watcher.getState();

  // Phase 3: Enrich with Temporal workflow counts
  try {
    if (await ensureTemporalConnected()) {
      const workflows = await listWorkflows(
        'WorkflowType = "PlanExecutionWorkflow"'
      );
      const counts = { running: 0, completed: 0, failed: 0, cancelled: 0 };
      for (const wf of workflows) {
        const s = wf.status.toLowerCase();
        if (s === "running") counts.running++;
        else if (s === "completed") counts.completed++;
        else if (s === "failed") counts.failed++;
        else if (s === "cancelled") counts.cancelled++;
      }
      (state as any).temporal = {
        connected: true,
        address: process.env.TEMPORAL_ADDRESS || "localhost:7233",
        namespace: process.env.TEMPORAL_NAMESPACE || "conduit",
        schedulerIntervalMs: 30000,
        workflowCounts: { ...counts, total: workflows.length },
      };
    } else {
      (state as any).temporal = { connected: false };
    }
  } catch (e: any) {
    console.warn(`[state] Temporal enrichment failed: ${e.message}`);
    (state as any).temporal = { connected: false };
  }

  res.json(state);
});

// MCP tools (HTTP POST) with standardized errors and request IDs
app.post("/tools/call", async (req, res) => {
  const requestId = crypto.randomUUID();
  const start = Date.now();
  const { name, arguments: args } = req.body;

  if (!name || !toolHandlers[name]) {
    console.log(
      `[${new Date().toISOString()}] TOOL ${name || "(missing)"} NOT_FOUND ${Date.now() - start}ms`,
    );
    res
      .status(400)
      .json(
        createError("TOOL_NOT_FOUND", `Unknown tool: ${name}`, null, requestId),
      );
    return;
  }

  try {
    const result = await toolHandlers[name](args || {});
    console.log(
      `[${new Date().toISOString()}] TOOL ${name} OK ${Date.now() - start}ms`,
    );
    res.json(createSuccess(result, requestId));
  } catch (err: any) {
    // Check if it's our structured error
    if (err?.error?.code) {
      res.status(400).json({ ...err, error: { ...err.error, requestId } });
    } else {
      console.log(
        `[${new Date().toISOString()}] TOOL ${name} ERROR ${Date.now() - start}ms: ${err.message}`,
      );
      res
        .status(500)
        .json(createError("INTERNAL_ERROR", err.message, null, requestId));
    }
  }
});

// Tool definitions endpoint (MCP discovery)
app.get("/tools", async (_req, res) => {
  res.json({ tools: toolDefinitions });
});

// Health check (read-only)
app.get("/health", async (_req, res) => {
  res.json({
    status: "ok",
    port: PORT,
    pid: process.pid,
    timestamp: new Date().toISOString(),
  });
});

// Phase 3: Workflow status endpoint — lists PlanExecutionWorkflows from Temporal
app.get("/workflows", async (req, res) => {
  try {
    if (!(await ensureTemporalConnected())) {
      res.json({
        connected: false,
        counts: { running: 0, completed: 0, failed: 0, cancelled: 0, total: 0 },
        workflows: [],
      });
      return;
    }

    const statusFilter = req.query.status as string | undefined;
    let query = 'WorkflowType = "PlanExecutionWorkflow"';
    if (statusFilter) {
      // Map uppercase API values to Temporal's native mixed-case status values
      const temporalStatus: Record<string, string> = {
        RUNNING: "Running",
        COMPLETED: "Completed",
        FAILED: "Failed",
        CANCELLED: "Canceled",
        TERMINATED: "Terminated",
        TIMED_OUT: "TimedOut",
      };
      const s = statusFilter.toUpperCase();
      const native = temporalStatus[s];
      if (native) {
        query += ` AND ExecutionStatus = "${native}"`;
      }
    }

    const workflows = await listWorkflows(query);

    const counts = { running: 0, completed: 0, failed: 0, cancelled: 0 };
    for (const wf of workflows) {
      const s = wf.status.toLowerCase();
      if (s === "running") counts.running++;
      else if (s === "completed") counts.completed++;
      else if (s === "failed") counts.failed++;
      else if (s === "cancelled") counts.cancelled++;
    }

    res.json({
      connected: true,
      counts: { ...counts, total: workflows.length },
      workflows,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Ticket lifecycle detection (v080 — dedicated endpoint, separate from health)
app.post("/tickets/detect", async (_req, res) => {
  const stale = await detectStaleTickets();
  const expired = await detectExpiredTickets();
  res.json({
    detected: true,
    stale,
    expired,
    timestamp: new Date().toISOString(),
  });
});

// Sessions endpoint (v066 — database-backed session history)
// Sessions now own their workflow metadata natively (workflow_id, run_id, etc.)
app.get("/sessions", async (_req, res) => {
  const sessions = await getAllSessions();
  res.json(sessions);
});

// Update session cost (v072 — captured after session ends by executor_cloud.py)
app.post("/sessions/:sessionId/cost", async (req, res) => {
  const { sessionId } = req.params;

  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  const { cost_usd } = req.body;
  if (typeof cost_usd !== "number" || cost_usd < 0) {
    res
      .status(400)
      .json({ error: "Invalid cost_usd — must be a non-negative number" });
    return;
  }

  try {
    await updateSessionCost(sessionId, cost_usd);
    res.json({ updated: true, sessionId, cost_usd });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Kill a running session (v072 — kill harness from UI)
app.post("/sessions/:sessionId/kill", async (req, res) => {
  const { sessionId } = req.params;

  // Sanitize sessionId
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  const session = await getSession(sessionId);
  if (!session) {
    res.status(404).json({ error: `Session ${sessionId} not found` });
    return;
  }

  if (!session.is_running) {
    res
      .status(400)
      .json({ killed: false, error: "Session is not running", sessionId });
    return;
  }

  const now = new Date().toISOString();
  let cancelledViaTemporal = false;
  let killedPids: number[] = [];
  const errors: string[] = [];

  // ── Phase 3: Try Temporal signal first (graceful cancellation) ──
  const workflowId = sessionToWorkflowId(session);
  if (workflowId && (await ensureTemporalConnected())) {
    try {
      await signalWorkflowCancel(workflowId);
      cancelledViaTemporal = true;
      console.log(
        `[${now}] KILL session ${sessionId} → Temporal signal 'cancel' sent to ${workflowId}`,
      );
    } catch (e: any) {
      errors.push(`Temporal signal failed: ${e.message}`);
      console.warn(
        `[${now}] KILL session ${sessionId} → Temporal signal failed: ${e.message}`,
      );
    }
  }

  // ── Fallback: SIGKILL the process if Temporal was unavailable ──
  if (!cancelledViaTemporal && session.pid) {
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

  // ── Always run DB cleanup (safe even if workflow already cleaned up) ──
  await endSession(sessionId, 137, now);

  const released = await releaseSessionTickets(sessionId);
  if (released > 0) {
    console.log(
      `[${now}] Released ${released} ticket(s) from killed session ${sessionId}`,
    );
  }

  // Broadcast SSE event so UI updates immediately
  for (const client of sseClients) {
    client.res.write(
      `data: ${JSON.stringify({
        type: "session_killed",
        data: {
          sessionId,
          cancelledViaTemporal,
          workflowId: workflowId || null,
          killedPids,
          timestamp: now,
        },
      })}\n\n`,
    );
  }

  res.json({
    killed: true,
    sessionId,
    cancelledViaTemporal,
    workflowId: workflowId || null,
    pids: killedPids,
    errors: errors.length > 0 ? errors : undefined,
    timestamp: now,
  });
});

// Kill a running agent by role (v073 — kill builder/reviewer/planner from UI)
app.post("/agents/:role/kill", async (req, res) => {
  const { role } = req.params;

  // Validate role
  const validRoles = [
    "planner",
    "builder",
    "reviewer",
    "critic",
    "analyst",
    "architect",
  ];
  if (!validRoles.includes(role)) {
    res
      .status(400)
      .json({
        error: `Invalid role: ${role}. Must be one of: ${validRoles.join(", ")}`,
      });
    return;
  }

  const agents = watcher.getAgents();
  const agent = agents.find((a) => a.role === role);

  if (!agent || !agent.pid) {
    res
      .status(404)
      .json({
        killed: false,
        error: `No running agent found for role: ${role}`,
      });
    return;
  }

  const killedPids: number[] = [];
  const errors: string[] = [];

  // Kill the agent's process group, then fall back to direct PID
  try {
    process.kill(-agent.pid, "SIGKILL");
    killedPids.push(agent.pid);
  } catch (e: any) {
    try {
      process.kill(agent.pid, "SIGKILL");
      killedPids.push(agent.pid);
    } catch (e2: any) {
      errors.push(`PID ${agent.pid}: ${e2.message}`);
    }
  }

  // Mark agent as idle
  watcher.updateAgentFinished(role as any);

  const now = new Date().toISOString();

  // v077: Release Tickets claimed by this agent's session so plans can retry
  if (agent && (agent as any).sessionId) {
    const released = await releaseSessionTickets((agent as any).sessionId);
    if (released > 0) {
      console.log(
        `[${now}] Released ${released} ticket(s) from killed ${role} agent session ${(agent as any).sessionId}`,
      );
    }
  }
  console.log(
    `[${now}] KILL agent role=${role} pid=${agent.pid} → killed=${killedPids.length}`,
  );

  // Broadcast SSE event
  for (const client of sseClients) {
    client.res.write(
      `data: ${JSON.stringify({
        type: "agent_killed",
        data: { role, pids: killedPids, timestamp: now },
      })}\n\n`,
    );
  }

  res.json({
    killed: true,
    role,
    pids: killedPids,
    timestamp: now,
  });
});

// Trip circuit breaker (v073 — manual trip from UI)
app.post("/circuit-breaker/trip", async (req, res) => {
  const { reason, detail, retryAfter } = req.body || {};

  try {
    await tripBreaker({
      error: reason || "MANUAL_TRIP",
      detail: detail || "Manually tripped from UI",
      source: "ui",
      retryAfter: typeof retryAfter === "number" ? retryAfter : 3600,
    });

    const now = new Date().toISOString();
    console.log(
      `[${now}] CIRCUIT BREAKER tripped from UI: ${reason || "MANUAL_TRIP"}`,
    );

    // Broadcast immediately (cb-watcher polls every 5s, but we want instant feedback)
    for (const client of sseClients) {
      client.res.write(
        `data: ${JSON.stringify({
          type: "circuit_breaker_update",
          data: {
            tripped: true,
            retryAfter: retryAfter ?? 3600,
            reason: reason || "MANUAL_TRIP",
          },
          timestamp: now,
        })}\n\n`,
      );
    }

    res.json({
      tripped: true,
      reason: reason || "MANUAL_TRIP",
      timestamp: now,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Reset circuit breaker (v073 — manual reset from UI)
app.post("/circuit-breaker/reset", async (_req, res) => {
  try {
    await clearBreaker();

    // Wake the Python scheduler so it re-polls immediately instead of
    // waiting out the idle backoff (SCHEDULER_IDLE_BACKOFF, 60s).
    await requestSchedulerWake();

    // v078: Reset abandoned Tickets to open so work can resume
    const ticketsReset = await resetAbandonedTickets();

    const now = new Date().toISOString();
    console.log(
      `[${now}] CIRCUIT BREAKER reset from UI — ${ticketsReset} abandoned ticket(s) reset to open`,
    );

    // Broadcast immediately
    for (const client of sseClients) {
      client.res.write(
        `data: ${JSON.stringify({
          type: "circuit_breaker_update",
          data: { tripped: false, ticketsReset },
          timestamp: now,
        })}\n\n`,
      );
    }

    res.json({
      tripped: false,
      ticketsReset,
      timestamp: now,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Pause conduit orchestration (v073 — workflow control, not failure mode)
app.post("/conduit/pause", async (_req, res) => {
  try {
    await setConduitPaused(true);

    const now = new Date().toISOString();
    console.log(`[${now}] CONDUIT paused from UI`);

    for (const client of sseClients) {
      client.res.write(
        `data: ${JSON.stringify({
          type: "conduit_paused",
          data: { paused: true, timestamp: now },
        })}\n\n`,
      );
    }

    res.json({ paused: true, timestamp: now });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Resume conduit orchestration (v073 — workflow control)
app.post("/conduit/resume", async (_req, res) => {
  try {
    await setConduitPaused(false);

    const now = new Date().toISOString();
    console.log(`[${now}] CONDUIT resumed from UI`);

    for (const client of sseClients) {
      client.res.write(
        `data: ${JSON.stringify({
          type: "conduit_paused",
          data: { paused: false, timestamp: now },
        })}\n\n`,
      );
    }

    res.json({ paused: false, timestamp: now });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Restart builder for a specific plan (v074 — user-triggered, bypasses cursor/pause)
app.post("/plans/:planId/restart-builder", async (req, res) => {
  const { planId } = req.params;
  const force = req.query.force === "true";

  // Validate planId
  if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
    res.status(400).json({ error: "Invalid plan ID" });
    return;
  }

  // Check that the plan exists
  const plan = await getPlanById(planId);
  if (!plan) {
    res.status(404).json({ error: `Plan ${planId} not found` });
    return;
  }

  // Check circuit breaker — if tripped and not forced, return warning
  const breaker = await getBreaker();
  if (breaker.tripped === 1 && !force) {
    res.json({
      blocked: true,
      message:
        "Circuit breaker is open. Builder restart is blocked until the breaker is reset.",
      breaker: {
        tripped: true,
        error: breaker.error,
        detail: breaker.detail,
        source: breaker.source,
        trippedAt: breaker.tripped_at,
        retryAfter: breaker.retry_after,
      },
    });
    return;
  }

  // Phase 3: Start builder via Temporal instead of spawning main.py subprocess
  const now = new Date().toISOString();
  console.log(`[${now}] RESTART builder plan=${planId} force=${force} (Temporal)`);

  try {
    const handle = await startPlanWorkflow(planId, "builder", force);
    const workflowId = `plan-${planId}-builder`;

    console.log(
      `[${now}] RESTART builder plan=${planId} → workflow ${workflowId} started`,
    );

    res.json({
      restarted: true,
      planId,
      force,
      workflowId,
      runId: handle.runId || null,
      breakerTripped: breaker.tripped === 1,
      timestamp: now,
    });
  } catch (e: any) {
    console.error(
      `[${now}] RESTART builder plan=${planId} → Temporal start failed:`,
      e.message,
    );
    res.status(500).json({ error: `Failed to start builder workflow: ${e.message}` });
  }
});

// Unblock a blocked plan: delete BLOCK receipts and move back to pending (v087)
app.post("/plans/:planId/unblock", async (req, res) => {
  const { planId } = req.params;

  if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
    res.status(400).json({ error: "Invalid plan ID" });
    return;
  }

  const plan = await getPlanById(planId);
  if (!plan) {
    res.status(404).json({ error: `Plan ${planId} not found` });
    return;
  }

  // Delegate to the MCP tool handler
  const handler = toolHandlers["unblock_plan"];
  if (!handler) {
    res.status(500).json({ error: "unblock_plan handler not registered" });
    return;
  }

  handler({ planNumber: planId })
    .then((result: any) => res.json(result))
    .catch((err: any) => res.status(500).json({ error: err.message }));
});

// ── v081: Supersede ticket (replace with a new objective) ──────────
app.post("/tickets/:ticketId/supersede", async (req, res) => {
  const { ticketId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(ticketId)) {
    res.status(400).json({ error: "Invalid ticket ID" });
    return;
  }
  const { reason, replace } = req.body || {};
  try {
    // v081: supersede + optional replacement in one atomic call
    const result = await supersedeTicket(
      ticketId,
      reason || "Manually superseded",
      !!replace,
    );
    if (!result.superseded) {
      res
        .status(404)
        .json({
          superseded: false,
          error: "Ticket not found or not in a supersedeable state",
        });
      return;
    }

    const now = new Date().toISOString();
    if (result.replacementId) {
      console.log(
        `[${now}] SUPERSEDE ticket ${ticketId} → replacement ${result.replacementId}`,
      );
    }

    // Broadcast SSE event so UI updates immediately
    for (const client of sseClients) {
      client.res.write(
        `data: ${JSON.stringify({
          type: "ticket_superseded",
          data: {
            ticketId,
            replacementId: result.replacementId,
            reason: reason || "Manually superseded",
            timestamp: now,
          },
        })}\n\n`,
      );
    }

    res.json({
      superseded: true,
      ticketId,
      replacementId: result.replacementId || null,
      replaced: !!replace,
      timestamp: now,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── v081: Cancel ticket (explicit denial of authorization) ─────────
app.post("/tickets/:ticketId/cancel", async (req, res) => {
  const { ticketId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(ticketId)) {
    res.status(400).json({ error: "Invalid ticket ID" });
    return;
  }
  const { reason } = req.body || {};
  try {
    const count = await cancelTicket(ticketId, reason || "Manually cancelled");
    if (count > 0) {
      const now = new Date().toISOString();
      // Broadcast SSE event so UI updates immediately
      for (const client of sseClients) {
        client.res.write(
          `data: ${JSON.stringify({
            type: "ticket_cancelled",
            data: {
              ticketId,
              reason: reason || "Manually cancelled",
              timestamp: now,
            },
          })}\n\n`,
        );
      }
      res.json({ cancelled: true, ticketId, timestamp: now });
    } else {
      res
        .status(404)
        .json({
          cancelled: false,
          error: "Ticket not found or not in a cancellable state",
        });
    }
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── v080: Token usage reporting ────────────────────────────────────
app.get("/tokens/plan/:planId", async (req, res) => {
  const { planId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
    res.status(400).json({ error: "Invalid plan ID" });
    return;
  }
  res.json(await getTokenUsageByPlan(planId));
});
app.get("/tokens/role/:role", async (req, res) => {
  const { role } = req.params;
  if (!["builder", "reviewer", "planner", "critic"].includes(role)) {
    res.status(400).json({ error: `Invalid role: ${role}` });
    return;
  }
  res.json(await getTokenUsageByRole(role));
});
app.get("/tokens/ticket/:ticketId", async (req, res) => {
  const { ticketId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(ticketId)) {
    res.status(400).json({ error: "Invalid ticket ID" });
    return;
  }
  res.json(await getTokenUsageByTicket(ticketId));
});

// ── v081: Ticket lineage (audit trail) ─────────────────────────────
app.get("/tickets/lineage/:planId", async (req, res) => {
  const { planId } = req.params;
  if (!/^[a-zA-Z0-9_-]+$/.test(planId)) {
    res.status(400).json({ error: "Invalid plan ID" });
    return;
  }
  res.json({
    plan_id: planId,
    tickets: await getTicketLineage(planId),
  });
});

// ── Cron schedule configuration ────────────────────────────────────
// Exposes the pipeline-manager's cron interval so the UI can
// display an accurate countdown to the next scheduled execution.
// The interval is read from the PIPELINE_CRON env var (default */3).

const PIPELINE_CRON = process.env.PIPELINE_CRON || "*/3";

app.get("/config/cron", async (_req, res) => {
  // Parse the interval from cron expression (only */N supported for now)
  const match = PIPELINE_CRON.match(/^\*\/(\d+)$/);
  const intervalMinutes = match ? parseInt(match[1], 10) : 3;

  res.json({
    cron: PIPELINE_CRON,
    intervalMinutes,
    description: `Every ${intervalMinutes} minute${intervalMinutes === 1 ? '' : 's'}`,
    timestamp: new Date().toISOString(),
  });
});

// ── v105: Failure Recovery Configuration ──────────────────────────

// Get failure recovery config (from circuit_breaker row)
app.get("/config/failure-recovery", async (_req, res) => {
  try {
    const breaker = await getBreaker();
    res.json({
      max_retries_per_model: breaker.max_retries_per_model ?? 3,
      retry_delay_seconds: breaker.retry_delay_seconds ?? 120,
      max_fallbacks: breaker.max_fallbacks ?? 3,
      push_back_to_pending: breaker.push_back_to_pending === 1 || breaker.push_back_to_pending === null,
      circuit_breaker_retry_after: breaker.retry_after ?? 1800,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Save failure recovery config
app.post("/config/failure-recovery", async (req, res) => {
  try {
    const {
      max_retries_per_model,
      retry_delay_seconds,
      max_fallbacks,
      push_back_to_pending,
      circuit_breaker_retry_after,
    } = req.body || {};

    const now = new Date().toISOString();
    const pool = getDb();
    await pool.query(
      `UPDATE circuit_breaker SET
        max_retries_per_model = $1,
        retry_delay_seconds = $2,
        max_fallbacks = $3,
        push_back_to_pending = $4,
        retry_after = $5,
        updated_at = $6
      WHERE id = 1`,
      [
        typeof max_retries_per_model === 'number' ? max_retries_per_model : 3,
        typeof retry_delay_seconds === 'number' ? retry_delay_seconds : 120,
        typeof max_fallbacks === 'number' ? max_fallbacks : 3,
        push_back_to_pending !== false ? 1 : 0,
        typeof circuit_breaker_retry_after === 'number' ? circuit_breaker_retry_after : 1800,
        now,
      ]
    );

    console.log(`[${now}] FAILURE RECOVERY config updated`);
    res.json({ saved: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── v083: AI Configuration Registry ───────────────────────────────

// Test invoke: run a model with a test prompt and stream stdout
app.post("/config/ai/test", async (req, res) => {
  try {
    const { model_id, test_prompt } = req.body || {};
    if (!model_id || !test_prompt) {
      res.status(400).json({ error: "model_id and test_prompt are required" });
      return;
    }

    // Look up the model from the DB
    const models = await getAIModels();
    const model = models.find((m: any) => m.id === model_id);
    if (!model) {
      res.status(404).json({ error: `Model ${model_id} not found` });
      return;
    }

    // Look up the harness
    const harnesses = await getAIHarnesses();
    const harness = harnesses.find((h: any) => h.id === model.harness_id);
    if (!harness) {
      res.status(404).json({ error: `Harness ${model.harness_id} not found` });
      return;
    }

    // Resolve harness type from invocation_semantics
    let harnessType = "opencode";
    try {
      const sem = JSON.parse(harness.invocation_semantics || "{}");
      const binary = (sem.binary || "opencode").toLowerCase();
      if (binary.includes("codex")) harnessType = "codex";
      else if (binary.includes("ollama")) harnessType = "ollama";
      else harnessType = "opencode";
    } catch { /* use default */ }

    // Create a test session
    const now = new Date().toISOString();
    const sessionId = `test-${model_id}-${Date.now()}`;
    await startSession({
      id: sessionId,
      agent_role: "test",
      start_iso: now,
      plans_processed: [],
      plan_count: 0,
      model: model.model_identifier,
    });

    // Set up session log path (the Temporal activity writes to this path)
    const sessionsDir = path.join(PIPELINE_DIR, "sessions");
    fs.mkdirSync(sessionsDir, { recursive: true });

    // Start via Temporal workflow instead of direct spawn
    if (await ensureTemporalConnected()) {
      const handle = await startTestInvokeWorkflow(model_id, test_prompt, sessionId);

      const timestamp = new Date().toISOString();
      console.log(`[${timestamp}] TEST INVOKE model=${model_id} session=${sessionId} workflow=${handle.workflowId}`);

      res.json({
        started: true,
        sessionId,
        model_id,
        model_name: model.name,
        model_identifier: model.model_identifier,
        harness: harnessType,
        workflowId: handle.workflowId,
        logPath: `/log/${sessionId}`,
        timestamp,
      });
    } else {
      // Fallback: direct spawn if Temporal is unavailable
      const sessionLogPath = path.join(sessionsDir, `${sessionId}.log`);
      const pythonBin = process.env.PYTHON_BIN || "python3";
      const testInvokePath = path.resolve(__dirname, "../../../../legacy/python/conduit/test_invoke.py");
      const projectRoot = process.env.PIPELINE_ROOT || "/home/codex/dev";

      const { spawn } = require("child_process");
      const proc = spawn(pythonBin, [
        testInvokePath,
        "--harness", harnessType,
        "--model-identifier", model.model_identifier,
        "--test-prompt", test_prompt,
        "--session-id", sessionId,
        "--session-log", sessionLogPath,
        "--working-dir", projectRoot,
      ], {
        detached: true,
        stdio: "ignore",
      });
      proc.unref();
      await updateSessionPid(sessionId, proc.pid);

      const timestamp = new Date().toISOString();
      console.log(`[${timestamp}] TEST INVOKE (fallback) model=${model_id} session=${sessionId} pid=${proc.pid}`);

      res.json({
        started: true,
        sessionId,
        model_id,
        model_name: model.name,
        model_identifier: model.model_identifier,
        harness: harnessType,
        logPath: `/log/${sessionId}`,
        timestamp,
      });
    }
  } catch (e: any) {
    console.error(`[${new Date().toISOString()}] TEST INVOKE error:`, e.message);
    res.status(500).json({ error: e.message });
  }
});

// Full snapshot: all providers, harnesses, models, and role configs
app.get("/config/ai", async (_req, res) => {
  res.json(await getAIConfigSnapshot());
});

// Validate AI config: checks for missing references, broken harness binaries, etc.
app.get("/config/ai/validate", async (_req, res) => {
  try {
    const warnings = await validateAIConfig();
    res.json({ valid: warnings.length === 0, warnings });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Seed defaults: populates empty AI config tables with reasonable starter values
app.post("/config/ai/seed-defaults", async (req, res) => {
  try {
    const { force } = req.body || {};
    const result = await seedDefaultAIConfig(!!force);
    res.json(result);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Import full AI config snapshot: clears existing data and bulk-inserts
app.post("/config/ai/import", async (req, res) => {
  try {
    const { providers, harnesses, models, roles, role_models } = req.body || {};
    if (!providers && !harnesses && !models && !roles) {
      res.status(400).json({ error: "No import data provided — need at least one of: providers, harnesses, models, roles" });
      return;
    }
    const result = await importAIConfig({ providers: providers || [], harnesses: harnesses || [], models: models || [], roles: roles || [], role_models: role_models || [] });
    res.json({ imported: true, ...result });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Providers ─────────────────────────────────────────────────────
app.post("/config/ai/provider", async (req, res) => {
  try {
    const { id, name, type, endpoint_url, api_key, config_json } =
      req.body || {};
    if (!id || !name || !type) {
      res.status(400).json({ error: "id, name, and type are required" });
      return;
    }
    await upsertAIProvider({
      id,
      name,
      type,
      endpoint_url: endpoint_url ?? null,
      api_key: api_key ?? null,
      config_json: config_json ?? "{}",
    });
    res.json({ saved: true, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

app.delete("/config/ai/provider/:id", async (req, res) => {
  const { id } = req.params;
  try {
    const deleted = await deleteAIProvider(id);
    res.json({ deleted, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Harnesses ─────────────────────────────────────────────────────
app.post("/config/ai/harness", async (req, res) => {
  try {
    const { id, name, invocation_semantics } = req.body || {};
    if (!id || !name) {
      res.status(400).json({ error: "id and name are required" });
      return;
    }
    await upsertAIHarness({
      id,
      name,
      invocation_semantics: invocation_semantics ?? "{}",
    });
    res.json({ saved: true, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

app.delete("/config/ai/harness/:id", async (req, res) => {
  const { id } = req.params;
  try {
    const deleted = await deleteAIHarness(id);
    res.json({ deleted, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Models ────────────────────────────────────────────────────────
app.post("/config/ai/model", async (req, res) => {
  try {
    const { id, name, harness_id, provider_id, model_identifier } =
      req.body || {};
    if (!id || !name || !harness_id || !model_identifier) {
      res
        .status(400)
        .json({
          error: "id, name, harness_id, and model_identifier are required",
        });
      return;
    }
    await upsertAIModel({
      id,
      name,
      harness_id,
      provider_id: provider_id ?? null,
      model_identifier,
    });
    res.json({ saved: true, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

app.delete("/config/ai/model/:id", async (req, res) => {
  const { id } = req.params;
  try {
    const deleted = await deleteAIModel(id);
    res.json({ deleted, id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Role Assignment ───────────────────────────────────────────────
app.post("/config/ai/role", async (req, res) => {
  try {
    const {
      id,
      role,
      provider_id,
      harness_id,
      model_id,
      extra_params,
      model_priorities,
    } = req.body || {};
    if (!id || !role || !provider_id || !harness_id || !model_id) {
      res
        .status(400)
        .json({
          error: "id, role, provider_id, harness_id, and model_id are required",
        });
      return;
    }
    await upsertAIRoleConfig({
      id,
      role,
      provider_id,
      harness_id,
      model_id,
      extra_params: extra_params ?? "{}",
    });

    // v093: Save multi-model priorities if provided (v098: per-model provider/harness)
    if (
      Array.isArray(model_priorities) &&
      model_priorities.length > 0
    ) {
      await upsertRoleModels(role, model_priorities);
    }

    // Signal scheduler to wake from idle backoff (config may affect eligibility)
    await requestSchedulerWake();

    res.json({ saved: true, id, role });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── v081: Agent chat (message box) ──────────────────────────────────

const AGENT_CHAT_URL = process.env.AGENT_CHAT_URL || "http://localhost:3102";

// Proxy: send message to agent via the chat server
app.post("/chat/send", async (req, res) => {
  const { role, message, log_level } = req.body || {};
  if (!role || !message) {
    res.status(400).json({ error: "role and message are required" });
    return;
  }
  try {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const agentToken = process.env.AGENT_CHAT_TOKEN;
    if (agentToken) {
      headers["Authorization"] = `Bearer ${agentToken}`;
    }

    const body = JSON.stringify({
      role,
      message,
      ...(log_level ? { log_level } : {}),
    });
    headers["Content-Length"] = Buffer.byteLength(body).toString();

    const proxyReq = http.request(
      `${AGENT_CHAT_URL}/chat`,
      { method: "POST", headers, timeout: 30000 },
      (proxyRes: any) => {
        let body = "";
        proxyRes.on("data", (chunk: string) => (body += chunk));
        proxyRes.on("end", () => {
          if (
            proxyRes.statusCode &&
            proxyRes.statusCode >= 200 &&
            proxyRes.statusCode < 300
          ) {
            try {
              res.status(proxyRes.statusCode).json(JSON.parse(body));
            } catch {
              res
                .status(502)
                .json({ error: "Invalid response from agent chat server" });
            }
          } else {
            let detail = body;
            try {
              const parsed = JSON.parse(body);
              detail = parsed.error || parsed.detail || body;
            } catch {
              /* use raw body */
            }
            res.status(proxyRes.statusCode || 502).json({
              error: "Agent chat server error",
              detail,
            });
          }
        });
      },
    );
    proxyReq.on("error", (err: Error) => {
      if ((err as any).code === "ECONNREFUSED") {
        res
          .status(502)
          .json({
            error: "Agent chat server unreachable",
            detail: "Connection refused — is the agent chat service running?",
          });
      } else if (
        (err as any).code === "ETIMEDOUT" ||
        err.message === "socket hang up"
      ) {
        res.status(502).json({ error: "Agent chat server timed out" });
      } else {
        res
          .status(502)
          .json({
            error: "Agent chat server unreachable",
            detail: err.message,
          });
      }
    });
    proxyReq.end(body);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Config: tell the UI where the agent chat SSE server lives
app.get("/chat/config", async (_req, res) => {
  res.json({
    agentChatUrl: AGENT_CHAT_URL,
    agents: [
      {
        role: "planner",
        label: "Planner",
        description: "Creates and refines implementation plans",
      },
      {
        role: "builder",
        label: "Builder",
        description: "Implements plans, modifies code",
      },
      {
        role: "reviewer",
        label: "Reviewer",
        description: "Reviews implementations against plans",
      },
      {
        role: "critic",
        label: "Critic",
        description: "Critiques plans for gaps and improvements",
      },
      {
        role: "analyst",
        label: "Analyst",
        description: "Analyzes inspection reports and triages issues",
      },
      {
        role: "architect",
        label: "Architect",
        description: "Designs architecture and writes specifications",
      },
      {
        role: "inspector",
        label: "Inspector",
        description: "Inspects codebase for errors and issues",
      },
      {
        role: "engineer",
        label: "Engineer",
        description: "Reports on backlog and identifies priority work",
      },
      {
        role: "rover",
        label: "Rover",
        description: "Processes chat transcripts through harvesting pipeline",
      },
    ],
  });
});

// Proxy: list active agent chat sessions
app.get("/chat/sessions", async (_req, res) => {
  try {
    const http = require("http") as typeof import("http");
    http
      .get(`${AGENT_CHAT_URL}/chat/sessions`, (proxyRes: any) => {
        let body = "";
        proxyRes.on("data", (chunk: string) => (body += chunk));
        proxyRes.on("end", () => {
          try {
            res.json(JSON.parse(body));
          } catch {
            res.json({ sessions: [] });
          }
        });
      })
      .on("error", () => res.json({ sessions: [] }));
  } catch {
    res.json({ sessions: [] });
  }
});

// Session log SSE endpoint (v071 — streaming live builder output)
app.get("/log/:sessionId", async (req, res) => {
  const { sessionId } = req.params;

  // Sanitize sessionId — prevent path traversal
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  const sessionsDir = path.join(PIPELINE_DIR, "sessions");
  const logPath = path.join(sessionsDir, `${sessionId}.log`);

  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });

  let lastSize = 0;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let resolved = false;

  const sendLines = () => {
    try {
      if (!fs.existsSync(logPath)) return;
      const stats = fs.statSync(logPath);
      if (stats.size <= lastSize) return;

      const fd = fs.openSync(logPath, "r");
      const buf = Buffer.alloc(stats.size - lastSize);
      fs.readSync(fd, buf, 0, buf.length, lastSize);
      fs.closeSync(fd);
      lastSize = stats.size;

      const newContent = buf.toString("utf-8");
      const lines = newContent.split("\n");
      for (const line of lines) {
        if (line.length === 0) continue;
        // Detect stderr lines from executor_cloud.py's [stderr] prefix
        const isStderr = line.startsWith("[stderr] ") || line.startsWith("[stderr]");
        const logType = isStderr ? "stderr" : "stdout";
        const event = JSON.stringify({
          type: "session_log",
          data: {
            sessionId,
            line,
            timestamp: new Date().toISOString(),
            logType,
          },
        });
        res.write(`data: ${event}\n\n`);
      }
    } catch {
      // file may disappear — stop polling
    }
  };

  // Send meta event so the UI knows whether a log file exists
  const logExists = fs.existsSync(logPath);
  res.write(
    `data: ${JSON.stringify({
      type: "session_log_meta",
      data: { sessionId, logFileExists: logExists, logPath },
    })}\n\n`,
  );

  // Send any existing content immediately (only if file exists)
  if (logExists) {
    sendLines();
  }

  // Poll for new content every 500ms (only if file exists)
  if (logExists) {
    pollTimer = setInterval(() => {
      if (resolved) return;
      sendLines();
    }, 500);
  }

  // Keep-alive ping every 15 seconds
  const keepAlive = setInterval(() => {
    if (resolved) return;
    res.write(`: keepalive\n\n`);
  }, 15000);

  req.on("close", () => {
    resolved = true;
    if (pollTimer) clearInterval(pollTimer);
    clearInterval(keepAlive);
  });
});

// ── Governance Events ───────────────────────────────────────────────
// Observability spine: replay historical receipts into peb.governance_events
// and list events for debugging/monitoring.

app.post("/governance/replay", async (_req, res) => {
  try {
    const result = await replayGovernanceEvents();
    res.json({ ok: true, ...result });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get("/governance/events", async (req, res) => {
  try {
    const events = await listGovernanceEvents({
      planId: req.query.planId as string | undefined,
      eventType: req.query.eventType as string | undefined,
      asOf: req.query.asOf as string | undefined,
      limit: req.query.limit ? parseInt(req.query.limit as string, 10) : 50,
    });
    res.json({ ok: true, events });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ── Vision HTTP API (for the Python vision_bridge module) ──────────
// These endpoints are consumed by the LOSM bridge
// (python/tackle/vision_bridge.py) which is the canonical typed writer.

app.post("/vision/work-requests", async (req, res) => {
  try {
    const { id, work_request_uuid, dco_json, context, status } = req.body;
    if (!id) {
      res.status(400).json({ ok: false, error: "Missing required field: id" });
      return;
    }
    const result = await createWorkRequest({
      id,
      work_request_uuid: work_request_uuid || undefined,
      dco_json: dco_json || "{}",
      context: context || {},
      status: status || "pending",
    });
    res.json({ ...result });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get("/vision/work-requests", async (req, res) => {
  try {
    const wrs = await listWorkRequests({
      planId: req.query.planId as string | undefined,
      status: req.query.status as string | undefined,
      limit: req.query.limit ? parseInt(req.query.limit as string, 10) : 50,
    });
    res.json({ ok: true, work_requests: wrs });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get("/vision/work-requests/:id", async (req, res) => {
  try {
    const wr = await getWorkRequest(req.params.id);
    if (!wr) {
      res.status(404).json({ ok: false, error: "Not found" });
      return;
    }
    res.json({ ok: true, work_request: wr });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.get("/vision/receipts", async (req, res) => {
  try {
    const planId = req.query.planId as string;
    const asOf = req.query.asOf as string | undefined;
    if (!planId) {
      res.status(400).json({ ok: false, error: "Missing required query: planId" });
      return;
    }
    const receipts = await listReceiptsByPlan(planId, asOf);
    res.json({ ok: true, receipts });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

/**
 * POST /vision/receipts — Direct receipt insertion (no state machine).
 * Used by the Python LOSM bridge for standalone work requests
 * that don't have a matching conduit plan. Skips the state machine
 * validation in tools.ts so the typed bridge can issue LOSM-native
 * ExecutionReceipts without creating dummy plans.
 */
app.post("/vision/receipts", async (req, res) => {
  try {
    const { id, plan_id, type, agent_role, session_id, artifact_path, summary, metadata_json, tokens_used, created_at } = req.body;
    if (!id || !plan_id || !type || !agent_role || !created_at) {
      res.status(400).json({ ok: false, error: "Missing required fields: id, plan_id, type, agent_role, created_at" });
      return;
    }
    await insertReceipt({
      id, plan_id, type, agent_role,
      session_id: session_id || '',
      ticket_id: req.body.ticket_id || null,
      artifact_path: artifact_path || null,
      summary: summary || '',
      metadata_json: metadata_json || '{}',
      tokens_used: tokens_used ?? 0,
      created_at,
    });
    res.json({ ok: true, id, plan_id });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ── PID file for reliable restarts ───────────────────────────────
// Detects systemd via INVOCATION_ID env var and skips the PID file
// mechanism entirely — systemd tracks PIDs via cgroups and handles
// restart policy.  Outside systemd, kills the previous instance via
// SIGTERM before binding to prevent EADDRINUSE.

const PID_FILE = path.join(PIPELINE_DIR, "mcp-server.pid");

/** True when running under systemd supervision (cgroup-tracked). */
const _underSystemd = !!process.env.INVOCATION_ID;

/** Claim the PID file: kill previous instance if any, then write ours.
 *  Under systemd this is a no-op since systemd manages lifecycle. */
function claimPidFile(): void {
  if (_underSystemd) {
    return; // systemd handles PIDs and restart policy
  }

  // ── Ad-hoc (no systemd): claim by killing previous instance ──
  try {
    if (fs.existsSync(PID_FILE)) {
      const oldPidStr = fs.readFileSync(PID_FILE, "utf-8").trim();
      const oldPid = parseInt(oldPidStr, 10);
      if (!isNaN(oldPid) && oldPid > 0 && oldPid !== process.pid) {
        try {
          process.kill(oldPid, 0);
          console.log(`[PID] Previous server PID ${oldPid} still running — killing it.`);
          try {
            process.kill(-oldPid, "SIGTERM");
          } catch {
            process.kill(oldPid, "SIGTERM");
          }
          for (let i = 0; i < 15; i++) {
            try {
              process.kill(oldPid, 0);
              Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 200);
            } catch {
              break;
            }
          }
          try {
            process.kill(oldPid, 0);
            try {
              process.kill(-oldPid, "SIGKILL");
            } catch {
              process.kill(oldPid, "SIGKILL");
            }
            console.log(`[PID] Force-killed previous server PID ${oldPid}.`);
          } catch {
            console.log(`[PID] Previous server PID ${oldPid} shut down gracefully.`);
          }
        } catch {
          console.log(`[PID] Previous PID ${oldPid} is not running — stale PID file.`);
        }
      }
    }
  } catch (e: any) {
    console.warn(`[PID] Error claiming PID file: ${e.message}`);
  }

  try {
    fs.writeFileSync(PID_FILE, String(process.pid), "utf-8");
    console.log(`[PID] PID ${process.pid} written to ${PID_FILE}`);
  } catch (e: any) {
    console.warn(`[PID] Could not write PID file: ${e.message}`);
  }
}

/** Remove the PID file on graceful shutdown. */
function releasePidFile(): void {
  if (_underSystemd) return;
  try {
    if (fs.existsSync(PID_FILE)) {
      const current = fs.readFileSync(PID_FILE, "utf-8").trim();
      if (current === String(process.pid)) {
        fs.unlinkSync(PID_FILE);
        console.log(`[PID] PID file removed.`);
      }
    }
  } catch {
    // best-effort cleanup
  }
}

// Register shutdown handlers — PID cleanup via exit event, exit via signal
process.on("exit", releasePidFile);
process.on("SIGINT", () => process.exit(0));
process.on("SIGTERM", () => process.exit(0));

// Initialize and start
async function start() {
  claimPidFile();
  await watcher.initialize();

  app.listen(PORT, () => {
    console.log(`Watching ${PIPELINE_DIR}...`);
    console.log(`Graph directory: ${GRAPH_DIR}`);
    console.log(`MCP server listening on http://localhost:${PORT}`);
    console.log(`SSE endpoint: http://localhost:${PORT}/events`);
    console.log(`State endpoint: http://localhost:${PORT}/state`);
  });
}

start().catch((err) => {
  console.error("Failed to start:", err);
  releasePidFile();
  process.exit(1);
});
