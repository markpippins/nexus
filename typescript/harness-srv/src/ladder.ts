/**
 * ladder.ts — widening the failover ladder beyond a role's configured bundles.
 *
 * Why this exists (2026-11-06): the ladder was exactly what
 * tackle.config_bundle contained. A role with three configured rungs had
 * three rungs, and when all three were dead the job died with them — even
 * though the machine had a dozen working providers. The tester bundle spent
 * hours rotating through glm-5.3-flash-free → big-pickle → gpt-oss-120b while
 * `nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b` answered in ten seconds the
 * whole time. Configuring a role is an expression of intent, not an inventory
 * of what the machine can run.
 *
 * What this adds is a CATALOG: known-good rungs appended to every ladder and
 * ordered by provider preference —
 *
 *     nvidia → opencode → opencode-go → openrouter → ollama → everything else
 *
 * Catalog rungs are ranked against the configured fallbacks rather than
 * dumped at the end, so a role whose fallbacks are all OpenCode still reaches
 * Nvidia before paying for a dead rung. The configured PRIMARY is never moved:
 * whoever configured the role chose it, and silently replacing a deliberate
 * primary with a catalog entry would make the config a suggestion.
 *
 * Everything here is pure (no db/redis/env reads apart from the two parsers
 * that take their raw value as an argument) so the ordering rules can be
 * tested without a live database — mirrors run-budget.ts and rung-cooldown.ts.
 */

/**
 * Catalog used when HARNESS_LADDER_CATALOG is unset. Every id below was
 * verified present in `opencode models` on 2026-11-06 — a catalog entry that
 * does not exist burns a rung, which is the exact failure this module is
 * meant to remove.
 *
 * Ordered by the preference rule above; the merge re-sorts by provider rank
 * anyway, so this order only decides ties within one provider.
 */
export const DEFAULT_CATALOG: readonly string[] = [
  // Three Nvidia rungs, not every Nvidia model that was listed: the cap is
  // shared with the configured fallbacks, and a catalog that is all one
  // provider is a single outage away from being useless again.
  "nvidia/nvidia/nemotron-3.5-super-vl-120b-a12b",
  "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b",
  "nvidia/z-ai/glm-5.3-flash",
  "opencode/nemotron-3.5-lightning-free",
  "opencode/nemotron-3.5-super-vl-free",
  "opencode/glm-5.3-flash-free",
  "opencode-go/glm-5.3-flash",
  // OpenRouter's free-models router: one rung that fans out server-side, so it
  // is a cheap hedge when every named provider is rate-limited.
  "openrouter/openrouter/free",
] as const;

/**
 * Total rungs a ladder may reach. The run's timeout_ms is shared across the
 * whole ladder (see run-budget.ts), so an unbounded catalog would let the
 * widening eat the budget: six is enough to cross the dead providers and
 * still leave the last rung a real slice.
 */
export const DEFAULT_MAX_RUNGS = 6;

/**
 * opencode provider prefix → preference rank (lower is tried earlier).
 *
 * Nvidia is first because it is the reliable primary here; `opencode` and
 * `opencode-go` follow in that order, then OpenRouter, then local ollama
 * (slow), then anything unrecognised. DeepSeek's own provider is last: its
 * key has been invalid for months, so a rung there is a guaranteed failure.
 */
const PROVIDER_MODEL_RANK: Record<string, number> = {
  nvidia: 0,
  opencode: 1,
  "opencode-go": 2,
  openrouter: 3,
  ollama: 4,
  deepseek: 5,
  "deepseek-ai": 5,
};

const UNKNOWN_PROVIDER_RANK = 6;

/**
 * Preference rank of an opencode wire model id, read from its provider
 * prefix. `nvidia/nvidia/nemotron-…` and `opencode/big-pickle` both split on
 * the FIRST slash, which is the opencode provider in both cases.
 */
export function providerRankOfModel(model: string | null | undefined): number {
  if (!model) return UNKNOWN_PROVIDER_RANK;
  const slash = model.indexOf("/");
  const provider = (slash > 0 ? model.slice(0, slash) : model).trim().toLowerCase();
  return PROVIDER_MODEL_RANK[provider] ?? UNKNOWN_PROVIDER_RANK;
}

