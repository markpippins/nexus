package com.aibizarchitect.nexus.v1.spring.search;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import com.aibizarchitect.nexus.v1.spring.broker.spi.BrokerOperation;
import com.aibizarchitect.nexus.v1.spring.broker.spi.BrokerParam;
import com.aibizarchitect.nexus.v1.broker.api.ServiceResponse;

@Service("googleSearchService")
public class GoogleSearchService {

    private static final Logger log = LoggerFactory.getLogger(GoogleSearchService.class);

    private final RestTemplate restTemplate;
    private final SearchResultsCacheRepository cacheRepository;
    private final SearchRateLimiter rateLimiter;

    @Value("${google.search.api.key:#{null}}")
    private String googleApiKey;

    @Value("${google.search.engine.id:#{null}}")
    private String searchEngineId;

    // Cache TTL in minutes (default 30 minutes) — for freshness, separate from rate-limit cooldown
    private static final long CACHE_TTL_MINUTES = 30;

    private static final String SERVICE_KEY = "google";

    public GoogleSearchService(RestTemplate restTemplate,
                               SearchResultsCacheRepository cacheRepository,
                               SearchRateLimiter rateLimiter) {
        this.restTemplate = restTemplate;
        this.cacheRepository = cacheRepository;
        this.rateLimiter = rateLimiter;
        log.info("GoogleSearchService initialized with MongoDB cache + Redis rate limiter");
    }

    @BrokerOperation("simpleSearch")
    public ServiceResponse<?> simpleSearch(@BrokerParam("token") String token, @BrokerParam("query") String query) {
        return simpleSearch(token, query, false);
    }

    @BrokerOperation("forceSearch")
    public ServiceResponse<?> forceSearch(@BrokerParam("token") String token, @BrokerParam("query") String query) {
        return simpleSearch(token, query, true);
    }

    // Typed failure (slice-1 D3): same {code, message, retryable} shape as
    // the moleculer SearchError. Transport stays HTTP 200 via
    // BrokerController; ok:false + errors[] carry the failure (frozen
    // compat — nexus-console null-guards to [] either way). The broker
    // re-stamps requestId on unwrap, so null here is fine.
    private static ServiceResponse<?> searchError(String code, String message, boolean retryable) {
        java.util.Map<String, Object> err = new java.util.LinkedHashMap<>();
        err.put("code", code);
        err.put("message", message);
        err.put("retryable", retryable);
        err.put("provider", "google");
        return ServiceResponse.error(java.util.List.of(err), null);
    }

