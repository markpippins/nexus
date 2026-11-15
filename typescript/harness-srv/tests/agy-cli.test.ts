/**
 * Unit test: the agy CLI harness path (src/agy-cli.ts + agy stream parsing
 * in src/terminal.ts).
 *
 * Pins the three things a copy-paste from the opencode/codex executors would
 * get wrong, each measured against agy 1.5.9 on 2026-11-15:
 *
 *   1. ARGV — `agy --output-format stream-json --model M
 *      --dangerously-skip-permissions --print PROMPT`. There is no usable
 *      --agent (`agy agent` lists nothing); the prompt travels on argv
 *      because print mode does not read stdin (`--print` eats the next
 *      token, so it goes last).
 *   2. STREAM — assistant text arrives as incremental `text_delta` fragments
 *      on `step_update` events with `step_type: "agent_response"`. A DONE
 *      step still carries its trailing "\n", so only a fragment with real
 *      text marks the first token.
 *   3. FAILURE — one `result` envelope with `status: "ERROR"` and the cause
 *      in `result.error` (also echoed on stderr, exit 1). The full answer on
 *      success lives in `result.response`.
 *
 * Usage: npx tsx tests/agy-cli.test.ts
 */

import { agyArgs, agyModelFromEnv, AGY_DEFAULT_MODEL } from "../src/agy-cli";
import {
  agyErrorMessages,
  agyFinalText,
  agyMarksToStreamMarks,
  agyTurnCompleted,
  detectHarness,
  scanAgyStream,
  scanHarnessStream,
} from "../src/terminal";

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

function eq(actual: unknown, expected: unknown, label: string): void {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (!ok) console.error(`      actual=${JSON.stringify(actual)} expected=${JSON.stringify(expected)}`);
  assert(ok, label);
}

console.log("== agyArgs ==");
{
  eq(
    agyArgs({ prompt: "hi" }),
    ["--output-format", "stream-json", "--model", AGY_DEFAULT_MODEL, "--dangerously-skip-permissions", "--print", "hi"],
    "default argv: stream-json/Flash/passthrough/print-last"
  );
  assert(
    !agyArgs({ prompt: "hi" }).includes("--agent"),
    "no --agent flag (agy has no configured agents; role travels in the prompt)"
  );
  assert(
    !agyArgs({ prompt: "hi" }).includes("--sandbox"),
    "no --sandbox flag (the harness is the containment layer)"
  );
  const argv = agyArgs({ prompt: "do the thing" });
  eq(argv[argv.length - 2], "--print", "--print is second-to-last");
  eq(argv[argv.length - 1], "do the thing", "the prompt is the last token, so it cannot swallow a flag");

  const withModel = agyArgs({ model: "gemini-4-argon-high", prompt: "hi" });
  eq(
    withModel.slice(withModel.indexOf("--model"), withModel.indexOf("--model") + 2),
    ["--model", "gemini-4-argon-high"],
    "resolved model is passed as --model M"
  );
  eq(
    agyArgs({ model: "", prompt: "hi" }).slice(2, 4),
    ["--model", AGY_DEFAULT_MODEL],
    "empty model falls back to Gemini Flash"
  );
  eq(
    agyArgs({ model: null, prompt: "hi" }).slice(2, 4),
    ["--model", AGY_DEFAULT_MODEL],
    "null model falls back to Gemini Flash"
  );

  eq(AGY_DEFAULT_MODEL, "gemini-3.8-flash-high", "the default is Gemini Flash");
  eq(agyModelFromEnv(undefined), AGY_DEFAULT_MODEL, "unset AGY_MODEL keeps Flash");
  eq(agyModelFromEnv("  "), AGY_DEFAULT_MODEL, "blank AGY_MODEL keeps Flash");
  eq(agyModelFromEnv(" gemini-4-hydrogen-low "), "gemini-4-hydrogen-low", "AGY_MODEL override is trimmed and honored");
}

