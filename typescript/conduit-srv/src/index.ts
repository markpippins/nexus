/**
 * conduit-srv — REST API for conduit/vision/peb schema.
 *
 * Extracted from conduit-mcp per Architect decision ("No SQL in MCP Servers").
 * Owns workflow, ticket, token, config, governance, vision, session-log, and
 * work-request REST routes. conduit-mcp retains only MCP-native endpoints
 * (POST /, GET /events, GET /state, POST /tools/call, GET /tools, GET /health).
 *
 * Port: 3104
 *
 * Routes (all mounted at root for backward compat with conduit-mcp consumers):
 *   GET    /workflows
 *   POST   /tickets/detect
 *   GET    /tickets/lineage/:planId
 *   GET    /tokens/plan/:planId
 *   GET    /tokens/role/:role
 *   GET    /tokens/ticket/:ticketId
 *   GET    /config/cron
 *   GET    /config/failure-recovery
 *   POST   /config/failure-recovery
 *   GET    /log/:sessionId              (SSE)
 *   POST   /governance/replay
 *   GET    /governance/events
 *   POST   /vision/work-requests
 *   GET    /vision/work-requests
 *   GET    /vision/work-requests/:id
 *   GET    /vision/receipts
 *   GET    /wr/:id/projection-drift
 *   GET    /wr/drift-scan
 *   GET    /health
 */

import express from "express";
import cors from "cors";
import rateLimit from "express-rate-limit";
import { startHeartbeat } from "heartbeat-client";
import { query } from "./db/client.js";

import workflowsRouter from "./routes/workflows.js";
import ticketsRouter from "./routes/tickets.js";
import tokensRouter from "./routes/tokens.js";
import configRouter from "./routes/config.js";
import governanceRouter from "./routes/governance.js";
import visionRouter from "./routes/vision.js";
import sessionLogRouter from "./routes/session-log.js";
import wrRouter from "./routes/wr.js";

import { app } from "./app.js";

const PORT = parseInt(process.env.CONDUIT_SRV_PORT || "3104", 10);

// ── Process-level safety net ─────────────────────────────────────
process.on('uncaughtException', (err: Error & { code?: string }) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`conduit-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
    process.exit(1);
  }
  if (err.code === 'EPIPE' || err.code === 'ECONNRESET' || err.code === 'ETIMEDOUT') {
    console.warn('[conduit-srv] uncaughtException (connection noise):', err.code, err.message);
    return;
  }
  console.error('[conduit-srv] uncaughtException:', err.message, err.stack?.split('\n').slice(0, 3).join('\n'));
});

const server = app.listen(PORT, () => {
  console.log(`[conduit-srv] listening on http://localhost:${PORT}`);
  console.log(`  Workflows:     http://localhost:${PORT}/workflows`);
  console.log(`  Tickets:       http://localhost:${PORT}/tickets`);
  console.log(`  Tokens:        http://localhost:${PORT}/tokens`);
  console.log(`  Config:        http://localhost:${PORT}/config`);
  console.log(`  Governance:    http://localhost:${PORT}/governance`);
  console.log(`  Vision:        http://localhost:${PORT}/vision`);
  console.log(`  Session Log:   http://localhost:${PORT}/log/:sessionId`);
  console.log(`  Health:        http://localhost:${PORT}/health`);

  // Register with service-registry (port 8085) via heartbeat-client.
  // serviceId 104 = conduit-srv (100 + port % 100).
  startHeartbeat({
    serviceId: 104,
    serviceName: "conduit-srv",
    interval: 30,
    log: (...args: any[]) => console.log(new Date().toISOString(), "[heartbeat conduit-srv]", ...args),
  });
});

server.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`conduit-srv: port ${PORT} already in use, exiting (code EADDRINUSE)`);
  } else {
    console.error('conduit-srv: listen error:', err.message);
  }
  process.exit(1);
});
