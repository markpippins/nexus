# Broker Client — Reference Guide

## Configuration

| Property | Default | Description |
|----------|---------|-------------|
| `gatewayUrl` | http://localhost:8080 | Broker gateway URL |
| `hostServerUrl` | http://localhost:8085 | Service registry URL |
| `timeout` | 5000 | HTTP request timeout (ms) |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BROKER_GATEWAY_URL` | http://localhost:8080 | Gateway URL |
| `HOST_SERVER_URL` | http://localhost:8085 | Registry URL |

## Commands

| Command | Description |
|---------|-------------|
| `npm test` | Run SDK unit tests |
| `node examples/basic.js` | Run the basic usage example |
| `node examples/express.js` | Run Express.js integration example |
| `node examples/lambda.js` | Run AWS Lambda integration example |

## Troubleshooting

- **SERVICE_NOT_FOUND**: No service is registered for the requested operation — check that the service has been registered with the gateway
- **GATEWAY_UNHEALTHY**: The broker gateway health check failed — verify the gateway URL is correct and the gateway is running
- **OPERATION_FAILED**: The HTTP request to the gateway failed — check network connectivity and gateway status
- **CLIENT_ERROR**: A client-side error occurred (network, parsing, etc.) — check the error details for more information
