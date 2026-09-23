import express from "express";
import cors from "cors";
import path from "node:path";
import crypto from "node:crypto";
import fs from "node:fs";
import { spawn } from "node:child_process";
import { PipelineWatcher } from "./watcher";
import { KILLABLE_ROLES } from "./role-vocabulary";
import { registerToolHandlers, toolDefinitions } from "./tools";
import { createError, createSuccess } from "./errors";
import {
  getBreaker,
  getPlanById,
  releaseSessionTickets,
  resetAbandonedTickets,
  supersedeTicket,
  cancelTicket,
  requestSchedulerWake,
  createWorkRequest,
  appendEvent,
  getEvents,
  selectNextRunnable,
  listWorkRequestStates,
  checkProjectionDrift,
  resolveWrUuid,
} from "./db";
import * as api from "./conduit-client";
import { enforceRouteContractAtBoot } from "./route-contract-guard";
import {
  gateWrTransition,
  recordGovernedDecisions,
  surfaceBlocker,
  getEnforcementState,
  CirsdmEnforceResult,
} from "./cirsdm";
import {
  validateCompilerOutput,
  compilerOutputToEvent,
  foldEvents,
  decide,
  validateTransitionWithOps,
  getOpTrace,
  isUnresolvedOps,
  dbEventsToRuntimeEvents,
  WorkRequestState,
} from "./runtime-kernel";
import { loadEnv } from "./env"; // shared .env loader (no dotenv dependency)
import { validateReceipt, receiptProvenanceMetadata } from "./receipts";

/** C1 gate 1 (Lilac plan 8261639): defensively parse a caller-supplied
 * metadata_json string into an object for provenance stamping. Returns {}
 * for malformed input — persistence-layer validation still rejects it. */
function safeParseJsonObject(raw: string | undefined | null): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

// .env already loaded by env.ts at module evaluation time

const PORT = parseInt(process.env.PORT || "3100", 10);
const PIPELINE_DIR =
  process.env.PIPELINE_DIR ||
  // .conduit-data deleted 2026-08-09; mirror is the posterity home
  path.resolve(__dirname, "../../../../nexus/audit/CONDUIT_DATA");
const GRAPH_DIR =
  process.env.GRAPH_DIR ||
  // graph lives at nexus/graph, independent of PIPELINE_DIR
  path.resolve(__dirname, "../../../../nexus/graph");

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

// ── Live REST passthrough: /api/*, /admin/*, /delta, /replay → Python kernel ──
// conduit-ui's live-mode proxy (MCP_PREFIXES) routes these prefixes to us.
// The Python conduit FastAPI (:3103) owns these routes — sessions, breaker,
// receipts, admin identities, delta, replay (see nexus/python/conduit/app/main.py).
// conduit-mcp is the documented thin MCP-to-REST proxy (conduit-client.ts →
// CONDUIT_API_URL), so forward verbatim and relay the upstream response
// (status, headers, body) so backend error envelopes surface to the UI.
const KERNEL_API_URL = process.env.CONDUIT_API_URL || "http://localhost:3103";
const KERNEL_PROXY_PREFIXES = ["/api", "/admin", "/delta", "/replay"];

