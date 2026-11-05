/**
 * Unit test: how a run's END is read (src/terminal.ts).
 *
 *
 * Regression target: harness-srv derived the terminal state from the child
 * exit status alone (`exit 0 → completed`). Job 2b5c34ae (tester, 2026-11-04)
 * exited 0 after 35s with a transcript that stopped mid-task and wrote
 * nothing; the job was reported `completed`/`partial:false`.
 *
 * The streams below are trimmed real envelopes (step_start / tool_use / text /
 * step_finish / error), taken from the captured streams of jobs 2b5c34ae and
 * eb583158 in cascade.events + GET /jobs — field names and nesting are not
 * invented.
 *
 * Usage: npx tsx tests/terminal.test.ts
 */

import {
  classifyLadder,
  classifyTerminal,
  errorMessagesOf,
  harnessEndedAttempt,
  lastStepReason,
  scanOpencodeStream,
} from "../src/terminal";
import type { AttemptOutcome } from "../src/terminal";

let passed = 0;
let failed = 0;

function assert(label: string, condition: boolean, detail?: string): void {
  if (condition) {
    console.log(`  ✓ ${label}`);
    passed++;
  } else {
    console.error(`  ✗ FAIL: ${label}${detail ? ` — ${detail}` : ""}`);
    failed++;
  }
}

// ── Fixtures (real envelope shapes) ────────────────────────────────

const stepStart = (id: string) =>
  JSON.stringify({
    type: "step_start",
    timestamp: 1793832377398,
    sessionID: "ses_e57424e2fffe3ztDlBsmVgk0KO",
    part: { id, messageID: "msg_1a8bdb90b00135W437SDHt3xSp", type: "step-start" },
  });

const stepFinish = (reason: string, output = 81) =>
  JSON.stringify({
    type: "step_finish",
    timestamp: 1793832397207,
    sessionID: "ses_e57424e2fffe3ztDlBsmVgk0KO",
    part: {
      id: "prt_1a8be2993001TNqdMM16LJUnIJ",
      reason,
      type: "step-finish",
      tokens: { total: 7604, input: 7523, output, reasoning: 0 },
      cost: 0,
    },
  });

const text = (t: string) =>
  JSON.stringify({
    type: "text",
    timestamp: 1793832396900,
    sessionID: "ses_e57424e2fffe3ztDlBsmVgk0KO",
    part: { type: "text", text: t, time: { start: 1793832396900, end: 1793832397138 } },
  });

const errorEnvelope = (status: number, message: string) =>
  JSON.stringify({
    type: "error",
    error: { name: "ProviderError", data: { message, statusCode: status } },
  });

const stream = (...lines: string[]) => lines.join("\n") + "\n";

const classify = (stdout: string, exitCode: number | null = 0, over: Partial<Parameters<typeof classifyTerminal>[0]> = {}) =>
  classifyTerminal({
    exitCode,
    marks: scanOpencodeStream(stdout),
    timedOut: false,
    noFirstToken: false,
    interrupted: false,
    ...over,
  });

// ── Scenario: a normal completed turn ──────────────────────────────
console.log("\nnormal completion:");
{
  const out = stream(stepStart("prt_1"), text("Verdict: approved."), stepFinish("stop"));
  const v = classify(out);
  assert("exit 0 + final reason=stop → completed", v.state === "completed", v.state);
  assert("stop reason is reported", lastStepReason(scanOpencodeStream(out)) === "stop");
}

// ── Scenario: the incident (2b5c34ae) ──────────────────────────────
// The model ended its turn early and correctly reported a natural stop. The
// harness cannot know the task was abandoned, so it must NOT pretend the
// process exit status was a judgement — it reports completed + the fact that
// the model stopped itself, which is exactly what this stream proves.
console.log("\nincident 2b5c34ae (exit 0, model stopped mid-task):");
{
  const out = stream(
    stepStart("prt_1"),
    text("Diff review: 4 generated seed files only … Running the parity/v"),
    stepFinish("stop", 81),
  );
  const v = classify(out);
  assert(
    "exit 0 with reason=stop is completed, not invented into a failure",
    v.state === "completed" && v.detail.includes("natural stop"),
    `${v.state}: ${v.detail}`,
  );
  assert(
    "…and the evidence that the model stopped itself is available to the caller",
    lastStepReason(scanOpencodeStream(out)) === "stop",
  );
}

// ── Scenario: provider error envelope with a ZERO exit ─────────────
console.log("\nerror envelope under a zero exit status:");
{
  const out = stream(stepStart("prt_1"), errorEnvelope(429, "rate limit exceeded"));
  const v = classify(out);
  assert("exit 0 + error as the last event → failed", v.state === "failed", v.state);
  assert("the detail names the error", v.detail.includes("429"), v.detail);
  const msgs = errorMessagesOf(scanOpencodeStream(out));
  assert(
    "the error message still reaches stderr callers",
    msgs.length === 1 && msgs[0].includes("rate limit exceeded"),
    JSON.stringify(msgs),
  );
}

