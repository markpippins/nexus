package com.aibizarchitect.nexus.v1.spring.search;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

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
        // NOTE: forceSearch currently still serves a FRESH cache entry (only
        // the rate-limiter is bypassed). Whether force must also bypass fresh
        // cache is slice-2 (cache parity) scope — asserted here only that a
        // force call with empty cache goes live and succeeds.
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
}
