/**
 * provider-refusal.ts — fail fast when opencode refuses, instead of waiting
 * out the first-token guard.
 *
 * The defect (record b42b7f64, 2026-11-06): opencode reports provider
 * failures as `{"type":"error", "error":{"data":{"message":…,"statusCode":429}}}`
 * envelopes on STDOUT. `executeOpencode` reads those envelopes at close, so a
 * refusal that also ends the process was already handled. But the process
 * does NOT always end: with `opencode/big-pickle` over its free-usage limit,
 * opencode emits the error envelope and then sits there retrying. No text
 * token arrives, so the first-token guard fires 120 s later and the attempt is
 * recorded as "produced no first token" — a statement about a hung model, when
 * the provider had already said, in 400 ms, exactly what was wrong.
 *
 * Two costs, both real: 120 s of the run's budget per dead rung, and a
 * misleading cause. The ladder fix (#898/#899) bounds the total, but the right
 * answer is to read the refusal when it arrives.
 *
 * ── Why a grace window and not an immediate kill ─────────────────────────
 * terminal.ts's doctrine is "the run's LAST word decides": a transient 429
 * followed by a successful step is a recovered run, not a failure. Killing on
 * the first error envelope would turn every recoverable blip into a failed
 * attempt. So a refusal only becomes terminal if nothing else arrives within
 * REFUSAL_GRACE_MS — a token, a step, or the process exiting on its own all
 * cancel it (the latter two because the normal close path then decides).
 *
 * ── Why this is not a stall ──────────────────────────────────────────────
 * A refusal is evidence about the provider, and it is honest evidence, but it
 * is NOT the "no first token" observation that rung-cooldown keys on: the
 * abandonment message below deliberately avoids those words, so
 * harnessEndedAttempt() stays false, the attempt classifies as `failed` with
 * the provider's own text, and the rung is not demoted for ten minutes on a
 * guess. (A 429 is worth cooling down, but that is a policy decision about
 * rate limits, not a side effect of how this message is worded.)
 *
 * Pure module (mirrors rung-cooldown.ts): tests load it without db/redis.
 */

/** How long a refusal may stand unanswered before it is treated as final. */
export const DEFAULT_REFUSAL_GRACE_MS = 5_000;

/** A refusal seen on the stream, waiting to see whether the run recovers. */
export interface RefusalWatch {
  /** The provider's message, as opencode reported it. */
  message: string;
  /** When it was first seen (epoch ms). */
  firstSeenAt: number;
}

/**
 * Grace from the environment. `0` makes a refusal terminal at the next
 * supervisor tick (still not instant — the tick is the observation point);
 * unset, empty, or unparseable values use the default.
 */
export function refusalGraceMsFromEnv(value: string | undefined): number {
  if (value === undefined || value.trim() === "") return DEFAULT_REFUSAL_GRACE_MS;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) return DEFAULT_REFUSAL_GRACE_MS;
  return parsed;
}

/**
 * Record a refusal. The FIRST message and timestamp are kept: repeated
 * retries of the same 429 should not keep pushing the grace deadline out,
 * or a retrying provider would never be abandoned.
 */
export function noteRefusal(
  watch: RefusalWatch | null,
  message: string,
  now: number,
): RefusalWatch {
  if (watch) return watch;
  return { message, firstSeenAt: now };
}

/** Has the grace window elapsed with no output since the refusal? */
export function refusalIsTerminal(
  watch: RefusalWatch | null,
  now: number,
  graceMs: number,
): boolean {
  if (!watch) return false;
  return now - watch.firstSeenAt >= Math.max(0, graceMs);
}

/**
 * The abandonment text for a refusal that never recovered.
 *
 * Must not contain "no first token" or "timeout after": those are the
 * markers terminal.ts treats as harness-ending, and mislabelling a provider
 * refusal as a timeout is the defect this module exists to correct.
 */
export function refusalAbandonMessage(watch: RefusalWatch, now: number): string {
  const waited = Math.max(0, now - watch.firstSeenAt);
  return `opencode was refused by the provider after ${waited}ms without producing any output — ${watch.message}`;
}
