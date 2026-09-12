package com.aibizarchitect.nexus.v1.spring.search;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;
import org.mockito.ArgumentCaptor;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpServerErrorException;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;

import com.aibizarchitect.nexus.v1.broker.api.ServiceResponse;

/**
 * Slice-1 (Option C, ruled 2026-09-11): legacy failure contract.
 * Failures surface as ok:false + typed errors[] ({code, message,
 * retryable, provider}); transport stays HTTP 200 via BrokerController.
 * Cache-write failure degrades to uncached success, never nulls.
 */
@ExtendWith(MockitoExtension.class)
class GoogleSearchServiceTest {

    @Mock
    private RestTemplate restTemplate;

    @Mock
    private SearchResultsCacheRepository cacheRepository;

    @Mock
    private SearchRateLimiter rateLimiter;

    private GoogleSearchService service;

    @BeforeEach
    void setUp() {
        service = new GoogleSearchService(restTemplate, cacheRepository, rateLimiter);
        ReflectionTestUtils.setField(service, "googleApiKey", "test-key");
        ReflectionTestUtils.setField(service, "searchEngineId", "test-cx");
        lenient().when(cacheRepository.findByQuery(anyString())).thenReturn(Optional.empty());
        lenient().when(rateLimiter.isRateLimited(anyString(), anyString())).thenReturn(false);
    }

    @Test
    void testCacheWriteNormalizesKeyAndRefreshesExistingRow() {
        // DBA audit 2026-09-12: UNIQUE {query:1} + TTL {expiresAt:1} era.
        // Re-search of an existing key must UPDATE the row (refresh works,
        // expiresAt re-stamped) — a blind save() would E11000 and silently
        // stop refreshes until TTL cleanup.
        SearchResultsCacheEntry existing = new SearchResultsCacheEntry();
        existing.setId("existing-id");
        existing.setQuery("angular signals");
        // Expired so the fresh-cache reader deletes it and the live search
        // (refresh path) proceeds to the upsert.
        existing.setExpiresAt(java.time.Instant.now().minusSeconds(60));
        when(cacheRepository.findByQuery(eq("angular signals"))).thenReturn(Optional.of(existing));
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenReturn(ResponseEntity.ok(new HashMap<>()));

        ServiceResponse<?> res = service.simpleSearch("tok", "  Angular   SIGNALS ");

        assertTrue(res.isOk());
        ArgumentCaptor<SearchResultsCacheEntry> captor = ArgumentCaptor.forClass(SearchResultsCacheEntry.class);
        verify(cacheRepository).save(captor.capture());
        SearchResultsCacheEntry saved = captor.getValue();
        assertEquals("existing-id", saved.getId()); // updated, not duplicated
        assertEquals("angular signals", saved.getQuery()); // canonical normalized key
        assertNotNull(saved.getExpiresAt());
        assertTrue(saved.getExpiresAt().isAfter(java.time.Instant.now())); // TTL re-stamped → not immortal
    }

    @Test
    void testCacheWriteCreatesCanonicalRowWhenAbsent() {
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenReturn(ResponseEntity.ok(new HashMap<>()));

        ServiceResponse<?> res = service.simpleSearch("tok", "  Spring   BOOT ");

        assertTrue(res.isOk());
        ArgumentCaptor<SearchResultsCacheEntry> captor = ArgumentCaptor.forClass(SearchResultsCacheEntry.class);
        verify(cacheRepository).save(captor.capture());
        SearchResultsCacheEntry saved = captor.getValue();
        assertEquals("spring boot", saved.getQuery());
        assertNotNull(saved.getExpiresAt());
        assertTrue(saved.getExpiresAt().isAfter(java.time.Instant.now()));
    }

    @Test
    @SuppressWarnings("unchecked")
    void testSimpleSearchSuccess() {
        Map<String, Object> body = new HashMap<>();
        Map<String, Object> item = new HashMap<>();
        item.put("kind", "customsearch#result");
        item.put("title", "T");
        item.put("link", "https://example.com/x");
        item.put("displayLink", "example.com");
        item.put("snippet", "S");
        body.put("items", List.of(item));
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenReturn(ResponseEntity.ok(body));

        ServiceResponse<?> res = service.simpleSearch("tok", "q");

        assertTrue(res.isOk());
        assertTrue(res.getErrors() == null || res.getErrors().isEmpty());
        SearchResult data = (SearchResult) res.getData();
        assertNotNull(data.getItems());
        assertEquals(1, data.getItems().size());
        assertEquals("example.com", data.getItems().get(0).getDisplayLink());
        verify(cacheRepository).save(any(SearchResultsCacheEntry.class));
        verify(rateLimiter).markSearched(eq("google"), eq("q"));
    }

