/**
 * Unit test: runaway-watchdog decision (src/watchdog.ts).
 *
 * Pins the rule that replaced "past 15 minutes, kill unless the role has
 * written an agent record" (issue 508195a6, F3 in record 467962a1). Job
 * 591282d5 was a tester killed at 902s while running the guard suite: it was
 * working the whole time and had simply not posted its attestation yet —
 * which for a tester is the normal shape (verify first, write last).
 *
 * The properties below are the whole contract:
 *   1. Below the threshold nothing is a runaway, however quiet.
 *   2. Past the threshold, stream progress keeps a run alive.
 *   3. Past the threshold, a durable record keeps a run alive even with no
 *      stream progress in that tick (a model inside a long tool call).
 *   4. Past the threshold with both channels silent, kill.
 *   5. Progress is strictly-greater, so a stalled counter is not movement.
 *
 * Usage: npx tsx tests/watchdog.test.ts
 */

import { isRunaway, RUNAWAY_THRESHOLD_MS, WATCHDOG_INTERVAL_MS } from "../src/watchdog";

let passed = 0;
let failed = 0;

function assert(cond: boolean, label: string): void {
  if (cond) {
    passed++;
    console.log(`  ✓ ${label}`);
  } else {
    failed++;
    console.error(`  ✗ ${label}`);
  }
}

const base = {
  elapsedMs: RUNAWAY_THRESHOLD_MS,
  thresholdMs: RUNAWAY_THRESHOLD_MS,
  progress: 0,
  lastProgress: 0,
  hasDurableRecord: false,
};

console.log("== cadence ==");
{
  assert(RUNAWAY_THRESHOLD_MS === 15 * 60 * 1000, "threshold is 15 minutes");
  assert(WATCHDOG_INTERVAL_MS === 60_000, "interval is 60s");
}

console.log("== below the threshold ==");
{
  assert(!isRunaway({ ...base, elapsedMs: 0 }), "at zero elapsed: not runaway");
  assert(
    !isRunaway({ ...base, elapsedMs: RUNAWAY_THRESHOLD_MS - 1 }),
    "one ms short of the threshold: not runaway"
  );
}

console.log("== stream progress keeps a run alive ==");
{
  // The 591282d5 shape, fixed: long elapsed, child still producing chunks.
  assert(
    !isRunaway({
      ...base,
      elapsedMs: 10 * WATCHDOG_INTERVAL_MS,
      progress: 4200,
      lastProgress: 4190,
    }),
    "long run with advancing chunks: not runaway"
  );
}

console.log("== a durable record keeps a run alive ==");
{
  // Quiet tick, but the role already wrote something: a model inside a
  // four-minute test run emits no text for onEvent to translate.
  assert(
    !isRunaway({ ...base, progress: 500, lastProgress: 500, hasDurableRecord: true }),
    "silent tick with a record since launch: not runaway"
  );
}

console.log("== both channels silent past the threshold kills ==");
{
  assert(
    isRunaway({ ...base, progress: 500, lastProgress: 500, hasDurableRecord: false }),
    "silent tick, no record: runaway"
  );
  assert(
    isRunaway({ ...base, elapsedMs: RUNAWAY_THRESHOLD_MS }),
    "fires exactly at the threshold"
  );
}

console.log("== a stalled counter is not movement ==");
{
  assert(isRunaway({ ...base, progress: 900, lastProgress: 900 }), "equal counter: runaway");
  assert(isRunaway({ ...base, progress: 899, lastProgress: 900 }), "lower counter: runaway");
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
