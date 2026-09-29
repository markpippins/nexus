/**
 * liveness-probe — post-spawn liveness classification for restart-builder.
 *
 * WO-1 task 4 (work order d6398876; engineer R1 d6cc6c42): the endpoint
 * used to spawn a detached main.py with stdio ignored and report
 * `restarted: true` unconditionally. Reproduced live (plan 8261653):
 * the child exited within seconds — the CONDUIT_WR_EMIT_MODE != "file"
 * gate makes _dispatch_one a clean no-op under the default runtime
 * emit mode — yet the caller saw success and no ticket materialized.
 *
 * Classification is pure and injectable so the endpoint stays thin and
 * the probe is unit-testable without spawning processes:
 *   - "running"     child alive at probe end → restarted=true
 *   - "exited"      child exited (code/signal + stdout tail for triage)
 *   - "gated-no-op" exited AND stdout carries the legacy emitter gate
 *                   marker printed by main.py _dispatch_one
 *   - "unknown"     probe error (e.g. kill(0) EPERM on exotic platforms)
 *
 * The marker string MUST stay in lockstep with main.py's
 * "[gated] Legacy WR file-emission disabled" print.
 */

/** Marker printed by main.py _dispatch_one when WR file-emission is gated. */
export const GATED_NOOP_MARKER = "[gated] Legacy WR file-emission disabled";

export interface ChildHandle {
  /** Child PID as reported by spawn (used for liveness signalling). */
  readonly pid: number | undefined;
  /** Fires when the child exits (mirrors ChildProcess "exit" event). */
  readonly onceExit: Promise<{ code: number | null; signal: string | null }>;
  /** POSIX-style liveness check; returns false once the child has exited. */
  readonly isAlive: () => boolean;
  /** Collected child stdout (decoded) for triage output. */
  readonly stdout: () => string;
  /** Collected child stderr (decoded) for triage output. */
  readonly stderr: () => string;
}

export type LivenessStatus = "running" | "exited" | "gated-no-op" | "unknown";

export interface LivenessResult {
  status: LivenessStatus;
  /** Present when status === "exited". */
  exitCode?: number | null;
  /** Present when the child was terminated by a signal. */
  exitSignal?: string | null;
  /** Trailing stdout fragment (bounded) for operator triage. */
  stdoutTail?: string;
  /** Trailing stderr fragment (bounded) for operator triage. */
  stderrTail?: string;
}

/** Sleep helper (injection seam for tests via settleMs/pollIntervalMs). */
const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/** Bound the captured output so responses stay small. */
function tail(text: string, maxChars = 400): string {
  const trimmed = text.trim();
  return trimmed.length <= maxChars ? trimmed : trimmed.slice(-maxChars);
}

/** True when captured stdout indicates main.py's gated no-op path. */
export function isGatedNoOp(stdout: string): boolean {
  return stdout.includes(GATED_NOOP_MARKER);
}

/**
 * Probe the child after spawn: give short-lived processes a settle window
 * to exit on their own, then classify. Never throws — probe errors
 * degrade to status "unknown" (restarted stays false so the caller does
 * not consume a false green).
 */
export async function probeChildLiveness(
  child: ChildHandle,
  opts: { settleMs?: number; pollIntervalMs?: number } = {},
): Promise<LivenessResult> {
  const settleMs = opts.settleMs ?? 1500;
  const pollIntervalMs = opts.pollIntervalMs ?? 250;

  try {
    // Deterministic fast path: the child already exited during the settle
    // window (the reproduced defect — gated no-op exits in milliseconds).
    const early = await Promise.race([
      child.onceExit.then((e) => ({ kind: "exit" as const, ...e })),
      sleep(settleMs).then(() => ({ kind: "timeout" as const })),
    ]);
    if (early.kind === "exit") {
      return classifyExit(child, early.code, early.signal);
    }

    // Still running after the settle window: poll liveness for a bounded
    // period so a child that dies just after settle is not misreported.
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) {
      if (child.isAlive()) {
        return { status: "running" };
      }
      const race = await Promise.race([
        child.onceExit.then((e) => ({ kind: "exit" as const, ...e })),
        sleep(pollIntervalMs).then(() => ({ kind: "timeout" as const })),
      ]);
      if (race.kind === "exit") {
        return classifyExit(child, race.code, race.signal);
      }
    }
    // Survived settle + poll window with a positive liveness check.
    return { status: "running" };
  } catch (err) {
    return {
      status: "unknown",
      stderrTail: tail(err instanceof Error ? err.message : String(err)),
    };
  }
}

function classifyExit(
  child: ChildHandle,
  code: number | null,
  signal: string | null,
): LivenessResult {
  const out = child.stdout();
  return {
    status: isGatedNoOp(out) ? "gated-no-op" : "exited",
    exitCode: code,
    exitSignal: signal,
    stdoutTail: tail(out),
    stderrTail: tail(child.stderr()),
  };
}
