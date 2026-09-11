/**
 * moleculer — TypeSpec Models
 *
 * Reverse-engineered from moleculer/search/services/google-search.service.ts.
 * No route or verb decorators live here; the reconciler scans this file and
 * must find none.
 */
namespace org.nexus.moleculer {

  /** A single search result item (canonical slice-1 contract). */
  model SearchResultItem {
    /** Result title. */
    title: string;
    /** Result URL. */
    link: string;
    /** Result snippet. */
    snippet: string;
    /** Display hostname. IdeaStream renders this as `source`; server derives
        it from the link when Google omits it, so it is always present. */
    displayLink: string;
  }

  /** Search metadata reported by the provider. */
  model SearchInformation {
    /** Total result count as reported by the provider. */
    totalResults: string;
    /** Search time in seconds. */
    searchTime: float64;
  }

  /** Request body for POST /api/search/simple. */
  model SearchRequest {
    /** Search query string. */
    query: string;
    /** Provider token (optional). Carried end-to-end and echoed in the
        response; reserved for slice-3 rate-limit accounting. */
    token?: string;
    /** Requested page. PLANNED — not implemented; ignored if supplied. */
    page?: int32;
    /** Requested page size. PLANNED — not implemented; ignored if supplied. */
    size?: int32;
  }

  /** Response body for POST /api/search/simple. */
  model SearchResponse {
    /** Result items. */
    items: SearchResultItem[];
    /** Provider search metadata. */
    searchInformation?: SearchInformation;
    /** Echo of the request token when one was supplied. */
    token?: string;
  }

  /** Typed failure body for POST /api/search/simple (non-2xx, D3). */
  model SearchError {
    /** Machine-readable code: SEARCH_CREDENTIALS | SEARCH_PROVIDER. */
    code: string;
    /** Human-readable message. */
    message: string;
    /** Whether retrying can succeed. SEARCH_CREDENTIALS: false.
        SEARCH_PROVIDER: true on 429/5xx/network failure, false on other 4xx.
        Params validation failures surface as the built-in ValidationError
        (no retry). */
    retryable: boolean;
    /** Google HTTP status when the failure came from the provider. */
    status?: int32;
  }

  /** Response body for GET /api/health. */
  model MoleculerHealthResponse {
    /** Health status, normally "ok". */
    status: string;
    /** Service name. */
    service: string;
  }
}
