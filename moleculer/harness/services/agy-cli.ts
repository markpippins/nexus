/**
 * agy-cli.ts — the `agy` invocation, built as data.
 *
 * Why this is its own module: agy looks opencode-like at a glance
 * (`--agent`, `--print`, `--output-format stream-json`, `--model`,
 * `--dangerously-skip-permissions`), but the surface differs in the three
 * places that matter. Verified against agy 1.5.9 (`agy --help` plus live
 * runs) on 2026-11-15:
 *
 *   agy --output-format stream-json --model M --dangerously-skip-permissions --print PROMPT
 *
 *   - There is no usable `--agent`: `agy agent` exits 0 with empty output
 *     (no agents are configured on this host), so the role charter travels
 *     in the PROMPT, exactly like the codex path. Passing `--agent tester`
 *     is rejected outright ("unknown agent tester").
 *   - Print mode does NOT read stdin. `--print` consumes the next argv token
 *     as the prompt ("--print took X as its prompt"), and only
 *     `--input-format stream-json` reads stdin — as NDJSON turn messages, a
 *     different protocol. The prompt therefore travels on argv via
 *     `--print <prompt>`, placed last so it cannot swallow a flag. E2BIG on
 *     very large prompts is agy's interface limit, not ours.
 *   - agy's bare `--sandbox` flag stays OFF, and `--effort`/`--mode` stay at
 *     their defaults: the harness is the containment layer (PROMPT_DIR and
 *     work-root boundary checks in executeHarness), and the model ids already
 *     encode the effort tier (gemini-3.8-flash-low/medium/high).
 *
 * The working directory is inherited from the spawn's cwd (agys `init` event
 * echoes it back); agy has `--add-dir` but no `--cd`, so there is nothing to
 * pass — workDir belongs to the spawn call, not the argv.
 */

/** Gemini Flash, the variant the 2026-11-14 records ran through agy. */
export const AGY_DEFAULT_MODEL = "gemini-3.8-flash-high";

export interface AgyArgParams {
  /** Resolved model (tackle config_bundle); absent means Gemini Flash. */
  model?: string | null;
  /** Full prompt text; travels on argv (agy print mode has no stdin). */
  prompt: string;
}

/** AGY_MODEL, trimmed; anything blank keeps the Gemini Flash default. */
export function agyModelFromEnv(value: string | undefined | null): string {
  const m = (value ?? "").trim();
  return m ? m : AGY_DEFAULT_MODEL;
}

/**
 * argv for one `agy` print-mode run. `--print` is last with the prompt as
 * the next token, so a prompt that starts with `-` cannot eat a flag.
 */
export function agyArgs({ model, prompt }: AgyArgParams): string[] {
  const resolvedModel = agyModelFromEnv(model);
  return [
    "--output-format",
    "stream-json",
    "--model",
    resolvedModel,
    "--dangerously-skip-permissions",
    "--print",
    prompt,
  ];
}
