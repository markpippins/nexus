/**
 * CIR-SDM enforcement bridge (T23 Step 7/8) — subprocess caller to the Python
 * enforcement CLI (`python -m nexus_core.wrp.enforce_cli`).
 *
 * The pure evaluator lives in Python (`nexus_core/wrp/cir_sdm.py`) and is NOT
 * ported to TypeScript. conduit-mcp shells out to the CLI at WR-transition
 * admission to run the per-family enforcement caller over the WR's own ordered
 * event stream plus the proposed transition, then rejects-or-records based on
 * the returned decision.
 *
 * Contract (architect direction f034573a):
 *   - The ONLY environment read on the Python side is `CIR_SDM_ENFORCE`
 *     (read by the enforcement caller, never inside the pure evaluate()).
 *   - Fail-closed: a subprocess error/timeout HOLDS the transition and
 *     surfaces a `type:blocker` record. `CIR_SDM_ENFORCE=0` is the sanctioned
 *     disable (deliberate rollback), not an unhandled crash.
 *   - TS consumes only the fixed output shape (state, enforced, rules,
 *     violations, decisions, reject): `reject` → block; else proceed.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { insertCirViolation } from "./db";

export interface CirsdmViolation {
  violation_id: string;
  rule_id: string;
  rule_version: string;
  severity: string;
  event_id: string;
  cer_id: string | null;
  description: string;
  detected_at: number | null;
  blocking: boolean;
}

export interface CirsdmEnforceResult {
  state: "shadow" | "enforced";
  enforced: boolean;
  rules: string[];
  violations: CirsdmViolation[];
  decisions: CirsdmViolation[];
  reject: boolean;
}

const CLI_TIMEOUT_MS = 5000;
const NEBULA_API_URL =
  process.env.NEBULA_API_URL || "http://localhost:3101";

function pythonBin(): string {
  return process.env.CONDUIT_PYTHON || "python3";
}

/**
 * Walk up from `startDir` to the checkout that owns the Python tree, i.e. the
 * nearest ancestor containing `python/nexus_core/wrp/enforce_cli.py`.
 *
 * Anchored on the file we actually need to import rather than on `.git`: it is
 * the thing the subprocess requires, and a linked worktree has a `.git` *file*
 * while a renamed or relocated checkout keeps the same layout. Returns
 * `undefined` when no ancestor qualifies (packaged/dist layout).
 */
export function findRepoRoot(startDir: string): string | undefined {
  let dir = path.resolve(startDir);
  for (;;) {
    if (
      fs.existsSync(path.join(dir, "python", "nexus_core", "wrp", "enforce_cli.py"))
    ) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) return undefined;
    dir = parent;
  }
}

/**
 * Default PYTHONPATH for the CIR-SDM CLI: the `python/` of whichever checkout
 * this source lives in.
 *
 * The previous default walked four levels up from `src/` and then named the
 * repo `nexus/` — correct only when the checkout is literally named `nexus`.
 * From a linked worktree (`nexus-worktrees/<topic>`) it resolved to
 * `nexus-worktrees/nexus/python`, which does not exist, so every CIR-SDM call
 * died with `ModuleNotFoundError` and `gateWrTransition` failed CLOSED: a
 * worktree-run conduit-mcp held every transition while reporting
 * "enforcement gate unreachable". Discovery by content is name- and
 * depth-independent, so it survives worktrees and a repo rename.
 */
export function resolvePythonPath(startDir: string = __dirname): string {
  const root = findRepoRoot(startDir);
  if (root) return path.join(root, "python");
  // Unchanged fallback: preserve the previous resolution when no ancestor owns
  // the Python tree, so a relocated build keeps its existing failure message.
  return path.resolve(startDir, "../../../../nexus/python");
}

let cachedDefaultPythonPath: string | undefined;

function pythonPath(): string {
  if (process.env.PYTHONPATH) return process.env.PYTHONPATH;
  cachedDefaultPythonPath ??= resolvePythonPath();
  return cachedDefaultPythonPath;
}

interface CliResult {
  code: number;
  stdout: string;
  stderr: string;
}