// ── Scenario: error, then the run recovers and stops ───────────────
console.log("\nerror recovered by a later stop:");
{
  const out = stream(
    errorEnvelope(503, "upstream unavailable"),
    stepStart("prt_2"),
    text("retried successfully"),
    stepFinish("stop"),
  );
  const v = classify(out);
  assert("last word is the stop → completed", v.state === "completed", v.state);
  assert("the recovered error is still surfaced", errorMessagesOf(scanOpencodeStream(out)).length === 1);
}

// ── Scenario: genuinely truncated answer ───────────────────────────
console.log("\ntoken-limited step:");
{
  const v = classify(stream(stepStart("prt_1"), text("partial answer"), stepFinish("length")));
  assert("reason=length → failed (not completed)", v.state === "failed", v.state);
  assert("detail names the reason", v.detail.includes("reason=length"), v.detail);
  assert(
    "the precise cause stays visible as stop_reason",
    lastStepReason(scanOpencodeStream(stream(stepFinish("length")))) === "length",
  );
}

// ── Precedence: the harness's own actions outrank the stream ───────
console.log("\nprecedence:");
{
  const out = stream(stepStart("prt_1"), stepFinish("stop"));
  assert("interrupted → cancelled", classify(out, 137, { interrupted: true }).state === "cancelled");
  assert("timed out → timed_out", classify(out, 124, { timedOut: true }).state === "timed_out");
  assert("no first token → timed_out", classify(out, 124, { noFirstToken: true }).state === "timed_out");
  assert("non-zero exit → failed", classify(out, 3).state === "failed");
  assert(
    "a truncated stream under a non-zero exit is still failed (exit outranks reason)",
    classify(stream(stepFinish("length")), 2).state === "failed",
  );
}

// ── Unparseable streams keep the old behaviour ────────────────────
console.log("\nunparseable stream:");
{
  const out = "plain text, no JSON envelopes\n{\"broken\":\n[1,2,3]\n";
  const v = classify(out);
  assert("no marks + exit 0 → completed (unchanged behaviour)", v.state === "completed", v.state);
  assert("no stop reason is invented", lastStepReason(scanOpencodeStream(out)) === null);
  assert("empty stdout is safe", classify("").state === "completed");
}

// ── The failover ladder decides the RUN's state ───────────────────
//
// Regression target (2026-11-05, jobs 7e2f58e4 / 87ae9ae3): the primary was
// abandoned for emitting no first token, the fallback exited 1 two seconds
// later, and the job was reported `timed_out` with "run exceeded its timeout".
// Nothing had timed out; every model had failed. The run's exit code belongs
// to the LAST attempt, so it cannot answer "did any attempt do the work?".
console.log("\nladder: exhausted attempts are a failure, not a timeout:");
{
  const abandoned: AttemptOutcome = {
    source: "primary",
    model: "opencode/x-preview-f-free",
    verdict: { state: "timed_out", detail: "no first token before the guard deadline" },
  };
  const errored: AttemptOutcome = {
    source: "fallback",
    model: "openrouter/stealth/ox-alpha",
    verdict: { state: "failed", detail: "opencode exited 1" },
  };
  const v = classifyLadder([abandoned, errored]);
  assert("abandoned primary + errored fallback → failed (not timed_out)", v.state === "failed", v.state);
  assert("the detail names both attempts", /opencode\/x-preview-f-free/.test(v.detail) && /ox-alpha/.test(v.detail), v.detail);
  assert("the detail keeps each attempt's own reading", /no first token/.test(v.detail) && /exited 1/.test(v.detail), v.detail);

  const bothTimedOut = classifyLadder([
    abandoned,
    { ...errored, verdict: { state: "timed_out", detail: "run exceeded its timeout" } },
  ]);
  assert("every attempt timing out is still a failed LADDER", bothTimedOut.state === "failed", bothTimedOut.state);

  const empty = classifyLadder([]);
  assert("an empty ladder fails rather than completing", empty.state === "failed", empty.state);

  const recovered = classifyLadder([
    errored,
    { source: "fallback", model: "opencode/glm-5.3-flash-free", verdict: { state: "completed", detail: "ok" } },
  ]);
  assert("a completing retry decides the run", recovered.state === "completed", recovered.state);
  assert("a winning retry says so", /attempt 2 of 2/.test(recovered.detail), recovered.detail);

  const single = classifyLadder([
    { source: "primary", model: "opencode/big-pickle", verdict: { state: "completed", detail: "natural stop" } },
  ]);
  assert("a single completing attempt passes its own detail through", single.detail === "natural stop", single.detail);

  const cancelled = classifyLadder([
    { ...abandoned, verdict: { state: "cancelled", detail: "interrupted" } },
    errored,
  ]);
  assert("an interrupted attempt is not reported as a model failure", cancelled.state === "cancelled", cancelled.state);
}

// ── harnessEndedAttempt reads one attempt's own stderr ────────────
console.log("\nharness abandonments are per attempt:");
{
  assert("exit 124 is an abandonment", harnessEndedAttempt(124, "") === true);
  assert("the first-token marker is an abandonment", harnessEndedAttempt(1, "opencode produced no first token within 120000ms — abandoning") === true);
  assert("a plain exit 1 is not", harnessEndedAttempt(1, "opencode error: Unexpected server error") === false);
  assert("a provider error is not", harnessEndedAttempt(1, "opencode error (403): model access is disabled") === false);
}

// ── Summary ───────────────────────────────────────────────────────
console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
