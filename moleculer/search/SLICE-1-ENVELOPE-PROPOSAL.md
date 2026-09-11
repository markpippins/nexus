# Slice 1 — Search Envelope Reconciliation Proposal (DRAFT for Architect review)

> **Status:** draft · **Author:** engineer · **Date:** 2026-09-11
> **Scope:** google-simple success + failure envelopes only (parity boundary
> decided 2026-09-09: google + cache + rate limit; other providers deferred).
> Cache (slice 2) and rate limit (slice 3) envelopes are NOT in scope here.
> **Evidence:** live probes 2026-09-11, same query both sides
> (`:4050` vs `:8081` `googleSearchService/simpleSearch` + `forceSearch`).

## 1. What parity already holds (closed, no action)

Same query, live both sides: **10 items, same set AND same order.**
Ordering, ranking, and result identity need no work.

## 2. Gap inventory (all verified live unless noted)

| # | Gap | Legacy (`:8081`) | Moleculer (`:4050`) | Consumer impact |
|---|---|---|---|---|
| G1 | Item shape | ~48-field optional union (`SearchResultItem.java`; live items carry the full union incl. `pagemap`, `metatags`, video/image/AI fields) | exactly `{title, link, snippet}` (TypeSpec matches impl) | **IdeaStream reads `displayLink`** (`source: item.displayLink`) — moleculer items DON'T carry it → `source` renders undefined on cutover |
| G2 | `rawResponse` | present (full Google body passthrough) | absent | none observed (IdeaStream doesn't read it) but undocumented reliance is possible elsewhere |
| G3 | Failure signal | **HTTP 200, `ok:true`, `data:{items:null}`, `errors:null`** — three distinct causes (Google error, cache-read error, cache-write error) collapse into identical nulls | **HTTP 500**, `{"name":"Error","message":"Failed to perform search: ...","code":500}` | console tolerates both today (null-guard → `[]`; catch → `[]`), but the nulls are undebuggable and untestable per-cause |
| G4 | Pagination | unknown (op takes `{token, query}`; no page/size surface observed) | SPEC documents `page`/`size`, **implementation ignores them** (params validation: `query` + optional `token` only; no `start`/`count` sent to Google) | contract claims a surface that doesn't exist |
| G5 | `token` | required by the client (empty → `[]` without calling); broker-side role TBD (rate-limit/accounting key suspected) | accepted, **silently ignored** (handler passes only `query`) | a token-bearing caller gets identical results with/without it — silent semantic drop |
| G6 | Envelope | `ServiceResponseBody`: `{ok, data, errors, requestId, ts, version, service, operation, encrypt}` | bare google-shape `{items, searchInformation}`; no request identity, no timestamp | canary/differential tooling has nothing to join on across sides |

## 3. Options

### A. Moleculer adopts the legacy shape wholesale
Moleculer returns `ServiceResponseBody` with `ok:true` + nulls on failure.
**For:** single surface fastest; console-compatible by construction.
**Against:** bakes G3's silent-null failure mode into the NEW service —
exactly the undebuggable behavior the cutover should retire. Rejected
(unless architect explicitly prefers bug-compat).

### B. Legacy adopts the moleculer shape
Legacy returns google-shape + non-2xx on failure.
**For:** cleanest end state; console already tolerates 500s (catch → `[]`).
**Against:** `BrokerController` wraps ALL ops in `ServiceResponseBody` —
changing one op's envelope means either a controller special-case or a
new route; affects every `submitRequest` consumer of `googleSearchService`,
not just IdeaStream; audit surface for other callers not yet done.

### C. Minimal contract convergence (RECOMMENDED)
Keep both transports; converge the ITEM contract and make failure
EXPLICIT on both sides without changing either transport's top-level shape:

1. **Item contract (new, canonical):** `{title, link, snippet, displayLink}`
   + open extension bag for provider extras. `displayLink` is derivable
   server-side (URL hostname) — moleculer adds it ( ~5 lines in
   `performSearch` mapper); legacy already carries it.
2. **Moleculer failure:** keep non-2xx, but adopt a typed error object
   `{code, message, retryable}` shared with (3), instead of the current
   free-form string. Distinguish at minimum: `validation` (no retry),
   `provider` (retryable), `credentials` (no retry).
3. **Legacy failure:** keep HTTP 200 transport (console compat), but set
   `ok:false` + populate the EXISTING (always-null-today) `errors[]`
   with the same typed error object, per-cause (google vs cache-read vs
   cache-write). Converts silent nulls into diagnosable nulls; console's
   null-guard behavior is unchanged.
4. **Pagination:** mark `page`/`size` as `planned` in TypeSpec (per-op
   status, Day-0.2 gate); remove-or-implement before cutover sign-off.
   No phantom contract at gate time.
5. **`token`:** document actual semantics on both sides (legacy: required?
   rate-limit key? moleculer: ignored). Either plumb it through on
   moleculer (rate-limit keying for slice 3) or reject-with-`validation`
   when supplied-but-meaningless. No silent drops at gate time.
6. **`rawResponse`:** declare legacy-only, out of contract (or drop it —
   architect call; it doubles payload for no observed consumer).
7. **TypeSpec:** per-op `implemented | planned` status on both ops;
   `displayLink` added to moleculer `SearchResultItem`; failure envelopes
   documented via `@doc`; shared error object modeled once.
8. **Tests:** extend the green jest suite (32/32) with displayLink +
   typed-error cases; legacy needs a matching Java unit test for the
   `errors[]` population (follow-up, DBA/JVM side).

## 4. Decisions needed from Architect

1. **A, B, or C?** (Engineer recommends C.)
2. **`rawResponse`:** keep-as-legacy-only or drop?
3. **Failure transport on the NEW service:** is non-2xx acceptable as the
   permanent moleculer convention, or should `:4050` also move to
   200-with-`ok:false` for gateway uniformity?
4. **`token`:** plumb-through (slice-3 keying) or validate-and-reject?
5. **Pagination:** implement in slice 1 or formal `planned` deferral?

## 5. Non-goals (explicit)

Cache envelope (slice 2), rate-limit envelope (slice 3), throttler-ui UX
(slice 4), other providers (deferred indefinitely), `BrokerController`
refactor beyond the `errors[]` population.
