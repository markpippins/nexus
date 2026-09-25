/**
 * db.ts — Database and Redis connections for harness-srv.
 *
 * Resolves the full context chain:
 *   wind.tasks → tackle.tasks → tackle.prompts + tackle.roles
 *   Redis mem:idx:{role} → procedure card index
 */

import { Pool } from "pg";
import Redis from "ioredis";
import { opencodeModelId, opencodeProviderFromConfig } from "./model.js";
import { decideConfigAdmission, decideRoleGovernance, type ConfigAdmission, type RoleGovernanceAdmission } from "./admission.js";

const pool = new Pool({
  host: process.env.PG_HOST || "localhost",
  port: parseInt(process.env.PG_PORT || "5432"),
  database: process.env.PG_DATABASE || "nexus",
  user: process.env.PG_USER || "pguser",
  password: process.env.PG_PASSWORD || "pgpass",
});

const redis = new Redis({
  host: process.env.REDIS_HOST || "localhost",
  port: parseInt(process.env.REDIS_PORT || "6379"),
  maxRetriesPerRequest: 3,
});

// ── Types ───────────────────────────────────────────────────────────

export interface RoleContext {
  role: string;
  prompt_slug: string;
  prompt_version: number;
  prompt_body: string;
  procedure_index: ProcedureCard[];
  tool_acl: ToolAclEntry[];
}

export interface ProcedureCard {
  slug: string;
  summary: string;
  tags: string[];
}

export interface ToolAclEntry {
  mcp_id: string;
  tool_slug: string;
}

export interface TaskContext {
  // From wind.tasks (workflow-specific)
  wind_task_id: string;
  wind_task_name: string;
  wind_task_description: string;
  input_spec: Record<string, any>;
  // From tackle.tasks (agent definition)
  tackle_task_id: string;
  task_slug: string;
  scope: string;
  acceptance_criteria: string[];
}

export interface TaskOutcome {
  id: string;
  code: string;
  description: string;
}

export interface ResolvedContext {
  role: string;
  prompt: string; // fully resolved prompt with {{PROCEDURE_INDEX}} replaced
  task: TaskContext;
  outcomes: TaskOutcome[];
  harness_id: string;
  harness_config: Record<string, any>;
  model: ResolvedModelConfig | null; // tackle config_bundle resolution for the role
}

/**
 * Active AI model config for a role, resolved from tackle.config_bundle
 * (mirrors tackle-srv getResolvedRoleConfig).
 */
export interface ResolvedModelConfig {
  model_identifier: string;
  provider_id: string;
  provider_name: string;
  provider_type: string;
  api_key: string | null;
  endpoint_url: string | null;
  harness_id: string;
  harness_name: string;
  invocation_semantics: Record<string, any>;
  /** config_bundle invocation_mode: CLI | HTTP | SDK | MCP | INTERACTIVE */
  invocation_mode: string;
  fallback_models: ResolvedFallbackModel[];
  /** opencode --model value computed from model_identifier */
  opencode_model_id: string;
}

export interface ResolvedFallbackModel {
  priority: number;
  model_identifier: string;
  /** opencode --model value computed from model_identifier (mirrors primary) */
  opencode_model_id: string;
  provider_type: string;
  api_key: string | null;
  endpoint_url: string | null;
  harness_id: string;
  harness_name: string;
  invocation_semantics: Record<string, any>;
  invocation_mode: string;
}

/**
 * Provider preference rank for fallback ordering — matches the operating
 * ladder: Nvidia first, then free OpenRouter, then free OpenCode Go,
 * then OpenCode (big-pickle), then Ollama (local, last resort), and the
 * known-dead DeepSeek key dead last.
 */
const PROVIDER_RANK: Record<string, number> = {
  "prov-1783906359513": 0, // Nvidia
  "prov-1782144397043": 1, // OpenRouter
  "prov-opencode-go": 2, // OpenCode Go
  "prov-opencode": 3, // OpenCode (big-pickle)
  "prov-ollama": 4, // Ollama
  "prov-deepseek": 5, // DeepSeek (key currently invalid)
};

// ── Resolution functions ────────────────────────────────────────────

/**
 * Resolve the full context for a workflow node.
 *
 * @param windTaskId - wind.tasks.id (the workflow node's task)
 * @param overrides - optional context overrides (inputs, etc.)
 */
