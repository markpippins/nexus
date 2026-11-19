# Broker Gateway Proxy — Reference Guide

## Configuration

| Property | Default | Description |
|----------|---------|-------------|
| `PORT` | 3333 | Service port |
| `UPSTREAM_URL` | http://localhost:8081 | Target Broker Gateway URL |
| `TLS_ENABLED` | false | Enable TLS termination |
| `TLS_CERT_PATH` | — | Path to TLS certificate file |
| `TLS_KEY_PATH` | — | Path to TLS key file |
| `RATE_LIMIT_MAX` | 200 | Max requests per window per API key |
| `RATE_LIMIT_WINDOW_MS` | 60000 | Rate limit window in milliseconds |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 3333 | Service port |
| `UPSTREAM_URL` | http://localhost:8081 | Upstream broker gateway URL |
| `TLS_ENABLED` | false | Enable TLS |
| `TLS_CERT_PATH` | — | TLS certificate path |
| `TLS_KEY_PATH` | — | TLS key path |
| `RATE_LIMIT_MAX` | 200 | Max requests per window |
| `NODE_ENV` | development | Runtime environment |

## Commands

| Command | Description |
|---------|-------------|
| `npm start` | Start the service |
| `npm run dev` | Start with live reload (nodemon) |
| `npm test` | Run tests |
| `node src/index.js` | Direct startup |

## Troubleshooting

- **Connection refused**: Verify the upstream Broker Gateway is running on port 8081 and reachable
- **TLS errors**: Check that TLS_CERT_PATH and TLS_KEY_PATH point to valid files — set TLS_ENABLED=false for testing
- **Rate limited**: The client has exceeded RATE_LIMIT_MAX requests — check the client's API key usage
- **Proxy timing out**: Increase the upstream timeout or check the Broker Gateway health
