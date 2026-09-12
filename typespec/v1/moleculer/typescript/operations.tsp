/**
 * moleculer — TypeSpec Operations
 *
 * Reverse-engineered from moleculer/search/services/api.service.ts +
 * google-search.service.ts.
 * Moleculer service bus: the search API gateway (moleculer-web) exposes the
 * modular search providers (Google Custom Search) as REST endpoints under
 * /api, and registers with the Spring Service Registry with heartbeats.
 *
 * Convention (see typespec/v1/typescript-conventions.md):
 *   - one op per route
 *   - flat full-path `@route` decorators (moleculer `path` prefix + alias
 *     sub-path are concatenated by the reconciler)
 *   - contract status is the FIRST line of every op `@doc`:
 *     `Status: implemented | planned | deprecated | retired`
 *     (M1 Day-0.2 gate; greppable, no emitter changes required)
 */
import "@typespec/http";
import "@typespec/rest";
import "./models.tsp";

using Http;
using Rest;

namespace org.nexus.moleculer {

  // ========================================================================
  // Search
  // ========================================================================

  @route("/api/search/simple")
  @post
  @doc("""
    Status: implemented.
    Execute a Google Custom Search query.
    Slice-1 contract (Option C, ruled 2026-09-11): items carry the canonical
    {title, link, snippet, displayLink}; caller token echoed when supplied
    (reserved for slice-3 rate-limit keying). Failure is non-2xx with a typed
    SearchError body ({code, message, retryable}): SEARCH_CREDENTIALS (no
    retry), SEARCH_PROVIDER (retryable on 429/5xx/network, not other 4xx),
    built-in params validation (no retry).     page/size are PLANNED, not
    implemented — see models.tsp.
    Cache (slice-2, reads-only): normalized-query lookup against the
    shared `nexus.search_results_cache` with raw-query fallback; TTL 30m
    (absent expiresAt = valid); expired rows are misses (never deleted by
    this side); any cache error fails open to live search; cache hits omit
    searchInformation (legacy parity) and echo the token when supplied.
    """)
  op simpleSearch(@body body: SearchRequest): SearchResponse;

  @route("/api/search/force")
  @post
  @doc("""
    Status: implemented.
    Force-refresh search: identical params/shape to simpleSearch but
    bypasses the shared-cache read (slice-2 G5). Still records nothing
    and writes nothing (reads-only phase).
    """)
  op forceSearch(@body body: SearchRequest): SearchResponse;

  // ========================================================================
  // Health
  // ========================================================================

  @route("/api/health")
  @get
  @doc("""
    Status: implemented.
    Health check for the Moleculer API gateway.
    """)
  op health(): MoleculerHealthResponse;
}
