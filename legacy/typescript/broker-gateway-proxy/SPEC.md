# Broker Gateway Proxy — Specification

## Functional Requirements

- Proxy incoming HTTP requests from external clients to the internal Broker Gateway
- Provide TLS termination for secure external communication
- Enforce API key authentication for incoming requests
- Log request/response metadata for auditing and debugging
- Support request rate limiting per client API key

## Non-Functional Requirements

- Throughput: 2,000+ requests per second
- P99 latency overhead: under 5ms (proxy only)
- No request buffering — streams data between client and upstream
- Graceful shutdown with draining of in-flight requests

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| ANY | /* | Proxy all requests to the internal Broker Gateway at port 8081 |

## Data Model

- ProxyRequest: id (UUID), method (String), path (String), headers (JSON), clientIp (String), apiKey (String), receivedAt (Instant)
- ProxyLogEntry: requestId (UUID), upstreamStatus (Integer), responseSize (Long), durationMs (Long), timestamp (Instant)
- ApiKey: key (String), clientName (String), enabled (Boolean), rateLimit (Integer), createdAt (Instant)