function runCli(
  args: string[],
  stdinData?: string,
  timeoutMs: number = CLI_TIMEOUT_MS,
): Promise<CliResult> {
  return new Promise((resolve, reject) => {
    const proc = spawn(pythonBin(), ["-m", "nexus_core.wrp.enforce_cli", ...args], {
      env: { ...process.env, PYTHONPATH: pythonPath() },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let settled = false;

    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        proc.kill("SIGKILL");
        reject(new Error(`CIR-SDM enforce CLI timed out after ${timeoutMs}ms`));
      }
    }, timeoutMs);

    proc.stdout.on("data", (d) => (stdout += d.toString()));
    proc.stderr.on("data", (d) => (stderr += d.toString()));
    proc.on("error", (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });
    proc.on("close", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code: code ?? -1, stdout, stderr });
    });

    if (stdinData !== undefined) {
      proc.stdin.write(stdinData);
    }
    proc.stdin.end();
  });
}

/**
 * Startup audit line — the dispatch service logs this once at boot so the
 * enforcement posture (shadow vs enforced) is auditable (ruling 4a57c089).
 */
export async function getEnforcementState(): Promise<string> {
  const { code, stdout, stderr } = await runCli(["--state-only"]);
  if (code !== 0) {
    throw new Error(`CIR-SDM --state-only failed (${code}): ${stderr.trim()}`);
  }
  return stdout.trim();
}

/**
 * Run the enforcement caller over an ordered event stream plus a proposed
 * transition. Returns the parsed decision (reject + governed decisions).
 */
export async function enforceTransition(
  events: unknown[],
  proposed: unknown,
): Promise<CirsdmEnforceResult> {
  const { code, stdout, stderr } = await runCli(
    [],
    JSON.stringify({ events, proposed }),
  );
  if (code !== 0) {
    throw new Error(`CIR-SDM enforce CLI failed (${code}): ${stderr.trim()}`);
  }
  try {
    return JSON.parse(stdout.trim()) as CirsdmEnforceResult;
  } catch (e: any) {
    throw new Error(
      `CIR-SDM enforce CLI returned invalid JSON: ${stdout.slice(0, 200)}`,
    );
  }
}

/**
 * Pre-row enforcement gate for a WR transition (T23 Step 8). Pass the WR's
 * own ordered event stream (RuntimeEvents) + the proposed transition type;
 * the proposed event is NOT appended yet — enforcement decides before the row
 * exists (verdict-only, no mutation), exactly the D5 pattern.
 */
export async function gateWrTransition(
  events: unknown[],
  proposedType: string,
  wrUuid: string,
): Promise<CirsdmEnforceResult> {
  const proposed = {
    event_id: crypto.randomUUID(),
    type: proposedType,
    wrId: wrUuid,
    timestamp: new Date().toISOString(),
  };
  return enforceTransition(events, proposed);
}

/**
 * Record governed decisions in peb.cir_violations (INSERT-only, idempotent on
 * the deterministic violation_id). Never mutates canonical WR rows.
 */
export async function recordGovernedDecisions(
  decisions: CirsdmViolation[],
): Promise<void> {
  for (const d of decisions) {
    try {
      await insertCirViolation(d);
    } catch (e: any) {
      console.error(
        `[CIR-SDM] failed to record decision ${d.violation_id}: ${e.message}`,
      );
    }
  }
}

/**
 * Surface a `type:blocker` record to the architect's inbox (best-effort) so a
 * fail-closed gate outage is visible, not silent. Logs prominently either way.
 */
export async function surfaceBlocker(message: string): Promise<void> {
  console.error(`[CIR-SDM][BLOCKER] ${message}`);
  try {
    await fetch(`${NEBULA_API_URL}/api/agent-records`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recordType: "report",
        role: "engineer",
        title: "CIR-SDM enforcement unavailable (fail-closed) — type:blocker",
        content: message,
        tags: ["to:architect", "type:blocker", "cir-sdm", "status:open"],
        level: 2,
        visibilityScope: "architect",
      }),
    });
  } catch (e: any) {
    console.error(`[CIR-SDM] failed to post blocker record: ${e.message}`);
  }
}
