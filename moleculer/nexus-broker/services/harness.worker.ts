import "dotenv/config";
import { Service, ServiceBroker, Context } from "moleculer";
import { Pool } from "pg";
import Redis from "ioredis";
import { v4 as uuidv4 } from "uuid";
import { execFile, spawn } from "child_process";
import { promisify } from "util";
import { writeFile, readFile, unlink, mkdir } from "fs/promises";
import { join } from "path";

const execFileAsync = promisify(execFile);

/**
 * worker.harness — process-spawning harness worker (Wave 4.1).
 *
 * Ports harness-srv's core flow (resolve context → admission gate →
 * governance receipts → opencode/ollama execution → watchdog) into broker
 * actions. Same SQL, same receipts, same guardrails.
 */

// ── Side-effect-free helpers (mirrors harness-srv admission/governance/model) ──
const ADMISSION_OUTCOME = {
  ADMISSION_DENIED: "ADMISSION_DENIED",
  ROLE_REVOKED: "ROLE_REVOKED",
  CONFIG_INVALIDATED: "CONFIG_INVALIDATED",
  NO_CONFIG: "NO_CONFIG",
} as const;

function decideConfigAdmission(bundles: any[]): { valid: boolean; outcome?: string; message?: string } {
  if (bundles.length === 0) {
    return { valid: false, outcome: "NO_CONFIG", message: "role has no config bundle configured" };
  }
  const anyActive = bundles.some((b) => b.is_active === 1);
  if (!anyActive) {
    return { valid: false, outcome: "ROLE_REVOKED", message: "role config bundle deactivated (is_active=0); re-activate before new work" };
  }
  const anyValid = bundles.some((b) => b.is_active === 1 && !b.not_yet_valid && !b.expired);
  if (!anyValid) {
    return { valid: false, outcome: "CONFIG_INVALIDATED", message: "role config bundle outside valid_from/valid_to window; correct the config before new work" };
  }
  return { valid: true };
}

/**
 * D-2026-08-16-009 (R6): governance-side role snapshot — port of harness-srv
 * admission.ts. The DB layer resolves a role to one of four states.
 */
type RoleGovernanceInput =
  | { kind: "current"; owns_domains: string[] | null }
  | { kind: "expired" }
  | { kind: "runtime_persona" }
  | { kind: "missing" };

type RoleGovernanceAdmission =
  | { valid: true }
  | { valid: false; outcome: string; message: string };

function decideRoleGovernance(input: RoleGovernanceInput): RoleGovernanceAdmission {
  if (input.kind === "missing") {
    return {
      valid: false,
      outcome: "ROLE_MISSING",
      message: "role has no canonical key — not a governance role or runtime persona",
    };
  }
  if (input.kind === "expired") {
    return {
      valid: false,
      outcome: "ROLE_EXPIRED",
      message: "role validity window expired or not yet active",
    };
  }
  if (input.kind === "runtime_persona") {
    return { valid: true };
  }
  if (!input.owns_domains || input.owns_domains.length === 0) {
    return {
      valid: false,
      outcome: "CAPABILITY_INSUFFICIENT",
      message: "role has no owned capabilities (owns_domains empty)",
    };
  }
  return { valid: true };
}

type LeaseDerivedState = "NEVER_LEASED" | "ACTIVE" | "REVOKED" | "EXPIRED";

const OPENCODE_PROVIDER_BY_TACKLE: Record<string, string> = {
  "prov-1783906359513": "nvidia",
  "prov-1782144397043": "openrouter",
  "prov-opencode-go": "opencode-go",
  "prov-opencode": "opencode",
  "prov-ollama": "ollama",
  "prov-deepseek": "deepseek",
};

function opencodeProviderFromConfig(configJson: unknown): string | undefined {
  if (!configJson) return undefined;
  try {
    const parsed = typeof configJson === "string" ? JSON.parse(configJson) : configJson;
    const name = parsed?.opencodeProvider;
    return typeof name === "string" && name.length > 0 ? name : undefined;
  } catch {
    return undefined;
  }
}

