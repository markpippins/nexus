# Broker Service Proxy — Reference Guide

## Configuration

| Property | Default | Description |
|----------|---------|-------------|
| `PORT` | 3334 | Service port |
| `UPSTREAM_URL` | http://localhost:8081 | Target Broker Gateway URL |
| `CACHE_ENABLED` | true | Enable response caching |
| `CACHE_TTL_SECONDS` | 60 | Default cache TTL |
| `CACHE_MAX_SIZE` | 1000 | Maximum cached entries |
| `CIRCUIT_BREAKER_THRESHOLD` | 5 | Failures before circuit opens |
| `CIRCUIT_BREAKER_RESET_MS` | 30000 | Time before circuit resets (30s) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 3334 | Service port |
| `UPSTREAM_URL` | http://localhost:8081 | Upstream broker gateway URL |
| `CACHE_TTL_SECONDS` | 60 | Cache TTL in seconds |
| `CACHE_MAX_SIZE` | 1000 | Maximum cache entries |
| `CIRCUIT_BREAKER_THRESHOLD` | 5 | Circuit breaker failure threshold |
| `NODE_ENV` | development | Runtime environment |

## Commands

| Command | Description |
|---------|-------------|
| `npm start` | Start the service |
| `npm run dev` | Start with live reload (nodemon) |
| `npm test` | Run tests |
| `curl http://localhost:3334/health` | Health check |
| `curl http://localhost:3334/cache/status` | Cache statistics |

## Troubleshooting

- **Circuit breaker open**: The upstream gateway (port 8081) may be down — the proxy will serve stale cache entries if available
- **Cache not working**: Verify CACHE_ENABLED is true and the endpoint is cacheable (GET requests only)
- **Stale responses**: Decrease CACHE_TTL_SECONDS for more frequent cache refreshes
- **Connection pool exhaustion**: Increase the upstream connection pool size or check gateway performance
