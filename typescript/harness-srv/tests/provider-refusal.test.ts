/**
 * Unit test: provider refusals fail fast (src/provider-refusal.ts).
 *
 * The defect (record b42b7f64): opencode/big-pickle answered "Free usage limit
 * reached" as an error envelope in ~400ms and then kept retrying. The harness
 * waited the full 120s first-token guard and reported "no first token" — 120s
 * of budget spent, and the cause misreported as a hung model.
 *
 * Usage: npx tsx tests/provider-refusal.test.ts
 */

import {
  DEFAULT_REFUSAL_GRACE_MS,
  noteRefusal,
  refusalAbandonMessage,
  refusalGraceMsFromEnv,
  refusalIsTerminal,
} from "../src/provider-refusal";
import { classifyTerminal, harnessEndedAttempt } from "../src/terminal";

let passed = 0;
let failed = 0;

function check(name: string, cond: boolean, detail?: unknown) {
  if (cond) {
    passed++;
  } else {
    failed++;
    console.error(`FAIL ${name}${detail === undefined ? "" : ` — ${JSON.stringify(detail)}`}`);
  }
}

const LIMIT = "opencode error (429): Free usage limit reached";

// ── grace configuration ────────────────────────────────────────────
check("unset grace uses the default", refusalGraceMsFromEnv(undefined) === DEFAULT_REFUSAL_GRACE_MS);
check("empty grace uses the default", refusalGraceMsFromEnv("  ") === DEFAULT_REFUSAL_GRACE_MS);
check("garbage grace uses the default", refusalGraceMsFromEnv("soon") === DEFAULT_REFUSAL_GRACE_MS);
check("negative grace uses the default", refusalGraceMsFromEnv("-1") === DEFAULT_REFUSAL_GRACE_MS);
check("zero grace is honored (immediate at next tick)", refusalGraceMsFromEnv("0") === 0);
check("explicit grace is honored", refusalGraceMsFromEnv("2500") === 2500);

// ── the watch ──────────────────────────────────────────────────────
const watch = noteRefusal(null, LIMIT, 1_000);
check("the first refusal is recorded", watch.message === LIMIT && watch.firstSeenAt === 1_000);

// A retrying provider must not keep pushing its own deadline out.
const repeated = noteRefusal(watch, "opencode error (429): still limited", 4_000);
check("later refusals keep the first message", repeated.message === LIMIT);
check("later refusals keep the first timestamp", repeated.firstSeenAt === 1_000);

check("no refusal is never terminal", refusalIsTerminal(null, 999_999, 5_000) === false);
check("refusal is not terminal before the grace window", refusalIsTerminal(watch, 1_000 + 4_999, 5_000) === false);
check("refusal is terminal at the grace boundary", refusalIsTerminal(watch, 6_000, 5_000) === true);
check("zero grace is terminal immediately", refusalIsTerminal(watch, 1_000, 0) === true);

// ── how the abandonment is read ────────────────────────────────────
const message = refusalAbandonMessage(watch, 6_000);

check("the message carries the provider's own words", message.includes("Free usage limit reached"), message);
check("the message names the wait", message.includes("5000ms"), message);
check(
  "the message does not use the stall marker",
  !/no first token/i.test(message),
  message,
);
check(
  "the message does not use the timeout marker",
  !/timeout after/i.test(message),
  message,
);

// The two markers above are what terminal.ts uses to decide the harness ended
// the attempt. A refusal must NOT look like one, or it would be recorded as a
// timeout and would demote the rung for ten minutes.
check("a refusal is not a harness-ended attempt", harnessEndedAttempt(1, message) === false);

const verdict = classifyTerminal({
  exitCode: 1,
  marks: [],
  timedOut: false,
  noFirstToken: false,
  interrupted: false,
});
check("a refused attempt classifies as failed, not timed_out", verdict.state === "failed", verdict);

// Contrast: the old path — guard abandonment — still reads as a timeout.
const stall = "opencode produced no first token within 120000ms — abandoning";
check("the stall marker still indicates a harness-ended attempt", harnessEndedAttempt(124, stall) === true);

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