export async function resolveContext(
  windTaskId: string,
  overrides?: Record<string, any>
): Promise<ResolvedContext> {
  // 1. Get wind.tasks + tackle.tasks + tackle.prompts in one join
  const taskResult = await pool.query(
    `
    SELECT
      wt.id as wind_task_id,
      wt.name as wind_task_name,
      wt.description as wind_task_description,
      wt.input_spec,
      tt.id as tackle_task_id,
      tt.task_slug,
      tt.scope,
      tt.acceptance_criteria,
      tt.role,
      tt.prompt_id,
      tp.slug as prompt_slug,
      tp.version as prompt_version,
      tp.body_md as prompt_body
    FROM wind.tasks wt
    JOIN tackle.tasks tt ON wt.tackle_task_id = tt.id
    JOIN tackle.prompts tp ON tt.prompt_id = tp.id
    WHERE wt.id = $1
    `,
    [windTaskId]
  );

  if (taskResult.rows.length === 0) {
    throw new Error(`No task found for wind.tasks.id=${windTaskId} (or no tackle_task_id linked)`);
  }

  const row = taskResult.rows[0];

  // 2. Get procedure card index from Redis
  const procedureIndex = await getProcedureIndex(row.role);

  // 3. Get tool ACL
  const toolAcl = await getToolAcl(row.role);

  // 4. Resolve {{PROCEDURE_INDEX}} in prompt
  const procedureIndexText = formatProcedureIndex(procedureIndex);
  const resolvedPrompt = row.prompt_body.replace(
    /\{\{PROCEDURE_INDEX\}\}/g,
    procedureIndexText
  );

  // 5. Get task outcomes from wind.task_outcomes
  const outcomesResult = await pool.query(
    `SELECT id, code, description FROM wind.task_outcomes WHERE task_id = $1`,
    [row.wind_task_id]
  );

  // 6. Resolve the role's active AI model from tackle config_bundle
  const modelConfig = await resolveRoleModel(row.role);

  // 7. Determine harness — prefer the one the config bundle specifies
  // (harn-opencode), falling back to the default when the bundle
  // references an unknown harness id.
  const harness = await getDefaultHarness(modelConfig?.harness_id || undefined);

  return {
    role: row.role,
    prompt: resolvedPrompt,
    task: {
      wind_task_id: row.wind_task_id,
      wind_task_name: row.wind_task_name,
      wind_task_description: row.wind_task_description,
      input_spec: row.input_spec || {},
      tackle_task_id: row.tackle_task_id,
      task_slug: row.task_slug,
      scope: row.scope,
      acceptance_criteria: row.acceptance_criteria || [],
    },
    outcomes: outcomesResult.rows as TaskOutcome[],
    harness_id: harness.id,
    harness_config: harness.config,
    model: modelConfig,
  };
}

/**
 * Resolve the active AI model config for a role from tackle.config_bundle.
 *
 * All active bundles for the role are loaded and sorted by provider
 * preference rank (Nvidia > OpenRouter > OpenCode Go > OpenCode >
 * Ollama > DeepSeek), then by bundle priority as a tiebreak. The first
 * entry is the primary; the rest form the fallback chain. Returns null
 * when the role has no active bundle (caller falls back to the harness
 * default).
 */
