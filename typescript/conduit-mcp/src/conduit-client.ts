/**
 * conduit-client.ts — HTTP REST client for Python conduit FastAPI (port 3103).
 *
 * All SQL that was in conduit-mcp's db.ts is being consolidated into the Python
 * conduit's DBAdapter + FastAPI routes. This client wraps the Python REST API
 * so conduit-mcp becomes a thin MCP-to-REST proxy.
 *
 * Phase 1: Sessions + Circuit Breaker
 * Phase 2: Receipts + Plans (coming)
 * Phase 3: Work Requests + Governance (coming)
 *
 * ROUTE CONTRACT (live, verified 2026-09-22 against the deployed Python
 * conduit FastAPI :3103 openapi.json): callers MUST match the LIVE contract,
 * not the planned consolidated one. The pre-consolidation bare-/api forms
 * (e.g. GET /api/{plan_id}/latest-type) are served by NO deployed server —
 * the client treated their 404s as null, which silently zeroed receipt
 * validation and insertion fleet-wide ("current state is none"; Conduit
 * walk-through finding, 2026-09-22). Live router prefixes:
 *   receipts_router: "/api/receipts"
 *   sessions_router: "/api/sessions"
 *   breaker_router:  "/api/breaker"
 * Plan 8261651 tracks the consolidated no-prefix forms; when a server that
 * actually serves them is deployed, repoint here under test — never
 * pre-empt the server (this file did once; it froze the pipeline).
 *   delta_router: "/delta/"
 */

const CONDUIT_API = process.env.CONDUIT_API_URL || "http://localhost:3103";

// ── HTTP helpers ─────────────────────────────────────────────────

async function get(path: string): Promise<any> {
  const res = await fetch(`${CONDUIT_API}${path}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`conduit-api GET ${path} → ${res.status}: ${errText}`);
  }
  return res.json();
}

async function post(path: string, body?: Record<string, any>): Promise<any> {
  const res = await fetch(`${CONDUIT_API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`conduit-api POST ${path} → ${res.status}: ${errText}`);
  }
  return res.json();
}

async function del(path: string): Promise<any> {
  const res = await fetch(`${CONDUIT_API}${path}`, { method: "DELETE" });
  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`conduit-api DELETE ${path} → ${res.status}: ${errText}`);
  }
  return res.json();
}

async function patch(path: string, body: Record<string, any>): Promise<any> {
  const res = await fetch(`${CONDUIT_API}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => "");
    throw new Error(`conduit-api PATCH ${path} → ${res.status}: ${errText}`);
  }
  return res.json();
}

// ── Sessions ───────────────────────────────────────────────────
// Router: sessions_router (live prefix="/api/sessions")

export async function getAllSessions(): Promise<any[]> {
  const data = await get("/api/sessions/");
  return data?.sessions || data || [];
}

export async function getSession(sessionId: string): Promise<any> {
  return get(`/api/sessions/${encodeURIComponent(sessionId)}`);
}

export async function getRunningSessions(): Promise<any[]> {
  const data = await get("/api/sessions/running");
  return data?.sessions || data || [];
}

export async function getStaleSessions(thresholdSeconds: number = 3600): Promise<any[]> {
  const data = await get(`/api/sessions/stale?threshold_seconds=${thresholdSeconds}`);
  return data?.sessions || data || [];
}

export async function updateSessionCost(sessionId: string, costUsd: number): Promise<any> {
  return patch(`/api/sessions/${encodeURIComponent(sessionId)}/cost`, { cost_usd: costUsd });
}

export async function updateSessionHeartbeat(sessionId: string): Promise<any> {
  return post(`/api/sessions/${encodeURIComponent(sessionId)}/heartbeat`);
}

export async function killSession(sessionId: string): Promise<any> {
  return post(`/api/sessions/${encodeURIComponent(sessionId)}/kill`);
}

// ── Circuit Breaker ─────────────────────────────────────────────
// Router: breaker_router (live prefix="/api/breaker")

export async function getBreaker(): Promise<any> {
  return get("/api/breaker/");
}

export async function tripBreaker(input: {
  error: string;
  detail?: string;
  source?: string;
  retryAfter?: number;
}): Promise<any> {
  return post("/api/breaker/trip", {
    reason: input.error,
    detail: input.detail,
    retryAfter: input.retryAfter,
  });
}

export async function clearBreaker(): Promise<any> {
  return post("/api/breaker/reset");
}

export async function setConduitPaused(paused: boolean): Promise<any> {
  return post(paused ? "/api/breaker/pause" : "/api/breaker/resume");
}

export async function isConduitPaused(): Promise<boolean> {
  const breaker = await getBreaker();
  return breaker?.paused === true;
}

export async function getFailureRecoveryConfig(): Promise<any> {
  return get("/api/breaker/failure-recovery");
}

export async function saveFailureRecoveryConfig(config: {
  max_retries_per_model?: number;
  retry_delay_seconds?: number;
  max_fallbacks?: number;
  push_back_to_pending?: boolean;
  circuit_breaker_retry_after?: number;
}): Promise<any> {
  return post("/api/breaker/failure-recovery", config);
}

// ── Receipts ───────────────────────────────────────────────────
// Router: receipts_router (live prefix="/api/receipts")

export async function getPlanReceipts(planId: string): Promise<{ plan_id: string; count: number; receipts: any[] }> {
  return get(`/api/receipts/${encodeURIComponent(planId)}`);
}

export async function getReceiptsRaw(planId: string): Promise<{ plan_id: string; count: number; receipts: any[] }> {
  return get(`/api/receipts/${encodeURIComponent(planId)}/raw`);
}

export async function getLatestReceiptType(planId: string): Promise<string | null> {
  const data = await get(`/api/receipts/${encodeURIComponent(planId)}/latest-type`);
  return data?.latest_type ?? null;
}

export async function insertReceipt(r: {
  id: string;
  plan_id: string;
  type: string;
  agent_role: string;
  session_id?: string;
  ticket_id?: string | null;
  artifact_path?: string | null;
  summary?: string;
  metadata_json?: string;
  tokens_used?: number;
  created_at: string;
  /** C1 gate 1 (Lilac): declaring-producer identity forwarded to the Python
   * REST persistence layer (optional; defaults to the Python channel). */
  producer_id?: string;
  source_channel?: string;
  correlation_id?: string;
}): Promise<{ ok: boolean; id: string; plan_id: string }> {
  return post("/api/receipts/", {
    id: r.id,
    plan_id: r.plan_id,
    type: r.type,
    agent_role: r.agent_role,
    session_id: r.session_id || "",
    ticket_id: r.ticket_id || null,
    artifact_path: r.artifact_path || null,
    summary: r.summary || "",
    metadata_json: r.metadata_json || "{}",
    tokens_used: r.tokens_used || 0,
    created_at: r.created_at,
    producer_id: r.producer_id,
    source_channel: r.source_channel,
    correlation_id: r.correlation_id,
  });
}

export async function deleteReceiptsByPlanAndType(
  planId: string,
  types: string[],
): Promise<number> {
  const data = await del(`/api/receipts/${encodeURIComponent(planId)}?types=${types.join(",")}`);
  return data?.deleted ?? 0;
}

// ── Health ──────────────────────────────────────────────────────

export async function checkHealth(): Promise<any> {
  return get("/healthz");
}