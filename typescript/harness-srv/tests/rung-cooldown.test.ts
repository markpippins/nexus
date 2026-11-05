/**
 * Unit test: the failover ladder's rung cooldown (src/rung-cooldown.ts).
 *
 * The property under test is the one the 2026-11-05 attestation burst needed:
 * when a rung is abandoned for producing no first token, the NEXT job tries it
 * last instead of first — while no rung is ever dropped, and the note expires.
 *
 * Timings that motivated it (jobs a706774a / ca0724f9): glm and big-pickle
 * each ate the 120 s no-first-token guard, then the job completed on
 * openrouter in ~40 s. Four jobs in a row meant four times the same 240 s.
 *
 * Usage: npx tsx tests/rung-cooldown.test.ts
 */

import {
  DEFAULT_RUNG_COOLDOWN_MS,
  cooldownMsFromEnv,
  isCooling,
  noteStall,
  orderAttempts,
  type StallMap,
} from "../src/rung-cooldown";

let passed = 0;
let failed = 0;

function assert(label: string, condition: boolean, detail?: string): void {
  if (condition) {
    passed += 1;
    console.log(`  PASS ${label}`);
  } else {
    failed += 1;
    console.error(`  FAIL ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

function assertEqual<T>(label: string, actual: T, expected: T): void {
  assert(
    label,
    JSON.stringify(actual) === JSON.stringify(expected),
    `expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
  );
}

const ladder = [
  { model: "opencode/glm-5.3-flash-free", source: "primary" as const },
  { model: "opencode/big-pickle", source: "fallback" as const },
  { model: "openrouter/openai/gpt-oss-120b", source: "fallback" as const },
];

const models = (attempts: Array<{ model: string }>): string[] => attempts.map((a) => a.model);

// ── Test 1: no stalls means configured priority is untouched ────────
console.log("\nTest 1 — a clean ladder keeps its configured order:");
{
  const stalls: StallMap = new Map();
  assertEqual(
    "no stall → identical order",
    models(orderAttempts(ladder, stalls, 1_000_000)),
    models(ladder),
  );
  assert("no stall → the result is a copy, not the same array", orderAttempts(ladder, stalls, 1_000_000) !== ladder);
}

// ── Test 2: a stalled rung moves to the back, the rest keep order ───
console.log("\nTest 2 — a stalled rung is tried last:");
{
  const stalls: StallMap = new Map();
  const now = 10_000_000;
  noteStall(stalls, "opencode/glm-5.3-flash-free", now);
  assertEqual(
    "glm stalled → big-pickle, openrouter, glm",
    models(orderAttempts(ladder, stalls, now + 1_000)),
    ["opencode/big-pickle", "openrouter/openai/gpt-oss-120b", "opencode/glm-5.3-flash-free"],
  );
}

// ── Test 3: several stalled rungs keep their relative order ─────────
console.log("\nTest 3 — two stalled rungs keep their relative order:");
{
  const stalls: StallMap = new Map();
  const now = 10_000_000;
  noteStall(stalls, "opencode/glm-5.3-flash-free", now);
  noteStall(stalls, "opencode/big-pickle", now);
  assertEqual(
    "both free rungs stalled → openrouter first",
    models(orderAttempts(ladder, stalls, now + 1_000)),
    ["openrouter/openai/gpt-oss-120b", "opencode/glm-5.3-flash-free", "opencode/big-pickle"],
  );
}

// ── Test 4: the note expires ────────────────────────────────────────
console.log("\nTest 4 — a stall note expires after the cooldown:");
{
  const stalls: StallMap = new Map();
  const now = 10_000_000;
  noteStall(stalls, "opencode/glm-5.3-flash-free", now);
  assertEqual(
    "still cooling 1 ms before the deadline",
    models(orderAttempts(ladder, stalls, now + DEFAULT_RUNG_COOLDOWN_MS - 1)),
    ["opencode/big-pickle", "openrouter/openai/gpt-oss-120b", "opencode/glm-5.3-flash-free"],
  );
  assertEqual(
    "exactly at the deadline the rung is back in place",
    models(orderAttempts(ladder, stalls, now + DEFAULT_RUNG_COOLDOWN_MS)),
    models(ladder),
  );
  assert("the expired note is pruned, not merely ignored", !stalls.has("opencode/glm-5.3-flash-free"));
}

// ── Test 5: 0 (and garbage) handling ────────────────────────────────
console.log("\nTest 5 — the cooldown can be turned off, and a bad value does not:");
{
  const stalls: StallMap = new Map();
  const now = 10_000_000;
  noteStall(stalls, "opencode/glm-5.3-flash-free", now);
  assertEqual(
    "RUNG_COOLDOWN_MS=0 disables reordering",
    models(orderAttempts(ladder, stalls, now, 0)),
    models(ladder),
  );
  assertEqual("absent env → default", cooldownMsFromEnv(undefined), DEFAULT_RUNG_COOLDOWN_MS);
  assertEqual("empty env → default", cooldownMsFromEnv("  "), DEFAULT_RUNG_COOLDOWN_MS);
  assertEqual("garbage env → default, not off", cooldownMsFromEnv("ten minutes"), DEFAULT_RUNG_COOLDOWN_MS);
  assertEqual("negative env → default", cooldownMsFromEnv("-5"), DEFAULT_RUNG_COOLDOWN_MS);
  assertEqual("explicit 0 is honoured", cooldownMsFromEnv("0"), 0);
  assertEqual("explicit value is honoured", cooldownMsFromEnv("30000"), 30_000);
}

// ── Test 6: availability is never traded away ───────────────────────
console.log("\nTest 6 — no rung is ever dropped, even when all of them stalled:");
{
  const stalls: StallMap = new Map();
  const now = 10_000_000;
  for (const rung of ladder) noteStall(stalls, rung.model, now);
  const ordered = orderAttempts(ladder, stalls, now + 1_000);
  assertEqual("all cooling → configured order (nothing better to try)", models(ordered), models(ladder));
  assertEqual("every attempt survives", ordered.length, ladder.length);

  // Drop the note on big-pickle so only glm is cooling: a two-rung ladder
  // must still keep both rungs and put the cooling one last.
  const two = ladder.slice(0, 2);
  const onlyGlmStalled: StallMap = new Map();
  noteStall(onlyGlmStalled, "opencode/glm-5.3-flash-free", now);
  const orderedTwo = orderAttempts(two, onlyGlmStalled, now + 1_000);
  assertEqual("a ladder of two keeps both", orderedTwo.length, 2);
  assertEqual("and puts the stalled one last", models(orderedTwo), ["opencode/big-pickle", "opencode/glm-5.3-flash-free"]);
}

// ── Test 7: the key is the model id ─────────────────────────────────
console.log("\nTest 7 — only the stalled model moves:");
{
  const stalls: StallMap = new Map();
  noteStall(stalls, "opencode/big-pickle", 10_000_000);
  assertEqual(
    "glm unaffected, big-pickle last",
    models(orderAttempts(ladder, stalls, 10_000_500)),
    ["opencode/glm-5.3-flash-free", "openrouter/openai/gpt-oss-120b", "opencode/big-pickle"],
  );
  const swaps: StallMap = new Map();
  noteStall(swaps, "opencode/glm-5.3-flash-free", 900);
  noteStall(swaps, "opencode/glm-5.3-flash-free", 10_000_000);
  assertEqual(
    "a later stall replaces the earlier timestamp",
    models(orderAttempts(ladder, swaps, 10_000_500)),
    ["opencode/big-pickle", "openrouter/openai/gpt-oss-120b", "opencode/glm-5.3-flash-free"],
  );
}

// ── Test 8: defensive edges ─────────────────────────────────────────
console.log("\nTest 8 — defensive edges:");
{
  const stalls: StallMap = new Map();
  noteStall(stalls, null, 1);
  noteStall(stalls, undefined, 1);
  noteStall(stalls, "", 1);
  assertEqual("a run with no model is never noted", stalls.size, 0);
  assert("an unknown model is not cooling", !isCooling(stalls, "opencode/nope", 2, DEFAULT_RUNG_COOLDOWN_MS));
  assertEqual("an empty ladder stays empty", orderAttempts([], stalls, 1), []);
  assertEqual(
    "a single-rung ladder is returned as-is",
    models(orderAttempts([{ model: "opencode/glm-5.3-flash-free" }], (noteStall(stalls, "opencode/glm-5.3-flash-free", 5), stalls), 6)),
    ["opencode/glm-5.3-flash-free"],
  );
  assertEqual("the input array is not mutated", models(ladder), [
    "opencode/glm-5.3-flash-free",
    "opencode/big-pickle",
    "openrouter/openai/gpt-oss-120b",
  ]);
}

// ── Summary ────────────────────────────────────────────────────────
console.log(`\n=== Results: ${passed} passed, ${failed} failed ===\n`);
if (failed > 0) {
  process.exit(1);
}