    @Test
    void testMissingKeysReturnsTypedCredentialsError() {
        ReflectionTestUtils.setField(service, "googleApiKey", "");
        ReflectionTestUtils.setField(service, "searchEngineId", "");

        ServiceResponse<?> res = service.simpleSearch("tok", "q");

        assertFalse(res.isOk());
        assertNotNull(res.getErrors());
        assertEquals(1, res.getErrors().size());
        Map<String, Object> err = res.getErrors().get(0);
        assertEquals("SEARCH_CREDENTIALS", err.get("code"));
        assertEquals(Boolean.FALSE, err.get("retryable"));
        assertEquals("google", err.get("provider"));
        verifyNoInteractions(restTemplate);
    }

    @Test
    void testNetworkErrorIsRetryableProviderError() {
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenThrow(new ResourceAccessException("Connection refused"));

        ServiceResponse<?> res = service.simpleSearch("tok", "q");

        assertFalse(res.isOk());
        Map<String, Object> err = res.getErrors().get(0);
        assertEquals("SEARCH_PROVIDER", err.get("code"));
        assertEquals(Boolean.TRUE, err.get("retryable"));
    }

    @Test
    void testProvider400IsNotRetryable() {
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenThrow(new HttpClientErrorException(HttpStatus.BAD_REQUEST));

        ServiceResponse<?> res = service.simpleSearch("tok", "q");

        assertFalse(res.isOk());
        Map<String, Object> err = res.getErrors().get(0);
        assertEquals("SEARCH_PROVIDER", err.get("code"));
        assertEquals(Boolean.FALSE, err.get("retryable"));
    }

