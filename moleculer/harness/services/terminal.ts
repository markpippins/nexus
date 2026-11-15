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
  /** Which CLI produced the stream; only used to word the detail line. */
  harness?: "opencode" | "codex";
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
  // Word the verdict with the CLI that actually ran: codex and opencode are
  // both harness children now, and "opencode exited 1" on a codex run sends
  // the reader to the wrong tool.
  const label = input.harness === "codex" ? "codex" : "opencode";

  if (input.exitCode !== 0) {
    return {
      state: "failed",
      detail: `${label} exited ${input.exitCode ?? "with no code"}`,
    };
  }

  const last = input.marks[input.marks.length - 1];
  if (last?.kind === "error") {
    // The process exited 0, but its last word was an error: the provider or
    // run failed and the zero exit status is about the wrapper, not the work.
    return {
      state: "failed",
      detail: `${label} exited 0 but its last stream event was an error — ${last.message}`,
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
    detail: `${label} exited 0 and the final step ended on a natural stop`,
  };
}

// ── codex stream ────────────────────────────────────────────────────

/**
 * One decision-relevant marker from codex's `--json` stream.
 *
 * codex emits a different JSONL vocabulary from opencode's, so it gets its
 * own mark type rather than being forced through StreamMark: there is no
 * `step_finish`/`part.reason`, and text arrives as whole `item.completed`
 * items instead of incremental `text`/`reasoning` envelopes. Observed on
 * codex-cli 0.169.0 (2026-11-15):
 *
 *   {"type":"thread.started","thread_id":"..."}
 *   {"type":"turn.started"}
 *   {"type":"item.started"|"item.completed","item":{"type":"agent_message","text":"..."}}
 *   {"type":"item.completed","item":{"type":"command_execution","command":...,"exit_code":n}}
 *   {"type":"item.completed","item":{"type":"error","message":"..."}}
 *   {"type":"error","message":"..."}
 *   {"type":"turn.failed","error":{"message":"..."}}
 *   {"type":"turn.completed","usage":{...}}
 */
export interface CodexMark {
  kind: "error" | "warning" | "message" | "turn_completed";
  /** error/warning only: the human-readable message. */
  message?: string;
  /** message only: assistant text from an `agent_message` item. */
  text?: string;
}

/**
 * The message carried by one codex FAILURE envelope, or null.
 *
 * Only the two hard shapes count — a top-level `error` event and a
 * `turn.failed`. The third shape (`item.completed` with `item.type` "error")
 * carries ADVICE, not failure: every run on this host starts with "Codex is
 * ignoring N unrecognized configuration settings", which is not a refusal and
 * must never be watched as one (a refusal that nothing answers is abandoned
 * after the grace window, so mistaking a warning for a refusal would kill
 * healthy runs).
 */
export function codexRefusalMessage(ev: any): string | null {
  if (!ev || typeof ev !== "object") return null;
  if (ev.type === "turn.failed") {
    const msg = ev.error?.message ?? ev.message;
    return typeof msg === "string" && msg.trim() ? msg.trim() : null;
  }
  if (ev.type === "error") {
    const msg = typeof ev.message === "string" ? ev.message : ev.error?.message;
    return typeof msg === "string" && msg.trim() ? msg.trim() : null;
  }
  return null;
}

/** An `item.completed` advisory (e.g. ignored config keys), or null. */
export function codexWarningMessage(ev: any): string | null {
  if (ev?.type !== "item.completed" && ev?.type !== "item.started") return null;
  const item = ev.item ?? {};
  if (item.type !== "error") return null;
  return typeof item.message === "string" && item.message.trim() ? item.message.trim() : null;
}

/**
 * Parse codex's JSON-lines stream into marks.
 *
 * `item.started` is skipped for text because the same item is repeated as
 * `item.completed` with its final text — counting both would double every
 * assistant message in the transcript.
 */
export function scanCodexStream(stdout: string): CodexMark[] {
  const marks: CodexMark[] = [];
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
    if (ev?.type === "turn.completed") {
      marks.push({ kind: "turn_completed" });
      continue;
    }
    const message = codexRefusalMessage(ev);
    if (message) {
      marks.push({ kind: "error", message: `codex error: ${message}` });
      continue;
    }
    const warning = codexWarningMessage(ev);
    if (warning) {
      marks.push({ kind: "warning", message: warning });
      continue;
    }
    if (ev?.type === "item.completed") {
      const item = ev.item ?? {};
      const text =
        typeof item.text === "string"
          ? item.text
          : typeof ev.text === "string"
            ? ev.text
            : "";
      if ((item.type === "agent_message" || item.type === "reasoning") && text.trim()) {
        marks.push({ kind: "message", text });
      }
    }
  }
  return marks;
}