function opencodeModelId(providerId: string, modelIdentifier: string, override?: string): string {
  const provider = override || OPENCODE_PROVIDER_BY_TACKLE[providerId];
  if (provider) return `${provider}/${modelIdentifier}`;
  const slash = modelIdentifier.indexOf("/");
  if (slash > 0) return modelIdentifier.slice(0, slash) + "/" + modelIdentifier;
  return modelIdentifier;
}

const KNOWN_EXECUTORS = new Set([
  "planner", "builder", "reviewer", "analyst",
  "critic", "inspector", "architect", "engineer", "engineer-ii", "leased-builder",
  "watchdog",
]);

function buildGovernanceReceiptPayload(params: {
  planId: string;
  type: string;
  agentRole: string;
  sessionId: string;
  summary: string;
  metadata?: Record<string, any>;
}): Record<string, any> {
  const { planId, type, agentRole, sessionId, summary, metadata } = params;
  const agent_role = KNOWN_EXECUTORS.has(agentRole) ? agentRole : "builder";
  return {
    id: `rec-${planId}-${type}-${Date.now()}`,
    plan_id: planId,
    type,
    agent_role,
    session_id: sessionId,
    summary,
    metadata_json: JSON.stringify({ harness_channel: true, ...metadata }),
    tokens_used: 0,
    created_at: new Date().toISOString(),
  };
}

