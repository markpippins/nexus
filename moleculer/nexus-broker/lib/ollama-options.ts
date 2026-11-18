/**
 * Ollama generation options (worker.harness).
 *
 * The ollama harness path used to hardcode `num_predict: 1024` in the
 * /api/generate body. On a small CPU-only board that ceiling is the dominant
 * cost: helium measured 3.41 tok/s on openbmb/minicpm5-2b, so 1024 tokens is a
 * ~300s floor for EVERY job regardless of how short the answer is. The failure
 * mode is a plain abort at the harness timeout, indistinguishable from a model
 * or network fault, which is why the ceiling is now deployment-configurable
 * instead of a magic number.
 *
 * Defaults are unchanged (1024) so every existing host behaves exactly as
 * before; only a host that sets OLLAMA_NUM_PREDICT takes the new path.
 */

export const DEFAULT_OLLAMA_NUM_PREDICT = 1024;

/**
 * Resolve a generation ceiling from a raw env value.
 *
 * Invalid, absent, zero, negative and non-finite values fall back to the
 * default rather than being sent to ollama: `num_predict: NaN` serialises to
 * `null` in JSON and ollama treats that as "unset", which would silently
 * reintroduce an unbounded ceiling instead of failing loudly.
 */
export function resolveOllamaNumPredict(raw: string | number | null | undefined): number {
  if (raw === undefined || raw === null || raw === "") return DEFAULT_OLLAMA_NUM_PREDICT;
  const parsed = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_OLLAMA_NUM_PREDICT;
  return Math.floor(parsed);
}

/** Resolve the ceiling from an environment map (process.env by default). */
export function resolveOllamaNumPredictFromEnv(
  env: Record<string, string | undefined> = process.env,
): number {
  return resolveOllamaNumPredict(env.OLLAMA_NUM_PREDICT);
}
