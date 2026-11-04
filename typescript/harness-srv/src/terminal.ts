/**
 * terminal.ts — how a run's END is read (pure, no db/redis imports).
 *
 * harness-srv used to derive the terminal job state from the child process
 * exit status alone: `exit 0 → "completed"`. That reads a MECHANICAL signal
 * (the process terminated normally) as a JUDGEMENT (the work was done).
 *
 * The two came apart on 2026-11-04 (job 2b5c34ae, role=tester): opencode
 * exited 0 after 35s, and the job was reported `completed` with
 * `partial: false` — while the transcript stopped mid-task and nothing was
 * written. The exit status was honest; the state was not. Worse, the run's
 * stream ended with `{"type":"step_finish","part":{"reason":"stop"}}`, i.e.
 * the MODEL ended its turn early, so no timeout/budget watchdog could have
 * caught it either.
 *
 * opencode's own JSON-lines stream carries better evidence than the exit
 * status, and this module reads it:
 *
 *   {"type":"error", ..., "error":{"data":{"message":...,"statusCode":...}}}
 *       a provider/run failure (429, auth, upstream error) — it can arrive
 *       with a ZERO exit status, which is why the exit status alone cannot
 *       be trusted;
 *   {"type":"step_finish","part":{"reason":"stop"|"length"|"tool-calls",...}}
 *       why the model's final step ended. `length` means the model was cut
 *       off by a token limit — a genuinely truncated answer, reported as
 *       `failed` with `stop_reason: "length"` (the state vocabulary is
 *       shared with duality.session_turns, so it is not extended here).
 *
 * The rule is "the run's LAST word decides": a step_finish reasons of
 * `stop` after a transient error envelope means the run recovered and its
 * stop is the verdict; an error envelope as the final marker means the run
 * failed even if the process exited 0.
 *
 * This module is deliberately dependency-free (mirrors governance.ts and
 * model.ts) so tests can load it without constructing db clients.
 */

/** How the run ended, as far as the evidence in the stream can establish. */
export type TerminalState = "completed" | "failed" | "timed_out" | "cancelled";

/** One decision-relevant marker from the stream, in arrival order. */
export interface StreamMark {
  kind: "error" | "step_finish";
  /** step_finish only: stop | length | tool-calls (opencode's own token). */
  reason?: string;
  /** error only: the human-readable message, prefixed with its HTTP status. */
  message?: string;
}

export interface TerminalInput {
  exitCode: number | null;
  marks: StreamMark[];
  timedOut: boolean;
  noFirstToken: boolean;
  interrupted: boolean;
}

export interface TerminalVerdict {
  state: TerminalState;
  /** One line for the log/stderr: WHY this state, in the caller's terms. */
  detail: string;
}

/**
 * Parse opencode's JSON-lines stream into the markers that decide the state.
 *
 * Non-JSON lines (opencode can interleave plain text) are ignored, and so are
 * malformed envelopes — a stream we cannot parse yields no marks, which
 * classifies exactly as it did before this module existed.
 */
export function scanOpencodeStream(stdout: string): StreamMark[] {
  const marks: StreamMark[] = [];
  if (!stdout) return marks;
  for (const line of stdout.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("{")) continue;
    let ev: any;
    try {
      ev = JSON.parse(trimmed);
    } catch {
      continue;
    }
    if (ev?.type === "error") {
      const err = ev.error ?? {};
      const data = err.data ?? err;
      const msg =
        typeof data?.message === "string"
          ? data.message
          : typeof err?.message === "string"
            ? err.message
            : null;
      if (msg) {
        const status = data?.statusCode ?? err?.statusCode ?? err?.status;
        marks.push({
          kind: "error",
          message: status ? `opencode error (${status}): ${msg}` : `opencode error: ${msg}`,
        });
      }
      continue;
    }
    if (ev?.type === "step_finish") {
      const reason = typeof ev.part?.reason === "string" ? ev.part.reason : null;
      marks.push(reason ? { kind: "step_finish", reason } : { kind: "step_finish" });
    }
  }
  return marks;
}

/** Why the model's final step ended, or null when the stream never said. */
export function lastStepReason(marks: StreamMark[]): string | null {
  for (let i = marks.length - 1; i >= 0; i--) {
    const m = marks[i];
    if (m.kind === "step_finish" && m.reason) return m.reason;
  }
  return null;
}

/** Error-envelope messages in arrival order (for stderr surfacing). */
export function errorMessagesOf(marks: StreamMark[]): string[] {
  return marks.filter((m) => m.kind === "error" && m.message).map((m) => m.message as string);
}

/**
 * Classify the run. Order matters: an interruption or timeout is a statement
 * about the harness, and outranks anything the stream says (a killed child
 * never gets to write its final step).
 */
export function classifyTerminal(input: TerminalInput): TerminalVerdict {
  if (input.interrupted) {
    return { state: "cancelled", detail: "interrupted by user request" };
  }
  if (input.noFirstToken) {
    return { state: "timed_out", detail: "no first token before the guard deadline" };
  }
  if (input.timedOut) {
    return { state: "timed_out", detail: "run exceeded its timeout" };
  }
  if (input.exitCode !== 0) {
    return {
      state: "failed",
      detail: `opencode exited ${input.exitCode ?? "with no code"}`,
    };
  }

  const last = input.marks[input.marks.length - 1];
  if (last?.kind === "error") {
    // The process exited 0, but its last word was an error: the provider or
    // run failed and the zero exit status is about the wrapper, not the work.
    return {
      state: "failed",
      detail: `opencode exited 0 but its last stream event was an error — ${last.message}`,
    };
  }
  const reason = lastStepReason(input.marks);
  if (reason === "length") {
    // The answer was cut off by a token limit. Deliberately NOT a new state:
    // the state vocabulary is shared with duality.session_turns (CHECK
    // constraint) and every subscriber that waits for a terminal state, so a
    // seventh value would make old pollers spin to their deadline and report
    // a fake timeout. `failed` + `stop_reason: "length"` says the same thing
    // without stranding consumers.
    return {
      state: "failed",
      detail: "the model's final step ended on a token limit (reason=length), not on a natural stop — the answer is cut off",
    };
  }
  return {
    state: "completed",
    detail: "opencode exited 0 and the final step ended on a natural stop",
  };
}
