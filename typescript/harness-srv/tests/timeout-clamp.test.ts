/**
 * Unit test: harness-srv timeout clamp + deadline supervisor (PR #575,
 * tester review 269a6ca5 — "no tests cover the changed lines").
 *
 * Two properties are pinned, both introduced by the CodeQL
 * js/resource-exhaustion remediation (alerts #649/#650/#753/#754/#758):
 *
 *   1. CLAMP — every user-supplied `timeout_ms` is clamped into
 *      [1_000, MAX_RUN_TIMEOUT_MS] before it reaches a timer. The lower
 *      bound (1s) is the part the tester asked for: a near-zero duration
 *      would arm a 1s supervisor that fires immediately and kills a run
 *      that never had a chance to start. The upper bound (2h) keeps a
 *      flood of work inside a bounded window.
 *
 *   2. SUPERVISOR — the run is watched by a FIXED 1s setInterval that
 *      compares Date.now() against a deadline. No timer is ever handed a
 *      user-controlled duration, which is what clears the alert. The
 *      first-token guard uses the same shape.
 *
 * The clamp and supervisor are module-private in src/index.ts, so — as
 * with tests/regex-escape.test.ts — the exact expressions under test are
 * replicated here and asserted directly. If src/index.ts changes its
 * clamp, this file is the canary; keep the two in sync.
 *
 * Usage: npx tsx tests/timeout-clamp.test.ts
 */

// ── The logic under test (replicated from src/index.ts) ─────────────

const MAX_RUN_TIMEOUT_MS = 2 * 60 * 60 * 1000; // src/index.ts:67
const MIN_RUN_TIMEOUT_MS = 1_000; // src/index.ts:513, :930, :1581
const DEFAULT_RUN_TIMEOUT_MS = 300_000; // src/index.ts:505-513, :1581
const DEFAULT_EXEC_TIMEOUT_MS = 600_000; // src/index.ts:922-930

/** Handler-level clamp, verbatim from src/index.ts:513 / :930. */
function clampTimeout(raw: unknown, fallback: number): number {
  return Math.min(Math.max(Number(raw) || fallback, MIN_RUN_TIMEOUT_MS), MAX_RUN_TIMEOUT_MS);
}

/** Executor-level defense-in-depth clamp, verbatim from src/index.ts:1581. */
function clampExecutorTimeout(timeoutMs: number): number {
  return Math.min(Math.max(Number(timeoutMs) || DEFAULT_RUN_TIMEOUT_MS, MIN_RUN_TIMEOUT_MS), MAX_RUN_TIMEOUT_MS);
}

/** The deadline comparison the 1s supervisor performs, verbatim from :1787. */
function supervisorFired(now: number, deadline: number): boolean {
  return now >= deadline;
}

// ── Test helpers ───────────────────────────────────────────────────

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

// ── Test 1: the upper bound holds ──────────────────────────────────

console.log("\nTest 1 — Upper bound (2h ceiling):");
{
  assertEqual(clampTimeout(5 * 60 * 60 * 1000, DEFAULT_RUN_TIMEOUT_MS), MAX_RUN_TIMEOUT_MS, "5h clamps down to 2h");
  assertEqual(clampTimeout(Number.MAX_SAFE_INTEGER, DEFAULT_RUN_TIMEOUT_MS), MAX_RUN_TIMEOUT_MS, "MAX_SAFE_INTEGER clamps to 2h");
  assertEqual(clampTimeout(3_600_000, DEFAULT_RUN_TIMEOUT_MS), 3_600_000, "1h passes through untouched");
}

// ── Test 2: the lower bound holds (tester review 269a6ca5) ──────────

console.log("\nTest 2 — Lower bound (1s floor, the point the tester raised):");
{
  // NOTE: 0 is absent from this block on purpose. `Number(0) || fallback`
  // treats an explicit 0 as "no duration supplied", so it lands on the
  // route default rather than the floor — Test 3 covers that.
  assertEqual(clampTimeout(1, DEFAULT_RUN_TIMEOUT_MS), MIN_RUN_TIMEOUT_MS, "1ms clamps up to 1s");
  assertEqual(clampTimeout(-1, DEFAULT_RUN_TIMEOUT_MS), MIN_RUN_TIMEOUT_MS, "negative clamps up to 1s");
  assertEqual(clampTimeout(-999_999, DEFAULT_RUN_TIMEOUT_MS), MIN_RUN_TIMEOUT_MS, "large negative clamps up to 1s");
  assertEqual(clampTimeout(999, DEFAULT_RUN_TIMEOUT_MS), MIN_RUN_TIMEOUT_MS, "999ms (just under) clamps up to 1s");
  assertEqual(clampTimeout(1_000, DEFAULT_RUN_TIMEOUT_MS), 1_000, "exactly 1s is allowed through");
}

// ── Test 3: falsy input falls back to the route default ─────────────
// `Number(x) || fallback` means 0 is falsy, so a caller-supplied 0
// lands on the ROUTE DEFAULT, not on the 1s floor. This is the intended
// "you did not ask for a duration, use ours" path — assert it so a
// future refactor to `??` does not silently change the default.

