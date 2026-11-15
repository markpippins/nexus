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

export interface RunSupervisedParams {
  bin: string;
  args: string[];
  workDir: string;
  env: NodeJS.ProcessEnv;
  promptContent: string;
  timeoutMs: number;
  firstTokenGuardMs: number;
  /** Which CLI this is, as it should read in a timeout/abandon message. */
  label: string;
  /**
   * Attach the CLI's own stdout/stderr readers. The executor owns its wire
   * format; this module only supplies the plumbing: call markFirstToken() the
   * first time real assistant text arrives (the guard polls it). Chunk-level
   * watchdog activity stays the executor's job, since it already sees each
   * chunk.
   */
  attachStreams: (child: ChildProcess, markFirstToken: () => void) => void;
  /** Has a provider refusal gone unanswered past its grace window? */
  refusalIsTerminal?: () => boolean;
  /** CLI-specific error text to append to stderr, from the full stdout. */
  streamErrors: (stdout: string) => string[];
  /** Called once with the child pid (or undefined when spawn failed), so the
   *  caller can register it with the runaway watchdog and job registry. */
  onChildPid?: (pid: number | undefined) => void;
}

export interface SupervisedResult {
  exitCode: number;
  stdout: string;
  stderr: string;
  state: SupervisionState;
}

/**
 * Run one supervised child to completion.
 *
 * Everything that is true of ANY harness child lives here: spawn with the
 * prompt pipe closed, PID hand-off, the deadline and first-token guard, and
 * the mapping from how the child ended to the exit code the callers already
 * key on (1 refusal, 124 timeout, 124 no-first-token, 137 signalled). The
 * executor supplies only what differs: its stdout reader and its idea of
 * which stream lines are errors.
 */
export function runSupervisedChild({
  bin,
  args,
  workDir,
  env,
  promptContent,
  timeoutMs,
  firstTokenGuardMs,
  label,
  attachStreams,
  refusalIsTerminal,
  streamErrors,
  onChildPid,
}: RunSupervisedParams): Promise<SupervisedResult> {
  return new Promise((resolve) => {
    const child = spawnHarnessChild({ bin, args, workDir, env, promptContent });
    onChildPid?.(child.pid);

    let stdout = "";
    let stderr = "";
    let firstTokenSeen = false;

    attachStreams(child, () => { firstTokenSeen = true; });

    // Every stdout chunk belongs to the executor's parser AND to the buffers
    // the caller receives; capturing here keeps that one writer.
    child.stdout?.on("data", (data: Buffer) => {
      stdout += data.toString();
    });
    child.stderr?.on("data", (data: Buffer) => {
      stderr += data.toString();
    });

    const supervision = armSupervision({
      child,
      timeoutMs,
      firstTokenGuardMs,
      firstTokenSeen: () => firstTokenSeen,
      refusalIsTerminal,
    });

    child.on("close", (code) => {
      supervision.stop();
      const state = supervision.state;
      let exitCode = code ?? 1;
      let stderrOut = stderr;
      if (state.refusedAbandon) {
        // Exit 1, not 124: the harness did not run out of time and the model
        // did not hang — the provider refused. The caller supplies the words.
        exitCode = 1;
      } else if (state.noFirstToken) {
        exitCode = 124;
        stderrOut = `${label} produced no first token within ${firstTokenGuardMs}ms — abandoning`;
      } else if (state.timedOut) {
        exitCode = 124;
        stderrOut = `${label} timeout after ${timeoutMs}ms — partial output below`;
      } else if (code === null) {
        exitCode = 137;
        stderrOut = `${label} killed by signal (watchdog or external kill) — partial output below`;
      }
      const errors = streamErrors(stdout);
      if (errors.length > 0) {
        const errText = errors.join("\n");
        stderrOut = stderrOut ? `${stderrOut}\n${errText}` : errText;
      }
      resolve({ exitCode, stdout, stderr: stderrOut, state });
    });

    child.on("error", (err) => {
      supervision.stop();
      resolve({
        exitCode: 1,
        stdout,
        stderr: err.message,
        state: supervision.state,
      });
    });
  });
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
