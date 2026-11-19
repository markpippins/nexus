# How-To Guide: Building a Mock Version of the Spring/Broker-Gateway Application

## Table of Contents
1. [Overview](#overview)
2. [Understanding the Broker Architecture](#understanding-the-broker-architecture)
3. [Input and Output Patterns](#input-and-output-patterns)
4. [Existing Framework Components](#existing-framework-components)
5. [Building the Mock Service API Tool](#building-the-mock-service-api-tool)
6. [Implementation Steps](#implementation-steps)
7. [Testing the Mock Service](#testing-the-mock-service)

## Overview

This guide explains how to build a mock version of the Spring/Broker-Gateway application. The broker-gateway serves as an API gateway that routes requests to various microservices using a standardized request/response pattern. The mock version will allow developers to simulate service APIs and responses before writing actual code.

The mock service will function as a Postman-like tool that enables testing of service APIs without requiring the actual backend services to be implemented.

## Understanding the Broker Architecture

The broker system follows a centralized routing pattern where:

1. **Service Requests** are submitted to the `/api/broker/submitRequest` endpoint
2. The **Broker Controller** receives the request and delegates to the **Broker Component**
3. The **Broker** resolves the target service and operation
4. The **Broker** invokes the appropriate method with parameter binding
5. **Service Response** is returned to the client

The architecture supports both internal services (Spring beans) and external services (discovered via service registry).

## Input and Output Patterns

### Service Request Format

The broker accepts requests in the following format:

```json
{
  "service": "string",
  "operation": "string", 
  "params": {
    "param1": "value1",
    "param2": "value2"
  },
  "requestId": "string",
  "encrypt": false
}
```

**Fields:**
- `service`: Name of the target service (e.g., "loginService", "userService")
- `operation`: Operation to execute on the service (e.g., "login", "createUser")
- `params`: Key-value pairs of parameters for the operation
- `requestId`: Unique identifier for the request
- `encrypt`: Flag indicating if the request should be encrypted

### Service Response Format

The broker returns responses in the following format:

```json
{
  "ok": true,
  "data": {},
  "errors": [],
  "requestId": "string",
  "ts": "ISO timestamp",
  "version": "1.0",
  "service": "string",
  "operation": "string",
  "encrypt": false
}
```

**Fields:**
- `ok`: Boolean indicating success (true) or failure (false)
- `data`: Response payload when successful
- `errors`: Array of error objects when failed
- `requestId`: Echo of the request ID
- `ts`: Timestamp of the response
- `version`: API version
- `service`: Name of the service that processed the request
- `operation`: Operation that was executed
- `encrypt`: Encryption flag

### Error Format

When errors occur, the response includes error details:

```json
{
  "ok": false,
  "data": null,
  "errors": [
    {
      "code": "error_code",
      "message": "Human-readable error message"
    }
  ],
  "requestId": "string",
  "ts": "ISO timestamp",
  "version": "1.0",
  "service": "string",
  "operation": "string"
}
```

## Existing Framework Components

### Core Components

1. **BrokerController** - REST endpoint that receives service requests
2. **Broker** - Core component that resolves services and executes operations
3. **ServiceRequest/ServiceResponse** - Standardized request/response DTOs
4. **ServiceDiscoveryClient** - Interface for discovering services from registry
5. **ExternalServiceInvoker** - Interface for invoking operations on external services

### Annotations

- `@BrokerOperation` - Marks methods that can be invoked via the broker
- `@BrokerParam` - Specifies parameter mapping for broker operations

### Service Resolution Process

1. **Local Bean Lookup**: First attempts to find a Spring bean with the service name
2. **Type-Based Lookup**: If no named bean, looks for beans by type simple name
3. **External Service Proxy**: If no local service found, creates a proxy for external service calls

## Building the Mock Service API Tool

### Concept

The mock service API tool will be a Postman-like interface that allows developers to:

1. Define mock service responses for specific service/operation combinations
2. Simulate service behavior without implementing actual services
3. Test client applications against mock services
4. Validate request/response patterns before implementation

### Mock Service Architecture

```
[Client Request] -> [Mock Broker Gateway] -> [Mock Service Registry] -> [Mock Response]
```

### Implementation Approach

1. **Mock Service Registry**: Store predefined responses for service/operation combinations
2. **Dynamic Response Generation**: Allow configuration of response data based on request parameters
3. **Rule-Based Matching**: Support pattern matching for dynamic responses
4. **UI Interface**: Provide a web interface similar to Postman for configuring mocks

## Implementation Steps

### Step 1: Create Mock Service Registry

Create a registry to store mock configurations:

```java
@Component
public class MockServiceRegistry {
    
    private final Map<String, MockServiceConfig> mockConfigs = new ConcurrentHashMap<>();
    
    public void registerMock(String service, String operation, MockServiceConfig config) {
        String key = buildKey(service, operation);
        mockConfigs.put(key, config);
    }
    
    public Optional<MockServiceConfig> getMock(String service, String operation) {
        String key = buildKey(service, operation);
        return Optional.ofNullable(mockConfigs.get(key));
    }
    
    private String buildKey(String service, String operation) {
        return service + ":" + operation;
    }
}
```

### Step 2: Create Mock Service Configuration Model

```java
public class MockServiceConfig {
    private String service;
    private String operation;
    private Object responseData;
    private boolean success = true;
    private List<Map<String, Object>> errors;
    private int delayMs = 0; // Simulate network delay
    private String condition; // Condition for when to return this response
    
    // Constructors, getters, and setters
}
```

### Step 3: Create Mock Broker Implementation

Extend the existing Broker class to handle mock responses:

```java
@Component
public class MockBroker extends Broker {
    
    @Autowired
    private MockServiceRegistry mockServiceRegistry;
    
    @Override
    public ServiceResponse<?> submit(ServiceRequest req) {
        // Check if there's a mock configuration for this service/operation
        Optional<MockServiceConfig> mockConfig = 
            mockServiceRegistry.getMock(req.getService(), req.getOperation());
            
        if (mockConfig.isPresent()) {
            return generateMockResponse(req, mockConfig.get());
        }
        
        // Fall back to normal broker processing if no mock exists
        return super.submit(req);
    }
    
    private ServiceResponse<?> generateMockResponse(ServiceRequest req, MockServiceConfig config) {
        // Apply delay if specified
        if (config.getDelayMs() > 0) {
            try {
                Thread.sleep(config.getDelayMs());
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        
        if (config.isSuccess()) {
            return ServiceResponse.ok(config.getService(), config.getOperation(), 
                                    config.getResponseData(), req.getRequestId());
        } else {
            return ServiceResponse.error(config.getService(), config.getOperation(), 
                                       config.getErrors(), req.getRequestId());
        }
    }
}
```

### Step 4: Create Mock Configuration API

Add endpoints to configure mock services:

```java
@RestController
@RequestMapping("/api/mock")
public class MockConfigurationController {
    
    @Autowired
    private MockServiceRegistry mockServiceRegistry;
    
    @PostMapping("/register")
    public ResponseEntity<String> registerMock(@RequestBody MockServiceConfig config) {
        mockServiceRegistry.registerMock(config.getService(), config.getOperation(), config);
        return ResponseEntity.ok("Mock registered successfully");
    }
    
    @DeleteMapping("/unregister/{service}/{operation}")
    public ResponseEntity<String> unregisterMock(@PathVariable String service, 
                                               @PathVariable String operation) {
        // Implementation to remove mock
        return ResponseEntity.ok("Mock unregistered successfully");
    }
}
```

### Step 5: Create Web Interface

Create a simple web interface for managing mocks:

```html
<!DOCTYPE html>
<html>
<head>
    <title>Mock Service Manager</title>
    <script src="https://cdn.jsdelivr.net/npm/axios/dist/axios.min.js"></script>
</head>
<body>
    <div id="app">
        <h1>Mock Service Manager</h1>
        
        <form id="mockForm">
            <div>
                <label>Service:</label>
                <input type="text" id="service" required />
            </div>
            <div>
                <label>Operation:</label>
                <input type="text" id="operation" required />
            </div>
            <div>
                <label>Success Response:</label>
                <textarea id="responseData"></textarea>
            </div>
            <div>
                <label>Error Response:</label>
                <textarea id="errorData"></textarea>
            </div>
            <div>
                <label>Delay (ms):</label>
                <input type="number" id="delay" value="0" />
            </div>
            <button type="submit">Register Mock</button>
        </form>
        
        <div id="testSection">
            <h2>Test Request</h2>
            <form id="testForm">
                <div>
                    <label>Service:</label>
                    <input type="text" id="testService" required />
                </div>
                <div>
                    <label>Operation:</label>
                    <input type="text" id="testOperation" required />
                </div>
                <div>
                    <label>Parameters (JSON):</label>
                    <textarea id="testParams">{}</textarea>
                </div>
                <button type="submit">Send Request</button>
            </form>
            <div id="response"></div>
        </div>
    </div>
    
    <script>
        document.getElementById('mockForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const config = {
                service: document.getElementById('service').value,
                operation: document.getElementById('operation').value,
                responseData: JSON.parse(document.getElementById('responseData').value || 'null'),
                errors: document.getElementById('errorData').value ? 
                       JSON.parse(document.getElementById('errorData').value) : [],
                success: !document.getElementById('errorData').value,
                delayMs: parseInt(document.getElementById('delay').value)
            };
            
            try {
                await axios.post('/api/mock/register', config);
                alert('Mock registered successfully');
            } catch (error) {
                alert('Error registering mock: ' + error.message);
            }
        });
        
        document.getElementById('testForm').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const request = {
                service: document.getElementById('testService').value,
                operation: document.getElementById('testOperation').value,
                params: JSON.parse(document.getElementById('testParams').value),
                requestId: 'test-' + Date.now()
            };
            
            try {
                const response = await axios.post('/api/broker/submitRequest', request);
                document.getElementById('response').innerHTML = 
                    '<pre>' + JSON.stringify(response.data, null, 2) + '</pre>';
            } catch (error) {
                document.getElementById('response').innerHTML = 
                    '<pre>' + JSON.stringify(error.response?.data || error.message, null, 2) + '</pre>';
            }
        });
    </script>
</body>
</html>
```

### Step 6: Integrate with Spring Boot

Configure the mock broker in your application:

```java
@Configuration
@Profile("mock")  // Only enable in mock profile
public class MockConfiguration {
    
    @Bean
    @Primary
    public Broker mockBroker(ApplicationContext ctx, ObjectMapper objectMapper, Validator validator) {
        return new MockBroker(ctx, objectMapper, validator);
    }
}
```

## Testing the Mock Service

### Basic Test

1. Start the application with the mock profile: `--spring.profiles.active=mock`
2. Register a mock service:
   ```bash
   curl -X POST http://localhost:8080/api/mock/register \
     -H "Content-Type: application/json" \
     -d '{
       "service": "userService",
       "operation": "getUser",
       "responseData": {"id": 1, "name": "John Doe", "email": "john@example.com"},
       "success": true
     }'
   ```
3. Test the mock service:
   ```bash
   curl -X POST http://localhost:8080/api/broker/submitRequest \
     -H "Content-Type: application/json" \
     -d '{
       "service": "userService",
       "operation": "getUser",
       "params": {"userId": 1},
       "requestId": "test-123"
     }'
   ```

### Advanced Testing

1. Test error responses by registering a mock with errors
2. Test delayed responses by setting delayMs
3. Test conditional responses based on request parameters
4. Verify that non-mocked services still work normally

## Conclusion

This mock service API tool provides a flexible way to simulate service behavior during development. It maintains compatibility with the existing broker architecture while allowing developers to define custom responses for testing purposes. The tool can be extended with additional features like request logging, response templating, and more sophisticated matching rules.