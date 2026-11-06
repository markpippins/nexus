/**
 * Unit test: the per-run wall-clock budget for failover ladders.
 *
 * The property under test is the one that was missing on 2026-11-06: a run
 * launched with `timeout_ms` must finish the WHOLE ladder inside that
 * window, not give every rung its own copy of it. Job 07469dca was given 30
 * minutes and was still running at 60 because the second attempt received a
 * fresh 30-minute timeout after the first consumed the first 30.
 *
 * Usage: npx tsx tests/run-budget.test.ts
 */

import {
  MIN_ATTEMPT_MS,
  attemptTimeoutMs,
  remainingBudgetMs,
  runDeadlineMs,
  shouldStartAttempt,
  sliceAllowsFirstToken,
} from "../src/run-budget";

let passed = 0;
let failed = 0;

function assert(condition: boolean, message: string): void {
  if (condition) {
    passed++;
    console.log(`  ✓ ${message}`);
  } else {
    failed++;
    console.error(`  ✗ ${message}`);
  }
}

function assertEqual(actual: unknown, expected: unknown, message: string): void {
  assert(actual === expected, `${message} (expected ${String(expected)}, got ${String(actual)})`);
}

const MIN = 60 * 1000;

// ── Test 1: one deadline per run ────────────────────────────────────

console.log("\nTest 1 — the run deadline is fixed once, from the caller's timeout:");
{
  const start = 1_000_000;
  assertEqual(runDeadlineMs(start, 30 * MIN) - start, 30 * MIN, "30-minute timeout → deadline 30 minutes out");
  assertEqual(runDeadlineMs(start, 0), start + MIN_ATTEMPT_MS, "a zero timeout is raised to the 1s floor, not a past deadline");
  assertEqual(runDeadlineMs(start, -5_000), start + MIN_ATTEMPT_MS, "a negative timeout is floored");
  assertEqual(runDeadlineMs(start, Number.NaN), start + MIN_ATTEMPT_MS, "NaN is floored rather than producing NaN deadline");
  assertEqual(runDeadlineMs(start, Number.POSITIVE_INFINITY), start + MIN_ATTEMPT_MS, "non-finite timeouts fall back to the floor");
}

// ── Test 2: remaining budget never goes negative ────────────────────

console.log("\nTest 2 — remaining budget clamps at zero:");
{
  assertEqual(remainingBudgetMs(10_000, 0), 10_000, "full budget before any work");
  assertEqual(remainingBudgetMs(10_000, 4_000), 6_000, "budget shrinks as the clock advances");
  assertEqual(remainingBudgetMs(10_000, 10_000), 0, "exactly at the deadline is 0");
  assertEqual(remainingBudgetMs(10_000, 99_000), 0, "past the deadline is 0, never negative");
}

// ── Test 3: the start/skip boundary ─────────────────────────────────

console.log("\nTest 3 — an attempt starts only with a usable slice:");
{
  assertEqual(shouldStartAttempt(0), false, "no budget left → do not start another rung");
  assertEqual(shouldStartAttempt(MIN_ATTEMPT_MS - 1), false, "999ms → too short to be worth starting");
  assertEqual(shouldStartAttempt(MIN_ATTEMPT_MS), true, "exactly 1s → allowed (matches the route floor)");
  assertEqual(shouldStartAttempt(20 * MIN), true, "20 minutes → obviously allowed");
}

// ── Test 4: the attempt timeout is the remaining budget ─────────────

console.log("\nTest 4 — an attempt receives only what is left:");
{
  assertEqual(attemptTimeoutMs(20 * MIN), 20 * MIN, "20 minutes remaining → 20-minute attempt");
  assertEqual(attemptTimeoutMs(MIN_ATTEMPT_MS), MIN_ATTEMPT_MS, "1s remaining → 1s attempt");
  assertEqual(attemptTimeoutMs(1_500.7), 1_500, "sub-ms is floored, never fractional");
  assertEqual(attemptTimeoutMs(0), MIN_ATTEMPT_MS, "a call with no budget still yields the floor (callers must gate first)");
}

// ── Test 5: the 07469dca regression ─────────────────────────────────

