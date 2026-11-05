/**
 * Runaway watchdog decision (T16 guardrail, 1285 remediation slice 2).
 *
 * Extracted from `startWatchdog()` in app.ts so the rule is testable without
 * timers or a live nebula, the same way admission.ts and terminal.ts were.
 *
 * ── Why this is a module and not an `if` inside the interval ──────────────
 * Until 2026-11-05 the rule past the threshold was: kill unless the role has
 * written an AGENT RECORD since launch. Work in progress was invisible to it —
 * stdout, tool calls, file reads, pytest runs. For a tester that is the wrong
 * shape entirely: verifying for ten or fifteen minutes and posting the
 * attestation at the end IS the normal shape, so a working run and a hung one
 * looked identical. Job 591282d5 was killed at 902s mid-verification (issue
 * 508195a6); two later runs escaped only because each happened to write
 * something after minute 6 and minute 11.
 *
 * The fix is a second, independent liveness signal: the job's own stream
 * advancing between watchdog ticks, counted in RAW chunks rather than in
 * translated text. That distinction matters — onEvent only fires for non-empty
 * text/reasoning, so a model sitting inside a four-minute test run emits
 * nothing it would translate while being entirely alive. Kill only when BOTH
 * channels are silent for the whole threshold: a hang emits nothing on either,
 * while a busy agent emits stream traffic even with nothing to report yet.
 *
 * ── What this deliberately does NOT do ────────────────────────────────────
 * It does not try to distinguish "working" from "spinning" by looking at what
 * the events SAY. A model looping over the same failing command still emits
 * events, and calling that runaway is a judgement about progress, not a
 * mechanical fact. Wall-clock cost control stays with the caller's
 * `timeout_ms` (clamped, see timeout-clamp.test.ts); this guard exists to stop
 * a process that has stopped, not to cap an expensive one.
 */

/** Kill a session that has been silent on every channel for this long. */
export const RUNAWAY_THRESHOLD_MS = 15 * 60 * 1000;

/** How often the decision is re-evaluated. */
export const WATCHDOG_INTERVAL_MS = 60_000;

export interface RunawaySignals {
  /** Milliseconds since the run started. */
  elapsedMs: number;
  /** Threshold the caller considers a runaway. */
  thresholdMs: number;
  /** Progress units now (raw stream chunks seen by the child). */
  progress: number;
  /** Progress units observed at the previous tick. */
  lastProgress: number;
  /** Whether the role has written an agent record since launch. */
  hasDurableRecord: boolean;
}

/**
 * True only when the run has outlived the threshold AND nothing has moved on
 * either channel. Below the threshold nothing is a runaway; above it, any
 * activity at all is enough to keep the run alive.
 */
export function isRunaway(s: RunawaySignals): boolean {
  if (s.elapsedMs < s.thresholdMs) return false;
  if (s.progress > s.lastProgress) return false;
  if (s.hasDurableRecord) return false;
  return true;
}