export async function resolveRoleModel(role: string): Promise<ResolvedModelConfig | null> {
  const result = await pool.query(
    `SELECT m.model_identifier,
            COALESCE(cb.provider_id, m.provider_id) AS provider_id,
            p.name AS provider_name,
            COALESCE(p.type, '') AS provider_type,
            p.api_key,
            p.config_json AS provider_config_json,
            COALESCE(cb.endpoint_url, p.endpoint_url) AS endpoint_url,
            COALESCE(h.id, '') AS harness_id,
            COALESCE(h.name, '') AS harness_name,
            COALESCE(h.invocation_semantics, '{}') AS invocation_semantics,
            COALESCE(cb.invocation_mode, '') AS invocation_mode,
            cb.priority
     FROM tackle.config_bundle cb
     JOIN tackle.models m          ON cb.model_id = m.id
     LEFT JOIN tackle.providers p  ON COALESCE(cb.provider_id, m.provider_id) = p.id
     LEFT JOIN tackle.harnesses h  ON COALESCE(cb.harness_id, m.harness_id) = h.id
     WHERE cb.role = $1 AND cb.is_active = 1
       AND (cb.valid_from IS NULL OR cb.valid_from <= NOW())
       AND (cb.valid_to IS NULL OR cb.valid_to > NOW())
     ORDER BY cb.priority ASC`,
    [role]
  );
  if (result.rows.length === 0) return null;

  const parseJson = (v: any): Record<string, any> =>
    typeof v === "string" ? (JSON.parse(v) as Record<string, any>) : (v as Record<string, any>);

  const rows = result.rows as any[];
  // Config bundle priority wins (tackle-ui + admin demotions are encoded
  // there); provider rank breaks ties between bundles at the same priority.
  rows.sort(
    (a, b) =>
      (a.priority ?? 0) - (b.priority ?? 0) ||
      (PROVIDER_RANK[a.provider_id] ?? 6) - (PROVIDER_RANK[b.provider_id] ?? 6)
  );

  const primary = rows[0];
  const fallbacks: ResolvedFallbackModel[] = rows.slice(1).map((f: any) => ({
    priority: f.priority,
    model_identifier: f.model_identifier,
    opencode_model_id: opencodeModelId(
      f.provider_id ?? "",
      f.model_identifier,
      opencodeProviderFromConfig(f.provider_config_json)
    ),
    provider_id: f.provider_id ?? "",
    provider_type: f.provider_type ?? "",
    api_key: f.api_key ?? null,
    endpoint_url: f.endpoint_url ?? null,
    harness_id: f.harness_id ?? "",
    harness_name: f.harness_name ?? "",
    invocation_semantics: parseJson(f.invocation_semantics),
    invocation_mode: f.invocation_mode ?? "",
  }));

  return {
    model_identifier: primary.model_identifier,
    provider_id: primary.provider_id ?? "",
    provider_name: primary.provider_name ?? "",
    provider_type: primary.provider_type ?? "",
    api_key: primary.api_key ?? null,
    endpoint_url: primary.endpoint_url ?? null,
    harness_id: primary.harness_id ?? "",
    harness_name: primary.harness_name ?? "",
    invocation_semantics: parseJson(primary.invocation_semantics),
    invocation_mode: primary.invocation_mode ?? "",
    fallback_models: fallbacks,
    opencode_model_id: opencodeModelId(
      primary.provider_id ?? "",
      primary.model_identifier,
      opencodeProviderFromConfig(primary.provider_config_json)
    ),
  };
}

/**
 * Check whether a role is admissible via its config_bundle validity.
 *
 * Distinguishes three denial reasons (T20 admission gate):
 *   - NO_CONFIG           — no config_bundle rows for the role
 *   - ROLE_REVOKED        — bundles exist but all is_active=0
 *   - CONFIG_INVALIDATED  — active bundles but none within valid_from/valid_to
 */
export type LeaseDerivedState = "NEVER_LEASED" | "ACTIVE" | "REVOKED" | "EXPIRED";

/**
 * D-2026-08-16-009 (R6): capability-proof admission from the governance store.
 *
 * nebula.roles is a bitemporal view — it only returns rows whose recorded
 * interval AND validity window are current. Classification order:
 *   1. current         — in nebula.roles → capability check (owns_domains).
 *   2. expired         — in roles_history but not the current view → validity
 *                        window not current (expired or not yet active).
 *   3. runtime_persona — in tackle.roles (runtime plane) but not a governance
 *                        role → admitted here; its capability proof is the
 *                        runtime config_bundle, gated by config admission.
 *   4. missing         — no canonical key anywhere → denied.
 */
export async function checkRoleGovernance(role: string): Promise<RoleGovernanceAdmission> {
  const current = await pool.query(
    `SELECT owns_domains FROM nebula.roles WHERE name = $1`,
    [role]
  );
  if (current.rows.length > 0) {
    return decideRoleGovernance({
      kind: "current",
      owns_domains: current.rows[0].owns_domains,
    });
  }
  const history = await pool.query(
    `SELECT 1 FROM nebula.roles_history WHERE name = $1 LIMIT 1`,
    [role]
  );
  if (history.rows.length > 0) {
    return decideRoleGovernance({ kind: "expired" });
  }
  const persona = await pool.query(
    `SELECT 1 FROM tackle.roles WHERE name = $1 LIMIT 1`,
    [role]
  );
  return decideRoleGovernance(
    persona.rows.length > 0 ? { kind: "runtime_persona" } : { kind: "missing" }
  );
}

/**
 * D-2026-08-16-008 (R1): derived lease state for a role from
 * tackle.role_leases — NEVER_LEASED / ACTIVE / REVOKED / EXPIRED.
 */
