/**
 * index.ts — Harness SRV: Generic execution harness.
 *
 * Merges Tackle role context (prompt + tool ACL + procedure cards)
 * with Wind task context (inputs + acceptance criteria) and
 * invokes an agent via the configured harness.
 *
 * Port: 3420
 *
 * Routes:
 *   POST /run              — resolve context + execute agent
 *   POST /resolve-context  — resolve context only (dry run)
 *   GET  /health           — health check
 */

import express from "express";
import rateLimit from "express-rate-limit";
import { resolveContext, resolveRoleModel, emitEvent, pool, redis, checkConfigAdmission, incrementConsumedUnits, emitGovernanceReceipt } from "./db.js";
import { ADMISSION_OUTCOME } from "./admission.js";
import { execFile, spawn } from "child_process";
import { promisify } from "util";
import { createHash } from "crypto";
import type { ServerResponse } from "http";
import { writeFile, readFile, unlink, mkdir, appendFile } from "fs/promises";
import { join, resolve } from "path";
import { v4 as uuidv4 } from "uuid";

const execFileAsync = promisify(execFile);
const app = express();
app.use(express.json());

// Global request limiter — mirrored from the incumbent (CodeQL
// js/missing-rate-limiting remediation): 300 req/min/IP, nebula-srv posture.
// Deliberately generous relative to job cost — execute routes are gated by
// admission + watchdog.
app.use(
  rateLimit({
    windowMs: 60 * 1000,
    max: 300,
    standardHeaders: "draft-7",
    legacyHeaders: false,
    message: { error: "harness-srv rate limit exceeded" },
  }),
);

const PORT = parseInt(process.env.HARNESS_PORT || "3420");
const WORK_DIR = process.env.HARNESS_WORK_DIR || "/home/codex/dev";
// Resolved absolute form — prompt files are written and read exclusively
// under this directory, so spawn/pass-through sites resolve against it and
// verify containment (CodeQL js/path-injection remediation, alert #590).
const PROMPT_DIR = resolve(WORK_DIR, ".harness", "prompts");

// ── Runaway watchdog (T16 guardrail, 1285 remediation slice 2) ───
const RUNAWAY_THRESHOLD_MS = 15 * 60 * 1000; // 15 min
const WATCHDOG_INTERVAL_MS = 60_000; // check every 60s

// ── First-token failover guard ────────────────────────────────────
// A model that never emits a text/reasoning token within this window is
// abandoned (exit 124 + marker) so the failover ladder can roll to the next
// model instead of waiting out the full run timeout on a hung provider.
const FIRST_TOKEN_TIMEOUT_MS = Number(process.env.FIRST_TOKEN_TIMEOUT_MS || 120_000);

// Ceiling for user-supplied timeout_ms on the execute routes — the value
// flows into child-process and abort timers, so it is clamped to 2 hours
// (CodeQL js/resource-exhaustion remediation, alerts #649/#650 family).
const MAX_RUN_TIMEOUT_MS = 2 * 60 * 60 * 1000;

// Allowed roots for user-supplied work_dir overrides — spawn cwd/`--dir`
// must resolve under one of these (default: the service work root; extend
// with HARNESS_EXTRA_WORK_ROOTS="a:b" if an operator needs more).
// CodeQL js/path-injection remediation (alerts #590/#751 family).
const WORK_ROOTS = [
  resolve(WORK_DIR),
  ...(process.env.HARNESS_EXTRA_WORK_ROOTS || "")
    .split(":")
    .filter(Boolean)
    .map((r) => resolve(r)),
];

function workDirAllowed(candidate: string): boolean {
  const abs = resolve(candidate);
  return WORK_ROOTS.some((root) => abs === root || abs.startsWith(root + "/"));
}

interface TrackedSession {
  jobId: string;
  role: string;
  model: string | undefined;
  startedAt: number;
  promptFile: string;
  wind_task_id?: string; // for governance BLOCK receipt on watchdog kill
  pid?: number; // child process PID (set by executeOpencode for direct SIGTERM)
}

const activeSessions = new Map<string, TrackedSession>();

// ── Async job registry (P1 item 6) ──────────────────────────────────
// POST /run-direct { async: true } → 202 { job_id, state } immediately;
// the job runs in the background and its lifecycle is observable via
// GET /jobs/:jobId (status + partial output) and GET /jobs/:jobId/events
// (replayable SSE). OpenCode's `--format json` event stream is translated
// ONCE here, at the boundary, into typed envelopes (text.delta / thinking)
// so no other consumer needs to know the opencode wire format. Partial
// output and exact exit/timeout/error metadata are preserved on the job.

type HarnessJobState =
  | "accepted" | "running" | "completed" | "failed" | "timed_out" | "cancelled";

interface HarnessJobEvent {
  seq: number;
  type:
    | "job.accepted" | "job.started" | "text.delta" | "thinking"
    | "job.completed" | "job.failed" | "job.timed_out" | "job.cancelled"
    | "job.fallback";
  ts: number;
  payload: Record<string, unknown>;
}

interface AttemptRecord {
  model: string;
  source: "primary" | "fallback";
  exit_code: number;
}

interface HarnessJob {
  jobId: string;
  role: string;
  model: string | null;
  modelUsed: string | null; // model that ran the terminal (successful) attempt
  attempts: AttemptRecord[]; // failover attempt log
  harnessId: string;
  state: HarnessJobState;
  startedAt: number;
  finishedAt: number | null;
  exitCode: number | null;
  stdout: string;      // raw accumulated stream (partial while running)
  stderr: string;
  durationMs: number | null;
  promptFile: string;
  events: HarnessJobEvent[];
  sseClients: Set<ServerResponse>;
  pid: number | null;  // child pid for interrupt (set by executeOpencode)
  interrupted: boolean; // set by POST /jobs/:id/interrupt — SIGTERM race guard
  plan: Record<string, unknown> | null; // P1 item 7 — versioned execution plan
}

const jobs = new Map<string, HarnessJob>();

function registerJob(opts: {
  jobId: string;
  role: string;
  model: string | null;
  harnessId: string;
  promptFile: string;
  plan?: Record<string, unknown> | null;
}): HarnessJob {
  const job: HarnessJob = {
    jobId: opts.jobId,
    role: opts.role,
    model: opts.model,
    modelUsed: null,
    attempts: [],
    harnessId: opts.harnessId,
    state: "accepted",
    startedAt: Date.now(),
    finishedAt: null,
    exitCode: null,
    stdout: "",
    stderr: "",
    durationMs: null,
    promptFile: opts.promptFile,
    events: [],
    sseClients: new Set(),
    pid: null,
    interrupted: false,
    plan: opts.plan ?? null,
  };
  // Evict oldest TERMINAL jobs beyond the cap so the in-memory registry
  // (status/events replay) does not grow unbounded.
  if (jobs.size >= 300) {
    const terminal = Array.from(jobs.values())
      .filter((j) => j.state !== "accepted" && j.state !== "running")
      .sort((a, b) => a.finishedAt! - b.finishedAt!);
    while (jobs.size >= 300 && terminal.length > 0) {
      const oldest = terminal.shift();
      if (oldest) jobs.delete(oldest.jobId);
    }
  }
  jobs.set(opts.jobId, job);
  pushJobEvent(opts.jobId, "job.accepted", {
    role: opts.role,
    model: opts.model,
    harness_id: opts.harnessId,
  });
  return job;
}

function pushJobEvent(
  jobId: string,
  type: HarnessJobEvent["type"],
  payload: Record<string, unknown>
): void {
  const job = jobs.get(jobId);
  if (!job) return;
  const ev: HarnessJobEvent = {
    seq: job.events.length + 1,
    type,
    ts: Date.now(),
    payload,
  };
  job.events.push(ev);
  const frame = `event: ${type}\ndata: ${JSON.stringify({
    seq: ev.seq,
    type,
    jobId,
    payload,
    ts: new Date(ev.ts).toISOString(),
  })}\n\n`;
  for (const client of job.sseClients) {
    try { client.write(frame); } catch { /* socket gone */ }
  }
}

function setJobState(
  jobId: string,
  state: HarnessJobState,
  extra: Partial<Pick<HarnessJob, "exitCode" | "stderr">> = {}
): void {
  const job = jobs.get(jobId);
  if (!job) return;
  // Forward transitions only: a terminal job is never rewritten (a late
  // runner callback cannot regress an already-cancelled/interrupted job).
  if (state !== "accepted" && state !== "running" && job.state !== "accepted" && job.state !== "running") {
    return;
  }
  job.state = state;
  if (extra.exitCode !== undefined) job.exitCode = extra.exitCode;
  if (extra.stderr !== undefined) job.stderr = extra.stderr;
  if (state !== "accepted" && state !== "running") {
    job.finishedAt = Date.now();
    job.durationMs = job.finishedAt - job.startedAt;
    // Terminal — drop SSE subscribers.
    for (const client of job.sseClients) {
      try { client.end(); } catch { /* noop */ }
    }
    job.sseClients.clear();
  }
  pushJobEvent(jobId, `job.${state}` as HarnessJobEvent["type"], {
    exit_code: job.exitCode,
    duration_ms: job.durationMs,
    stdout_preview: job.stdout.slice(-500),
    stderr_preview: job.stderr.slice(0, 500),
  });
}

