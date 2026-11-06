/**
 * Environment construction for a spawned opencode child.
 *
 * Why this is its own module: the child env is the ONLY channel by which a
 * harness-launched run learns which model it is running on. Until this
 * existed, `executeOpencode` passed `--model <id>` to opencode but exported
 * only HARNESS_ROLE / HARNESS_JOB_ID to the child, so `$NEXUS_AGENT_MODEL`
 * was whatever the long-lived harness process happened to inherit at boot.
 *
 * Measured 2026-11-06: a tester job resolved to `opencode/glm-5.3-flash-free`
 * (the value on its `--model` argv) posted seven agent records attributed to
 * `opencode/glm-4.5` — the stale inherited value. With PR #896's change
 * (`--model` inherits `$NEXUS_AGENT_MODEL`), that stale value would silently
 * become the provenance of every record the run writes, which is worse than
 * the nulls it replaced: confidently wrong attribution.
 *
 * The rule encoded here is that the resolved model is authoritative. A child
 * with NO resolved model gets NO NEXUS_AGENT_MODEL even when the parent has
 * one, so attribution degrades to null (honest) instead of inheriting a
 * different run's model (wrong).
 */

export interface ChildEnvParams {
  role: string;
  jobId: string;
  /** The model opencode is launched with (`--model`); absent means unresolved. */
  model?: string | null;
  /** Parent environment; injectable so tests need not mutate process.env. */
  base?: NodeJS.ProcessEnv;
}

export function buildChildEnv({
  role,
  jobId,
  model,
  base = process.env,
}: ChildEnvParams): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...base,
    HARNESS_ROLE: role,
    HARNESS_JOB_ID: jobId,
  };

  const resolved = typeof model === "string" ? model.trim() : "";
  if (resolved) {
    env.NEXUS_AGENT_MODEL = resolved;
  } else {
    // Delete rather than blank: post-agent-record.py treats "" as an explicit
    // opt-out and an unusable value as a warning, but an empty string is not
    // what "no model was resolved" means — absent keeps the record's model
    // null, which is the truthful reading.
    delete env.NEXUS_AGENT_MODEL;
  }

  return env;
}
