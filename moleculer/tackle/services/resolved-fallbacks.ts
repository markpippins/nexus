/**
 * resolved-fallbacks.ts — the fallback ladder handed to a caller must not
 * repeat the row the resolver already chose as the primary.
 *
 * Why this exists (2026-11-06): `getResolvedFallbackModels` selects every
 * active bundle for a role, and the role's primary is one of them. The sysadmin
 * role therefore resolved to
 *
 *     model_identifier: nvidia/nemotron-3-super-120b-a12b
 *     fallback_models:  [ { priority: 1, model_identifier: same model } ]
 *
 * — a ladder whose only rung retries the identical provider answer. Consumers
 * that de-duplicate (harness-srv's ladder merge, the sysadmin shell harness)
 * collapse it; consumers that do not pay twice for the same failure.
 *
 * Kept in its own module, with no db imports, so the filter is unit-testable
 * without a live PostgreSQL or a service bootstrap.
 */

/**
 * Remove the primary's bundle from a fallback list.
 *
 * Matching is by bundle id, not by model id: the same model may legitimately
 * appear twice under different providers (an Nvidia-hosted copy and an
 * OpenRouter copy are different rungs), but one bundle row is one rung.
 * An unknown primary id leaves the list untouched — a caller that does not know
 * the primary should not silently lose its fallbacks.
 */
export function withoutPrimaryBundle<T extends { bundle_id?: string | null }>(
  fallbacks: readonly T[],
  primaryBundleId: string | null | undefined,
): T[] {
  if (!primaryBundleId) return [...fallbacks];
  return fallbacks.filter((fallback) => fallback.bundle_id !== primaryBundleId);
}
