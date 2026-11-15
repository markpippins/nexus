/**
 * child-process.ts — spawn + supervise a harness child, once.
 *
 * Why this exists: harness-srv grew a second CLI (codex) beside opencode, and
 * the two executors were written against the same supervision contract —
 * register the PID so the runaway watchdog and POST /jobs/:id/interrupt can
 * reach it, arm a deadline on a fixed 1s tick (never a timer handed a
 * user-influenced duration: CodeQL js/resource-exhaustion), abandon an attempt
 * that never produces a first token, and end a child with SIGTERM then
 * SIGKILL. Copying that into the second executor duplicated ~90 lines of it,
 * and the duplication is not free: the first copy is the one a fix gets
 * applied to.
 *
 * What stays in the executors: how to READ the child's stdout (opencode and
 * codex speak different JSON dialects) and how to turn the end of the run into
 * an exit code plus stderr, because those legitimately differ.
 *
 * Dependencies: only node builtins. The refusal watch is passed in as a
 * predicate rather than imported, so this module stays portable to any
 * executor that grows one later.
 */

import { spawn, type ChildProcess } from "child_process";

/** Give a child this long to exit on SIGTERM before SIGKILL. */
export const KILL_GRACE_MS = 5_000;

/** How often the supervisor compares wall clock against its deadlines. */
export const SUPERVISION_TICK_MS = 1_000;

/**
 * End a child: SIGTERM, then SIGKILL if it is still alive after the grace
 * window. Safe to call twice (a dead child throws ESRCH on kill).
 */
export function terminateChild(child: ChildProcess): void {
  child.kill("SIGTERM");
  setTimeout(() => {
    try {
      child.kill("SIGKILL");
    } catch {
      /* already dead */
    }
  }, KILL_GRACE_MS);
}

export interface SpawnHarnessChildParams {
  bin: string;
  args: string[];
  workDir: string;
  env: NodeJS.ProcessEnv;
  /** Prompt text, delivered on stdin. Closed immediately: both CLIs block
   *  on an open pipe (codex prints "Reading additional input from stdin..."
   *  even when a prompt was supplied). */
  promptContent: string;
}

/** Spawn a harness child with the pipe/stdin contract both CLIs need. */
export function spawnHarnessChild({
  bin,
  args,
  workDir,
  env,
  promptContent,
}: SpawnHarnessChildParams): ChildProcess {
  const child = spawn(bin, args, {
    cwd: workDir,
    env,
    stdio: ["pipe", "pipe", "pipe"],
  });

  if (child.stdin) {
    // EPIPE when the child exits early is normal, not an error to report.
    child.stdin.on("error", () => { /* child may have exited early */ });
    child.stdin.write(promptContent);
    child.stdin.end();
  }

  return child;
}

export interface SupervisionState {
  /** The attempt ran past its timeout and was ended by us. */
  timedOut: boolean;
  /** The attempt produced no first token before the guard deadline. */
  noFirstToken: boolean;
  /** The attempt was abandoned because a provider refusal went unanswered. */
  refusedAbandon: boolean;
}

export interface ArmSupervisionParams {
  child: ChildProcess;
  /** Whole-attempt budget. */
  timeoutMs: number;
  /** Silence allowance for the first token. */
  firstTokenGuardMs: number;
  /** Has the attempt emitted its first real token yet? */
  firstTokenSeen: () => boolean;
  /** Has a provider refusal gone unanswered past its grace window? */
  refusalIsTerminal?: () => boolean;
}

/**
 * Arm the attempt's two deadlines and return the live state plus a stop().
 *
 * The deadline is compared on a fixed tick instead of being handed to
 * setTimeout, so no timer is ever armed with a user-influenced duration.
 * stop() must be called when the child closes; otherwise both intervals keep
 * the event loop armed.
 */
export function armSupervision({
  child,
  timeoutMs,
  firstTokenGuardMs,
  firstTokenSeen,
  refusalIsTerminal,
}: ArmSupervisionParams): { state: SupervisionState; stop: () => void } {
  const state: SupervisionState = {
    timedOut: false,
    noFirstToken: false,
    refusedAbandon: false,
  };

  let firstTokenTimer: NodeJS.Timeout | null = null;

  const deadline = Date.now() + timeoutMs;
  const firstTokenDeadline = Date.now() + firstTokenGuardMs;

  const timer = setInterval(() => {
    if (Date.now() < deadline) return;
    state.timedOut = true;
    clearInterval(timer);
    if (firstTokenTimer) clearInterval(firstTokenTimer);
    terminateChild(child);
  }, SUPERVISION_TICK_MS);

  firstTokenTimer = setInterval(() => {
    if (firstTokenSeen()) {
      if (firstTokenTimer) clearInterval(firstTokenTimer);
      return;
    }
    // The provider's own words outrank the guard's deadline: a refusal that
    // nothing answered is final after its grace window.
    if (refusalIsTerminal?.()) {
      if (firstTokenTimer) clearInterval(firstTokenTimer);
      state.refusedAbandon = true;
      terminateChild(child);
      return;
    }
    if (Date.now() < firstTokenDeadline) return;
    if (firstTokenTimer) clearInterval(firstTokenTimer);
    state.noFirstToken = true;
    terminateChild(child);
  }, SUPERVISION_TICK_MS);

  return {
    state,
    stop: () => {
      clearInterval(timer);
      if (firstTokenTimer) clearInterval(firstTokenTimer);
    },
  };
}
