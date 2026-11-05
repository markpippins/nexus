/**
 * Rung cooldown — the failover ladder remembers a rung that just stalled.
 *
 * Why this exists (measured 2026-11-05, jobs a706774a / ca0724f9):
 *
 * The tester bundle is ordered glm-5.3-flash-free, big-pickle,
 * openrouter/openai/gpt-oss-120b. On a bad day the two free rungs produce no
 * first token at all and are abandoned at the 120 s guard; the job then
 * completes on the third rung in ~40 s. One job paying 240 s of dead time is
 * tolerable. What is not tolerable is that the ladder keeps no memory, so
 * EVERY job in a burst re-pays both dead rungs before reaching the working
 * one — four back-to-back attestation runs each paid ~4 minutes.
 *
 * The rungs are not broken. The same glm answered in 8–10 s standalone with
 * the harness's exact input path, minutes after stalling under the harness.
 * A stall is a property of the moment, not of the model, which is why this is
 * a cooldown and not a demotion: the note expires, and the rung is back in
 * priority order.
 *
 * Nothing is ever dropped. A cooling rung moves to the END of the ladder, so
 * it is still tried when every other rung fails. This trades dead time, never
 * availability.
 */

/** How long a stalled rung stays at the back of the ladder. */
export const DEFAULT_RUNG_COOLDOWN_MS = 600_000;

/** model id → epoch ms of its most recent "no first token" abandonment. */
export type StallMap = Map<string, number>;

/**
 * Read the cooldown from the environment, falling back to the default.
 *
 * `0` disables the feature (ordering is left untouched), which is the
 * supported escape hatch if a cooldown ever mispredicts. Absent, empty, or
 * unparseable values use the default rather than silently disabling: a typo
 * in a unit file should not turn the feature off without anyone noticing.
 */
export function cooldownMsFromEnv(value: string | undefined): number {
  if (value === undefined || value.trim() === "") return DEFAULT_RUNG_COOLDOWN_MS;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) return DEFAULT_RUNG_COOLDOWN_MS;
  return parsed;
}

/** Remember that `model` was abandoned for producing no first token. */
export function noteStall(
  stalls: StallMap,
  model: string | null | undefined,
  now: number,
): void {
  if (!model) return;
  stalls.set(model, now);
}

/**
 * Is `model` still cooling at `now`?
 *
 * An expired note is pruned here, so the map cannot grow without bound: each
 * rung owns at most one entry and it is removed the first time it is read
 * after expiring.
 */
export function isCooling(
  stalls: StallMap,
  model: string,
  now: number,
  cooldownMs: number,
): boolean {
  if (!(cooldownMs > 0)) return false;
  const stalledAt = stalls.get(model);
  if (stalledAt === undefined) return false;
  if (now - stalledAt >= cooldownMs) {
    stalls.delete(model);
    return false;
  }
  return true;
}

/**
 * Order the ladder for one run: rungs that are not cooling keep their
 * configured priority, and cooling rungs follow in their original relative
 * order.
 *
 * The input array is never mutated and no attempt is ever removed — the
 * result is a permutation of the input, always of the same length.
 */
export function orderAttempts<T extends { model?: string | null }>(
  attempts: T[],
  stalls: StallMap,
  now: number,
  cooldownMs: number = DEFAULT_RUNG_COOLDOWN_MS,
): T[] {
  if (!(cooldownMs > 0)) return attempts.slice();
  const ready: T[] = [];
  const cooling: T[] = [];
  for (const attempt of attempts) {
    const key = attempt.model ?? "";
    if (key && isCooling(stalls, key, now, cooldownMs)) cooling.push(attempt);
    else ready.push(attempt);
  }
  // When every rung is cooling this returns the configured order unchanged
  // (cooling preserves input order), which is the availability-preserving
  // answer: there is nothing better to try first.
  return [...ready, ...cooling];
}