async function emitGovernanceReceipt(params: {
  planId: string;
  type: string;
  agentRole: string;
  sessionId: string;
  summary: string;
  metadata?: Record<string, any>;
}): Promise<void> {
  const payload = buildGovernanceReceiptPayload(params);
  const conduitUrl = (process.env.CONDUIT_MCP_URL || "http://localhost:3100").replace(/\/$/, "");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  try {
    const resp = await fetch(`${conduitUrl}/vision/receipts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!resp.ok) {
      const body = await resp.text();
      console.warn(`[worker.harness] governance receipt ${params.type} for ${params.planId} rejected: HTTP ${resp.status}: ${body.slice(0, 200)}`);
    } else {
      console.log(`[worker.harness] governance receipt ${params.type} issued for ${params.planId} (session ${params.sessionId.slice(0, 8)})`);
    }
  } catch (err: any) {
    console.warn(`[worker.harness] governance receipt ${params.type} for ${params.planId} failed: ${err?.message || err}`);
  } finally {
    clearTimeout(timer);
  }
}

// ── Watchdog (runaway session guard, T16) ─────────────────────────────
const RUNAWAY_THRESHOLD_MS = 15 * 60 * 1000;
const WATCHDOG_INTERVAL_MS = 60_000;

interface TrackedSession {
  jobId: string;
  role: string;
  model: string | undefined;
  startedAt: number;
  promptFile: string;
  wind_task_id?: string;
  pid?: number;
}

export default class HarnessWorker extends Service {
  private pool: Pool | null = null;
  private redis: Redis | null = null;
  private activeSessions = new Map<string, TrackedSession>();
  private watchdog: NodeJS.Timeout | null = null;

  constructor(broker: ServiceBroker) {
    super(broker);

    this.parseServiceSchema({
      name: "worker.harness",

      actions: {
        run: {
          params: {
            wind_task_id: "string",
            context: { type: "object", optional: true },
            work_dir: { type: "string", optional: true },
            harness_id: { type: "string", optional: true },
            agent: { type: "string", optional: true },
            timeout_ms: { type: "number", optional: true },
            resolve_only: { type: "boolean", optional: true },
          },
          handler: (ctx: Context<any>) => this.run(ctx),
        },

        resolveContext: {
          params: {
            wind_task_id: "string",
            context: { type: "object", optional: true },
          },
          handler: (ctx: Context<{ wind_task_id: string; context?: Record<string, any> }>) => this.resolveContext(ctx.params.wind_task_id, ctx.params.context),
        },

        sessions: {
          handler: () => {
            const sessions = Array.from(this.activeSessions.values()).map((s) => ({
              job_id: s.jobId,
              role: s.role,
              model: s.model,
              started_at: new Date(s.startedAt).toISOString(),
              elapsed_ms: Date.now() - s.startedAt,
              wind_task_id: s.wind_task_id,
              pid: s.pid,
            }));
            return { count: sessions.length, sessions };
          },
        },

        health: {
          handler: () => this.health(),
        },
      },

      async started() {
        this.pool = new Pool({
          host: process.env.PG_HOST || "localhost",
          port: Number(process.env.PG_PORT || 5432),
          user: process.env.PG_USER || "pguser",
          password: process.env.PG_PASSWORD || "pgpass",
          database: process.env.PG_DB_NAME || "nexus",
          max: 5,
          idleTimeoutMillis: 30000,
          connectionTimeoutMillis: 5000,
        });
        this.redis = new Redis({
          host: process.env.REDIS_HOST || "localhost",
          port: Number(process.env.REDIS_PORT || 6379),
          maxRetriesPerRequest: 2,
        });
        this.startWatchdog();
      },

      async stopped() {
        if (this.watchdog) clearInterval(this.watchdog);
        if (this.pool) await this.pool.end();
        if (this.redis) await this.redis.quit();
      },
    });
  }

  private startWatchdog(): void {
    this.watchdog = setInterval(async () => {
      const now = Date.now();
      for (const [jobId, session] of this.activeSessions) {
        if (now - session.startedAt < RUNAWAY_THRESHOLD_MS) continue;
        // Kill the child (SIGTERM then SIGKILL), emit BLOCK receipt, drop.
        try {
          if (session.pid) {
            try { process.kill(session.pid, "SIGTERM"); } catch { /* gone */ }
            setTimeout(() => { try { process.kill(session.pid!, "SIGKILL"); } catch { /* gone */ } }, 5000);
          }
        } catch { /* ignore */ }
        if (session.wind_task_id) {
          await emitGovernanceReceipt({
            planId: session.wind_task_id,
            type: "BLOCK",
            agentRole: session.role,
            sessionId: jobId,
            summary: `worker.harness ${jobId.slice(0, 8)}: watchdog killed runaway session (${now - session.startedAt}ms)`,
            metadata: { stage: "watchdog_kill", wind_task_id: session.wind_task_id },
          });
        }
        this.activeSessions.delete(jobId);
      }
    }, WATCHDOG_INTERVAL_MS);
  }

  private async getPool(): Promise<Pool> {
    if (!this.pool) {
      this.pool = new Pool({
        host: process.env.PG_HOST || "localhost",
        port: Number(process.env.PG_PORT || 5432),
        user: process.env.PG_USER || "pguser",
        password: process.env.PG_PASSWORD || "pgpass",
        database: process.env.PG_DB_NAME || "nexus",
        max: 5,
      });
    }
    return this.pool;
  }

  private async getRedis(): Promise<Redis> {
    if (!this.redis) {
      this.redis = new Redis({
        host: process.env.REDIS_HOST || "localhost",
        port: Number(process.env.REDIS_PORT || 6379),
        maxRetriesPerRequest: 2,
      });
    }
    return this.redis;
  }

  private async resolveContext(windTaskId: string, overrides?: Record<string, any>): Promise<any> {
    const pool = await this.getPool();
    const redis = await this.getRedis();

    const taskResult = await pool.query(
      `SELECT
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
      WHERE wt.id = $1`,
      [windTaskId],
    );

    if (taskResult.rows.length === 0) {
      throw new Error(`No task found for wind.tasks.id=${windTaskId} (or no tackle_task_id linked)`);
    }

    const row = taskResult.rows[0];

    // Procedure index from Redis
    const key = `mem:idx:${row.role}`;
    let procedureIndex: any[] = [];
    try {
      const raw = await redis.get(key);
      if (raw) procedureIndex = JSON.parse(raw);
    } catch { /* empty */ }
    const procedureIndexText = procedureIndex.length === 0
      ? "(no procedure cards available for this role)"
      : procedureIndex.map((c: any) => `- **${c.slug}**: ${c.summary}`).join("\n");
    const resolvedPrompt = row.prompt_body.replace(/\{\{PROCEDURE_INDEX\}\}/g, procedureIndexText);

    // Tool ACL
    const toolAclResult = await pool.query(
      `SELECT mcp_id, tool_slug FROM tackle.role_tool_access WHERE role = $1`,
      [row.role],
    );

    // Outcomes
    const outcomesResult = await pool.query(
      `SELECT id, code, description FROM wind.task_outcomes WHERE task_id = $1`,
      [row.wind_task_id],
    );

    // Model config
    const modelConfig = await this.resolveRoleModel(row.role);

    // Harness
    const harness = await this.getDefaultHarness(modelConfig?.harness_id || undefined);

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
      outcomes: outcomesResult.rows,
      harness_id: harness.id,
      harness_config: harness.config,
      model: modelConfig,
      tool_acl: toolAclResult.rows,
    };
  }

  private async resolveRoleModel(role: string): Promise<any | null> {
    const pool = await this.getPool();
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
      [role],
    );
    if (result.rows.length === 0) return null;

    const parseJson = (v: any): Record<string, any> =>
      typeof v === "string" ? JSON.parse(v) : v;

    const rows = result.rows as any[];
    const PROVIDER_RANK: Record<string, number> = {
      "prov-nvidia": 0, "prov-openrouter": 1, "prov-opencode-go": 2,
      "prov-opencode": 3, "prov-ollama": 4, "prov-deepseek": 5,
    };
    rows.sort(
      (a, b) =>
        (a.priority ?? 0) - (b.priority ?? 0) ||
        (PROVIDER_RANK[a.provider_id] ?? 6) - (PROVIDER_RANK[b.provider_id] ?? 6),
    );

    const primary = rows[0];
    const fallbacks = rows.slice(1).map((f: any) => ({
      priority: f.priority,
      model_identifier: f.model_identifier,
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
        opencodeProviderFromConfig(primary.provider_config_json),
      ),
    };
  }

  private async getDefaultHarness(preferredId?: string): Promise<{ id: string; config: Record<string, any> }> {
    const pool = await this.getPool();
    const parseConfig = (row: any): Record<string, any> =>
      typeof row.invocation_semantics === "string"
        ? JSON.parse(row.invocation_semantics)
        : row.invocation_semantics;

    if (preferredId) {
      const preferred = await pool.query(
        `SELECT id, invocation_semantics FROM tackle.harnesses WHERE id = $1`,
        [preferredId],
      );
      if (preferred.rows.length > 0) {
        return { id: preferred.rows[0].id, config: parseConfig(preferred.rows[0]) };
      }
    }

    const result = await pool.query(
      `SELECT id, invocation_semantics FROM tackle.harnesses WHERE id = 'harn-opencode'`,
    );
    if (result.rows.length > 0) {
      return { id: result.rows[0].id, config: parseConfig(result.rows[0]) };
    }

    const fallback = await pool.query(
      `SELECT id, invocation_semantics FROM tackle.harnesses LIMIT 1`,
    );
    if (fallback.rows.length === 0) throw new Error("No harnesses configured in tackle.harnesses");
    return { id: fallback.rows[0].id, config: parseConfig(fallback.rows[0]) };
  }

  private async emitEvent(params: {
    event_type: string;
    source: string;
    aggregate_type: string;
    aggregate_id: string;
    payload: Record<string, any>;
    actor_type: string;
    causation_id?: string;
    caused_by_event_type?: string;
  }): Promise<string | null> {
    const pool = await this.getPool();
    const r = await pool.query(
      `INSERT INTO cascade.events
         (event_id, event_type, source, event_timestamp,
          payload, aggregate_type, aggregate_id,
          actor_type, actor_id,
          causation_id, caused_by_event_type)
       VALUES (gen_random_uuid(), $1, $2, now(),
               $3::jsonb, $4, $5,
               $6, 'worker.harness',
               $7::uuid, $8)
       RETURNING event_id`,
      [
        params.event_type,
        params.source,
        JSON.stringify(params.payload || {}),
        params.aggregate_type || null,
        params.aggregate_id || null,
        params.actor_type || "harness",
        params.causation_id || null,
        params.caused_by_event_type || null,
      ],
    );
    return r.rows[0]?.event_id ?? null;
  }

  /**
   * D-2026-08-16-009 (R6): resolve a role to its governance snapshot and
   * decide capability-proof admission. Port of harness-srv checkRoleGovernance.
   */
  private async checkRoleGovernance(
    pool: Pool,
    role: string,
  ): Promise<RoleGovernanceAdmission> {
    const current = await pool.query(
      `SELECT owns_domains FROM nebula.roles WHERE name = $1`,
      [role],
    );
    if (current.rows.length > 0) {
      return decideRoleGovernance({
        kind: "current",
        owns_domains: current.rows[0].owns_domains,
      });
    }
    const history = await pool.query(
      `SELECT 1 FROM nebula.roles_history WHERE name = $1 LIMIT 1`,
      [role],
    );
    if (history.rows.length > 0) {
      return decideRoleGovernance({ kind: "expired" });
    }
    const persona = await pool.query(
      `SELECT 1 FROM tackle.roles WHERE name = $1 LIMIT 1`,
      [role],
    );
    return decideRoleGovernance(
      persona.rows.length > 0 ? { kind: "runtime_persona" } : { kind: "missing" },
    );
  }

  /**
   * D-2026-08-16-008 (R1): derived lease state for a role from
   * tackle.role_leases — NEVER_LEASED / ACTIVE / REVOKED / EXPIRED.
   * Port of harness-srv checkRoleLeaseState.
   */
  private async checkRoleLeaseState(pool: Pool, role: string): Promise<LeaseDerivedState> {
    const result = await pool.query(
      `SELECT status, released_at, window_end, expires_at
       FROM tackle.role_leases
       WHERE role = $1
       ORDER BY created_at DESC LIMIT 1`,
      [role],
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

  /**
   * The legacy three-gate admission (harness-srv checkConfigAdmission):
   * (1) governance capability-proof, (2) runtime config bundle,
   * (3) lease-derived revocation (D-2026-08-16-008 R2). The worker
   * previously implemented only gate 2 — the parity probe before the
   * harness repoint (PR #226) caught legacy denying on gate 3 while the
   * broker admitted.
   */
  private async checkAdmission(
    pool: Pool,
    role: string,
  ): Promise<{ valid: boolean; outcome?: string; message?: string }> {
    const governance = await this.checkRoleGovernance(pool, role);
    if (!governance.valid) return governance;

    const result = await pool.query(
      `SELECT is_active,
              (valid_from IS NOT NULL AND valid_from > NOW()) AS not_yet_valid,
              (valid_to IS NOT NULL AND valid_to <= NOW()) AS expired
       FROM tackle.config_bundle
       WHERE role = $1`,
      [role],
    );
    const config = decideConfigAdmission(result.rows as any[]);
    if (!config.valid) return config;

    const leaseState = await this.checkRoleLeaseState(pool, role);
    if (leaseState === "REVOKED") {
      return {
        valid: false,
        outcome: "ROLE_REVOKED",
        message: "role lease revoked — issue a new lease to resume work",
      };
    }
    if (leaseState === "NEVER_LEASED") {
      // Advisory only (warn-and-proceed), matching the legacy surface.
      this.logger.warn(`[admission] role ${role} is NEVER_LEASED — advisory (warn-and-proceed)`);
    }
    return { valid: true };
  }

  private async run(ctx: Context<any>): Promise<any> {
    const jobId = uuidv4();
    const startTime = Date.now();
    const {
      wind_task_id,
      context: contextOverrides,
      work_dir,
      harness_id,
      agent,
      timeout_ms = 300_000,
    } = ctx.params;
    const resolveOnly = ctx.params.resolve_only === true;

    const WORK_DIR = work_dir || process.env.HARNESS_WORK_DIR || "/home/codex/dev";
    const PROMPT_DIR = join(WORK_DIR, ".harness", "prompts");
    const pool = await this.getPool();

    try {
      if (!wind_task_id) throw Object.assign(new Error("wind_task_id is required"), { status: 400 });

      const resolved = await this.resolveContext(wind_task_id, contextOverrides);

      // Interactive-hosted guard (Freebuff roles) — never launched here.
      if (resolved.model?.invocation_mode === "INTERACTIVE") {
        return {
          job_id: jobId,
          error: `role ${resolved.role} is INTERACTIVE-hosted (Freebuff) — cannot be launched via harness; run it in the Freebuff interactive session instead`,
        };
      }

      // Admission gate (T20) — legacy three-gate admission, ported for
      // parity (see checkAdmission; the lease-revocation gate and the
      // governance capability-proof were missing before PR #226).
      const configAdmission = await this.checkAdmission(pool, resolved.role);
      if (!configAdmission.valid) {
        await this.emitEvent({
          event_type: "admission.denied",
          source: "worker.harness.run",
          aggregate_type: "harness_job",
          aggregate_id: jobId,
          payload: { wind_task_id, role: resolved.role, outcome: ADMISSION_OUTCOME.ADMISSION_DENIED, reason: configAdmission.outcome },
          actor_type: "system",
        });
        await emitGovernanceReceipt({
          planId: wind_task_id,
          type: "BLOCK",
          agentRole: resolved.role,
          sessionId: jobId,
          summary: `worker.harness ${jobId.slice(0, 8)}: admission denied (${configAdmission.outcome})`,
          metadata: { stage: "admission_denied", wind_task_id, outcome: ADMISSION_OUTCOME.ADMISSION_DENIED, reason: configAdmission.outcome },
        });
        return { job_id: jobId, error: configAdmission.message, admission: { outcome: ADMISSION_OUTCOME.ADMISSION_DENIED, reason: configAdmission.outcome } };
      }

      const effectiveHarnessId = harness_id || resolved.harness_id;
      const effectiveWorkDir = work_dir || WORK_DIR;
      const effectiveAgent = agent || resolved.role;
      const effectiveModel = resolved.model?.opencode_model_id;

      this.logger.info(`run job=${jobId} role=${resolved.role} task=${resolved.task.task_slug} model=${effectiveModel ?? "(harness default)"} wind_task=${wind_task_id}`);

      const startedEventId = await this.emitEvent({
        event_type: "harness.started",
        source: "worker.harness.run",
        aggregate_type: "harness_job",
        aggregate_id: jobId,
        payload: { wind_task_id, role: resolved.role, task_slug: resolved.task.task_slug, harness_id: effectiveHarnessId },
        actor_type: "system",
      });

      if (!resolveOnly) {
        await emitGovernanceReceipt({
          planId: wind_task_id,
          type: "PLAN_CREATE",
          agentRole: resolved.role,
          sessionId: jobId,
          summary: `worker.harness ${jobId.slice(0, 8)}: run started (${resolved.task.task_slug})`,
          metadata: { stage: "run_start", wind_task_id, task_slug: resolved.task.task_slug, harness_id: effectiveHarnessId },
        });
      }

      const fullPrompt = resolved.prompt;
      await mkdir(PROMPT_DIR, { recursive: true });
      const promptFile = join(PROMPT_DIR, `${jobId}.md`);
      await writeFile(promptFile, fullPrompt, "utf-8");

      let exitCode = 0;
      let stdout = "";
      let stderr = "";

      if (resolveOnly) {
        stdout = JSON.stringify({
          role: resolved.role,
          prompt_length: resolved.prompt.length,
          task: resolved.task,
          procedure_cards: resolved.prompt.match(/^- \*\*/gm)?.length || 0,
        });
      } else {
        this.activeSessions.set(jobId, {
          jobId,
          role: resolved.role,
          model: effectiveModel,
          startedAt: startTime,
          promptFile,
          wind_task_id,
        });
        try {
          const result = await this.executeHarness({
            harness_id: effectiveHarnessId,
            prompt_file: promptFile,
            work_dir: effectiveWorkDir,
            agent: effectiveAgent,
            role: resolved.role,
            model: effectiveModel,
            model_identifier: resolved.model?.model_identifier,
            timeout_ms,
            jobId,
          });
          stdout = result.stdout;
          stderr = result.stderr;
          exitCode = result.exitCode;
        } catch (execError: any) {
          exitCode = execError.exitCode || 1;
          stdout = execError.stdout || "";
          stderr = execError.stderr || execError.message;
        } finally {
          this.activeSessions.delete(jobId);
        }
      }

      await unlink(promptFile).catch(() => {});

      await this.emitEvent({
        event_type: exitCode === 0 ? "harness.completed" : "harness.failed",
        source: "worker.harness.run",
        aggregate_type: "harness_job",
        aggregate_id: jobId,
        payload: {
          wind_task_id,
          role: resolved.role,
          task_slug: resolved.task.task_slug,
          exit_code: exitCode,
          duration_ms: Date.now() - startTime,
          stdout_preview: stdout.slice(0, 500),
          stderr_preview: stderr.slice(0, 500),
        },
        actor_type: "system",
        causation_id: startedEventId ?? undefined,
        caused_by_event_type: "harness.started",
      });

      if (!resolveOnly) {
        const completedMetadata = {
          stage: "run_complete",
          wind_task_id,
          task_slug: resolved.task.task_slug,
          harness_id: effectiveHarnessId,
          exit_code: exitCode,
          duration_ms: Date.now() - startTime,
          stdout_preview: stdout.slice(0, 300),
          stderr_preview: stderr.slice(0, 300),
        };
        await emitGovernanceReceipt({
          planId: wind_task_id,
          type: "IMPLEMENTATION",
          agentRole: resolved.role,
          sessionId: jobId,
          summary: `worker.harness ${jobId.slice(0, 8)}: implementation ${exitCode === 0 ? "ok" : "failed"} (${resolved.task.task_slug})`,
          metadata: completedMetadata,
        });
        await emitGovernanceReceipt({
          planId: wind_task_id,
          type: exitCode === 0 ? "REVIEW_PASS" : "REVIEW_REJECT",
          agentRole: resolved.role,
          sessionId: jobId,
          summary: `worker.harness ${jobId.slice(0, 8)}: ${exitCode === 0 ? "completed" : "failed"} exit=${exitCode} (${resolved.task.task_slug})`,
          metadata: completedMetadata,
        });
      }

      return {
        job_id: jobId,
        role: resolved.role,
        task: {
          wind_task_id: resolved.task.wind_task_id,
          wind_task_name: resolved.task.wind_task_name,
          task_slug: resolved.task.task_slug,
          scope: resolved.task.scope,
        },
        outcomes: (resolved.outcomes || []).map((o: any) => ({ code: o.code, description: o.description })),
        harness_id: effectiveHarnessId,
        exit_code: exitCode,
        stdout,
        stderr,
        duration_ms: Date.now() - startTime,
        events: { started: startedEventId },
      };
    } catch (error: any) {
      await this.emitEvent({
        event_type: "harness.error",
        source: "worker.harness.run",
        aggregate_type: "harness_job",
        aggregate_id: jobId,
        payload: { error: error.message },
        actor_type: "system",
      }).catch(() => {});
      throw error;
    }
  }

  private async executeHarness(params: {
    harness_id: string;
    prompt_file: string;
    work_dir: string;
    agent: string;
    role: string;
    model?: string;
    model_identifier?: string;
    timeout_ms: number;
    jobId: string;
  }): Promise<{ exitCode: number; stdout: string; stderr: string }> {
    const { harness_id, prompt_file, work_dir, agent, role, timeout_ms, jobId } = params;
    const pool = await this.getPool();
    const promptContent = await readFile(prompt_file, "utf-8");

    const result = await pool.query(
      `SELECT invocation_semantics FROM tackle.harnesses WHERE id = $1`,
      [harness_id],
    );
    if (result.rows.length === 0) throw new Error(`Harness ${harness_id} not found in tackle.harnesses`);

    const config =
      typeof result.rows[0].invocation_semantics === "string"
        ? JSON.parse(result.rows[0].invocation_semantics)
        : result.rows[0].invocation_semantics;

    const binary = config.binary;

    if (binary === "ollama") {
      return this.executeOllama(promptContent, role, params.model_identifier ?? params.model, timeout_ms);
    } else if (binary === "opencode") {
      return this.executeOpencode({ ...params, promptContent, binary: config.binary, config });
    }
    throw new Error(`Harness ${harness_id} binary '${binary}' not yet supported`);
  }

  private async executeOllama(prompt: string, role: string, model: string | undefined, timeout_ms: number): Promise<{ exitCode: number; stdout: string; stderr: string }> {
    const ollamaUrl = process.env.OLLAMA_URL || "http://192.168.1.202:11434";
    const effectiveModel = model || process.env.OLLAMA_MODEL || "qwen2.5:0.5b";
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeout_ms);
      const resp = await fetch(`${ollamaUrl}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: effectiveModel, prompt, stream: false, options: { num_predict: 1024, temperature: 0.3 } }),
        signal: controller.signal,
      });
      clearTimeout(timer);
      const data: any = await resp.json();
      return { exitCode: resp.ok ? 0 : 1, stdout: data?.response || "", stderr: resp.ok ? "" : `ollama HTTP ${resp.status}` };
    } catch (err: any) {
      return { exitCode: 1, stdout: "", stderr: err.message };
    }
  }

  private async executeOpencode(params: any): Promise<{ exitCode: number; stdout: string; stderr: string }> {
    const { prompt_file, work_dir, agent, role, model, model_identifier, config, timeout_ms, jobId } = params;
    const cmd = config.command || config.binary || "opencode";
    const args: string[] = [];
    if (config.args) {
      for (const a of config.args) {
        args.push(String(a).replace("{{PROMPT_FILE}}", prompt_file).replace("{{ROLE}}", role));
      }
    } else {
      args.push("run", "--model", model || model_identifier || role, prompt_file);
    }

    const child = spawn(cmd, args, {
      cwd: work_dir,
      env: { ...(process.env as Record<string, string>), AGENT: agent, ROLE: role },
      stdio: ["ignore", "pipe", "pipe"],
    });

    // Track PID for watchdog
    const session = this.activeSessions.get(jobId);
    if (session) session.pid = child.pid;

    let stdout = "";
    let stderr = "";
    let killed = false;

    const timer = setTimeout(() => {
      killed = true;
      try { child.kill("SIGTERM"); } catch { /* ignore */ }
    }, timeout_ms);

    return new Promise((resolve) => {
      child.stdout?.on("data", (d) => { stdout += d; });
      child.stderr?.on("data", (d) => { stderr += d; });
      child.on("close", (code) => {
        clearTimeout(timer);
        resolve({ exitCode: killed ? 124 : (code ?? 1), stdout, stderr: killed ? stderr + `\n[timeout after ${timeout_ms}ms]` : stderr });
      });
      child.on("error", (err) => {
        clearTimeout(timer);
        resolve({ exitCode: 1, stdout, stderr: err.message });
      });
    });
  }

  private async health(): Promise<any> {
    const pool = await this.getPool();
    try {
      const { rows } = await pool.query("SELECT 1 as ok");
      return {
        status: "ok",
        db: rows[0].ok === 1,
        active_sessions: this.activeSessions.size,
      };
    } catch (err: any) {
      return { status: "error", db: false, message: err.message };
    }
  }
}
