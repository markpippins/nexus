# Mock Broker Service — Reference Guide

## Configuration

| Property | Default | Description |
|----------|---------|-------------|
| `PORT` | 8099 | Service port |
| `DEFAULT_DELAY_MS` | 0 | Default artificial response delay |
| `RANDOM_FAILURE_RATE` | 0 | Rate of random 5xx errors (0.0 to 1.0) |
| `FIXTURES_PATH` | ./fixtures | Path to fixture JSON files |
| `LOG_MAX_REQUESTS` | 1000 | Maximum stored request log entries |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 8099 | Service port |
| `MOCK_DELAY_MS` | 0 | Global delay in milliseconds |
| `MOCK_FAILURE_RATE` | 0 | Random failure rate |
| `MOCK_FIXTURES_PATH` | ./fixtures | Fixture file location |
| `NODE_ENV` | development | Runtime environment |

## Commands

| Command | Description |
|---------|-------------|
| `npm start` | Start the service |
| `npm run dev` | Start with live reload (nodemon) |
| `npm test` | Run tests |
| `curl -X POST -d '{"method":"GET","pathPattern":"/api/health","statusCode":200,"responseBody":{"status":"ok"}}' http://localhost:8099/api/mock/configure` | Configure a mock |
| `curl -X POST http://localhost:8099/api/mock/reset` | Reset all mocks and logs |

## Troubleshooting

- **Node 18 required**: This service overrides to Node 18 for legacy dependency compatibility — ensure Node 18 is installed
- **Mock not matching**: Check the pathPattern uses regex syntax — literal paths should be escaped or use `{ }` variables
- **Responses too slow**: Check if MOCK_DELAY_MS is set globally or per-fixture — reset with `POST /api/mock/reset`
- **Unexpected failures**: RANDOM_FAILURE_RATE may be set — set to 0 to disable random failures during debugging
- **Request log full**: Increase LOG_MAX_REQUESTS or clear the log periodically via reset endpoint
