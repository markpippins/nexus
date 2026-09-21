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
 * CONSOLIDATION NOTE (Plan 8261651): Routes updated to match consolidated
 * kernel (losm-host, port 3106) where receipts/sessions/breaker share the
 * /api prefix with NO sub-path prefixes. Router prefixes from main.py:
 *   receipts_router: "/api"  (NOT "/api/receipts")
 *   sessions_router: "/api"  (NOT "/api/sessions")
 *   breaker_router: "/api"   (NOT "/api/breaker")
 *   state_router: "/state/"
 *   replay_router: "/replay/"
 *   admin_router: "/admin/"
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
// Router: sessions_router (prefix="/api")

export async function getAllSessions(): Promise<any[]> {
  const data = await get("/api");
  return data?.sessions || data || [];
}

export async function getSession(sessionId: string): Promise<any> {
  return get(`/api/${encodeURIComponent(sessionId)}`);
}

export async function getRunningSessions(): Promise<any[]> {
  const data = await get("/api/running");
  return data?.sessions || data || [];
}

export async function getStaleSessions(thresholdSeconds: number = 3600): Promise<any[]> {
  const data = await get(`/api/stale?threshold_seconds=${thresholdSeconds}`);
  return data?.sessions || data || [];
}

export async function updateSessionCost(sessionId: string, costUsd: number): Promise<any> {
  return patch(`/api/${encodeURIComponent(sessionId)}/cost`, { cost_usd: costUsd });
}

export async function updateSessionHeartbeat(sessionId: string): Promise<any> {
  return post(`/api/${encodeURIComponent(sessionId)}/heartbeat`);
}

export async function killSession(sessionId: string): Promise<any> {
  return post(`/api/${encodeURIComponent(sessionId)}/kill`);
}

// ── Circuit Breaker ─────────────────────────────────────────────
// Router: breaker_router (prefix="/api")

export async function getBreaker(): Promise<any> {
  return get("/api/breaker");
}

export async function tripBreaker(input: {
  error: string;
  detail?: string;
  source?: string;
  retryAfter?: number;
}): Promise<any> {
  return post("/api/trip", {
    reason: input.error,
    detail: input.detail,
    retryAfter: input.retryAfter,
  });
}

export async function clearBreaker(): Promise<any> {
  return post("/api/reset");
}

export async function setConduitPaused(paused: boolean): Promise<any> {
  return post(paused ? "/api/pause" : "/api/resume");
}

export async function isConduitPaused(): Promise<boolean> {
  const breaker = await getBreaker();
  return breaker?.paused === true;
}

export async function getFailureRecoveryConfig(): Promise<any> {
  return get("/api/failure-recovery");
}

export async function saveFailureRecoveryConfig(config: {
  max_retries_per_model?: number;
  retry_delay_seconds?: number;
  max_fallbacks?: number;
  push_back_to_pending?: boolean;
  circuit_breaker_retry_after?: number;
}): Promise<any> {
  return post("/api/failure-recovery", config);
}

// ── Receipts ───────────────────────────────────────────────────
// Router: receipts_router (prefix="/api")

export async function getPlanReceipts(planId: string): Promise<{ plan_id: string; count: number; receipts: any[] }> {
  return get(`/api/${encodeURIComponent(planId)}`);
}

export async function getReceiptsRaw(planId: string): Promise<{ plan_id: string; count: number; receipts: any[] }> {
  return get(`/api/${encodeURIComponent(planId)}/raw`);
}

export async function getLatestReceiptType(planId: string): Promise<string | null> {
  const data = await get(`/api/${encodeURIComponent(planId)}/latest-type`);
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
  return post("/api", {
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
  const data = await del(`/api/${encodeURIComponent(planId)}?types=${types.join(",")}`);
  return data?.deleted ?? 0;
}

// ── Health ──────────────────────────────────────────────────────

export async function checkHealth(): Promise<any> {
  return get("/healthz");
}