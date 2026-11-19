# Mock Broker Service — Specification

## Functional Requirements

- Simulate Broker Gateway API responses for integration testing
- Support configurable response delays for timeout testing
- Return pre-defined response fixtures based on request matching
- Generate realistic error responses (4xx, 5xx) for negative testing
- Provide a reset endpoint to clear simulated state between test runs
- Log all received requests for test assertion verification

## Non-Functional Requirements

- Response latency configurable per endpoint (0ms to 30s)
- Deterministic fixture matching by method + path + body pattern
- Zero external dependencies — fully self-contained for CI environments
- Node 18 compatibility for legacy deployment environments

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| ANY | /api/mock/* | Mock endpoint — returns configured fixture |
| POST | /api/mock/configure | Configure a mock response for a specific request pattern |
| POST | /api/mock/reset | Reset all mock configurations and request log |
| GET | /api/mock/requests | Return log of all received requests for test assertions |
| POST | /api/mock/delay | Configure artificial response delay |
| GET | /health | Service health check |

## Data Model

- MockFixture: method (String), pathPattern (String), statusCode (Integer), responseBody (JSON), headers (JSON), delayMs (Integer)
- MockRequestLog: method (String), path (String), headers (JSON), body (JSON), timestamp (Instant), matchedFixture (String)
- MockConfig: globalDelayMs (Integer), randomFailureRate (Float), enabled (Boolean)