console.log("== scanAgyStream ==");
{
  const stream = [
    `{"event":"init","init":{"model":"gemini-3.8-flash-low","cwd":"/tmp","permission_mode":"always-proceed"}}`,
    `{"event":"step_update","step_update":{"step_index":0,"state":"DONE","step_type":"user_input"}}`,
    `{"event":"step_update","step_update":{"step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"AGY"}}`,
    `{"event":"step_update","step_update":{"step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"_OK"}}`,
    `{"event":"step_update","step_update":{"step_index":1,"state":"DONE","step_type":"agent_response","text_delta":"\\n","duration_seconds":1.7}}`,
    `{"event":"result","result":{"status":"SUCCESS","response":"AGY_OK\\n","duration_seconds":1.7,"num_turns":1}}`,
  ].join("\n");
  const marks = scanAgyStream(stream);

  eq(
    marks.filter((m) => m.kind === "delta").map((m) => m.text),
    ["AGY", "_OK", "\n"],
    "assistant fragments are collected in order, init and user_input skipped"
  );
  eq(
    marks.filter((m) => m.kind === "error").length,
    0,
    "a successful run carries no error marks"
  );
  eq(agyErrorMessages(marks), [], "nothing reaches stderr surfacing on success");
  eq(agyFinalText(marks), "AGY_OK\n", "final text is the SUCCESS response");
  eq(agyTurnCompleted(marks), true, "SUCCESS result is observed");

  eq(scanAgyStream(""), [], "empty stream yields no marks");
  eq(
    scanAgyStream("not json\n{\"broken\"\n").length,
    0,
    "non-JSON and malformed lines are skipped"
  );
  eq(
    scanAgyStream(`{"event":"step_update","step_update":{"step_type":"agent_response"}}`).length,
    0,
    "a delta envelope without text_delta is not a fragment"
  );
}

console.log("== agy failures ==");
{
  // Measured 2026-11-15: unknown model id. The message appears once on
  // stdout (result.error) and verbatim on stderr — the executor's
  // stderr-append must say it once.
  const failed = [
    `{"event":"result","result":{"status":"ERROR","response":"","error":"invalid model selection","finished_reason":"error"}}`,
  ].join("\n");
  const marks = scanAgyStream(failed);
  eq(
    agyErrorMessages(marks),
    ["agy error: invalid model selection"],
    "result.error reaches stderr worded with its source"
  );
  eq(agyFinalText(marks), "", "no response text on a failed run");
  eq(agyTurnCompleted(marks), false, "an ERROR result is not a completed turn");

  eq(
    agyErrorMessages(scanAgyStream([
      `{"event":"result","result":{"status":"ERROR","error":"boom"}}`,
      `{"event":"result","result":{"status":"ERROR","error":"boom"}}`,
    ].join("\n"))),
    ["agy error: boom"],
    "identical repeat errors are collapsed"
  );
  eq(
    scanAgyStream(`{"event":"result","result":{"status":"ERROR"}}`)[0]?.message,
    "agy error: run failed",
    "an ERROR result without text still names the failure"
  );

  eq(
    agyMarksToStreamMarks(scanAgyStream([
      `{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"hi"}}`,
      `{"event":"result","result":{"status":"SUCCESS","response":"hi"}}`,
    ].join("\n"))),
    [{ kind: "step_finish", reason: "stop" }],
    "a SUCCESS result projects to step_finish/stop (deltas are not terminal marks)"
  );
}

console.log("== agy in the shared readers ==");
{
  const agyStdout = [
    `{"event":"init","init":{"model":"gemini-3.8-flash-low"}}`,
    `{"event":"result","result":{"status":"SUCCESS","response":"done"}}`,
  ].join("\n");
  eq(detectHarness(agyStdout), "agy", "event envelopes detect agy");
  eq(
    scanHarnessStream(agyStdout),
    [{ kind: "step_finish", reason: "stop" }],
    "shared reader ends an agy run on a natural stop"
  );
  const agyFailed = `{"event":"result","result":{"status":"ERROR","error":"nope"}}`;
  eq(detectHarness(agyFailed), "agy", "an ERROR-only stream still detects agy");
  eq(
    scanHarnessStream(agyFailed),
    [{ kind: "error", message: "agy error: nope" }],
    "shared reader surfaces an agy failure"
  );
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
