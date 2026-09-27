/**
 * model.ts — opencode model-id resolution (side-effect-free).
 *
 * Maps a tackle provider + model_identifier to the opencode `--model` wire
 * id. opencode config keys its provider models by the wire model ID, and the
 * `--model` value is the map key verbatim — so the wire id is always
 * `<opencode-provider>/<model_identifier>`:
 *
 *   - Nvidia:       z-ai/glm-5.2                → nvidia/z-ai/glm-5.2
 *   - Nvidia:       nvidia/nemotron-3-super-…   → nvidia/nvidia/nemotron-3-super-…
 *   - DeepSeek:     deepseek-chat               → deepseek/deepseek-chat
 *   - OpenCode:     big-pickle                  → opencode/big-pickle
 *   - OpenCode Go:  gemini-3.5-flash            → opencode-go/gemini-3.5-flash
 *   - Ollama:       qwen2.5-coder               → ollama/qwen2.5-coder
 *
 * The opencode provider name is resolved canonically from
 * tackle.providers.config_json.opencodeProvider (editable in tackle-ui),
 * with a hardcoded map as fallback for provider ids whose config_json is
 * missing or unparseable.
 *
 * This module intentionally has NO db/redis imports — pure functions only,
 * so unit tests can load it without constructing connection clients (mirrors
 * governance.ts).
 */

/**
 * Hardcoded fallback: tackle provider id → opencode provider name.
 *
 * Canonical source of truth is tackle.providers.config_json.opencodeProvider;
 * this map only backstops rows where config_json is missing. It was
 * historically wrong for DeepSeek (mapped to `deepseek-ai`, but opencode's
 * real provider slug is `deepseek` — `opencode models deepseek` works,
 * `opencode models deepseek-ai` fabricates a self-namespaced model) —
 * config_json-first resolution fixes that.
 */
const OPENCODE_PROVIDER_BY_TACKLE: Record<string, string> = {
  "prov-1783906359513": "nvidia", // Nvidia
  "prov-1782144397043": "openrouter", // OpenRouter
  "prov-opencode-go": "opencode-go",
  "prov-opencode": "opencode",
  "prov-ollama": "ollama",
  "prov-deepseek": "deepseek",
};

/**
 * Extract the opencode provider name from a tackle.providers.config_json
 * value. config_json may arrive as a JSON string (jsonb through the pg
 * driver) or as an already-parsed object. Returns undefined when absent,
 * unparseable, or missing the `opencodeProvider` key.
 */
export function opencodeProviderFromConfig(configJson: unknown): string | undefined {
  if (!configJson) return undefined;
  try {
    const parsed =
      typeof configJson === "string"
        ? (JSON.parse(configJson) as Record<string, unknown>)
        : (configJson as Record<string, unknown>);
    const name = parsed?.opencodeProvider;
    return typeof name === "string" && name.length > 0 ? name : undefined;
  } catch {
    return undefined;
  }
}

/**
 * Map a tackle provider + model_identifier to the opencode --model wire id.
 *
 * The opencode provider name MUST come from the mapping (config_json → map),
 * never from the model id's first segment: a namespaced identifier like
 * `z-ai/glm-5.2` is served under the provider that owns it
 * (`nvidia/z-ai/glm-5.2`), not under a fabricated first-segment provider
 * (`z-ai/z-ai/glm-5.2`), which has no credential and silently falls back to
 * opencode's default model (→ "OK"-only replies).
 */
export function opencodeModelId(
  providerId: string,
  modelIdentifier: string,
  opencodeProviderOverride?: string
): string {
  const opencodeProvider = opencodeProviderOverride || OPENCODE_PROVIDER_BY_TACKLE[providerId];

  if (opencodeProvider) {
    return `${opencodeProvider}/${modelIdentifier}`;
  }

  const slash = modelIdentifier.indexOf("/");
  if (slash > 0) {
    // Unmapped provider + already-namespaced identifier → last-resort
    // first-segment heuristic (legacy behavior).
    return modelIdentifier.slice(0, slash) + "/" + modelIdentifier;
  }

  // Bare identifier + unmapped provider → pass through unchanged.
  return modelIdentifier;
}
