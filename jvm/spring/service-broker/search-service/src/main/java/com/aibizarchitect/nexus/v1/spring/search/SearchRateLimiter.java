package com.aibizarchitect.nexus.v1.spring.search;

import java.time.Duration;
import java.time.Instant;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

/**
 * Redis-backed rate limiter for search queries.
 *
 * <p>Prevents the same search query from hitting external APIs (Google, YouTube,
 * Unsplash, Gemini) more than once within a configurable cooldown window.
 * When a query is rate-limited, the caller should fall back to MongoDB-cached
 * results — even if the cache entry's own TTL has expired.
 *
 * <p>Redis key format: {@code search:ratelimit:{service}:{normalizedQuery}}
 * Value: ISO-8601 instant when the last external API call was made.
 * TTL on the key matches the cooldown window so keys auto-expire and don't
 * accumulate indefinitely.
 */
@Service
public class SearchRateLimiter {

    private static final Logger log = LoggerFactory.getLogger(SearchRateLimiter.class);

    private static final String KEY_PREFIX = "search:ratelimit:";

    private final StringRedisTemplate redis;
    private final long cooldownHours;
    private final boolean enabled;

    public SearchRateLimiter(
            StringRedisTemplate redis,
            @Value("${search.ratelimit.cooldown-hours:4}") long cooldownHours,
            @Value("${search.ratelimit.enabled:true}") boolean enabled) {
        this.redis = redis;
        this.cooldownHours = cooldownHours;
        this.enabled = enabled;
        log.info("SearchRateLimiter initialized: enabled={}, cooldownHours={}", enabled, cooldownHours);
    }

    /**
     * Build a Redis key for the given service and query.
     */
    static String key(String service, String query) {
        return KEY_PREFIX + service + ":" + normalize(query);
    }

    // ── Public API ────────────────────────────────────────────────────────

    /**
     * Check whether the given query is within the cooldown window for the
     * named service.  Returns {@code true} if the query was already searched
     * recently and should NOT hit the external API again.
     */
    public boolean isRateLimited(String service, String query) {
        if (!enabled) {
            return false;
        }
        try {
            String val = redis.opsForValue().get(key(service, query));
            if (val == null) {
                return false; // never searched
            }
            Instant lastSearched = Instant.parse(val);
            Duration elapsed = Duration.between(lastSearched, Instant.now());
            boolean limited = elapsed.toHours() < cooldownHours;
            if (limited) {
                log.debug("Rate-limited {}/{} — last searched {} ago (cooldown {}h)",
                        service, query, elapsed.toMinutes() + "m", cooldownHours);
            }
            return limited;
        } catch (Exception e) {
            log.warn("Error checking rate limit for {}/{}: {}", service, query, e.getMessage());
            return false; // fail open — if Redis is down, allow the search
        }
    }

    /**
     * Record that the given query was just searched against the named
     * service's external API.  Sets the cooldown timer.
     */
    public void markSearched(String service, String query) {
        if (!enabled) {
            return;
        }
        try {
            String k = key(service, query);
            redis.opsForValue().set(k, Instant.now().toString(),
                    Duration.ofHours(cooldownHours));
            log.debug("Marked {}/{} — cooldown until +{}h", service, query, cooldownHours);
        } catch (Exception e) {
            log.warn("Error marking rate limit for {}/{}: {}", service, query, e.getMessage());
        }
    }

    /**
     * Remove the rate-limit key for the given query, allowing an immediate
     * fresh search the next time it is requested.
     *
     * <p>Not currently wired — force-refresh bypasses the rate-limit check
     * and calls {@link #markSearched} on success, which overwrites the
     * timestamp.  This method remains available for programmatic reset
     * flows (e.g., admin invalidation from an MCP tool).
     */
    public void resetRateLimit(String service, String query) {
        if (!enabled) {
            return;
        }
        try {
            redis.delete(key(service, query));
            log.debug("Reset rate limit for {}/{}", service, query);
        } catch (Exception e) {
            log.warn("Error resetting rate limit for {}/{}: {}", service, query, e.getMessage());
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────

    /**
     * Normalize a query for consistent cache/rate-limit keys:
     * lowercase, trim, collapse runs of whitespace.
     * Package-visible: GoogleSearchService reuses the identical rule for
     * cache lookups (slice-2 G2 — one normalization, not two).
     */
    static String normalize(String query) {
        if (query == null) {
            return "";
        }
        return query.toLowerCase().trim().replaceAll("\\s+", " ");
    }
}