    private ServiceResponse<?> simpleSearch(String token, String query, boolean forceRefresh) {
        log.info("Query Received: {} (forceRefresh={})", query, forceRefresh);

        // ── Rate-limit check (skip if force-refresh) ─────────────────
        if (!forceRefresh && rateLimiter.isRateLimited(SERVICE_KEY, query)) {
            // Within cooldown — serve from MongoDB even if cache TTL has expired
            SearchResultsCacheEntry cachedEntry = findAnyCacheEntry(query);
            if (cachedEntry != null) {
                log.info("Rate-limited — returning MongoDB-cached result for query: {}", query);
                return ServiceResponse.ok(buildResult(cachedEntry), null);
            }
            // No cached entry at all; must proceed with fresh search
            log.info("Rate-limited but no MongoDB cache entry — falling through to fresh search");
        }

        // ── Fresh cache check ────────────────────────────────────────
        // forceRefresh bypasses the fresh-cache read as well as the rate
        // limiter (slice-2 G5: Refresh means fresh results, not fresh cache).
        SearchResultsCacheEntry cachedEntry = forceRefresh ? null : findValidCacheEntry(query);
        if (cachedEntry != null) {
            log.info("Returning fresh MongoDB-cached result for query: {}", query);
            rateLimiter.markSearched(SERVICE_KEY, query);
            return ServiceResponse.ok(buildResult(cachedEntry), null);
        }

        if (googleApiKey == null || googleApiKey.isEmpty()) {
            log.warn("Google API Key is not configured. Search functionality will fail.");
            return searchError("SEARCH_CREDENTIALS", "Google API credentials not configured", false);
        }

        if (searchEngineId == null || searchEngineId.isEmpty()) {
            log.warn("Search Engine ID is not configured. Search functionality will fail.");
            return searchError("SEARCH_CREDENTIALS", "Google API credentials not configured", false);
        }

        // Properly URL encode the query
        String encodedQuery = java.net.URLEncoder.encode(query, java.nio.charset.StandardCharsets.UTF_8);
        String url = String.format("https://www.googleapis.com/customsearch/v1?key=%s&cx=%s&q=%s",
                googleApiKey, searchEngineId, encodedQuery);

        try {
            log.debug("Making request to: {}", url);
            ResponseEntity<Map> response = restTemplate.exchange(url, HttpMethod.GET, HttpEntity.EMPTY, Map.class);

            if (response.getStatusCode().is2xxSuccessful()) {
                Map<String, Object> data = response.getBody();

                // Extract items from the response
                List<Map<String, Object>> rawItems = (List<Map<String, Object>>) data.get("items");
                List<SearchResultItem> items = null; // Keep as null if rawItems is null

                if (rawItems != null) {
                    items = new ArrayList<>();
                    for (Map<String, Object> rawItem : rawItems) {
                        SearchResultItem item = new SearchResultItem();
                        item.setKind((String) rawItem.get("kind"));
                        item.setTitle((String) rawItem.get("title"));
                        item.setHtmlTitle((String) rawItem.get("htmlTitle"));
                        item.setLink((String) rawItem.get("link"));
                        item.setDisplayLink((String) rawItem.get("displayLink"));
                        item.setSnippet((String) rawItem.get("snippet"));
                        item.setHtmlSnippet((String) rawItem.get("htmlSnippet"));
                        item.setFormattedUrl((String) rawItem.get("formattedUrl"));
                        item.setHtmlFormattedUrl((String) rawItem.get("htmlFormattedUrl"));
                        Map<String, Object> pagemap = (Map<String, Object>) rawItem.get("pagemap");
                        item.setPagemap(pagemap);

                        // Extract metatags and other specific data from pagemap if available
                        if (pagemap != null) {
                            item.setMetatags((List<Map<String, String>>) pagemap.get("metatags"));
                            item.setCseThumbnail((List<Map<String, String>>) pagemap.get("cse_thumbnail"));
                            item.setCseImage((List<Map<String, String>>) pagemap.get("cse_image"));
                        }

                        // Set the timestamp to current time
                        item.setTimestamp(Instant.now());

                        items.add(item);
                    }
                }

                SearchResult result = new SearchResult();
                result.setItems(items);
                result.setRawResponse(data);

                // Cache the result in MongoDB before returning. A cache-write
                // failure must NOT discard fresh results (failure-isolated):
                // log it and return the live result uncached.
                try {
                    SearchResultsCacheEntry newCacheEntry = new SearchResultsCacheEntry(query, items, CACHE_TTL_MINUTES);
                    cacheRepository.save(newCacheEntry);
                    log.info("Cached result in MongoDB for query: {}", query);
                } catch (Exception e) {
                    log.warn("Cache write failed for query {} — returning live result uncached: {}", query, e.getMessage());
                }

                // Record rate-limit timestamp
                rateLimiter.markSearched(SERVICE_KEY, query);

                return ServiceResponse.ok(result, null);
            } else {
                log.error("Google search API returned error: {}", response.getStatusCode());
                int status = response.getStatusCode().value();
                return searchError("SEARCH_PROVIDER",
                        "Google search API returned error: " + response.getStatusCode(),
                        status == 429 || status >= 500);
            }
        } catch (org.springframework.web.client.ResourceAccessException e) {
            log.error("Network error performing Google search: {}", e.getMessage());
            return searchError("SEARCH_PROVIDER", "Network error performing Google search: " + e.getMessage(), true);
        } catch (Exception e) {
            log.error("Error performing Google search: {}", e.getMessage());
            // restTemplate throws HttpStatusCodeException for 4xx/5xx (it never
            // returns a non-2xx ResponseEntity with the default error handler),
            // so classify here: retryable on 429/5xx, not on other 4xx.
            if (e instanceof org.springframework.web.client.HttpStatusCodeException hse) {
                int status = hse.getStatusCode().value();
                return searchError("SEARCH_PROVIDER",
                        "Google search API returned error: " + hse.getStatusCode(),
                        status == 429 || status >= 500);
            }
            return searchError("SEARCH_PROVIDER", "Error performing Google search: " + e.getMessage(), false);
        }
    }

