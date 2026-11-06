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

/**
 * Shortest slice worth starting an attempt with — matches the route floor.
 *
 * Deliberately NOT raised to the first-token allowance: an attempt of a
 * minute is a perfectly good attempt, and the allowance itself shrinks with
 * the slice (see sliceAllowsFirstToken). What a short slice must not do is
 * get its outcome blamed on the model, which is a separate question.
 */
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

/**
 * Whether an attempt's slice reaches the harness's own first-token allowance.
 *
 * `executeOpencode` arms its first-token guard at `min(timeout_ms,
 * FIRST_TOKEN_TIMEOUT_MS)`, so the allowance shrinks with the slice: an
 * attempt given less than the allowance is abandoned for "no first token" by
 * construction, because the guard fires before the provider could reasonably
 * have answered.
 *
 * That distinction is not cosmetic. A "no first token" observation also costs
 * the rung its priority slot for RUNG_COOLDOWN_MS (see rung-cooldown.ts), so
 * treating a budget-shortened attempt as a stall pushes a healthy model to the
 * back of every ladder for ten minutes — the run's own budget, twice punished.
 *
 * A slice at or above the allowance is the only case where the absence of a
 * token is evidence about the provider rather than about the slice we gave it.
 */
export function sliceAllowsFirstToken(sliceMs: number, firstTokenMs: number): boolean {
  return sliceMs >= firstTokenMs;
}

/**
 * Default floor for one attempt's silence allowance — the least time a rung is
 * given to produce a first token when the run budget is too small to grant it
 * the full guard.
 *
 * Deliberately far below FIRST_TOKEN_TIMEOUT_MS: the point of the floor is
 * that no single rung may spend the run's entire budget in silence. 30s is
 * roughly 3x the observed first-token latency of the catalog models (8-10s,
 * see rung-cooldown.ts) and still short enough that a dead provider is cheap.
 */
export const DEFAULT_FIRST_TOKEN_FLOOR_MS = 30_000;

/**
 * The silence floor from the environment.
 *
 * `0` restores the pre-2026-11-06 behaviour (one attempt may consume the whole
 * run budget in silence) and is the supported escape hatch. Absent, blank, or
 * unparseable values use the default; a negative value is 0 rather than a
 * silent revert to the default.
 */
export function firstTokenFloorMsFromEnv(value: string | undefined): number {
  if (value === undefined || value.trim() === "") return DEFAULT_FIRST_TOKEN_FLOOR_MS;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_FIRST_TOKEN_FLOOR_MS;
  return Math.max(0, Math.floor(parsed));
}

/**
 * How long ONE attempt may stay silent before the harness abandons it.
 *
 * The defect (job 78aaf0d2, 2026-11-06): `executeOpencode` arms its
 * first-token guard at `min(timeout_ms, FIRST_TOKEN_TIMEOUT_MS)`. That tester
 * job was posted with `timeout_ms = 120_000` — exactly the guard — so the
 * primary's silence consumed the WHOLE run budget. The ladder then logged
 * "run budget of 120000ms exhausted (0ms left) — not starting fallback" and
 * the run ended `timed_out` after 1 of 6 rungs, for a provider that had
 * produced nothing at all (verified independently: 0 bytes on stdout, 0 on
 * stderr). A refusal is not the only way a provider can fail without saying
 * anything; this is the silent case that provider-refusal.ts cannot read.
 *
 * The rule: a rung may have the full guard only when the remaining budget can
 * still leave every rung BEHIND it the floor allowance. Otherwise its
 * allowance is what remains after that reservation, never less than the
 * floor. The attempt's SLICE is unaffected — once a token arrives it keeps the
 * rest of the run's budget for actual work — so this bounds silence, not work.
 *
 * `floorMs = 0` disables the reservation and reproduces the old behaviour.
 */
export function firstTokenAllowanceMs(
  remainingMs: number,
  rungsLeft: number,
  guardMs: number,
  floorMs: number = DEFAULT_FIRST_TOKEN_FLOOR_MS,
): number {
  const left = Number.isFinite(remainingMs) ? Math.max(0, Math.floor(remainingMs)) : 0;
  const rungs = Number.isFinite(rungsLeft) ? Math.max(1, Math.floor(rungsLeft)) : 1;
  const guard = Number.isFinite(guardMs) ? Math.max(0, Math.floor(guardMs)) : 0;
  const floor = Number.isFinite(floorMs)
    ? Math.max(0, Math.floor(floorMs))
    : DEFAULT_FIRST_TOKEN_FLOOR_MS;

  const reserve = floor * (rungs - 1);
  // Never above the guard, never below the floor, and never past the budget we
  // actually have: an allowance longer than the remaining time would outlive
  // the run it belongs to.
  return Math.min(left, Math.min(guard, Math.max(floor, left - reserve)));
}