export async function checkRoleLeaseState(role: string): Promise<LeaseDerivedState> {
  const result = await pool.query(
    `SELECT status, released_at, window_end, expires_at
     FROM tackle.role_leases
     WHERE role = $1
     ORDER BY created_at DESC LIMIT 1`,
    [role]
  );
  if (result.rows.length === 0) return "NEVER_LEASED";
  const last = result.rows[0];
  if (last.status === "ACTIVE") {
    const pastWindow = new Date(last.expires_at || last.window_end) < new Date();
    return pastWindow ? "EXPIRED" : "ACTIVE";
  }
  if (last.status === "RELEASED" && last.released_at) return "REVOKED";
  return "EXPIRED";
}

export async function checkConfigAdmission(role: string): Promise<ConfigAdmission> {
  // 1. Governance capability-proof (D-2026-08-16-009 R6): canonical role
  //    key + active validity window + capability proof BEFORE runtime
  //    admission. Denials: ROLE_MISSING / ROLE_EXPIRED /
  //    CAPABILITY_INSUFFICIENT. A runtime persona (tackle.roles, absent from
  //    nebula.roles) is admitted here; its capability proof is the runtime
  //    config_bundle checked in step 2.
  const governance = await checkRoleGovernance(role);
  if (!governance.valid) return governance;

  // 2. Runtime config admission (NO_CONFIG / ROLE_REVOKED /
  //    CONFIG_INVALIDATED).
  const result = await pool.query(
    `SELECT is_active,
            (valid_from IS NOT NULL AND valid_from > NOW()) AS not_yet_valid,
            (valid_to IS NOT NULL AND valid_to <= NOW()) AS expired
     FROM tackle.config_bundle
     WHERE role = $1`,
    [role]
  );
  const config = decideConfigAdmission(result.rows as any[]);
  if (!config.valid) return config;

  // 3. D-2026-08-16-008 (R2): lease-derived revocation gates admission. An
  // explicitly-revoked lease (released_at set) is a governance signal — deny
  // new work with ROLE_REVOKED. NEVER_LEASED stays advisory (warn-and-
  // proceed); lease quota remains worker-pool-only (D-007).
  const leaseState = await checkRoleLeaseState(role);
  if (leaseState === "REVOKED") {
    return {
      valid: false,
      outcome: "ROLE_REVOKED",
      message: "role lease revoked — issue a new lease to resume work",
    };
  }
  if (leaseState === "NEVER_LEASED") {
    console.warn(`[admission] role ${role} is NEVER_LEASED — advisory (warn-and-proceed)`);
  }
  return { valid: true };
}

/**
 * Get procedure card index for a role from Redis.
 */
