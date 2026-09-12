# Slice 3 — Rate-Limit Parity Proposal (DRAFT for Architect review)

> **Status:** draft · **Author:** engineer · **Date:** 2026-09-12
> **Scope:** Redis-backed rate limiter for google-simple only (parity
> boundary decided 2026-09-09). Cache internals are slice 2 (merged, PRs
> #214/#216); client `stream-cache` behavior is slice 4.
> **Evidence:** source recon 2026-09-12 (`SearchRateLimiter.java` full read,
> `GoogleSearchService.java` flow L76–175, moleculer
> `google-search.service.ts` full read, `package.json` deps, broker
> `application.properties` redis config). Moleculer has NO rate limiter and
> NO redis dep (only axios/dotenv/moleculer/moleculer-web/mongodb/nats) —
> every `:4050` call hits Google unconditionally.

## 1. Legacy semantics (frozen reference — verified in code)

| Aspect | Behavior |
|---|---|
| Backing store | Redis (`StringRedisTemplate`), key `search:ratelimit:{service}:{normalizedQuery}`, service = `google` |
| Value | ISO-8601 instant of the last live search (`Instant.now().toString()`) |
| Normalization | lowercase, trim, collapse whitespace runs — **same rule as cache keys (slice-2 G2 fix)** |
| Cooldown | 4 h default (`search.ratelimit.cooldown-hours:4`); limited iff `elapsed.toHours() < 4` (sub-hour truncation — 3h59m59s still limited) |
| Key TTL | matches cooldown (`set(k, val, Duration.ofHours(4))`) — keys auto-expire, no janitor needed |
| Read: `isRateLimited` | miss → not limited; parse failure → not limited; **any Redis error → fail-open** (warn + allow) |
| Write: `markSearched` | sets instant + TTL; **fail-silent** on Redis error (search proceeds regardless) |
| Reset: `resetRateLimit` | exists, **zero callers** — documented as available for admin/MCP invalidation flows |
| Call order (broker) | ① `!forceRefresh && isRateLimited` → serve `findAnyCacheEntry` (stale-serve, post-#216: normalized-first + raw fallback); no entry → fall through. ② `!forceRefresh` fresh-cache hit → serve + `markSearched` (**hit extends cooldown**, slice-2 G6 declared intended). ③ live success → cache write + `markSearched`. `forceSearch` bypasses limiter check entirely but still marks on success (L171) |
| Enabled flag | `search.ratelimit.enabled:true` — kill-switch short-circuits both check and mark |

## 2. Gap inventory

| # | Gap | Notes |
|---|---|---|
| G1 | Moleculer has no limiter | every `:4050` call is a live Google call — quota burn; slice-4 auto-search magnifies per folder-switch |
| G2 | No redis dependency | new dep needed (`ioredis` — precedent: `moleculer/nexus-broker`, `nexus-control-edge`, `role-memory-srv`) |
| G3 | No cooldown/limited envelope | moleculer can't even express "limited"; slice-1 envelope has no rate-limit slot (token silently ignored — suspected limiter/accounting key) |
| G4 | Shared vs separate Redis | broker uses `localhost:6379` via Spring config; moleculer needs a URL — shared instance (warm keys during cutover) vs isolated |
| G5 | Clock truncation quirk | `elapsed.toHours() < 4` means cooldown is 3–4 h depending on start second — bug-compat vs fix (use millis) |

## 3. Options

### A. Shared Redis instance, mirror semantics (RECOMMENDED)
Moleculer reads/writes the same Redis the broker's limiter uses (env
`REDIS_URL`, default `redis://localhost:6379` — matches broker's
localhost:6379). Same key namespace = one cooldown bucket per query across
both implementations during the cutover: a query throttled by the broker is
throttled for moleculer too, and vice versa. Keys carry the TTL already.
**Cost:** shared infra blast radius; a new env secret (Q2 follow-up).

### B. Shared Redis, fixed semantics
Same as A but cooldown measured in millis (no truncation quirk) — a
query can get up to ~1 h extra service life vs legacy. Divergence is
one-sided (moleculer strictly *less* limited) and documented. Same cost
profile as A.

### C. Moleculer-local limiter (in-memory)
No redis dep, no shared state. **Cost:** cooldowns reset on every moleculer
restart (the canary restarts often); two limiter instances disagree during
cutover (broker-limited ≠ moleculer-limited — murky parity evidence);
in-process memory contradicts the shared-state direction slice 2 chose for
the cache.

## 4. Recommendation (A, bug-compatible)

Adopt A and mirror the legacy semantics exactly, quirks included:
1. **Key/value/TTL identical**: `search:ratelimit:google:{normalized}`,
   ISO-8601 instant, `EXPIRE` = cooldown (auto-janitor, same as broker).
2. **Cooldown default 4 h** via env `SEARCH_RATELIMIT_COOLDOWN_HOURS=4`,
   **enabled flag** via `SEARCH_RATELIMIT_ENABLED=true` (kill-switch
   parity).
3. **Fail-open reads, fail-silent writes** — identical posture to the
   cache layer (any Redis error: warn, proceed to live search).
4. **Call order mirrors broker exactly** (incl. the #216 normalized-first
   stale-serve on the limited path, and `markSearched`-on-fresh-hit).
   G6 re-declared intended (consistent with slice-2 ruling).
5. **Bug-compatible clock**: `toHours() < cooldown` on the elapsed millis —
   the G5 truncation quirk is preserved so both implementations agree to
   the second. A millis-precision fix (option B) can follow cutover,
   applied to both sides in the same wave.
6. **`resetRateLimit`**: not wired (parity — legacy has zero callers);
   available for admin flows later.
7. **`token`**: still out of scope here (slice-1 envelope decision
   pending); limiter keys stay query-normalized only, like the broker.

Gating decision (not engineer's to make): **Redis credentials/topology**
(Q2 from the day-1 walkthrough, still open). If shared-instance is
unacceptable, C is the fallback — but flag that it forfeits cutover-period
cooldown continuity.

## 5. Decisions needed from Architect

1. **A, B, or C?** (Engineer recommends A — bug-compatible mirror.)
2. **G5 truncation quirk: preserve (recommended) or fix-now-on-both-sides?**
3. **Redis topology/creds: confirm shared `redis://localhost:6379` (or
   provide the real URL) — unblocks the Q2 open question.**

## 6. Implementation sketch (post-ruling)

- `ioredis` dep; lazy client, connection reuse, no eager ping (fail-open).
- `rateLimiter.ts` inlined or sibling-module per #210 constraint (prod
  runs .ts under Node type-stripping — package imports only).
- `performSearch` gains the 3-step order above; `forceSearch` bypasses
  step ① only.
- Jest: mock ioredis — limited-hit stale-serve, limited-no-cache fall-
  through, fresh-hit extends cooldown, live-success marks, force bypass,
  fail-open on redis throw, kill-switch off, truncation-quirk pin.
- Envelope: no response-shape change (limiter is internal; slice-1
  contract untouched).
