# Atomic Broker Gateway Python SDK

Lightweight Python client for interacting with Atomic Broker Gateway services.

## Installation

```bash
pip install requests
```

## Quick Start

```python
from atomic_broker_sdk import create_client, ServiceDetails

# Create client
client = create_client(
    gateway_url="http://localhost:8080",
    host_server_url="http://localhost:8085"
)

# Example: Register a service
service = ServiceDetails(
    service_name="python-microservice",
    endpoint="http://localhost:3001",
    health_check="health",
    framework="FastAPI"
)

if client.register_service(service):
    print("Service registered successfully!")
else:
    print("Service registration failed")

# Example: Invoke a service operation
response = client.invoke_operation(
    "getUserRegistrationForToken",
    {"token": "sample-token-123"}
)

if response.success:
    print(f"Success: {response.data}")
else:
    print(f"Error: {response.errors}")

# Example: Check service health
is_healthy = client.health_check("loginService")
print(f"Login service healthy: {is_healthy}")
```

## API Reference

### BrokerGatewayClient

Main client class for interacting with the broker gateway.

#### Constructor

```python
BrokerGatewayClient(base_url="http://localhost:8080", host_server_url="http://localhost:8085")
```

**Parameters:**
- `base_url`: URL of the broker gateway (default: "http://localhost:8080")
- `host_server_url`: URL of the host server for service discovery (default: "http://localhost:8085")

#### Methods

##### discover_service(operation: str) -> Optional[ServiceDetails]

Find a service that can handle the specified operation.

**Parameters:**
- `operation`: Operation name (e.g., "getUserRegistrationForToken")

**Returns:** `ServiceDetails` if found, `None` otherwise

```python
service = client.discover_service("getUserRegistrationForToken")
if service:
    print(f"Found service: {service.service_name} at {service.endpoint}")
```

##### get_service_details(service_name: str) -> Optional[ServiceDetails]

Get detailed information about a specific service.

**Parameters:**
- `service_name`: Name of the service

**Returns:** `ServiceDetails` if found, `None` otherwise

```python
details = client.get_service_details("loginService")
if details:
    print(f"Service endpoint: {details.endpoint}")
```

##### invoke_operation(operation: str, params: Dict[str, Any], service_name: str = None) -> BrokerResponse

Invoke an operation on a service through the broker gateway.

**Parameters:**
- `operation`: Operation name to invoke
- `params`: Parameters for the operation
- `service_name`: Optional service name (discovered if not provided)

**Returns:** `BrokerResponse` with operation results

```python
response = client.invoke_operation(
    "getUserRegistrationForToken",
    {"token": "user-token-123"},
    "loginService"  # Optional, auto-discovered
)

if response.success:
    print(f"Result: {response.data}")
else:
    print(f"Error: {response.errors}")
```

##### health_check(service_name: str) -> bool

Perform health check on a specific service.

**Parameters:**
- `service_name`: Name of the service to check

**Returns:** `True` if healthy, `False` otherwise

```python
is_healthy = client.health_check("loginService")
if is_healthy:
    print("Service is healthy")
else:
    print("Service is unhealthy")
```

##### register_service(service_details: ServiceDetails) -> bool

Register a new service with the broker gateway.

**Parameters:**
- `service_details`: ServiceDetails object containing service information

**Returns:** `True` if registration successful, `False` otherwise

```python
service = ServiceDetails(
    service_name="my-service",
    endpoint="http://localhost:3000",
    health_check="health",
    framework="FastAPI"
)

success = client.register_service(service)
if success:
    print("Service registered!")
else:
    print("Registration failed")
```

##### get_gateway_health() -> BrokerResponse

Check the health of the broker gateway itself.

**Returns:** `BrokerResponse` with gateway health status

```python
response = client.get_gateway_health()
if response.success:
    print(f"Gateway status: {response.data}")
else:
    print(f"Gateway error: {response.errors}")
```

## Data Models

### ServiceDetails

Contains service information from the broker gateway.

```python
@dataclass
class ServiceDetails:
    service_name: str
    endpoint: str
    health_check: Optional[str]
    framework: Optional[str]
    status: Optional[str]
    operations: Optional[str]
```

### BrokerResponse

Response from broker gateway operations.

```python
@dataclass
class BrokerResponse:
    success: bool
    data: Optional[Any]
    errors: Optional[list]
    status_code: int
    raw_response: Optional[str]
```

### BrokerRequest

Request for broker gateway operations.

```python
@dataclass
class BrokerRequest:
    service_name: str
    operation: str
    params: Dict[str, Any]
    request_id: Optional[str]
```

## Error Handling

All SDK operations return structured error information:

```python
if not response.success:
    for error in response.errors:
        print(f"Error {error['code']}: {error['message']}")
```

Common error codes:
- `SERVICE_NOT_FOUND`: No service found for operation
- `SERVICE_DETAILS_NOT_FOUND`: Could not get service details
- `OPERATION_FAILED`: HTTP operation failed
- `CLIENT_ERROR`: Client-side error (network, parsing, etc.)
- `GATEWAY_UNHEALTHY`: Gateway health check failed

## Logging

The SDK uses Python's `logging` module. Configure logging in your application:

```python
import logging

# Set logging level
logging.basicConfig(level=logging.INFO)

# Or use your app's logger
logger = logging.getLogger(__name__)
```

## Examples

See the complete working examples in the main README at `/atomic/BROKER_SDK_README.md`