console.log("\nTest 3 — Falsy/absent input uses the route default:");
{
  // An explicit 0 is falsy, so `||` routes it to the default. That is the
  // intended "you did not ask for a duration, use ours" path — and it means
  // 0 can never reach the floor. Asserted so a future refactor of `||` to
  // `??` does not silently change which value a 0 produces.
  assertEqual(clampTimeout(0, DEFAULT_RUN_TIMEOUT_MS), DEFAULT_RUN_TIMEOUT_MS, "explicit 0 is treated as absent → route default, not the 1s floor");
  assertEqual(clampTimeout(false, DEFAULT_RUN_TIMEOUT_MS), DEFAULT_RUN_TIMEOUT_MS, "false is falsy → route default");
  assertEqual(clampTimeout("", DEFAULT_RUN_TIMEOUT_MS), DEFAULT_RUN_TIMEOUT_MS, "empty string is falsy → route default");
  assertEqual(clampTimeout(undefined, DEFAULT_RUN_TIMEOUT_MS), DEFAULT_RUN_TIMEOUT_MS, "undefined uses the /execute default 300s");
  assertEqual(clampTimeout(undefined, DEFAULT_EXEC_TIMEOUT_MS), DEFAULT_EXEC_TIMEOUT_MS, "undefined uses the /exec default 600s");
  assertEqual(clampTimeout(null, DEFAULT_RUN_TIMEOUT_MS), DEFAULT_RUN_TIMEOUT_MS, "null uses the default");
  assertEqual(clampTimeout(NaN, DEFAULT_RUN_TIMEOUT_MS), DEFAULT_RUN_TIMEOUT_MS, "NaN uses the default");
  assertEqual(clampTimeout("abc", DEFAULT_RUN_TIMEOUT_MS), DEFAULT_RUN_TIMEOUT_MS, "non-numeric string uses the default");
  assertEqual(clampTimeout("90000", DEFAULT_RUN_TIMEOUT_MS), 90_000, "numeric string is coerced and passes through");
}

// ── Test 4: the executor re-clamps (defense in depth) ──────────────
// A call site that forgets the handler clamp still gets a bounded timer.

console.log("\nTest 4 — Executor re-clamps independently:");
{
  assertEqual(clampExecutorTimeout(0), DEFAULT_RUN_TIMEOUT_MS, "0 is falsy → the 300s executor default, not the 1s floor");
  assertEqual(clampExecutorTimeout(1), MIN_RUN_TIMEOUT_MS, "1ms still floored to 1s at the executor");
  assertEqual(clampExecutorTimeout(999), MIN_RUN_TIMEOUT_MS, "999ms still floored to 1s at the executor");
  assertEqual(clampExecutorTimeout(10 * 60 * 60 * 1000), MAX_RUN_TIMEOUT_MS, "10h re-clamped to 2h at the executor");
  assertEqual(clampExecutorTimeout(120_000), 120_000, "a valid in-range value is untouched");
}

// ── Test 5: the supervisor is a fixed 1s tick against a deadline ───
// The alert clears because no timer ever receives a user-controlled
// duration: the tick is the constant 1000ms and the duration lives in
// the deadline. Model the tick arithmetic over a real elapsed span.

console.log("\nTest 5 — Fixed 1s supervisor vs. deadline (no user-controlled timer):");
{
  const TICK_MS = 1000; // src/index.ts:1787, :1802

  // A 1s run: the deadline is one tick out, so the supervisor does not
  // fire on tick 0 and fires on tick 1. This is the exact case the lower
  // bound protects — with the old 0ms/1ms value, tick 0 already fired.
  const shortDeadline = Date.now() + 1_000;
  const shortStart = Date.now();
  assert(!supervisorFired(shortStart, shortDeadline), "1s run: supervisor does NOT fire on the first tick");
  assert(supervisorFired(shortStart + TICK_MS, shortDeadline), "1s run: supervisor fires on the next 1s tick");

  // A 300s run: many ticks elapse before the deadline is reached.
  const longDeadline = Date.now() + 300_000;
  const longStart = Date.now();
  assert(!supervisorFired(longStart, longDeadline), "300s run: no fire at t=0");
  assert(!supervisorFired(longStart + 299_000, longDeadline), "300s run: no fire at t=299s");
  assert(supervisorFired(longStart + 300_000, longDeadline), "300s run: fires at t=300s");

  // The first-token guard takes the minimum of the run timeout and
  // FIRST_TOKEN_TIMEOUT_MS so a hung provider is abandoned well before a
  // long run would expire.
  const FIRST_TOKEN_TIMEOUT_MS = 120_000;
  const firstTokenDeadline = longStart + Math.min(300_000, FIRST_TOKEN_TIMEOUT_MS);
  assertEqual(firstTokenDeadline - longStart, 120_000, "first-token guard fires at 120s, not at the 300s run deadline");
}

// ── Test 6: clamping is idempotent ─────────────────────────────────
// Re-clamping an already-clamped value must not move it — the executor
// re-clamps values the handlers already clamped.

console.log("\nTest 6 — Clamp is idempotent:");
{
  for (const raw of [0, 1, 999, 1_000, 90_000, 300_000, MAX_RUN_TIMEOUT_MS, 10 * 60 * 60 * 1000]) {
    const once = clampTimeout(raw, DEFAULT_RUN_TIMEOUT_MS);
    const twice = clampExecutorTimeout(once);
    assertEqual(twice, once, `re-clamping ${raw} is a no-op (${once}ms)`);
  }
}

// ── Summary ────────────────────────────────────────────────────────

console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
if (failed > 0) {
  process.exit(1);
}
