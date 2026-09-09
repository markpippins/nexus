# M1 — Moleculer Cutover Service Manifest (FROZEN)

> **Frozen:** 2026-09-09 (Day 0.1 of To Do `b3f8bf70`, source discussion `5e149937`).
> **Purpose:** the single inventory M1 gates cutover against — Moleculer service,
> legacy counterpart, ports, systemd units, source revisions, contract paths,
> ownership. A cutover may proceed only against the revisions frozen here; any
> newer commit re-opens the freeze for the affected row.
> **Verification method:** live `systemctl --user` + `ss -tlnp` + HTTP probes on
> 2026-09-09, `git log` revisions from `github/main`.

## Day 1 — Search (M1 in-scope cutover)

| Field | Moleculer side | Legacy side |
|---|---|---|
| Services | `google-search` (`simpleSearch`, `health`) + `api` gateway + `registry-client` (`moleculer/search/services/`) | `search-service` bean library (`GoogleSearchService`, `YouTubeSearchService`, `UnsplashSearchService`, `GeminiSearchService`, `SearchRateLimiter`, cache) — **no HTTP surface of its own**; component-scanned into `broker-gateway` |
| Cutover seam | `POST /api/search/simple`, `GET /api/health` on `:4050` | **Op-dispatch**, not a URL: `BrokerService.submitRequest(url, service, operation, params)` against `:8081` with ops incl. `googlePublicSearch` (`nexus-console/src/services/search.service.ts`) |
| Port | `4050` (live, `GET /api/health` → `ok`) | `8081` (live; broker-gateway) |
| Systemd unit | `moleculer-search.service` (active) | `broker-gateway.service` (active) |
| Source revision | `0ef20043` (2026-08-19) | search-service lib `55b7c389` (2026-07-29); broker-gateway `9fe758dd` (2026-08-25) |
| TypeSpec contract | `typespec/v1/moleculer/typescript/` (`main/models/operations.tsp`) — route-count reconciliation only (2/2); **no per-op status, no body/error parity yet** | `typespec/v1/service-broker/spring/search-service/models.tsp` |
| Live callers (legacy) | _none found_ — no consumer currently calls `:4050` | `nexus-console` Throttler/search paths via `search.service.ts` (`:4200`, unit active, rev `ceb0007a`) |
| Target consumer | `throttler-ui` (`:4211`, unit active, rev `7e1091b8`) — **has NO search call site today** (`SearchPane.tsx` → own `/api/folder-search` only). Search UX here is **greenfield**, not a redirect. | — |
| Ownership | Engineer (execution) + Architect (gates) + Analyst (evidence) | same |
| Standing | **PRE-CUTOVER — both authorities live** | — |

### Search scope notes (frozen open questions — operator/architect)
- **Parity boundary undecided:** `:4050` is google-simple-only; legacy is 4
  providers + cache + rate limiting. Day 1 must declare google-simple-only
  (defer rest) or full `search-service`.
- **Google creds unset** in this environment (`GOOGLE_API_KEY` /
  `GOOGLE_SEARCH_ENGINE_ID` absent). Missing-creds path verified live on
  `:4050` (`500 "Google API credentials not configured"`); legacy null-key
  failure envelope not yet captured.
- **Test suite not runnable as-is:** `ts-jest` declared but not installed;
  May `ISSUES.md` reports 24/32 failing. Triage pending (Day 1 prep).

## M2/M3 context — nexus-broker (NOT M1 cutover scope, frozen for reference)

| Moleculer worker (`:4080`, `nexus-broker.service` active, rev `326ad244` 2026-09-05) | Legacy counterpart (all units active, all ports live) |
|---|---|
| `worker.harness` | `harness-srv` `:3420` (rev `161cac78`); `wind-srv` still calls `HARNESS_URL` `:3420` directly |
| `worker.pty` | `pty-srv` `:3121` (rev `1885b121`); note `:3120` also listening on loopback — dual-listen flag |
| `worker.execution` | `execution-srv` `:3110` (rev `756ace12`); gateway exposes only execution health at `/api/workers/execution`, not the full read catalog |
| `keychain-snapshot` | **No legacy counterpart** (Keychains subsystem excepted from 1:1 per operator guidance) |
| Contract `typespec/v1/nexus-broker/typescript/` | **Known drift (unresolved):** contract-only `GET/POST /api/solir/...` with no source implementation; no per-op status annotations |

## Freeze discipline
- Cutover evidence (contract suite, parity, canary, zero-traffic, rollback test)
  must reference the revisions above.
- Any newer commit to a frozen path re-opens that row: re-verify port/unit/
  contract deltas and re-freeze before gating.
