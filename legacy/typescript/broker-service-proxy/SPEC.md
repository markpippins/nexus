# Broker Service Proxy — Specification

## Functional Requirements

- Proxy HTTP requests from internal services to the Broker Gateway
- Provide service-to-service authentication via proxy-level tokens
- Cache frequently accessed responses to reduce gateway load
- Aggregate responses from multiple gateway endpoints into single responses
- Circuit-breaker protection for upstream gateway failures

## Non-Functional Requirements

- Throughput: 3,000+ requests per second
- Cache TTL: configurable per endpoint (default 60s)
- P99 latency overhead: under 3ms (cache hit), under 8ms (cache miss)
- Graceful degradation: serve stale cache entries when gateway is unavailable

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| ANY | /api/* | Proxy to corresponding Broker Gateway endpoint |
| GET | /health | Proxy health check |
| GET | /cache/status | Cache hit/miss statistics |

## Data Model

- ProxyCacheEntry: cacheKey (String), response (JSON), statusCode (Integer), headers (JSON), cachedAt (Instant), expiresAt (Instant)
- CircuitBreakerState: serviceName (String), state (CLOSED|OPEN|HALF_OPEN), failureCount (Integer), lastFailureAt (Instant), cacheFallbackEnabled (Boolean)