console.log("\nTest 5 — REGRESSION (job 07469dca): a 30-minute run cannot take 60:");
{
  const timeout = 30 * MIN;
  const start = 1_700_000_000_000;
  const deadline = runDeadlineMs(start, timeout);

  // Attempt 1 is the primary; it is abandoned by the supervisor exactly at
  // the deadline (16:03:33 → 16:33:34 in the journal, exit=124).
  const afterPrimary = deadline;
  const remaining = remainingBudgetMs(deadline, afterPrimary);
  assertEqual(remaining, 0, "after the primary burns the whole budget, 0ms remain");
  assert(!shouldStartAttempt(remaining), "the fallback is NOT started — this is the fix");

  // What the old code did: hand the fallback another full timeout, which is
  // how the run reached the 60-minute mark without any timer misbehaving.
  const oldBehaviour = timeout;
  assert(oldBehaviour === 30 * MIN, "the old per-attempt timeout would have run another 30 minutes");
}

// ── Test 6: a healthy failover keeps its remainder ──────────────────

console.log("\nTest 6 — a fast primary failure leaves the rest for the fallback:");
{
  const timeout = 30 * MIN;
  const deadline = runDeadlineMs(0, timeout);
  // Primary dies after 4 minutes for a reason that is not the budget (402).
  const remaining = remainingBudgetMs(deadline, 4 * MIN);
  assertEqual(remaining, 26 * MIN, "26 minutes remain for the fallback");
  assert(shouldStartAttempt(remaining), "the fallback starts");
  assertEqual(attemptTimeoutMs(remaining), 26 * MIN, "and it gets 26 minutes, not another 30");
}

// ── Test 7: total ladder time never exceeds the budget ──────────────

console.log("\nTest 7 — invariant: summed attempt time can never exceed the budget:");
{
  const timeout = 30 * MIN;
  const rungDurations = [0, 5 * MIN, 12 * MIN, 29 * MIN, 30 * MIN, 45 * MIN, 7_000];
  for (const firstAttemptMs of rungDurations) {
    const deadline = runDeadlineMs(0, timeout);
    let now = Math.min(firstAttemptMs, timeout);
    const granted: number[] = [];
    for (let rung = 0; rung < 4; rung++) {
      const left = remainingBudgetMs(deadline, now);
      if (!shouldStartAttempt(left)) break;
      const slice = attemptTimeoutMs(left);
      granted.push(slice);
      now += slice; // worst case: the attempt consumes its whole slice
    }
    const total = granted.reduce((a, b) => a + b, 0);
    assert(
      total <= timeout,
      `first attempt ${firstAttemptMs / MIN}min → ladder total ${total / MIN}min ≤ ${timeout / MIN}min (${granted.length} rung(s) started)`
    );
  }
}

// ── Test 8: a run with no time left starts no rung at all ───────────

console.log("\nTest 8 — an already-expired deadline starts nothing:");
{
  const deadline = runDeadlineMs(0, 5 * MIN);
  const left = remainingBudgetMs(deadline, 5 * MIN + 1);
  assertEqual(left, 0, "no budget remains");
  assert(!shouldStartAttempt(left), "no rung is started, so no first-token guard is paid");
}

// ── Test 9: a budget-shortened attempt says nothing about the model ──

console.log("\nTest 9 — the run budget must not be recorded as a model stall:");
{
  const guard = 120_000; // FIRST_TOKEN_TIMEOUT_MS default
  assertEqual(sliceAllowsFirstToken(1_000, guard), false, "1s slice → the guard fires first, so no verdict on the model");
  assertEqual(sliceAllowsFirstToken(guard - 1, guard), false, "one millisecond short of the allowance → still the budget's fault");
  assertEqual(sliceAllowsFirstToken(guard, guard), true, "exactly the allowance → a stall is the provider's");
  assertEqual(sliceAllowsFirstToken(guard + 1, guard), true, "longer than the allowance → still the provider's");

  // The shape of the live case that started this: POST /run-direct with
  // timeout_ms=800 was clamped to the 1000ms route floor, so one attempt ran
  // for a second, produced no token, and was recorded as a stall — demoting a
  // healthy model in rung-cooldown for ten minutes.
  const deadline = runDeadlineMs(0, 800);
  const slice = attemptTimeoutMs(remainingBudgetMs(deadline, 0));
  assertEqual(slice, 1_000, "the 800ms request became a 1000ms attempt");
  assert(!sliceAllowsFirstToken(slice, guard), "and it cannot reach the allowance, so no stall is recorded");
}

// ── Summary ─────────────────────────────────────────────────────────

console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
if (failed > 0) {
  process.exit(1);
}
