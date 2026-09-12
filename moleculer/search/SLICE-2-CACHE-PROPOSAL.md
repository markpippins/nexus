# Slice 2 — Cache Parity Proposal (DRAFT for Architect review)

> **Status:** draft · **Author:** engineer · **Date:** 2026-09-12
> **Scope:** read/write cache semantics for google-simple only (parity
> boundary decided 2026-09-09). Rate-limiter internals are slice 3 except
> where they touch cache keys/flow. Client `stream-cache` (UI-side) is
> slice 4.
> **Evidence:** source recon 2026-09-12 (`GoogleSearchService`,
> `SearchResultsCacheEntry/Repository`, `SearchRateLimiter`,
> `stream-cache.service.ts`, `idea-stream.component.ts`); moleculer has
> NO cache (direct axios every call — verified: no mongo/redis deps).

## 1. Legacy semantics (frozen reference — verified in code)

| Aspect | Behavior |
|---|---|
| Store | Mongo `nexus.search_results_cache`, `{query, items[], timestamp, expiresAt}` |
| Key | **RAW query string** (`{'query': ?0}` exact match) |
| TTL | 30 min (`CACHE_TTL_MINUTES`), app-checked (`isExpired`); expired rows deleted one-by-one **on read only** (`deleteById`) |
| Janitor | `findExpiredEntries` declared, **zero callers** — no background purge |
| Duplicates | `save()` always inserts (null `@Id`): repeated fresh searches pile **duplicate docs**; `findByQuery` returns an arbitrary first match |
| Rate-limit interplay | limiter key `search:ratelimit:{service}:{NORMALIZED query}` (lowercase/trim/collapse) — **normalized, unlike the cache key**; `markSearched` fires on live success AND on fresh-cache hit (hit extends cooldown); rate-limited + stale-or-absent cache → fall through to live |
| `forceRefresh` | skips the limiter check but **still serves a fresh cache entry** (only the rate path is bypassed, not the cache) — the Refresh button can return cached results |
| Client mirror | UI `stream-cache`: 30-min TTL matching broker, keyed service/query, magnetPath-tagged; IdeaStream aggregates across magnets |

## 2. Gap inventory

| # | Gap | Notes |
|---|---|---|
| G1 | Moleculer has no cache at all | every `:4050` call = live Google (quota + latency); slice-4 auto-search magnifies this per folder-switch |
| G2 | Key normalization mismatch | cache raw vs limiter normalized — same intent, different rows/buckets |
| G3 | Duplicate rows on every fresh search | unbounded growth, arbitrary match |
| G4 | No janitor | expired rows linger until randomly re-read |
| G5 | `forceRefresh` serves fresh cache | contradicts the Refresh-button expectation of new results |
| G6 | `markSearched`-on-hit extends cooldown | serving from cache pushes the next live call further out — intended? undocumented |

## 3. Options

### A. Shared Mongo collection (RECOMMENDED)
Moleculer reads+writes `nexus.search_results_cache` (new `mongodb` dep).
Single warm cache across the cutover: dual-read period shares state,
IdeaStream-equivalent consumers see warm results day one, zero-traffic
proof unaffected (cache reads aren't provider calls either way).
Item shapes are compatible (legacy union is all-optional; moleculer
writes canonical slim items + displayLink).
**Cost:** shared persistence = **DBA review required** (M1 acceptance);
moleculer gains a Mongo dependency.

### B. Moleculer-local cache (in-memory LRU+TTL)
Isolated blast radius, no DBA review, no new infra. **Cost:** cold
moleculer cache during migration (all `:4050` calls hit Google until warm);
two caches can diverge on identical queries (murky parity evidence);
per-process memory (restart loses it — consistent with canary philosophy,
but IdeaStream-equivalent wants stability).

### C. No moleculer cache until cutover
Document live-hit-everything as interim. **Cost:** slice 2 delivers docs
only; quota/latency burn continues; slice 4 inherits the problem unsolved.

## 4. Recommendation (A + joint quirk fixes)

Adopt A, and fix G2/G3/G5 jointly (both sides, same PR wave) since parity
on a quirk is worse than no parity:
1. **Normalize cache keys** (same normalize() as limiter) — new writes
   normalized; legacy rows migrate lazily (read-tolerant: try normalized,
   fall back to raw once, rewrite normalized).
2. **Upsert by normalized query** instead of blind insert (kills G3
   going forward; existing duplicates age out via TTL-expiry deletion).
3. **`forceRefresh` bypasses BOTH limiter and fresh cache** (matches the
   Refresh-button contract; behavior change vs legacy, documented).
4. **TTL 30 min unchanged**; `markSearched`-on-hit preserved + documented
   (G6 declared intended: cache service extends provider cooldown).
5. **Janitor:** out of scope; optional TTL index on `expiresAt` as a
   DBA-reviewable follow-up (small, safe, kills G4 properly).

## 5. Decisions needed from Architect (DBA looped on persistence)

1. **A, B, or C?** (Engineer recommends A.)
2. **Quirk fixes (1–3): fix-jointly vs bug-compat?** (Engineer recommends fix.)
3. **TTL index follow-up:** approve in principle now or defer?
4. **DBA review:** shared-collection write triggers M1's persistence-review clause — confirm DBA sign-off required before moleculer writes (reads-only first?).

## 6. Non-goals (explicit)

Rate-limiter behavior changes (slice 3), client stream-cache changes
(slice 4), other providers, janitor implementation, `BrokerController`
changes.
