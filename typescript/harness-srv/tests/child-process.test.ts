/**
 * Unit test: the shared child supervision (src/child-process.ts).
 *
 * This module is what the opencode and codex executors both rely on for their
 * deadline, first-token guard and kill path, so its contract is pinned here
 * once rather than only exercised end-to-end through a CLI:
 *
 *   - a run that never produces a first token is abandoned (noFirstToken) and
 *     the child is terminated;
 *   - once a first token is seen the guard stands down, and a short timeout
 *     still marks timedOut;
 *   - an unanswered refusal outranks the guard's own deadline;
 *   - stop() clears every interval so the event loop is not kept armed.
 *
 * Usage: npx tsx tests/child-process.test.ts
 */

import { EventEmitter } from "events";
import { armSupervision } from "../src/child-process";

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

/** Minimal ChildProcess stand-in: records kills, does not really die. */
function stubChild() {
  const emitter = new EventEmitter() as any;
  emitter.killed = [] as string[];
  emitter.pid = 12345;
  emitter.kill = (sig: string) => {
    emitter.killed.push(sig);
    return true;
  };
  return emitter;
}

function wait(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function main(): Promise<void> {
  console.log("== first-token guard ==");
  {
    const child = stubChild();
    const seen = false;
    const { state, stop } = armSupervision({
      child,
      timeoutMs: 60_000,
      firstTokenGuardMs: 50,
      firstTokenSeen: () => seen,
    });
    await wait(1_300); // one supervision tick past the 50ms guard
    stop();
    assert(state.noFirstToken === true, "a silent attempt is abandoned");
    assert(state.timedOut === false, "…and is not reported as a whole-run timeout");
    assert(child.killed.includes("SIGTERM"), "the abandoned child gets SIGTERM");
  }

  console.log("== first token seen ==");
  {
    const child = stubChild();
    const { state, stop } = armSupervision({
      child,
      timeoutMs: 60_000,
      firstTokenGuardMs: 50,
      firstTokenSeen: () => true,
    });
    await wait(1_300);
    stop();
    assert(state.noFirstToken === false, "a speaking attempt is never abandoned");
    assert(child.killed.length === 0, "…and is not killed");
  }

  console.log("== refusal outranks the guard ==");
  {
    const child = stubChild();
    const { state, stop } = armSupervision({
      child,
      timeoutMs: 60_000,
      firstTokenGuardMs: 50,
      firstTokenSeen: () => false,
      refusalIsTerminal: () => true,
    });
    await wait(1_300);
    stop();
    assert(state.refusedAbandon === true, "an unanswered refusal ends the attempt as a refusal");
    assert(state.noFirstToken === false, "…instead of as a first-token stall");
    assert(child.killed.includes("SIGTERM"), "the refused child is terminated");
  }

  console.log("== whole-run deadline ==");
  {
    const child = stubChild();
    const { state, stop } = armSupervision({
      child,
      timeoutMs: 100,
      firstTokenGuardMs: 60_000,
      firstTokenSeen: () => true, // guard disarmed; only the deadline can fire
    });
    await wait(1_300);
    stop();
    assert(state.timedOut === true, "an over-budget attempt is marked timedOut");
    assert(state.noFirstToken === false, "the first-token guard did not fire");
    assert(child.killed.includes("SIGTERM"), "an over-budget child gets SIGTERM");
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
}

main();
