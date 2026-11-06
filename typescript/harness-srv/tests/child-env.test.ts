/**
 * Unit test: child environment for spawned runs (src/child-env.ts).
 *
 * Pins the fix for the 2026-11-06 attribution mismatch: a tester run launched
 * with `--model opencode/glm-5.3-flash-free` posted records attributed to
 * `opencode/glm-4.5`, because the harness exported only HARNESS_ROLE and
 * HARNESS_JOB_ID and the child inherited a stale NEXUS_AGENT_MODEL from the
 * long-lived server process.
 *
 * The properties under test:
 *   1. A resolved model is exported as NEXUS_AGENT_MODEL (trimmed).
 *   2. An UNRESOLVED model leaves the variable ABSENT even when the parent
 *      environment carries one — inheriting another run's model is worse than
 *      null attribution, and it is the exact bug observed live.
 *   3. Blank / non-string values count as unresolved.
 *   4. HARNESS_ROLE and HARNESS_JOB_ID are always exported, and the rest of
 *      the parent environment (PATH et al.) survives.
 *
 * Usage: npx tsx tests/child-env.test.ts
 */

import { buildChildEnv } from "../src/child-env";

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

const BASE = { PATH: "/usr/bin", HOME: "/home/codex", NEXUS_AGENT_MODEL: "opencode/glm-4.5" };

console.log("== resolved model is exported ==");
{
  const env = buildChildEnv({
    role: "tester",
    jobId: "job-1",
    model: "opencode/glm-5.3-flash-free",
    base: BASE,
  });
  assert(env.NEXUS_AGENT_MODEL === "opencode/glm-5.3-flash-free", "resolved model wins over the inherited value");
  assert(env.HARNESS_ROLE === "tester", "HARNESS_ROLE exported");
  assert(env.HARNESS_JOB_ID === "job-1", "HARNESS_JOB_ID exported");
  assert(env.PATH === "/usr/bin" && env.HOME === "/home/codex", "parent environment preserved");
}

console.log("== stale inherited model is dropped when nothing resolved ==");
{
  const env = buildChildEnv({ role: "tester", jobId: "job-2", model: undefined, base: BASE });
  assert(!("NEXUS_AGENT_MODEL" in env), "unresolved model removes the stale inherited value (the live bug)");
  assert(env.HARNESS_ROLE === "tester" && env.PATH === "/usr/bin", "other vars unaffected");
}

console.log("== blank values count as unresolved ==");
{
  for (const [label, model] of [
    ["empty string", ""],
    ["whitespace only", "   "],
    ["null", null],
  ] as const) {
    const env = buildChildEnv({ role: "engineer", jobId: "job-3", model, base: BASE });
    assert(!("NEXUS_AGENT_MODEL" in env), `${label} leaves the variable absent (no blank attribution)`);
  }
}

console.log("== model is trimmed ==");
{
  const env = buildChildEnv({
    role: "engineer",
    jobId: "job-4",
    model: "  opencode/big-pickle  ",
    base: {},
  });
  assert(env.NEXUS_AGENT_MODEL === "opencode/big-pickle", "surrounding whitespace trimmed");
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