    /**
     * Find any MongoDB cache entry for the query — even if expired.
     * Used during rate-limited cooldown to serve stale-but-valid results.
     *
     * Normalized-key first, raw-query fallback (slice-2 F1, re-review
     * 8abdd093): once phase-2 writes normalized keys, a raw-key-only
     * cooldown lookup would miss and fall through to live Google DURING
     * cooldown — the exact cost the limiter exists to prevent. Mirrors the
     * normalized-first + raw-fallback treatment findValidCacheEntry has.
     */
    private SearchResultsCacheEntry findAnyCacheEntry(String query) {
        SearchResultsCacheEntry entry = findAnyByKey(SearchRateLimiter.normalize(query));
        if (entry != null) {
            return entry;
        }
        if (!SearchRateLimiter.normalize(query).equals(query)) {
            return findAnyByKey(query);
        }
        return null;
    }

    /**
     * Cooldown-path lookup under one exact key. Any age (stale-serve),
     * never deletes — a cooldown read must not destroy data.
     */
    private SearchResultsCacheEntry findAnyByKey(String key) {
        try {
            var optionalEntry = cacheRepository.findByQuery(key);
            return optionalEntry.orElse(null);
        } catch (Exception e) {
            log.warn("Error accessing cache for query {}: {}", key, e.getMessage());
            return null;
        }
    }

    /**
     * Find a valid (non-expired) cache entry. Normalized-key first, raw-query
     * fallback (slice-2 G2 lazy-migration era: old rows were written under
     * raw keys). Deletes expired entries on read (unchanged).
     */
    private SearchResultsCacheEntry findValidCacheEntry(String query) {
        SearchResultsCacheEntry entry = findValidByKey(SearchRateLimiter.normalize(query));
        if (entry != null) {
            return entry;
        }
        if (!SearchRateLimiter.normalize(query).equals(query)) {
            return findValidByKey(query);
        }
        return null;
    }

    /**
     * Find a valid (non-expired) cache entry under one exact key.
     */
    private SearchResultsCacheEntry findValidByKey(String key) {
        try {
            var optionalEntry = cacheRepository.findByQuery(key);
            if (optionalEntry.isPresent()) {
                SearchResultsCacheEntry entry = optionalEntry.get();
                if (!entry.isExpired()) {
                    return entry;
                } else {
                    cacheRepository.deleteById(entry.getId());
                    log.info("Removed expired cache entry for query: {}", key);
                }
            }
            return null;
        } catch (Exception e) {
            log.warn("Error accessing cache for query {}: {}", key, e.getMessage());
            return null;
        }
    }

    /**
     * Wrap a cache entry into a SearchResult.
     */
    private SearchResult buildResult(SearchResultsCacheEntry entry) {
        SearchResult result = new SearchResult();
        result.setItems(entry.getItems());
        if (entry.getItems() != null && !entry.getItems().isEmpty()) {
            result.setRawResponse(entry.getItems().get(0).getPagemap());
        }
        return result;
    }
}