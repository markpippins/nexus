/**
 * resolved-fallbacks.ts — the fallback ladder handed to a caller must not
 * repeat the row the resolver already chose as the primary.
 *
 * Why this exists (2026-11-07): `getResolvedFallbackModels` selects every
 * active bundle for a role, and the role's primary is one of them, so
 * `GET /config/ai/resolve/sysadmin` returned
 *
 *     model_identifier: nvidia/nemotron-3.5-super-vl-120b-a12b
 *     fallback_models:  [ { priority: 1, model_identifier: same } ]
 *
 * — a ladder whose first rung retries the identical provider answer. The
 * shell harness collapsed it, so sysadmin cycles looked fine, but every other
 * consumer of this endpoint (`python/tackle/db.py` passes `fallback_models`
 * through unchanged; wind-ui-v2 and nexus-control-edge read the same route)
 * carried the duplicate.
 *
 * The same filter landed first in `moleculer/tackle/services/resolved-fallbacks.ts`
 * (PR #907) — but that twin serves SERVICE_PORT 4410 and is not the deployed
 * resolver, so this service kept the duplicate. This module is that fix,
 * ported to the implementation that actually answers :3410.
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