app.use(async (req, res, next) => {
  const hit = KERNEL_PROXY_PREFIXES.find(
    (p) => req.path === p || req.path.startsWith(p + "/"),
  );
  if (!hit) {
    next();
    return;
  }

  const qs = req.originalUrl.includes("?")
    ? req.originalUrl.substring(req.originalUrl.indexOf("?"))
    : "";
  const target = `${KERNEL_API_URL}${req.path}${qs}`;
  const headers: Record<string, string> = {};
  if (req.headers["content-type"]) {
    headers["content-type"] = String(req.headers["content-type"]);
  }
  if (req.headers.accept) {
    headers.accept = String(req.headers.accept);
  }

  try {
    const opts: RequestInit = { method: req.method, headers };
    const hasBody =
      !["GET", "HEAD"].includes(req.method) &&
      req.body &&
      typeof req.body === "object" &&
      Object.keys(req.body).length > 0;
    if (hasBody) {
      if (!headers["content-type"]) headers["content-type"] = "application/json";
      opts.body = JSON.stringify(req.body);
    }
    const upstream = await fetch(target, opts);
    const body = await upstream.text();
    res.status(upstream.status);
    const ct = upstream.headers.get("content-type");
    if (ct) res.set("content-type", ct);
    res.send(body);
  } catch (e: any) {
    console.error(`[kernel-proxy] ${req.method} ${req.path} → ${e.message}`);
    res.status(502).json({
      error: `Kernel API unreachable (${KERNEL_API_URL}): ${e.message}`,
    });
  }
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

// Liveness probe — always 200 while the process is up.
// conduit-ui's live-mode proxy routes /healthz here.
app.get("/healthz", async (_req, res) => {
  res.json({
    status: "alive",
    port: PORT,
    pid: process.pid,
    timestamp: new Date().toISOString(),
  });
});

// Readiness probe — 200 once the pipeline watcher can produce state, else 503.
app.get("/readyz", async (_req, res) => {
  try {
    await watcher.getState();
    res.json({
      status: "ready",
      port: PORT,
      pid: process.pid,
      timestamp: new Date().toISOString(),
    });
  } catch (err: any) {
    res.status(503).json({
      status: "not_ready",
      error: err.message,
      timestamp: new Date().toISOString(),
    });
  }
});

// Prometheus-style metrics (text/plain)
app.get("/metrics", async (_req, res) => {
  res.type("text/plain").send(
    [
      "# HELP conduit_mcp_up Whether conduit-mcp is up (1)",
      "# TYPE conduit_mcp_up gauge",
      "conduit_mcp_up 1",
      "# HELP conduit_mcp_uptime_seconds Process uptime in seconds",
      "# TYPE conduit_mcp_uptime_seconds gauge",
      `conduit_mcp_uptime_seconds ${Math.round(process.uptime())}`,
      "# HELP conduit_mcp_port Listening port",
      "# TYPE conduit_mcp_port gauge",
      `conduit_mcp_port ${PORT}`,
    ].join("\n"),
  );
});

// ── Routes below extracted to conduit-srv (port 3104) per Architect decision ──
// Removed: GET /workflows, POST /tickets/detect → conduit-srv
// Kept: Sessions, agents/kill, circuit-breaker, conduit pause/resume (Phase 2)

// Sessions endpoint (v066 — database-backed session history)
// Sessions now own their workflow metadata natively (workflow_id, run_id, etc.)
app.get("/sessions", async (_req, res) => {
  try {
    const sessions = await api.getAllSessions();
    res.json(sessions);
  } catch (e: any) {
    console.error("[sessions] Error:", e.message);
    res.status(500).json({ error: e.message });
  }
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
    await api.updateSessionCost(sessionId, cost_usd);
    res.json({ updated: true, sessionId, cost_usd });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// Agent heartbeat — called periodically by executor_cloud.py during harness execution
// Updates last_activity and last_heartbeat_at on the sessions row for staleness detection.
// Also updates the in-memory agent-watcher state for /state visibility.
app.post("/sessions/:sessionId/heartbeat", async (req, res) => {
  const { sessionId } = req.params;

  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    res.status(400).json({ error: "Invalid session ID" });
    return;
  }

  try {
    await api.updateSessionHeartbeat(sessionId);
    // Update in-memory agent state if role is provided in body
    const role = req.body?.role;
    if (role && watcher) {
      watcher.updateAgentHeartbeat(
        role as any,
        req.body?.state || "working",
        req.body?.detail || null,
        req.body?.pid || null,
      );
    }
    res.json({ updated: true, sessionId, timestamp: new Date().toISOString() });
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
  }    const session = await api.getSession(sessionId);
  if (!session) {
    res.status(404).json({ error: `Session ${sessionId} not found` });
    return;
  }

  if (!session.is_running) {
    res
      .status(400)
      .json({ killed: false, error: "Session is not running", sessionId });
    return;
  }    // Delegate to Python conduit which handles kill signal + DB cleanup atomically
  const result = await api.killSession(sessionId);

  // Broadcast SSE event so UI updates immediately
  for (const client of sseClients) {
    client.res.write(
      `data: ${JSON.stringify({
        type: "session_killed",
        data: {
          sessionId,
          killedPids: result.pids,
          timestamp: result.timestamp,
        },
      })}\n\n`,
    );
  }

  res.json(result);
});

// Kill a running agent by role (v073 — kill builder/reviewer/planner from UI)
app.post("/agents/:role/kill", async (req, res) => {
  const { role } = req.params;

  // Validate role — allowlist derived from the ratified roles matrix
  // (src/role-vocabulary.ts). Superset of the legacy 6-role gate.
  const validRoles = KILLABLE_ROLES;
  if (!validRoles.includes(role as (typeof KILLABLE_ROLES)[number])) {
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
    await api.tripBreaker({
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
    await api.clearBreaker();

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
    await api.setConduitPaused(true);

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
    await api.setConduitPaused(false);

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

  // Spawn main.py --plan <id> --force (cron-driven dispatch)
  const now = new Date().toISOString();
  console.log(`[${now}] RESTART builder plan=${planId} force=${force} (main.py)`);

  try {
    const pyBin = process.env.CONDUIT_PYTHON || "python3";
    const conduitDir = process.env.CONDUIT_PY_DIR ||
      path.resolve(__dirname, "../../../../nexus/python/conduit");
    // `tackle` package lives in nexus/python (parent of conduit dir). The
    // scheduled builder relies on PYTHONPATH set by cron; mirror that here so
    // `from tackle.db import get_role_config` resolves when restarted via the
    // REST endpoint instead of the scheduler.
    const pythonPath = process.env.PYTHONPATH ||
      path.resolve(conduitDir, "..");
    const proc = spawn(pyBin, ["main.py", "--plan", planId, ...(force ? ["--force"] : [])], {
      cwd: conduitDir,
      detached: true,
      stdio: "ignore",
      env: { ...process.env, PYTHONPATH: pythonPath },
    });
    proc.unref();

    console.log(
      `[${now}] RESTART builder plan=${planId} → main.py PID ${proc.pid} spawned`,
    );

    res.json({
      restarted: true,
      planId,
      force,
      pid: proc.pid,
      breakerTripped: breaker.tripped === 1,
      timestamp: now,
    });
  } catch (e: any) {
    console.error(
      `[${now}] RESTART builder plan=${planId} → spawn failed:`,
      e.message,
    );
    res.status(500).json({ error: `Failed to spawn builder: ${e.message}` });
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

// ── Routes below extracted to conduit-srv (port 3104) per Architect decision ──
// Removed: /tokens/*, /tickets/lineage, /config/*, /log/:sessionId, /governance/*,
//          /vision/work-requests (POST+GET+GET:id), GET /vision/receipts → conduit-srv
// Kept: POST /vision/receipts (validateReceipt dependency), /wr/* (runtime-kernel dependency)

/**
 * POST /vision/receipts — issue_receipt: validate and insert a receipt.
 * Returns 400 error if validateReceipt rejects the receipt transition.
 * Used by the Python LOSM bridge for standalone work requests
 * that don't have a matching conduit plan. Validates the receipt
 * transition via validateReceipt before inserting, so the typed
 * bridge can issue LOSM-native ExecutionReceipts without creating
 * dummy plans.
 */
app.post("/vision/receipts", async (req, res) => {
  try {
    const { id, plan_id, type, agent_role, session_id, artifact_path, summary, metadata_json, tokens_used, created_at } = req.body;
    if (!id || !plan_id || !type || !agent_role || !created_at) {
      res.status(400).json({ ok: false, error: "Missing required fields: id, plan_id, type, agent_role, created_at" });
      return;
    }
    // Validate receipt transition before inserting
    const validation = await validateReceipt(plan_id, type);
    if (!validation.valid) {
      res.status(400).json({ ok: false, error: validation.error });
      return;
    }
    await api.insertReceipt({
      id, plan_id, type, agent_role,
      session_id: session_id || '',
      ticket_id: req.body.ticket_id || null,
      artifact_path: artifact_path || null,
      summary: summary || '',
      // C1 gate 1 (Lilac): provenance overrides in metadata + declared
      // producer identity so the persistence layer records the front-door
      // channel, not just the physical Python writer.
      metadata_json: JSON.stringify({
        ...safeParseJsonObject(metadata_json),
        ...receiptProvenanceMetadata(id),
      }),
      tokens_used: tokens_used ?? 0,
      created_at,
      producer_id: 'conduit-mcp',
      source_channel: 'conduit-mcp-http',
      correlation_id: id,
    });
    res.json({ ok: true, id, plan_id });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

// ══════════════════════════════════════════════════════════════════
//  RUNTIME KERNEL — WorkRequest lifecycle state machine
// ══════════════════════════════════════════════════════════════════

/**
 * POST /wr/submit — Submit compiler output as a new WorkRequest.
 * Validates the contract boundary (no execution fields in compiler output),
 * creates the WR, appends WR_SUBMITTED event, and returns the initial state.
 */
app.post("/wr/submit", async (req, res) => {
  try {
    const output = req.body;
    // Enforce the compiler/runtime contract boundary
    validateCompilerOutput(output);
    // Convert to event and persist
    const event = compilerOutputToEvent(output);
    // Upsert the work request (idempotent on wrId)
    await createWorkRequest({
      id: event.wrId,
      dco_json: JSON.stringify(output),
      context: { intent: output.intent, constraints: output.constraints, opTrace: output.opTrace, normalization_pending: output.normalization_pending },
      status: "draft",
      title: output.intent?.objective || "",
    });
    // Append the WR_SUBMITTED event
    await appendEvent(event.wrId, event.type, event.payload as Record<string, unknown>);
    // Fold events to get current state
    const events = dbEventsToRuntimeEvents(await getEvents(event.wrId));
    const state = foldEvents(event.wrId, events);
    // Broadcast SSE event
    const now = new Date().toISOString();
    watcher.emitToolEvent({
      type: "wr_state_changed",
      data: {
        wrId: event.wrId,
        event: event.type,
        previousStatus: "DRAFT",
        currentStatus: state.status,
        state,
      },
      timestamp: now,
    });
    res.status(201).json({ ok: true, state });
  } catch (err: any) {
    const status = err.message.startsWith("COMPILER_LEAK") ? 422 : 500;
    res.status(status).json({ ok: false, error: err.message });
  }
});

/**
 * GET /wr — List all WorkRequests with optional status filter.
 */
app.get("/wr", async (req, res) => {
  try {
    const { status, limit } = req.query;
    const rows = await listWorkRequestStates({
      status: status as string | undefined,
      limit: limit ? parseInt(limit as string, 10) : undefined,
    });
    // Fold events for each row to get the authoritative state
    const states: WorkRequestState[] = [];
    for (const row of rows) {
      const events = dbEventsToRuntimeEvents(await getEvents(row.work_request_uuid));
      states.push(foldEvents(row.work_request_uuid, events));
    }
    res.json({ ok: true, count: states.length, states });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

/**
 * GET /wr/:id — Get the current folded state of a WorkRequest.
 */
app.get("/wr/:id", async (req, res) => {
  try {
    const { id } = req.params;
    const rawEvents = await getEvents(id);
    if (rawEvents.length === 0) {
      res.status(404).json({ ok: false, error: `WorkRequest ${id} not found` });
      return;
    }
    const events = dbEventsToRuntimeEvents(rawEvents);
    const state = foldEvents(id, events);
    res.json({ ok: true, state });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

/**
 * GET /wr/:id/events — Get the raw event log for a WorkRequest.
 */
app.get("/wr/:id/events", async (req, res) => {
  try {
    const { id } = req.params;
    const events = await getEvents(id);
    if (events.length === 0) {
      res.status(404).json({ ok: false, error: `WorkRequest ${id} not found` });
      return;
    }
    res.json({ ok: true, count: events.length, events });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

/**
 * GET /wr/:id/projection-drift — Check if the live work_request_state
 * projection matches what a full event replay would produce.
 * Non-destructive: computes expected state without writing.
 */
app.get("/wr/:id/projection-drift", async (req, res) => {
  try {
    const { id } = req.params;
    const uuid = await resolveWrUuid(id);
    const drift = await checkProjectionDrift(uuid);
    if (!drift) {
      res.status(404).json({ ok: false, error: `WorkRequest ${id} not found` });
      return;
    }
    res.json({ ok: true, drift });
  } catch (err: any) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

/**
 * POST /wr/:id/transition — Apply a transition event to a WorkRequest.
 * Generic endpoint for manual/supervised transitions.
 * Body: { type: "WR_CLAIMED" | "WR_ACKED" | "WR_SETTLED" | "WR_REJECTED" | "WR_FAILED" | "WR_NOOP" | "WR_DEFERRED", payload?: {} }
 */
app.post("/wr/:id/transition", async (req, res) => {
  try {
    const { id } = req.params;
    const { type, payload } = req.body;
    if (!type) {
      res.status(400).json({ ok: false, error: "Missing required field: type" });
      return;
    }
    // Get current state from event log
    const rawEvents = await getEvents(id);
    if (rawEvents.length === 0) {
      res.status(404).json({ ok: false, error: `WorkRequest ${id} not found` });
      return;
    }
    const events = dbEventsToRuntimeEvents(rawEvents);
    const state = foldEvents(id, events);
    // Validate the transition (D4: UNRESOLVED ops gate VALIDATED→QUEUED)
    validateTransitionWithOps(state.status, type, getOpTrace(events));
    // T23 Step 8: CIR-SDM pre-row enforcement gate (fail-closed).
    let cirsdm: CirsdmEnforceResult;
    try {
      cirsdm = await gateWrTransition(events, type, rawEvents[0].work_request_id);
    } catch (e: any) {
      await surfaceBlocker(`enforcement unavailable for ${id} (${type}): ${e.message}`);
      res.status(500).json({ ok: false, error: `CIR_SDM_UNAVAILABLE: ${e.message}` });
      return;
    }
    if (cirsdm.reject) {
      await recordGovernedDecisions(cirsdm.decisions);
      res.status(422).json({
        ok: false,
        error: "CIR_SDM_REJECTED: transition violates an enforced CIR-SDM rule",
        state: cirsdm.state,
        decisions: cirsdm.decisions,
      });
      return;
    }
    const warnings = cirsdm.violations.filter((v) => !v.blocking);
    if (warnings.length > 0) {
      console.log(
        `[CIR-SDM] warnings on ${id}: ${warnings.map((v) => `${v.rule_id}@${v.event_id}`).join(", ")}`,
      );
    }
    // Persist the event
    await appendEvent(id, type, payload || {});
    // Return new state
    const rawNewEvents = await getEvents(id);
    const newEvents = dbEventsToRuntimeEvents(rawNewEvents);
    const newState = foldEvents(id, newEvents);
    // Broadcast SSE event
    const timestamp = new Date().toISOString();
    watcher.emitToolEvent({
      type: "wr_state_changed",
      data: {
        wrId: id,
        event: type,
        previousStatus: state.status,
        currentStatus: newState.status,
        state: newState,
      },
      timestamp,
    });
    res.json({ ok: true, state: newState });
  } catch (err: any) {
    const status = err.message.startsWith("INVALID_TRANSITION") ? 422 : 500;
    res.status(status).json({ ok: false, error: err.message });
  }
});

/**
 * POST /wr/tick — Run ONE tick of the decision loop.
 * Scans for the next runnable WR, applies the decision, returns what happened.
 * This is the "causal loop" entry point — call it on a timer or after any event.
 */
app.post("/wr/tick", async (_req, res) => {
  try {
    const wr = await selectNextRunnable();
    if (!wr) {
      res.json({ ok: true, ticked: false, reason: "no runnable work requests" });
      return;
    }
    // Get current state
    const rawEvents = await getEvents(wr.work_request_uuid);
    const events = dbEventsToRuntimeEvents(rawEvents);
    const state = foldEvents(wr.work_request_uuid, events);
    // D4: never auto-advance an UNRESOLVED WR past VALIDATED.
    if (state.status === "VALIDATED" && isUnresolvedOps(getOpTrace(events))) {
      res.json({ ok: true, ticked: false, reason: "UNRESOLVED_OPS: held at VALIDATED, not auto-advancing" });
      return;
    }
    const decision = decide(state);
    if (!decision) {
      res.json({ ok: true, ticked: false, reason: `state ${state.status} has no automatic transition` });
      return;
    }
    // T23 Step 8: CIR-SDM pre-row enforcement gate (fail-closed) — HOLD the
    // tick on a gate outage (mirror the D4 UNRESOLVED hold).
    let cirsdm: CirsdmEnforceResult;
    try {
      cirsdm = await gateWrTransition(events, decision.type, wr.work_request_uuid);
    } catch (e: any) {
      await surfaceBlocker(`enforcement unavailable for ${wr.work_request_uuid} (${decision.type}): ${e.message}`);
      res.json({ ok: true, ticked: false, reason: "CIR_SDM_UNAVAILABLE: held (fail-closed)" });
      return;
    }
    if (cirsdm.reject) {
      await recordGovernedDecisions(cirsdm.decisions);
      res.json({
        ok: true, ticked: false,
        reason: `CIR_SDM_REJECTED: ${cirsdm.decisions.map((d) => d.rule_id).join(", ")}`,
        decisions: cirsdm.decisions,
      });
      return;
    }
    await appendEvent(decision.wrId, decision.type, decision.payload as Record<string, unknown>);
    const rawNewEvents = await getEvents(wr.work_request_uuid);
    const newEvents = dbEventsToRuntimeEvents(rawNewEvents);
    const newState = foldEvents(wr.work_request_uuid, newEvents);
    // Broadcast SSE event
    const now = new Date().toISOString();
    watcher.emitToolEvent({
      type: "wr_state_changed",
      data: {
        wrId: wr.wr_id,
        event: decision.type,
        previousStatus: state.status,
        currentStatus: newState.status,
        state: newState,
      },
      timestamp: now,
    });
    res.json({
      ok: true,
      ticked: true,
      wrId: wr.wr_id,
      event: decision.type,
      previousStatus: state.status,
      currentStatus: newState.status,
      state: newState,
    });
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

  // Route-contract guard (PR #448 lesson, 2026-09-22): verify the client's
  // required routes are actually served by the deployed Python conduit
  // BEFORE serving tools. The silent failure mode (404 → null → "current
  // state is none") froze the entire builder backlog; drift now fails at
  // boot (strict default → exit, systemd restarts loudly). Unreachable
  // server warns and continues — that failure mode throws real errors per
  // call and was never silent. CONDUIT_ROUTE_GUARD=warn|off to soften.
  await enforceRouteContractAtBoot(KERNEL_API_URL);

  // T23 Step 8: log the CIR-SDM enforcement posture at startup (shadow vs
  // enforced) so the gate's state is auditable (ruling 4a57c089). A failed
  // startup check is a blocker — never proceed silently.
  try {
    console.log(`[CIR-SDM] ${await getEnforcementState()}`);
  } catch (e: any) {
    await surfaceBlocker(`enforcement state unavailable at startup: ${e.message}`);
  }

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