    @Test
    void testProvider500IsRetryable() {
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenThrow(new HttpServerErrorException(HttpStatus.INTERNAL_SERVER_ERROR));

        ServiceResponse<?> res = service.simpleSearch("tok", "q");

        assertFalse(res.isOk());
        Map<String, Object> err = res.getErrors().get(0);
        assertEquals("SEARCH_PROVIDER", err.get("code"));
        assertEquals(Boolean.TRUE, err.get("retryable"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void testCacheWriteFailureDegradesToUncachedSuccess() {
        Map<String, Object> body = new HashMap<>();
        Map<String, Object> item = new HashMap<>();
        item.put("title", "T");
        item.put("link", "https://example.com/x");
        item.put("snippet", "S");
        body.put("items", List.of(item));
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenReturn(ResponseEntity.ok(body));
        doThrow(new RuntimeException("command insert requires authentication"))
                .when(cacheRepository).save(any(SearchResultsCacheEntry.class));

        ServiceResponse<?> res = service.simpleSearch("tok", "q");

        assertTrue(res.isOk());
        SearchResult data = (SearchResult) res.getData();
        assertNotNull(data.getItems());
        assertEquals(1, data.getItems().size());
    }

    @Test
    @SuppressWarnings("unchecked")
    void testForceSearchReturnsLiveResult() {
        // forceSearch bypasses the fresh-cache read (slice-2 G5): with an
        // empty cache it goes live and succeeds (covered below with a
        // populated cache asserting the bypass explicitly).
        Map<String, Object> body = new HashMap<>();
        Map<String, Object> live = new HashMap<>();
        live.put("title", "LIVE");
        live.put("link", "https://live.example/");
        live.put("snippet", "S");
        body.put("items", List.of(live));
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenReturn(ResponseEntity.ok(body));

        ServiceResponse<?> forced = service.forceSearch("tok", "q");
        assertTrue(forced.isOk());
        assertEquals("LIVE", ((SearchResult) forced.getData()).getItems().get(0).getTitle());
        verify(restTemplate).exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class));
    }

    // ── Slice-2 read-side: normalized lookup + raw fallback + force ──

    private SearchResultsCacheEntry cachedEntry(String query, long ttlMinutes) {
        SearchResultItem item = new SearchResultItem();
        item.setTitle("CACHED");
        item.setLink("https://cached.example/x");
        item.setSnippet("C");
        item.setDisplayLink("cached.example");
        return new SearchResultsCacheEntry(query, List.of(item), ttlMinutes);
    }

    @Test
    void testNormalizedCacheHitServesWithoutGoogleCall() {
        when(cacheRepository.findByQuery(eq("angular signals")))
                .thenReturn(Optional.of(cachedEntry("angular signals", 30)));

        ServiceResponse<?> res = service.simpleSearch("tok", "  Angular   SIGNALS ");

        assertTrue(res.isOk());
        assertEquals("CACHED", ((SearchResult) res.getData()).getItems().get(0).getTitle());
        verify(cacheRepository).findByQuery(eq("angular signals"));
        verifyNoInteractions(restTemplate);
    }

    @Test
    void testRawFallbackServesLegacyRows() {
        when(cacheRepository.findByQuery(eq("angular signals"))).thenReturn(Optional.empty());
        when(cacheRepository.findByQuery(eq("Angular Signals")))
                .thenReturn(Optional.of(cachedEntry("Angular Signals", 30)));

        ServiceResponse<?> res = service.simpleSearch("tok", "Angular Signals");

        assertTrue(res.isOk());
        assertEquals("CACHED", ((SearchResult) res.getData()).getItems().get(0).getTitle());
        verifyNoInteractions(restTemplate);
    }

    @Test
    void testCooldownStaleServeHitsNormalizedKey() {
        // Slice-2 F1 (re-review 8abdd093): rate-limited + expired entry
        // stored under the NORMALIZED key (the phase-2 write shape). The
        // cooldown stale-serve must find it via normalized-first lookup
        // instead of falling through to live Google during cooldown.
        SearchResultsCacheEntry stale = cachedEntry("angular signals", -120);
        when(rateLimiter.isRateLimited(eq("google"), eq("  Angular   SIGNALS "))).thenReturn(true);
        when(cacheRepository.findByQuery(eq("angular signals"))).thenReturn(Optional.of(stale));

        ServiceResponse<?> res = service.simpleSearch("tok", "  Angular   SIGNALS ");

        assertTrue(res.isOk());
        assertEquals("CACHED", ((SearchResult) res.getData()).getItems().get(0).getTitle());
        verifyNoInteractions(restTemplate);
        // Cooldown reads never delete — stale rows stay (TTL owns expiry).
        verify(cacheRepository, never()).deleteById(anyString());
    }

    @Test
    void testCooldownStaleServeFallsBackToRawKey() {
        // Legacy rows live under the raw key; cooldown must still find them.
        SearchResultsCacheEntry legacy = cachedEntry("Angular Signals", -120);
        when(rateLimiter.isRateLimited(eq("google"), eq("Angular Signals"))).thenReturn(true);
        when(cacheRepository.findByQuery(eq("angular signals"))).thenReturn(Optional.empty());
        when(cacheRepository.findByQuery(eq("Angular Signals"))).thenReturn(Optional.of(legacy));

        ServiceResponse<?> res = service.simpleSearch("tok", "Angular Signals");

        assertTrue(res.isOk());
        assertEquals("CACHED", ((SearchResult) res.getData()).getItems().get(0).getTitle());
        verifyNoInteractions(restTemplate);
    }

    @Test
    @SuppressWarnings("unchecked")
    void testForceBypassesFreshCache() {
        // lenient: the stub exists to prove the bypass (findByQuery must
        // NEVER be consulted on the force path — strict stubs would fail
        // the test for exactly the behavior it asserts).
        lenient().when(cacheRepository.findByQuery(anyString()))
                .thenReturn(Optional.of(cachedEntry("q", 30)));
        Map<String, Object> body = new HashMap<>();
        Map<String, Object> live = new HashMap<>();
        live.put("title", "LIVE");
        live.put("link", "https://live.example/");
        live.put("snippet", "S");
        body.put("items", List.of(live));
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenReturn(ResponseEntity.ok(body));

        ServiceResponse<?> forced = service.forceSearch("tok", "q");

        assertTrue(forced.isOk());
        assertEquals("LIVE", ((SearchResult) forced.getData()).getItems().get(0).getTitle());
        verify(restTemplate).exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class));
    }

    @Test
    @SuppressWarnings("unchecked")
    void testExpiredEntryFallsThroughToLive() {
        SearchResultsCacheEntry expired = cachedEntry("q", -60);
        when(cacheRepository.findByQuery(anyString())).thenReturn(Optional.of(expired));
        Map<String, Object> body = new HashMap<>();
        Map<String, Object> live = new HashMap<>();
        live.put("title", "LIVE");
        live.put("link", "https://live.example/");
        live.put("snippet", "S");
        body.put("items", List.of(live));
        when(restTemplate.exchange(anyString(), eq(HttpMethod.GET), any(HttpEntity.class), eq(Map.class)))
                .thenReturn(ResponseEntity.ok(body));

        ServiceResponse<?> res = service.simpleSearch("tok", "q");

        assertTrue(res.isOk());
        assertEquals("LIVE", ((SearchResult) res.getData()).getItems().get(0).getTitle());
        verify(cacheRepository).deleteById(expired.getId());
    }
}
