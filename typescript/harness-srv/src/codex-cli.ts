/**
 * codex-cli.ts — the `codex exec` invocation, built as data.
 *
 * Why this is its own module: the codex CLI's surface differs from
 * opencode's in ways a copy-pasted argv hides. Verified against
 * codex-cli 0.169.0 (`codex exec --help`) on 2026-11-15:
 *
 *   opencode run --agent X --model M --dir D --format json
 *   codex exec   --model M --cd D    --json     [-s SANDBOX] [--ephemeral] -
 *
 *   - There is no `--agent`/`--format`. The agent identity travels in the
 *     PROMPT (HarnessExecParams.agent is logged, not passed) — the Tester and
 *     Engineer charters are already prepended to every prompt file, so
 *     nothing is lost; codex's own "agents" are a hosted concept, not a flag.
 *   - The prompt goes in on `-` (read stdin). Passing it as argv would hit
 *     E2BIG on the large Assembly-thread prompts that made opencode's path
 *     switch to stdin as well.
 *   - `--skip-git-repo-check` is required: harness work dirs are not always
 *     git repos (scratch/job dirs are plain directories).
 *   - `--ephemeral` is the default here. A server-side harness must not
 *     accumulate a session per job under $CODEX_HOME/sessions, and nothing
 *     in the harness resumes a thread.
 *
 * The sandbox default is the counterpart of opencode's
 * `--dangerously-skip-permissions`: the harness is the containment layer
 * (PROMPT_DIR and work-root boundary checks in executeHarness), and
 * model-generated commands must be able to write and run inside a checked
 * work tree. Override per deployment with CODEX_SANDBOX (read-only |
 * workspace-write | danger-full-access); an unrecognized value falls back to
 * the default rather than being forwarded, so a typo cannot silently
 * escalate or break a run.
 */

export const CODEX_SANDBOX_MODES = ["read-only", "workspace-write", "danger-full-access"] as const;
export type CodexSandboxMode = (typeof CODEX_SANDBOX_MODES)[number];

/** Matches the autonomy the opencode path already grants a harness child. */
export const CODEX_DEFAULT_SANDBOX: CodexSandboxMode = "danger-full-access";

export interface CodexArgParams {
  workDir: string;
  /** Resolved model (tackle config_bundle); absent means CLI default. */
  model?: string | null;
  sandbox?: string | null;
  /** Persist a session under $CODEX_HOME/sessions. Off by default. */
  persistSession?: boolean;
}

/** CODEX_SANDBOX, validated; anything unrecognized keeps the default. */
export function codexSandboxFromEnv(value: string | undefined | null): CodexSandboxMode {
  const mode = (value ?? "").trim();
  return (CODEX_SANDBOX_MODES as readonly string[]).includes(mode)
    ? (mode as CodexSandboxMode)
    : CODEX_DEFAULT_SANDBOX;
}

/**
 * argv for one `codex exec` run. The trailing `-` is the prompt-from-stdin
 * sentinel; without it codex reads stdin only when it finds it piped AND no
 * positional prompt is given, which is a subtlety not worth depending on.
 */
export function codexArgs({
  workDir,
  model,
  sandbox,
  persistSession = false,
}: CodexArgParams): string[] {
  const resolvedModel = typeof model === "string" ? model.trim() : "";
  const args = ["exec", "--json", "--skip-git-repo-check", "--cd", workDir];
  if (resolvedModel) args.push("--model", resolvedModel);
  args.push("--sandbox", codexSandboxFromEnv(sandbox));
  if (!persistSession) args.push("--ephemeral");
  args.push("-");
  return args;
}