/**
 * Error-envelope messages in arrival order (for stderr surfacing), with exact
 * repeats collapsed. codex says the same thing twice on a hard failure — once
 * as a top-level `error` event and again inside `turn.failed` (measured
 * 2026-11-15: an unsupported-model run emitted the identical message in both)
 * — and doubling it in the job's stderr only makes the cause harder to read.
 */
export function codexErrorMessages(marks: CodexMark[]): string[] {
  const out: string[] = [];
  for (const m of marks) {
    if (m.kind !== "error" || !m.message) continue;
    if (!out.includes(m.message)) out.push(m.message);
  }
  return out;
}

/** The assistant's last non-empty message, or "" when it never spoke. */
export function codexFinalText(marks: CodexMark[]): string {
  for (let i = marks.length - 1; i >= 0; i--) {
    const m = marks[i];
    if (m.kind === "message" && m.text) return m.text;
  }
  return "";
}

/** Did codex report the turn complete (the run's own last word)? */
export function codexTurnCompleted(marks: CodexMark[]): boolean {
  for (let i = marks.length - 1; i >= 0; i--) {
    if (marks[i].kind === "turn_completed") return true;
  }
  return false;
}

/**
 * Project codex marks onto the shared vocabulary the terminal classifier reads.
 *
 * A completed codex turn is the exact counterpart of opencode's
 * `step_finish(part.reason="stop")` — "the model ended its turn on purpose" —
 * so it maps to that mark instead of teaching classifyTerminal a second
 * dialect. Arrival order is preserved, which is what makes the "the run's
 * LAST word decides" rule work for codex too.
 */
export function codexMarksToStreamMarks(marks: CodexMark[]): StreamMark[] {
  const out: StreamMark[] = [];
  for (const m of marks) {
    if (m.kind === "error" && m.message) out.push({ kind: "error", message: m.message });
    else if (m.kind === "turn_completed") out.push({ kind: "step_finish", reason: "stop" });
  }
  return out;
}

/** Which CLI wrote this stream. Defaults to opencode (the incumbent). */
export function detectHarness(stdout: string): "opencode" | "codex" {
  if (!stdout) return "opencode";
  for (const line of stdout.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("{")) continue;
    let ev: any;
    try {
      ev = JSON.parse(trimmed);
    } catch {
      continue;
    }
    if (ev?.type === "thread.started" || ev?.type === "turn.started" || ev?.type === "turn.completed" || ev?.type === "turn.failed") {
      return "codex";
    }
    if (ev?.type === "step_finish" || ev?.type === "text" || ev?.type === "reasoning") {
      return "opencode";
    }
  }
  return "opencode";
}

/**
 * Read a harness child's stream whichever CLI produced it.
 *
 * The two vocabularies are disjoint (`step_finish`/`text`/`reasoning` versus
 * `thread.*`/`turn.*`/`item.*`), so one pass can read both exactly rather
 * than heuristically. That matters for a failover ladder that mixes CLIs in
 * one run: each attempt's stdout is re-read on its own, so attempt 1 (codex)
 * and attempt 2 (opencode) each classify on their own evidence.
 */
export function scanHarnessStream(stdout: string): StreamMark[] {
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
    if (ev?.type === "turn.completed") {
      marks.push({ kind: "step_finish", reason: "stop" });
      continue;
    }
    if (ev?.type === "turn.failed") {
      const msg = codexRefusalMessage(ev);
      if (msg) marks.push({ kind: "error", message: `codex error: ${msg}` });
      continue;
    }
    if (ev?.type === "error") {
      // Both CLIs call it `error`, but only opencode nests the cause under
      // `error.data`/`error.message`; codex carries a flat `message`.
      const err = ev.error ?? {};
      const data = err.data ?? err;
      const opencodeMsg =
        typeof data?.message === "string"
          ? data.message
          : typeof err?.message === "string"
            ? err.message
            : null;
      if (opencodeMsg && ev?.error) {
        const status = data?.statusCode ?? err?.statusCode ?? err?.status;
        marks.push({
          kind: "error",
          message: status ? `opencode error (${status}): ${opencodeMsg}` : `opencode error: ${opencodeMsg}`,
        });
        continue;
      }
      const codexMsg = codexRefusalMessage(ev);
      if (codexMsg) marks.push({ kind: "error", message: `codex error: ${codexMsg}` });
      continue;
    }
    if (ev?.type === "step_finish") {
      const reason = typeof ev.part?.reason === "string" ? ev.part.reason : null;
      marks.push(reason ? { kind: "step_finish", reason } : { kind: "step_finish" });
    }
  }
  return marks;
}