// ── Role-memory readiness (P1 item 8) ──────────────────────────────
// The role-memory Redis cache must not silently resolve to zero procedure
// cards. This probe makes cache freshness/count part of the execution-plan
// admission: an empty/stale cache or a missing role index marks the plan
// degraded (visible in the plan + warn-logged) rather than silently
// proceeding. POST /refresh on role-memory-srv is the repair action.

const ROLE_MEMORY_URL = process.env.ROLE_MEMORY_URL || "http://localhost:3500";

interface RoleMemoryReadiness {
  status: "ok" | "degraded" | "unavailable";
  last_updated: string | null;
  procedure_count: number;
  role_index_count: number;
  role_index_present: boolean;
  stale: boolean;
  reason?: string;
}

async function checkRoleMemoryReadiness(role: string): Promise<RoleMemoryReadiness> {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    let resp: Response;
    try {
      resp = await fetch(`${ROLE_MEMORY_URL}/health`, { signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
    if (!resp.ok) {
      return {
        status: "unavailable", last_updated: null, procedure_count: 0,
        role_index_count: 0, role_index_present: false, stale: true,
        reason: `role-memory /health HTTP ${resp.status}`,
      };
    }
    const health = await resp.json() as any;

    let role_index_present = false;
    try {
      const idxResp = await fetch(
        `${ROLE_MEMORY_URL}/procedures/${encodeURIComponent(role)}`
      );
      if (idxResp.ok) {
        const idx = await idxResp.json();
        role_index_present = Array.isArray(idx) && idx.length > 0;
      }
    } catch { /* index probe best-effort */ }

    const stale = Boolean(health.stale);
    const degraded = stale || health.status === "degraded" || !role_index_present;
    return {
      status: degraded ? "degraded" : "ok",
      last_updated: health.lastUpdated ?? health.last_updated ?? null,
      procedure_count: health.procedureCount ?? health.procedure_count ?? 0,
      role_index_count: health.roleIndexCount ?? health.role_index_count ?? 0,
      role_index_present,
      stale,
      reason: degraded
        ? (!role_index_present ? `role index for '${role}' missing/empty` : "role-memory cache stale/degraded")
        : undefined,
    };
  } catch {
    return {
      status: "unavailable", last_updated: null, procedure_count: 0,
      role_index_count: 0, role_index_present: false, stale: true,
      reason: "role-memory unreachable",
    };
  }
}

function jobEnvelope(job: HarnessJob): Record<string, unknown> {
  return {
    job_id: job.jobId,
    state: job.state,
    role: job.role,
    model: job.model,
    model_used: job.modelUsed,
    attempts: job.attempts,
    harness_id: job.harnessId,
    plan: job.plan,
    exit_code: job.exitCode,
    stdout: job.stdout,
    stderr: job.stderr,
    partial: job.state === "accepted" || job.state === "running",
    duration_ms: job.durationMs,
    started_at: new Date(job.startedAt).toISOString(),
    finished_at: job.finishedAt ? new Date(job.finishedAt).toISOString() : null,
    event_count: job.events.length,
  };
}

function startWatchdog(): void {
  setInterval(async () => {
    const now = Date.now();
    for (const [jobId, session] of activeSessions) {
      const elapsed = now - session.startedAt;
      if (elapsed < RUNAWAY_THRESHOLD_MS) continue;

      // Check for durable output since launch
      let hasOutput = false;
      try {
        const nebulaUrl = process.env.NEBULA_URL || "http://localhost:3101";
        const since = new Date(session.startedAt).toISOString();
        const resp = await fetch(
          `${nebulaUrl}/api/agent-records?role=${encodeURIComponent(session.role)}&createdAfter=${since}&limit=1`
        );
        const data = await resp.json() as any;
        hasOutput = (data?.items?.length || 0) > 0;
      } catch {
        // Can't reach nebula — assume worst case, don't kill
        continue;
      }

      if (hasOutput) continue; // agent is producing output, not runaway

      // ── Runaway detected — kill + unload ──────────────────────
      await log("warn", `runaway detected: job=${jobId} role=${session.role} elapsed=${Math.round(elapsed/1000)}s — killing`);

      // Kill the child process directly by PID (set by executeOpencode spawn)
      if (session.pid) {
        try {
          process.kill(session.pid, 'SIGTERM');
          await log("info", `runaway kill: SIGTERM sent to pid=${session.pid} job=${jobId.slice(0, 8)}`);
        } catch {
          // Process already dead — that's fine
        }
      }

      // Unload Ollama model if one was in use
      if (session.model) {
        try {
          const ollamaUrl = process.env.OLLAMA_URL || "http://192.168.1.202:11434";
          await fetch(`${ollamaUrl}/api/generate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: session.model, keep_alive: 0 }),
          });
        } catch {
          // best-effort unload
        }
      }

      // Emit runaway detection record
      const exhaustId = uuidv4();
      const isoNow = new Date().toISOString();
      try {
        await pool.query(
          `INSERT INTO nebula.agent_records_history (id, record_type, role, title, content, tags, created_at, recorded_on_dt)
           VALUES ($1::uuid, 'report', 'architect', $2, $3, $4, $5, $5)`,
          [
            exhaustId,
            `Runaway agent killed: ${session.role} (job ${jobId.slice(0, 8)})`,
            `## Runaway agent detected + killed

- **Job:** ${jobId}
- **Role:** ${session.role}
- **Model:** ${session.model || 'unknown'}
- **Elapsed:** ${Math.round(elapsed / 1000)}s
- **Threshold:** ${RUNAWAY_THRESHOLD_MS / 1000}s

No agent records were produced since launch. The process was killed and the model unloaded.`,
            ['type:runaway-detected', 'to:architect', 'to:engineer', 'to:engineer-ii', 'to:devops', 'to:topologist', `role:${session.role}`],
            isoNow
          ]
        );
      } catch {
        // best-effort record
      }

      // Governance BLOCK receipt — the killed run is an abnormal
      // completion; BLOCK is the always-valid override receipt type
      // (watchdog is whitelisted as a receipt-issuing executor in
      // tackle.vision_bridge._PASS_THROUGH_EXECUTORS).
      if (session.wind_task_id) {
        emitGovernanceReceipt({
          planId: session.wind_task_id,
          type: "BLOCK",
          agentRole: "watchdog",
          sessionId: jobId,
          summary: `runaway watchdog kill: ${session.role} (job ${jobId.slice(0, 8)})`,
          metadata: {
            stage: "watchdog_kill",
            role: session.role,
            model: session.model || "unknown",
            elapsed_ms: elapsed,
          },
        });
      }

      activeSessions.delete(jobId);
    }
  }, WATCHDOG_INTERVAL_MS);
  log("info", `watchdog started (threshold=${RUNAWAY_THRESHOLD_MS}ms, interval=${WATCHDOG_INTERVAL_MS}ms)`);
}

// ── File logging (nexus/logs/harness-srv.log) ─────────────────────
const LOG_DIR = process.env.NEXUS_LOG_DIR || "/home/codex/dev/nexus/logs";
const LOG_FILE = join(LOG_DIR, "harness-srv.log");
mkdir(LOG_DIR, { recursive: true }).catch(() => {});

async function log(level: "info" | "warn" | "error", message: string): Promise<void> {
  const line = `[harness-srv] ${new Date().toISOString()} [${level.toUpperCase()}] ${message}`;
  console.log(line);
  try {
    await appendFile(LOG_FILE, line + "\n", "utf-8");
  } catch {
    // Log dir/file unavailable — journald/stdout still captured
  }
}

// ── Process-level safety net (TWIN DEVIATION: dropped) ─────────
// The incumbent's uncaughtException handler is not ported — the broker and
// moleculer-runner own process lifecycle and signal handling (sibling-twin
// convention; see the Start block comment at the bottom of this file).

// ── POST /run ───────────────────────────────────────────────────────

/**
 * Run an agent job.
 *
 * Body:
 *   wind_task_id: string    — wind.tasks.id to execute
 *   context?: object        — additional context to merge into input_spec
 *   work_dir?: string       — working directory override
 *   harness_id?: string     — harness override (default: harn-opencode)
 *   agent?: string          — agent name override (for opencode)
 *   timeout_ms?: number     — execution timeout (default: 300000 = 5 min)
 *
 * Returns:
 *   { job_id, role, task, prompt_preview, exit_code, stdout, stderr, events }
 */
app.post("/run", async (req, res) => {
  const jobId = uuidv4();
  const startTime = Date.now();

  try {
    const {
      wind_task_id,
      context: contextOverrides,
      work_dir,
      harness_id,
      agent,
      timeout_ms: raw_timeout_ms = 300_000,
    } = req.body;
    // Clamp the user-controlled duration before it reaches any timer
    // (CodeQL js/resource-exhaustion remediation).
    // Clamp the user-controlled duration into [1s, 2h] before it reaches any
    // timer (CodeQL js/resource-exhaustion remediation; lower bound per
    // tester review 269a6ca5 — a near-zero timeout would spin the supervisor).
    const timeoutMs = Math.min(Math.max(Number(raw_timeout_ms) || 300_000, 1_000), MAX_RUN_TIMEOUT_MS);
    const resolveOnly = req.body.resolve_only === true;

    if (!wind_task_id) {
      return res.status(400).json({ error: "wind_task_id is required" });
    }

    // 1. Resolve context
    const resolved = await resolveContext(wind_task_id, contextOverrides);

    // ── Interactive-hosted guard (Freebuff roles, plan 1286 follow-up) ─
    // Roles whose config_bundle resolves to invocation_mode=INTERACTIVE
    // (harness harn-freebuff) run INSIDE Freebuff — they are never
    // launched by harness-srv. Refuse before any spawn happens.
    if (resolved.model?.invocation_mode === "INTERACTIVE") {
      await log(
        "warn",
        `run job=${jobId} role=${resolved.role} — INTERACTIVE-hosted role (Freebuff); refusing harness launch`
      );
      return res.status(400).json({
        job_id: jobId,
        error: `role ${resolved.role} is INTERACTIVE-hosted (Freebuff) — cannot be launched via harness-srv; run it in the Freebuff interactive session instead`,
      });
    }

    // ── Admission gate (T20 two-tier): config validity, uniform across paths ──
    const configAdmission = await checkConfigAdmission(resolved.role);
    if (!configAdmission.valid) {
      await log(
        "warn",
        `run job=${jobId} role=${resolved.role} — admission denied: ${configAdmission.outcome}`
      );
      await emitEvent({
        event_type: "admission.denied",
        source: "harness-srv.run",
        aggregate_type: "harness_job",
        aggregate_id: jobId,
        payload: {
          wind_task_id,
          role: resolved.role,
          outcome: ADMISSION_OUTCOME.ADMISSION_DENIED,
          reason: configAdmission.outcome,
        },
        actor_type: "system",
      });
      // BLOCK is the always-valid terminal receipt and a valid first receipt
      // for a fresh plan_id — durable "admission denied" audit trail.
      await emitGovernanceReceipt({
        planId: wind_task_id,
        type: "BLOCK",
        agentRole: resolved.role,
        sessionId: jobId,
        summary: `harness-srv ${jobId.slice(0, 8)}: admission denied (${configAdmission.outcome})`,
        metadata: {
          stage: "admission_denied",
          wind_task_id,
          outcome: ADMISSION_OUTCOME.ADMISSION_DENIED,
          reason: configAdmission.outcome,
        },
      });
      return res.status(403).json({
        job_id: jobId,
        error: configAdmission.message,
        admission: {
          outcome: ADMISSION_OUTCOME.ADMISSION_DENIED,
          reason: configAdmission.outcome,
        },
      });
    }

    // 2. Merge overrides
    const effectiveHarnessId = harness_id || resolved.harness_id;
    // Resolve + boundary-check the user-supplied override before it can
    // reach spawn cwd/--dir (CodeQL js/path-injection remediation).
    const effectiveWorkDir = work_dir ? String(work_dir) : WORK_DIR;
    if (!workDirAllowed(effectiveWorkDir)) {
      return res.status(400).json({
        job_id: jobId,
        error: `work_dir must resolve under an allowed work root (${WORK_ROOTS.join(":")})`,
      });
    }
    const resolvedWorkDir = resolve(effectiveWorkDir);
    const effectiveAgent = agent || resolved.role;
    const effectiveModel = resolved.model?.opencode_model_id;

    await log(
      "info",
      `run job=${jobId} role=${resolved.role} task=${resolved.task.task_slug} model=${effectiveModel ?? "(harness default)"} wind_task=${wind_task_id}`
    );

    // 3. Emit harness.started event
    const startedEventId = await emitEvent({
      event_type: "harness.started",
      source: "harness-srv.run",
      aggregate_type: "harness_job",
      aggregate_id: jobId,
      payload: {
        wind_task_id,
        role: resolved.role,
        task_slug: resolved.task.task_slug,
        harness_id: effectiveHarnessId,
      },
      actor_type: "system",
    });

    // Governance PLAN_CREATE — bootstrap the receipt lifecycle for the
    // wind_task_id (fresh plan id → PLAN_CREATE is the only valid first
    // receipt). Skipped for resolve_only dry-runs: no work unit runs.
    if (!resolveOnly) {
      await emitGovernanceReceipt({
        planId: wind_task_id,
        type: "PLAN_CREATE",
        agentRole: resolved.role,
        sessionId: jobId,
        summary: `harness-srv ${jobId.slice(0, 8)}: run started (${resolved.task.task_slug})`,
        metadata: {
          stage: "run_start",
          wind_task_id,
          task_slug: resolved.task.task_slug,
          harness_id: effectiveHarnessId,
        },
      });
    }

    // 4. Append outcome instruction to prompt and write to temp file
    const outcomeInstruction = buildOutcomeInstruction(resolved.outcomes || []);
    const fullPrompt = resolved.prompt + "\n\n" + outcomeInstruction;
    await mkdir(PROMPT_DIR, { recursive: true });
    const promptFile = join(PROMPT_DIR, `${jobId}.md`);
    await writeFile(promptFile, fullPrompt, "utf-8");

    // 5. Execute via harness (or resolve-only mode)
    let exitCode = 0;
    let stdout = "";
    let stderr = "";
    let modelUsed: string | null = null;

    if (resolveOnly) {
      // Skip execution — return resolved context only
      stdout = JSON.stringify({
        role: resolved.role,
        prompt_length: resolved.prompt.length,
        task: resolved.task,
        procedure_cards: resolved.prompt.match(/^- \*\*/gm)?.length || 0,
      });
    } else {
      // ── Register session for runaway watchdog ──────────────────
      activeSessions.set(jobId, {
        jobId,
        role: resolved.role,
        model: effectiveModel,
        startedAt: startTime,
        promptFile,
        wind_task_id,
      });

      // ── Failover ladder (mirrors /run-direct) — primary first, then
      // fallbacks in priority order. Each fallback carries its own opencode
      // wire id + harness so a failed/rate-limited primary rolls to the next
      // provider/binary instead of failing the whole workflow node.
      const attempts: Array<{
        model: string | undefined;
        model_identifier: string | undefined;
        harness_id: string;
        source: "primary" | "fallback";
      }> = [
        {
          model: effectiveModel,
          model_identifier: resolved.model?.model_identifier,
          harness_id: effectiveHarnessId,
          source: "primary",
        },
        ...(resolved.model?.fallback_models ?? []).map((f) => ({
          model: f.opencode_model_id,
          model_identifier: f.model_identifier,
          harness_id: f.harness_id,
          source: "fallback" as const,
        })),
      ];

      try {
        for (let i = 0; i < attempts.length; i++) {
          const attempt = attempts[i];
          let attemptExit = 1;
          let attemptStdout = "";
          let attemptStderr = "";
          try {
            const result = await executeHarness({
              harness_id: attempt.harness_id,
              prompt_file: promptFile,
              work_dir: resolvedWorkDir,
              agent: effectiveAgent,
              role: resolved.role,
              model: attempt.model,
              model_identifier: attempt.model_identifier,
              timeout_ms: timeoutMs,
            });
            attemptExit = result.exitCode;
            attemptStdout = result.stdout;
            attemptStderr = result.stderr;
          } catch (execError: any) {
            attemptExit = execError.exitCode || 1;
            attemptStdout = execError.stdout || "";
            attemptStderr = execError.stderr || execError.message;
          }

          stdout = attemptStdout;
          // Accumulate stderr across attempts so a failed primary's real
          // cause survives into the terminal result even when a later
          // fallback also fails.
          stderr = stderr
            ? `${stderr}\n--- ${attempt.source} (${attempt.model ?? "unset"}) exit=${attemptExit} ---\n${attemptStderr}`
            : attemptStderr;
          exitCode = attemptExit;

          if (attemptExit === 0) {
            modelUsed = attempt.model ?? null;
            break;
          }

          const next = attempts[i + 1] ?? null;
          await log(
            "warn",
            `run job=${jobId} role=${resolved.role} ${attempt.source} model=${attempt.model ?? "(unset)"} exit=${attemptExit} — failing over to ${next ? `${next.source} ${next.model ?? "unset"}` : "none"}`
          );
        }
      } finally {
        activeSessions.delete(jobId);
      }
    }

    // Clean up prompt file
    await unlink(promptFile).catch(() => {});

    // 6. Emit harness.completed event
    await emitEvent({
      event_type: exitCode === 0 ? "harness.completed" : "harness.failed",
      source: "harness-srv.run",
      aggregate_type: "harness_job",
      aggregate_id: jobId,
      payload: {
        wind_task_id,
        role: resolved.role,
        task_slug: resolved.task.task_slug,
        model_used: modelUsed,
        exit_code: exitCode,
        duration_ms: Date.now() - startTime,
        stdout_preview: stdout.slice(0, 500),
        stderr_preview: stderr.slice(0, 500),
      },
      actor_type: "system",
      causation_id: startedEventId,
      caused_by_event_type: "harness.started",
    });

    await log(
      exitCode === 0 ? "info" : "warn",
      `run job=${jobId} role=${resolved.role} exit=${exitCode} duration_ms=${Date.now() - startTime} model=${modelUsed ?? effectiveModel ?? "(harness default)"}`
    );

    // 6b. Governance completion receipts — IMPLEMENTATION then
    // REVIEW_PASS (exit 0) / REVIEW_REJECT (exit != 0), walked in the
    // order validateReceipt requires. Best-effort + sequential so the
    // chain lands in peb.governance_events like every other channel.
    if (!resolveOnly) {
      const durationMs = Date.now() - startTime;
      const completedMetadata = {
        stage: "run_complete",
        wind_task_id,
        task_slug: resolved.task.task_slug,
        harness_id: effectiveHarnessId,
        model_used: modelUsed,
        exit_code: exitCode,
        duration_ms: durationMs,
        stdout_preview: stdout.slice(0, 300),
        stderr_preview: stderr.slice(0, 300),
      };
      await emitGovernanceReceipt({
        planId: wind_task_id,
        type: "IMPLEMENTATION",
        agentRole: resolved.role,
        sessionId: jobId,
        summary: `harness-srv ${jobId.slice(0, 8)}: implementation ${exitCode === 0 ? "ok" : "failed"} (${resolved.task.task_slug})`,
        metadata: completedMetadata,
      });
      await emitGovernanceReceipt({
        planId: wind_task_id,
        type: exitCode === 0 ? "REVIEW_PASS" : "REVIEW_REJECT",
        agentRole: resolved.role,
        sessionId: jobId,
        summary: `harness-srv ${jobId.slice(0, 8)}: ${exitCode === 0 ? "completed" : "failed"} exit=${exitCode} (${resolved.task.task_slug})`,
        metadata: completedMetadata,
      });
    }

    // 7. Parse outcome from agent output
    const parsedOutcome = parseOutcome(stdout, resolved.outcomes || []);

    // 8. Return result
    res.json({
      job_id: jobId,
      role: resolved.role,
      task: {
        wind_task_id: resolved.task.wind_task_id,
        wind_task_name: resolved.task.wind_task_name,
        task_slug: resolved.task.task_slug,
        scope: resolved.task.scope,
      },
      outcomes: (resolved.outcomes || []).map((o) => ({ code: o.code, description: o.description })),
      outcome: parsedOutcome, // { code, id, confidence } or null
      prompt_preview: resolved.prompt.slice(0, 300) + "...",
      harness_id: effectiveHarnessId,
      model_used: modelUsed,
      exit_code: exitCode,
      stdout,
      stderr,
      duration_ms: Date.now() - startTime,
      events: { started: startedEventId },
    });
  } catch (error: any) {
    // Emit harness.error event
    await emitEvent({
      event_type: "harness.error",
      source: "harness-srv.run",
      aggregate_type: "harness_job",
      aggregate_id: jobId,
      payload: { error: error.message },
      actor_type: "system",
    }).catch(() => {});

    res.status(500).json({
      job_id: jobId,
      error: error.message,
      duration_ms: Date.now() - startTime,
    });
  }
});

// ── POST /resolve-context ───────────────────────────────────────────

/**
 * Resolve context without executing. Useful for debugging.
 *
 * Body:
 *   wind_task_id: string
 */
app.post("/resolve-context", async (req, res) => {
  try {
    const { wind_task_id } = req.body;
    if (!wind_task_id) {
      return res.status(400).json({ error: "wind_task_id is required" });
    }

    const resolved = await resolveContext(wind_task_id);

    res.json({
      role: resolved.role,
      task: resolved.task,
      prompt_length: resolved.prompt.length,
      prompt_preview: resolved.prompt.slice(0, 500) + "...",
      procedure_cards: resolved.prompt.includes("(no procedure cards")
        ? 0
        : (resolved.prompt.match(/^- \*\*/gm) || []).length,
      harness_id: resolved.harness_id,
      tool_acl: resolved.task,
    });
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// ── POST /run-direct ────────────────────────────────────────────────

/**
 * Run an agent with raw context — no wind task resolution required.
 *
 * For interactive conversation turns (Duality/Plurality) where the
 * subscriber already assembled the full prompt from Assembly thread
 * context. Bypasses wind.task lookup entirely.
 *
 * Body:
 *   role: string           — agent role (maps to tackle config_bundle)
 *   prompt: string         — full prompt text (already assembled)
 *   model?: string         — model override (from tackle config_bundle)
 *   work_dir?: string      — working directory override
 *   agent?: string         — agent name override (for opencode)
 *   timeout_ms?: number    — execution timeout (default: 300000 = 5 min)
 *   channel?: string       — invocation channel (default: "duality")
 *   async?: boolean        — async contract: return 202 {job_id, state}
 *                            immediately and run in the background
 *                            (P1 item 6). Observable via GET /jobs/:jobId
 *                            (status + partial output), GET /jobs/:jobId/events
 *                            (replayable SSE), POST /jobs/:jobId/interrupt.
 *
 * Sync returns:
 *   { job_id, role, exit_code, stdout, stderr, duration_ms, prompt_preview }
 * Async returns 202:
 *   { job_id, state: "accepted", role, model, harness_id, events }
 */
app.post("/run-direct", async (req, res) => {
  const jobId = uuidv4();
  const startTime = Date.now();

  try {
    const {
      role,
      prompt,
      model: modelOverride,
      work_dir,
      agent,
      timeout_ms: raw_timeout_ms = 600_000,
      channel = "duality",
      async: asyncMode = false,
    } = req.body;
    // Clamp the user-controlled duration before it reaches any timer
    // (CodeQL js/resource-exhaustion remediation).
    // Clamp the user-controlled duration into [1s, 2h] before it reaches any
    // timer (CodeQL js/resource-exhaustion remediation; lower bound per
    // tester review 269a6ca5 — a near-zero timeout would spin the supervisor).
    const timeoutMs = Math.min(Math.max(Number(raw_timeout_ms) || 600_000, 1_000), MAX_RUN_TIMEOUT_MS);

    if (!role) {
      return res.status(400).json({ error: "role is required" });
    }
    if (!prompt) {
      return res.status(400).json({ error: "prompt is required" });
    }

    // ── Admission gate (T20 two-tier): config validity, uniform across paths ──
    const configAdmission = await checkConfigAdmission(role);
    if (!configAdmission.valid) {
      await log(
        "warn",
        `run-direct job=${jobId} role=${role} — admission denied: ${configAdmission.outcome}`
      );
      await emitEvent({
        event_type: "admission.denied",
        source: `harness-srv.run-direct.${channel}`,
        aggregate_type: "harness_job",
        aggregate_id: jobId,
        payload: {
          role,
          channel,
          outcome: ADMISSION_OUTCOME.ADMISSION_DENIED,
          reason: configAdmission.outcome,
        },
        actor_type: "system",
      });
      return res.status(403).json({
        job_id: jobId,
        error: configAdmission.message,
        admission: {
          outcome: ADMISSION_OUTCOME.ADMISSION_DENIED,
          reason: configAdmission.outcome,
        },
      });
    }

    // ── Resolve the role's model/harness config from tackle ──────
    // Reuse resolveRoleModel — the same resolver as the /run workflow path —
    // so both paths agree on the SAME primary bundle, model wire id, and
    // harness. (Previously this ran its own SQL with `ORDER BY priority DESC`,
    // which inverted the primary-bundle selection vs resolveRoleModel's
    // ascending priority sort — picking the fallback model instead of the
    // primary. One role reference must resolve deterministically, T20.)
    const modelConfig = await resolveRoleModel(role);

    if (!modelConfig) {
      await log("warn", `run-direct job=${jobId} role=${role} — no active config_bundle found`);
      return res.status(400).json({
        job_id: jobId,
        error: `No active config_bundle found for role ${role}`,
      });
    }

    const harnessId = modelConfig.harness_id;
    // ── P1 item 7: Tackle is the single model/execution-plan authority ──
    // An explicit override must be provider-qualified (<provider>/<model>) —
    // a bare or stale value (e.g. a lease `model` column) must NOT bypass the
    // canonical Tackle-resolved wire id. Otherwise the canonical
    // opencode_model_id from resolveRoleModel is used.
    if (modelOverride && !String(modelOverride).includes("/")) {
      await log(
        "warn",
        `run-direct job=${jobId} role=${role} — rejecting unqualified model override '${modelOverride}'`
      );
      return res.status(400).json({
        job_id: jobId,
        error: `unqualified model override '${modelOverride}' — must be provider-qualified (<provider>/<model>)`,
      });
    }
    const effectiveModel = modelOverride || modelConfig.opencode_model_id;
    const invocationMode = modelConfig.invocation_mode;

    // ── P1 item 8 — role-memory readiness is part of admission ─────
    // Probe the role-memory cache; a stale/empty cache or a missing role
    // index marks the plan degraded (visible + warn-logged) instead of
    // silently resolving to zero procedure cards.
    const roleMemory = await checkRoleMemoryReadiness(role);
    if (roleMemory.status !== "ok") {
      await log(
        "warn",
        `run-direct job=${jobId} role=${role} — role-memory ${roleMemory.status}: ${roleMemory.reason ?? ""}`
      );
    }

    // Versioned execution plan — a stable fingerprint of the resolved plan
    // (provider-qualified model, harness, invocation mode, fallback ladder,
    // prompt, and role-memory readiness). Consumers record plan_version so a
    // stale/unqualified override or a plan change is detectable.
    const planVersion = createHash("sha256")
      .update(JSON.stringify({
        model: effectiveModel,
        harness_id: harnessId,
        invocation_mode: invocationMode,
        fallbacks: modelConfig.fallback_models.map((f: any) => ({
          priority: f.priority,
          model: f.model_identifier,
          provider: f.provider_id,
        })),
        prompt_sha: createHash("sha256").update(prompt).digest("hex"),
        role_memory: {
          status: roleMemory.status,
          last_updated: roleMemory.last_updated,
          role_index_present: roleMemory.role_index_present,
        },
      }))
      .digest("hex")
      .slice(0, 16);
    const plan = {
      plan_version: planVersion,
      model: effectiveModel,
      harness_id: harnessId,
      invocation_mode: invocationMode,
      fallback_policy: modelConfig.fallback_models.map((f: any) => ({
        priority: f.priority,
        model: f.model_identifier,
        provider_type: f.provider_type,
      })),
      role_memory: {
        status: roleMemory.status,
        last_updated: roleMemory.last_updated,
        procedure_count: roleMemory.procedure_count,
        role_index_count: roleMemory.role_index_count,
        role_index_present: roleMemory.role_index_present,
        stale: roleMemory.stale,
        reason: roleMemory.reason ?? null,
      },
      resolved_by: "tackle",
    };

    // ── Interactive-hosted guard ─────────────────────────────────
    // invocation_mode is a column on tackle.config_bundle (not on
    // harnesses.invocation_semantics). It's set when a role is
    // configured to run inside Freebuff rather than via harness-srv.
    if (invocationMode === "INTERACTIVE") {
      await log("warn", `run-direct job=${jobId} role=${role} — INTERACTIVE-hosted; refusing harness launch`);
      return res.status(400).json({
        job_id: jobId,
        error: `role ${role} is INTERACTIVE-hosted (Freebuff) — use freebuff backend instead`,
      });
    }

    // Resolve + boundary-check the user-supplied override before it can
    // reach spawn cwd/--dir (CodeQL js/path-injection remediation).
    const effectiveWorkDir = work_dir ? String(work_dir) : WORK_DIR;
    if (!workDirAllowed(effectiveWorkDir)) {
      return res.status(400).json({
        job_id: jobId,
        error: `work_dir must resolve under an allowed work root (${WORK_ROOTS.join(":")})`,
      });
    }
    const resolvedWorkDir = resolve(effectiveWorkDir);
    const effectiveAgent = agent || role;

    await log("info", `run-direct job=${jobId} role=${role} model=${effectiveModel ?? "(harness default)"} channel=${channel}`);

    // Write prompt to temp file (shared by sync + async paths)
    await mkdir(PROMPT_DIR, { recursive: true });
    const promptFile = join(PROMPT_DIR, `${jobId}.md`);
    await writeFile(promptFile, prompt, "utf-8");

    // Register for watchdog + async job registry (status/events/interrupt)
    activeSessions.set(jobId, {
      jobId,
      role,
      model: effectiveModel,
      startedAt: startTime,
      promptFile,
    });
    registerJob({
      jobId,
      role,
      model: effectiveModel ?? null,
      harnessId,
      promptFile,
      plan,
    });

    // 1. Emit harness.started event
    const startedEventId = await emitEvent({
      event_type: "harness.started",
      source: `harness-srv.run-direct.${channel}`,
      aggregate_type: "harness_job",
      aggregate_id: jobId,
      payload: { role, channel, prompt_length: prompt.length },
      actor_type: "system",
    });

    const execParams: HarnessExecParams = {
      harness_id: harnessId,
      prompt_file: promptFile,
      work_dir: resolvedWorkDir,
      agent: effectiveAgent,
      role,
      model: effectiveModel,
      model_identifier: modelConfig.model_identifier,
      timeout_ms: timeoutMs,
      onEvent: (type, payload) => {
        // P1 item 6 — translate the opencode JSON stream ONCE here into the
        // same typed envelopes the duality SSE stream carries: reasoning →
        // thinking, text → text.delta. Consumers of /jobs/:id/events never
        // parse the opencode wire format.
        pushJobEvent(
          jobId,
          type === "reasoning" ? "thinking" : "text.delta",
          { text: payload.text }
        );
      },
    };

    // ── Failover ladder — primary first, then fallbacks in priority order.
    // resolveRoleModel sorts bundles ascending; each fallback now carries its
    // own opencode_model_id + harness so a failed/rate-limited primary rolls
    // to the next provider or binary instead of leaving the role dead.
    const attempts: Array<{
      model: string;
      model_identifier: string;
      harness_id: string;
      source: "primary" | "fallback";
    }> = [
      {
        model: effectiveModel,
        model_identifier: modelConfig.model_identifier,
        harness_id: harnessId,
        source: "primary",
      },
      ...modelConfig.fallback_models.map((f) => ({
        model: f.opencode_model_id,
        model_identifier: f.model_identifier,
        harness_id: f.harness_id,
        source: "fallback" as const,
      })),
    ];

    const runJob = async (): Promise<void> => {
      setJobState(jobId, "running");
      const interruptedBeforeStart = jobs.get(jobId)?.interrupted;
      if (interruptedBeforeStart) return; // cancelled by interrupt before spawn
      let exitCode = 0;
      let stdout = "";
      let stderr = "";
      let modelUsed: string | null = null;
      const attemptLog: AttemptRecord[] = [];

      try {
        for (const attempt of attempts) {
          // Honour an interrupt that landed between attempts.
          if (jobs.get(jobId)?.interrupted) break;

          execParams.harness_id = attempt.harness_id;
          execParams.model = attempt.model;
          execParams.model_identifier = attempt.model_identifier;

          let attemptExit = 1;
          let attemptStdout = "";
          let attemptStderr = "";
          try {
            const result = await executeHarness(execParams);
            attemptExit = result.exitCode;
            attemptStdout = result.stdout;
            attemptStderr = result.stderr;
          } catch (execError: any) {
            attemptExit = execError.exitCode || 1;
            attemptStdout = execError.stdout || "";
            attemptStderr = execError.stderr || execError.message;
          }

          attemptLog.push({
            model: attempt.model,
            source: attempt.source,
            exit_code: attemptExit,
          });
          stdout = attemptStdout;
          // Accumulate stderr across attempts so a failed primary's real
          // cause (e.g. a 429 envelope) survives into the terminal envelope
          // even when a later fallback also fails.
          stderr = stderr
            ? `${stderr}\n--- ${attempt.source} (${attempt.model}) exit=${attemptExit} ---\n${attemptStderr}`
            : attemptStderr;
          exitCode = attemptExit;

          if (attemptExit === 0) {
            modelUsed = attempt.model;
            break;
          }

          // Don't fail over if the user interrupted mid-attempt.
          if (jobs.get(jobId)?.interrupted) break;

          const next = attempts[attemptLog.length] ?? null;
          await log(
            "warn",
            `run-direct job=${jobId} role=${role} ${attempt.source} model=${attempt.model} exit=${attemptExit} — failing over to ${next ? `${next.source} ${next.model}` : "none"}`
          );
          pushJobEvent(jobId, "job.fallback", {
            from: attempt.model,
            source: attempt.source,
            exit_code: attemptExit,
            next: next ? { model: next.model, source: next.source } : null,
          });
        }
      } finally {
        activeSessions.delete(jobId);
      }

      // Preserve partial output + exact exit/timeout metadata on the job.
      // Exit metadata is only written while the job is still pre-terminal:
      // a lost interrupt race (cancelled while the executor was in flight)
      // must keep its cancellation exit code (137), not the executor's.
      const job = jobs.get(jobId);
      if (job) {
        job.stdout = stdout;
        job.stderr = stderr;
        job.modelUsed = modelUsed;
        job.attempts = attemptLog;
        if (job.state === "accepted" || job.state === "running") {
          job.exitCode = exitCode;
        }
      }

      await unlink(promptFile).catch(() => {});

      // Terminal state — an interrupted job is cancelled, a timeout
      // (exit 124 / timeout marker) is reported honestly as timed_out,
      // signal-kills otherwise fail with the marker.
      if (jobs.get(jobId)?.interrupted) {
        setJobState(jobId, "cancelled", {
          exitCode: 137,
          stderr: "interrupted by user request",
        });
        return;
      }
      let state: HarnessJobState = exitCode === 0 ? "completed" : "failed";
      if (exitCode === 124 || /timeout after|no first token/i.test(stderr)) state = "timed_out";
      setJobState(jobId, state, { exitCode, stderr });

      // 5. Emit harness.completed/failed event
      await emitEvent({
        event_type: exitCode === 0 ? "harness.completed" : "harness.failed",
        source: `harness-srv.run-direct.${channel}`,
        aggregate_type: "harness_job",
        aggregate_id: jobId,
        payload: {
          role,
          channel,
          exit_code: exitCode,
          duration_ms: Date.now() - startTime,
          stdout_preview: stdout.slice(0, 500),
          stderr_preview: stderr.slice(0, 500),
        },
        actor_type: "system",
        causation_id: startedEventId,
        caused_by_event_type: "harness.started",
      });

      await log(
        exitCode === 0 ? "info" : "warn",
        `run-direct job=${jobId} role=${role} exit=${exitCode} duration_ms=${Date.now() - startTime}`
      );
    };

    // ── Async contract (P1 item 6): 202 { job_id, state } immediately ──
    if (asyncMode) {
      void runJob().catch(async (err: any) => {
        await log("error", `run-direct async job=${jobId} failed: ${err.message}`);
        setJobState(jobId, "failed", {
          exitCode: 1,
          stderr: err.message,
        });
      });
      return res.status(202).json({
        job_id: jobId,
        state: "accepted",
        role,
        model: effectiveModel || null,
        harness_id: harnessId,
        plan,
        events: { started: startedEventId },
      });
    }

    // ── Sync contract (default, backward compatible) ──
    await runJob();
    const job = jobs.get(jobId);
    res.json({
      job_id: jobId,
      role,
      exit_code: job?.exitCode ?? 1,
      stdout: job?.stdout ?? "",
      stderr: job?.stderr ?? "",
      duration_ms: job?.durationMs ?? Date.now() - startTime,
      prompt_preview: prompt.slice(0, 300) + (prompt.length > 300 ? "..." : ""),
      harness_id: harnessId,
      model: effectiveModel || null,
      model_used: job?.modelUsed ?? null,
      attempts: job?.attempts ?? [],
      plan,
      events: { started: startedEventId },
    });
  } catch (error: any) {
    await emitEvent({
      event_type: "harness.error",
      source: "harness-srv.run-direct",
      aggregate_type: "harness_job",
      aggregate_id: jobId,
      payload: { error: error.message },
      actor_type: "system",
    }).catch(() => {});

    res.status(500).json({
      job_id: jobId,
      error: error.message,
      duration_ms: Date.now() - startTime,
    });
  }
});

// ── GET /jobs/:jobId — job status + partial output (P1 item 6) ─────
// The async contract's read path: state envelope with the RAW accumulated
// stdout (partial while running — preserved on timeout/failure), stderr,
// and exact exit/timeout metadata. 404 for unknown jobs.
app.get("/jobs/:jobId", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    return res.status(404).json({ error: "job not found", job_id: req.params.jobId });
  }
  res.json({ job: jobEnvelope(job) });
});

// ── GET /jobs/:jobId/events?after=<seq> — replayable SSE job stream ──
// Typed envelopes translated once at the boundary from the opencode JSON
// event stream: job.accepted, job.started, text.delta, thinking, and the
// terminal job.completed / job.failed / job.timed_out / job.cancelled.
// Replays seq > after on connect, then follows live (same cursor semantics
// as the duality session stream). 15s heartbeats.
app.get("/jobs/:jobId/events", (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    return res.status(404).json({ error: "job not found", job_id: req.params.jobId });
  }

  let after = 0;
  if (req.query.after !== undefined && req.query.after !== "") {
    after = Number(req.query.after);
    if (!Number.isInteger(after) || after < 0) {
      return res.status(400).json({ error: "after must be a non-negative integer sequence" });
    }
  }

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();
  req.socket.setTimeout(0);
  res.setTimeout(0);

  let closed = false;
  const send = (event: string, data: Record<string, unknown>) => {
    if (!closed) {
      try { res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`); } catch { /* noop */ }
    }
  };

  // Replay backlog (seq > after), then mark the client live.
  let replayed = 0;
  let lastSeq = after;
  for (const ev of job.events) {
    if (ev.seq <= after) continue;
    send(ev.type, {
      seq: ev.seq,
      type: ev.type,
      jobId: job.jobId,
      payload: ev.payload,
      ts: new Date(ev.ts).toISOString(),
    });
    lastSeq = Math.max(lastSeq, ev.seq);
    replayed++;
  }
  send("connected", {
    jobId: job.jobId,
    state: job.state,
    after: after || null,
    seq: job.events.length,
    replayed,
  });
  job.sseClients.add(res);

  const heartbeat = setInterval(() => {
    send("heartbeat", { jobId: job.jobId, seq: lastSeq });
  }, 15_000);

  const cleanup = () => {
    if (closed) return;
    closed = true;
    clearInterval(heartbeat);
    job.sseClients.delete(res);
  };
  req.on("close", cleanup);
  res.on("error", cleanup);
});

// ── POST /jobs/:jobId/interrupt — SIGTERM the child, cancel the job ──
// The async contract's cancellation path: kills the opencode child by PID
// (same mechanism as the runaway watchdog) and marks the job cancelled so
// the subscriber can surface the interrupt instead of a timeout.
app.post("/jobs/:jobId/interrupt", async (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) {
    return res.status(404).json({ error: "job not found", job_id: req.params.jobId });
  }
  if (job.state !== "accepted" && job.state !== "running") {
    return res.json({ job: jobEnvelope(job), note: "job already terminal" });
  }
  job.interrupted = true;
  if (job.pid) {
    try {
      process.kill(job.pid, "SIGTERM");
      await log("info", `interrupt: SIGTERM sent to pid=${job.pid} job=${job.jobId.slice(0, 8)}`);
    } catch {
      // Process already dead — the close handler will mark the job.
    }
  } else {
    // No child pid (e.g. still resolving) — mark cancelled directly; the
    // runner's interrupted guard prevents a later running/terminal override.
    setJobState(job.jobId, "cancelled", {
      exitCode: 137,
      stderr: "interrupted by user request",
    });
  }
  res.json({ job: jobEnvelope(job) });
});

// ── GET /health ─────────────────────────────────────────────────────

app.get("/health", async (_req, res) => {
  try {
    await pool.query("SELECT 1");
    await redis.ping();
    res.json({ status: "ok", port: PORT, uptime: process.uptime() });
  } catch (error: any) {
    res.status(503).json({ status: "error", error: error.message });
  }
});

// ── GET /sessions — active session list (runaway watchdog visibility)
app.get("/sessions", (_req, res) => {
  const sessions = Array.from(activeSessions.values()).map((s) => ({
    jobId: s.jobId,
    role: s.role,
    model: s.model || null,
    startedAt: new Date(s.startedAt).toISOString(),
    elapsedSeconds: Math.round((Date.now() - s.startedAt) / 1000),
  }));
  res.json({ sessions, count: sessions.length });
});

// ── Harness execution ───────────────────────────────────────────────

interface HarnessExecParams {
  harness_id: string;
  prompt_file: string;
  work_dir: string;
  agent: string;
  role: string;
  model?: string; // opencode --model value (from tackle config_bundle)
  model_identifier?: string; // bare model_identifier (for binary=ollama)
  timeout_ms: number;
  /** P1 item 6 — translate the executor's stream once, at the boundary,
   *  into typed envelopes ("text" → text.delta, "reasoning" → thinking).
   *  Called for every parsed stream event as it arrives. */
  onEvent?: (type: "text" | "reasoning", payload: { text: string }) => void;
}

interface HarnessExecResult {
  exitCode: number;
  stdout: string;
  stderr: string;
}

async function executeHarness(params: HarnessExecParams): Promise<HarnessExecResult> {
  const { harness_id, prompt_file, work_dir, agent, role, timeout_ms } = params;
  // Containment assert — prompt files are service-generated under PROMPT_DIR
  // and work_dir is boundary-checked at the handlers; this guards the
  // executor invariant regardless of caller (CodeQL js/path-injection
  // remediation, alert #590).
  if (!resolve(prompt_file).startsWith(resolve(PROMPT_DIR) + "/")) {
    throw new Error("prompt_file escaped PROMPT_DIR");
  }
  if (!workDirAllowed(work_dir)) {
    throw new Error("work_dir escaped allowed work roots");
  }

  // Read the prompt content from file
  const promptContent = await readFile(prompt_file, "utf-8");

  // Get harness config from DB
  const result = await pool.query(
    `SELECT invocation_semantics FROM tackle.harnesses WHERE id = $1`,
    [harness_id]
  );

  if (result.rows.length === 0) {
    throw new Error(`Harness ${harness_id} not found in tackle.harnesses`);
  }

  const config =
    typeof result.rows[0].invocation_semantics === "string"
      ? JSON.parse(result.rows[0].invocation_semantics)
      : result.rows[0].invocation_semantics;

  const binary = config.binary;

  // Route to the right executor. The resolved model (from tackle
  // config_bundle) is honored on both paths — opencode via --model, and
  // ollama via the model_identifier passed to the generate API.
  if (binary === "ollama") {
    // Ollama's /api/generate expects the bare model_identifier (e.g.
    // `qwen2.5-coder`), not the opencode-qualified wire id
    // (`ollama/qwen2.5-coder`) — feeding the latter 404s.
    return executeOllama(
      promptContent,
      role,
      params.model_identifier ?? params.model,
      timeout_ms
    );
  } else if (binary === "opencode") {
    return executeOpencode(params, promptContent);
  } else {
    throw new Error(`Harness ${harness_id} binary '${binary}' not yet supported`);
  }
}

/**
 * Execute via ollama HTTP API directly (no opencode, no tool access).
 * This is the fast path for CPU-only environments.
 */
async function executeOllama(
  prompt: string,
  role: string,
  model: string | undefined,
  timeout_ms: number
): Promise<HarnessExecResult> {
  const ollamaUrl = process.env.OLLAMA_URL || "http://192.168.1.202:11434";
  const effectiveModel = model || process.env.OLLAMA_MODEL || "qwen2.5:0.5b";
  // Defense-in-depth clamp inside the executor too (CodeQL
  // js/resource-exhaustion remediation): even if a future call site
  // forgets the handler-level clamp, the timer duration stays bounded.
  // Defense-in-depth clamp inside the executor too (CodeQL
  // js/resource-exhaustion remediation): even if a future call site
  // forgets the handler-level clamp, the timer duration stays bounded in
  // [1s, 2h] (lower bound per tester review 269a6ca5).
  timeout_ms = Math.min(Math.max(Number(timeout_ms) || 300_000, 1_000), MAX_RUN_TIMEOUT_MS);

  await log("info", `ollama exec role=${role} model=${effectiveModel}`);

  try {
    const controller = new AbortController();
    // Fixed 1s supervisor tick instead of setTimeout(timeout_ms): the
    // user-influenced duration is compared against a deadline rather than
    // handed to the timer API, so no timer with a user-controlled duration
    // is ever armed (CodeQL js/resource-exhaustion remediation, alerts
    // #649/#650 family — same deadline pattern as the child-process
    // supervisor and the runaway watchdog).
    const deadline = Date.now() + timeout_ms;
    const timer = setInterval(() => {
      if (Date.now() < deadline) return;
      clearInterval(timer);
      controller.abort();
    }, 1000);

    // try/finally guarantees the timeout timer is released even when the
    // fetch rejects — an orphaned timer keeps the event loop armed
    // while a large response body streams (CodeQL js/resource-exhaustion
    // remediation, alert #472 family). Aborts still surface as AbortError,
    // so the timeout envelope below is unchanged.
    let resp: Response;
    try {
      resp = await fetch(`${ollamaUrl}/api/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: effectiveModel,
          prompt,
          stream: false,
          options: {
            num_predict: 1024,
            temperature: 0.3,
          },
        }),
        signal: controller.signal,
      });
    } finally {
      clearInterval(timer);
    }

    if (!resp.ok) {
      const body = await resp.text();
      return { exitCode: 1, stdout: "", stderr: `Ollama error (${resp.status}): ${body}` };
    }

    const data = await resp.json() as any;
    // ── consumed_units tracking (RoleLeases plan 1286) ──────────
    incrementConsumedUnits(role).catch(() => {});
    return {
      exitCode: 0,
      stdout: data.response || "",
      stderr: "",
    };
  } catch (error: any) {
    if (error.name === "AbortError") {
      return { exitCode: 124, stdout: "", stderr: `Ollama timeout after ${timeout_ms}ms` };
    }
    return { exitCode: 1, stdout: "", stderr: error.message };
  }
}

/**
 * Execute via opencode CLI (full tool access, requires GPU for reasonable speed).
 * When a model was resolved from tackle config_bundle it is passed via --model,
 * so external-provider changes made in tackle-ui take effect on harness runs.
 *
 * Uses child_process.spawn (not execFile) so the T16 runaway watchdog can
 * directly SIGTERM the child process by PID instead of pattern-matching pkill.
 */
async function executeOpencode(
  params: HarnessExecParams,
  promptContent: string
): Promise<HarnessExecResult> {
  const { prompt_file, work_dir, agent, role, model, timeout_ms, onEvent } = params;
  const opencodeBin = process.env.OPENCODE_BIN || "opencode";
  // Extract jobId from prompt filename (${jobId}.md) for watchdog PID tracking
  const jobId = prompt_file.split("/").pop()?.replace(".md", "") || "";

  // NOTE (opencode 1.18.x): `opencode run [message..]` requires the prompt as
  // a message. We pipe the prompt via stdin instead of passing it as a
  // positional arg: opencode's resolveRunInput() uses piped stdin when the
  // positional message is empty, and piping avoids argv-length limits (E2BIG)
  // for large Assembly-thread prompts.
  //
  // `--file` is deliberately NOT passed: (a) the prompt file only contains the
  // same text we already deliver as the message, so attaching it adds nothing;
  // and (b) empirically, passing `--file` breaks model resolution for custom
  // config providers (`Model not found: <provider>/<model>`) — with --file,
  // opencode 1.18.16 resolves the model against a provider registry that has
  // not yet loaded config providers. The prompt file stays on disk for jobId/
  // watchdog bookkeeping (prompt_file.split("/").pop() below).
  const cmdArgs = [
    "run",
    "--agent", agent,
    ...(model ? ["--model", model] : []),
    "--dir", work_dir,
    "--format", "json",
    "--dangerously-skip-permissions",
  ];

  await log("info", `opencode exec agent=${agent} model=${model ?? "(unset)"} file=${prompt_file} dir=${work_dir} bin=${opencodeBin} prompt_chars=${promptContent.length}`);

  return new Promise((resolve) => {
    const child = spawn(opencodeBin, cmdArgs, {
      cwd: work_dir,
      env: {
        ...process.env,
        HARNESS_ROLE: role,
        HARNESS_JOB_ID: prompt_file.split("/").pop()?.replace(".md", "") || "",
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    // Pipe the prompt as the run message (opencode reads non-TTY stdin as the
    // message via resolveRunInput). Ignore EPIPE — the child may exit early.
    if (child.stdin) {
      child.stdin.on("error", () => { /* child may have exited early */ });
      child.stdin.write(promptContent);
      child.stdin.end();
    }

    // Register PID for runaway watchdog (direct SIGTERM instead of pkill -f)
    // and for the async job contract's POST /jobs/:id/interrupt.
    if (jobId) {
      const session = activeSessions.get(jobId);
      if (session) {
        session.pid = child.pid;
        log("info", `opencode pid=${child.pid} registered for watchdog (job=${jobId.slice(0, 8)})`);
      }
      const job = jobs.get(jobId);
      if (job) {
        job.pid = child.pid ?? null;
      }
    }

    let stdout = '';
    let stderr = '';
    let timedOut = false;
    let noFirstToken = false;
    let firstTokenSeen = false;
    let firstTokenTimer: NodeJS.Timeout | null = null;
    // ── Stream translation (P1 item 6) ───────────────────────────
    // opencode --format json emits a JSON-lines stream; translate each
    // envelope ONCE here into typed events (text → text.delta, reasoning →
    // thinking) so downstream consumers (SSE /jobs/:id/events, the duality
    // subscriber) never parse opencode's wire format themselves. Lines can
    // span chunks, so a partial line buffer is kept.
    let lineBuf = '';
    const errorMsgs: string[] = [];
    const emitParsed = () => {
      const lines = (lineBuf + '').split('\n');
      lineBuf = lines.pop() ?? ''; // last element may be incomplete
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith('{')) continue;
        try {
          const ev = JSON.parse(trimmed) as any;
          if (ev?.type === 'error') {
            // opencode surfaces provider failures (e.g. 429 rate-limit) as a
            // `{"type":"error","error":{"name":..,"data":{"message":..,
            // "statusCode":..}}}` envelope on stdout — NOT on stderr. Surface
            // it so callers see the real cause instead of "no text output".
            const err = ev.error ?? {};
            const data = err.data ?? err;
            const msg =
              typeof data?.message === 'string'
                ? data.message
                : typeof err?.message === 'string'
                  ? err.message
                  : null;
            const status = data?.statusCode ?? err?.statusCode ?? err?.status;
            if (msg) {
              errorMsgs.push(
                status ? `opencode error (${status}): ${msg}` : `opencode error: ${msg}`
              );
            }
          }
          if (ev?.type === 'text' || ev?.type === 'reasoning') {
            if (!firstTokenSeen) {
              firstTokenSeen = true;
              if (firstTokenTimer) clearInterval(firstTokenTimer);
            }
            if (onEvent) {
              const part = ev.part ?? {};
              const text =
                typeof part?.text === 'string'
                  ? part.text
                  : typeof ev.text === 'string'
                    ? ev.text
                    : '';
              if (text.trim()) {
                onEvent(ev.type, { text });
              }
            }
          }
        } catch { /* non-JSON line — skip */ }
      }
    };
    child.stdout?.on('data', (data: Buffer) => {
      const chunk = data.toString();
      stdout += chunk;
      lineBuf += chunk;
      emitParsed();
    });
    child.stderr?.on('data', (data: Buffer) => { stderr += data.toString(); });

    // Fixed 1s supervisor tick instead of setTimeout(timeout_ms): the
    // user-influenced duration is compared against a deadline rather than
    // handed to the timer API, so no timer with a user-controlled duration
    // is ever armed (CodeQL js/resource-exhaustion remediation, alerts
    // #753/#754/#758 family — same deadline pattern as the runaway watchdog).
    const deadline = Date.now() + timeout_ms;
    const firstTokenDeadline = Date.now() + Math.min(timeout_ms, FIRST_TOKEN_TIMEOUT_MS);
    const timer = setInterval(() => {
      if (Date.now() < deadline) return;
      timedOut = true;
      clearInterval(timer);
      child.kill('SIGTERM');
      // Give it 5s to exit gracefully, then force-kill
      setTimeout(() => {
        try { child.kill('SIGKILL'); } catch { /* already dead */ }
      }, 5000);
    }, 1000);

    // First-token guard — a model that never emits a text/reasoning token
    // within FIRST_TOKEN_TIMEOUT_MS is abandoned (exit 124 + marker) so the
    // failover ladder rolls to the next model instead of waiting out the full
    // run timeout on a hung provider.
    firstTokenTimer = setInterval(() => {
      if (Date.now() < firstTokenDeadline) return;
      if (firstTokenTimer) clearInterval(firstTokenTimer);
      if (firstTokenSeen) return;
      noFirstToken = true;
      child.kill('SIGTERM');
      setTimeout(() => {
        try { child.kill('SIGKILL'); } catch { /* already dead */ }
      }, 5000);
    }, 1000);

    child.on('close', (code) => {
      clearInterval(timer);
      if (firstTokenTimer) clearInterval(firstTokenTimer);
      let exitCode = code ?? 1;
      let stderrOut = stderr;
      if (noFirstToken) {
        exitCode = 124;
        stderrOut = `opencode produced no first token within ${Math.min(timeout_ms, FIRST_TOKEN_TIMEOUT_MS)}ms — abandoning`;
      } else if (timedOut) {
        // A signal-killed child reports code=null, which otherwise surfaces
        // as a misleading '(no stderr; exit 1)'. Mirror the ollama path's
        // exitCode 124 + message so callers know this was a timeout, and
        // keep the partial stdout for the caller to surface.
        exitCode = 124;
        stderrOut = `opencode timeout after ${timeout_ms}ms — partial output below`;
      } else if (code === null) {
        // Killed by a signal outside the timeout (e.g. the 15-min runaway
        // watchdog or an external kill) — report it honestly too instead of
        // the generic exit 1.
        exitCode = 137;
        stderrOut = 'opencode killed by signal (watchdog or external kill) — partial output below';
      }
      if (errorMsgs.length > 0) {
        const errText = errorMsgs.join("\n");
        stderrOut = stderrOut ? `${stderrOut}\n${errText}` : errText;
      }
      if (exitCode === 0) {
        incrementConsumedUnits(role).catch(() => {});
      }
      resolve({ exitCode, stdout, stderr: stderrOut });
    });

    child.on('error', (err) => {
      clearInterval(timer);
      if (firstTokenTimer) clearInterval(firstTokenTimer);
      resolve({ exitCode: 1, stdout, stderr: err.message });
    });
  });
}

// ── Outcome helpers ─────────────────────────────────────────────────

/**
 * Build instruction text telling the agent to emit an OUTCOME line.
 */
function buildOutcomeInstruction(outcomes: { code: string; description: string }[]): string {
  if (outcomes.length === 0) return "";
  const codes = outcomes.map((o) => o.code).join(", ");
  const descriptions = outcomes
    .map((o) => `  - ${o.code}: ${o.description}`)
    .join("\n");
  return `## Outcome Declaration

When you have completed your analysis, you MUST end your response with a line
in exactly this format:

OUTCOME: <code>

Where <code> is one of: ${codes}

Outcome descriptions:
${descriptions}

Choose the outcome that best reflects your conclusion.`;
}

/**
 * Parse the agent's output for an OUTCOME: <code> line.
 * Returns the matched outcome or null.
 */
function parseOutcome(
  output: string,
  outcomes: { id: string; code: string; description: string }[]
): { code: string; id: string; confidence: string } | null {
  // Look for "OUTCOME: <code>" in the last few lines
  const lines = output.trim().split("\n");
  const lastLines = lines.slice(-10); // check last 10 lines

  for (const line of lastLines) {
    const match = line.trim().match(/^OUTCOME:\s*(\w[\w-]*)\s*$/i);
    if (match) {
      const code = match[1].toLowerCase();
      const outcome = outcomes.find((o) => o.code === code);
      if (outcome) {
        return { code: outcome.code, id: outcome.id, confidence: "exact" };
      }
      // Close match (ignore case, underscores)
      const fuzzy = outcomes.find(
        (o) => o.code.replace(/_/g, "-") === code.replace(/_/g, "-")
      );
      if (fuzzy) {
        return { code: fuzzy.code, id: fuzzy.id, confidence: "fuzzy" };
      }
    }
  }

  // Fallback: keyword scan in full output
  for (const outcome of outcomes) {
    // SECURITY: escape regex metacharacters in outcome.code before interpolation
    const escapedCode = outcome.code.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp(`\\b${escapedCode.replace(/_/g, "[-_]?")}\\b`, "i");
    if (pattern.test(output)) {
      return { code: outcome.code, id: outcome.id, confidence: "keyword" };
    }
  }

  return null;
}

// ── Start (TWIN DEVIATION) ───────────────────────────────────────
// The incumbent's app.listen + server.on('error') + startWatchdog() are NOT
// ported: the moleculer broker owns lifecycle, and the twin's harness.service
// starts the watchdog in started() instead. Also dropped (per sibling-twin
// convention): the process-level uncaughtException handler near the top of
// this file — signal handling belongs to the broker/runner.

export {
  app,
  jobs,
  activeSessions,
  jobEnvelope,
  registerJob,
  pushJobEvent,
  setJobState,
  startWatchdog,
  checkRoleMemoryReadiness,
};
export type { HarnessJob, HarnessJobState, HarnessJobEvent, TrackedSession };
