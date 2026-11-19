# Broker Client — Specification

## Functional Requirements

- Provide a lightweight Node.js SDK for interacting with the Nexus Broker Gateway
- Support service discovery by operation name
- Invoke operations on registered services through the broker gateway
- Register new services with the broker gateway
- Perform health checks on individual services and the gateway itself
- Return structured error responses with standardized error codes

## Non-Functional Requirements

- Zero external dependencies beyond axios for HTTP
- Compatible with Node.js 18+ (including AWS Lambda and edge runtimes)
- All methods return Promises for async/await usage
- Structured BrokerResponse wrapper with success, data, and errors fields

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| (SDK) | `discoverService(operation)` | Find a service by operation name |
| (SDK) | `getServiceDetails(serviceName)` | Get service endpoint and metadata |
| (SDK) | `invokeOperation(operation, params, serviceName?)` | Invoke an operation on a service |
| (SDK) | `healthCheck(serviceName)` | Check if a service is healthy |
| (SDK) | `registerService(serviceDetails)` | Register a new service |
| (SDK) | `getGatewayHealth()` | Check broker gateway health |

## Data Model

- ServiceDetails: serviceName (String), endpoint (String), healthCheck (String), framework (String), status (String), operations (String[])
- BrokerResponse: success (Boolean), data (Object), errors (Object[]), statusCode (Integer), rawResponse (Object)
- ErrorDetail: code (String), message (String), field (String)
