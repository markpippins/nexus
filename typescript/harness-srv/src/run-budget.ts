/**
 * Per-run wall-clock budget for the failover ladders (/run and /run-direct).
 *
 * ── Why this is a module and not arithmetic inside the loop ──────────────
 * On 2026-11-06 job 07469dca was posted with `timeout_ms: 1_800_000` (30
 * minutes) and was still running at minute 60. The deadline supervisor was
 * NOT at fault: the journal shows it abandoning the primary at
 * 16:03:33 → 16:33:34 with exit=124, exactly on time. The ladder then
 * started the fallback with the SAME 30-minute timeout, because `timeout_ms`
 * was read as a per-attempt setting while callers read it as "this run may
 * take at most this long". Two rungs therefore bounded the run at 2 ×
 * timeout_ms, three at 3 × — the documented intent and the mechanical
 * behaviour disagreed, and nothing said so.
 *
 * The fix is one deadline per RUN, with each attempt receiving only what is
 * left of it. An attempt that cannot get a usable slice is not started at
 * all: paying a first-token guard for a rung that cannot finish just burns
 * the caller's wall clock somewhere else.
 *
 * ── What this deliberately does NOT do ───────────────────────────────────
 * It does not shorten a single attempt, cap an individual rung, or decide
 * when to give up on a ladder. Ordering (rung cooldown) stays in
 * rung-cooldown.ts, terminal classification stays in terminal.ts, and the
 * clamp of the caller's number stays in the route handlers. This module only
 * answers "how much time does the run have left".
 */

/** Shortest slice worth starting an attempt with — matches the route floor. */
export const MIN_ATTEMPT_MS = 1_000;

/**
 * The single deadline for a run, fixed when the ladder starts.
 *
 * A timeout below the floor is raised to it rather than producing a deadline
 * already in the past: the route handlers already clamp, and this is the
 * in-process backstop for a caller that does not.
 */
export function runDeadlineMs(nowMs: number, timeoutMs: number): number {
  const budget = Number.isFinite(timeoutMs) ? Math.max(MIN_ATTEMPT_MS, timeoutMs) : MIN_ATTEMPT_MS;
  return nowMs + budget;
}

/** Time left before the run deadline. Never negative. */
export function remainingBudgetMs(deadlineMs: number, nowMs: number): number {
  return Math.max(0, deadlineMs - nowMs);
}

/**
 * Whether an attempt may still be started with the time that remains.
 * A zero or sub-second remainder is not "almost done" — it is a rung that
 * would be killed before its first token, so it is skipped instead.
 */
export function shouldStartAttempt(remainingMs: number): boolean {
  return remainingMs >= MIN_ATTEMPT_MS;
}

/**
 * The timeout handed to one attempt: the remaining run budget, floored at
 * the minimum slice. Only meaningful when shouldStartAttempt() is true.
 */
export function attemptTimeoutMs(remainingMs: number): number {
  return Math.max(MIN_ATTEMPT_MS, Math.floor(remainingMs));
}
