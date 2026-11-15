/**
 * Unit test: the codex CLI harness path (src/codex-cli.ts + codex stream
 * parsing in src/terminal.ts).
 *
 * Pins the three things that a copy-paste from the opencode executor would
 * get wrong, each measured against codex-cli 0.169.0 on 2026-11-15:
 *
 *   1. ARGV — `codex exec --json --skip-git-repo-check --cd DIR [--model M]
 *      --sandbox MODE [--ephemeral] -`. There is no --agent and no --format;
 *      the prompt is read from stdin via the trailing `-`.
 *   2. STREAM — assistant text arrives as whole `item.completed` items of
 *      type agent_message, and an EMPTY agent_message arrives before the
 *      model works. It must not count as a first token, or the guard that
 *      catches a hung provider is disarmed for the whole run.
 *   3. ADVICE vs FAILURE — `item.completed` with item.type "error" carries
 *      config warnings on every run ("Codex is ignoring N unrecognized
 *      configuration settings"). Treating those as refusals would abandon
 *      healthy runs after the grace window, so only `error` and
 *      `turn.failed` may surface on stderr or be watched.
 *
 * Usage: npx tsx tests/codex-cli.test.ts
 */

import { codexArgs, codexSandboxFromEnv, CODEX_DEFAULT_SANDBOX } from "../src/codex-cli";
import {
  codexErrorMessages,
  codexFinalText,
  codexRefusalMessage,
  codexTurnCompleted,
  codexWarningMessage,
  scanCodexStream,
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

console.log("== codexArgs ==");
{
  eq(
    codexArgs({ workDir: "/tmp/wd" }),
    ["exec", "--json", "--skip-git-repo-check", "--cd", "/tmp/wd", "--sandbox", CODEX_DEFAULT_SANDBOX, "--ephemeral", "-"],
    "default argv: exec/json/cd/sandbox/ephemeral/stdin sentinel"
  );
  assert(
    !codexArgs({ workDir: "/tmp/wd" }).includes("--agent"),
    "no --agent flag (codex has none; role travels in the prompt)"
  );
  assert(
    !codexArgs({ workDir: "/tmp/wd" }).includes("--format"),
    "no --format flag (JSONL comes from --json)"
  );

  const withModel = codexArgs({ workDir: "/tmp/wd", model: "gpt-5.4" });
  eq(
    withModel.slice(withModel.indexOf("--model"), withModel.indexOf("--model") + 2),
    ["--model", "gpt-5.4"],
    "resolved model is passed as --model M"
  );
  assert(
    !codexArgs({ workDir: "/tmp/wd", model: "" }).includes("--model"),
    "empty model is omitted, not forwarded as --model ''"
  );
  assert(
    !codexArgs({ workDir: "/tmp/wd", model: null }).includes("--model"),
    "null model is omitted"
  );

  const persistent = codexArgs({ workDir: "/tmp/wd", persistSession: true });
  assert(!persistent.includes("--ephemeral"), "persistSession=true keeps the session on disk");
  eq(persistent[persistent.length - 1], "-", "prompt-from-stdin sentinel stays last");

  eq(
    codexArgs({ workDir: "/tmp/wd", sandbox: "workspace-write" }).slice(-4, -2),
    ["--sandbox", "workspace-write"],
    "explicit sandbox mode is honored"
  );
}

console.log("== codexSandboxFromEnv ==");
{
  eq(codexSandboxFromEnv("read-only"), "read-only", "valid read-only accepted");
  eq(codexSandboxFromEnv("workspace-write"), "workspace-write", "valid workspace-write accepted");
  eq(codexSandboxFromEnv("danger-full-access"), "danger-full-access", "valid danger-full-access accepted");
  eq(codexSandboxFromEnv("yolo"), CODEX_DEFAULT_SANDBOX, "typo falls back to default instead of being forwarded");
  eq(codexSandboxFromEnv(undefined), CODEX_DEFAULT_SANDBOX, "unset falls back to default");
  eq(codexSandboxFromEnv(""), CODEX_DEFAULT_SANDBOX, "empty falls back to default");
}

console.log("== scanCodexStream ==");
{
  const stream = [
    `{"type":"thread.started","thread_id":"t-1"}`,
    `{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Codex is ignoring 1 unrecognized configuration setting."}}`,
    `{"type":"turn.started"}`,
    `{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":""}}`,
    `{"type":"item.started","item":{"id":"item_3","type":"command_execution","command":"/bin/bash -lc 'echo hi'","aggregated_output":"","exit_code":null}}`,
    `{"type":"item.completed","item":{"id":"item_3","type":"command_execution","command":"/bin/bash -lc 'echo hi'","aggregated_output":"hi\\n","exit_code":0}}`,
    `{"type":"item.completed","item":{"id":"item_4","type":"agent_message","text":"first answer"}}`,
    `{"type":"item.completed","item":{"id":"item_5","type":"agent_message","text":"second answer"}}`,
    `{"type":"turn.completed","usage":{"input_tokens":1}}`,
  ].join("\n");
  const marks = scanCodexStream(stream);

  eq(
    marks.filter((m) => m.kind === "message").map((m) => m.text),
    ["first answer", "second answer"],
    "assistant text is collected, empty agent_message dropped"
  );
  eq(
    marks.filter((m) => m.kind === "warning").length,
    1,
    "item-level errors are warnings, not failures"
  );
  eq(
    marks.filter((m) => m.kind === "error").length,
    0,
    "a config warning never becomes an error mark"
  );
  eq(codexErrorMessages(marks), [], "config warnings do not reach stderr surfacing");
  eq(codexFinalText(marks), "second answer", "final text is the last non-empty message");
  eq(codexTurnCompleted(marks), true, "turn.completed is observed");

  eq(scanCodexStream(""), [], "empty stream yields no marks");
  eq(
    scanCodexStream("not json\n{\"broken\"\n").length,
    0,
    "non-JSON and malformed lines are skipped"
  );
}

console.log("== codex failures ==");
{
  const failed = [
    `{"type":"turn.started"}`,
    `{"type":"error","message":"The 'gpt-5-nano' model is not supported when using Codex with a ChatGPT account."}`,
    `{"type":"turn.failed","error":{"message":"turn aborted"}}`,
  ].join("\n");
  const marks = scanCodexStream(failed);
  eq(
    codexErrorMessages(marks),
    [
      "codex error: The 'gpt-5-nano' model is not supported when using Codex with a ChatGPT account.",
      "codex error: turn aborted",
    ],
    "top-level error and turn.failed reach stderr in arrival order"
  );
  // Measured 2026-11-15: a hard failure repeats the identical message in the
  // top-level event and in turn.failed. The job's stderr must say it once.
  eq(
    codexErrorMessages(scanCodexStream([
      `{"type":"error","message":"model unsupported"}`,
      `{"type":"turn.failed","error":{"message":"model unsupported"}}`,
    ].join("\n"))),
    ["codex error: model unsupported"],
    "identical repeat messages are collapsed"
  );
  eq(codexFinalText(marks), "", "no assistant text on a failed run");
  eq(codexTurnCompleted(marks), false, "turn.completed absent on a failed run");

  eq(
    codexRefusalMessage({ type: "error", message: "  insufficient_quota  " }),
    "insufficient_quota",
    "refusal message is trimmed"
  );
  eq(codexRefusalMessage({ type: "item.completed", item: { type: "error", message: "advice" } }), null,
    "item advice is NOT a refusal (would abandon healthy runs)");
  eq(codexWarningMessage({ type: "item.completed", item: { type: "error", message: "advice" } }), "advice",
    "item advice is exposed as a warning instead");
  eq(codexRefusalMessage({ type: "turn.failed", error: {} }), null, "empty failure envelope names no message");
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