/**
 * The catalog from the environment.
 *
 *   unset            → DEFAULT_CATALOG
 *   ""               → empty (explicit opt-out: ladder is config-bundle-only)
 *   "a,b , c"        → ["a", "b", "c"]
 *
 * Blank entries are dropped rather than becoming a rung with no model name.
 */
export function catalogFromEnv(value: string | undefined): string[] {
  if (value === undefined) return [...DEFAULT_CATALOG];
  return value
    .split(",")
    .map((m) => m.trim())
    .filter((m) => m.length > 0);
}

/**
 * The rung cap from the environment. A non-positive or unparseable value uses
 * the default rather than silently disabling the ladder.
 */
export function maxRungsFromEnv(value: string | undefined): number {
  if (value === undefined || value.trim() === "") return DEFAULT_MAX_RUNGS;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 1) return DEFAULT_MAX_RUNGS;
  return Math.floor(parsed);
}

/** Two ids naming the same model differ by case/whitespace at most. */
function modelKey(model: string | null | undefined): string {
  return (model ?? "").trim().toLowerCase();
}

/**
 * Merge catalog rungs into a configured ladder.
 *
 * Rules:
 *  - The configured primary (index 0) stays first, always.
 *  - Every other rung — configured fallback or catalog addition — is ordered
 *    by provider rank, then by its original position (so configured order
 *    survives inside one provider, and the primary keeps its slot).
 *  - A model appears once: a catalog entry equal to a configured rung is
 *    dropped, not duplicated.
 *  - At most `maxRungs` rungs are returned, counting the primary. When the cap
 *    binds, CATALOG rungs are dropped first: a configured rung is an explicit
 *    choice by whoever configured the role, and widening the ladder must not
 *    silently delete it to make room for a default.
 *  - An empty configured ladder stays empty: a role with no bundle has no
 *    model to run, and inventing a primary here would bypass admission
 *    (the routes reject NO_CONFIG before this runs).
 *
 * `makeRung` builds a ladder entry for a catalog model id, so this module
 * never needs to know how attempts carry their harness or provider.
 */
export function mergeLadder<T extends { model?: string | null }>(
  configured: T[],
  catalog: string[],
  makeRung: (model: string) => T,
  maxRungs: number = DEFAULT_MAX_RUNGS,
): T[] {
  if (configured.length === 0) return [];

  const cap = Number.isFinite(maxRungs) && maxRungs >= 1 ? Math.floor(maxRungs) : DEFAULT_MAX_RUNGS;
  const [primary, ...rest] = configured;
  const seen = new Set<string>();
  const primaryKey = modelKey(primary.model);
  if (primaryKey) seen.add(primaryKey);

  const candidates: Array<{ rung: T; rank: number; index: number; fromCatalog: boolean }> = [];
  rest.forEach((rung, index) => {
    const key = modelKey(rung.model);
    // A duplicate of the primary (or an earlier fallback) would pay twice for
    // the same provider answer.
    if (key) {
      if (seen.has(key)) return;
      seen.add(key);
    }
    candidates.push({ rung, rank: providerRankOfModel(rung.model), index, fromCatalog: false });
  });

  let nextIndex = rest.length;
  for (const model of catalog) {
    const key = modelKey(model);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    candidates.push({
      rung: makeRung(model),
      rank: providerRankOfModel(model),
      index: nextIndex++,
      fromCatalog: true,
    });
  }

  candidates.sort((a, b) => a.rank - b.rank || a.index - b.index);

  const room = Math.max(0, cap - 1);
  let selected = candidates;
  if (candidates.length > room) {
    // Configured rungs get the cap first; whatever is left is filled from the
    // catalog in preference order. The union is then re-read in the sorted
    // order, so rank still decides positions.
    const keep = new Set(
      [
        ...candidates.filter((c) => !c.fromCatalog).slice(0, room),
        ...candidates
          .filter((c) => c.fromCatalog)
          .slice(0, Math.max(0, room - candidates.filter((c) => !c.fromCatalog).length)),
      ],
    );
    selected = candidates.filter((c) => keep.has(c));
  }

  return [primary, ...selected.map((c) => c.rung)];
}