async function getProcedureIndex(role: string): Promise<ProcedureCard[]> {
  const key = `mem:idx:${role}`;
  const raw = await redis.get(key);
  if (!raw) return [];
  try {
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

/**
 * Get tool ACL for a role from tackle.role_tool_access.
 */
async function getToolAcl(role: string): Promise<ToolAclEntry[]> {
  const result = await pool.query(
    `SELECT mcp_id, tool_slug FROM tackle.role_tool_access WHERE role = $1`,
    [role]
  );
  return result.rows;
}

/**
 * Format procedure card index as a readable list for prompt injection.
 */
function formatProcedureIndex(cards: ProcedureCard[]): string {
  if (cards.length === 0) {
    return "(no procedure cards available for this role)";
  }
  return cards
    .map((c) => `- **${c.slug}**: ${c.summary}`)
    .join("\n");
}

/**
 * Get a harness by id, or the default harness (opencode CLI, harn-opencode)
 * when no preferred id is given or it does not exist.
 *
 * Note: this used to default to 'harn-ollama', which silently routed every
 * harness-srv run through the local Ollama direct-http path (qwen2.5:0.5b)
 * regardless of the role's tackle config_bundle. The default is now the
 * opencode harness so external-model config from tackle-ui takes effect.
 */
async function getDefaultHarness(preferredId?: string): Promise<{
  id: string;
  config: Record<string, any>;
}> {
  const parseConfig = (row: any): Record<string, any> =>
    typeof row.invocation_semantics === "string"
      ? JSON.parse(row.invocation_semantics)
      : row.invocation_semantics;

  if (preferredId) {
    const preferred = await pool.query(
      `SELECT id, invocation_semantics FROM tackle.harnesses WHERE id = $1`,
      [preferredId]
    );
    if (preferred.rows.length > 0) {
      return { id: preferred.rows[0].id, config: parseConfig(preferred.rows[0]) };
    }
  }

  // Default: opencode CLI harness
  const result = await pool.query(
    `SELECT id, invocation_semantics FROM tackle.harnesses WHERE id = 'harn-opencode'`
  );
  if (result.rows.length > 0) {
    return { id: result.rows[0].id, config: parseConfig(result.rows[0]) };
  }

  // Fallback to any harness
  const fallback = await pool.query(
    `SELECT id, invocation_semantics FROM tackle.harnesses LIMIT 1`
  );
  if (fallback.rows.length === 0) {
    throw new Error("No harnesses configured in tackle.harnesses");
  }
  return { id: fallback.rows[0].id, config: parseConfig(fallback.rows[0]) };
}

/**
 * Emit an event to cascade.events.
 */
export async function emitEvent(params: {
  event_type: string;
  source: string;
  aggregate_type?: string;
  aggregate_id?: string;
  payload?: Record<string, any>;
  actor_type?: string;
  causation_id?: string;
  caused_by_event_type?: string;
}): Promise<string> {
  const { v4: uuidv4 } = await import("uuid");
  const eventId = uuidv4();
  const now = new Date().toISOString();

  await pool.query(
    `
    INSERT INTO cascade.events (
      event_id, event_type, source, event_timestamp,
      payload, aggregate_type, aggregate_id,
      actor_type, actor_id,
      causation_id, caused_by_event_type
    ) VALUES (
      $1::uuid, $2, $3, $4,
      $5::jsonb, $6, $7,
      $8, $9,
      $10::uuid, $11
    )
    `,
    [
      eventId,
      params.event_type,
      params.source,
      now,
      JSON.stringify(params.payload || {}),
      params.aggregate_type || null,
      params.aggregate_id || null,
      params.actor_type || "harness",
      "harness-srv",
      params.causation_id || null,
      params.caused_by_event_type || null,
    ]
  );

  return eventId;
}

export { pool, redis };

// ── Role-lease guard (RoleLeases plan 1286, slice 3) ─────────────────

export interface RoleLeaseStatus {
  id: string;
  role: string;
  channel: string;
  status: string;
  window_end: string;
  budget_units: number | null;
  consumed_units: number;
  expired: boolean;
  exhausted: boolean;
}

/**
 * Check whether a role has an active lease in tackle.role_leases.
 *
 * Returns null when no ACTIVE lease exists (role has not been leased).
 * Returns the lease with computed `expired` (past window_end) and
 * `exhausted` (budget consumed) flags so callers can decide whether
 * to proceed, warn, or reject.
 */
export async function checkRoleLease(role: string): Promise<RoleLeaseStatus | null> {
  const result = await pool.query(
    `SELECT id, role, channel, status,
            window_end, budget_units, consumed_units,
            NOW() > window_end AS expired,
            (budget_units IS NOT NULL AND consumed_units >= budget_units) AS exhausted
     FROM tackle.role_leases
     WHERE role = $1 AND status = 'ACTIVE'
     LIMIT 1`,
    [role]
  );
  if (result.rows.length === 0) return null;
  return result.rows[0] as RoleLeaseStatus;
}

/**
 * Increment consumed_units on the active role lease via the canonical
 * POST /api/role-leases/consume endpoint (nebula-srv).
 *
 * Unified accounting: all three execution channels (execution_worker,
 * harness-srv, interactive) hit the same endpoint, so lease accounting
 * is a single canonical event rather than three copies of the same SQL.
 *
 * Idempotent — no-op when no ACTIVE lease exists for the role.
 */
export async function incrementConsumedUnits(role: string): Promise<void> {
  const nebulaUrl = process.env.NEBULA_URL || "http://localhost:3101";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  try {
    await fetch(`${nebulaUrl}/api/role-leases/consume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

// ── Governance receipts (vision.receipts → peb.governance_events) ──
// The harness/Wind channel was the last unverified execution channel for
// governance events (it only wrote cascade.events). The receipt payload
// builder + best-effort emitter live in the side-effect-free
// ./governance module so unit tests can load them without constructing
// db/redis clients; index.ts imports emitGovernanceReceipt via this
// re-export.
export { buildGovernanceReceiptPayload, emitGovernanceReceipt } from "./governance.js";
export type { GovernanceReceiptParams } from "./governance.js";
